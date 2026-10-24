# サブPCでの開発

## 2026年10月24日13:39・当日限定救済Agentを本番配置、救済OFF

本人の条件付き承認に基づき、最終CI40049695981と本体固定5001250の専用Preview40059175879成功を確認してAgent5dbc45bを配置。既存27表全行不変・追加7空台帳、実3サービス子プロセスで救済false、UTM/通常置換true保持。起動監査2件のみ追記、巡回失敗0、既存5timer active。食事登録/置換・実課金・投稿・Log-Eats本体変更は0。本体PR151本番導入、救済ON、当日L/Rは別承認。退避/var/lib/mina-agent/deployment-rescue-off-5dbc45b、旧コード/opt/marketing-agent-rollback-rescue-5dbc45b。詳細はMINA_RESCUE_AGENT_DEPLOYMENT_20261024.md。下記の未配置記述は以前の時点の記録。


## 2026年10月24日・当日限定救済のAgent実装（本番未配置）

本人承認の実装・隔離検証・独立レビュー・PR作成範囲で、稼働f2119d4系を保持したL許可・mina-rescue-v1 adapter・専用台帳を追加。本体固定48e5459/Schema SHA1532cae8を参照し、本体変更・本番配置／有効化・実課金・食事登録／置換・投稿は行わない。詳細はMINA_RESCUE_AGENT_IMPLEMENTATION_20261024.md。独立最終判定・CIは専用証跡を参照し、先行設計Goと混同しない。


## 2026年10月24日08:38・Agent初回P補修の配置・有効化完了

本人承認に基づきf2119d4（受入実装6c43282と同一の実行コード）を配置し、MINA_MEAL_REPLACEMENT_ENABLED=trueと専用設定/etc/marketing-agent-replacement/agent.jsonを反映。UTMと他の運用設定を保持。Linux対象80件・私用DB実構築・全状態全4ページの公開先照合を経て、切替後は業務25表/既存監査行不変、起動監査2件追記、3サービス/3タイマーactive・巡回失敗0。退避は/var/lib/mina-agent/deployment-initial-f2119d4-r2、旧コードは/opt/marketing-agent-rollback-initial-f2119d4。Instagram draft4件の停止条件を維持し、実置換・投稿・過去日救済・期限延長は行っていない。詳細と設定読取失敗からの復帰経緯はMINA_AGENT_REPLACEMENT_ACTIVATION_20261024.md。

## 2026年10月24日・提供側本番ONの読取確認、Agent配置は別段階

本体761a55f6の配備/ON報告を受領し、07:24 JSTの認証付きGETで固定Schema・確定Gemini/waived policy・両digest・M1との版/画像一致を確認した。Agent設定は8ac2fdc・置換OFF・UTM ONを保持し、Agent修正版6c43282の配置や設定変更は行っていない。集約期限false/nullを補完せず、過去日の適格性も付与しない。提供側Previewの最新成功と過去の失敗runを区別する。詳細と次段階の境界はMINA_PROVIDER_RELEASE_ACCEPTANCE_20261024.mdを参照。

## 2026年10月23日・初回P補修は隔離受入まで、本番未配置

本体PR149の固定0e80de53を用いる隔離HTTP受入に対応し、Agent固定6c43282で初回Pの原本移行・期限・所有/epoch・試行消費と明示resumeのpolicy再確認を補修した。実ハンドラーHTTP41件・対象回帰70件・adapter型検査成功、独立レビューGo。今回の配置や設定反映は実行せず、本番コード8ac2fdc・置換OFFを保持する。本体Previewの後続C90秒タイムアウト1件も未合格のまま。再現手順と要求数・SHAはMINA_INITIAL_PREPARE_AGENT_ACCEPTANCE_20261023.mdを参照。

## 2026年10月23日・Gemini一食置換コード配置、機能OFF

本人が100円/日を撤回し金額上限なしへ変更。提供側66be008の新Schema SHA 4f8d2be8を照合し、waived/cooldown/明示resume・元UUID/制御UUID照会・saved保持へ対応。手動履歴対応be109b3を保持する8ac2fdcを、Windows/Linux各541件・CI39944266392両job成功後に配置した。切替時26台帳不変、起動後は既存行不変・起動監査2件追記のみ、3サービス/3タイマーactive・巡回失敗0。UTM ON・置換OFF。提供側policy未設定、Instagramのdraft4件（既存台帳照合2件・未照合2件）が開始を阻害する。24日の部分日422や期限は変更しない。詳細はMINA_REPLACEMENT_GEMINI_ACCEPTANCE_20261023.md、提供側への依頼はLOGEATS_REPLACEMENT_POLICY_HANDOFF_20261023.md。

## 2026年10月23日・Xの新規候補へUTM付与を有効化

本番固定版afd369c87fde43f71205d50e0c7dd3de8c08d512、保護設定MINA_X_UTM_ENABLED=true。旧候補・承認・台帳は保持し、新規候補の承認元へ計測policyを固定する。Linux440件・CI・本番4枠読取照合成功。3サービス・3タイマーactive。保護バックアップは/var/lib/mina-agent/deployment-utm-afd369c87f。一食置換統合は未配置・無効、提供側設定の確認待ち。詳細はMINA_X_UTM_RELEASE_20261023.mdとMINA_REPLACEMENT_PRODUCTION_READINESS_20261023.md。

## 2026年10月22日・Instagram公開結果の自動照会を接続

稼働f35a7edを保持した1539fd5を配置。公開確認の初回待機を超えても、保存済み受付IDをバックオフ付きで読取照会し、公開済みと解消通知へ進める。新規投稿・IDなし再送・未知結果の未保存扱いはしない。Windows/Linux各417件・CI・実受付を使う私用台帳コピーの確認成功。切替時24台帳を保持し、配置後は稼働記録以外の台帳不変、未解決0件・平常時API計数増加0。3サービス・3タイマーactive。保護バックアップは`/var/lib/mina-agent/deployment-instagram-delivery-1539fd59f7`。詳細は`MINA_INSTAGRAM_DELIVERY_RECONCILIATION_20261022.md`。

## 接続と配置

ChatGPTアプリのSSH接続は`katsu-server1-wsl`を使用する。Windowsへの管理用SSH別名`katsu-server1`は、この接続の経由先なのでSSH設定から削除しない。

| 用途 | サブPC内のパス |
|---|---|
| 開発用Gitチェックアウト | `/home/logeats-runner-win/projects/marketing-agent` |
| 引き継ぎ文書の参照用コピー | `/home/logeats-runner-win/projects/handoff/logeats-marketing-20261007` |
| サービス用コード | `/opt/marketing-agent` |
| サービス台帳・heartbeat | `/var/lib/mina-agent` |
| 保護された運用設定 | `/etc/marketing-agent/mina.env` |
| 保護されたDopplerサービス用認証 | `/etc/marketing-agent/doppler.env` |

開発用チェックアウトにはメインPCのGit履歴と未コミットの実装をコピーした。GitHubへのpushは行っていない。サービス用コードは別配置で、開発フォルダーで編集しても自動反映されない。参照用文書は2026年10月7日時点のコピーであり、正本はLogEatsの`docs/marketing`である。

LogEats本体にはメインPCで未コミットの変更が複数あるため、この作業では本体の開発先を切り替えない。将来同じSSH接続を使用できるが、切り替え時に履歴・未コミット変更・依存・認証を別途移す。

## アプリへのプロジェクト追加

設定の接続一覧で、`katsu-server1-wsl`行のフォルダー追加ボタンから、上記の開発用Gitチェックアウトを選ぶ。既存のメインPCプロジェクトと混同しないように、サブPC用であることが分かる表示名にする。

## 開発用の確認

```sh
cd /home/logeats-runner-win/projects/marketing-agent
.venv-mina/bin/python -m unittest discover -s tests/mina -q
.venv-mina/bin/python -m src.mina doctor
```

開発用の診断は開発環境の設定と台帳を参照する。サービス設定・台帳は専用ユーザーとrootの権限で保護されるため、開発ユーザーへ秘密ファイルの閲覧権限を追加しない。サービス側の診断は管理経路で設定を読み込み、`mina-agent`ユーザーとして実行する。診断結果には設定値、投稿本文、操作nonceを出さない。

`doctor`は設定の不足名、翌日の完成枠数、台帳の状態別件数、heartbeat、外部状態の照合が必要かを返す。DBがない場合も新規作成せず、診断だけでは書き込みや公開を行わない。設定が揃っても、候補生成・独立レビュー・HTTPS承認経路の実動確認が必要である。

## 継続時の条件

- ドキュメントとコード内コメントは日本語で記載する。
- 毎日20:00 JSTに翌日4枠の承認依頼を届ける。公開はDiscord承認後にX APIで行う。
- 既存GitHub Actionsランナー2件の稼働を維持する。
- 本番設定値や台帳内の承認トークンをチャット・Git・ログへ出さない。
- 献立・画像・固定本文の実際の確認結果がない状態で、品質確認済みのフラグを作らない。
- 本番の書き込み・承認受付・X公開は独立した設定で管理する。現在はすべて無効。

DiscordのGateway承認サービスは認証済み接続を確認済みだが、承認受付は停止中。アプリ画面でBotがオンラインでも、候補の自動生成・通知・承認・公開の全体が完成したことを意味しない。
