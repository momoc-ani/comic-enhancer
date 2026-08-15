from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from scripts import demo_qwen3_vl_gguf as demo


# 方法说明：验证多图请求保留页面与参考图的明确顺序。
def test_build_content_parts_preserves_image_roles(tmp_path: Path) -> None:
    page = tmp_path / "page.jpg.c1500x.webp"
    reference = tmp_path / "reference.jpg"
    Image.new("RGB", (8, 8), "white").save(page, format="WEBP")
    Image.new("RGB", (8, 8), "red").save(reference, format="JPEG")

    parts = demo.build_content_parts(page, [reference], "analyze", ["角色 A"])

    assert parts[0]["text"] == "Picture 1 = current manga page."
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert 'exact name = "角色 A"' in parts[2]["text"]
    assert parts[3]["image_url"]["url"].startswith("data:image/png;base64,")
    assert '"name": "角色 A"' in parts[4]["text"]
    assert parts[4]["text"].endswith("analyze")


# 方法说明：验证 JSON 模式只增加兼容的响应格式约束。
def test_build_request_payload_enables_json_object() -> None:
    payload = demo.build_request_payload(
        [{"type": "text", "text": "analyze"}],
        max_tokens=256,
        temperature=0.0,
        json_output=True,
    )

    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["content"][0]["text"] == "analyze"
    assert payload["seed"] == 42
    assert payload["stream"] is False


# 方法说明：验证角色名默认来自文件名，并拒绝重复或数量不一致的映射。
def test_resolve_reference_names_validates_mapping(tmp_path: Path) -> None:
    references = [tmp_path / "艾尔玛.jpg", tmp_path / "路切.png"]

    assert demo.resolve_reference_names(references, []) == ["艾尔玛", "路切"]
    with pytest.raises(ValueError, match="数量"):
        demo.resolve_reference_names(references, ["艾尔玛"])
    with pytest.raises(ValueError, match="重复"):
        demo.resolve_reference_names(references, ["同名", "同名"])


# 方法说明：验证结构化结果拒绝候选列表之外的模型自创角色名。
def test_validate_character_analysis_rejects_unknown_name() -> None:
    value = {
        "characters": [
            {"name": "虚构角色", "visible": True, "instances": []},
        ]
    }

    with pytest.raises(RuntimeError, match="候选列表外"):
        demo.validate_character_analysis(value, ["艾尔玛"])


# 方法说明：验证角色分析契约接受候选匹配并拒绝未匹配人物姓名。
def test_validate_character_analysis_checks_unmatched_people() -> None:
    value = {
        "characters": [
            {
                "name": "艾尔玛",
                "reference_slot": 1,
                "visible": True,
                "instances": [
                    {
                        "panel_id": 3,
                        "box_2d": [100, 200, 300, 400],
                        "match_evidence": ["发型一致"],
                        "counter_evidence": [],
                        "confidence": 0.8,
                    }
                ],
            }
        ],
        "unmatched_people": [
            {"panel_id": 1, "box_2d": [10, 20, 30, 40], "reason": "证据不足"}
        ],
    }

    demo.validate_character_analysis(value, ["艾尔玛"])
    value["unmatched_people"][0]["name"] = "虚构姓名"
    with pytest.raises(RuntimeError, match="不得包含姓名"):
        demo.validate_character_analysis(value, ["艾尔玛"])


# 方法说明：验证启动参数同时绑定主模型、视觉投影和本机地址。
def test_build_server_command_contains_multimodal_paths(tmp_path: Path) -> None:
    command = demo.build_server_command(
        executable=tmp_path / "llama-server.exe",
        model=tmp_path / "model.gguf",
        mmproj=tmp_path / "mmproj.gguf",
        host="127.0.0.1",
        port=8091,
        context_size=8192,
        gpu_layers=99,
    )

    assert command[0].endswith("llama-server.exe")
    assert command[command.index("--mmproj") + 1].endswith("mmproj.gguf")
    assert command[command.index("-ngl") + 1] == "99"
    assert command[command.index("--parallel") + 1] == "1"
    assert command[command.index("--image-min-tokens") + 1] == "1024"
    assert command[command.index("--host") + 1] == "127.0.0.1"


# 方法说明：验证模型文件大小和摘要校验拒绝损坏内容。
def test_verify_model_file_checks_size_and_hash(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"verified-model")
    expected_hash = hashlib.sha256(b"verified-model").hexdigest()
    spec = demo.ModelFileSpec(model, model.stat().st_size, expected_hash)

    demo.verify_model_file(spec, verify_hash=True)
    model.write_bytes(b"broken")

    with pytest.raises(RuntimeError, match="大小错误"):
        demo.verify_model_file(spec, verify_hash=True)


# 方法说明：验证 OpenAI 兼容响应的文本提取与错误语义。
def test_extract_response_content_rejects_missing_choices() -> None:
    assert (
        demo.extract_response_content(
            {"choices": [{"message": {"content": "result"}}]}
        )
        == "result"
    )
    with pytest.raises(RuntimeError, match="缺少"):
        demo.extract_response_content({})
