# Buffer実枠との整合と限定継続

本人は、既存キーの読取確認1回で実枠を確認できた場合だけ累積上限最大540へ整合し、重複削減/共通枠予約を実装・検証・独立Go/CI後配置することを承認した。今回のprobe/実行は10/29 15:00 JSTまで、新継続は初回+15分との早い方。画像追加/SNS予約公開/本体変更は禁止、旧usage/記録/waivedは保持する。

## 実装

rootだけが、既存キーのsubject hash・期待組織digest・今回の日/絶対期限・一意UUIDへprobeを許可する。固定ORGANIZATIONS queryだけを一回、retry0。送信前の同SQLite TXでreserved→startedのclaimと既存usageの241件目を記録する。GraphQL/HTTP成功、期待組織一意一致、実3窓q/r/tと同一要求IDを確認後だけcompleteへ進む。失敗/unknownでも消費は戻さず再probe不可。

rawヘッダー観測だけでは上限を変更しない。承認probe completeの同subject、全窓が有効、day q>=600の場合だけ共通guardを累積最大540/24hへ整合。15分90/月2900と実q90%の低い方を使い、既存消費/未送信予約/実r/通常枠を差し引く。失効/欠損/低q/識別不一致なら240へ戻して停止する。pk/資格/ヘッダー原文は保存しない。Retry-Afterの実値は要求IDと保存し待機中の新送信を拒否する。

HTTP応答ヘッダーは各要求のローカルrequest_idへ結合し、共有clientの可変IDで並行応答を取り違えない。古いIDの観測が新しい観測を上書きしない。

全外部一覧のmetadata org/channelsは1check開始時に照合し、全postsページと状態を維持したまま4ページ12→6要求へ削減。失敗後の次checkではmetadataを破棄して取り直す。normalization gateの完全なreceiptだけを同case/承認/対象/hold所有世代/channels/DBの実digest/90秒以内へ束縛して再利用する。実行中の鮮度更新は維持する。

実枠確認後、15:00までの最大78読取要求を通常運用8要求とともに全3窓で原子予約する。通常の別処理は予約分を消費できない。予約使用は固定ORGANIZATIONS/CHANNELS/POSTSだけ、コメント付きmutation等も拒否。claim前のremainingだけ解放し、送信済み/unknownのusageは保持する。ページ増加で予約が足りなければ省略せず停止する。

## 運用と受入

buffer_budget_cliのidentityは秘密を出さないhash確認だけ。probe/reserve/releaseは既定OFFのMINA_BUFFER_BUDGET_ENABLED、root所有の固定承認JSON、固定配布commitを要求する。quota probeは予約承認と別の一回権限であり、新キー/有料プラン/カウンタ初期化は行わない。

新probe成功後も、未開始の正本/外部予約/旧原本/総枠を照合してから既承認normalization caseを一度だけ有効化する。保存済み3画像を使い、Drive/通常三食/保存後点検/日次/実OGP/X4Instagram4のDiscord送付へ進む。新AI解析等は既存waivedを維持する。

隔離は240停止/probe241/成功後認定/別組織拒否/再probe不可/低q・欠損・unknown/予約claim/通常枠保全/未送信のみ解放/mutation拒否/Retry-After/失効、およびinventory/normalization/Instagram回帰を対象にする。独立指摘の「ヘッダー観測だけで認定」「組織未照合」「並行client ID」「コメント付きmutation」は補修した。

残る仕様上の制約：証跡失効後かつ累積240超では、無観測のまま540を維持せず停止する。今回の一回probeを将来の無制限確認許可とは扱わない。常設guardの失効後自動再確認は今回の承認に含めず、通常運用の後続課題として残す。
