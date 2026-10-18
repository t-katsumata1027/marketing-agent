固定コミット `4ceffb765a9a5ffaf710f210e4590e446b16eac6` の配布コードに対する**読取専用独立再レビュー**を実施しました。
詳細な検証記録および指摘事項は、作成したアーティファクト independent_recovery_rereview_4ceffb7.md（アプリ内保存） に記録しています。

---

### 1. 前回（`6af579b`）の 6 指摘事項への対応状況

前回レビュー報告書（[INDEPENDENT_RECOVERY_REVIEW_20261012.md](INDEPENDENT_RECOVERY_REVIEW_20261012.md)）で挙げられた 6 つの指摘事項について、該当コードおよびテストを精査した結果、**すべて適切に対応されており「解消」と判定**しました。

| # | 前回の指摘事項 | 重大度 | 主な実装箇所 | 解消判定 |
|---|---|---|---|---|
| 1 | 復元台帳の未送信承認巻き戻しによる重複公開リスク | 高 | [`src/mina/recovery_set.py`](src/mina/recovery_set.py#L194-L201), [`src/mina/store.py`](src/mina/store.py#L21-L23) | **解消** |
| 2 | Zip 解凍時のメモリ大量消費および Zip Bomb 耐性不足 | 中 | [`src/mina/recovery_set.py`](src/mina/recovery_set.py#L132-L141) | **解消** |
| 3 | 復元失敗時の中間ファイル残留と同一パス再実行遮断 | 中 | [`src/mina/recovery_set.py`](src/mina/recovery_set.py#L97-L111) | **解消** |
| 4 | 鍵生成 CLI における「入力不一致」の非漏洩テスト欠落 | 低 | [`tests/mina/test_encrypted_backup.py`](tests/mina/test_encrypted_backup.py#L16-L30) | **解消** |
| 5 | `create_recovery_set` を実行する CLI サブコマンド未登録 | 低 | [`src/mina/__main__.py`](src/mina/__main__.py#L118-L147), [`tests/mina/test_recovery_set.py`](tests/mina/test_recovery_set.py#L100-L112) | **解消** |
| 6 | Windows 環境での親ディレクトリ保護検査（0o077相当）スキップ | 低 | [`src/mina/encrypted_backup.py`](src/mina/encrypted_backup.py#L75-L91), [`src/mina/recovery_set.py`](src/mina/recovery_set.py#L27-L95) | **解消** |

---

### 2. 新たに検出された欠陥・懸念事項

配布コード（`4ceffb7`）の精査により、以下の 5 点（中 2 件、低 3 件）の課題を検出しました。

#### 指摘 A: [`save_new`](src/mina/encrypted_backup.py#L35-L51) における Windows 親ACL検査のスキップ（機密露出リスク）
- **重大度**: **中（Major / 機密保護）**
- **対象行**: [`src/mina/encrypted_backup.py:40-41`](src/mina/encrypted_backup.py#L40-L41)
- **再現条件**: Windows 環境で [`unseal_file`](src/mina/encrypted_backup.py#L140-L167)（復号）を実行した際、親フォルダが第三者アクセス可能な共有フォルダであっても、[`check_private_parent`](src/mina/encrypted_backup.py#L75-L91) が呼ばれず `posix` チェックのみですり抜ける。
- **影響**: 復号された平文の復旧データ（DB、認証ファイル群）が意図しない公開領域に書き出される。
- **最小修正案**: [`encrypted_backup.py:40-41`](src/mina/encrypted_backup.py#L40-L41) で `check_private_parent(path.parent)` を呼び出す。

#### 指摘 B: 復元処理における送信中過渡状態（`dispatching` / `sending`）の未確定保持と運用スタックリスク
- **重大度**: **中（Major / 運用性・整合性）**
- **対象行**: [`src/mina/recovery_set.py:195-198`](src/mina/recovery_set.py#L195-L198)、[`src/mina/store.py:84-85`](src/mina/store.py#L84-L85)
- **再現条件**: X 公開処理中（`dispatching`）や Discord 通知中（`sending`）のクラッシュ等でバックアップされた台帳を復元した場合、失効対象が `approved`/`approval_pending`/`pending` のみに限定されているため、`dispatching` や `sending` のまま残る。
- **影響**: 復元後、外部履歴照合を経て手動で再点検・候補差し替えを行おうとしても、[`Store.save_many`](src/mina/store.py#L84-L85) の「送信開始済みの投稿は差し替えできません」によって永久にブロック（スタック）する。
- **最小修正案**: 失効条件に `dispatching` および `sending` を含める。

#### 指摘 C: Windows 環境における確定リネームの例外型不一致と確定先ディレクトリ ACL の未保護
- **重大度**: **低（Low / プラットフォーム一貫性）**
- **対象行**: [`src/mina/recovery_set.py:116-118`](src/mina/recovery_set.py#L116-L118)
- **影響**: 並行作成時に Linux（`ValueError`）と異なり Windows では `FileExistsError` が送出される。また、リネーム確定後の `output` ディレクトリに対して明示的な [`protect_directory`](src/mina/encrypted_backup.py#L53-L73) が適用されていない。
- **最小修正案**: `os.rename` の `OSError` を `ValueError` に正規化し、確定後に `protect_directory(output)` を呼び出す。

#### 指摘 D: [`backup_artifacts`](src/mina/artifact_backup.py#L48-L60) における Windows 親ディレクトリ保護検査のスキップ
- **重大度**: **低（Low / プラットフォーム一貫性）**
- **対象行**: [`src/mina/artifact_backup.py:55-56`](src/mina/artifact_backup.py#L55-L56)
- **影響**: Windows 環境で成果物バックアップ時に第三者アクセス権の検査がスキップされる。
- **最小修正案**: [`check_private_parent`](src/mina/encrypted_backup.py#L75-L91) を呼び出す。

#### 指摘 E: ZIP 展開時における大文字小文字同一視環境でのファイル名衝突耐性
- **重大度**: **低（Low / 堅牢性）**
- **対象行**: [`src/mina/recovery_set.py:146-154`](src/mina/recovery_set.py#L146-L154)
- **影響**: ZIP 内に大文字小文字のみが異なる同名ファイルが存在する場合、事前検査をすり抜けてファイル作成時に `FileExistsError` で異常終了する。
- **最小修正案**: 事前検査ループで `name.casefold()` による重複も検査し `ValueError` で拒否する。

---

### 3. 未確認範囲

本レビューは配布コードに対する読取専用の静的解析に基づきます。以下の項目は未確認（未実施）です：
1. **外部サービスとの実認証・実通信の動作**（X API 実認証・実投稿、Discord Bot 実 Gateway 接続・実操作、LogEats 本番内部 API の正規リリース・受入、Google Drive OAuth の本番継続性）
2. **本番運用環境の実地受入・無人復帰**（サブPCでの実通し処理所要時間計測、Windows 再起動後の WSL・systemd 無人復帰、実復号鍵の生成・隔離保管、Google Drive への実暗号化バックアップ保管・復号検証）
3. **LogEats 本体のリリース完了**（LogEats 側の本番マイグレーション・正式リリース）

---

### 4. 結論

> [!CAUTION]
> **本番開始の非承認通知（継続）**
> 前回の 6 指摘事項への適切な修正を確認しましたが、今回の再レビューにおいて新たに **`save_new` における Windows 親ACL検査スキップ（指摘 A）** および **復元台帳における過渡状態の未確定保持（指摘 B）** などの課題が検出されました。
> 
> 安全原則（高・中の指摘がなくても本番開始を承認しない方針）および未確認範囲（実環境・実認証・実地受入）に鑑み、**本コードによる本番運用の開始は承認いたしません**。
読取専用独立再レビューの全検証工程が完了しました。
検証結果および結論の総括は、作成したアーティファクト walkthrough.md（アプリ内保存） および詳細報告書 independent_recovery_rereview_4ceffb7.md（アプリ内保存） に記録しています。

### 総括
- **前回 `6af579b` の 6 指摘**: 全件適切に修正され、**解消**を確認しました。
- **新規検出事項**: 中 2 件（`save_new` の Windows 親ACL検査スキップ、過渡状態 `dispatching`/`sending` の未確定保持）、低 3 件を指摘しました。
- **本番運用判定**: 運用安全原則および実地受入検証未完了に基づき、**本番運用の開始は非承認（継続）**と判定します。


## 主担当の受領・判断

終了コード0、配布109ファイルの無変更を確認。原報告SHA-256: `322db5106073be0a9f6d611d02f6b6358e543f954e0d4cb4aea6ee8553a5dfa2`。前回6指摘の解消と、新規中2件・低3件を受領した。アプリ自身の報告保存も通知されたため、全ファイルシステム無書込みとは主張しない。

Aはsave_newから親ACL検査を呼ぶ修正を採用する。Cは確定前の私有ディレクトリ保護とWindows例外正規化で対応し、確定後の権限変更失敗で中間状態を作らない。Dは成果物バックアップの親検査・私有一時領域へ反映する。Eは大小文字だけで異なる親名も検査し、Windows予約名・末尾の点や空白も拒否する。

Bの送信中状態を失効して差替え可能にする提案は採用しない。送信済みか不明な枠の再送に繋がり、成否不明からの自動再送禁止と矛盾するためである。表示上の送信中はoutcome_unknownへ移すが、公開済み・不明の証跡、元アーカイブ、アプリ起動停止を維持する。外部履歴照合と明示的な運用判断を経ずに候補差替えや再送を許可しない。この判断を独立レビュー側の解消判定と読み替えない。

修正後の再レビュー・サブPC検証が必要。実バックアップ作成・Drive保存・本人による復号検証はまだ完了していない。
