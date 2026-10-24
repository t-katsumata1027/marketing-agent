# 10/24限定救済：Agent統合実装・隔離受入

状態：本人承認の実装・隔離検証・独立レビュー・PR作成範囲。本番未配置・救済未有効化・実課金／登録／置換／投稿0。Log-Eats本体は読取参照だけ。

## 基点と正式契約

Agentは稼働f2119d4／受入6c43282系、配置文書ea77976を基点とし、UTM・手動X履歴・Instagram後続処理を保持した。PRは稼働基点を含む既存PR14のbranch codex/mina-replacement-utm-productionへの差分として作成する。mainとの大量既存差分を本変更と混同しない。

本体固定48e5459de86130ea622a098bf1c316bb11836112、Draft PR151。mina-rescue-v1 Schema SHA 1532cae8d201be23fbbee8ee1a8bd11d3740a527cf5a0e188734ff65b5c36082、SQL SHA 12dfe9d834689b28730a506bb8b45caabac3706d482c37e8b1fd04f78a318b1a。通常Schema SHA 4f8d2be8f47a741ff8578a25c29bd6422cf88ebd817f8450339eaee76216f6e2は保持。

## 実装した接続

- rescue_contract/client：専用Schemaを同梱・SHA固定し、if/then/elseを評価する。case/P/画像/解析/digest/期限を検証。専用URLだけへ接続し、BearerのOrigin/Cookie付与やPOST再送はない。Agentに権限発行・取消口は持たせない。
- rescue_collection/registration：全日holdを保つL許可を本人artifact・10/24昼夕・絶対終了・画像・所有epoch・コード版へ束縛。通常RequestLedgerのclaimと同じSQLite transactionで各1登録・必要各1訂正を限定許可する。朝食の登録／訂正、別日、別食品枠への偽装、訂正shareの差替え、旧sv1流用を拒否する。朝食保留は維持して昼夕だけ点検する。
- rescue_origin/storage：旧工程・要求実バイト・画像実体・再計画ordinal/receipt・生成監査・費用を列挙して照合。孤児生成／監査や欠損を0回・初回nullへ戻さない。発行承認前に全集合を再取得し、追加要求・費用・監査も検出する。
- rescue_flow：L→3食正本→未承認grant案→実本人承認artifactのdigest束縛→運用者CLIへのbundle→同case GET→候補1回P→実画像独立点検→D終端またはC。発行時から15分、累積2回／日60解析と共通waived・停止制御を維持。P残60秒を実送信直前にも検査する。
- 未承認draftの再準備は同case/Pを維持し、最新証跡へ差し替える。本人artifactの固定後／発行後は不変。権限の再発行・消費返却・候補2回目はない。
- 準備・要求・費用・停止段階と固定理由、サーバー拒否原本、次に可能な照会を追記する。unknownはGETだけ。savedはOGP障害でも保持し、終端矛盾はsaved事実を保持して全新規操作を止める。旧承認失効はsaved初回だけで、再照会では新承認を失効させない。
- 他2食の点検が古くなった場合はrefresh_other_mealsで同じ正本・画像を一度だけ再点検する。登録・画像生成なし、費用とGET/unknownを記録し、キャッシュ再利用で時刻を更新しない。再度古くなれば停止する。
- C後は固定した新朝食menuを採用し、昼夕・画像の不変、実合計1800〜2000kcal、日次と全画像／解析／OGPを再点検して承認前原本を保存する。候補通知や公開はこの入口では行わない。

## OFFと再起動

新L/P/C/P系resumeは既定OFFで拒否。既存jobのrecovery構築は生成器を持たず、GET、既知readyの安全D、既知savedで未発行のOGP stageだけの明示resumeを残す。期限／取消後Dは候補の準備時権限版を使い、現在版へ偽装しない。

新しいAI点検と日次M1を行うfinish全体はOFFで実行しない。saved OGP仕上げ期限もmin(saved+15分,元hard)であり、hard後へ延長しない。再起動でstarted要求／生成費用unknownを未送信へ戻さない。

## 永続化と配置前に必要な確認

追加SQLite台帳はrescue_collection_permits、rescue_collection_claims、rescue_jobs、rescue_requests、rescue_events、rescue_calls、rescue_uuid_registryの7表。通常要求／費用／公開hold台帳を共用する。既存行を移行で更新する処理はない。saved確定時だけ既存の承認失効・履歴保存を行う。

本番DBの読取コピーで既存27表全行hash不変、追加7表、再構築不変、integrity okを確認し、コピーを削除済み。本番書込みは0。新要求を作った後の運用切戻しは別承認の本番手順で照合する。新要求があれば旧コードへ戻して照会口を失わせず、新規操作OFF＋照会を残すrollforwardを原則とする。DB全体を巻き戻さない。

rescue_runtime.build_flowは専用のMINA_SAME_DAY_RESCUE_CONFIGと、両側固定版受入／独立レビュー／本番反映承認／当日実行承認artifactを実読取する。role・日・scope・両commit・Schema・判定とSHAを照合する。既存通常ONを救済ONへ転記しない。今回これらの本番artifactや設定は作成していない。

## 隔離検証の境界

実SQLite・合成AI／Drive／外部予約でL送信排他、画像・枠・訂正先、原本・監査、UUID、回数・期限、saved/矛盾保持を確認する。実HTTPは固定Git48e5459をgit archiveで専用snapshotへ展開し、本体の実救済ハンドラーへlocalhostだけ接続。トークンは合成、外部networkは禁止、AI/DB/R2/OGP描画は合成。実PostgreSQLの置換制約・CLI受入は本体側の担当範囲で、Agent試験の代替と混同しない。

実HTTPでP/実画像GET/独立点検/C、D終端、P/C応答喪失後の元UUID GET、取消、共通cooldown、saved OGP障害、OFF・再構築後の未発行OGP resume完了、旧P再照会でも完成済み保持を確認する。実render失敗の既発行工程はunknownとして再発行しない。

最終証拠はinvestigations/rescue-integration-20261024へ記録。Windows618件（51skip、実行567成功）、独立72件と配布補修7件成功、CI40048808303両job成功（mina同618件、一般32件）、配布tar正式SHA一致。実行コードは2bf89bc、改行補修はb185509。途中で見つかった不足要求項目、要求hash規則の違い、保存再照会の失効、終端矛盾、版退行等を補修した。先行試験成功や設計Goを実装最終Goとして流用しない。

## 次の承認

1. 両側固定版・CI/Preview・最終独立Goと、私用DB移行検証を揃えて、本体DDL3表／関連制約とAgent追加7表・設定／配置の具体的範囲を提示する。本体操作は本体担当が行う。今回は配置しない。
2. Lの本人承認絶対終了時刻と、Rのnot_afterをJSTの年月日時分で指定し、当日の登録／必要訂正／独立点検、運用者によるgrant1回、新朝食候補1回・P/D/C・全3食／日次点検を別に承認する。既存waivedは再承認や追加予算を求めず、無料とも扱わない。
3. 外部予約は全状態・全ページを実行時に再取得する。日不明Instagram draftの対象を推測せず、必要なら別担当と所有照合・個別操作承認。3食が揃う前の部分公開はしない。
4. 全3食が合格した後、新版候補と遅延公開時刻について別の投稿承認を得る。

本体専用Previewは担当作業中。稼働版・今日の保留状態は今回更新しておらず、復旧済みとは報告しない。本人の実行承認時刻と外部予約は未確定なので仮値で実行しない。
