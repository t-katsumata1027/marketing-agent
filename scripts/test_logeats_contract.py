"""DB・外部APIなしのLogEats実ハンドラー契約結合試験を明示的に開始する。"""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
import sys
import unittest


def producer_manifest(source):
    """試験で参照した供給側ソースの版を、未コミット変更も含めて記録する。"""
    files = list((source / 'src/lib/minaM1').glob('*.ts'))
    files.extend(source / relative for relative in (
        'src/app/api/internal/mina/state/route.ts', 'src/app/api/internal/mina/requests/[uuid]/route.ts',
        'src/app/api/internal/mina/meals/route.ts', 'src/app/api/internal/mina/daily-share/route.ts',
        'src/lib/minaAgentApi.ts', 'src/lib/shareOgImage.ts', 'src/lib/site.ts',
        'scripts/lib/mina-m1-fake-store.ts', 'tsconfig.json', 'package.json', 'package-lock.json'))
    return {path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(files) if path.is_file()}


def main():
    parser = argparse.ArgumentParser(description='LogEatsとmarketing-agentの契約結合テスト')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if not (args.source / 'src/lib/minaM1/handlers.ts').is_file():
        parser.error('M1実装済みLogEatsソースが必要です')
    os.environ['LOGEATS_CONTRACT_SOURCE'] = str(args.source.resolve())
    before = producer_manifest(args.source.resolve())
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    suite = unittest.defaultTestLoader.discover(str(root / 'tests/contract_bridge'), pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    stable = before == producer_manifest(args.source.resolve())
    success = result.wasSuccessful() and stable
    if not stable:
        print('試験中に供給側ソースが変わりました。結果を採用せず再検証してください。', file=sys.stderr)
    if args.report:
        report = {'checked_at': datetime.now(UTC).isoformat(), 'success': success,
                  'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                  'producer_source_stable': stable, 'producer_files_sha256': before,
                  'external_connections_blocked': True,
                  'boundaries': ['実routeハンドラーと実HTTPクライアント', 'DB・AI・画像保存・描画・R2は試験代替', '公開HTMLは合成', '実Discord・Xは未接続']}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
