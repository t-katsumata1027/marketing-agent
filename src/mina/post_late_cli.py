"""本人承認済みの旧日次二件だけを扱う、root封印構成の明示operator。"""
import argparse,hashlib,json,os,stat,tarfile,time
from datetime import datetime,UTC
from pathlib import Path
from . import post_late as late
from .current_day_cli import protected_json,scoped_meal
from .preparation import fingerprint
from .readiness import release_commit

def authority(path):
    document,sha=protected_json(path,root_owned=True)
    if set(document)!={'approval','old_manifest','references'}:raise ValueError('限定継続構成の形式が不正です')
    a=document['approval'];root=Path(__file__).resolve().parents[2]
    release_commit(root,a['commit'])
    binding=fingerprint({k:v for k,v in document.items() if k!='references'})
    if set(document['references'])!={'implementation_review','deployment_approval','execution_approval'}:raise ValueError('三つの承認原本が必要です')
    for role,ref in document['references'].items():
        value,digest=protected_json(ref['path'],root_owned=True)
        if digest!=ref['sha256'] or value!={'role':role,'contract_version':'mina-daily-late-v1','config_digest':binding,'verdict':'Go' if role=='implementation_review' else 'approved'}:raise ValueError('限定継続承認の原本が不一致です')
    manifest,msha=protected_json(document['old_manifest']['path'],root_owned=True)
    if msha!=document['old_manifest']['sha256'] or manifest['source_commit']!=a['old_commit']:raise ValueError('旧配布manifestが不一致です')
    archive=Path(document['old_manifest']['path']).parent/'source.tar'
    if archive.is_symlink() or hashlib.sha256(archive.read_bytes()).hexdigest()!=manifest['artifacts']['source.tar']['sha256']:raise ValueError('旧配布実体が不一致です')
    oldroot=Path(a['old_root']);release_commit(oldroot,a['old_commit'])
    with tarfile.open(archive) as tar:
        for name in manifest['files']:
            p=oldroot/name
            if p.is_symlink() or not p.is_file() or p.stat().st_uid!=0 or p.stat().st_mode&0o222 or p.read_bytes()!=tar.extractfile(name).read():raise ValueError('旧コード/資産の読取専用原本が不一致です')
    def check():
        if protected_json(path,root_owned=True)[1]!=sha:raise ValueError('限定継続構成が変わりました')
        for role,ref in document['references'].items():
            if protected_json(ref['path'],root_owned=True)[1]!=ref['sha256']:raise ValueError('限定継続承認が変わりました')
        release_commit(root,a['commit'])
    check();return document,check

def run(path):
    from .store import Store
    from .instagram_store import InstagramStore
    from .buffer_publisher import production_client
    from .buffer_budget import using,release
    from .current_day_cli import config
    from .post_promotion import VERSION
    from .ogp import capture_ogp
    from .instagram_media import verify_public
    from .clients import DiscordClient
    document,check=authority(path);a=document['approval'];store=Store('/var/lib/mina-agent/mina.sqlite3');ig=InstagramStore(store.path)
    if os.getenv('MINA_DAILY_LATE_ENABLED','false').lower()!='true':raise ValueError('限定日次は無効です')
    if datetime.now(UTC)>=late.END:raise ValueError('限定日次の絶対期限を超過しました')
    settings,oldcheck=config(a['config_path'],Path(a['old_root']),version=VERSION,extras=('checkpoint',))
    checkpoint,sha=protected_json(settings['checkpoint']['path'],root_owned=True)
    if sha!=settings['checkpoint']['sha256']:raise ValueError('旧checkpointの実体が不一致です')
    meal=scoped_meal();api=production_client(store)
    with store.transaction() as db:
        if late.row(db):raise ValueError('限定日次operatorを再起動しません。原IDを照会してください')
        old=late.snapshot(db)
    if protected_json(a['config_path'],root_owned=True)[1]!=a['config_sha256'] or settings['approval']['promotion_uuid']!=old['post_promotions'][0]['promotion_uuid'] or fingerprint(checkpoint)!=old['post_promotions'][0]['source_digest']:raise ValueError('旧構成/原本/権限の束縛が不一致です')
    def read(phase,inputs=None):
        check();oldcheck();deadline=min(late.END.timestamp(),datetime.now(UTC).timestamp()+120)
        with store.transaction() as db:
            r=late.row(db)
            if r:deadline=min(deadline,r['deadline'])
        budget=late.refresh_budget(store,api,a['case_uuid'],phase,deadline)
        try:
            with using(budget):result=late.fresh(store,meal,api,capture_ogp,verify_public,lambda:datetime.now(UTC),inputs)
            check();oldcheck();return result
        finally:release(store,budget)
    proof=read('stage',(old,settings,checkpoint,oldcheck));check()
    deadline=late.stage(store,a,old,proof,lambda:datetime.now(UTC))
    store.issue_cards(late.TARGET,datetime.now(UTC),slots=('daily',))
    for r in ig.rows(late.TARGET):
        if r['slot']=='daily':ig.queue_approval(r)
    discord=DiscordClient(os.environ['DISCORD_BOT_TOKEN'],settings['discord_channel']);late.send_cards(store,discord)
    print(json.dumps({'stage':'cards_sent','count':2,'deadline':deadline.isoformat()}),flush=True)
    while datetime.now(UTC)<deadline:
        check()
        with store.transaction() as db:r=dict(late.row(db))
        if r['state']=='ready':break
        if r['state']!='approval_pending':raise ValueError('限定日次が停止しています')
        time.sleep(2)
    else:raise ValueError('二件の本人承認前に期限となりました')
    from .runtime import publish_due
    from .clients import XClient
    from .source_audit import verify_audited
    from .instagram_publisher import publish_once
    def x():
        check();return publish_due(store,XClient(),datetime.now(UTC),'www.log-eats.com',lambda c:verify_audited(c,meal,'www.log-eats.com',capture_ogp,store))
    def instagram():
        check();r=ig.claim_due(datetime.now(UTC))
        if not r or r['slot']!='daily':raise ValueError('限定Instagramの日次claimがありません')
        from .instagram_buffer_client import InstagramBufferClient
        from .instagram_media import HOSTS
        return publish_once(ig,r,lambda c:late.verify_candidate(store,c),HOSTS,client=InstagramBufferClient(ig))
    result=late.execute(store,lambda:read('publish'),x,instagram)
    print(json.dumps({'stage':result,'published':2,'dinner_reposts':0}),flush=True)
    from .runtime import send_notifications
    send_notifications(store,discord);ig.send_notices(discord)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='10/29日次二件だけの明示承認operator')
    parser.add_argument('--config',required=True);args=parser.parse_args();run(args.config)
