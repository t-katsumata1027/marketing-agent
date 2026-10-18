"""本人承認後に検証済み固定版だけを配置し、Xの業務台帳を保持し、Instagramサービスを操作しないする。"""
import hashlib,json,os,pathlib,re,shutil,sqlite3,subprocess,sys
commit='9cdae3f3cedb1cc602bfaef1c3da14108604a38f'
if sys.argv[1:]!=['--user-approved']:raise RuntimeError('本人承認後の明示実行が必要です')
old_commit='c2f8b496870c7077f148f901ed42a00d2d7c5679'
short=commit[:10];live=pathlib.Path('/opt/marketing-agent');candidate=pathlib.Path('/opt/marketing-agent-candidate-'+short)
rollback=pathlib.Path('/opt/marketing-agent-rollback-20261019-'+short)
backup=pathlib.Path('/var/lib/mina-agent/deployment-preparation-'+short)
for path in (live,candidate,rollback,backup):
    if path.is_symlink() or not path.is_absolute():raise RuntimeError('配置先のリンク・相対パスを拒否します')
if rollback.exists() or backup.exists():raise RuntimeError('既存の退避先を上書きしません')
old=subprocess.run(['git','-c','safe.directory='+str(live),'-C',str(live),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
if old!=old_commit:raise RuntimeError('本番コードが承認対象の基点と違います')
if subprocess.check_output(['git','-c','safe.directory='+str(live),'-C',str(live),'status','--porcelain','--untracked-files=no'],text=True).strip():raise RuntimeError('本番に未記録差分があります')
for unit in ('mina-prepare.service','mina-instagram-prepare.service'):
    if subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0:raise RuntimeError('準備実行中の切替を拒否します')
subprocess.run(['runuser','-u','mina-agent','--',str(candidate/'.venv-mina/bin/python'),'-c',
 'from src.mina.readiness import release_commit; from pathlib import Path; print(release_commit(Path.cwd(),"'+commit+'"))'],cwd=candidate,check=True)
shutil.copytree(live/'.codex-cli',candidate/'.codex-cli',symlinks=True)
def snapshot():
    with sqlite3.connect('file:/var/lib/mina-agent/mina.sqlite3?mode=ro',uri=True) as db:
        tables=[row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if row[0] in (
            'candidates','notices','mina_requests','buffer_publication','buffer_policy','preparation','preparation_steps','publication_recoveries') ]
        if any(not re.fullmatch('[a-z_]+',table) for table in tables):raise RuntimeError('台帳名が不正です')
        return {table:hashlib.sha256(json.dumps(db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall(),default=str).encode()).hexdigest() for table in tables}
config=pathlib.Path('/etc/marketing-agent/mina.env');text=config.read_text()
updated,count=re.subn(r'^MINA_SOURCE_COMMIT=.*$','MINA_SOURCE_COMMIT='+commit,text,flags=re.M)
if count!=1:raise RuntimeError('配置版設定の一意性を確認できません')
units=('mina-agent','mina-gateway')
if any(subprocess.run(['systemctl','is-active','--quiet',unit]).returncode!=0 for unit in units):raise RuntimeError('配置前の稼働状態を確認できません')
swapped=False;config_written=False
try:
    subprocess.run(['systemctl','stop',*units],check=True)
    backup.mkdir(mode=0o700)
    shutil.copy2(config,backup/'mina.env');os.chmod(backup/'mina.env',0o600)
    before=snapshot()
    with sqlite3.connect('file:/var/lib/mina-agent/mina.sqlite3?mode=ro',uri=True) as source,sqlite3.connect(backup/'mina.sqlite3') as dest:
        source.backup(dest)
        if dest.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('退避台帳の整合性を確認できません')
    os.chmod(backup/'mina.sqlite3',0o600)
    if config.read_text()!=text:raise RuntimeError('設定が他作業で変更されました')
    live.rename(rollback);candidate.rename(live);swapped=True
    config.write_text(updated);os.chmod(config,0o600);config_written=True
    if snapshot()!=before:raise RuntimeError('切替中に業務台帳が変わりました')
    subprocess.run(['systemctl','start',*units],check=True)
except Exception:
    subprocess.run(['systemctl','stop',*units],check=False)
    if swapped and rollback.exists():
        if live.exists():live.rename(pathlib.Path('/opt/marketing-agent-failed-'+short))
        rollback.rename(live)
    if config_written and config.read_text()==updated:
        config.write_text(text);os.chmod(config,0o600)
    # 台帳を巻き戻さず旧コードを起動する。切替中に外部送信は開始していない。
    subprocess.run(['systemctl','start',*units],check=False)
    raise RuntimeError('切替に失敗し、元のコード・設定へ戻しました') from None
(backup/'operational-hashes.json').write_text(json.dumps(before));os.chmod(backup/'operational-hashes.json',0o600)
print(json.dumps({'source_commit':commit,'operational_rows_unchanged':snapshot()==before,'retained_tables':len(before)}),flush=True)
subprocess.run(['systemctl','is-active',*units,'mina-prepare.timer','mina-notify.timer'],check=True)
