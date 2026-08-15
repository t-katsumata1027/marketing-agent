"""プロジェクト設定から CrewAI の広報エージェントを組み立てるモジュール。"""

from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml
from crewai import Agent, Task

try:
    from src.llm_client import get_helicone_claude_client
except ModuleNotFoundError:
    from llm_client import get_helicone_claude_client


def load_project_config(filepath: str | Path) -> dict[str, Any]:
    """YAML ファイルからプロジェクト設定を読み込む。"""
    with Path(filepath).open(encoding="utf-8") as config_file:
        project_config = yaml.safe_load(config_file)

    if not isinstance(project_config, dict):
        raise ValueError("プロジェクト設定は YAML のマッピング形式で指定してください。")

    return project_config


def add_utm_parameters(content: str, base_url: str, campaign: str) -> str:
    """本文中のプロダクト URL に X 向け UTM パラメータを付与する。"""
    if not base_url or base_url not in content:
        return content

    parsed_url = urlsplit(base_url)
    existing_parameters = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
    existing_parameters.update(
        {
            "utm_source": "x",
            "utm_medium": "social",
            "utm_campaign": campaign,
        }
    )
    tracked_url = urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            urlencode(existing_parameters),
            parsed_url.fragment,
        )
    )
    return content.replace(base_url, tracked_url)


def create_pr_agent(project_config: dict[str, Any]) -> Agent:
    """プロジェクト設定に基づく広報・SNS マーケティングエージェントを作成する。"""
    return Agent(
        role=project_config.get("role", "広報担当"),
        goal=project_config.get("goal", "プロダクトの魅力を発信する"),
        backstory=project_config.get("backstory", "あなたは優秀なマーケターです。"),
        llm=get_helicone_claude_client(),
        verbose=True,
        allow_delegation=False,
    )


def create_drafting_task(
    agent: Agent, project_config: dict[str, Any], topic: str
) -> Task:
    """プロジェクト固有の文脈を含む X 向け投稿作成タスクを定義する。"""
    description = (
        "以下のテーマとプロダクト情報に基づいて、X（旧Twitter）向けの"
        "魅力的な投稿文を1つ作成してください。\n\n"
        f"【プロダクト名】: {project_config.get('name', '未設定')}\n"
        f"【ターゲット層】: {project_config.get('target_audience', '未設定')}\n"
        f"【テーマ】: {topic}\n\n"
        "条件: 140文字以内、適切な絵文字を使用し、ハッシュタグは2つまで。\n"
        "プロダクトのURLを紹介する場合は、必ず以下のベースURLへUTMパラメータを"
        "付与してください。\n"
        f"ベースURL: {project_config.get('base_url', '')}\n"
        "UTMパラメータ: utm_source=x&utm_medium=social&utm_campaign=ai_agent_promo"
    )
    return Task(
        description=description,
        expected_output="Xにそのまま投稿できるテキスト文字列",
        agent=agent,
    )


def create_reply_task(
    agent: Agent,
    project_config: dict[str, Any],
    target_tweet: str,
    author_username: str,
) -> Task:
    """特定の投稿に自然に応答するリプライ作成タスクを定義する。"""
    description = (
        "以下のユーザーの投稿に対して、X（旧Twitter）向けの自然なリプライ"
        "（返信）を作成してください。\n\n"
        f"【プロダクト名】: {project_config.get('name', '未設定')}\n"
        f"【ターゲット層】: {project_config.get('target_audience', '未設定')}\n"
        f"【相手のユーザー名】: @{author_username}\n"
        f"【相手の投稿内容】: {target_tweet}\n\n"
        "条件: 140文字以内。相手の文脈、特に不満や課題に強く共感し、必要な場合は"
        "解決策としてプロダクトを自然に提案すること。宣伝感を抑え、対話を重視すること。\n"
        "URLを案内する必要がある場合は、以下のベースURLへUTMパラメータを付与すること。\n"
        f"ベースURL: {project_config.get('base_url', '')}\n"
        "UTMパラメータ: utm_source=x&utm_medium=social&utm_campaign=ai_agent_reply"
    )
    return Task(
        description=description,
        expected_output="Xにそのままリプライできるテキスト文字列（@ユーザー名は含めない）",
        agent=agent,
    )


if __name__ == "__main__":
    print("CrewAI の動的セットアップモジュールを読み込みました。")
