# J.A.R.V.I.S Marketing Agent

X の投稿案生成、承認、公開、メンション返信、分析を常駐実行するバックエンドです。稼働環境は **Windows 11 Pro + WSL2 Ubuntu + Docker Desktop** を前提にします。Windows 側の `C:\project\marketing-agent` は対象外で、リポジトリは WSL の Linux ファイルシステム（例: `/home/<ユーザー>/jarvis-marketing-agent`）に置いてください。

## 構成と安全境界

- `agent-loop`: リスニング、返信下書き、プロモーション下書き、分析を実行します。
- `publisher`: DBで原子的に確保した承認済み投稿だけを X に送ります。
- `discord-bot`: 許可された Discord の管理者による下書きリライトだけを受け付けます。
- `preflight`: 外部接続をせず、必須環境変数と危険な設定を検証します。

すべて `production` プロファイルです。誤って `docker compose up` だけを実行して外部APIに接続しないよう、必ず `--profile production` を明示してください。Compose は Supabase migration を自動適用しません。

## Windows 11 Pro への移行

1. Docker Desktop をインストールし、Settings > Resources > WSL Integration で Ubuntu を有効にします。
2. PowerShell（管理者）で `wsl --install -d Ubuntu` を実行し、再起動後に Ubuntu の初期ユーザーを作成します。既存WSLの移送時は Microsoft のエクスポート/インポート手順を使います。
3. Ubuntu で Git を用いてリポジトリを `/home/<ユーザー>/jarvis-marketing-agent` に取得します。`/mnt/c` と共有フォルダーへ置かないでください。
4. `.python-version` の Python 3.12.12 と Docker Desktop が利用できることを確認します。WSL間で `.venv` をコピーせず、必要なら `python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt` で作り直します。
5. `.env.example` を `.env` にコピーし、WSL内でのみ値を設定します。`.env`、バックアップ、Docker volume を Git・クラウド同期・画面共有へ含めないでください。
6. 新しい Supabase プロジェクトを使う場合だけ、管理者が `supabase_migration.sql` を確認して適用します。既存DBを使う場合は、事前にバックアップを取り、差分をレビューします。
7. Vercel の承認エンドポイントを別の Next.js プロジェクトに配置します。`SUPABASE_SERVICE_ROLE_KEY` は Vercel のサーバー環境変数にのみ設定し、公開変数やブラウザへ渡しません。

## 起動・更新・停止

起動前検証は秘密値を表示しません。最初に次を実行します。

```bash
cd ~/jarvis-marketing-agent
cp .env.example .env
docker compose --profile production run --rm preflight
docker compose --profile production config --quiet
```

実運用開始後の起動、状態確認、更新、停止は以下です。

```bash
docker compose --profile production up -d --build
docker compose --profile production ps
docker compose --profile production logs --since=15m publisher
git pull --ff-only
docker compose --profile production up -d --build
docker compose --profile production down
```

`docker compose down -v` は named volume のメディアと heartbeat を削除します。バックアップ・復旧の意図がある場合以外は実行禁止です。Docker Desktop を再起動後は `docker compose --profile production up -d` を実行します。

## バックアップと復元

Supabase は管理画面または管理者が認めた手順で Postgres バックアップを取得・復元します。service role key をコマンド出力へ出さないでください。Dockerメディアのバックアップ例は、サービス停止後に安全な保存先へ tar を作る方法です。

```bash
docker compose --profile production down
docker run --rm -v jarvis-marketing-agent_media-data:/data -v "$PWD/backups:/backup" alpine tar czf /backup/media-$(date +%F).tgz -C /data .
docker volume create jarvis-marketing-agent_media-data
docker run --rm -v jarvis-marketing-agent_media-data:/data -v "$PWD/backups:/backup" alpine tar xzf /backup/media-YYYY-MM-DD.tgz -C /data
```

復元では最初に Supabase をポイントインタイム復元または検証環境へ戻し、次にメディアvolumeを復元します。`publishing` または `failed` の投稿は **自動再送しません**。X の投稿履歴を人が確認し、投稿済みなら Tweet ID を記録、未投稿なら新規下書きとして再承認します。

## 障害対応・監視・保持

- 毎日 `docker compose --profile production ps` と `logs --since=24h` を確認します。healthcheck が unhealthy ならログを保存してから対象サービスだけを `restart` します。
- JSON形式ログはDockerの `local` driver で1ファイル10MB、5世代に回転します。ログからトークンを隠す実装がありますが、本文には個人情報が入り得るため外部共有前に確認してください。
- `media-data` は投稿候補画像を保持します。保持期間と削除責任者を運用で定め、長期・複数ホスト運用ではオブジェクトストレージへ移行します。
- Discord、LLM、画像APIの一時障害はバックオフします。X投稿は重複を避けるため自動再試行しません。
- `approved → publishing → published` はDB RPCでclaimされます。二重Publisher起動を避け、DB migrationのRPC権限はservice role限定を維持します。

## 開発・検証・CI

依存関係は `requirements.txt` に完全固定されています。更新は専用ブランチで実施し、Python 3.12.12 で再生成・レビュー・テストしてからコミットしてください。新しい依存を追加する際も上限なし指定を禁止します。

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
python -m compileall -q src scripts
python -m unittest discover -s tests -v
```

GitHub Actions は Python 3.12.12 で構文検証と全ユニットテストを実行します。CIには秘密値を設定せず、実API、実Supabase、実X投稿、実Discord通知は実行しません。

## 承認と秘密情報

承認URLのGETは確認画面のみを返し、POSTが一回限り・期限付き・ハッシュ保存済みトークンを消費します。URLトークンは短命でも bearer token です。アクセスログ、画面共有、転送を避けてください。より厳格な運用ではログイン済み運用者だけが承認できる画面に移行します。

`.env.example` は変数名だけです。実キー、Supabase service role、Discord Webhook、X認証情報はリポジトリ、ログ、issue、Discord本文へ絶対に載せません。
