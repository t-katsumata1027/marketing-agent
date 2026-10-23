# Instagram定期投稿の運用

## 構成

既存X用の同日成果物とLog-Eatsの読取APIを利用する。追加の料理画像生成・食事登録・再解析は行わない。公式ロゴ、M PLUS Rounded 1c、献立、カロリー、PFCを固定合成し、日次の最後に現在の登録目標との達成率を表示する。

Xと同じMINA_DATABASEを使用し、Buffer要求数を合算する。Instagramの承認候補・試行・通知は別テーブルに保存する。既存Gateway一つで両方のボタンを処理する。

## 初回の手順

1. CIに成功した固定commitの配布を検証し、既存台帳・設定を整合退避してから配置する。既存X候補・承認・要求・公開経路の行ハッシュを配置前後で確認する。
2. instagram.env.exampleを専用設定へ配置し、最初は4項目ともfalse。既存Gatewayへmina-gateway-instagram.conf.exampleをdrop-inとして追加する。
3. 完成済み実データの日付を指定して、次の非公開検証を実行する。acceptは過去日でも使えるが、公開候補を登録しない。新規合成画像だけをDriveでリンク閲覧可能にする。

```sh
/bin/sh /opt/marketing-agent/deploy/run-mina-instagram-doppler.sh accept --date YYYY-MM-DD --allow-public-media
```

4. storyと5枚カルーセルのdraft状態・自動配信方式・対象・本文・画像順・メタデータ・再読取が一致したことを確認する。成否不明のcreatePostは再作成しない。受付IDが既知なら読取だけで照合する。
5. MINA_INSTAGRAM_PREPARE_ENABLED、MINA_INSTAGRAM_NOTIFY_ENABLED、MINA_INSTAGRAM_APPROVAL_ENABLED、MINA_INSTAGRAM_PUBLISH_ENABLEDをtrueにし、既存Gatewayを設定再読込のため再起動する。MINA_LIVE_WRITESの既存設定も必要。
6. mina-instagram.serviceとmina-instagram-prepare.timerを有効化する。次の20:10 JSTから翌日分の4件を準備し、Instagram専用承認カードを送る。既存のX用準備は19:00、X承認通知は20:00のまま。

## 時刻と承認

07:30、12:15、19:15はストーリーズ、21:30は5枚の日次カルーセル。本人がInstagram専用カードで承認した版だけを予定時刻にshareNowで送る。Xの承認を転用しない。過去日のprepare/saveは拒否し、遅れた投稿は自動で追いつかせない。

## 停止と照合

公開前の正本・投稿先・画像の検査に失敗した枠は保留。送信開始後の成否不明は後続Instagram枠も止め、自動再送しない。再起動後も同じ。

```sh
/bin/sh /opt/marketing-agent/deploy/run-mina-instagram-doppler.sh status
/bin/sh /opt/marketing-agent/deploy/run-mina-instagram-doppler.sh reconcile
```

reconcileは保存済み受付IDだけをGETし、createPostを使わない。受付ID不明の場合はBufferの画面で人が照合する。失敗通知の二重送信も避ける。

MINA_INSTAGRAM_PUBLISH_ENABLED=falseで配信を停止できる。既存XのMINA_PUBLISH_ENABLEDと区別する。サービスや設定の復帰だけで、保留・不明・失効済み承認を公開可能へ戻さない。
