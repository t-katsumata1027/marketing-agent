"""承認を保持する接続照会の有限再試行。外部送信の再試行は扱わない。"""
import json
from datetime import UTC,datetime,timedelta
from .audit import OperationalFailure


def unchanged_unsent(db,row):
    current=db.execute('SELECT * FROM candidates WHERE target_date=? AND slot=?',(row['target_date'],row['slot'])).fetchone()
    route=db.execute('SELECT * FROM buffer_publication WHERE target_date=? AND slot=?',(row['target_date'],row['slot'])).fetchone()
    return bool(current and route and current['state']=='dispatching' and current['approved'] is not None and current['actor'] and
        current['revision']==row['revision']==route['revision'] and current['digest']==row['digest']==route['digest'] and
        route['state']=='armed' and all(route[key] is None for key in ('attempted','post_id','request_json','proof_json')))


def begin_connection(store,row,now):
    with store.transaction() as db:
        if not unchanged_unsent(db,row):raise OperationalFailure('binding_changed','connection')
        old=db.execute('SELECT * FROM publication_retries WHERE target_date=? AND slot=? AND revision=?',
                       (row['target_date'],row['slot'],row['revision'])).fetchone()
        if old and (old['digest']!=row['digest'] or old['attempts']>=3 or old['next_at']>now.timestamp()):
            raise OperationalFailure('retry_exhausted','connection')
        attempt=old['attempts']+1 if old else 1
        db.execute('INSERT INTO publication_retries VALUES(?,?,?,?,?,?) ON CONFLICT(target_date,slot,revision) DO UPDATE SET attempts=excluded.attempts,next_at=excluded.next_at',
            (row['target_date'],row['slot'],row['revision'],row['digest'],attempt,now.timestamp()))
        store.audit.emit('publication',row['target_date'],row['slot'],row['revision'],'connection','started',details={'attempt':attempt},now=now,db=db)


def defer_connection(store,row,now):
    """送信なしの証拠と同じ承認を再照合し、回数と次回時刻を原子的に記録する。"""
    with store.transaction() as db:
        if not unchanged_unsent(db,row):return False
        retry=db.execute('SELECT * FROM publication_retries WHERE target_date=? AND slot=? AND revision=?',
                         (row['target_date'],row['slot'],row['revision'])).fetchone()
        if not retry or retry['attempts']>=3 or now.timestamp()+15>row['scheduled']+120:return False
        next_at=now.timestamp()+15
        db.execute('UPDATE publication_retries SET next_at=? WHERE target_date=? AND slot=? AND revision=?',
                   (next_at,row['target_date'],row['slot'],row['revision']))
        db.execute("UPDATE candidates SET state='approved',dispatch_started=NULL WHERE target_date=? AND slot=? AND revision=?",(row['target_date'],row['slot'],row['revision']))
        store.audit.emit('publication',row['target_date'],row['slot'],row['revision'],'connection','retry_wait','retry_pending',
            {'attempt':retry['attempts'],'next_at':next_at,'before':'dispatching','after':'approved','write_started':False},now=now,db=db)
        return True
