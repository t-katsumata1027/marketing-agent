# Google利用説明とLogEats文書の照合

2026年10月12日。対象はMarketing Agentのコードと、手元のLogEats src/app/privacy/page.tsx・terms/page.tsx。公開中の本文・OAuth画面のリンク先と同じかは未確認。LogEats側ファイル・公開設定は変更していない。

Google公式ではdrive.fileはアプリが使用する個別ファイルを対象にする非機密スコープとして説明されている。権限の分類だけで、公開説明・審査・運用全体を完了したとは判断しない。[Driveスコープの公式説明](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)。Googleデータの用途は公開ポリシーの説明と対応させる必要があるため、取得・保存・外部処理・削除の説明を実装へ合わせて確認する。[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy)。

## コードから確認した差

| 項目 | 手元のLogEats本文 | Marketing Agentで説明する内容 |
| --- | --- | --- |
| Drive | 明示なし | 運用者の専用OAuthとdrive.fileで、専用画像・暗号化復旧セットを保存・再取得。無関係なDrive全体を収集しない |
| AI | LogEats解析のGemini／OpenAI API | Agentの生成・点検はChatGPT認証のCodex CLI。APIへの自動退避は無効。LogEats側の解析とは処理とデータ取扱いを分ける |
| 共有・承認 | 利用者の共有操作と公開共有URL | Mina専用運用ではAPIで共有ページを準備し、Discordで本人承認後にXへ送信する。ページ作成とX公開の時点を混同しない |
| バックアップ | 保存・削除の一般説明 | 運用台帳・画像・履歴・認証は暗号化して専用Driveへ保存。秘密鍵・パスワードは同梱しない。鍵予備は本人指定のiCloudで別保管 |
| 取消し・削除 | 一般利用者の記録・退会 | OAuth取消しでDrive処理を停止。一つの保存先の削除だけで別媒体・バックアップも消えたとは扱わない。要求／公開の不明状態は保全する |
| 窓口 | support@log-eats.com | Agentにも同じ窓口を使うか、受信と本人確認を運営者が確定する。受信体制はこの担当では未確認 |

## LogEats側へ渡す最小確認

1. GOOGLE_DATA_USE_DRAFT.mdを、Mina運用者向けの独立説明にするか既存ポリシーへ含めるかを決める。一般ユーザーの全食事データをAgentが読むような説明にしない。
2. 運営窓口、保持方針、アクセス取消し後の各保存先の扱いを確定する。現在は自動削除なし。30世代／10GiBの整理警告は保持期限・消去保証ではない。
3. CLIの実際のデータ設定と、LogEats解析プロバイダーの設定を別に確認する。Agentが学習データセットを実装していないことだけで、外部サービスの取扱いを保証しない。
4. 公開文書とOAuthブランディングの実リンク・アプリ名を対応付ける。Google公開ステータスの本番化と、説明文書の整合は別に確認する。

この確認のために新スコープ・新資格情報・設定変更・課金呼出しを追加しない。文書反映をM1の新API機能追加へ広げず、既存の正規リリース作業へ最小の確認事項として引き継ぐ。
