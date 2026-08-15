"""投稿案の生成・画像作成・下書き保存・承認通知を行うワークフロー。"""

import os
from typing import Any, TypedDict

from crewai import Crew
from langgraph.graph import END, StateGraph

try:
    from src.crew_setup import (
        add_utm_parameters,
        create_drafting_task,
        create_pr_agent,
        load_project_config,
    )
    from src.discord_client import send_discord_notification
    from src.image_generator import generate_promotional_image
    from src.supabase_client import log_post
    from src.quality_gate import validate_content
    from src.retry import retry_call
    from src.paths import project_config_path
except ModuleNotFoundError:
    from crew_setup import add_utm_parameters, create_drafting_task, create_pr_agent, load_project_config
    from discord_client import send_discord_notification
    from image_generator import generate_promotional_image
    from supabase_client import log_post
    from quality_gate import validate_content
    from retry import retry_call
    from paths import project_config_path


class AgentState(TypedDict):
    """投稿生成ワークフローで受け渡す状態。"""

    project_name: str
    topic: str
    project_config: dict[str, Any] | None
    draft_content: str | None
    media_path: str | None
    db_response: list[dict[str, Any]] | None


def load_config_node(state: AgentState) -> dict[str, dict[str, Any]]:
    """プロダクト名に対応する YAML 設定を読み込む。"""
    filepath = project_config_path(state["project_name"])
    return {"project_config": load_project_config(filepath)}


def generate_post_node(state: AgentState) -> dict[str, str]:
    """CrewAI を使ってプロジェクト固有の投稿案を生成する。"""
    project_config = state["project_config"]
    if project_config is None:
        raise ValueError("プロジェクト設定が読み込まれていません。")

    agent = create_pr_agent(project_config)
    task = create_drafting_task(agent, project_config, state["topic"])
    result = retry_call(lambda: Crew(agents=[agent], tasks=[task], verbose=True).kickoff())
    draft_content = add_utm_parameters(
        str(result.raw),
        project_config.get("base_url", ""),
        "ai_agent_promo",
    )
    return {"draft_content": draft_content}


def generate_image_node(state: AgentState) -> dict[str, str | None]:
    """設定時だけ、投稿案に基づくプロモーション画像を生成する。"""
    enabled = os.getenv("GENERATE_PROMOTIONAL_IMAGES", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    draft_content = state["draft_content"]
    if not enabled or draft_content is None:
        return {"media_path": None}

    return {
        "media_path": retry_call(lambda: generate_promotional_image(
            prompt=draft_content,
            project_name=state["project_name"],
        ))
    }


def save_to_db_node(state: AgentState) -> dict[str, list[dict[str, Any]]]:
    """生成した投稿案を下書きとして Supabase に保存する。"""
    draft_content = state["draft_content"]
    if draft_content is None:
        raise ValueError("保存する投稿案が生成されていません。")
    project_config = state["project_config"] or {}
    errors = validate_content(draft_content, project_config.get("quality_policy"))
    if errors:
        raise ValueError("品質ゲートで投稿案を停止しました: " + "、".join(errors))

    return {
        "db_response": log_post(
            project_name=state["project_name"],
            content=draft_content,
            status="draft",
            media_path=state["media_path"],
        )
    }


def notify_node(state: AgentState) -> dict[str, Any]:
    """保存された投稿 ID を含めて Discord へ承認通知を送信する。"""
    db_response = state["db_response"]
    draft_content = state["draft_content"]
    if not db_response or "id" not in db_response[0]:
        raise ValueError("Supabase の保存結果から投稿 ID を取得できません。")
    if draft_content is None:
        raise ValueError("通知する投稿案が生成されていません。")

    retry_call(lambda: send_discord_notification(
        project_name=state["project_name"],
        topic=state["topic"],
        content=draft_content,
        post_id=db_response[0]["id"],
        media_path=state["media_path"],
    ))
    return {}


workflow = StateGraph(AgentState)
workflow.add_node("load_config", load_config_node)
workflow.add_node("generate_post", generate_post_node)
workflow.add_node("generate_image", generate_image_node)
workflow.add_node("save_db", save_to_db_node)
workflow.add_node("notify", notify_node)
workflow.set_entry_point("load_config")
workflow.add_edge("load_config", "generate_post")
workflow.add_edge("generate_post", "generate_image")
workflow.add_edge("generate_image", "save_db")
workflow.add_edge("save_db", "notify")
workflow.add_edge("notify", END)

app = workflow.compile()


if __name__ == "__main__":
    print("LangGraph の承認通知ワークフローをコンパイルしました。")
