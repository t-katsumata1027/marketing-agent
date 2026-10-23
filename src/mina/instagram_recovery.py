"""未送信の保留枠だけを、同じ内容の新しい時刻承認へ移す。"""

from dataclasses import asdict, replace
from datetime import datetime, timedelta
import hashlib
import json

from .domain import JST, SLOTS, instant, slot_time
from .instagram_domain import InstagramCandidate, sha

CHAT_ACTOR = 'chat:explicit_instagram_recovery'


def check_instruction(instruction, recorded_at, now, until):
    """本人の依頼本文を保持し、同じ依頼で承認期限を延長しない。"""
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction.encode()) > 4096:
        raise ValueError('本人の明示的な復旧依頼本文が必要です')
    recorded_at, now, until = instant(recorded_at), instant(now), instant(until)
    if not recorded_at <= now < until <= recorded_at + timedelta(minutes=15):
        raise ValueError('復旧依頼の記録から15分以内に限って公開します')
    return hashlib.sha256(instruction.encode()).hexdigest()


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
        instruction = None
        if row['actor'] == CHAT_ACTOR:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='instagram_recovery_instructions'").fetchone():
                raise ValueError('本人の復旧指示記録がありません')
            instruction = db.execute('SELECT * FROM instagram_recovery_instructions WHERE target_date=? AND slot=? AND revision=? AND digest=?',
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
    if row['actor'] == CHAT_ACTOR:
        if (not instruction or instruction['approved'] != row['approved'] or
                check_instruction(instruction['instruction'], datetime.fromtimestamp(instruction['recorded'], JST),
                                  datetime.fromtimestamp(row['approved'], JST), until) != instruction['instruction_sha256'] or
                not old['actor'] or old['approved'] is None):
            raise ValueError('元の内容承認と今回の復旧依頼が一致しません')
    return datetime.fromtimestamp(row['scheduled'], JST), until


def approve_recovery_instruction(store, row, now, instruction, recorded_at):
    """信頼された入口が受領した本人指示を、Discord操作と区別して承認記録へ残す。"""
    import os
    from .clients import require_live
    require_live()
    if os.getenv('MINA_INSTAGRAM_APPROVAL_ENABLED', 'false').lower() != 'true':
        raise ValueError('Instagram承認受付が無効です')
    c = InstagramCandidate(**json.loads(row['payload']))
    _, until = publication_window(store, row, c)
    if until is None or row['state'] != 'approval_pending':
        raise ValueError('未承認の復旧一枠だけを扱います')
    instruction_sha = check_instruction(instruction, recorded_at, now, until)
    with store.transaction() as db:
        actual = db.execute('SELECT * FROM instagram_candidates WHERE target_date=? AND slot=?',
                            (c.target_date, c.slot)).fetchone()
        proof = db.execute('SELECT original_row FROM instagram_recoveries WHERE target_date=? AND slot=? AND revision=?',
                           (c.target_date, c.slot, row['revision'])).fetchone()
        old = json.loads(proof['original_row']) if proof else {}
        if (not actual or dict(actual) != row or not old.get('actor') or old.get('approved') is None or
                db.execute('SELECT 1 FROM instagram_attempts WHERE target_date=? AND slot=?',
                           (c.target_date, c.slot)).fetchone()):
            raise ValueError('内容未承認・送信済み・変更済みの候補は依頼承認できません')
        db.execute('''CREATE TABLE IF NOT EXISTS instagram_recovery_instructions (
            target_date TEXT NOT NULL,slot TEXT NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL,
            instruction TEXT NOT NULL,instruction_sha256 TEXT NOT NULL,recorded REAL NOT NULL,approved REAL NOT NULL,
            PRIMARY KEY(target_date,slot,revision))''')
        approved = instant(now).timestamp()
        db.execute('INSERT INTO instagram_recovery_instructions VALUES(?,?,?,?,?,?,?,?)',
                   (c.target_date, c.slot, row['revision'], c.digest, instruction, instruction_sha,
                    instant(recorded_at).timestamp(), approved))
        db.execute("UPDATE instagram_candidates SET state='approved',actor=?,approved=?,scheduled=?,nonce_hash=NULL WHERE target_date=? AND slot=?",
                   (CHAT_ACTOR, approved, approved, c.target_date, c.slot))
        db.execute("UPDATE instagram_notices SET state='obsolete' WHERE target_date=? AND slot=? AND state='pending' AND kind='approval'",
                   (c.target_date, c.slot))
    return next(r for r in store.rows(c.target_date) if r['slot'] == c.slot)


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
