import base64
import hashlib
import json

import httpx
import pytest

from comic_enhancer.gitee import GiteeAdapterStore, GiteeError
from comic_enhancer.models import AdapterManifest


def store(handler):
    return GiteeAdapterStore(
        api_url="https://gitee.com/api/v5",
        owner="robot",
        repo="comic-lora",
        branch="main",
        token="secret",
        index_path="adapters/index.json",
        release_tag="lora",
        transport=httpx.MockTransport(handler),
    )


def test_sync_index_uses_expected_repository_and_atomic_file(tmp_path):
    index = {"schema_version": 1, "generic": None, "works": {}}

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v5/repos/robot/comic-lora/contents/adapters/index.json"
        assert request.url.params["access_token"] == "secret"
        return httpx.Response(
            200,
            json={
                "content": base64.b64encode(json.dumps(index).encode()).decode(),
                "sha": "index-sha",
            },
        )

    target = tmp_path / "adapters" / "index.json"
    result = store(handler).sync_index(target)

    assert result == index
    assert json.loads(target.read_text()) == index


def test_download_requires_gitee_url_and_matching_sha(tmp_path):
    data = b"safetensors-placeholder"
    manifest = AdapterManifest(
        adapter_id="work-42",
        name="Work 42",
        base_model="sd15-anime",
        revision="v1",
        file="works/work-42.safetensors",
        sha256=hashlib.sha256(data).hexdigest(),
        download_url="https://gitee.com/robot/comic-lora/attach_files/42/download",
        work_key="copy_manga:42",
    )

    client = store(lambda request: httpx.Response(200, content=data))
    target = client.download_adapter(manifest, tmp_path)

    assert target.read_bytes() == data

    unsafe = manifest.model_copy(update={"download_url": "https://example.com/lora"})
    with pytest.raises(GiteeError, match="Gitee HTTPS"):
        client.download_adapter(unsafe, tmp_path)


def test_publish_uploads_version_then_updates_index(tmp_path, monkeypatch):
    source = tmp_path / "work-42-v1.safetensors"
    source.write_bytes(b"weights")
    manifest = AdapterManifest(
        adapter_id="work-42-v1",
        name="Work 42 V1",
        base_model="sd15-anime",
        revision="v1",
        file="works/work-42-v1.safetensors",
        work_key="copy_manga:42",
    )
    client = store(lambda request: httpx.Response(500))
    calls = []
    monkeypatch.setattr(
        client,
        "_get_or_create_release",
        lambda body: calls.append("release") or {"id": 9},
    )
    monkeypatch.setattr(
        client,
        "_upload_release_asset",
        lambda release_id, path, name: calls.append("upload")
        or {
            "id": 10,
            "browser_download_url": "https://gitee.com/robot/comic-lora/attach_files/10/download",
        },
    )
    monkeypatch.setattr(
        client,
        "_update_index",
        lambda published, message: calls.append("index"),
    )

    published = client.publish_adapter(
        source=source,
        manifest=manifest,
        commit_message="publish",
    )

    assert calls == ["release", "upload", "index"]
    assert published.sha256 == hashlib.sha256(b"weights").hexdigest()
    assert published.asset_id == 10
