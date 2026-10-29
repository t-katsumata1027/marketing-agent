# 10/29日次X・Instagram限定遅延復旧計画

## 対象と現在状態

対象は10/29の日次2件だけ。夕食X/Instagramは公開済みで再送0。旧日次Xは送信前停止（route armed、attempted/post_idなし）、旧日次IGはheldでattemptなし。ただしローカル空欄だけを未受付の証明にせず、旧要求/attempt/request/proof全履歴と外部Buffer全状態・全ページを採用前に照合する。正本・食品・画像・本文の作成障害とは別件。

## 新しい本人承認事項

修正版Agentの限定継続実装・隔離検証・最終独立Go・CI・本番配置と、日次X/Instagram2件の新承認カード送付を対象とする。本人が新カード2件とも承認して初めて遅延公開する。時間窓は10/29 22:30 JSTと新継続初回有効化+15分の早い方。未回答を承認と扱わない。夕食再送・AI/有料解析・食事登録/訂正/置換・画像追加・Log-Eats本体変更を含めない。

## 権限・版と旧記録の保持

1. 旧promotion、元config実SHA、元承認原本、document、daily held phase、旧期限、全候補/revision/nonce/承認/公開試行を不変な履歴として保存する。旧phaseをpendingへ戻さない。
2. 元ebbc5eadはroot保護の別pathへ全追跡src/assets/manifest・実HEAD・cleanを確認して退避・読取専用化する。元configはこの実旧rootに対して元release_commit検証を維持する。動的sys.path差替えや旧commit名義での新コード実行はしない。
3. 新continuationは新commit、本人の新承認、旧snapshot SHA、変更allowlistへ束縛する。新たなdoc viewは日次2件の時刻・新commit・新承認binding以外の本文/栄養/画像/正本を変更できない。旧文書の書換えではなく、明示した別投影として保存する。
4. 元日次2件の旧revision/nonce/承認を、履歴保存と同じSQLite TXで失効し、新revision/nonceへ接続する。旧daemon claim/armed routeと新continuationの送信を同TXで排他する。未送信・同版を確認できない場合は採用しない。

## 2件専用の公開排他と時刻

新2memberのsource/owner/epochに固定した専用predicateを用意し、通知・Gateway本人承認・2件承認後hold解除・scoped lock許可・公開claim・直前照合・送信結果まで接続する。旧4member bundleの構成条件や、旧promotionのlock_releasedを緩めない。

カードには「2件とも承認後に遅延公開」と全体絶対期限を表示する。両承認成立の実時刻をactivation_atとして専用台帳へ保存し、共通送信dueとする（各カードの実approved時刻を上書きしない）。送信はactivation_atから120秒以内、かつ全体期限より前だけ。2承認未満・片方差戻し・不明でholdを維持する。過去21:30をdueとして追いつき送信したり、自動延長したりしない。

## Freshな検査と一度限りの実行

- 夕食の同受付ID GETを、照合済み同じ数値アカウントURLを認める補修で検査する。消失/本文/媒体/アカウント/投稿ID/送信時刻の不一致は停止。
- Buffer全状態・全ページ、外部受付済み/予約/不明、M1全日正本/版、実OGP、IG配信画像URLの実SHAを新窓内・規定鮮度で確認する。共有使用量・不明消費・他予約・Retry-Afterを保持する。
- 新continuationの外部照合と送信は事前に永続claimし、日次各1回に固定する。片方公開後は保存済み同IDを照会するだけとし、もう片方や同じ投稿を新UUID/caseで自動やり直ししない。
- 真のunknown/品質不一致/予算・期限到達は証跡を残して停止する。

## 必須隔離受入

旧夕食2published・旧日次2failed/armedから、新2通知→本人2承認→新日次各1公開・夕食0の実Fake通し。新通知/Gateway/hold/scoped lock/Bufferの一連を省略しない。旧承認再利用・片承認・差戻し・旧route競合・異版・URL他アカウント・本文変更・既受付・応答喪失・再起動・期限/120秒境界は拒否し、旧held/原本保持を検査する。外部書込・実課金なし。

計画の独立レビューGoは実装/配置/遅延実行の本人承認を代替しない。最終Go/CIの後でも、本人の新時間窓承認と新カード2件の承認が成立しなければ公開しない。
