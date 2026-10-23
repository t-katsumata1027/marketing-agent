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

## 2026年10月23日の最終結果

- 固定実装afd369c87fde43f71205d50e0c7dd3de8c08d512を本番反映し、MINA_X_UTM_ENABLED=trueを設定。3プロセスが同じcommitとtrueを継承したことを確認した。
- 専用9件成功。Windows全体438件成功後、追加2件を含む専用9件を確認。隔離サブPCLinux全440件成功。CI39914480453のmina（440件）/test両ジョブ成功。
- 新コードから23日4枠すべてを読取検証し、現在正本・API完成本文・UTM付き実OGPを照合した。元候補のdigestと行は不変。投稿要求0件。
- 切替時は26台帳が不変。起動後も25台帳と既存監査行は不変で、operation_eventsへ起動startedが2件追加された。cycle_healthは別途更新、live・連続失敗0。SQLite quick_check=ok。
- 3サービス・3タイマーactive。既存23日承認・公開記録を保持し、以後作成する候補から適用する。初回の自然準備・本人の新承認・UTM付き実公開は今後の確認事項。
- 保護バックアップ：/var/lib/mina-agent/deployment-utm-afd369c87f。台帳は巻き戻していない。
- PR #13：https://github.com/t-katsumata1027/marketing-agent/pull/13。ドラフト維持、mainへマージしていない。
