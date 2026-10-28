# 10/28投稿案の停止原因・復旧と恒久対応

## 調査で確認した事実

10/27 19:00の通常準備は19:09:49に停止した。稼働はfde12d64。
朝食455kcal・昼食628.4kcalはM1登録・一度の訂正とも保存済み。夕食は未登録、候補は未完成。
昼食訂正後の独立点検は正常に応答し、`image_analysis_mismatch`を返した。
詳細は`uncertain / center / food_index=3 / expected=null / observed=null`。
対象は計画上のまいたけ50g。実画像の独立確認でも種類を確定できず、保留は維持する。
CLIの異常終了や投稿要求の失敗ではない。写真だけを根拠に当該食品を削除し、栄養値を下げる案は採用しない。

同じ処理内で保存済み置換へ移った際、Bufferの全状態・全ページGETが日付不明のInstagram下書き4件を検出した。
既存朝食511kcalの類似下書き3件と日次1972kcalの下書き1件で、個別GETも全件draft・日時未設定・未公開。
2件は既存draft台帳verified、2件は対応行なし。推測で除外したり、無承認で削除したりしない。

置換の初回停止は`external_dispatch_present`だった。ところがUUID発行前のcheckingを既存UUID照会専用のreconcileへ渡し、
`replacement_no_request_to_reconcile`を繰り返し、上位の監査・通知にはvalidation_failed/unconfirmedだけが表示された。
さらに朝昼2食の時点では、3食各1件を必要とする正式preconditionが成立しない。
食品品質保留、外部下書きによる開始停止、UUID前再開の欠落、部分日の依存循環を分けて対応する。

## 実装内容

- checking専用再開。同じhold owner/epoch、期限、旧予約スナップショットを維持する。
- 所有claimの同じSQLite TXでepisodeを再読取し、UUID・置換要求・試行・外部呼出し・不明費用・孤児材料の不存在を検査する。
  初期作成・画像修復・既存M1・課金成否不明を消去したり、費用0へ変換したりしない。
- boot ID・PID・開始tickによる実プロセス所有。begin中も所有し、稼働中の処理を時間だけで奪わない。
- 新しい将来日収集入口は既定OFF（`MINA_REPLACEMENT_COLLECTION_ENABLED`）。
  3食不足を日GETの正式なM1応答で確認し、外部予約の全状態・全ページ照合後だけ不足枠を通常登録する。
  今回の不足枠は夕食のみ。対象の保存済み昼食の現在正本とcomplete品質holdを入力digestで照合し、保留を解除しない。
- 不足枠の登録1回・必要時の既存通常訂正1回だけをRequestLedgerの送信claimと同じTXで拘束する。
  画像、日、区分、コメント、期待版、UUIDを固定し、朝昼・日次の書込みを許可しない。
  応答喪失・未知は元UUID照会へ戻り、新UUID再送を行わない。未知工程・不明費用の既存履歴は保持する。
- 原本3画像・draft/Drive資料・既存工程とM1入力・観測を固定する。
  他2食の食品・OGP独立点検の入力digest・実画像SHA・現在正本を確認し、正式3食precondition取得後にも結果版集合を突合する。
- waivedの操作予約を保持し、未検証のbounded費用割当を作らない。
  完全な既存日のGETは提供側policy/provider/circuit確認にのみ使用し、対象日の版・期限へ転用しない。
- 期限は既存準備期限・真の既存再計画開始+15分・通常前日20時の最短。
  収集前に同じ実期限まで180秒以上を要求し、各外部工程前後にも検査する。
  P直前60秒、登録前からの累積最大2回、日60解析、C前後実合計1800〜2000kcal等は変更しない。
- 保存前画像点検でも食品種類ごとの照合を明示する。識別不確実なら既存の有限画像修復・メニュー再計画へ送る。
  量・調味料を写真から測定できないことだけを不一致にはしない。
- 元の品質保留と自動修復の事前条件拒否を別々に監査へ残し、通知へ固定理由を表示する。
  期限後checkingは要求不明と呼ばず期限保留とし、新規要求は開始しない。

## 本番操作の承認境界

下書き4件の削除と限定復旧について、独立Codexの計画レビューは必須条件を付してGo。
本人へ、対象4件の保存・同ID/内容/未公開の再照合後の削除、夕食収集、既存APIによる昼食の別メニュー置換、
3食・日次点検とDiscord候補作成までを具体的に承認依頼した。返答前には実行しない。
削除成否不明は再削除せず照会し、旧verified行は履歴保存する。Buffer全状態・全ページの再照合を省略しない。
旧20:00の期限を延長せず、過去日救済を有効化しない。
X/Instagram公開、朝昼再登録、本体コード・DB変更は含めない。

削除APIの参照先：
https://developers.buffer.com/types/DeletePostInput.html
https://developers.buffer.com/types/DeletePostPayload.html
https://developers.buffer.com/types/DeletePostSuccess.html

## 検証状況

新規18件で欠損夕食だけの実register_day、再起動時の外部増分0、元昼保留保持、
未知、provider拒否、通常期限、旧初回+15分、対象holdの改変、原本改変、
稼働中所有、未知費用、UUID前再開のowner/epoch保持を確認した。
関連既存回帰も成功。Linuxで既存IG境界試験の実行日依存を検出したため、当該試験の時刻だけ固定した。
旧版bdcf589fのCI/Linux失敗は成功へ読み替えず保持する。これは本番期限を変更する修正ではない。

最終固定版：5c19f4d91cfc3f204a5a47f062b59838fed26420。
CI40490523987：Mina720件中669成功・51skip、一般32件成功。
実UID・秘密資格なしのLinux隔離チェックアウト：90件成功、実課金・本番業務操作0。
別Codexの最終独立レビュー：コード/隔離Go、追加重大残件なし。
OFF配置operatorも独立Go（SHA 93cdd49a9e29e7b54a89202edc908b45ba4943cea2ac9fb70171201599b4e933）。
PR19：https://github.com/t-katsumata1027/marketing-agent/pull/19

## 19:46の本番確認

19:45に同固定版を配置した。切替瞬間の既存61表は全行同一、DB移行なし。
収集機能と当日救済はともにOFF。実プロセス環境、3サービス・5timer active、巡回失敗0、SQLite整合性okを確認した。
再起動後に変化を許容する監査・巡回・停止通知等とは分け、既存業務52表の行保持を確認した。
バックアップ：/var/lib/mina-agent/deployment-review-recovery-5c19f4d9。
切戻しコード：/opt/marketing-agent-rollback-review-5c19f4d9。業務DBを巻き戻さない。

19:46のUUID前読取再確認では4下書きの存在を新コードも検出し、
`replacement_preflight:lunch / stopped / external_dispatch_present`を正確に記録した。
元の朝昼M1要求4件はcompleteのまま不変、P/D/C要求0・試行0、X/Instagram候補0。
下書き削除・追加食事登録・置換・課金処理・公開は行っていない。
過去の下書き4件の削除と限定復旧に対する本人の追加承認は未受領。
投稿案の完成・業務復旧は未達であり、コード反映だけを完了として報告しない。

## 本人追加承認後の実行結果と追加の恒久修正

19:49、本人から「明日の投稿案作成に必要な作業について承認します」を受領。
固定4旧draftの原本と承認を専用台帳へ保存し、同一ID/内容/未公開状態を再照合して限定削除した。
最初のDELETEは成功ID照合後、個別GETがnullではなくGraphQL not-foundエラーとなった。
当該要求はunknownとして保存し再DELETEせず、本人channel/組織の全状態・全ページGETによる現在不在の証跡を別に保持した。
残るprepared3件のみを一度削除し、ACKを直ちに保存、全ページの不在確認後に終端化した。
最終状態はACK確認済みdeleted3件・元unknown履歴1件・現在不在観測4件。旧draft台帳は保持、再削除0・公開0。

19:55、同じ公開hold/owner/epoch/期限で、operator内だけ収集を許可して実行した。
夕食のM1登録・通常訂正各1回、訂正後食品/栄養独立点検、OGP点検まで19:57に完了した。
朝昼の再登録・再訂正は0。正本は朝455/昼628.4/夕716＝1799.4kcal。
朝夕は独立合格、昼のまいたけ識別保留は維持。1800〜2000条件を0.6kcal緩める等の変更はしていない。

この実行で、旧origin受入がlegacy `held+classification`だけを認め、正常な通常点検の`complete+status:hold`を拒否する不整合を確認した。
先行試験は収集と合成P受入を別に実行しており、この結合不整合を見落としていた。
20時までに修正・受入・反映が揃わなかったため、本番P/D/C・置換試行は0のまま停止した。
旧記録をheldへ書き換えたり、架空の保留工程を追加したり、初回時刻・期限を戻したりしていない。

### 原本受入の最終補修

通常の最新register/correction responseと、現在正本の版/共有URL/原・保存画像SHA/実食品/栄養/品質へ結び付いたnative holdを受け入れる。
献立・点検policy・OGP入力digestも再計算し、旧結果・異なる画像・訂正後pass・成否不明は拒否する。
実データRO照合では、M1とpreの食品/summary/品質/rv/URL/2SHAは一致したが、正式preはprovenance/spec/quality内包形式で、
M1.is_ambiguous=falseに対してstored_canonical descriptorはnullだった。
正式Schema・食品合算・descriptor digestを先に検証し、共有する食品順序/名称/分量/全栄養とsummary/品質を厳密照合する。
M1=false、pre=falseまたはnullのみを許し、nullは補完せず両値とdescriptor digestを原本証跡に保存する。
preの必須amount欠損を補完して通さず、M1でのamount欠落は比較投影上だけnullとして保持する。

実register_day→通常correct_meal_once→訂正後native hold→不足夕食収集→実Flow/Policy→multipart P合成HTTP201/readyの結合試験を追加した。
正式descriptor形での正例と、食品順・分量・栄養・品質・provenance・曖昧さ・版・画像・OGPの負例を確認。
実データROによるorigin受入も成功した。これは本番P送信や期限適格性の付与ではない。
予約前の段階をreserving_attemptとして区別し、UUID無しの失効日を要求照会失敗として繰り返さない。

### 20時以降の通常巡回の補修

追加の反映後検証で、翌日holdがnotify_day→issue_cardsのPublicationHeldとして伝わり、20時以降の巡回全体が失敗していたことを確認した。
旧5c/新945の両版で発生しており、別日の公開巡回も妨げるため、notify_day境界でこの型だけを捕捉した。
直接issue_cardsの承認禁止、未確認bundleの拒否、一般のValueError伝播は維持する。
日保留を解除したり不適合候補を作ったりせず、他日の通知・公開巡回は続行する。

### 最終反映と実確認

- 最終実行版：`a34005efd50afa5b75431f2baee05471ffa8027d`。
- CI40497133180：Mina733件中682成功/51skip、一般32成功。
- Linuxの秘密なし実UID隔離試験103件成功、独立のnative9件/通知38件ともGo。
- 20:37、独立Goのoperatorで配置。切替71表全行保持、DB移行なし。
- 通常の将来日不足枠収集ON、当日救済OFF。実プロセス環境で照合した。
  これは前日20時以前の通常処理だけに適用し、10/28の失効済みgeneratingを再開しない。
- 20:38、3サービス/5timer active、SQLite整合性ok、実巡回mode live・連続失敗0を確認。
  業務上の10/28作成保留1件は残り、これを正常完了へ丸めていない。
- 既存業務19表の行不変、M1要求6件complete、旧hold epoch1/held/20時期限、P/D/C0・試行0を確認。
- 10/27の21:30 X/Instagram dailyはapprovedのまま保持。実公開済みとは報告しない。
- 20:39、現在の停止理由を`replacement_preflight:lunch / deadline_exceeded`として追記し、旧停止履歴は保持した。
- backup：`/var/lib/mina-agent/deployment-review-recovery-a34005ef`。
  切戻しコード：`/opt/marketing-agent-rollback-review-a34005ef`。業務DBは巻き戻さない。

本番で将来日の実P/D/Cが成功したことは、まだ確認していない。
10/28投稿案はX/Instagramとも0件、昼食保留・合計1799.4・期限失効のため未完成である。
M1通常解析と独立点検は既承認waivedの操作予約で実施したが、料金実測はしていない。費用0とは扱わない。

## Log-Eats側の調査準備

担当の最新引継ぎ（PR154/155、3ea42672、救済v2配備済み/OFF）とSchema実SHA45155853...を確認した。
v2は日付指定可能だがRescueBasis.target_slotがbreakfast固定のため、今回の昼食へ流用しない。
具体的な調査・限定救済計画依頼はLOGEATS_LUNCH_RESCUE_REQUEST_20261028.mdへ作成し、独立の計画Goを取得した。
本人の明示依頼に基づき20:48頃、担当チャットへ詳細調査依頼を送信した。回答・新契約は待機中。Agent側計画はMINA_LUNCH_RESCUE_AGENT_PLAN_20261028.mdへ作成し、独立Codexの条件付き計画Goを取得した（同PLAN_REVIEW文書参照）。本体の変更・有効化・新case発行・期限例外・実置換は行っていない。

担当側は本人から別チャットで受けた追加指示に基づき、日付・食事枠を指定できる契約へ改修中と報告した。こちらから本体改修を承認していない。
Agentの旧救済台帳・公開前source verifier・Drive保存名・日次本文の再利用不整合を計画へ反映した。
Drive保存名は実Clientを使う合成入力で拒否を再現し、外部呼出0（DRIVE_FILENAME_REPRO.json）。今回新たなコード配置・実課金・食事API書込・投稿は行っていない。
