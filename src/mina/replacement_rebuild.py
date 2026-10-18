"""現在正本の4投稿とInstagram8画像を版別に再構成する。呼出前に費用を拘束する。"""
import copy
import hashlib
import json
from pathlib import Path
from dataclasses import asdict
from .replacement_contract import canonical,digest,source_digest
from .replacement_postreview import review_posts
from .replacement_artifacts import write_version,adopt
from .readiness import build_candidates
from .domain import Candidate,SLOTS

class ReplacementRebuilder:
    def __init__(self,store,meal,ai,drive,daily_directory,instagram_directory,evidence,commit,host,clock,*,api,ports_directory,instagram_enabled):
        self.store,self.meal,self.ai,self.drive=store,meal,ai,drive
        self.daily,self.instagram=Path(daily_directory),Path(instagram_directory)
        self.evidence,self.commit,self.host,self.clock=evidence,commit,host,clock
        self.api,self.ports_directory,self.instagram_enabled=api,Path(ports_directory),instagram_enabled

    def __call__(self,e,response,p,material):
        from .instagram_source import load_bundle
        from .instagram_render import render_bundle
        from .instagram_runtime import save_json,caption
        from .instagram_domain import InstagramCandidate,LAYOUT_VERSION
        from .instagram_media import host_asset,HOSTS
        from .instagram_publisher import instagram_channel
        target=e['target_date'];base=self.daily/target;version=base/('replacement-'+e['prepare_uuid'])
        draft=json.loads((base/'draft.json').read_text(encoding='utf-8-sig'));draft=copy.deepcopy(draft);draft['meals'][e['slot']]=json.loads(material['plan'])
        saved=copy.deepcopy(json.loads((base/'drive.json').read_text(encoding='utf-8-sig')))
        drive_proof=json.loads((self.ports_directory/(e['prepare_uuid']+'-drive.json')).read_text(encoding='utf-8'))
        raw_images={};files={}
        for slot in ('breakfast','lunch','dinner'):
            raw=material['image'] if slot==e['slot'] else Path(saved['images'][slot]['path']).read_bytes()
            raw_images[slot]=raw;info=saved['images'][slot]
            expected=next(m for m in p['meals'] if m['expected']['target_slot']==slot)['image_sha256']
            if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('replacement_source_image_changed')
            if slot==e['slot']:
                info.update(sha256=expected,size=len(raw),drive=drive_proof['new'],image_review={'status':'image_acceptable','reason_codes':[]})
            info['path']=str((version/(slot+'.jpg')).resolve());files[slot+'.jpg']=raw
            name=info['drive'].get('name',target+'-'+slot+'.jpg')
            self.drive.verify(info['drive']['id'],raw,name)
        files['draft.json']=canonical(draft);files['drive-final.json']=canonical(saved)
        if version.exists():
            # 前回の生成途中は時刻や存在だけで採用せず、全バイト一致を確認する。
            for name,raw in files.items():
                if (version/name).is_symlink() or (version/name).read_bytes()!=raw:raise ValueError('replacement_artifact_changed')
            manifest_sha=hashlib.sha256((version/'manifest.json').read_bytes()).hexdigest()
        else:manifest_sha=write_version(version,files)
        from .ogp import capture_ogp
        from .m1_contract import validate_state,completed_request
        from .replacement_contract import Contract
        from .replacement_client import ReplacementReadClient
        from .request_ledger import RequestLedger
        state=self.meal.day_state(target);state_version=validate_state(state,target,self.clock())
        if state.get('daily_share') is None:
            # Cが作らなかった日次だけ、既存M1の台帳から一度作る。要求不明は再実行しない。
            import uuid
            with self.store.transaction() as db:
                db.execute('CREATE TABLE IF NOT EXISTS replacement_daily_requests (target_date TEXT PRIMARY KEY,request_key TEXT,payload TEXT)')
                old=db.execute('SELECT * FROM replacement_daily_requests WHERE target_date=?',(target,)).fetchone()
                if old:key=old['request_key'];payload=json.loads(old['payload'])
                else:
                    key=str(uuid.uuid4());payload={'contract_version':'mina-write-v1','date':target,'comment':draft['daily_comment'],'expected_meal_count':3,'expected_state_version':state_version}
                    db.execute('INSERT INTO replacement_daily_requests VALUES(?,?,?)',(target,key,json.dumps(payload)))
            ledger=RequestLedger(self.store);ledger.prepare(key,'daily_share',target,'daily',payload)
            record=ledger.restore(key)
            if record['phase']=='prepared':
                from .publication_hold import replacement_daily_scope
                with replacement_daily_scope(target,e['owner'],e['epoch']):
                    ledger.execute_once(key,self.meal,lambda:self.meal.json_write('/api/internal/mina/daily-share',payload,request_key=key))
            lookup=self.meal.request_state(key,'daily_share');completed_request(lookup,key,'daily_share',lookup['current_result']);ledger.record(key,'complete',{'persistence':'saved','stage':'completed'})
        contract=Contract(self.clock);reader=ReplacementReadClient(self.meal.base,self.meal.token,decoder=contract.decode,clock=self.clock)
        response=reader.request(e['commit_uuid'],'meal_replace')
        reviewed,proof=review_posts(self.store,self.meal,self.ai,capture_ogp,
            target,e['commit_uuid'],response,draft,raw_images,self.host,self.clock)
        xs=[Candidate(**v) for v in build_candidates(reviewed,self.evidence,self.commit,self.host,self.clock())]
        igs=[]
        if self.instagram_enabled:
            # 新X候補だけの一時SQLiteで描画する。本番の候補や停止行を先行更新しない。
            from .store import Store
            shadow_path=version/'render-snapshot.sqlite3'
            if shadow_path.exists():raise ValueError('replacement_rebuild_lookup_required')
            shadow=Store(shadow_path);shadow.save_many(xs,self.host,self.clock())
            state=self.meal.day_state(target);bundle=load_bundle(shadow_path,self.daily,target,state,self.clock(),artifact_directory=version)
            destination=self.instagram/target/LAYOUT_VERSION/bundle['bundle_sha256'];destination.mkdir(parents=True,exist_ok=True,mode=0o700)
            save_json(destination/'bundle.json',bundle);manifest=render_bundle(bundle,destination);channel=instagram_channel(self.api)
            for slot in SLOTS:
                assets=[host_asset(a,self.drive,self.store,allow_public=True) for a in manifest['assets'][slot]]
                c=InstagramCandidate(target,slot,channel['id'],channel['serviceId'],'post' if slot=='daily' else 'story',caption(bundle,slot),assets,
                    {k:bundle[k] for k in ('bundle_sha256','state_version','x_digests')})
                c.validate(self.clock(),HOSTS);igs.append(c)
            save_json(destination/'candidates.json',[asdict(c) for c in igs])
        adopt(self.store,target,e['owner'],e['epoch'],self.daily,version.name,manifest_sha)
        final=contract.preconditions(reader.preconditions(target),target)
        proof['source_digest']=source_digest(final)
        return xs,igs,proof

    def verify_readonly(self,target,x_candidates=None,ig_candidates=None):
        """承認後の停止解除用。生成・解析・書込みをせず、現在正本と実画像を再照合する。"""
        from .domain import Candidate
        from .m1_contract import verify_source
        from .ogp import capture_ogp
        from .instagram_runtime import InstagramRuntime,caption
        from .instagram_source import load_bundle
        from .replacement_artifacts import resolve
        from .instagram_domain import InstagramCandidate
        from .instagram_media import verify_public
        from .replacement_contract import Contract,stamp
        from .replacement_client import ReplacementReadClient
        started=self.clock();contract=Contract(self.clock)
        reader=ReplacementReadClient(self.meal.base,self.meal.token,decoder=contract.decode,clock=self.clock)
        p=contract.preconditions(reader.preconditions(target),target)
        version=resolve(self.daily/target);saved=json.loads((version/'drive-final.json').read_text(encoding='utf-8'))
        for meal in p['meals']:
            info=saved['images'][meal['expected']['target_slot']];raw=Path(info['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=meal['image_sha256']:raise ValueError('replacement_source_image_changed')
        xs=x_candidates or [Candidate(**json.loads(r['payload'])) for r in self.store.list_day(target)]
        for c in xs:verify_source(c,self.meal,self.host,capture_ogp,clock=self.clock)
        if self.instagram_enabled:
            if ig_candidates is None:
                with self.store.transaction() as db:igs=[InstagramCandidate(**json.loads(r['payload'])) for r in db.execute('SELECT payload FROM instagram_candidates WHERE target_date=?',(target,))]
            else:igs=ig_candidates
            state=self.meal.day_state(target)
            bundle=load_bundle(version/'render-snapshot.sqlite3',self.daily,target,state,self.clock(),artifact_directory=version)
            for candidate in igs:
                if candidate.source!={k:bundle[k] for k in ('bundle_sha256','state_version','x_digests')} or candidate.caption!=caption(bundle,candidate.slot):raise ValueError('replacement_source_changed')
                manifest=json.loads((self.instagram/target/candidate.layout_version/bundle['bundle_sha256']/'rendered.json').read_text(encoding='utf-8'))
                if [a['sha256'] for a in candidate.assets]!=[a['sha256'] for a in manifest['assets'][candidate.slot]]:raise ValueError('replacement_source_image_changed')
                for asset in candidate.assets:verify_public(asset)
        final=contract.preconditions(reader.preconditions(target),target)
        if source_digest(p)!=source_digest(final):raise ValueError('replacement_source_changed')
        proof={'source_digest':source_digest(final),'state_version':final['state_version'],'read_started_at':stamp(started),'verdict':'pass',
               'verification':'current-source-uuid-ogp-v1','checks':['current_state','uuid_results','original_bytes','ogp_bytes','independent_review']}
        return source_digest(final),proof
