from __future__ import annotations

import asyncio
import logging
import time

from ..character_library import CharacterReferenceAsset
from ..domain import ProcessingMode, ProcessOptions, ProcessResult, WorkIdentity
from ..logging_utils import log_operation


logger = logging.getLogger(__name__)

REFERENCE_MODES = {
    ProcessingMode.FLUX2,
    ProcessingMode.FLUX2_QUANT,
    ProcessingMode.FLUX2_CHARACTER,
    ProcessingMode.FLUX2_CHARACTER_LINEART,
    ProcessingMode.FLUX2_9B_LORA,
    ProcessingMode.FLUX2_9B_FAST,
    ProcessingMode.FLUX2_9B_FAST_LOWRES,
    ProcessingMode.FLUX2_4B_SOURCE,
    ProcessingMode.FLUX2_4B_COLOR,
}
OPTIONAL_CHARACTER_REFERENCE_MODES = {
    ProcessingMode.FLUX2_CHARACTER,
    ProcessingMode.FLUX2_CHARACTER_LINEART,
}


# 方法说明：为同步页面和后台预生成统一准备角色参考图并执行推理。
async def process_page_with_references(
    *,
    processor,
    metadata,
    reference_bank,
    settings,
    image_bytes: bytes,
    work: WorkIdentity,
    options: ProcessOptions,
    priority: int = 0,
) -> ProcessResult:
    character_references: dict[str, bytes] = {}
    character_reference_assets: list[CharacterReferenceAsset] = []
    if options.mode in REFERENCE_MODES:
        reference_started = time.perf_counter()
        reference_limit = settings.flux2_reference_limit
        metadata_candidates = 0
        entries = []
        reference_fallback = False
        try:
            resolution = await asyncio.to_thread(metadata.resolve, work)
            entries = await reference_bank.build(resolution, work)
            metadata_candidates = len(resolution.candidates)
        except Exception as error:
            optional_references = options.mode in OPTIONAL_CHARACTER_REFERENCE_MODES
            log_operation(
                logger,
                logging.WARNING if optional_references else logging.ERROR,
                feature="页面角色参考图准备",
                parameters={
                    "work_key": work.key,
                    "mode": str(options.mode),
                    "reference_limit": reference_limit,
                },
                result={
                    "status": "fallback" if optional_references else "failed",
                    "fallback": "no_reference" if optional_references else "",
                    "error": type(error).__name__,
                },
                elapsed_ms=(time.perf_counter() - reference_started) * 1000,
            )
            if optional_references:
                entries = []
                reference_fallback = True
            else:
                raise
        for entry, reference in entries[:reference_limit]:
            if entry.character_id in character_references:
                continue
            character_references[entry.character_id] = reference
            character_reference_assets.append(
                CharacterReferenceAsset(
                    character_id=entry.character_id,
                    display_name=entry.name,
                    image_bytes=reference,
                    provider=entry.provider,
                    summary=entry.summary,
                )
            )
        log_operation(
            logger,
            logging.INFO,
            feature="页面角色参考图准备",
            parameters={
                "work_key": work.key,
                "mode": str(options.mode),
                "reference_limit": reference_limit,
            },
            result={
                "status": "fallback" if reference_fallback else "success",
                "metadata_candidates": metadata_candidates,
                "bank_entries": len(entries),
                "selected_characters": len(character_reference_assets),
                "fallback": (
                    "no_reference"
                    if options.mode in OPTIONAL_CHARACTER_REFERENCE_MODES
                    and not character_reference_assets
                    else ""
                ),
            },
            elapsed_ms=(time.perf_counter() - reference_started) * 1000,
        )
    return await processor.process(
        image_bytes,
        None,
        work,
        options,
        character_references=character_references,
        character_reference_assets=tuple(character_reference_assets),
        priority=priority,
    )
