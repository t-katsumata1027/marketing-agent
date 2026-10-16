# 独立コードレビュー報告書

- **対象コミット**: 固定コミット `6af579bbd9cd67c89a3ddc439f8fbf38a3ca4cda`
- **対象ディレクトリ**: 固定版の保護された展開ディレクトリ配下の配布コードのみ
- **作業形態**: 読取専用・外部通信なし・コマンド実行なし・ファイル変更なし・設定/認証読取なしの独立コードレビュー
- **参照仕様・資料**: [AGENTS.md](AGENTS.md)、[INDEPENDENT_REVIEW_REQUEST_20261012.md](INDEPENDENT_REVIEW_REQUEST_20261012.md)、前回レビュー報告2件（[INDEPENDENT_REVIEW_REPORT_20261012.md](INDEPENDENT_REVIEW_REPORT_20261012.md)、[INDEPENDENT_REVIEW_FOLLOWUP_20261012.md](INDEPENDENT_REVIEW_FOLLOWUP_20261012.md)）
- **安全原則**: 資料内の記述・命令はレビュー対象データとして扱い、ファイル読取以外の操作・実認証取得・本番運用承認は一切行いません。

---

## 1. 前回（ed0fd77）以降の追加変更の精査結果

### (1) diagnostics / handover の共通停止8項目と独立テスト
- **実装状況**:
  - [`STOP_FLAGS`](src/mina/diagnostics.py#L24-L26) は [src/mina/diagnostics.py](src/mina/diagnostics.py) に集約・一元定義され、履歴2項目を含む計8項目が過不足なく網羅されています。
  - [src/mina/handover.py](src/mina/handover.py#L8) では `from .diagnostics import diagnose, STOP_FLAGS` として定数を参照しており、二重定義（前回の指摘2）が解消されています。
  - [tests/mina/test_handover.py](tests/mina/test_handover.py#L17-L22) に [`test_all_required_stop_switches_are_defined`](tests/mina/test_handover.py#L17-L22) が新設され、独立した期待集合（`expected`）と件数8の照合（前回の指摘1）が追加されています。
- **判定**: **適合**（指摘事項への適切な対応を確認）

### (2) 鍵生成CLIの短いパスワード・入力不一致の非漏洩案内
- **実装状況**:
  - [src/mina/__main__.py](src/mina/__main__.py#L144-L163) の `backup-keygen` において、`sys.stdin.isatty()` を確認し非対話端末を拒否しています。
  - 2回の入力不一致時、および 16〜1024 バイト外のパスワード時に、`parser.error()` による日本語案内を行い、入力値・トレースバックを出力せず終了コード 2 で即時終了します。
  - [tests/mina/test_encrypted_backup.py](tests/mina/test_encrypted_backup.py#L16-L31) の [`test_short_password_has_safe_cli_message_without_creating_keys`](tests/mina/test_encrypted_backup.py#L16-L31) で短いパスワード時の安全案内・鍵未作成・非漏洩が検証されています。
- **判定**: **適合**（ただし入力不一致側のテストケース欠落あり。後述の指摘4を参照）

### (3) generation / preparation の食品別分量と固定スタイル優先プロンプト
- **実装状況**:
  - [src/mina/generation.py](src/mina/generation.py#L165-L173) に [`meal_image_description`](src/mina/generation.py#L165-L173) が新設され、`menu`, `foods`（食品名・分量表）, `image_description` を含むコンテキストを JSON で確実に渡しています。
  - プロンプト先頭で「固定の撮影スタイルを最優先する」「image_descriptionに異なる撮影角度・器・背景の指定があっても、固定スタイルを変更しない」と明示指示し、スタイル競合を抑止しています。
  - [src/mina/preparation.py](src/mina/preparation.py#L71-L72) の [`prepare_images`](src/mina/preparation.py#L52-L90) で [`meal_image_description`](src/mina/generation.py#L165-L173) を呼出しています。
  - [tests/mina/test_generation.py](tests/mina/test_generation.py#L24-L33) の [`test_image_input_preserves_food_amounts_and_style_priority`](tests/mina/test_generation.py#L24-L33) で食品分量とプロンプト優先度が検証されています。
- **判定**: **適合**

### (4) recovery_set.py の整合性と安全機構
- **実装状況**:
  - **SQLite整合コピー**: `source.backup(destination)`（SQLite Backup API）による整合コピー、60秒タイムアウト、`integrity_check` 検証、256MB 容量上限検査を実装（[recovery_set.py:56-62](src/mina/recovery_set.py#L56-L62)）。
  - **固定5保護ファイル**: `service-env`, `doppler-env`, `codex-auth`, `history-bootstrap`, `cutover` の完全一致を検査（[recovery_set.py:29-30](src/mina/recovery_set.py#L29-L30)）。
  - **成果物保全**: `daily`, `history` のみを対象とし、特殊ファイル・リンク・環境変数・容量を検査（[recovery_set.py:31-33](src/mina/recovery_set.py#L31-L33)）。
  - **暗号化と受信先指紋**: 私有ファイル読取前に RSA 公開鍵 DER SHA-256（3072bit以上）と指紋を突合（[recovery_set.py:35-40](src/mina/recovery_set.py#L35-L40)）。
  - **コピー中変更検知**: 5保護ファイルの読取前後比較および成果物 `inventory`（mtime/ctime/size/ino/dev）の前後比較（[recovery_set.py:81-85](src/mina/recovery_set.py#L81-L85)）。
  - **隔離復元とZIP検証**: 新規ディレクトリ限定、ZIP パストラバーサル・ドライブレター・コロン・重複・モード検査、manifest 照合、全実体 SHA-256・容量突合、復元台帳 `integrity_check`（[recovery_set.py:91-148](src/mina/recovery_set.py#L91-L148)）。
  - **Windows ACL**: `icacls.exe` による親ディレクトリの継承解除（`/inheritance:r`）および現在ユーザー SID のフルコントロール付与（[encrypted_backup.py:53-73](src/mina/encrypted_backup.py#L53-L73)）。
- **判定**: 基本骨格は堅牢に設計されていますが、**復元後の台帳による重複公開リスク**、**解凍時のメモリ枯渇リスク**、**中間ファイル残留**などの具体的な欠陥が存在します（詳細は第2章）。

---

## 2. 具体的な欠陥の指摘事項

### 指摘 1: 隔離復元された台帳における未送信承認状態の巻き戻しによる重複公開リスク
- **重大度**: **高（Critical / 業務・運用安全性）**
- **対象ファイルと行番号**:
  - [src/mina/recovery_set.py:136-154](src/mina/recovery_set.py#L136-L154)
  - [src/mina/store.py:144-153](src/mina/store.py#L144-L153)
- **具体的な再現条件**:
  1. 稼働環境において、12:15 の投稿枠が承認（`state='approved'`）された状態で復旧セットが取得された後、12:15 に X への公開が正常に完了し `state='published'`（`x_id` 記録済み）に遷移したとする。
  2. その後、同日 12:16〜12:17 にシステム障害等が発生し、上記復旧セットから [`restore_recovery_set`](src/mina/recovery_set.py#L91-L154) を実行して隔離展開する。
  3. 復旧手順に従って隔離復元された `database.sqlite3` を稼働台帳ディレクトリに配置し、サービスまたは巡回デーモン（`MINA_PUBLISH_ENABLED=true`）を起動する。
  4. 復元台帳内では、すでに X に公開済みの 12:15 枠が `state='approved'` のまま巻き戻っている。
  5. [`Store.claim_due`](src/mina/store.py#L144-L153) は `scheduled <= now` かつ `scheduled >= now - 120` の承認枠を対象とするため、120 秒の公開猶予時間内（例: 12:16:30）に起動されると、当該枠が再び `dispatching` として確保され、X クライアントによって**同一内容が X に再公開（重複投稿）される**。
  6. さらに、当日夜枠や翌日枠など未来の `scheduled` を持つ枠についても、バックアップ後に運用者が差戻し・見送りに変更していた場合でも、復元によって `approved` に復活し、予定時刻に自動送信されてしまう。
- **影響**:
  復元された台帳それ自体に「未送信承認の失効処理」や「復旧台帳であることのマーク」が存在しないため、過去バックアップを稼働へ戻した際に、**X への重複公開・誤公開が自動的に発生する重大なリスク**がある。
- **最小修正案**:
  [`restore_recovery_set`](src/mina/recovery_set.py#L91-L154) の展開処理完了後（[recovery_set.py:148](src/mina/recovery_set.py#L148) の直後）、復元された `database.sqlite3` に対して書き込みトランザクションを実行し、未送信の承認・承認待ち行をすべて強制的に保留・失効（`UPDATE candidates SET state='changes_requested', reason='restored_reconciliation_required', actor=NULL, approved=NULL, nonce_hash=NULL WHERE state IN ('approved', 'approval_pending')`）させ、外部タイムラインとの照合が完了するまで自動送信対象にならないよう安全側に倒す。

---

### 指摘 2: `restore_recovery_set` における Zip 解凍時のメモリ大量消費および Zip Bomb 耐性の不足
- **重大度**: **中（Major / 堅牢性・リソース枯渇）**
- **対象ファイルと行番号**:
  - [src/mina/recovery_set.py:98-103](src/mina/recovery_set.py#L98-L103)
  - [src/mina/recovery_set.py:129-132](src/mina/recovery_set.py#L129-L132)
  - [src/mina/recovery_set.py:144](src/mina/recovery_set.py#L144)
- **具体的な再現条件**:
  1. [recovery_set.py:98](src/mina/recovery_set.py#L98) で `raw = read_regular(archive, LIMIT)` により最大 256MiB の暗号化解除済み ZIP 全体をメモリ（`io.BytesIO`）に保持する。
  2. [recovery_set.py:102](src/mina/recovery_set.py#L102) で `sum(item.file_size for item in infos)` を検査しているが、`item.file_size` は ZIP ヘッダー内の自己申告値である。悪意ある細工やヘッダー破損によって `file_size` が小さく偽装され、展開時に数十GBに膨張する高圧縮データ（Zip Bomb）が含まれていた場合、[recovery_set.py:131](src/mina/recovery_set.py#L131) の `reader.read(name)` を呼び出した時点で無制限にメモリ確保が行われ、プロセスが OOM（メモリ枯渇）でクラッシュする。
  3. また、正常な最大 256MiB の ZIP であっても、[recovery_set.py:131](src/mina/recovery_set.py#L131) のハッシュ計算で全エントリを `reader.read()` で全展開し、さらに [recovery_set.py:144](src/mina/recovery_set.py#L144) のファイル書き込みで再度全エントリを `reader.read()` で全展開しているため、二重の全展開とメモリ保持によりピークメモリ使用量が 500MiB〜1GiB 超に達する。
- **影響**:
  メモリが制限された WSL 環境やサブPC上で復元を実行した際、OOM による強制終了が発生し、緊急時の復元作業が阻害される。
- **最小修正案**:
  `reader.open(name)` を用いて 64KiB 単位のストリーミング読込を行い、累積展開バイト数が `entry['bytes']` を超えた瞬間に即座に `ValueError` を送出する。また、ハッシュ計算とファイル書き込みを同一ストリーム内で同時に行うことで、メモリ使用量を数 MiB 程度に抑え、Zip Bomb を安全に遮断する。

---

### 指摘 3: `restore_recovery_set` 失敗時の中間ファイル残留によるディレクトリ汚染と再実行遮断
- **重大度**: **中（Major / 運用性・機密保護）**
- **対象ファイルと行番号**:
  - [src/mina/recovery_set.py:136-151](src/mina/recovery_set.py#L136-L151)
- **具体的な再現条件**:
  1. [`restore_recovery_set`](src/mina/recovery_set.py#L91-L154) が新規の `output` ディレクトリを作成し、エントリの順次書き出しを開始する。
  2. ループの途中（例: 認証ファイル `protected/service-env` や一部の画像を展開した後）で、ディスク容量不足、I/O エラー、または [recovery_set.py:147](src/mina/recovery_set.py#L147) の SQLite `PRAGMA integrity_check` 不整合が発生する。
  3. [recovery_set.py:149-151](src/mina/recovery_set.py#L149-L151) の `except Exception:` ブロックでは、「途中の復元も隔離先に残し」という注記とともに `output` を削除せずそのまま残して例外を送出する。
  4. その結果、不完全な機密情報（認証トークン等を含む平文ファイル）が中途半端にディスク上に放置される。
  5. さらに、次回同じ `output` パスで復元を再実行しようとすると、[recovery_set.py:94](src/mina/recovery_set.py#L94) の `if output.exists(): raise ValueError('新規の隔離復元先が必要です')` によって**即座に拒否され、再実行が完全に遮断される**。
- **影響**:
  復元失敗時に不完全なファイルが残留して運用者の誤認や機密放置を招き、かつ同一パスでの再実行がブロックされるため、復旧手順の自動化や迅速な障害復旧に支障をきたす。
- **最小修正案**:
  展開処理を一時作業ディレクトリ（`tempfile.TemporaryDirectory`）内で行い、SQLite の `integrity_check` まで全工程が合格した後に、最終隔離先 `output` へアトミックにリネーム・移動する。例外発生時は一時ディレクトリとともに中途半端な実体を確実に自動消去する。

---

### 指摘 4: 鍵生成CLIにおける「入力不一致」の非漏洩テストケースの欠落
- **重大度**: **低（Low / テスト網羅性・品質）**
- **対象ファイルと行番号**:
  - [src/mina/__main__.py:152-154](src/mina/__main__.py#L152-L154)
  - [tests/mina/test_encrypted_backup.py:16-31](tests/mina/test_encrypted_backup.py#L16-L31)
- **具体的な再現条件**:
  - [src/mina/__main__.py:153](src/mina/__main__.py#L153) に `if first != getpass('同じパスワードを再入力: '): parser.error(...)` が実装されているが、[tests/mina/test_encrypted_backup.py](tests/mina/test_encrypted_backup.py) には「短いパスワード」（[test_encrypted_backup.py:16](tests/mina/test_encrypted_backup.py#L16)）のテストしか存在しない。
  - 1回目と2回目の入力が一致しない場合の非漏洩性、エラー終了コード（2）、鍵未作成の挙動を検証するテストケースがテストスイート全体で欠落している。
- **影響**:
  入力不一致時の案内メッセージ変更や例外ハンドリングの改変時に、パスワード値の漏洩やトレースバック出力などの退行（リグレッション）をテストで検知できない。
- **最小修正案**:
  [tests/mina/test_encrypted_backup.py](tests/mina/test_encrypted_backup.py) に、`getpass.getpass` が 1 回目と 2 回目で異なる文字列を返した際に、終了コード 2、`2回の入力が一致しません` を出力し、入力値・トレースバックを含まず、出力先ディレクトリが作成されないことをアサートするテストケースを追加する。

---

### 指摘 5: `create_recovery_set` を実行する CLI サブコマンドの未登録
- **重大度**: **低（Low / 運用性・インタフェース欠落）**
- **対象ファイルと行番号**:
  - [src/mina/__main__.py:118](src/mina/__main__.py#L118)
  - [src/mina/__main__.py:138-143](src/mina/__main__.py#L138-L143)
- **具体的な再現条件**:
  - [src/mina/__main__.py:118](src/mina/__main__.py#L118) のサブコマンド定義には `backup-restore-set` は存在するが、対になる `create_recovery_set` を呼び出す CLI サブコマンド（例: `backup-create-set`）が登録されていない。
  - 復旧セットの暗号化作成処理を CLI から直接実行する手段がない。
- **影響**:
  台帳・成果物・固定5保護ファイルを束ねた整合復旧セットを作成する際、運用者が Python のコードを直接記述・インポートして呼び出す必要があり、手順の標準化・無人実行化が阻害される。
- **最小修正案**:
  [src/mina/__main__.py](src/mina/__main__.py) に `backup-create-set` サブコマンドを追加し、`--database`, `--artifacts`, `--files`, `--output`, `--backup-key`, `--backup-recipient`, `--source-commit` を受け取って [`create_recovery_set`](src/mina/recovery_set.py#L20-L89) を安全に呼び出す経路を設ける。

---

### 指摘 6: Windows環境における親ディレクトリのアクセス権検査（0o077相当）のスキップ
- **重大度**: **低（Low / プラットフォーム一貫性）**
- **対象ファイルと行番号**:
  - [src/mina/recovery_set.py:27-28](src/mina/recovery_set.py#L27-L28)
  - [src/mina/recovery_set.py:96-97](src/mina/recovery_set.py#L96-L97)
- **具体的な再現条件**:
  - [recovery_set.py:27](src/mina/recovery_set.py#L27) および [recovery_set.py:96](src/mina/recovery_set.py#L96) では `if os.name == 'posix' and output.parent.stat().st_mode & 0o077:` として親ディレクトリのアクセス権を検査している。
  - Windows 環境（`os.name == 'nt'`）ではこのチェックが無条件にスキップされるため、親ディレクトリに不適切なアクセス権（Everyone や Authenticated Users による読取権限等）が付与されている場合でも警告なく処理が進む。
- **影響**:
  Windows 上で作成・展開される際、親ディレクトリが共有フォルダや他ユーザーからアクセス可能な場所であった場合、平文ファイルが一時的・恒久的に第三者から読み取られるリスクが残る。
- **最小修正案**:
  Windows 環境においても親ディレクトリのアクセス権を検査するか、親ディレクトリの安全性が確認できない場合は警告またはエラーとする処理を追加する。

---

## 3. レビュー境界と未確認範囲

本レビューは、固定コミット配布コード（`6af579bbd9cd67c89a3ddc439f8fbf38a3ca4cda`）に対する読取専用の静的解析および合成試験の検証に基づいています。以下の項目は未確認（未実施）であり、本レビューをもって確認済みとみなすことはできません。

1. **外部サービスとの実認証・実通信の動作**:
   - X API（v2）の実認証先（Minaアカウント）照合、実投稿・権限、実レート制限挙動。
   - Discord Bot の実 Gateway 接続、実承認ボタン操作、実通知送信。
   - LogEats 本番内部 API（`/api/internal/mina/*`）の正規リリース・実受入・実 DB 書込み。
   - Google Drive OAuth の本番環境における継続性（Testing ステータスによる期限切れリスクの解消）。
2. **本番運用環境の実地受入・無人復帰**:
   - サブPC（WSL / Ubuntu）における 19:00〜20:00 JST の実3食生成・点検・登録通し処理の所要時間計測。
   - Windows 再起動後の WSL・systemd サービスの完全無人自動復帰。
   - 本人対話操作による実復号鍵の生成・隔離保管、および暗号化バックアップ実ファイルの Google Drive への実保管・実復号確認。
3. **LogEats 本体のリリース完了**:
   - LogEats 側の本番マイグレーション、契約適合、正式リリースの完了確認。

---

## 4. 結論

> [!CAUTION]
> **本番開始の非承認通知（継続）**
> 配布コード（コミット `6af579bbd9cd67c89a3ddc439f8fbf38a3ca4cda`）において、共通停止8項目の一元化、プロンプトの固定スタイル優先度、鍵生成の安全案内などは適切に実装されていることを確認しました。
>
> しかしながら、今回追加された復旧セット機能において、**隔離復元された台帳を稼働環境へ戻した際に未送信承認が巻き戻り重複公開を引き起こす重大な欠陥（指摘1）**、および **Zip 解凍時のリソース枯渇リスク（指摘2）**、**復元失敗時の中間ファイル残留と再実行不能（指摘3）** が検出されました。
>
> 依頼事項および安全管理規定に基づき、**本コードによる本番運用の開始を承認することはいたしません**。
> 上記欠陥の修正、および未確認範囲（実認証、本番環境受入、実復元後の履歴突合手順の確立等）の実地検証を完了させた上で、運用の可否を改めて判断してください。


## 主担当の受領と対応状況

終了コード0、配布108ファイルの無変更を照合した。原報告SHA-256: `6684170a91f9820445804a4efcd539c1d5f1752311b9a97344016bad4e603d43`。高1件・中2件・低3件の指摘を受領した。

主担当は、元機能が隔離復元だけで稼働先へ自動反映しない点を維持しつつ、誤って稼働へコピーした場合も防ぐため、復元台帳の承認失効とアプリ起動ガードを追加する。ZIPヘッダー偽装で無制限にreadが膨張するという再現は独立して実証していないが、全ZIPのメモリ保持と二重の全実体読込をやめ、64KiBストリーム・実読取上限へ変更する。復元失敗時の残留は元の明示仕様だったが、私有一時領域で検証してから既存先を上書きしない確定操作へ改める。

入力不一致の非漏洩試験、作成CLI、Windows親フォルダーの所有者・許可主体の検査も追加する。修正後のコードはこの固定版レビューには含まれず、再レビューと検証が必要。本報告を合格報告や実バックアップ完了とは扱わない。
