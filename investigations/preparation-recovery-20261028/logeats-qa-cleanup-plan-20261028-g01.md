# ONリリース停止とQA回収の最小対応案（実装未承認）

## 確認済み

ONrelease40626952820 attempt1：70成功/2失敗。client CFworker_search HTTP500で通常C前refresh停止・C POSTなし、v2P404/要求行0とserverhost500が整合（個別request相関なし）。cleanup_verified=true。health40631151538は2試行verified、独立Go後に正式--failed1回だけ。

attempt2：72成功/0失敗/0flaky、v3全4成功、replacement診断0。ただし親のfixture_cleanupでlocal_db_fixture_http_rejectedとなりcleanup_verified=false。HTTP status/body/cleanupかinspectかの識別は現行記録にない。二つの失敗を同一要因や本番API不具合とは断定しない。Production/postdeployは両attemptともskip。

QA専用NeonへのSELECTのみで、固定root namespaceのmain/line/deletion所有者条件に一致するusersが2、qa_fixture_runsが0、attempt2の通常2/救済12 runがclosed_retained、open run0を確認。単純なnamespace全文ユーザーcountには保持対象Minaも混在するため、上記2はrouteの固定main/line/deletion条件で読み直した値。旧unknown/closed資料は削除しない。

ソースcleanupFixtureはnamespace/保存先を検証後、deletionClerk/image/user/job処理→qa_fixture_runs行削除（autocommit）→line画像のDELETE/GET確認→main OGP DELETE→BEGINで残DB行削除/COMMITの順。root2ユーザーが残りmapping0という読取結果は途中停止と整合するが、実際の失敗外部処理・statusは記録不足で特定不能。GET404を未保存/外部削除完了証明へ読み替えない。

## 停止状態

3回目の自動再実行はしない。Doppler v3flagを明示0へ復帰し再読取、同じintegrationのVercel Production sensitive metadata更新を確認、旧flagなし。Production alias Deploymentは旧OFFと一致。grant/有料点検/本番PDC/食事/投稿0。case/P/本人絶対15時は保持し延長しない。

## 最小の修正計画（未実装）

A. 現在の未完了QA回収はまず既存QA権限だけのREAD ONLYでDB/R2/Clerk所有と現在有無を照合する。未確認/外部効果unknownを成功や再DELETE可へ丸めず原本保持。書込回収・別namespace再利用は実行条件と対象を固定し、必要な明示承認があるまで行わない。

B. QA限定2箇所で観測を補う。run-local-db-qa.mjsのfixture POST失敗にaction/HTTPstatus/固定reason分類を保存し、/api/qa/fixtureのlegacy cleanupで固定stageだけを記録。本文/URL/生ID/鍵/DB生例外は出さない。既存の親の最終stageだけで回収原因を決めない。

C. qa_fixture_runsのmappingを外部/DB回収成功まで保持し、main/lineの最終DB削除と同一TXの最後で削除する。main不在分岐も成功terminalまでmappingを維持。外部操作がunknownならmappingを残し、書込を自動再送しない。既存の隔離・namespace所有・外部確認・R2先行削除・現行rollback/unknown条件は維持する。新DDL/role/鍵/本番Mina経路/Schema/policyは変更しない。現在の失敗namespaceに失われたmappingを推測再構成して書き戻す処理は対象外。

D. 直接局所試験で外部各stage失敗時のmapping保持、正常時の最終同TX削除、main不在、COMMIT不明時の非再送、診断の秘密非露出を確認。型検査/必須QAを変更後1回、専用隔離E2Eと非実装者独立レビューを通してPRまで。成功済み無関係PG/広範QAの手動反復はしない。実装・PR・QA回収書込・リリース/再有効化は現在の承認範囲外で、別途本人承認が必要。

## 今日の15時枠

現在の正規gateは未合格なのでgrant発行不可。記録不足を推測で補いProductionへ進めない。新たな実装修正/隔離受入/PR/正規配備を15時前に完了できる根拠はなく、期限延長を前提にしない。既存本人承認の内側では停止と読取/保全/無課金の構造準備まで。Agentの再課金・新P・別caseへの作り直しは行わない。

## 独立レビューGoと採用事項

非実装Codexが実コードを読取確認し計画Go。これは実装/回収書込/新CIの承認ではない。以下を全採用する。

1. 診断はstderr止まりにせず、固定stage/action/HTTPstatus/allowlist理由を既存IPCで親の公式artifactへ到達させる。本文/ID/URL/鍵/DB生エラーは含めない。
2. mappingの最終削除は通常/main不在両分岐で成功TXの最後とし、読み取ったmappingの同一性・対象件数を検証する。
3. COMMIT ACK不明後にROLLBACK ACKがあっても成功や未保存確定にせず、元namespaceのSELECT照会限定とする。外部DELETEの再送をせず局所試験で境界を確認する。

現在の2ユーザー/失われたmappingについて推測で再構成・再DELETEする回収処理は本計画の実装対象外。既存QA権限の読取調査と対象原本固定から、必要な書込回復を別途具体化して本人承認を得る。
