# LogEats M1タスクリストのレビューと利用側の準備

## レビュー対象と結論

ユーザー提供の2026年10月12日版一覧をレビューした。PR #129のpush済みheadはf3c4242、Draft・未マージであることを読取確認した。進行中の未コミット実装・各PG試験の実行内容はこの担当では再検証していない。今回の評価は一覧の依存関係・受入範囲に対するものであり、供給側コードの独立レビュー完了を意味しない。

実装、代替試験、実PG、実Clerk／R2、回収を分離している点は妥当。工程4の全経路接続が未完了であり、現在は本番引継ぎへ進めない。追加の大きな設計変更を利用側から要求する段階ではなく、現在の失敗境界を閉じ、版を固定して受入へ進むことを優先する。

## 修正・確認してほしい点

| 項目 | 指摘・次の行動 |
|---|---|
| 工程8の位置 | commit固定・push・同head CIは工程5の最終レビューと工程6の正規Preview受入より前に着手する。工程8の最後で各証跡を対応付ける。未コミット版をレビューしてから別コードをCIへ送らない |
| 工程4の失敗 | start_unconfirmedを、producer送信・Worker受理・coordinator受理・PG TXの開始／確定・応答受領へ分けて分類する。原文秘密値を出さず、同じ失敗経路の原因を修正し、全経路の実PG結合を再実行する |
| 再試行 | 結果不明の資源生成・start・finishを単純に再送しない。DBの回収成功と外部資源の不存在を混同しない。必要な照会・終了確認・証跡保全を明示する |
| レビュー対象 | full variant／Renderer／runtime SQL／Clerk接続の局所レビューと、最終停止解除レビューの対象commit・ファイル範囲を区別。局所合格を全体へ拡張しない |
| 描画digest | 64hex／24hexの整合は生成元・用途・契約を確認して解決する。切詰めやguard解除で通さない。利用側に返すOGP実画像SHAとAPI契約を変更する場合だけ、影響を先に通知する |
| 外部切替 | QA Worker切替が実受入の前提なら、その差分・同head・対象・Core QA影響・復元条件を具体化した段階で承認対象を一度にまとめる。安全条件成立前に実行しない |
| 正規受入 | 最終固定版で移行、3食→日次、競合・故障、共有HTML→配信PNG→APIの版／SHA、PC・スマホ、実資源cleanupと対象外保持までを確認する |
| 本番工程 | 8工程は隔離受入までの一覧。本番DDL・正規リリース・postdeploy・Mina有効化・本番受入・後処理・最終引継ぎは後続工程として一覧へ明示する。実行開始は既存承認境界に従う |

Clerkの完全0件観測は、新規作成に用いる観測範囲・instance・時刻・期限を明記する必要がある。Console全体の0件を曖昧に要求する表現と、実際に採用した条件を混同しない。確認のために未承認の主体・鍵・権限を追加しない。

SDK代替試験の外部要求可能性を結果不明のまま保持する扱いは妥当。訂正後の固定.invalid／mock fetch試験の成功で、初回の不明状態を解決したとは扱わない。

## Marketing Agent側で追加した受入入口

`inspect-handoff`は最終引継ぎ情報の形式確認だけを行う。コードcommit、契約原本の実SHA-256、環境、認証の参照先、停止復旧文書、証跡の種別・結果・対象版を検査する。秘密値、本番URLのQA流用、既知の本番Doppler configのQA流用、古いコード・代替結果の実受入扱いを拒否する。

これはMarketing Agent内部の補助書式`mina-handoff-v1`であり、LogEatsのAPI契約`mina-write-v1`に新規フィールドを要求するものではない。供給側は既存の報告・証跡・最終接続情報を渡せばよく、利用側がその内容から補助JSONを作成する。証跡内容を自動で真と判定せず、担当者による原文確認と固定ソースの結合試験を別途実施する。

```sh
python -m src.mina inspect-handoff \
  --manifest <利用側で整理した非秘密の引継ぎJSON> \
  --contract-file <受領した契約原本> \
  --producer-commit <確認対象の40桁commit>
```

この入口は台帳・接続設定を変更せず、クライアントを構築せず、ネットワークを呼ばない。構造が合格しても`production_start_allowed=false`、`evidence_contents_verified=false`、`producer_code_compatibility_verified=false`を維持する。

補助JSONの`evidence`はisolation／ci／independent_review／acceptance／cleanupを含む。本番引継ぎではmigration／release／postdeploy／mina_activation／production_acceptance／production_cleanupも含む。各項目はcode_commit・result・kind・referenceを持つ。`kind=real`は実行済みの当該証跡を指し、CI成功だけで実資源の受入成功を保証しない。merge commit等とレビュー対象版が異なる場合、供給側の対応資料を先に確認し、未確認のまま同じSHAへ書き換えない。

本番接続先・認証参照先は実受入と正規リリース後に確定する。未確定の情報で実行可能な完成JSONを作らない。管理読取認証・秘密値・生の内部IDを引継ぎ資料へ含めない。

## 利用側の残件

- X認証先の実API照合と初回29投稿・旧資料の対応は確認済み。Googleは本番化後に本人が再認証し、実Drive保存・再取得、予備鍵での実復号・隔離復元も成功。
- 履歴は毎日の手入力を求めず、公式X読取・台帳照合による自動資料を実装。旧予約ゼロ・旧生成停止は本人確認済み。日次実経路は開始許可後に確認する。
- 独立差分再確認、217件・同版CI、固定7b06e18の業務停止配置と新版実バックアップは完了。Google公開文書整合、完全なログインなし復帰、自然なタイマー発火、最終LogEats版への適合、実日次・20時通知・本人承認・初回公開が残る。保持手順はBACKUP_RETENTION_RUNBOOK.md、Googleの不足はGOOGLE_POLICY_ALIGNMENT_20261012.md。
- LogEatsの最終版と開始条件が届くまでは、業務API接続・登録・共有作成・Discord通知・X公開・prepare timer有効化を行わない。
