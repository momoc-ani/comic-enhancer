import hashlib
import json

from comic_enhancer.adapters import AdapterRegistry
from comic_enhancer.models import AdapterSource, WorkIdentity


# 方法说明：写入测试使用的适配器索引。
def write_index(tmp_path, data):
    path = tmp_path / "index.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# 方法说明：验证作品专用适配器优先于通用适配器。
def test_work_adapter_has_priority(tmp_path):
    work_file = tmp_path / "work.safetensors"
    work_file.write_bytes(b"work")
    digest = hashlib.sha256(b"work").hexdigest()
    index = write_index(
        tmp_path,
        {
            "generic": {
                "adapter_id": "generic",
                "name": "Generic",
                "base_model": "sd15",
                "revision": "development-placeholder",
            },
            "works": {
                "copy_manga:42": {
                    "adapter_id": "work-42",
                    "name": "Work 42",
                    "base_model": "sd15",
                    "revision": "v1",
                    "file": "work.safetensors",
                    "sha256": digest,
                    "work_key": "copy_manga:42",
                }
            },
        },
    )
    registry = AdapterRegistry(index, "generic")

    resolved = registry.resolve(
        WorkIdentity(source="copy_manga", source_work_id="42")
    )

    assert resolved.source == AdapterSource.WORK
    assert resolved.adapter.adapter_id == "work-42"


# 方法说明：验证缺少作品适配器时会回退到通用适配器。
def test_generic_adapter_fallback(tmp_path):
    generic_file = tmp_path / "generic.safetensors"
    generic_file.write_bytes(b"generic")
    index = write_index(
        tmp_path,
        {
            "generic": {
                "adapter_id": "generic",
                "name": "Generic",
                "base_model": "sd15",
                "revision": "v1",
                "file": "generic.safetensors",
            },
            "works": {},
        },
    )
    registry = AdapterRegistry(index, "generic")

    resolved = registry.resolve(
        WorkIdentity(source="copy_manga", source_work_id="missing")
    )

    assert resolved.source == AdapterSource.GENERIC
    assert resolved.adapter.adapter_id == "generic"


# 方法说明：验证禁用通用回退后不会选择任何适配器。
def test_no_adapter_when_generic_is_disabled(tmp_path):
    index = write_index(
        tmp_path,
        {
            "generic": {
                "adapter_id": "generic",
                "name": "Generic",
                "base_model": "sd15",
                "revision": "development-placeholder",
            },
            "works": {},
        },
    )
    registry = AdapterRegistry(index, "generic")

    resolved = registry.resolve(
        WorkIdentity(source="copy_manga", source_work_id="missing"),
        allow_generic_adapter=False,
    )

    assert resolved.source == AdapterSource.NONE
    assert resolved.adapter is None


# 方法说明：验证缺少权重的占位适配器不会被视为可用。
def test_placeholder_without_weight_is_not_available(tmp_path):
    index = write_index(
        tmp_path,
        {
            "generic": {
                "adapter_id": "generic",
                "name": "Generic",
                "base_model": "sd15",
                "revision": "development-placeholder",
                "file": None,
            },
            "works": {},
        },
    )
    registry = AdapterRegistry(index, "generic")

    resolved = registry.resolve(
        WorkIdentity(source="copy_manga", source_work_id="missing")
    )

    assert resolved.source == AdapterSource.NONE
    assert resolved.adapter is None


# 方法说明：验证不兼容的作品适配器会回退到通用适配器。
def test_incompatible_work_adapter_falls_back_to_generic(tmp_path):
    work_file = tmp_path / "work.safetensors"
    work_file.write_bytes(b"work")
    generic_file = tmp_path / "generic.safetensors"
    generic_file.write_bytes(b"generic")
    index = write_index(
        tmp_path,
        {
            "generic": {
                "adapter_id": "generic",
                "name": "Generic",
                "base_model": "sd15",
                "revision": "v1",
                "file": "generic.safetensors",
            },
            "works": {
                "copy_manga:42": {
                    "adapter_id": "cobra-work",
                    "name": "Cobra Work",
                    "base_model": "cobra-pixart",
                    "revision": "v1",
                    "file": "work.safetensors",
                }
            },
        },
    )
    registry = AdapterRegistry(index, "generic")

    resolved = registry.resolve(
        WorkIdentity(source="copy_manga", source_work_id="42"),
        compatible_base_models=frozenset({"sd15"}),
    )

    assert resolved.source == AdapterSource.GENERIC
    assert resolved.adapter.adapter_id == "generic"


# 方法说明：验证适配器必须提供请求档位对应的工作流。
def test_adapter_requires_workflow_for_requested_mode(tmp_path):
    work_file = tmp_path / "work.safetensors"
    work_file.write_bytes(b"work")
    generic_file = tmp_path / "generic.safetensors"
    generic_file.write_bytes(b"generic")
    index = write_index(
        tmp_path,
        {
            "generic": {
                "adapter_id": "generic",
                "name": "Generic",
                "base_model": "sd15-anime",
                "revision": "v1",
                "file": "generic.safetensors",
                "workflows": {"fast": "generic-fast.json"},
            },
            "works": {
                "copy_manga:42": {
                    "adapter_id": "work-42",
                    "name": "Work 42",
                    "base_model": "sd15-anime",
                    "revision": "v1",
                    "file": "work.safetensors",
                    "workflows": {"quality": "work-42-quality.json"},
                }
            },
        },
    )
    registry = AdapterRegistry(index, "generic")

    resolved = registry.resolve(
        WorkIdentity(source="copy_manga", source_work_id="42"),
        compatible_base_models=frozenset({"sd15-anime"}),
        required_workflow="fast",
    )

    assert resolved.source == AdapterSource.GENERIC
