# 一食置換の本番導入・受入残件

2026年10月23日。本人が本番反映を希望し、追加従量課金を1日100円までと指定した。UTMはXから開始する。

## 実読取で確認した未達条件

本番稼働commitは910419a0c633f33f30f1c53fa544f559c53de0ae。23日precondition GETは200だが、policyはunconfigured、budget_configured=false、budget_policy_digest=null、existing_preparation_deadline_verified=false、hard_deadline_at=null。停止理由はservice_unavailable/budget_unconfigured/deadline_unverified。24日は未完成日のため422/day_composition_invalid。

上限100円の本人指定は受領済みだが、提供側の配備・予算割当・実単価・既消費の受入を代替しない。Marketing AgentからLog-Eats本体のコード・DB・設定は変更していない。過去18日分の期限延長・再登録・置換は対象にしない。

## Agent側で進めたこと

最新稼働910419aを基点に、隔離実装a8fcaa3を統合した。Instagram復旧承認の実公開起点を保持し、置換停止中の新版承認制御と共存させる。既存のX文字数対策・Instagram受付IDの自動照会を保持する。統合とテストは別worktreeで行い、本番には未配置・無効のまま。検証結果を追記する。

## Log-Eats担当へ渡す依頼

保存済み一食置換mina-replace-v1をMarketing Agentから本番利用したい。本人は追加従量課金上限を1日100円と指定した。現行preconditionは上記3理由で開始不能。

1. 現在の本番機能の配備commit、QA/PreviewでのP/D/C・応答喪失・GET復旧・画像取得・予算/期限・公開停止の受入証跡と、正式Schemaのパス/SHAを提示する。
2. 100円/日をAgent生成と本体解析の共通上限とし、既消費・成否不明の費用を含む割当、各呼出しの最大額、通貨/金額単位、policy_version/budget_policy_digestを合意する。AI生成はChatGPT認証利用中だが、本体側の解析費用まで0円と仮定しない。
3. 対象日の前日20:00 JSTと初回再計画から15分の早い方を期限とし、最大2回の試行を再起動やUUIDでリセットしない。過去日の期限を自動延長しない。
4. service_unavailable/budget_unconfigured/deadline_unverifiedを解消する正式な提供側設定と運用手順を示し、設定反映後の読取確認が可能になったら報告する。単に検証フラグをtrueにしたり仮の金額を設定したりしない。
5. 既存承認・食事・公開済み投稿を保全する。API提供側の変更はそちらの承認・配置手順で実施し、他の担当チャットへ自動送信しない。

Agent側では返答後、実Buffer全状態/全ページ列挙、台帳コピーの移行dry-run、100円上限と呼出し前費用予約、新版X/Instagramの再承認、切戻しを受け入れたうえで有効化する。

## 現在の結論

提供側の未達条件が解消されるまで、置換機能の有効化は行わない。UTMは独立して配布可能とし、置換未受入コードをUTMの本番配布へ混ぜない。
