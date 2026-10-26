# Instagram臨時作成と準備継続の恒久対応

## 原因と臨時作成の実績

10/26 20:10:06 JSTのInstagram翌日準備は、共通の3食成果物・X候補が未完成のため失敗した。既存常駐は配信と通知だけを巡回し、準備は一度のtimer実行だけだった。共通成果物が10/27 00:58に完成しても追従せず、日付をまたいでprepare-nextを実行すると10/28が選ばれるため、10/27が取り残された。10/27の初期台帳はInstagram候補0・投稿要求0・画像0、保留通知1件送信済みだった。

本人の臨時作成依頼に基づき稼働00b1f898の既存render/prepare/notifyを対象日10/27へ明示実行した。食事登録・解析・画像生成AIは使用していない。原本3食と日次、実合計1961.6kcal、API目標2000/60/56/314に一致するstory3枚とcarousel5枚を作成。実装不参加Codexが全8枚を実視認し、全実SHA/長さ・料理・表示値・文字・余白・達成率の独立Goを確認した。

bundle SHAは3a4a5f99459b44d798a6f2d4220a23fb1c49fec53077dacb2dda7af7ff7ff558。新規合成素材だけを既存Drive保存・匿名実バイト確認で配信可能にし、Instagram候補4件を承認待ちで保存、Discord4件送信完了。実Instagram公開・Buffer新規投稿要求・食事再登録/再訂正・Log-Eats本体変更は0。X候補の本人承認は保持した。

## 恒久実装

- Instagram常駐が、対象日前日20:10から対象日朝食前まで準備を追跡する。日付変更後も当日分を選び、朝食の時刻を過ぎた4枠を自動で作り直さない。
- 未完成の依存は外部APIを呼ばず待つ。準備済みX候補のdigestが変われば即再確認し、同じ入力の読取不一致は60秒間隔に抑える。API品質・画像・栄養目標・X正本の既存照合は維持する。
- 永続claimはLinux boot ID/PID/開始tickで所有者を識別する。生存プロセスを奪わず、読取工程での中断だけを再開する。不可逆な画像保存・権限付与を開始した不明工程は照合停止を維持し、時間切れで再実行しない。
- 保存先metadata/OAuth・API再読取・BufferチャネルGETは読取工程に含める。最初の新規保存の直前に外部開始を確定する。各Drive書込み直前と候補保存同一transaction前後に、公開hold・日付期限・X digest・所有者を再検査する。
- 4候補を全件検査後、同じSQLite transactionで保存する。途中の競合・期限・公開holdでは全件rollbackする。候補保存後〜通知作成の間に中断しても、次巡回で不足する承認通知を冪等に作る。通知成否不明は再送しない。
- instagram_preparationsとinstagram_preparation_eventsに状態と停止工程・固定理由・安全な呼出し位置を保存する。外部応答や秘密の原文は保存しない。

## 通常準備のCLI異常終了への対処

過去のCLI異常終了は結果がなく、原stderrを保存していないため下位原因は確定できない。認証ラベルも任意の401部分一致による誤分類があり得るので、HTTP/status文脈のある401だけを分類する。

通常の翌日prepare_dayだけが明示許可するContext内で、書込み能力のない点検・文案・訂正案・献立案の6処理を同じ入力で最大3回、総時間360秒以内に扱う。各試行前に既存の期限/版guardを呼び、同じプロンプトSHA・schema SHA・全画像SHAを固定する。終了コード正数・結果ファイルなし・失敗原本/診断が一致したexecutionだけが対象。認証・上限・構文不正・timeout・出力不正・品質holdは再試行しない。画像生成、Drive、食事API、Buffer、Discordの書込みは対象外。

全試行の順序・失敗job・不明使用量をreview-chain台帳に残し、最終成功で過去の不明使用量を0に戻さない。再起動後にunknownとなったPreparationJournal工程を新しいchainで再実行しない。3回後も不明なら従来どおり停止する。

置換・救済・当日収集のmanaged_callでは内側の許可Contextに優先して再試行を禁止する。既存の1呼出し1費用予約・unknown・累積回数・期限を維持し、bounded/waived共通台帳の内側で未計上の複数CLIを呼ばない。実ReplacementFlow/RescueFlowを通す回帰で、CLI1回・費用unknown1件・再call拒否と期限超過拒否を確認する。

## 検証・反映

日跨ぎ依存復旧、読取backoff、生存owner競合、死んだ読取owner復帰、外部不明停止、公開hold競合と解除、候補原子保存、通知前中断、実RuntimeのBuffer読取失敗からの復帰、CLI有限再試行/品質保留/費用境界/期限を検証する。最終固定commit・CI・Linux実owner検証・独立Goを揃えてからAgentを反映する。

本体コード、既存食事・画像・X承認、Instagram公開権限の既存スイッチ、救済OFFは変更しない。旧未知記録は保持する。具体的な固定版と配置後実績は検証完了後に追記する。

## 最終検証と本番反映

固定実装fde12d64a63fc94683e1ecac272d5c60f675243c、Draft PR #18。CI40359655087はMina702件中651成功/51skip、一般32成功。秘密環境を引き継がない別checkoutをサブPCへ作り、実mina-agent UIDで54件を8.292秒で成功。Linuxのboot ID/PID/開始tickの実所有証明、別プロセスの生存→終了、読取owner回復、公開hold解除、実RuntimeのBuffer読取失敗からの復帰、実Replacement/Rescueの共通費用unknown保持を含む。

実装不参加Codexの最終コードレビューGo。最終対象30件はWindowsで29成功・Linux実PID1skip、Linux側で当該試験成功。画像8枚も生成不参加のCodexが全SHA/長さ・実視認でGo。初回レビューの通知復帰・費用予約内のretry・読取境界・hold競合の4指摘は対応済み。

配置operatorは独立Go後に実行。Type=oneshotのactivating/deactivatingもbusyとして、prepare/IGprepare/notify/backup/offsiteの5処理を初回・停止後・rename直前・rollback待機で確認する。6状態の停止判定を隔離検証し、実行中の処理をkillして切り替えることはしない。

10/27 01:35 JSTに本番へ反映。旧40表の全行は切替時不変、新しいinstagram_preparations/eventsの2空表だけを追加した。退避は/var/lib/mina-agent/deployment-review-recovery-fde12d64、旧コードは/opt/marketing-agent-rollback-review-fde12d64。配置後も既存39非health表の既存行を保持し、operation_eventsは起動2件の追記だけ、新2表は0行。3サービス/既存5timer、実プロセスの固定commit、救済OFF/UTM/通常置換の保持、切替後巡回失敗0、SQLite integrity_check=okを確認した。

Instagram実プロセスではPREPARE/APPROVAL/NOTIFY/PUBLISHが既存どおりtrue、固定commit=fde12d64を確認。これは準備追従が有効である証跡であり、未承認候補を公開する許可ではない。10/27のInstagram4候補とX4候補は本人がすべて承認済み。Instagram承認依頼4件sent、8配信URLを再取得して全SHA・寸法一致、4候補の現在API/原本/本文整合を確認。Instagram投稿要求は0で、実公開はまだ行っていない。時刻は通常どおり朝07:30・昼12:15・夕19:15・日次21:30 JST。

今回の恒久化は、依存復旧への追従と、副作用のない通常CLI処理の有限な再試行・証跡保全である。過去CLI異常終了の下位原因は、元stderrが残っていないため未確定。認証/上限/タイムアウト、品質保留、全試行失敗、外部書込み不明は引き続き保留し、再発ゼロや任意の障害からの無制限回復は保証しない。

実証跡はinvestigations/instagram-continuity-20261027のINITIAL_STATE.json、RENDER_MANIFEST.json、LINUX_ACCEPTANCE.json、POSTDEPLOY.json、FINAL_INSTAGRAM_VERIFICATION.json、RUNTIME_FLAGS.jsonを参照。
