#!/bin/sh
# Instagram専用の処理だけを実行する。秘密・設定の取得失敗は原文をログへ出さない。
set -eu
umask 077
cd /opt/marketing-agent
case "${1:-}" in
    status|render|artwork|accept|prepare|prepare-next|draft|notify|reconcile|tick|daemon) ;;
    *) echo 'Instagramの実行コマンドを指定してください' >&2; exit 2 ;;
esac
/usr/bin/doppler run --silent --no-check-version --no-fallback \
    --config-dir /var/lib/mina-agent/.doppler \
    --project marketing-agent --config "${MINA_DOPPLER_CONFIG:-dev}" \
    --only-secrets MINA_MEAL_API_BASE_URL,MINA_AGENT_API_TOKEN,DISCORD_BOT_TOKEN,DISCORD_ALLOWED_CHANNEL_ID,MINA_GOOGLE_CLIENT_ID,MINA_GOOGLE_CLIENT_SECRET,MINA_GOOGLE_REFRESH_TOKEN,MINA_DRIVE_FOLDER_ID,BUFFER_API_KEY \
    --no-exit-on-missing-only-secrets \
    --preserve-env=MINA_LIVE_WRITES,MINA_DATABASE,MINA_DAILY_DIRECTORY,MINA_INSTAGRAM_DIRECTORY,MINA_INSTAGRAM_PREPARE_ENABLED,MINA_INSTAGRAM_NOTIFY_ENABLED,MINA_INSTAGRAM_APPROVAL_ENABLED,MINA_INSTAGRAM_PUBLISH_ENABLED \
    -- /opt/marketing-agent/.venv-mina/bin/python -m src.mina.instagram_cli "$@" 2>/dev/null || {
    instagram_exit_status=$?
    printf '{"event":"instagram_process_exit","exit_status":%s}\n' "$instagram_exit_status"
    exit "$instagram_exit_status"
}
