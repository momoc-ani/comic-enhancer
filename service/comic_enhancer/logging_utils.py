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


# 方法说明：提取适合日志记录的异常类型及本地系统错误信息。
def exception_log_fields(error: BaseException) -> dict[str, Any]:
    fields: dict[str, Any] = {"error": type(error).__name__}
    if not isinstance(error, OSError):
        return fields
    if error.errno is not None:
        fields["error_errno"] = error.errno
    fields["error_detail"] = error.strerror or str(error)
    if error.filename is not None:
        fields["error_path"] = str(error.filename)
    if error.filename2 is not None:
        fields["error_path_secondary"] = str(error.filename2)
    return fields


# 方法说明：按统一格式记录安全日志，并允许显式字段保留完整文本。
def log_operation(
    target_logger: logging.Logger,
    level: int,
    *,
    feature: str,
    parameters: dict[str, Any],
    result: dict[str, Any],
    elapsed_ms: int | float | None = None,
    full_text_keys: set[str] | None = None,
) -> None:
    allowed_full_text_keys = {
        item.lower() for item in (full_text_keys or set())
    }
    parameter_text = _safe_json(parameters, allowed_full_text_keys)
    result_text = _safe_json(result, allowed_full_text_keys)
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
def _safe_json(
    value: dict[str, Any],
    full_text_keys: set[str] | None = None,
) -> str:
    return json.dumps(
        _sanitize(value, full_text_keys=full_text_keys or set()),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


# 方法说明：隐藏敏感键并限制任意字符串的日志长度。
def _sanitize(
    value: Any,
    key: str = "",
    full_text_keys: set[str] | None = None,
) -> Any:
    normalized_key = key.lower()
    allowed_full_text_keys = full_text_keys or set()
    if (
        normalized_key in SENSITIVE_KEYS
        or normalized_key.endswith("_api_key")
        or normalized_key.endswith("_token")
    ):
        return "***"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize(
                item_value,
                str(item_key),
                allowed_full_text_keys,
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [
            _sanitize(item, key, allowed_full_text_keys)
            for item in value
        ]
    if (
        isinstance(value, str)
        and normalized_key not in allowed_full_text_keys
        and len(value) > 256
    ):
        return f"{value[:253]}..."
    return value
