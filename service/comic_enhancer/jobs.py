"""兼容旧任务编排导入路径，业务实现位于 application 包。"""

from .application.processing import ProcessingService

__all__ = ["ProcessingService"]
