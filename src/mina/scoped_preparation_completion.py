"""準備専用の限定実行。SNS予約・公開をせず、8候補のDiscord送付までを一度だけ進める。"""
import json


def prepare_once(flow):
    """要求や生成の既存記録があれば、新しいP・課金を開始せず照会へ戻す。"""
    if (flow.journal.rows('requests') or flow.journal.rows('calls','AND call_key NOT LIKE ?',
            (flow.journal.scope[3]+':pregrant:%',))):
        raise ValueError('scoped_rescue_lookup_only')
    flow.prepare_candidate();value=flow.decide()
    if value.get('request',{}).get('persistence')!='saved':raise ValueError('scoped_rescue_saved_case_unconfirmed')
    return value


def notify_once(flow,completed,client):
    """入口・カード準備を含む失敗時に、当日未着手行だけを必ず失効する。"""
    try:return _notify_once(flow,completed,client)
    except BaseException:
        with flow.store.transaction() as db:
            for table in ('notices','instagram_notices'):
                db.execute('UPDATE '+table+" SET state='obsolete' WHERE target_date=? AND state='pending' AND kind='approval'",(flow.target,))
        raise


def _notify_once(flow,completed,client):
    """送信開始を永続化し、成否不明のDiscord行を再送しない。旧承認でSNSを動かさない。"""
    from .instagram_store import InstagramStore
    from .replacement_bundle import notice_allowed
    from .scoped_preparation import locked
    store,target=flow.store,flow.target
    if len(completed['x'])!=4 or len(completed['instagram'])!=4:raise ValueError('scoped_rescue_bundle_changed')
    flow.checkpoint(saved=True)
    with store.transaction() as db:
        if not locked(db,target):raise ValueError('scoped_preparation_marker_invalid')
    store.issue_cards(target,flow.clock());ig=InstagramStore(store.path)
    for row in ig.rows(target):
        if row['state']=='approval_pending':ig.queue_approval(row)
    for table,channel in (('notices','x'),('instagram_notices','instagram')):
        while True:
            flow.checkpoint(saved=True)
            with store.transaction() as db:
                flow.checkpoint(db,saved=True)
                records=db.execute('SELECT * FROM '+table+" WHERE target_date=? AND state='pending' AND kind='approval' ORDER BY id",(target,)).fetchall()
                row=next((r for r in records if notice_allowed(db,r,channel)),None)
                if not row:break
                payload=json.loads(row['payload'])
                from .scoped_preparation import KEY,approval_notice
                approval_notice(payload,{KEY:True})
                db.execute('UPDATE '+table+" SET state='sending',payload=? WHERE id=? AND state='pending'",(json.dumps(payload,ensure_ascii=False),row['id']))
            attempted=False
            try:
                flow.checkpoint(saved=True);attempted=True;client.send(payload)
            except BaseException:
                with store.transaction() as db:db.execute('UPDATE '+table+" SET state=? WHERE id=? AND state='sending'",('outcome_unknown' if attempted else 'obsolete',row['id']))
                raise
            with store.transaction() as db:db.execute('UPDATE '+table+" SET state='sent' WHERE id=? AND state='sending'",(row['id'],))
    with store.transaction() as db:
        counts={channel:dict(db.execute('SELECT state,count(*) AS n FROM '+table+" WHERE target_date=? AND kind='approval' GROUP BY state",(target,)).fetchall()) for table,channel in (('notices','x'),('instagram_notices','instagram'))}
    if counts!={'x':{'sent':4},'instagram':{'sent':4}}:raise ValueError('scoped_rescue_reconciliation_required')
    return {'counts':counts,'sns_reservations':0,'sns_publications':0,'publication_hold':True}
