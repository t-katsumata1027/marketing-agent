# 保存済み一食置換のAgent側先行実装

本人の2026年10月17日の着手指示に基づき、添付された契約計画v2.1を入力としてMarketing Agentだけを変更する。本体コード・本体DBは参照・変更しない。本番への配置・新APIの実送信・18日分の置換は今回行わない。

正式な実装先は同一リポジトリの管理worktree C:/Users/Takashi Katsumata/.codex/worktrees/mina-meal-replacement-preparation/marketing-agent、branchはcodex/mina-meal-replacement-preparation-20261017。主作業フォルダーの既存未コミット変更を一括で上書きしない。

## 現在の範囲

API提供側の確定Schema・fixture・MANIFESTは未受領。本文から推測したJSONを本番契約として実装しない。今回は、確定形式に依存しない次の基盤と既存入口への停止検査を実装する。

| 部品 | 実装内容 |
|---|---|
| publication_hold | 単一の固定みなアカウントのJST日ごとの永続停止、所有者と単調epoch。送信確保と同じSQLite書込トランザクションで競合を調停 |
| 既存入口 | X/Instagramの候補更新・承認カード作成・承認判断・送信確保・最終直前照合、Buffer経路設定・GraphQL書込みを停止中に拒否。停止中の旧承認通知を送らない |
| 下書き | Instagram非公開下書きの外部操作を先に日別leaseへ記録。操作中・成否不明は置換対象外。古い対象日不明の下書きも推測せず保留 |
| replacement_client | 計画上のprecondition・候補・P/D/C照会・原本/保存画像GETだけ。Bearer/no-store、リダイレクト禁止、404/通信失敗は不明のまま。Schema検証器なしでは通信0件 |
| 実画像取得 | 長さ・実SHA・MIME・Content-Digest・候補版・完全性・UTCミリ秒日時・鮮度・画像デコードを検証。独立画像点検の合格は生成しない |
| replacement_journal | P/D/CのUUID、対象日枠、停止epoch・所有者、送信予定の実バイトとローカルSHAを送信前に固定。一度送信開始した要求は照会専用。P/DのoutcomeとCのpersistenceを分離 |

既存のmina-write-v1・ih1/sv1/rv1を変更しない。新台帳のbody_sha256はローカルの実バイト保全用であり、新契約のanalysis_digest/candidate_version等を代替しない。内部ImageDescriptorはSchema変換後の内部型であり、提供側JSONの宣言ではない。原本画像の独立品質確認と、通信上の完全性確認を区別する。

## 有効化と永続停止

停止の明示取得はMINA_MEAL_REPLACEMENT_ENABLEDの既定falseで拒否する。自動取得や有効化CLIは追加しない。停止行が存在する場合は、設定をfalseに戻してもその保護を継続する。再起動・時刻経過・候補不採用を理由に停止を解除しない。

停止取得時に送信中・公開済み・外部受付済み・成否不明等のローカル証跡があればblocked_externalへ固定する。ローカルに何もなくてもheld/reason=external_inventory_requiredであり、外部予約が存在しないとの証明にはしない。外部取消・投稿再送は行わない。

固定アカウントは既存のサービス全体と同じ単一境界であり、呼出側が任意account IDを指定する機能はない。提供側の実アカウント・世代との照合は確定Schema接続時に追加する。

今回の段階には、停止解除、旧承認復帰、新版承認受領と解除の原子的な処理、P/D/CのHTTP書込み実装はない。新機能の本番有効化はできない。送信前台帳のstartは送信権の永続確保だけであり、費用承認や外部不存在の受入を意味しない。

## 確定契約を受領後の残作業

1. CONTRACT.schema.json・FIXTURES.json・EVIDENCE.md・AGY_REVIEW.md・MANIFEST.jsonと対象版を受領し、内容ハッシュ・入出力・エラー契約・検証結果を照合する。現在ある合成応答試験を提供側fixtureの受入試験と呼ばない。
2. UTF-16キー順・ECMAScript数値表現・-0・UTF-8・日時ミリ秒・Unicode・amount:nullを含む正規化を両言語の固定fixtureで検証する。既存Python json.dumpsのdigestをcv1/ad1へ流用しない。
3. precondition/P/C/current三者一致・候補内容版とcontrol_revisionを検証するSchema変換器と、画像の独立レビュー/Drive/栄養判定の証跡bundleを実装する。
4. Bufferの対象account/日を全ページ照会する外部確認を、既存のX+Instagram共通無料枠計数へ接続する。照会完了・対応付け・鮮度のいずれか不明ならblocked_external。新機能有効時は調停先を持たない単体Buffer書込みを拒否する。
5. meal_replan既存ordinalの移行、日枠最大2回、最初の開始時刻から15分、共通費用予約・SDK呼出上限・解析60枠を固定policyへ接続する。APIに未到達の画像生成試行も回数を戻さない。
6. P/D/Cの送信、応答喪失時のGET照合、D/C競合・画像PUT不明・保存後OGP不明等を隔離環境で検証する。不明時には新UUIDで迂回しない。
7. 新正本取得後、旧X/Instagram候補・旧カード・未送信経路を永続失効し、X4候補とInstagram8画像を再作成する。旧成果物を上書きしない版別保存と、独立点検・人間再承認後の可送信化を同じ日排他へ実装する。
8. Windows/Linux回帰・提供側QAとの接続受入・固定版レビュー・本番移行条件を満たしてから、新機能の配置と有効化を扱う。

## 18日分と無料運用の未確定事項

通常の翌日準備期限は10月17日20:00 JST。計画v2.1のhard_deadlineをそのまま適用すると、18日分は既に期限外である。先行実装で延長・時計変更・期限無視を行わない。18日分を救済する場合は、対象日・対象食・実行窓・回数・旧版保持・人間再承認を限定した復旧方針が必要。

費用上限・対象・既存承認範囲は未確定。ユーザーの無料希望に従い、今回の作業では有料API・画像生成・解析の外部呼出しを行わない。既存契約内の利用で実費0円と判断できる範囲も、提供側のbudget_policy_digestに結び付く確認を省略しない。0円や承認済みという仮値を作らない。

朝食484・昼食633を維持するなら、置換夕食の実解析683〜883kcalが日次条件を満たす。目標値に合わせた数値補正や品質判定の合格への書換えを行わない。

## 検証方針

実SQLiteで停止取得と送信確保の競合、X/Instagram旧承認の拒否、別日の維持、停止中のBuffer書込み0回、下書き送信不明と再起動後の保持を試験する。新APIは合成HTTP応答だけを使い、認証ヘッダの宛先・GET限定・404/302の一回停止・画像改ざん/欠落/期限切れ・JSON不正を検証する。新台帳はUUID入力不変、送信開始一回、P/DとCの分類分離、savedの保全、保存バイト破損時の拒否を試験する。

本番の台帳・サービス設定・稼働コード・17日の承認・18日の保存済み3食は、この先行実装では変更しない。

## 固定版の検証結果

コード版06a89fa、受領計画の末尾改行だけを整えた固定head449f4acb7e0bf52f21e50f90dae356ef38ad529cで、Windows・隔離Linuxとも全412件が成功した。新規は36件。Linuxは秘密なし・外部機能無効で123.620秒、稼働先の変更なし。サービス所有者からの配布コード固定版照合も成功。[CI39032524934](https://github.com/t-katsumata1027/marketing-agent/actions/runs/39032524934)もmina/test両ジョブ成功。[ドラフトPR #6](https://github.com/t-katsumata1027/marketing-agent/pull/6)は通知修正PR #5を基点とする。

配布物はactivation_allowed=false。bundleのSHA-256は585821ea0b9355854d865183d621f28ecfbce7c147d11fe4990f1085a15f18cf。本番へ切り替える配置スクリプトは作成・実行していない。実装の独立レビューと提供側契約fixtureの受入は未実施であり、今回の合成試験の成功で代替しない。
