"""固定本体を別フォルダーへ展開し、課金・本番接続なしで初回Pの受入を再現する。"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
COMMIT='0e80de53ef5dc48ddd997db9e88bb9750d753719'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--provider-repo',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    output=args.output.resolve()
    if output.exists():raise ValueError('既存の受入証跡を上書きしません')
    provider=args.provider_repo.resolve();modules=provider/'node_modules'
    if not (modules/'tsx/dist/cli.mjs').is_file():raise ValueError('本体用のローカル依存を先に用意してください')
    raw=subprocess.check_output(['git','-C',str(provider),'archive',COMMIT],timeout=60)
    sys.path.insert(0,str(ROOT))
    with tempfile.TemporaryDirectory(prefix='mina-initial-isolated-') as folder:
        exported=Path(folder)/'provider';exported.mkdir()
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:archive.extractall(exported,filter='data')
        link=exported/'node_modules'
        if os.name=='nt':
            subprocess.run(['cmd','/c','mklink','/J',str(link),str(modules)],check=True,capture_output=True)
        else:link.symlink_to(modules,target_is_directory=True)
        try:
            evidence=Path(folder)/'http.json'
            os.environ['MINA_ISOLATED_PROVIDER_ROOT']=str(exported)
            os.environ['MINA_INITIAL_ACCEPTANCE_REPORT']=str(evidence)
            suite=unittest.defaultTestLoader.loadTestsFromName('tests.mina.test_replacement_initial_http')
            result=unittest.TextTestRunner(verbosity=2).run(suite)
            report=json.loads(evidence.read_text(encoding='utf-8')) if evidence.exists() else {}
            report.update(provider_commit=COMMIT,tests_run=result.testsRun,failures=len(result.failures),errors=len(result.errors),
                skipped=len(result.skipped),successful=result.wasSuccessful(),production_changes=False,paid_external_calls=0,
                agent_source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
                    'src/mina/replacement_flow.py','src/mina/replacement_origin.py','src/mina/replacement_policy.py',
                    'src/mina/replacement_control.py','scripts/mina_initial_prepare_http.ts','tests/mina/test_replacement_initial_http.py')})
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            return 0 if result.wasSuccessful() else 1
        finally:
            # 専用一時フォルダー内の依存リンクだけを外し、本体のnode_modulesを保持する。
            if link.parent!=exported or not link.is_dir():raise ValueError('隔離依存リンクの位置が不正です')
            if os.name=='nt':os.rmdir(link)
            else:link.unlink()


if __name__=='__main__':sys.exit(main())
