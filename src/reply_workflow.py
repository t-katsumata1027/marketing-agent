"""未処理メンションに対するリプライ下書きを生成する LangGraph ワークフロー。"""

from typing import Any, TypedDict

from crewai import Crew
from langgraph.graph import END, StateGraph

try:
    from src.crew_setup import (
        add_utm_parameters,
        create_pr_agent,
        create_reply_task,
        load_project_config,
    )
    from src.discord_client import send_discord_notification
    from src.supabase_client import (
        claim_mentions_for_reply,
        complete_mention,
        fail_mention,
        log_post,
    )
    from src.quality_gate import validate_content
    from src.retry import retry_call
    from src.paths import project_config_path
except ModuleNotFoundError:
    from crew_setup import add_utm_parameters, create_pr_agent, create_reply_task, load_project_config
    from discord_client import send_discord_notification
    from supabase_client import claim_mentions_for_reply, complete_mention, fail_mention, log_post
    from quality_gate import validate_content
    from retry import retry_call
    from paths import project_config_path


class ReplyState(TypedDict):
    """リプライ作成ワークフローで受け渡す状態。"""

    project_name: str
    project_config: dict[str, Any] | None
    unprocessed_mentions: list[dict[str, Any]]
    current_mention: dict[str, Any] | None
    draft_reply: str | None
    db_response: list[dict[str, Any]] | None


def load_config_and_mentions_node(state: ReplyState) -> dict[str, Any]:
    """プロジェクト設定と未処理メンションを取得する。"""
    print("--- ノード: 設定・メンション読込 ---")
    project_name = state["project_name"]
    project_config = load_project_config(project_config_path(project_name))
    # run_reply_processor が確保済みの対象を渡す。単独呼出し時のみここで確保する。
    mentions = state["unprocessed_mentions"] or claim_mentions_for_reply(project_name)
    return {"project_config": project_config, "unprocessed_mentions": mentions}


def generate_reply_node(state: ReplyState) -> dict[str, Any]:
    """最初の未処理メンションに対するリプライ下書きを生成する。"""
    mentions = state["unprocessed_mentions"]
    if not mentions:
        print("未処理のメンションはありません。")
        return {"current_mention": None, "draft_reply": None}

    project_config = state["project_config"]
    if project_config is None:
        raise ValueError("プロジェクト設定が読み込まれていません。")

    print("--- ノード: リプライ生成 ---")
    mention = mentions[0]
    agent = create_pr_agent(project_config)
    task = create_reply_task(
        agent=agent,
        project_config=project_config,
        target_tweet=mention["content"],
        author_username=mention["author_username"],
    )
    result = retry_call(lambda: Crew(agents=[agent], tasks=[task], verbose=True).kickoff())
    reply = add_utm_parameters(
        f"@{mention['author_username']} {result.raw}",
        project_config.get("base_url", ""),
        "ai_agent_reply",
    )
    return {"current_mention": mention, "draft_reply": str(reply)}


def save_and_notify_node(state: ReplyState) -> dict[str, list[dict[str, Any]]]:
    """リプライ下書きを保存し、メンションを処理済みにして Discord へ通知する。"""
    mention = state["current_mention"]
    draft_reply = state["draft_reply"]
    if mention is None or draft_reply is None:
        return {"db_response": []}

    print("--- ノード: 下書き保存・通知 ---")
    errors = validate_content(draft_reply, (state["project_config"] or {}).get("quality_policy"), reply_to_username=mention["author_username"])
    if errors:
        fail_mention(mention["id"], mention["reply_lease_token"], "品質ゲート: " + "、".join(errors))
        return {"db_response": []}
    db_response = log_post(
        project_name=state["project_name"],
        content=draft_reply,
        status="draft",
        in_reply_to_id=str(mention["tweet_id"]),
    )
    if not db_response or "id" not in db_response[0]:
        raise ValueError("Supabase の保存結果から投稿 ID を取得できません。")

    retry_call(lambda: send_discord_notification(
        project_name=state["project_name"],
        topic=f"リプライ案: @{mention['author_username']} への返信",
        content=draft_reply,
        post_id=db_response[0]["id"],
    ))
    complete_mention(mention["id"], mention["reply_lease_token"])
    return {"db_response": db_response}


workflow = StateGraph(ReplyState)
workflow.add_node("init", load_config_and_mentions_node)
workflow.add_node("generate", generate_reply_node)
workflow.add_node("save", save_and_notify_node)
workflow.set_entry_point("init")
workflow.add_edge("init", "generate")
workflow.add_edge("generate", "save")
workflow.add_edge("save", END)

reply_app = workflow.compile()


def run_reply_processor(project_name: str, limit: int = 5) -> dict[str, Any]:
    """原子的に確保したメンションを上限まで逐次処理する。"""
    print(f"{project_name} のリプライ処理を開始します。")
    mentions = claim_mentions_for_reply(project_name, limit=limit)
    results: list[dict[str, Any]] = []
    for mention in mentions:
        try:
            results.append(reply_app.invoke({
            "project_name": project_name,
            "project_config": None,
            "unprocessed_mentions": [mention],
            "current_mention": mention,
            "draft_reply": None,
            "db_response": None,
            }))
        except Exception as error:
            fail_mention(mention["id"], mention["reply_lease_token"], str(error))
            print(f"メンション ID {mention['id']} の処理に失敗しました: {error}")
    return {"processed": len(results), "results": results}


if __name__ == "__main__":
    print("リプライ生成ワークフローをコンパイルしました。")
