# サブPCでの開発

## 2026年10月19日19:24・InstagramとXの同時実行停止を修正

稼働ade7556を保持したf35a7edを配置し、X送信中の状態をInstagramの食事不適合と扱う競合を解消した。Windows/Linux各409件・CI・実データ4枠と画像8枚・私用台帳コピーによる3状態×4枠の照合に成功。切替時の24台帳を保持し、本人の明示依頼による夕食だけの復旧を通常公開処理で実施。19:24:59 JSTのBuffer sentと実公開URL、完了通知sentを確認した。X候補・X経路・他のInstagram行と日次承認は不変。3サービス・3タイマーともactive。保護バックアップは`/var/lib/mina-agent/deployment-instagram-dinner-f35a7ed4ae`。詳細は`MINA_INSTAGRAM_DINNER_RECOVERY_20261019.md`。

## 接続と配置

ChatGPTアプリのSSH接続は`katsu-server1-wsl`を使用する。Windowsへの管理用SSH別名`katsu-server1`は、この接続の経由先なのでSSH設定から削除しない。

| 用途 | サブPC内のパス |
|---|---|
| 開発用Gitチェックアウト | `/home/logeats-runner-win/projects/marketing-agent` |
| 引き継ぎ文書の参照用コピー | `/home/logeats-runner-win/projects/handoff/logeats-marketing-20261007` |
| サービス用コード | `/opt/marketing-agent` |
| サービス台帳・heartbeat | `/var/lib/mina-agent` |
| 保護された運用設定 | `/etc/marketing-agent/mina.env` |
| 保護されたDopplerサービス用認証 | `/etc/marketing-agent/doppler.env` |

開発用チェックアウトにはメインPCのGit履歴と未コミットの実装をコピーした。GitHubへのpushは行っていない。サービス用コードは別配置で、開発フォルダーで編集しても自動反映されない。参照用文書は2026年10月7日時点のコピーであり、正本はLogEatsの`docs/marketing`である。

LogEats本体にはメインPCで未コミットの変更が複数あるため、この作業では本体の開発先を切り替えない。将来同じSSH接続を使用できるが、切り替え時に履歴・未コミット変更・依存・認証を別途移す。

## アプリへのプロジェクト追加

設定の接続一覧で、`katsu-server1-wsl`行のフォルダー追加ボタンから、上記の開発用Gitチェックアウトを選ぶ。既存のメインPCプロジェクトと混同しないように、サブPC用であることが分かる表示名にする。

## 開発用の確認

```sh
cd /home/logeats-runner-win/projects/marketing-agent
.venv-mina/bin/python -m unittest discover -s tests/mina -q
.venv-mina/bin/python -m src.mina doctor
```

開発用の診断は開発環境の設定と台帳を参照する。サービス設定・台帳は専用ユーザーとrootの権限で保護されるため、開発ユーザーへ秘密ファイルの閲覧権限を追加しない。サービス側の診断は管理経路で設定を読み込み、`mina-agent`ユーザーとして実行する。診断結果には設定値、投稿本文、操作nonceを出さない。

`doctor`は設定の不足名、翌日の完成枠数、台帳の状態別件数、heartbeat、外部状態の照合が必要かを返す。DBがない場合も新規作成せず、診断だけでは書き込みや公開を行わない。設定が揃っても、候補生成・独立レビュー・HTTPS承認経路の実動確認が必要である。

## 継続時の条件

- ドキュメントとコード内コメントは日本語で記載する。
- 毎日20:00 JSTに翌日4枠の承認依頼を届ける。公開はDiscord承認後にX APIで行う。
- 既存GitHub Actionsランナー2件の稼働を維持する。
- 本番設定値や台帳内の承認トークンをチャット・Git・ログへ出さない。
- 献立・画像・固定本文の実際の確認結果がない状態で、品質確認済みのフラグを作らない。
- 本番の書き込み・承認受付・X公開は独立した設定で管理する。現在はすべて無効。

DiscordのGateway承認サービスは認証済み接続を確認済みだが、承認受付は停止中。アプリ画面でBotがオンラインでも、候補の自動生成・通知・承認・公開の全体が完成したことを意味しない。
