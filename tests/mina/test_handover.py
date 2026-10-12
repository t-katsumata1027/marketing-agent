"""引継ぎ診断が設定の存在を本番開始許可へ読み替えないことを検証する。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.mina.handover import STOP_FLAGS, check_handover
from src.mina.diagnostics import SETTING_GROUPS
from src.mina.store import Store
from tests.mina.test_readiness import evidence
from tests.mina.test_daily import NOW


class HandoverTest(unittest.TestCase):
    def test_missing_files_do_not_create_database_or_leak_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('src.mina.handover.release_commit', side_effect=ValueError('秘密の原文')):
                report = check_handover(root, '', root/'db', root/'heartbeat', root/'evidence', NOW,
                                        {'X_API_KEY': '秘密トークン'})
            self.assertFalse((root/'db').exists())
            self.assertFalse(report['production_start_allowed'])
            self.assertFalse(report['independent_checks_passed'])
            self.assertNotIn('秘密', json.dumps(report, ensure_ascii=False))

    def test_all_local_conditions_still_require_real_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Store(root/'db')
            (root/'heartbeat').write_text(str(NOW.timestamp()))
            (root/'evidence').write_text(json.dumps(evidence()))
            (root/'auth.json').write_text('これは実認証ではない')
            env = {name: '存在確認用' for names in SETTING_GROUPS.values() for name in names}
            env.update({'MINA_CODEX_HOME': str(root), 'MINA_CODEX_BIN': '確認用',
                        **{name: 'false' for name in STOP_FLAGS}})
            with patch('src.mina.handover.release_commit'):
                report = check_handover(root, 'a'*40, root/'db', root/'heartbeat', root/'evidence', NOW, env)
                self.assertTrue(report['independent_checks_passed'])
                self.assertFalse(report['production_start_allowed'])
                for name in STOP_FLAGS:
                    bad = check_handover(root, 'a'*40, root/'db', root/'heartbeat', root/'evidence', NOW,
                                         {**env, name: 'true'})
                    self.assertFalse(bad['checks']['all_stop_flags_false'])

    def test_cli_does_not_construct_store_or_external_client(self):
        import os
        from src.mina.__main__ import main
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=True), \
                 patch('sys.argv', ['mina', 'handover-check', '--database', str(Path(tmp)/'missing')]), \
                 patch('src.mina.__main__.Store') as store, \
                 patch('src.mina.__main__.MealClient', create=True) as client, \
                 patch('builtins.print'):
                main()
            store.assert_not_called()
            client.assert_not_called()
            self.assertFalse((Path(tmp)/'missing').exists())
