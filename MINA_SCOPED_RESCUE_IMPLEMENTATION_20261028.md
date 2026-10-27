# 10/28昼食救済v3：Agent実装・隔離受入

## 承認範囲と本番状態

本人の「上記の実装・隔離受入・PR更新を承認」に基づく実装。
本番配置・有効化・権限発行・食事登録/置換・実課金・Discord/X/Instagram送信は実施していない。
本番Agentは既報a34005efd50afa5b75431f2baee05471ffa8027dのまま。本体コード・設定・DBを変更していない。

契約はmina-rescue-v3、接頭辞 `/api/internal/mina/rescue-v3`。
Schema実SHAは `baf76695c55298cd97fe8b24fc488c3c164585c11ae735986da380c4e52397ab`。
当初固定06f43311から、提供側通知の858e2bbb4f62a26ec8dc6e0d5310944541d0ec27へ隔離HTTP参照を更新した。Schemaは同一。
本体PR157の専用Previewは通常Cの日次OGP準備保留が残るとの報告で、成功扱いにしていない。

## 実装内容

- v3専用validator/client/明示Flow/runtimeを追加した。旧v1/v2/通常M1の版計算・PFC/栄養ゲートは変更しない。
- caseは任意の承認対象日・朝昼夕へ束縛し、1case1予定P/1候補。不採用・失効後の新caseや第二Pは作らない。全履歴最大2、日解析60、waived/停止制御は継承する。
- SQLiteのscoped_rescue_jobs/requests/events/callsは契約・日・枠・caseを所有鍵にする。同日同枠の再発行と、旧M1/通常置換/旧救済をまたぐUUID衝突を拒否する。
- UUID、実multipartバイト/境界、画像SHA、期待版・世代・認可revision、承認/費用/原本参照を送信前に固定する。started/unknown/404は元UUID照会だけ。processing/not_savedを未保存確定にせず、savedは後続不明応答でも保持する。真の終端矛盾は新版承認も停止する。
- 元期限は永続episode/receiptから読み、20時は上限検査にだけ使う。欠損を現在時刻や20時で補完しない。他枠のordinal unionを対象枠の消費へ混ぜない。
- 旧業務行・画像・draft/drive・承認原本を実バイトで封印し、各外部処理前後と候補保存TX前後に再照合する。所有者/epoch/入力/コード/Schema変更は停止する。
- 他2食の実food/OGP点検の入力・応答・終了時刻を同caseへ保存し、正式strict receipt/24hex OGP/自己digest除外規則を検証する。receipt形式だけの合格入力は受け入れない。
- 発行時120秒の他食レビューと、P/C時に凍結したレビュー・native正本の一致を分ける。候補画像の認証GETはCV/SHA/MIME/長さ/Content-Digest/鮮度を検証する。
- AIとDriveの各実RPCを個別予約する。親の調整処理を課金として二重予約しない。SDK内再試行は抑止し、不明費用を返却しない。OAuthの生応答はメモリだけへ返し、DBにはhash/長さ/状態だけを残す。再起動で秘匿応答を再利用・再送しない。
- Driveは置換専用の厳密名、所有appProperties、新FileID、旧FileID保持、再読取SHAを使用する。通常保存名の検査は緩和しない。
- C後の日次本文は対象日と実合計から作り、120字/重み110/固定文とURL込み280を送信前に検査する。同じ栄養合計の別日でも本文が重複しないよう対象日を含める。旧本文は保存する。
- 日次M1は契約・日・P UUID別の一度送信台帳を持つ。新しい日次UUID/result/rvを承認元とし、古いCへ結び替えない。C saved後のOGP障害はsavedを維持する。
- 公開前source verifierもv3のC照会に接続し、未知契約を通常置換やM1へfallbackしない。
- 実画像/本文/OGP点検後にX4/Instagram4候補・8画像を作り、旧承認失効/新版再承認までholdを保つ。Discord送信はこのFlowから行わない。

## 完成後の承認照合

完成時は旧normal episodeのUUID・期限・初回・消費を保持し、stageだけをnew_bundle_pending_approvalへ原本追記付きで進める。
通常巡回の読取承認照合へ接続するためのhandoffで、新規生成・通常P送信の入口にはしない。
全X/Instagramの新版判断後、case期限後・救済OFFでもM1/C/OGP/公開画像の読取だけでproofを更新できる。実課金・画像再生成・P/D/C/resumeは行わない。
初回の封印には旧episode全体が残り、許可する差分は当該caseが全点検済みである場合のstage移行だけ。旧期限やUUIDの変更は引き続き拒否する。

全4枠を作るため、最初の公開予定（朝7:30 JST）を過ぎた新規生成は拒否する。
本人のnot_after値自体は変更しない。例えば9:00までの承認でも今夜発行+15分なら利用できるが、Agentの仕上げ期限はcase期限と最初の予定の早い方。
予定後の臨時投稿時刻は別承認が必要。既知savedのOGP再開は本文公開とは分離し、本体のOGP期限を守る。

## 隔離検証

本体の固定Git treeを一時領域へ展開し、原本ファイルSHAを前後比較する。管理DB/実AI/R2/Drive/投稿先へは接続しない。
Node側は外部fetch/socketを全面拒否し、HTTPは127.0.0.1のみ。Python側の外部transportは合成資格/メモリ保存/匿名画像読取へ置換する。

一本の試験で、次を実施した。

1. 本体の実M1登録handler由来の3保存原本・UUID・入力画像と、通常のnative昼holdをAgentへ固定する。
2. 他2食の実点検呼出し原本→Agentのstage_issue/operator_bundle→本体の実createRescueOperator(v3)で合成grantを検査する。
3. 実v3 HTTPでP→認証2画像→実PreparationPorts/DriveClient→C→GET savedへ進む。
4. Flow.finishのGuardedAI/Drive、実M1日次handler、新日次UUID出自、X4/Instagram4と実JPEG8画像を確認する。
5. 全4候補を公開前M1 dispatcher→C専用Reader/各M1 UUID/実OGP画素で照合する。
6. 合成ユーザーの全新版判断後、case期限後/OFFで通常巡回の読取proof更新だけによりholdを解除し、POST/AI増分0を確認する。実SNS公開はしない。

単一grant・新解析1・置換履歴1、hold維持、旧期限/UUID不変、外部ネットワーク0を確認した。
追加負例は、D後再P拒否、応答喪失/再起動/unknown、OFF saved resume、期限60秒、元画像/台帳変更、case取消、各RPCの期限、OAuth秘密非保存、processing→saved/終端未保存、候補TX末尾rollback、他枠ordinal分離、配布Schemaの欠落/改変拒否。
固定実装版は8a160e2952edc42cc06a432489db1bdc5a78ad86。局所49件成功（85.371秒）、独立43件成功（86.537秒）でコード・隔離範囲Go。
独立レビューはMINA_SCOPED_RESCUE_REVIEW_20261028.md、検証原本要約はinvestigations/preparation-recovery-20261028/SCOPED_V3_ACCEPTANCE.json。
同commitの配布物358ファイルを構築し、Schema同梱・全実SHA・activation_allowed=falseを確認した。最終CIはPR更新後に確認する。
初回全体試験の旧配布fixture不足1件は、正式v3の追加と欠落/改変の負例で補修した。

10/28 00:06 JSTの本番RO再確認でもAgentはa34005ef、hold epoch1/held、M1要求6、通常P/D/C・置換試行・候補0。
新v3のjobs/requests表は未作成で、今回の実装・DDL・実食事操作は本番へ反映されていない。

## 本番の停止条件と次の承認

本体の専用Preview/正規本番配備commit/実API照合は完了待ち。既存GETから他食OGP入力24hexを得る正規経路は担当へ照会中で、自己計算・切詰め・仮値では補完しない。
隔離fixtureでは固定本体のmealOgRenderDigestFromRowを使用している。これは本番での取得手段確認ではない。

次のB承認は、両固定commit・Schema一致・最終CI/両側受入/独立Go、SQLite追加と退避/移行整合/コード切戻しを具体化して救済OFFで配置する範囲。
C承認は、10/28昼の旧rv/2SHA/実ad1、所有hold、管理側単一grant/予定P、実承認原本、実効時間窓と本人not_after、既存waivedの費用範囲、取消と照会復旧を固定して提示する。
Cの操作は生成/解析/独立点検/新Drive、単一Pと必要時DまたはC、日次/OGP、4X/4IG・8画像・Discord承認候補まで。実SNS公開、既存3食の再登録/再訂正、過去日の救済を含めない。

設定のMINA_SCOPED_RESCUE_ENABLEDは既定false、MINA_SCOPED_RESCUE_CONFIGは未設定を維持する。
通常daemonから新救済の生成・送信は自動起動しない。runtime構築は実コードcommitと、provider/Agent受入・実装レビュー・配置/実行承認の5原本を対象日/枠/case/両commit/Schemaへ束縛し、未確認をtrueへ補完しない。
