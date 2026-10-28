# v3固定契約の受入準備：独立レビュー記録

## 判定と範囲

**準備資料のGo**。今回確認した資料・構造fixture・承認票に、未対応の重大指摘はない。
Agent実装、実HTTP受入、本番配置、有効化、権限発行、食事置換や公開のGoではない。

- レビュー担当：独立Codex `/root/review_x_comment_fix`。準備資料・実装の作成者とは別担当。
- 対象文書：`MINA_RESCUE_V3_ACCEPTANCE_PREPARATION_20261028.md`。
- 対象文書の実バイトSHA-256：`b0c11809e4acf7965d915a23d52499e919de15a16effcfeeb1b0264da7da2d29`。
- 提供側固定commit：`06f43311a7fc8efa0c15cd29960cd3944ecca05c`。
- Schema実バイトSHA-256：`baf76695c55298cd97fe8b24fc488c3c164585c11ae735986da380c4e52397ab`。

## 独立確認

保存済みSchemaとSHAファイルを照合し、Draft 2020-12 Schema検査と保存済み20fixtureの再検証を読取りだけで実施した。20件成功、ファイル書込み0、外部呼出0。`STRUCTURAL_RESULT.json` の20成功と一致した。
fixture作成スクリプトは入力の固定Schema SHAを検査し、旧fixtureをdeep copyした合成入力へ変更する。実承認artifactや実grantを生成するものではない。
朝食・夕食の構造正例には他食との意味上の重複が残るが、資料はこれを構造検査だけと明記し、相互整合を実受入項目へ分けている。これらを有効な発行要求として流用しないことを確認した。

固定commitの提供側 `scoped.ts`、`operator.ts`、`handlers.ts` 等も読取り、以下を確認した。実ハンドラーの起動・管理CLI・DB・外部接続は行っていない。

- 日付と朝昼夕はSchemaで指定できる。管理側はDB時刻のJST当日・翌日だけを許可し、対象日終了以降のnot_afterを拒否する。
- `OtherMealReview` は余剰キー拒否、OGP入力digestは24hex。自己の `evidence_digest` だけを除外したcanonical digestを使い、指定されたdigestが異なる場合は拒否する。
- 他2食の枠・rv・原本/保存画像SHA・ad1・OGP・policyを束縛する。権限発行時は現在時刻との差120秒以内、P/Cではcaseの初回有効化時刻を境界として同じ凍結レビューを照合する。再GETだけで独立点検日時を新しくする設計ではない。
- context有無と元first/hardを区別する。Schemaでnullが通ることは実context不在の証明ではなく、資料はAgent/本体両台帳での照合を必要条件としている。
- 本人承認artifactはreview policy・Agent commit・実配備Body commit・Schema実SHAへ束縛される。Draft commitを実配備済みとみなさない。

## 旧計画との整合

旧計画のD後次候補はv3に適用しない。単一case・単一planned Pで、不採用・不受理・失効後は停止する。全履歴最大2回を追加Pや再caseの許可へ読み替えない。
旧v1/v2の定数・route置換や全件横断検索を使わず、新しい契約namespace/日/枠/caseで所有を分離する。
元画像・六M1要求・native昼保留・旧20時期限・通常episode・日hold・真の初回時刻・累積回数・旧費用を維持する。Buffer削除unknownを成功へ丸めない。

Driveの専用保存名と再取得SHA、日次本文の新実値再構成、120字/コメント重み110/最終重み280、日次UUID/result/rvの出自、公開前source readerの新契約対応は実装対象として継承されている。
朝455・夕716を維持する場合、新昼の実解析629〜829kcalと日1800〜2000kcalをC前・本体transaction・C後に確認する。現1799.4kcalの丸めや品質ゲート緩和は認めていない。

## 承認と未受入

**A** は新adapter・台帳/SQLite移行設計・必要な既存接続補修・隔離受入・独立レビュー・PR更新だけ。追加の明示承認前にruntime実装を始めない。
**B** は双方の固定版・CI・専用Preview・独立受入後の救済OFF配置として別提示する。移行表/行、原本保持、バックアップ、DB巻戻しを伴わない切戻しを具体化する。
**C** は実配備版、最新旧rv/2SHA、単一case/planned P、同hold、既存waivedの操作範囲、本人artifact、取消/絶対終了を揃えた限定実行として別提示する。09:00は未承認候補で、実行期限は承認not_afterと初回発行+15分の早い方とする。
提供側のDDL・正規配備・設定・grant発行の承認をAgent文書で代理しない。新X/Instagram候補とDiscord通知は実公開の承認ではない。

20構造fixture成功は、freshness/digest相互一致・他食重複・管理権限・実HTTP/DB・排他・実画像の受入を証明しない。資料はこれらを未受入と明記している。
実native保留から新case、実adapter/本体HTTP、P、認証画像、C、saved照会、日次/OGP、最終source verifierの通し受入、およびunknown・期限・競合・再起動・累積回数/費用の拒否試験が残る。
提供側CI・専用Preview・main/Production配備状況は今回再取得していない。担当報告を超えて完了扱いしない。

## 再検証対象の実バイト

- `prepare_fixtures.py`：`6bb565e964beb3c34d7bafad65874a65cb88ee5538cb9a83dc74dfbd92409eaa`
- `structural-fixtures.json`：`5f8fec7f6831b75829b0a2cbddd6217644f1d953ed257748d0447e94440dc7bf`
- `STRUCTURAL_RESULT.json`：`3413881dbd5f2278de819dd881b4584e430a16a09e6de269ef827b272ac0e2ee`

本記録だけを追加した。Agent実行コード・提供側本体・本番・承認状態は変更していない。
