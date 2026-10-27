"""仕上げの実再構成・日次M1出自・Instagram8画像を隔離して確認する。"""
import copy
import hashlib
import io
import json
import tempfile
import unittest
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
from nightly_fakes import NightlyAI,NightlyMeal
from test_nightly_flow import NOW,TARGET
from test_readiness import evidence
from src.mina.store import Store
from src.mina.domain import slot_time
from src.mina.request_ledger import RequestLedger
from src.mina.publication_hold import acquire
from src.mina.replacement_contract import Contract,canonical,digest,stamp
from src.mina.replacement_rebuild import ReplacementRebuilder
from src.mina.instagram_media import host_asset as actual_host,verify_public
from tests.mina.scoped_fakes import SyntheticDrive


class RebuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
        flags=patch.dict('os.environ',{'MINA_LIVE_WRITES':'true','MINA_MEAL_REPLACEMENT_ENABLED':'true','MINA_URL_TRACKING_ENABLED':'false'})
        flags.start();self.addCleanup(flags.stop)
        self.store=Store(self.base/'db');self.ledger=RequestLedger(self.store);self.clock=[NOW];self.host='www.log-eats.com'
        self.ai=NightlyAI('plain',self.clock);self.png=io.BytesIO();Image.new('RGB',(1200,630),'white').save(self.png,format='PNG');self.png=self.png.getvalue()
        ogp_sha=hashlib.sha256(self.png).hexdigest()
        class Meal(NightlyMeal):
            base='https://www.log-eats.com';token='synthetic-'+40*'x'
            daily=None
            def wrap(inner,*args,**kwargs):
                response=super().wrap(*args,**kwargs);response['result']['ogp']['image_sha256']=ogp_sha
                inner.requests[args[1]]=copy.deepcopy(response['result']);response['ogp']=response['result']['ogp'];return response
            def day_state(inner,target):return {**super().day_state(target),'daily_share':copy.deepcopy(inner.daily),
                'nutrition_targets':{'calories':2000,'protein':90,'fat':55,'carbs':250},
                'summary':{k:sum(m['analysis']['summary'][k] for m in inner.meals.values()) for k in ('totalCalories','totalProtein','totalFat','totalCarbs')}}
            def json_write(inner,*args,**kwargs):
                response=super().json_write(*args,**kwargs);inner.daily=copy.deepcopy(response['result']);return response
        self.meal=Meal('plain',self.clock,self.host);self.drive=SyntheticDrive();self.images={};self.saved={'target_date':TARGET,'images':{}}
        daily=self.base/'daily'/TARGET;daily.mkdir(parents=True);self.directory=daily
        for i,slot in enumerate(('breakfast','lunch','dinner')):
            buf=io.BytesIO();Image.new('RGB',(96,96),(70*i,40,90)).save(buf,format='JPEG');raw=buf.getvalue();self.images[slot]=raw
            p=daily/(slot+'.jpg');p.write_bytes(raw);key=str(uuid.uuid4());fid='synthetic_old_image_'+slot
            self.drive.seed(fid,raw,TARGET+'-'+slot+'.jpg')
            response=self.meal.register(raw,'fixture.jpg',slot,slot_time(date.fromisoformat(TARGET),slot).isoformat(),self.ai.plan['meals'][slot]['comment'],key,self.meal.sv)
            self.ledger.prepare(key,'meal_register',TARGET,slot,{});self.ledger.record(key,'complete',{'persistence':'saved'})
            self.saved['images'][slot]={'path':str(p),'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
                'image_review':{'status':'image_acceptable','reason_codes':[]},
                'drive':{'id':fid,'name':TARGET+'-'+slot+'.jpg','verified':True,'sha256':hashlib.sha256(raw).hexdigest()}}
        self.owner='99999999-9999-4999-8999-999999999999';acquire(self.store,TARGET,self.owner,NOW)
        with self.store.transaction() as db:db.execute("UPDATE publication_holds SET state='saved_pending_review' WHERE target_date=?",(TARGET,))
        self.prepare=str(uuid.uuid4());self.commit=str(uuid.uuid4());self.ports=self.base/'ports';self.ports.mkdir()
        old=self.saved['images']['dinner']['drive'];newid='synthetic_replacement_1';name=f'{TARGET}-dinner-replacement-{self.prepare}.jpg'
        self.drive.seed(newid,self.images['dinner'],name)
        (self.ports/(self.prepare+'-drive.json')).write_bytes(canonical({'new':{'id':newid,'name':name,'verified':True,'sha256':hashlib.sha256(self.images['dinner']).hexdigest()},'previous':old}))
        (daily/'draft.json').write_bytes(canonical({**self.ai.plan,'target_date':TARGET,'daily_comment':'パスタで約1970kcalという旧本文'}))
        (daily/'drive.json').write_bytes(canonical(self.saved))
        self.response={'contract_version':'mina-rescue-v3','request':{'request_uuid':self.commit,'persistence':'saved','status':'succeeded','input_hash':'ih1_'+'a'*64},
            'result_status':'unchanged','current_state_version':self.meal.sv,'result':copy.deepcopy(self.meal.meals['dinner']),
            'current_result':copy.deepcopy(self.meal.meals['dinner']),'affected_daily_result':None,'current_daily_result':None}
        self.reader=Mock();self.reader.request.side_effect=lambda *args:copy.deepcopy(self.response)

    def preconditions(self):
        from tests.mina.test_replacement_integration import SAMPLES,fresh
        value=copy.deepcopy(SAMPLES['precondition_success']);value.update(date=TARGET,state_version=self.meal.sv,freshness=fresh(NOW))
        for m in value['meals']:
            slot=m['expected']['target_slot'];r=self.meal.meals[slot]
            m['expected'].update(target_date=TARGET,state_version=self.meal.sv,target_result_version=r['result_version'],target_share_url=r['share']['url'])
            m.update(image_sha256=r['image_sha256'],stored_image_sha256=r['stored_image_sha256'])
            m['analysis'].update(foods=[{**f,'amount':f.get('amount')} for f in r['analysis']['foods']],summary=r['analysis']['summary'],is_ambiguous=None,analysis_source='stored_canonical')
            m['analysis']['quality'].update(status='pass',warnings=[],atwater_calories=600,calorie_difference=0,calorie_difference_ratio=0)
            m['analysis_digest']=Contract(lambda:NOW).analysis(m['analysis']);m['precondition_digest']=digest(m['expected'],'pc1_')
        value['summary']={k:sum(m['analysis']['summary'][k] for m in value['meals']) for k in value['summary']};value['daily_share']=None
        return Contract(lambda:NOW).preconditions(value,TARGET)

    def build(self,instagram):
        self.response['affected_daily_result']=copy.deepcopy(self.meal.daily);self.response['current_daily_result']=copy.deepcopy(self.meal.daily)
        e={'target_date':TARGET,'slot':'dinner','prepare_uuid':self.prepare,'commit_uuid':self.commit,'owner':self.owner,'epoch':1}
        rebuilder=ReplacementRebuilder(self.store,self.meal,self.ai,self.drive,self.base/'daily',self.base/'instagram',evidence(), 'a'*40,self.host,lambda:self.clock[0],
            api=Mock(),ports_directory=self.ports,instagram_enabled=instagram,response_reader=self.reader)
        def capture(url,host):return {'meta':{'og:image':url+'/og-image','image_sha256':hashlib.sha256(self.png).hexdigest()},'digest':'d'*64,'review_image':self.png}
        normal=Mock();normal.preconditions.side_effect=lambda target:self.preconditions()
        from urllib.parse import parse_qs,urlsplit
        class Response(io.BytesIO):
            headers={'Content-Type':'image/jpeg'}
        class Opener:
            def open(inner,request,timeout):return Response(self.drive.files[parse_qs(urlsplit(request.full_url).query)['id'][0]][0])
        def host(asset,drive,store,allow_public):
            return actual_host(asset,drive,store,allow_public,verify=lambda a:verify_public(a,Opener()))
        with patch('src.mina.ogp.capture_ogp',side_effect=capture),patch('src.mina.replacement_client.ReplacementReadClient',return_value=normal),\
             patch('src.mina.instagram_media.host_asset',side_effect=host),patch('src.mina.instagram_publisher.instagram_channel',return_value={'id':'ig','serviceId':'synthetic'}):
            return rebuilder(e,self.response,self.preconditions(),{'plan':canonical({**self.ai.plan['meals']['dinner'],'menu':'別の魚献立'}).decode(),'image':self.images['dinner']})

    def test_actual_rebuilder_uses_new_daily_uuid_and_makes_four_plus_eight_images(self):
        xs,igs,proof=self.build(True)
        self.assertEqual(len(xs),4);self.assertEqual(len(igs),4);self.assertEqual(sum(len(c.assets) for c in igs),8)
        daily=next(c for c in xs if c.slot=='daily');self.assertIn('1800kcal',daily.comment);self.assertNotIn('パスタ',daily.comment)
        self.assertEqual(daily.approval_source['operation'],'daily_share');self.assertNotEqual(daily.approval_source['request_key'],self.commit)
        self.assertEqual(next(c for c in xs if c.slot=='dinner').approval_source['contract_version'],'mina-rescue-v3')
        self.assertEqual(len([c for c in self.meal.calls if c[:2]==('POST','daily')]),1)
        self.assertEqual(proof['verdict'],'pass')
        self.assertIn('旧本文',(self.directory/'draft.json').read_text(encoding='utf-8'))

    def test_existing_c_daily_result_does_not_steal_new_m1_daily_source(self):
        old=str(uuid.uuid4());payload={'date':TARGET,'comment':'旧パスタ1970kcal','expected_state_version':self.meal.sv}
        self.meal.json_write('/api/internal/mina/daily-share',payload,request_key=old)
        self.ledger.prepare(old,'daily_share',TARGET,'daily',payload);self.ledger.record(old,'complete',{'persistence':'saved'})
        xs,igs,proof=self.build(False);daily=next(c for c in xs if c.slot=='daily')
        self.assertEqual(daily.approval_source['operation'],'daily_share')
        self.assertNotIn(daily.approval_source['request_key'],(old,self.commit));self.assertIn('1800kcal',daily.comment)

    def test_daily_response_loss_recovers_by_get_without_another_post(self):
        from src.mina.clients import OutcomeUnknown
        before=self.meal.json_write
        def lose(*args,**kwargs):before(*args,**kwargs);raise OutcomeUnknown('合成の応答喪失')
        self.meal.json_write=lose
        with self.assertRaises(OutcomeUnknown):self.build(False)
        writes=len(self.meal.calls)
        self.meal.json_write=Mock(side_effect=AssertionError('再POSTは禁止'))
        xs,igs,proof=self.build(False)
        self.assertEqual(len(self.meal.calls),writes);self.meal.json_write.assert_not_called()
        self.assertEqual(next(c for c in xs if c.slot=='daily').approval_source['operation'],'daily_share')

    def test_candidate_transaction_rolls_back_when_deadline_changes_before_commit(self):
        from datetime import timedelta
        from src.mina.replacement_bundle import stage
        xs,igs,proof=self.build(False);calls=[0]
        def checkpoint(db):
            calls[0]+=1
            if calls[0]==2:raise ValueError('scoped_rescue_deadline')
        with self.assertRaises(ValueError):stage(self.store,TARGET,self.owner,1,proof['source_digest'],xs,[],self.host,(),NOW,proof,required_channels=('x',),checkpoint=checkpoint)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM candidates').fetchone()[0],0)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='replacement_bundles'").fetchone())
            self.assertEqual(db.execute('SELECT state FROM publication_holds WHERE target_date=?',(TARGET,)).fetchone()[0],'saved_pending_review')


if __name__=='__main__':unittest.main()
