# 10/28点検鮮度切れの補修と管理資格を分離した受渡し

## 状態と範囲

19時の限定実行は権限発行前に停止し、枠は終了した。Log-Eats本体はAgent担当が変更していない。稼働Agentは1c5224b6、救済OFF。以下は次の具体的承認に備える未配置のローカル実装であり、新しい実行時間・実点検・権限発行・食事操作・Discord送付を許可しない。

## 原因と変更

朝夕の実点検4件はpassだったが、最初の完了からstageまで137.083秒で120秒条件を超えた。CLIや品質ゲートを緩和せず、点検後のチャット往復を固定ファイル協調へ置き換える。既存の古いcompleteを同keyで再実行できないため、本人が別途承認する追加一組4点検だけを、同case/同予定Pへ追記する。

- `pregrant_refresh_claim`を有料開始前に既存SQLiteの同transactionで永続化する。旧4call/2receipt/費用digest、同日枠、P、正本/画像/解析、policy、公開hold owner/epoch、新実行承認実SHAと固定版に束縛する。新表・本体DDLは不要。
- 追加keyは`pregrant:refresh1`だけ。新4callと費用を追記し、旧4件/旧2receiptを変更しない。旧新混合・余分なkey・未完了claim・unknown・未照合費用を拒否する。失敗しても回数/費用を戻さず、旧passへ戻らない。
- 正式`reviewed_at`は追加の食品/OGPの実完了時刻の最古を採る。120秒を維持する。完了一組だけは実時刻のまま読取再開できる。stage済みは機能OFF/期限後でも元caseのGETだけに分岐し、再発行/新課金しない。
- `mina-management-handshake-v1` Schema SHAは`38b94c69bcd23f5384b15d2900ecc58fcca280b01286a50391d6cf87b189759a`。重複key/非有限値/不正UTF8/余分なcommand/path入力、scope/hash/helper差替を拒否する。
- 管理資格はWindows管理checkoutに残す。Agentは既存Mina Bearerだけを用いる。管理出力はroot所有でAgentが親を置換できない領域、Actor inboxはJSON専用とする。既存管理CLI/固定FS proxyへだけ接続し、Actor入力を実行命令や管理権限へ昇格しない。
- 管理manifestは同case/P・新実行承認SHA・両commit・Schema・policy・期限と、4 helper SHAに束縛する。helper SHAをexecution roleとも照合する。execution role自身はmanifest SHAを含めず、循環hashを作らない。
- native metadataは実画像の合格証明とは区別する。`read_started_at <= checked_at <= now < valid_until`、最長120秒、自己digest・3食正本/世代/解析/画像を照合する。
- CLIは明示`--execute`または`--lookup`だけ。新5原本・private context実SHA・固定codeを検証する。管理協調からP/C、再点検/新版再承認候補、Discord8件まで一プロセスで接続する。serviceの救済OFFとSNS予約/公開禁止は維持する。
- Discordはカードをpendingにする時点で「準備専用・公開停止中」「提案時刻」を固定する。送信開始後不明は保持し再送しない。入口失敗・期限切れ時も当日pendingだけを失効し、他日・sending/unknownは変えない。

## 独立レビューと検証

非実装Codexは局所30件を独立実行し成功。予定Pの承認束縛、stage済みの期限後/OFF照会、helper SHAの承認照合という3指摘を補修後、局所暫定Go。下流通知の入口失敗cleanupとdaemon先行時の説明表示も指摘を受け補修した。固定headの最終独立Go、管理側との全体Goは別途必要。

実施済み：

- 新点検/協調と既存救済/遅延準備の対象46件成功。
- 本体e0e0bb3をgit archiveで隔離展開し、実localhostハンドラーで旧4+追加4保存→実operator→P/C→X/IG各4案/8画像→Discord8モック送付→再通知0/再P拒否→承認後も公開禁止を確認。
- Discord応答不明時は食事savedを保持し、2回目の送信増分0。
- 期限切れ通知は当日pending0・送信0、他日pendingとunknownを保持。カード生成時点の準備専用表示を確認。
- 実root/既存mina-agent UID995で、root原本読取、group書込/非root所有/symlink/置換可能親/領域外の拒否、Agentによるauthority置換拒否の7境界成功。業務接続/実課金0。`/run`の専用一時領域は削除。

Windowsでのfile port結合はroot所有権検査だけを合成し、実UID検査とは分けている。本体handlerのDB/R2/AI/Drive/Discordは隔離fixture・合成応答であり、本番E2Eや実費測定ではない。

## 残件と次の承認

管理側はFS33、Manager34、adapter67、合成glue16の層別成功・局所独立Goを報告した。正式CLI/固定FS proxy接続、deep validatorからManagerへの実コード結合、4 helperの依存閉包と配布単位、両側固定headでの全体レビューは未完了。層別成功を全体Goへ読み替えない。最終fixtureはcontext原本/期待参照と正式helperのchecked_at形を含む整合セットを別名で提供する。

全体Go・最終CI後にだけ、固定Agent/管理helperの配置・起動、追加点検一組4件、同case/同Pの新絶対終了時刻と初回有効化+15分の最短、昼食限定救済→X/IG各4案→Discordを具体化して本人承認を求める。朝夕の再登録、SNS予約/公開、旧QA残存回収、日付を跨ぐ実行は含めない。
