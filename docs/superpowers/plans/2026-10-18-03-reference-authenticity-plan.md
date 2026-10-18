# Issue #3 引用真实性校验（CrossRef/OpenAlex）实施计划

> 状态：执行中（2026-10-18 起）
> 上游：GitHub Issue #3（P0）；路线图 `docs/product/2026-10-18-demo-to-product-roadmap.md` §2.4 缺口排序第 1 位
> 节奏：严格按 AGENTS.md §3 —— **先写失败测试（红）→ 实现（绿）→ 全量门禁 → 提交 → 同步服务器 → 文档 → 推送备份**

---

## 1. 目标

把现有「内部证据可追溯」（`[E{n}]` → `doc_id` + `chunk_id`）扩展到「**外部文献可核验**」：
对每条参考文献走 CrossRef / OpenAlex 元数据核验，输出四态结论（三态判定 + 一态降级），
并把结论并入 `ValidationReport.checks`、导出报告统计与可选拦截。

| 结果 | 判定条件 |
| --- | --- |
| ✅ `verified` | DOI 或标题可解析，且标题/作者/年份与外部元数据吻合 |
| ⚠️ `metadata_mismatch` | 文献存在，但标题相似度低于阈值，或作者/年份偏差超阈值，或 DOI 无效 |
| ❌ `not_found` | DOI 不存在（或无效），**且**标题检索在外部源无命中 |
| ⏸ `unverified` | 外部源超时/限流/不可用 → 降级态，**不得判失败、不得阻塞任务**（避免误伤） |

**版权约束**：只取元数据，不取全文。

---

## 2. 冻结接口（Lead 制定；实现方不得改名/改语义，需变更先在任务板提）

所有共享契约已由 Lead 落在 `backend/app/tools/literature/models.py`（本节末尾列出）。
**测试不产生真实网络请求**：适配器测试用 `httpx.MockTransport`，核验器测试注入 fake source。

### 2.1 `app/tools/literature/crossref.py`（负责人：adapters）

```python
class CrossrefClient:
    BASE_URL = "https://api.crossref.org"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,   # 注入即复用，不负责关闭
        mailto: str = "",                           # 礼貌池联系方式，进 User-Agent
        user_agent: str = "maa-literature-review/0.1",
        timeout: httpx.Timeout | None = None,
        max_retries: int = 1,                       # 429 / Retry-After 重试次数
    ) -> None: ...

    @property
    def client(self) -> httpx.AsyncClient: ...      # 惰性创建
    async def aclose(self) -> None: ...             # 只关自己创建的

    async def lookup_doi(self, doi: str) -> MetadataRecord | None:
        """GET /works/{doi}；404 → None；其余 4xx → UpstreamError。"""

    async def search_by_title(
        self, title: str, *, rows: int = 3, author: str | None = None
    ) -> list[MetadataRecord]:
        """GET /works?query.bibliographic=...；无命中 → []（不是异常）。"""
```

错误分类（与 `arxiv.py` 一致）：超时 / 连接失败 / 5xx / 429 重试耗尽 → `UpstreamTemporaryError`；
非 404 的 4xx → `UpstreamError`；响应不是合法 JSON 或缺必需字段 → `ValidationException`。
礼貌调用：请求头 `User-Agent: {user_agent} (mailto:{mailto})`（mailto 为空时只发 user_agent）。

### 2.2 `app/tools/literature/openalex.py`（负责人：adapters）

```python
class OpenAlexClient:
    BASE_URL = "https://api.openalex.org"

    def __init__(self, *, client=None, mailto: str = "", user_agent=..., timeout=None, max_retries: int = 1) -> None: ...

    async def lookup_doi(self, doi: str) -> MetadataRecord | None:
        """GET /works/https://doi.org/{doi}；404 → None。"""

    async def search_by_title(self, title: str, *, per_page: int = 3) -> list[MetadataRecord]: ...
```

`mailto` 同时作为查询参数进入 polite pool；`MetadataRecord.cited_by_count` / `is_open_access` 由 OpenAlex 填充。
两客户端的 DOI 归一化必须一致：小写、去 `https://doi.org/` 前缀、去首尾空白。

### 2.3 `app/knowledge/services/reference_verifier.py`（负责人：verifier）

```python
DEFAULT_SIMILARITY_THRESHOLD = 0.90

class MetadataSource(Protocol):
    async def lookup_doi(self, doi: str) -> MetadataRecord | None: ...
    async def search_by_title(self, title: str, *, rows_or_per_page...) -> list[MetadataRecord]: ...

class ReferenceVerificationCache:
    """Redis 优先、内存回退；键为 `refverify:{reference_key}`。

    接口为 **async**（修订 R2，2026-10-18）：项目 Redis 只有 `redis.asyncio` 客户端
    （`app/core/redis.py::RedisManager.get_client()`），同步接口无法消费 awaitable，
    会导致「Redis 优先」名存实亡。核验器本身是 async，await 无额外成本。
    """
    def __init__(self, *, redis_client: Any | None = None, ttl: int = 604800) -> None: ...
    async def get(self, key: str) -> ReferenceVerification | None: ...
    async def set(self, key: str, verification: ReferenceVerification) -> None: ...

def normalize_title(title: str) -> str: ...
def title_similarity(left: str, right: str) -> float: ...   # 0.0–1.0，基于 difflib.SequenceMatcher

def candidates_from_artifacts(artifacts: Sequence[dict[str, Any]]) -> list[ReferenceCandidate]: ...
def candidates_from_bibliography(entries: Sequence[dict[str, Any]]) -> list[ReferenceCandidate]: ...

def blocked_keys(verifications: Sequence[ReferenceVerification]) -> set[str]:
    """verdict ∈ {METADATA_MISMATCH, NOT_FOUND} 的 reference_key。"""

def summarize_verifications(
    verifications: Sequence[ReferenceVerification], *, dropped: int = 0, intercepted: bool = False
) -> ReferenceVerificationSummary: ...

class ReferenceVerifier:
    def __init__(
        self,
        *,
        crossref: MetadataSource | None = None,
        openalex: MetadataSource | None = None,
        cache: ReferenceVerificationCache | None = None,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        year_tolerance: int = 1,
        max_concurrency: int = 4,
        intercept_unverified: bool = False,
    ) -> None: ...

    @property
    def intercept_unverified(self) -> bool: ...

    async def verify(self, candidate: ReferenceCandidate) -> ReferenceVerification: ...
    async def verify_many(self, candidates: Sequence[ReferenceCandidate]) -> list[ReferenceVerification]:
        """去重（按 key）→ 缓存查询 → 有界并发（Semaphore）→ 保持入参顺序。"""
```

**判定算法（`verify` 唯一权威口径）**

1. 缓存命中 → 直接返回（`cached=True`），**不得外呼**。
2. 有 DOI：`crossref.lookup_doi` → 命中则比对；`None` 则 `openalex.lookup_doi` → 命中则比对。
3. DOI 全部落空（或本来无 DOI）：标题检索 CrossRef → OpenAlex，取相似度最高者。
4. 比对规则：
   - 标题：`title_similarity(candidate.title, record.title) >= threshold`；
   - 年份：`|candidate.year - record.year| <= year_tolerance`（任一侧缺失则跳过）；
   - 作者：双方作者非空时，姓氏/姓名 token 集合必须相交（任一侧为空则跳过）。
   - **判定映射（修订 R1，2026-10-18）——以 DOI 是否解析成功为分支**：

     | 情形 | 结论 |
     | --- | --- |
     | DOI 解析成功（`doi_status=resolved`）且标题/年份/作者全通过 | `VERIFIED` |
     | DOI 解析成功，但标题相似度 < 阈值 / 年份或作者不符 | `METADATA_MISMATCH` |
     | 无 DOI（或 DOI 落空），标题命中且相似度达标、年份作者全通过 | `VERIFIED` |
     | 无 DOI（或 DOI 落空），标题命中相似度达标但年份或作者不符 | `METADATA_MISMATCH` |
     | DOI 落空（所有源都明确返回 None）但标题命中且元数据吻合 | `METADATA_MISMATCH`，reason 说明「DOI 无效」 |
     | 无 DOI（或 DOI 落空），标题检索无命中 **或** 最佳相似度 < 阈值 | `NOT_FOUND` |
     | 所有可用源均故障（无法判定） | `UNVERIFIED` |

     **修订理由**：Issue #3 原文表格定义「⚠️ 元数据不符 = **DOI 存在**，但标题/作者/年份偏差超阈值」，
     验收标准含「故意改错标题 → 判为元数据不符」。若 DOI 已解析成功却因标题不符判 `NOT_FOUND`，
     会把「文献存在但我们记录错了」误报为「查无此文」，且不满足验收项。故
     **标题相似度不足只有在 DOI 未解析成功时才判 `NOT_FOUND`**。
5. 任一源抛 `UpstreamTemporaryError`：继续尝试其余源；**所有可用源均失败** → `UNVERIFIED`（reason 记录原因）。
   `verify` 永不向上抛网络类异常。

**标题归一化**：`NFKC` → 去 HTML 标签 → 转小写 → 去标点/全角符号 → 折叠空白（中英混排必须可用）。

### 2.4 `app/knowledge/services/citation_validator.py`（负责人：Lead）

```python
REFERENCE_CHECK = "reference_authenticity"
ReferenceCheckMode = Literal["off", "report", "enforce"]

class CitationValidator:
    def __init__(
        self, *, duplicate_ratio_threshold=..., ownership_violation=False,
        reference_mode: ReferenceCheckMode = "off",
    ) -> None: ...

    def validate(
        self, result, required_sections=None, *,
        evidence_pool=None,
        reference_verifications: Sequence[ReferenceVerification] | None = None,
    ) -> ValidationReport: ...

    @staticmethod
    def check_reference_authenticity(
        verifications: Sequence[ReferenceVerification], *, advisory: bool
    ) -> ValidationCheck: ...
```

- `reference_mode="off"` **或** `reference_verifications is None` → 不追加该检查（现有报告形状完全不变）。
- `"report"` → 检查带 `advisory=True`：**不影响** `passed` / `retryable` / `score`（默认模式，避免误伤）。
- `"enforce"` → 检查非 advisory：未找到/元数据不符 → `passed=False`，`retryable=True`。
- `unverified` 永不导致检查失败（降级态），只在 message 中计数。
- message 形如：`引用核验：已验证 2 / 元数据不符 1 / 未找到 1 / 未核验 0；问题条目：...`（问题条目最多列 3 条）。

`ValidationCheck` 新增 `advisory: bool = False`（`contracts.py`，Lead 落地）。
`ValidationReport.passed` / `score` 只统计 **非 advisory** 检查：`passed = all(非 advisory 检查通过)`，
`score = 100 * 通过的非 advisory 检查数 / 非 advisory 检查总数`。

### 2.5 配置（负责人：wiring）

`app/core/config.py` 新增 `ReferenceVerificationConfig` 并挂到 `Settings.reference_verification`；
`config/default.yaml` 增加同名段（与字段一一对应，中文注释）：

```yaml
reference_verification:
  enabled: true
  mode: "report"              # off / report / enforce
  intercept_unverified: false # 开启时未通过核验的条目剔出参考文献列表
  similarity_threshold: 0.90
  year_tolerance: 1
  cache_ttl_seconds: 604800
  request_timeout_seconds: 20.0
  max_concurrency: 4
  contact_email: ""           # 礼貌池联系方式（建议进 .env，不进代码）
  user_agent: "maa-literature-review/0.1 (+https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant)"
  max_retries: 1
```

### 2.6 编排接线（负责人：wiring / report）

- `RuntimeContainer` 惰性属性 `reference_verifier -> ReferenceVerifier | None`：`enabled=False` 返回 `None`；
  Redis 可用时注入 `ReferenceVerificationCache`；容器关闭时一并 `aclose()` 两个客户端。
- `task_execution._literature_execution_workflow`：`CitationValidator(reference_mode=<mode or off>)`，
  并把 `reference_verifier` 传给 `LiteratureReviewWorkflow`。
- `LiteratureReviewWorkflow(..., reference_verifier=None)`；`validate_node` 中当 verifier 存在且
  `result.artifacts` 可解析出候选时：`verify_many` → 以 `reference_verifications` 传入校验 Agent 输入。
- `ReviewerAgent(llm=..., reference_verifier: ReferenceVerifier | None = None)`：
  组装 bibliography 前核验候选 → 写 `stats["reference_verification"]`（`ReferenceVerificationSummary.model_dump()`）、
  给 bibliography 条目加 `verification_status`；`verifier.intercept_unverified=True` 时剔除被拦截条目并计 `dropped`。
- `LiteratureExporter`：`stats["reference_verification"]` 存在且正文没有 `## 引用核验` 时追加该章节
  （已验证 N / 元数据不符 K / 未找到 M / 未核验 U / 已拦截 D），参考文献行加核验标记；Markdown 与 DOCX 同一路径。

### 2.7 共享契约（Lead 已落地于 `app/tools/literature/models.py`）

`MetadataRecord` / `ReferenceCandidate` / `ReferenceVerdict` / `ReferenceVerification` / `ReferenceVerificationSummary`。
字段与语义以该文件为准，**禁止**实现方各自新增并行类型。

---

## 2.8 真实 API 探针结论（2026-10-18 本机实跑，用作判定规则依据）

本机能直连 `api.crossref.org` 与 `api.openalex.org`（无需代理），因此「真实 DOI」验收可在本机跑：

| 探针 | 结果 |
| --- | --- |
| CrossRef `GET /works/10.2196/80371` | **200**，标题 `Influence of Topic Familiarity and Prompt Specificity on Citation Fabrication...`，作者 `['Linardon','Jarman','McClure']`，year 2025 |
| CrossRef `GET /works/10.9999/definitely-not-real-xyz` | **404** `Resource not found.` |
| CrossRef `GET /works?query.bibliographic=<真实标题>&rows=3` | **200**，命中真实记录 + **重复预印本条目**（`10.2196/preprints.80371`）+ 一条无关结果 → **必须靠相似度阈值挑选，不能取首条** |
| OpenAlex `GET /works/https://doi.org/10.2196/80371` | **200**，year 2025，`cited_by_count` 37，`is_oa` true（gold） |
| OpenAlex `GET /works/https://doi.org/10.9999/...` | **404**，返回体是 **HTML**（`<!doctype html>`）而非 JSON → 404 必须在解析前短路为 `None` |
| OpenAlex `GET /works?search=<真实标题>` | **200**，命中真实记录，且能召回 **arXiv 预印本（DOI `10.48550/arxiv.*`）** → arXiv 来源的本地文献走标题检索可核验 |

**判定规则由此确认**：标题检索必须按相似度取最优并设阈值；OpenAlex 是 arXiv 语料的主要核验通道。

### 2.8.1 冻结 demo 语料实测（10 条，OpenAlex 标题检索，本机实跑）

| 结果 | 条数 | 说明 |
| --- | --- | --- |
| 标题相似度 = 1.000（命中） | 6 | 年份漂移 ≤ 1 年（2023→2024、2024→2025），落在默认 `year_tolerance=1` 内 |
| 最佳相似度 0.38–0.48（未命中） | 3 | 新近 arXiv 预印本，OpenAlex `search` / `filter=title.search` / CrossRef 三路均无收录 |
| 最佳相似度 0.475（命中错条目） | 1 | 同上，属未收录 |

**结论**：① 标题相似度阈值 0.90 能正确区分「命中」与「未收录」；② 语料存在 30% 的
「外部源尚未收录」条目 —— 这类 `not_found` 是**诚实结论**而非误判，因此
**默认必须用 `mode=report`（advisory）而不是 `enforce`**，否则会阻断正常任务；
③ 年份漂移在本语料内 ≤ 1 年，默认容差可用（`Attention Is All You Need` 出现 8 年漂移属上游
记录异常，故标题检索路径的年份判定保留容差而非严格相等）。

---

## 2.9 集成实跑发现的缺陷与裁决 R3–R7（2026-10-18 全量门禁红）

全量 `pytest tests` 从 152s 变为 **>25 分钟不结束**，`tests/e2e/test_literature_review_e2e.py` 3 项全部超时失败
（547s ≈ 3 × 180s 轮询上限）。**计时探针实测根因**：

| # | 实测现象 | 后果 |
| --- | --- | --- |
| 1 | OpenAlex 对每次标题检索返回 **429 + `Retry-After: 60`**，适配器按礼貌约定**在任务路径里 sleep 60s** | 每任务从秒级变成分钟级 |
| 2 | 本机 `redis://redis:6379/0` 不可解析，缓存每次操作 **2.7–5s** 连接超时后才回退内存 | 10 候选 ≈ 20 次缓存操作 ≈ 1–2 分钟 |
| 3 | 离线演示任务（`offline_dataset=True`）也走外部核验 | e2e「不访问网络」契约被破坏；离线演示不再确定性 |

**裁决（实现方按此改，先写失败测试）**

- **R3（429 快速降级）**：上游 `Retry-After` **超过 2 秒**时**不得在任务路径内睡眠等待**，
  直接将该源视为不可用（`UpstreamTemporaryError`）→ 核验降级为 `UNVERIFIED`；
  仅当 `Retry-After ≤ 2s` 才做短暂重试。理由：核验是任务的**旁路能力**，
  不能为了等上游冷却把用户任务挂住；429 是「稍后再来」，不是「现在必须等」。
- **R4（核验总预算）**：配置新增 `total_timeout_seconds`（默认 **20.0**）。
  单节点核验整体超预算 → 全部候选降级为 `UNVERIFIED`（写审计），**不阻塞任务、不抛异常**。
- **R5（缓存快速失败）**：核验缓存必须避免「每次操作都等连接超时」：
  ①`RuntimeContainer` 为核验缓存构造**专用** Redis 客户端（`socket_connect_timeout=1.0`、`socket_timeout=1.0`），
  不复用 `RedisManager` 的全局单例（实时推送需要正常超时）；
  ②`ReferenceVerificationCache` 增加**连续失败熔断**：连续 ≥2 次失败后本进程内不再尝试 Redis，
  直接走内存缓存（避免每个 key 都付一次超时代价）。
- **R6（离线任务不核验）**：配置新增 `verify_offline_tasks`（默认 **false**）。
  `ManifestLiteratureSource` 离线任务默认**不做外部核验**（`reference_verifier=None`）：
  冻结语料已人工核验，离线演示要零外呼、可复现；需要展示核验时显式打开该开关。
- **R7（默认并发降到 2）**：`max_concurrency` 默认 **2**（原 4）。未配置 `contact_email` 时处于
  OpenAlex 公共池（约 1 req/s），4 并发必然触发 429；配好 `contact_email` 进入 polite pool 后可调高。
  ⚠️ **注意**：`contact_email` 目前只能改 `config/default.yaml` —— 配置系统存在已知缺陷
  （YAML 作为 init 参数优先级最高，环境变量无法覆盖，见 `execution-notes` 的「配置优先级缺陷」），
  写 `REFERENCE_VERIFICATION__CONTACT_EMAIL` 环境变量**实测不生效**（本机已复现）。
  该缺陷修复后本字段应改回 `.env` 注入。

**回归要求**：修完后 `pytest tests` 必须回到分钟级以内（基线 152s）且 `tests/e2e` 全绿；
`test_reference_live.py`（`MAA_REFERENCE_LIVE=1`）仍全绿。

---

## 3. 任务拆分与写作用域（互不重叠）

| 任务 | 负责人 | 写作用域 | 依赖 |
| --- | --- | --- | --- |
| T0 契约冻结 | lead | `app/tools/literature/models.py`、`app/orchestration/contracts.py` | — |
| T1 适配器 | adapters | `app/tools/literature/crossref.py`、`openalex.py`、`tests/test_crossref_client.py`、`tests/test_openalex_client.py`、`tests/fixtures/{crossref,openalex}_*.json` | T0 |
| T2 核验器 | verifier | `app/knowledge/services/reference_verifier.py`、`tests/test_reference_verifier.py` | T0 |
| T3 校验检查 | lead | `app/knowledge/services/citation_validator.py`、`tests/test_citation_reference_check.py` | T0、T2 |
| T4 配置与接线 | wiring | `app/core/config.py`、`config/default.yaml`、`app/orchestration/runtime.py`、`app/orchestration/services/task_execution.py`、`app/orchestration/workflows/literature_review.py`、`tests/test_reference_config.py` | T1、T2 |
| T5 报告统计与拦截 | report | `app/orchestration/agents/reviewer.py`、`app/tools/literature/exporter.py`、`tests/test_reviewer_references.py`、`tests/test_exporter_references.py` | T2 |
| T6 全量门禁 + 提交 + 服务器验证 | lead | `docs/superpowers/plans/2026-08-09-00-execution-notes.md`、路线图 | 全部 |

---

## 4. 验收标准映射（Issue #3 原文 → 验证方式）

| 验收项 | 验证方式 |
| --- | --- |
| 三态判定 + 超时/429 降级（无真实网络） | `tests/test_reference_verifier.py`（fake source + MockTransport） |
| 真实 DOI `10.2196/80371` 判「已验证」 | 服务器实跑（本机无法访问外网模型端点，CrossRef 走服务器） |
| 伪造 DOI 判「未找到」 | 单测（fake）+ 服务器实跑 |
| 故意改错标题判「元数据不符」 | 单测（fake） |
| 导出报告展示统计 | `tests/test_exporter_references.py` + 服务器实跑产物 |
| 「拦截未验证引用」开关生效且可审计 | `tests/test_reviewer_references.py` |
| 缓存命中不重复外呼（有断言） | `tests/test_reference_verifier.py` |
| black / ruff / mypy / 覆盖率门禁 | §5 四条命令 |

---

## 5. 门禁命令（缺一不可，AGENTS.md §3）

```powershell
cd backend
.\.venv\Scripts\python.exe -m black app tests evaluation
.\.venv\Scripts\python.exe -m ruff check --fix app tests evaluation
.\.venv\Scripts\python.exe -m mypy app evaluation
.\.venv\Scripts\python.exe -m pytest tests -q --cov=app --cov-fail-under=80 --basetemp="$env:TEMP\pytest-dsh"
```

铁律提醒：**绝不用 PowerShell 做 UTF-8 文本往返**（改文件一律用 `edit`/`write` 工具或 Python 脚本）。
