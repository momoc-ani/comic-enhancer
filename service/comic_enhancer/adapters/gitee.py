from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..domain import AdapterManifest


class GiteeError(RuntimeError):
    """表示适配器索引或权重分发失败。"""


class GiteeAdapterStore:
    """通过 Gitee 索引和 Release 附件分发版本化适配器。"""

    # 方法说明：初始化 Gitee 仓库、认证、发行版和请求配置。
    def __init__(
        self,
        *,
        api_url: str,
        owner: str,
        repo: str,
        branch: str,
        token: str,
        index_path: str,
        release_tag: str,
        timeout_seconds: int = 60,
        transport: httpx.BaseTransport | None = None,
    ):
        if not owner or not repo or not token:
            raise ValueError("Gitee 配置缺少 owner、repo 或 token")
        self.api_url = api_url.rstrip("/")
        self.owner = owner
        self.repo = repo
        self.branch = branch
        self.token = token
        self.index_path = index_path.strip("/")
        self.release_tag = release_tag
        self.timeout = httpx.Timeout(timeout_seconds, connect=10)
        self.transport = transport

    # 方法说明：返回 Gitee 仓库的 API 路径。
    @property
    def repo_path(self) -> str:
        return f"/repos/{self.owner}/{self.repo}"

    # 方法说明：创建配置好认证和超时的 HTTP 客户端。
    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.api_url,
            timeout=self.timeout,
            transport=self.transport,
            headers={"Accept": "application/json", "User-Agent": "comic-enhancer"},
        )

    # 方法说明：合并 Gitee 请求所需的访问参数。
    def _params(self, **extra: str) -> dict[str, str]:
        return {"access_token": self.token, **extra}

    # 方法说明：从 Gitee 读取适配器索引及文件摘要。
    def fetch_index(self) -> tuple[dict[str, Any], str | None]:
        with self._client() as client:
            response = client.get(
                f"{self.repo_path}/contents/{self.index_path}",
                params=self._params(ref=self.branch),
            )
            self._raise(response)
            payload = response.json()
        if isinstance(payload, list):
            raise GiteeError("Gitee 索引路径指向目录，而不是文件")
        try:
            content = base64.b64decode(payload["content"].replace("\n", ""))
            return json.loads(content.decode("utf-8")), payload.get("sha")
        except (KeyError, ValueError, UnicodeDecodeError) as error:
            raise GiteeError("Gitee 远端索引不是有效 JSON") from error

    # 方法说明：同步远端适配器索引到本地。
    def sync_index(self, local_index: Path) -> dict[str, Any]:
        index, _ = self.fetch_index()
        self._atomic_write_json(local_index, index)
        return index

    # 方法说明：下载、校验并原子安装指定远端适配器。
    def download_adapter(
        self,
        manifest: AdapterManifest,
        weights_root: Path,
    ) -> Path:
        if not manifest.file or not manifest.file.endswith(".safetensors"):
            raise GiteeError("只允许下载 .safetensors LoRA")
        if not manifest.sha256 or len(manifest.sha256) != 64:
            raise GiteeError("远端 LoRA 必须提供完整 SHA-256")
        target = (weights_root / manifest.file).resolve()
        try:
            target.relative_to(weights_root.resolve())
        except ValueError as error:
            raise GiteeError("LoRA 路径越界") from error
        target.parent.mkdir(parents=True, exist_ok=True)

        download_url = manifest.download_url
        with self._client() as client:
            if download_url:
                self._validate_download_url(download_url)
                response = client.get(
                    download_url,
                    params=self._params(),
                    follow_redirects=True,
                )
            else:
                response = client.get(
                    f"{self.repo_path}/contents/{manifest.file}",
                    params=self._params(ref=self.branch),
                )
            self._raise(response)
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            with temporary.open("wb") as stream:
                if download_url:
                    stream.write(response.content)
                else:
                    payload = response.json()
                    stream.write(
                        base64.b64decode(payload["content"].replace("\n", ""))
                    )
        digest = self._sha256(temporary)
        if manifest.sha256 and digest.lower() != manifest.sha256.lower():
            temporary.unlink(missing_ok=True)
            raise GiteeError(f"LoRA SHA-256 校验失败: {manifest.adapter_id}")
        temporary.replace(target)
        return target

    # 方法说明：发布指定适配器并更新索引。
    def publish_adapter(
        self,
        *,
        source: Path,
        manifest: AdapterManifest,
        commit_message: str,
    ) -> AdapterManifest:
        if not source.is_file() or source.suffix.lower() != ".safetensors":
            raise GiteeError("待发布文件必须是存在的 .safetensors")
        digest = self._sha256(source)
        if manifest.sha256 and manifest.sha256.lower() != digest:
            raise GiteeError("待发布 LoRA 的 SHA-256 与清单不一致")
        file_name = PurePosixPath(manifest.file or source.name).name
        release = self._get_or_create_release(commit_message)
        asset = self._upload_release_asset(release["id"], source, file_name)
        published = manifest.model_copy(
            update={
                "file": manifest.file or f"release/{file_name}",
                "sha256": digest,
                "download_url": (
                    asset.get("browser_download_url") or asset.get("download_url")
                ),
                "release_id": release["id"],
                "asset_id": asset.get("id"),
            }
        )
        self._update_index(published, commit_message)
        return published

    # 方法说明：获取或创建用于托管适配器的发行版。
    def _get_or_create_release(self, body: str) -> dict[str, Any]:
        with self._client() as client:
            response = client.get(
                f"{self.repo_path}/releases/tags/{self.release_tag}",
                params=self._params(),
            )
            if response.status_code == 200:
                return response.json()
            if response.status_code != 404:
                self._raise(response)
            response = client.post(
                f"{self.repo_path}/releases",
                data={
                    "access_token": self.token,
                    "tag_name": self.release_tag,
                    "name": f"Comic Enhancer LoRA {self.release_tag}",
                    "body": body,
                    "target_commitish": self.branch,
                },
            )
            self._raise(response)
            return response.json()

    # 方法说明：上传适配器文件到指定发行版。
    def _upload_release_asset(
        self,
        release_id: int,
        source: Path,
        file_name: str,
    ) -> dict[str, Any]:
        with self._client() as client, source.open("rb") as stream:
            response = client.post(
                f"{self.repo_path}/releases/{release_id}/attach_files",
                params=self._params(),
                files={"file": (file_name, stream, "application/octet-stream")},
            )
            self._raise(response)
            payload = response.json()
        if isinstance(payload, list):
            if not payload:
                raise GiteeError("Gitee 未返回 Release 附件")
            return payload[-1]
        return payload

    # 方法说明：将适配器清单写回远端索引。
    def _update_index(self, manifest: AdapterManifest, message: str) -> None:
        index, sha = self.fetch_index()
        works = index.setdefault("works", {})
        if manifest.work_key:
            works[manifest.work_key] = json.loads(manifest.model_dump_json())
        elif (index.get("generic") or {}).get("adapter_id") == manifest.adapter_id:
            index["generic"] = json.loads(manifest.model_dump_json())
        else:
            raise GiteeError(
                "发布清单必须包含 work_key，或匹配当前 generic adapter_id"
            )
        content = base64.b64encode(
            json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("ascii")
        payload = {
            "access_token": self.token,
            "content": content,
            "message": message,
            "branch": self.branch,
        }
        if sha:
            payload["sha"] = sha
        with self._client() as client:
            response = client.put(
                f"{self.repo_path}/contents/{self.index_path}",
                data=payload,
            )
            self._raise(response)

    # 方法说明：原子写入格式化的 JSON 文件。
    @staticmethod
    def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    # 方法说明：计算文件的 SHA-256 摘要。
    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    # 方法说明：将失败的 Gitee 响应转换为明确异常。
    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.is_error:
            detail = response.text[:500]
            raise GiteeError(f"Gitee API {response.status_code}: {detail}")

    # 方法说明：校验下载地址是否属于受信任的 Gitee 域名。
    @staticmethod
    def _validate_download_url(download_url: str) -> None:
        parsed = urlsplit(download_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            host == "gitee.com" or host.endswith(".gitee.com")
        ):
            raise GiteeError("LoRA 下载地址必须是 Gitee HTTPS 地址")
