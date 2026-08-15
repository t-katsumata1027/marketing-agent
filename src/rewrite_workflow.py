"""管理者のフィードバックを基に投稿下書きをリライトする LangGraph ワークフロー。"""

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

try:
    from src.llm_client import get_helicone_claude_client
    from src.supabase_client import get_post, update_draft_content
except ModuleNotFoundError:
    from llm_client import get_helicone_claude_client
    from supabase_client import get_post, update_draft_content


class RewriteState(TypedDict):
    """投稿リライトワークフローで受け渡す状態。"""

    post_id: str
    original_content: str
    user_instruction: str
    rewritten_content: str


def fetch_original_node(state: RewriteState) -> dict[str, str]:
    """下書きとして保存されている元の投稿内容を取得する。"""
    post = get_post(state["post_id"])
    if post.get("status") != "draft":
        raise ValueError("下書き状態の投稿だけをリライトできます。")
    return {"original_content": post["content"]}


def rewrite_node(state: RewriteState) -> dict[str, str]:
    """管理者の修正指示を反映した新しい投稿案を生成する。"""
    prompt = (
        "あなたは優秀なSNSマーケターです。以下の投稿案に対し、管理者から修正指示が入りました。\n\n"
        f"【元の投稿案】\n{state['original_content']}\n\n"
        f"【修正指示】\n{state['user_instruction']}\n\n"
        "指示に従い、140文字以内で新しくX（旧Twitter）向けの投稿文を書き直してください。"
        "テキストのみを出力してください。"
    )
    result = get_helicone_claude_client().invoke(prompt)
    return {"rewritten_content": str(result.content).strip()}


def update_db_node(state: RewriteState) -> dict[str, Any]:
    """生成した投稿案で対象の下書きを更新する。"""
    update_draft_content(state["post_id"], state["rewritten_content"])
    return {}


workflow = StateGraph(RewriteState)
workflow.add_node("fetch", fetch_original_node)
workflow.add_node("rewrite", rewrite_node)
workflow.add_node("update", update_db_node)
workflow.set_entry_point("fetch")
workflow.add_edge("fetch", "rewrite")
workflow.add_edge("rewrite", "update")
workflow.add_edge("update", END)

rewrite_app = workflow.compile()


def process_rewrite(post_id: str | int, user_instruction: str) -> dict[str, Any]:
    """指定投稿の下書きを管理者指示に従ってリライトする。"""
    instruction = user_instruction.strip()
    if not instruction:
        raise ValueError("修正指示を空にすることはできません。")

    print(f"投稿 ID {post_id} のリライトを開始します。")
    return rewrite_app.invoke(
        {
            "post_id": str(post_id),
            "original_content": "",
            "user_instruction": instruction,
            "rewritten_content": "",
        }
    )
