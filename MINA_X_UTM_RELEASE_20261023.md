# X共有URLのUTM付与・本番反映

2026年10月23日。本人がXからの開始を選択した。対象は新規X候補であり、既存候補・承認・公開台帳を更新しない。

## 仕様

- utm_source=x
- utm_medium=social
- utm_campaign=mina_daily
- utm_content=YYYY-MM-DD_投稿枠（breakfast/lunch/dinner/daily）

MINA_X_UTM_ENABLED=trueのとき、通常の候補作成時に固定policyをapproval_sourceへ保存する。APIのshare_url・publish_text・result hashは正本のまま保持する。公開本文は末尾の共有URLだけへUTMを付け、Discordのカードにも同じ完成本文を表示する。本文の計測設定は候補digestに含まれ、後から環境設定を変えても既存候補の本文・digestは変わらない。

公開前には従来の正本・実OGP照合に加え、UTM付きURLも読み取り、転送先、全OGPメタ項目と画像実体が正本と一致することを検証する。未知のpolicy、元URLのquery/fragment、本文の不正な重複URL、別共有先や異なる画像は拒否する。Instagramは変更しない。

## 検証と配置

本番23日分の3食の共有ページを読取だけで確認し、UTM付きでも実OGPと転送先pathが一致し、UTMが保持された。外部書込み0件。配置・CI・両OS試験と台帳不変の最終結果は追記する。

## 適用・停止

新しい候補の作成から適用する。既に生成・承認された23日分へは遡及しない。設定をfalseへ戻すと以後の新規候補への付与を止める。既に承認したUTM付き本文を変えるには新しい候補版と再承認が必要。URLの付与成功は、遷移先での分析基盤・コンバージョン計測の受入を意味しない。
