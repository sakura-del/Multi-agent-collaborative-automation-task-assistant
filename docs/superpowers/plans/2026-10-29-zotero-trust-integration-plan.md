# 方案 A：OpenAlex 撤稿状态 + Zotero 本地只读导入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 OpenAlex 独立撤稿状态和 Zotero 本机 BibTeX 导出，打通「Zotero 条目 → 现有引用库 → 核验/导出展示」的可信引文闭环。

**Architecture:** 仅在现有 OpenAlex 映射、核验器与展示层增加独立 `is_retracted` 字段，缓存升级为 v2；另设本机只读 Zotero 导出脚本生成现有导入器可解析的 BibTeX。远程后端不访问用户电脑的 localhost，不新增数据库迁移、第二套去重逻辑或第二套任务状态机。

**Tech Stack:** Python 3.11 / FastAPI / Pydantic / SQLAlchemy / httpx / pytest / Vue 3 / TypeScript / Vitest。

---

## 执行约束

- 工作目录：`E:\Multi-agent collaborative automation task assistant\.agents\worktrees\codex\zotero-trust-integration`
- 所有后端命令默认在 `backend` 下执行；前端命令默认在 `frontend` 下执行。
- 严格 TDD：先写失败测试并确认失败原因，再写最小实现。
- 每个 Task 单独提交，只 `git add` 本 Task 的精确路径；禁止 `git add -A` / `git commit -a`。
- 不修改 Alembic head，不访问远程 `localhost`，不写 Zotero，不接 Scite，不做引文图谱。
- OpenAlex 不可用时只能显示「未知」，不能显示「未撤稿」或「安全」。
- 真实 Zotero 不可用时只记录「未执行真实 Zotero 验证」，不得把 fixture 说成真实 Zotero 验收。

---

### Task 1: OpenAlex 撤稿字段解析

**Files:**
- Modify: `backend/app/tools/literature/models.py`
- Modify: `backend/app/tools/literature/openalex.py`
- Test: `backend/tests/test_openalex_client.py`

- [ ] **Step 1: 写失败测试——true / false / 缺字段三态**

在 `backend/tests/test_openalex_client.py` 的 `TestLookupDoi` 中添加：

```python
@pytest.mark.parametrize(
    ("retracted", "expected"),
    [(True, True), (False, False), (None, None)],
)
async def test_lookup_doi_parses_is_retracted(retracted, expected) -> None:
    payload = dict(WORK_PAYLOAD)
    if retracted is None:
        payload.pop("is_retracted", None)
    else:
        payload["is_retracted"] = retracted

    async with _openalex(_json_handler(payload, [])) as adapter:
        record = await adapter.lookup_doi("10.7717/peerj.4375")

    assert record is not None
    assert record.is_retracted is expected
```

- [ ] **Step 2: 运行测试，确认因字段不存在而失败**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_openalex_client.py::TestLookupDoi::test_lookup_doi_parses_is_retracted -q
```

Expected: FAIL，错误包含 `MetadataRecord` 没有 `is_retracted` 字段或等效 `AttributeError`。

- [ ] **Step 3: 在模型与 OpenAlex 映射中加入三态字段**

在 `MetadataRecord` 的 `is_open_access` 后增加：

```python
    is_retracted: bool | None = None
    """撤稿标记：True=明确撤稿，False=未标记撤稿，None=未知。"""
```

在 `OpenAlexClient._parse_work()` 的 `MetadataRecord(...)` 返回参数中加入：

```python
                is_retracted=(
                    work.get("is_retracted")
                    if isinstance(work.get("is_retracted"), bool)
                    else None
                ),
```

- [ ] **Step 4: 运行 Task 1 全部 OpenAlex 测试**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_openalex_client.py -q
```

Expected: 全部 PASS，包括新三态用例和既有错误分类用例。

- [ ] **Step 5: 提交 Task 1**

```powershell
git add backend/app/tools/literature/models.py backend/app/tools/literature/openalex.py backend/tests/test_openalex_client.py
git commit -m "feat: parse OpenAlex retraction status"
```

---

### Task 2: 核验结果传播与 v2 缓存

**Files:**
- Modify: `backend/app/tools/literature/models.py`
- Modify: `backend/app/knowledge/services/reference_verifier.py`
- Test: `backend/tests/test_reference_verifier.py`

- [ ] **Step 1: 写两个失败测试——结果传播与 v2 缓存键**

在 `backend/tests/test_reference_verifier.py` 中添加：

```python
async def test_verified_result_preserves_retraction_flag() -> None:
    record = make_record(source="openalex").model_copy(update={"is_retracted": True})
    verifier = ReferenceVerifier(openalex=FakeSource(doi_result=record))

    result = await verifier.verify(make_candidate())

    assert result.verdict is ReferenceVerdict.VERIFIED
    assert result.is_retracted is True


async def test_cache_uses_v2_namespace_and_preserves_unknown_retraction() -> None:
    redis = FakeAsyncRedis()
    cache = ReferenceVerificationCache(redis_client=redis)
    verification = make_verification("k", ReferenceVerdict.VERIFIED).model_copy(
        update={"is_retracted": None}
    )

    await cache.set("k", verification)
    cached = await cache.get("k")

    assert "refverify:v2:k" in redis.store
    assert "refverify:k" not in redis.store
    assert cached is not None
    assert cached.is_retracted is None
```

- [ ] **Step 2: 运行两个测试，确认失败**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_reference_verifier.py::test_verified_result_preserves_retraction_flag tests/test_reference_verifier.py::test_cache_uses_v2_namespace_and_preserves_unknown_retraction -q
```

Expected: 第一个因缺少 `is_retracted` 失败；第二个因缓存键不是 `refverify:v2:k` 失败。

- [ ] **Step 3: 给 ReferenceVerification 加字段并传播，升级缓存命名空间**

在 `ReferenceVerification` 的 `matched` 前增加：

```python
    is_retracted: bool | None = None
    """与四态核验正交的撤稿状态；True/False/None 分别对应已撤稿/未标记/未知。"""
```

在 `_judge()` 最后的 `ReferenceVerification(...)` 返回参数中加入：

```python
            is_retracted=record.is_retracted,
```

把 `_CACHE_KEY_PREFIX` 改为：

```python
_CACHE_KEY_PREFIX = "refverify:v2:"
```

- [ ] **Step 4: 统一既有缓存键断言并运行核验器全量测试**

先检索所有旧硬编码，不能只改当前失败位置：

```powershell
cd backend
python -c "from pathlib import Path; p=Path('tests/test_reference_verifier.py'); print('\n'.join(f'{i+1}: {line}' for i,line in enumerate(p.read_text(encoding='utf-8').splitlines()) if 'refverify:' in line))"
```

将测试文件中的期望键 `refverify:` 统一改为 `refverify:v2:`，包括 Redis `setex` 和直接 `client.get(...)` 的断言。然后运行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_reference_verifier.py -q
```

Expected: 全部 PASS；旧 `refverify:k` 仅作为「旧缓存不再使用」的负向断言时可以保留。

- [ ] **Step 5: 提交 Task 2**

```powershell
git add backend/app/tools/literature/models.py backend/app/knowledge/services/reference_verifier.py backend/tests/test_reference_verifier.py
git commit -m "feat: propagate retraction through verification cache"
```

---

### Task 3: 复盘、导出和前端撤稿徽标

**Files:**
- Modify: `backend/app/orchestration/agents/reviewer.py`
- Modify: `backend/app/tools/literature/exporter.py`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/components/tasks/ReferenceVerificationPanel.vue`
- Test: `backend/tests/test_reviewer_references.py`
- Test: `backend/tests/test_exporter_references.py`
- Test: `frontend/src/components/tasks/ReferenceVerificationPanel.test.ts`

- [ ] **Step 1: 写后端展示失败测试**

在 `FakeVerifier` 中先加入可选 `retractions` 参数（测试夹具修改）：

```python
    def __init__(self, verdicts=None, *, intercept=False, error=None, cached_first=False, retractions=None):
        self._verdicts = dict(verdicts or {})
        self._intercept = intercept
        self._error = error
        self._cached_first = cached_first
        self._retractions = dict(retractions or {})
        self.calls = 0
        self.candidates = []

    # verify_many 的 ReferenceVerification(...) 返回参数加入：
    is_retracted=self._retractions.get(candidate.title),
```

在 `TestReviewerVerificationStats` 中添加：

```python
async def test_bibliography_entries_include_independent_retraction_status() -> None:
    fake = FakeVerifier(
        VERDICTS,
        retractions={VERIFIED_TITLE: True, MISMATCH_TITLE: False, NOT_FOUND_TITLE: None},
    )
    report = await ReviewerAgent(reference_verifier=fake).execute(make_input())

    first, second, third, fourth = report["bibliography"]
    assert first["is_retracted"] is True
    assert second["is_retracted"] is False
    assert third["is_retracted"] is None
    assert fourth["is_retracted"] is None
```

在 `TestMarkdownVerificationSection` 中添加：

```python
def test_reference_line_marks_retraction_independently(tmp_path) -> None:
    report = make_report()
    report.bibliography[0]["is_retracted"] = True

    text = exported_text(tmp_path, report)

    assert "（核验：已验证；已撤稿）" in text
```

- [ ] **Step 2: 写前端三态徽标失败测试**

先把测试夹具 `entry()` 改为支持可选撤稿字段：

```ts
function entry(
  index: number,
  status: VerificationStatus | null,
  is_retracted: boolean | null = null,
): BibliographyEntry {
  return {
    index,
    title: `文献 ${index}`,
    url: `https://arxiv.org/abs/2401.0000${index}v1`,
    verification_status: status,
    is_retracted,
  };
}
```

然后添加：

```ts
it("renders independent retraction badges in three states", () => {
  const wrapper = mountPanel({
    entries: [
      { ...entry(1, "verified"), is_retracted: true },
      { ...entry(2, "verified"), is_retracted: false },
      { ...entry(3, "verified"), is_retracted: null },
    ],
  });

  expect(wrapper.find('[data-test="bibliography-retraction-1"]').text()).toBe("已撤稿");
  expect(wrapper.find('[data-test="bibliography-retraction-2"]').text()).toBe("未标记撤稿");
  expect(wrapper.find('[data-test="bibliography-retraction-3"]').text()).toBe("撤稿状态未知");
  expect(wrapper.find('[data-test="bibliography-verdict-1"]').text()).toBe("已验证");
});
```

- [ ] **Step 3: 分别运行后端与前端测试，确认失败**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_reviewer_references.py::TestReviewerVerificationStats::test_bibliography_entries_include_independent_retraction_status tests/test_exporter_references.py::TestMarkdownVerificationSection::test_reference_line_marks_retraction_independently -q
```

```powershell
cd frontend
node node_modules/vitest/vitest.mjs run src/components/tasks/ReferenceVerificationPanel.test.ts -t "renders independent retraction badges"
```

Expected: 后端缺 `is_retracted` / 导出标记不匹配；前端找不到 `bibliography-retraction-*` 或类型错误。

- [ ] **Step 4: 实现复盘与导出三态标记**

在 `reviewer.py` 为 bibliography 条目写入：

```python
            entry["is_retracted"] = verification.is_retracted
```

在 `reviewer.py` 与 `exporter.py` 分别增加同一个三态标签函数（避免为一个小标签制造跨层循环依赖）：

```python
def _retraction_label(value: Any) -> str:
    if value is True:
        return "已撤稿"
    if value is False:
        return "未标记撤稿"
    return "撤稿状态未知"
```

把两个模块各自的 `_verification_mark()` 改为：

```python
def _verification_mark(status: Any, is_retracted: Any = None) -> str:
    label = dict(_VERDICT_LABELS).get(status, status)
    return f"（核验：{label}；{_retraction_label(is_retracted)}）"
```

两个调用处均改为：

```python
_verification_mark(entry.get("verification_status"), entry.get("is_retracted"))
```

同时更新旧字符串契约：

- `backend/tests/test_reviewer_references.py` 中逐条标记期望改为 `（核验：<原四态>；撤稿状态未知）`；
- `backend/tests/test_exporter_references.py` 中 `VERIFIED_LINE` 与 `NOT_FOUND_LINE` 的期望尾部更新为 `（核验：已验证；撤稿状态未知）`、`（核验：未找到；撤稿状态未知）`。

无核验信息的历史 `LEGACY_LINES` 不含核验标记，保持不变。

再用以下命令检查所有旧标记断言，按「设计变更」统一更新，不能只改当前失败的两处：

```powershell
cd backend
python -c "from pathlib import Path; [(print(f'{p}:{i+1}: {line}')) for p in Path('tests').rglob('test_*.py') for i,line in enumerate(p.read_text(encoding='utf-8').splitlines()) if '（核验：' in line]"
```

- [ ] **Step 5: 实现前端类型与徽标**

在 `BibliographyEntry` 中添加：

```ts
  is_retracted?: boolean | null;
```

在 `ReferenceVerificationPanel.vue` 添加：

```ts
function retractionLabel(value: boolean | null | undefined): string {
  if (value === true) return "已撤稿";
  if (value === false) return "未标记撤稿";
  return "撤稿状态未知";
}
```

在每个 bibliography 条目中原四态 Badge 后增加：

```vue
<Badge
  :variant="entry.is_retracted === true ? 'destructive' : 'muted'"
  :data-test="`bibliography-retraction-${entry.index}`"
>
  {{ retractionLabel(entry.is_retracted) }}
</Badge>
```

- [ ] **Step 6: 运行 Task 3 全部测试**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_reviewer_references.py tests/test_exporter_references.py tests/test_csl_integration.py tests/test_csl_renderer.py -q
```

```powershell
cd frontend
node node_modules/vitest/vitest.mjs run src/components/tasks/ReferenceVerificationPanel.test.ts
node node_modules/vue-tsc/bin/vue-tsc.js -b
```

Expected: 后端与前端全部 PASS；四态统计不变，三态徽标独立显示。

- [ ] **Step 7: 提交 Task 3**

```powershell
git add backend/app/orchestration/agents/reviewer.py backend/app/tools/literature/exporter.py backend/tests/test_reviewer_references.py backend/tests/test_exporter_references.py frontend/src/api/types.ts frontend/src/components/tasks/ReferenceVerificationPanel.vue frontend/src/components/tasks/ReferenceVerificationPanel.test.ts
git commit -m "feat: show retraction badges in verification results"
```

---

### Task 4: Zotero 条目到 BibTeX 的纯转换

**Files:**
- Create: `scripts/zotero_export.py`
- Test: `backend/tests/test_zotero_export.py`

- [ ] **Step 1: 写失败测试——类型、作者、转义与解析复用**

在 `backend/tests/test_zotero_export.py` 添加：

```python
from pathlib import Path
import importlib.util
from app.tools.csl.importers import parse_bibtex

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("zotero_export", ROOT / "scripts/zotero_export.py")
zotero_export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zotero_export)


def test_items_to_bibtex_preserves_metadata_and_escapes() -> None:
    items = [{
        "key": "ABC123",
        "data": {
            "itemType": "journalArticle",
            "title": "Evidence {RAG} 100%",
            "creators": [{"firstName": "Wei", "lastName": "Zhang"}],
            "date": "2024-01-02",
            "DOI": "10.1234/ABC",
            "url": "https://example.org/paper",
        },
    }]

    text = zotero_export.items_to_bibtex(items)

    assert text.startswith("@article{doi:10.1234/abc,")
    assert "title={Evidence \\{RAG\\} 100\\%}" in text
    assert "author={Zhang, Wei}" in text
    assert "year={2024}" in text
    assert "doi={10.1234/abc}" in text
    assert "url={https://example.org/paper}" in text


def test_unknown_type_does_not_fabricate_year() -> None:
    items = [{"key": "X1", "data": {"itemType": "unknownType", "title": "No year"}}]

    text = zotero_export.items_to_bibtex(items)

    assert text.startswith("@misc{zotero:X1,")
    assert "1970" not in text


def test_export_is_parseable_by_existing_importer() -> None:
    items = [{
        "key": "ABC123",
        "data": {
            "itemType": "journalArticle",
            "title": "Evidence RAG",
            "creators": [{"firstName": "Wei", "lastName": "Zhang"}],
            "date": "2024-01-02",
            "DOI": "10.1234/ABC",
        },
    }]

    text = zotero_export.items_to_bibtex(items)
    parsed = parse_bibtex(text)

    assert len(parsed) == 1
    assert parsed[0].doi == "10.1234/abc"
    assert parsed[0].issued_year == 2024
    assert parsed[0].title == "Evidence RAG"
```

- [ ] **Step 2: 运行测试，确认脚本尚不存在**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_zotero_export.py -q
```

Expected: FAIL，找不到 `scripts/zotero_export.py`。

- [ ] **Step 3: 实现纯转换函数**

在 `scripts/zotero_export.py` 实现（不访问网络）：

```python
from __future__ import annotations
import re
from typing import Any, Iterable

BIBTEX_TYPES = {
    "journalArticle": "article",
    "book": "book",
    "conferencePaper": "inproceedings",
    "report": "techreport",
    "thesis": "phdthesis",
    "webpage": "misc",
}
YEAR_RE = re.compile(r"\d{4}")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _escape(value: Any) -> str:
    text = re.sub(r"[\r\n]+", " ", _text(value))
    text = text.replace("\\", r"\textbackslash ")
    return text.replace("{", r"\{").replace("}", r"\}").replace("%", r"\%")


def _year(value: Any) -> int | None:
    match = YEAR_RE.search(_text(value))
    return int(match.group(0)) if match else None


def _authors(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    names = []
    for creator in value:
        if not isinstance(creator, dict):
            continue
        literal = _text(creator.get("name"))
        if literal:
            names.append(literal)
            continue
        first, last = _text(creator.get("firstName")), _text(creator.get("lastName"))
        if first and last:
            names.append(f"{last}, {first}")
        elif first or last:
            names.append(first or last)
    return " and ".join(names)


def _doi(data: dict[str, Any]) -> str:
    return _text(data.get("DOI") or data.get("doi")).lower()


def zotero_item_to_bibtex(item: dict[str, Any]) -> str:
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    key = _text(item.get("key")) or "unknown"
    doi = _doi(data)
    cite_key = f"doi:{doi}" if doi else f"zotero:{key}"
    entry_type = BIBTEX_TYPES.get(_text(data.get("itemType")), "misc")
    fields = {"title": _text(data.get("title")), "author": _authors(data.get("creators"))}
    year = _year(data.get("date") or data.get("year"))
    if year is not None:
        fields["year"] = str(year)
    if doi:
        fields["doi"] = doi
    url = _text(data.get("url")) or (f"https://doi.org/{doi}" if doi else "")
    if url:
        fields["url"] = url
    body = ",\n".join(f"  {name}={{{_escape(value)}}}" for name, value in fields.items() if value)
    return f"@{entry_type}{{{cite_key},\n{body}\n}}\n"


def items_to_bibtex(items: Iterable[dict[str, Any]]) -> str:
    return "\n".join(zotero_item_to_bibtex(item) for item in items)
```

- [ ] **Step 4: 运行转换与现有解析器测试**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_zotero_export.py -q
```

Expected: 全部 PASS；未知类型为 `@misc`，不出现伪造 `1970`，产物可由 `parse_bibtex()` 直接解析。

- [ ] **Step 5: 提交 Task 4**

```powershell
git add scripts/zotero_export.py backend/tests/test_zotero_export.py
git commit -m "feat: convert Zotero items to BibTeX"
```

---

### Task 5: Zotero Local API、CLI 与现有引用库集成

**Files:**
- Modify: `scripts/zotero_export.py`
- Create: `docs/runbooks/zotero-local-import.md`
- Test: `backend/tests/test_zotero_export.py`

- [ ] **Step 1: 写 Local API 成功与 403 失败测试**

在 `backend/tests/test_zotero_export.py` 添加：

```python
import httpx
import pytest


def _zotero_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://localhost:23119/api")


def test_cli_reads_collection_and_writes_parseable_bibtex(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/":
            return httpx.Response(200, headers={"Zotero-API-Version": "3"}, json={})
        if request.url.path == "/api/users/0/collections/ABC/items":
            return httpx.Response(200, json=[{
                "key": "ABC123",
                "data": {
                    "itemType": "journalArticle",
                    "title": "Evidence RAG",
                    "creators": [{"firstName": "Wei", "lastName": "Zhang"}],
                    "date": "2024-01-02",
                    "DOI": "10.1234/ABC",
                },
            }])
        if request.url.path == "/api/users/0/collections":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    output = tmp_path / "zotero.bib"
    exit_code = zotero_export.main(
        ["--collection", "ABC", "--output", str(output)],
        client=_zotero_client(handler),
    )

    assert exit_code == 0
    parsed = parse_bibtex(output.read_text(encoding="utf-8"))
    assert len(parsed) == 1
    assert parsed[0].doi == "10.1234/abc"


def test_cli_rejects_disabled_local_api(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    output = tmp_path / "disabled.bib"
    exit_code = zotero_export.main(["--output", str(output)], client=_zotero_client(handler))

    assert exit_code == 1
    assert not output.exists()


def test_cli_rejects_empty_collection(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/":
            return httpx.Response(200, headers={"Zotero-API-Version": "3"}, json={})
        if request.url.path in {"/api/users/0/collections", "/api/users/0/collections/EMPTY/items"}:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    output = tmp_path / "empty.bib"
    exit_code = zotero_export.main(["--collection", "EMPTY", "--output", str(output)], client=_zotero_client(handler))

    assert exit_code == 1
    assert not output.exists()
```

- [ ] **Step 2: 运行测试，确认缺少客户端与 main()**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_zotero_export.py::test_cli_reads_collection_and_writes_parseable_bibtex tests/test_zotero_export.py::test_cli_rejects_disabled_local_api -q
```

Expected: FAIL，`ZoteroLocalClient` 或 `main()` 不存在。

- [ ] **Step 3: 实现只读客户端与 main()**

在 `scripts/zotero_export.py` 添加：

```python
import argparse
import sys
import httpx

DEFAULT_BASE_URL = "http://localhost:23119/api"


class ZoteroError(RuntimeError):
    pass


class ZoteroLocalClient:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def _get_json(self, path: str) -> Any:
        response = self._client.get(path)
        if response.status_code == 403:
            raise ZoteroError("Zotero Local API 未启用，请在 Zotero 设置中允许本机应用访问")
        if response.status_code >= 400:
            raise ZoteroError(f"Zotero Local API 返回 {response.status_code}: {path}")
        return response.json()

    def check_version(self) -> None:
        response = self._client.get("/")
        if response.headers.get("Zotero-API-Version") != "3":
            raise ZoteroError("仅支持 Zotero API v3；请检查桌面客户端版本")

    def collections(self) -> list[dict[str, Any]]:
        payload = self._get_json("/users/0/collections")
        if not isinstance(payload, list):
            raise ZoteroError("Zotero 返回的集合不是列表")
        return payload

    def collection_items(self, collection: str | None) -> list[dict[str, Any]]:
        path = f"/users/0/collections/{collection}/items" if collection else "/users/0/items"
        payload = self._get_json(path)
        if not isinstance(payload, list):
            raise ZoteroError("Zotero 返回的条目不是列表")
        return payload


def main(argv: list[str] | None = None, *, client: httpx.Client | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Zotero items to BibTeX without writes")
    parser.add_argument("--collection", default=None)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    owned = client is None
    http_client = client or httpx.Client(base_url=DEFAULT_BASE_URL, timeout=5.0)
    try:
        local = ZoteroLocalClient(http_client)
        local.check_version()
        items = local.collection_items(args.collection)
        if not items:
            keys = [str(item.get("key")) for item in local.collections() if item.get("key")]
            raise ZoteroError(
                f"集合为空或不存在：{args.collection or '全部条目'}；"
                f"可选集合：{', '.join(keys) or '无'}"
            )
        text = items_to_bibtex(items)
        if len(text.encode("utf-8")) > 1024 * 1024:
            raise ZoteroError("导出超过 1 MB，请按集合拆分")
        with open(args.output, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return 0
    except (ZoteroError, OSError, httpx.HTTPError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if owned:
            http_client.close()
```

不得接受远程 base URL；本实现默认且仅使用 `DEFAULT_BASE_URL`。

- [ ] **Step 4: 运行 Local API 与转换测试**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_zotero_export.py -q
```

Expected: 全部 PASS；403 返回 1 且不生成文件，成功响应可经现有导入器解析。

- [ ] **Step 5: 验证现有 ReferenceLibraryService 去重与准入**

在 `backend/tests/test_zotero_export.py` 先加入以下独立临时 SQLite 夹具（不依赖其他测试文件的隐藏共享状态）：

```python
import uuid
from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
import app.db.models  # noqa: F401
from app.db.base import Base
from app.knowledge.services.reference_library_service import ReferenceLibraryService

USER_A = uuid.UUID("11111111-1111-1111-1111-111111111111")

@pytest.fixture
async def session(tmp_path) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/refs.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()
```

然后添加：

```python
async def test_zotero_bibtex_uses_existing_save_and_idempotency(session) -> None:
    service = ReferenceLibraryService(session)
    items = parse_bibtex(zotero_export.items_to_bibtex([{
        "key": "ABC123",
        "data": {
            "itemType": "journalArticle",
            "title": "Evidence RAG",
            "creators": [{"firstName": "Wei", "lastName": "Zhang"}],
            "date": "2024-01-02",
            "DOI": "10.1234/ABC",
        },
    }]))

    first = await service.save(USER_A, items)
    await session.commit()
    second = await service.save(USER_A, items)
    await session.commit()

    assert first.created == 1
    assert second.created == 0
    assert second.skipped == 1
```

另加缺年份用例，断言 `rejected[0].reason` 含「年份」，不生成 1970。

- [ ] **Step 6: 编写本机使用手册**

在 `docs/runbooks/zotero-local-import.md` 写明：

- Zotero 设置中启用 Local API；
- `python scripts/zotero_export.py --collection <key> --output zotero.bib`；
- 在现有导入页上传 BibTeX，确认预览后保存；
- 只读、不写回、不远程访问 localhost、不实时同步；
- 403、空集合、超 1 MB、OpenAlex 不可用时的处理；
- 真实 Zotero 不可用时只能标记「未执行真实 Zotero 验证」。

- [ ] **Step 7: 运行 Task 5 门禁**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_zotero_export.py tests/test_reference_library_service.py tests/test_reference_library_dedup.py -q
```

Expected: 全部 PASS；同一 DOI 第二次导入按现有逻辑 skipped/merged，缺年份按现有逻辑 rejected。

- [ ] **Step 8: 提交 Task 5**

```powershell
git add scripts/zotero_export.py backend/tests/test_zotero_export.py docs/runbooks/zotero-local-import.md
git commit -m "feat: add read-only Zotero local import"
```

---

### Task 6: 全链路验收与交付记录

**Files:**
- Create: `docs/runbooks/zotero-trust-integration-acceptance.md`
- Modify: `docs/runbooks/zotero-local-import.md`

- [ ] **Step 1: 运行后端定向回归**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_openalex_client.py tests/test_reference_verifier.py tests/test_reviewer_references.py tests/test_exporter_references.py tests/test_reference_library_service.py tests/test_reference_library_dedup.py tests/test_zotero_export.py tests/test_csl_integration.py tests/test_csl_renderer.py -q
```

Expected: 全部 PASS。

- [ ] **Step 2: 运行前端回归与类型检查**

Run:

```powershell
cd frontend
node node_modules/vitest/vitest.mjs run src/components/tasks/ReferenceVerificationPanel.test.ts
node node_modules/vue-tsc/bin/vue-tsc.js -b
```

Expected: 全部 PASS，`vue-tsc` 0 错误。

- [ ] **Step 3: 运行格式与静态检查**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m black --check app tests ../scripts
.\.venv\Scripts\python.exe -m ruff check app tests ../scripts
.\.venv\Scripts\python.exe -m mypy app ../scripts
```

Expected: Black 无需改写、Ruff 0 错误、mypy 0 错误。若 Black 报格式差异，只对该任务文件运行 `black` 后重跑上述检查。

- [ ] **Step 4: 执行本机可选真实 Zotero 冒烟（不可用则明确记录未执行）**

仅当本机 Zotero 已启用 Local API 且存在集合时执行：

```powershell
cd backend
python ../scripts/zotero_export.py --collection <真实集合key> --output "$env:TEMP\zotero.bib"
python -c "import os; from pathlib import Path; from app.tools.csl.importers import parse_bibtex; p=Path(os.environ['TEMP'])/'zotero.bib'; print(len(parse_bibtex(p.read_text(encoding='utf-8'))))"
```

Expected: 脚本返回 0，现有解析器可解析。若 Zotero 未安装或未启用 Local API，**不执行该命令**，在验收文档记录「未执行真实 Zotero 验证」。

- [ ] **Step 5: 编写验收记录**

在 `docs/runbooks/zotero-trust-integration-acceptance.md` 中记录：

- Task 1–5 的实际测试命令与结果；
- OpenAlex 三态、v2 缓存、四态统计不变和独立撤稿徽标证据；
- Zotero fixture 与真实 Zotero（若执行）结果，明确区分；
- 无新迁移、无远程 localhost、无写回、无 Scite、无引文图谱；
- 已知边界：未接 Scite、未做云同步、OpenAlex 不可用时为未知。

- [ ] **Step 6: 提交验收文档**

```powershell
git add docs/runbooks/zotero-trust-integration-acceptance.md docs/runbooks/zotero-local-import.md
git commit -m "docs: record Zotero trust integration acceptance"
```

---

## 计划自查结果

- **规格覆盖**：Task 1–2 覆盖 OpenAlex 三态和缓存；Task 3 覆盖面板与导出；Task 4–5 覆盖 BibTeX、Zotero Local API、现有导入/去重/准入；Task 6 覆盖验收和边界。
- **类型一致**：`MetadataRecord.is_retracted` → `ReferenceVerification.is_retracted` → bibliography `is_retracted` → `BibliographyEntry.is_retracted`；均为 `bool | None`（前端可缺省）。
- **无占位**：每个任务包含精确文件、失败测试、实现内容、运行命令、预期结果和提交路径。
- **范围限制**：不包含引文图谱、Scite、云同步、回写、数据库迁移或第二套任务架构。
