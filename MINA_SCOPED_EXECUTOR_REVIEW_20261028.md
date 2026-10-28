# 10/28昼食限定executor 独立レビュー

判定：**Go（本人承認済みの限定実行operatorの範囲）**。追加必須指摘なし。

対象は `C:/project/marketing-agent/tmp/preparation-recovery-20261028/scoped_executor.py`。固定SHA-256は `0145524a287300e70dcd9c9f854cf558e5d1fd902564d13961cd00f539064e73`。実行基盤はAgent `1c5224b6d0fe1670ee9b874b08688eeac42da12f`、本体 `f4aee73ecc9ad2c784c6196fb4d04009812037d6` を前提とする。

## 確認した境界

- 対象は2026-10-28昼食。同じrunの繰返しを拒否し、成否不明は元UUIDの照会へ戻す。朝夕の再登録、SNS予約・公開の入口は呼ばない。
- 本番ON管理原本を日・枠・case・本体・Schemaへ束縛し、最初に採用した原本とON時刻を固定する。再ONによる延長を拒否する。実行上限は本人の15:00 JSTと初回ONから15分の早い方で、既存Flowのcase期限も維持する。
- 実ON後に他2食の独立点検を行い、最新anchor・実正本・SHA・版世代・120秒鮮度を照合してstageする。管理grantの発行は本体担当に分離する。
- 準備専用の永続公開禁止を維持する。4X・4Instagram候補の通知には、時刻は案で内容の承認だけでは予約・公開されない旨を付ける。
- 通知は同一SQLite transaction内のclaimと実送信直前のcheckpointを通す。実送信前の停止はobsolete、試行後の不明はoutcome_unknownを保持して再送しない。通知の期限拒否時は当日の未送信pendingだけを失効させる。
- lookupは期限後もAI・Driveなしで元UUIDのGET照会だけを許可する。

## 独立検証

固定実バイトのSHAと全体AST構文を検査した。ASTから該当分岐を取り出した隔離試験で、期限後のrun拒否、lookup許可、notifyが例外処理へ到達して当日のpendingのみobsoleteになることを確認した。既存outcome_unknownと他日のpendingは保持された。外部呼出し・Discord送信・課金・本番変更はすべて0。

直前指摘だった「notifyの入口の期限拒否が失効処理を迂回する」問題は、notifyを冒頭拒否・初期checkから外し、try内の同じ期限checkpointで拒否する形で修正済み。

このGoは実行成功や本番原本の真正性を代行証明するものではない。実行時に固定コード・Schema・5承認原本・実ON原本・fresh anchor・公開holdを既存runtimeが照合し、不整合・期限超過・unknownでは停止する。新しい期限、SNS予約・公開、追加試行を許可する判定ではない。

## 期限の根拠訂正・最終採用版

最終採用SHA-256は **`8d56a29539d91e3ff2672d405ca4c84ae7cca36a6223405f98e03d4da953f97f`**。この版をGoとする。上記の「サービス初回ONから15分」は採用しない。前回レビューもサービスONとcase権限発行の起点を混同していたため訂正する。

本人承認の「権限有効化から15分」は、正式Caseの `first_activated_at` を起点とする。Agentの `ScopedRescueContract.case` は `created_at == first_activated_at` と `hard_deadline_at == min(first_activated_at + 15分, basis.not_after)` を検査する。本体 `store.issueCase` はcase発行時のDB時刻を使用し、対応DDLも同じ起点・期限の不変性を要求する。サービスflag ON/READYはP権限の発行ではない。

最終executorは実ON管理原本のSHA・初回原本不変・未来拒否・対象確認を保持し、grant前の4点検とstageは本人の絶対期限15:00 JSTを適用する。grant後のP・decide・finish・notifyは既存Flowのcheckpointによって、同一caseの正式期限（15:00と権限発行から15分の早い方。保存後OGP期限もその上限以内）を適用する。旧期限・同case起点・UUID・消費回数を再起算しない。期限後lookupのGET専用経路は保持する。

独立に固定実バイトとASTを確認した。変更したコメント・期限returnを旧版へ逆変換すると、旧Go版 `0145524a…` のSHAと完全一致した。他の実装差分はない。絶対期限returnとcase期限算術の隔離試験も成功した。外部呼出し・課金・本番変更は0。
