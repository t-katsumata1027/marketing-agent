#!/bin/sh
# サービス用の限定トークンで秘密値を注入し、停止スイッチはローカル設定を優先する。
set -eu
umask 077
cd /opt/marketing-agent
export MINA_LIVE_WRITES="${MINA_LIVE_WRITES:-false}"
export MINA_APPROVAL_ENABLED="${MINA_APPROVAL_ENABLED:-false}"
export MINA_PUBLISH_ENABLED="${MINA_PUBLISH_ENABLED:-false}"
export MINA_DATABASE="${MINA_DATABASE:-/var/lib/mina-agent/mina.sqlite3}"
export MINA_HEARTBEAT="${MINA_HEARTBEAT:-/var/lib/mina-agent/mina.heartbeat}"
export MINA_AI_ENABLED="${MINA_AI_ENABLED:-false}"
export MINA_PREPARE_ENABLED="${MINA_PREPARE_ENABLED:-false}"
export MINA_READ_REVIEW_ENABLED="${MINA_READ_REVIEW_ENABLED:-false}"
export MINA_X_HISTORY_READ_ENABLED="${MINA_X_HISTORY_READ_ENABLED:-false}"
export MINA_HISTORY_AUTO_ENABLED="${MINA_HISTORY_AUTO_ENABLED:-false}"
case "${1:-}" in
    doctor|status|handover-check|request-status|request-reconcile|rereview|rebind-review|prepare|prepare-today|resume-today|correct-today|finalize-today|resume-preparation|draft|images|drive|register|notify|tick|daemon|serve|import|gateway) ;;
    *) echo 'みなの実行コマンドを指定してください' >&2; exit 2 ;;
esac
# キャッシュへの退避を無効化し、取得不能なら起動を止める。
# 設定取得に失敗しても、Dopplerの応答本文・秘密値をサービスログへ出さない。
exec /usr/bin/doppler run --silent --no-check-version --no-fallback \
    --config-dir /var/lib/mina-agent/.doppler \
    --project marketing-agent --config "${MINA_DOPPLER_CONFIG:-dev}" \
    --only-secrets MINA_SHARE_HOST,MINA_MEAL_API_BASE_URL,MINA_AGENT_API_TOKEN,MINA_DISCORD_APPLICATION_ID,MINA_DISCORD_PUBLIC_KEY,MINA_APPROVER_USER_IDS,DISCORD_BOT_TOKEN,DISCORD_ALLOWED_GUILD_ID,DISCORD_ALLOWED_CHANNEL_ID,X_API_KEY,X_API_SECRET,X_ACCESS_TOKEN,X_ACCESS_TOKEN_SECRET,MINA_GOOGLE_CLIENT_ID,MINA_GOOGLE_CLIENT_SECRET,MINA_GOOGLE_REFRESH_TOKEN,MINA_DRIVE_FOLDER_ID,BUFFER_API_KEY \
    --no-exit-on-missing-only-secrets \
    --preserve-env=MINA_LIVE_WRITES,MINA_APPROVAL_ENABLED,MINA_PUBLISH_ENABLED,MINA_DATABASE,MINA_HEARTBEAT,MINA_AI_ENABLED,MINA_PREPARE_ENABLED,MINA_READ_REVIEW_ENABLED,MINA_X_HISTORY_READ_ENABLED,MINA_HISTORY_AUTO_ENABLED \
    -- /opt/marketing-agent/.venv-mina/bin/python -m src.mina "$@" 2>/dev/null
