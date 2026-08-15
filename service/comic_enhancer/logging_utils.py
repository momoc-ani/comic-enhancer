from __future__ import annotations

import json
import logging
from typing import Any


SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "image_bytes",
    "prompt",
    "token",
}


# 方法说明：按统一的功能、参数、结果和耗时格式记录安全日志。
def log_operation(
    target_logger: logging.Logger,
    level: int,
    *,
    feature: str,
    parameters: dict[str, Any],
    result: dict[str, Any],
    elapsed_ms: int | float | None = None,
) -> None:
    parameter_text = _safe_json(parameters)
    result_text = _safe_json(result)
    suffix = (
        f" 耗时_ms={max(0, round(elapsed_ms))}"
        if elapsed_ms is not None
        else ""
    )
    target_logger.log(
        level,
        "功能=%s 参数=%s 结果=%s%s",
        feature,
        parameter_text,
        result_text,
        suffix,
    )


# 方法说明：将日志字段递归脱敏并编码为紧凑 JSON。
def _safe_json(value: dict[str, Any]) -> str:
    return json.dumps(
        _sanitize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


# 方法说明：隐藏敏感键并限制任意字符串的日志长度。
def _sanitize(value: Any, key: str = "") -> Any:
    normalized_key = key.lower()
    if (
        normalized_key in SENSITIVE_KEYS
        or normalized_key.endswith("_api_key")
        or normalized_key.endswith("_token")
    ):
        return "***"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str) and len(value) > 256:
        return f"{value[:253]}..."
    return value
