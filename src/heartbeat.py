"""外部サービスに依存しない常駐プロセスの生存確認。"""

import os
import time
from pathlib import Path

HEARTBEAT_DIRECTORY = Path(os.getenv("HEARTBEAT_DIRECTORY", "/var/lib/jarvis/heartbeat"))


def write_heartbeat(service_name: str) -> None:
    """サービスごとの最終稼働時刻を共有ボリュームへ原子的に書き込む。"""
    HEARTBEAT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    destination = HEARTBEAT_DIRECTORY / f"{service_name}.timestamp"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(str(time.time()), encoding="ascii")
    temporary.replace(destination)


def is_heartbeat_fresh(service_name: str, max_age_seconds: int = 120) -> bool:
    """最終heartbeatが許容時間以内なら真を返す。"""
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds は正の値にしてください")
    try:
        timestamp = float((HEARTBEAT_DIRECTORY / f"{service_name}.timestamp").read_text(encoding="ascii"))
    except (OSError, ValueError):
        return False
    return time.time() - timestamp <= max_age_seconds
