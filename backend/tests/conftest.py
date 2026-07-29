"""pytest 公共夹具。

在导入应用之前设置测试环境变量，让测试无需依赖外部 .env 文件即可运行。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 把 backend/ 加入 sys.path，便于 `from app...` 导入
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# ===== 测试环境变量（必须在 import app.* 之前设置）=====
# 使用 SQLite 内存库，避免依赖真实 PostgreSQL
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_DEBUG", "false")
os.environ.setdefault("APP_LOG_FORMAT", "console")
# pydantic-settings 嵌套分隔符为 __ ，故 DATABASE__URL 映射到 settings.database.url
os.environ.setdefault("DATABASE__URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS__URL", "redis://localhost:6379/0")
# JWT 测试密钥
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production")


import pytest  # noqa: E402


@pytest.fixture(scope="session")
def app_settings():
    """返回全局配置单例。"""
    from app.core.config import get_settings

    return get_settings()
