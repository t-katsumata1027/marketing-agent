# 10/30朝昼夕六案の先行承認

本人回答「正常な朝昼夕の承認依頼を先に揃える」に基づく。日次OGPはp3だが実画素は昼夕二枚で朝食欠落。旧日次品質hold・停止済みnext_saved case・元画像・七要求・全工程を保持する。食事/日次の追加要求、追加AI、Log-Eats本体変更はしない。

朝昼夕のX三案とInstagramストーリーズ三案だけを別台帳へ固定する。全日holdのowner/epoch/stateは変えない。旧初回と00:30期限を継承し、配信保存/通知の直前にも確認する。合成済み三画像は別Codexが実画素・各栄養・食品対応・可読性を点検し、すべてpublishable。日次画像を合格に読み替えない。

六通知の同ID・本文・ボタン・nonce・著者・宛先の受領照合と、六候補の本人判断が成立した場合だけreadyとする。通常の「見送り」も一件の本人判断として数えるが、見送った候補自身はclaimできない。

公開例外はtarget30/朝昼夕/platform/rowのdigest・revision・payload・actor・approvedが登録済み六memberと完全一致する個別leaseだけ。引数なしのhold検査、M1、日次、汎用Buffer mutationは拒否を維持する。実route/attemptの版・digest・unknownも毎claimで照合し、一件不明なら残りも停止。通知準備期限と翌朝以降の通常定時公開/120秒猶予を混同しない。

Instagram固有sourceは専用markerに限りX三参照を許容し、日次は禁止。全日SV・三原画像SHA・同枠X正本/個別OGP・凍結manifest/caption/実assetを検査し、欠落した共有日次OGPへは依存しない。

隔離試験は六通知/六判断/通常claim/route bind/IG begin/実BufferPublisher Fake createの通し、tuple改変、未知candidate/attempt、日次誤claim、旧hold変更、通知応答喪失を含む。最終独立GoとCI成功後に配置・準備を一回実行する。日次二案は未完成として別報告する。
