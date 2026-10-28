"""条件付き本人承認に基づく固定Agent配置。救済OFFを維持し、業務処理を開始しない。"""
import contextlib,datetime,hashlib,json,os,pathlib,re,shutil,sqlite3,subprocess,tempfile,time

COMMIT=os.environ['MINA_DEPLOY_COMMIT']
if not re.fullmatch('[a-f0-9]{40}',COMMIT):raise RuntimeError('配置commit形式不正')
OLD='a34005efd50afa5b75431f2baee05471ffa8027d'
live=pathlib.Path('/opt/marketing-agent')
candidate=pathlib.Path('/opt/marketing-agent-candidate-review-scoped-'+COMMIT[:8])
rollback=pathlib.Path('/opt/marketing-agent-rollback-review-scoped-'+COMMIT[:8])
failed=pathlib.Path('/opt/marketing-agent-failed-review-scoped-'+COMMIT[:8])
backup=pathlib.Path('/var/lib/mina-agent/deployment-review-recovery-scoped-'+COMMIT[:8])
config=pathlib.Path('/etc/marketing-agent/mina.env')
database=pathlib.Path('/var/lib/mina-agent/mina.sqlite3')
bundle=pathlib.Path('/var/tmp/mina-scoped-'+COMMIT[:8]+'.bundle')
def run(*args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def output(*args):return subprocess.check_output(args,text=True).strip()
def git(root,*args):return output('git','-c','safe.directory='+str(root),'-C',str(root),*args)
def active(unit):return subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0
def stopped_unit(unit):return output('systemctl','show',unit,'--property=ActiveState','--value') in ('inactive','failed')
def snapshot():
    with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        result={}
        for table in tables:
            if not re.fullmatch('[a-z_]+',table):raise RuntimeError('台帳名を確認できません')
            def encode(v):return {'blob_sha256':hashlib.sha256(v).hexdigest()} if isinstance(v,bytes) else v
            rows=sorted(json.dumps([encode(v) for v in row],ensure_ascii=True) for row in db.execute('SELECT * FROM '+table))
            result[table]={'count':len(rows),'sha256':hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
        return result
def atomic_config(text):
    fd,name=tempfile.mkstemp(dir=config.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(text);f.flush();os.fsync(f.fileno())
        shutil.copystat(config,name);os.chown(name,config.stat().st_uid,config.stat().st_gid);os.replace(name,config)
    finally:
        if os.path.exists(name):os.unlink(name)

for p in (live,candidate,rollback,failed,backup,bundle):
    if p.is_symlink() or not p.is_absolute():raise RuntimeError('配置先リンクを拒否します')
if any(p.exists() for p in (candidate,rollback,failed,backup)):raise RuntimeError('既存配置・退避先を上書きしません')
if git(live,'rev-parse','HEAD')!=OLD or git(live,'status','--porcelain','--untracked-files=no'):
    raise RuntimeError('稼働基点が変更されています')
if any(not stopped_unit(u) for u in ('mina-prepare.service','mina-instagram-prepare.service','mina-notify.service','mina-backup.service','mina-offsite-backup.service')):
    raise RuntimeError('定期処理実行中の切替は行いません')
text=config.read_text();values=dict(line.split('=',1) for line in text.splitlines() if '=' in line and not line.startswith('#'))
if values.get('MINA_SAME_DAY_RESCUE_ENABLED','false')!='false':raise RuntimeError('救済OFFを確認できません')
if values.get('MINA_SOURCE_COMMIT')!=OLD:raise RuntimeError('稼働設定が変更されています')
updated,n=re.subn(r'^MINA_SOURCE_COMMIT=.*$','MINA_SOURCE_COMMIT='+COMMIT,text,flags=re.M)
if n!=1:raise RuntimeError('配布版設定が一意ではありません')
if 'MINA_SAME_DAY_RESCUE_ENABLED' in values:
    updated,n=re.subn(r'^MINA_SAME_DAY_RESCUE_ENABLED=.*$','MINA_SAME_DAY_RESCUE_ENABLED=false',updated,flags=re.M)
    if n!=1:raise RuntimeError('救済OFF設定が一意ではありません')
else:updated=updated.rstrip('\n')+'\nMINA_SAME_DAY_RESCUE_ENABLED=false\n'
# 通常収集・UTM・Instagram設定は変更せず、新旧救済だけを明示OFFにする。
for flag in ('MINA_SCOPED_RESCUE_ENABLED',):
    if values.get(flag,'false').lower()!='false':raise RuntimeError('新救済OFFを確認できません')
    if flag in values:
        updated,n=re.subn('^'+flag+'=.*$',flag+'=false',updated,flags=re.M)
        if n!=1:raise RuntimeError('新救済設定が一意ではありません')
    else:updated=updated.rstrip('\n')+'\n'+flag+'=false\n'
if values.get('MINA_SCOPED_RESCUE_CONFIG',''):raise RuntimeError('新救済config未設定を確認できません')

run('git','clone','-q',str(bundle),str(candidate))
run('git','-C',str(candidate),'checkout','-q','--detach',COMMIT)
if (candidate/'requirements-mina.txt').read_bytes()!=(live/'requirements-mina.txt').read_bytes():
    raise RuntimeError('依存変更は今回の配置に含めません')
for name in ('.venv-mina','.codex-cli'):
    source=live/name;destination=candidate/name
    if source.exists():
        shutil.copytree(source,destination,symlinks=True)
        # 秘密の内容を読出・表示せず、既存の所有者と保護modeを保持する。
        for original in (source,*source.rglob('*')):
            copied=destination/original.relative_to(source);stat=original.lstat()
            os.chown(copied,stat.st_uid,stat.st_gid,follow_symlinks=False)
python=str(candidate/'.venv-mina/bin/python')
isolated={k:v for k,v in os.environ.items() if not k.startswith(('MINA_','DOPPLER_','OPENAI_','DISCORD_','BUFFER_','X_'))}
isolated.update(PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(candidate)+':'+str(candidate/'tests/mina'))
isolated['MINA_SAME_DAY_RESCUE_ENABLED']='false'
isolated['MINA_SCOPED_RESCUE_ENABLED']='false'
check="from pathlib import Path;from src.mina.readiness import release_commit;from src.mina.rescue_contract import resources;resources();assert release_commit(Path.cwd(),'"+COMMIT+"')=='"+COMMIT+"';from src.mina.scoped_rescue_runtime import build_flow\ntry:build_flow(None,None,None,None,None,None,None,None,None)\nexcept ValueError as e:assert str(e)=='scoped_rescue_execution_disabled'\nelse:raise AssertionError('OFFが機能しません')"
run('runuser','-u','mina-agent','--',python,'-c',check,cwd=candidate,env=isolated)
services=('mina-agent.service','mina-gateway.service','mina-instagram.service')
timers=('mina-prepare.timer','mina-notify.timer','mina-backup.timer','mina-instagram-prepare.timer','mina-offsite-backup.timer')
was_active={u:active(u) for u in (*services,*timers)}
if not all(was_active[u] for u in services):raise RuntimeError('配置前3サービスの稼働を確認できません')
swapped=False;old_moved=False;written=False;stopped=False
try:
    run('systemctl','stop',*[u for u in timers if was_active[u]])
    stopped=True
    run('systemctl','stop',*services)
    if not all(stopped_unit(u) for u in (*services,*(u for u in timers if was_active[u]))):
        raise RuntimeError('停止状態を確認できません')
    if any(not stopped_unit(u) for u in ('mina-prepare.service','mina-instagram-prepare.service','mina-notify.service','mina-backup.service','mina-offsite-backup.service')):
        raise RuntimeError('停止中に定期処理が開始されました')
    backup.mkdir(mode=0o700)
    shutil.copy2(config,backup/'mina.env');os.chmod(backup/'mina.env',0o600)
    with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True) as src,sqlite3.connect(backup/'mina.sqlite3') as dst:
        src.backup(dst)
        if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('退避DBが不整合です')
    os.chmod(backup/'mina.sqlite3',0o600)
    before=snapshot()
    if config.read_text()!=text or git(live,'rev-parse','HEAD')!=OLD:raise RuntimeError('切替中に基点が変わりました')
    # 既存全行を保持し、空の専用5台帳だけを追加する。権限・業務要求は作らない。
    migration="from src.mina.store import Store;from src.mina.scoped_rescue_storage import ScopedRescueJournal;from datetime import UTC,datetime;ScopedRescueJournal(Store('/var/lib/mina-agent/mina.sqlite3'),lambda:datetime.now(UTC),'2026-10-28','lunch','00000000-0000-4000-8000-000000000001')"
    run('runuser','-u','mina-agent','--',python,'-c',migration,cwd=candidate,env=isolated)
    after=snapshot();added=set(after)-set(before)
    expected_added={'scoped_rescue_jobs','scoped_rescue_requests','scoped_rescue_events','scoped_rescue_calls','scoped_preparation_locks'}
    if added!=expected_added or any(v!=after[k] for k,v in before.items()) or any(after[t]['count'] for t in added):raise RuntimeError('専用台帳移行の整合不一致')
    if not all(stopped_unit(u) for u in services) or any(not stopped_unit(u) for u in ('mina-prepare.service','mina-instagram-prepare.service','mina-notify.service','mina-backup.service','mina-offsite-backup.service')):raise RuntimeError('コード移動直前の停止を確認できません')
    live.rename(rollback);old_moved=True
    candidate.rename(live);swapped=True
    atomic_config(updated);written=True
    if snapshot()!=after:raise RuntimeError('切替中に台帳が変わりました')
    report={'source_commit':COMMIT,'previous_commit':OLD,'checked_at':datetime.datetime.now(datetime.UTC).isoformat(),'existing_tables':len(before),'existing_rows_unchanged_at_switch':True,'added_empty_tables':sorted(added),'same_day_rescue_enabled':False,'business_recovery_started':False,'scoped_rescue_enabled':False,'before':before,'after_migration':after,'was_active':was_active,'backup':str(backup),'rollback':str(rollback)}
    (backup/'deployment.json').write_text(json.dumps(report,indent=2));os.chmod(backup/'deployment.json',0o600)
    run('systemctl','start',*services)
    if not all(active(u) for u in services):raise RuntimeError('配置後サービスの稼働確認に失敗しました')
    run('systemctl','start',*[u for u in timers if was_active[u]])
    if not all(active(u) for u,on in was_active.items() if on):raise RuntimeError('配置後の稼働確認に失敗しました')
except BaseException:
    subprocess.run(['systemctl','stop',*[u for u in timers if was_active[u]]],check=False)
    if not all(stopped_unit(u) for u in timers if was_active[u]):raise RuntimeError('復旧未完：timer停止失敗、コード移動は行いません') from None
    # 再開直後のtimerがoneshotを起動しても、実処理を強制中断して成否不明を作らない。
    oneshots=('mina-prepare.service','mina-instagram-prepare.service','mina-notify.service','mina-backup.service','mina-offsite-backup.service')
    until=time.monotonic()+30
    while any(not stopped_unit(u) for u in oneshots) and time.monotonic()<until:time.sleep(1)
    if any(not stopped_unit(u) for u in oneshots):raise RuntimeError('復旧未完：実行中の定期処理を保持し、コード切替を停止。timerは停止済み') from None
    if stopped:
        subprocess.run(['systemctl','stop',*services],check=False)
        if not all(stopped_unit(u) for u in services):raise RuntimeError('復旧未完：サービス停止失敗、コード移動は行いません') from None
        if old_moved:
            if live.exists():live.rename(failed)
            rollback.rename(live)
        if written:atomic_config(text)
        # DBの事実は巻き戻さず、空の追加台帳は保持する。
        subprocess.run(['systemctl','start',*[u for u in services if was_active[u]]],check=False)
    subprocess.run(['systemctl','start',*[u for u in timers if was_active[u]]],check=False)
    restored=(live.exists() and git(live,'rev-parse','HEAD')==OLD and config.read_text()==text
              and all(active(u) for u,on in was_active.items() if on))
    message='切替失敗。業務DBを巻き戻さず元のコード・設定・稼働へ復帰済み' if restored else '切替失敗。復旧未完のため配置先・設定・サービスの確認が必要'
    raise RuntimeError(message) from None
print(json.dumps({k:v for k,v in report.items() if k not in ('before','after_migration','was_active')}) ,flush=True)
