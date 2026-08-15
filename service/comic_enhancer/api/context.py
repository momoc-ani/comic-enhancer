from __future__ import annotations

from dataclasses import dataclass

from ..adapters import AdapterRegistry, GiteeAdapterStore
from ..application import (
    ProcessingService,
    ReferenceBankService,
    RemoteAdapterService,
)
from ..config import Settings
from ..identities import WorkIdentityRegistry
from ..inference import InferenceBackend
from ..metadata import MetadataAggregator
from ..references import ReferenceImageStore
from ..storage import ResultCache


@dataclass(frozen=True)
class ApplicationContext:
    settings: Settings
    backend: InferenceBackend
    registry: AdapterRegistry
    cache: ResultCache
    references: ReferenceImageStore
    metadata: MetadataAggregator
    identities: WorkIdentityRegistry
    processor: ProcessingService
    reference_bank: ReferenceBankService
    gitee_store: GiteeAdapterStore | None
    remote_adapters: RemoteAdapterService | None
