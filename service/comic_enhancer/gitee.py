"""兼容 Gitee 适配器分发旧导入路径。"""

from .adapters.gitee import GiteeAdapterStore, GiteeError


__all__ = ["GiteeAdapterStore", "GiteeError"]
