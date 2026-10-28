# 対象指定型救済v3：独立実装・隔離受入レビュー

## 判定と固定対象

**Agentコード・今回の隔離受入範囲Go**。確認した固定版に未対応の重大指摘はない。
本番配置・有効化・権限発行・食事置換・実課金・Discord送信・SNS公開のGoではない。

- レビュー日：2026-10-28 JST。
- 独立担当：Codex `/root/review_x_comment_fix`。実装作成者とは別担当。
- Agent固定head：`8a160e2952edc42cc06a432489db1bdc5a78ad86`。
- 固定本体HTTP参照：`858e2bbb4f62a26ec8dc6e0d5310944541d0ec27`。
- 正式v3 Schema実SHA：`baf76695c55298cd97fe8b24fc488c3c164585c11ae735986da380c4e52397ab`。
- 独立試験開始前・終了後に固定headと作業ツリーの不変を確認した。レビュー記録以外のコード・本体・本番は変更していない。

## 独立実行した検証

固定版で次の4モジュールを独立実行し、**43件成功、失敗0、86.537秒**。

- `tests.mina.test_scoped_rescue`
- `tests.mina.test_scoped_rescue_http`
- `tests.mina.test_scoped_rebuild`
- `tests.mina.test_replacement_runtime_schema`

実行は専用Python環境と一時SQLite・固定Git archiveから展開した本体ソースを使用した。
HTTPはlocalhostの本体実ハンドラー、DB・AI・Drive・公開素材transportは合成依存であり、実資格・外部書込み・実課金はない。
Nodeの外部fetch/socketを禁止し、各HTTP試験の終了時に外部接続試行0を検査している。
全体CI・提供側専用Previewの成功は、この独立実行の結果に含めない。

通し試験では、実M1登録handler由来の3原本からnative昼保留、実点検呼出しreceipt、Agentのstage_issue/operator_bundle、本体実operator、P、認証画像2種、実PreparationPorts/DriveClient、C、saved照会、Flow.finish、実M1日次handler、新日次UUID、X4/Instagram4候補・実JPEG8画像、全4候補の公開前source verifierまで接続した。
その後、case期限経過・救済OFF・合成の全新版判断済み状態で、通常maintainから実verify_readonlyを行いhold解除へ進むこと、POST/AIの増分0、旧期限・UUID保持を確認した。
合成判断は承認後の状態遷移を検査するための入力であり、Discordへの実送信・実ユーザー認証・実承認・実SNS公開を検証したものではない。

## 指摘の反映と安全境界

- v3専用経路・Schema・candidate specを使い、未知版を通常置換やM1へ退避しない。case/日/枠を所有鍵へ固定し、単一planned P・単一候補・累積回数・UUID衝突拒否を維持する。
- 元期限は永続episode/receiptから取得し、欠損を20時や現在時刻へ補完しない。他枠のordinal unionは対象枠の試行へ混ぜない。
- 他2食は正式な4点検callの状態・入力digest・receipt集合と照合する。発行時120秒と、P/Cでのcase初回時刻に対する凍結照合を分離する。
- 旧業務行・画像・draft/drive、コード/Schemaと5役割の原本をcheckpointで再照合する。Pの残60秒を同TXと実送信直前に検査し、precondition応答のcase変更も拒否する。
- 各AI/Drive実RPCに前後検査と費用記録を付け、SDKの自動再送を抑止する。OAuth生応答はメモリだけへ返し、DBにはhash/長さ/状態だけを保存する。合成tokenがSQL・DB実バイトへ残らない試験を確認した。
- `processing/not_saved`を終端未保存と誤認しない。savedは後続不明で降格せず、真の終端矛盾は新版bundleの承認も停止する。
- Driveの専用名・新FileID・所有metadata・再取得SHA、日次本文の実値と対象日・120字/重み110/280、新M1日次UUID/result/rvの出自を検査する。
- 候補保存TXの前後で期限・hold・原本を検査し、末尾不成立時は全候補/bundle書込みを巻き戻す。既存bundleはpayload/owner/epoch/sourceを含む完全同一だけを冪等許可する。
- 全点検完了後だけ旧normal episodeのstageを承認待ちへ移し、旧UUID・期限・初回・消費は変更しない。初回sealとhandoffイベントを残す。通常巡回の接続先は読取承認照合であり、期限後の新規Pや生成ではない。
- 本人not_afterは保持し、Agent側の実効期限はcase期限と最初の公開予定の早い方とする。広い承認窓から予定後の投稿時刻を勝手に作らない。

## 本番へ進めない条件

1. 本体の最終専用Preview、正規配備commit・Schema・実API取得確認が未完なら進めない。この記録で提供側の配備を代理承認しない。
2. 他食OGP入力24hexの正規取得経路は担当回答・実照合待ち。隔離では固定本体関数から得ており、本番の取得手段を証明していない。自己計算・切詰め・仮値で補完しない。
3. 最終Agent CI、配布物検査、SQLiteの追加表/行・退避・移行整合性・切戻しとサービス確認を揃える。配置は別のB承認・operatorレビューが必要。
4. 有効化・権限発行・限定実行は別のC承認が必要。対象旧rv/2SHA/ad1、日hold所有者、両固定commit、Schema、単一case/planned P、5原本、既存waived費用、実not_after/実効期限を具体化する。
5. X/Instagram公開・予約は投稿案作成の承認へ含めない。旧承認失効と新版再承認、解除直前の読取照合を維持する。

本レビューは提供側の本番受入、実モデル品質評価、実DB/R2/Drive結合の代替ではない。
未確定項目を受入済みへ補完せず、次段階の停止条件として明示した上で、今回承認されたA範囲のコードと隔離受入をGoとする。

## レビュー対象の実バイトSHA-256

以下は固定headの変更ファイルを、今回のWindows作業ツリーから実バイトで算出した値。改行を正規化していない。

| ファイル | SHA-256 |
|---|---|
| `.gitattributes` | `fbf2fde6c942d4189d38d6da660583fee09c22862dad831e5715ace489a5e536` |
| `IMPLEMENTATION_STATUS.md` | `d740c1744c77a2ba9197785664c60d325313c6a42defea8d5f75d8831b127e8a` |
| `MINA_SCOPED_RESCUE_IMPLEMENTATION_20261028.md` | `ee16a8a1646e2d07e6165575a84224c502a639ecc1a52cbd54a6b3cfd0270b7b` |
| `deploy/mina.env.example` | `60dfa18ce137ebcccc63762a10e00b0326888ff9ae692ef9eabcf11050b46cda` |
| `scripts/build_mina_release.py` | `3b9370876f5e198634d45eee3d21108a3aa4d46626804129b6139b79ac92e0ea` |
| `scripts/mina_scoped_rescue_http.mts` | `4bff44052604a5a7342388cde5c585b9174bba4a93ab1c953e3898645d3b3f47` |
| `src/mina/contracts/rescue_v3/mina-rescue-v3.schema.json` | `baf76695c55298cd97fe8b24fc488c3c164585c11ae735986da380c4e52397ab` |
| `src/mina/contracts/rescue_v3/mina-rescue-v3.sha256` | `e90a900ee0da3c0e4f09ae6a60b309992bf8a2bda399a499b3ed430adb5108a8` |
| `src/mina/domain.py` | `dd01a68febd86967dfa8c179ff55df1e8a259ef26eb468f63d2f38b3fd9081b5` |
| `src/mina/drive.py` | `eb0c4360c2c386f883f7854e1ff5d4ee4558849b6cdd80954c3bcaf476b13450` |
| `src/mina/m1_contract.py` | `d6e29c0d423637ecc0aa9b98cce9f6369321db950d169b7e2478dd1c56daf9f9` |
| `src/mina/replacement_bundle.py` | `8441083d5fb53e454ffa5894de9b012d9f2c38440ff63e8846883e9b37803491` |
| `src/mina/replacement_daily.py` | `2b8ed43969afa1b5de03369e6b422da5639a375832f01c6313c6ced13b4891fc` |
| `src/mina/replacement_policy.py` | `b5e0b6eed19317ae3d0f93ae2dc358f3a6af8bfc7f4a4954b7ca6332cf2fdb0e` |
| `src/mina/replacement_ports.py` | `48ad91f114971eae99c8c71542698a59f359ee5cedbc90f569b51b0fcc49124b` |
| `src/mina/replacement_postreview.py` | `7d6b76f67ef79f0e0e8537fdfa36da3b047c99e0133506ae12c1be825bc5cf09` |
| `src/mina/replacement_rebuild.py` | `0a1c89ca985a5dd1fe5b980e41efdd01e7f4c10e3b3e2989bb746f4223667f3e` |
| `src/mina/replacement_source.py` | `7408e505effe674d98599821042a4b149f983e5fe8c7a77f800eebec01c0b78a` |
| `src/mina/scoped_rescue_contract.py` | `b872195cab951b98ce9c066f66ac0850a1b8aa0bcf8c60adc44db43dbb5e55e6` |
| `src/mina/scoped_rescue_flow.py` | `b2df377fe173777a44a4b27bcb9461f51da355ca46bcded5bca610203ce4583f` |
| `src/mina/scoped_rescue_origin.py` | `221b3c95efb73dff16e65d8702c01718d6fc39cf2f7412bfe4494916b79452b5` |
| `src/mina/scoped_rescue_reasons.py` | `8f95141aa232ce9e3bdcf2982db8589010f98171816ba05d557a6360fa5ca83c` |
| `src/mina/scoped_rescue_reviews.py` | `b65ee032292f95269c91004a9da22880dfb155762bd6d2ad65cbc975c74a5940` |
| `src/mina/scoped_rescue_rpc.py` | `541803f7febd0c2161a10c654876a16b802e5b68b353e87fef3174b0eab01551` |
| `src/mina/scoped_rescue_runtime.py` | `454c30c7e1ad73fe1fc9066835be4586ed64bca78e8be0c171312bce89b79ce9` |
| `src/mina/scoped_rescue_storage.py` | `31d2342371fb3b4b32ea7344e840d8f85b7cff1710e75340a4146873abf031fd` |
| `tests/mina/scoped_fakes.py` | `c5e20fd1401627b2d21aab5eec1e4383e7f7f2b84e36efbc72d17b1ecffd55cd` |
| `tests/mina/test_replacement_runtime_schema.py` | `72992f0637c62fa9ffe6e196e731b4028bbd3a86ee91468d00a1e0e34cc9259f` |
| `tests/mina/test_scoped_rebuild.py` | `5ae2bb9191496595a7d0ce2d30d587e1326e85c97107f2d80d894aaab3b49213` |
| `tests/mina/test_scoped_rescue.py` | `1df3a5c18b5b95288992d4296077785a90efd6a7fe9dfeb28b94dc3a84db485a` |
| `tests/mina/test_scoped_rescue_http.py` | `2e8755d07654d461ea7c69e0b22056f518bcc81927e47ef64261bde559311c7e` |
