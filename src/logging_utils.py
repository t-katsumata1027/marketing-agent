"""常駐プロセス向けの秘密情報を伏せる JSON ログ機能。"""

import json
import logging
import re
import sys
from typing import Any


_SECRET_KEY_PATTERN = re.compile(r"(token|secret|password|api[_-]?key|authorization)", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"(Bearer\s+)[^\s]+", re.IGNORECASE)
_URL_TOKEN_PATTERN = re.compile(r"([?&](?:token|key|secret)=)[^&\s]+", re.IGNORECASE)


def mask_sensitive(value: Any) -> Any:
    """辞書内の秘密らしい値と文字列内の bearer/query token をマスクする。"""
    if isinstance(value, dict):
        return {str(key): "***" if _SECRET_KEY_PATTERN.search(str(key)) else mask_sensitive(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_sensitive(item) for item in value]
    if isinstance(value, str):
        return _URL_TOKEN_PATTERN.sub(r"\1***", _BEARER_PATTERN.sub(r"\1***", value))
    return value


class JsonFormatter(logging.Formatter):
    """ログ収集基盤で扱いやすい一行 JSON に整形する。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(mask_sensitive(fields))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(mask_sensitive(payload), ensure_ascii=False, default=str)


def get_logger(name: str) -> logging.Logger:
    """二重ハンドラを作らず JSON logger を返す。"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """イベント名と文脈を構造化して記録する。"""
    logger.info(event, extra={"fields": fields})
