# 10/29日次公開停止：公開済みXリンクの数値アカウント表記

## 読取確認した事実

21:35 JST時点の稼働はebbc5ead。夕食X/Instagramは公開済み。日次Xはchanges_requested/preflight_failed、Buffer routeはarmedでattempted/post_idなし。日次Instagramはheldで送信試行行なし。日次のSNS送信開始を示す証跡はない。dailyの共通外部照合phaseはheldであり、自動解除/再送は行っていない。

夕食Xの同じBuffer受付IDを一回GETした。本文230文字、Buffer投稿ID、channel.id/service、status=sent、sentAt、同じネイティブ投稿IDが全て一致。一方、externalLinkだけが次の表記へ変化した。

- 公開時保存応答: https://twitter.com/EatsLog88161/status/2115749351351820503
- 現在の同ID GET: https://x.com/2032214868485685249/status/2115749351351820503
- 保存済みroute.service_id: 2032214868485685249

AgentのBufferPublisher.validateはユーザー名付きURLだけを許可していたため、同じ数値アカウント表記をbuffer_native_x_result_missingとして拒否した。post_promotion_external.own_sentから呼ばれたこの検査によりInstagram側の事前照合が先に停止し、共有daily phaseがheldとなりX側も停止した。認証失敗・夕食の消失・本文改変・新しい投稿の成否不明を確認したという意味ではない。

## 恒久修正と検証

channel/textが一致した後に、従来のEatsLog88161表記に加え、保存済みroute.service_idと完全一致する数字だけを同値として扱う。他の数値ID・他ユーザー・host偽装・http・query付きは拒否する。ネイティブ投稿ID、status、sentAtと元proofの範囲、本文、channelを引き続き検査する。正本/候補/旧承認/公開時刻/既存heldの変更は含めない。

局所14テスト成功。実装不参加Codexによる追加差分の独立Go、清浄Linux/Fake14件成功（実外部/課金0）。最終pinとCI結果はPR22へ記載する。本番コードは未変更で、この差分だけで旧停止phaseや定時期限を解除しない。

## 明日10/30分との区別

10/30は朝昼夕の保存工程が存在するが、昼食の訂正後の独立画像点検がimage_analysis_mismatchで保留。朝夕の訂正後点検はpublishable。保存済み修復へ進む際にBufferの既定ローカル枠へ戻る別の停止もある。Instagram準備候補は未成立であり、X用正本候補が揃わない依存停止と区別する。旧品質判定を新しい概算方針で自動合格へ変換しない。

## 今日の日次を復旧する際の承認境界

21:30の定時猶予は経過済み。既存承認を遅延公開へ読み替えず、日次X/Instagramの2件だけについて、外部未受付・現在正本・実画像・同一本文・旧承認/停止原本を確認した上で、新しい時間窓/通常承認カードを使う専用継続計画が必要。夕食は再送しない。

既存promotionのconfig/code pinがebbc5eadへ固定されているため、PR22の単純配置だけで今日の日次が再開するとは扱わない。元config/文書・held phaseを保持した継続と、正規配布版/構成の受入を独立レビューする。本体変更・食事再登録・画像生成をこの障害の対応へ追加しない。
