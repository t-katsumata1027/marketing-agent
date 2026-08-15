"""外部API向けの副作用を増やさない共通リトライ方針。"""

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def is_retryable(error: Exception) -> bool:
    """接続・タイムアウト・429・5xxだけを再試行対象にする。"""
    status = getattr(getattr(error, "response", None), "status_code", None) or getattr(error, "status_code", None)
    return isinstance(error, (TimeoutError, ConnectionError)) or status == 429 or (isinstance(status, int) and 500 <= status <= 599)


def retry_call(operation: Callable[[], T], attempts: int = 3, sleep: Callable[[float], None] = time.sleep, random_value: Callable[[], float] = random.random) -> T:
    """Retry-Afterを尊重しつつ指数バックオフ＋ジッターで実行する。"""
    for index in range(attempts):
        try:
            return operation()
        except Exception as error:
            if not is_retryable(error) or index == attempts - 1:
                raise
            response = getattr(error, "response", None)
            retry_after = getattr(response, "headers", {}).get("Retry-After") if response else None
            try: delay = min(float(retry_after), 60) if retry_after else min(2 ** index + random_value(), 30)
            except ValueError: delay = min(2 ** index + random_value(), 30)
            sleep(delay)
    raise RuntimeError("到達不能なリトライ状態")
