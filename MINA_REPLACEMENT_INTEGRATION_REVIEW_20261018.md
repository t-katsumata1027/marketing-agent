# 保存済み一食置換のAgent統合計画 独立レビュー結果

2026年10月18日。対象はMINA_REPLACEMENT_INTEGRATION_PLAN_20261018.md。判定は計画Go、Blocker・必須修正0件。ユーザーの実装承認、本番・リリースのGoではありません。

## 固定入力

- Agent基準head：78ca80a869dd3e6c5cb43e10b007e407c70d7f36。
- 提供側基準commit：bb49b016f79119917c5f0cf520537316fdb098c1。
- 計画本文SHA-256：953a4abfd41bdb7505a022d9e7a9869d903764880072164931e94fe7feee0bfb。
- Schema SHA-256：8b5fc6ecce9a9b406e7197f60c6ae49cb8f94edd9702c3b2db33babe78396090。
- レビュー入力SHA-256：f2fa6368c9e772c13a475fcdca4d4fd95010dcfcc8f29688a0573b70c118d3e7。

agyのgemini-4-argon-highへ、計画全文・提供側Schemaの境界抜粋・既存先行実装の状態・提供契約の意味規範を直接渡しました。ファイル・ツール・ネット・編集・委譲を禁止した独立計画レビューです。提供側全コードまたは未実装Agentコードのレビューとは扱いません。

## Goの範囲

Marketing Agentの保存済み一食置換（mina-replace-v1）統合実装および、外部書込み・実課金を伴わないローカル・隔離Linux・CI環境での合成HTTP／一時SQLite検証まで（本体変更・本番DB移行・本番配置と有効化・専用QA実接続・実サービス送信・予算追加・18日分救済は対象外）。

実装前にこの範囲の明示承認を求めます。Log-Eats担当からの報告をAgent実装承認へ読み替えません。

## 確認された点

- P/Dの候補操作とCの保存成否を分離し、一度送信開始した要求を再送しない。HTTP 409/503や後の読取障害でsavedを降格させない。
- 三者一致・候補版・実画像・証跡・120秒鮮度の検査を行い、P時のholdとC直前の外部再照会を区別する。
- 日holdと送信確保の同一SQLite排他、旧承認失効、新bundleだけの承認受付、必要承認集合に基づく解除を定義している。
- 共通予算から本体割当を拘束して二重支出を防ぎ、既存ordinalと不明費用を引き継ぐ。
- 置換の期限と通常の人間承認・定刻公開の期限を分け、期限後に有料生成やP/D/Cを始めない。

## 今回実施した検査と未実施

提供側凍結MANIFESTの11ファイルは実SHAが全件一致しました。Draft 2020-12 Schema自体と、提供51ケースの期待valid/invalid判定をローカルで確認しました。これらは契約データの検査で、Agent統合の実装・回帰試験や実HTTP接続成功ではありません。

今回はAgent・Log-Eatsともコードの変更なし、本番のサービス・設定・DB変更なし、外部API書込みなし、18日分の操作なし。新APIは提供側報告上まだ未push・未デプロイであり、本番利用可能とは扱いません。

## 証跡

原文はtmp/replacement-agent-plan-20261018/agy-review/result.json、固定入力は同input-manifest.jsonとprompt.txt。契約照合はcontract-provenance-check.jsonとfixture-schema-check.jsonです。
