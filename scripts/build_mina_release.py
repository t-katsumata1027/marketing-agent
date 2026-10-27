"""外部接続・配置・サービス起動を行わず、固定commitの配布物を作る。"""

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.mina.readiness import release_commit
from src.mina.replacement_contract import load_contract_resources
from src.mina.rescue_contract import resources as rescue_resources, SCHEMA_SHA as RESCUE_SCHEMA_SHA
from src.mina.scoped_rescue_contract import ScopedRescueContract,SCHEMA_SHA as SCOPED_SCHEMA_SHA


def verify_archive_contract(archive, evidence):
    """配布tarにも正式契約と凍結履歴が同じ実バイトで入っていることを確認する。"""
    for entry in (evidence['runtime_schema'],*evidence['frozen_history'].values(),
                  {'path':'src/mina/contracts/rescue_v1/mina-rescue-v1.schema.json','sha256':RESCUE_SCHEMA_SHA},
                  {'path':'src/mina/contracts/rescue_v3/mina-rescue-v3.schema.json','sha256':SCOPED_SCHEMA_SHA}):
        try:
            member=archive.getmember(entry['path'])
            if not member.isfile():
                raise ValueError('契約資料は通常ファイルである必要があります')
            raw=archive.extractfile(member).read()
        except (KeyError,OSError):
            raise ValueError('配布物に契約資料がありません') from None
        if hashlib.sha256(raw).hexdigest()!=entry['sha256']:
            raise ValueError('配布物の契約SHAが一致しません')


def git(*args):
    result = subprocess.run(['git', '-c', 'core.autocrlf=false', '-C', str(ROOT), *args], capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError('配布Git操作に失敗しました')
    return result.stdout


def build(output, commit):
    """秘密値・実行時データ・仮想環境を含めず、停止状態の配布証跡を作る。"""
    if git('rev-parse', 'HEAD').decode().strip() != commit:
        raise ValueError('配布commitは現在のHEADに固定してください')
    release_commit(ROOT, commit)
    contract_evidence,_ = load_contract_resources(ROOT/'src/mina/contracts')
    rescue_resources()
    ScopedRescueContract(lambda:None)
    names = git('ls-tree', '-r', '--name-only', commit).decode().splitlines()
    selected = [name for name in names if (
        name.startswith(('src/mina/', 'deploy/', 'tests/mina/', 'tests/contract_bridge/', 'assets/instagram/'))
        or name in ('.gitattributes', 'src/__init__.py', 'requirements-mina.txt', 'requirements-mina.in',
                    'scripts/test_logeats_contract.py', 'scripts/build_mina_release.py', 'scripts/build_replacement_waived_policy.py', 'scripts/mina_rescue_http.mts')
        or ('/' not in name and name.endswith('.md') and name != 'README.md')
    )]
    # 配布にリンクがあると、展開先以外の設定・認証を参照する恐れがある。
    for line in git('ls-tree', '-r', commit, '--', *selected).decode().splitlines():
        if not line.startswith(('100644 ', '100755 ')):
            raise ValueError('通常ファイル以外の配布を拒否しました')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('既存の配布物を上書きしません')
    output.mkdir(parents=True, mode=0o700)
    git('archive', '--format=tar', f'--output={output / "source.tar"}', commit, '--', *selected)
    with tarfile.open(output / 'source.tar') as archive:
        verify_archive_contract(archive,contract_evidence)
        for name in selected:
            if name.endswith('.sh') and b'\r' in archive.extractfile(name).read():
                raise ValueError('配布シェルスクリプトのLF改行を確認できません')
    # bundleは履歴だけであり、ローカルGit設定・認証・未コミット値をコピーしない。
    git('bundle', 'create', str(output / 'source.bundle'), 'HEAD')
    artifacts = {}
    for name in ('source.tar', 'source.bundle'):
        path = output / name
        artifacts[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
    manifest = {'source_commit': commit, 'contract_version': 'mina-write-v1',
                'logeats_code_reference': 'b71d4a27ab8a4208487acbceb2f106b3ff128794',
                'logeats_document_reference': '2bc563843646e594e443f5cec8428caa554b6bbf',
                'meal_replacement_contract':contract_evidence,
                'same_day_rescue_contract':{'schema_sha256':RESCUE_SCHEMA_SHA,'activation_allowed':False},
                'scoped_rescue_contract':{'schema_sha256':SCOPED_SCHEMA_SHA,'activation_allowed':False},
                'activation_allowed': False, 'files': selected, 'artifacts': artifacts}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'source_commit': commit, 'file_count': len(selected), 'activation_allowed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='停止状態のMina配布物を作成する')
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.commit)))
