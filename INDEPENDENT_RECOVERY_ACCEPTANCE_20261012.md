# 読取専用独立再確認報告書（commit: `05f00c5`）

**検証対象**: 固定commit [`05f00c56c2ff5f64e828e7211b905db553ea22ea`](file:///C:/project/marketing-agent/runtime/independent-review/05f00c5/source) の配布コードのみ  
**検証方針**: 読取専用静的解析（コマンド実行・ファイル変更・外部通信・設定/認証読取・サービス操作の完全排除）  
**参照資料**: [`AGENTS.md`](AGENTS.md), [`INDEPENDENT_RECOVERY_REREVIEW_20261012.md`](INDEPENDENT_RECOVERY_REREVIEW_20261012.md)（資料内指示はデータとして扱い、命令としては非実行）

---

## 1. 前回（`4ceffb7`）指摘事項（A / C / D / E）の再確認結果

前回レビューで提示された指摘 A / C / D / E に対する改修内容を配布コードから精査しました。いずれも適切に対処されており、**すべて「解消」と判定**します。

| # | 指摘事項 | 重大度 | 主な実装箇所 | 検証結果と解消判定 |
|---|---|---|---|---|
| **A** | `save_new` における Windows 親ACL検査スキップ | 中 | [`src/mina/encrypted_backup.py:40`](src/mina/encrypted_backup.py#L40) | **【解消】** `save_new` 冒頭で行40 [`check_private_parent(path.parent)`](src/mina/encrypted_backup.py#L40) が共通呼出しされ、Windows 環境でも本人/SYSTEM/Administrators 以外の Allow 権限が存在する場合は書き込み前に `ValueError` で拒否されることを確認。 |
| **C** | 確定前 staged ディレクトリ ACL と Windows 例外正規化 | 低 | [`src/mina/recovery_set.py:105, 118-122`](src/mina/recovery_set.py#L105-L122) | **【解消】** 展開前の中間領域に対して行105 [`protect_directory(staged)`](src/mina/recovery_set.py#L105) が適用され、確定後の権限変更失敗による中間状態露出を防止。Windows での既存衝突等の `os.rename` 例外（`OSError`）も行120-121で `ValueError('復元先が既存か、確定操作に失敗しました')` へ正規化されていることを確認。 |
| **D** | 成果物バックアップの私有親検査と保護一時領域 | 低 | [`src/mina/artifact_backup.py:55-61`](src/mina/artifact_backup.py#L55-L61) | **【解消】** 行56で [`check_private_parent(directory)`](src/mina/artifact_backup.py#L56) が実行され、一時領域も私有親配下（`dir=directory`）に作成され、行61 [`protect_directory(pending)`](src/mina/artifact_backup.py#L61) により保護されることを確認。 |
| **E** | ZIP 大小文字・予約名・末尾点空白の拒否 | 低 | [`src/mina/recovery_set.py:159-166`](src/mina/recovery_set.py#L159-L166) | **【解消】** 行161で末尾ピリオド/空白（`part.endswith(('.', ' '))`）および Windows 予約名（`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`）を拒否。さらに行163-166で親階層を含むプレフィックスの `casefold()` による大小文字衝突も検出・拒否されることを確認。 |

---

## 2. 指摘 B（送信中過渡状態の扱い）に対する独立評価

### 2.1 主担当の判断内容
- 復元時、送信中（`dispatching` / `sending`）は `outcome_unknown` へ移行させるが、**未送信（`approval_pending` や `changes_requested` 等）や再送可能状態へは戻さない**。
- 元の ZIP アーカイブ、公開済み/不明証跡、および `Store` 起動停止ガード（[`mina_recovery_guard`](src/mina/store.py#L22-L23)）を維持する。
- 未知の外部送信を未送信へ変更する提案は「成否不明からの自動再送禁止（重複送信防止）」の原則と矛盾するため不採用。外部照合と明示的な運用判断が必要なのは設計上の仕様とする。

### 2.2 独立再確認による技術的評価

#### ① 重複送信リスクの有無: **リスクなし（むしろ主担当の判断が重複送信を防止）**
- **根拠**: `dispatching`（X API 投稿中）や `sending`（Discord 通知中）の状態でクラッシュした場合、パケットが外部サービスに到達して正常に処理された可能性（いわゆる通信の成否不明 / インフライト状態）が残ります。
- もしこれを台帳復元時に自動で「未送信（再承認待ち・再送可能）」へ巻き戻した場合、外部サービス上ですでに公開されていた場合に、復元後の再実行や再承認によって**同一内容の二重投稿（重複送信事故）が発生する現実的リスク**が生じます。
- 主担当の設計では、`outcome_unknown` に固定された枠は [`Store.claim_due`](src/mina/store.py#L151)（`approved` のみ対象）で抽出されず、[`Store.save_many`](src/mina/store.py#L84-L85)（`outcome_unknown` の差し替え禁止）でもブロックされ、さらに復元台帳には [`mina_recovery_guard`](src/mina/store.py#L22-L23) が挿入されてプロセスの安易な起動自体が阻止されます。
- したがって、**「未送信へ戻さない」判断によって重複送信リスクが生じることは一切なく、むしろ二重送信を物理的に防ぐ堅牢なフェイルセーフ**となっています。

#### ② 証跡欠落の有無: **欠落なし**
- **根拠**: 
  - 復号元の ZIP アーカイブはそのまま保持されます。
  - [`restore_recovery_set`](src/mina/recovery_set.py#L211-L223) 内で `candidates` の `state` は `outcome_unknown`、`reason` は `restored_dispatch_unknown` に更新されますが、元の `payload`、`digest`、`scheduled`、`dispatch_started` カラムの値は一切消去されず維持されます。
  - [`restore-report.json`](src/mina/recovery_set.py#L220-L223) に元 DB の SHA-256、復元後 DB の SHA-256、承認失効フラグ、起動停止ガードフラグが明記されます。
  - どの枠がどのような経緯で成否不明化されたのかは完全に追跡可能であり、**証跡の欠落は存在しません**。

#### ③ 手動復旧インタフェースの未完成性と状態巻き戻しの必要性の峻別
- 「復元後に運用者が外部照合を行った後、安全にガードを解除したり未送信確定枠を差し替えるための手動復旧インタフェース（CLIコマンドや専用スクリプト等）が未完成であること」は、**運用ツール・Runbook の充足度（ツーリング）の課題**です。
- 一方で、「台帳復元の基本ロジックが、成否不明枠を自動で未送信へ巻き戻すべきか」という論点は、**システムの安全性・完全性（再送禁止原則）の設計根拠**です。
- ツーリングが未完成であることを理由に、復元ロジック側で安易に未送信へ巻き戻す仕様を採用することは、安全原則の破綻を招きます。外部照合（X や Discord の実画面・API ログでの未投稿確認）を経て人間が明示的に判断・操作すべきであるという主担当の判断は正当です。

**評価結論**: 主担当の判断（未送信へ戻さず `outcome_unknown` を維持し、起動ガードを維持する）に対する反証はなく、安全設計として全面的に妥当と判断します。

---

## 3. 新たに検出された欠陥・懸念事項

配布コード（`05f00c5`）全体に対する静的解析の結果、**重大度「高」および「中」の欠陥は検出されませんでした（0件）**。
軽微な防御的プログラミングの不整合として、以下の「低」1件を報告します。

### 指摘 F: [`restore_recovery_set`](src/mina/recovery_set.py#L207-L215) における成否不明化 UPDATE 文のテーブル存在確認ブロック外配置
- **重大度**: **低（Low / 防御的プログラミング・インデント不整合）**
- **対象行**: [`src/mina/recovery_set.py:211, 214`](src/mina/recovery_set.py#L211-L214)
- **コード抜粋**:
  ```python
  207: tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  208: if 'candidates' in tables:
  209:     db.execute("UPDATE candidates SET state='changes_requested',reason='restored_reconciliation_required',actor=NULL,approved=NULL,nonce_hash=NULL WHERE state IN ('approved','approval_pending')")
  210: # 元の送信プロセスは再開しない。送信済みか不明な枠を再送可能に戻さない。
  211: db.execute("UPDATE candidates SET state='outcome_unknown',reason='restored_dispatch_unknown' WHERE state='dispatching'")
  212: if 'notices' in tables:
  213:     db.execute("UPDATE notices SET state='obsolete' WHERE state='pending'")
  214: db.execute("UPDATE notices SET state='outcome_unknown' WHERE state='sending'")
  ```
- **再現条件**: テーブル初期化前の SQLite DB や、`candidates` または `notices` テーブルを含まない特殊な DB を含む復旧セットを展開・復元した場合。
- **影響**: 行208・行212の `if ... in tables:` チェックをすり抜け、行211または行214で `sqlite3.OperationalError: no such table: candidates`（または `notices`）が発生して復元処理が異常終了する。
  ※通常運用で作成された正規台帳には両テーブルが必ず存在するため通常フローでは発現しませんが、先行の存在確認ガードの意図とインデントが不整合を起こしています。
- **最小修正案**:
  行211を行208の `if 'candidates' in tables:` ブロック配下へ、行214を行212の `if 'notices' in tables:` ブロック配下へそれぞれインデントして収容する。

---

## 4. 未確認範囲

本検証は配布コードに対する読取専用の静的解析に基づきます。以下の項目は未確認（未実施）のまま維持されます：

1. **外部サービスとの実認証・実通信の動作**
   - X API 実認証（OAuth 1.0a / 2.0）および実投稿
   - Discord Bot 実 Gateway 接続および実承認カード操作・通知送信
   - LogEats 本番内部 API との正式通信および本番正式リリース
   - Google Drive OAuth の本番環境における長期継続性（refresh token 期限）
2. **本番運用環境の実地受入・無人復帰**
   - サブPC（`KATSU-Server1`）環境での実処理所要時間計測（19:00〜20:00 窓の収束確認）
   - Windows 再起動後の WSL2・systemd サービス無人復帰動作
   - 実復号鍵の対話的生成・隔離保管
   - Google Drive への実暗号化バックアップ保管および本人による実データ復号検証
3. **LogEats 本体のリリース完了**
   - LogEats 側の本番マイグレーションおよび正式稼働

---

## 5. 総合結論

> [!CAUTION]
> **本番開始の非承認通知（継続）および実データ復号未確認の確認**
> 
> 1. **前回指摘の解消**: 前回 `4ceffb7` の指摘 A / C / D / E はすべて適切に修正され、**解消**と判定します。
> 2. **指摘 B に対する判断の支持**: 主担当の判断（成否不明枠を未送信へ戻さず `outcome_unknown` に固定し、`Store` 起動停止ガードを維持する）は、重複送信防止および証跡保全の観点から極めて正当であり、これを追認します（反証なし）。
> 3. **新規検出事項**: 重大度「高」「中」の欠陥は検出されず、軽微なインデント不整合（重大度「低」1件）のみです。
> 
> しかしながら、**本報告をもって「実データの復号確認」や「本番運用の開始」を承認したとは扱いません**。未確認範囲（実機・実認証・実データ復号・本番受入）の検証が完了するまで、**本番運用の開始は非承認（継続）**と判定します。


## 主担当の受領記録

終了コード0、配布110ファイルの無変更を照合した。原報告SHA-256: `35be713ce9b0d16e02b86f197e16c2319ad49641b131dbc1959b8274d54928ab`。A/C/D/Eの解消と、送信中を結果不明へ移して再送を許可しない判断への支持を受領した。高・中の未解決指摘なし。軽微なFはUPDATEを存在確認ブロック内へ修正し、Minaテーブルのない実SQLiteを隔離復元する試験を追加する。実データの復号・無人復帰・最終供給版への受入は未確認である。

この固定版には、その後に追加した専用Drive分割アップロード・CLI・限定認証ラッパーは含まれない。追加機能の検証と独立確認は別途必要。
