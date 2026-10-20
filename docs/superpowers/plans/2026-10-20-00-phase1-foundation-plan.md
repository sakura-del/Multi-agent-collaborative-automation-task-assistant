# Phase 1 · 打地基 实施计划（从「能演示」到「能交付」）

> 上游：[产品路线图](../product/2026-10-18-demo-to-product-roadmap.md) §5 Phase 1
> 节奏：AGENTS.md §3（先写失败测试 → 实现 → 全量门禁 → 提交 → 同步服务器 → 文档 → 推送备份）

---

## 0. 清单校正（先对账，避免重复劳动）

用户给的 Phase 1 清单里有两行状态**已过期**，先澄清：

| 清单项 | 真实状态 |
| --- | --- |
| ✅ 引用真实性校验 | **已完成**（`42b5e54` 后端 + `c270348` 前端展示） |
| 🟡 CSL 引擎 + GB/T 7714—2025（M2 待做） | **已 100% 完成**，不止 M1：M2 `f0123d4`（样式实体/上传 API/前端选择器）、著者-出版年制 `deefaaa`、剩余三项 `a986785`（medium/拼音排序/姓名归一）。**无需再做** |

所以 Phase 1 **真正待做的是 5 项**：

| # | 功能 | 优先级 | 依赖 |
| --- | --- | --- | --- |
| A | Word 模板导入 + 一键切换 | P0 | CSL 引擎 ✅ |
| B | 检索源扩展（OpenAlex + S2 + PubMed） | P0 | 无 |
| C | 账号体系（邮箱验证/密码重置） | P1 | 无 |
| D | 速率限制 + 按用户配额 | P1 | 无 |
| E | RIS/BibTeX **导入**（导出已完成） | P2 | 无 |

五项互不依赖（A 依赖的 CSL 已就绪），可并行。

---

## 1. 冻结接口

### 1.1 A · Word 模板（`app/tools/templates/`）

```python
# models.py
class PlaceholderField(BaseModel):          # extra="forbid"
    key: str                                 # docxtpl 变量名（{{ key }}）
    label: str = ""                           # UI 展示名
    required: bool = True
    sample: str = ""

class StyleRole(str, Enum):                   # 语义角色（style_map 的值）
    TITLE = "title"; H1 = "h1"; H2 = "h2"; H3 = "h3"
    BODY = "body"; CAPTION = "caption"; REFERENCE = "reference"

class TemplateMeta(BaseModel):                # extra="forbid"
    template_id: str                          # 由文件内容 sha256 派生
    name: str
    placeholders: list[PlaceholderField]
    style_map: dict[str, StyleRole]           # Word styleId → 语义角色
    checksum: str                             # 源 docx sha256

# inspector.py
def inspect_template(path: Path) -> TemplateMeta
    """扫描 .docx：用 docxtpl 的 get_undeclared_template_variables() 抽占位符，
    并从 styles.xml 抽 styleId → 推断 style_map（按 styleId，**不按本地化 name**，坑 #3）。
    拒绝 .docm / 宏 / 外部关系（坑 #7）。"""

# renderer.py
def render_document(*, template: Path, sections: dict[str, str],
                    bibliography: Sequence[str], style_map: dict[str, StyleRole]) -> bytes
    """把综述正文按 style_map 落到模板：标题/正文/参考文献各用对应样式；
    参考文献块由调用方传入**已 CSL 渲染好的行**（本模块不碰引用引擎）。"""
```

**安全约束（坑 #7）**：只接受 `.docx`；拒绝含 `vbaProject.bin` 的包；
剥离 `w:attachedTemplate`；限制解压总大小（防 zip 炸弹）。

### 1.2 B · 检索源扩展（`app/tools/literature/`）

统一到既有 `LiteratureRecord`，新增源实现同一 Protocol：

```python
class LiteratureSource(Protocol):
    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]: ...
    async def aclose(self) -> None: ...

# 新增
class OpenAlexSearchSource   # 复用 OpenAlexClient 的 HTTP 层，新增 works 检索端点
class SemanticScholarSource  # api.semanticscholar.org/graph/v1/paper/search
class PubMedSource           # eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch+efetch

# 聚合与去重（去重键优先级：DOI → source_id → 归一化题名）
class MultiSourceLiteratureSource:
    def __init__(self, sources: Sequence[LiteratureSource], *, per_source_limit: int = 10)
```

`resolve_literature_source()` 扩展：任务 config 的 `literature_sources: list[str] | None`
（`None` → 默认 `["arxiv","openalex"]`），离线任务仍走冻结清单（优先级不变）。

**限流（路线图 §6）**：arXiv ≥3s/请求、PubMed 3 req/s、OpenAlex 带 `mailto`、
S2 免费档约 1 req/s。全部经既有 `_wait` 式节流 + 缓存，**不得并发轰炸**。

### 1.3 C · 账号体系

```python
# User 新增字段（Alembic 迁移）
email_verified_at: datetime | None = None
# 新表 email_token（或复用 token 表）：purpose=verify|reset, token_hash, expires_at, used_at
```

端点：
```
POST /api/v1/auth/verify-email/request   重发验证邮件
POST /api/v1/auth/verify-email/confirm   {token}
POST /api/v1/auth/password-reset/request {email}   （**恒定响应**，不泄漏账号是否存在）
POST /api/v1/auth/password-reset/confirm {token, new_password}
```

邮件发送走可替换的 `EmailSender` 协议（测试注入 fake；未配置 SMTP 时只记日志，
**不阻塞注册**——保持现有演示可用）。

### 1.4 D · 速率限制 + 配额

```python
# app/core/rate_limit.py
class RateLimiter(Protocol):
    async def hit(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]: ...

class RedisRateLimiter   # Redis INCR+EXPIRE；不可用时**放行**（fail-open，避免拖垮服务）
class InMemoryRateLimiter  # 测试/单机

# 配额
class QuotaService:
    async def check_and_consume(self, user_id: UUID, *, tokens: int) -> None
    """超配额 → 429（带 Retry-After / 剩余额度）。按日扣减，存 Redis。"""
```

中间件按路由打标（登录/注册/建任务/上传/搜索/核验），各自独立限额。

### 1.5 E · RIS/BibTeX 导入

```python
# app/tools/csl/importers.py
def parse_bibtex(text: str) -> list[CslItem]
def parse_ris(text: str) -> list[CslItem]
```
与既有 `exporters.to_bibtex/to_ris` **互逆**（round-trip 测试锁定）。
端点：`POST /api/v1/references/import`（multipart 或 JSON body，返回 `list[CslItem]` 预览，不落库）。

---

## 2. 任务拆分与写作用域（互不重叠）

| 任务 | 内容 | 写作用域 |
| --- | --- | --- |
| **P1-A** Word 模板 | `app/tools/templates/{__init__,models,inspector,renderer}.py` + `tests/test_template_*.py` + pyproject 依赖 | 这些文件 |
| **P1-B** 检索源 | `app/tools/literature/{openalex_search,semantic_scholar,pubmed}.py`、`source_registry.py` + `tests/test_literature_sources*.py` | 这些文件 |
| **P1-C** 账号 | `app/knowledge/...` 之外的 `app/api/v1/endpoints/auth_email.py`、`app/services/email_sender.py`、`app/db/models/email_token.py`、迁移、`tests/test_auth_lifecycle.py` | 这些文件 |
| **P1-D** 限流配额 | `app/core/rate_limit.py`、`app/core/quota.py`、`tests/test_rate_limit*.py` | 这些文件 |
| **P1-E** 导入 | `app/tools/csl/importers.py`、`app/api/v1/endpoints/references.py` + `tests/test_csl_importers.py` | 这些文件 |
| **Lead** | 各模块接线（`main.py`/`router.py`/`task_execution.py`）、契约、门禁、提交、部署、文档 | 见下 |

**Lead 独占**：`app/main.py`、`app/api/v1/router.py`、`app/orchestration/contracts.py`、
`app/orchestration/services/literature_source.py`、`app/core/config.py`、`config/default.yaml`。

---

## 3. 验收标准

- [ ] A：能上传 `.docx` 模板 → 吐出占位符清单 + style_map；能用模板渲染出成稿（参考文献按 CSL 结果填充）；`.docm`/含宏/zip 炸弹被拒
- [ ] B：一次任务可同时检索 ≥2 个源并去重；任一源故障不影响其他源；限流生效（不并发轰炸）
- [ ] C：注册后可收到验证邮件（可注入 fake）；密码重置**不泄漏账号是否存在**；未配 SMTP 时注册不被阻塞
- [ ] D：超限返回 429 且带 Retry-After；Redis 故障时 fail-open（不拖垮服务）
- [ ] E：BibTeX/RIS 导入 → 与导出的 round-trip 一致；非法输入给明确错误
- [ ] 全量门禁：black/ruff/mypy/pytest（≥80% cov）+ 前端 + CI 6 作业全绿

---

## 4. 门禁命令

```powershell
cd backend
.\.venv\Scripts\python.exe -m black app tests evaluation
.\.venv\Scripts\python.exe -m ruff check --fix app tests evaluation
.\.venv\Scripts\python.exe -m mypy app evaluation
.\.venv\Scripts\python.exe -m pytest tests -q --cov=app --cov-fail-under=80 --basetemp="$env:TEMP\pytest-dsh"
```
