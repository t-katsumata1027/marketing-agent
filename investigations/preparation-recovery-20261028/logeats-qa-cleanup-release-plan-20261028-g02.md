# QA限定修正後の最短承認案（未承認・未実行）

対象はDraft PR158、固定head 64590d84a982f9269a29266f576dc7179ddace1c。DDL、Schema、policy、鍵、権限、Agent依存変更はない。

1. 専用CI・回収・Preview合格、独立Go、Agent差分受入を照合する。
2. 本人が本番反映・救済ON設定・失敗時のOFF正規復旧を一括承認した場合だけ、既存Doppler連携のflagを変更し、固定headとtree一致のmainへ正規mergeする。同じ検証をOFF/ONで二度手動実行する段取りは増やさない。
3. mainの正規本番リリースが同commitの隔離QA・専用Preview・Production・postdeployを実施する。Deploymentとalias、必要な認証GETを照合する。未発行caseのまま維持し、サービスONを権限発行や食事操作の許可とは扱わない。
4. ON配備後の失敗はgrantを発行せず停止する。flag0だけでは現行Deploymentが即時OFFになったと断定しない。既存連携で0を確認し、正規OFFリリースとDeployment/alias/GETを確認する復旧範囲を事前承認へ含める。
5. 新しい実行窓は実検証完了後に本人へ具体的に提示する。旧15時原本を変更しない。未発行の同case/P、累積費用・試行回数、元holdを維持する。新承認原本を追加して既存契約が受理できることを確認し、不明なら発行しない。
6. case発行は新窓承認と最新の管理anchor・Agent bundle・外部予約一覧・期限/回数等の照合後だけ。同一transactionのfirst_activated_atから15分と本人絶対期限の早い方を適用する。仮の日時を設定しない。

旧QA残存資源の実回収・mapping推測復元・外部DELETE再送・Agent配置・食事操作・投稿はこの本番反映承認案に含めない。本人の従来の実行窓は失効したままとする。

## 独立レビューの追加条件

flag ON直前に、進行中・待機中の本番リリースと別Production buildがないことを確認する。main/head/treeと期待Deploymentの対応を固定し、競合・merge不能・queue不明ならONを保持せず0へ戻して停止する。別commitをONで配備しない。

旧15時原本、同case/Pの未発行、jobs/requests/calls/casesが全て0であることは、最終発行前に各担当の既存読取権限で再確認する。1件でも存在または不明なら初回条件へ読み替えない。

Agentの別名settings・追加承認原本・限定operatorの参照切替は、本体配備の承認だけでは実行できない。Agent担当が別の明示承認・局所検証・独立Goを得た範囲で行う。srcやサービスの再配置は不要という読取結果と、operator操作の許可を区別する。
