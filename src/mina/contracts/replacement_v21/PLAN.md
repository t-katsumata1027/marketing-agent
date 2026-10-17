# 保存済み一食の別メニュー置換 契約計画 v2.1
作成日：2026-10-17。状態：実装承認前。前版を置き換える規範計画。コード基準：f2ced5e2a884a73587b1091ba3852fa9a2e4af9a。Agent配布版の情報源：担当者報告の1d3753f5130c43c76029a8b6b73febcaefe0a3e9（本体担当が配布コードを直接検証したという意味ではない）。
関連文書：CONTRACT.schema.json（入出力）、FIXTURES.json（合成データのみ）、EVIDENCE.md（採否・QA・レビュー対象）、AGY_REVIEW.md（対象版と結果）、MANIFEST.json（SHA照合）。

## 0. 調査結論・前提の確認範囲
現行Mina operationはmeal_register/meal_correction/daily_shareのみ（canonical.ts:6）。meals新規POSTは予約時と保存時に枠の占有を検査（handlers.ts:645-805）。訂正は栄養合計とanalyzed_dataのマージのみ（store.ts:436-449）で画像列は変わらない。一般logs PATCH/reanalyze/meal-images GETもMinaの保存済み原画像置換契約の代替にならない。既存の安全な置換手段はない。
以下は2026-10-17 20:15 JSTのAgent担当者による読取要約であり、本体担当による本番再確認ではない。
- 18日分の元登録3要求はsucceeded/saved/completed/modified。後続訂正によるmodifiedを未保存と扱わない。
- 最後の訂正3要求はsucceeded/saved/completed/unchanged、保護台帳とcurrent_result一致。既知未完了0件、日GET完全・3食・版形式と鮮度合格。
- 朝484、昼633、夕610、合計1727kcal。食品・コメント・共有等とrvは最後の訂正と一致。
- 原本SHAはローカル実バイト・既存Drive照合証跡・訂正結果で一致。今回はDrive再取得、変換後画像・OGP実バイトの取得は未実施。OGP metadata ready/SHAありは実画像点検合格の代用にしない。
- 18日分のX/Instagram候補・送信台帳/Buffer台帳は0件。外部Buffer全件照会は未実施で、外部予約不存在の証明ではない。17日分の投稿・承認は保持。
- meal_replan工程数は朝昼夕0件。同メニュー画像修復・登録・訂正の試行まで0件という意味ではない。
- 費用上限実額・承認範囲・管理場所、現行準備期限の実値、新契約・世代は未確認/未提供。
夕食独立点検image_analysis_mismatch（uncertain/center/food_index=3、計画まいたけ30g）は未解決。API品質passを独立点検のpassへ読み替えない。1727は下限1800より73kcal不足。朝昼が同じなら夕食683〜883kcalが必要。登録栄養目標とこの運用条件は別々に扱う。

## 1. 変更範囲・認証・互換性
新契約mina-replace-v1を明示選択する。旧mina-write-v1の入力・応答・ih1/sv1/rv1計算・GETの読取専用性を再定義しない。新しい候補/制御台帳、旧版履歴、世代、公開識別失効管理、対応OGP・QAだけを本体実装範囲にする。
全経路は既存Bearer・固定アカウント・Origin拒否・no-storeを継承。UUIDを送信前にAgentの保護台帳へ永続化する。任意の外部URL取込、callerのuser ID、管理資格、Clerk認証流用を受けない。認証不備/別対象は既存同様の隠蔽404。画像もこの認証を必須とし公開URL・リダイレクト・署名URLを返さない。
本体担当はAgentプロジェクトを変更しない。両担当は固定Schema/fixtureで契約を合わせ、両側QAと接続条件成立まで本体新機能を有効化しない。

## 2. APIと応答の固定
表のUUIDはすべて要求台帳の識別子であり、UUID単体から対象を推測しない。GETに画像/解析/OGP準備・修復・期限処理の副作用を持たせない。
| 操作 | 経路 | 契約 |
|---|---|---|
| precondition | GET /api/internal/mina/meal-replacement-preconditions?date=YYYY-MM-DD | 同じread-only snapshotの3食・各rv/画像hash/解析・日sv・不透明世代・日次rv/共有状態・server policy/制限を返す。実画像確認は別 |
| 準備P | POST /api/internal/mina/meal-replacement-candidates | multipart metadata(JSON)+image。metadataはSchema PrepareInput。201 ready、202 processing、終端拒否422等。正本・公開共有を変更しない |
| 候補GET | GET /api/internal/mina/meal-replacement-candidates/{P} | live候補状態、固定候補版と解析、準備時期待値、画像hash/byte_length/MIME/固定認証経路、期限、制御revision |
| 候補実画像 | GET /api/internal/mina/meal-replacement-candidates/{P}/images/original または stored | 原本実バイト/変換後WebP。Candidate-Versionヘッダに期待候補版必須。200だけが画像、他はSchema Error。認証・候補版・完全性を検査、範囲応答/変換/CDNを使わず実バイトを返す |
| 不採用D | POST /api/internal/mina/meal-replacement-candidates/{P}/discard | 別UUID D、候補版・解析digest・expected_control_revision・不合格理由と証跡digest。readyをdiscardedへCAS |
| P/D要求照会 | GET /api/internal/mina/meal-replacement-requests/{uuid}?operation=candidate_prepare または candidate_discard | 元要求のoutcome（applied/rejected/processing/unknown）と現在候補を区別する。P/DにM1 persistenceを付けない |
| 確定C | POST /api/internal/mina/meal-replacements | 別UUID C、candidate/版/digest/制御revision、Pと同じ期待値、公開pause証跡・レビュー・Drive・実栄養判定のbundle |
| C照会 | GET /api/internal/mina/requests/{C}?operation=meal_replace | 新operationだけに新契約envelopeを使用。M1と同じstatus/persistence/stage・保存時result/現在current_result/result_status・日次結果を返す。旧operationの応答は変更しない |
入力UUIDとIdempotency-Keyは一致必須。P/Dは独立候補要求台帳で扱い、status=succeededかつpersistence=not_savedという既存制約違反の行を作らない。outcome=appliedはその候補操作が適用された意味だけで、食事保存を意味しない。
同UUID同入力/異入力再送は409 duplicate_request_same_input/different_input。元UUID/operation/outcomeまたはpersistence/stageを返し、新しい外部処理・課金・PUT・工程再開はしない。404/読取失敗/応答喪失は未処理・未保存の証明にしない。
Schemaは正常・拒否・不明のenvelopeを固定する。P/Dの登録済み拒否・重複はCandidateResponse/ControlLookupResponseで元outcomeを保持し、Cの登録済み拒否・重複・保存後失敗はCommitResponseで元persistenceと結果/現在結果を保持する。Errorは入力前拒否・読取不明等に使い、保存済みをrejectedというread_outcomeへ押し込まない。固定reason表とHTTP対応はEVIDENCE.mdに記載。HTTP単独で保存結果を上書きしない。complete=falseまたはfreshness期限外の応答を前提確認へ使わない。

## 3. candidate版・解析digest・実バイトの定義
正規化は既存canonicalJsonと同じキー順（UTF-16単位）、配列順保持、有限JSON数のみ、-0は0、UTF-8・空白なし。NFC/trimはname/amount/commentの入力正規化時だけ行い、digest算出時に再編集しない。食品順は解析の順序を維持する。表示用nameJa等の重複情報は規定投影に含めない。
analysis_payload = {spec:"mina-candidate-analysis-v1", analysis_source, analyzer_spec, quality_spec, foods:[{name,amount,calories,protein,fat,carbs}], summary:{totalCalories,totalProtein,totalFat,totalCarbs}, is_ambiguous, quality:{status,atwater_calories,calorie_difference,calorie_difference_ratio,warnings:[{code,message}]}}。
analysis_digest = ad1_ + SHA256(UTF8(canonicalJson(analysis_payload)))。解析値は再丸め・4/9/4強制換算をしない。summaryは同じfoodsの実数和と一致させる。品質計算は既存reviewMinaNutritionを使い、quality_specを固定する。
candidate_payload = {spec:"mina-meal-replacement-candidate-v1", prepare_uuid, target_date, target_slot, prepared_preconditions, prepared_publication_hold, original_sha256, stored_sha256, original_mime, stored_mime, original_byte_length, stored_byte_length, analysis_digest, normalized_comment, policy_version, hard_deadline_at, candidate_expires_at, attempt_ordinal}。
candidate_version = cv1_ + SHA256(canonicalJson(candidate_payload))。候補内容はready後不変。候補状態の更新は別のcontrol_revision（単調増分）で比較する。消費/放棄によって内容版を再発行しない。
画像GETは完全な原本と保存画像を別々に返し、Content-Type/Content-Length、Content-Digest（sha-256のbase64）、X-Mina-Candidate-Version、X-Mina-Image-SHA256、X-Mina-Complete:true、X-Mina-Checked-At/Valid-Untilを返す。Agentは受信完了・長さ・実SHA・MIME・候補版を照合し、JSONのhashだけで実画像点検したとしない。バイト改ざん/欠落/候補不一致は200を返さない。
expires_atを過ぎたreadyはGETで自動expireに更新せず、effective_usable=falseとして返す。discarded/consumed等の履歴画像は保持方針に従い認証GETできるが、effective_usable=falseで確定に使えない。旧原本hash不明を推測で補完しない。

## 4. PとDとCの状態遷移・競合
| 現在 | 操作 | 遷移/効果 |
|---|---|---|
| 未予約 | P | UUID/固定account日枠/試行ordinal/費用/期限を原子的に予約しprocessing。高価な解析は予約後ロック外 |
| processing | P処理正常終端 | 画像実体/解析/品質が固定されready。品質拒否等はrejected。副作用不明はreconciliation_required |
| ready | D | 候補版/digest/control_revision一致でdiscarded。D台帳outcome=applied、枠予約を同一commitで解放 |
| ready | C | 三者一致・証跡等を満たす場合だけconsumedと正本切替を同一commit。制御revision増分 |
| ready | 安全な期限処理 | 外部処理終端・C/D未適用・予約所有者一致をlock内で確認しexpired。GETだけで更新しない |
| processing/reconciliation_required | D/C/期限 | 拒否。時間だけで解放せずP/要求UUIDの照会限定 |
| discarded/rejected/expired/consumed | C | 拒否。遅延Cは決して正本を保存しない |
| discarded | 新D | 候補非readyとして拒否。同じDの再送は409と元outcome、GETでは適用済みと確認できる |
Dは候補行・要求D・枠予約を同一txでロック/CASする。Cも同じ候補行をロックする。D先勝ならCはcandidate_not_ready、C先勝ならDはcandidate_not_ready。どちらも成功しない/片方だけ成功を許し、両方appliedは許さない。COMMIT応答喪失時はD自身のeffectがunknownであり、not_appliedと推測せずD UUIDとPとCを照会する。
放棄後も候補内容・D要求・独立点検証跡・費用・累積試行・既存食事と履歴を保存する。Dは回数/時間/費用の返金や旧正本の変更をしない。次のメニューはD適用と枠解放をGETで確認し、残り回数/時間/予算がある場合だけ新Pを準備する。TTL待ちは不要。
画像PUT成功後の処理不明等はreconciliation_requiredに留める。再起動・lease切れで勝手にfailed/rejectedへ変換する復旧処理は今回追加しない。

## 5. 三者一致と確定トランザクション
precondition対象は固定account、JST日、slot、share_url、state_version、target_result_version、account_generation、day_generation、meal_generation。各generationは専用読取が同じsnapshotで発行する不透明token。日次コメント/共有状態・目標/写真設定、一般経路の変更、日付移動の両日を世代更新対象にする。
Cでは候補がP準備時に保存したprepared_preconditionsとC.expectedを全項目一致で比較し、その両者を現在正本から再計算した値と照合する。
**P.expected = C.expected = current**。Cだけ新しい版/世代へ変更したらcandidate_preconditions_mismatch。P/Cは一致しても正本変更ならstate_conflictまたはgeneration_conflict。自動rebase・新候補版への読み替えは禁止。
account→day→meal→candidateの順でM1ロックを統一。一般経路/既存triggerと交差するdeadlock/timeoutは、rollbackを確認できた場合だけnot_savedとし、不明ならunknown。どちらも自動再実行しない。
Cは候補ready/期限・一回消費、固定policy・pause receiptとepoch、証跡対象一致、P準備前後での他2食と実解析の合計を検査する。C栄養条件は旧日sumから旧夕食を引き、候補実sumを足して1800〜2000 inclusive。設定上の目標値へ偽装しない。
成功txは旧行完全snapshot/旧共有とOGP metadataを不変履歴へ退避し、同じmeal_logs.idの新画像/解析/合計/commentと共有識別を更新、候補消費・要求Cのsaved関連付け・保存sv/rvを同時に確定する。analyzed_dataは許可リストで新規構成し旧画像由来metaを混ぜない。既存daily_shares.idは維持し、有効共有の識別だけローテーション、公開開始時刻/期限を延長しない。共有なしは作成せず、停止/期限切れは自動対象外。
保存後はCが対象食事と既存日次共有の新版OGPを準備する。片方でも失敗ならsaved/公開保留。UUID再送で再開しない。AgentはC GETとstate/専用preconditionを読み直し、新rv/sv/現在3食と実合計、OGP実バイトを再検証する。異常なら新候補を自動保存せず、pause維持。元の承認は復活させない。

## 6. 公開排他：Agentの永続状態と本体の境界
本体はAgent SQLiteやBufferの排他を実施・証明できない。Agentはdate単位の永続PublicationHold表（対象account/JST日、epoch、state、owner、P/D/C UUID、旧sv/rv、期限、診断digest）と全チャネル共通の調停を別実装する。本体はP/Cに含むpause_receipt_digest+hold_epochを固定し、Cで同じreceipt/epochを要求する。これは認証済みAgentの表明であり、本体が外部不存在を独立証明したとの表示は禁止。
X・Instagram・Buffer作成/更新/送信・Gateway承認の全入口が同じ日排他を参照する。Store.claim_dueの承認済→dispatchingと日hold作成/CASを**同じSQLite書込トランザクション**へ統合する。別DBの入口は同じ調停サービスを通すまで機能を有効にしない。承認UI/Gatewayもhold中の旧承認を受け付けない。送信の最終直前にもepoch/hold/候補版のfenceを再確認する。
順序：Agentがholdを永続化→送信確保済/送信中/不明がないこと確認→Buffer等の対象account/日全件を全ページ照会し外部受付済み予約がないことを確認→precondition/元UUID/実画像・制限を取得→replan試行予約→P作成。外部照会不可/未完了/対応不明ならblocked_externalのまま停止。ローカル台帳0件を外部予約不存在の証明にしない。既公開/外部受付済/送信中/不明は自動対象外で、取消・再投稿を自動実行しない。
| 境界/クラッシュ位置 | 再開時の照会 | hold/復帰条件 |
|---|---|---|
| hold保存前 | 中央排他とclaim状態 | holdなしでPを作らない。既claimなら自動対象外 |
| hold保存後・P前 | 永続attempt/P送信予定UUID、外部台帳全件、旧M1/state | 未送信が永続証跡で確定し旧版一致ならunusedを閉じて旧承認復帰可。送信有無不明は解除不可 |
| P processing/応答喪失 | P要求GETと候補GET | processing/404/unknownはhold維持、GETのみ。再POSTしない |
| P rejected/D適用 | PとDのGET、C未保存、旧M1/state、外部状態 | 次候補に進むならhold維持。終了時は未解決要求なし・旧版/公開条件/実画像・承認bundle一致を新たに照合して旧承認復帰可 |
| DとC競合/直前 | D/C UUID、P状態、現在state | 勝者未確定はpause維持。D適用+C not_savedなら上記旧版復帰条件。C savedなら新版処理 |
| C送信後・応答喪失/再起動 | 永続C UUID GET、P、state/元UUID | unknown/404/不一致はhold維持。savedを件数や似た画像で推測しない |
| C saved・OGP未完成/失敗 | C/現在state/OGP metadataと実体 | pause維持、旧承認を失効。新承認/旧承認で送信しない |
| C saved・新版点検合格 | 新sv/rv、元/新UUID、個別/日次OGP実体、全チャネル台帳 | pause中に旧承認・Gateway許可・予約候補を永続失効。新bundleを独立再点検/人間再承認し、日hold解除と新queueの可送信化を同じtxで行う |
| 任意位置で既公開/外部受付済/不明発見 | 対象外部台帳/送信状態 | blocked_external。自動解除・自動取消なし。別の人間判断 |
旧版復帰と新版公開は異なる遷移。旧版不変でも署名/期限切れ・鮮度超過・OGP不一致なら再承認が必要。新正本になったら旧bundleを絶対に復活させない。既存公開前の120秒照会鮮度と実バイト照合は継続する。
失効した旧短縮/共有/OGP URLは404/410固定で新版へredirect/fallbackしない。新置換共有も検証済み対応PNG readyまでfallback禁止。旧画像/OGPは履歴参照中GC除外。一般利用者の公開経路を変更しない。外部キャッシュや送信先をDBと同じACID境界に含めない。

## 7. 共通試行・時間・費用の移行
上限は固定account/JST日/slotで最大2回の別メニューreplan。Agentのpreparation_steps meal_replan:{slot}:{n}を正として、既存工程の数・ordinal・時刻・未完了を保護台帳内で照合する。新episode UUIDやP/Cを作り直して回数を戻さない。
Agentは画像生成前に同じSQLite txで次ordinalと費用枠を予約。既存工程nへ一つの新Pだけを関連付ける。本体はP前処理でそのmigration_receiptとattempt_ordinalを永続照合し、(account,date,slot,ordinal)一意制約と累積上限を強制する。初回にprior_consumed_ordinalsを一度だけ取り込み、減算/再初期化不可。既存nが不明・未完了・重複ならmigration_unverifiedで停止。新Pは前のordinalを再使用できない。P未到達で消費した別メニュー試行も次receiptに引継ぎ、サーバーに来た回数だけで判定しない。
同メニュー修復・通常登録/訂正は別メニュー2回の計数には入れないが、実行時間・外部費用と各API枠では無視しない。
| 記録 | 計数/処理 |
|---|---|
| replan ordinal予約 | 1回消費。以後不採用/失敗でも戻さない。画像生成前に開始 |
| API/SDK呼出し費用予約 | call_keyと最大予定課金額を予約。準備済だが未発行と証明できる分だけ解放可能 |
| 実行済・課金確認済 | 実費へ確定。APIが品質拒否でも費用消費は残す |
| 発行/課金成否不明 | 予約最大額を拘束し照会限定。0円・未実行・自動再送へ変換しない |
| 候補不採用D | replan回数・実費・不明費用を維持。次ordinalは残枠内だけ |
| SDK retry/fallback | 外部呼出しごと別call_keyと費用予約。replan ordinalは増やさないが呼出上限/予算は消費 |
日解析60枠は共有し、新候補に別の無制限枠を作らない。未知のSDK自動retry/fallbackを無制限に許可せず、許可回数をpolicyで固定して呼出前予約する。
費用上限の実額・通貨・管理場所・対象（画像生成/解析/独立点検/OGP/Drive等）と既存承認範囲は未確認。新規承認済/0円としない。承認元に対応する固定policy_version/budget_policy_digestが両側で確認できるまでPを拒否budget_unconfiguredとする。P入力で任意金額を指定して承認扱いにしない。
期限はhard_deadline=min(既存の準備期限,最初の共通replan開始+15分)。既存replanがあればその最初の永続時刻を使い、新episodeで延長しない。既存replanが0なら初めてのreplan予約時刻を固定する。既存準備期限が未確認ならdeadline_unverifiedで停止。候補期限=min(ready時刻+15分,hard_deadline)。不採用で次候補に進めるが期限は延長しない。API準備60秒・既存OGP描画20秒・残時間による開始可否も確認し、超過だけで不明要求を解放しない。

## 8. 栄養判定の時点
1) P前：同じpreconditionの他2食の実解析を用い、食品別概算で候補の予算を確認。概算だけを最終判定にしない。
2) P後の独立点検合格後かつC送信前：固定候補analysis_digestの実解析と、P準備時と同一preconditionの他2食を合算。1800〜2000 inclusiveと画像/食品一致・品質を確認したnutrition_checkを証跡bundleへ入れる。朝484/昼633が維持される場合は候補夕食683〜883。
3) C内：P/C/current三者一致後にサーバーが同じ実合計を再計算。nutrition_checkのprecondition_digest/analysis_digest/合計と一致しなければnot_savedで拒否。
4) C後：現在3食・新sv/rv・実合計を読み直す。変化/不足/不明なら公開pauseを維持し、数値補正・自動再置換なし。
schemaにnutrition_checkの計算対象digestと実合計を固定し、候補分析の差替え/他2食更新/別のsvからの値の流用を防ぐ。

## 9. 検証・リリース・承認
今回行うのは計画文書とSchema/合成fixtureの整合検証、agy読取独立レビューだけ。実装・本番DB/画像/外部予約の操作をしない。
承認後はEVIDENCE.mdの隔離試験を実装。test:qa・型検査・M1互換・専用DB障害注入・localhost事前QA、専用log-eats-qa-e2eの実HTTP/DB/R2試験を必須とする。新feature宣言を削除して合格扱いにしない。
正規経路はPR Product QA→main→本番リリースの隔離Preview/Production/postdeploy/必要canary。直接Production CLIは禁止。対象commit/PR/run/Deploymentを対応付け、DB移行は用途別スクリプト/SQL・接続先を確認して別承認。本番3食をQA素材にしない。
計画Goを得た対象版について、ユーザーへ本体変更範囲とAgent別実装、費用未確認/外部Buffer照会未完/準備期限未確認等の停止条件とリリース経路を提示して実装着手の明示承認を求める。計画Go/この依頼は実装承認ではない。本番DDL/DML、18日分の実置換、本番反映、課金予算追加、Agent実装/配置は別承認・別担当とする。

## 10. v2.1の規範追補（v2.0 No-Go対応）
- 日時は保存/応答/digest計算の前に既存toUtcMillisIsoと同じUTCミリ秒ISO（YYYY-MM-DDTHH:mm:ss.sssZ）へ正規化し、同じ文字列を保持する。canonicalJson自体が日時を変換するとは扱わない。レビュー入力もJSONを日時型へ変換して再serializeせず、ファイルの原文を渡す。
- 既存食事の分析投影はanalysis_source=stored_canonical、analyzer_spec=stored-canonical-projection-v1、quality_spec=mina-nutrition-v1。foodsは既存mealFoods、summaryはmealSummaryで読み、元の順序・文字・amount:nullをそのまま保持する。過去の解析器や曖昧さを推測せずis_ambiguous=nullとする。qualityは現行M1と同じreviewMinaNutritionの正本読取評価（歴史的画像品質の合格とは異なる）。この固定投影へad1を計算し、旧sv1/rv1や保存データは変更しない。空foods、明細と合計不一致、不正値は自動置換対象外。新候補はanalysis_source=candidate_analysis、実解析器/品質仕様と実booleanのis_ambiguousを記録する。
- 共通Food.amountはstring|nullであり、既存M1食品へ新規入力の文字数上限を遡及適用しない。新候補の準備側には候補の食品数/入力制約を適用する。新候補のreadyは品質pass・曖昧さfalseを必要とするが、独立画像点検の合格を意味しない。
- DはDiscardInput.reviewに候補版・解析/原本/保存SHA・独立点検主体の役割・実施時刻・fail/declinedと固定観測コードを持つ。review_evidence_digestはこのreview正規JSONのSHA256。request.discard_inputへDの正規入力（reason/証跡を含む）を不変保存し、成功・重複・D照会で返す。候補GETではlast_control_request_uuidからD照会へ対応付ける。Dの同UUID異入力は元discard_inputを保持し、上書きしない。
- C保存後のsource_changed/og_image_preparation_failedはCommitResponseでpersistence=savedを維持する。重複Cも同じenvelope、重複P/Dも候補envelopeで元outcomeとトップ重複reasonを分ける。登録済みの既知拒否も同じ型、応答喪失/読取不明だけErrorのunknownを使う。HTTPは業務保存の成否を上書きしない。

