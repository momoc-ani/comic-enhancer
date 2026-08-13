import hashlib
import json

from comic_enhancer.adapters import AdapterRegistry
from comic_enhancer.models import AdapterSource, WorkIdentity


def write_index(tmp_path, data):
    path = tmp_path / "index.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


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
