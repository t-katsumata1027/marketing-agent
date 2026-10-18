"""未送信の保留枠だけを、同じ内容の新しい時刻承認へ移す。"""

from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json

from .domain import JST, SLOTS, instant, slot_time
from .instagram_domain import InstagramCandidate, sha


def base_source(candidate):
    return {k: v for k, v in candidate.source.items() if k != 'recovery'}


def recovery_metadata(candidate):
    value = candidate.source.get('recovery')
    if not isinstance(value, dict) or set(value) != {'until', 'original_digest', 'original_revision'}:
        raise ValueError('復旧承認の時刻・旧版が不正です')
    sha(value['original_digest'])
    if type(value['original_revision']) is not int or value['original_revision'] < 1:
        raise ValueError('復旧元の版が不正です')
    until = instant(datetime.fromisoformat(value['until']))
    slots = list(SLOTS)
    index = slots.index(candidate.slot)
    end = (slot_time(candidate.scheduled_at.astimezone(JST).date(), slots[index + 1]) if index < 3 else
           datetime.combine(candidate.scheduled_at.astimezone(JST).date() + timedelta(days=1),
                            datetime.min.time(), JST))
    if not candidate.scheduled_at < until <= end:
        raise ValueError('復旧期限が次の枠または対象日を越えています')
    return value, until


def publication_window(store, row, candidate):
    """通常の承認時刻は変えず、復旧は旧版の保存証跡と照合する。"""
    if candidate.digest != row['digest']:
        raise ValueError('公開対象の版が変わっています')
    if 'recovery' not in candidate.source:
        if row['scheduled'] != candidate.scheduled_at.timestamp():
            raise ValueError('通常投稿の時刻が変わっています')
        return candidate.scheduled_at, None
    metadata, until = recovery_metadata(candidate)
    with store.transaction() as db:
        proof = db.execute('SELECT * FROM instagram_recoveries WHERE target_date=? AND slot=? AND revision=? AND digest=?',
                           (candidate.target_date, candidate.slot, row['revision'], candidate.digest)).fetchone()
    if not proof or proof['until'] != until.timestamp():
        raise ValueError('復旧元の保存証跡がありません')
    old = json.loads(proof['original_row'])
    original = InstagramCandidate(**json.loads(old['payload']))
    if (old['state'] != 'held' or original.digest != old['digest'] or
            old['revision'] != metadata['original_revision'] or old['digest'] != metadata['original_digest'] or
            row['revision'] != old['revision'] + 1 or asdict(replace(candidate, source=base_source(candidate))) != asdict(original)):
        raise ValueError('復旧画像・本文・正本が旧版と一致しません')
    if row['approved'] is None:
        if row['state'] != 'approval_pending' or row['scheduled'] != until.timestamp():
            raise ValueError('復旧の承認待ち時刻が不正です')
    elif (not row['actor'] or row['scheduled'] != row['approved'] or
          not original.scheduled_at.timestamp() <= row['approved'] < until.timestamp()):
        raise ValueError('復旧投稿の新しい時刻承認がありません')
    return datetime.fromtimestamp(row['scheduled'], JST), until


def prepare_recovery(store, row, now, until, hosts, verify_source, verify_media):
    """実体の再照合後、未送信・未変更の保留一枠だけを原子的に新承認へ移す。"""
    now, until = instant(now), instant(until)
    original = InstagramCandidate(**json.loads(row['payload']))
    original.validate(now, hosts, require_future=False)
    if (row['state'] != 'held' or original.digest != row['digest'] or 'recovery' in original.source or
            original.target_date != now.astimezone(JST).date().isoformat() or
            not original.scheduled_at <= now < until <= now + timedelta(hours=1)):
        raise ValueError('当日・未送信の保留枠だけを1時間以内の復旧承認へ移せます')
    candidate = replace(original, source={**original.source, 'recovery': {
        'until': until.isoformat(), 'original_digest': row['digest'], 'original_revision': row['revision']}})
    recovery_metadata(candidate)
    verify_source(original)
    for asset in original.assets:
        verify_media(asset)
    with store.transaction() as db:
        actual = db.execute('SELECT * FROM instagram_candidates WHERE target_date=? AND slot=?',
                            (row['target_date'], row['slot'])).fetchone()
        if (not actual or dict(actual) != row or
                db.execute('SELECT 1 FROM instagram_attempts WHERE target_date=? AND slot=?',
                           (row['target_date'], row['slot'])).fetchone()):
            raise ValueError('送信開始済み・変更済みの候補は復旧できません')
        db.execute('''CREATE TABLE IF NOT EXISTS instagram_recoveries (
            target_date TEXT NOT NULL,slot TEXT NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL,
            until REAL NOT NULL,original_row TEXT NOT NULL,created REAL NOT NULL,
            PRIMARY KEY(target_date,slot,revision))''')
        db.execute('INSERT INTO instagram_recoveries VALUES(?,?,?,?,?,?,?)',
                   (row['target_date'], row['slot'], row['revision'] + 1, candidate.digest, until.timestamp(),
                    json.dumps(row, ensure_ascii=False), now.timestamp()))
        db.execute('''UPDATE instagram_candidates SET revision=revision+1,payload=?,digest=?,scheduled=?,
            state='approval_pending',actor=NULL,approved=NULL,nonce_hash=NULL,dispatch_started=NULL,reason=NULL
            WHERE target_date=? AND slot=?''',
                   (json.dumps(asdict(candidate), ensure_ascii=False), candidate.digest, until.timestamp(),
                    row['target_date'], row['slot']))
        db.execute("UPDATE instagram_notices SET state='obsolete' WHERE target_date=? AND slot=? AND state='pending'",
                   (row['target_date'], row['slot']))
    return next(r for r in store.rows(row['target_date']) if r['slot'] == row['slot'])
