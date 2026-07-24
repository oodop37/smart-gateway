"""
routes/__init__.py - 路由模块统一导出
"""
from .providers import router as providers_router
from .models import router as models_router
from .stats import router as stats_router
from .config import router as config_router

__all__ = ["providers_router", "models_router", "stats_router", "config_router"]