# 保存済み一食置換：Agent本番配置・有効化

## 結果

本人の「Agent修正版の本番配置・有効化を進める。過去日の救済・期限延長は行わない」という明示承認に基づき、2026年10月24日08:38 JSTに配置・有効化を完了した。08:39 JSTの事後検査で実プロセスへの反映、3サービス・3タイマーactive、巡回失敗0、既存業務行の保持を確認した。

今回の作業で実食事置換・投稿・候補生成・強制的な準備再開は実施していない。通常の将来日準備で品質保留が発生しても、日別の完全3食、公開排他、実原本、回数、期限、点検、新版承認の各条件を満たす必要がある。

| 項目 | 固定値 |
|---|---|
| 配置前 | `8ac2fdc118fcfebde07a23053364de884aeb7254` |
| 配置commit | `f2119d48f2f9c5a7a5c4f979cdc85a9248b81f09` |
| 受入済みAgent実装 | `6c43282f28c5cd2cd68b302833da82f954e20534` |
| 本体 | `761a55f606fbe87b42cf50e4321815fb7ffeb916` |
| 配布bundle SHA-256 | `2ff82187d95a454f80999142df120d0438f70ba54c23d4f3ec0897ef6bdc84dd` |
| Schema SHA-256 | `4f8d2be8f47a741ff8578a25c29bd6422cf88ebd817f8450339eaee76216f6e2` |
| policy_version | `mina_gemini_waived_20261023_v1` |
| budget_policy_digest | `244918faac42ea4b91b96116c75b251c9361b9a1389a88503b7ba6aedb07e79b` |
| approval_reference_digest | `8747bbcffcedfe18baa09e59ea0d25985d3c8472c47bad31e93554cb305d6083` |
| 有効化設定の実バイトSHA-256 | `2c65ea2979f7b5c8d623a71eda9f71cde4933d553a6ed051aea3c681e744d3a1` |

配置commitの実行コード・依存・試験は受入済み実装と同一であり、その後は受入・本体配備記録の文書差分だけである。UTM・手動投稿履歴・Instagramの実装を保持した。

## 検証と実際の設定

- 固定Git bundleを別配置し、SHA・commit・配布実コードの一致を確認。依存ファイルが稼働版と同一のため、既存の仮想環境をコピーし、追加インストールは行っていない。
- 別配置のLinuxで、置換・公開hold・旧M1・巡回・保存後点検の対象回帰80件成功。既存の固定本体実HTTP41件・対象回帰70件・独立レビューGo・CI両job成功の証跡も保持。変更のない全面QAや実モデル呼出は反復していない。
- 本番資格で確定policyをGETし、版・両digest・明示Gemini・waivedを照合。
- X/Instagramの固定アカウントを確認し、両方とも全2ページを取得。statusや日付のフィルターでdraftを取り落としていない。Buffer読取14回を既存の共通API枠へ計数した。
- 有効化設定を使って本番DBの私用コピー上で実Coordinatorを構築。既存27テーブルの全行を保持し、試行0・ネットワーク呼出0。本番DBに合成contextを作っていない。
- `MINA_MEAL_REPLACEMENT_ENABLED=true`、`MINA_SOURCE_COMMIT=f2119d48...`、`MINA_REPLACEMENT_AGENT_CONFIG=/etc/marketing-agent-replacement/agent.json`を設定。
- UTMは既存のtrueを保持。他の準備・公開・承認設定は変更していない。
- サービスのDoppler親プロセスとPython実行子への設定継承を確認。3サービス・3タイマーactive、巡回consecutive_failures 0。
- 配置後の認証付きGETでも確定policy一致、circuit closed/recovery_required false。全枠の期限集約false/nullと`deadline_unverified`を補完していない。

設定ファイルはroot所有・mina-agentグループ読取の0640、専用ディレクトリは0750とした。既存の`/etc/marketing-agent`の保護権限は変更していない。

readinessの旧名称`budget_allocation_verified=true`は、明示waivedの本人承認と本体policy版・digest・承認参照の一致を検証した意味として、設定内に根拠を記載した。policyの金額・通貨・呼出上限額はnull、`cost_bounds_verified=false`、費用既消費/割当の金額検証フラグはfalseのまま。0円・無料・費用実測済みとは扱っていない。

配布builderと過去のpolicy提案ファイルの既定falseを機械的に書き換えず、本人の今回の明示承認と上記実受入に基づく別の`activation-decision.json`へ`activation_allowed=true`を記録した。日別適格性や公開許可をtrueにした意味ではない。

## 退避・切替・事後検証

定期処理が実行中でないことと公開・通知の送信中/unknownがないことを確認し、タイマーと3サービスを停止してから退避した。

初回の切替は、設定読取の検査でサービスユーザーが既存の保護ディレクトリ下のファイルを読めず停止した。コードの入替前であり、旧版サービスとタイマーを再開した。その記録と退避DBを保持し、失敗を成功へ変更していない。専用設定ディレクトリで読取を確認し、別の退避先を使って再度切り替えた。

| 退避物 | 場所 |
|---|---|
| 初回停止時の退避 | `/var/lib/mina-agent/deployment-initial-f2119d4` |
| 成功した切替のDB・設定・事前hash・有効化判断・事後検査 | `/var/lib/mina-agent/deployment-initial-f2119d4-r2` |
| 旧コード | `/opt/marketing-agent-rollback-initial-f2119d4` |
| 元の未適用設定と外部一覧証跡 | `/var/lib/mina-agent/activation-initial-f2119d4` |

DBバックアップのquick_check成功。切替中の既存行hashは一致。起動後も業務25テーブルの全行が不変、既存監査行のprefix hashが一致し、監査追記は起動2件だけだった。残るcycle_healthは正常巡回により更新されている。コード・設定の失敗時復帰経路は用意したが、DBを過去snapshotへ戻す処理は使用していない。

## 維持した停止条件と残る日別制約

事前の翌25日向け外部一覧では、Xの対象予約0、Instagramにdraft4件が存在した。全4件を停止対象のまま保持し、削除・除外・未送信扱いをしていない。機能がONでも、同じ外部状態なら置換開始時の実一覧照合で停止する。この記録を日別の許可証跡として再利用せず、実行時に毎回新鮮な全状態/全ページを照合する。

以下は変更していない。

- 対象日前日20:00 JST・既存準備期限・真の初回再計画＋15分の最短期限、P直前の残60秒以上。
- 登録前からの累積最大2回。欠損・started・unknownは初期化しない。
- 部分日の422、正本版/世代/画像SHA、原本・receipt所有情報の不一致で停止。
- 日単位の公開排他、候補実画像と食品解析の独立点検、C前後の1,800〜2,000kcalと三者一致。
- cooldown・単一probe・HALF_OPEN・明示resume・unknown時の元UUID照会限定。停止中の期限延長なし。
- saved保持、旧承認失効、新正本の再点検・再承認まで公開holdを保持。

過去18日分や期限経過した24日分を救済せず、準備再開・食事置換・投稿の強制実行はしていない。今回のLog-Eatsアクセスは設定確認のGET2回だけで、本番P/D/C/resume POST、実Gemini、課金呼出は0である。

事後証跡は`investigations/replacement-agent-activation-20261024`に保存した。停止が必要な場合も、既存hold・要求・unknown・保存結果を保持し、DBの巻き戻しや新UUIDでの再実行は行わない。
