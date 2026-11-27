# 文献正文获取 Phase B：用户自有正文零摩擦接入 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:executing-plans`（或
> `superpowers:subagent-driven-development`）逐 Task 实施。步骤用 `- [ ]` 复选框跟踪。
> **依赖：Phase A 已完成**（`fulltext_cache`、`FulltextService`、知识库全文挂载、
> `meta_info.fulltext_cache_id/ref_id/doi/origin` 已就绪）。

**Goal:** 把用户自己的正文（上传 PDF / 剪藏附带 PDF / Zotero 附件）零摩擦关联到引用库条目，
使中文闭源文献也能以带页码 locator 的 `excerpt` 证据参与写作与核验；失败一律回落
metadata，不阻塞任务。

**Architecture:** ① `KnowledgeDocumentService.ingest()` 增加 `ref_id` 参数，按
`ref_id → 归一化 DOI → 归一化标题` 匹配当前用户 `ReferenceLibraryItem` 并写
`meta_info`；② 新增 `UserFulltextEvidenceService`：任务命中文献时按 DOI/标题查当前用户
`knowledge_doc`，命中则从 `doc_chunk` 抽取带定位的 excerpt 证据；③ 新增
`POST /api/v1/reference-library/sync-zotero`（凭据复用 `DataSourceCredential`，
`provider="zotero"`）；④ 剪藏扩展升级为「题录 + 可选附带 PDF」一次提交；⑤ 前端
`TaskCreate` 开关与 `TaskResult` 证据占比/升级入口。

**Tech Stack:** FastAPI + SQLAlchemy(async) + Pydantic v2 + httpx（Zotero/下载）+
MV3 原生 JS 扩展 + Vue3（既有 shadcn-vue/Tailwind tokens）。

**关联文档：** `docs/superpowers/specs/2026-11-26-fulltext-acquisition-spec.md`、
`docs/superpowers/plans/2026-11-27-fulltext-acquisition-phase-a-plan.md`、Issue #73。

---

## 执行约束（必须遵守）

1. 测试只在 10.0.10.2 跑（`ssh ylin-dev` → `~/vp-test` → `./run-tests.sh`）；本机只
   `black --check` / `ruff check` / `mypy app`；前端改动 `npm run build`（本机）+ 测试机
   `vitest`/`playwright`。
2. 每 Task 先红后绿、一次 Conventional Commit、精确 `git add`。
3. 合规：不上传/抓取需登录态的付费正文；Zotero 只同步用户自己的库；跨用户隔离测试必须存在。
4. 剪藏扩展只在用户主动点击时上传文件；新增权限须在 README 说明。

---

## Task B1：知识库上传 ↔ 引用库自动关联

**Files:**
- Modify: `backend/app/knowledge/services/document_service.py`（`ingest(..., ref_id=None)`）
- Create: `backend/app/knowledge/services/reference_linker.py`
- Modify: `backend/app/knowledge/api/endpoints.py`（上传端点透传可选 `ref_id` 表单字段）
- Test: `backend/tests/test_knowledge_reference_linker.py`

**Step 1：写失败测试**

- 显式 `ref_id="doi:10.1234/abc"` → `KnowledgeDoc.meta_info["ref_id"]` 写回且
  `meta_info["doi"] == "10.1234/abc"`；
- 无 `ref_id` 但 DOI 归一化匹配 → 自动关联；
- 标题匹配仅在归一化标题长度 ≥20 且唯一时生效；歧义（2 条同标题）不关联；
- 跨用户：用户 B 的同 DOI 条目不会被用户 A 的上传关联。

**Step 2：跑测试确认失败** → `TypeError: ingest() got an unexpected keyword argument 'ref_id'`。

**Step 3：实现**

```python
# backend/app/knowledge/services/reference_linker.py
"""上传文档 ↔ 引用库条目匹配（Issue #73 Phase B）。"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.reference_library import ReferenceLibraryItem

_DOI_PREFIX_RE = re.compile(r"^doi:", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def normalize_doi(raw: str | None) -> str | None:
    if not raw:
        return None
    value = _DOI_PREFIX_RE.sub("", raw.strip()).lower()
    return value or None


def normalize_title(raw: str | None) -> str | None:
    if not raw:
        return None
    value = _PUNCT_RE.sub("", raw).lower()
    return value if len(value) >= 20 else None


class ReferenceLinker:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def match(
        self, user_id: UUID, *, ref_id: str | None, doi: str | None, title: str | None
    ) -> ReferenceLibraryItem | None: ...
```

匹配顺序：`ref_id` 精确 → DOI（归一化，`where user_id == user_id`）→ 标题
（归一化后内存比较，且候选唯一）。命中后由 `document_service.ingest()` 写入
`meta_info = {**原 meta, "ref_id": item.ref_id, "doi": item.doi or doi}`。

上传端点：`backend/app/knowledge/api/endpoints.py` 的 `POST /knowledge/documents`
新增可选 `Form` 字段 `ref_id: str | None = None`，传给 `ingest(..., ref_id=ref_id)`。

**Step 4：跑测试确认通过** → `tests/test_knowledge_reference_linker.py` 全绿；
回归 `tests -k "knowledge or document"`。

**Step 5：提交**

```bash
git add backend/app/knowledge/services/reference_linker.py \
  backend/app/knowledge/services/document_service.py backend/app/knowledge/api/endpoints.py \
  backend/tests/test_knowledge_reference_linker.py
git commit -m "feat(fulltext): 知识库上传按 ref_id/DOI/标题自动关联引用库条目"
```

---

## Task B2：用户全文证据升级（引用库/检索命中 → excerpt）

**Files:**
- Create: `backend/app/orchestration/services/user_fulltext_evidence.py`
- Modify: `backend/app/orchestration/workflows/literature_review.py`
  （`_metadata_evidence_from` / `_prepare_node` 并入）
- Test: `backend/tests/test_user_fulltext_evidence.py`

**Step 1：写失败测试**

- 用户已有 `knowledge_doc`（`meta_info.doi="10.1234/abc"`，`index_status="ready"`，
  chunks 带 `position.page_start` 与 `chunk_meta.section_title`）→ 命中文献产出
  `Evidence(kind="excerpt", doc_id, chunk_id, excerpt, locator.page_start=...)`；
- 无匹配全文 → 返回空列表（调用方继续用 metadata）；
- 跨用户：用户 B 的同 DOI 文档不返回；
- `index_status != "ready"` 的文档不返回。

**Step 2：跑测试确认失败** → `ModuleNotFoundError`。

**Step 3：实现**

```python
# backend/app/orchestration/services/user_fulltext_evidence.py
"""把用户已关联的全文升级为 excerpt 证据（Issue #73 Phase B）。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge import DocChunk, KnowledgeDoc
from app.orchestration.contracts import Evidence
from app.tools.literature.models import LiteratureRecord


class UserFulltextEvidenceService:
    def __init__(self, db: AsyncSession, *, max_chunks_per_paper: int = 2) -> None: ...

    async def evidence_for(
        self, user_id: UUID, records: list[LiteratureRecord]
    ) -> list[Evidence]: ...
```

实现：按记录 DOI（优先）/标题归一化 → 查 `KnowledgeDoc`（`user_id` + `index_status
== "ready"`，`func.json_extract` 或读 `meta_info` 内存过滤，兼容 SQLite/Postgres）→
取 `DocChunk`（按 `chunk_index`，每篇 ≤2 条，优先 `section_role in
{"result","method"}`）→ 组 `Evidence(excerpt=content[:2000], locator=
extract_locator_metadata(position, chunk_meta))`。

接入 `literature_review.py`：

- `_prepare_node` 组装 `prepared["evidence"]` 时，先调 `UserFulltextEvidenceService`
  拿 excerpt 证据并去重（按 `(doc_id, chunk_id)`），再加 metadata 证据；
- `reference_library=True` 的检索节点同样生效（引用库条目也能升级全文）。

**Step 4：跑测试确认通过** → `tests/test_user_fulltext_evidence.py` 全绿；
回归 `tests -k "literature_review or evidence or reference_library"`。

**Step 5：提交**

```bash
git add backend/app/orchestration/services/user_fulltext_evidence.py \
  backend/app/orchestration/workflows/literature_review.py \
  backend/tests/test_user_fulltext_evidence.py
git commit -m "feat(fulltext): 用户已关联全文升级为带页码 locator 的 excerpt 证据"
```

---

## Task B3：Zotero Web API 附件同步

**Files:**
- Create: `backend/app/tools/literature/zotero_client.py`
- Create: `backend/app/tools/literature/zotero_sync.py`（归一化 + 入库编排）
- Modify: `backend/app/api/v1/endpoints/reference_library.py`（新增 sync-zotero 端点）
- Modify: `backend/app/api/v1/endpoints/data_source_credentials.py`（允许 `provider="zotero"`）
- Test: `backend/tests/test_zotero_sync.py`

**Step 1：写失败测试**（`httpx.MockTransport` 模拟 Zotero API，不联网）

- `GET /users/{id}/items` → 归一化为 `CslItem` 并写入 `reference_library_item`（幂等：
  同 DOI 已存在则跳过）；
- attachment 条目（`contentType=application/pdf`）→ 下载并调用统一入库
  （`origin="zotero"`、`ref_id` 绑定）；文件 >30MB 跳过并返回 `skipped`；
- 凭据缺失 → 404/400 可读错误；凭据解密失败 → 401 可读错误；
- 跨用户：用户 A 的 Zotero 条目不会出现在用户 B 的库中。

**Step 2：跑测试确认失败** → 端点 404 / `ModuleNotFoundError`。

**Step 3：实现**

- `zotero_client.ZoteroClient`：`base_url="https://api.zotero.org"`，
  头 `Zotero-API-Version: 3` + `Zotero-API-Key`（**从 `DataSourceCredential` 解密**，
  不落日志）；`fetch_items(library_type, library_id, collection_key, limit)`、
  `download_attachment(...)`（流式、≤30MB）。
- `zotero_sync.ZoteroSyncService`：解密凭据 → 拉条目 → 归一化（`CslItem`：
  itemType/creators/date/title/DOI/abstractNote）→ 复用既有
  `reference_library_service.save_items()`（按 DOI/标题幂等）→ 附件走
  `KnowledgeDocumentService.ingest()`（`ref_id` 绑定）。
- 端点：

```python
class ZoteroSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection_key: str | None = None
    library_type: Literal["user", "group"] = "user"
    limit: int = Field(default=50, ge=1, le=100)


@router.post("/sync-zotero", response_model=ZoteroSyncOut)
async def sync_zotero(payload: ZoteroSyncRequest, user: ApiUserDep, db: DbDep):
    """拉取 Zotero 条目与 PDF 附件并关联到引用库/知识库。"""
```

返回 `{imported, skipped, attachments, failed}`。

**Step 4：跑测试确认通过** → `tests/test_zotero_sync.py` 全绿；回归引用库相关测试。

**Step 5：提交**

```bash
git add backend/app/tools/literature/zotero_client.py \
  backend/app/tools/literature/zotero_sync.py \
  backend/app/api/v1/endpoints/reference_library.py \
  backend/app/api/v1/endpoints/data_source_credentials.py backend/tests/test_zotero_sync.py
git commit -m "feat(fulltext): Zotero Web API 条目与 PDF 附件同步"
```

---

## Task B4：剪藏扩展「题录 + 附带 PDF」一次提交

**Files:**
- Modify: `extensions/veripaper-clipper/content.js`（开放 HTML 正文提取）
- Modify: `extensions/veripaper-clipper/popup.html` / `popup.js`（附带 PDF 选择区）
- Modify: `extensions/veripaper-clipper/background.js`（两次 POST：题录 + 文档）
- Modify: `extensions/veripaper-clipper/README.md`
- Modify: `extensions/veripaper-clipper/test/verify-extension.mjs`
- Create: `extensions/veripaper-clipper/test/fixture-open-access.html`

**Step 1：先改冒烟脚本（红）**

在 `verify-extension.mjs` 增加场景：
- fixture 开放页（无 `citation_*` 限制）→ `VERIPAPER_EXTRACT` 返回
  `item.abstract` 或 `source.abstract_only=false`（含正文文本标记）；
- popup 选择 PDF fixture 后点击保存 → mock 收到
  `POST /api/v1/reference-library`（题录）与 `POST /api/v1/knowledge/documents`
  （multipart：`ref_id` + `file`）；
- 未选择 PDF 时行为与旧版一致（只提题录）。

**Step 2：跑测试确认失败**

- 测试机：`PLAYWRIGHT_ROOT=~/vp-test/frontend xvfb-run -a /tmp/node22/bin/node \
  extensions/veripaper-clipper/test/verify-extension.mjs` → 新场景 FAIL。

**Step 3：实现**

- `content.js`：新增 `extractOpenAccessBody()`：`<article>/<main>` 内 `p` 文本拼接
  （≤20000 字），返回 `source.extracted_text`；仅当页面无 `citation_*` 也仍可提题录
  （标题取 `document.title`/`h1`）。
- `popup.html`：新增 `<input type="file" id="attach-pdf" accept=".pdf">` 与
  「附带刚下载的 PDF（可选）」文案；`.caj` 选择时提示转换。
- `popup.js`：保存时若选了 PDF：
  1) `POST {apiBase}/api/v1/reference-library`（JSON，X-API-Key）；
  2) `FormData` 追加 `file` + `ref_id`（取返回条目 ref_id 或本地 slug id），
     `POST {apiBase}/api/v1/knowledge/documents`（X-API-Key）；
  3) 结果合并展示「新增题录 N / 全文已关联」。
- `background.js`：把保存流程迁移到 SW 统一发请求（保持 `X-API-Key`，不存 Key 到日志）。
- 权限不新增（`activeTab` + host_permissions 已含 veripaper.me；复用到 `/api/v1/...`）。

**Step 4：打包 + 冒烟**

```bash
node extensions/veripaper-clipper/scripts/package.mjs   # 生成 release zip
# 测试机跑 verify-extension.mjs → RESULT=PASS
```

README 增加「附带 PDF 上传」与「.caj 请转 PDF」说明，版本号 1.1.0。

**Step 5：提交**

```bash
git add extensions/veripaper-clipper extensions/veripaper-clipper/README.md
git commit -m "feat(clipper): 剪藏支持附带 PDF 上传并自动关联引用库"
```

---

## Task B5：前端（开关 / 证据占比 / 升级入口 / .caj 提示）

**Files:**
- Modify: `frontend/src/components/tasks/TaskCreateForm.vue`（`full_text` 开关）
- Modify: `frontend/src/components/tasks/TaskResult.vue`（全文证据占比 + 上传升级按钮）
- Modify: `frontend/src/components/knowledge/**`（上传 `.caj` 提示，按现有上传组件）
- Modify: `frontend/src/stores/tasks.ts` / `frontend/src/api/tasks.ts`（字段透传）
- Test: `frontend/src/components/tasks/__tests__/TaskResult.fulltext.test.ts`

**Step 1：写失败测试（Vitest）**

- TaskCreateForm：`full_text` 开关默认开；离线/引用库模式禁用且显示原因；
- TaskResult：给定 `fulltext_summary` → 显示「全文证据 M 篇 / 仅题录摘要 K 篇」；
- 仅题录条目显示「上传 PDF 升级全文核验」按钮；上传成功后调用
  `knowledgeApi.upload(..., ref_id)` 并刷新；
- `.caj` 文件选择 → 提示「请先转为 PDF」。

**Step 2：跑测试确认失败**（测试机 `~/vp-test/frontend`：`./node_modules/.bin/vitest run <path>`）。

**Step 3：实现**（沿用 shadcn-vue 组件与 Nova tokens；交互模式先查 beUI 同类组件）

- `TaskCreateForm.vue`：`Switch`（`components/ui/switch`）绑定 `form.full_text`；
  `offline_dataset || reference_library` 时 `disabled` + 说明文案。
- `TaskResult.vue`：从 `task.review_report`/节点 stats 读 `fulltext_summary`；
  统计卡 + 每条仅题录文献旁「上传 PDF 升级全文核验」→ `input type=file`
  （accept `.pdf`）→ `POST /knowledge/documents`（multipart，带 `ref_id`）→ 成功 toast + 刷新。
- `.caj`：知识库上传组件文件过滤与提示。
- 样式只用既有 tokens（`bg-card`/`text-muted-foreground` 等），不引入新颜色。

**Step 4：验证**

- 本机 `npm run build`（vue-tsc）通过；
- 测试机：`vitest run src/components/tasks` 全绿；Playwright 用例（若涉及）双视口通过。

**Step 5：提交**

```bash
git add frontend/src/components/tasks frontend/src/components/knowledge \
  frontend/src/stores/tasks.ts frontend/src/api/tasks.ts \
  frontend/src/components/tasks/__tests__/TaskResult.fulltext.test.ts
git commit -m "feat(frontend): 任务全文开关、证据占比与上传 PDF 升级入口"
```

---

## Task B6：端到端验收与部署

**Step 1：后端门禁**

- 本机：`black --check app tests`、`ruff check app tests`、`mypy app`；
- 测试机：`./run-tests.sh tests -k "fulltext or zotero or reference_library or knowledge"`，
  再跑全量（若用户要求）。

**Step 2：部署后端**

按 Phase A Task 8 Step 3 的 bundle 流程部署；`/health/ready` 200；记录镜像 sha256。

**Step 3：部署前端**

```powershell
$env:CF_API_TOKEN = (Get-Content 'C:\Users\Lenovo\cf-secrets.env' | ...)  # 按现有脚本
node scripts/deploy-pages.mjs
```

按仓库既有 `scripts/deploy-pages.mjs` 流程（凭据 `C:/Users/Lenovo/cf-secrets.env`）。

**Step 4：真机验收（Issue #73 验收证据）**

1. 知网/维普详情页 → 剪藏题录 → 附带本地 PDF → 保存成功；
2. 引用库页出现条目，知识库页出现文档且 `meta_info` 已关联；
3. 创建中文综述任务（reference_library 或在线检索）→ completed；
4. 任务详情：该中文文献证据为 excerpt，论断核验抽屉显示「PDF 第 N 页 / 章节」；
5. 截图 + 操作录像/GIF 内嵌 Issue #73 评论，回读 `body_html` 校验，再关闭/更新 Issue。

**Step 5：清理与收尾**

删除服务器/测试机临时脚本与包；`git push backup main`；更新
`docs/superpowers/plans/2026-08-09-00-execution-notes.md` 与 Issue #73 勾选项。

---

## 自审清单（完成后逐项勾）

- [x] 上传按 ref_id/DOI/标题关联，歧义不自动关联（测试覆盖）
- [x] 跨用户隔离（关联、检索、证据、Zotero）全部有测试
- [x] `.caj` 前端明确提示；后端仍只收 `.pdf/.docx/.md/.txt`
- [x] Zotero 凭据加密存储、不落日志；单文件 ≤30MB；失败可跳过
- [x] 剪藏扩展无新增高危权限；不抓付费正文；测试 fixture 覆盖
- [x] `full_text` 开关与 TaskResult 统计/升级入口前端测试全绿
- [ ] 真机知网/维普验收证据（截图 + 录屏/GIF）已内嵌 Issue #73
  - 未跑：真实站点需要用户账号，尚未提供；已用本地 fixture 自动化冒烟
    （`verify-extension.mjs`，50 项 PASS）与生产 API/UI 验收替代，边界已在 Issue #73 注明。
- [x] 本机静态检查、测试机定向回归、生产真机验收全部留证

---

## 实施记录（2026-11-27 完成）

**提交链（main）**

- B1 `7152d42`：知识库上传按 `ref_id`/DOI/标题自动关联引用库条目
- B2 `f895256`：用户已关联全文升级为带页码 locator 的 excerpt 证据；
  期间测试对齐 `a8c2e40`（缺 `user_id` 时既有 `HumanActionRequiredError` 语义）
- B3 `3fed161`：Zotero Web API 条目与 PDF 附件同步
- B4 `fb48c16`：剪藏扩展支持附带 PDF 上传并自动关联引用库；
  打包 `97f76be`（`release/veripaper-clipper-1.1.0.zip`，17,915 bytes）
- 额外修复：`a69d2f5` 知识库上传端点支持 X-API-Key + `knowledge:write` scope；
  `373ceab` 命名 scope 依赖并更新测试；`7b218b4` `ApiKeyScope` 放行 `knowledge:write`
- B5 `efa6d1b`：任务全文开关、证据占比与上传 PDF 升级入口

**门禁**

- 测试机：B1 49 passed、B2 67 passed、B3 82 passed、knowledge 补丁 62 passed、
  B6 定向 17 文件 **240 passed**；前端 Vitest **149 passed**（5 文件）
- 剪藏扩展：fixture 冒烟 50 项 **RESULT=PASS**（含 multipart PDF + ref_id）
- 本机：black/ruff/mypy 全绿

**部署与生产验收**

- 后端镜像 `sha256:295ae8edbbb8b22f1c6c6a6b7fdd505da4977615197cf577a3e03ec93540ef96`
  （HEAD `7b218b4`），`/health/ready` 200
- 前端 Cloudflare Pages 部署成功，别名 `https://2d613f3e.multi-agent-assistant.pages.dev`
- API 验收（容器内脚本）：API Key 上传 PDF（带 `ref_id`）201 → 任务
  `4301978d-f117-46a6-9599-9ac4a4dec5b8` completed；bibliography 命中上传文献，
  excerpt 2 条，locator `{page_start:1,page_end:3,section_title:"1 引言"}`
- UI 验收（Playwright 双证据）：全文证据占比「1 篇 / 7 篇」、上传 PDF 升级入口、
  全文开关、API Key 页均可见；`PAGE_ERRORS=[]`
- 证据：Issue #73 评论 https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/73#issuecomment-7218492480
  （5 截图 + GIF 内嵌，回读 6 张图渲染）；Issue #73 已关闭（completed）
- 清理：测试 API Key / 2 个测试知识库文档 / 测试引用库条目已删除；
  `knowledge_doc=14`、`fulltext_cache=1`（Phase A OA 缓存保留）、`reference_library_item=2`

**遗留边界**

- 真机知网/维普剪藏验收需用户账号，未跑（本地 fixture + 生产 API/UI 替代）
- Zotero 真实账号未联调（MockTransport 覆盖协议与错误码）
- `POST /reference-library/sync-zotero` 采用 `ApiUserDep`（JWT 或 X-API-Key），
  与规范 §6.3 的「JWT only」不一致，已在 Issue #73 评论注明

