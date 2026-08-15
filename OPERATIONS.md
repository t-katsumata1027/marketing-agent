# 運用・監視手順

このシステムは Windows 11 Pro 上の WSL2 Ubuntu で Docker Desktop を利用して動かします。Windows 側の作業フォルダではなく、WSL 内のリポジトリから実行してください。

## 秘密情報と Supabase

- バックエンドコンテナには `SUPABASE_SERVICE_ROLE_KEY` のみを注入します。旧 `SUPABASE_KEY` は移行期間だけ互換として読み取れますが、新規設定では使用しません。
- service role はブラウザ、Discord、Vercel の公開環境変数、クライアント JavaScript に絶対に渡しません。
- RLS は `service_role` 専用です。PostgREST の公開スキーマに RPC を置く場合も、migration の `REVOKE` と `GRANT` を維持し、anon/authenticated からの実行拒否をステージングで確認します。
- `SECURITY DEFINER` 関数は `search_path = public` を固定し、公開ロールへ実行権限を付与しません。

## 監視と障害対応

- `docker compose ps`、`docker compose logs --since=24h publisher`、`docker compose logs --since=24h agent-loop` を毎日確認します。ログは JSON で標準出力へ出力され、token 等の値はマスクされます。
- healthcheck は `/var/lib/jarvis/heartbeat` の更新時刻を確認します。unhealthy の場合は原因をログで確認してから `docker compose restart <service>` を実施します。
- DB では `posts_log.status in ('failed','publishing')`、`mentions_log.status='failed'`、`scheduled_job_runs.status='failed'` を監視対象とします。`publishing` かつ `publish_dispatch_started_at` がある投稿は X の投稿履歴と照合するまで再送しません。
- failed 投稿の復旧は `recover_failed_post` RPC のみで行います。X 未投稿を確認し、8文字以上の運用メモを残す必要があります。送信開始済み投稿はこの RPC では復旧できません。

## メディアの保持

- 画像は named volume の `/var/lib/jarvis/media` に保存されます。Pillow による PNG/JPEG、容量、幅・高さ検証を通過したものだけを使用します。
- 容量を定期確認します: `docker run --rm -v jarvis-marketing-agent_media-data:/data alpine du -sh /data`。削除前に DB の `media_path` を参照し、未公開・承認済み投稿のファイルは削除しません。
- 複数PC移行や長期保管には外部オブジェクトストレージへ移すまで、volume をバックアップしてください。`docker compose down -v` はメディアを消すため通常運用では実行しません。

## 導入前の確認

1. migration をステージングで適用し、service role のみ各 RPC を実行できることを確認する。
2. 2つの publisher を同時起動し、1つの approved 投稿が一度だけ claim されることを確認する。
3. Discord の許可 guild/channel/role を設定し、許可外の操作が無視されることを確認する。
4. 実投稿前は X のテストアカウントで、画像添付・返信・429時リトライ・DB更新失敗時の運用手順を確認する。
