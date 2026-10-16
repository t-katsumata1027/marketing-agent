"""終端未保存の夕食だけを新画像で復旧し、朝昼と未知要求の再送を拒否する。"""
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from src.mina.clients import MealWriteFailure,OutcomeUnknown
from src.mina.registration import register_day
from src.mina.store import Store
import test_registration as registration_fixtures

ROUND='11111111-1111-4111-8111-111111111111'


class TerminalUnsavedTests(unittest.TestCase):
    def setup_flow(self,root):
        fixtures=registration_fixtures.RegistrationTests()
        draft,saved=fixtures.setup_inputs(root)
        store=Store(root/'db')
        client=fixtures.client()
        register=client.register.side_effect
        lookup=client.request_state.side_effect
        failed=[]
        def send(raw,filename,slot,*args,**kwargs):
            if slot=='dinner' and not failed:
                failed.append(kwargs['request_key'])
                raise MealWriteFailure(422,{'persistence':'not_saved','stage':'quality','reason':'quality_review_required'})
            return register(raw,filename,slot,*args,**kwargs)
        def read(key,operation):
            if key in failed:
                return {'contract_version':'mina-write-v1','request_uuid':key,'operation':operation,'status':'failed',
                    'persistence':'not_saved','stage':'quality','reason':'quality_review_required','result_status':'unresolved','current_result':None}
            return lookup(key,operation)
        client.register.side_effect=send
        client.request_state.side_effect=read
        ai=Mock()
        ai.review_meal.return_value=ai.review_ogp.return_value={'status':'publishable','reason_codes':[]}
        capture=lambda url,host:{'meta':{'og:image':url+'/og-image','image_sha256':'e'*64},'digest':'d'*64,'review_image':b'\xff\xd8\xffpreview'}
        return store,draft,saved,client,ai,capture,failed

    def invoke(self,root,flow,replacement=False):
        store,draft,saved,client,ai,capture,_=flow
        return register_day(store,draft,saved,root/'ogp',client,ai,'www.log-eats.com',capture=capture,
                            replacement_rounds={'dinner':ROUND} if replacement else None)

    def replace_image(self,root,flow):
        info=flow[2]['images']['dinner']
        raw=b'\xff\xd8\xffnew-dinner'
        path=root/'new-dinner.jpg';path.write_bytes(raw)
        sha=hashlib.sha256(raw).hexdigest()
        info.update(path=str(path),size=len(raw),sha256=sha,drive={'verified':True,'sha256':sha})

    def test_only_new_dinner_is_registered_and_old_unknown_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);flow=self.setup_flow(root)
            with self.assertRaises(OutcomeUnknown):self.invoke(root,flow)
            self.replace_image(root,flow)
            result=self.invoke(root,flow,True)
            self.assertEqual(result['state'],'posts_reviewed')
            self.assertEqual([c.args[2] for c in flow[3].register.call_args_list],['breakfast','lunch','dinner','dinner'])
            self.assertEqual(flow[4].review_meal.call_count,3)
            with flow[0].transaction() as db:
                old=db.execute("SELECT state FROM preparation_steps WHERE step='meal_register:dinner'").fetchone()[0]
                self.assertEqual(old,'outcome_unknown')
                self.assertEqual(db.execute("SELECT phase FROM mina_requests WHERE request_key=?",(flow[6][0],)).fetchone()[0],'held')
            with self.assertRaises(ValueError):self.invoke(root,flow,True)
            self.assertEqual(flow[3].register.call_count,4)

    def test_same_image_and_unknown_or_saved_lookup_never_create_new_request(self):
        for mode in ('same_image','processing','saved','unknown'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);flow=self.setup_flow(root)
                with self.assertRaises(OutcomeUnknown):self.invoke(root,flow)
                if mode!='same_image':
                    self.replace_image(root,flow)
                    original=flow[3].request_state.side_effect
                    def changed(key,operation):
                        result=original(key,operation)
                        if key in flow[6]:
                            result['persistence']='saved' if mode=='saved' else 'unknown'
                            result['status']='processing' if mode=='processing' else 'failed'
                        return result
                    flow[3].request_state.side_effect=changed
                with self.assertRaises(ValueError):self.invoke(root,flow,True)
                self.assertEqual(flow[3].register.call_count,3)
