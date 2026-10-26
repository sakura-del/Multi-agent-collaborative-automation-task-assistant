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

__all__ = [
    "ArxivLiteratureSource",
    "ReferenceLibraryUnavailableError",
    "is_offline_literature_task",
    "resolve_literature_source",
]


class ReferenceLibraryUnavailableError(RuntimeError):
    """任务要求用引用库作文献源，但缺少 DB 会话或用户标识。

    **故意不是 `HumanActionRequiredError`**：这是调用方接线错误（配置或注入缺失），
    不是需要人工介入的业务状态。抛普通异常能让问题在开发/测试阶段就暴露，
    而不是变成一个「任务卡住等人工」的线上事故。
    """


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
    session: Any | None = None,
) -> Any | None:
    """按任务配置选择文献源。

    优先级（高 → 低）：

    1. `config["reference_library"] = True` → 用户自己的引用库（**不外呼**）；
    2. `config["offline_dataset"] = True` 且冻结清单存在 → `ManifestLiteratureSource`；
    3. 容器注入的 `fallback`；
    4. 多源检索（默认 arXiv + OpenAlex，可用 `config["literature_sources"]` 覆盖）。

    ## 为什么引用库排第一

    用户显式选了「用我的引用库」，就该用他的库。离线清单是演示兜底；
    两者同时为真说明调用方传错了，此时「用户显式选择」优先，
    但要记 warning 便于发现前端 bug。

    ## 为什么引用库**绝不**回退到外呼检索

    用户以为自己用的是自己那 20 篇，实际去 arXiv 拉了 10 篇无关的 ——
    这比任务直接失败更糟：产出的综述引用了一批用户从没读过的文献，
    而用户不会察觉。所以 `session` 缺失时**抛明确异常**。

    ## `session` 为什么是可选参数

    本函数原本是纯函数（只读 `task.config`），被测试与 CLI 大量调用。
    加可选参数保持向后兼容：不用引用库的调用方完全不受影响。
    """
    config = getattr(task, "config", None) or {}

    # ===== 1. 引用库（用户显式选择）=====
    if config.get("reference_library"):
        if config.get("offline_dataset"):
            logger.warning(
                "literature_source_conflicting_config",
                detail="reference_library 与 offline_dataset 同时为真，按引用库处理",
            )
        user_id = getattr(task, "user_id", None)
        if session is None or user_id is None:
            raise ReferenceLibraryUnavailableError(
                "任务要求使用引用库作为文献源，但缺少数据库会话或用户标识"
                "（拒绝静默回退到外部检索：那会让用户以为在用自己导入的文献）"
            )
        from app.tools.literature.library_source import LibraryLiteratureSource

        logger.info("literature_source_reference_library", user_id=str(user_id))
        return LibraryLiteratureSource(user_id, session)

    # ===== 2. 离线冻结清单 =====
    path = manifest_path or MANIFEST_PATH
    if config.get("offline_dataset") and path.is_file():
        from app.tools.literature.offline import ManifestLiteratureSource

        logger.info("literature_source_offline", manifest=str(path))
        return ManifestLiteratureSource(path)
    if config.get("offline_dataset"):
        logger.warning("literature_source_offline_manifest_missing", manifest=str(path))

    # ===== 3. 注入的 fallback =====
    if fallback is not None:
        return fallback

    # ===== 4. 多源检索 =====
    # Phase 1 P1-B：任务 config 可指定 `literature_sources`（如 ["openalex","pubmed"]）；
    # 未指定时用注册表默认（arXiv + OpenAlex），失败则退回单 arXiv 源，保证任务可跑。
    from app.tools.literature.source_registry import (
        DEFAULT_SOURCE_NAMES,
        build_multi_source,
    )

    names = config.get("literature_sources")
    if not isinstance(names, list) or not names:
        names = list(DEFAULT_SOURCE_NAMES)
    try:
        logger.info("literature_source_multi", sources=names)
        return build_multi_source(names)
    except Exception as exc:
        logger.warning("literature_source_multi_failed", sources=names, error=str(exc))
        return ArxivLiteratureSource()


def is_offline_literature_task(task: Any, *, manifest_path: Path | None = None) -> bool:
    """任务是否走离线冻结清单（与 `resolve_literature_source` 同一条件）。

    Issue #3 R6 接线用此判定「离线零外呼」：离线演示使用已人工核验的冻结语料，
    默认不做外部引用核验；清单缺失时会回退 arXiv 源，因此这里也必须返回 False，
    否则会漏掉本该执行的核验。
    """
    config = getattr(task, "config", None)
    if not isinstance(config, dict) or not config.get("offline_dataset"):
        return False
    path = manifest_path or MANIFEST_PATH
    return path.is_file()
