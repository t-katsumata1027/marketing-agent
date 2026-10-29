# 10/29 正規化補修後の限定継続

本人は、限定継続入口の実装・検証・独立レビュー、最終Go/CI成功後の配置・実行を承認した。期限は10/29 14:30 JSTと新継続の初回+15分の早い方。朝昼夕の画像生成・既存画像点検は追加0、旧記録/費用/不明使用量/再計画消費は保持し、既存waivedを維持する。SNS予約/公開、Log-Eats本体変更は禁止。

## 継続の根拠

旧4a1eec83の限定caseで昼画像は新点検合格、夕画像もAI原出力は合格した。しかし食品index2のしょうが5gをAgentが誤拒否し、旧caseはheld、旧夕点検の正規化結果はdiagnostic_incompleteとなった。1749da35の補修で実夕16食品の純再評価は合格。旧AIのholdをpassに書き換えるものではない。

normalization_checkpointは旧toleranceの原本を引き継ぎ、19call・102files・旧case/旧判定・全監査を凍結する。旧3CLI（昼点検/夕生成/夕点検）の実Schema・入力画像・Journaldigest・送信stdin SHA・返却digestを照合。旧AI肯定/旧拒否と、補修validator版/新policy SHA/純再評価採用を別adoptionへ結合する。

旧朝合格、旧昼の新合格、夕の補修後採用を新しい3画像packetで参照する。旧result、画像、case、UUID、初回/期限は更新しない。

## 実行範囲と停止

一度限りの別normalization continuationは既定OFF。root所有承認3原本、固定コード、現在0食/日次なし、全状態全ページ外部予約、公開hold/所有世代、全旧原本のfresh確認を必要とする。旧heldをissuedへ戻さず、新caseの再発行もしない。

送信前のJournal.started/input_digestとcase-callは同TX。ai:image/review_imageと、structuredの画像工程を拒否する。許可するAIは未開始の保存後食品解析・既存一回訂正案・実OGP点検だけ。量の概算許容/警告原本/同policy SHAを維持し、画像由来holdでM1訂正に入らない。

未開始のDrive保存/再読取→通常M1三食初回登録/根拠付き既存一回訂正→実合計1800〜2000kcal→日次/実OGP→時刻未定X4/Instagram4案のDiscord送付まで進む。公開holdは送付後も保持する。実AI/解析等にはwaived方針で費用が発生し得る。無料/0円とは扱わない。

残60秒・一覧鮮度120秒、長い点検後の全外部再照合を維持。new unknown/hold/期限では原本を保存して停止し、M1元UUID/AI元call/Discord送付元の照合へ戻る。間に合わなければ開始/延長しない。

## 隔離受入

実工程/Fake外部で旧正規化拒否を再現し、補修採用から8案送付まで通す。旧原本保持、生成/既存画像点検追加0、構造化画像点検の直接拒否、旧raw改変、再発行/再起動/残59秒、食品保存応答喪失/Discord不明の再送禁止、OFF/status読取を検証する。管理専用checkpointの実本番原本RO照合も行い、全体QAの反復はCI一回に限定する。
