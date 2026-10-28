# 通常QA回収修正・独立コードレビュー

- 判定：コード修正はGo。公式の型検査・必須QA・専用Preview E2Eの合格と、統合担当による最終差分確認は別途必要。
- 基点：f4aee73ecc9ad2c784c6196fb4d04009812037d6。
- 担当：実装に参加していない別Codex。統合担当が記録したagyの結果空に伴う代行。AGENTS.mdと承認済み最小計画・独立計画レビューを先に読んだ。
- 実施範囲：ローカルソースと試験コードの読取のみ。試験実行、外部・本番接続、実資源回収、コード編集、Git変更は行っていない。本記録だけを作成した。

## 確認と指摘対応

| 条件・指摘 | 読取結果 |
| --- | --- |
| 対応表を外部処理途中で失わない | 通常・main不在を同じ最終TXへ統合。外部処理が失敗すれば対応表のDELETEへ到達しない |
| 同一性と対象件数 | 対応表の各値とxmin、main/LINE/deletion利用者の所有を再確認。対応表と利用者のDELETE件数を検証する |
| 対応表が既に失われた残存利用者 | 推測復元や外部DELETEへ進まず停止。旧残存資源の回収は別承認のまま |
| COMMIT応答不明 | ROLLBACKで未保存へ落とさず接続を破棄。親はcleanupを再送せず同namespaceのSELECT照会を一回だけ行い、残件0でも元の失敗を保持する |
| ROLLBACK自体の失敗 | 確認中に統合担当が追加対応。失敗接続を破棄し、局所試験にも境界を追加した最終差分を再読取済み |
| 診断の到達性と非露出 | 固定stage/reason/action/statusを既存IPCで親へ送り、親で再検証・上限32件・重複計数後に公式runtime成果物へ保存。HTTP非200・非JSON・不完全・timeout・読取失敗を本文なしで分類 |
| 互換性・隔離 | 認証とnamespace/provider境界、M1専用所有フェンスを保持。既存cleanupの404本文を維持。本番Mina経路・Schema・policy・DDL・鍵・権限の差分なし |
| 試験範囲 | 実関数を合成境界で実行する局所試験と、親HTTP/IPC診断・非再送試験を確認。packageの必須QA経路へ登録済み |
| 既存VM試験のimport追従 | 新しい診断module用のmockを2試験へ追加した差分を確認。呼出し時は例外にするため、想定外のcleanup実行を許す変更ではない |

重大な未対応指摘はない。永続unknown状態のDDLは今回含まれないため、同namespaceの人手再cleanupも禁止する運用上の制約が残る。これを自動復旧済みとは扱わない。専用E2Eの実資源回収成功は本読取レビューでは未検証。

## 読取時のファイルSHA-256

| ファイル | SHA-256 |
| --- | --- |
| src/app/api/qa/fixture/route.ts | bc4d48f74e1c94f7bb724ac13eb8de2af06f839df8cd84c6011aea3d4a44158a |
| scripts/qa/run-local-db-qa.mjs | 37821f05ea14f3ea68518ddda2ea66d348361372c735c4384f681d8fa69f1a6d |
| scripts/qa/fixture-cleanup-diagnostic.mjs | 484a388df5501ccb13e6781d04de64c3669598365e0095411191f7ac0d739909 |
| scripts/test-qa-legacy-cleanup.ts | 7c272d7f5606dd6423bec954b5b6a1ff3144038b6731a05f96ed68a8e940d2b2 |
| scripts/test-fixture-cleanup-diagnostic.mjs | f2d14cdd980f2c86848aceecede24623ae76572fb9a5401c5a3b33b2a1eda3e1 |
| package.json | 9f90357d63b7e8f824a2f4f32cd9aadf8b0a72d2a9ec3f6334aee09d24e952d0 |
| scripts/test-mina-m1-qa-fixture-commit-order.ts | 72a238a066979478441df9f8d1c1bed6316e9f1e88aa9ddc25e4613111d23d2a |
| scripts/test-mina-m1-qa-fixture-commit-order-isolated-db.ts | f8b6f76da0074f04ca1d9b15408be5e814a2b70ea0216949100cb1de83a315cb |

このGoはQA限定実装のコードレビューであり、本番反映、再ON、新時間窓、残存QA資源の書込回収、Agent配置、食事操作、投稿を承認するものではない。
