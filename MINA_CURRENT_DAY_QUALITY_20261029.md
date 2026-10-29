# 10/29昼食・本人例外の追加1画像と根拠付き点検

## 承認境界と原因

本人の現在承認はAgent実装・外部代替の隔離検証・独立レビュー・PR更新まで。本番配置・case発行・実AI/課金・Drive・食事操作・Discordはまだ行わない。Log-Eats本体のコード/DB/設定/API契約を変更しない。

本番64f360d1では昼修復点検がhold/image_analysis_mismatch、卵25gの観測量nullで停止した。実送信指示/画像/schema/JournalのSHAを照合済み。精密g計測不能を不一致としない指示は含まれていたが、old schemaには量差の方向/比較根拠がなく、計量不能だけの保留か可視の過量かは確定できない。旧holdを誤判定とも合格とも認定しない。

## 新しい点検経路

`portion_review.py`は今回の新規昼画像だけのpolicy。通常画像・保存後M1・OGPの既存点検policyは変えない。

- 原稿の全foodsをindexで一度ずつ出力し、見える料理構成要素と混合調味料を区別する。卵index2は可視対象から外せない。
- checksは料理構成/スタイル/食品識別/見て分かる量。食品ごとの可視性、量評価、比較根拠、方向、region、観測値/単位を固定項目で残す。
- 今回の米/そぼろ/炒り卵/青菜等では、粒や葉の個数をgへ読み替えない。observedはnull、unitはnone。精密gを観測した値は作らない。
- 明らかな過大/過少や食品欠落/識別不確実はhold。数値がnullでも可視の大差を合格にしない。精密計測不能だけを不一致の証拠にしない。
- raw hold/null、方向/根拠の欠落、矛盾したpass、旧schema応答は`diagnostic_incomplete`のhold。rawをpassへ変換せず、新画像に対する新しい肯定判定＋全checks pass＋全可視食品の根拠だけを採用する。
- 生の構造化結果をJournal/CLIへ保存し、正規化verdictを`quality_case_reviews`へ一回記録。内部思考/自由文を要求しない。

## 原本と一度の例外

契約`mina-current-day-quality-image-v1`、10/29昼食だけ。flagは`MINA_CURRENT_DAY_QUALITY_ENABLED`既定OFF、通常daemonへ接続しない。

root checkpointは、旧parent checkpointと継続停止traceをROで読む。旧12call（AI10/M1読取2）、10組CLI・67ファイル、旧工程/画像/食品basis/adoption/監査を凍結する。旧原本の実ファイルと結果digest、元job/継続jobのheld、両方の初回/旧期限・承認digestと開始監査の一致を確認する。以前のunknown/費用も保持する。

新caseは日で一意、slot=lunchを閉じた承認へ固定。新UUIDでも再発行不可。元job・継続jobはUPDATEしない。本人指定絶対終了と**新case初回+15分の最短**を別欄に一度記録する。旧期限を上書きせず、失効後はやり直さない。

承認構成は既存のroot所有3原本/固定code/checkpoint/履歴/Buffer/Discord宛先の閉形式を使う。approvalはspec/target_date/recovery_uuid（元親）/quality_case_uuid/slot/code_commit/not_after/checkpoint_digest/initial_state_version/approval_reference/mode/cost_mode。mode=prepare_only、cost_mode=waived。Actor作成approved、リンク、書込可能な原本を拒否する。

## 実行順序と上限

1. freshな0食/日次なし、要求/正常候補/専用カード全状態0、全日hold/lock所有epoch、外部全状態全ページ、旧call/工程集合exactを確認する。
2. 旧2画像/2holdを保存し、計画の食品・量・コメント/朝夕献立を変えず、別の指示artifactで昼画像を1枚だけ新規生成する。控えめな卵を薄く盛り、米も適度に見せる。写真で25gの計量証明とはしない。旧画像は編集/上書きしない。
3. 新画像を新policyで一度だけ独立点検。画像生成/点検の不明・診断不備・holdは即停止。旧画像の再点検へフォールバックしない。
4. 朝の合格修復画像は旧policy/実path/round/入力/receiptを参照再利用し、新policyの完了工程に作り直さない。
5. 昼合格後、未開始夕画像/同献立の既存一回修復→Drive→通常M1/既存一回訂正→実解析1800〜2000→日次/実OGP→時刻未定X4/Instagram4/Discord添付へ進む。

追加昼1/点検1は自動修復上限外の**本人例外**。旧初回/自動修復の消費を残し、合計3画像と数える。夕食の累積再計画2回も戻さず、3回目を増やさない。case内画像生成呼出は昼1＋夕最大2まで。AIは送信前に同じTXの実Journal.started/input_digestとcase-callリンクへ固定し、朝生成・旧昼点検・別round・再計画・概算生成を拒否する。別のgeneration-policy.jsonも実バイトで検証し、後から指示を変更しない。

新`quality_case_calls`は旧12callと新caseの操作を区別する。処理不明は該当call/job原本を照合し、M1なら元UUIDのGETだけ。SDK retryなし、waivedの実使用量/不明費用を0円・未消費へ戻さない。

公開hold/送信claim排他、外部一覧の90秒更新/120秒鮮度、残60秒、版/画像SHA・三食実合計、旧コードへの公開禁止、Discord unknown再送禁止は維持する。SNS予約/公開、保存済み食事の削除/再登録/画像置換は含めない。

## operatorと切戻し

`python -m src.mina.quality_case_cli`のcheckpoint/statusは機能OFFでもRO。checkpointはroot専用で`--parent-checkpoint`（旧継続の保護原本）/`--receipts`/`--stop-log`を要求する。食品情報を含む原本JSONは端末へ展開せず保護ファイルへ保存する。

activateは3承認原本とfreshな正本/外部一覧を照合し、別caseを一度発行。runは未消費caseだけ。終了後は既存lookupsで元要求GETへ戻り、失敗caseをissuedへ戻さない。コード切戻しは64f360d1、DB/費用/画像/holdは巻き戻さない。

## 検証と次の承認

実SQLite/旧停止の再現/実PNG→JPEG/旧policy朝画像の再利用/新typed点検/通常M1一回訂正/日次/8案を通す。外部AI/Drive/M1/Discordだけを代替する。nullだけのpass禁止、可視過量hold、旧hold/画像/監査改変、追加case/再生成/再点検/再計画、unknown、二重実行、期限改変、130秒生成後の外部再取得、外部不明、M1応答喪失、Discord再送拒否を確認する。実本番原本のRO照合も行うが、実生成/本番M1成功とは扱わない。

固定版CI/独立Go後に、救済OFF本番配置と別の限定実行を本人へ提示する。現時点候補は**10/29 13:30 JSTとcase初回+15分の早い方**。受入完了時刻で改めて明示承認を得る。旧09:44/10:23/10:30を更新したことにせず、間に合わなければ開始/無断延長をしない。15分内の完成は保証しない。
