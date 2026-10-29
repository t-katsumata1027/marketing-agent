"""本人承認済みの夕食・日次だけを一度昇格し、ボタン付き通常通知へ渡す。"""
import argparse,json,os
from dataclasses import asdict
from datetime import UTC,datetime
from pathlib import Path
from .post_promotion import VERSION,KEY,TARGET,SLOTS,register,new_xs,verify_all,stage,guard_source
from .current_day_cli import config,protected_json,scoped_meal
from .preparation import fingerprint

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);a=p.parse_args();clock=lambda:datetime.now(UTC)
    if os.getenv('MINA_POST_PROMOTION_ENABLED','false').lower()!='true':raise ValueError('投稿候補の昇格は無効です')
    settings,check=config(a.config,Path(__file__).resolve().parents[2],version=VERSION,extras=('checkpoint',));snapshot,sha=protected_json(settings['checkpoint']['path'],root_owned=True)
    if sha!=settings['checkpoint']['sha256'] or fingerprint(snapshot)!=settings['approval']['source_digest']:raise ValueError('昇格原本のSHAが不一致です')
    from .clients import require_live,XClient
    from .store import Store
    from .instagram_store import InstagramStore
    from .automatic_history import refresh_history
    from .readiness import read_json
    require_live();store=Store(settings['database']);ig_store=InstagramStore(settings['database'])
    history=Path(a.config).parent.parent/'data/current-history.json'
    refresh_history(store.path,TARGET,history,read_json(Path(os.getenv('MINA_HISTORY_BOOTSTRAP','/var/lib/mina-agent/history-bootstrap.json'))),read_json(Path(os.getenv('MINA_CUTOVER_FILE','/var/lib/mina-agent/cutover.json'))),XClient(),clock)
    from .current_day_checkpoint import file_record
    history_record=file_record(history)
    check();_,config_sha=protected_json(a.config,root_owned=True);register(store,settings,snapshot,a.config,config_sha,clock)
    with store.transaction() as db:
        r=db.execute('SELECT document FROM post_promotions WHERE target_date=?',(TARGET,)).fetchone();d=json.loads(r[0]);d['fresh_history']=history_record;db.execute('UPDATE post_promotions SET document=? WHERE target_date=?',(json.dumps(d),TARGET))
    from .drive import DriveClient
    from .instagram_media import host_asset,HOSTS
    drive=DriveClient.from_env()
    def guard():
        from .replacement_client import timestamp
        check()
        if clock()>=timestamp(settings['approval']['not_after']):raise ValueError('候補昇格の期限を超過しました')
        with store.transaction() as db:guard_source(db,snapshot)
    drive.before_media_write=guard
    hosted={}
    for row in snapshot['cards']:
        if row['platform']=='instagram' and row['slot'] in SLOTS:
            hosted[row['slot']]=[]
            for asset in json.loads(row['payload'])['assets']:
                guard();hosted[row['slot']].append(host_asset(asset,drive,store,allow_public=True))
    from .post_promotion_external import ensure_external
    external=ensure_external(store,TARGET,'stage',clock)
    from .instagram_publisher import instagram_channel
    from .instagram_buffer_client import InstagramBufferClient
    channel=instagram_channel(InstagramBufferClient(store))
    if channel['id']!=settings['channels']['instagram']:raise ValueError('Instagram配信先が承認と不一致です')
    xs=new_xs(snapshot,settings['approval']['code_commit'],settings['approval']['promotion_uuid'])
    from .ogp import capture_ogp
    proof=verify_all(snapshot,xs,scoped_meal(),settings['host'],capture_ogp,clock)
    from .instagram_domain import InstagramCandidate
    igs=[]
    for slot in SLOTS:
        original=json.loads(next(r['payload'] for r in snapshot['cards'] if (r['platform'],r['slot'])==('instagram',slot)))
        igs.append(InstagramCandidate(TARGET,slot,channel['id'],channel['serviceId'],'post' if slot=='daily' else 'story',original['body'],hosted[slot],{'bundle_sha256':original['source']['bundle_sha256'],'state_version':proof['state_version'],'x_digests':{s:c.digest for s,c in xs.items()},KEY:settings['approval']['promotion_uuid']}))
    guard();stage(store,settings,snapshot,xs,igs,proof,clock)
    cards=store.issue_cards(TARGET,clock(),slots=SLOTS)
    for row in ig_store.rows(TARGET):ig_store.queue_approval(row)
    from .runtime import send_notifications
    from .clients import DiscordClient
    discord=DiscordClient(os.environ['DISCORD_BOT_TOKEN'],settings['discord_channel'])
    x_sent=send_notifications(store,discord);ig_sent=ig_store.send_notices(discord)
    print(json.dumps({'target_date':TARGET,'approval_candidates':4,'x_cards_created':len(cards),'x_notifications_sent':x_sent,'instagram_notifications_sent':ig_sent,'published':0,'food_writes':0,'ai_calls':0,'media_count':sum(map(len,hosted.values())),'external_pages':len(external['pages'])}))

if __name__=='__main__':main()
