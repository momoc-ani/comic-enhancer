from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import logging
import os
import platform
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid

import httpx
from PIL import Image, ImageChops, ImageEnhance, ImageOps

from .models import (
    ProcessingMode,
    ProcessOptions,
    ResolvedAdapter,
)
from .workflows import WorkflowLoader


logger = logging.getLogger(__name__)
FLUX2_PROCESSING_REVISION = "flux2-baseline-direct-prompt-v12"
FLUX2_OUTPUT_SCALE = 2
REALCUGAN_MODEL_PROFILE = "realcugan-se-2x"
REALCUGAN_PROCESSING_REVISION = "realcugan-se-2x-no-denoise-v1"


class InferenceBackend(ABC):
    name: str
    applies_adapters: bool = False
    supported_base_models: frozenset[str] = frozenset()
    model_profiles: tuple[str, ...] = ()

    # 方法说明：检查推理后端是否已准备就绪。
    def ready(self) -> bool:
        return True

    # 方法说明：检查 Cobra 模型档位是否可用。
    def cobra_profile_ready(self) -> bool:
        return False

    # 方法说明：检查 FLUX.2 模型档位是否可用。
    def flux2_profile_ready(self) -> bool:
        return False

    # 方法说明：检查 FLUX.2 量化模型档位是否可用。
    def flux2_quant_profile_ready(self) -> bool:
        return False

    # 方法说明：检查 Real-CUGAN 放大档位是否可用。
    def upscale_profile_ready(self) -> bool:
        return False

    # 方法说明：生成影响推理缓存的版本标识。
    def cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: "InferenceAssets | None" = None,
    ) -> str:
        return self.name

    # 方法说明：返回当前处理档位的适配器使用策略。
    def adapter_policy(
        self,
        assets: "InferenceAssets",
        options: ProcessOptions,
    ) -> "AdapterPolicy":
        return AdapterPolicy(
            enabled=True,
            compatible_base_models=self.supported_base_models,
            required_workflow=(
                str(options.mode) if self.applies_adapters else None
            ),
        )

    # 方法说明：按当前策略处理输入并返回推理结果。
    @abstractmethod
    def process(
        self,
        assets: "InferenceAssets",
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> "InferenceOutcome":
        raise NotImplementedError


@dataclass(frozen=True)
class InferenceOutcome:
    adapter_applied: bool
    reference_applied: bool = False
    processed_panels: int = 0
    model_profile: str = ""


@dataclass(frozen=True)
class InferenceAssets:
    image_bytes: bytes
    reference_bytes: bytes | None = None
    character_references: dict[str, bytes] | None = None


@dataclass(frozen=True)
class AdapterPolicy:
    enabled: bool
    compatible_base_models: frozenset[str]
    required_workflow: str | None


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
        logger.info("Real-CUGAN 两倍放大开始")
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
                raise RuntimeError(
                    f"Real-CUGAN 执行失败（退出码 {completed.returncode}）: {detail}"
                )
            if not native_output_path.is_file():
                raise RuntimeError("Real-CUGAN 未生成输出图片")
            with Image.open(native_output_path) as result_file:
                result = ImageOps.exif_transpose(result_file).convert("RGB").copy()

        expected_size = (source_size[0] * 2, source_size[1] * 2)
        if result.size != expected_size:
            raise RuntimeError(
                "Real-CUGAN 输出尺寸不符合两倍放大契约: "
                f"expected={expected_size} actual={result.size}"
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.webp")
        result.save(temporary, format="WEBP", quality=93, method=4)
        temporary.replace(output_path)
        logger.info(
            "Real-CUGAN 两倍放大完成，耗时 %.3f 秒",
            time.perf_counter() - started,
        )
        return InferenceOutcome(
            adapter_applied=False,
            model_profile=self.model_profile,
        )


class RoutedInferenceBackend(InferenceBackend):
    """在主推理后端之外路由不依赖 ComfyUI 的独立处理档位。"""

    # 方法说明：组合主推理后端与平台原生放大处理器。
    def __init__(
        self,
        backend: InferenceBackend,
        upscaler: RealCuganUpscaler,
    ):
        self.backend = backend
        self.upscaler = upscaler
        self.name = backend.name
        self.applies_adapters = backend.applies_adapters
        self.supported_base_models = backend.supported_base_models

    # 方法说明：返回当前实际可声明的模型档位。
    @property
    def model_profiles(self) -> tuple[str, ...]:
        profiles = list(self.backend.model_profiles)
        if self.upscale_profile_ready():
            profiles.append(self.upscaler.model_profile)
        return tuple(dict.fromkeys(profiles))

    # 方法说明：检查主推理后端是否已准备就绪。
    def ready(self) -> bool:
        return self.backend.ready()

    # 方法说明：检查 Cobra 模型档位是否可用。
    def cobra_profile_ready(self) -> bool:
        return self.backend.cobra_profile_ready()

    # 方法说明：检查 FLUX.2 模型档位是否可用。
    def flux2_profile_ready(self) -> bool:
        return self.backend.flux2_profile_ready()

    # 方法说明：检查 FLUX.2 量化模型档位是否可用。
    def flux2_quant_profile_ready(self) -> bool:
        return self.backend.flux2_quant_profile_ready()

    # 方法说明：检查 Real-CUGAN 放大档位是否可用。
    def upscale_profile_ready(self) -> bool:
        return self.upscaler.available()

    # 方法说明：生成所选档位影响推理缓存的版本标识。
    def cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None = None,
    ) -> str:
        if options.mode == ProcessingMode.UPSCALE:
            return self.upscaler.cache_revision()
        revision = self.backend.cache_revision(options, resolved, assets)
        return revision if self.name == self.backend.name else f"{self.name}:{revision}"

    # 方法说明：返回独立放大档或主推理档对应的适配器策略。
    def adapter_policy(
        self,
        assets: InferenceAssets,
        options: ProcessOptions,
    ) -> AdapterPolicy:
        if options.mode == ProcessingMode.UPSCALE:
            return AdapterPolicy(
                enabled=False,
                compatible_base_models=frozenset(),
                required_workflow=None,
            )
        return self.backend.adapter_policy(assets, options)

    # 方法说明：将请求路由到 Real-CUGAN 或现有主推理后端。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        if options.mode == ProcessingMode.UPSCALE:
            return self.upscaler.process(assets, output_path)
        return self.backend.process(assets, output_path, options, resolved)


class ComfyUIModeStrategy(ABC):
    """Owns one processing mode while reusing transport primitives from the backend."""

    mode: ProcessingMode
    adapter_workflow: str

    # 方法说明：检查当前处理策略是否可用。
    @abstractmethod
    def available(self, backend: "ComfyUIBackend") -> bool:
        raise NotImplementedError

    # 方法说明：生成影响推理缓存的版本标识。
    @abstractmethod
    def cache_revision(
        self,
        backend: "ComfyUIBackend",
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None,
    ) -> str:
        raise NotImplementedError

    # 方法说明：返回当前处理档位的适配器使用策略。
    def adapter_policy(self, backend: "ComfyUIBackend") -> AdapterPolicy:
        return AdapterPolicy(
            enabled=True,
            compatible_base_models=backend.supported_base_models,
            required_workflow=self.adapter_workflow,
        )

    # 方法说明：按当前策略处理输入并返回推理结果。
    @abstractmethod
    def process(
        self,
        backend: "ComfyUIBackend",
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        raise NotImplementedError


@dataclass(frozen=True)
class PresetModeStrategy(ComfyUIModeStrategy):
    mode: ProcessingMode

    # 方法说明：返回当前档位要求的适配器工作流名称。
    @property
    def adapter_workflow(self) -> str:
        return str(self.mode)

    # 方法说明：检查当前处理策略是否可用。
    def available(self, backend: "ComfyUIBackend") -> bool:
        return backend.ready()

    # 方法说明：生成影响推理缓存的版本标识。
    def cache_revision(
        self,
        backend: "ComfyUIBackend",
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None,
    ) -> str:
        return backend._preset_cache_revision(options, resolved)

    # 方法说明：按当前策略处理输入并返回推理结果。
    def process(
        self,
        backend: "ComfyUIBackend",
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        return backend._process_preset(assets, output_path, options, resolved)


class CobraModeStrategy(ComfyUIModeStrategy):
    mode = ProcessingMode.COBRA
    adapter_workflow = "quality"

    # 方法说明：检查当前处理策略是否可用。
    def available(self, backend: "ComfyUIBackend") -> bool:
        return backend._workflow_profile_ready(
            str(self.mode),
            enabled=backend.cobra_enabled,
            workflow_supported=backend.workflow_loader.supports_cobra(),
        )

    # 方法说明：生成影响推理缓存的版本标识。
    def cache_revision(
        self,
        backend: "ComfyUIBackend",
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None,
    ) -> str:
        return backend._reference_model_cache_revision(options, resolved, assets)

    # 方法说明：按当前策略处理输入并返回推理结果。
    def process(
        self,
        backend: "ComfyUIBackend",
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        try:
            return backend._process_cobra(assets, output_path, resolved)
        except Exception:
            logger.exception("Cobra 实验档失败，回退到质量工作流")
            return backend._fallback_to_quality(assets, output_path, options, resolved)


class Flux2ModeStrategy(ComfyUIModeStrategy):
    mode = ProcessingMode.FLUX2
    adapter_workflow = "quality"

    # 方法说明：检查当前处理策略是否可用。
    def available(self, backend: "ComfyUIBackend") -> bool:
        return backend._workflow_profile_ready(
            str(self.mode),
            enabled=backend.flux2_enabled,
            workflow_supported=backend.workflow_loader.supports_flux2(),
        )

    # 方法说明：生成影响推理缓存的版本标识。
    def cache_revision(
        self,
        backend: "ComfyUIBackend",
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None,
    ) -> str:
        revision = backend._reference_model_cache_revision(options, resolved, assets)
        return f"{revision}:{FLUX2_PROCESSING_REVISION}"

    # 方法说明：按当前策略处理输入并返回推理结果。
    def process(
        self,
        backend: "ComfyUIBackend",
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        try:
            backend._unload_cobra_worker()
            return backend._process_flux2(assets, output_path, resolved, options)
        except Exception:
            logger.exception("FLUX.2 最高质量档失败，回退到质量工作流")
            return backend._fallback_to_quality(assets, output_path, options, resolved)


@dataclass(frozen=True)
class Flux2QuantModeStrategy(ComfyUIModeStrategy):
    """Independent FLUX.2 Qwen3 quantized experiment strategy."""

    mode = ProcessingMode.FLUX2_QUANT
    adapter_workflow = "quality"

    # 方法说明：检查当前处理策略是否可用。
    def available(self, backend: "ComfyUIBackend") -> bool:
        return backend._workflow_profile_ready(
            str(self.mode),
            enabled=backend.flux2_quant_enabled,
            workflow_supported=backend.workflow_loader.supports_flux2_quant(),
        )

    # 方法说明：生成影响推理缓存的版本标识。
    def cache_revision(
        self,
        backend: "ComfyUIBackend",
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None,
    ) -> str:
        revision = backend._reference_model_cache_revision(options, resolved, assets)
        return f"{revision}:{FLUX2_PROCESSING_REVISION}:quant"

    # 方法说明：按当前策略处理输入并返回推理结果。
    def process(
        self,
        backend: "ComfyUIBackend",
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        try:
            backend._unload_cobra_worker()
            return backend._process_flux2(assets, output_path, resolved, options)
        except Exception:
            logger.exception("FLUX.2 Qwen3 4B 量化实验档失败，回退到质量工作流")
            return backend._fallback_to_quality(assets, output_path, options, resolved)


class PassthroughBackend(InferenceBackend):
    """Development backend preserving the full API without bundling model weights."""

    name = "passthrough"

    # 方法说明：按当前策略处理输入并返回推理结果。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(BytesIO(assets.image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            if options.mode == "quality":
                image = ImageEnhance.Contrast(image).enhance(1.04)
                image = ImageEnhance.Sharpness(image).enhance(1.08)
            image.save(output_path, format="WEBP", quality=92, method=4)
        return InferenceOutcome(
            adapter_applied=False,
            model_profile="passthrough",
        )


class ComfyUIBackend(InferenceBackend):
    name = "comfyui"
    applies_adapters = True
    supported_base_models = frozenset({"sd15-anime"})
    model_profiles = (
        "sd15-colorize",
        "cobra",
        "flux2-klein-4b",
        "flux2-klein-4b-qwen3-fp8",
    )

    # 方法说明：初始化当前对象及其运行状态。
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int,
        poll_interval_seconds: float,
        workflow_loader: WorkflowLoader,
        cobra_enabled: bool = False,
        cobra_workflow: Path | None = None,
        cobra_reference_limit: int = 12,
        flux2_enabled: bool = False,
        flux2_workflow: Path | None = None,
        flux2_reference_limit: int = 3,
        flux2_quant_enabled: bool = False,
        flux2_quant_workflow: Path | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.workflow_loader = workflow_loader
        self.cobra_enabled = cobra_enabled
        self.cobra_workflow = cobra_workflow
        self.cobra_reference_limit = max(1, min(12, cobra_reference_limit))
        self.flux2_enabled = flux2_enabled
        self.flux2_workflow = flux2_workflow
        self.flux2_reference_limit = max(1, min(3, flux2_reference_limit))
        self.flux2_quant_enabled = flux2_quant_enabled
        self.flux2_quant_workflow = flux2_quant_workflow
        self._profile_ready_cache: dict[str, tuple[float, bool]] = {}
        strategies: tuple[ComfyUIModeStrategy, ...] = (
            PresetModeStrategy(ProcessingMode.FAST),
            PresetModeStrategy(ProcessingMode.QUALITY),
            CobraModeStrategy(),
            Flux2ModeStrategy(),
            Flux2QuantModeStrategy(),
        )
        self._mode_strategies = {strategy.mode: strategy for strategy in strategies}

    # 方法说明：检查推理后端是否已准备就绪。
    def ready(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/system_stats", timeout=2)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    # 方法说明：检查 Cobra 模型档位是否可用。
    def cobra_profile_ready(self) -> bool:
        return self.mode_available(ProcessingMode.COBRA)

    # 方法说明：检查 FLUX.2 模型档位是否可用。
    def flux2_profile_ready(self) -> bool:
        return self.mode_available(ProcessingMode.FLUX2)

    # 方法说明：检查 FLUX.2 量化档位是否可用。
    def flux2_quant_profile_ready(self) -> bool:
        return self.mode_available(ProcessingMode.FLUX2_QUANT)

    # 方法说明：检查指定处理档位是否可用。
    def mode_available(self, mode: ProcessingMode | str) -> bool:
        return self._strategy(mode).available(self)

    # 方法说明：解析并返回指定处理档位的策略。
    def _strategy(self, mode: ProcessingMode | str) -> ComfyUIModeStrategy:
        normalized = ProcessingMode(mode)
        try:
            return self._mode_strategies[normalized]
        except KeyError as error:
            raise ValueError(f"unsupported processing mode: {normalized}") from error

    # 方法说明：检查工作流及其模型依赖是否齐全。
    def _workflow_profile_ready(
        self,
        cache_key: str,
        *,
        enabled: bool,
        workflow_supported: bool,
    ) -> bool:
        if not enabled or not workflow_supported:
            return False
        now = time.monotonic()
        cached_until, cached_value = self._profile_ready_cache.get(
            cache_key,
            (0.0, False),
        )
        if now < cached_until:
            return cached_value
        try:
            response = httpx.get(f"{self.base_url}/system_stats", timeout=2)
            ready = response.status_code == 200
        except httpx.HTTPError:
            ready = False
        self._profile_ready_cache[cache_key] = (now + (5 if ready else 1), ready)
        return ready

    # 方法说明：生成影响推理缓存的版本标识。
    def cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None = None,
    ) -> str:
        return self._strategy(options.mode).cache_revision(
            self,
            options,
            resolved,
            assets,
        )

    # 方法说明：生成预设工作流的缓存版本标识。
    def _preset_cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> str:
        return self.workflow_loader.revision(
            options,
            resolved,
            reference_available=False,
        )

    # 方法说明：生成参考模型工作流的缓存版本标识。
    def _reference_model_cache_revision(
        self,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
        assets: InferenceAssets | None,
    ) -> str:
        reference_hashes = []
        if assets is not None:
            reference_hashes = sorted(
                hashlib.sha256(value).hexdigest()
                for value in (assets.character_references or {}).values()
            )
        workflow_revision = self.workflow_loader.revision(
            options,
            resolved,
            reference_available=False,
        )
        return ":".join([workflow_revision, *reference_hashes])

    # 方法说明：返回当前处理档位的适配器使用策略。
    def adapter_policy(
        self,
        assets: InferenceAssets,
        options: ProcessOptions,
    ) -> AdapterPolicy:
        return self._strategy(options.mode).adapter_policy(self)

    # 方法说明：按当前策略处理输入并返回推理结果。
    def process(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        return self._strategy(options.mode).process(
            self,
            assets,
            output_path,
            options,
            resolved,
        )

    # 方法说明：在实验档失败时显式回退到质量档。
    def _fallback_to_quality(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        quality_options = options.model_copy(update={"mode": ProcessingMode.QUALITY})
        return self.process(assets, output_path, quality_options, resolved)

    # 方法说明：使用预设 ComfyUI 工作流处理图片。
    def _process_preset(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        loaded_workflow = self.workflow_loader.load(
            options,
            resolved,
            reference_available=False,
        )
        generated = self._run_page_prompt(
            self.base_url,
            assets.image_bytes,
            loaded_workflow.prompt,
        )
        image = self._protect_source_structure(assets.image_bytes, generated)
        self._save_output(image, output_path)
        return InferenceOutcome(
            adapter_applied=loaded_workflow.adapter_applied,
            model_profile=loaded_workflow.model_profile,
        )

    # 方法说明：请求 ComfyUI 卸载 Cobra 工作进程。
    def _unload_cobra_worker(self) -> None:
        try:
            response = httpx.post(
                f"{self.base_url}/comic-enhancer/cobra/unload",
                timeout=15,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.warning("Cobra 显存释放请求失败: %s", error)

    # 方法说明：执行 Cobra 多参考图上色流程。
    def _process_cobra(
        self,
        assets: InferenceAssets,
        output_path: Path,
        resolved: ResolvedAdapter,
    ) -> InferenceOutcome:
        if not self.cobra_profile_ready():
            raise RuntimeError("Cobra 服务未就绪")
        references = self._cobra_reference_images(assets)
        if not references:
            raise RuntimeError("Cobra 需要至少一张角色参考图")
        if self.cobra_workflow is None:
            raise RuntimeError("Cobra 工作流未配置")
        loaded_workflow = self.workflow_loader.load(
            ProcessOptions(mode="cobra"),
            resolved,
        )
        generated = self._run_cobra_prompt(
            assets.image_bytes,
            references,
            loaded_workflow.prompt,
        )
        generated = self._restore_geometry(assets.image_bytes, generated)
        self._save_output(
            self._protect_cobra_structure(assets.image_bytes, generated),
            output_path,
        )
        return InferenceOutcome(
            adapter_applied=False,
            reference_applied=True,
            model_profile="cobra",
        )

    # 方法说明：执行 FLUX.2 多参考图上色流程。
    def _process_flux2(
        self,
        assets: InferenceAssets,
        output_path: Path,
        resolved: ResolvedAdapter,
        options: ProcessOptions | None = None,
    ) -> InferenceOutcome:
        selected_options = options or ProcessOptions(mode="flux2")
        profile_ready = (
            self.flux2_quant_profile_ready()
            if selected_options.mode == ProcessingMode.FLUX2_QUANT
            else self.flux2_profile_ready()
        )
        if not profile_ready:
            raise RuntimeError("FLUX.2 服务未就绪")
        references = self._flux2_reference_images(assets)
        if not references:
            raise RuntimeError("FLUX.2 需要至少一张角色参考图")
        workflow_path = (
            self.flux2_quant_workflow
            if selected_options.mode == ProcessingMode.FLUX2_QUANT
            else self.flux2_workflow
        )
        if workflow_path is None:
            raise RuntimeError("FLUX.2 工作流未配置")
        loaded_workflow = self.workflow_loader.load(
            selected_options,
            resolved,
        )
        generated = self._run_flux2_prompt(
            assets.image_bytes,
            references,
            loaded_workflow.prompt,
        )
        generated = self._restore_geometry(
            assets.image_bytes,
            generated,
            output_scale=FLUX2_OUTPUT_SCALE,
        )
        # 工作流直接输出四步 FLUX 上色结果；API 只按原图比例统一为精确 2 倍尺寸。
        self._save_output(generated, output_path)
        return InferenceOutcome(
            adapter_applied=False,
            reference_applied=True,
            model_profile=loaded_workflow.model_profile,
        )

    # 方法说明：筛选并排序 Cobra 使用的参考图片。
    def _cobra_reference_images(self, assets: InferenceAssets) -> list[bytes]:
        candidates: list[bytes] = []
        if assets.reference_bytes is not None:
            candidates.append(assets.reference_bytes)
        candidates.extend((assets.character_references or {}).values())
        unique: list[bytes] = []
        seen: set[str] = set()
        for value in candidates:
            digest = hashlib.sha256(value).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            unique.append(value)
            if len(unique) >= self.cobra_reference_limit:
                break
        return unique

    # 方法说明：筛选并排序 FLUX.2 使用的参考图片。
    def _flux2_reference_images(self, assets: InferenceAssets) -> list[bytes]:
        candidates: list[bytes] = []
        if assets.reference_bytes is not None:
            candidates.append(assets.reference_bytes)
        candidates.extend((assets.character_references or {}).values())
        unique: list[bytes] = []
        seen: set[str] = set()
        for value in candidates:
            digest = hashlib.sha256(value).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            unique.append(value)
            if len(unique) >= self.flux2_reference_limit:
                break
        return unique

    # 方法说明：绑定输入后提交一次 Cobra 工作流。
    def _run_cobra_prompt(
        self,
        image_bytes: bytes,
        references: list[bytes],
        workflow_template: dict,
    ) -> Image.Image:
        workflow = json.loads(json.dumps(workflow_template))
        cobra_nodes = [
            node
            for node in workflow.values()
            if isinstance(node, dict) and node.get("class_type") == "CobraColorize"
        ]
        if len(cobra_nodes) != 1:
            raise RuntimeError(
                "Cobra workflow must contain exactly one CobraColorize node"
            )
        with httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds) as client:
            uploaded_references = [
                self._upload(
                    client,
                    reference,
                    f"reference-{index}",
                )
                for index, reference in enumerate(references, 1)
            ]
            input_images = {
                "INPUT_IMAGE": self._upload(client, image_bytes, "page"),
                **{
                    f"REFERENCE_IMAGE_{index}": uploaded_references[
                        min(index - 1, len(uploaded_references) - 1)
                    ]
                    for index in range(1, 13)
                },
            }
            cobra_nodes[0].setdefault("inputs", {})["reference_count"] = len(
                uploaded_references
            )
            output_nodes = self._bind_io(
                workflow,
                input_images=input_images,
                output_prefix=f"comic-enhancer/cobra-{uuid.uuid4().hex}",
            )
            queued = client.post(
                "/prompt",
                json={"prompt": workflow, "client_id": uuid.uuid4().hex},
            )
            queued.raise_for_status()
            image_info = self._wait_for_output(
                client,
                queued.json()["prompt_id"],
                output_nodes,
            )
            result = client.get("/view", params=image_info)
            result.raise_for_status()
        with Image.open(BytesIO(result.content)) as generated_file:
            return ImageOps.exif_transpose(generated_file).convert("RGB").copy()

    # 方法说明：绑定输入后提交一次 FLUX.2 工作流。
    def _run_flux2_prompt(
        self,
        image_bytes: bytes,
        references: list[bytes],
        workflow_template: dict,
    ) -> Image.Image:
        workflow = json.loads(json.dumps(workflow_template))
        with httpx.Client(base_url=self.base_url, timeout=self.timeout_seconds) as client:
            input_images = {
                "INPUT_IMAGE": self._upload(client, image_bytes, "page"),
                **{
                    f"REFERENCE_IMAGE_{index}": self._upload(
                        client,
                        references[min(index - 1, len(references) - 1)],
                        f"reference-{index}",
                    )
                    for index in range(1, 4)
                },
            }
            output_nodes = self._bind_io(
                workflow,
                input_images=input_images,
                output_prefix=f"comic-enhancer/flux2-{uuid.uuid4().hex}",
            )
            queued = client.post(
                "/prompt",
                json={"prompt": workflow, "client_id": uuid.uuid4().hex},
            )
            queued.raise_for_status()
            image_info = self._wait_for_output(
                client,
                queued.json()["prompt_id"],
                output_nodes,
            )
            result = client.get("/view", params=image_info)
            result.raise_for_status()
        with Image.open(BytesIO(result.content)) as generated_file:
            return ImageOps.exif_transpose(generated_file).convert("RGB").copy()

    # 方法说明：回注原图结构并保留 Cobra 生成的色彩。
    @staticmethod
    def _protect_cobra_structure(source_bytes: bytes, generated: Image.Image) -> Image.Image:
        """Keep source geometry while retaining Cobra chroma in colored highlights."""
        with Image.open(BytesIO(source_bytes)) as source_file:
            source = ImageOps.exif_transpose(source_file).convert("RGB")
        source = source.resize(generated.size, Image.Resampling.LANCZOS)
        source_y, _, _ = source.convert("YCbCr").split()
        generated = generated.convert("RGB")
        generated_y, generated_cb, generated_cr = generated.convert("YCbCr").split()
        _, generated_saturation, _ = generated.convert("HSV").split()
        source_highlight_mask = source_y.point(
            lambda value: 255 if value >= 248 else max(0, round((value - 220) * 255 / 28))
        )
        generated_color_mask = generated_saturation.point(
            lambda value: 255 if value >= 56 else max(0, round((value - 20) * 255 / 36))
        )
        generated_color_luma_mask = generated_y.point(
            lambda value: 255 if value >= 160 else max(0, round((value - 96) * 255 / 64))
        )
        colored_highlight_mask = ImageChops.multiply(
            source_highlight_mask,
            ImageChops.multiply(generated_color_mask, generated_color_luma_mask),
        )
        highlight_y = ImageChops.darker(
            source_y,
            ImageChops.lighter(generated_y, Image.new("L", generated.size, 192)),
        )
        protected_y = Image.composite(highlight_y, source_y, colored_highlight_mask)
        colorized = Image.merge(
            "YCbCr",
            (protected_y, generated_cb, generated_cr),
        ).convert("RGB")
        ink_mask = source_y.point(
            lambda value: (
                255
                if value <= 52
                else max(0, min(180, round((84 - value) * 180 / 32)))
            )
        )
        source_paper_mask = source_highlight_mask
        generated_light_mask = generated_y.point(
            lambda value: 255 if value >= 244 else max(0, round((value - 224) * 255 / 20))
        )
        generated_neutral_mask = generated_saturation.point(
            lambda value: 255 if value <= 16 else max(0, round((48 - value) * 255 / 32))
        )
        paper_mask = ImageChops.multiply(
            source_paper_mask,
            ImageChops.multiply(generated_light_mask, generated_neutral_mask),
        )
        structure_mask = ImageChops.lighter(ink_mask, paper_mask)
        return Image.composite(source, colorized, structure_mask)

    # 方法说明：提交单页 ComfyUI 工作流并读取结果。
    def _run_page_prompt(
        self,
        base_url: str,
        image_bytes: bytes,
        workflow_template: dict,
    ) -> Image.Image:
        workflow = json.loads(json.dumps(workflow_template))
        with httpx.Client(base_url=base_url, timeout=self.timeout_seconds) as client:
            comfy_inputs = {
                "INPUT_IMAGE": self._upload(client, image_bytes, "page"),
            }
            output_nodes = self._bind_io(
                workflow,
                input_images=comfy_inputs,
                output_prefix=f"comic-enhancer/{uuid.uuid4().hex}",
            )
            client_id = uuid.uuid4().hex
            queued = client.post("/prompt", json={"prompt": workflow, "client_id": client_id})
            queued.raise_for_status()
            prompt_id = queued.json()["prompt_id"]
            image_info = self._wait_for_output(client, prompt_id, output_nodes)
            result = client.get("/view", params=image_info)
            result.raise_for_status()
        with Image.open(BytesIO(result.content)) as source:
            return ImageOps.exif_transpose(source).convert("RGB").copy()

    # 方法说明：按统一格式原子保存推理结果图。
    @staticmethod
    def _save_output(image: Image.Image, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(".tmp.webp")
        image.save(temporary, format="WEBP", quality=93, method=4)
        temporary.replace(output_path)

    # 方法说明：将图像字节上传到 ComfyUI。
    def _upload(self, client: httpx.Client, image_bytes: bytes, role: str) -> str:
        upload_name = f"comic-enhancer-{role}-{uuid.uuid4().hex}.png"
        normalized = BytesIO()
        with Image.open(BytesIO(image_bytes)) as source:
            ImageOps.exif_transpose(source).convert("RGB").save(
                normalized,
                format="PNG",
            )
        upload = client.post(
            "/upload/image",
            files={"image": (upload_name, normalized.getvalue(), "image/png")},
            data={"type": "input", "overwrite": "true"},
        )
        upload.raise_for_status()
        return self._comfy_path(upload.json())

    # 方法说明：将图像等比缩放并填充为正方形。
    @staticmethod
    def _pad_square(image_bytes: bytes, size: int = 512) -> bytes:
        output = BytesIO()
        with Image.open(BytesIO(image_bytes)) as source_file:
            source = ImageOps.exif_transpose(source_file).convert("RGB")
            scale = min(size / source.width, size / source.height)
            resized = source.resize(
                (
                    max(1, round(source.width * scale)),
                    max(1, round(source.height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )
        canvas = Image.new("RGB", (size, size), "white")
        canvas.paste(
            resized,
            ((size - resized.width) // 2, (size - resized.height) // 2),
        )
        canvas.save(output, format="PNG", optimize=True)
        return output.getvalue()

    # 方法说明：恢复生成图与原图一致的宽高比例，并按指定倍率输出。
    @staticmethod
    def _restore_geometry(
        source_bytes: bytes,
        generated: Image.Image,
        output_scale: int = 1,
    ) -> Image.Image:
        if output_scale < 1:
            raise ValueError("output_scale must be at least 1")
        with Image.open(BytesIO(source_bytes)) as source_file:
            source = ImageOps.exif_transpose(source_file)
            source_size = source.size
        source_width, source_height = source_size
        source_ratio = source_width / source_height
        generated_ratio = generated.width / generated.height
        if generated_ratio > source_ratio:
            content_width = min(
                generated.width,
                max(1, round(generated.height * source_ratio)),
            )
            left = (generated.width - content_width) // 2
            generated = generated.crop((left, 0, left + content_width, generated.height))
        elif generated_ratio < source_ratio:
            content_height = min(
                generated.height,
                max(1, round(generated.width / source_ratio)),
            )
            top = (generated.height - content_height) // 2
            generated = generated.crop((0, top, generated.width, top + content_height))
        output_size = (
            source_width * output_scale,
            source_height * output_scale,
        )
        return generated.resize(output_size, Image.Resampling.LANCZOS)

    # 方法说明：轮询 ComfyUI 历史记录并下载输出。
    def _wait_for_output(
        self,
        client: httpx.Client,
        prompt_id: str,
        output_nodes: tuple[str, ...],
    ) -> dict[str, str]:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            response = client.get(f"/history/{prompt_id}")
            response.raise_for_status()
            history = response.json().get(prompt_id)
            if history:
                status = history.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(f"ComfyUI prompt failed: {status}")
                outputs = history.get("outputs", {})
                for node_id in reversed(output_nodes):
                    images = outputs.get(node_id, {}).get("images", [])
                    if images:
                        image = images[-1]
                        return {
                            "filename": image["filename"],
                            "subfolder": image.get("subfolder", ""),
                            "type": image.get("type", "output"),
                        }
            time.sleep(self.poll_interval_seconds)
        raise TimeoutError(f"ComfyUI prompt timed out: {prompt_id}")

    # 方法说明：发现并绑定工作流的输入、参考图和输出节点。
    @staticmethod
    def _bind_io(
        workflow: dict,
        *,
        input_images: dict[str, str],
        output_prefix: str,
    ) -> tuple[str, ...]:
        load_nodes = [
            (str(node_id), node)
            for node_id, node in workflow.items()
            if isinstance(node, dict) and node.get("class_type") == "LoadImage"
        ]
        if len(load_nodes) == 1 and "INPUT_IMAGE" in input_images:
            load_nodes[0][1].setdefault("inputs", {})["image"] = input_images[
                "INPUT_IMAGE"
            ]
        elif len(load_nodes) > 1:
            discovered_roles: set[str] = set()
            for _, node in load_nodes:
                role = str(node.get("_meta", {}).get("title", "")).strip().upper()
                if role in input_images:
                    node.setdefault("inputs", {})["image"] = input_images[role]
                    discovered_roles.add(role)
            missing = sorted(set(input_images) - discovered_roles)
            if missing:
                raise RuntimeError(
                    "ComfyUI workflow is missing titled LoadImage nodes: "
                    + ", ".join(missing)
                )
        else:
            raise RuntimeError(
                "ComfyUI workflow must contain exactly one LoadImage node or "
                "titled LoadImage nodes for all inputs; "
                f"found {len(load_nodes)}"
            )

        output_nodes = tuple(
            str(node_id)
            for node_id, node in workflow.items()
            if isinstance(node, dict) and node.get("class_type") == "SaveImage"
        )
        if not output_nodes:
            raise RuntimeError("ComfyUI workflow must contain at least one SaveImage node")
        for node_id in output_nodes:
            workflow[node_id].setdefault("inputs", {})["filename_prefix"] = output_prefix

        serialized = json.dumps(workflow, ensure_ascii=False)
        placeholders = sorted(set(re.findall(r"\$\{[^}]+\}", serialized)))
        if placeholders:
            raise RuntimeError(
                "ComfyUI workflow contains runtime placeholders: "
                + ", ".join(placeholders)
            )

        return output_nodes

    # 方法说明：将原图明度、文字和墨线回注到结果图。
    @staticmethod
    def _protect_source_structure(source_bytes: bytes, generated: Image.Image) -> Image.Image:
        with Image.open(BytesIO(source_bytes)) as source_file:
            source = ImageOps.exif_transpose(source_file).convert("RGB")
        source = source.resize(generated.size, Image.Resampling.LANCZOS)

        source_y, _, _ = source.convert("YCbCr").split()
        _, generated_cb, generated_cr = generated.convert("YCbCr").split()
        colorized = Image.merge(
            "YCbCr",
            (source_y, generated_cb, generated_cr),
        ).convert("RGB")

        color_mask = source_y.point(
            lambda value: max(0, min(255, round((245 - value) * 255 / 80)))
        )
        colorized = Image.composite(colorized, source, color_mask)

        dark_mask = source_y.point(
            lambda value: (
                255
                if value <= 112
                else max(0, min(255, round((176 - value) * 255 / 64)))
            )
        )
        return Image.composite(source, colorized, dark_mask)

    # 方法说明：拼接 ComfyUI 上传文件的内部路径。
    @staticmethod
    def _comfy_path(uploaded: dict) -> str:
        name = uploaded["name"]
        subfolder = uploaded.get("subfolder", "")
        return f"{subfolder}/{name}" if subfolder else name


# 方法说明：根据配置创建对应的推理后端。
def create_backend(name: str, **options) -> InferenceBackend:
    upscaler = RealCuganUpscaler(
        enabled=bool(options.pop("realcugan_enabled", False)),
        resource_root=Path(
            options.pop(
                "realcugan_resource_root",
                Path(__file__).resolve().parents[2] / "resource" / "realcugan",
            )
        ),
        timeout_seconds=int(options.pop("realcugan_timeout_seconds", 180)),
    )
    if name == PassthroughBackend.name:
        backend: InferenceBackend = PassthroughBackend()
    elif name == ComfyUIBackend.name:
        backend = ComfyUIBackend(**options)
    else:
        raise ValueError(f"unsupported backend: {name}")
    return RoutedInferenceBackend(backend, upscaler)
