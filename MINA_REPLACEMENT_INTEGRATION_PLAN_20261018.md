# 保存済み一食置換のAgent統合計画

2026年10月18日。対象契約はmina-replace-v1、提供側計画v2.1、Log-Eats報告コミットbb49b016f79119917c5f0cf520537316fdb098c1。今回は計画と読取り専用の独立レビューまで。Agentの実装・配置・有効化、外部生成・解析・投稿、18日分の操作は行わない。

## 目的と承認範囲

保存済み一食の品質保留に対し、新しいメニュー・画像を候補Pとして準備し、独立点検後にCで置換する。候補不合格ならDで不採用を確定して、残り回数内で次のメニューへ進む。旧正本・旧画像・失敗履歴を保持し、品質検査を省略しない。

今回ユーザーへ求める承認は、Marketing Agentだけの統合実装と、外部書込みを伴わないローカル・隔離Linux・CI検証までとする。本体の新APIは未push・未配置で、専用Preview E2Eも未完了。本体のローカルGoを、本番APIが利用可能との証拠にはしない。

承認対象外は、本体プロジェクトの変更、本番DB移行、Agent本番配置・有効化、実サービスでのP/D/C送信、18日分の救済、課金予算追加、既存投稿の取消・再送。提供側の専用QA環境との実接続、両側リリース、予算と対象日の運用許可は、その条件が具体化した段階で別途説明する。

## 調査基点と受領証跡

Agentの先行実装は管理worktree mina-meal-replacement-preparation、branch codex/mina-meal-replacement-preparation-20261017、読取時head78ca80a869dd3e6c5cb43e10b007e407c70d7f36。主作業フォルダーの未コミット変更を混入させない。実装を承認された時点でhead・差分・本番基点を再確認し、別作業が進んでいれば取り込む前に影響を照合する。

先行実装にpublication_hold、replacement_client、replacement_journalと既存入口の停止検査がある。停止解除、新版承認、Schema変換、P/D/C送信、外部全件照会、共通費用・回数・期限の統合は未実装。先行412件成功は、今回の契約接続や置換全体の合格ではない。

指定コミットから、利用を指示された契約・報告・レビュー文書だけをgit showで読み、Agentのtmp/replacement-agent-plan-20261018/provider-contractへ原文コピーした。本体のcheckout・コード・DBを変更しない。

| 凍結入力 | SHA-256 |
|---|---|
| CONTRACT.schema.json | 8b5fc6ecce9a9b406e7197f60c6ae49cb8f94edd9702c3b2db33babe78396090 |
| FIXTURES.json | 6561ad1dbdeb755e9a6fca55f2981355dee1b879f404f069489e8c927ecf52c7 |
| PLAN.md | 2f3d15a12cf40d262faa9c0c61d1c4a267f5ed0febd56896216645964ebac305 |

提供側MANIFESTの凍結ファイルとコピーの実SHAを検証する。Providerの実装manifestは対象コード集合とローカル検証の証跡として保持し、コード全体をこちらで再レビューしたと主張しない。agyの最終判定は隔離ローカル実装範囲Goであり、専用QA所有runtime・Preview・本番は未完了と読み分ける。

## 変更する部品

| 部品 | 追加・変更内容 |
|---|---|
| replacement_contract | Draft 2020-12の入力・応答検証、operationとUUIDの照合、意味・鮮度検査、正規化とdigest再計算。旧m1_contractを再定義しない |
| replacement_client | 既存GETに確定検証器を接続。Pのmultipart、D/CのJSONを一度だけ送信。HTTPS固定宛先・Bearer・no-store・redirect拒否・応答容量上限 |
| replacement_journal | P/D outcomeとC persistenceを別型で保持。実送信バイト・operation・UUID・hold・候補版・入力hashを固定し、観測結果を追記する |
| replacement_flow | 保存済み専用の状態機械。生成、候補点検、D/C、再照会、旧版復帰、新版点検を通常準備と分けて調停する |
| publication_holdと各入口 | 外部全件確認、所有epochのCAS、旧版への安全復帰、新bundleの承認受領、解除と可送信化の原子的処理 |
| replacement_policyと台帳 | 既存replanの移行、期限、共通費用予約、本体への予算割当、外部呼出上限を固定する |
| 正本・候補の利用側 | Cの新envelopeに対応する承認元の解決。X・Instagram・日次の旧候補失効、新成果物、現在正本への参照を接続 |
| 診断・運用文書 | 作成障害と投稿障害を分け、失敗理由・対象工程・停止条件・最後に確定した成否を表示する |

## 契約と意味の検証

凍結Schemaを固定版として同梱し、ハッシュ不一致なら起動時の機能受入を拒否する。検証器なしでは通信しない。AnyResponseへ一括で通すだけでは足りず、経路ごとの型、UUID、operation、対象日・枠、候補準備UUID、候補版、control_revisionを照合する。

canonicalJsonはUTF-16キー順・ECMAScript数値表現・有限数・-0・UTF-8・配列順を固定fixtureで検証する。日時は規定のUTCミリ秒文字列を保持する。既存食品amount:null、stored_canonicalの曖昧さnullを保持し、過去の品質を推測しない。現在のPython fingerprintをad1/cv1/pc1へ流用しない。新依存の選定は実装時に固定バージョン・ライセンス・両OS再現性を確認し、不一致を許容する代替経路を作らない。

Schemaだけでなく、foodsとsummaryの実和、品質、analysis_digest、candidate_version、precondition_digest、各証跡digest、P.expected=C.expected=currentを再計算・照合する。Cだけのrebaseは禁止。policyとbudgetの変更も不一致として扱う。preconditionのpolicy集約値を対象枠の許可と読み替えない。

候補の原本・保存画像は専用認証GETで取得し、完全性、長さ、MIME、実SHA、Content-Digest、候補版、checked_at/valid_untilを検証する。先行実装のImageDescriptorをSchemaから変換し、サーバーの任意URLを開かない。画像取得成功を独立食品点検の合格にはしない。

HTTPErrorでも本文を制限付きで読み、正しいenvelope内の元成否を優先する。409重複や503 OGP失敗を一律not_savedへ変換しない。P/D appliedは候補操作の適用であって食事保存ではない。savedは後の404や読取障害で降格させず、矛盾は別フラグで停止する。

## 状態遷移

| 状態 | 実施条件・次の処理 | 不明・失敗時 |
|---|---|---|
| eligibility | 対象日・保存済み品質保留・現在正本・既存要求・期限・予算・未公開を確認 | 不足は固定理由で開始しない |
| held | 日holdを送信確保と同じtxで取得し、外部全件照会を完了 | 外部受付・送信中・不明はblocked_external |
| attempt_reserved | 既存ordinalと予算を同じtxで予約し、その一食だけ再計画・画像準備 | 生成失敗でも回数・不明費用は戻さない |
| P_prepared/started | 新UUIDと実バイトを永続化して一度だけ送信 | started以降はGETのみ。未送信と推測しない |
| P_processing/unknown | P要求と候補GETで終端を照合 | 新P/D/Cで迂回せずhold保持 |
| P_ready | 画像実体・解析・版を検証し、独立点検と新旧Drive再取得照合を行う | 真の不合格だけDへ。点検や取得の成否不明はGET限定で保持 |
| D_started | 固定した不採用証跡でDを一度だけ送信 | D/P/C照会で勝者確認。不明中に次Pへ進まない |
| D_applied/P_rejected | 終端・枠解放をGET確認し、残り回数・期限・予算があれば次メニュー | 上限で終了。旧版復帰の条件は別検査 |
| C_prepared/started | 独立点検後、C直前の全件外部照会・正本・他2食実解析・証跡を再照合し一度だけ送信 | 404/応答喪失はC/P/正本GET。再確定しない |
| C_not_saved | UUIDの終端未保存と旧正本を確認 | 古いCを再送しない。ready候補を終了するならD照会も完了させる |
| C_saved | 旧承認失効、hold=saved_pending_review、新正本・実画像・実栄養・OGPを点検 | OGP片方未完成や内容不一致ならsavedのまま公開停止 |
| new_bundle_pending_approval | 新X/Instagram候補だけを通知・承認受付可能にする。公開はまだ不可 | 旧カードや旧bundleの承認は拒否 |
| released_new | 必要な新承認が揃い、直前照合が新鮮なら解除と新版可送信化を同じtxで行う | 一つでも不明なら解除せず保持 |
| released_old | P未送信、または全P/D/C終端とC未保存が確定し、旧版・旧承認が有効な場合だけ復帰 | C savedや矛盾が一度でもあればこの遷移は禁止 |

新しいepisode、サービス再起動、feature flag無効化、時刻経過で停止を解除しない。D/Cが競合した場合はUUID照会・候補状態・正本の三者で判定し、両成功や対象不一致は不明として維持する。

## 公開排他と再承認

XとInstagramのcandidates、Buffer経路・送信、非公開下書き、Gateway承認、候補更新、各復旧CLIが同じSQLiteの日holdを参照する。claim_dueとhold取得は同じBEGIN IMMEDIATEで競合を決着させる。外部呼出し中にDB txを開き続けるのではなく、先にdispatchingまたはmutation leaseを永続確保し、hold取得側がそれを拒否する。送信直前にも所有版・epochを照合する。

外部全件確認はX・Instagramの固定Buffer接続先について、pending/scheduled/draft/processing/failed等、APIが列挙可能な危険状態を全ページ照会する。ページ完了・アカウント・対象日・所有対象の対応を証明できない応答、cursor反復、予算不足、外部操作との競合はblocked_external。列挙範囲が保証できないAPIを推測で補わず、有効化の未達条件とする。既存X/Instagram共通Buffer無料枠を全照会でも消費し、枠計数を迂回しない。ローカル0行で外部不存在を宣言しない。

P時の公開停止証跡とC時の証跡でhold_epoch/pause_receipt_digestは維持する。provider_check_digest/時刻は契約どおりC直前の完全な再照会で更新する。最も早い照会開始から送信まで120秒以内を検査し、生成前の古い照会をCに流用しない。

旧承認復帰は、未送信が永続証跡で確定した要求か、全要求終端・C未保存・候補非使用を確認した場合に限定する。旧sv/rv・世代・URL・画像・実OGP・承認bundle・期限・外部状態を再検査し、送信可能な旧状態だけ復帰させる。時刻経過を埋め合わせる遅延公開は含まない。すでに旧版が品質不合格なら旧候補を公開可能にしない。

C savedを観測したtxで旧X/Instagramの承認nonce・許可・未送信経路を失効し、旧候補と経路の完全snapshotを履歴化する。新bundleは最終日sv、各rv、C/M1各要求との対応、画像SHA、固定本文、policyを束ねる。既存のM1 completed_requestがmodifiedを拒否する性質は維持し、C由来の新承認元は別の型・照合器へ渡す。

hold中でも新bundleに限定した承認カード送信と承認受領は許可する。先行実装の全拒否を単純に解除せず、owner/epoch/bundle/channel/revision一致のホワイトリストにする。旧カードは拒否したまま、X4候補・Instagram4候補と8画像を再生成・独立点検する。チャネル単位の利用選択と通常の人間承認を維持し、必要チャネル・枠の集合をbundleへ固定する。無効チャネルの未承認で永久停止させず、有効チャネルを勝手に除外もしない。必要集合の承認完了後にだけ、日hold解除・新候補可送信化を同一txで行う。

既存の日次共有がある場合はCのaffected_daily_result/current_daily_resultと現在正本を照合する。ない場合、Cが作ったと仮定しない。通常の未期限前準備でのみ、既存のdaily_share工程を送信前台帳・期待sv付きで一度実施し、独立OGP点検と最終日svへの全候補の再結束を行う。成否不明ならGET限定。期限後の日次新規作成をこの実装で認可しない。

## 台帳移行と成果物

既存M1のmina_requestsとih1/sv1/rv1はそのまま保持する。新operationのために旧完成応答を偽装しない。先行ReplacementJournalを拡張し、P/D outcomeとC persistenceの型・互換観測を分離する。全要求のUUID索引で旧M1とP/D/Cの再使用を防ぐ。

追加する論理台帳は、対象日・枠のattempt、operation別要求と追記観測、費用予約と割当、外部確認、旧新bundle履歴・承認、hold解除の証拠。既存publication_holds等は版付き移行する。旧行の削除・過去失敗のcomplete化・未完了のpreparedへの戻しはしない。

meal_replan:{slot}:{n}の件数だけでなくordinal、対象、最初のstarted、未完了、重複、実入力を照合する。画像生成前にordinalを消費し、Pまで到達しなかった試行も移行receiptに残す。最大2回を日・枠で共有し、同メニュー画像修復やM1訂正は別メニュー回数に加えず費用・時間には含める。

既存成果物を上書きせず、対象日/置換UUID/bundle版ごとに新計画・画像・Drive・解析・OGP・候補を固定する。旧Drive参照と新参照は保護台帳で保持し、双方を再ダウンロードしてSHAを照合する。日全体の採用pointerは、新正本・新bundleの照合後にCASで切り替える。ファイル保存とSQLiteの間でクラッシュした場合は固定manifestから採用状況を照会し、時刻が最新という理由で採用しない。

SQLite移行は本番のコピーでdry-runし、整合性と既存全業務テーブルの全行hash不変を確認する。実装承認で本番DBを移行しない。実行SQL、バックアップ、schema版、影響とコード切替手順を提示し、本番配置時に別承認を得る。新要求や承認があるDBを古いDBバックアップへ巻き戻さない。

## 共通予算と期限

予算policyは承認元digest・通貨・対象サービス・上限・実施済み/不明費用・最大拘束額・モデル/SDK上限・本体への割当を固定する。金額の仮値は置かない。Codex CLIの既存認証利用と追加API課金を区別し、従量APIへ自動退避しない。無料で利用できる条件も根拠を確認してからpolicyへ結ぶ。

本体APIへの未使用予算割当はAgent共通予算から先に拘束し、本体費用はその割当内の日内全枠で計数する。Agent側費用＋本体割当の二重支出を防ぐ。本体内のcompleteを請求額確定や返金と読み替えず、実費未確定なら最大額を拘束したままにする。外部呼出しごとのcall_key、SDK retry/fallbackを予約前に固定し、未発行が証明できる予約だけ解放する。解析60枠、Buffer枠、金額は別の制限として全て満たす。

hard_deadline=min(対象日前日20:00 JSTという既存準備期限,初回共通replan開始+15分)。残時間からP最大60秒、独立点検、Drive、CとOGP、公開前照会の時間を見積もり、開始できる工程だけ実行する。経過してもprocessing/unknownを未保存へ変換しない。期限後は新しい課金・P/D/Cを開始せず、既存UUIDのGET照合と診断だけを継続する。

この期限は置換の自動工程に適用する。期限前に完成・点検済みの新bundleへの通常の人間承認と、その後の本来の公開時刻での送信は別の既存期限で扱う。承認待ちを理由に置換期限を延長せず、期限後に未完成の新bundleの有料生成や追加解析を開始しない。

18日分の通常期限は17日20:00 JSTで経過している。この計画・実装承認では延長しない。別の対象限定復旧方針が必要であり、仮にAPIが公開されても18日分へ自動実行しない。

## 栄養と独立点検

P前は同じpreconditionの他2食の実解析から残予算を求め、食品別概算で新メニューを選ぶ。朝484・昼633が維持される場合は夕食683〜883kcal。候補ready後、実画像と候補解析を独立点検し、C前に同じ他2食のad1と候補ad1の実合計が1800〜2000 inclusiveを満たすことを証跡化する。nutrition_check自身を除いたdigest対象等は凍結仕様に従う。

不合格は事実に基づくD証跡へ結び、次メニューへ進む。画像識別のuncertainをpassへ変換しない。C後は現在3食・sv/rv・世代・個別/日次OGP実バイトを再検査し、値不足・変更・不明ならholdを維持する。既存1727kcalを補正して条件を通さない。

## 復旧と停止理由

started/processing/404/通信失敗は照会専用とする。GETで終端と対象の一致を確認できれば、同じ状態機械の次の未送信工程へ進める。不明な工程を再送しない。GETは上限付きのバックオフとread budget内で自動照会し、期限後に業務操作を再開しない。照会できない状態のまま無制限に外部費用を使わない。

診断にはoperation、stage、固定reason、ordinal、経過、費用分類、証跡digest、期待版一致、最後の確定成否、次に照会する操作種別を残す。実UUID・画像・食品原文・Drive URL・認証は保護台帳に限定する。作成保留をBuffer投稿失敗として通知せず、プロセスの生存と業務保留も分ける。

## 検証と完了条件

| 段階 | 内容と合格条件 |
|---|---|
| 契約受入 | MANIFEST実SHA、Schema自体、提供51ケース、pc1/ad1/cv1と各証跡、画像2ベクトル。合成画像を食品品質の合格としない |
| 実SQLite | holdとX/Instagram claim・Gateway・下書きlease・単体Buffer書込の全順序、再起動、別日非影響。停止中の外部mutation0件 |
| 状態機械 | ready→独立不合格→D適用→次P→C保存を期限内に通す。D/C競合、全応答喪失、保存済OGP失敗、404、C後変化を注入し再送0件 |
| 移行と予算 | 既存ordinal0/1/2、不明/重複/未完了、画像生成前失敗、既費用不明、本体割当、日内複数枠、再起動・UUID再作成による回数/期限延長不可 |
| 画像と版 | 実バイト改ざん/途中切断/候補版違い、旧画像/Drive再取得、P/C/currentのABA・policy変更、UTF-16・小数・null・ミリ秒日時 |
| 承認と成果物 | 旧カード拒否、新bundleだけhold中に承認可能、必要承認集合・最終承認と解除の競合、未送信routeの版切替、旧URL失効、日次あり/なし |
| 栄養境界 | 候補683/883可、682/884不可、概算pass/実解析不足、他2食変化、C後変化。不正な値補正0件 |
| Agent回帰 | Windows/LinuxのMina全体、既存M1/X/Instagram/Buffer経路、CIと配布固定版、機能無効時の通常動作、新hold存在時の保護継続 |
| 実装独立レビュー | 固定コード版と実試験結果をagyへ渡し、指摘対応後にGoを得る。計画レビューで代替しない |

今回の承認後の試験は合成HTTP・一時SQLite・秘密なし隔離Linuxを使い、本体API・Drive・Bufferへの実書込みは行わない。本体の31項目PG試験やClerk試験は提供側報告として扱い、Agentの成功件数へ加算しない。

両側の専用Preview実HTTP/DB/R2 E2Eは、本体のrun/head所有runtimeが接続され、対象commit・資格・fixture・cleanupが揃ってから別の接続受入として実施する。本番や普通PreviewをQA代用にしない。両側リリース・本番readiness確認が完了するまで有効化しない。

## 実装と本番反映の順序

1. この計画のagy独立レビュー、指摘対応、計画Go、ユーザーのAgent統合実装・隔離検証承認。
2. 先行worktreeの基点を再確認し、契約受入→policy/台帳→P/D/C状態機械→新旧承認切替の順に実装・回帰。機能は既定無効。
3. 固定コードの独立レビューとCI・両OS検証。未解決の重大指摘なしを確認する。
4. 提供側の専用QA準備・本体リリース計画と調整し、接続受入対象・費用・期限・公開排他を具体化する。担当間連絡はユーザーの明示許可なしに送らない。
5. 検証結果と配布物、SQL、対象日、費用policy、機能無効での配置／有効化の区別、監視・コード切戻し手順を提示し、別の明示承認を得る。本番DDL/DMLや18日分操作を含めて推定しない。

## 現在の未解決条件

本体の新APIは未リリース。専用QA所有runtimeとPreview E2E、Agent統合、実額・既消費・本体割当、外部Buffer全ページ照会の実証が未完了。18日分は期限外で救済操作は承認対象外。通常の人間による投稿承認は維持し、成否不明や既公開・外部受付済みを含む全状態で無停止を保証する計画ではない。

この文書への計画Goは、条件が未解決でも課金や本番操作を開始してよいという意味ではない。
