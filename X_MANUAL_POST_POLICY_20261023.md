# 手動X投稿を許容する翌日準備の恒久対応

2026-10-23。本人の「手動投稿があっても翌日準備を止めない、ただし自動投稿の消失・本文変更・成否不明の検査を維持」の承認に基づく。朝食画像の品質保留・保存済み一食置換とは別のコード修正。

## 本番反映

固定版：be109b3990450c950d9893f74f701d8681b8dd0a

基点：afd369c87fde43f71205d50e0c7dd3de8c08d512（UTM反映版を保持）

手動投稿を毎回bootstrapへ追記する運用を不要にした。X APIの固定本人アカウント・author ID・日時範囲・全ページ・重複・応答errorの確認は維持する。台帳外の投稿はID、投稿日、取得元、観測hashをmanual_postsへ分離し、通常の履歴JSONへ自動記録する。本文は生成資料へ追加せず、架空の献立や栄養値、投稿成功証拠を作らない。

既知の自動投稿・移行前確認履歴は、従来のreconcile_timelineで公開ID・URL展開後の完成本文・日付を厳密に照合する。別IDの手動投稿が同じ文面でも、自動投稿の欠落を補完しない。dispatching/outcome_unknownがある場合も従来どおり停止する。reconcile_timeline自体の厳密モードは変更していない。

長文note_tweetやメディアのある手動投稿も取得できるよう、標準自動投稿の本文検査を取得工程から照合工程へ分離した。自動投稿のIDに対する長文・不正なURL情報は引き続き拒否する。

DB migration・追加API書込み・bootstrap書換え・新機能flagの設定はない。原子切替と保護されたDB/設定退避を使い、AgentとGatewayだけを短時間再起動した。Instagram専用サービスは操作していない。

## 検証と独立レビュー

| 検証 | 結果 |
|---|---|
| 履歴関連の専用試験 | 26件成功 |
| Windows全体 | 448件成功 |
| サブPCLinux全体 | 448件成功 |
| 同一head CI39942180517 | mina / test 両ジョブ成功 |
| Codex独立コードレビュー | Go、重大残件なし |
| 配置operator独立レビュー | Go、重大残件なし |
| 本番配置コードでの隔離試験 | 7件成功、外部書込み0 |

通常CLIから実SQLite・工程を通し、手動投稿2件があっても画像3枚・通常登録4要求・4候補・4承認通知まで進むことを確認した。自動投稿の消失・改変・不正URL・送信中・成否不明、手動投稿の本人ID不一致や範囲外日時は引き続き停止する。

実X全件GETと実DBの私用コピーでも検証した。先ほど本人確認で追加済みの手動2行だけをbootstrapのメモリ内コピーから除いて元の障害を再現し、旧コードは拒否、新コードは手動2件を取り込んで履歴完全性に合格した。同じ実データで自動投稿のmissing/changed、私用DBのoutcome_unknownが拒否されることを確認。本番DB・bootstrap・食事・画像への書込みは0。

## 配置後の照合（19:59 JST）

- 業務25テーブルのハッシュ不変。周期更新するcycle_healthと追記監査operation_eventsは別扱いにし、既存監査行の全prefix一致も確認した。
- 設定はMINA_SOURCE_COMMITだけ変更、UTM true等の既存設定を保持。実Agent/GatewayのPythonプロセス2件が固定commitとUTM trueを継承。
- 3常駐サービスと3タイマーactive、SQLite quick_check=ok。
- bootstrap SHAはf0b25428423d714018004487f1592911e6ce7273d5b3cb08f07034005190124cで不変。
- 朝食画像SHAは15cbff4764b7741809ad0500f1f4fa6ed2da453f528c3da63ac0f9614a3ae61aで不変。
- 翌24日preparation=held:saved_recheck、朝食の旧保留と単回再点検の保留をすべて保持、候補0を維持した。準備再開・再点検・食事登録・画像生成・置換は実行していない。

保護された配置退避・照合receipt：/var/lib/mina-agent/deployment-manual-history-be109b3990

実履歴の照合証跡：/var/lib/mina-agent/incident-x-manual-policy-20261023

PR：https://github.com/t-katsumata1027/marketing-agent/pull/15

CI：https://github.com/t-katsumata1027/marketing-agent/actions/runs/39942180517

## 別件：朝食画像の品質保留

本対策は手動投稿を理由とする停止だけを解消する。10/24の承認案4件は画像品質保留により未完成のまま。

ユーザーからLog-Eats担当の報告を受領した。報告上の提供側配布版は66be0089cbdfb49150ba9271a7e25899e1d81483、正式Schema実バイトSHAは4f8d2be8f47a741ff8578a25c29bd6422cf88ebd817f8450339eaee76216f6e2。案Aは、朝食の保留を維持した永続日公開holdの下で他2食を通常登録・独立点検まで揃え、その後既存3食置換契約を使うという計画Goであり、実行Goではない。

今回、案Aの実装・設定・登録・置換・受入フラグ・費用policyは操作していない。費用は既存waivedの承認範囲と予約方式の確認が残り、100円を現行上限として補完しない。現行期限20:00を過ぎて10/24分の初回時刻や期限を戻さない。将来日・費用方式・両側契約受入の計画と実行承認は別に必要。