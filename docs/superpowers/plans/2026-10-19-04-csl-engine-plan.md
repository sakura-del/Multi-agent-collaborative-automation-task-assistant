# Issue #4 M1 实施计划：确定性 CSL 渲染核心 + GB/T 7714—2025

> 上游：[Issue #4](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/4)（P0）
> 依赖已解除：[Issue #5 → ADR 0001](../adr/0001-csl-bilingual-strategy.md)（**Option C：标准 CSL 1.0.2 + 逐条 locale 切换**）
> 节奏：AGENTS.md §3（先写失败测试 → 实现 → 全量门禁 → 提交 → 同步服务器 → 文档 → 推送备份）

---

## 0. 范围界定（本轮 = M1）

Issue #4 全量约 1–1.5 周。本轮交付**可端到端生效的渲染核心**，DB 实体与上传 API 留下一轮：

| 项 | 本轮（M1） | 下一轮（M2） |
| --- | --- | --- |
| CSL-JSON 契约与映射 | ✅ | — |
| `CitationRenderer`（逐条 locale + 应用层编号/排序 + 内联标记） | ✅ | — |
| GB/T 7714—2025 自研样式（数字制） | ✅ | 著者-出版年制、校对 |
| 预置样式 ≥4 种（含官方 vendored） | ✅ | 样式管理 UI |
| 样式准入校验（schema + CSL-M 扫描 + 显式拒绝） | ✅ | 上传 API + `CitationStyle` 实体 |
| golden 快照测试 | ✅ | 扩到全类型矩阵 |
| `.bib` / RIS 导出（CSL-JSON → 文本） | ✅ | ZIP/下载接口 |
| 导出链路集成（`citation_style` 真正生效） | ✅ | 前端样式选择器 |
| Alembic 迁移 / `Reference.csl_json` 落库 | ❌ | ✅ |

**⚠️ Issue 原文已被 ADR 取代的一处**：Issue #4 写「**不要用 `citeproc-py`**，社区多次反馈其维护停滞」——
一手核实（见 ADR §2.4）表明该判断不成立：`citeproc-py` **0.11.1 / 2026-09-01**，2026 年 ≥12 个版本；
真实短板是 **CSL 覆盖率约 60% + Alpha**。ADR 的决策是**用它**（纯 Python、BSD-2、无 Node、无 copyleft），
并以 **golden 测试**兜住必需构造，失败则在同一路线内切 `pandoc --citeproc`。本文档按 ADR 执行。

---

## 1. 冻结接口（实现方不得改名/改语义）

新增包 `backend/app/tools/csl/`：

### 1.1 `models.py`（CSL-JSON 契约与映射）

```python
DEFAULT_STYLE_ID = "gbt7714-2025-numeric"

class CslName(BaseModel):          # extra="forbid"
    family: str = ""
    given: str = ""
    literal: str = ""              # 机构作者

class CslItem(BaseModel):          # extra="forbid"，只放我们会填充的字段
    id: str                        # 稳定 ref_id（见 §1.5）
    type: str                      # CSL type："article-journal" / "book" / "chapter" /
                                   # "paper-conference" / "thesis" / "report" / "dataset" /
                                   # "webpage" / "manuscript"（预印本）
    title: str
    author: list[CslName] = []
    container_title: str | None = None     # → CSL "container-title"
    volume: str | None = None
    issue: str | None = None
    page: str | None = None
    publisher: str | None = None
    publisher_place: str | None = None
    issued_year: int | None = None
    doi: str | None = None
    url: str | None = None
    language: str = "zh-CN"        # ★ 决定 locale 分组（ADR §4.2）

    def to_csl_json(self) -> dict[str, Any]: ...   # 输出标准 CSL-JSON dict

def csl_item_from_literature_record(record: Any, *, ref_id: str | None = None) -> CslItem: ...
def csl_item_from_bibliography_entry(entry: Mapping[str, Any]) -> CslItem | None:
    """bibliography 条目（reviewer 产出）→ CslItem；缺 title 时返回 None。"""

def detect_language(*, title: str, authors: Sequence[str] = (), explicit: str | None = None) -> str:
    """显式 language > 中文作者名 > 题名含 CJK → zh-CN，否则 en-US。"""

#: 默认样式（＝ StyleRegistry 的 is_default）
DEFAULT_STYLE_ID = "gbt7714-2025-numeric"

class StyleMeta(BaseModel):        # extra="forbid"；裁决 R-M1-1 采纳实现方形状
    style_id: str
    title: str                     # 供 API/UI 展示（不得等于 style_id）
    path: Path
    numbering: Literal["citation-number", "none"]   # 决定应用层是否编号
    ordering: Literal["citation-order", "author-date"]
    default_locale: str = "en-US"  # 读自样式根 `default-locale`；缺失则 en-US
    is_default: bool = False

class StyleResolver(Protocol):
    def resolve(self, style_id: str) -> str: ...    # 别名归一 → 规范 style_id
    def meta(self, style_id: str) -> StyleMeta: ...
```

**样式文件命名规则（裁决 R-M1-1）**：样式资产文件名 = `{style_id}.csl`
（`gbt7714-2025-numeric.csl` / `gbt7714-2015-numeric.csl` / `apa.csl` / `ieee.csl` / `nature.csl`）。
`StyleMeta.default_locale` **按样式文件实际声明**，预置样式**并不统一**：
GB/T 7714 系列为 `zh-CN`，apa/ieee/nature 通常 `en-US`（渲染时的 locale 由条目语言逐条决定，
样式默认值只作元数据）。

```python
TYPE_IDENTIFIERS: dict[str, str] = {
    "article-journal": "J", "book": "M", "chapter": "M", "paper-conference": "C",
    "thesis": "D", "report": "R", "dataset": "DS", "webpage": "EB",
    "manuscript": "PP", "standard": "S", "patent": "P",
}
```

### 1.2 `styles.py`（注册表实现 + 准入）

> 为避免跨任务循环依赖，**`StyleMeta` 与 `StyleResolver` 协议定义在 `models.py`**（§1.1），
> 本文件的 `StyleRegistry` 实现该协议；`renderer.py` 只依赖协议，测试可注入 fake resolver。

```python
class StyleRejected(ValidationException):
    def __init__(self, reasons: Sequence[str]) -> None: ...
    @property
    def reasons(self) -> list[str]: ...

class StyleRegistry(StyleResolver):          # 协议见 §1.1
    def __init__(self, *, styles_dir: Path | None = None) -> None: ...
    def get(self, style_id: str) -> Path: ...              # 未知 → ValidationException
    def meta(self, style_id: str) -> StyleMeta: ...        # 未知 → ValueError
    def resolve(self, style_id: str) -> str:
        """别名归一：'gbt7714' → 'gbt7714-2025-numeric'（向后兼容旧请求）。"""
    def list_styles(self) -> list[StyleMeta]: ...
    def validate_csl_file(self, path: Path) -> StyleMeta:
        """准入校验：① 必需 CSL 1.0.2 结构；② CSL-M 构造扫描；
        不合规 → StyleRejected(reasons)（**绝不静默接受**，ADR §4.1）。"""

#: CSL-M 构造扫描规则（ADR §2.1/§4.1）
CSL_M_PATTERNS: tuple[tuple[str, str], ...] = (
    ("layout-locale", r"<layout[^>]*\blocale\s*="),
    ("institution", r"<institution\b"),
    ("has-day", r"has-day\s*="),
    ("citation-range-delimiter", r"citation-range-delimiter"),
    ("term-percent-s", r"<term[^>]*>[^<]*%s[^<]*</term>"),
)
```

### 1.3 `renderer.py`（核心）

```python
@dataclass(frozen=True)
class RenderedBibliography:
    style_id: str
    entries: list[str]                     # 最终参考文献行（**不含** "1. " 前缀，按 ordering 排序、已编号）
    marker_by_item_id: dict[str, str]      # ref_id → 正文引用标记（"[1]" 或 "(牛耕田 等, 2024)"）
    item_order: list[str]                  # 排序后的 ref_id 顺序

class CitationRenderer:
    def __init__(self, *, resolver: StyleResolver | None = None) -> None:
        """resolver 缺省时惰性构造 `styles.StyleRegistry()`（方法内导入，避免循环依赖）。"""
    def render(self, items: Sequence[CslItem], *, style_id: str = DEFAULT_STYLE_ID) -> RenderedBibliography:
        """① 归一 style_id；② 全局排序（citation-order / author-date）；
        ③ numbering=citation-number 时分配 1..n；④ 按 item.language 分组，
        每组用对应 locale 渲染（zh-CN / en-US）；⑤ 剥离引擎自带编号并前置应用层编号；
        ⑥ 按排序结果合并。空输入 → 空结果。"""
    def substitute_inline(self, text: str, bibliography: RenderedBibliography) -> str:
        """把 `[[cite:ref_id]]` 占位替换为 marker；未知 ref_id 保留原样并计入 warnings。"""

#: 占位符匹配（裁决 R-M1-2：原字符类漏了 DOI 里的 `/`，改为「非空白且非 `]`」，
#: 以免 ref_id 字符集随 DOI/来源 ID 变化而再次失配）
INLINE_PATTERN = re.compile(r"\[\[cite:([^\s\]]+)\]\]")
```

**编号剥离规则**（冻结，裁决 R-M1-3① 放宽）：`numbering="citation-number"` 的样式，引擎会在行首输出
**方括号编号或裸编号**（`[1] ` 或 `1. `，后者见 `nature.csl`）；渲染器统一剥离**一次**后前置应用层编号：

```python
ENGINE_NUMBER_PATTERN = re.compile(r"^(?:\[\d+\]\s*|\d+[.)]\s*)")
```

仅在行首、仅剥离一次；`numbering="none"` 的样式（APA 等）不剥离、不编号。
（实测：引擎忽略预设 `citation-number`，见 ADR §2.2/§5。）

**类型标识规则**（裁决 R-M1-3②，针对样式资产）：GB/T 7714 的**文献类型标识为必备项**——
`book` 必须输出 `[M]`、`thesis` 输出 `[D]`、`article-journal` 输出 `[J]`，**不得**只在有
`medium/URL/DOI` 时才输出；`/OL` 等**载体标识**才是按条件追加（有 URL/DOI 时为 `[J/OL]`）。

### 1.4 `exporters.py`（互通格式）

```python
def to_bibtex(items: Sequence[CslItem]) -> str: ...   # @article/@book/... + citation key
def to_ris(items: Sequence[CslItem]) -> str: ...      # TY/AU/TI/JO/PY/DO/UR/ER 行
```
不引入新依赖（纯字符串生成）；转义 `& % $ # _ { } ~ ^ \`，BibTeX key 规则 `{第一作者姓}{年份}{序号}`。

### 1.5 集成契约（Lead 负责）

- **ref_id 规则**：优先 `doi:{归一化 DOI}`，否则 `src:{source_id}`，否则 `title:{归一化标题哈希前 12 位}`。
- **bibliography 条目增强**：reviewer 产出的 bibliography 条目新增可选键
  `authors: list[str]`、`year: int | None`、`language: str`、`csl_json: dict | None`
  （缺省不产生、旧数据渲染不变）。
- **正文占位**：`[[cite:ref_id]]`；`[E{n}]` 证据标记与本功能**互不影响**（验收要求）。
- **导出链路**：`citation_style` 在**渲染期**生效——`LiteratureExporter` 增加
  `citation_style: str | None = None` 参数；提供则用 `CitationRenderer` 产出参考文献列表，
  否则保持既有 `_bibliography_line()` 行为（向后兼容，旧测试不变）。

---

## 2. 任务拆分与写作用域（互不重叠）

| 任务 | 负责人 | 写作用域 | 依赖 |
| --- | --- | --- | --- |
| M1-0 契约冻结 + 集成 + 门禁 | lead | `app/tools/csl/__init__.py`、reviewer/exporter 集成、`tests/test_csl_integration.py` | — |
| M1-1 样式资产 | style | `app/tools/csl/styles/**`（自研 2025 + vendored 官方 + ATTRIBUTION.md）、`tests/test_csl_style_files.py` | M1-0 |
| M1-2 渲染核心 | render | `app/tools/csl/models.py`、`renderer.py`、`tests/test_csl_renderer.py`、`tests/fixtures/csl/**` | M1-0、M1-1（golden 用其样式） |
| M1-3 准入与导出 | admit | `app/tools/csl/styles.py`、`exporters.py`、`tests/test_csl_styles.py`、`tests/test_csl_exporters.py` | M1-0 |
| M1-4 门禁 + 提交 + 服务器 + 文档 | lead | 全量门禁、execution-notes、roadmap、commit | 全部 |

---

## 3. 验收标准映射（Issue #4 原文 → 本轮）

| Issue 验收项 | 本轮状态 |
| --- | --- |
| 同一篇综述切换 4 种以上样式，正文引用与参考文献正确重排，`[E{n}]` 不受影响 | ✅ 预置 5 种样式 + golden 测试 + 集成测试（`[E{n}]` 保留断言） |
| GB/T 7714—2025 为默认，英文作者「仅首字母大写」 | ✅ 自研样式 + 该规则的专门 golden 断言 |
| 上传 `.csl` 后可作为可选样式使用 | ⚠️ 准入校验逻辑本轮完成（含显式拒绝）；**上传 API 与实体留下一轮** |
| golden-file 快照测试 | ✅ `tests/fixtures/csl/*.expected.md` + 比对测试 |
| CSL-JSON 可导出（.bib / RIS 兼容） | ✅ `exporters.py` + 测试（M2 接下载接口） |
| 迁移脚本（Alembic）+ 单测 + 门禁 | ⚠️ 本轮无 DB 改动（未落库）→ M2；**单测与门禁按要求全绿** |

---

## 4. 门禁命令（AGENTS.md §3，缺一不可）

```powershell
cd backend
.\.venv\Scripts\python.exe -m black app tests evaluation
.\.venv\Scripts\python.exe -m ruff check --fix app tests evaluation
.\.venv\Scripts\python.exe -m mypy app evaluation
.\.venv\Scripts\python.exe -m pytest tests -q --cov=app --cov-fail-under=80 --basetemp="$env:TEMP\pytest-dsh"
```

依赖：`citeproc-py==0.11.1`（BSD-2-Clause-Views）需加入 `pyproject.toml` 的 dependencies（Lead 负责，
**新增依赖必须锁版本**）。铁律：改文件一律用 `edit`/`write` 工具，禁止 PowerShell 文本往返。
