# 保存済み一食置換：Agent初回Pの隔離受入

## 2026年10月24日の更新

本体PR149は761a55f6で本番配備・ON済みとの報告を受領し、Agent担当も本番GETで固定Schema・確定policyを照合した。提供側の最新専用Previewは成功しており、以下の「Draft/本番未反映」「現行Preview未合格」は10月23日時点の記録として保持する。Agent側は未配置/OFFを継続し、配置・有効化の承認へ自動転記しない。最新状態と直接確認/提供側報告の区別はMINA_PROVIDER_RELEASE_ACCEPTANCE_20261024.mdを参照。

## 結論と固定版

Agent実装と隔離受入は完了。独立レビューは今回の隔離受入範囲でGo。本番配置・有効化・既存食事置換・投稿は実施していない。

| 項目 | 固定値 |
|---|---|
| Agent実装commit | `6c43282f28c5cd2cd68b302833da82f954e20534` |
| 着手時のAgent稼働コード | `8ac2fdc118fcfebde07a23053364de884aeb7254` |
| 作業開始HEAD | `a294dd2`（稼働記録の文書追記） |
| 本体 | LogEats PR #149、`0e80de53ef5dc48ddd997db9e88bb9750d753719`、Draft・本番未反映 |
| 契約 | `mina-replace-v1`、Schema変更なし |
| Schema SHA-256 | `4f8d2be8f47a741ff8578a25c29bd6422cf88ebd817f8450339eaee76216f6e2` |
| HTTP fixture版 | `mina-initial-prepare-http-v1` |
| 本体展開物の全ファイルSHA集約 | `e823f4a98a7771893b1a1d18806b565f93faf5762ec693a09d807114b2a5adf1`（1,376ファイル） |

稼働コードに統合済みのUTM・手動投稿履歴・Instagramを保持した。別作業の`e506c42`も確認し、手動履歴基点`be109b3`から後続のsrc/deploy/scripts差分がないこと、`be109b3`が本実装の祖先であることを確認した。本番との差分は置換の4モジュールと隔離受入用ファイル・文書だけである。

## 修正内容

- 全3枠の期限集約をtrueへ補完せず、保存済み3食と固定policyを確認した対象枠専用の初回P入口を設けた。`deadline_unverified`を含む応答そのものは変更しない。
- 公開holdは既存のSQLite送信claim排他を使用する。外部一覧の全状態・全ページ・鮮度検査を維持し、draftを削除・未送信とみなさない。
- 登録要求の原本バイト・入力digest・UUIDキー、登録工程の入力digest、保存結果版・共有URL・画像SHAを結び付ける。訂正結果も対応payloadと照合する。
- 旧再計画は完全な献立、生成round、対応するimage/key/register工程、実ファイルと要求画像の一致を確認する。UUIDと日付だけの旧原本は拒否する。対応原本が不足する既存台帳は`migration_unverified`で停止する。
- 試行・真の初回開始時刻・準備期限・日/枠/policy/hold所有者/epoch・操作予約・receipt原本参照を生成前に同一SQLite transactionで確保する。`next_candidate`での二重消費はない。
- 原本スナップショットをcanonical digestで固定し、費用予約digestから所有版と結び付ける。P直前にも原本の実在と一致を再読取する。receiptの形式適合だけでは通さない。
- 期限は既存の準備期限、真の初回開始＋15分、対象日前日20:00 JSTの最短。既存receiptの期限と時刻を継承し、欠損・未来時刻・started・unknown・非連続ordinalを初期化しない。P直前にも残60秒以上を検査する。
- PのUUID・送信実バイト・画像SHAを送信前に固定する。応答喪失・再起動後は元UUIDのGETだけを行う。対象候補のhard deadlineと予約原本を照合する。
- 生成前・P直前・C前・明示resume前に、新鮮な正本版とpolicyを再確認する。機能・予算方式・provider・guard・recoveryの拒否は維持する。
- OPENの実GETで得た固定retry_atとgenerationを保存する。時間経過後も新鮮なHALF_OPEN・同generation・recoveryなし・未終端台帳なしを確認した専用経路だけで、単一probeをSQLiteへ予約する。未観測HALF_OPENやprobe不明を時間だけで解放しない。第二候補は現在のcircuitをGETし直す。
- saved後のOGP停止はsavedを保持する。明示resumeには現在正本版の一致と15分以内の保存期限も必要。独立点検、C前後1,800〜2,000kcal、三者一致、旧承認失効と新版再承認の仕組みを保持する。

## 実施した試験

### 固定本体の実ハンドラーによるHTTP：41成功、失敗0、skip0

`git archive`で固定本体を専用一時フォルダーへ展開し、起動前に全1,376ファイルのSHA集約を照合した。Agentの実クライアントをlocalhostへ接続し、本体の実`handlers.ts`・runtime・guardを実行した。正本storeと外部I/Oのみ提供側の隔離store・合成callbackに置き換えている。未設定値や期限確認trueをHTTP fixtureへ補完していない。

隔離環境だけで合成の有効policyを注入した。本番policy案の`agent_integration_verified=false`、`activation_allowed=false`は変更していない。

| 必須経路 | 結果 |
|---|---|
| contextなしGET→初回P→対象枠contextだけ→独立点検→C | 成功。試行1回、履歴1件、他枠の集約falseを維持 |
| 同UUIDの実HTTP再送 | 409、解析・画像保存・OGPの増分0 |
| D applied→第二候補 | 累積ordinal 2へ一度だけ進み、再点検後C saved |
| 登録前の再計画ordinal 1 | 初回置換をordinal 2/prior `[1]`として継承 |
| 原本欠損・形式だけの旧原本・digest不一致・生成中の原本削除 | P前に拒否、提供側解析・画像保存0 |
| started/unknown・ordinal欠落・最大2回・未来/古い初回時刻 | 拒否。0回/現在時刻への復元なし |
| 残59秒/残60秒 | 59秒はPを送信せず、60秒ちょうどは受入成功 |
| 本体実ハンドラーの未来/古い初回・ordinal欠落・残59秒 | 実HTTPで503、指定reason、context・解析・画像保存0 |
| 部分日・正本版変更・生成中policy停止 | 拒否。部分日422を迂回しない |
| 公開送信claim | 同SQLiteの排他で開始拒否 |
| P/C応答喪失と再起動 | 元UUID照会のみ。P/CのPOST各1回、保存履歴の増分なし |
| OPEN・未観測HALF_OPEN・unknown | 新規生成/再POSTを拒否 |
| 期限経過OPENの単一probe→D→第二P | 成功。既知OPENの固定証跡を保持し、closed回復後の第二候補も受入 |
| 解析完了後pause→明示resume | 解析1回、画像保存1回。GETでは再開せず、再解析なし |
| C saved/OGP pause→明示resume | savedと履歴1件を保持 |
| resume前の機能OFF/provider不可/予算未設定 | resume POST増分0 |

### 要求・外部処理数

41ケース合計の業務API実HTTPはGET 203、POST 33、計236件。fixture制御用のlocalhost要求はこの集計から除いている。各ケースの内訳は`investigations/replacement-initial-prepare-20261023/http-acceptance.json`に保存した。

提供側へ注入した模擬callbackは解析20、画像保存16、OGP描画4、OGP PUT 4。実Gemini・OpenAI・Drive・Buffer・Gateway・本番APIの外部呼出は0、課金呼出0である。実モデル品質・実課金額を測定した証拠ではない。

### 対象回帰と型検査

- 置換制御・既存P/D/C・要求台帳・外部一覧・正式Schema・公開hold・旧M1契約の7モジュール：70成功、失敗0。
- 隔離HTTP adapterのTypeScript型検査：成功。
- Agent実装6ファイルの実バイトSHAと固定Agent commitのGit blob SHAを照合：全一致。
- 独立レビュー：実装不参加のCodexが最終`6c43282`を読取確認しGo。途中の原本結合、resume停止条件、第二試行、fixture固定版の指摘を修正済み。
- 全面QAはローカルで反復していない。専用HTTP試験は通常CIの本体未指定時には明示skipとなるため、通常CIの成功を41ケース受入の代用にしない。

## 再現手順

Agentの依存と、固定本体commitを保持するGitリポジトリのNode依存が必要。以下は固定本体を一時展開し、検査後に隔離HTTPを終了する。既存の出力証跡は上書きしない。

```powershell
python scripts/run_initial_prepare_acceptance.py `
  --provider-repo C:/project/LogEats `
  --output investigations/replacement-initial-prepare-20261023/http-rerun.json
```

## 残課題と有効化境界

1. 本体PR #149はDraft・本番未反映。本体側の専用Previewは新応答4ケースが成功している一方、後続Cの90秒タイムアウト1件で全体未合格のまま。このAgent受入で成功へ読み替えず、待受延長・再実行もしていない。
2. 本体補修の正規配備と双方の受入最終確認後、別段階でpolicy版・budget_policy_digest・承認参照digestを確定し、設定反映・有効化を判断する。
3. 金額上限なしwaivedを保持する。金額・通貨はnull、費用確認済みfalse。仮金額、0円、無料、実費確認済みの証拠は作っていない。
4. 対応原本が欠ける旧台帳は安全停止の対象。自動的な0回復元や過去日救済は行わない。既存の日・枠の期限は延長しない。
5. 本番Agent配置・機能ON・既存食事の置換・投稿・外部draft削除・本番DB/設定変更は今回の対象外で、実行していない。
