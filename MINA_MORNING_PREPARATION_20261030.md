# 10/30朝までの承認依頼作成

本人の「朝までにDiscordに明日の投稿案」の指示は10/30の八件を対象とする。旧00:30終了の二caseを再開せず、別の準備専用権限で06:30 JSTまで作業する。旧開始時刻・期限・保留・不明費用・画像・7要求・47工程は保持する。Log-Eats本体は変更しない。

## 原因と経路

本体担当の読取調査では、日次変換失敗を除去して残り二枚でもreadyへ進める不備が確定した。個別OGPにはtimeoutと500、部分応答があり、個別の因果関係は未確定。担当の修正計画は独立Goだが本体実装・配備は未承認。本体の画像readyを実画素合格に読み替えない。

保存済みの三食写真から派生JPEGを決定的に作る。Xは1200×630の四画像を直接添付、Instagramは三storyと日次五画像。AI生成は追加しない。元M1本文・共有URL・栄養1967kcalと版は維持し、旧OGPはhold/unverifiedを残す。別のnative_media契約で原画像SHA・実JPEG・独立画像点検原本・本文・要求UUID・sv1/rv1を固定する。公開前にもM1/元UUID/現行版・共有HTML/UTM・自前画像GET実バイトを照合する。Bufferは添付元URL/MIME/数を照合し、公開成功は同ID GET必須。

## 排他と送付

朝昼夕六件と日次二件を別cohortにする。日公開holdは解除せず、各cohort全員の人間承認とDiscord同ID受領が成立した正確なmemberだけ既存送信排他を通す。取消・別版・成否不明・受領欠落は公開できない。日次の遅れで三食の承認カードを止めない。準備executorはM1 GETとBuffer queryのみを渡し、SNS mutationフラグをfalseに固定する。Drive保存とDiscord承認カードだけを書き込む。

通知は通常の三ボタン、安定nonce、一度送信、同ID GETで本文・画像・送付先・著者・操作を照合する。unknownなら元受領照会に限定し、同cohortの残りも停止する。既存caseを再送するCLI入口は設けない。

## 検証

- 独立したLinux関連回帰50成功。新専用試験9成功。
- 本番をSELECT/backupだけでコピーした隔離台帳と実12画像、外部FakeでCLI八件終端まで成功。同ID受領8、旧7要求・47工程・hold・親case・locks全行不変。本番書込・外部network・食事/日次/AI/SNS呼出0。
- 実12画像は非作成Codexが独立点検し合格。生点検SHA `4e288c29bb88c0d3cb0620c37b679ffd2b031f3aa0fd8416aa91eb3f4972068f`。三写真、栄養、文字、構図、達成率を確認。Xの実Buffer投稿は本番未実行であり、公式契約・Fakeの1POST/同IDGETと区別する。

最終独立Go・固定CI・配置・本番八件同ID受領は別の実績として追記する。現段階の隔離成功をDiscord実送付完了とは扱わない。

## 実施結果（10/30 01時台）

実装固定 `9f86deff252c0f09657c348d8d3be479c8b33943`。非作成Codexによる最終Go、新9件と関連50件の独立Linux成功、CI `40909842686` 成功（Mina1052件・75skip、一般32件）を確認して配置した。配布物の固定commit/SHA/activation_allowed=falseを照合。切替時124台帳全行不変、追加DDL移行なし。退避は `/var/lib/mina-agent/deployment-review-recovery-scoped-9f86deff`、旧ソースは `/opt/marketing-agent-rollback-review-scoped-9f86deff`。

別準備case `6e3462a3-3d7f-47b5-8bbe-fd96a278c8ed`、専用unit `mina-morning-20261030.service` を一度だけ実行し success/inactive。meals六件→daily二件を送付し、phase complete。全八件は承認待ち、各三ボタンが操作可能。実Discordの同ID GETと表示/本文/画像/ボタン/送付先/著者を再確認し、公開配信12画像も再GET検証した。旧7要求・47工程・両停止case・hold・locks・原画像を保持。食事/日次/AI/SNS追加操作0。3サービスはactiveへ復帰済み。

実受領ID：X三食 `1565400235939987577` / `1565400244655620248` / `1565400255158165658`、IG三食 `1565400264125587499` / `1565400272811986955` / `1565400281213173857`、X日次 `1565400597614825584`、IG日次 `1565400607236296850`。送付は完了であり、本人承認・SNS公開の完了を意味しない。

Log-Eats側は調査・計画v2の独立Goまで。本体実装・配備・既存OGPの切替は未実施。本対応は10/30固定の準備・添付画像経路であり、本体OGPの恒久修正完了ではない。
