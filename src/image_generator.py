"""差し替え可能な画像生成プロバイダーを扱うモジュール。"""

import base64
import os
import time
import uuid
import re
from pathlib import Path
from typing import Any, Protocol

import requests
from PIL import Image, UnidentifiedImageError
try:
    from src.retry import retry_call
except ModuleNotFoundError:
    from retry import retry_call  # type: ignore


# コンテナでは named volume を使い、ホスト固有の絶対パスを持ち込まない。
OUTPUT_DIRECTORY = Path(os.getenv("MEDIA_OUTPUT_DIRECTORY", "/var/lib/jarvis/media"))
TINY_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Jk3sAAAAASUVORK5CYII="
)


class ImageProvider(Protocol):
    """画像生成プロバイダーが実装する共通インターフェース。"""

    def generate(self, prompt: str, output_path: Path) -> Path:
        """画像を生成し、ローカル保存先を返す。"""


def _download_image(url: str, output_path: Path, headers: dict[str, str] | None = None) -> Path:
    """生成サービスが返した画像 URL をローカルへ保存する。"""
    response = retry_call(lambda: requests.get(url, headers=headers, timeout=120))
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path


def validate_image_file(path: Path) -> None:
    """添付前に形式と容量を検査し、HTML等の誤保存を拒否する。"""
    data = path.read_bytes()
    if not data:
        raise ValueError("画像ファイルが空です")
    is_png = data.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpeg = data.startswith(b"\xff\xd8\xff")
    if not (is_png or is_jpeg):
        raise ValueError("PNG または JPEG 以外の画像は添付できません")
    max_bytes = int(os.getenv("MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            max_dimension = int(os.getenv("MAX_IMAGE_DIMENSION", "4096"))
            min_dimension = int(os.getenv("MIN_IMAGE_DIMENSION", "64"))
            if width < min_dimension or height < min_dimension:
                raise ValueError("画像の寸法が小さすぎます")
            if width > max_dimension or height > max_dimension:
                raise ValueError("画像の寸法が上限を超えています")
    except UnidentifiedImageError as error:
        raise ValueError("画像を Pillow で検証できません") from error
    if len(data) > max_bytes:
        raise ValueError("画像ファイルの容量が上限を超えています")


class MockImageProvider:
    """外部 API を使わず、パイプライン検証用の PNG を生成する。"""

    def generate(self, prompt: str, output_path: Path) -> Path:
        # テスト用でも本番と同じ寸法検査を通る有効な PNG を作る。
        Image.new("RGB", (512, 512), color=(240, 244, 248)).save(output_path, "PNG")
        return output_path


class ReplicateImageProvider:
    """Replicate の Predictions API を利用する画像生成プロバイダー。"""

    def generate(self, prompt: str, output_path: Path) -> Path:
        api_token = os.getenv("REPLICATE_API_TOKEN", "")
        model_version = os.getenv("REPLICATE_MODEL_VERSION", "")
        if not api_token or not model_version:
            raise ValueError("REPLICATE_API_TOKEN と REPLICATE_MODEL_VERSION が必要です。")

        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "Prefer": "wait=60",
        }
        response = retry_call(lambda: requests.post(
            "https://api.replicate.com/v1/predictions",
            headers=headers,
            json={"version": model_version, "input": {"prompt": prompt}},
            timeout=70,
        ))
        response.raise_for_status()
        prediction: dict[str, Any] = response.json()
        while prediction.get("status") in {"starting", "processing"}:
            time.sleep(2)
            poll_url = prediction.get("urls", {}).get("get")
            if not poll_url:
                raise RuntimeError("Replicate のポーリング URL を取得できませんでした。")
            poll_response = retry_call(lambda: requests.get(poll_url, headers=headers, timeout=30))
            poll_response.raise_for_status()
            prediction = poll_response.json()

        if prediction.get("status") != "succeeded":
            raise RuntimeError(f"Replicate の画像生成に失敗しました: {prediction.get('error')}")
        output = prediction.get("output")
        image_url = output[0] if isinstance(output, list) and output else output
        if not isinstance(image_url, str):
            raise RuntimeError("Replicate から画像 URL を取得できませんでした。")
        return _download_image(image_url, output_path, {"Authorization": f"Bearer {api_token}"})


class LocalApiImageProvider:
    """ComfyUI 等、画像 URL を返すローカル HTTP API 用のプロバイダー。"""

    def generate(self, prompt: str, output_path: Path) -> Path:
        endpoint = os.getenv("LOCAL_IMAGE_API_URL", "")
        if not endpoint:
            raise ValueError("LOCAL_IMAGE_API_URL が未設定です。")

        response = retry_call(lambda: requests.post(endpoint, json={"prompt": prompt}, timeout=120))
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        image_url = payload.get("image_url") or payload.get("url")
        if not isinstance(image_url, str):
            raise RuntimeError("ローカル画像 API の応答に image_url または url が必要です。")
        return _download_image(image_url, output_path)


def _get_provider(name: str) -> ImageProvider:
    """設定名に対応する画像生成プロバイダーを返す。"""
    providers: dict[str, ImageProvider] = {
        "mock": MockImageProvider(),
        "replicate": ReplicateImageProvider(),
        "local_api": LocalApiImageProvider(),
    }
    try:
        return providers[name]
    except KeyError as error:
        raise ValueError(f"未対応の画像生成プロバイダーです: {name}") from error


def _safe_output_path(project_name: str) -> Path:
    """プロジェクト名由来のパス移動を防ぎ、保存先配下だけを返す。"""
    safe_name = re.sub(r"[^a-z0-9_-]+", "_", project_name.lower()).strip("_")
    if not safe_name:
        raise ValueError("画像保存用のプロジェクト名が不正です")
    directory = OUTPUT_DIRECTORY.resolve()
    destination = (directory / f"{safe_name}_{uuid.uuid4().hex[:8]}.png").resolve()
    if directory not in destination.parents:
        raise ValueError("画像保存先が許可ディレクトリ外です")
    return destination


def generate_promotional_image(prompt: str, project_name: str) -> str:
    """プロモーション画像を生成し、保存したローカル絶対パスを返す。"""
    if not prompt.strip() or not project_name.strip():
        raise ValueError("画像生成用のプロンプトとプロジェクト名が必要です。")

    provider_name = os.getenv("IMAGE_PROVIDER", "mock")
    image_prompt = f"High-quality editorial promotional image for {project_name}. Concept: {prompt[:300]}"
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    output_path = _safe_output_path(project_name)
    print(f"[{project_name}] {provider_name} に画像生成を依頼します。")
    generated_path = _get_provider(provider_name).generate(image_prompt, output_path)
    if generated_path.resolve() != output_path.resolve():
        raise ValueError("画像生成プロバイダが許可されていない保存先を返しました")
    validate_image_file(generated_path)
    return str(generated_path.resolve())
