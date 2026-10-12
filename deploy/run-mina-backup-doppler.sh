#!/bin/sh
# 暗号化バックアップだけを保存する。業務API・通知・投稿の認証は渡さない。
set -eu
umask 077
cd /opt/marketing-agent
export MINA_BACKUP_UPLOAD_ENABLED="${MINA_BACKUP_UPLOAD_ENABLED:-false}"
unset MINA_AGENT_API_TOKEN MINA_MEAL_API_BASE_URL DISCORD_BOT_TOKEN MINA_DISCORD_PUBLIC_KEY \
    X_API_KEY X_API_SECRET X_ACCESS_TOKEN X_ACCESS_TOKEN_SECRET OPENAI_API_KEY GEMINI_API_KEY
case "${1:-}" in
    backup-upload|backup-run|backup-reconcile) ;;
    *) echo '暗号化バックアップの保存コマンドだけを指定してください' >&2; exit 2 ;;
esac
if /usr/bin/doppler run --silent --no-check-version --no-fallback \
    --config-dir /var/lib/mina-agent/.doppler --project marketing-agent \
    --config "${MINA_DOPPLER_CONFIG:-dev}" \
    --only-secrets MINA_GOOGLE_CLIENT_ID,MINA_GOOGLE_CLIENT_SECRET,MINA_GOOGLE_REFRESH_TOKEN,MINA_DRIVE_FOLDER_ID \
    --preserve-env=MINA_BACKUP_UPLOAD_ENABLED \
    -- /opt/marketing-agent/.venv-mina/bin/python -m src.mina.backup_entry "$@" 2>/dev/null; then
    exit 0
else
    status=$?
    echo 'backup_process_failed: 秘密値を含まない診断イベントと終了コードを確認してください' >&2
    exit "$status"
fi
