import logging

from comic_enhancer.logging_utils import log_operation


# 方法说明：验证统一日志包含功能、参数、结果和关键耗时并自动脱敏。
def test_log_operation_formats_fields_and_redacts_sensitive_values(caplog):
    target = logging.getLogger("comic-enhancer-test-logging")

    with caplog.at_level(logging.INFO, logger=target.name):
        log_operation(
            target,
            logging.INFO,
            feature="Qwen测试请求",
            parameters={
                "model": "qwen3-vl",
                "max_tokens": 1024,
                "api_key": "should-not-appear",
                "image_bytes": b"protected-image",
            },
            result={"status": "success", "instances": 2},
            elapsed_ms=12.6,
        )

    message = caplog.records[-1].getMessage()
    assert message.startswith("功能=Qwen测试请求 参数=")
    assert " 结果=" in message
    assert "耗时_ms=13" in message
    assert '"max_tokens":1024' in message
    assert "should-not-appear" not in message
    assert "protected-image" not in message
    assert '"api_key":"***"' in message
    assert '"image_bytes":"***"' in message
