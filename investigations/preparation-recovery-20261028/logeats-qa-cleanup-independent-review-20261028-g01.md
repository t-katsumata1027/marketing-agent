# QA回収の最小対応案の独立レビュー

- 判定：下記の具体化を受入条件へ含める前提で、修正計画はGo。実装・QA回収書込・追加CI・PR・本番反映の承認ではない。
- 別Codexが計画と現行のlegacy cleanupおよびrunner親POST処理を読取。外部/本番接続、書込回収、試験実行、実装変更なし。
- attempt2の72pass/0failと回収失敗、QA専用DB件数、OFF復帰は統合担当が確認した実行証拠を前提とする。独立担当は外部の再取得を行っていない。

## 根拠

現行cleanupはqa_fixture_runsをautocommitで削除した後にLINE画像・OGPの外部操作と最終DB transactionへ進む。途中失敗でも必要mappingが失われる構造はソースで確認でき、mappingを成功terminalの最終TXまで保持する案は妥当。一方、親POSTはcleanupとinspect-local-cleanup双方の非200を同じ固定理由へ丸めるため、現状の件数と例外だけでは失敗した外部操作やHTTP statusを特定できない。mapping0・root利用者2は途中停止との整合に留め、本番Mina API不具合やCloudflare原因と断定しない整理も妥当。

## 実装時に固定する指摘

| 指摘 | 必要な対応 | 対象・担当 |
| --- | --- | --- |
| 固定stageだけstderrへ出しても親の現行破棄経路では証拠にならない | 既存IPCを通じ親artifactへ到達させる。親ではaction・HTTP status・固定allowlist reason/失敗種別を区別し、非JSON/不完全body/timeoutも本文を露出せず分類する | QA2箇所・本体担当 |
| main不在の早期return経路もmappingを失わせ得る | 通常/main不在双方で全必要外部確認成功後の最終DB TX最後にmappingを削除する。取得したmappingの同一性・想定行数を確認し、途中例外時は保持する | legacy cleanup・本体担当 |
| COMMIT ACK不明を後続rollback ACKで未保存へ落としてはならない | COMMIT不明は元namespaceの読取照会限定。自動再cleanup/再DELETEを行わず、最終TX失敗とCOMMIT不明を局所試験で分ける | cleanup/親・本体担当 |

既存の所有・namespace方式・QA保存先照合、M1永久フェンス、外部先行確認、旧unknown/closed保持を維持する。現在失われたmappingの推測復元、新DDL/role/鍵、本番Mina経路・Schema/policy改変は不要で対象外とする。将来の回収安全性の修正を、現在の未完了QA資源の自動回収許可へ読み替えない。

## 承認境界

現段階は計画レビューのみ。本人の新規実装承認後に、必要局所試験・型検査・必須QA1回・独立レビュー・専用E2E・PRへ進む。QA資源への書込回収と本番リリース/再有効化は対象固定の別承認が必要。正規gate未合格のままgrant/PDCへ進めず、3回目retry・別case・本人15:00上限延長は行わない。
