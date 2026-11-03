"""向量后端实现包。

对外只暴露协议与异常；具体后端按需导入，避免未使用到的驱动成为硬依赖
（例如只跑 Chroma 的测试环境不需要安装 psycopg）。
"""

from __future__ import annotations

from app.knowledge.services.vector_backends.base import (
    VectorBackend,
    VectorDimensionMismatchError,
    VectorMatch,
    VectorStoreConfigError,
    VectorStoreError,
    VectorStoreUnavailableError,
)
from app.knowledge.services.vector_backends.chroma_backend import ChromaVectorBackend

__all__ = [
    "ChromaVectorBackend",
    "VectorBackend",
    "VectorDimensionMismatchError",
    "VectorMatch",
    "VectorStoreConfigError",
    "VectorStoreError",
    "VectorStoreUnavailableError",
]
