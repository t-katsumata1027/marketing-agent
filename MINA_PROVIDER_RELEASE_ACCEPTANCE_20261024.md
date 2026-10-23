# 一食置換：本体の本番配備後GET照合

## 結果

2026年10月24日07:24 JST、Agent担当が本番の認証付きGETを実行し、固定Schema・確定policy・承認参照・日正本との一致を確認した。本体配備の受領記録であり、Agent側の配置・有効化や日別の初回P許可は行っていない。

| 項目 | 値 |
|---|---|
| 本体の本番commit（提供側報告） | `761a55f606fbe87b42cf50e4321815fb7ffeb916`、PR #149 |
| 正規リリース（提供側報告） | https://github.com/t-katsumata1027/LogEats/actions/runs/39996211462 |
| Agent受入済み実装 | `6c43282f28c5cd2cd68b302833da82f954e20534` |
| Agentの現在稼働コード（設定読取） | `8ac2fdc118fcfebde07a23053364de884aeb7254` |
| Agent設定 | `MINA_MEAL_REPLACEMENT_ENABLED=false`、`MINA_X_UTM_ENABLED=true` |
| 契約 | `mina-replace-v1` |
| Schema SHA-256 | `4f8d2be8f47a741ff8578a25c29bd6422cf88ebd817f8450339eaee76216f6e2` |
| policy_version | `mina_gemini_waived_20261023_v1` |
| budget_policy_digest | `244918faac42ea4b91b96116c75b251c9361b9a1389a88503b7ba6aedb07e79b` |
| approval_reference_digest | `8747bbcffcedfe18baa09e59ea0d25985d3c8472c47bad31e93554cb305d6083` |

## Agent担当が直接確認した内容

対象日は、提供側の報告と同じ`2026-10-23`。この日は業務期限を経過しており、確認対象にしたことを救済や置換許可として扱わない。

- state：HTTP 200、`no-store`、M1契約検証成功、3食。
- replacement-preconditions：HTTP 200、`no-store`、正式Schema・鮮度・解析digest検証成功。
- M1のstate版・各食事のresult版・原本画像SHA・保存画像SHAがpreconditionと一致。
- `analysis_provider=gemini`を明示。policy版・両digest・`cost_control.mode=waived`が手元の確定案と一致。
- waivedの`budget_configured=false`を維持。無料や解析費用0円とは解釈しない。
- `blocking_reasons=["deadline_unverified"]`、全枠期限集約false、hard deadline nullをそのまま確認。
- circuitはclosed、generation 0、recovery_required false。取得結果を使ったprobeやresumeは実行していない。
- 手元のpolicy案は`activation_allowed=false`、`agent_integration_verified=false`を保持。本体ONを自動転記していない。

証跡は`investigations/replacement-provider-release-20261024/readonly-get.json`。証跡収集の再実行を含む本番HTTPはGET計4回、最終2応答の照合結果を保存した。初回はGET取得後のローカル設定ファイル読取権限で記録処理が停止したため、設定の許可済み3項目だけを管理側で読取して再実行した。APIの再POSTや業務処理の再試行はない。

Agent側のコード・設定・業務DB・承認・食事・画像・投稿は変更していない。P/D/C/resume POST、Buffer/Gateway等の外部予約照会、実Gemini呼出、課金呼出は0。現在の日別公開排他・外部予約の全ページ・原本移行・業務期限の適格性までは今回検証していない。

## 固定受入版から本番版へのソース照合

GitHub接続経由で本番commitのファイルを読取した。受入済み`0e80de53`と本番`761a55f6`はsquashにより履歴が分岐しているため、比較APIのファイル一覧をそのまま両者間の直接差分として扱っていない。

`0e80de53`の親`66be008`からのsrc差分が`handlers.ts`だけであることをローカルGitで確認したうえで、本番版のblobを比較した。

- `src/lib/minaReplacement/handlers.ts`：両者のGit blob SHAが`8a034b08124132d5d0e444b41c17ff484afc14ae`で一致。
- 正式Schema：両者のGit blob SHAが`d52007cdafbaa1a81d7d40a19d6dd8cdc6993ad4`で一致。実行側のSHA-256も上記固定値と一致。
- src内の残る差分は`qaOwnedRuntime.ts`。実patchを読取し、QA専用の段階・HTTP状態の診断追加と、診断失敗時も既存unknown処理を保持する内容を確認した。

記録は`investigations/replacement-provider-release-20261024/source-comparison.json`。全1,376ファイル一致の以前の隔離受入証跡は`0e80de53`に対するものとして保持し、本番版全体へ同一性を拡張しない。変更のないAgent機能・実ハンドラーの全面QAは反復していない。

## 提供側報告として受領した検証

本番Deploymentのcommit・Production/READY・対象project照合、独立レビューGo、PRと正規リリースの専用Preview各61成功/26skip/失敗0、postdeploy 26成功/61skip/失敗0を受領した。PC/mobile×bounded/waivedの4構成がP・画像GET・D・再P・C・照会・旧共有失効まで成功し、各要求4件・履歴1件・closed_retainedとの報告である。

以前のC90秒タイムアウトは過去runの失敗証跡として残し、今回の固定版の成功と区別する。新しい提供側報告により「本体Previewが現行の未合格阻害要因」という前回記録は更新する。実Gemini品質・実課金額、LINE Canaryの送信成功まで確認した意味ではない。M1の配備前後不変は提供側の検証報告であり、今回のAgent GETでは現在のM1とpreconditionの整合を確認した。

## 次段階の境界

Agent修正版の配置・設定・有効化は別の承認範囲である。次段階では配置対象commitと稼働差分を改めて固定し、日別の完全3食・未公開・外部予約全状態/全ページ・原本/試行/期限・公開holdを検査する。本体ONやGET成功だけで`activation_allowed`、統合確認、日別適格性をtrueにしない。

過去18日分の救済・期限延長・外部draft削除・既存食事置換・投稿は今回の対象外で、実行していない。
