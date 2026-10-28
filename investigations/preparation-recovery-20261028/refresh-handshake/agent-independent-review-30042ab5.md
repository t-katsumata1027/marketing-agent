# 追加点検・管理受渡し Agent独立レビュー原本

判定：**Agent局所実装・隔離受入Go**。管理側CLI・固定FS proxyとの実接続、両側固定版での全体受入、本番配置・新実行窓・追加実点検・権限発行・食事操作・通知の実行Goは含まない。

- 対象head：`30042ab5e32c909f062d12c9033d9b3e8b5679e4`。
- 独立レビュー担当：実装に参加していないCodex。
- 記録時刻：`2026-10-28T11:45:18.475361+00:00`。
- 固定HEAD一致・作業ツリーclean・差分空白検査を確認。下記のコード/Schema/配布検査対象は固定Git実バイトと作業ファイルの改行正規化後完全一致を確認した。SHA表はGit blob実バイトのSHA-256。

## 確認内容と指摘の解消

旧4call・2receipt・費用を変更せず、同case・同予定Pの追加4点検一組を永続claimで追記する。親全履歴digest、承認原本SHA、固定source、policy、hold owner/epoch、両commit・Schemaへ束縛する。新旧混合、余分なkey、部分未完了、unknown、未照合費用は停止する。120秒を緩和せず、正式時刻は実完了時刻の最古を採用する。

予定Pはexecution role・settings・flow不変属性・refresh間で一致を必須化した。管理manifestの4helper SHAはportおよび本人承認原本で束縛した値と一致させる。管理出力のroot所有/親書込み権限・非symlink・実fd属性・実SHAと、厳密JSON/固定Schema/scopeを検査する。公開ファイルは原子的かつ上書き拒否で発行する。

再開分岐不足の指摘は解消した。追加一組のcompleteだけを実時刻120秒のまま再利用し、partialは再点検せず停止する。stage済みは機能OFF・期限後でも元caseのGETへ分岐する。CLIのlookupはAI・Driveなし、recovery構築で元case/UUIDの照会に限定する。

private contextの参照SHAをexecution roleへ束縛し、明示execute/lookup、private設定・作業先・flockを使用する。P・生成の既存記録があれば新しいP/課金を始めない。SNS予約・公開は永続prepare_only lockと既存fenceで拒否する。

通知の2指摘は解消した。入口checkpoint・カード準備を含めて外側tryの失効処理へ接続し、当日の未着手pendingだけを失効する。送信開始後unknown/sendingと他日は保持する。X/IGカードはpending INSERT前から「準備専用・公開停止中」「提案時刻」を固定し、daemon先行でも説明が欠落しない。

## 独立担当が実施した検証

1. 作業版の `test_scoped_review_refresh` と `test_scoped_coordination` を独立実行し、30件成功・59.297秒。これは最終固定前の実行であり、最終headの全面試験を再実行したという意味ではない。単一claim/旧費用保持/未知・旧新混合拒否/予定Pとhelper束縛/再起動/OFF期限後GET/JSONと不変ファイルを含む。
2. 最終固定headで `test_scoped_preparation` を独立実行し、4件成功・0.046秒。承認window、予定P・helper・contextと実execution原本の束縛を確認した。
3. 下流CLI、通知、rootファイル読取、固定Schema・配布時SHAの差分を読取レビューした。コード変更はしていない。
4. 正式管理helper `tmp/lunch-rescue-plan/read-native-anchors.ts` の実バイトSHA `d695c0e80b8f8adc387f189557ce3481180c3bdd813691d7c3d7bca969ae58ce` を照合してbuildArtifactを読取確認した。checked_atを含むtop-level、native各11field、calories、api_qualityの5要素、世代・rv・2画像SHA・解析digest・OGP24hexのAgent投影と一致する。helperは実行していない。管理checkoutの絶対パスは記録しない。

## 担当者の実行報告・受領証跡

実本体e0e0bb3のlocalhostハンドラーを用いた旧4+追加4→管理operator→P/C→X/IG各4候補・8画像→Discordモック送付、応答不明時の再送0、期限後通知の失効、実root/UID995の7境界成功は担当者の報告であり、独立担当自身の実行として数えない。

`investigations/preparation-recovery-20261028/refresh-handshake/SHA256.json` の全証跡を実バイト長・SHAで照合した。HTTP3証跡のprovider_commitは `e0e0bb391609d7483e6a74998376e9e051a144d3`。層別モック受入や実UID原本読取は、本番E2E、管理CLI/FS proxyの実接続、実課金測定の証明ではない。

## 残る停止条件・判定の範囲

追加の重大なAgent局所指摘はない。管理側の正式CLI・固定FS proxy・deep validator・helper依存閉包の固定と実接続、両側固定版の全体レビュー、最終CI、配布物実バイト確認は別の受入条件である。

旧19時枠は失効済み。配置・新しい同日絶対終了/提案時刻・追加実4点検・同case/同Pによる昼食救済は、具体的固定結果を提示して本人の別承認後に進める。旧原本/期限/回数/費用/unknownを保全し、case初回有効化+15分と新本人絶対終了の最短、1800〜2000kcal、120秒鮮度、全外部一覧、hold、実画像点検を維持する。朝夕再登録、SNS予約・公開、別caseや別Pへの迂回、日付跨ぎ、無承認再試行は対象外。

独立担当による本番接続・本番変更・外部書込み・実課金・食事操作は0。このMarkdown原本1件だけをローカル作成した。

## 固定ファイルSHA-256

| root-relativeファイル | 固定Git blob SHA-256 |
|---|---|
| `src/mina/scoped_review_history.py` | `8e2ffa6d4b980ec84feddfec3777f1e1fe74ab06909753afb821539d96fd2642` |
| `src/mina/scoped_review_refresh.py` | `b563f039382bcc8817a970e43c5d473908a79987d21c164b33760cf7806b4965` |
| `src/mina/scoped_management_proofs.py` | `d9a62c04a08528e2fdf461d19e9405b9116b13791e0b8892e0f0b435d22f802b` |
| `src/mina/scoped_coordination_json.py` | `e503a0177d135d653bb45aec04a57b15dc6eab341bd8673b3df90deb591c1681` |
| `src/mina/scoped_coordination.py` | `f283e51ddddc4519a45862b13dbf6b8f32e2aa5751d1ef11148cfe23d1ef3348` |
| `src/mina/scoped_pregrant_coordinator.py` | `0f3056a4d3088293de2f0417169b3b6f045a929c28c06b3636b11ea9898f3a45` |
| `src/mina/scoped_issue_builder.py` | `43ecdca61ed61778c310b0a315f69273cc00056c012b9b60c3241e1ff7e6cd3b` |
| `src/mina/scoped_preparation_operator.py` | `6c0eb4ab89e4c4684904c90d1b76ca3a13b2210b794b2c0de9444bac8a522039` |
| `src/mina/scoped_preparation_completion.py` | `2b6271bee5756939764555f727355a6844370599c0c677f2b2b7008831b21673` |
| `src/mina/scoped_rescue_runtime.py` | `71ce32a336e38d248522729cd0be2c63cee02102c9cbec61c5356114f30c358a` |
| `src/mina/scoped_rescue_origin.py` | `7217c4f04d92a25a84c49452dcd07f558e34e7059f78111b5be0ef4650cf2064` |
| `src/mina/scoped_rescue_reviews.py` | `140f30a493217391606a0f2b2ddfd19a7ea2a0c034d7954974e6938b1e3eac52` |
| `src/mina/scoped_rescue_flow.py` | `1fdf21fd170b0eee1ec7aa16239eb9d194ce23f38a3bc5bb18e3f5648b25a0e2` |
| `src/mina/scoped_preparation.py` | `12cd5e443dee3ec069015c3ed5f9db2402a995d3691e76dc5584d1abf3cda18e` |
| `src/mina/store.py` | `36bb0899fe2fd40c28cb9f9de051e6025d573d4c2534722bc7275fd78d3c41d7` |
| `src/mina/instagram_store.py` | `a101798fa279428adfd43badeeb1f5101c18c50897d543adfb1c30b06f907f22` |
| `src/mina/contracts/management_handshake_v1/envelope.schema.json` | `38b94c69bcd23f5384b15d2900ecc58fcca280b01286a50391d6cf87b189759a` |
| `scripts/build_mina_release.py` | `a0747b71686c3a87630086f4f044959258fb03647950e46c3709bf21d9aae869` |
| `scripts/mina_scoped_rescue_http.mts` | `e5fe956a8fe3577c760e24b86cc01ff9ebb58ba205b713400ba72cf4e01cd1c2` |
