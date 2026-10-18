"""本人指定のX修正だけを本番と別の配置で固定し、外部接続なしでLinux試験する。"""
import hashlib,json,os,pathlib,shutil,subprocess
commit='9cdae3f3cedb1cc602bfaef1c3da14108604a38f'
bundle=pathlib.Path('/tmp/mina-9cdae3f-20261019.bundle')
assert hashlib.sha256(bundle.read_bytes()).hexdigest()=='6308704f1c3d09ef5eb26154ded4a9e22ab1ebe4024fc8e7b54c396d7efa660a'
candidate=pathlib.Path('/opt/marketing-agent-candidate-'+commit[:10])
assert not candidate.exists() and not candidate.is_symlink()
subprocess.run(['git','clone','-q',str(bundle),str(candidate)],check=True)
subprocess.run(['git','-C',str(candidate),'checkout','-q','--detach',commit],check=True)
shutil.copytree('/opt/marketing-agent/.venv-mina',candidate/'.venv-mina',symlinks=True)
env={k:v for k,v in os.environ.items() if not k.startswith(('MINA_','DOPPLER_','OPENAI_','X_','DISCORD_','BUFFER_'))}
for name in ('AI_ENABLED','LIVE_WRITES','PUBLISH_ENABLED','APPROVAL_ENABLED','PREPARE_ENABLED','MEAL_REPLACEMENT_ENABLED','X_HISTORY_READ_ENABLED','HISTORY_AUTO_ENABLED'):
 env['MINA_'+name]='false'
env['MINA_DATABASE']='/tmp/mina-9cdae3f-isolated-absent.sqlite3'
env['PYTHONDONTWRITEBYTECODE']='1'
with open('/tmp/mina-9cdae3f-linux-tests.log','w') as output:
 r=subprocess.run(['runuser','-u','mina-agent','--',str(candidate/'.venv-mina/bin/python'),'-m','unittest','discover','-s','tests/mina','-q'],cwd=candidate,env=env,stdout=output,stderr=subprocess.STDOUT)
print(pathlib.Path('/tmp/mina-9cdae3f-linux-tests.log').read_text()[-2000:],flush=True)
assert r.returncode==0
subprocess.run(['runuser','-u','mina-agent','--',str(candidate/'.venv-mina/bin/python'),'-c','from src.mina.readiness import release_commit;from pathlib import Path;print(release_commit(Path.cwd(),"'+commit+'"))'],cwd=candidate,env=env,check=True)
print(json.dumps({'commit':commit,'tests':'passed','production_changed':False}),flush=True)
