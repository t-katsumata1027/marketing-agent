"""専用バックアップ入口の失敗理由を秘密値なしで記録する。"""
import json
import sys


def run():
    """生の例外本文・引数・認証情報をログへ流さない。"""
    if len(sys.argv)<2 or sys.argv[1] not in ('backup-run','backup-upload','backup-reconcile'):
        print('{"event":"backup_command_rejected"}',flush=True)
        return 2
    try:
        from .__main__ import main
        main()
        return 0
    except SystemExit as error:
        if error.code in (None,0):return 0
        event='backup_arguments_rejected'
    except Exception as error:
        categories={'OutcomeUnknown':'backup_reconciliation_required',
                    'ValueError':'backup_validation_failed','RuntimeError':'backup_runtime_failed',
                    'OSError':'backup_filesystem_failed','PermissionError':'backup_permission_failed',
                    'ImportError':'backup_runtime_dependency_missing','ModuleNotFoundError':'backup_runtime_dependency_missing'}
        event=categories.get(type(error).__name__,'backup_unconfirmed_failure')
    print(json.dumps({'event':event,'business_calls_allowed':False}),flush=True)
    return 1


if __name__=='__main__':
    raise SystemExit(run())
