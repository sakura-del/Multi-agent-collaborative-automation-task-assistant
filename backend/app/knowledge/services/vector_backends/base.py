"""向量后端协议与异常类型。

设计目的：把「向量库到底存在哪里」与「上层怎么用」解耦。
`VectorStore` 是对外门面（同步接口、得分语义固定），具体实现由本协议的后端类提供：

- `ChromaVectorBackend`：历史实现（HTTP / 本地持久化 / 内存三种模式）；
- `PgVectorBackend`：Supabase PostgreSQL + pgvector。

后端只负责「按 id 写、按距离查、按条件删」，**不负责**生成嵌入、
不负责把距离换算成得分——换算在门面里统一做，保证换后端时
`score = 1 - cosine_distance` 的语义只有一个实现点。

异常分层（供 API 层区分 500 与 503）：
- `VectorStoreConfigError`：配置错（缺 DSN、维度不匹配）→ 部署问题，启动期就该暴露；
- `VectorStoreUnavailableError`：依赖不可达（连接失败 / 超时 / 权限）→ 应映射 503；
- `VectorStoreError`：其余操作失败（SQL 错误等）→ 500。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class VectorStoreError(RuntimeError):
    """向量存储操作失败的基类。"""


class VectorStoreConfigError(VectorStoreError):
    """向量存储配置缺失或非法（DSN 为空、表名非法、维度不匹配）。"""


class VectorStoreUnavailableError(VectorStoreError):
    """向量存储不可达：连接失败、超时、鉴权或权限不足。

    与配置错误的区别：配置错误改代码/配置才能好，不可达是**运行时**状态，
    HTTP 层应映射 503（依赖不可用）而不是 500（服务端缺陷）。
    """


class VectorDimensionMismatchError(VectorStoreConfigError):
    """嵌入向量维度与目标表列维度不一致。

    典型场景：表按 1024 维（bge-large-zh）建好，却用 64 维的 Mock 嵌入写入。
    必须显式报错：pgvector 的 `vector(n)` 会拒绝写入，但报错信息埋在 SQL 层，
    不指出「谁和谁不一致」会很难排查。
    """


@dataclass(frozen=True)
class VectorMatch:
    """后端返回的一条命中（尚未换算成得分）。"""

    chunk_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    # 余弦距离（0=完全相同，2=完全相反）。得分换算由门面统一负责。
    distance: float = 0.0


@runtime_checkable
class VectorBackend(Protocol):
    """向量后端协议：门面只依赖这里定义的存储方法。"""

    name: str

    def count(self) -> int:
        """当前 collection 的向量条数。"""
        ...

    def list_ids(self) -> list[str]:
        """当前 collection 的全部向量 id（用于一致性校验，结果稳定排序）。"""
        ...

    def upsert(
        self,
        ids: list[str],
        vectors: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """按 id 幂等写入：已存在则覆盖向量与正文。"""
        ...

    def delete_by_doc(self, doc_id: str) -> int:
        """删除某文档的全部向量，返回实际删除条数。"""
        ...

    def delete_by_ids(self, ids: list[str]) -> int:
        """按 chunk ID 精确删除，返回实际删除条数。"""
        ...

    def reset(self) -> None:
        """清空当前 collection（等价于删除后重建）。"""
        ...

    def query(
        self,
        vector: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[VectorMatch]:
        """按余弦距离升序返回 top_k 条。"""
        ...
