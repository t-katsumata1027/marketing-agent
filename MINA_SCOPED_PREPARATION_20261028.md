# 10/28昼食：遅延準備専用の実装・配置計画

## 承認境界

本人が①の実装・検証・独立レビュー・救済OFFのAgent本番配置を承認した。②の実課金・権限発行・食事置換・Discord送信・SNS投稿は行わない。Log-Eats本体は変更しない。

## 固定本体と実装

本体f4aee73ecc9ad2c784c6196fb4d04009812037d6はed486a8とtree一致、正規リリース40612716619成功。Schema SHA baf76695c55298cd97fe8b24fc488c3c164585c11ae735986da380c4e52397abは不変。
本番版へ隔離HTTP pinを更新した。通常置換・旧救済の契約や期限、UUID、品質、1800〜2000kcalは変更しない。

10/28昼食だけ、execution_approval原本へ完全一致するpreparation_windowを設定できる。modeはprepare_only、対象日・枠・case・絶対終了・4提案時刻を固定する。別日・枠・case、期限経過、欠損、改変、同日外や逆順の提案は拒否する。通常7:30ゲートは維持し、承認原本のある限定準備だけ絶対終了を上限にする。本体の初回有効化+15分・最大回数は保持する。

4X/4IG・8画像の準備候補に提案時刻を記録する。提案時刻は実公開許可ではない。通常の候補/版/本文/画像/源照合と全点検を維持する。
scoped_preparation_locksへ日・case・owner/epoch・window原本・全候補digestを同じ候補TXで固定する。通常holdがreleasedでも、印を削除/改変しても、永続ロックがX/IG claim・最終require_claim・Buffer mutation・IG下書き・全承認後の停止解除を拒否する。公開可へ変換する経路は追加しない。
IG実runtimeも永続windowと同slot X原本の印を照合する。

## 検証

本番固定版で実HTTP20件成功（112.099秒）。うち遅延通しは朝7:30後のnative保留→管理operator→P→2画像→実Drive port→C→日次→4X4IG/8画像→全source/実IG runtime確認を実施。全件合成承認後もhold解除なし、印を全候補から削除し通常holdを解除してもclaimなし、最終fence/IG下書き拒否・外部CREATE0。実資格/課金/外部書込みなし。本体原本SHA前後不変。
局所55件成功（63.156秒）。独立レビューと最終CIは固定commitで別途確認する。

## 救済OFF配置

scripts/deploy_mina_scoped_off.pyを使用。稼働a34005efを検査し、新旧救済OFF・新config未設定、通常収集/UTM/Instagramの既存設定を維持する。3service/既存5timerを停止し、設定/SQLiteを保護退避する。既存全表の全行digestを保持し、空のscoped_rescue_jobs/requests/events/callsとscoped_preparation_locksの5台帳だけを追加する。権限・業務要求行は作らない。
固定CI・独立Go・本体成功を確認して配布し、専用UIDの隔離Linux試験、OFF構築拒否・Schema照合を実施する。切替後に実processのcommit/新旧OFF、3service/5timer、巡回、当日候補0/試行0/元holdと期限を確認する。
失敗時は停止を確認し、元コード・設定を復元する。実行中oneshotは強制中断しない。業務DBは巻き戻さない。

## ②の提示予定（まだ未承認・未実行）

対象は10/28昼食のみ。絶対終了候補は10/28 15:00 JST、有効化から15分との最短。15分を使う場合の開始上限は14:45。承認待ちで過ぎた場合は延長や現在時刻への再初期化をしない。
提案表示は朝15:30・昼16:00・夕19:15・日次21:30。準備専用の表示であり、予約・公開はしない。後の実公開時刻/公開可への変換は別計画・承認とする。
他2食native anchorは管理側の読取成果物として正本版/画像SHA/解析digest/OGP24hexへ束縛する。Agentへ管理資格を渡さない。新鮮な3食・全外部一覧/予約・不明費用・共通試行・期限・所有holdを再照合する。
②は新画像/解析/独立点検/Drive、単一権限/Pと必要時DまたはC、日次/OGP、準備候補とDiscord説明までを具体化する。旧朝夕再登録/再訂正・SNS公開は含めない。waivedは費用0を意味しない。
