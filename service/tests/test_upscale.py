import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from comic_enhancer.inference.realcugan import (
    REALCUGAN_MODEL_PROFILE,
    REALCUGAN_PROCESSING_REVISION,
    RealCuganUpscaler,
)
from comic_enhancer.inference import InferenceAssets, InferenceOutcome, PassthroughBackend
from comic_enhancer.inference.routing import RoutedInferenceBackend
from comic_enhancer.config import Settings
from comic_enhancer.main import create_app
from comic_enhancer.models import (
    ProcessOptions,
    ProcessingMode,
)


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

    monkeypatch.setattr(
        "comic_enhancer.inference.realcugan.subprocess.run",
        fake_run,
    )
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
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


# 方法说明：验证 FLUX.2 输出会进入 UPSCALE 二阶段并返回组合模型标识。
def test_flux2_pipeline_uses_upscale_as_second_stage(tmp_path, monkeypatch):
    backend = PassthroughBackend()
    upscaler = create_realcugan_resources(tmp_path / "resource" / "realcugan")
    routed = RoutedInferenceBackend(backend, upscaler)
    captured = {}

    monkeypatch.setattr(backend, "flux2_profile_ready", lambda: True)

    # 方法说明：模拟 FLUX.2 首阶段写入已由工作流恢复到原图尺寸的结果。
    def process_flux2(assets, output_path, options):
        with Image.open(BytesIO(assets.image_bytes)) as source:
            source.save(output_path, format="WEBP")
        return InferenceOutcome(
            reference_applied=True,
            model_profile="flux2-klein-4b",
        )

    # 方法说明：模拟 UPSCALE 二阶段读取原图尺寸首阶段结果并放大两倍。
    def process_upscale(assets, output_path):
        with Image.open(BytesIO(assets.image_bytes)) as source:
            captured["stage_size"] = source.size
            generated = source.resize((source.width * 2, source.height * 2))
            generated.save(output_path, format="WEBP")
        return InferenceOutcome(model_profile=REALCUGAN_MODEL_PROFILE)

    monkeypatch.setattr(backend, "process", process_flux2)
    monkeypatch.setattr(upscaler, "process", process_upscale)
    output_path = tmp_path / "flux2-upscaled.webp"
    outcome = routed.process(
        InferenceAssets(image_bytes=png_bytes((8, 12))),
        output_path,
        ProcessOptions(mode="flux2"),
    )

    assert captured["stage_size"] == (8, 12)
    with Image.open(output_path) as result:
        assert result.size == (16, 24)
    assert outcome.reference_applied is True
    assert outcome.model_profile == "flux2-klein-4b+realcugan-se-2x"
    assert "post-upscale" in routed.cache_revision(
        ProcessOptions(mode="flux2"),
    )


# 方法说明：验证角色档直出模式跳过首阶段后处理后仍执行 Real-CUGAN 放大。
def test_flux2_character_direct_output_still_uses_upscale(
    tmp_path,
    monkeypatch,
):
    backend = PassthroughBackend()
    upscaler = create_realcugan_resources(tmp_path / "resource" / "realcugan")
    routed = RoutedInferenceBackend(backend, upscaler)
    captured = {}

    monkeypatch.setattr(backend, "flux2_character_profile_ready", lambda: True)

    # 方法说明：模拟角色档 ComfyUI 原图直出并记录开关值。
    def process_character(assets, output_path, options):
        captured["direct_output"] = options.comfyui_direct_output
        Image.new("RGB", (20, 20), (80, 120, 200)).save(
            output_path,
            format="WEBP",
        )
        return InferenceOutcome(
            reference_applied=True,
            model_profile="flux2-klein-4b-qwen3-vl-character",
        )

    # 方法说明：模拟 Real-CUGAN 读取直出阶段结果并再次放大两倍。
    def process_upscale(assets, output_path):
        with Image.open(BytesIO(assets.image_bytes)) as source:
            captured["stage_size"] = source.size
            source.resize((source.width * 2, source.height * 2)).save(
                output_path,
                format="WEBP",
            )
        return InferenceOutcome(model_profile=REALCUGAN_MODEL_PROFILE)

    monkeypatch.setattr(backend, "process", process_character)
    monkeypatch.setattr(upscaler, "process", process_upscale)
    output_path = tmp_path / "character-direct-upscaled.webp"

    outcome = routed.process(
        InferenceAssets(
            image_bytes=png_bytes((8, 12)),
            work_key="copy_manga:123",
        ),
        output_path,
        ProcessOptions(
            mode="flux2_character",
            comfyui_direct_output=True,
        ),
    )

    assert captured["direct_output"] is True
    assert captured["stage_size"] == (20, 20)
    with Image.open(output_path) as result:
        assert result.size == (40, 40)
    assert outcome.reference_applied is True
    assert outcome.model_profile == "flux2-klein-4b-qwen3-vl-character"


# 方法说明：验证 UPSCALE 二阶段失败时不回退到质量档。
def test_flux2_upscale_failure_does_not_fallback(tmp_path, monkeypatch):
    backend = PassthroughBackend()
    upscaler = create_realcugan_resources(tmp_path / "resource" / "realcugan")
    routed = RoutedInferenceBackend(backend, upscaler)
    calls = []

    # 方法说明：模拟首阶段成功并记录实际执行档位。
    def process_flux2(assets, output_path, options):
        calls.append(str(options.mode))
        Image.new("RGB", (16, 24), "white").save(output_path, format="WEBP")
        return InferenceOutcome(
            reference_applied=True,
            model_profile="flux2-klein-4b",
        )

    # 方法说明：模拟 UPSCALE 二阶段执行失败。
    def fail_upscale(*_args, **_kwargs):
        raise RuntimeError("upscale failed")

    monkeypatch.setattr(backend, "process", process_flux2)
    monkeypatch.setattr(upscaler, "process", fail_upscale)

    with pytest.raises(RuntimeError, match="upscale failed"):
        routed.process(
            InferenceAssets(image_bytes=png_bytes((8, 12))),
            tmp_path / "failed.webp",
            ProcessOptions(mode="flux2"),
        )
    assert calls == ["flux2"]


# 方法说明：验证未启用放大资源时处理接口明确拒绝请求。
def test_upscale_process_rejects_unavailable_profile(tmp_path):
    settings = Settings(
        api_token="test-token",
        runtime_dir=tmp_path / "runtime",
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
