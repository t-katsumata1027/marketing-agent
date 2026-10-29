# 添付送付のUser-Agentと拒否診断

当日三食/日次/共有カードの点検と画像合成は完了したが、最初のInstagram日次添付送付が結果不明になった。旧send_filesはUser-Agentを明示せず、HTTPErrorを全てOutcomeUnknownへ丸めてステータスを失っていた。

書込を伴わない同一URLの比較GETでは、旧形式はHTTP403/Cloudflare1010、正規DiscordBot形式は200だった。これは元POSTの拒否を直接証明しない。正規Botでown authorを照合した履歴100件は送信開始以前まで到達し、一致メッセージ0だったが、過去未受付とは断定しない。旧unknownと画像/内容/履歴照会原本を保持する。

send_filesへ正規DiscordBot User-Agentを追加する。nonceを使う場合は25文字以内を事前検査する。明示拒否400/401/403/404/405/413/415/422だけを秘密を含まないstatus/Discordcode/応答SHAを持つ拒否例外へ分離する。408/409/429/5xx/通信/不正応答は結果不明のまま停止する。いずれも自動再送しない。準備カード側も固定診断を追記し、将来の明示拒否をunknownへ丸めない。今回既存のunknownは遡及変更しない。

残4案送付は別の本人承認が必要であり、送付専用caseを計画中。食事/AI/生成/Drive/SNS操作は含めず、各4案の本文・添付SHA・送信先・own author・安定nonceを送信前に永続化する。成功応答と同IDの読取を照合し、unknown/429/5xxでは停止・照会限定とする。Discordのnonce重複抑止は数分間なので永続台帳を主保護とする。

公式仕様：https://github.com/discord/discord-api-docs/blob/main/developers/resources/message.mdx

## 送付期限の追加承認
本人承認により送付専用期限を2026-10-29 17:45 JSTと新case初回有効化から15分の早い方へ変更。各案1回、旧不明保持、食事・AI・画像・Drive・SNS操作なしの範囲を維持する。
