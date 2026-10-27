# v3固定契約の受入準備・追加承認事項

## 固定資料と実施範囲

Log-Eats担当から受領したPR157、head `06f43311a7fc8efa0c15cd29960cd3944ecca05c` のSchemaをGitHub APIから取得した。
実SHAは `baf76695c55298cd97fe8b24fc488c3c164585c11ae735986da380c4e52397ab`、報告と一致。
原本は `investigations/preparation-recovery-20261028/rescue-v3/` に保存した。旧v1/v2資料は変更していない。
Draft202012構造検査と合成fixture20件が成功、外部呼出0。runtime実装・本体実HTTP・実画像・実DB受入ではない。
本体CIは担当報告時点で未完。main/Productionは3ea42672、v3未配備・未有効という報告を維持する。

この準備は資料・fixture・実装設計のみ。Agent runtime・配置・flag・食事・課金処理・投稿は変更していない。
旧計画Goの実バイトは変更せず、この差分を追加レビューする。v3実装Goとして旧原本補修Goを流用しない。

## 旧計画から確定した差分

- endpointは `/api/internal/mina/rescue-v3`。別adapter・validator・source readerを設け、未知契約を通常置換へfallbackしない。
- 日付・朝昼夕を指定できるが、発行はDBのJST当日/翌日のみ。無制限な期限緩和ではない。
- 管理CLIが単一case・単一planned P・次ordinalを発行する。AgentにDB/管理資格を与えない。
- **1case1候補**。D、不受理、失効後の再case発行は不可。旧計画の「D後次候補」はv3には適用せず、不採用なら停止する。全履歴最大2回は追加候補を許可する意味ではない。
- 新flag `MINA_SCOPED_RESCUE_ENABLED` は既定OFF。旧朝食救済flagはOFFのまま。受入前の有効化は不可。
- basisへ旧原本/保存画像2SHA、target ad1、元context有無、他2食anchor/固定review、review policyを含める。
- 実context不在を両台帳で証明した場合だけfirst/hardをnullにできる。元20時準備期限は保持する。
- other meal reviewは正式strictキーのみ。OGP入力digestは24hex。任意evidence_digestだけを除外したcanonicalからdigest計算し、指定digest不一致を拒否する。余剰キーを黙って除去しない。
- 他2食の実独立点検は権限発行時120秒以内。発行後P/Cは同じ凍結reviewとnative rv/2SHA/ad1/OGP/policy不変をcase最大15分内で照合する。GETだけで点検日時を更新しない。
- 本人承認artifactはreview policy・Agent commit・実配備Body commit・Schema実SHAを固定する。現在のDraft headを将来のProduction commitに見立てない。

## adapter・台帳の実装設計

v1のTARGET差替えやroute置換はしない。新contract namespace/日/枠/caseをjob/request/event/cost・候補sourceの所有鍵とする。
旧v1イベントとsaved検索を横断しない。既存M1/通常置換/旧救済/新caseをまたぐUUID衝突検査を維持する。
case入力、planned P、実multipartバイト/境界/画像SHA、期待版・世代、認可revision、policy、承認artifactとreceipt原本参照を送信前に固定する。
grantは管理側発行、Agentは新鮮な読取結果との照合を行う。送信失敗/404/processing/unknownは元UUID照会のみ。D/C/取消の競合は本体の確定結果に従い、期限でunknownを解放しない。
操作予約は既存waived、旧生成/修復/登録/不明使用量と共通化する。無料や予算0と扱わない。原本Buffer削除unknown1を勝手に終端化しない。
同日hold owner/epochとclaim_dueをSQLite排他で検査し、外部全provider全状態全ページ/鮮度を実行直前に更新する。既存朝夕・通常M1を再登録しない。

Drive専用保存名/新FileID/原本保持・一度書込・再読取SHA、日次本文の新実値再構成と120字/重み110/280検査、日次UUID/result/rv出自、新契約公開前Readerを既存計画どおり実装対象とする。
Cのsaved後はOGP失敗でもsaved保持。全正本/実画像/本文/OGP再点検、新X4/Instagram4候補（8画像）とDiscord配信、新版再承認まで公開holdを維持する。

## 必須の隔離受入と未受入項目

1. 実native昼holdと元context不在の両台帳照合からbasisを作り、管理側合成grant→実adapter/実本体HTTPハンドラー→P→実認証画像2種→C→GET saved→日次/OGP→source verifierまで通す。
2. D成功後追加P/case再発行を拒否する。取消/C競合、失効、unknown、再起動、全履歴2回、日60、cooldown/probe/resumeを確認する。
3. 旧別日case混入、UUID衝突、original_context_presentの虚偽、旧期限/版/世代/2SHA/ad1/OGP/hold/policy変更を拒否する。
4. 正式reviewの余剰キー、24hex/64hex混同、digest不一致、同じ他食重複、対象枠混入、点検鮮度切れ、日時だけ更新、review policy相違を拒否する。これらの相互検証をSchema20件成功で代用しない。
5. 朝455/夕716を維持し、昼629〜829の実解析と日1800〜2000をC前・本体transaction・C後に確認する。現1799.4を丸めない。
6. Driveは実Port/実Clientを通して外部transportのみ隔離する。日次API出自、長文、source reader、Discord応答不明、別日公開巡回を回帰する。
7. integration証跡は固定Agent commit/Body commit/Schema SHA/fixture版/要求数/外部呼出数/独立レビューを保持する。Body専用Preview成功と最終CIを別に確認し、未完をGoにしない。

## 次に求める具体的な承認

### A：Agent実装と隔離受入

上記固定v3に対するadapter/namespace付き台帳と必要SQLite移行設計/source reader/Drive/日次補修、局所回帰・提供側実HTTP隔離受入、独立レビュー、PR更新まで。
実資格なし・外部書込みなし・実課金なし。本番配置・有効化・grant・P/D/C/resume・実食事・投稿を含めない。
CI待ちの間の資料/fixture準備は実施済みだが、新runtime実装はAの追加明示承認待ちとする。

### B：救済OFF配置

Agentの固定commit/CI、Bodyの固定実配備commit/専用Preview、Schema一致、双方の独立受入後に提示する。
SQLite追加の表/行・バックアップ・移行整合性・サービス確認・コード切戻しを具体化し、業務DBを巻き戻さない。新旧救済flagはOFFのまま。

### C：期限後限定実行

10/28昼の旧rv `rv1_da02d04212a74b2f85824fac0fa3d15538cab95fed508e6573c3462458695a78`、原本SHA `a98477b3f73361f4890b44197ecaeccc3dbfd217d0e64a78f80e15501ac3be4d`、保存SHA `76ddd161602cf28b3c3a99c470bd9741dbe6fce7386ab48ba41e17bf29f762cf` に束縛する。保存SHAは提供側20:52 GET報告であり、Agent実行前に再取得する。
管理側grant発行主体・単一case/planned P・同hold・実承認artifact・既存waivedの操作範囲・取消/切戻し・絶対終了を確定して本人へ提示する。
09:00 JSTは提供側の未承認候補であり、承認値ではない。実際の期限は本人承認not_afterと最初の権限発行+15分の最短。旧期限の変更や過去日救済へ転用しない。
承認対象操作は候補生成/解析/独立点検/新Drive、単一P・必要時DまたはC、GET照会、saved後の日次/OGP・候補/8画像・Discord通知を列挙する。
X/Instagram実公開、新候補のユーザー承認、旧3食の通常再登録・再訂正は含めない。本体DDL/正規配備/設定/grantの承認は担当側が具体提示し、この文書で代理承認しない。
