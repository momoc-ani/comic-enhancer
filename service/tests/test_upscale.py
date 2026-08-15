import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from comic_enhancer.backends import (
    REALCUGAN_MODEL_PROFILE,
    REALCUGAN_PROCESSING_REVISION,
    RealCuganUpscaler,
)
from comic_enhancer.config import Settings
from comic_enhancer.main import create_app
from comic_enhancer.models import ProcessOptions, ProcessingMode


# 方法说明：生成测试使用的 PNG 图片字节。
def png_bytes(size=(32, 48)) -> bytes:
    stream = BytesIO()
    Image.new("RGB", size, "white").save(stream, format="PNG")
    return stream.getvalue()


# 方法说明：创建当前测试平台所需的最小 Real-CUGAN 资源结构。
def create_realcugan_resources(root: Path) -> RealCuganUpscaler:
    upscaler = RealCuganUpscaler(
        enabled=True,
        resource_root=root,
        timeout_seconds=30,
    )
    if upscaler.platform_key() is None:
        pytest.skip("当前测试平台没有 Real-CUGAN 资源目录约定")
    executable = upscaler.executable_path()
    model_dir = upscaler.model_dir()
    assert executable is not None
    assert model_dir is not None
    model_dir.mkdir(parents=True)
    executable.write_bytes(b"test executable")
    executable.chmod(0o755)
    (model_dir / "up2x-no-denoise.param").write_bytes(b"test param")
    (model_dir / "up2x-no-denoise.bin").write_bytes(b"test model")
    return upscaler


# 方法说明：验证 upscale 是合法且固定的处理档位。
def test_upscale_mode_is_valid():
    options = ProcessOptions(mode="upscale")

    assert options.mode == ProcessingMode.UPSCALE


# 方法说明：验证资源齐全时能力接口公布放大档和实际模型。
def test_capabilities_advertise_upscale_only_when_resources_are_ready(tmp_path):
    resource_root = tmp_path / "resource" / "realcugan"
    create_realcugan_resources(resource_root)
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
        adapter_index=tmp_path / "missing.json",
        realcugan_enabled=True,
        realcugan_resource_root=resource_root,
    )
    client = TestClient(create_app(settings))

    response = client.get(
        "/v1/capabilities",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["upscale_available"] is True
    assert "upscale" in payload["processing_modes"]
    assert REALCUGAN_MODEL_PROFILE in payload["model_profiles"]
    option = next(
        item for item in payload["mode_options"] if item["value"] == "upscale"
    )
    assert option["prefetch_pages"] == 1


# 方法说明：验证放大档固定调用两倍无降噪模型并缓存结果。
def test_upscale_process_returns_realcugan_model_and_two_x_image(
    tmp_path,
    monkeypatch,
):
    resource_root = tmp_path / "resource" / "realcugan"
    upscaler = create_realcugan_resources(resource_root)
    captured = {}

    # 方法说明：模拟原生程序读取输入并生成精确两倍图片。
    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        input_path = Path(command[command.index("-i") + 1])
        output_path = Path(command[command.index("-o") + 1])
        with Image.open(input_path) as source:
            source.resize(
                (source.width * 2, source.height * 2),
                Image.Resampling.NEAREST,
            ).save(output_path, format="PNG")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("comic_enhancer.backends.subprocess.run", fake_run)
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
        adapter_index=tmp_path / "missing.json",
        realcugan_enabled=True,
        realcugan_resource_root=resource_root,
    )
    client = TestClient(create_app(settings))
    headers = {"Authorization": "Bearer test-token"}
    data = {
        "work_json": '{"source":"copy_manga","source_work_id":"123"}',
        "options_json": '{"mode":"upscale","page_index":1}',
    }

    first = client.post(
        "/v1/pages/process",
        headers=headers,
        data=data,
        files={"image": ("page.png", png_bytes(), "image/png")},
    )
    second = client.post(
        "/v1/pages/process",
        headers=headers,
        data=data,
        files={"image": ("page.png", png_bytes(), "image/png")},
    )

    assert first.status_code == 200
    assert first.json()["model_profile"] == REALCUGAN_MODEL_PROFILE
    assert first.json()["adapter_source"] == "none"
    assert first.json()["adapter_applied"] is False
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    command = captured["command"]
    assert command[command.index("-s") + 1] == "2"
    assert command[command.index("-n") + 1] == "-1"
    assert Path(command[command.index("-m") + 1]) == upscaler.model_dir()
    assert captured["kwargs"]["cwd"] == upscaler.platform_dir()
    image_response = client.get(first.json()["result_url"], headers=headers)
    with Image.open(BytesIO(image_response.content)) as result:
        assert result.size == (64, 96)


# 方法说明：验证模型内容变化会更新放大档缓存版本。
def test_upscale_cache_revision_covers_executable_and_model_files(tmp_path):
    upscaler = create_realcugan_resources(tmp_path / "resource" / "realcugan")
    first = upscaler.cache_revision()
    model_dir = upscaler.model_dir()
    assert model_dir is not None

    (model_dir / "up2x-no-denoise.bin").write_bytes(b"changed test model")
    second = upscaler.cache_revision()

    assert first.startswith(REALCUGAN_PROCESSING_REVISION)
    assert second.startswith(REALCUGAN_PROCESSING_REVISION)
    assert first != second


# 方法说明：验证未启用放大资源时处理接口明确拒绝请求。
def test_upscale_process_rejects_unavailable_profile(tmp_path):
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
        adapter_index=tmp_path / "missing.json",
        realcugan_enabled=False,
        realcugan_resource_root=tmp_path / "missing-realcugan",
    )
    client = TestClient(create_app(settings))

    response = client.post(
        "/v1/pages/process",
        headers={"Authorization": "Bearer test-token"},
        data={
            "work_json": '{"source":"copy_manga","source_work_id":"123"}',
            "options_json": '{"mode":"upscale"}',
        },
        files={"image": ("page.png", png_bytes(), "image/png")},
    )

    assert response.status_code == 409
    assert "Real-CUGAN" in response.json()["detail"]
