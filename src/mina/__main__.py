"""検証・日次タイマー・署名付き承認サーバーの入口。"""

import argparse
import json
import os
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .clients import DiscordClient, XClient
from .domain import JST
from .diagnostics import diagnose
from .interactions import ApprovalPolicy, handle
from .ogp import verify
from .runtime import import_manifest, notify_day, publish_due, send_notifications
from .store import Store


def settings_status():
    """値を表示せず、接続設定の有無だけを報告する。"""
    names = ("OPENAI_API_KEY", "MINA_SHARE_HOST", "DISCORD_BOT_TOKEN", "MINA_DISCORD_APPLICATION_ID",
             "MINA_DISCORD_PUBLIC_KEY", "DISCORD_ALLOWED_GUILD_ID", "DISCORD_ALLOWED_CHANNEL_ID",
             "MINA_APPROVER_USER_IDS", "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
             "X_ACCESS_TOKEN_SECRET", "MINA_AGENT_API_TOKEN", "MINA_MEAL_API_BASE_URL")
    return {"ai_enabled": os.getenv("MINA_AI_ENABLED", "false").lower() == "true",
            "prepare_enabled": os.getenv("MINA_PREPARE_ENABLED", "false").lower() == "true",
            "read_review_enabled": os.getenv("MINA_READ_REVIEW_ENABLED", "false").lower() == "true",
            "x_history_read_enabled": os.getenv("MINA_X_HISTORY_READ_ENABLED", "false").lower() == "true",
            "history_auto_enabled": os.getenv("MINA_HISTORY_AUTO_ENABLED", "false").lower() == "true",
            "live_writes": os.getenv("MINA_LIVE_WRITES", "false").lower() == "true",
            "publish_enabled": os.getenv("MINA_PUBLISH_ENABLED", "false").lower() == "true",
            "approval_enabled": os.getenv("MINA_APPROVAL_ENABLED", "false").lower() == "true",
            "settings": {name: bool(os.getenv(name)) for name in names}}


def policy_from_env():
    return ApprovalPolicy(os.getenv("MINA_DISCORD_PUBLIC_KEY", ""), os.getenv("MINA_DISCORD_APPLICATION_ID", ""),
                          os.getenv("DISCORD_ALLOWED_GUILD_ID", ""), os.getenv("DISCORD_ALLOWED_CHANNEL_ID", ""),
                          frozenset(v.strip() for v in os.getenv("MINA_APPROVER_USER_IDS", "").split(",") if v.strip()))


def serve(store, host: str, port: int):
    """ループバックで待機。外部HTTPS入口は別途設定する。"""
    policy = policy_from_env()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            # HTTP原文や承認トークンを標準ログへ出さない。
            return

        def response(self, code, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.response(200 if self.path == "/health" else 404, {"ok": self.path == "/health"})

        def do_POST(self):
            if self.path != "/interactions":
                return self.response(404, {"error": "not_found"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 65536:
                    return self.response(413, {"error": "request_size"})
                self.connection.settimeout(5)
                body = self.rfile.read(size)
                # PINGは設定確認に使うが、停止中は実際の承認を受け付けない。
                if os.getenv("MINA_APPROVAL_ENABLED", "false").lower() != "true":
                    try:
                        is_ping = json.loads(body).get("type") == 1
                    except (ValueError, AttributeError):
                        is_ping = False
                    if not is_ping:
                        return self.response(503, {"error": "approval_paused"})
                code, payload = handle(body, self.headers.get("X-Signature-Ed25519", ""),
                                       self.headers.get("X-Signature-Timestamp", ""), policy, store, datetime.now(UTC))
                self.response(code, payload)
            except (ValueError, OSError):
                self.response(400, {"error": "invalid_request"})

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def cycle(store: Store, now: datetime):
    """20時の通知準備と公開期限の確認。検証時は外部接続しない。"""
    target = (now.astimezone(JST).date() + timedelta(days=1)).isoformat()
    today = now.astimezone(JST).date().isoformat()
    # 夜間準備が日付を跨いで完成した場合も、当日分の追加承認を処理する。
    if store.list_day(today):
        notify_day(store, today, now)
    notify_day(store, target, now)
    status = settings_status()
    if not status["live_writes"]:
        return {"mode": "dry_run", "target_date": target, "candidate_count": len(store.list_day(target))}
    if not all(status["settings"][name] for name in ("MINA_SHARE_HOST", "DISCORD_BOT_TOKEN", "DISCORD_ALLOWED_CHANNEL_ID")):
        raise ValueError("本番運用の接続設定が不足しています")
    sent = send_notifications(store, DiscordClient(os.environ["DISCORD_BOT_TOKEN"], os.environ["DISCORD_ALLOWED_CHANNEL_ID"]))
    host = os.environ["MINA_SHARE_HOST"]
    published = 0
    if status["publish_enabled"] and store.has_due(now):
        # 公開開始には承認設定も必須。確認用PINGだけで公開を有効にしない。
        policy_from_env()
        from .m1_contract import verify_source
        from .clients import MealClient
        from .ogp import capture_ogp
        meal_client = MealClient(os.getenv('MINA_MEAL_API_BASE_URL', ''), os.getenv('MINA_AGENT_API_TOKEN', ''))
        published = publish_due(store, XClient(), now, host, lambda c: verify_source(c, meal_client, host, capture_ogp))
    return {"mode": "live", "notices_sent": sent, "published": published}


def main():
    parser = argparse.ArgumentParser(description="みなの日次運用と承認の検証")
    parser.add_argument("command", choices=("status", "doctor", "handover-check", "inspect-handoff", "history-draft", "backup", "backup-artifacts", "backup-keygen", "backup-encrypt", "backup-decrypt", "backup-create-set", "backup-restore-set", "backup-upload", "backup-reconcile", "backup-run", "backup-status", "verify-artifacts", "request-status", "request-reconcile", "rereview", "rebind-review", "prepare", "prepare-today", "resume-today", "correct-today", "finalize-today", "resume-preparation", "draft", "images", "drive", "register", "import", "notify", "tick", "daemon", "serve", "gateway"))
    parser.add_argument("--database", default=os.getenv("MINA_DATABASE", "runtime/mina.sqlite3"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--draft", type=Path)
    parser.add_argument('--request-key')
    parser.add_argument('--review-round')
    parser.add_argument('--publish-lunch-after-approval', action='store_true')
    parser.add_argument('--bindings', type=Path)
    parser.add_argument('--contract-file', type=Path)
    parser.add_argument('--producer-commit')
    parser.add_argument('--backup-key', type=Path)
    parser.add_argument('--backup-recipient')
    parser.add_argument('--backup-sha256')
    parser.add_argument('--backup-filename')
    parser.add_argument('--operation', choices=('meal_register', 'meal_correction', 'daily_share'), default='meal_register')
    parser.add_argument("--date")
    parser.add_argument("--history", type=Path)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--source-commit', default=os.getenv('MINA_SOURCE_COMMIT', ''))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == 'backup-status':
        if not args.output:
            parser.error('--outputにバックアップの保護保存先を指定してください')
        from .backup_health import backup_health
        print(json.dumps(backup_health(args.output),ensure_ascii=False))
        return
    if args.command == 'backup-run':
        if not args.manifest or not args.source_commit:
            parser.error('--manifestに私有の対象設定、--source-commitに固定コード版が必要です')
        from .drive_backup import require_backup_upload
        from .backup_job import run_backup_job
        from .drive import DriveClient
        from .readiness import read_json,release_commit
        require_backup_upload()
        release_commit(Path(__file__).resolve().parents[2],args.source_commit)
        print(json.dumps(run_backup_job(read_json(args.manifest),DriveClient.from_env(),args.source_commit),ensure_ascii=False))
        return
    if args.command in ('backup-upload','backup-reconcile'):
        if not all((args.manifest,args.output,args.backup_sha256,args.backup_filename,args.source_commit)):
            parser.error('--manifest・--output・--backup-sha256・--backup-filename・--source-commitが必要です')
        from .drive_backup import BackupDriveClient,require_backup_upload
        from .drive import DriveClient
        require_backup_upload()
        client=BackupDriveClient(DriveClient.from_env())
        action=client.reconcile if args.command=='backup-reconcile' else client.upload_once
        print(json.dumps(action(args.manifest,args.output,
            args.backup_filename,args.backup_sha256,args.source_commit),ensure_ascii=False))
        return
    if args.command == 'backup-create-set':
        if not all((args.manifest,args.output,args.backup_key,args.backup_recipient,args.source_commit)):
            parser.error('--manifest・--output・--backup-key・--backup-recipient・--source-commitが必要です')
        from .readiness import read_json,release_commit
        from .recovery_set import create_recovery_set
        release_commit(Path(__file__).resolve().parents[2],args.source_commit)
        config=read_json(args.manifest)
        print(json.dumps(create_recovery_set(args.database,config['artifacts'],config['files'],
            args.output,args.backup_key,args.backup_recipient,args.source_commit),ensure_ascii=False))
        return
    if args.command == 'backup-restore-set':
        if not args.manifest or not args.output:
            parser.error('--manifestに復号済みZIP、--outputに新規隔離先を指定してください')
        from .recovery_set import restore_recovery_set
        print(json.dumps(restore_recovery_set(args.manifest, args.output), ensure_ascii=False))
        return
    if args.command == 'backup-keygen':
        if not args.output:
            parser.error('--outputに新規の保護フォルダーを指定してください')
        import sys
        if not sys.stdin.isatty():
            parser.error('PowerShellなど、本人が入力できる端末で実行してください')
        from getpass import getpass
        from .encrypted_backup import create_recovery_keys
        first = getpass('復号用パスワード（半角英数字20文字以上を推奨。チャットに送らない）: ')
        if first != getpass('同じパスワードを再入力: '):
            parser.error('2回の入力が一致しません。鍵は作成していません。同じパスワードでやり直してください')
        encoded = first.encode('utf-8')
        if not 16 <= len(encoded) <= 1024 or not encoded.strip():
            parser.error('パスワードが短すぎるか長すぎます。鍵は作成していません。半角英数字20文字以上の新しいパスワードを保管してからやり直してください')
        try:
            report = create_recovery_keys(args.output, encoded)
        except ValueError as error:
            parser.error(str(error))
        print(json.dumps(report, ensure_ascii=False))
        return
    if args.command in ('backup-encrypt', 'backup-decrypt'):
        if not args.manifest or not args.output or not args.backup_key:
            parser.error('--manifest・--output・--backup-keyが必要です')
        from .encrypted_backup import seal_file, unseal_file
        if args.command == 'backup-encrypt':
            if not args.backup_recipient:
                parser.error('--backup-recipientに確認済みの公開鍵SHA-256を指定してください')
            report = seal_file(args.manifest, args.output, args.backup_key, args.backup_recipient)
        else:
            # 復号用パスワードをコマンド履歴・引数・環境変数へ保存しない。
            from getpass import getpass
            import sys
            if not sys.stdin.isatty():
                raise ValueError('復号用パスワードは本人の対話端末で入力してください')
            password = getpass('復号用秘密鍵のパスワード: ').encode('utf-8')
            report = unseal_file(args.manifest, args.output, args.backup_key, password)
        print(json.dumps(report, ensure_ascii=False))
        return
    if args.command == "status":
        print(json.dumps(settings_status(), ensure_ascii=False))
        return
    if args.command == "doctor":
        report = diagnose(args.database, os.getenv("MINA_HEARTBEAT", "runtime/mina.heartbeat"), datetime.now(UTC))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if args.command == 'backup':
        from .backup import backup_database
        print(json.dumps(backup_database(args.database, args.output or Path(args.database).parent / 'backups')))
        return
    if args.command == 'handover-check':
        from .handover import check_handover
        report = check_handover(Path(__file__).resolve().parents[2], args.source_commit,
                  args.database, os.getenv('MINA_HEARTBEAT', 'runtime/mina.heartbeat'),
                  args.evidence or Path(os.getenv('MINA_HISTORY_FILE', '/var/lib/mina-agent/readiness.json')),
                  datetime.now(UTC))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if args.command == 'backup-artifacts':
        from .artifact_backup import backup_artifacts
        source = Path(os.getenv('MINA_PREPARE_DIRECTORY', '/var/lib/mina-agent/daily'))
        output = args.output or Path(args.database).parent / 'artifact-backups'
        print(json.dumps(backup_artifacts(source, output)))
        return
    if args.command == 'inspect-handoff':
        if not args.manifest or not args.contract_file or not args.producer_commit:
            parser.error('--manifest・--contract-file・--producer-commitが必要です')
        from .handoff_packet import inspect_handoff
        from .readiness import read_json
        print(json.dumps(inspect_handoff(read_json(args.manifest), args.contract_file,
                                        args.producer_commit), ensure_ascii=False, indent=2))
        return
    if args.command == 'history-draft':
        if not args.date or not args.output:
            parser.error('--date・--outputが必要です')
        from .history_draft import export_history_draft
        print(json.dumps(export_history_draft(args.database,args.date,args.output)))
        return
    if args.command == 'verify-artifacts':
        from .artifact_backup import verify_artifacts
        if not args.manifest:
            parser.error('--manifestに復元候補ディレクトリが必要です')
        print(json.dumps(verify_artifacts(args.manifest)))
        return
    if args.command == 'request-status':
        if os.getenv('MINA_READ_REVIEW_ENABLED', 'false').lower() != 'true':
            raise ValueError('照合・再レビューの接続は停止中です')
        if not args.request_key:
            parser.error('--request-keyが必要です')
        from .clients import MealClient
        from .m1_contract import request_report
        client = MealClient(os.getenv('MINA_MEAL_API_BASE_URL', ''), os.getenv('MINA_AGENT_API_TOKEN', ''))
        print(json.dumps(request_report(client.request_state(args.request_key, args.operation), args.request_key, args.operation)))
        return
    if args.command == "draft":
        if not args.date or not args.history or not args.output:
            parser.error("--date・--history・--outputが必要です")
        from .generation import draft_day
        from .codex_client import generation_client
        print(json.dumps(draft_day(args.date, args.history, args.output, generation_client()), ensure_ascii=False))
        return
    if args.command in ('prepare', 'prepare-today', 'resume-today', 'correct-today', 'resume-preparation'):
        if os.getenv('MINA_PREPARE_ENABLED', 'false').lower() != 'true':
            raise ValueError('日次準備は停止中です')
        if os.getenv('MINA_LIVE_WRITES', 'false').lower() != 'true':
            raise ValueError('Drive・LogEats書込みの許可が必要です')
    if args.command in ('request-reconcile', 'rereview', 'rebind-review', 'resume-today', 'correct-today', 'finalize-today', 'resume-preparation'):
        if os.getenv('MINA_READ_REVIEW_ENABLED', 'false').lower() != 'true':
            raise ValueError('照合・再レビューの接続は停止中です')
        if not Path(args.database).is_file():
            raise ValueError('既存の運用台帳が必要です')
    store = Store(args.database)
    now = datetime.now(UTC)
    if args.command == 'request-reconcile':
        if not args.request_key:
            parser.error('--request-keyが必要です')
        from .request_ledger import RequestLedger
        from .clients import MealClient
        result = RequestLedger(store).reconcile(args.request_key,
                    MealClient(os.getenv('MINA_MEAL_API_BASE_URL', ''), os.getenv('MINA_AGENT_API_TOKEN', '')))
        print(json.dumps(result))
    elif args.command == 'rebind-review':
        if not all((args.date, args.review_round, args.manifest, args.draft, args.bindings, args.evidence, args.output)):
            parser.error('--date・--review-round・--manifest・--draft・--bindings・--evidence・--outputが必要です')
        from .rebinding import rebind_day
        from .readiness import read_json
        from .clients import MealClient
        from .codex_client import generation_client
        from .ogp import capture_ogp
        rebind_day(store, args.date, args.review_round, read_json(args.bindings), read_json(args.evidence),
                   read_json(args.manifest), read_json(args.draft), generation_client(),
                   MealClient(os.getenv('MINA_MEAL_API_BASE_URL', ''), os.getenv('MINA_AGENT_API_TOKEN', '')),
                   os.getenv('MINA_SHARE_HOST', ''), args.output, lambda: datetime.now(UTC), capture_ogp,
                   args.source_commit, Path(__file__).resolve().parents[2])
        print(json.dumps({'target_date': args.date, 'state': 'approval_pending', 'post_count': 4}))
    elif args.command == 'rereview':
        if not args.date or not args.review_round or not args.manifest or not args.output or not args.draft or not args.evidence:
            parser.error('--date・--review-round・--manifest・--draft・--evidence・--outputが必要です')
        from .rereview import rereview_day
        from .readiness import read_json
        from .clients import MealClient
        from .codex_client import generation_client
        from .ogp import capture_ogp
        rereview_day(store, args.date, args.review_round, read_json(args.manifest), generation_client(),
                     MealClient(os.getenv('MINA_MEAL_API_BASE_URL', ''), os.getenv('MINA_AGENT_API_TOKEN', '')),
                     os.getenv('MINA_SHARE_HOST', ''), args.output, lambda: datetime.now(UTC), capture_ogp,
                     read_json(args.draft), args.source_commit, Path(__file__).resolve().parents[2], evidence=read_json(args.evidence))
        print(json.dumps({'target_date': args.date, 'state': 'approval_pending', 'post_count': 4}))
    elif args.command == 'resume-preparation':
        if not args.date or not args.review_round:
            parser.error('--dateと--review-roundに本人指定の再開対象が必要です')
        from .resume_preparation import resume_preparation
        from .clients import MealClient
        from .codex_client import generation_client
        result=resume_preparation(store,Path(os.getenv('MINA_PREPARE_DIRECTORY','/var/lib/mina-agent/daily')),
            args.date,args.review_round,Path(__file__).resolve().parents[2],args.source_commit,
            os.getenv('MINA_SHARE_HOST',''),generation_client(),
            MealClient(os.getenv('MINA_MEAL_API_BASE_URL',''),os.getenv('MINA_AGENT_API_TOKEN','')),
            Path(os.getenv('MINA_HISTORY_DIRECTORY','/var/lib/mina-agent/history')))
        print(json.dumps(result,ensure_ascii=False))
    elif args.command == 'finalize-today':
        if not args.review_round:
            parser.error('--review-roundに明示的な再点検のUUIDが必要です')
        from .saved_day import finalize_saved_day
        from .clients import MealClient
        from .codex_client import generation_client
        result = finalize_saved_day(store,Path(os.getenv('MINA_PREPARE_DIRECTORY','/var/lib/mina-agent/daily')),
            Path(__file__).resolve().parents[2],args.source_commit,os.getenv('MINA_SHARE_HOST',''),generation_client(),
            MealClient(os.getenv('MINA_MEAL_API_BASE_URL',''),os.getenv('MINA_AGENT_API_TOKEN','')),args.review_round,
            history_directory=Path(os.getenv('MINA_HISTORY_DIRECTORY','/var/lib/mina-agent/history')),
            publish_lunch_after_approval=args.publish_lunch_after_approval)
        print(json.dumps(result,ensure_ascii=False))
    elif args.command in ('resume-today', 'correct-today'):
        if not args.review_round:
            parser.error('--review-roundに明示的な再照合のUUIDが必要です')
        from .daily_pipeline import resume_today_read_failure
        from .codex_client import generation_client
        from .clients import MealClient
        result = resume_today_read_failure(store, Path(os.getenv('MINA_PREPARE_DIRECTORY', '/var/lib/mina-agent/daily')),
            args.source_commit, Path(__file__).resolve().parents[2], os.getenv('MINA_SHARE_HOST', ''),
            generation_client(), MealClient(os.getenv('MINA_MEAL_API_BASE_URL', ''), os.getenv('MINA_AGENT_API_TOKEN', '')),
            args.review_round, history_directory=Path(os.getenv('MINA_HISTORY_DIRECTORY', '/var/lib/mina-agent/history')), allow_correction=args.command == 'correct-today')
        print(json.dumps(result, ensure_ascii=False))
    elif args.command in ('prepare', 'prepare-today'):
        from .daily_pipeline import prepare_day
        from .codex_client import generation_client
        from .clients import MealClient
        from .drive import DriveClient
        history = args.history or Path(os.getenv('MINA_HISTORY_FILE', '/var/lib/mina-agent/readiness.json'))
        evidence = args.evidence or history
        if os.getenv('MINA_HISTORY_AUTO_ENABLED', 'false').lower() == 'true':
            from .automatic_history import prepare_history
            now = datetime.now(UTC)
            same_day = args.command == 'prepare-today'
            target = (now.astimezone(JST).date() + timedelta(days=0 if same_day else 1)).isoformat()
            if same_day:
                from .domain import slot_time, date
                if not slot_time(date.fromisoformat(target), 'breakfast') <= now < slot_time(date.fromisoformat(target), 'lunch'):
                    raise ValueError('当日試行は朝食時刻経過後から昼食前までです')
            # 履歴取得も19時から20時の準備窓内に限定する。
            if not same_day and now.astimezone(JST).hour != 19:
                raise ValueError('自動履歴取得は19時の準備窓内だけです')
            base = Path(os.getenv('MINA_HISTORY_DIRECTORY', '/var/lib/mina-agent/history'))
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
            history = prepare_history(store, args.database, target, base / (now.strftime('%Y%m%dT%H%M%S%fZ') + '.json'),
                                      Path(os.getenv('MINA_HISTORY_BOOTSTRAP', '/var/lib/mina-agent/history-bootstrap.json')),
                                      Path(os.getenv('MINA_CUTOVER_FILE', '/var/lib/mina-agent/cutover.json')),
                                      XClient(), lambda: datetime.now(UTC))
            evidence = history
        result = prepare_day(store, args.output or Path(os.getenv('MINA_PREPARE_DIRECTORY', '/var/lib/mina-agent/daily')),
                             history, evidence, args.source_commit, Path(__file__).resolve().parents[2],
                             os.getenv('MINA_SHARE_HOST', ''), generation_client(), DriveClient.from_env(),
                             MealClient(os.getenv('MINA_MEAL_API_BASE_URL', ''), os.getenv('MINA_AGENT_API_TOKEN', '')),
                             same_day=args.command == 'prepare-today')
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "images":
        if not args.manifest or not args.output:
            parser.error("--manifestに下書きJSON、--outputに画像保存ディレクトリが必要です")
        from .codex_client import generation_client
        from .preparation import prepare_images
        result = prepare_images(store, args.manifest, args.output, generation_client())
        result_path = args.output / 'images-reviewed.json'
        if result_path.exists():
            if json.loads(result_path.read_text(encoding='utf-8')) != result:
                raise ValueError('画像点検結果の版が変わっています')
        else:
            with result_path.open('x', encoding='utf-8') as file:
                json.dump(result, file, ensure_ascii=False, indent=2)
        print(json.dumps({"target_date": result["target_date"], "state": result["state"], "image_count": 3}))
    elif args.command == 'drive':
        if not args.manifest or not args.output:
            parser.error('--manifestに画像点検結果、--outputに保存結果JSONが必要です')
        if args.output.exists():
            raise ValueError('既存のDrive保存結果を自動上書きしません')
        from .drive import DriveClient
        from .preparation import save_images_to_drive
        result = save_images_to_drive(store, json.loads(args.manifest.read_text(encoding='utf-8')), DriveClient.from_env())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as file:
            json.dump(result, file, ensure_ascii=False, indent=2)
        print(json.dumps({'target_date': result['target_date'], 'state': result['state'], 'image_count': 3}))
    elif args.command == 'register':
        if not args.manifest or not args.draft or not args.output or not os.getenv('MINA_SHARE_HOST'):
            parser.error('--manifestにDrive保存結果、--draftに献立、--outputに結果JSONとMINA_SHARE_HOSTが必要です')
        if args.output.exists():
            raise ValueError('既存の登録結果を自動上書きしません')
        from .clients import MealClient
        from .codex_client import generation_client
        from .registration import register_day
        result = register_day(store, json.loads(args.draft.read_text(encoding='utf-8-sig')),
                              json.loads(args.manifest.read_text(encoding='utf-8-sig')), args.output.parent / 'ogp',
                              MealClient(os.getenv('MINA_MEAL_API_BASE_URL', ''), os.getenv('MINA_AGENT_API_TOKEN', '')),
                              generation_client(), os.environ['MINA_SHARE_HOST'])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as file:
            json.dump(result, file, ensure_ascii=False, indent=2)
        print(json.dumps({'target_date': result['target_date'], 'state': result['state'], 'post_count': 4}))
    elif args.command == "import":
        if not args.manifest or not os.getenv("MINA_SHARE_HOST"):
            parser.error("--manifestとMINA_SHARE_HOSTが必要です")
        target = import_manifest(store, args.manifest, os.environ["MINA_SHARE_HOST"], now)
        print(json.dumps({"target_date": target, "imported": 4}))
    elif args.command == "notify":
        target = args.date or (now.astimezone(JST).date() + timedelta(days=1)).isoformat()
        notify_day(store, target, now)
        print(json.dumps({"target_date": target, "prepared": True}))
    elif args.command == "tick":
        print(json.dumps(cycle(store, now)))
    elif args.command == "serve":
        serve(store, args.host, args.port)
    elif args.command == "gateway":
        from .gateway import run_gateway
        run_gateway(store, policy_from_env())
    else:
        heartbeat = Path(os.getenv("MINA_HEARTBEAT", "runtime/mina.heartbeat"))
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        while True:
            succeeded, mode = False, 'failed'
            try:
                result = cycle(store, datetime.now(UTC))
                succeeded, mode = True, result['mode']
                print(json.dumps(result), flush=True)
            except Exception:
                print('{"event":"mina_cycle_failed"}', flush=True)
            try:
                store.record_cycle_health(succeeded, datetime.now(UTC), mode)
            except Exception:
                print('{"event":"mina_health_record_unconfirmed"}', flush=True)
            heartbeat.write_text(str(time.time()), encoding="ascii")
            time.sleep(15)


if __name__ == "__main__":
    main()
