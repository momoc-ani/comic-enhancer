from __future__ import annotations

from io import BytesIO
import logging
from pathlib import Path
from time import perf_counter
import uuid

from PIL import Image, ImageOps

from ....domain import ProcessOptions
from ....logging_utils import exception_log_fields, log_operation
from ...contracts import InferenceAssets, InferenceOutcome
from ..image_ops import restore_geometry, save_output
from ..workflow_processing import (
    WorkflowImagePreparation,
    WorkflowImageProcessor,
)
from .base import (
    ComfyUIModeStrategy,
    reference_cache_revision,
    select_reference_images,
)


logger = logging.getLogger(__name__)


FLUX2_PROCESSING_REVISION = "flux2-baseline-direct-prompt-v12"
FLUX2_SOURCE_SIZE_OUTPUT_REVISION = "flux2-source-size-workflow-output-v1"
FLUX2_OUTPUT_SCALE = 2


class Flux2StrategyBase(ComfyUIModeStrategy):
    """复用两个 FLUX.2 档位共有的参考图执行过程。"""

    output_prefix = "flux2"

    # 方法说明：初始化 FLUX.2 开关、工作流和参考图限制。
    def __init__(
        self,
        *,
        enabled: bool,
        workflow_path: Path | None,
        reference_limit: int,
        image_processor: WorkflowImageProcessor | None = None,
        **options,
    ):
        super().__init__(**options)
        self.enabled = enabled
        self.workflow_path = workflow_path
        self.reference_limit = max(1, min(3, reference_limit))
        self.image_processor = image_processor

    # 方法说明：将可插拔图像加工策略版本加入当前档位缓存标识。
    def _with_image_processing_revision(self, revision: str) -> str:
        if self.image_processor is None:
            return revision
        return f"{revision}:{self.image_processor.cache_revision}"

    # 方法说明：生成包含工作流、参考图和 FLUX.2 处理版本的缓存标识。
    def _flux2_cache_revision(
        self,
        options: ProcessOptions,
        assets: InferenceAssets | None,
        *,
        quantized: bool,
    ) -> str:
        revision = reference_cache_revision(
            self.workflow_loader,
            options,
            assets,
        )
        suffix = (
            f"{FLUX2_PROCESSING_REVISION}:quant"
            if quantized
            else (
                f"{FLUX2_PROCESSING_REVISION}:"
                f"{FLUX2_SOURCE_SIZE_OUTPUT_REVISION}"
            )
        )
        return self._with_image_processing_revision(f"{revision}:{suffix}")

    # 方法说明：安全执行工作流前图像分析，失败时禁用本页后处理。
    def _prepare_image_processing(
        self,
        assets: InferenceAssets,
        options: ProcessOptions,
    ) -> tuple[WorkflowImagePreparation, float] | None:
        if self.image_processor is None:
            return None
        started = perf_counter()
        try:
            preparation = self.image_processor.prepare(assets.image_bytes)
        except Exception as error:
            log_operation(
                logger,
                logging.WARNING,
                feature="FLUX.2工作流图像加工",
                parameters={
                    "mode": str(options.mode),
                    "processor": self.image_processor.name,
                    "phase": "before_workflow",
                },
                result={
                    "status": "fallback_generated",
                    **exception_log_fields(error),
                },
                elapsed_ms=(perf_counter() - started) * 1000,
            )
            return None
        return preparation, (perf_counter() - started) * 1000

    # 方法说明：安全执行工作流后图像加工，失败时保留原生成图。
    def _apply_image_processing(
        self,
        assets: InferenceAssets,
        generated: Image.Image,
        options: ProcessOptions,
        prepared: tuple[WorkflowImagePreparation, float] | None,
    ) -> Image.Image:
        if self.image_processor is None or prepared is None:
            return generated
        preparation, preparation_ms = prepared
        started = perf_counter()
        try:
            outcome = self.image_processor.process(
                assets.image_bytes,
                generated,
                preparation,
            )
        except Exception as error:
            log_operation(
                logger,
                logging.WARNING,
                feature="FLUX.2工作流图像加工",
                parameters={
                    "mode": str(options.mode),
                    "processor": self.image_processor.name,
                    "phase": "after_workflow",
                },
                result={
                    "status": "fallback_generated",
                    **preparation.metrics,
                    **exception_log_fields(error),
                },
                elapsed_ms=preparation_ms + (perf_counter() - started) * 1000,
            )
            return generated
        log_operation(
            logger,
            logging.INFO,
            feature="FLUX.2工作流图像加工",
            parameters={
                "mode": str(options.mode),
                "processor": self.image_processor.name,
            },
            result={
                "status": outcome.status,
                **preparation.metrics,
                **outcome.metrics,
            },
            elapsed_ms=preparation_ms + (perf_counter() - started) * 1000,
        )
        return outcome.image

    # 方法说明：执行当前 FLUX.2 工作流并按档位完成独立的输出尺寸处理。
    def _process_flux2(
        self,
        assets: InferenceAssets,
        output_path: Path,
        options: ProcessOptions,
        *,
        restore_source_output: bool = False,
    ) -> InferenceOutcome:
        if not self.available():
            raise RuntimeError("FLUX.2 服务未就绪")
        references = select_reference_images(assets, limit=self.reference_limit)
        if not references:
            raise RuntimeError("FLUX.2 需要至少一张角色参考图")
        if self.workflow_path is None:
            raise RuntimeError("FLUX.2 工作流未配置")
        prepared_image_processing = self._prepare_image_processing(assets, options)
        loaded_workflow = self.workflow_loader.load(options)
        workflow_revision = self.workflow_loader.revision(options)
        log_operation(
            logger,
            logging.INFO,
            feature="FLUX.2工作流加载",
            parameters={
                "mode": str(options.mode),
                "workflow": str(loaded_workflow.source),
                "model_profile": loaded_workflow.model_profile,
                "reference_limit": self.reference_limit,
                "restore_source_output": restore_source_output,
            },
            result={
                "status": "loaded",
                "workflow_revision": workflow_revision[:16],
                "reference_count": len(references),
                "input_bytes": len(assets.image_bytes),
            },
        )
        input_images = {
            "INPUT_IMAGE": assets.image_bytes,
            **{
                f"REFERENCE_IMAGE_{index}": references[
                    min(index - 1, len(references) - 1)
                ]
                for index in range(1, 4)
            },
        }
        generated = self.transport.run(
            loaded_workflow.prompt,
            input_images=input_images,
            output_prefix=(
                f"comic-enhancer/{self.output_prefix}-{uuid.uuid4().hex}"
            ),
            prepare_workflow=(
                _bind_source_geometry_output if restore_source_output else None
            ),
        )
        generated_size = generated.size
        if restore_source_output:
            source_size = _source_size(assets.image_bytes)
            if generated_size != source_size:
                raise RuntimeError(
                    "FLUX.2 工作流输出尺寸与原图不一致："
                    f"expected={source_size}, actual={generated_size}"
                )
            output_scale = 1
            geometry_handler = "comfyui-workflow"
        else:
            generated = restore_geometry(
                assets.image_bytes,
                generated,
                output_scale=FLUX2_OUTPUT_SCALE,
            )
            output_scale = FLUX2_OUTPUT_SCALE
            geometry_handler = "service-pillow"
        generated = self._apply_image_processing(
            assets,
            generated,
            options,
            prepared_image_processing,
        )
        save_output(generated, output_path)
        log_operation(
            logger,
            logging.INFO,
            feature="FLUX.2服务端几何恢复",
            parameters={
                "mode": str(options.mode),
                "workflow": str(loaded_workflow.source),
                "output_scale": output_scale,
            },
            result={
                "status": "success",
                "comfyui_size": list(generated_size),
                "saved_size": list(generated.size),
                "model_profile": loaded_workflow.model_profile,
                "geometry_handler": geometry_handler,
            },
        )
        return InferenceOutcome(
            reference_applied=True,
            model_profile=loaded_workflow.model_profile,
        )


# 方法说明：读取原图经过 EXIF 方向校正后的准确宽高。
def _source_size(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(image_bytes)) as source_file:
        source = ImageOps.exif_transpose(source_file)
        return source.size


# 方法说明：把最高质量工作流的最终输出绑定到原图宽高恢复节点。
def _bind_source_geometry_output(workflow: dict) -> None:
    source_id, _ = _unique_titled_node(workflow, "LoadImage", "INPUT_IMAGE")
    size_id, size_node = _unique_titled_node(
        workflow,
        "GetImageSize",
        "SOURCE_IMAGE_SIZE",
    )
    restore_id, restore_node = _unique_titled_node(
        workflow,
        "ImageScale",
        "RESTORE SOURCE GEOMETRY",
    )
    _, output_node = _unique_titled_node(workflow, "SaveImage", "OUTPUT_IMAGE")

    size_node.setdefault("inputs", {})["image"] = [source_id, 0]
    restore_inputs = restore_node.setdefault("inputs", {})
    restore_inputs["width"] = [size_id, 0]
    restore_inputs["height"] = [size_id, 1]
    restore_inputs["upscale_method"] = "lanczos"
    restore_inputs["crop"] = "disabled"
    output_node.setdefault("inputs", {})["images"] = [restore_id, 0]


# 方法说明：按节点类型和标题查找工作流中的唯一节点。
def _unique_titled_node(
    workflow: dict,
    class_type: str,
    title: str,
) -> tuple[str, dict]:
    expected_title = title.strip().upper()
    matches = [
        (str(node_id), node)
        for node_id, node in workflow.items()
        if isinstance(node, dict)
        and node.get("class_type") == class_type
        and str(node.get("_meta", {}).get("title", "")).strip().upper()
        == expected_title
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"FLUX.2 工作流缺少唯一节点：{class_type}/{title}"
        )
    return matches[0]
