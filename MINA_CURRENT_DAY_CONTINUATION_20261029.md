# 10/29・停止済み準備の限定継続

## 承認範囲

今回の本人承認は実装・外部代替の隔離検証・独立レビューまで。本番配置、有効化、追加の実AI/課金、Drive、食事登録/訂正、日次/Discordは未承認。Log-Eats本体は変更しない。10/28救済API/管理helperを使わない。

09:29の親jobと09:44の旧期限はheldのまま保存する。再発行・issued復帰・旧期限更新で再開しない。新契約は`mina-current-day-continuation-v1`、対象10/29固定。`MINA_CURRENT_DAY_CONTINUATION_ENABLED`は既定OFF、通常daemonへ接続しない。

## 実原本の入場条件

root管理入口`python -m src.mina.current_day_continuation_cli checkpoint --database ... --directory ... --receipts ... --stop-log ...`が、既存DBをmode=ro/query_onlyで読む。停止traceはroot所有・第三者書込不可。既存の鮮度例外で、旧3工程/原因監査・親held・夕食ordinal2採用・朝修復合格・昼修復画像complete/点検未開始だけを認める。

原稿・採用計画・plan-revisions・修復round・各画像の実path/SHA/長さ・Journal入力digest・食品basis/admission/adoptionを固定する。朝昼/日次計画は旧原稿と一致すること、採用前後と食品別根拠の整合を要求する。親初回・旧期限・旧unknown費用は変更しない。

新AI9回とM1読取1回の元callがcompleteであることを確認する。実CLI9組のinvocation/preparation入力digest・schema・state/diagnostic終了0・raw結果を対応させる。画像は実PNGを既存と同じquality90/optimize JPEGへ変換して保存SHA/操作結果digestと一致させる。reviewはfindingsを従来規則で検証して旧判定とcall digestへ照合。再計画rawは採用夕食、食品推定rawは旧食品値とcall digestへ照合する。receiptの形式やファイル凍結だけでは由来確認としない。

capture/継続発行時には通常候補・専用カードのprepared/sending/unknown/sentを含む全状態、M1要求、置換要求、公開/送信/unknown/外部受付済みが0件。発行時は元call/Journalの集合もexact一致し、停止後の追加操作を取り込まない。外部X/Instagram/Buffer/Gatewayは全状態・全ページの新鮮な実receiptを要求する。未照合draftも拒否する。

checkpointには食品情報を含む旧行があるため、端末や公開ログへ展開せずroot所有の保護ファイルへ保存する。Agentへ管理資格を渡さない。root checkpointもActorが自作したapprovedでは代用できない。

## 別権限と工程

新台帳`current_day_continuations`は日と親jobが一意。別UUID・別初回を一度だけ保存し、本人指定絶対終了と継続初回+15分の最短を期限とする。親`current_day_jobs`はUPDATEしない。新grantのCASのみissued→running→complete/heldへ進める。再発行・二重起動・再起動によるやり直しは不可。

root所有構成は従来の閉じた構成へcheckpointのpath/SHAとreceiptsの絶対pathだけを追加する。approvalはspec/target_date/recovery_uuid（親ID）/continuation_uuid/code_commit/not_after/checkpoint_digest/initial_state_version/approval_reference/mode/cost_mode。実装Go・配置承認・継続実行承認の3原本を新契約/構成digestへ束縛する。保護設定の親はActorに通過/読取だけを許す領域を使い、秘密ファイルの親権限を緩めない。

1. 合格済み朝食修復画像を同じJournal入力/path/roundから再利用する。再生成/再点検呼出は0。
2. 生成済み昼修復画像を同じ原本から再利用し、未開始の独立点検だけを一度実施。不合格は即停止。
3. 未開始の夕食画像を生成・独立点検。同献立の一回画像修復だけを許す。夕食再計画3回目や朝昼の再生成/再計画は不可。
4. 3食画像合格後、共通のfinish工程でDrive保存、通常M1登録/既存一回訂正、実解析1800〜2000、日次/実OGP、公開時刻未定のX4/Instagram4カードとDiscord添付へ進む。

各外部境界・Journal開始のTX前に実一覧を更新し、TX内で原本・権限・期限・所有epochと鮮度120秒を検査する。更新開始前にも新grantがissued/running・残60秒以上であることを確認し、terminalで新一覧を取得しない。新unknown費用を未消費へ戻さず、SDK自動retryを使わない。

昼点検保留は即停止、夕画像は許可済み一回修復内、M1は既存一回訂正内だけ。各上限後の保留・unknown・期限切れを迂回しない。親hold/旧コードへの公開禁止・専用8カード/再送禁止は保持する。SNS予約/公開ボタンや通常候補への取込は追加しない。

## operatorと復旧

- checkpoint/statusは機能OFFでもROで利用可。checkpointの原本確認はroot管理入口だけ。
- activateは3原本・現在正本0食・実外部全ページを確認して別grantを発行。
- runは未消費grantだけ。終了後は既存`current_day_cli lookups`で元UUIDのGET照会へ戻る。新POST/別UUID/別episodeで回避しない。
- コード切戻しは旧版668e7cb6。親job、新grant、画像、要求、費用、holdは消さない。通常のOFF配置のみで現在のheldが復旧したと報告しない。

## 検証と次の承認

実SQLite/既存Journal/PNG→JPEG/独立点検/通常M1/OGP/Instagram合成/Discord multipartを通し、外部AI/Drive/M1/Discordは代替する。CLI resultのpass差替え、操作digest、receipt欠損、元入力/初回/期限/hold変化、専用カード4状態、追加unknown、二重実行、残59秒、130秒生成後の再取得、一覧不明、昼不合格、夕修復上限、Discord応答喪失を拒否する。

本番の実原本は別の読取専用受入でも照合するが、実AIや本番M1/Discordの成功とは扱わない。独立レビュー、固定版CI、配置/実行許可は区別する。

提案する次の承認は固定版の救済OFF配置・実ファイル/台帳不変の確認、継続原本の管理封印と一度の限定実行。時間窓は現在時刻を確認して絶対終了を提示し、継続初回+15分と早い方に固定する。10:30案は本人に新たに承認されるまで実行許可ではない。間に合わなければ有効化も延長もしない。
