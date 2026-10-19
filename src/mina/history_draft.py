"""公開台帳から履歴確認の下書きを作る。外部履歴・旧予約の確認済みにはしない。"""

import json
import os
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from .domain import ACCOUNT, Candidate, SLOTS


def export_history_draft(database, target, output):
    """送信済みIDと候補ハッシュを照合し、人の入力負担だけを減らす。"""
    day = date.fromisoformat(target)
    if day.isoformat() != target:
        raise ValueError('対象日が不正です')
    source, output = Path(database), Path(output)
    if source.is_symlink() or not source.is_file() or any(p.is_symlink() for p in (output,*output.parents)) or output.exists():
        raise ValueError('履歴台帳・出力先が不正です')
    first, last = (day-timedelta(days=14)).isoformat(), (day-timedelta(days=1)).isoformat()
    try:
        with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            rows = db.execute("SELECT target_date,slot,payload,digest,x_id FROM candidates WHERE state='published' AND target_date BETWEEN ? AND ? ORDER BY target_date,scheduled LIMIT 101",(first,last)).fetchall()
            unresolved = db.execute("SELECT COUNT(*) FROM candidates WHERE state IN ('dispatching','outcome_unknown') AND target_date BETWEEN ? AND ?",(first,last)).fetchone()[0]
        if len(rows)>100:
            raise ValueError()
        entries=[]
        for recorded, slot, raw, digest, x_id in rows:
            c=Candidate(**json.loads(raw))
            if c.target_date != recorded or c.slot != slot or slot not in SLOTS or c.account != ACCOUNT or c.digest != digest or not isinstance(x_id,str) or not x_id.isdigit():
                raise ValueError()
            entries.append({'date':recorded,'menu':c.menu,'comment':c.comment,'x_id':x_id})
    except (sqlite3.Error,ValueError,TypeError,KeyError):
        raise ValueError('公開台帳の証跡を確認できません') from None
    draft={'account':ACCOUNT,'target_date':target,'checked_at':'','reviewer':'',
           'history_source':'Marketing Agent公開台帳（X履歴は未照合）','schedule_source':'',
           'history_complete':False,'legacy_schedule_clear':False,'history_from':first,'history_to':last,
           'entries':entries,'unresolved_local_count':unresolved,
           'review_required':['外部X投稿・旧運用を含む14日履歴との照合','旧予約一覧の確認',
                              '確認日時・確認者・参照元と確認済み判定の入力']}
    output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as file:
        json.dump(draft,file,ensure_ascii=False,indent=2)
    return {'history_draft_created':True,'entry_count':len(entries),'unresolved_local_count':unresolved,
            'history_verified':False,'external_calls_performed':False}
