"""Helicone 経由の LangChain LLM クライアントを生成するモジュール。"""

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

try:
    from src.config import config
except ModuleNotFoundError:
    from config import config


def _helicone_headers() -> dict[str, str]:
    """Helicone プロキシへ送信する認証ヘッダーを返す。"""
    if not config.HELICONE_API_KEY:
        raise ValueError("HELICONE_API_KEY が未設定です。Doppler 経由で実行してください。")

    return {"Helicone-Auth": f"Bearer {config.HELICONE_API_KEY}"}


def get_helicone_claude_client(
    model_name: str = "claude-3-7-sonnet-20250219", temperature: float = 0.7
) -> ChatAnthropic:
    """Helicone 経由で Claude を呼び出す ChatAnthropic クライアントを返す。"""
    if not config.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY が未設定です。Doppler 経由で実行してください。")

    return ChatAnthropic(
        model=model_name,
        anthropic_api_key=config.ANTHROPIC_API_KEY,
        anthropic_api_url="https://anthropic.helicone.ai",
        temperature=temperature,
        default_headers=_helicone_headers(),
    )


def get_helicone_openai_client(
    model_name: str = "gpt-4o-mini", temperature: float = 0.7
) -> ChatOpenAI:
    """Helicone 経由で OpenAI を呼び出す ChatOpenAI クライアントを返す。"""
    if not config.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY が未設定です。Doppler 経由で実行してください。")

    return ChatOpenAI(
        model=model_name,
        api_key=config.OPENAI_API_KEY,
        base_url="https://oai.helicone.ai/v1",
        temperature=temperature,
        default_headers=_helicone_headers(),
    )


if __name__ == "__main__":
    print("モジュールを読み込みました。Doppler の環境変数注入後に LLM 呼び出しを実行できます。")
