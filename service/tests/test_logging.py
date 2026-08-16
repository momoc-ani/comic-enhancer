from io import BytesIO
import logging

from PIL import Image

from comic_enhancer.api.app import _configure_application_logging
from comic_enhancer.inference.comfyui.transport import ComfyUITransport
from comic_enhancer.logging_utils import log_operation


class FakeResponse:
    """提供 ComfyUI 传输日志测试使用的最小 HTTP 响应。"""

    # 方法说明：保存测试响应的 JSON 和图片内容。
    def __init__(self, payload=None, content=b""):
        self.payload = payload or {}
        self.content = content

    # 方法说明：模拟成功响应的状态校验。
    def raise_for_status(self):
        return None

    # 方法说明：返回测试配置的 JSON 响应。
    def json(self):
        return self.payload


class FakeComfyClient:
    """模拟一次完整的 ComfyUI 上传、提交、轮询和下载。"""

    # 方法说明：保存下载结果并忽略真实 HTTP 客户端参数。
    def __init__(self, image_bytes, **_kwargs):
        self.image_bytes = image_bytes

    # 方法说明：进入测试 HTTP 客户端上下文。
    def __enter__(self):
        return self

    # 方法说明：退出测试 HTTP 客户端上下文。
    def __exit__(self, *_args):
        return False

    # 方法说明：模拟图片上传和工作流入队响应。
    def post(self, path, **_kwargs):
        if path == "/upload/image":
            return FakeResponse({"name": "input.png", "subfolder": ""})
        if path == "/prompt":
            return FakeResponse({"prompt_id": "prompt-1"})
        raise AssertionError(f"unexpected POST path: {path}")

    # 方法说明：模拟历史记录完成和结果图片下载响应。
    def get(self, path, **_kwargs):
        if path == "/history/prompt-1":
            return FakeResponse(
                {
                    "prompt-1": {
                        "status": {"status_str": "success"},
                        "outputs": {
                            "2": {
                                "images": [
                                    {
                                        "filename": "result.png",
                                        "subfolder": "",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                }
            )
        if path == "/view":
            return FakeResponse(content=self.image_bytes)
        raise AssertionError(f"unexpected GET path: {path}")


# 方法说明：生成 ComfyUI 传输日志测试使用的 PNG 图片。
def png_bytes(size=(8, 12)) -> bytes:
    stream = BytesIO()
    Image.new("RGB", size, (80, 120, 200)).save(stream, format="PNG")
    return stream.getvalue()


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


# 方法说明：验证业务日志会回退复用 Uvicorn 父级终端处理器。
def test_application_logging_uses_uvicorn_parent_handler():
    application_logger = logging.getLogger("comic_enhancer")
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    original = {
        "application_handlers": list(application_logger.handlers),
        "application_propagate": application_logger.propagate,
        "application_level": application_logger.level,
        "uvicorn_handlers": list(uvicorn_logger.handlers),
        "uvicorn_error_handlers": list(uvicorn_error_logger.handlers),
    }
    handler = logging.NullHandler()
    try:
        application_logger.handlers = []
        application_logger.propagate = True
        uvicorn_logger.handlers = [handler]
        uvicorn_error_logger.handlers = []

        _configure_application_logging()

        assert application_logger.handlers == [handler]
        assert application_logger.propagate is False
        assert application_logger.level == logging.INFO
    finally:
        application_logger.handlers = original["application_handlers"]
        application_logger.propagate = original["application_propagate"]
        application_logger.setLevel(original["application_level"])
        uvicorn_logger.handlers = original["uvicorn_handlers"]
        uvicorn_error_logger.handlers = original["uvicorn_error_handlers"]


# 方法说明：验证 ComfyUI 日志包含执行摘要和动态绑定后的完整提示词。
def test_comfyui_transport_logs_workflow_progress_and_full_prompt(
    caplog,
    monkeypatch,
):
    image = png_bytes()

    # 方法说明：为当前测试创建确定性的模拟 HTTP 客户端。
    def build_client(**kwargs):
        return FakeComfyClient(image, **kwargs)

    monkeypatch.setattr(
        "comic_enhancer.inference.comfyui.transport.httpx.Client",
        build_client,
    )
    transport = ComfyUITransport(
        base_url="http://comfy",
        timeout_seconds=10,
        poll_interval_seconds=0.01,
    )
    final_prompt = "final colorization prompt " + ("character palette " * 30)
    workflow = {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": "preset.png"},
            "_meta": {"title": "INPUT_IMAGE"},
        },
        "2": {
            "class_type": "SaveImage",
            "inputs": {"images": ["1", 0]},
            "_meta": {"title": "OUTPUT_IMAGE"},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": final_prompt},
            "_meta": {"title": "Colorization Instruction"},
        },
        "4": {
            "class_type": "KSampler",
            "inputs": {
                "steps": 4,
                "cfg": 1.0,
                "seed": 20260816,
                "positive": ["3", 0],
            },
            "_meta": {"title": "FLUX.2 Sampler"},
        },
    }

    with caplog.at_level(
        logging.INFO,
        logger="comic_enhancer.inference.comfyui.transport",
    ):
        result = transport.run(
            workflow,
            input_images={"INPUT_IMAGE": image},
            output_prefix="comic-enhancer/test",
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert result.size == (8, 12)
    assert "功能=ComfyUI工作流准备" in messages
    assert "功能=ComfyUI最终提示词" in messages
    assert "功能=ComfyUI工作流提交" in messages
    assert "功能=ComfyUI任务轮询" in messages
    assert "功能=ComfyUI结果下载" in messages
    assert '"CLIPTextEncode":1' in messages
    assert '"steps":4' in messages
    assert '"cfg":1.0' in messages
    assert '"seed":20260816' in messages
    assert '"prompt_id":"prompt-1"' in messages
    assert '"size":[8,12]' in messages
    assert final_prompt in messages
