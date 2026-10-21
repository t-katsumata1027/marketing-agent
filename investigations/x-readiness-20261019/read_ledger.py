"""明日のX承認と稼働状態を、秘密や本文を出さず読取り専用で確認する。"""
import hashlib,json,pathlib,sqlite3,subprocess
from datetime import UTC,datetime

target='2026-10-19'
db=sqlite3.connect('file:/var/lib/mina-agent/mina.sqlite3?mode=ro',uri=True)
db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
def rows(sql,args=()):return [dict(r) for r in db.execute(sql,args)]
out={'observed_at':datetime.now(UTC).isoformat(),'target_date':target,'read_only':True}
out['production_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd='/opt/marketing-agent',text=True).strip()
out['services']={s:subprocess.run(['systemctl','is-active',s],capture_output=True,text=True).stdout.strip() for s in ('mina-agent.service','mina-gateway.service','mina-prepare.timer','mina-notify.timer')}
out['preparation']=rows('SELECT target_date,state,started FROM preparation WHERE target_date=?',(target,))
out['progress']=rows('SELECT step,status,reasons,updated FROM preparation_progress WHERE target_date=?',(target,))
out['candidates']=rows('SELECT slot,revision,state,reason,scheduled,approved,actor IS NOT NULL AS has_actor,dispatch_started,x_id IS NOT NULL AS has_publication_id FROM candidates WHERE target_date=? ORDER BY scheduled',(target,))
out['buffer_routes']=rows('SELECT slot,revision,state,post_id IS NOT NULL AS has_post_id,attempted FROM buffer_publication WHERE target_date=? ORDER BY slot',(target,))
out['route_bindings_match']=all(db.execute('SELECT 1 FROM candidates c JOIN buffer_publication b ON c.target_date=b.target_date AND c.slot=b.slot WHERE c.target_date=? AND c.slot=? AND c.revision=b.revision AND c.digest=b.digest',(target,r['slot'])).fetchone() is not None for r in out['buffer_routes'])
out['buffer_policy']=rows('SELECT date_from,review_until,state FROM buffer_policy')
out['notices']=rows('SELECT slot,revision,kind,state FROM notices WHERE target_date=? ORDER BY id',(target,))
out['outstanding_requests']=rows("SELECT slot,operation,phase FROM mina_requests WHERE target_date=? AND phase<>'complete'",(target,))
out['cycle_health']=rows('SELECT * FROM cycle_health')
out['active_day_holds']=rows('SELECT state FROM day_publication_holds WHERE target_date=?',(target,)) if 'day_publication_holds' in tables else []
out['step_results']=rows('SELECT step,state FROM preparation_steps WHERE target_date=? ORDER BY started',(target,))
out['target_hashes']={day:{table:hashlib.sha256(json.dumps([list(r) for r in db.execute('SELECT * FROM '+table+' WHERE target_date=? ORDER BY rowid',(day,))],ensure_ascii=False,default=str).encode()).hexdigest() for table in ('candidates','mina_requests','preparation','preparation_steps','buffer_publication','notices')} for day in ('2026-10-18',target)}
pid=subprocess.run(['systemctl','show','mina-agent.service','--property=MainPID','--value'],capture_output=True,text=True).stdout.strip()
out['runtime_flags']={}
if pid.isdigit() and pid!='0':
 env=dict(part.split(b'=',1) for part in pathlib.Path('/proc/'+pid+'/environ').read_bytes().split(b'\0') if b'=' in part)
 for name in ('MINA_LIVE_WRITES','MINA_PUBLISH_ENABLED','MINA_APPROVAL_ENABLED','MINA_PREPARE_ENABLED','MINA_MEAL_REPLACEMENT_ENABLED'):
  val=env.get(name.encode());out['runtime_flags'][name]=val.decode() if val in (b'true',b'false') else 'unset_or_other'
db.rollback();db.close();print(json.dumps(out,ensure_ascii=False,indent=2))
