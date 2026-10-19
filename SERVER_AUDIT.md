# サブPC稼働環境の確認

確認日: 2026年10月7日（日本時間）

## 接続

- 接続先: `192.168.0.20`、Windowsホスト名: `KATSU-Server1`。
- Windowsアカウント: `logeats-runner-win`。
- メインPCに既存のSSH鍵があり、公開鍵認証で接続成功。パスワードの入力・変更・リセットは不要だった。
- 既知ホスト鍵の照合を有効にして接続した。

## 確認できた構成

| 項目 | 結果 |
|---|---|
| CPU | Intel Core i5-12500 |
| Windowsの表示バージョン・ビルド | 25H2・26200。製品名レジストリは旧表記を返したため、それだけでは製品名を判定しない |
| WSL | `LogEats-Runner`、WSL2、稼働中 |
| Linux | Ubuntu 24.04.4 LTS、systemd有効 |
| WSL既定ユーザー | `logeats-runner-win` |
| WSLから見えるCPU | 12論理CPU |
| WSLから見えるメモリ | 合計約15GiB、利用可能約14GiB（調査時点） |
| WSLファイルシステム | 約1007GB中12GB使用、空き944GB。仮想ディスク側の表示でありWindows物理ディスクの空き容量ではない |
| WSL稼働時間 | 約20日 |
| Python | WSL内は3.12.3。本プロジェクトの指定は3.12.12 |
| Docker | Windows側のCLIは存在するがエンジン接続不可。WSL側でもDocker Desktop連携が使用できない |

## 既存GitHub Actionsランナー

どちらも`https://github.com/t-katsumata1027/LogEats`に登録され、systemdのサービスが`enabled / active / running`。各`Runner.Listener`のプロセスを確認した。調査時点で`Runner.Worker`はプロセス一覧に見当たらなかった。GitHub側のオンライン表示・直近ジョブ成功は未確認。

| ランナー | 実行ユーザー | 配置先 |
|---|---|---|
| `logeats-secure-01` | `logeats-runner` | `/opt/actions-runner-logeats` |
| `logeats-ops-01` | `logeats-ops-runner` | `/opt/actions-runner-logeats-ops` |

WSL内のsystemd起動設定は確認済みだが、Windows再起動後にWSLを自動起動するタスク設定までは未確認。

## 本プロジェクト配置に向けた残事項

- 調査時点のWSL負荷とメモリには余裕がある。ただし、ランナーのジョブ実行中のピーク負荷・Windows物理ディスク残量は未確認で、同居の余力を保証するものではない。
- 現行Docker Composeを使うにはDocker実行基盤を整える必要がある。サーバー向けの起動方式と再起動後の復帰方法を決める。
- ランナーの`_work`とは別に、マーケティングエージェント専用の配置先・実行ユーザー・秘密情報を用意する。
- Python・依存関係、Supabase、Discord、X、日次予約処理を配置・検証する。
- 読み取り可能な`/home/logeats-runner-win`、`/opt`、`/srv`の深さ3までには、名前に`marketing`を含むディレクトリが見当たらなかった。全ホストで未配置と断定するものではない。
- WindowsのCIM・サービス一覧はSSHアカウントの権限で拒否された。ディスク容量の代替取得も有効な値を返さず、Windows側のメモリ・物理ディスク・サービス詳細は未確認。

## 操作範囲

SSHによる読み取りのみ。既存ランナーの停止・再起動、Dockerの起動や導入、設定変更、資格情報の変更は行っていない。稼働環境はサブPCを前提とし、メインPCの依存不足やDocker停止状態をサブPCの状態と混同しない。

## 後続のアプリSSH接続設定（2026年10月7日）

上記の操作範囲は初回調査時点の記録。後続のユーザー依頼に基づき、以下を追加した。

- `katsu-server1`: Windows OpenSSHへ既存鍵で接続する管理用の別名。Windows側のCodex CLI 0.160.1をSSHユーザーへ導入。
- アプリの接続ログで、鍵認証後にPOSIXの`sh`と`[`がWindowsのコマンド環境で失敗することを確認。
- `katsu-server1-wsl`: 既存Windows SSHをProxyCommandで経由し、WSLの`127.0.0.1:2222`へ接続する開発用の別名。
- Linux OpenSSHを導入し、`codex-wsl-ssh.service`を有効化。待受はループバックのみ、公開鍵認証のみ、rootログイン不可、許可ユーザーは`logeats-runner-win`だけ。標準のssh.service/ssh.socketは新規導入後に無効化し、独立したサービスで待ち受ける。
- WSLの許可ユーザーへ既存の公開鍵を登録。ホスト鍵は認証済みWindows SSH経由で取得し、メインPCのknown_hostsへ固定した。
- Linux版Codex CLI 0.160.1をユーザー領域へ導入し、`sh -lc`から起動できることを確認。Codexログインは未完了。
- みな検証サービスと既存GitHub Actionsランナー2件のactiveを確認。

アプリ画面では`katsu-server1-wsl`を新規追加する。Windows側の接続は管理用として引き続き使用可能。アプリでの登録完了・ログイン・プロジェクト選択はユーザー操作後に確認する。

### 登録完了の確認

- ユーザー提供の画面で、`katsu-server1-wsl`の「接続済み」を確認した。
- SSH経由のログインシェルで`codex login status`を実行し、`Logged in using ChatGPT`を確認した。
- `codex-wsl-ssh.service`、`mina-agent.service`、既存GitHub Actionsランナー2件がすべてactiveであることを再確認した。
- 今後のアプリ開発接続には`katsu-server1-wsl`を使用する。Windows用の`katsu-server1`はアプリ上では無効化できるが、WSL接続の経由先にも使うためSSH設定から削除しない。
- プロジェクトの開発フォルダー登録は別途必要。現在の`/opt/marketing-agent`はサービス配置先であり、開発用のGitチェックアウトとは区別する。

### 開発フォルダーと常時運用の追加調査

- `/home/logeats-runner-win/projects/marketing-agent`へ、Git履歴と現在の未コミット実装を配置。転送した開発ファイル69件のSHA-256一致を確認した（後続の文書更新・AGENTS.md追加は別途転送）。
- 同ユーザー配下に専用の開発仮想環境を作成し、みな専用テスト37件と依存整合性を確認した。
- 引き継ぎ文書10件を`/home/logeats-runner-win/projects/handoff/logeats-marketing-20261007`へ参照用コピーとして配置した。
- Windows Cドライブの空き159,404,818,432バイト（約148.5GiB）、合計254,196,838,400バイトを.NETのDriveInfoで取得した。
- 電源設定はバランス。AC電源時の自動スリープは0秒（無効）、DC時は600秒だった。
- Windowsの予定タスク一覧はCIMアクセス拒否で取得できなかった。再起動後のWSL起動は引き続き未確認。今回の作業ではWindowsの再起動や電源設定変更を行っていない。
