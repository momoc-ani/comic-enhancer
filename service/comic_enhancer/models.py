"""兼容旧领域模型导入路径，业务实现位于 domain 包。"""

from .domain import (
    Capabilities,
    ChapterIdentity,
    CharacterBankEntry,
    CharacterReference,
    MetadataResolution,
    ProcessingMode,
    ProcessingModeOption,
    ProcessOptions,
    ProcessResult,
    WorkIdentity,
    WorkMetadata,
)

__all__ = [
    "Capabilities",
    "ChapterIdentity",
    "CharacterBankEntry",
    "CharacterReference",
    "MetadataResolution",
    "ProcessingMode",
    "ProcessingModeOption",
    "ProcessOptions",
    "ProcessResult",
    "WorkIdentity",
    "WorkMetadata",
]
