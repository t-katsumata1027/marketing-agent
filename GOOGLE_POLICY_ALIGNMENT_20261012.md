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

## 公開中本文の実取得確認

2026年10月12日、認証なしのHTTPS GETで/privacyと/termsを取得した。両方200でwww.log-eats.comへ転送。HTMLのscript/styleを除いた本文を確認した。OAuth画面の実登録URLとの一致は別途未確認。

- privacy HTML SHA-256: 5ce47849f1357e980309a2fc3ef9617e73a0b153ba5a272f62e0a6a1ebf272b4。
- terms HTML SHA-256: 1ede510218294d5add374259f3b431f89d802bc84587707f60e11e83243fcfb9。
- 両本文にGoogle Drive・Mina専用運用の明示は見つからなかった。
- 公開privacyの第3節は、識別可能な食事画像・自由入力文を独自AIの学習に利用しないと説明する。一方、公開termsの第3・4条には入力画像・テキスト等の学習利用を許容し、自社または第三者AIモデルの構築・訓練・改善への同意を求める説明がある。実際の運用を確認して両本文を整合させる必要がある。法的評価や適法性の判定ではなく、公開説明の差の記録である。

公開ページはhttps://www.log-eats.com/privacy と https://www.log-eats.com/terms。生HTMLはGit対象外のruntime/public-policy-20261012へ保存した。公開中の本文が手元の作業中ソースと一致するとは判断しない。

### LogEats側への最小追加指示

実装範囲を広げず、公開本文のAI学習説明の不整合を解消してください。実際のLogEats解析とMarketing AgentのCodex CLIを別に確認し、第三者のデータ取扱いを推測で保証しないでください。Google利用説明案を運用者向けの説明へ反映し、drive.fileによる専用ファイル利用、暗号化バックアップ、取消し・削除・保持の扱いを実装と一致させてください。窓口とOAuthの公開リンクも照合してください。修正案と差分を準備し、公開反映は既存の正規リリースと承認境界に従ってください。本番M1接続・X公開の停止は維持してください。
