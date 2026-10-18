# 検証版の業務停止配置

2026年10月12日。配置版は`7b06e18f20df8b5deb7c31b5315474d0992257fc`。実装コード・deploy・依存は独立再確認済みc087611から変更なし。後続は境界試験と説明の追加。

## 配布・検証

- Windows217件、サブPC別配置をmina-agentユーザーで実行して217件成功。
- [同版CI38231534011](https://github.com/t-katsumata1027/marketing-agent/actions/runs/38231534011)の専用・従来ジョブが成功。
- 停止配布129ファイル、bundle実SHA照合、root所有の候補で配布commit照合、固定依存の一致を確認。
- 共通画像指定の実3食成功はIMAGE_QUALITY_ACCEPTANCE_20261012.md、独立再確認の範囲・指摘対応はINDEPENDENT_IMAGE_RELEASE_REVIEW_20261012.md。

## 実施した切替

既存の固定venvとCLIを複製し、新しい資格情報や依存の追加なしで候補を準備した。CLIコード内へ認証ファイルを混ぜず、認証は既存の専用ユーザー保護領域を維持。

旧コード28388a3は`/opt/marketing-agent-rollback-20261012-7b06e18`、旧設定・整合SQLite・unitはroot専用の`/var/lib/mina-agent/deployment-rollback-7b06e18`へ保全した。新コードを`/opt/marketing-agent`へ切替え、MINA_SOURCE_COMMITだけを新しい固定版へ変更。業務用8設定をすべてfalseのまま保持した。

同日の完了・SHA一致・実Drive照合済みの旧バックアップ台帳だけを、同じ保存先で版付きのverified名へ変更した。暗号化実体・受領記録は移動も削除もせず保持。成否不明記録を消したり未完了工程を成功へ変更したりしていない。新版の実バックアップサービスを実行し、Drive保存・全バイト再取得照合が成功。旧28388a3と新7b06e18の両台帳はverified、未解決0件、暗号化2ファイル計24,935バイト。

## 切替後の確認

- 正式Doppler経由の専用ユーザー診断：業務用8設定false、必要設定不足なし、候補0件、成否照合要求なし。
- heartbeatと巡回監視は新鮮・正常、Mina本体とGatewayはactive。
- 生成タイマーinactive。通知・外部バックアップタイマーactiveだが、業務通知の許可はfalse。バックアップ専用許可だけtrue。
- バックアップ実サービスResult=success／ExecMainStatus=0、読取監視healthy。読取監視そのものはDrive再取得・復号をしない。
- QAランナーlogeats-ops-01、logeats-secure-01はonline・非稼働。今回停止していない。

## 復旧と未完了

旧コードへ戻す場合も、全業務を停止し、固定コードと保護設定、DB・画像・UUID・外部正本を照合する。旧DBをそのまま戻して運用を再開せず、承認失効と不明送信の保全を守る。同日のバックアップ台帳も版へ対応付け、未完了記録を削除・上書きして再実行しない。

自然なバックアップタイマー発火、ログインなしの自動復帰、保持世代の運用整理、Google公開文書整合、日次全工程とLogEats最終版への適合、許可後のDiscord承認・X公開の実受入は残る。停止配置の成功は開始承認ではない。
