# 10/28遅延投稿案の準備専用対応：独立レビュー

## 判定と範囲

**固定コード・隔離受入Go。条件付き救済OFF配置operator Go。**
未対応の重大指摘はない。配置のGoは下記の実照合条件を満たす場合に限る。
実行windowの採用、有効化、管理権限発行、生成/課金、P/D/C/resume、Discord送信、X/Instagram公開の承認ではない。

- 日付：2026-10-28 JST。
- 独立担当：Codex `/root/review_x_comment_fix`。実装担当とは別担当。
- 固定Agent head：`1c5224b6d0fe1670ee9b874b08688eeac42da12f`。
- 本体HTTP pin：`f4aee73ecc9ad2c784c6196fb4d04009812037d6`。
- v3正式Schema SHA：`baf76695c55298cd97fe8b24fc488c3c164585c11ae735986da380c4e52397ab`。
- 配置operator SHA：`005597e5f63a67c5489396c2a506a3a8b4ccc8ffb7f7e67e16e2a93170e6cf43`。

今回変更したのはこのレビュー記録だけ。本体・コード・本番への変更、operator実行、外部書込み・実課金は行っていない。

## 独立検証

次の対象を固定版で独立実行し、**5件成功、失敗0、67.274秒**。

- `tests.mina.test_scoped_preparation` の3件。
- `test_late_preparation_full_chain_never_releases_or_claims_even_after_approval`。
- `test_native_hold_to_real_operator_c_finish_eight_images_and_all_source_verifiers`。

固定Git archiveの実本体ハンドラーへlocalhost HTTPで接続し、DB・AI・Drive・公開素材transportを合成した。各HTTP試験の外部ネットワーク試行0も確認した。
全既存試験を再実行した結果ではない。実UID Linux受入、最終CI、配布物362ファイルの結果は実装担当の別証跡であり、この独立実行の件数には加算しない。
配置operatorは読取とAST構文検査を行った。Linux上の配置・停止・復帰処理自体は実行していない。

遅延側の通しでは、元3食/native保留から実operator・P・画像・C・日次・X4/Instagram4候補/8画像と、実InstagramRuntime.verifyまで確認した。
全候補に合成ユーザー判断を与えてもholdは解除されず、通常publication_holdsを隔離SQLでreleasedへ変えても送信claimは成立しない。
さらにmarkerのcase/window SHA/提案時刻の改変を拒否し、全候補からmarkerを削除した後も永続ロックにより最終claim・Buffer mutation・Instagram下書きを拒否し、CREATE呼出し0を確認した。
通常側の一本も成功し、従来の期限後/OFFの読取承認照合が今回の準備専用例外と混同されないことを回帰確認した。

## 指摘3点の反映

1. **最終送信境界**：X/IG claimだけでなくrequire_claim、BufferPublisher、Instagram request_for/draft_once、Buffer mutation fenceでも拒否する。候補の時刻表示を公開許可にしない。
2. **marker消失・改変**：scoped_preparation_locksへ日/case/owner/epoch/window原本/候補digestを保存する。held・require_clear・maybe_release・無対象Buffer fenceも永続ロックを照会する。payloadからmarkerが消えても公開可能へ戻さない。自動解除・公開候補への変換経路は追加されていない。
3. **Instagram源照合**：実InstagramRuntime.verifyが永続windowと同slot X markerへ一致を要求する。余剰fieldを単に除去して合格へ寄せない。Rebuilderの読取照合とも整合する。

準備例外は10/28昼食、当日の本人execution_approval原本と完全一致するwindowだけ。元の期限・UUID・初回・累積試行、本体のcase最大15分を延長しない。
提案時刻は当日内・順序固定・重複なし・準備期限後の値として検査する。実行許可が未取得ならwindowや課金へ進まない。

## OFF配置operatorの確認と条件

`scripts/deploy_mina_scoped_off.py` は次を維持する。

- 旧HEADとMINA_SOURCE_COMMITを `a34005efd50afa5b75431f2baee05471ffa8027d` に固定照合する。依存ファイル変更、既存退避先、実行中oneshotは拒否する。
- 新旧救済flagをfalse、新config未設定に保ち、通常収集・UTM・既存Instagram設定を保持する。
- 元active timerと3サービスを停止し、inactive/failedを実読取する。停止後とrename直前にもoneshotの非稼働を確認する。
- 設定/SQLiteを保護された退避先へ保存し、quick_checkと全既存テーブル行digestを取得する。
- 追加するのは空のscoped_rescue_jobs/requests/events/callsとscoped_preparation_locksの5表だけ。権限・job・要求・候補・食事・承認の新行は作らない。追加集合/件数/全旧行digestの不一致は停止する。
- 配置・設定は退避と原子切替を使い、部分移動後の例外は旧コードへ復帰する。DBの事実は巻き戻さず空の追加表を残す。
- 復帰時にtimer起動済みのoneshotがあれば強制killせず待ち、停止できない場合はコードを動かさず復旧未完として固定停止する。復帰済みと未完を区別し、旧HEAD/設定/元active unitを検証する。

実配置前の条件は、当該固定版の最終CI・Linux受入・配布物検査の成功、承認対象COMMIT/配布物の一致、稼働旧HEAD/設定の一致、非稼働oneshot、適切な退避先を実読取で確認すること。
配置後は実processのcommit/新旧救済OFF/config未設定、既存設定、3サービス/元active timer、業務台帳保持と新cycle正常を別途確認する。
このoperator Goを、配置済み・復帰試験済み・本番の投稿案完成と報告しない。

## 本番実行の残事項

本体の正規配備/API状態、OGP24hexの正規取得と原本、両固定版、実hold/台帳/旧rv/2SHA/ad1、実execution_approvalとwindow、既存waived費用の対象操作、単一case/planned Pを揃える必要がある。
今回の本人承認は実装・隔離検証・独立レビュー・救済OFF配置まで。実window/生成課金/食事置換の実行は別の明示承認が必要。
準備専用候補は、通常のユーザー承認やhold変更だけで公開へ移行しない。公開・予約・下書き送信には別の具体的な計画・承認・受入が必要。

## 対象差分の実バイトSHA-256

Windows作業ツリーの実バイトで算出し、改行の正規化は行っていない。

| ファイル | SHA-256 |
|---|---|
| `MINA_SCOPED_PREPARATION_20261028.md` | `e8a016825f2fa0af8fb443727fdde1679bf8cb12db4ac121d3ff442a202add9b` |
| `scripts/deploy_mina_scoped_off.py` | `005597e5f63a67c5489396c2a506a3a8b4ccc8ffb7f7e67e16e2a93170e6cf43` |
| `scripts/mina_scoped_rescue_http.mts` | `ce17864cf7c570c865a07317ec88db24a794619e7044456d1aa0e6e0457869c5` |
| `src/mina/buffer_publisher.py` | `86638c49fd894f3bf102302c2ce7745dfbcdf04e6a8f00a07a05cb52a5c28fe9` |
| `src/mina/domain.py` | `c7d4b463c27c0e41a9da5bc39d698783ceeb43db5f452afafb2ccecca5c8bf50` |
| `src/mina/instagram_domain.py` | `1ff16907d3a7e3533de01fa5840224ee14d7f2ed6ff0fff79c3801037d2b23ba` |
| `src/mina/instagram_publisher.py` | `07880911ab580862d724e03210a30eef712cc86c3d786d6129bcce9ea25920e4` |
| `src/mina/instagram_runtime.py` | `f2aeace745ad814b27518913a21dd41524c7a1b86e717b243268297d14383710` |
| `src/mina/instagram_store.py` | `194fd27f49d762b975ae9d44b9694daaa80c7184852e4cb13d1c2a9f787f4ab3` |
| `src/mina/publication_hold.py` | `6356daafd65642a4b4a97ac81a68e54e982f4910011b48edb3cd60c65a62d1f1` |
| `src/mina/readiness.py` | `7c191b9f5ac35438592d4ece033a01d0ad82fe7e314b1090680068fee4dcd06e` |
| `src/mina/replacement_bundle.py` | `339d74b88cb9516a7fdcf288e296e10c76afb02973c3bd71b91c9340451e3dcd` |
| `src/mina/replacement_rebuild.py` | `5c78bfdcb0b1471ddfb96b877216ca8d7b57d471b16ea32a3640f30b859d3822` |
| `src/mina/scoped_preparation.py` | `927702d139b49f7c2c69f4e45fac1794041e043c91fa3b8b31676b0f9ea05570` |
| `src/mina/scoped_rescue_flow.py` | `885405a2f2d3723f161042c021e62707a01d2ffba460ceb306d990a5921aec86` |
| `src/mina/scoped_rescue_reasons.py` | `7074d956458a360af17d8e5c3053abb00a0877af7f41d40e9b2a88a34102be81` |
| `src/mina/scoped_rescue_runtime.py` | `a25508123664ae36fceed8b3e990933d5640346103b727560a1b5cfc39895b2f` |
| `src/mina/scoped_rescue_storage.py` | `db986b4ecc71eef658b95b204cb26d862fa161c958b773f3239cfa37f8cbb599` |
| `src/mina/store.py` | `e84c64c5120eab678487e8da465f5f704efb86c593da302d8d72ac193344e9c1` |
| `tests/mina/test_scoped_preparation.py` | `19323f724ac8f5e163ab4c6042db798a4ba549e970b64176995b79ef01412575` |
| `tests/mina/test_scoped_rescue_http.py` | `5ce4b559911697f91890a9fbeb6c2862c7a265b882256e5484341d54cbb0a675` |

## 配布operator改行差の追加照合

固定head `1c5224b6d0fe1670ee9b874b08688eeac42da12f` のGit blobを直接読み、実配布LF版operatorのSHA-256が `984c08aaf2742607291de565448aefdda30b7ad057aea171353a56b86d59030d` であることを独立確認した。
上記Windows作業ツリー版SHA `005597e5f63a67c5489396c2a506a3a8b4ccc8ffb7f7e67e16e2a93170e6cf43` との差はCRLFからLFへの変換だけで、正規化後は全バイト一致する。Git blob自体のAST構文検査も成功した。
したがって同じ条件付きOFF配置operator Goをこの固定Git blobへ適用する。実送信ではLF版のSHAを検証し、未照合の第三のバイト列へ読み替えない。配置wrapperがSHA不一致で送信前に停止したことは担当報告であり、本レビューで本番状態を再取得したものではない。
