from __future__ import annotations

from dataclasses import dataclass

from ..application import (
    PregenerationService,
    ProcessingService,
    ReferenceBankService,
)
from ..config import Settings
from ..identities import WorkIdentityRegistry
from ..inference import InferenceBackend
from ..metadata import MetadataAggregator
from ..references import ReferenceImageStore
from ..storage import PregenerationStore, ResultCache


@dataclass(frozen=True)
class ApplicationContext:
    settings: Settings
    backend: InferenceBackend
    cache: ResultCache
    references: ReferenceImageStore
    metadata: MetadataAggregator
    identities: WorkIdentityRegistry
    processor: ProcessingService
    reference_bank: ReferenceBankService
    pregeneration_store: PregenerationStore
    pregeneration: PregenerationService
