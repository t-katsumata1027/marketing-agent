# QA修正後のAgent受入と、別承認事項の準備

作成：2026-10-28。これは実行許可ではない。Log-Eats担当のQA限定修正・検証・独立レビュー・PR作成が完了し、次の本人承認に必要な結果と範囲を整理した。Agent担当は本体を編集しない。

## 17時台の最終結果

QA限定修正・独立レビュー・Draft PR158作成・公式検証まで完了した。固定head64590d84a982f9269a29266f576dc7179ddace1c、tree e952c120fcdc76939d74984ce2855f2582428657。Product QA40648871128とSEO40648871130は成功。公式artifactをAgent側でも取得し、埋込report.jsonの72成功/失敗0/flaky0/予定skip38、local-runtimeのcleanup_verified=true、対象users0/fixture_runs0、診断空/dropped0を読取確認した。専用Previewは3 GET/書込0。localhost実行6689要求/215書込、remote heavy QA0。正規QA providerの実アクセス総数は別計測されておらず、Agent局所受入の外部0と混同しない。

公式取得ファイルSHA：local-runtime-measurement.json=a2c8685ce8a6f5662ded926e88d1f6c3bed0f5534e0a1cc7b6d553afed0d6563、platform-smoke-measurement.json=c4dd88179ab41f4598bc8cbd10f19d9a5727e56bc872491bce9f5052b9774459、report/index.html=6ec6fa5b90e6b485e2967e2cb3543436e314ffe949ef0fcb68a1215838bca73f。

担当のg02実装レビューGo・実行報告・本番反映案・独立計画Goの4原本は指定SHAと一致して受領した。旧QAのusers2/mapping0は担当のSELECTで確認され、今回回収せず保持。新namespaceの回収成功で旧資源を回収済みとしない。Agent側具体的承認案も実装不参加Codexが読取し計画Go。新原本/operator実物はまだ未作成・未検証で、承認後に独立Goが必要。

本番反映・再ON・新時間窓・権限発行・実課金・食事操作・Discord案送付・SNS操作は今回0。今日の3食投稿案復旧は未完了で、以下①②の明示承認待ち。本体編集とPR作成はLog-Eats担当が行い、Agent担当は実施していない。

## 16時台の確認結果

- 本人のQA限定実装・検証・独立レビュー・PR作成承認を担当チャットへ送信済み。Draft PR158の初回固定headは11c16c46c242aad579fc19de105d9ac17846b9c4。
- 担当の局所回収19件・診断・型検査が成功。必須QAは前半成功と、import mock不足を修正後の停止箇所以降の成功を合わせた結果であり、最初から中断なく全面成功したとは記載しない。実装不参加CodexのコードレビューGo原本をSHA照合して受領した。
- Agent担当が固定11c16c46の実ハンドラーをlocalhostで起動し、P/画像/点検/C/照会、遅延4X・4IG候補と公開禁止、P応答喪失、C saved後OGP障害の4経路を隔離検証した。4件成功、外部通信試行0、実課金0、本番操作0。証跡はQA_FIX_AGENT_ACCEPTANCE_11c16c46.json。
- f4aee73からの本番Mina各経路・認証境界・Schemaに差分なし。正式Schemaの実バイトSHAはbaf76695c55298cd97fe8b24fc488c3c164585c11ae735986da380c4e52397abを維持。
- 初回公式CI40647721202はtest:qa成功後、旧静的試験の内部フィールド名参照で停止し、専用E2E未到達。回収失敗や本番API不具合とは断定しない。
- 更新head64590d84a982f9269a29266f576dc7179ddace1cとの差分は静的試験1件と文書のみ。実装・Schema・依存不変をgit差分で確認し、4経路を不要に再試験しない。担当は対象単体・M1回帰・追加独立Goを報告。更新公式CI40648871128は進行中で、専用Preview/回収の最終合格は未確認。

現在の本番は救済OFF、Agent実行コード1c5224b6のまま。旧15:00枠は失効済みで、新しい時間窓の承認はない。権限発行・有料生成解析・食事操作・Discord候補送付・SNS予約公開は実行していない。

## 結果受領後に行う最小確認

1. 本体の固定PR/head、独立Go、局所/型/必須QA/専用E2E、残存QA資源と回収の結果を確認する。Core成功と回収成功を分離する。
2. f4aee73との差分を固定treeで比較し、本番Minaのroute/認証/版/世代/PDC/分析/期限/回数/guard/OGPの入力出力・mina-rescue-v3 Schema実SHA baf76695…が不変か確認する。QA限定という名前だけでは判断しない。
3. 実行経路が同一なら、成功済み全面QAを繰り返さない。必要な差分に絞り、既存Agentのlocalhost実HTTP受入を新しい固定本体treeで行う。外部ネットワーク/実資格/実課金/実食事操作を使用しない。実原本SHAと呼出数を保存する。
4. 非互換があれば未検証をtrueへ補完せず、Agent側最小案を別途具体化する。

## 本番反映・再ONの別承認資料

本体固定commit/PR/公式CI・Preview/期待Deployment、QA資源の実回収が必要か、変更対象・退避/切戻し・OFFでのGET比較を具体化する。Mina契約/食品/Schema/DDL/role/鍵/現mapping推測書戻し/外部再DELETEを混ぜない。
Agent実行コード1c5224b6の変更が不要なら追加配置を提案しない。新body pinの受入・独立原本を用意し、既存の旧原本を上書きせず参照版を区別する。本番再ONは本人承認後に担当が行う。最短案は、最終専用CI・回収成功後の固定PRをtree一致でmainへ統合し、他production release/buildの待機・実行がないことを確認してON設定と正規リリースを一度実施するもの。競合・queue不明・配備/GET失敗は発行へ進まない。設定0だけで稼働Deploymentが即OFFと断定せず、必要な正規OFFリリース・alias・認証GET確認を切戻し範囲に含める。これは別承認案であり、未実行。

## 新しい実行窓の別承認資料

旧15:00は失効済み。新しい絶対終了・対象日/昼食・提案時刻・費用waived範囲・単一case/Pと除外操作を具体化して承認を求める。時刻は修正版検証完了時に決め、予め承認済みと扱わない。

case b414b8f2-368c-4ad9-8beb-9ae867c5e36e / 予定P7251f4e7-597c-4320-a224-1d0a1089e25cは未発行/未送信。再開前に、本体cases0・Agent jobs/requests/calls0・初回未有効化・旧費用/回数/元holdの実在を再確認する。いずれかに実行記録/unknownがあれば単純に新承認へ差し替えず照会から始める。
旧承認・旧settings・全executor・g01・保全manifestを保持し、新承認の原本/参照版を別に作る。既に発行されたcaseの初回/期限/回数を戻す経路は作らない。
既存coreのbuild_flowは明示settingsを受け入れるが、未実行の限定executor-v2は旧settings.jsonを固定参照する。旧原本の上書き・そのままの再実行は不可。次の実行承認には、別名settings/承認原本を参照する限定operator版の参照切替・局所検証・独立Goも明記する。現在はコード読取だけであり、このoperator変更はまだ実施していない。
正式期限は新たに本人承認した絶対終了と、同じcase.first_activated_at+15分の最短。サービスON/READYと権限発行を混同しない。権限の発行は正規リリース/postdeploy成功、fresh native、実4点検、全外部一覧、費用/回数/期限の照合後に限る。

X/Instagram各4候補・8画像・Discordまでを目標とし、朝夕再登録、SNS予約/公開は含めない。遅延提案はprepare_onlyと永続公開禁止を維持し、内容承認だけで公開可へ変換しない。1799.4kcalと昼品質保留を合格候補へ読み替えない。

## 最終結果に添える具体的承認案（未承認）

公式CI・回収・専用Previewの結果が揃った場合に、次の二つを提示する。結果未確定の段階で本番操作を始めない。

1. 本体担当がPR158固定64590d84/tree e952c120fcdc76939d74984ce2855f2582428657、mainとのtree一致可能性と競合するproduction release/buildがないことを先に確認する。既存連携の救済ON設定後に固定treeでmainへ正規mergeし、正規本番リリースを一回実施する。競合/merge不能/queue不明は設定0へ戻して停止する。Deployment/project/alias/commitとpostdeploy・認証GETを確認する。ON後失敗時は権限未発行のまま停止し、設定0と正規OFF復旧・alias・GET確認を行う。追加DDL/鍵/権限・旧QA残存資源の実回収は含めない。
2. 本番結果が成立した場合だけ、10/28昼食の同case/予定Pへ新承認原本を束縛する。旧settings/原本は保持し、別名参照の限定operatorを局所検証・独立Go後に使用する。新たな絶対終了案は10/28 19:00 JSTで、実際の終了は同case初回有効化+15分との最短。これは提案であり、まだ承認も設定反映もしていない。提示時に十分な時間が残っていなければ別の具体案を示す。

②の前提は、双方で未発行・初回未有効化・jobs/requests/calls/cases0を再読取し、真の費用/試行/期限履歴、fresh正本・画像・native anchor、全状態全ページの外部一覧、永続公開排他を照合すること。旧unknownを消さず、不明があれば元UUID照会へ限定する。既存waived方針を維持し、無料/費用0と補完しない。共通最大2回・日解析60回等を維持し、このcaseの予定Pを自動で増やさない。

②に含む実操作は必要な生成/解析/独立画像点検、Drive保存、管理担当による限定権限発行、Pと必要なDまたはC、現在正本と日次の再照合、X4/IG4の準備専用案とDiscord送付まで。朝夕455/716kcalが維持されるなら昼の実解析629〜829kcal、C前後1800〜2000kcalを条件とする。

提案表示時刻案は朝食19:15・昼食19:30・夕食19:45・日次21:30 JST。実際の予約や公開は一切行わず、準備専用の公開禁止を維持する。朝夕の再登録、SNS予約/公開、旧QA資源回収、過去日救済は対象外。
