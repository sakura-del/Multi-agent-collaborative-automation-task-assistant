"""向量嵌入服务：将文本转为向量供向量检索使用。

双模式设计：
- 真实模式：基于 sentence-transformers 加载 BGE-large-zh 模型生成高质量嵌入
- Mock 模式：基于哈希生成确定性伪向量，供 CI 与单元测试使用（无需下载模型）

选择逻辑：
- 环境变量 RAG_EMBEDDING_MOCK=1 或模型文件不存在时自动降级为 Mock
- 测试代码可直接构造 MockEmbeddingService 注入

设计要点：
- 延迟加载：模型在首次 embed 调用时才加载，避免启动阻塞（内存优化）
- 批量嵌入：支持一次传入多条文本，减少模型调用开销
- 维度对齐：Mock 向量维度与真实模型一致（1024），保证下游接口兼容
"""

from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.knowledge.services.vector_backends.base import VectorStoreConfigError

logger = get_logger(__name__)

# BGE-large-zh 的嵌入维度
_EMBEDDING_DIM = 1024


class BaseEmbeddingService(ABC):
    """嵌入服务抽象基类。"""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """嵌入向量维度。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成嵌入向量。"""

    def embed_one(self, text: str) -> list[float]:
        """单条文本嵌入（便捷方法）。"""
        return self.embed([text])[0]


class MockEmbeddingService(BaseEmbeddingService):
    """Mock 嵌入服务：基于哈希生成确定性伪向量。

    供 CI 与单元测试使用，无需下载模型文件。
    相同文本始终生成相同向量，保证检索可复现。
    """

    def __init__(self, dimension: int = _EMBEDDING_DIM) -> None:
        self._dim = dimension

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            # 用 MD5 哈希生成确定性种子，再扩展到目标维度
            digest = hashlib.md5(text.encode("utf-8")).digest()
            # 重复种子填充到目标维度
            vec = []
            for i in range(self._dim):
                byte_val = digest[i % len(digest)]
                # 归一化到 [-1, 1] 区间
                vec.append((byte_val / 127.5) - 1.0)
            vectors.append(vec)
        return vectors


class SentenceTransformerEmbeddingService(BaseEmbeddingService):
    """真实嵌入服务：基于 sentence-transformers 加载 BGE 模型。

    模型在首次 embed 调用时延迟加载，避免启动阻塞。
    """

    def __init__(self, model_name: str | None = None) -> None:
        rag = get_settings().rag
        self._model_name = model_name or rag.embedding_model
        self._model: Any = None  # 延迟加载

    @property
    def dimension(self) -> int:
        return _EMBEDDING_DIM

    def _ensure_model(self) -> None:
        """延迟加载模型，仅首次调用时初始化。"""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("loading_embedding_model", model=self._model_name)
            self._model = SentenceTransformer(self._model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._ensure_model()
        # normalize_embeddings=True 使向量归一化，便于余弦相似度计算
        embeddings = self._model.encode(texts, normalize_embeddings=True)
        return [emb.tolist() for emb in embeddings]


def create_embedding_service(
    force_mock: bool | None = None,
) -> BaseEmbeddingService:
    """工厂函数：按环境自动选择真实或 Mock 嵌入服务。

    选择优先级：
    1. force_mock 参数显式指定
    2. 环境变量 RAG_EMBEDDING_MOCK=1
    3. 默认使用真实服务（首次调用时加载模型）
    """
    if force_mock is None:
        force_mock = os.getenv("RAG_EMBEDDING_MOCK", "0") == "1"

    if force_mock:
        logger.info("using_mock_embedding")
        return MockEmbeddingService()

    provider = get_settings().rag.embedding_provider.strip().lower()
    if provider == "siliconflow":
        # 托管 API 的鉴权/配置错误不得落入本地 Mock 兜底，否则生产会静默换语义空间
        from app.knowledge.services.siliconflow_embedding import SiliconFlowEmbeddingService

        return SiliconFlowEmbeddingService()
    if provider != "local":
        raise VectorStoreConfigError(f"未知嵌入 provider：{provider}")

    try:
        return SentenceTransformerEmbeddingService()
    except Exception as exc:
        # 模型加载失败时降级为 Mock，保证服务可用
        logger.warning("embedding_model_unavailable_fallback_mock", error=str(exc))
        return MockEmbeddingService()
