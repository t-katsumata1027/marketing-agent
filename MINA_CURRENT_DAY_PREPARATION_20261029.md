# 10/29限定再開と、時刻未定の準備専用カード

## 範囲と現状

本人の承認は、ローカル実装・外部書込み/実課金なしの隔離検証・独立レビュー・PR作成まで。本番配置/有効化、当日の実生成/解析/登録/訂正、Discord送付、SNS予約/公開はまだ行わない。28日過去日置換は使わず、本体を変更しない。

基点はAgent41159b3b。29は当日正本0食、旧献立/食品別概算が完了、夕食再計画1回目がunknown。初稿1930と食品別概算1682.4の矛盾は基点で修正済みだが、旧unknownを新コードで自動解除することはできない。通常prepare-todayは朝食を除くため流用しない。

## 例外権限

新契約は`mina-current-day-preparation-v1`、対象は10/29固定。`MINA_CURRENT_DAY_RECOVERY_ENABLED`は既定OFFで通常daemonに接続しない。

原本は当日draft、旧3工程の全行、旧夕食のローカルvalidation_failed監査、旧食品別policyの固定Git版/指示に束縛する。旧unknown・費用・初回時刻・旧準備行は更新せず、別のoriginとして保存する。旧期限原本が存在しない場合はnullを保持し、旧コードから計算した前日20時は`derived_policy_deadline`へ分離する。旧unknownの開始時刻を真の全工程初回だと推測しない。

固定旧版のPythonを実行せずASTで食品別点検の指示リテラルを照合し、完了済み三枠の概算へ参照だけを追加する。旧call・結果・費用の複製はしない。他2食の献立・食品・量・コメント・初稿概算欄は変えず、夕食の残ordinal2だけを生成可能とする。新episodeや日付変更で3回目へ進まない。

権限の初回有効化と期限を独立欄へ一度だけ保存する。期限は本人指定絶対終了と例外初回+15分の早い方。初回は監査でも固定し、再発行/時計戻り/期限改変を拒否。新しい外部操作は残60秒未満で開始しない。

全日holdと送信claimは同じSQLite排他で管理する。全状態・全ページの外部予約を取得した実receiptを要求し、90秒で再取得、最大120秒の鮮度を検査する。既存公開・送信中・不明・外部受付済みがあれば開始しない。手動の食事以外の投稿は既存の履歴照合方針を維持する。

各外部境界で同じDB・権限・root承認/コード・旧3行・原因監査・元draft実バイト・公開holdを再照合する。通常入口からの期限後再計画、画像作成、Drive、M1を認めない。SDK自動retryを無効にし、新しい論理操作の開始/完了/unknownをwaived台帳に残す。unknown費用を未消費0へ戻さない。既存要求UUIDは送信前固定・不明時GET照会だけとする。

## 準備カード

通常`candidates`/`instagram_candidates`テーブルは使わず、専用の8カードを`current_day_cards`へ保存する。scheduled_atは常にnull。元の予定時刻・未公開・遅延公開未承認を表示し、仮の未来時刻を作らない。朝食も含むX4/Instagram4を作る。

既存`scoped_preparation_only` KEYと永続`scoped_preparation_locks`を保持する。KEY内容が壊れても、holdが解除されても、旧1cへ戻しても送信を拒否する。専用カードは通常通知daemonが読まない。SNS予約/公開ボタンも付けない。内容の確認と公開時刻の承認は別。

本文検査は通常候補の内容検査を再利用する。通常Candidateの未来scheduled_at検査は緩和しない。現在M1/UUID/日版/実OGPを確認し、3食実解析1800〜2000と一回訂正・独立点検を維持。Instagramは既存料理写真と実栄養/目標からPILで実合成する。目標nullを仮値で埋めない。

Discordは点検済みJPEGを添付し、一回だけPOST。送付直前にも期限を確認し、成否不明を新メッセージで再送しない。ローカル合成画像は準備カードに添付し、Buffer下書き・Instagram投稿・新たな匿名Drive配信は作らない。料理原本のDrive保存は既存通常手順を使う。

## operator

実装入口は`python -m src.mina.current_day_cli`。`--database`と`--directory`を明示する。

- `origin`：既存DBをROで読み、旧入力/結果/監査と旧policyの由来digestを表示。外部呼出し0。
- `status`：既存jobをROで表示。
- `lookups`：機能OFF・期限後・承認configなしでも既存UUIDのGETのみを実行。DB作成・更新・新claim・POSTは0。コード切戻し時は保全した新ツールの読取入口から実行する。
- `activate --config ...`：別承認3原本・実配置版・初期M1・全外部一覧を確認し、初回とholdを一度だけ保存。
- `run --config ...`：未消費jobをCASで確保し、夕食残1回→3画像→Drive→通常M1/一回訂正→日次→実OGP再照合→8カード→Discord。失敗/再起動後のrun再実行は不可。元UUID照会へ戻る。

構成JSONはapproval/database/directory/host/organization_id/channels/references/historyの閉じた形。approvalにはspec/target_date/recovery_uuid/code_commit/not_after/origin_digest/initial_state_version/approval_reference/mode/cost_modeを持つ。not_afterはUTCミリ秒Z形式。referencesはimplementation_review・deployment_approval・execution_approvalの各path/SHA。historyは既存の履歴原本をpath/SHAで明示し、日付フォルダーにhistory.jsonがあるとは推測しない。履歴の本文・hashも各境界で固定する。

各role原本はrole/contract_version/config_digest/verdictを持ち、config_digestはreferencesを除く構成全体のfingerprint。verdictは実装Go、他はapproved。構成と全roleはLinux root所有・group/world書込禁止、Actorには読取りだけを与える。Actor自身が作ったapprovedを承認と認めない。Windowsからの実行承認読込は未対応で拒否する。API資格は既存の保護環境を用い、新規キーを作らない。

## 検証・次の承認

実SQLite・旧1cの固定生ソース・PIL実画像・通常登録/共有工程・multipart実バイトを検査し、AI/Drive/業務API/Discordは代替する。実課金・本番操作を試験成功の条件にしない。独立レビューの修正と最終固定版の試験/CIはPRへ記録する。

本番配置・有効化・権限原本の封印、必要な画像生成/解析・通常3食登録/契約内訂正・日次・Discord送付は別の具体的承認。28の救済APIや管理helperはこの実行の依存ではない。既存食事の削除・再登録・保存済み画像置換・SNS予約/公開を含めない。

実行窓は受入後の現在時刻を見て絶対終了を提示する。過去の10時候補を許可済みとせず、期限に間に合わなければ始めない。合計15分を保証のために無断延長しない。保存後の品質保留や未知結果が出たら、その事実を保持して停止する。
