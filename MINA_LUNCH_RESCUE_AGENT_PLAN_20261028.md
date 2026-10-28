# 10/28昼食の期限後救済：Agent実装・受入計画

## 範囲と現在地

2026-10-27作成。これは契約調査中の条件付き計画であり、実装・配置・期限例外・権限発行・実置換の承認ではない。
Log-Eats本体は担当側の管理範囲とし、このAgent作業では変更しない。
本人の依頼に基づき、担当チャット「みなの栄養目標をAPIへ追加」へ詳細な読取調査依頼を送信済み。
担当は既存v2が朝食専用かつ当日発行のみで、今夜の10/28昼食へ使える既存救済経路はないと報告した。
その後、本人が担当チャットで日付・食事枠を柔軟に指定するAPI改修を直接指示したことを確認した。担当側はその範囲で改修中と報告している。最終回答・固定契約・DDL要否・本番承認は未確定であり、このAgent計画を本体の追加承認として転送しない。

Agent本番はa34005efd50afa5b75431f2baee05471ffa8027d、救済OFF。20:49の読取では巡回live、連続失敗0。
朝455・昼628.4・夕716、実合計1799.4kcal。昼の食品・画像不一致保留と、前日20:00の通常期限失効を維持する。
日公開holdは同一所有者/epoch1、P/D/C・置換試行・meal_replan消費は0、初回再計画時刻はnull。
既存6件のM1は完了済み。3食の再登録・再訂正・不足枠収集は今回の許可対象に含めない。
昼の旧rv・原画像SHA等はLOGEATS_LUNCH_RESCUE_REQUEST_20261028.mdに固定する。
朝夕が維持される場合、新昼食の実解析は629〜829kcal、日合計1800〜2000kcalをC前後に満たす必要がある。

## 先に進める準備と契約待ちの境界

原本・現在正本・六M1要求の実バイト/UUID・画像2SHA・native hold入力・全工程/画像修復/費用台帳の対応表と負例を準備する。
通常episodeのgenerating表示や失効履歴を削除せず、新救済から参照する。初回時刻をepisode作成時刻や現在時刻で代用しない。
過去Buffer削除はunknown1件と別観測の現在不在を区別し、terminal要件の対象範囲を提供側契約で明確にする。
新契約確定前は資料・負例・隔離試験設計までとし、旧Schema定数の差替えや有効化は行わない。

提供側へ以下の固定を求める：昼食のcontract/Schema実SHA/endpoint、発行主体・日付制約、単一権限の発行/取消/失効状態、planned P、権限revision、P/D/C排他、照会404/unknown、saved後のOGP復旧、OFF時の許可操作、DDL/ACLの要否、停止・probe・明示resume、証跡鮮度と承認artifactの束縛方式。
絶対終了時刻は実行準備完了後に本人へ具体値を提示する。設計中に15分窓を開始しない。
新窓は本人承認の絶対終了と初回有効化+15分の最短とし、旧期限・累積最大2回・日60解析・旧費用/unknownを保持する。再発行で回数/時間を戻さない。

## Agentの変更範囲

1. 新契約専用adapter/validatorを追加し、固定Schemaを配布物へ同梱して実SHAを検証する。旧v1の10/24・breakfast、v2のbreakfast固定は履歴のまま保持する。route文字列置換やTARGETの付替えはしない。
2. job/request/event/costの所有鍵へ契約namespace・日・枠・case UUIDを持たせる。既存v1の全件横断判定を再利用せず、旧10/24のsavedや原本との混在を防ぐ。必要なAgent SQLite追加は移行案・復旧案とともに別途レビューする。
3. case/P/D/C/resume/日次のUUIDを送信前に永続化し、既存M1/通常置換/旧救済との衝突も拒否する。metadata・実body・multipart境界・画像SHA・期待版/世代・policy/権限revision・原本receiptを固定する。
4. 全日holdとclaim_dueを同一SQLiteトランザクションで排他する。X/Instagram/Buffer/Gatewayの全状態/全ページ/鮮度を照合し、公開済み・送信中・unknown・外部受付済みは停止する。既存draftを未送信扱いにしない。他日の正常巡回は維持する。
5. 既存waived方針を維持し、過去画像修復・通常登録・不明使用量を共通台帳へ継承する。AI/画像生成/独立点検/Drive/日次等の操作予約を送信前に記録する。金額0・仮予算・確認済みtrueを作らず、SDK自動retryを抑止し、不明費用を返却しない。
6. 各P UUIDは一度だけ送信し、応答喪失・processing・404・unknownは元UUID照会に限定する。ready後は同候補版の原本/保存実画像、解析digest、期限、120秒等の証跡鮮度を別々に検証する。古いreviewed_atを現在へ書き換えない。
7. 不合格はD適用確認後、正式契約が許す権限遷移内だけ次候補へ進む。単一case/planned Pと追加Pの関係は新契約で確定し、累積最大2回は上限として扱う。勝手なcase再発行・TTL初期化をしない。CはP時・入力・現在正本と権限revisionを照合し、他2食の実解析/実点検と合算する。上限緩和・丸め・食品削除で保留を解消しない。C saved後のOGP失敗もsavedとしてholdを維持する。
8. C後に新正本・実画像・本文・OGPを再点検し、旧承認を失効させる。日次本文は新3食と実値から再構成し、M1の120字条件・COMMENT_WEIGHT_LIMIT=110・固定本文とURL込みweighted280をAPI送信前に検証する。旧draftのdaily_commentをそのまま流用しない。元本文を保持し、新日次UUID/実bodyを一度送信する。既存daily_shareがある場合の訂正契約も先に確定する。M1で日次更新した場合は新daily UUID/result/rvを承認元として保持し、古いCのinput_hashへ結び替えない。replacement_postreviewのaffected_daily_result分岐も受入対象とする。
9. 新版のX4候補とInstagram4候補（8画像）を構成し、Discord配信結果を一度記録する。replacement_sourceの公開前verify_sourceまで新契約専用Reader/adapterを通し、未知契約を通常置換へfallbackさせず拒否する。全3食・日次の点検と新版再承認まで公開holdを維持する。投稿案作成とX/Instagram実公開の承認を分ける。

## 既存共通処理の事前補修

replacement_ports.pyのdriveはprepare UUID.jpgを渡すが、DriveClient.uploadは日付-食事区分.jpgのみ許す。この組合せは実処理で拒否される。
通常保存名の検査を一括緩和せず、置換専用の厳密な名前/所有metadata/新規FileIDを設計する。旧FileID/画像を保持し、新FileIDと再読取SHAを束縛する。
Drive送信前の操作記録、成功ACK直後の永続化、応答不明時の再upload禁止と読取照合を実装範囲へ含める。
ReplacementRebuilderは現在対象mealだけを変更しdaily_commentを保持するため、上記の本文再構成も必要。
これらは今回コード・本番未変更。独立レビュー後の実装承認対象とする。

## 外部書込み・実課金なしの隔離受入

実native hold→新case→P→認証実画像2種点検→D→次候補→C→saved→日次/OGP→新版4X/4IG/Discordの一連を、実クラスと本体実ハンドラーの隔離HTTPで検証する。
Driveは実PreparationPortsと実DriveClientを通し、外部transportのみfakeにしてupload/metadata/再downloadを照合する。fixtureのフラグをtrueへ足しただけの試験で代替しない。
要求UUID数・外部呼出数・試行/費用消費を各境界でassertし、送信喪失/再起動/unknown/404/保存後OGP障害でも二重POST・二重課金予約・saved降格がないことを確認する。
旧v1別日データ混在、UUID衝突、所有者/hold epoch変更、古いrv/世代/2SHA/policy、取消とC競合、期限直前/経過、累積2回、日60、cooldown/probe/resume、他日巡回を差分回帰する。
日次本文の旧献立/旧概算混入、長文、保存後点検不合格、新daily UUID/result/rvの出自、承認失効、Discord応答喪失を確認する。実source verifierの新契約読取結合と未知版拒否も含める。Instagramは候補・8画像までで公開しない。
本番DBは読取snapshotのみ。実資格・実Gemini・本番Drive/Buffer/Discord/食事API書込は接続しない。

## 承認の段階と提示物

1. 契約確定・提供側計画Go後：固定契約に対するAgent実装、必要SQLite移行設計、隔離検証、独立レビュー、PR作成。実課金/本番書込なし。
2. 両側受入後：両commit・Schema SHA・CI/専用Preview成功・移行対象/バックアップ/コード切戻しを提示して、Agentを救済OFFのまま配置する承認。業務DBを巻き戻さない。
3. 実行直前：10/28昼食の旧rv・画像2SHA・新caseの単一発行、発行主体、明示絶対終了時刻、既存waived操作範囲、取消・停止・復旧条件を提示し、提供側有効化/権限発行とAgent限定実行をそれぞれ明示承認へ進める。
4. 実操作の対象を列挙する：新候補生成/解析/実画像点検/Drive保存、最大2回内のP/D/C、C saved後の日次/OGP点検・必要な日次API、X4/Instagram4候補・8画像・Discord配信。既存3食の通常再登録/再訂正、過去日救済、X/Instagram公開は含めない。

契約・DDL・提供側レビュー未確定の間は1の実装開始承認を求めず、計画承認と契約確定後の再提示を区別する。今回の独立Goは計画のGoであり、本番操作のGoではない。
