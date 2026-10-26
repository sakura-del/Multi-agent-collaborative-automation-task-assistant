"""`resolve_literature_source` 的引用库接线测试（引用库计划 §3.3）。

覆盖最关键的两条：
1. `reference_library=true` → 返回 `LibraryLiteratureSource`（不外呼）；
2. **缺 session 时抛异常，绝不静默回退到外部检索** —— 否则用户以为在用自己
   导入的 20 篇，实际去 arXiv 拉了 10 篇无关的，产出的综述引用了他从没读过的文献，
   而他自己不会察觉。这比任务直接失败更糟。
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.orchestration.services.literature_source import (
    ReferenceLibraryUnavailableError,
    resolve_literature_source,
)


def make_task(config: dict[str, Any], user_id: uuid.UUID | None = None) -> Any:
    return SimpleNamespace(
        config=config,
        user_id=user_id or uuid.UUID("55555555-5555-5555-5555-555555555555"),
    )


class FakeSession:
    """占位 session：`LibraryLiteratureSource` 构造时不碰 DB。"""


def test_reference_library_returns_library_source() -> None:
    from app.tools.literature.library_source import LibraryLiteratureSource

    task = make_task({"reference_library": True})
    source = resolve_literature_source(task, session=FakeSession())

    assert isinstance(source, LibraryLiteratureSource)


def test_reference_library_without_session_raises() -> None:
    """**绝不静默回退到外呼检索** —— 见模块 docstring。"""
    task = make_task({"reference_library": True})

    with pytest.raises(ReferenceLibraryUnavailableError):
        resolve_literature_source(task, session=None)


def test_reference_library_without_user_id_raises() -> None:
    task = SimpleNamespace(config={"reference_library": True}, user_id=None)

    with pytest.raises(ReferenceLibraryUnavailableError):
        resolve_literature_source(task, session=FakeSession())


def test_reference_library_takes_priority_over_offline() -> None:
    """两者同时为真 → 用引用库（用户显式选择优先），并记 warning。"""
    from app.tools.literature.library_source import LibraryLiteratureSource

    task = make_task({"reference_library": True, "offline_dataset": True})
    source = resolve_literature_source(task, session=FakeSession())

    assert isinstance(source, LibraryLiteratureSource)


def test_fallback_still_used_when_no_reference_library() -> None:
    """不用引用库的调用方完全不受影响（向后兼容）。"""
    sentinel = object()
    task = make_task({})

    assert resolve_literature_source(task, fallback=sentinel) is sentinel


def test_offline_dataset_still_works(tmp_path: Any) -> None:
    """离线清单路径未被引用库改动影响。

    清单必须含至少一条真实记录（`_raw_records` 拒绝空列表），
    所以这里造一条最小合法记录 —— 目的只是验证**分派逻辑**没被改坏。
    """
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "source_id": "2401.00001",
                        "title": "Offline fixture",
                        "published_at": "2024-01-01T00:00:00Z",
                        "source_url": "https://arxiv.org/abs/2401.00001",
                        "pdf_url": "https://arxiv.org/pdf/2401.00001",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    task = make_task({"offline_dataset": True})

    source = resolve_literature_source(task, manifest_path=manifest)

    assert source is not None
    assert type(source).__name__ == "ManifestLiteratureSource"
