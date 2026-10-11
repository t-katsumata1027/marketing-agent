#!/bin/sh
# 検証モードのみを配置する。既存設定や本番送信を有効にしない。
set -eu
umask 077
test "$(id -u)" = 0 || { echo 'rootで実行してください' >&2; exit 1; }
test -d /opt/marketing-agent/src/mina
if ! id mina-agent >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/mina-agent --shell /usr/sbin/nologin mina-agent
fi
install -d -m 700 -o mina-agent -g mina-agent /var/lib/mina-agent
install -d -m 700 /etc/marketing-agent
if [ ! -f /etc/marketing-agent/mina.env ]; then
    install -m 600 /opt/marketing-agent/deploy/mina.env.example /etc/marketing-agent/mina.env
fi
# 既存の本番設定がある場合は、この導入スクリプトから起動しない。
grep -qx 'MINA_LIVE_WRITES=false' /etc/marketing-agent/mina.env
chown -R root:root /opt/marketing-agent
chmod -R go+rX /opt/marketing-agent
python3 -m venv /opt/marketing-agent/.venv-mina
/opt/marketing-agent/.venv-mina/bin/python -m pip install -r /opt/marketing-agent/requirements-mina.txt
chmod -R go+rX /opt/marketing-agent/.venv-mina
install -m 644 /opt/marketing-agent/deploy/systemd/mina-agent.service /etc/systemd/system/
install -m 644 /opt/marketing-agent/deploy/systemd/mina-notify.service /etc/systemd/system/
install -m 644 /opt/marketing-agent/deploy/systemd/mina-notify.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mina-agent.service mina-notify.timer
# 認証・HTTPS入口の設定前に承認サーバーを自動起動しない。
echo '検証モードを導入しました。本番書き込みと承認HTTP受付は無効です。'
