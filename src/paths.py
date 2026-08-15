"""実行場所に依存しないプロジェクト内パスを提供する。"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIRECTORY = PROJECT_ROOT / "projects"


def project_config_path(project_name: str) -> Path:
    """プロジェクト名から設定ファイルの絶対パスを返す。"""
    normalized_name = project_name.strip().lower()
    if not normalized_name or any(part in normalized_name for part in ("/", "\\", "..")):
        raise ValueError("プロジェクト名が不正です")
    return PROJECTS_DIRECTORY / f"{normalized_name}.yaml"
