"""文献源解析：引用库 / 离线清单 / 多源检索（arXiv + OpenAlex + 万方…）。

背景（Plan 4 Task 8 实跑发现）：API 容器此前既没接知识库检索、也没有文献源，
导致界面上创建的文献任务走的是通用执行路径（无证据、无引用）。这里把选择逻辑
收敛成一个可测的纯函数。

## 万方 Key 为什么是参数而不是环境变量或 task.config（Issue #67 BYOK）

- 平台不持有万方 Key：每个用户可以在设置页填自己的万方 App Key；
- **不能写进 task.config**：config 会随 `request.model_dump()` 进入任务表并出现在
  前端快照里，明文 Key 落库就是事故；
- 因此由 `TaskExecution` 在任务启动时解密用户凭据，作为关键字参数传进来，
  用完全程只在内存里存在。
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

#: 中文任务 + 用户已配置万方 Key 时的默认源组合（中文文献优先）
WANFANG_CHINESE_SOURCES: tuple[str, ...] = ("wanfang", "openalex")

#: 万方的源别名（与 source_registry 的 SOURCE_ALIASES 对齐，用于「无 Key 剔除」）
_WANFANG_ALIASES: frozenset[str] = frozenset({"wanfang", "wf", "wfdata"})


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
    wfdata_app_key: str = "",
) -> Any | None:
    """按任务配置选择文献源。

    优先级（高 → 低）：

    1. `config["reference_library"] = True` → 用户自己的引用库（**不外呼**）；
    2. `config["offline_dataset"] = True` 且冻结清单存在 → `ManifestLiteratureSource`；
    3. 容器注入的 `fallback`；
    4. 多源检索（默认 arXiv + OpenAlex，可用 `config["literature_sources"]` 覆盖；
       Issue #67：中文任务且**用户自己配置了万方 Key**时默认「万方 + OpenAlex」）。

    `wfdata_app_key` 是用户自带万方 Key（已解密）。为空表示用户没配置：
    显式选了万方也会被剔除，避免任务一开始就因鉴权 401 而降级。
    """
    config = getattr(task, "config", None) or {}
    key = (wfdata_app_key or "").strip()

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
    # Issue #67：中文任务 + 用户有万方 Key 时，默认改为「万方 + OpenAlex」。
    from app.tools.literature.source_registry import DEFAULT_SOURCE_NAMES, build_multi_source

    names = config.get("literature_sources")
    if isinstance(names, list) and names:
        normalized = [str(name) for name in names]
        names = _strip_wanfang_without_key(normalized, key)
        if not names:
            # 显式只选了万方但没配 Key：退回默认免费源，任务不能因此失败
            logger.warning(
                "literature_sources_all_unavailable",
                requested=normalized,
                fallback=list(DEFAULT_SOURCE_NAMES),
            )
            names = list(DEFAULT_SOURCE_NAMES)
    else:
        names = _default_source_names(config, wfdata_app_key=key)

    try:
        logger.info("literature_source_multi", sources=names)
        return build_multi_source(names, wfdata_app_key=key)
    except Exception as exc:
        logger.warning("literature_source_multi_failed", sources=names, error=str(exc))
        return ArxivLiteratureSource()


def _strip_wanfang_without_key(names: list[str], wfdata_app_key: str) -> list[str]:
    """用户没配万方 Key 时把万方（含别名）从显式源列表里剔除，保持其余源顺序。"""
    if wfdata_app_key:
        return names
    filtered: list[str] = []
    stripped = False
    for name in names:
        if name.strip().lower() in _WANFANG_ALIASES:
            stripped = True
            continue
        filtered.append(name)
    if stripped:
        logger.warning(
            "literature_source_wanfang_skipped",
            detail="未配置万方 Key，已从检索源中剔除万方（可在设置页填写自己的万方 App Key）",
        )
    return filtered


def _default_source_names(config: dict[str, Any], *, wfdata_app_key: str) -> list[str]:
    """未显式指定源时的默认组合（Issue #67：中文综述优先万方）。

    - 中文任务 + 用户已配置万方 Key → 万方 + OpenAlex（中英兼顾）；
    - 其余情况 → 注册表默认（arXiv + OpenAlex），行为与接入万方前一致。
    """
    from app.tools.literature.source_registry import DEFAULT_SOURCE_NAMES

    if wfdata_app_key and config.get("output_language") == "zh":
        return list(WANFANG_CHINESE_SOURCES)
    return list(DEFAULT_SOURCE_NAMES)


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
