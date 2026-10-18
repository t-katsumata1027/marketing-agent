"""稼働サービスの既存環境を必要名だけ再利用し、明示承認されたX復旧を専用ユーザーで行う。"""
import json,pathlib,subprocess
script=pathlib.Path('tmp/x-clock-recovery-20261019/recover_live.py').read_text(encoding='utf-8')
launcher='''
import json,os,pathlib,pwd,traceback
try:
 expected='9cdae3f3cedb1cc602bfaef1c3da14108604a38f'
 allowed=('PATH','HOME','MINA_DATABASE','MINA_SOURCE_COMMIT','MINA_LIVE_WRITES','MINA_PUBLISH_ENABLED','MINA_MEAL_API_BASE_URL','MINA_AGENT_API_TOKEN','MINA_SHARE_HOST','BUFFER_API_KEY')
 selected=None
 for p in pathlib.Path('/proc').iterdir():
  if not p.name.isdigit():continue
  try:
   args=(p/'cmdline').read_bytes().split(b'\\0')
   if b'src.mina' not in args or b'daemon' not in args:continue
   values=dict(x.split(b'=',1) for x in (p/'environ').read_bytes().split(b'\\0') if b'=' in x)
   if values.get(b'MINA_SOURCE_COMMIT')==expected.encode() and len(values.get(b'MINA_AGENT_API_TOKEN',b''))>=32:
    selected={k:values[k.encode()].decode() for k in allowed if k.encode() in values};break
  except (FileNotFoundError,ProcessLookupError):continue
 if selected is None:raise ValueError('current_service_environment_unavailable')
 assert selected['MINA_LIVE_WRITES']=='true' and selected['MINA_PUBLISH_ENABLED']=='true'
 assert selected['MINA_DATABASE']=='/var/lib/mina-agent/mina.sqlite3'
 account=pwd.getpwnam('mina-agent');os.initgroups(account.pw_name,account.pw_gid);os.setgid(account.pw_gid);os.setuid(account.pw_uid)
 os.chdir('/opt/marketing-agent')
 code="import json,traceback\\ntry:\\n exec(compile("+repr(SCRIPT)+", 'x_clock_recovery', 'exec'))\\nexcept Exception as e:\\n print(json.dumps({'ok':False,'error_type':type(e).__name__,'frames':[{'file':f.filename.rsplit('/',1)[-1],'line':f.lineno} for f in traceback.extract_tb(e.__traceback__)]}))"
 python='/opt/marketing-agent/.venv-mina/bin/python'
 os.execve(python,[python,'-c',code],selected)
except Exception as e:
 print(json.dumps({'ok':False,'error_type':type(e).__name__,'stage':'existing_environment'}))
'''.replace('SCRIPT',repr(script))
r=subprocess.run(['ssh','-T','-o','BatchMode=yes','katsu-server1','wsl','-d','LogEats-Runner','-u','root','--','/usr/bin/python3','-'],input=launcher.encode('utf-8'),capture_output=True,timeout=180)
if r.returncode:print(json.dumps({'ok':False,'stage':'remote_execution','returncode':r.returncode}))
else:print(r.stdout.decode('utf-8').strip())
