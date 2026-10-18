"""文献源解析：离线演示任务用冻结清单，其余走 arXiv 适配器。

背景（Plan 4 Task 8 实跑发现）：API 容器此前既没接知识库检索、也没有文献源，
导致界面上创建的文献任务走的是通用执行路径（无证据、无引用）。这里把选择逻辑
收敛成一个可测的纯函数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.tools.literature.models import LiteratureRecord, LiteratureSearch

logger = get_logger(__name__)

# 仓库/镜像内的冻结语料清单（backend/demo_data/... 与容器 /app/demo_data/...）
MANIFEST_PATH = (
    Path(__file__).resolve().parents[3] / "demo_data" / "literature_review" / "manifest.json"
)


class ArxivLiteratureSource:
    """把 ArxivClient 适配成工作流需要的检索源（惰性建连、复用一个客户端）。"""

    def __init__(self) -> None:
        self._client: Any | None = None

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        if self._client is None:
            from app.tools.literature.arxiv import ArxivClient

            self._client = ArxivClient()
        return await self._client.search(search)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def resolve_literature_source(
    task: Any,
    *,
    fallback: Any | None = None,
    manifest_path: Path | None = None,
) -> Any | None:
    """按任务配置选择文献源。

    - `offline_dataset=True` 且冻结清单存在 → `ManifestLiteratureSource`（完全不访问网络）
    - 否则使用容器注入的源（测试/CLI 可覆盖），缺失时回退到 arXiv 适配器
    """
    config = getattr(task, "config", None) or {}
    path = manifest_path or MANIFEST_PATH
    if config.get("offline_dataset") and path.is_file():
        from app.tools.literature.offline import ManifestLiteratureSource

        logger.info("literature_source_offline", manifest=str(path))
        return ManifestLiteratureSource(path)
    if config.get("offline_dataset"):
        logger.warning("literature_source_offline_manifest_missing", manifest=str(path))
    return fallback if fallback is not None else ArxivLiteratureSource()
