# Log-Eats担当CodexへのQA回収修正依頼（承認待ち）

この文書は次段階の承認対象を具体化した依頼案であり、実装・回収書込み・本番反映・再ON・食事操作を許可しない。

## 問題と根拠

- 固定本体f4aee73、ON正規run40626952820。
- attempt1：provider管理GET500により70成功/2失敗、cleanup=true。既存GET-only診断40631151538の2試行が正常復帰を確認。
- 独立Go後の正式failed-job再実行はattempt2だけ。Core72成功/v3全4成功だが、親回収確認はlocal_db_fixture_http_rejected/cleanup=false。Production/postdeployはskip。
- 2回目はHTTP status/body/actionが残らず、正確な停止substageを断定できない。初回のCF500や本番APIの不具合と混同しない。
- legacy cleanupには、所有mappingであるqa_fixture_runsを外部画像/OGP処理・最終TXより先にautocommit削除し得る構造がある。QA SELECTでclosed_retained/open0でも回収成功とは扱わない。

## 担当が独立計画Goを得た最小案

1. QA限定でaction、HTTP status、固定stage、allowlist理由を既存IPCから公式artifactまで保存する。生本文、食品原文、プロンプト、秘密・接続文字列を記録しない。
2. mappingは通常/main不在の双方で成功TXの最後まで保持し、同一性と件数を確認する。外部処理が失敗・不明なら所有根拠を消さない。既存の保護・namespace/attempt・所有証明を維持する。
3. COMMIT ACK不明時は書込再送しない。元namespaceのSELECT照会だけで復旧し、成功/失敗不明を勝手に確定しない。

## 承認後の実装・検証範囲

QA限定の必要最小実装、局所境界試験、型検査・必須QA1回、非実装者の独立レビュー、専用E2E、PR作成まで。
HTTP拒否、外部処理失敗、main/通常不在、mapping保持、件数/所有変化、COMMIT応答喪失、旧unknown/closed保持を検証する。成功済みの無関係な全面QAを追加しない。

新DDL・role・鍵・本番Mina契約・Schema・policyは変更しない。既存の失われたmappingを推測で書き戻さず、外部再DELETE、namespace再利用、期限/証明の更新だけによる迂回を行わない。
QA回収の実書込み、本番反映、ON設定、権限発行、新たな実行窓はこの実装承認と分離する。固定commit/PR/CI/独立判定/残事項を返し、具体的な次の承認を求める。

## Agent側の状態

Agent1c5224b6は新旧救済OFF、実行設定未接続。10/28の同case b414b8f2-368c-4ad9-8beb-9ae867c5e36eと予定P7251f4e7-597c-4320-a224-1d0a1089e25cは未発行/未送信のまま。本人15時は延長しない。費用・回数・旧期限・元hold・未実行executorを保全している。
この依頼はAgent担当がLog-Eats本体を変更する権限を得るものではない。実装は本人承認後にLog-Eats担当が行う。


## 提供側の実在原本

提供側固定基点はf4aee73。計画/レビューは担当の未コミット原本で、実装commitや実行承認とは扱わない。既存保護領域から実バイトを受領し、SHAを検証した。
- investigations/preparation-recovery-20261028/logeats-qa-cleanup-plan-20261028-g01.md：b0c99d395c408c5851e27712899fcf007b98868883d3eab16950b7b8294fc67a。
- 同logeats-qa-cleanup-independent-review-20261028-g01.md：8d8e0cdf76cc839edc468345faf10001f3125c3610883cb1556577950e319826。
- レビュー識別：INDEPENDENT_QA_CLEANUP_MINIMAL_PLAN_20261028、非実装Codex v3_delta_independent。指摘3点は計画に全採用済み。
担当の14:02 JSTの用途別SELECTでも、v3 catalog/実効ACL一致・rescue_enabled=false・未完了0・cases0を再確認したとの報告を受領した。
