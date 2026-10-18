# v2.1 指摘対応・契約照合・検証証跡
この文書とPLAN.md/CONTRACT.schema.json/FIXTURES.jsonをagyの同一レビュー対象とする。レビュー時点のSHAはREVIEW_INPUT_MANIFEST.jsonに固定し、最終配布物のSHAはMANIFEST.jsonに固定する。レビュー原文はAGY_REVIEW.md。初版のGoはこの修正版のGoを代替しない。

## 指摘対応と担当
| 入力指摘 | 採否・根拠・対応 | 担当 |
|---|---|---|
| ready候補を不採用にできない | 採用。Dと照会を追加、ready+候補版/digest/control revisionのCAS。D/Cは候補行で競合、片方だけ適用。回数・費用を戻さず枠だけ解放 | 本体API、Agent状態機械 |
| 候補画像の認証経路がない | 採用。原本/保存画像専用GET、実バイト・長さ・SHA・候補版・完全性を固定。現行meal-imagesは流用しない | 本体、Agent照合 |
| 応答/正規化/fixtureが曖昧 | 採用。Schema、51合成ケース、正規JSONとSHAベクトル、画像バイト例を固定。実API成功の証拠とはしない | 本体契約、両担当照合 |
| P準備期待値の再確認不足 | 採用。P.expected=C.expected=current。Cだけのrebase拒否。状態版だけでなく全世代・対象・元共有を一致 | 本体C tx |
| 日公開排他/Buffer受付済の扱い不足 | 採用。日holdとclaim_dueを同じSQLite txへ統合、全入口調停、外部受付済/送信中/既公開/不明は自動対象外。別DB入口が未統合なら有効化不可 | Agent別実装。本体はpause証跡を固定 |
| クラッシュ時の停止/復帰不足 | 採用。PLAN状態表。未送信が確定した旧版復帰と、新版後の失効・再承認を分離。404で復帰不可 | Agent別実装 |
| 回数/費用/期限の未確認 | 採用。既存meal_replan工程から固定ordinalを移行、減算/再初期化禁止。費用不明は拘束、金額未確認は開始不可。期限min規則、準備期限未確認で停止 | 両担当。金額承認はユーザー |
| 実解析の栄養判定時点 | 採用。独立点検後C前とC tx内で同じ他2食+候補実合計を確認。C後も新正本確認 | 両担当 |
| 20:15要約不足の補完 | 採用。担当者読取要約として確認範囲を限定。Buffer全件/保存画像実体/Drive再取得/費用/準備期限は未確認のまま | Agent事実、本体前提記録 |
| Goの対象版/実行証跡が見えない | 採用。レビュー入力manifest、対象版、実行結果原文、対応表と配布manifestを添付 | 本体 |
不採用指摘はなし。受け入れた対策でも、Agent/外部状態を本体が自ら検証できると主張しない。

## Schema参照先
JSON Schema Draft 2020-12。全オブジェクトはadditionalProperties:false。各入力のUUIDはIdempotency-Keyと一致。multipart PはmetadataをPrepareInputに検証し、imageは固定MIME/4MiB/実SHAで別検証。
| 経路 | 入力 | 正常 | 拒否/不明 |
|---|---|---|---|
| precondition | PreconditionQuery | PreconditionResponse | Error/Hidden |
| P | PrepareInput+image | CandidateResponse (201/202) | 登録済拒否/重複はCandidateResponse、その他Error/Hidden |
| 候補GET | CandidateLookup | CandidateResponse | Error/Hidden |
| 原本/保存GET | CandidateLookup+Candidate-Versionヘッダ+role enum | BinaryHeaders+実バイト | Error/Hidden |
| D | DiscardInput | CandidateResponse | 登録済拒否/重複はCandidateResponse、その他Error/Hidden |
| P/D照会 | ControlLookup | ControlLookupResponse | Error/Hidden |
| C | CommitInput | CommitResponse | 登録済拒否/重複/保存後失敗はCommitResponse、その他Error/Hidden |
| C照会 | CommitLookup | CommitResponse | Error/Hidden |
HTTP200の要求照会は元要求がfailed/rejected/unknownでも現在の記録を返す意味であり、成功や公開可能と等価ではない。GETのfreshness.completeはその読取の完全性だけ。candidate.effective_usableは期限/状態による候補使用可否。画像実体の整合性を通常JSON GETが再検証する保証はない。
candidate_versionとanalysis_digestの再計算は条件であり、JSON Schema単独ではSHA/算術/時刻順/三者一致を検証できない。独立した意味検査とサーバーtxで強制する。数値NaN/Infinityは禁止し、完全性を満たさないデータを0/nullの正常応答に補正しない。

## 固定理由コードとHTTP
| HTTP | reason（初回拒否/不明） |
|---|---|
| 400 | input_invalid, operation_invalid, image_missing, image_hash_mismatch, candidate_version_required, precondition_invalid |
| 404隠蔽 | 認証不備/別対象はHiddenのみ |
| 404照会 | candidate_not_found, request_not_found：Error.read_outcome=unknown。要求未実行/未保存を意味しない |
| 409 | candidate_version_mismatch, candidate_preconditions_mismatch, state_conflict, generation_conflict, target_not_found, share_inactive, candidate_busy, candidate_not_ready, candidate_expired, candidate_already_consumed, publication_epoch_mismatch, attempt_already_used, duplicate_request_same_input, duplicate_request_different_input |
| 413 / 415 | image_size_invalid / image_type_invalid |
| 422 | day_composition_invalid, state_record_invalid, response_too_large, quality_review_required, candidate_integrity_invalid, independent_review_required, evidence_mismatch, nutrition_budget_failed |
| 429 | replan_limit_exceeded, budget_exceeded, quota_exceeded |
| 503既知拒否 | publication_pause_required, external_dispatch_unverified, migration_unverified, deadline_unverified, deadline_exceeded, budget_unconfigured, service_unavailable |
| 503処理失敗 | analysis_failed, image_store_failed, image_integrity_failed, transaction_aborted, og_image_preparation_failed。記録済outcome/persistenceを必ず優先し、判明しなければunknown |
| 503読取不明 | read_failed：unknown |
| 500 | commit_outcome_unknown, discard_outcome_unknown, prepare_outcome_unknown：unknown |
| 409保存後 | source_changed：C.savedのまま。元結果と現在正本を区別 |
同UUID再送の409トップreasonは重複理由、request.reasonは元要求理由。元操作のoutcome/persistence/stageを維持し、重複をnot_savedへ変換しない。
候補P/DとCはUUID型を共有するが台帳の意味を分ける。共通UUID予約索引に操作種別/所有者を固定し、旧M1台帳も衝突検査に含める。別表だから同じUUIDを別操作へ再使用できる設計は禁止。

## 意味検査の追加規範
- precondition_digest=pc1_+SHA256(canonicalJson(Expected))。CはPと同じ値を使う。precondition GETの各食でExpectedを同じsnapshotから組み立てる。freshness.valid_untilはchecked_at+120秒以内、ただしAgentの送信前120秒は既存契約どおり最も早い要求開始から測る。
- candidate_payloadにprepared_publication_holdも含める。C.publication_holdはPのhold_epoch/pause_receipt_digestと一致させる。provider_check_digest/provider_checked_atは同じholdのままC直前の完全な外部再照会で更新できる。P時の外部確認も候補証跡に残す。hold自体のepoch/receiptが変われば新Pが必要。外部確認を含む照会一式は最も早い要求開始から送信まで120秒以内。
- C.reviewとC.drive_checkの正規JSON SHA256はそれぞれreview_evidence_digest/drive_evidence_digestへ一致。reviewは候補版・解析・原本/保存SHA・独立主体の役割/方法・時刻/pass。drive_checkは候補版・新旧原本と再取得SHA/時刻/passを固定。サーバーは値と対象の一致を検査するが、実施主体・外部Driveの事実は認証済Agentの証跡を信頼する境界である。
- nutrition_check.evidence_digestは自分自身のevidence_digestを除いたnutrition_checkの正規JSON SHA256。他2食digestはbreakfast/lunch/dinnerの固定順で対象slotを除いた順。Cは同じpreconditionを再読取し、その2食の実解析digest/実合計を検証する。
- policy_versionは候補/要求/現在の有効policyで一致。予算設定変更時に古い候補を自動grandfatherしない。旧原本hashや既存準備期限/費用承認/試行履歴が未確認なら自動確定/準備を開始しない。
- 候補画像GETの期限はmin(checked_at+120秒,候補期限,hard deadline)。期限後の履歴画像GETは読取専用のままeffective_usable=false、レビュー証跡として新しい確定に使用不可。期限超過した通常操作は開始しない。
- P入力hashは新operationと正規化metadata（request_uuid除外）/受信実image SHAを含める。D/Cもoperationと正規化入力（request_uuid除外）を含む。旧M1のhash投影は変更しない。
- 失効URLを含む履歴は公開ルートから引かず、権限付きの監査読取だけとする。旧OGPや保存画像の一般公開済み実体・外部キャッシュを消せる保証はしない。

## 隔離検証計画
以下は承認後の実装試験であり、この文書のfixture検証で成功済みとは扱わない。
1. 契約：全endpointの正常/拒否/不明、認証/別対象/operation、Pの三者一致とCだけrebase、各generationのABA、Schema/非有限値/Unicode・食品順・実数digest。
2. 候補ライフサイクル：ready→D→次PをTTL前に実行、D/C競合の両順序、遅延C、D再送/応答喪失、processing/unknown解放禁止、期限境界/再起動、試行消費保持。
3. バイト：原本/保存画像専用認証、候補版違い、長さ/MIME/hash改ざん、PUT/GET失敗、画像GETとD/C競合、実体変更後の200禁止、任意URL/一般meal-images流用拒否。
4. 正本：成功時3食維持、他2食/17日分不変、旧全データ/画像/OGP保持・GC、image meta完全置換、世代trigger/一般編集/設定/日次コメント/日付移動/停止の競合、rollback/COMMIT喪失の両実態。
5. UUID：P/D/C同入力/異入力・operation間/旧M1との衝突、照会404/DB障害、saved降格禁止、旧UUID modifiedと旧保存result、OGP失敗後重複で再開しない。
6. 公開：Agent側のhold作成とStore.claim_due同時実行、全X/Instagram/Buffer/Gateway入口、外部受付済予約/全ページ未完了/送信中/不明、全クラッシュ位置、旧承認の復帰可否と新版再承認、別日非影響。外部送信はstubで試験する。
7. 回数/費用/時間：既存meal_replan工程0/1/2/不明/重複の移行、P前失敗・D・SDK retry・課金不明・予算未設定、同UUID再送0追加課金、新episodeでも期限延長なし、2回と15分/既存期限境界。
8. 栄養：朝484/昼633固定、候補683/883ちょうど可、682/884拒否、概算passでも実解析不足拒否、独立点検不合格、他2食が変わったC拒否、C後変更なら公開保留。
9. 共有：個別/日次OGP片方のみready、旧URL/OGP fallback禁止、新ready前保留、一般ユーザー経路不変、停止/期限切れ拒否、日次なしは未作成、外部旧キャッシュの範囲説明。
10. QA基盤：test:qa/型検査/M1既存/専用PG（許可flag、専用URL、本番不一致、専用DB後始末）、localhost事前QA、専用Preview HTTP/DB/QA R2/所有権/cleanup・対象commit来歴。管理API読取失敗や来歴不足は停止し、通常Preview/本番・古い固定branch証跡で代替しない。

## 現時点の停止条件
外部Buffer全件不存在の未確認、費用実額/管理場所/承認範囲未確認、既存準備期限未確認、世代/候補API未提供、Agent公開排他/新operation台帳未実装、夕食独立点検保留と1727不足。これらをGoにより解決済みとはしない。
本体計画Goはインターフェースを実装する計画の妥当性だけ。両側の実装/隔離試験/接続条件の合格、費用/期限の承認確認、日付対象の新鮮な照合が揃うまで自動実行しない。

## v2.0独立No-Goへの対応
| agy重大指摘 | v2.1の対応 |
|---|---|
| cv1本文と証跡のhold定義不一致/日時不一致 | PLAN payloadへholdを追加、Schema TimeにUTCミリ秒pattern、fixtureは同じ文字列のまま照合。前回packet組立のPowerShell JSON日時変換が実ファイルと異なる時刻表現を作ったため、今回は原文を結合し、manifest自体も同じpacketへ含める |
| 保存後失敗/重複とError型の不整合 | 登録済CはCommitResponse、登録済P/Dは候補envelope。元saved/appliedを維持し、トップreasonだけ重複/保存後失敗を示す。51ケースへsource_changed/OGP失敗/同異入力重複等を追加 |
| 既存M1のamount:nullと他2食ad1投影が未定義 | amount:nullを保持、stored_canonical投影/spec/曖昧さnullを明示。既存正本データ/旧sv1/rv1を変更せずad1の読取投影を固定。fixtureで既存朝食amount:nullを検査 |
| Dの理由/証跡GET欠落と入力manifest不在 | DiscardInput.reviewを定義、ControlRequest.discard_inputでD全正規入力を不変保存/照会。同じ対象SHA一覧をreview packetへ原文のまま添付 |
全件採用。レビュー入力の日時変換は本文内容の問題とは別に、レビュー対象の同一性を損なう組立処理の不備として修正した。旧レビュー原文と旧入力manifestを保存する。v2.1のGoは再レビュー完了後にだけ記録する。

