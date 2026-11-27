# 文献正文获取设计（Phase A：OA 自动正文 + Phase B：用户自有正文接入）

> 日期：2026-11-26（需求方技术规范），2026-11-27 落盘
> 状态：设计已确认，待实施（实施计划见 `docs/superpowers/plans/2026-11-27-fulltext-acquisition-phase-a-plan.md` 与 `...-phase-b-plan.md`）
> 关联 Issue：[#73](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/73)
> 关联借鉴：Issue #35（论断溯源到表/节/页，已关闭）、#36（Consensus 式抽取表，已关闭）

## 1. 目标

打破「只拿到题录/摘要」的瓶颈，把核心参考文献证据从 `kind="metadata"` 升级为
带页码/章节定位的 `kind="excerpt"`：

- **Phase A — OA 自动正文**：检索命中的 OA 文献在重排（相关性 + 撤稿过滤 + OA 优先）
  后自动抓取 Top 8 正文，去掉 References、按双栏/章节干净解析，生成全局复用的
  Paper Card；证据自动升级为 excerpt。
- **Phase B — 用户自有正文零摩擦接入**：打通「引用库 ↔ 知识库 PDF」自动关联、
  剪藏扩展附带 PDF 上传、Zotero Web API 附件同步，覆盖知网/维普/万方等中文闭源文献。
- **绝对容错**：任何外部抓取、解析或扫描件失败都降级为 metadata 证据 + 可读
  warning，绝不阻塞主任务、绝不编造正文。

验收判据（与 Issue #73 一致）：

1. 线上跑 1 个英文综述任务，报告中出现「PDF 第 N 页」级 locator，且节点 output
   `stats["fulltext_summary"]` 计数自洽（attempted = succeeded + fallback_metadata +
   scanned_skipped）。
2. 外呼全部失败（断网/429/扫描件）时任务仍 `completed`，证据退回 metadata，
   warning 可读。
3. 中文文献经「上传 PDF / 剪藏附带 PDF / Zotero 同步」关联后，以 excerpt 形态参与
   写作，论断核验抽屉能定位到原文页码。

## 2. 红线与明确不做

| 红线 | 落实方式 |
| --- | --- |
| 严禁爬取 CNKI / 维普 / 万方 / Google Scholar 付费正文 | 代码只对 OA provider 发起下载；禁止用登录态 Cookie 抓中文付费站点 |
| 严禁接入 Sci-Hub 或盗版镜像 | 域名白名单 + 单测断言；文档写明 |
| `fulltext_cache` 仅存 OA（license 白名单）且可下架 | 入库前 license 校验；`status=taken_down` 后不再被新任务引用 |
| 用户自有正文仅归属该 `user_id` | 挂载到该用户 `knowledge_doc`；检索按 `user_id` 过滤 |
| 首期不做 OCR | 扫描件检测到 `scanned_no_text_layer` 直接降级，不入向量库 |
| 不阻塞主任务 | 所有 external 调用有超时；失败写 warning 并回落 metadata |

## 3. 现有能力与复用依据

**直接复用，不重写：**

- `backend/app/tools/literature/source_registry.py`：检索源注册（arxiv / openalex /
  semantic_scholar / pubmed / wanfang），`LiteratureRecord` 已有 `pdf_url`；
  `backend/app/tools/literature/openalex_search.py:274-289` 已读取
  `best_oa_location.pdf_url`（但未保留 license，需在 Phase A 补字段）。
- `backend/app/tools/literature/arxiv.py::download_pdf()`（`ALLOWED_DOWNLOAD_HOSTS`
  已只允许 `arxiv.org` / `export.arxiv.org`），Phase A 二是它的第一个真实调用方。
- PyMuPDF（`fitz`）已在 `backend/pyproject.toml`（`PyMuPDF>=1.23.26`）——
  PDF 解析不需要新增依赖；**没有** beautifulsoup4/lxml，arXiv HTML 用标准库
  `html.parser` 实现（若确需 bs4，必须同 commit 改 pyproject 并补测试）。
- `backend/app/knowledge/services/document_parser.py`：PDF 按页切分（`第 N 页`）、
  DOCX 按标题样式——新解析器**不复用**它（学术双栏重排、去参考文献是不同职责），
  但返回结构对齐 `sections` 语义，便于 `document_service.ingest` 复用分块管线。
- `backend/app/knowledge/services/document_storage.py`：受控存储
  （`ALLOWED = {.pdf, .docx, .md, .markdown, .txt}`、签名/大小校验、
  `save()` / `localize()` / `presigned_url()`）。
- `backend/app/knowledge/services/document_service.py:117-208` `ingest()`：
  存储 → `KnowledgeDoc(indexing)` → 解析/分块 → 写双索引 → `index_status=ready`。
  Phase A 入库复用这条管线；用户在 Phase B 上传 PDF 也走同一条。
- `backend/app/db/models/knowledge.py`：`KnowledgeDoc.meta_info`（列名 `metadata`
  JSON）与 `DocChunk`（`chunk_index`、`position`、`chunk_meta`）——Phase B 的
  `{fulltext_cache_id, ref_id, doi, origin}` 直接写 `meta_info`，**不改表结构**。
- `backend/app/db/models/reference_library.py::ReferenceLibraryItem`：
  `ref_id`、`title`、`doi`、`payload`（CslItem JSON）、`user_id` —— Phase B 自动关联
  与 Zotero 同步的写入目标。
- `backend/app/db/models/data_source_credential.py`：`user_id + provider` 唯一、
  `api_key_ciphertext`（AES-256-GCM）、`key_mask`、`status` —— Zotero 凭据直接复用
  （`provider="zotero"`，密文里放 `{"zotero_user_id": "...", "zotero_api_key": "..."}`）。
- `backend/app/core/config.py`：`Settings` 的嵌套配置模式（如
  `ReferenceVerificationConfig`）——照抄新增 `FulltextConfig`。
- `backend/app/orchestration/workflows/literature_review.py`：
  `_execute_search`（:634）、`_prepare_node` 证据组装（:403-442）、
  `_metadata_evidence_from`（:482）、`_evidence_text`（:516）——接入点。
- `backend/app/orchestration/contracts.py:230` 已有 `evidence_scope:
  Literal["full_text","abstract_only"]`，Phase A 的 `full_text` 开关可对齐此语义，
  但按规范新增明确字段 `full_text: bool = True` 到两个请求契约。

## 4. 总体架构与数据流

```
检索命中（6 源）→ 重排（相关性 + 非撤稿 + 有 OA 链接）→ Top N（默认 8）
   ↓
FullTextResolver（串行 provider 尝试；单篇 20s，总预算 60s，Semaphore(4)）
   ↓ 命中 + license 白名单
CleanFullTextParser（双栏重排 / 去页眉页脚 / 扫描检测 / 截 References / section_role）
   ↓
双层存储
   ├─ 全局 OA 缓存 fulltext_cache（按 DOI/arXiv/PMID/sha256 去重，含 parsed_sections + paper_card）
   └─ 用户私有挂载 knowledge_doc + doc_chunk + 向量（meta_info 记 fulltext_cache_id/origin）
   ↓
消费
   ├─ Executor：paper_card 汇总（横向对比）+ section_role 加权检索 excerpt（locator 页码/章节）
   └─ Validator/Exporter：locator 透传 + 「全文证据 M 篇 / 仅题录摘要 K 篇」统计
```

## 5. Phase A 设计

### 5.1 数据模型 `fulltext_cache`

新增 `backend/app/db/models/fulltext_cache.py`：

| 列 | 类型 | 约束/说明 |
| --- | --- | --- |
| `id` | UUID PK | default uuid4 |
| `doi` | String(256) | unique, nullable, index（归一化小写 DOI） |
| `arxiv_id` | String(64) | unique, nullable, index |
| `pmid` | String(32) | unique, nullable, index |
| `sha256` | String(64) | unique, not null（正文文件内容哈希，防重复下载） |
| `provider` | String(32) | `openalex_oa / arxiv_html / arxiv_pdf / europe_pmc / unpaywall / core` |
| `source_url` | String(1024) | 实际下载/解析来源 |
| `license` | String(64) | 归一化后的 license 标识（白名单内） |
| `content_format` | String(16) | `pdf / html / jats_xml` |
| `file_path` | String(512) | 正文文件存储路径（可为空：HTML 不落盘） |
| `page_count` | Integer | 默认 0 |
| `parsed_sections` | JSON | `[{title, role, page_start, page_end, text}]` |
| `paper_card` | JSON | `{research_question, method, dataset_or_sample, key_results, limitations}` |
| `status` | String(16) | `active / taken_down`，index |
| `created_at` | DateTime(timezone=True) | server default now() |

Alembic：新增可回滚迁移 `add_fulltext_cache`，`down_revision = "d8b2f5c3a1e9"`
（当前 head，`d8b2f5c3a1e9_add_task_draft_table.py`）；revision id ≤ 32 字符；
`downgrade()` 必须 `op.drop_table("fulltext_cache")`。

用户侧不改 `knowledge_doc` 表；`meta_info` 写：

```json
{
  "original_name": "paper.pdf",
  "fulltext_cache_id": "…",
  "ref_id": "doi:10.xxxx/…",
  "doi": "10.xxxx/…",
  "origin": "oa_auto | user_upload | clipper | zotero"
}
```

### 5.2 配置契约

`backend/app/core/config.py` 新增：

```python
class FulltextConfig(BaseModel):
    enabled: bool = True
    max_documents_per_task: int = Field(default=8, ge=1, le=20)
    per_document_timeout_seconds: float = Field(default=20.0, gt=0)
    total_timeout_seconds: float = Field(default=60.0, gt=0)
    max_concurrency: int = Field(default=4, ge=1, le=8)
    unpaywall_email: str = ""      # FULLTEXT__UNPAYWALL_EMAIL
    core_api_key: str = ""         # FULLTEXT__CORE_API_KEY（空=不启用 CORE）
    license_whitelist: list[str] = Field(default_factory=lambda: [...])
```

挂到 `Settings`：`fulltext: FulltextConfig = FulltextConfig()`。

**License 白名单（归一化小写后比较）**：`cc-by`、`cc-by-4.0`、`cc-by-3.0`、
`cc-by-sa`、`cc-by-nc`、`cc0`、`public-domain`、`arxiv`、`pmc-oa`、`elsevier-oa`。
OpenAlex 实际返回的 license 串（如 `cc-by`、`publisher-specific-oa`）需实现前抽样
核对并在白名单里显式补充；不在白名单 → 不下载、不缓存、warning 记录。

任务契约：`TaskCreate`（`backend/app/schemas/task.py:44`）与
`LiteratureReviewRequest`（`backend/app/orchestration/contracts.py:331`）各加：

```python
full_text: bool = True
"""是否自动抓取 OA 正文（Issue #73）。离线任务 / reference_library 模式自动跳过。"""
```

### 5.3 `FullTextResolver`

新增 `backend/app/tools/literature/fulltext_resolver.py`：

```python
class FullTextProvider(Protocol):
    name: str
    def supports(self, record: LiteratureRecord) -> bool: ...
    async def fetch(self, record: LiteratureRecord, timeout: float) -> FullTextCandidate | None: ...
```

- `FullTextCandidate`：`source_url`、`content: bytes`、`content_format`、
  `license`、`provider`、`record`。
- 顺序：OpenAlexOA → ArxivHtml → ArxivPdf → EuropePMC → Unpaywall → CORE（仅配置 key）。
- 命中即止；单篇 20s（`asyncio.timeout`）、总 60s（外层 budget）；
  `Semaphore(max_concurrency)` 控制并发，默认 4。
- 去重：先按 DOI 查 `fulltext_cache`（命中直接复用，**零外呼**），再按
  `sha256` 查重。
- 下载安全：只允许 `http/https`；拒绝私有/回环地址（`ipaddress` 解析后校验）；
  跟随重定向 ≤ 3 次且最终 URL 仍需通过校验；响应体上限 30MB（`AppConfig.max_upload_bytes`）；
  `content-type` 不匹配（HTML 当 PDF 等）即失败。
- 超时/429/5xx/解析失败：记录 warning，回落下一 provider。

### 5.4 `CleanFullTextParser`

新增 `backend/app/knowledge/services/fulltext_parser.py`：

```python
class ParseStatus(StrEnum):
    OK = "ok"
    SCANNED_NO_TEXT_LAYER = "scanned_no_text_layer"
    EMPTY = "empty"
    FAILED = "failed"

class ParsedSection(BaseModel):
    title: str
    role: Literal["background", "method", "result", "limitation", "other"]
    page_start: int
    page_end: int
    text: str
```

四项核心处理（PDF 用 PyMuPDF）：

1. **双栏坐标重排**：`page.get_text("blocks")` 得到 `(x0, y0, x1, y1, text)`；
   过滤 `y1 < page_height*0.08`（页眉）与 `y0 > page_height*0.92`（页脚）；
   若左半区（`x1 < page_width*0.52`）与右半区（`x0 > page_width*0.48`）文本块
   各占 ≥25%，先按 `y0` 排左栏、再排右栏；否则按 `(y0, x0)` 单栏排序。
2. **扫描件密度检测**：每页有效字符（去空白）数；`< 50` 的页占比 ≥70% →
   `SCANNED_NO_TEXT_LAYER`，不入库、不建向量，warning：「该 PDF 为扫描件无文本层，
   请上传可复制文本版」。
3. **截断末尾参考文献**：仅在后半段（≥50% 文本位置）匹配独立标题行
   `^References$|^Bibliography$|^参考文献$|^Acknowledg(e)?ments$|^致谢$`，
   截断其后全部内容。
4. **章节角色**：标题关键词确定性映射（introduction/background/related work/引言/背景
   → background；method/approach/architecture/方法/模型/设计 → method；
   experiment/result/evaluation/实验/结果 → result；
   limitation/discussion/future/局限/讨论/展望 → limitation；其余 other）。

HTML（arXiv HTML / PMC）用标准库 `html.parser` 提取 `<section>/<h1..h6>/<p>/<table>`
文本块，复用同一套 section_role 映射；JATS XML 同理按 `<sec>/<title>/<p>` 解析。

### 5.5 Paper Card

`backend/app/knowledge/services/paper_card_service.py`：仅当
`fulltext_cache.paper_card IS NULL` 且 `fulltext_cache` 首次落库时调用一次轻量
LLM（`max_tokens=450`，输入摘要 + method/result/limitation 前段 ≤ 3000 字），
产出严格 JSON；失败/超时/非法 JSON → `None`（不抛异常、不阻塞任务）。
后续同 DOI 任务直接复用。

### 5.6 工作流接入

`backend/app/orchestration/workflows/literature_review.py`：

- `_execute_search` 成功后：若 `task.config.get("full_text", True)` 且非离线且非
  `reference_library`：对 records 重排（相关性 + 非撤稿 + 有 OA 链接）取
  `max_documents_per_task`，并发走 resolver → parser → 入库；把 `stats["fulltext_summary"]`
  （attempted/succeeded/fallback_metadata/scanned_skipped）写入节点 output。
- 入库：为每篇成功文献创建该用户的 `KnowledgeDoc`（`source="fulltext_oa"`，
  `meta_info` 见 §5.1）+ 分块 + 向量（复用 `IndexManager.prepare_document` 语义或
  直接由 parser sections 分块）；`DocChunk.chunk_meta["section_role"]`、`position`
  带 `page_start/page_end`。
- `_prepare_node`/`_metadata_evidence_from`：对已有全文的文献生成 excerpt 证据
  （`kind="excerpt"`、`doc_id`、`chunk_id`、`doc_title`、`excerpt`、`locator` 带页码/章节），
  metadata 证据仍保留作兜底；`_evidence_text` 拼接 `[页码/章节]` 前缀。
- 失败兜底：resolver/parser 全失败 → 与现状完全一致（metadata 证据 + warning）。

## 6. Phase B 设计

### 6.1 引用库 ↔ 知识库自动关联

`backend/app/knowledge/services/document_service.py::ingest()` 增加可选参数
`ref_id: str | None = None`；入库后按优先级匹配当前用户 `ReferenceLibraryItem`：

1. 显式 `ref_id`；
2. 归一化 DOI（`doi:10.xxxx/…` → `10.xxxx/…`，小写）；
3. 归一化标题（小写、去标点空白、长度 ≥ 20 字符才启用，避免误配）。

命中 → `KnowledgeDoc.meta_info` 写 `{ref_id, doi}`；未命中 → 仅记录 `origin`，
后续可由用户在 TaskResult 手动关联（不自动猜测）。

### 6.2 用户全文证据升级

任务检索命中某文献时（外部检索结果或引用库条目），先查当前用户 `knowledge_doc`
是否已有 `meta_info.ref_id` / `meta_info.doi` 对应的 ready 文档：

- 有 → 证据 `kind="excerpt"`（带页码 locator）；
- 无 → 回落摘要/题录（`kind="metadata"`）。

跨用户隔离：查询必须带 `user_id`（沿用 `KnowledgeDoc.user_id` 过滤）。

### 6.3 Zotero Web API 附件同步

- 凭据：`DataSourceCredential(provider="zotero")`，密文 JSON
  `{"zotero_user_id": "...", "zotero_api_key": "..."}`（复用
  `DataSourceCredentialService` 的加密/脱敏逻辑）。
- 端点：`POST /api/v1/reference-library/sync-zotero`，body
  `{collection_key?: str, library_type?: "user"|"group", limit?: int}`；JWT only。
- 流程：调 Zotero Web API `GET /users/{id}/items`（或 `/groups/{id}/items`）
  → 归一化为 `CslItem` 写入 `reference_library_item`（复用既有保存逻辑、按 DOI/标题幂等）
  → 对 `itemType=attachment` 且 `contentType=application/pdf` 的条目调
  `GET /users/{id}/items/{itemKey}/file` 下载（单文件 ≤30MB）→ 走统一入库管线
  （origin=`zotero`，写 `ref_id`）。
- 限流/失败：单次同步最多 100 条；单文件失败只跳过该文件并返回 `skipped` 列表。

### 6.4 剪藏扩展升级（稳健优先）

`extensions/veripaper-clipper/`：

- 开放 HTML 页：`content.js` 提取正文文本（同一套 section 规则可选），随
  `VERIPAPER_SAVE` 一起发送；`background.js` 调 `POST /api/v1/reference-library`
  后再调 `POST /api/v1/knowledge/documents`（multipart，带 `ref_id`）。
- 闭源付费页：用户在官网点「PDF 下载」后，在 popup 的「附带刚下载的 PDF」区域
  选择/拖放文件，一次操作完成题录 + PDF 上传 + `ref_id` 关联；不上传 Cookie、
  不跨域抓取付费 PDF。
- 权限：仅新增 `content_scripts` 匹配与既有 `activeTab`，**不新增** `cookies` /
  跨域 host 权限。

### 6.5 前端展示

- `TaskCreateForm`：`full_text` 开关（默认开；离线/引用库模式禁用并说明原因）。
- `TaskResult`：统计卡显示「全文证据 M 篇 / 仅题录摘要 K 篇」；仅题录条目显示
  「上传 PDF 升级全文核验」按钮；`.caj` 选择时提示转换 PDF。
- 论断核验抽屉：excerpt 证据显示「PDF 第 N 页 / 章节名」并可复制定位。

## 7. 错误处理与降级矩阵

| 场景 | 行为 |
| --- | --- |
| provider 超时/429/5xx | 尝试下一个 provider；全失败 → metadata + warning |
| license 不在白名单 | 跳过该来源（不下载），尝试下一个；全失败同上 |
| 扫描件（无文本层） | `scanned_no_text_layer`，不建向量，warning 提示上传可复制版 |
| PDF 解析异常 / 空文本 | `failed`/`empty`，回落下一 provider 或 metadata |
| Paper Card LLM 失败 | `paper_card=None`，不重试、不阻塞 |
| 知识库入库失败（向量/DB） | 记录 warning，该篇回落 metadata，其余篇不受影响 |
| Zotero API 401/429 | 返回可读 401/429；不删除已有条目 |
| 全局超时（60s） | 已完成入库的保留；未完成篇目降级 metadata，`stats` 记录 attempts |

## 8. 安全与合规

- **SSRF 防护**：所有外部下载 URL 只允许 `http/https`，拒绝私有/回环/元数据地址，
  重定向 ≤3 次并二次校验，响应体上限 30MB。
- **License 闸门**：白名单常量集中在 `FulltextConfig`，单测覆盖「非白名单拒绝下载」。
- **用户隔离**：用户私有全文只挂在该用户 `knowledge_doc`；检索/证据查询强制带
  `user_id`；Zotero 凭据按用户加密存储。
- **下架**：`fulltext_cache.status="taken_down"` 后 resolver 不再命中、任务不再引用
  （已挂载 knowledge_doc 可软下架，不影响历史任务可读性）。
- **不做 OCR**；不新增 cookies 权限；不访问需要登录态的付费站点。

## 9. 测试与验收

**Phase A 单测**

- `backend/tests/test_fulltext_resolver.py`：provider 命中顺序；license 非白名单拒绝；
  429/超时降级；单篇/总超时熔断；同 DOI 二次零外呼；SSRF 拦截（`127.0.0.1`、`169.254.*`）；
  重定向超限拒绝。
- `backend/tests/test_fulltext_parser.py`：双栏 PDF 不串行；页眉页脚裁切；
  References/Acknowledgements 截断；扫描件阈值 → `SCANNED_NO_TEXT_LAYER`；
  `section_role` 映射；HTML/JATS 解析。
- `backend/tests/test_literature_review_fulltext.py`：全失败仍 completed（metadata +
  warning）；成功时 `ReviewReport` 产出带页码 locator 的 excerpt 与 `fulltext_summary`。

**Phase B 单测**

- 上传 PDF 按 `ref_id`/DOI/标题自动关联 `ReferenceLibraryItem`；
- 跨用户 `knowledge_doc` 严格隔离（用户 A 全文不进入用户 B 证据）；
- Zotero API mock（条目 + PDF 附件）同步并关联 `ref_id`；单文件失败跳过；
- `.caj` 提示文案与前端过滤。

**门禁**：测试只在 `10.0.10.2` 跑（`ssh ylin-dev`）；本机只跑
`black --check` / `ruff check` / `mypy app` / `npm run build`（前端改动时）。
定向：`./run-tests.sh tests/test_fulltext_resolver.py tests/test_fulltext_parser.py
tests/test_literature_review_fulltext.py`；回归：`tests -k "fulltext or literature_review
or reference_library or knowledge"`。

**真机验收**：线上英文综述 1 个（抽查页码 locator 与 `fulltext_summary`）；
知网/维普详情页剪藏附带 PDF → 中文综述任务 → 中文文献以 excerpt 参与写作并可在
抽屉定位原文页码。

## 10. 未决问题（实施前确认）

1. OpenAlex `license` 实际取值抽样与白名单补齐（实现前跑 20 条真实记录核对）。
2. Unpaywall 的 `email` 默认值：建议复用 `reference_verification.contact_email`，未配置时
   `fulltext.enabled=false` 并 warning，避免匿名调用被拒。
3. CORE 是否本期启用（需免费 key）；未配置即跳过。
4. 用户私有全文是否参与跨任务向量检索（建议：仅该用户自己的任务可检索）。
5. `taken_down` 后已挂载 `knowledge_doc` 的处置（建议：软下架，保留历史可读）。
6. Phase B 的 Zotero 是否本期交付，或拆独立 Issue（建议：Phase A 先上线，Zotero 随
   Phase B 第二批）。

## 11. 关联文档

- Issue #73（方案评审与补充评论）
- `docs/superpowers/plans/2026-11-27-fulltext-acquisition-phase-a-plan.md`
- `docs/superpowers/plans/2026-11-27-fulltext-acquisition-phase-b-plan.md`
- 引用真实性核验：`docs/superpowers/plans/2026-10-18-03-reference-authenticity-plan.md`
- 引用库与证据闭环：`docs/superpowers/plans/2026-10-26-01-reference-library-plan.md`
