# 10/30投稿準備を阻むOGP障害の調査依頼

本人承認「担当へ調査と修正計画を依頼する」に基づき、2026年10月30日00:30頃、担当チャット「みなの栄養目標をAPIへ追加」へ依頼済み。調査・計画・独立レビューのみで、本体実装・本番設定/DDL/DML・配備・食事/日次再POST・SNS操作は未承認。担当は読取調査の開始を回答した。

## 確定した事実

- 対象10/30の朝552、昼723、夕692、実合計1967kcal。登録/訂正六要求は保存完了。日次一要求を追加保存し、七要求すべてcomplete。旧食品・画像・保留を保持。
- 日次共有は https://www.log-eats.com/s/q27-vn7p 。revisionは `daily-collage-goal-v6-p3-rebd7ff4de2930e81103cdccb-s178ea82317e038a8`、PNG SHAは `bc4b76e9acdfb4ea0903bf8cc9a85190ccd5b0467db9ff5112b50389932ed03a`。実画素は昼夕二枚で朝食欠落。CLIのexpected3/observed2と別Codexの実画像確認が一致。旧日次OGP holdを維持。
- 固定f4aee73の読取では、日次rendererが三URLを変換後null除外し、一枚以上残ればready PNGを作れる。p3/入力digestは変換前、保存SHAは完成PNGの整合性なので、写真欠落を見逃せる。ただし現在Production aliasの固定版と事故時の朝写真変換失敗理由は未確認。
- 朝昼夕六案を先行する実行では、最初の朝食share HTML由来og:image GETがHTTP500となり、候補登録前に停止。朝URLは https://www.log-eats.com/s/nnaoq2yr 、昼は https://www.log-eats.com/s/nqduhfsx 、夕は https://www.log-eats.com/s/7uvqwtlx 。担当への最初の依頼で昼URLを誤記したが、直後にこの正しいURLと原本優先を訂正送信した。
- 追加RO診断では三HTMLは200、朝HTML画像は200、昼夕HTML画像はtimeout、朝API画像はtimeout、昼API画像は200/8192bytesだがM1期待SHAと不一致、夕API画像は200/期待SHA一致。timeoutを成功/404/未保存へ丸めない。500の原因や8192bytes画像の内容は未確定。

## 担当への依頼範囲

1. 現在のProduction project/alias/commitを取得し、日次写真欠落と個別画像の500・timeout・alias SHA不一致を別々に切り分ける。
2. 採用原本URL/画像の取得・署名・HTTP・容量・decode、変換結果、PNG/ready/格納/配信の安全な理由コードを確認。GET専用署名へHEADを流用しない。秘密や署名URLをログへ露出しない。
3. 期待数と変換成功数の完全一致、partial PNGのready拒否、canonical/HTML同版同SHA、500/fallbackの修正を最小計画化。枚数・品質ゲートの緩和では解決しない。
4. 既存readyの正規な対象限定回復を別計画化。同revision GETで再描画されると仮定せず、immutable新版、M1/旧sv1・rv1、履歴、Agent再照合/再承認への影響、必要な権限・費用・対象・期限を具体化。食事や日次の再登録で迂回しない。
5. agy、利用不能時は本人承認済みの別Codex代行で計画への独立Goを取得。その後本人へ説明し、実装・本番反映・実回復の具体的承認を求める。計画Goを実行Goへ読み替えない。

## 原本

私用領域 `C:\project\marketing-agent\tmp\preparation-recovery-20261028` の `next-daily-ogp.jpg`、`next-daily-ogp-diagnostic.json`、`next-current-state.json`、`next-ogp-alias-readonly.json`、`next-final-status.json`。署名や資格原文は含めない運用で担当へパスを渡した。

## Agent側の状態

本番f1c57c0b、CI40899869833成功、独立45件と実原本DBコピーCLI通し成功、切替123表全行不変。日次保留を残し、六memberに限定した通常承認/個別公開leaseを実装・配置済み。ただし本番のOGP受入が失敗し、承認候補0・新承認カード0、配信保存/SNS送信0。旧next_saved/next_meal caseはheld、旧初回と00:30期限は変更せず、期限経過後の再起動なし。三サービスはactiveへ復帰。
