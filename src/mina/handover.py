"""外部へ接続せず、引継ぎ前に独立して確認できる条件を診断する。"""

import os
import subprocess
from datetime import timedelta
from pathlib import Path

from .diagnostics import diagnose, STOP_FLAGS
from .domain import JST
from .readiness import check_evidence, read_json, release_commit

def check_handover(root, commit, database, heartbeat, evidence, now, env=None):
    """設定の存在や自己申告だけでは本番開始可能としない。秘密値は返さない。"""
    env = os.environ if env is None else env
    target = (now.astimezone(JST).date() + timedelta(days=1)).isoformat()
    diagnostic = diagnose(database, heartbeat, now, env)
    code_matches = False
    try:
        release_commit(root, commit)
        code_matches = True
    except (ValueError, OSError, subprocess.SubprocessError):
        pass
    history_valid = False
    try:
        check_evidence(read_json(evidence), target, now)
        history_valid = True
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        pass
    checks = {
        'all_stop_flags_false': all(env.get(name, 'false').lower() == 'false' for name in STOP_FLAGS),
        'release_code_matches': code_matches,
        'history_evidence_current': history_valid,
        'database_readable': diagnostic['database']['readable'],
        'heartbeat_fresh': diagnostic['heartbeat']['fresh'],
        'daemon_cycle_healthy': diagnostic['cycle_health']['healthy'],
        'no_unknown_operations': not diagnostic['manual_reconciliation_needed'],
        'cli_auth_file_present': bool(env.get('MINA_CODEX_HOME')) and
                                 (Path(env.get('MINA_CODEX_HOME', '')) / 'auth.json').is_file(),
    }
    missing = {}
    for group, config in diagnostic['connections'].items():
        checks[group + '_settings_present'] = config['configured']
        missing[group] = config['missing']
    return {'target_date': target, 'checks': checks, 'missing_setting_names': missing,
            'independent_checks_passed': all(checks.values()), 'production_start_allowed': False,
            'external_calls_performed': False,
            'required_acceptance': ['LogEats最終版・正規リリース・本番受入・後処理',
                'X認証先の実照合と承認された公開試験', 'Google OAuthの継続性',
                '実1日分の所要時間と20時通知・Discord実操作', '旧予約・旧自動化の切替',
                'Windows起動からの無人復帰・保護された別媒体の復旧', '正式CIと独立レビュー'],
            'history_mode': '初回履歴と旧予約停止を確認し、運用時はAgentが14日履歴を取得・照合する',
            'notes': ['設定の存在は認証成功の証明ではない',
                      '認証ファイルの存在は期限・無人実行の証明ではない',
                      '独立診断成功は外部処理の開始許可ではない']}
