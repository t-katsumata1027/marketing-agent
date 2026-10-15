"""本人指定の翌朝食一件だけを、既存の承認・排他台帳からBufferへ送る。"""

import json
import os
import re
import time
from datetime import UTC, datetime

from .buffer_trial import BufferTrialClient, CREATE, ProbeError
from .domain import ACCOUNT, Candidate, instant

TARGET = '2026-10-16'


def arm(store, channel, now):
    """承認済みの日時・版・公開先を固定する。候補とDiscord承認は変更しない。"""
    if (channel.get('service') != 'twitter' or channel.get('name', '').lstrip('@').lower() != ACCOUNT.lower()
            or channel.get('isDisconnected') is not False or channel.get('isLocked') is not False
            or channel.get('isQueuePaused') is not False or not channel.get('id') or not channel.get('serviceId')):
        raise ProbeError('buffer_channel_not_ready')
    with store.transaction() as db:
        row = db.execute("SELECT * FROM candidates WHERE target_date=? AND slot='breakfast'", (TARGET,)).fetchone()
        if (not row or row['state'] != 'approved' or not row['actor'] or row['approved'] is None
                or row['scheduled'] <= instant(now).timestamp()):
            raise ProbeError('approved_future_breakfast_required')
        c = Candidate(**json.loads(row['payload']))
        if c.digest != row['digest'] or not c.approval_source or not c.publish_text:
            raise ProbeError('approved_m1_candidate_required')
        binding = (TARGET, 'breakfast', row['revision'], row['digest'], channel['id'], channel['serviceId'])
        previous = db.execute('SELECT * FROM buffer_publication WHERE singleton=1').fetchone()
        if previous:
            if tuple(previous[k] for k in ('target_date','slot','revision','digest','channel_id','service_id')) != binding:
                raise ProbeError('buffer_route_already_bound')
            return
        db.execute("INSERT INTO buffer_publication(singleton,target_date,slot,revision,digest,channel_id,service_id,state) VALUES(1,?,?,?,?,?,?,'armed')", binding)


def publication_client(store, row, default):
    """同じ枠の版不一致や停止時も、既存X経路へ退避させない。"""
    with store.transaction() as db:
        route = db.execute('SELECT * FROM buffer_publication WHERE target_date=? AND slot=?',
                           (row['target_date'],row['slot'])).fetchone()
    if route is None:
        return default
    if route['revision'] != row['revision'] or route['digest'] != row['digest'] or route['state'] != 'armed':
        raise ProbeError('buffer_route_binding_or_state_changed')
    return BufferPublisher(store, row, dict(route), BufferTrialClient(os.getenv('BUFFER_API_KEY', '')))


class BufferPublisher:
    """時刻到来後の直前照合を済ませて一回送信し、実XのIDだけを成功にする。"""

    def __init__(self, store, row, route, api, clock=None, timer=None, sleep=None):
        self.store, self.row, self.route, self.api = store, row, route, api
        self.clock = clock or (lambda: datetime.now(UTC))
        self.timer, self.sleep = timer or time.monotonic, sleep or time.sleep
        self.proof = None

    def verify_account(self, account):
        channel = self.api.channel()
        if (account != ACCOUNT or channel['id'] != self.route['channel_id']
                or channel['serviceId'] != self.route['service_id'] or channel.get('isQueuePaused') is not False):
            raise ProbeError('buffer_live_target_unavailable')

    def validate(self, post, text):
        if (not isinstance(post, dict) or not post.get('id') or post.get('text') != text
                or post.get('channel') != {'id':self.route['channel_id'],'service':'twitter'}):
            raise ProbeError('buffer_post_binding_changed')
        if post.get('status') != 'sent':
            if post.get('status') not in ('scheduled','sending') or post.get('sentAt') or post.get('externalLink'):
                raise ProbeError('buffer_delivery_unconfirmed')
            return None
        match = re.fullmatch(r'https://(?:www\.)?(?:x\.com|twitter\.com)/' + ACCOUNT + r'/status/([0-9]+)', post.get('externalLink') or '', re.I)
        if not match or not post.get('sentAt'):
            raise ProbeError('buffer_native_x_result_missing')
        sent = instant(datetime.fromisoformat(post['sentAt'].replace('Z','+00:00')))
        started = instant(datetime.fromisoformat(self.proof['started_at']))
        expires = instant(datetime.fromisoformat(self.proof['expires_at']))
        if not 0 <= (sent-started).total_seconds() <= 120 or sent >= expires:
            raise ProbeError('buffer_delivery_freshness_unconfirmed')
        return match[1]

    def publish(self, text):
        from .clients import require_live
        require_live()
        if os.getenv('MINA_PUBLISH_ENABLED','false').lower() != 'true':
            raise ProbeError('buffer_publication_disabled')
        c = Candidate(**json.loads(self.row['payload']))
        if text != c.content or not isinstance(self.proof, dict):
            raise ProbeError('buffer_approved_text_or_proof_missing')
        # 外部へ送る直前にも単調時計・期限を再検査する。API受付をX公開成功とは扱わない。
        now = instant(self.clock())
        if (not 0 <= (now-c.scheduled_at).total_seconds() <= 120
                or not 0 <= self.timer()-self.proof['started_monotonic'] <= 120
                or now >= instant(datetime.fromisoformat(self.proof['expires_at']))):
            raise ProbeError('buffer_preflight_expired')
        request = {'channelId':self.route['channel_id'],'text':text,'assets':[],
                   'schedulingType':'automatic','mode':'shareNow','saveToDraft':False,'needsApproval':False}
        with self.store.transaction() as db:
            row = db.execute('SELECT * FROM candidates WHERE target_date=? AND slot=?',
                             (self.row['target_date'],self.row['slot'])).fetchone()
            if (not row or row['state'] != 'dispatching' or row['revision'] != self.row['revision']
                    or row['digest'] != c.digest or row['approved'] is None or not row['actor']):
                raise ProbeError('buffer_claim_not_owned')
            changed = db.execute("UPDATE buffer_publication SET state='attempting',request_json=?,proof_json=?,attempted=? WHERE singleton=1 AND state='armed' AND digest=? AND revision=?",
                (json.dumps(request,ensure_ascii=False),json.dumps(self.proof),now.timestamp(),c.digest,row['revision']))
            if changed.rowcount != 1:
                raise ProbeError('buffer_attempt_already_started')
        # 同じ要求・新しい要求のどちらも自動再送しない。応答喪失でも上の記録を保持する。
        if (not 0 <= self.timer()-self.proof['started_monotonic'] <= 120
                or not 0 <= (instant(self.clock())-c.scheduled_at).total_seconds() <= 120
                or instant(self.clock()) >= instant(datetime.fromisoformat(self.proof['expires_at']))):
            raise ProbeError('buffer_preflight_expired')
        result = self.api._request(CREATE, {'input':request}).get('createPost')
        if not isinstance(result,dict) or result.get('__typename') != 'PostActionSuccess':
            raise ProbeError('buffer_submission_unconfirmed')
        post = result.get('post')
        if not isinstance(post,dict) or not isinstance(post.get('id'),str) or not post['id']:
            raise ProbeError('buffer_submission_id_unknown')
        with self.store.transaction() as db:
            db.execute("UPDATE buffer_publication SET state='accepted',post_id=?,response_json=? WHERE singleton=1",
                       (post['id'],json.dumps(post,ensure_ascii=False)))
        # 送信は一度だけ。最大約60秒の読取確認後、不明のままなら停止する。
        for attempt in range(7):
            x_id = self.validate(post,text)
            if x_id:
                with self.store.transaction() as db:
                    db.execute("UPDATE buffer_publication SET state='sent',response_json=? WHERE singleton=1",(json.dumps(post,ensure_ascii=False),))
                return x_id
            if attempt == 6 or self.timer()-self.proof['started_monotonic'] > 90:
                break
            self.sleep(10)
            expected_id = post['id']
            post = self.api.post(expected_id)
            if not isinstance(post,dict) or post.get('id') != expected_id:
                raise ProbeError('buffer_delivery_id_changed')
        raise ProbeError('buffer_delivery_outcome_unknown')


def recover_publication(store, now, api_factory=None):
    """再起動で送信中が残った場合は読取だけで照合し、送信を再開しない。"""
    with store.transaction() as db:
        route = db.execute('SELECT * FROM buffer_publication WHERE singleton=1').fetchone()
        if not route or route['state'] == 'armed':
            return
        row = db.execute('SELECT * FROM candidates WHERE target_date=? AND slot=?',
                         (route['target_date'],route['slot'])).fetchone()
    if not row or row['state'] != 'dispatching':
        return
    # 別の通常巡回がまだ送信確認中の場合には介入しない。
    if route['attempted'] is not None and instant(now).timestamp()-route['attempted'] < 180:
        return
    x_id = None
    try:
        if route['post_id'] and route['proof_json']:
            api = api_factory() if api_factory else BufferTrialClient(os.getenv('BUFFER_API_KEY',''))
            publisher = BufferPublisher(store,dict(row),dict(route),api)
            publisher.proof = json.loads(route['proof_json'])
            post = api.post(route['post_id'])
            if post.get('id') != route['post_id']:
                raise ProbeError('buffer_recovery_id_changed')
            x_id = publisher.validate(post,Candidate(**json.loads(row['payload'])).content)
    except Exception:
        pass
    store.finish(dict(row),x_id,now)
