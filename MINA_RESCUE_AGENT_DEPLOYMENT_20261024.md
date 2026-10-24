# 10/24限定救済：Agent本番配置、救済OFF

2026-10-24 13:39 JST、本人の条件付き承認の両条件を確認し、Agentのみ配置した。食事登録・置換・実課金・通知・投稿は行っていない。Log-Eats本体の変更は0。

## 固定版と条件

- Agent配置：5dbc45bce2e49fb8f940b31075f304fb5a3292a3。実行コードは独立Goのfc81e75と同一、後続は文書のみ。
- 最終CI：[40049695981](https://github.com/t-katsumata1027/marketing-agent/actions/runs/40049695981)、両job成功。
- 本体照合版：5001250ce852a35c8b2242c714f278da60000a32、[専用Preview成功](https://github.com/t-katsumata1027/LogEats/actions/runs/40059175879)。本体担当報告65成功、30skip、失敗/flaky0。これは本番反映の証明ではない。
- 既受入48e5459からのsrc差分はQA routeとqaFixture.tsだけ。救済業務コード、正式Schema SHA1532cae8d201be23fbbee8ee1a8bd11d3740a527cf5a0e188734ff65b5c36082、SQL SHA12dfe9d834689b28730a506bb8b45caabac3706d482c37e8b1fd04f78a318b1aは同一。新commit自体のAgent全面試験を実行済みとは記載しない。
- 配置operatorは別Codexが限定レビューしGo。途中移動・部分停止・timer再開後失敗の復帰境界を実行前に補修。確認SHAはinvestigations/rescue-deployment-20261024/GATES.json。

## 実施と事後確認

旧f2119d48f2f9c5a7a5c4f979cdc85a9248b81f09と依存不変を確認し、固定bundleを別配置。既存仮想環境とCLI資格情報は所有者・保護modeを保持してコピーし、新規依存インストールなし。

3サービスと既存active timerを停止し、設定・SQLiteを退避した。追加は7空台帳のみ。既存27表全行hashは切替時一致。コード切替後、MINA_SOURCE_COMMITだけを配置版へ更新し、MINA_SAME_DAY_RESCUE_ENABLED=falseを明示した。他の運用設定・通常置換設定ファイルは保持。

13:39:36 JSTの読取検査で、3サービスのDoppler親/実Python子も同じcommit・救済false・UTM true・通常置換trueを確認。3サービス、準備/通知/バックアップ/Instagram準備/offsiteバックアップの既存5timerはactive。cycle_healthの失敗0、integrity_check ok。

起動後もcycle_healthを除く26表の既存行を保持し、業務表25表の行数不変。operation_eventsは起動監査2件の追記のみ。追加7台帳は空、今日のX候補0。朝食品質保留は解消していない。実行承認artifact、救済grant、日別の適格性や有効化設定は作成していない。

| 保全物 | リモートパス |
|---|---|
| DB・設定・切替前後hash | /var/lib/mina-agent/deployment-rescue-off-5dbc45b |
| 旧コード | /opt/marketing-agent-rollback-rescue-5dbc45b |

実行前のローカル照合1回目はGitHubの日本語JSONをcp932で読めず停止した。リモート操作前であり、UTF-8指定後に成功条件を再照合して配置した。過去の本体Preview失敗runも保持。

## 残事項と境界

本体PR151はDraftで本番未導入。担当側の本番移行入口・実効ACL・正規リリース・認証付き本番照合と救済有効化は別承認。Agent配置完了を本体準備完了へ読み替えない。

今日のL/Rは別承認。旧11:45/12:00案は未承認のまま過ぎたため使わない。新しい時刻の提案はMINA_RESCUE_EXECUTION_APPROVALS_20261024.md。実行時の正本・版・画像・外部全状態全ページ・要求/費用/ordinal・他2食点検を再照合し、未確認を補完しない。全3食合格まで公開しない。Instagram固有操作は別担当の範囲を保持する。
