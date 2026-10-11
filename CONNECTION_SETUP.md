# みなの接続設定

## 現在の準備順序

1. サブPCの開発ユーザーをDopplerにログインさせる。
2. `marketing-agent / dev`へ接続設定を登録する。
3. Discordの通知先・承認者を設定する。
4. LogEatsの内部APIと共有ホスト、X API認証を設定する。
5. 生成・Drive・独立レビュー・履歴照会の処理を接続する。
6. Discord常時接続で承認ボタンの実操作を確認する。
7. サービスユーザーへ限定したDopplerサービス用トークンを配置し、本番切替を検証する。

現在、開発環境・サービス配置・20時の通知キュー・承認処理・公開処理は実装済みだが、認証設定と候補生成は未完了。

## Doppler

本プロジェクトは別のDopplerアカウントのWorkplaceで運用する。新しいWorkplaceに`marketing-agent`プロジェクトと`dev` configを作成する。旧Workplaceのプロジェクトは今回削除しない。

開発ユーザーのCLIログインは開発フォルダーにスコープを限定し、さらに設定保存先を`/home/logeats-runner-win/.doppler-marketing-agent`へ分離する。他プロジェクトの認証は変更しない。ログイン用の一時コードは長期資格情報ではなく、認証完了後に文書へ保存しない。

```sh
cd /home/logeats-runner-win/projects/marketing-agent
doppler run --config-dir /home/logeats-runner-win/.doppler-marketing-agent --no-fallback --project marketing-agent --config dev -- .venv-mina/bin/python -m src.mina doctor
```

秘密値を表示する`doppler secrets`やトークン取得の`--plain`を診断に使用しない。設定が不足している場合は`doctor`に表示された設定名を登録する。

新しいWorkplaceへの認証と`marketing-agent`プロジェクトの作成を確認済み。`dev`には`MINA_LIVE_WRITES`、`MINA_APPROVAL_ENABLED`、`MINA_PUBLISH_ENABLED`をすべて`false`で登録した。今後はこの新しいWorkplaceの画面へ接続設定を入力する。上記CLIは開発フォルダー内で実行し、他プロジェクトにはこのconfig-dirを使わない。

サービスの接続には開発者のCLIトークンを流用せず、該当configだけを読めるサービス用トークンを使用する。`deploy/doppler.env.example`の実値は`/etc/marketing-agent/doppler.env`へroot所有・0600で保存する。停止スイッチは既存の`mina.env`で管理し、Dopplerから上書きしない。

`deploy/run-mina-doppler.sh`とsystemdの切替例を用意したが、まだ適用していない。秘密値取得を行うサービスへ変える前に、同じサービスユーザー・設定で`doctor`を実行し、値が表示されないことと不足項目を確認する。20時のoneshot通知は台帳作成だけで、実送信はdaemonから行う。

公式資料: [CLI](https://docs.doppler.com/docs/cli)、[サービス用トークン](https://docs.doppler.com/docs/service-tokens)。

## Discordで最初に用意するもの

[Discord Developer Portal](https://discord.com/developers/applications)でみなの承認用Applicationを作成する。

| 設定名 | 登録するもの |
|---|---|
| `MINA_DISCORD_APPLICATION_ID` | General InformationのApplication ID |
| `MINA_DISCORD_PUBLIC_KEY` | General InformationのPublic Key |
| `DISCORD_BOT_TOKEN` | BotのToken。秘密値をDopplerへ直接入力する |
| `DISCORD_ALLOWED_GUILD_ID` | 承認用サーバーのID |
| `DISCORD_ALLOWED_CHANNEL_ID` | 承認専用チャンネルのID |
| `MINA_APPROVER_USER_IDS` | 承認者のユーザーID。複数ならカンマ区切り |

Botには対象チャンネルの閲覧・メッセージ送信・埋め込みリンクだけをまず付与する。管理者権限は不要。サーバー・チャンネル・ユーザーIDはDiscordの開発者モードからコピーする。

本運用ではGateway常時接続で承認ボタンを受信するため、Interactions Endpoint URLは空のままにする。サブPCからDiscordへ外向きに接続し、公開URLとルーターのポート開放は不要。GatewayとHTTPの受信は排他的なので、Endpointが設定済みなら起動を止める。通知送信を確認してから承認ボタンの動作へ進む。

GatewayはBotの認証済みTLS接続で受信し、Application・サーバー・チャンネル・承認者・イベント時刻・候補の版・操作nonceを検査する。HTTP署名付きの受付も代替実装として残すが、同時には有効化しない。公式資料: [Interactionの受信方式](https://docs.discord.com/developers/interactions/receiving-and-responding)。

公式資料: [OAuth2](https://discord.com/developers/docs/topics/oauth2)。

## AI生成の認証と実行方式

現在の主構成は、ChatGPT認証済みのCodex CLIをPythonから非対話で呼ぶ方式。デスクトップアプリは不要で、CLIへ業務用APIの秘密値を渡さない。本番専用ユーザーの認証と、実画像生成・画像点検・認証更新を確認してから日次タイマーを有効化する。

`draft`は未レビューの候補準備であり、API解析・Drive・OGP・履歴・既存予約・ユーザー承認を代替しない。生成の起動は`MINA_AI_ENABLED`で独立管理する。CodexClientはまだ試作で、本番のプロセス管理・証跡保全を整備する。

OpenAI APIキーはDopplerへ登録済みだが、生成APIの初回試行はHTTP 429となった。CLI運用の前提資格情報にはしない。CLIの実地検証後、必要な工程のみOpenAIまたはGemini APIを採用できる設計とし、モデル・予算・キーを決めてから実行する。Discord承認やX公開にはそれぞれの認証を使う。

公式資料: [構造化出力](https://developers.openai.com/api/docs/guides/structured-outputs)、[画像生成API](https://developers.openai.com/api/reference/resources/images/methods/generate)。

## XとLogEatsの設定項目

| 設定名 | 用途 |
|---|---|
| `X_API_KEY` / `X_API_SECRET` | X開発用Applicationのキー |
| `X_ACCESS_TOKEN` / `X_ACCESS_TOKEN_SECRET` | みなアカウントの書き込み可能なユーザー認証 |
| `MINA_AGENT_API_TOKEN` | LogEatsの内部みなAPI用の限定トークン |
| `MINA_MEAL_API_BASE_URL` | 食事登録・日次共有APIの基点URL |
| `MINA_SHARE_HOST` | 公開用の共有ページのホスト名。URL全体ではなくホストだけ |

ユーザー認証先が`EatsLog88161`に一致することを実装側で検査する。APIキーがあるだけでは公開開始せず、書き込み権限・実アカウント・既存予約との衝突を確認する。LogEats内部トークンは別プロジェクトの正本設定と整合させる。

2026年10月7日のVercel設定名の確認で、LogEats本番・previewに`MINA_AGENT_API_TOKEN`と`MINA_AGENT_ACCOUNT_EMAIL`が既に存在した。秘密値の取得・更新は行っていない。サブPCへ新しいトークンを勝手に作らず、LogEats本体のDoppler正本から既存のみなトークンを新Workplaceの`marketing-agent / dev`へ設定する。本体の一般AIキーやDB接続情報は渡さない。

## サーバー用Google Drive認証

同じGoogleアカウントを使用することはユーザー確認済み。アプリのGoogle Drive連携はサーバーのPythonプロセスへ自動継承されないため、専用OAuth認証を整備する。

| Doppler設定名 | 内容 |
|---|---|
| `MINA_GOOGLE_CLIENT_ID` | 専用OAuthクライアントID |
| `MINA_GOOGLE_CLIENT_SECRET` | 専用OAuthクライアント秘密値 |
| `MINA_GOOGLE_REFRESH_TOKEN` | 本人の同意で取得するオフライン認証のrefresh token |
| `MINA_DRIVE_FOLDER_ID` | 専用の今後の投稿画像保存先。日付期限のある過去画像フォルダーは流用しない |

Google CloudでDrive APIを有効にし、OAuth同意画面と専用クライアントを用意する。本人が同意してrefresh tokenを取得し、秘密値はDopplerへ直接保存する。最小スコープ`drive.file`を基本とし、このクライアントが作成した新しい専用フォルダー、またはPickerで明示的に許可したフォルダーを使用する。既存フォルダーIDを知っているだけではアクセス許可にならない。

2026年10月7日の本人の画面で、Google Cloudの`logeats`にはレポート用サービスアカウントだけがあり、OAuthクライアントはないことを確認した。同じプロジェクトへ専用OAuthクライアントを追加する。種類は「デスクトップ アプリ」、名前は`mina-marketing-agent`とする。この種類は初回認証のループバック受信方式を表し、ChatGPTデスクトップアプリの常時起動を意味しない。

準備順序:

1. `logeats`でGoogle Drive APIを有効化する。
2. 「OAuth同意画面」からGoogle Auth Platformの初期設定を開始する。アプリ名`LogEats Mina Marketing Agent`、サポート・連絡先は本人のメールを使用する。
3. 個人Googleアカウントを使用する場合は対象を「外部」とし、テスト段階では画像を保存する本人のGoogleアカウントをテストユーザーへ追加する。
4. データアクセスへ`https://www.googleapis.com/auth/drive.file`だけを追加する。
5. クライアント画面で「デスクトップ アプリ」の`mina-marketing-agent`を作成する。ID・秘密値は新Dopplerの対応項目へ登録する。
6. 本人の同意とrefresh token取得、専用フォルダー作成、サブPCでの保存・再読取・認証更新を確認する。
7. 長期運用前にOAuth公開状態を確認する。外部アプリのテスト状態ではDriveを含むrefresh tokenが7日で失効するため、テスト状態を無人運用の完成と扱わない。

手順根拠: [Google Auth Platformの設定](https://developers.google.com/workspace/guides/configure-oauth-consent)、[デスクトップOAuth](https://developers.google.com/identity/protocols/oauth2/native-app)、[refresh tokenの失効](https://developers.google.com/identity/protocols/oauth2#expiration)。

初回同意を終えても、OAuthアプリの公開状態・refresh tokenの期限・再認証方法を確認し、長期の無人運用が可能か検証する。アクセス・refresh tokenを工程台帳やログへ保存しない。

2026年10月7日に専用クライアント`LogEats Mina Marketing Agent`の初回同意が完了した。refresh tokenと新規作成した専用フォルダー`AI広報みな marketing-agent`のIDはDopplerへ保存済み。本番専用ユーザーから認証更新、点検済み画像1枚の保存と実体再読取を確認した。OAuth公開状態は本人への確認待ち。

初回認証は開発環境で`python -m src.mina.google_auth_setup --config-dir <専用Doppler設定ディレクトリ>`を実行し、そのループバックポートをSSHでメインPCへ転送する。本人がメインPCのブラウザーで同意する。既存refresh tokenがある場合は再認証を開始せず、保存結果を先に照合する。Doppler保存の成否不明時だけ`runtime/google-oauth/refresh-pending.json`を所有者限定の復旧用ファイルとして保持する。これは工程台帳へ取り込まず、値を表示せずに照合・保存後に削除する。

画像は4MB以下のJPEGとして保存し、保存名・親フォルダー・容量・MD5と再ダウンロードしたSHA-256を照合する。書き込み成否不明の場合は自動再送しない。アップロード成功後の読取失敗は、保存済みIDを使って読取だけをやり直す。

公式資料: [アップロード](https://developers.google.com/workspace/drive/api/guides/manage-uploads)、[ダウンロード](https://developers.google.com/workspace/drive/api/guides/manage-downloads)、[オフラインOAuth認証](https://developers.google.com/identity/protocols/oauth2/web-server#offline)。

## LogEats M1接続の準備

接続側の契約基準はLogEatsの`docs/marketing/MINA_M1_API_CONTRACT.md`。2026年10月7日のローカル仕様へ追従したが、本番未適用である。移行・正規リリース・受入確認前に本番のM1登録を開始しない。

送信前にUUIDとexpected_state_versionを工程台帳へ保存し、食事・日次要求には`contract_version: mina-write-v1`を指定する。旧形式への自動退避は行わない。UUID照会の404やsaved状態の失敗を未登録と判定せず、同じ食事を再送しない。

承認対象の本文は検証した`result.x.publish_text`をそのまま使用する。公開前には対象日state、UUID照会の`current_result`、現在のOGP実画像を照合する。保存時の`result`だけで現在の正本を保証しない。変更・期限切れ・取得失敗は公開前停止とし、承認を失効する。X送信後の成否不明は再送停止のまま照合を待つ。
