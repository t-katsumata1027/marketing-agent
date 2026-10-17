# Instagram定期投稿の登録目標接続・配布

## 受領した契約と実接続

本人からLog-Eatsの目標追加完了報告を受領した。提供側のPRは#140、本番版はf2ced5e2a884a73587b1091ba3852fa9a2e4af9a。Marketing Agentから提供側コードへの参照・変更・配置は行わない。

2026年10月17日、既存のサーバー用秘密参照だけで固定originのstate APIをGETし、HTTP 200・no-store、4目標の契約適合、同日の既存X正本4件との一致を確認した。Origin・Cookieは送らず、利用側もCache-Control: no-storeを指定する。食事登録・日次共有作成・再解析・追加料理画像生成は0件。

現在登録された目標は2000kcal／P60g／F56g／C314g。同日のAPI集計1972.3kcal／P87.11g／F55.71g／C300.87gから、達成率は表示丸め後99%／145%／99%／96%。Pの145%を100%へ切り下げない。棒の長さだけ表示領域へ収める。nullは目標未設定と表示し、未取得・非200・不正形式は画像確定を保留する。取得するのは現在の登録目標であり、対象日当時の目標履歴ではない。

## 配布内容

- 稼働固定版2caa404を基点に、同じmarketing-agentの専用worktree・codex/mina-instagram-publishing-20261017へInstagram差分だけを切り出した。他案件の共有作業領域は保持する。
- 3食の9:16画像と5枚の4:5日次カルーセルを、実際のX用原本とAPI値から合成。追加の生成API・有料フォントは使用しない。
- APIのsummary.totalCalories／totalProtein／totalFat／totalCarbsを日次合計として使い、既存X日次との対応も検査する。食事・結果ハッシュ・日版・原本・点検記録の照合を維持する。
- 過去日の実データでも非公開下書きの受入検証だけを行えるacceptを追加。過去日の公開候補登録はprepare/saveで拒否し、下書きから公開へ自動変換しない。
- 配信形式と画像・本文・メタデータの下書き再読取が成功した形式だけを公開可能にする。承認はInstagram専用ボタンで別途取得する。
- 無料Buffer要求数はXと同じSQLiteで合算。HTTP読取中に共有SQLiteの書込ロックを保持しない。
- 公式ロゴ・フォント・ライセンスを正式配布へ含め、配布commit照合の対象にする。
- 既存Discord GatewayへInstagram操作を追加。別Gatewayは起動しない。独立した配信サービスと20:10 JSTの翌日準備timerを用意する。

## 完成画像の共有

以下は10月17日分の実データであり、公開前の確認用。Driveで保存・匿名JPEG取得・SHA-256・寸法を照合した。

- [朝食](https://drive.google.com/file/d/1XGoNoe68hkYZTD4XAaldynQvzVk1OEiw/view)
- [昼食](https://drive.google.com/file/d/1p6CYptUX9QEPlVc-H0yWQvJWm10BQnTQ/view)
- [夕食](https://drive.google.com/file/d/1VLoMHU0duXCEq1dxhnNZk0PDKju_5-Qe/view)
- 日次：[表紙](https://drive.google.com/file/d/1VF3cXfh2LCEyisfFitEuYMW9wQ3XqE2_/view)／[朝](https://drive.google.com/file/d/14pF32DPoxbcNnKX4w07luTQzTBolP77O/view)／[昼](https://drive.google.com/file/d/1qu046nRFgvKSEH7eRGta6uNK2NczbUDI/view)／[夕](https://drive.google.com/file/d/17wEFEMfucUZ-HB6Bl_cTgvNMF-abvQuV/view)／[目標達成率](https://drive.google.com/file/d/1_oI-26Ym0mu27MgkL_PfHWzfIGquJxO4/view)

## 検証・稼働証跡

専用試験23件成功。目標の全null、現在の登録値の反映、取得失敗、100%超、summary不一致、認証ヘッダー、Xの承認保持、古い操作の拒否、画像改変、API予算、既知IDの読取照合、共有DBのロック解放を含む。

実下書き確認の最初の試行は、APIのchecked_atがサブPCより約1.84秒先のため送信前に停止した。Windows/WSLはNTP同期済みで、OS時計は変更していない。Instagramで使う読取だけ最大5秒を実時間で待ち、checked_atに達してから既存の鮮度検査へ進む。Xの既定上限1秒は維持し、5秒超・待っても未来の応答は停止する。Bufferへの下書き作成前だったため、この試行の再送防止上の未解決要求はない。Xの既存8テーブルの行ハッシュも不変。

全体試験・固定版CI・Linux確認・非公開ストーリーと5枚カルーセルの実確認・配布と有効化結果は実施後にこの節へ記録する。現時点では未確認を成功と扱わない。

## 定常運用と残る確認

翌日分のX完成成果物を20:10 JSTに再利用してInstagram画像と4件の専用承認カードを作成する。本人が承認した枠だけ07:30／12:15／19:15のストーリーズと21:30の日次カルーセルを送る。本文や時刻を過去へ偽装して先行公開しない。送信成否不明は自動再送しない。

初回の自然な準備timer、本人の実際のInstagram承認、予定時刻のInstagram実公開は各時刻に実結果を確認する。設定・下書きの成功だけで公開成功とは報告しない。
