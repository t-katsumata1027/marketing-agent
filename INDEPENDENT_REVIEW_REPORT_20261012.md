# 独立コードレビュー報告書

- **対象コミット**: 固定コミット `efc1f2bbd8be36f107fa763d07e8121aa5b07f4c`
- **対象ディレクトリ**: 固定版の保護された展開ディレクトリ 配下の配布コードのみ
- **作業形態**: 読取専用・外部通信・コマンド実行・設定/認証読取なしの独立コードレビュー
- **参照要求仕様**: [AGENTS.md](AGENTS.md) および [INDEPENDENT_REVIEW_REQUEST_20261012.md](INDEPENDENT_REVIEW_REQUEST_20261012.md)

---

## 1. 重点8項目の実コード照合結果

配布コードおよび合成試験・文書を読み取り、重点8項目について実コードの実装状況を照合しました。

### 項目1: UUID永続化と成否不明の停止。同UUIDでも書込みを自動再要求しないこと。
- **判定**: **適合（実装確認済み）**
- **実コード根拠**:
  - [`RequestLedger.prepare`](src/mina/request_ledger.py#L23-L44): リクエスト送信前に `mina_requests` テーブルへ `phase='prepared'`、入力ダイジェスト、原本画像ハッシュを永続化。同一UUIDで入力が変更された場合は拒否（L40）。
  - [`RequestLedger.execute_once`](src/mina/request_ledger.py#L80-L96): `phase='prepared'` から `dispatching` への原子的な状態更新に成功した1件のみ送信を実行。例外発生時は `phase='held'` に記録し、同UUIDの照会（`reconcile`）を試みた後、[`OutcomeUnknown`](src/mina/clients.py#L33) を送出して停止。同UUIDでの再実行は `phase` が `prepared` でないため排他制御により拒否される（L83-85）。
  - [`RequestLedger.reconcile`](src/mina/request_ledger.py#L64-L78): GET照会のみを実行し、`automatic_retry_allowed: False` を維持。過去の `saved` 証拠を取得失敗や矛盾で降格させない（L73-76）。
  - [`MealClient.request_key`](src/mina/clients.py#L209-L215): 送信前の事前UUID指定を必須化し、Noneや不正形式を拒否。

### 項目2: current_result、個別rvと日sv、完成本文、実OGP SHA・期限、最初のGET開始からの単調時計120秒を、公開直前まで確認すること。
- **判定**: **適合（実装確認済み）**
- **実コード根拠**:
  - [`verify_source`](src/mina/m1_contract.py#L164-L218):
    - 単調時計による開始時刻の記録: `started_monotonic = timer()`（L171）。
    - 日全体正本の取得と検証: `day_state` から `validate_state` で日sv（`state_version`）の一致および `meal_count == 3` を照合（L173-177）。
    - UUID正本照会と現在結果の突合: `request_state` から `response.get('current_result')` を取得し、[`completed_request`](src/mina/m1_contract.py#L135-L144) で要求状態・保存状態・ハッシュを照合（L181-190）。
    - 個別rv・原本/保存画像SHA・完成本文の完全一致照合: `normalized_comment`、`fixed_text`、`publish_text`、`result_version`、`stored_image_sha256` の一致を確認（L191-198）。
    - OGP実体検証: [`validate_ogp`](src/mina/m1_contract.py#L73-L93) で版・期限（`expires_at`）を検査し、実画像再取得（`capture`）で実体SHAの一致を確認（L199-206）。
    - 照合所要時間判定: 単調時計の経過時間（`finished_monotonic - started_monotonic`）が0〜120秒以内であることを検査（L208-211）。
  - [`publish_due`](src/mina/runtime.py#L56-L91):
    - 送信直前（X公開API呼び出し直前）に単調時計 `sending_monotonic = timer()` を計測し、最初のGET開始から送信直前までが120秒以内（`sending_monotonic - start <= 120`）であることを再検証（L76-82）。
    - OGP有効期限が送信直前時点で切れていないことを検証（L87-88）。照合失敗時は [`store.stop_before_publish`](src/mina/store.py#L197-L207) で承認失効・公開中止。

### 項目3: Discord承認、旧ボタン失効、4候補の原子的な差替え、X送信成否不明・通知成否不明の非再送。
- **判定**: **適合（実装確認済み）**
- **実コード根拠**:
  - [`ApprovalPolicy`](src/mina/interactions.py#L11-L24) / [`handle`](src/mina/interactions.py#L26-L45): Ed25519署名、タイムスタンプ期限（300秒以内）、Application ID、Guild ID、Channel ID、承認者User IDの許可リストを検証。
  - [`Store.decide`](src/mina/store.py#L125-L143): `custom_id` の `nonce` とDB内の `nonce_hash` を `secrets.compare_digest` で突合。承認反映時に `nonce_hash=NULL` に更新し、同一ボタンの再利用や旧操作を拒否。
  - [`Store.save_many`](src/mina/store.py#L68-L93): 4枠全件を1つのトランザクション内で原子的反映。送信開始済み（`dispatching`）、公開済み（`published`）、成否不明（`outcome_unknown`）の行がある場合は差し替えを拒否（L82-83）。差し替え時は `revision` を繰り上げ、旧通知を `obsolete` に更新。
  - [`Store.finish`](src/mina/store.py#L174-L183) / [`publish_due`](src/mina/runtime.py#L98-L99): X送信失敗時は `state='outcome_unknown'` に固定。自動再送経路なし。
  - [`Store.finish_notice`](src/mina/store.py#L191-L196) / [`send_notifications`](src/mina/runtime.py#L48-L51): 通知送信失敗時は `state='outcome_unknown'` に記録し、保留。二重送信を防止。

### 項目4: 自動履歴の全ページ検査、秒単位UTC照会、実観測終端、投稿ID・本文・日付照合。交流投稿を確認なしで除外したり、献立を捏造したりしないこと。
- **判定**: **適合（実装確認済み）**
- **実コード根拠**:
  - [`collect_timeline`](src/mina/x_history.py#L9-L57):
    - `as_of` から30秒前を上限とする観測終端 `end` を秒単位（`microsecond=0`）のUTC形式（`%Y-%m-%dT%H:%M:%SZ`）でAPIへ渡し、未来時刻の照会を防止（L18-23）。
    - 最大40ページの `next_token` を走査し、`token is None` の終端まで全ページ取得（L25-56）。重複トークンや取得上限（3200件）到達時は例外送出。
    - [`canonical_text`](src/mina/x_history.py#L59-L73) で短縮URLを展開URLへ厳密復元。
  - [`build_automatic_history`](src/mina/automatic_history.py#L17-L84):
    - 観測終端が直近30秒〜151秒前であることを検査（L22-24）。
    - 食事投稿の交流偽装除外の禁止: `kind == 'other'` かつ食事・日次固定文言を含む投稿を例外で拒否（L41-42）。
    - 交流投稿の献立捏造の防止: `kind == 'other'` の投稿には献立（menu）を要求・捏造せず、`publish_text` のみで受け入れ（L38-40）、献立重複検査用の `entries` からは除外する（L77）一方で、全体カバレッジ件数（`coverage_post_count`）および全投稿照合（`reconcile_timeline`）には確実に含める（L66, L78）。
    - 各投稿のJST日付と台帳・資料の日付一致を照合（L69-71）。

### 項目5: 初回旧資料のplan・保存応答・実X本文対応、確認者と元資料ハッシュ。旧資料を現在の鮮度や投稿承認にしないこと。
- **判定**: **適合（実装確認済み）**
- **実コード根拠**:
  - [`confirm_legacy_history`](src/mina/legacy_history.py#L38-L94):
    - 旧献立表（`plan`）、保存応答（`results`）、実公開本文（`snapshot`）の三者突合を厳密に照合。
    - 確認者 `reviewer` の存在・非空を検証。
    - 元資料ハッシュ（`source_plan_sha256`、各エントリの `source_result_sha256`）を記録（L88, L92）。
    - 交流返信（`reviewed_other_ids`）を明示的に確認・突合（L89-90）。
    - 出力結果に `'production_start_allowed': False` を固定（L93）。
  - [`build_automatic_history`](src/mina/automatic_history.py#L30-L31, #L87-L98): 旧資料（`bootstrap`）は移行前投稿の照合証跡としてのみ参照され、現在のタイムラインの鮮度（最新の実X GETによる `snapshot`）の代替にはならず、投稿承認へ自動昇格しない。

### 項目6: 履歴失敗の通知保留と未送信候補の承認失効。停止中の外部要求・業務書込みが発生しないこと。
- **判定**: **適合（実装確認済み）**
- **実コード根拠**:
  - [`prepare_history`](src/mina/automatic_history.py#L101-L109): 履歴照合失敗時に [`store.queue_history_hold`](src/mina/store.py#L221-L233) を呼び出し、例外を送出して生成・登録へ進まない。
  - [`Store.queue_history_hold`](src/mina/store.py#L221-L233): 未送信候補（`approved`, `approval_pending`）をすべて `changes_requested`（`reason='history_hold'`, `actor=NULL`, `approved=NULL`, `nonce_hash=NULL`）に更新して旧承認を即座に失効し、未送信の承認通知を `obsolete` に更新した上で、保留通知（`kind='history_hold'`）を登録。
  - 停止スイッチの保護:
    - [`settings_status`](src/mina/__main__.py#L20-L35) / 各コマンド入口: `MINA_LIVE_WRITES`, `MINA_APPROVAL_ENABLED`, `MINA_PUBLISH_ENABLED`, `MINA_AI_ENABLED`, `MINA_PREPARE_ENABLED`, `MINA_READ_REVIEW_ENABLED`, `MINA_X_HISTORY_READ_ENABLED`, `MINA_HISTORY_AUTO_ENABLED` が既定ですべて `false`。
    - [`require_live`](src/mina/clients.py#L54-L57): `MINA_LIVE_WRITES != 'true'` 時はすべての外部書き込みを遮断。
    - [`XClient.read_history`](src/mina/clients.py#L115-L118): `MINA_X_HISTORY_READ_ENABLED != 'true'` 時はXのGET照会を拒否。

### 項目7: 復旧ファイルの暗号化・鍵生成・指紋固定・私有保存・非表示入力・誤鍵や破損の拒否。非対話実行でパスワードを表示しないこと。
- **判定**: **適合（実装確認済み）**
- **実コード根拠**:
  - [`create_recovery_keys`](src/mina/encrypted_backup.py#L53-L90): 3072bit RSA鍵ペアを生成し、秘密鍵を `BestAvailableEncryption` で暗号化。POSIXでは 0700/0600、Windowsでは `icacls.exe` によりACL継承解除（`/inheritance:r`）および現在ユーザーSIDのみにアクセス権を付与（L65-77）。
  - [`save_new`](src/mina/encrypted_backup.py#L35-L51): `os.O_CREAT | os.O_EXCL` で既存ファイルの上書きを拒否。POSIX環境で親ディレクトリに他者アクセス権（`0o077`）がある場合は拒否。
  - [`seal_file`](src/mina/encrypted_backup.py#L92-L110): AES-256-GCM + RSA-OAEP-SHA256。`expected_recipient_sha256` が指定された場合、公開鍵のDER SHA-256（指紋）との一致を事前検証（指紋固定）。
  - [`unseal_file`](src/mina/encrypted_backup.py#L112-L139): 誤鍵、誤パスワード、MAGIC不一致、AEAD認証タグ検証失敗、破損時は例外で拒否し、平文ファイルを出力しない。
  - 非表示入力と非対話実行の保護:
    - [`main`](src/mina/__main__.py#L141-L148, #L162-L166): `backup-keygen` および `backup-decrypt` において、`if not sys.stdin.isatty(): raise ValueError(...)` を明示検査し、対話端末以外での実行を即時エラーとして拒否。入力には `getpass` を使用し、端末上への平文表示やログ出力を遮断。

### 項目8: サービス入口で停止スイッチを保持し、CLIへ業務秘密を渡さないこと。旧配置との差・復元時の重複投稿防止を確認すること。
- **判定**: **適合（実装確認済み）**
- **実コード根拠**:
  - [`run-mina-doppler.sh`](deploy/run-mina-doppler.sh#L6-L15, #L22-L28): サービス起動ラッパーにおいて、全停止スイッチ変数をローカル設定優先（既定 `false`）で設定し、Doppler実行時に `--preserve-env` で保護。秘密値は `--only-secrets` に限定注入。
  - [`CodexClient.environment`](src/mina/codex_client.py#L23-L30): 子プロセスへの環境変数渡しをホワイトリスト（`HOME`, `USER`, `PATH`, `LANG` 等のOS標準変数のみ）に限定。APIキーやトークン等の業務秘密は一切渡さない。プロンプト先頭にデータ防壁（`guard`）を付与（L93-98）。
  - 旧配置との差・復元時の重複投稿防止:
    - [`Store.claim_due`](src/mina/store.py#L149-L152): `state='approved'` のレコードのみを排他取得。復元された台帳に `published`, `dispatching`, `outcome_unknown` の投稿が存在しても再取得・再送信されない。
    - [`Store.save_many`](src/mina/store.py#L82-L83): 既存レコードが `dispatching`, `published`, `outcome_unknown` の場合、候補の差し替えを拒否。
    - [`release_commit`](src/mina/readiness.py#L22-L49): 配布コードの実体と実在40桁Gitコミットのツリー・内容ハッシュを突合し、ファイル追加・欠落・シンボリックリンク・改変を検知して拒否。
    - [`check_handover`](src/mina/handover.py#L16-L58): 停止フラグ6項目、コミット一致、認証ファイル存在、heartbeat、台帳読取を外部接続なしで診断し、`production_start_allowed: False` を固定返却。

---

## 2. 指摘事項（個別レビュー結果）

実コードの精査を行いましたが、**重大度：高（Critical）および重大度：中（Major）に該当する脆弱性・仕様違反・不整合などの未解決指摘事項は検出されませんでした**。

設計・コード品質の観点から確認された軽微な留意点（重大度：軽微 / Info）は以下の通りです。

### 指摘 1: Windows環境における `check_handover` 内 Git サブプロセス呼び出し時の挙動
- **重大度**: 軽微（Low / Info）
- **対象ファイルと行番号**: [src/mina/handover.py:22-26](src/mina/handover.py#L22-L26)
- **具体的な再現条件**:
  サブPCのWSL内ではなく、WindowsネイティブのPython環境で `python -m src.mina handover-check` を実行した際、作業ツリーにCRLF改行が適用されていると、[`release_commit`](src/mina/readiness.py#L47) のバイト列比較（`result.stdout.replace(b'\r\n', b'\n') != path.read_bytes().replace(b'\r\n', b'\n')` により吸収処理は実装済みだが）やGitコマンド探索の失敗により `code_matches = False` と判定される可能性がある。
- **影響**:
  引継ぎ診断（`handover-check`）の判定で `release_code_matches` が `False` となり、診断結果が不合格となる可能性がある（ただし本番稼働はWSL上のLinux環境を前提としており、また安全側に倒れて停止する動作であるため、誤動作や情報漏洩等の実害はない）。
- **最小修正案**:
  本番稼働環境（WSL / Linux環境）で実行することを手順書（[REMOTE_DEVELOPMENT.md](REMOTE_DEVELOPMENT.md#L26-L30)）通りに徹底する。

---

## 3. レビュー境界と未確認範囲

本レビューは、固定コミット配布コードに対する読取専用の静的解析および合成試験文書の検証に基づいています。以下の項目は今回のレビュー範囲外（未確認）であり、コード上の重大な未解決指摘がないことのみを根拠として本番運用を開始することはできません。

1. **外部サービスとの実認証・実通信の動作**:
   - X API（v2）の本アカウントにおける実投稿、権限確認、レート制限挙動。
   - Discord Botの実Gateway接続、実承認ボタン操作、Interactions受信。
   - LogEats本番内部API（`/api/internal/mina/*`）の正規リリース後の実受入・DB書き込み。
   - Google Drive OAuthの長期運用継続性（Google Auth Platformの公開ステータスがTestingの場合のトークン失効リスク）。
2. **本番運用環境の実地検証**:
   - サブPC（WSL / Ubuntu）における 19:00〜20:00 JST の実1日分生成・点検・登録通し処理の所要時間計測。
   - Windows再起動からのWSLおよび各systemdサービスの無人自動復帰。
   - 秘密鍵の生成、および暗号化バックアップ実ファイルのGoogle Driveへの実保管・実隔離復元。
3. **LogEats本体側の完了状態**:
   - LogEats側の本番M1移行、DBマイグレーション、正規リリースおよび後処理の完了確認。

---

## 4. 結論

> [!IMPORTANT]
> **本番開始の非承認通知**
> 配布コード（コミット `efc1f2bbd8be36f107fa763d07e8121aa5b07f4c`）において、指定された重点8項目に関する重大な未解決指摘事項は確認されませんでした。
> しかしながら、依頼事項および安全管理規定に従い、**本レビュー結果をもって本番運用の開始を承認することはいたしません**。
> 上記の未確認範囲（実サービス認証、本番環境受入、LogEats本体側の正規リリース完了等）に関する実地検証と別途定められた移行工程を完了させた上で、運用の判断を行ってください。


## 主担当による受領記録

原報告SHA-256: `9b05268dc0d5a64d6e16625aca6501d0ec7efbe6c53ff2c2f656ac4c88c4a957`。終了コード0。配布104ファイルはレビュー終了後も元アーカイブと一致した。原報告と標準エラーは公開Git外で保持する。

起動時にCLIから「slash command expansionを無効にした場合、planモードは無効」と警告された。機械的な読取専用制限の成立は主張しない。読取限定の依頼・担当者報告・展開コードの無変更を確認した範囲の静的レビューとして採用する。外部操作ゼロの独立監査ではない。追加レビューでは当該オプションを併用しない。

報告受領後、主担当はhandover.pyの停止一覧が6項目のままであることを確認し、履歴用2項目を追加した。この差分は上記固定版レビューに含まれず、追加確認が必要。過去バックアップからの復元は稼働台帳のpublished状態を巻き戻し得るため、停止維持・実投稿と復元時点以後の台帳照合を省略しない。現存バックアップの状態だけで重複公開を防げるとは扱わない。
