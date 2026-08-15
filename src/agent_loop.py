"""リスニング・リプライ・プロアクティブ投稿生成を定期実行するループ。"""

import random
import time
from datetime import datetime
from typing import Any, TypedDict

try:
    from src.crew_setup import load_project_config
    from src.analytics import run_analytics_job
    from src.listener import run_social_listening
    from src.reply_workflow import run_reply_processor
    from src.workflow import app as promo_workflow
    from src.heartbeat import write_heartbeat
    from src.supabase_client import claim_job, finish_job
    from src.paths import project_config_path
except ModuleNotFoundError:
    from crew_setup import load_project_config
    from analytics import run_analytics_job
    from listener import run_social_listening
    from reply_workflow import run_reply_processor
    from workflow import app as promo_workflow
    from heartbeat import write_heartbeat
    from supabase_client import claim_job, finish_job
    from paths import project_config_path


class ActiveProject(TypedDict):
    """自律処理の対象プロジェクトとスケジュール設定。"""

    name: str
    query: str
    promo_interval_hours: int


ACTIVE_PROJECTS: tuple[ActiveProject, ...] = (
    {
        "name": "LogEats",
        "query": "食事記録 アプリ -is:retweet",
        "promo_interval_hours": 6,
    },
)

ANALYTICS_INTERVAL_HOURS = 12


def run_proactive_generation(project: ActiveProject, current_timestamp: float | None = None) -> bool:
    """生成間隔を満たす場合に、プロモーション投稿の下書きを1件生成する。"""
    interval_seconds = project["promo_interval_hours"] * 60 * 60
    project_name = project["name"]
    lease_token = claim_job(project_name, "proactive_generation", interval_seconds)
    if not lease_token:
        return False
    try:
        project_config = load_project_config(project_config_path(project_name))
        topics: Any = project_config.get("promo_topics", ["プロダクトの魅力について"])
        if not isinstance(topics, list) or not topics:
            topics = ["プロダクトの魅力について"]
        selected_topic = random.choice(topics)
        print(f"[{project_name}] プロアクティブ投稿の生成を開始します: {selected_topic}")
        promo_workflow.invoke({
            "project_name": project["name"],
            "topic": selected_topic,
            "project_config": None,
            "draft_content": None,
            "media_path": None,
            "db_response": None,
        })
        finish_job(project_name, "proactive_generation", lease_token, True)
        return True
    except Exception as error:
        finish_job(project_name, "proactive_generation", lease_token, False, str(error))
        raise


def run_agent_cycle(active_projects: tuple[ActiveProject, ...] = ACTIVE_PROJECTS) -> None:
    """全対象プロジェクトのリスニング・リプライ・投稿生成を1回実行する。"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n--- [ {current_time} ] エージェントサイクルを開始します ---")

    for project in active_projects:
        project_name = project["name"]
        try:
            run_social_listening(
                project_name=project_name,
                query=project["query"],
                max_results=10,
            )
        except Exception as error:
            print(f"プロジェクト {project_name} の通常リスニングでエラーが発生しました: {error}")

        try:
            project_config = load_project_config(project_config_path(project_name))
            pain_queries: Any = project_config.get("pain_queries", [])
            if not isinstance(pain_queries, list):
                raise ValueError("pain_queries はリスト形式で指定してください。")
            for pain_query in pain_queries:
                if not isinstance(pain_query, str) or not pain_query.strip():
                    continue
                print(f"[{project_name}] 課題・不満の検知を実行します: {pain_query}")
                run_social_listening(
                    project_name=project_name,
                    query=pain_query,
                    max_results=10,
                )
        except Exception as error:
            print(f"プロジェクト {project_name} の課題検知でエラーが発生しました: {error}")

        try:
            run_reply_processor(project_name=project_name)
        except Exception as error:
            print(f"プロジェクト {project_name} のリプライ生成でエラーが発生しました: {error}")

    for project in active_projects:
        try:
            run_proactive_generation(project)
        except Exception as error:
            print(f"プロジェクト {project['name']} の投稿生成でエラーが発生しました: {error}")

    run_periodic_analytics()


def run_periodic_analytics(current_timestamp: float | None = None) -> bool:
    """12時間ごとに公開投稿のメトリクス同期を実行する。"""
    lease_token = claim_job("__system__", "analytics", ANALYTICS_INTERVAL_HOURS * 60 * 60)
    if not lease_token:
        return False
    try:
        print("公開投稿のパフォーマンス分析を開始します。")
        run_analytics_job(days=7)
        finish_job("__system__", "analytics", lease_token, True)
        return True
    except Exception as error:
        finish_job("__system__", "analytics", lease_token, False, str(error))
        raise


def run_agent_loop(poll_interval_minutes: int = 15) -> None:
    """指定間隔でエージェントサイクルを継続実行する。"""
    if poll_interval_minutes <= 0:
        raise ValueError("ポーリング間隔は 1 分以上で指定してください。")

    print(f"Agent Loop を開始します。{poll_interval_minutes} 分ごとに実行します。")
    while True:
        write_heartbeat("agent-loop")
        run_agent_cycle()
        print(
            f"エージェントサイクルが完了しました。{poll_interval_minutes} 分待機します。"
        )
        time.sleep(poll_interval_minutes * 60)


if __name__ == "__main__":
    run_agent_loop()
