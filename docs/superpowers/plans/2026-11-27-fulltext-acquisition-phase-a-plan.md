# 文献正文获取 Phase A：OA 自动正文 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:executing-plans`（或
> `superpowers:subagent-driven-development`）逐 Task 实施。步骤用 `- [ ]` 复选框跟踪。

**Goal:** 检索命中的 OA 文献在重排后自动抓取 Top 8 正文，去 References、双栏/章节清洁解析、
生成全局复用的 Paper Card，并作为该用户知识库文档参与检索，使证据从 `metadata` 升级为
带页码/章节 locator 的 `excerpt`；任何失败降级 metadata + warning，绝不阻塞任务。

**Architecture:** 新增 `FullTextResolver`（provider 链：OpenAlex OA → arXiv HTML → arXiv PDF →
Europe PMC → Unpaywall → CORE）拿到原始正文；`CleanFullTextParser` 产出
`parsed_sections`；`FulltextService` 负责 license 闸门、`fulltext_cache` 去重落库、
挂载到用户 `knowledge_doc` + `doc_chunk` + 向量索引；`literature_review` 工作流在 search 节点后
调用并写 `stats["fulltext_summary"]`；证据升级由既有 `LiteratureContextBuilder` 检索链路自然完成
（`extract_locator_metadata` 已把 `position.page_start/page_end` 与
`chunk_meta.section_title` 映射成 `EvidenceLocator`）。

**Tech Stack:** FastAPI + SQLAlchemy(async) + Alembic + Pydantic v2；PyMuPDF（已在依赖中）；
标准库 `html.parser` 解析 arXiv HTML / JATS（**不新增 bs4/lxml 依赖**）；httpx（既有）；
pytest。

**关联文档：** `docs/superpowers/specs/2026-11-26-fulltext-acquisition-spec.md`、
Issue #73。

---

## 执行约束（必须遵守）

1. **一切测试只在 10.0.10.2 跑**：`ssh ylin-dev` → `cd ~/vp-test` → `git fetch origin main &&
   git reset --hard origin/main`（若需当前分支代码，用 `git archive` + overlay 或直接 push 前在
   测试机 `git fetch` 对应分支）；`./run-tests.sh <paths>`。本机只跑
   `black --check` / `ruff check` / `mypy app`（前端改动才 `npm run build`）。
2. **每个 Task 先写失败测试（红）→ 实现 → 测试绿 → 本机静态检查 → 一次 Conventional Commit**；
   提交只用精确 `git add <路径>`，禁止 `git add -A`。
3. 迁移 revision id ≤ 32 字符；`downgrade()` 必须可回滚。
4. 新增任何第三方 import 必须同 commit 改 `backend/pyproject.toml`（本计划不新增依赖）。
5. 生产部署按既有流程（bundle → `/opt/veripaper` fetch+merge → `deploy/ecs` build+up），
   部署后跑 `/health/ready` 与真实任务验收。

---

## Task 1：配置与任务契约（full_text 开关）

**Files:**
- Modify: `backend/app/core/config.py`（新增 `FulltextConfig` + `Settings.fulltext`）
- Modify: `backend/app/schemas/task.py`（`TaskCreate.full_text`）
- Modify: `backend/app/orchestration/contracts.py`（`LiteratureReviewRequest.full_text`）
- Test: `backend/tests/test_fulltext_config.py`

**Step 1：写失败测试**

```python
# backend/tests/test_fulltext_config.py
from app.core.config import settings
from app.orchestration.contracts import LiteratureReviewRequest
from app.schemas.task import TaskCreate


def test_fulltext_config_defaults():
    cfg = settings.fulltext
    assert cfg.enabled is True
    assert cfg.max_documents_per_task == 8
    assert cfg.per_document_timeout_seconds == 20.0
    assert cfg.total_timeout_seconds == 60.0
    assert cfg.max_concurrency == 4
    assert "cc-by" in cfg.license_whitelist


def test_task_create_full_text_default_true_and_can_disable():
    assert TaskCreate(topic="测试主题五个字", keywords=["k"]).full_text is True
    assert (
        TaskCreate(topic="测试主题五个字", keywords=["k"], full_text=False).full_text is False
    )


def test_literature_review_request_full_text_default_true():
    req = LiteratureReviewRequest(topic="测试主题五个字", keywords=["k"])
    assert req.full_text is True
```

**Step 2：跑测试确认失败**

- 10.0.10.2：`./run-tests.sh tests/test_fulltext_config.py` → 预期 `AttributeError:
  'Settings' object has no attribute 'fulltext'` / `extra_forbidden`。

**Step 3：实现**

`backend/app/core/config.py`（放在 `ReferenceVerificationConfig` 之后，风格照抄）：

```python
class FulltextConfig(BaseModel):
    """OA 自动正文抓取（Issue #73 Phase A）。"""

    enabled: bool = True
    max_documents_per_task: int = Field(default=8, ge=1, le=20)
    per_document_timeout_seconds: float = Field(default=20.0, gt=0.0)
    total_timeout_seconds: float = Field(default=60.0, gt=0.0)
    max_concurrency: int = Field(default=4, ge=1, le=8)
    unpaywall_email: str = ""
    """Unpaywall 要求带 email；为空时 resolver 跳过 Unpaywall（不匿名调用）。"""
    core_api_key: str = ""
    license_whitelist: list[str] = Field(
        default_factory=lambda: [
            "cc-by",
            "cc-by-4.0",
            "cc-by-3.0",
            "cc-by-sa",
            "cc-by-nc",
            "cc0",
            "public-domain",
            "arxiv",
            "pmc-oa",
            "elsevier-oa",
        ]
    )
```

`Settings` 中挂载（与 `reference_verification` 同一区域）：
`fulltext: FulltextConfig = FulltextConfig()`。

契约各加一行（带 docstring 说明离线/引用库模式跳过）：

```python
full_text: bool = True
"""是否自动抓取 OA 正文（Issue #73）。离线任务 / reference_library 模式自动跳过。"""
```

**Step 4：跑测试确认通过**

- 10.0.10.2：`./run-tests.sh tests/test_fulltext_config.py` → 预期 `3 passed`。
- 本机：`black --check app tests`、`ruff check app tests`、`mypy app` 全绿。

**Step 5：提交**

```bash
git add backend/app/core/config.py backend/app/schemas/task.py \
  backend/app/orchestration/contracts.py backend/tests/test_fulltext_config.py
git commit -m "feat(fulltext): 新增 FulltextConfig 与任务级 full_text 开关"
```

---

## Task 2：`fulltext_cache` 模型与迁移

**Files:**
- Create: `backend/app/db/models/fulltext_cache.py`
- Modify: `backend/app/db/models/__init__.py`（导出）
- Create: `backend/alembic/versions/a1f2b3c4d5e6_add_fulltext_cache.py`
- Test: `backend/tests/test_fulltext_cache_model.py`

**Step 1：写失败测试**

```python
# backend/tests/test_fulltext_cache_model.py
import uuid

from app.db.models import FulltextCache


def test_fulltext_cache_columns_and_defaults():
    row = FulltextCache(
        doi="10.1234/abc",
        arxiv_id=None,
        pmid=None,
        sha256="a" * 64,
        provider="arxiv_pdf",
        source_url="https://arxiv.org/pdf/2401.00001",
        license="arxiv",
        content_format="pdf",
        page_count=12,
        parsed_sections=[{"title": "1 Introduction", "role": "background",
                          "page_start": 1, "page_end": 2, "text": "..."}],
        paper_card=None,
    )
    assert isinstance(row.id, uuid.UUID)
    assert row.status == "active"
```

**Step 2：跑测试确认失败** → `ImportError: cannot import name 'FulltextCache'`。

**Step 3：实现模型**

```python
# backend/app/db/models/fulltext_cache.py
"""OA 文献全局正文缓存（Issue #73）：仅存合规 OA，按 DOI/arXiv/PMID/sha256 去重。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FulltextCache(Base):
    """全局 OA 正文与 Paper Card 缓存（严禁存付费闭源文献）。"""

    __tablename__ = "fulltext_cache"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    doi: Mapped[str | None] = mapped_column(String(256), unique=True, index=True, nullable=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    pmid: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    license: Mapped[str] = mapped_column(String(64), nullable=False)
    content_format: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parsed_sections: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    paper_card: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
```

`backend/app/db/models/__init__.py` 的 `__all__` 与 import 增加 `FulltextCache`。

迁移（文件名 `a1f2b3c4d5e6_add_fulltext_cache.py`，`down_revision = "d8b2f5c3a1e9"`）：
`op.create_table("fulltext_cache", ...)` 全列 + 三个唯一索引 + `op.create_index("ix_fulltext_cache_status", ...)`；
`downgrade()` 里 `op.drop_table("fulltext_cache")`。

**Step 4：验证**

- 10.0.10.2：`./run-tests.sh tests/test_fulltext_cache_model.py` → `1 passed`。
- 迁移可执行性（本地 SQLite 或测试机临时库）：`cd backend && .venv/bin/alembic upgrade head`
  在本机只做 `alembic check` 级别的静态核对即可；**真实迁移验证放到测试机**：
  `./run-tests.sh tests/test_migrations.py`（若存在）或 `alembic upgrade head` + `downgrade -1` + `upgrade head`。

**Step 5：提交**

```bash
git add backend/app/db/models/fulltext_cache.py backend/app/db/models/__init__.py \
  backend/alembic/versions/a1f2b3c4d5e6_add_fulltext_cache.py \
  backend/tests/test_fulltext_cache_model.py
git commit -m "feat(fulltext): 新增 fulltext_cache 全局 OA 正文缓存表与迁移"
```

---

## Task 3：`CleanFullTextParser`（双栏 / 去 References / 扫描检测 / section_role）

**Files:**
- Create: `backend/app/knowledge/services/fulltext_parser.py`
- Test: `backend/tests/test_fulltext_parser.py`

**Step 1：写失败测试**（用 PyMuPDF 现场生成 fixture，避免提交二进制）

```python
# backend/tests/test_fulltext_parser.py
import fitz
import pytest

from app.knowledge.services.fulltext_parser import (
    CleanFullTextParser,
    ParseStatus,
)


def _make_two_column_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((40, 100), "1 Introduction", fontsize=12)
    page.insert_text((40, 140), "Left column text about background.", fontsize=10)
    page.insert_text((330, 100), "2 Method", fontsize=12)
    page.insert_text((330, 140), "Right column text about method.", fontsize=10)
    page.insert_text((40, 760), "References", fontsize=12)
    page.insert_text((40, 780), "[1] should be truncated.", fontsize=10)
    return doc.tobytes()


def test_two_column_order_and_references_truncated():
    parsed = CleanFullTextParser().parse_pdf(_make_two_column_pdf())
    assert parsed.status is ParseStatus.OK
    text = "\n".join(section.text for section in parsed.sections)
    assert text.index("1 Introduction") < text.index("2 Method")
    assert "should be truncated" not in text
    assert parsed.sections[0].page_start == 1


def test_scanned_pdf_is_rejected():
    doc = fitz.open()
    page = doc.new_page()  # 空白页：无文本层
    parsed = CleanFullTextParser().parse_pdf(doc.tobytes())
    assert parsed.status is ParseStatus.SCANNED_NO_TEXT_LAYER
    assert parsed.sections == []


def test_section_role_mapping():
    parser = CleanFullTextParser()
    cases = {
        "1 Introduction": "background",
        "2 Related Work": "background",
        "3 Method": "method",
        "4 Experiments": "result",
        "5 Limitations": "limitation",
        "Appendix": "other",
    }
    for title, role in cases.items():
        assert parser.role_for_title(title) == role
```

**Step 2：跑测试确认失败** → `ModuleNotFoundError`。

**Step 3：实现**

```python
# backend/app/knowledge/services/fulltext_parser.py
"""学术全文清洁解析（Issue #73 Phase A）。

四项核心处理：双栏坐标重排 + 页眉页脚裁切；扫描件密度检测；末尾 References 截断；
章节角色确定性映射。输出与 `fulltext_cache.parsed_sections` 对齐。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

SectionRole = Literal["background", "method", "result", "limitation", "other"]


class ParseStatus(StrEnum):
    OK = "ok"
    SCANNED_NO_TEXT_LAYER = "scanned_no_text_layer"
    EMPTY = "empty"
    FAILED = "failed"


@dataclass(slots=True)
class ParsedSection:
    title: str
    role: SectionRole
    page_start: int
    page_end: int
    text: str


@dataclass(slots=True)
class ParseResult:
    status: ParseStatus
    sections: list[ParsedSection] = field(default_factory=list)
    page_count: int = 0


_ROLE_KEYWORDS: list[tuple[tuple[str, ...], SectionRole]] = [
    (("introduction", "background", "related work", "引言", "背景"), "background"),
    (("method", "approach", "architecture", "方法", "模型", "设计"), "method"),
    (("experiment", "result", "evaluation", "实验", "结果"), "result"),
    (("limitation", "discussion", "future", "局限", "讨论", "展望"), "limitation"),
]

_REFERENCES_RE = re.compile(
    r"^(references|bibliography|参考文献|acknowledg(e)?ments|致谢)$", re.IGNORECASE
)


class CleanFullTextParser:
    """PDF/HTML/JATS 清洁解析器（PDF 用 PyMuPDF，HTML/JATS 用标准库）。"""

    HEADER_RATIO = 0.08
    FOOTER_RATIO = 0.92
    MIN_CHARS_PER_PAGE = 50
    SCANNED_PAGE_RATIO = 0.70

    def role_for_title(self, title: str) -> SectionRole: ...

    def parse_pdf(self, content: bytes) -> ParseResult: ...
```

实现要点（同一文件内）：

1. `parse_pdf`：`fitz.open(stream=content, filetype="pdf")`；逐页取 blocks；
   过滤页眉页脚；判定双栏（左半区块数与右半区块数各 ≥25%）→ 先左栏后右栏，否则
   `(y0, x0)` 排序；拼接本页文本。
2. 扫描判定：先统计每页有效字符数，`< 50` 的页占比 ≥ 0.70 →
   `ParseStatus.SCANNED_NO_TEXT_LAYER`（此时 `sections=[]`）。
3. 全文拼接后找后 50% 位置的独立行匹配 `_REFERENCES_RE`，截断其后内容。
4. 分节：按「独立行且命中角色关键词或以数字编号开头」切分，生成
   `ParsedSection`（无标题的段落归入 `other`，标题取首行前 120 字符）；
   `page_start/page_end` 用块所在页码区间。
5. `parse_html(content: bytes) -> ParseResult`：标准库 `HTMLParser` 收集
   `h1-h6`/`p`/`table` 文本，标题切节并套 `role_for_title`。
6. `parse_jats(content: bytes) -> ParseResult`：`xml.etree.ElementTree` 读
   `sec -> title + p`，异常返回 `FAILED`。

**Step 4：跑测试确认通过** → `tests/test_fulltext_parser.py` 全绿（≥4 passed）。

**Step 5：提交**

```bash
git add backend/app/knowledge/services/fulltext_parser.py backend/tests/test_fulltext_parser.py
git commit -m "feat(fulltext): 学术全文清洁解析器（双栏/扫描检测/References 截断/章节角色）"
```

---

## Task 4：License 闸门 + `FullTextResolver`（provider 链、超时、SSRF 防护）

**Files:**
- Create: `backend/app/tools/literature/fulltext_resolver.py`
- Test: `backend/tests/test_fulltext_resolver.py`

**Step 1：写失败测试**（`httpx.MockTransport` 注入，不发真实请求）

```python
# backend/tests/test_fulltext_resolver.py
import httpx
import pytest

from app.tools.literature.fulltext_resolver import (
    FullTextResolver,
    LicenseNotAllowed,
    _is_safe_url,
)
from app.tools.literature.models import LiteratureRecord


def _record(**overrides) -> LiteratureRecord:
    base = dict(
        source_id="10.1234/abc",
        title="A Test Paper",
        authors=["A"],
        abstract="abstract",
        source_url="https://example.org/paper",
        pdf_url="https://arxiv.org/pdf/2401.00001",
        categories=[],
    )
    base.update(overrides)
    return LiteratureRecord(**base)


def test_license_gate_rejects_non_whitelisted():
    resolver = FullTextResolver(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    with pytest.raises(LicenseNotAllowed):
        resolver.ensure_license("publisher-specific-oa")


def test_ssrf_guard_blocks_private_and_loopback():
    assert _is_safe_url("https://arxiv.org/pdf/1") is True
    assert _is_safe_url("http://127.0.0.1/x.pdf") is False
    assert _is_safe_url("http://169.254.169.254/latest/meta-data") is False
    assert _is_safe_url("file:///etc/passwd") is False


@pytest.mark.asyncio
async def test_provider_falls_back_on_timeout_then_succeeds(monkeypatch):
    # 第 1 个 provider 超时 → 第 2 个命中；断言 order 与最终 provider
    ...
```

**Step 2：跑测试确认失败** → `ModuleNotFoundError`。

**Step 3：实现**

```python
# backend/app/tools/literature/fulltext_resolver.py
"""OA 正文抓取（Issue #73 Phase A）。

优先级：OpenAlex best_oa_location → arXiv HTML → arXiv PDF → Europe PMC JATS →
Unpaywall → CORE（需 key）。命中即止；单篇 20s / 总 60s；只接受白名单 license。
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.tools.literature.models import LiteratureRecord


class LicenseNotAllowed(RuntimeError):
    """命中的 OA 来源 license 不在白名单内（不下载、不缓存）。"""


@dataclass(slots=True)
class FullTextCandidate:
    provider: str
    source_url: str
    content: bytes
    content_format: str  # pdf / html / jats_xml
    license: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def _is_safe_url(url: str) -> bool:
    """拒绝非 http(s)、私有/回环/链路本地地址。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return True


class FullTextResolver:
    MAX_BYTES = 30 * 1024 * 1024
    MAX_REDIRECTS = 3

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._settings = get_settings().fulltext

    def ensure_license(self, license_id: str) -> str: ...

    async def resolve(self, records: list[LiteratureRecord]) -> list[tuple[LiteratureRecord, FullTextCandidate]]: ...
```

实现要点：

- `resolve()` 按 `max_concurrency` 用 `asyncio.Semaphore` 并发；外层 `asyncio.timeout(
  total_timeout_seconds)`；单 provider `asyncio.timeout(per_document_timeout_seconds)`。
- provider 函数：`_openalex_oa`（用检索返回的 `pdf_url` + 记录里携带的 `oa_license`）、
  `_arxiv_html`（`https://arxiv.org/html/{id}`）、`_arxiv_pdf`（复用
  `app.tools.literature.arxiv.download_pdf`）、`_europe_pmc`、`_unpaywall`（仅当
  `unpaywall_email` 非空）、`_core`（仅当 `core_api_key` 非空）。
- 下载校验：`follow_redirects=False` 手动跟随 ≤3 次、每个 URL 都过 `_is_safe_url`；
  `content-length` 或累计字节超 30MB 中止；content-type 与 format 不匹配即失败。
- `LicenseNotAllowed` 在 provider 内捕获并转 warning（继续下一个 provider）。
- 结果按 `sha256` 去重（同批内）。

**Step 4：跑测试确认通过** → `tests/test_fulltext_resolver.py` ≥6 passed。

**Step 5：提交**

```bash
git add backend/app/tools/literature/fulltext_resolver.py backend/tests/test_fulltext_resolver.py
git commit -m "feat(fulltext): OA 正文 resolver（provider 链/超时预算/license 白名单/SSRF 防护）"
```

---

## Task 5：`FulltextService`：缓存落库 + 用户知识库挂载 + 统计

**Files:**
- Create: `backend/app/knowledge/services/fulltext_service.py`
- Modify: `backend/app/knowledge/services/document_storage.py`（新增 `save_bytes`）
- Test: `backend/tests/test_fulltext_service.py`

**Step 1：写失败测试**

覆盖：
- 同一 DOI 二次调用命中缓存 → 不创建新 `fulltext_cache`、不重复下载（resolver 不被调用）；
- 成功挂载 → `KnowledgeDoc.meta_info` 含 `fulltext_cache_id/doi/origin="oa_auto"`，
  `DocChunk.chunk_meta["section_title"]` 与 `position["page_start"]` 有值，
  `index_status="ready"`；
- 扫描件 → 不建文档，`stats["scanned_skipped"] += 1`；
- 单篇异常不影响其它篇（`stats["failed"]` 记录）。

测试用 `httpx.MockTransport` + SQLite 内存库（沿用 `tests/conftest.py` 的 async 客户端夹具），
`KnowledgeDocumentService`/`IndexManager` 用现有 mock 或内存实现。

**Step 2：跑测试确认失败** → `ModuleNotFoundError`。

**Step 3：实现**

```python
# backend/app/knowledge/services/fulltext_service.py
"""全文获取编排（Issue #73 Phase A）：缓存 → 解析 → 挂载用户知识库 → 统计。"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True)
class FulltextStats:
    attempted: int = 0
    succeeded: int = 0
    fallback_metadata: int = 0
    scanned_skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "fallback_metadata": self.fallback_metadata,
            "scanned_skipped": self.scanned_skipped,
            "failed": self.failed,
        }


class FulltextService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        resolver: FullTextResolver,
        parser: CleanFullTextParser,
        documents: KnowledgeDocumentService,
        storage: DocumentStorage,
        paper_cards: PaperCardService,
    ) -> None: ...

    async def enrich(
        self, user_id: UUID, records: list[LiteratureRecord]
    ) -> FulltextStats: ...
```

- `DocumentStorage.save_bytes(user_id, filename, content) -> StoredDocument`：复用
  `_validate_signature` / `_validate_size` 语义；local 后端写 `upload_root`；
  S3 后端走 `put_object`（照抄 `save()` 分支）。
- `enrich()` 流程：按 DOI 查缓存 → 未命中调 `resolver.resolve` → `parser.parse_*` →
  license 闸门写入 `fulltext_cache`（`file_path` 存正文落盘路径；HTML/JATS 不落盘，
  `file_path=None`）→ `paper_cards.get_or_create(cache_row)` →
  `documents.ingest_bytes(...)`（新增轻量方法：写 `KnowledgeDoc(source="fulltext_oa")` +
  `DocChunk` + 向量，语义同 `ingest()`）→ `stats` 累加。
- 分块：每 `ParsedSection` 按 512 字符 / 64 overlap 切分（沿用
  `RAGConfig.chunk_size/chunk_overlap`），`chunk_meta={"section_title": section.title,
  "section_role": section.role}`，`position={"page_start": ..., "page_end": ...}`。
- `FulltextStats.fallback_metadata` = 请求篇数 - succeeded - scanned_skipped - failed；
  每篇失败只记 warning 日志，不抛异常。

**Step 4：跑测试确认通过** → `tests/test_fulltext_service.py` ≥4 passed。

**Step 5：提交**

```bash
git add backend/app/knowledge/services/fulltext_service.py \
  backend/app/knowledge/services/document_storage.py backend/tests/test_fulltext_service.py
git commit -m "feat(fulltext): 全文落库与用户知识库挂载（缓存去重/章节定位/统计）"
```

---

## Task 6：Paper Card 服务

**Files:**
- Create: `backend/app/knowledge/services/paper_card_service.py`
- Test: `backend/tests/test_paper_card_service.py`

**Step 1：写失败测试**

- LLM 返回合法 JSON → 字段齐全并写入 `fulltext_cache.paper_card`；
- LLM 返回非 JSON / 抛异常 / 超时 → 返回 `None`，不抛异常、不覆盖已有值；
- 已有 `paper_card` 时不调用 LLM（duck-typed fake LLM 记录 call count）。

**Step 2：跑测试确认失败** → `ModuleNotFoundError`。

**Step 3：实现**

```python
# backend/app/knowledge/services/paper_card_service.py
"""Paper Card（Issue #73）：全文首次落库时生成一次，供跨任务横向对比复用。"""

from __future__ import annotations

import json
from typing import Any

from app.llm.base import BaseLLM

PAPER_CARD_PROMPT = """你是论文信息抽取器。根据给定片段输出严格 JSON：
{"research_question": str, "method": str, "dataset_or_sample": str,
 "key_results": str, "limitations": str}
只输出 JSON，不要 Markdown 代码块；信息缺失的字段填空字符串。"""


class PaperCardService:
    def __init__(self, llm: BaseLLM | None = None) -> None:
        self._llm = llm

    async def get_or_create(self, *, abstract: str, sections: list[dict[str, Any]]) -> dict[str, str] | None: ...
```

要点：输入截断 3000 字（摘要 + method/result/limitation 节前段 ≤1200 字/节）；
`max_tokens=450`；`json.loads` 失败 → `None`；异常全部捕获（`logger.warning`）。

**Step 4：跑测试确认通过** → ≥3 passed。

**Step 5：提交**

```bash
git add backend/app/knowledge/services/paper_card_service.py backend/tests/test_paper_card_service.py
git commit -m "feat(fulltext): Paper Card 轻量生成与缓存复用"
```

---

## Task 7：工作流接入（search 后抓取 + 统计 + 降级）

**Files:**
- Modify: `backend/app/orchestration/workflows/literature_review.py`
- Modify: `backend/app/orchestration/contracts.py`（`ExecutionResult.stats`，若决定持久化统计）
- Test: `backend/tests/test_literature_review_fulltext.py`

**Step 1：写失败测试**

- `task.config["full_text"]=True` 且非离线：search 节点后 resolver/parser 被调用，
  节点输出含 `stats["fulltext_summary"]`；
- 全篇失败：任务仍能完成，`stats.fulltext_summary.succeeded == 0` 且证据退回 metadata；
- 离线任务 / `reference_library` 模式：resolver 完全不被调用（mock 断言 0 次）；
- `full_text=False`：同上零调用。

**Step 2：跑测试确认失败**（预期 `KeyError: 'fulltext_summary'` 或 resolver 未被调用）。

**Step 3：实现**

在 `literature_review.py` 的 `_execute_search`（约 :634）末尾、返回前：

```python
if (
    bool(config.get("full_text", True))
    and not bool(config.get("offline_dataset"))
    and not bool(config.get("reference_library"))
    and self._fulltext_service is not None
):
    records = [LiteratureRecord.model_validate(a) for a in result.artifacts or []]
    stats = await self._fulltext_service.enrich(self._user_id(task), records)
    result.warnings.extend(stats.warnings)   # 或写 output payload
    # 统计随节点输出落库（ExecutionResult 新增 stats: dict[str, Any] = Field(default_factory=dict)）
    result.stats["fulltext_summary"] = stats.as_dict()
```

Worker/装配处（`backend/app/orchestration/services/task_execution.py` 构造 workflow 的地方）
注入 `FulltextService`（`enabled=False` 或非 OA 任务时传 `None`，保持零外呼）。

证据升级：**无需新增代码**——全文文档已挂到该用户 `knowledge_doc`，`_prepare_node` 的
`self._context_builder.build(user_id, query)` 会自然检索到；`LiteratureContextBuilder`
用 `extract_locator_metadata(position, chunk_meta)` 生成页/节 locator，
证据 `kind="excerpt"` 带 `doc_id/chunk_id`。

验证辅助：`_evidence_text` 对 excerpt 前缀拼接 `[第N页]`（可选，便于模型引用）。

**Step 4：跑测试确认通过**

- 10.0.10.2：`./run-tests.sh tests/test_literature_review_fulltext.py tests/test_literature_review.py`
  → 全绿；再跑 `tests -k "literature_review or evidence"` 回归。

**Step 5：提交**

```bash
git add backend/app/orchestration/workflows/literature_review.py \
  backend/app/orchestration/contracts.py backend/app/orchestration/services/task_execution.py \
  backend/tests/test_literature_review_fulltext.py
git commit -m "feat(fulltext): 检索节点自动抓取 OA 正文并降级为 metadata 证据"
```

---

## Task 8：端到端验收与部署

**Step 1：本地静态门禁**（本机）

```powershell
cd backend
.\.venv\Scripts\python.exe -m black --check app tests
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m mypy app
```

**Step 2：测试机全量相关回归**

```bash
ssh ylin-dev
cd ~/vp-test && git fetch origin main && git reset --hard origin/main
# 若代码未合入 main：用 git archive + overlay 方式（参见 Issue #72 流程）
./run-tests.sh tests -k "fulltext or literature_review or knowledge or reference" 
./run-tests.sh tests --no-cov      # 全量（用户要求时）
```

**Step 3：部署后端**

```powershell
$b = Join-Path $env:TEMP 'vp73.bundle'; git bundle create $b main
scp -i 'C:\Users\Lenovo\.ssh\id_ed25519_hermes' $b root@47.253.81.6:/root/vp73.bundle
```

服务器写 `deploy73.sh`：fetch `/root/vp73.bundle` refspec → `git merge --ff-only origin/main`
→ `cd /opt/veripaper/deploy/ecs && docker compose build backend && docker compose up -d backend`
→ 记录镜像 sha256；`curl -s https://api.veripaper.me/api/v1/health/ready`。

**Step 4：生产真实任务验收**

- 登录 Lingrove（口令 `/opt/veripaper/.demo-password`）→ 创建一个英文综述任务
  （topic 选 OA 密集方向，如 "RAG evaluation benchmarks"），等待 `completed`；
- 检查任务详情：证据侧栏出现「PDF 第 N 页」locator；节点 output 含
  `fulltext_summary`；`SELECT count(*) FROM fulltext_cache WHERE status='active'` > 0；
- 造失败降级：临时把 `FULLTEXT__ENABLED=false`（或断外网）跑一个小任务 → 仍
  `completed`，证据全为 metadata；
- 截图 + 操作录像/GIF 作为 Issue #73 验收证据（按 AGENTS 质量铁律内嵌）。

**Step 5：清理与收尾**

删服务器 `/root/vp73*`、`/tmp/vp73*`；测试机临时文件；`git push backup main`；
更新 Issue #73（勾选待确认项、贴验收证据）；文档更新
`docs/superpowers/plans/2026-08-09-00-execution-notes.md`。

---

## 自审清单（完成后逐项勾）

- [x] `full_text` 默认 True；离线/引用库模式零外呼（测试覆盖）
- [x] license 白名单外一律不下载、不缓存（测试覆盖）
- [x] 扫描件不入库、不建向量，warning 可读（测试覆盖）
- [x] References/Acknowledgements 被截断（测试覆盖）
- [x] 同 DOI 二次解析零下载（测试覆盖）
- [x] 外部下载 SSRF 防护（测试覆盖）
- [x] 任意外呼失败任务仍 `completed`（测试覆盖）
- [x] 证据 locator 带页码/章节（测试覆盖）
- [x] 用户私有全文跨用户隔离（Phase B 测试补齐前先保证 `knowledge_doc.user_id` 过滤）
- [x] 迁移可 upgrade/downgrade（测试机验证）
- [x] 本机静态检查、测试机定向回归、生产真实任务验收全部留证
