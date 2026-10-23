# Log-Eats担当への一食置換policy受入・設定確認

2026年10月23日。本人は100円/日を撤回し、金額上限なしへ変更すると明示回答した。

Marketing Agentは正式Schema 4f8d2be8f47a741ff8578a25c29bd6422cf88ebd817f8450339eaee76216f6e2へ追従し、waived/cooldown/元UUID・制御UUID照会/明示resume/公開排他/旧承認失効を実装した。実装68bead2を、別作業の手動履歴対応を保持する8ac2fdc118fcfebde07a23053364de884aeb7254として本番配置済み。ただし置換OFF、UTM ON。

Windows/隔離Linux各541件・同コードCI39944266392の両jobが成功。実APIの新Schema照合、Buffer固定2接続先の全ページ読取、実台帳コピーの既存27表・全既存列不変を確認した。詳細と範囲はMINA_REPLACEMENT_GEMINI_ACCEPTANCE_20261023.md、構造化証跡はinvestigations/replacement-gemini-20261023/acceptance.json。

外部APIのP/D/C/resume POST・実Gemini課金・既存食事置換は0件。これらの実動受入済みとは主張しない。上記証跡でagent_integration_verifiedの確認対象を満たすかを評価し、不足があれば対象・期待結果を具体的に提示してほしい。確認前にtrueへ補完しない。

## 両側で固定するpolicy案

budget_policy_digestは同梱waived-policy-proposal.jsonのagent_policyをRFC8785で正規化して計算した。承認参照digestは同ファイルのapprovalを正規化して計算した。版やdigestを片側だけで変えない。

```json
{
  "agent_integration_verified": false,
  "analysis_provider": "gemini",
  "approval_reference_digest": "8747bbcffcedfe18baa09e59ea0d25985d3c8472c47bad31e93554cb305d6083",
  "budget_policy_digest": "244918faac42ea4b91b96116c75b251c9361b9a1389a88503b7ba6aedb07e79b",
  "budget_total_minor": null,
  "commit_max_cost_minor": null,
  "cost_bounds_verified": false,
  "cost_control_mode": "waived",
  "currency": null,
  "max_output_tokens": 2048,
  "model": "gemini-3.1-flash-lite",
  "prepare_max_cost_minor": null,
  "version": "mina_gemini_waived_20261023_v1"
}
```

このJSONは受入確認前の案であり、agent_integration_verified=falseのまま本番有効設定へ投入する依頼ではない。受入範囲を合意した後、提供側のMINA_REPLACEMENT_POLICY_JSONと機能設定へ反映する手順・担当・適用日を明確にしてほしい。解析費用を0円と扱わず、回数・期限・技術枠・公開停止・品質は維持する。

## 独立した開始阻害

BufferではX2ページに対象24日/日付不明の阻害0件、Instagram1ページにdraft4件。2件はAgentの既存試験台帳と一致、2件未照合。削除・除外はしておらず、Agentは外部状態が解消するまで開始を拒否する。

24日は保存済み朝食1件だけでpreconditionが422になる別課題もある。現行3食契約を緩めたり、他2食を数合わせで登録したり、前日20時を延長して24日を復旧する依頼ではない。将来日の正式な通常運用に向けた受入・設定として扱う。

今回はmarketing-agent内だけで対応し、Log-Eats本体のコード・設定・DBは変更していない。他担当チャットへも送信していない。
