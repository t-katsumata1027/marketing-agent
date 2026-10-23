#!/bin/sh
# 本番専用CLIを配置する。認証情報は開発ユーザーからコピーしない。
set -eu
umask 077
test "$(id -u)" = 0 || { echo 'rootで実行してください' >&2; exit 1; }
id mina-agent >/dev/null
command -v node >/dev/null
command -v npm >/dev/null
install -d -m 755 /opt/marketing-agent/.codex-cli
npm install --prefix /opt/marketing-agent/.codex-cli --ignore-scripts --no-audit --no-fund --save-exact @openai/codex@0.160.1
chown -R root:root /opt/marketing-agent/.codex-cli
chmod -R go+rX /opt/marketing-agent/.codex-cli
install -d -m 700 -o mina-agent -g mina-agent /var/lib/mina-agent/.codex /var/lib/mina-agent/codex-jobs
# 秘密値を含む既存設定は表示せず、CLI固有の設定だけを更新する。
python3 - <<'PY'
from pathlib import Path
path = Path('/etc/marketing-agent/mina.env')
settings = {
    'HOME': '/var/lib/mina-agent',
    'MINA_AI_BACKEND': 'codex_cli',
    'MINA_CODEX_HOME': '/var/lib/mina-agent/.codex',
    'MINA_CODEX_WORKDIR': '/var/lib/mina-agent/codex-jobs',
    'MINA_CODEX_BIN': '/opt/marketing-agent/.codex-cli/node_modules/.bin/codex',
}
lines = [line for line in path.read_text().splitlines() if line.split('=', 1)[0] not in settings]
lines.extend(f'{key}={value}' for key, value in settings.items())
path.write_text('\n'.join(lines) + '\n')
path.chmod(0o600)
PY
runuser -u mina-agent -- env HOME=/var/lib/mina-agent CODEX_HOME=/var/lib/mina-agent/.codex /opt/marketing-agent/.codex-cli/node_modules/.bin/codex --version
echo '専用CLIを配置しました。ChatGPT認証とサービス内実行検証は別工程です。'
