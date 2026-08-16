from __future__ import annotations

import hashlib
from io import BytesIO
import logging
import os
import platform
from pathlib import Path
import subprocess
import tempfile
import time

from PIL import Image, ImageOps

from .contracts import InferenceAssets, InferenceOutcome
from ..logging_utils import log_operation


logger = logging.getLogger(__name__)
REALCUGAN_MODEL_PROFILE = "realcugan-se-2x"
REALCUGAN_PROCESSING_REVISION = "realcugan-se-2x-no-denoise-v1"


class RealCuganUpscaler:
    """通过当前平台的 Real-CUGAN 原生程序执行固定两倍放大。"""

    model_profile = REALCUGAN_MODEL_PROFILE
    model_stem = "up2x-no-denoise"

    # 方法说明：初始化平台资源目录、开关和超时配置。
    def __init__(
        self,
        *,
        enabled: bool,
        resource_root: Path,
        timeout_seconds: int,
    ):
        self.enabled = enabled
        self.resource_root = resource_root.resolve()
        self.timeout_seconds = max(1, timeout_seconds)
        self._revision_signature: tuple[tuple[str, int, int], ...] | None = None
        self._revision_value = ""

    # 方法说明：返回当前操作系统与架构对应的资源目录名。
    @staticmethod
    def platform_key() -> str | None:
        system = platform.system().lower()
        machine = platform.machine().lower()
        architecture = {
            "amd64": "x64",
            "x86_64": "x64",
            "arm64": "arm64",
            "aarch64": "arm64",
        }.get(machine)
        system_name = {
            "windows": "windows",
            "linux": "linux",
            "darwin": "macos",
        }.get(system)
        if not architecture or not system_name:
            return None
        return f"{system_name}-{architecture}"

    # 方法说明：返回当前平台的 Real-CUGAN 资源目录。
    def platform_dir(self) -> Path | None:
        key = self.platform_key()
        return self.resource_root / key if key else None

    # 方法说明：返回当前平台的 Real-CUGAN 可执行文件路径。
    def executable_path(self) -> Path | None:
        directory = self.platform_dir()
        if directory is None:
            return None
        filename = (
            "realcugan-ncnn-vulkan.exe"
            if platform.system().lower() == "windows"
            else "realcugan-ncnn-vulkan"
        )
        return directory / filename

    # 方法说明：返回固定 models-se 模型目录。
    def model_dir(self) -> Path | None:
        directory = self.platform_dir()
        return directory / "models-se" if directory else None

    # 方法说明：返回执行两倍无降噪放大所需的全部文件。
    def required_files(self) -> tuple[Path, ...]:
        executable = self.executable_path()
        model_dir = self.model_dir()
        if executable is None or model_dir is None:
            return ()
        return (
            executable,
            model_dir / f"{self.model_stem}.param",
            model_dir / f"{self.model_stem}.bin",
        )

    # 方法说明：检查开关、可执行文件和两倍模型权重是否齐全。
    def available(self) -> bool:
        files = self.required_files()
        if not self.enabled or not files or not all(path.is_file() for path in files):
            return False
        executable = self.executable_path()
        return bool(
            executable
            and (
                platform.system().lower() == "windows"
                or os.access(executable, os.X_OK)
            )
        )

    # 方法说明：计算可执行文件和模型权重共同决定的缓存版本。
    def cache_revision(self) -> str:
        if not self.available():
            return f"{REALCUGAN_PROCESSING_REVISION}:unavailable"
        files = self.required_files()
        signature = tuple(
            (str(path), path.stat().st_size, path.stat().st_mtime_ns)
            for path in files
        )
        if signature != self._revision_signature:
            digest = hashlib.sha256()
            for path in files:
                digest.update(path.name.encode("utf-8"))
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
            self._revision_signature = signature
            self._revision_value = digest.hexdigest()
        return f"{REALCUGAN_PROCESSING_REVISION}:{self._revision_value}"

    # 方法说明：调用 Real-CUGAN 并将结果原子保存为缓存 WebP。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
    ) -> InferenceOutcome:
        if not self.available():
            raise RuntimeError("Real-CUGAN 放大资源未启用或文件不完整")
        executable = self.executable_path()
        model_dir = self.model_dir()
        platform_dir = self.platform_dir()
        if executable is None or model_dir is None or platform_dir is None:
            raise RuntimeError("当前平台不支持 Real-CUGAN 放大资源")

        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="comic-enhancer-realcugan-") as temp:
            temp_dir = Path(temp)
            input_path = temp_dir / "input.png"
            native_output_path = temp_dir / "output.png"
            with Image.open(BytesIO(assets.image_bytes)) as source_file:
                source = ImageOps.exif_transpose(source_file).convert("RGB")
                source_size = source.size
                source.save(input_path, format="PNG")

            command = [
                str(executable),
                "-i",
                str(input_path),
                "-o",
                str(native_output_path),
                "-s",
                "2",
                "-n",
                "-1",
                "-m",
                str(model_dir),
                "-f",
                "png",
            ]
            log_operation(
                logger,
                logging.INFO,
                feature="Real-CUGAN放大执行",
                parameters={
                    "executable": executable.name,
                    "model_dir": str(model_dir),
                    "scale": 2,
                    "denoise": -1,
                    "timeout_seconds": self.timeout_seconds,
                    "source_size": list(source_size),
                },
                result={
                    "status": "started",
                    "output_format": "png",
                },
            )
            completed = subprocess.run(
                command,
                cwd=platform_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                ),
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()[-1000:]
                log_operation(
                    logger,
                    logging.ERROR,
                    feature="Real-CUGAN放大执行",
                    parameters={
                        "executable": executable.name,
                        "source_size": list(source_size),
                    },
                    result={
                        "status": "failed",
                        "returncode": completed.returncode,
                    },
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                )
                raise RuntimeError(
                    f"Real-CUGAN 执行失败（退出码 {completed.returncode}）: {detail}"
                )
            if not native_output_path.is_file():
                raise RuntimeError("Real-CUGAN 未生成输出图片")
            with Image.open(native_output_path) as result_file:
                result = ImageOps.exif_transpose(result_file).convert("RGB").copy()

        expected_size = (source_size[0] * 2, source_size[1] * 2)
        if result.size != expected_size:
            log_operation(
                logger,
                logging.ERROR,
                feature="Real-CUGAN放大校验",
                parameters={
                    "source_size": list(source_size),
                    "expected_size": list(expected_size),
                },
                result={
                    "status": "failed",
                    "actual_size": list(result.size),
                },
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
            raise RuntimeError(
                "Real-CUGAN 输出尺寸不符合两倍放大契约: "
                f"expected={expected_size} actual={result.size}"
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.webp")
        result.save(temporary, format="WEBP", quality=93, method=4)
        temporary.replace(output_path)
        log_operation(
            logger,
            logging.INFO,
            feature="Real-CUGAN放大完成",
            parameters={
                "scale": 2,
                "denoise": -1,
                "source_size": list(source_size),
            },
            result={
                "status": "success",
                "output_size": list(result.size),
                "model_profile": self.model_profile,
            },
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return InferenceOutcome(model_profile=self.model_profile)
