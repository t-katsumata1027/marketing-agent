# みな運用・最初の実装

> この文書の初期実装説明は2026年10月7日の履歴を含む。最新の実装・停止状態・切替条件は`IMPLEMENTATION_STATUS.md`、`PRODUCTION_RUNBOOK.md`、`RELEASE_PREPARATION_20261011.md`を優先する。現在は19時準備タイマー・CLI生成・Drive・M1照合・再レビューの実装があるが、実日次運用は未受入。既存サービスへ`install-mina.sh`を再実行して最新版へ更新する手順ではない。

この実装は、完成した4候補の取り込み、20時の通知キュー作成、署名付きDiscord承認、時刻到達した承認済み投稿の公開を検証する。既定は外部接続なしの検証モードである。

本番運用の完成ではない。献立・画像生成、Drive保存、画像と解析値の独立レビュー、履歴照会、接続設定、Discord承認の実操作検証が残っている。`Candidate`の確認項目は単なるAI自己申告で埋めず、各工程の実際の結果から作成する。確認できない場合は候補を取り込まない。

Discord受信はGateway常時接続を採用する。`mina-gateway.service`はBotの認証済み接続からボタンを受け取り、許可リストと候補の版を検査する。HTTP署名付き受付は代替であり、Gateway使用時には起動しない。公開URL・特権Intent・メッセージ本文の受信は不要。

## 実行

```powershell
python -m venv .venv-mina
.\.venv-mina\Scripts\python.exe -m pip install -r requirements-mina.txt
.\.venv-mina\Scripts\python.exe -m unittest discover -s tests/mina -v
.\.venv-mina\Scripts\python.exe -m src.mina status
.\.venv-mina\Scripts\python.exe -m src.mina doctor
.\.venv-mina\Scripts\python.exe -m src.mina tick
```

みなモードは従来の検索・自動返信・CrewAIに依存しない。今回はSQLiteを単一サブPC検証用の台帳に採用した。設計書で提案した本番共有Supabaseへの接続・移行は未実装であり、同じDBファイルをネットワーク共有で複数ホストから開かない。

## 候補ファイル

`src/mina/domain.py`の`Candidate`に対応したJSONオブジェクト4件の配列を取り込む。日付、朝昼夕・日次の4枠、API固定文、共有URL、献立、カロリー/PFC、元画像ハッシュ、OGPハッシュ、各判定、実装commitを必要とする。元画像ハッシュは画像バイト列のSHA-256、OGPハッシュは`ogp_snapshot`が返す正規化した主要メタ項目のSHA-256。OGPの意味と画像内容の照合は独立レビュー側で行う。過去の献立・語尾・フック判定もレビュー側の責任で、現在の機械検査は完全一致コメント等の基本検査までである。

```powershell
$env:MINA_SHARE_HOST = "正式な共有ページのホスト名"
.\.venv-mina\Scripts\python.exe -m src.mina import --manifest runtime/day.json
```

取り込みで予定日時を勝手に変更できない。承認後の差し替えは承認と旧ボタンを失効させる。画像はXへ添付しない。

## 本番への切り替え条件

1. 接続設定、候補生成・独立レビュー、履歴と登録成否照会を整備する。
2. Discord Appの公開鍵・App ID・許可guild/channel/userを設定し、Gatewayで実カードの承認を検証する。HTTP受付を代わりに使用する場合だけHTTPS入口を整備し、Gatewayと同時には使用しない。
3. テスト用DiscordとXで操作を確認する。実X既存予約との衝突を照合する。
4. 対象の本番投稿を確認後、`MINA_LIVE_WRITES=true`へ切り替える。承認受付は`MINA_APPROVAL_ENABLED`、X公開は`MINA_PUBLISH_ENABLED`の独立スイッチで有効にする。

実装は枠別の承認・差戻し・見送りに対応する。一括承認と差戻し理由modalは後続。SQLite内のカードには操作nonceがあるためDBを公開しない。ログや画面共有に設定ファイルやDB内容を出さない。

## サブPC

`deploy/install-mina.sh`はrootで実行し、専用の`mina-agent`ユーザーと`/opt/marketing-agent`、`/var/lib/mina-agent`を使用する。既存GitHub Actionsランナーとは分離する。検証daemonと20時timerのみ起動し、Discord HTTP受付・実送信は起動しない。

19時の献立・画像生成timerは生成実装が完成してから追加する。現段階で20時timerが動いても、Discord通知は実送信されず台帳に積まれる。候補が未完成の場合は不足枠の通知を積む。

Pythonは現在のWSLの3.12.3で初期検証する。本番指定3.12.12への固定とWindows再起動後のWSL自動復帰は後続確認が必要。

## 本番用Codex CLI

`deploy/install-mina-cli.sh`はrootでCLIの検証済み版0.160.1を`/opt/marketing-agent/.codex-cli`へ配置し、専用ユーザーの認証・作業領域を整備する。既存サービスの再起動や本番送信の有効化は行わない。

ChatGPT認証は専用ユーザーで実施する。開発ユーザーの認証ファイルはコピーしない。次のコマンドが示すURLと一時コードを本人がブラウザーで入力する。認証ファイルはパスワードと同様に扱い、内容を表示しない。

```sh
runuser -u mina-agent -- env HOME=/var/lib/mina-agent CODEX_HOME=/var/lib/mina-agent/.codex /opt/marketing-agent/.codex-cli/node_modules/.bin/codex login --device-auth
```

認証後も、実際のsystemd制限下で構造化出力・画像生成・独立レビューを検証する。公開daemonの512MB制限を画像生成プロセスへ流用せず、生成専用サービスのメモリ・時間・CPU上限を実測から設定する。CLI子プロセスへ業務サービスの秘密値やAPIキーを渡さない。

失敗後、`dispatching`、通知`sending`、`outcome_unknown`は自動再送しない。外部状態を照合した復旧操作と通知の限定再送UIは後続実装。監視は生存heartbeatと、処理エラーのログを両方確認する。
