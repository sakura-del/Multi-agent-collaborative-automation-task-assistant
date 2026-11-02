# PRISMA 内置主动筛选排序实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 PRISMA 筛选页加入默认关闭的主动学习优先级排序，用任务全员标签和可复现的 TF-IDF/逻辑回归模型给未筛记录置顶，保持人工决策与 PRISMA 计数不变。

**Architecture:** 新增纯 Python 排序模块负责文本分类与稳定排序；`ScreeningRepository` 一次查询任务内决策，`ScreeningService` 聚合标签并选候选；新增只读 API 与前端开关。无新表、无后台任务、不自动决策。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / scikit-learn / pytest / Vue 3 / TypeScript / Vitest。

---

## 执行约束

- 工作目录：`E:\Multi-agent collaborative automation task assistant\.agents\worktrees\codex\screening-priority`
- 后端命令在 `backend` 下执行；使用主 checkout 绝对解释器：
  `E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe`
- 前端 node_modules 不在 worktree；先建立 gitignored junction（见 Task 4 环境步骤）。
- 严格 TDD：先写失败测试、看它失败，再写最小实现。
- 每个 Task 单独提交，只精确 `git add` 本 Task 路径；禁止 `git add -A`。
- 不新增 Alembic 迁移，不改决策/PRISMA 语义，不自动纳入/排除，不显示概率。
- 真实 Zotero 与真实浏览器验收不属于本计划；本计划只做 PRISMA 排序功能与自动化测试。

---

### Task 1: 依赖与纯排序模块

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/knowledge/services/screening_priority.py`
- Create: `backend/tests/test_screening_priority.py`

- [ ] **Step 1: 安装并记录运行时依赖**

在 `backend/pyproject.toml` 的运行时依赖中加入：

```toml
    # 主动筛选排序（ASReview 式）：字符 n-gram TF-IDF + 逻辑回归。
    # scikit-learn 为 BSD-3-Clause；不引入 ASReview 的 Flask 应用栈。
    "scikit-learn>=1.5,<2",
```

安装到当前后端 venv（worktree 共用主 checkout 的 venv）：

```powershell
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m pip install "scikit-learn>=1.5,<2"
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -c "import sklearn; print(sklearn.__version__)"
```

Expected: 安装成功，打印 `1.x`。

- [ ] **Step 2: 写纯排序器失败测试**

创建 `backend/tests/test_screening_priority.py`：

```python
"""主动筛选纯排序器测试（不碰数据库/HTTP）。"""

from __future__ import annotations

from typing import Literal

from app.knowledge.services.screening_priority import (
    ALGORITHM_VERSION,
    LabeledText,
    PriorityCandidate,
    rank_candidates,
)


def _label(text: str, label: Literal["positive", "negative"]) -> LabeledText:
    return LabeledText(text=text, label=label)


def _candidate(record_id: str, text: str) -> PriorityCandidate:
    return PriorityCandidate(record_id=record_id, text=text)


def test_ranks_relevant_candidate_first() -> None:
    labeled = [
        _label("retrieval augmented generation evaluation evidence benchmark", "positive"),
        _label("clinical cancer trial patient treatment outcome", "negative"),
    ]
    candidates = [
        _candidate("negative", "clinical cancer patient trial outcome treatment"),
        _candidate("positive", "retrieval augmented generation benchmark evidence"),
    ]

    ranking = rank_candidates(labeled, candidates)

    assert ranking.status == "ready"
    assert ranking.algorithm == ALGORITHM_VERSION
    assert [item.record_id for item in ranking.items] == ["positive", "negative"]
    assert [item.rank for item in ranking.items] == [1, 2]
    assert ranking.positive == 1
    assert ranking.negative == 1


def test_chinese_text_is_rankable() -> None:
    labeled = [
        _label("检索增强生成的证据评估方法", "positive"),
        _label("临床肿瘤试验患者治疗结果", "negative"),
    ]
    candidates = [
        _candidate("negative", "临床肿瘤患者试验治疗结果"),
        _candidate("positive", "检索增强生成证据评估方法"),
    ]

    ranking = rank_candidates(labeled, candidates)

    assert ranking.status == "ready"
    assert ranking.items[0].record_id == "positive"


def test_same_input_is_deterministic() -> None:
    labeled = [
        _label("graph neural network message passing", "positive"),
        _label("marine biology coral reef ecology", "negative"),
    ]
    candidates = [
        _candidate("b", "coral reef marine biology"),
        _candidate("a", "graph neural network message"),
    ]

    first = rank_candidates(labeled, candidates)
    second = rank_candidates(labeled, candidates)

    assert first == second
    assert [item.rank for item in first.items] == [1, 2]


def test_ties_keep_input_order() -> None:
    labeled = [
        _label("active learning screening evidence", "positive"),
        _label("unrelated topic completely different", "negative"),
    ]
    candidates = [
        _candidate("first", "active learning screening evidence"),
        _candidate("second", "active learning screening evidence"),
    ]

    ranking = rank_candidates(labeled, candidates)

    assert [item.record_id for item in ranking.items] == ["first", "second"]


def test_insufficient_labels_returns_empty_ranking() -> None:
    ranking = rank_candidates(
        [_label("only positive", "positive")],
        [_candidate("c1", "only positive")],
    )

    assert ranking.status == "insufficient_labels"
    assert ranking.items == ()
    assert ranking.positive == 1
    assert ranking.negative == 0


def test_no_candidates_wins_over_insufficient_labels() -> None:
    ranking = rank_candidates([_label("only positive", "positive")], [])

    assert ranking.status == "no_candidates"
    assert ranking.items == ()
    assert ranking.candidates == 0


def test_empty_text_returns_no_candidates() -> None:
    ranking = rank_candidates(
        [_label("", "positive"), _label("", "negative")],
        [_candidate("c1", "")],
    )

    assert ranking.status == "no_candidates"
    assert ranking.items == ()
    assert "文本" in ranking.message


def test_ignored_count_is_preserved() -> None:
    ranking = rank_candidates(
        [
            _label("positive text about screening", "positive"),
            _label("negative text about other work", "negative"),
        ],
        [_candidate("c1", "positive text about screening")],
        ignored=2,
    )

    assert ranking.ignored == 2
    assert ranking.status == "ready"
```

- [ ] **Step 3: 运行测试确认失败**

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m pytest tests/test_screening_priority.py -q --no-cov
```

Expected: FAIL，`ModuleNotFoundError` 或找不到 `screening_priority`。

- [ ] **Step 4: 实现纯排序模块**

创建 `backend/app/knowledge/services/screening_priority.py`：

```python
"""PRISMA 主动筛选排序（纯函数模块）。

设计边界：
- 不导入 SQLAlchemy/FastAPI/数据库模型；
- 只负责二分类文本排序与稳定顺序；
- 不暴露概率，返回的 rank 是给前端展示的唯一分数形式；
- 同一输入 + 同一算法版本必须产生相同顺序。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

ALGORITHM_VERSION = "tfidf-char-logreg-v1"

PriorityStatus = Literal["ready", "insufficient_labels", "no_candidates"]


class PriorityUnavailableError(RuntimeError):
    """排序器/模型不可用；HTTP 层映射为 503，不伪装成正常空结果。"""


@dataclass(frozen=True)
class LabeledText:
    text: str
    label: Literal["positive", "negative"]


@dataclass(frozen=True)
class PriorityCandidate:
    record_id: str
    text: str


@dataclass(frozen=True)
class RankedCandidate:
    record_id: str
    rank: int


@dataclass(frozen=True)
class PriorityRanking:
    status: PriorityStatus
    items: tuple[RankedCandidate, ...]
    positive: int
    negative: int
    ignored: int
    candidates: int
    message: str
    algorithm: str = ALGORITHM_VERSION


def _vectorizer() -> TfidfVectorizer:
    """字符 n-gram 同时适配中英文；固定参数保证可复现。"""
    return TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(2, 4),
        max_features=30_000,
        min_df=1,
        sublinear_tf=True,
        lowercase=True,
    )


def _classifier() -> LogisticRegression:
    return LogisticRegression(
        C=1.0,
        class_weight="balanced",
        solver="liblinear",
        random_state=42,
        max_iter=1000,
    )


def _empty(status: PriorityStatus, *, positive: int, negative: int, ignored: int, candidates: int, message: str) -> PriorityRanking:
    return PriorityRanking(
        status=status,
        items=(),
        positive=positive,
        negative=negative,
        ignored=ignored,
        candidates=candidates,
        message=message,
    )


def rank_candidates(
    labeled: Sequence[LabeledText],
    candidates: Sequence[PriorityCandidate],
    *,
    ignored: int = 0,
) -> PriorityRanking:
    positive = sum(1 for item in labeled if item.label == "positive")
    negative = sum(1 for item in labeled if item.label == "negative")
    ignored = max(0, ignored)

    if not candidates:
        return _empty(
            "no_candidates",
            positive=positive,
            negative=negative,
            ignored=ignored,
            candidates=0,
            message="没有可排序的未筛记录",
        )

    if positive < 1 or negative < 1:
        return _empty(
            "insufficient_labels",
            positive=positive,
            negative=negative,
            ignored=ignored,
            candidates=len(candidates),
            message=f"至少需要 1 条纳入和 1 条排除决策（当前 {positive} / {negative}）",
        )

    usable = [item for item in labeled if item.text.strip()]
    positive_texts = [item.text for item in usable if item.label == "positive"]
    negative_texts = [item.text for item in usable if item.label == "negative"]
    if not positive_texts or not negative_texts:
        return _empty(
            "no_candidates",
            positive=positive,
            negative=negative,
            ignored=ignored,
            candidates=len(candidates),
            message="缺少可用文本，无法训练排序模型",
        )

    rankable = [item for item in candidates if item.text.strip()]
    if not rankable:
        return _empty(
            "no_candidates",
            positive=positive,
            negative=negative,
            ignored=ignored,
            candidates=len(candidates),
            message="候选记录缺少可用文本",
        )

    texts = positive_texts + negative_texts
    labels = [1] * len(positive_texts) + [0] * len(negative_texts)
    vectorizer = _vectorizer()
    matrix = vectorizer.fit_transform(texts)
    classifier = _classifier()
    classifier.fit(matrix, labels)

    scores = [float(value) for value in classifier.decision_function(vectorizer.transform([item.text for item in rankable]))]
    paired = list(zip(rankable, scores, strict=True))
    ordered = [candidate for candidate, _ in sorted(paired, key=lambda pair: -pair[1])]

    unrankable = [item for item in candidates if not item.text.strip()]
    ordered.extend(unrankable)
    items = tuple(
        RankedCandidate(record_id=item.record_id, rank=index)
        for index, item in enumerate(ordered, start=1)
    )
    return PriorityRanking(
        status="ready",
        items=items,
        positive=positive,
        negative=negative,
        ignored=ignored,
        candidates=len(candidates),
        message=f"基于任务内 {positive} 条纳入 / {negative} 条排除决策排序",
    )
```

- [ ] **Step 5: 运行纯排序器测试**

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m pytest tests/test_screening_priority.py -q --no-cov
```

Expected: 全部 PASS。

- [ ] **Step 6: 格式与类型检查**

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m black --check app/knowledge/services/screening_priority.py tests/test_screening_priority.py
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m ruff check app/knowledge/services/screening_priority.py tests/test_screening_priority.py
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m mypy app/knowledge/services/screening_priority.py
```

Expected: 全部 0 错误。若 Black 需要格式化，先运行 `black` 后再检查。

- [ ] **Step 7: 提交 Task 1**

```powershell
git add backend/pyproject.toml backend/app/knowledge/services/screening_priority.py backend/tests/test_screening_priority.py
git commit -m "feat: add deterministic active screening ranker"
```

---

### Task 2: 决策查询与候选聚合服务

**Files:**
- Modify: `backend/app/db/repositories/screening.py`
- Modify: `backend/app/knowledge/services/screening_service.py`
- Create: `backend/tests/test_screening_priority_service.py`

- [ ] **Step 1: 写 Repository/Service 失败测试**

创建 `backend/tests/test_screening_priority_service.py`：

```python
"""筛选优先级：Repository 查询与 Service 标签聚合测试。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.knowledge.services.screening_service as screening_service_module
from app.db.base import Base
from app.db.models.screening import DecisionStage, ScreeningVerdict
from app.db.repositories.screening import ScreeningRepository
from app.knowledge.services.screening_priority import PriorityUnavailableError
from app.knowledge.services.screening_service import ScreeningService
from app.tools.literature.models import LiteratureRecord


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def _record(source_id: str, title: str, abstract: str = "") -> LiteratureRecord:
    return LiteratureRecord(
        source_id=f"arxiv:{source_id}",
        title=title,
        authors=["A"],
        abstract=abstract,
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source_url=f"https://arxiv.org/abs/{source_id}",
        pdf_url=f"https://arxiv.org/abs/{source_id}.pdf",
        categories=["cs.CL"],
    )


async def _seed(session: AsyncSession, task_id: uuid.UUID) -> list:
    service = ScreeningService(session)
    await service.record_search(
        task_id,
        query="rag screening",
        sources=["arxiv"],
        records=[
            _record("pos", "retrieval augmented generation evaluation", "evidence benchmark"),
            _record("neg", "clinical cancer trial patient outcome", "medical treatment"),
            _record("maybe", "uncertain review", "needs manual judgment"),
            _record("c1", "retrieval augmented generation evidence", "benchmark evaluation"),
            _record("c2", "clinical trial patient treatment", "cancer outcome"),
        ],
        hits_by_source={"arxiv": 5},
    )
    return await service.list_records(task_id)


async def test_repository_lists_task_decisions_in_seq_order(session: AsyncSession) -> None:
    task_id = uuid.uuid4()
    records = await _seed(session, task_id)
    reviewer = uuid.uuid4()
    repo = ScreeningRepository(session)
    await repo.append_decision(records[0].id, stage=DecisionStage.TITLE_ABSTRACT, verdict=ScreeningVerdict.INCLUDE, reviewer_id=reviewer)
    await repo.append_decision(records[1].id, stage=DecisionStage.TITLE_ABSTRACT, verdict=ScreeningVerdict.EXCLUDE, reviewer_id=reviewer, exclusion_reason="not relevant")
    await session.commit()

    decisions = await repo.list_decisions_for_task(task_id, DecisionStage.TITLE_ABSTRACT)

    assert {row.record_id for row in decisions} == {records[0].id, records[1].id}
    assert all(row.stage == DecisionStage.TITLE_ABSTRACT.value for row in decisions)


async def test_service_aggregates_task_wide_labels_and_excludes_decided_records(session: AsyncSession) -> None:
    task_id = uuid.uuid4()
    records = await _seed(session, task_id)
    reviewer = uuid.uuid4()
    service = ScreeningService(session)
    await service.decide(records[0].id, stage=DecisionStage.TITLE_ABSTRACT, verdict=ScreeningVerdict.INCLUDE, reviewer_id=reviewer)
    await service.decide(records[1].id, stage=DecisionStage.TITLE_ABSTRACT, verdict=ScreeningVerdict.EXCLUDE, reviewer_id=reviewer, exclusion_reason="not relevant")
    await service.decide(records[2].id, stage=DecisionStage.TITLE_ABSTRACT, verdict=ScreeningVerdict.MAYBE, reviewer_id=reviewer)

    ranking = await service.priority(task_id)

    assert ranking.status == "ready"
    assert ranking.positive == 1
    assert ranking.negative == 1
    assert ranking.ignored == 1
    assert ranking.candidates == 2
    assert {item.record_id for item in ranking.items} == {str(records[3].id), str(records[4].id)}


async def test_priority_is_task_scoped(session: AsyncSession) -> None:
    task_a = uuid.uuid4()
    task_b = uuid.uuid4()
    records_a = await _seed(session, task_a)
    records_b = await _seed(session, task_b)
    reviewer = uuid.uuid4()
    service = ScreeningService(session)
    await service.decide(records_a[0].id, stage=DecisionStage.TITLE_ABSTRACT, verdict=ScreeningVerdict.INCLUDE, reviewer_id=reviewer)
    await service.decide(records_a[1].id, stage=DecisionStage.TITLE_ABSTRACT, verdict=ScreeningVerdict.EXCLUDE, reviewer_id=reviewer, exclusion_reason="x")
    await service.decide(records_b[1].id, stage=DecisionStage.TITLE_ABSTRACT, verdict=ScreeningVerdict.EXCLUDE, reviewer_id=reviewer, exclusion_reason="x")

    ranking = await service.priority(task_a)

    assert ranking.positive == 1
    assert ranking.negative == 1


async def test_missing_labels_returns_insufficient(session: AsyncSession) -> None:
    task_id = uuid.uuid4()
    await _seed(session, task_id)
    service = ScreeningService(session)

    ranking = await service.priority(task_id)

    assert ranking.status == "insufficient_labels"
    assert ranking.positive == 0
    assert ranking.negative == 0
    assert ranking.candidates == 5


async def test_model_failure_becomes_priority_unavailable(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = uuid.uuid4()
    await _seed(session, task_id)
    service = ScreeningService(session)

    def boom(*args: object, **kwargs: object) -> None:
        raise ValueError("model failed")

    monkeypatch.setattr(screening_service_module, "rank_candidates", boom)

    with pytest.raises(PriorityUnavailableError):
        await service.priority(task_id)
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m pytest tests/test_screening_priority_service.py -q --no-cov
```

Expected: FAIL，缺少 `list_decisions_for_task`、`ScreeningService.priority`，或 `rank_candidates` 不存在。

- [ ] **Step 3: 在 Repository 增加一次任务级决策查询**

在 `backend/app/db/repositories/screening.py` 的 `list_decisions` 之后增加：

```python
    async def list_decisions_for_task(
        self, task_id: uuid.UUID, stage: DecisionStage
    ) -> list[ScreeningDecision]:
        """一次查询任务内某阶段的全部决策（按记录、seq 正序）。

        供主动排序聚合标签；不要按记录逐条查询，否则 1000 条会变成 N+1。
        """
        stmt = (
            select(ScreeningDecision)
            .join(ScreeningRecord, ScreeningDecision.record_id == ScreeningRecord.id)
            .where(
                ScreeningRecord.task_id == task_id,
                ScreeningDecision.stage == stage.value,
            )
            .order_by(ScreeningDecision.record_id.asc(), ScreeningDecision.seq.asc())
        )
        return list((await self.db.execute(stmt)).scalars().all())
```

- [ ] **Step 4: 在 Service 增加标签聚合与排序**

在 `backend/app/knowledge/services/screening_service.py` 顶部的 import 中加入：

```python
from app.core.logging import get_logger
from app.knowledge.services.screening_priority import (
    LabeledText,
    PriorityCandidate,
    PriorityRanking,
    PriorityUnavailableError,
    rank_candidates,
)

logger = get_logger(__name__)
```

在 `ScreeningService` 的 `list_search_logs` 之后加入：

```python
    async def priority(self, task_id: uuid.UUID) -> PriorityRanking:
        """按任务全员标签排序未筛的标题/摘要记录。

        标签规则：
        - 任一 include → 正样本（保护召回）；
        - 无 include 且有 exclude → 负样本；
        - 只有 maybe → 忽略；
        - 候选必须是 title_abstract 且任务内无任何该阶段决策。
        """
        records = await self._repo.list_records(task_id, limit=1000)
        decisions = await self._repo.list_decisions_for_task(
            task_id, DecisionStage.TITLE_ABSTRACT
        )
        by_record: dict[uuid.UUID, list[Any]] = {}
        for decision in decisions:
            by_record.setdefault(decision.record_id, []).append(decision)

        labeled: list[LabeledText] = []
        candidates: list[PriorityCandidate] = []
        ignored = 0
        for record in records:
            text = f"{record.title}\n{record.abstract}"
            record_decisions = by_record.get(record.id, [])
            if not record_decisions:
                if record.current_stage == DecisionStage.TITLE_ABSTRACT.value:
                    candidates.append(
                        PriorityCandidate(record_id=str(record.id), text=text)
                    )
                continue
            verdicts = {decision.verdict for decision in record_decisions}
            if ScreeningVerdict.INCLUDE.value in verdicts:
                labeled.append(LabeledText(text=text, label="positive"))
            elif ScreeningVerdict.EXCLUDE.value in verdicts:
                labeled.append(LabeledText(text=text, label="negative"))
            else:
                ignored += 1

        try:
            return rank_candidates(labeled, candidates, ignored=ignored)
        except (ValueError, RuntimeError) as exc:
            logger.warning("screening_priority_unavailable", error=str(exc))
            raise PriorityUnavailableError("主动排序暂不可用，请稍后重试") from exc
```

- [ ] **Step 5: 运行 Service/Repository 测试**

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m pytest tests/test_screening_priority_service.py -q --no-cov
```

Expected: 全部 PASS。

- [ ] **Step 6: 回归既有筛选测试**

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m pytest tests/test_screening_service.py tests/test_screening_repository.py tests/test_screening_api.py -q --no-cov
```

Expected: 全部 PASS（新增方法不改变既有行为）。

- [ ] **Step 7: 格式检查并提交 Task 2**

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m black --check app/db/repositories/screening.py app/knowledge/services/screening_service.py tests/test_screening_priority_service.py
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m ruff check app/db/repositories/screening.py app/knowledge/services/screening_service.py tests/test_screening_priority_service.py
```

```powershell
git add backend/app/db/repositories/screening.py backend/app/knowledge/services/screening_service.py backend/tests/test_screening_priority_service.py
git commit -m "feat: aggregate task-wide labels for screening priority"
```

---

### Task 3: 只读 HTTP API

**Files:**
- Modify: `backend/app/schemas/screening.py`
- Modify: `backend/app/api/v1/endpoints/screening.py`
- Test: `backend/tests/test_screening_api.py`

- [ ] **Step 1: 写 API 失败测试**

在 `backend/tests/test_screening_api.py` 末尾添加：

```python
def _post_screening_records(
    client: TestClient,
    task_id: uuid.UUID,
    records: list[dict[str, Any]],
) -> None:
    response = _post_search(client, task_id, records=records)
    assert response.status_code == 201, response.text


def _record_by_title(client: TestClient, task_id: uuid.UUID, title: str) -> str:
    items = client.get(
        "/api/v1/screening/records", params={"task_id": str(task_id)}
    ).json()["items"]
    return str(next(item["record_id"] for item in items if item["title"] == title))


def test_priority_returns_ranked_unreviewed_records(
    client: TestClient, task_id: uuid.UUID
) -> None:
    _post_screening_records(
        client,
        task_id,
        [
            _record("pos", "retrieval augmented generation evaluation", abstract="evidence"),
            _record("neg", "clinical cancer trial patient outcome", abstract="medical"),
            _record("c1", "retrieval augmented generation evidence", abstract="benchmark"),
            _record("c2", "clinical trial patient treatment", abstract="cancer"),
        ],
    )
    pos_id = _record_by_title(client, task_id, "retrieval augmented generation evaluation")
    neg_id = _record_by_title(client, task_id, "clinical cancer trial patient outcome")
    client.post(
        f"/api/v1/screening/records/{pos_id}/decisions",
        json={"stage": "title_abstract", "verdict": "include"},
    )
    client.post(
        f"/api/v1/screening/records/{neg_id}/decisions",
        json={
            "stage": "title_abstract",
            "verdict": "exclude",
            "exclusion_reason": "not relevant",
        },
    )

    response = client.get(
        "/api/v1/screening/priority", params={"task_id": str(task_id)}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["algorithm"] == "tfidf-char-logreg-v1"
    assert body["labels"] == {"positive": 1, "negative": 1, "ignored": 0}
    assert body["candidates"] == 2
    assert [item["rank"] for item in body["items"]] == [1, 2]
    assert all("score" not in item for item in body["items"])


def test_priority_insufficient_labels(
    client: TestClient, task_id: uuid.UUID
) -> None:
    _post_screening_records(
        client,
        task_id,
        [
            _record("pos", "retrieval augmented generation"),
            _record("c1", "another candidate one"),
            _record("c2", "another candidate two"),
        ],
    )
    pos_id = _record_by_title(client, task_id, "retrieval augmented generation")
    client.post(
        f"/api/v1/screening/records/{pos_id}/decisions",
        json={"stage": "title_abstract", "verdict": "include"},
    )

    response = client.get(
        "/api/v1/screening/priority", params={"task_id": str(task_id)}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_labels"
    assert response.json()["items"] == []


def test_priority_no_candidates(client: TestClient, task_id: uuid.UUID) -> None:
    _post_screening_records(
        client,
        task_id,
        [
            _record("pos", "retrieval augmented generation"),
            _record("neg", "clinical cancer trial"),
        ],
    )
    pos_id = _record_by_title(client, task_id, "retrieval augmented generation")
    neg_id = _record_by_title(client, task_id, "clinical cancer trial")
    client.post(
        f"/api/v1/screening/records/{pos_id}/decisions",
        json={"stage": "title_abstract", "verdict": "include"},
    )
    client.post(
        f"/api/v1/screening/records/{neg_id}/decisions",
        json={
            "stage": "title_abstract",
            "verdict": "exclude",
            "exclusion_reason": "not relevant",
        },
    )

    response = client.get(
        "/api/v1/screening/priority", params={"task_id": str(task_id)}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "no_candidates"
    assert response.json()["items"] == []


def test_priority_rejects_other_task(
    client: TestClient, other_task_id: uuid.UUID
) -> None:
    response = client.get(
        "/api/v1/screening/priority", params={"task_id": str(other_task_id)}
    )

    assert response.status_code == 404


def test_priority_rejects_unknown_stage(
    client: TestClient, task_id: uuid.UUID
) -> None:
    response = client.get(
        "/api/v1/screening/priority",
        params={"task_id": str(task_id), "stage": "fulltext"},
    )

    assert response.status_code == 400
    assert "title_abstract" in response.json()["detail"]


def test_priority_requires_auth(app: FastAPI, client: TestClient, task_id: uuid.UUID) -> None:
    original = app.dependency_overrides.pop(screening_endpoint.get_current_active_user)
    try:
        response = client.get(
            "/api/v1/screening/priority", params={"task_id": str(task_id)}
        )
    finally:
        app.dependency_overrides[screening_endpoint.get_current_active_user] = original

    assert response.status_code == 401


def test_priority_maps_model_error_to_503(
    app: FastAPI, client: TestClient, task_id: uuid.UUID
) -> None:
    class _BoomService:
        async def ensure_task_owned(self, *args: object, **kwargs: object) -> None:
            return None

        async def priority(self, *args: object, **kwargs: object) -> None:
            from app.knowledge.services.screening_priority import (
                PriorityUnavailableError,
            )

            raise PriorityUnavailableError("排序暂不可用")

    app.dependency_overrides[screening_endpoint.get_screening_service] = lambda: _BoomService()
    try:
        response = client.get(
            "/api/v1/screening/priority", params={"task_id": str(task_id)}
        )
    finally:
        app.dependency_overrides.pop(screening_endpoint.get_screening_service, None)

    assert response.status_code == 503
    assert "排序暂不可用" in response.json()["detail"]
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m pytest tests/test_screening_api.py -q --no-cov
```

Expected: 新用例 404（路由不存在）或 schema 不存在。

- [ ] **Step 3: 增加响应 DTO**

在 `backend/app/schemas/screening.py` 中把 `Literal` 加入 typing 导入，并在 `PrismaOut` 之前加入：

```python
class ScreeningPriorityItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: uuid.UUID
    rank: int = Field(ge=1)


class ScreeningPriorityLabelsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    positive: int = Field(ge=0)
    negative: int = Field(ge=0)
    ignored: int = Field(ge=0)


class ScreeningPriorityOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "insufficient_labels", "no_candidates"]
    algorithm: str
    stage: str
    items: list[ScreeningPriorityItemOut] = Field(default_factory=list)
    labels: ScreeningPriorityLabelsOut
    candidates: int = Field(ge=0)
    message: str
```

- [ ] **Step 4: 增加端点**

在 `backend/app/api/v1/endpoints/screening.py` 新增域错误导入，并把 `ScreeningPriorityOut` 加入现有 schema 导入列表：

```python
from app.knowledge.services.screening_priority import PriorityUnavailableError
from app.schemas.screening import (
    DecisionCreate,
    DecisionOut,
    LiteratureRecordIn,
    PrismaOut,
    ScreeningPriorityOut,
    ScreeningRecordListOut,
    ScreeningRecordOut,
    SearchLogCreate,
    SearchLogOut,
)
```

在 `get_prisma` 前加入：

```python
@router.get(
    "/priority",
    response_model=ScreeningPriorityOut,
    summary="列出主动学习优先级排序（只读）",
)
async def get_priority(
    service: ServiceDep,
    current_user: CurrentUserDep,
    task_id: Annotated[uuid.UUID, Query(description="任务 ID")],
    stage: Annotated[str, Query(description="排序阶段")] = "title_abstract",
) -> ScreeningPriorityOut:
    """只排序标题/摘要阶段的未筛记录，不自动决策、不改 PRISMA 计数。"""
    await service.ensure_task_owned(current_user.id, task_id)
    if stage != DecisionStage.TITLE_ABSTRACT.value:
        raise _bad_request("主动排序仅支持 title_abstract 阶段")
    try:
        ranking = await service.priority(task_id)
    except PriorityUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return ScreeningPriorityOut(
        status=ranking.status,
        algorithm=ranking.algorithm,
        stage=stage,
        items=[
            {"record_id": uuid.UUID(item.record_id), "rank": item.rank}
            for item in ranking.items
        ],
        labels={
            "positive": ranking.positive,
            "negative": ranking.negative,
            "ignored": ranking.ignored,
        },
        candidates=ranking.candidates,
        message=ranking.message,
    )
```

- [ ] **Step 5: 运行 API 测试**

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m pytest tests/test_screening_api.py tests/test_screening_priority.py tests/test_screening_priority_service.py -q --no-cov
```

Expected: 全部 PASS。若 401 用例因独立测试 app 缺异常 handler 返回 500，补与生产一致的 `UnauthorizedException` handler，不得删除该验收项。

- [ ] **Step 6: 格式检查并提交 Task 3**

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m black --check app/schemas/screening.py app/api/v1/endpoints/screening.py tests/test_screening_api.py
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m ruff check app/schemas/screening.py app/api/v1/endpoints/screening.py tests/test_screening_api.py
```

```powershell
git add backend/app/schemas/screening.py backend/app/api/v1/endpoints/screening.py backend/tests/test_screening_api.py
git commit -m "feat: expose active screening priority API"
```

---

### Task 4: 前端 API、开关与置顶展示

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/screening.ts`
- Modify: `frontend/src/views/ScreeningView.vue`
- Test: `frontend/src/views/ScreeningView.test.ts`

- [ ] **Step 1: 准备前端依赖环境**

```powershell
$main='E:\Multi-agent collaborative automation task assistant\frontend\node_modules'
$link='E:\Multi-agent collaborative automation task assistant\.agents\worktrees\codex\screening-priority\frontend\node_modules'
if (-not (Test-Path $link)) { New-Item -ItemType Junction -Path $link -Target $main | Out-Null }
Test-Path (Join-Path $link 'vitest\vitest.mjs')
```

Expected: `True`。

- [ ] **Step 2: 写前端失败测试**

在 `frontend/src/views/ScreeningView.test.ts` 的 mock 中加入：

```ts
vi.mock("@/api/screening", () => ({
  recordSearch: vi.fn(),
  listScreeningRecords: vi.fn(),
  createDecision: vi.fn(),
  getPrismaCounts: vi.fn(),
  getScreeningPriority: vi.fn(),
}));

import {
  createDecision,
  getPrismaCounts,
  getScreeningPriority,
  listScreeningRecords,
} from "@/api/screening";
import type { ScreeningPriority } from "@/api/types";
```

在 `beforeEach` 后添加 helper：

```ts
function priorityResult(overrides: Partial<ScreeningPriority> = {}): ScreeningPriority {
  return {
    status: "ready",
    algorithm: "tfidf-char-logreg-v1",
    stage: "title_abstract",
    items: [],
    labels: { positive: 1, negative: 1, ignored: 0 },
    candidates: 0,
    message: "基于任务内 1 条纳入 / 1 条排除决策排序",
    ...overrides,
  };
}
```

在 `describe` 末尾添加：

```ts
it("默认关闭时不请求主动排序", async () => {
  const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
  await flushPromises();

  expect(getScreeningPriority).not.toHaveBeenCalled();
  expect(wrapper.find('[data-test="screening-priority-toggle"]').attributes("aria-pressed")).toBe("false");
});

it("开启后把推荐候选置顶并保留其他记录", async () => {
  vi.mocked(listScreeningRecords).mockResolvedValue([
    recordRow({ record_id: "r1", title: "第一篇" }),
    recordRow({ record_id: "r2", title: "第二篇" }),
    recordRow({ record_id: "r3", title: "第三篇" }),
  ]);
  vi.mocked(getScreeningPriority).mockResolvedValue(
    priorityResult({
      items: [
        { record_id: "r2", rank: 1 },
        { record_id: "r1", rank: 2 },
      ],
      candidates: 2,
    })
  );

  const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
  await flushPromises();
  await wrapper.find('[data-test="screening-priority-toggle"]').trigger("click");
  await flushPromises();

  const ids = wrapper
    .findAll('[data-test^="screening-row-"]')
    .map((row) => row.attributes("data-test"));
  expect(ids).toEqual(["screening-row-r2", "screening-row-r1", "screening-row-r3"]);
  expect(wrapper.find('[data-test="screening-priority-rank-r2"]').text()).toContain("#1");
  expect(wrapper.find('[data-test="screening-priority-rank-r3"]').exists()).toBe(false);
});

it("标签不足时保持原序并显示提示", async () => {
  vi.mocked(listScreeningRecords).mockResolvedValue([
    recordRow({ record_id: "r1", title: "第一篇" }),
    recordRow({ record_id: "r2", title: "第二篇" }),
  ]);
  vi.mocked(getScreeningPriority).mockResolvedValue(
    priorityResult({
      status: "insufficient_labels",
      items: [],
      candidates: 2,
      message: "至少需要 1 条纳入和 1 条排除决策（当前 1 / 0）",
    })
  );

  const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
  await flushPromises();
  await wrapper.find('[data-test="screening-priority-toggle"]').trigger("click");
  await flushPromises();

  const ids = wrapper
    .findAll('[data-test^="screening-row-"]')
    .map((row) => row.attributes("data-test"));
  expect(ids).toEqual(["screening-row-r1", "screening-row-r2"]);
  expect(wrapper.find('[data-test="screening-priority-status"]').text()).toContain("至少需要");
});

it("排序请求失败时保持原序并显示错误", async () => {
  vi.mocked(getScreeningPriority).mockRejectedValue(new Error("排序服务不可用"));

  const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
  await flushPromises();
  await wrapper.find('[data-test="screening-priority-toggle"]').trigger("click");
  await flushPromises();

  expect(wrapper.find('[data-test="screening-priority-status"]').text()).toContain("排序服务不可用");
  expect(wrapper.find('[data-test="screening-row-r1"]').exists()).toBe(true);
});

it("开启后提交决策会刷新排序", async () => {
  vi.mocked(getScreeningPriority).mockResolvedValue(priorityResult());
  vi.mocked(createDecision).mockResolvedValue({
    record_id: "r1",
    current_stage: "fulltext",
  });

  const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
  await flushPromises();
  await wrapper.find('[data-test="screening-priority-toggle"]').trigger("click");
  await flushPromises();
  const before = vi.mocked(getScreeningPriority).mock.calls.length;

  await wrapper.find('[data-test="screening-include-r1"]').trigger("click");
  await flushPromises();

  expect(vi.mocked(getScreeningPriority).mock.calls.length).toBe(before + 1);
});
```

- [ ] **Step 3: 运行测试确认失败**

```powershell
cd frontend
& 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe' node_modules/vitest/vitest.mjs run src/views/ScreeningView.test.ts
```

Expected: FAIL，缺少 `getScreeningPriority`、开关或排序展示。

- [ ] **Step 4: 增加前端类型与 API**

在 `frontend/src/api/types.ts` 的 `PrismaCounts` 后加入：

```ts
export type ScreeningPriorityStatus = "ready" | "insufficient_labels" | "no_candidates";

export interface ScreeningPriorityItem {
  record_id: string;
  rank: number;
}

export interface ScreeningPriority {
  status: ScreeningPriorityStatus;
  algorithm: string;
  stage: string;
  items: ScreeningPriorityItem[];
  labels: { positive: number; negative: number; ignored: number };
  candidates: number;
  message: string;
}
```

在 `frontend/src/api/screening.ts` 顶部把 `ScreeningPriority` 加入现有 type import：

```ts
import type {
  PrismaCounts,
  ScreeningDecisionPayload,
  ScreeningPriority,
  ScreeningRecord,
  SearchLogCreatePayload,
  SearchLogOut,
} from "./types";

export async function getScreeningPriority(
  taskId: string,
  stage: "title_abstract" = "title_abstract"
): Promise<ScreeningPriority> {
  const params = new URLSearchParams({ task_id: taskId, stage });
  const response = await api.get<ScreeningPriority>(
    `/screening/priority?${params.toString()}`
  );
  return response.data;
}
```

- [ ] **Step 5: 实现 ScreeningView 开关与排序**

在 `ScreeningView.vue` 中：先保留现有 `stageFilter` 等状态声明，在它们之后加入优先级状态与 computed（`priorityDisabled` 依赖 `stageFilter`，不能声明在它前面）：

```ts
import { computed, onMounted, ref } from "vue";
import { Check, CircleX, MinusCircle, Sparkles } from "lucide-vue-next";
import { createDecision, getPrismaCounts, getScreeningPriority, listScreeningRecords } from "@/api/screening";
import type { PrismaCounts, ScreeningPriority, ScreeningRecord } from "@/api/types";

const priorityEnabled = ref(false);
const priority = ref<ScreeningPriority | null>(null);
const priorityLoading = ref(false);
const priorityError = ref<string | null>(null);

const priorityRanks = computed(
  () => new Map((priority.value?.items ?? []).map((item) => [item.record_id, item.rank]))
);
const priorityDisabled = computed(
  () => priorityLoading.value || ["fulltext", "included"].includes(stageFilter.value)
);
const displayRecords = computed(() => {
  if (!priorityEnabled.value || priority.value?.status !== "ready") {
    return records.value;
  }
  const ranked = records.value
    .filter((record) => priorityRanks.value.has(record.record_id))
    .sort(
      (left, right) =>
        (priorityRanks.value.get(left.record_id) ?? 0) -
        (priorityRanks.value.get(right.record_id) ?? 0)
    );
  const rest = records.value.filter(
    (record) => !priorityRanks.value.has(record.record_id)
  );
  return [...ranked, ...rest];
});
const priorityTone = computed<"success" | "warning" | "muted" | "destructive">(() => {
  if (priorityError.value) return "destructive";
  if (priority.value?.status === "insufficient_labels") return "warning";
  if (priority.value?.status === "ready") return "success";
  return "muted";
});

async function loadPriority() {
  if (!priorityEnabled.value) return;
  priorityLoading.value = true;
  priorityError.value = null;
  try {
    priority.value = await getScreeningPriority(props.taskId);
  } catch (err) {
    priorityError.value = (err as Error).message || "主动排序加载失败";
    priority.value = null;
  } finally {
    priorityLoading.value = false;
  }
}

async function togglePriority() {
  if (priorityDisabled.value) return;
  priorityEnabled.value = !priorityEnabled.value;
  if (!priorityEnabled.value) {
    priority.value = null;
    priorityError.value = null;
    return;
  }
  await loadPriority();
}

async function handleStageChange() {
  if (priorityEnabled.value && ["fulltext", "included"].includes(stageFilter.value)) {
    priorityEnabled.value = false;
    priority.value = null;
    priorityError.value = null;
  }
  await refresh();
}

async function refresh() {
  loading.value = true;
  error.value = null;
  try {
    const [nextRecords, nextPrisma] = await Promise.all([
      listScreeningRecords(props.taskId, stageFilter.value || undefined),
      getPrismaCounts(props.taskId),
    ]);
    records.value = nextRecords;
    prisma.value = nextPrisma;
    if (priorityEnabled.value) {
      await loadPriority();
    }
  } catch (err) {
    error.value = (err as Error).message || "筛选数据加载失败";
  } finally {
    loading.value = false;
  }
}
```

将阶段选择的 `@change="refresh()"` 改为 `@change="handleStageChange()"`；把 `v-for="record in records"` 改为 `v-for="record in displayRecords"`。

在阶段过滤下拉框后加入：

```vue
<div class="mb-3 flex flex-wrap items-center gap-2">
  <Button
    variant="outline"
    size="sm"
    type="button"
    data-test="screening-priority-toggle"
    :aria-pressed="priorityEnabled"
    :disabled="priorityDisabled"
    @click="togglePriority"
  >
    <Sparkles :size="15" aria-hidden="true" />
    智能排序：{{ priorityEnabled ? "开" : "关" }}
  </Button>
  <span class="text-xs text-muted-foreground">
    按标题/摘要排序，不自动纳入或排除
  </span>
</div>

<Alert
  v-if="priorityEnabled"
  :variant="priorityTone"
  class="mb-3"
  data-test="screening-priority-status"
>
  <span v-if="priorityLoading">正在计算优先级…</span>
  <span v-else-if="priorityError">{{ priorityError }}</span>
  <span v-else-if="priority">{{ priority.message }}</span>
</Alert>
```

在标题后的阶段 Badge 后加入：

```vue
<Badge
  v-if="priorityRanks.get(record.record_id)"
  variant="success"
  :data-test="`screening-priority-rank-${record.record_id}`"
>
  推荐优先 #{{ priorityRanks.get(record.record_id) }}
</Badge>
```

- [ ] **Step 6: 运行前端测试与类型检查**

```powershell
cd frontend
$node='C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'
& $node node_modules/vitest/vitest.mjs run src/views/ScreeningView.test.ts
& $node node_modules/vue-tsc/bin/vue-tsc.js -b
```

Expected: 全部 PASS，`vue-tsc` 0 错误。

- [ ] **Step 7: 提交 Task 4**

```powershell
git add frontend/src/api/types.ts frontend/src/api/screening.ts frontend/src/views/ScreeningView.vue frontend/src/views/ScreeningView.test.ts
git commit -m "feat: add active screening priority toggle"
```

---

### Task 5: 端到端验收、性能记录与文档

**Files:**
- Create: `docs/runbooks/screening-priority.md`
- Create: `docs/runbooks/screening-priority-acceptance.md`

- [ ] **Step 1: 运行完整后端定向回归**

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m pytest tests/test_screening_priority.py tests/test_screening_priority_service.py tests/test_screening_api.py tests/test_screening_service.py tests/test_screening_repository.py -q --no-cov
```

Expected: 全部 PASS。

- [ ] **Step 2: 运行完整前端回归与类型检查**

```powershell
cd frontend
$node='C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'
& $node node_modules/vitest/vitest.mjs run
& $node node_modules/vue-tsc/bin/vue-tsc.js -b
```

Expected: 全部 PASS，`vue-tsc` 0 错误。

- [ ] **Step 3: 运行后端格式与类型门禁**

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m black --check app tests
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m ruff check app tests
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -m mypy app
```

Expected: 全部 0 错误。

- [ ] **Step 4: 记录纯排序器 1000 条候选耗时**

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' -c "import time; from app.knowledge.services.screening_priority import LabeledText, PriorityCandidate, rank_candidates; pos=[LabeledText('retrieval augmented generation evidence '+str(i), 'positive') for i in range(50)]; neg=[LabeledText('clinical cancer trial patient outcome '+str(i), 'negative') for i in range(50)]; cand=[PriorityCandidate(str(i), 'retrieval augmented generation evidence benchmark' if i%2==0 else 'clinical cancer trial patient outcome') for i in range(1000)]; start=time.perf_counter(); result=rank_candidates(pos+neg, cand); print(result.status, len(result.items), round(time.perf_counter()-start, 3))"
```

Expected: `ready 1000 <秒数>`；把实际秒数写入验收文档，不虚构 SLA。

- [ ] **Step 5: 写使用与限制手册**

创建 `docs/runbooks/screening-priority.md`，写明：

- 开关位置与默认关闭；
- 仅在标题/摘要阶段可用；
- 训练标签规则（任一 include 为正、无 include 的 exclude 为负、maybe 忽略）；
- 候选要求（任务内未筛）；
- 冷启动与错误提示；
- 模型版本 `tfidf-char-logreg-v1`，不展示概率、不自动决策；
- 双人筛选的已知边界（第二位筛选者的独立队列不在本切片）；
- 依赖 `scikit-learn` 与 Docker 镜像需重建；
- 不重跑论文实验矩阵。

- [ ] **Step 6: 写验收记录**

创建 `docs/runbooks/screening-priority-acceptance.md`，记录：

- Task 1–4 的实际测试命令与结果；
- 后端/前端门禁数字；
- 1000 条候选实测耗时；
- 无新迁移、无自动决策、PRISMA 计数不变；
- 真实浏览器/真实部署未执行时明确写“未执行”，不得把组件测试说成浏览器验收；
- 已知边界与后续扩展（全文、模拟指标、按人队列、停止准则）。

- [ ] **Step 7: 提交 Task 5**

```powershell
git add docs/runbooks/screening-priority.md docs/runbooks/screening-priority-acceptance.md
git commit -m "docs: record active screening priority acceptance"
```

---

## 计划自查结果

- **规格覆盖**：Task 1 覆盖纯排序器/依赖；Task 2 覆盖 Repository 查询、标签聚合、候选过滤与 503 域错误；Task 3 覆盖 API 契约/鉴权/状态；Task 4 覆盖开关、置顶、冷启动、错误和决策后刷新；Task 5 覆盖回归、性能、文档。
- **类型一致**：`PriorityRanking` 的 `status/items/positive/negative/ignored/candidates/message/algorithm` 在 Service、Schema、前端类型中命名一致；`record_id` 在纯模块为 `str`，在 API/DB 边界转换为 `uuid.UUID`/字符串。
- **无占位**：每个任务包含实际测试、实现片段、运行命令、预期结果和精确提交路径。
- **范围限制**：仅标题/摘要、任务级标签、无迁移、无持久化模型、无自动决策、无概率展示。
