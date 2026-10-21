# 独立コードレビュー報告書（追加差分検証）

- **対象コミット**: 固定コミット `ed0fd771db0a167b0321ff5a0469ef7da6e67609`
- **対象ディレクトリ**: 固定版の保護された展開ディレクトリ配下の配布コードのみ
- **作業形態**: 読取専用・外部通信なし・コマンド実行なし・ファイル変更なし・設定/認証読取なしの独立コードレビュー
- **参照要求仕様**: [AGENTS.md](AGENTS.md) および [INDEPENDENT_REVIEW_REPORT_20261012.md](INDEPENDENT_REVIEW_REPORT_20261012.md)
- **対象差分**: 前回固定版 `efc1f2b` から [src/mina/handover.py](src/mina/handover.py) の `STOP_FLAGS` に履歴2項目を追加し `history_mode` 説明を更新した5行の変更

---

## 1. 8停止項目の照合結果（欠落・条件誤り・副作用）

[src/mina/handover.py](src/mina/handover.py) の変更差分を、[tests/mina/test_handover.py](tests/mina/test_handover.py)、[src/mina/diagnostics.py](src/mina/diagnostics.py)、[deploy/run-mina-doppler.sh](deploy/run-mina-doppler.sh)、および [src/mina/__main__.py](src/mina/__main__.py) と突合・精査しました。

### 1-1. 8停止項目の定義と欠落確認

| # | 停止フラグ名 | [handover.py](src/mina/handover.py#L12-L14) (`STOP_FLAGS`) | [diagnostics.py](src/mina/diagnostics.py#L35-L36) (`switches`) | [run-mina-doppler.sh](deploy/run-mina-doppler.sh#L6-L15) (export) | [run-mina-doppler.sh](deploy/run-mina-doppler.sh#L27) (`--preserve-env`) | 判定 |
| :-: | :--- | :---: | :---: | :---: | :---: | :---: |
| 1 | `MINA_AI_ENABLED` | 記載あり (L12) | 記載あり (L36) | 記載あり (L11) | 記載あり (L27) | **適合（欠落なし）** |
| 2 | `MINA_LIVE_WRITES` | 記載あり (L12) | 記載あり (L36) | 記載あり (L6) | 記載あり (L27) | **適合（欠落なし）** |
| 3 | `MINA_PUBLISH_ENABLED` | 記載あり (L12) | 記載あり (L36) | 記載あり (L8) | 記載あり (L27) | **適合（欠落なし）** |
| 4 | `MINA_APPROVAL_ENABLED` | 記載あり (L13) | 記載あり (L36) | 記載あり (L7) | 記載あり (L27) | **適合（欠落なし）** |
| 5 | `MINA_PREPARE_ENABLED` | 記載あり (L13) | 記載あり (L36) | 記載あり (L12) | 記載あり (L27) | **適合（欠落なし）** |
| 6 | `MINA_READ_REVIEW_ENABLED` | 記載あり (L13) | 記載あり (L36) | 記載あり (L13) | 記載あり (L27) | **適合（欠落なし）** |
| 7 | `MINA_X_HISTORY_READ_ENABLED` (追加) | 記載あり (L14) | 記載あり (L36) | 記載あり (L14) | 記載あり (L27) | **適合（欠落なし）** |
| 8 | `MINA_HISTORY_AUTO_ENABLED` (追加) | 記載あり (L14) | 記載あり (L36) | 記載あり (L15) | 記載あり (L27) | **適合（欠落なし）** |

- **照合結果**:
  前回固定版 `efc1f2b` において 6 項目だった [src/mina/handover.py:12-14](src/mina/handover.py#L12-L14) の `STOP_FLAGS` に、履歴関連の 2 項目（`MINA_X_HISTORY_READ_ENABLED`, `MINA_HISTORY_AUTO_ENABLED`）が正しく追加され、合計 8 項目が完全に揃っていることを確認しました。欠落はありません。

---

### 1-2. 条件判定の論理整合性と条件誤り確認

1. **引継ぎ診断時の停止判定 ([src/mina/handover.py:35](src/mina/handover.py#L35))**:
   ```python
   'all_stop_flags_false': all(env.get(name, 'false').lower() == 'false' for name in STOP_FLAGS),
   ```
   - **判定論理**:
     - 環境変数が未定義の場合、デフォルト値 `'false'` となり通過（`True`）。
     - 環境変数が `'false'`（大文字小文字不問）の場合にのみ通過（`True`）。
     - 環境変数が `'true'` またはそれ以外の文字列（空文字 `""` や `"1"` など）の場合、不合格（`False`）。
     - 全8項目すべてが `'false'`（または未設定）であること（`all(...)`）を要求しており、論理積の取り違えや反転等の条件誤りはありません。
     - 安全側（Fail-Safe）に倒れた判定であり、追加された履歴2項目も同一条件で厳格に判定されます。

2. **サービス起動ラッパーの環境変数保護 ([deploy/run-mina-doppler.sh:6-15, 27](deploy/run-mina-doppler.sh#L6-L15))**:
   - `export MINA_X_HISTORY_READ_ENABLED="${MINA_X_HISTORY_READ_ENABLED:-false}"`
   - `export MINA_HISTORY_AUTO_ENABLED="${MINA_HISTORY_AUTO_ENABLED:-false}"`
   - `--preserve-env=...,MINA_X_HISTORY_READ_ENABLED,MINA_HISTORY_AUTO_ENABLED`
   - シェルのデフォルト展開構文 `${VAR:-false}` により未設定時は確実に `false` となり、Doppler 起動時の `--preserve-env` によりリモート設定による停止フラグの上書きを防止しています。条件誤りや構文エラーはありません。

3. **機能コード側の二重防壁 ([src/mina/clients.py:117](src/mina/clients.py#L117), [src/mina/__main__.py:285](src/mina/__main__.py#L285))**:
   - `MINA_HISTORY_AUTO_ENABLED` が `'true'` の場合のみ自動履歴取得ルーチンが起動し、かつ X クライアント内部でも `MINA_X_HISTORY_READ_ENABLED` が `'true'` でない限り実読取を例外で拒否する二重構造となっています。

---

### 1-3. 副作用確認

1. **`production_start_allowed` への影響**:
   - [src/mina/handover.py:50](src/mina/handover.py#L50) において `'production_start_allowed': False` は固定値としてハードコードされており、追加差分によって本番開始が許可される副作用はありません。
2. **外部通信の発生有無**:
   - `STOP_FLAGS` のチェック処理は渡された辞書/環境変数のキー検索と文字列比較のみで完結しており、外部ネットワーク呼び出しやディスク書き込み等の副作用は一切ありません（`'external_calls_performed': False` は維持）。
3. **`history_mode` 説明文の更新 ([src/mina/handover.py:56](src/mina/handover.py#L56))**:
   - 更新内容: `'history_mode': '初回履歴と旧予約停止を確認し、運用時はAgentが14日履歴を取得・照合する'`
   - 配布コード全体（`readiness.py`, `daily_pipeline.py`, `handoff_packet.py`, `__main__.py` 等）を精査した結果、この文字列を機械的に比較・分岐させているコードは存在せず、純粋な人間向けの運用注記（メタデータ）として機能しています。コード上の副作用や互換性破壊はありません。
4. **合成試験 ([tests/mina/test_handover.py](tests/mina/test_handover.py)) への影響**:
   - [test_handover.py:38, 43](tests/mina/test_handover.py#L38) は `STOP_FLAGS` のループ処理を行っているため、要素数が 6 から 8 に増加したことで、追加された 2 項目についても自動的に `name: 'true'` 時の拒否テストが実行される構造となっており、既存テストの破綻はありません。

---

## 2. 指摘事項（個別レビュー結果）

重大度：高（Critical）および重大度：中（Major）に該当する脆弱性・仕様違反・不整合などの未解決指摘事項は検出されませんでした。
設計・テスト品質の観点から確認された軽微な留意点（重大度：低 / Info）は以下の 2 点です。

### 指摘 1: `test_handover.py` における停止項目定義の循環参照と完全性の未検証
- **重大度**: 低（Low / テスト品質）
- **対象ファイルと行番号**: [tests/mina/test_handover.py:9, 38, 43-46](tests/mina/test_handover.py#L9-L46)
- **具体的な再現条件**:
  テストコードが被テストモジュールから `from src.mina.handover import STOP_FLAGS` を直接インポートし、ループで反転テストを実行している。もし将来、`handover.py` 内の `STOP_FLAGS` から誤って項目が削除されたりタイポが発生したりした場合でも、テスト側は短縮されたリストのみを検証するため、テストスイート全体が正常終了（Pass）してしまう。
- **影響**:
  停止項目自体の欠落やタイポといった退行（リグレッション）をテストコード単体で検知できない。
- **最小修正案**:
  [tests/mina/test_handover.py](tests/mina/test_handover.py) 内に、期待される固定の8項目セット（`EXPECTED_STOP_FLAGS = frozenset({...})`）を独立定義し、`self.assertEqual(set(STOP_FLAGS), EXPECTED_STOP_FLAGS)` および `self.assertEqual(len(STOP_FLAGS), 8)` をアサートする独立したテストケースを追加する。

### 指摘 2: `handover.py` と `diagnostics.py` 間での停止スイッチ一覧の二重定義
- **重大度**: 低（Low / 保守性）
- **対象ファイルと行番号**: [src/mina/handover.py:12-14](src/mina/handover.py#L12-L14) / [src/mina/diagnostics.py:35-36](src/mina/diagnostics.py#L35-L36)
- **具体的な再現条件**:
  [src/mina/handover.py](src/mina/handover.py) では `STOP_FLAGS` として定数定義されているが、[src/mina/diagnostics.py](src/mina/diagnostics.py) の `diagnose()` 内では同一の 8 項目がインラインのタプルリテラルとして直書きされている。
- **影響**:
  将来停止スイッチを追加・変更した際に、片方のみが更新されて不整合が生じるリスク（前回 `efc1f2b` で `handover.py` が 6 項目のまま取り残されたのと同様のリスク）が残る。
- **最小修正案**:
  共通定数（例: `src/mina/domain.py` や `diagnostics.py`）に定義を一元化し、各モジュールがそれをインポートして参照する構成とする。

---

## 3. レビュー境界と未確認範囲

本レビューは、固定コミット配布コード（`ed0fd771db0a167b0321ff5a0469ef7da6e67609`）に対する読取専用の静的解析およびコード突合に基づいています。以下の項目は今回のレビュー範囲外（未確認）です。

1. **実環境における Doppler および systemd の動的挙動**:
   - `deploy/run-mina-doppler.sh` の `--preserve-env` 指定が、実際のサブPC（WSL / Ubuntu）上の Doppler CLI 実実行において、ローカルの `false` 値を 100% 確実に維持し、Doppler 側のシークレット値で上書きされないことの実機動作確認。
2. **外部サービスとの実認証・実通信の動作**:
   - `MINA_X_HISTORY_READ_ENABLED=true` 時に呼び出される X API（v2）の実タイムライン取得（`users/:id/tweets`）における実通信、レート制限挙動、実レスポンスの構造整合性。
   - `MINA_HISTORY_AUTO_ENABLED=true` における 19:00〜20:00 JST 準備窓外での実行遮断の実動作。
3. **LogEats本体側の完了状態**:
   - LogEats本番内部API（`/api/internal/mina/*`）の正規リリース後の実受入・DB書き込み、および移行後処理の完了確認。
4. **過去バックアップからの復元時の重複公開防止**:
   - 稼働台帳が過去バックアップから復元された場合、`published` 状態が巻き戻る可能性があるため、停止維持および実投稿と復元時点以後の台帳照合を省略せず実施すること（現存バックアップの状態だけで重複公開を防げるとは扱わない）。

---

## 4. 結論

> [!IMPORTANT]
> **本番開始の非承認通知（継続）**
> 配布コード（コミット `ed0fd771db0a167b0321ff5a0469ef7da6e67609`）において、追加された 5 行の差分（`STOP_FLAGS` への履歴2項目追加および `history_mode` 更新）を精査した結果、**8停止項目の欠落・条件誤り・有害な副作用は確認されず、正常に8項目が停止スイッチとして網羅されていることを確認しました**。
>
> しかしながら、依頼事項および安全管理規定に基づき、**過去の結果や今回の差分適合性のみをもって本番運用の開始を承認することはいたしません**。
> 上記の未確認範囲（実機でのDoppler動的挙動、実サービス認証、LogEats本体側のリリース受入、台帳復元時の照合等）の実地検証と別途定められた移行工程をすべて完了させた上で、運用の判断を行ってください。


## 主担当の受領・対応

終了コード0、展開105ファイルの無変更を確認。原報告SHA-256: `9be0c749450ff7349895b8d67780e38f5ae44cc7855ce6b01628abccf800baeb`。今回はplanモードの無効化警告なし。重大な未解決指摘なし。軽微な2件について、停止項目をdiagnostics.pyへ集約し、試験には期待8項目の独立定義・件数照合を追加した。対応後の変更はこの固定版レビューには含まれない。
