"""导出服务端稳定的领域模型。"""

from .adapters import AdapterManifest, AdapterSource, ResolvedAdapter
from .identity import (
    ChapterIdentity,
    CharacterBankEntry,
    CharacterReference,
    WorkIdentity,
)
from .metadata import MetadataResolution, WorkMetadata
from .processing import (
    Capabilities,
    ProcessingMode,
    ProcessingModeOption,
    ProcessOptions,
    ProcessResult,
)

__all__ = [
    "AdapterManifest",
    "AdapterSource",
    "Capabilities",
    "ChapterIdentity",
    "CharacterBankEntry",
    "CharacterReference",
    "MetadataResolution",
    "ProcessingMode",
    "ProcessingModeOption",
    "ProcessOptions",
    "ProcessResult",
    "ResolvedAdapter",
    "WorkIdentity",
    "WorkMetadata",
]
