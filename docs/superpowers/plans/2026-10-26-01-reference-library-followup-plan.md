# 引用库遗留三项：证据闭环 / 去重合并 / 条目编辑

> 日期：2026-10-26 ｜ 状态：**已实施**（G1–G4 全部完成）
> 前序：[2026-10-26-00-reference-library-plan.md](2026-10-26-00-reference-library-plan.md)

---

## 1. 为什么做这一轮

上一轮新建了引用库，真实任务跑到 `completed`、检索节点 6/6 artifacts 来自引用库。
但**「引用可追溯」这条核心卖点在自己的文献源上并没有闭环**：

| 现象 | 取证 |
| --- | --- |
| 检索节点 `evidence_pool` 为空 | 真实任务 `3b20abc0` 的 n1/n2 `output.evidence_pool = []` |
| 引用库无摘要 | `csl_item_to_record()` 里写死 `abstract=""` |
| 重复导入只能靠 `ref_id` 幂等 | 同一文献不同 DOI 写法会存两条 |
| 条目不能改 | 只有保存/删除/清空，改一个错字要删了重导 |

### 1.1 根因：证据来源与文献源是两条互不相干的线

```
search 节点   → self._source.search()      → artifacts（文献元数据）
extract/write → _context_builder.build()   → evidence（证据）
                        ↓
              LiteratureContextBuilder.build()
                        ↓
        knowledge.search_for_context(user_id, query)   ← 只查用户知识库向量索引
```

`Evidence` 契约要求 `excerpt: str = Field(min_length=1)`，而
`LiteratureContextBuilder` 的 excerpt 来自**知识库分块的 text**。

**结论**：即使引用库里有 20 篇文献、检索节点把它们全取到了，
`extract`/`write` 节点的 `evidence` 仍然只能来自用户知识库 ——
**与文献源完全无关**。引用库任务若用户知识库为空，`evidence` 必然为空，
写作节点就只能在没有任何证据的情况下生成内容。

这不是引用库独有的问题：**arXiv 任务同样如此**（检索到的 arXiv 元数据从不进入证据池）。
引用库只是让它变得可见 —— 因为用户明确知道自己给了 3 篇文献。

### 1.2 为什么不能直接把 `abstract` 塞进 `excerpt`

`abstract=""` 是我上一轮**故意**写死的：CSL 条目里没有摘要字段，
编一个摘要就是造假（AGENTS §5 #28）。

但检索节点的 artifacts 里**有真实可用的字段**：`title` / `authors` /
`published_at` / `source_url` / `categories`。这些足以构造一条**真实的**证据：

> 「文献《X》由 A、B 于 2024 年发表，DOI 10.xxxx/yyy」

它不含原文片段，所以**不能叫「原文证据」**；但它是**可核验的元数据证据**。
两者必须区分，不能混为一谈。

---

## 2. 目标与非目标

### 目标

| # | 目标 | 验收 |
| --- | --- | --- |
| G1 | 检索到的文献能进入证据池（元数据证据），标注来源类型 | `evidence_pool` 非空，且每条能追到 `source_id` |
| G2 | 证据类型显式区分「元数据证据」与「原文证据」 | 契约里有字段标明，下游不混用 |
| G3 | 引用库支持去重合并（按 DOI 归一后判重） | 重复导入给出 `merged` 计数 |
| G4 | 引用库支持条目编辑 | `PATCH` 端点 + 前端可改 |

### 非目标（明确不做）

| 不做 | 理由 |
| --- | --- |
| 抓取全文/摘要 | 需要外呼与 PDF 解析，是另一个量级的功能；且离线可复现性会破 |
| 用 LLM 生成摘要填充 excerpt | **造假**。摘要必须来自真实来源 |
| 把元数据证据伪装成原文证据 | 下游 `citation_validator` 会按原文证据去比对，语义不符 |
| 跨用户去重 | 引用库是 owner-scoped 的，跨用户合并会泄漏存在性 |

---

## 3. 设计

### 3.1 证据类型：显式标注，不混用

`Evidence` 契约新增字段：

```python
kind: Literal["excerpt", "metadata"] = "excerpt"
"""证据类型。

- ``excerpt``：来自知识库分块的**原文片段**，可引用原文；
- ``metadata``：来自文献检索结果的**书目元数据**（题名/作者/年份/DOI），
  **不含原文**，只能用于「这篇文献存在且是谁写的」这类陈述。

两者不可混用：``excerpt`` 可以支撑「作者说……」，``metadata`` 只能支撑
「存在这样一篇文献」。
"""
```

**为什么用默认值 `excerpt` 而不是必填**：既有知识库证据全部是原文片段，
给默认值能保持 100% 向后兼容（`extra="forbid"` 下新增带默认值的字段是安全的）。

### 3.2 元数据证据的 excerpt 文本

```python
def metadata_excerpt(record: LiteratureRecord) -> str:
    """把文献元数据压成一句可核验的陈述（**不是原文**）。"""
    authors = "、".join(record.authors[:3]) or "佚名"
    if len(record.authors) > 3:
        authors += " 等"
    year = record.published_at.year
    return f"《{record.title}》— {authors}，{year}，{record.source_url}"
```

**不编造任何字段**：没有摘要就不写摘要；没有期刊名就不写期刊名。

### 3.3 证据来源的优先级

`prepare_node` 里 `EVIDENCE_NODE_TYPES` 的证据构造改为：

1. **先取依赖的 search 节点 artifacts**（真实文献元数据）→ `kind="metadata"`
2. **再叠加知识库检索结果**（原文片段）→ `kind="excerpt"`
3. 两者都为空才算 `insufficient_evidence`

**为什么不是二选一**：用户可能既有知识库文档（自己的笔记/PDF）又有引用库，
两类证据各有用途，合并后交给写作 Agent 更完整。

**为什么 search artifacts 优先**：它们是用户显式选择的文献，写作必须覆盖它们；
知识库检索是补充。

### 3.4 去重合并：按归一化 DOI 判重

现有幂等键是 `ref_id`（`doi:10.1234/a` 或 `title:xxx`）。问题：
同一文献写成 `10.1234/A`、`https://doi.org/10.1234/a`、`DOI: 10.1234/a`
会产生三个 `ref_id` → 存三条。

归一化规则：

```python
def normalize_doi(value: str | None) -> str | None:
    """DOI 归一：去 URL 前缀、去 `doi:` 前缀、小写、去空白。"""
    # "https://doi.org/10.1234/A" → "10.1234/a"
    # "DOI: 10.1234/a"           → "10.1234/a"
```

保存时若归一化 DOI 命中已有条目 → **合并**（保留旧条目，补充空字段），
计入 `merged` 而不是 `created`。

**为什么「补充空字段」而不是覆盖**：用户编辑过旧条目后，重复导入不应覆盖他的修改。

### 3.5 条目编辑：`PATCH` 语义

```
PATCH /api/v1/reference-library/{item_id}
body: { title?, author?, issued_year?, doi?, url?, container_title?, type? }
```

- 只更新**提供了的**字段（部分更新）；
- 改完后**重新跑准入检查**（`rejection_reason`）—— 把年份改空要拒绝；
- `ref_id` **不可改**（它是幂等键，改了会破坏去重语义）。

---

## 4. 验收标准

| # | 标准 | 验证方式 |
| --- | --- | --- |
| A1 | 引用库任务的 `evidence_pool` 非空且 `kind="metadata"` | 真实任务快照 |
| A2 | 每条元数据证据能追到 `source_id` | 断言 excerpt 含 DOI 或 URL |
| A3 | 知识库证据仍是 `kind="excerpt"` | 既有测试不回归 |
| A4 | 元数据证据**不含编造的摘要** | 断言 excerpt 里没有「摘要」字样且长度合理 |
| A5 | 同一 DOI 三种写法只存一条 | 保存三次 → `created=1 merged=2` |
| A6 | 合并时不覆盖用户已编辑的字段 | 编辑后再导入旧数据 → 值不变 |
| A7 | `PATCH` 能把年份改空 → 拒绝 | 400 + 可读原因 |
| A8 | `PATCH` 不能改 `ref_id` | 契约层拒绝（`extra="forbid"`） |

---

## 5. 风险

| 风险 | 缓解 |
| --- | --- |
| 元数据证据被下游当成原文证据用 → 生成「作者指出……」却无原文 | `kind` 字段显式标注；写作提示词里说明 `metadata` 只能支撑存在性陈述 |
| `Evidence.kind` 新增字段破坏既有载荷 | 带默认值 `"excerpt"`，`extra="forbid"` 下向后兼容 |
| 去重合并误合并不同文献 | 只在**归一化 DOI 完全相等**时合并；`title` 派生 id 不参与合并 |
| `PATCH` 绕过准入检查 | 更新后重新跑 `rejection_reason`，不通过则整体回滚 |

---

## 6. 执行顺序

1. 取证固化（已做）：真实任务 `evidence_pool=[]` 的证据链
2. `Evidence.kind` + `metadata_excerpt()` + 单测（红→绿）
3. `prepare_node` 合并两类证据 + 单测
4. `normalize_doi` + 合并逻辑 + 单测
5. `PATCH` 端点 + 单测
6. 前端：编辑弹层 + 合并计数展示 + 测试
7. 门禁（后端全量 / 前端全量 / `vue-tsc -b`）→ 诚实性验证 → 提交 → 部署
8. 真实任务验证 `evidence_pool` 非空 → 文档 → push → **查 CI**

---

## 7. 要更新的文档

- `docs/superpowers/specs/2026-10-23-frontend-redesign-design.md` §4.19
- `docs/superpowers/plans/2026-08-09-00-execution-notes.md`
- `AGENTS.md` §2 快照 + §5（如有新坑）+ §6.1 P1-E 遗留
- `docs/runbooks/2026-10-26-handoff.md`

---

## 8. 实施结果

### 8.1 交付内容

| # | 目标 | 交付 | 测试 |
| --- | --- | --- | --- |
| G1 | 检索到的文献进证据池 | `_metadata_evidence_from()` 从依赖节点 artifacts 构造 | 9 条接线测试 |
| G2 | 证据类型显式区分 | `Evidence.kind: Literal["excerpt","metadata"]`（默认 `excerpt`） | 3 条契约测试 |
| G3 | DOI 归一去重合并 | `normalize_doi()` + `same_work()` + `SaveResult.merged` | 14 条归一/判重 + 4 条合并 |
| G4 | 条目编辑 | `PATCH /reference-library/{id}` + 前端编辑表单 | 8 条 API + 8 条前端 |

### 8.2 关键实现细节

**为什么 `kind` 用默认值而不是必填**：既有知识库证据全是原文片段，默认
`"excerpt"` 让它们 100% 向后兼容（`extra="forbid"` 下新增带默认值的字段是安全的）。
必填会强制改遍所有既有调用点，收益为零。

**为什么元数据证据排在知识库证据前面**：它们是用户**显式选择**的文献，
写作必须覆盖；知识库检索是补充材料。

**为什么 `retrieval_mode=none` 仍然收元数据证据**：那个开关关的是
**知识库检索**（实验画像 `no_rag`），不是「用户明确选的文献」。
这两件事混在一起会让 `no_rag` 画像把用户文献也丢掉。

**为什么合并只补空字段不覆盖**：用户可能手动编辑过旧条目，
重复导入旧数据不该冲掉他的修改。

**为什么 `merged` 与 `skipped` 分开报**：用户看到「合并 2 条」需要去引用库
核对（同一篇文献有不同写法），看到「跳过 2 条」什么都不用做。

### 8.3 一个必须记录的判断

改动合并逻辑后 **7 条既有测试变红**（`test_reference_library_service.py` 3 条、
`test_reference_library_api.py` 2 条、`test_library_literature_source.py` 2 条）。

查根因：测试夹具 `csl_item()` / `csl_payload()` 的 `doi` 默认值固定为
`10.1234/...`，而测试用 `ref_id=f"doi:10.1/{index}"` 造多条 ——
**`ref_id` 不同但 DOI 全同**，被新的合并逻辑正确地并成一条。

这是**设计变更**（合并是新行为），不是回归。修法是给测试数据补上变化的 DOI，
并在注释里写明原因，避免下一个人再踩。

> 判断「设计变更 vs 回归」的标准：**旧断言表达的行为是否仍是我们想要的**。
> 这里「存 5 条不同文献就该有 5 条」仍成立，只是测试数据不再是 5 篇不同文献。

### 8.4 诚实性验证（3 组回退，全部确认变红）

| # | 回退内容 | 变红的断言 |
| --- | --- | --- |
| 1 | 元数据证据不进证据池 | **7 条** |
| 2 | DOI 不做归一（只小写） | **5 条** |
| 3 | PATCH 跳过准入检查 | **1 条** |

> 回退 1 第一次跑出 9 个 ERROR，但那是 `PermissionError`（临时目录被占用），
> **不是测试断言失败**。换成干净 `basetemp` 后才是真正的 7 failed。
> 如果不看错误类型就把「9 errors」当成验证通过，那就是自欺。

### 8.5 门禁

```
后端：black 8 files reformatted；ruff 2 fixed；mypy --no-incremental 0 错（206 文件）
      pytest 全量 2753 passed / 8 skipped（1 条 evaluation 测试见 §8.6）
前端：458 passed（+8）；vue-tsc -b 0 错；vite build OK
迁移：**不需要新迁移** —— `Evidence` 只存在内存与快照 JSON，`merged` 是响应字段
```

### 8.6 一条未定论的测试

全量跑时 `tests/evaluation/test_reporting.py::test_aggregate_csv_columns` 失败过一次
（`assert len(rows) == 4 * len(METRIC_KEYS)`）。已做的排查：

| 排查 | 结果 |
| --- | --- |
| 单独跑该文件 | 20 passed |
| 跑 `tests/evaluation` 全目录（`--no-cov`） | 76 passed |
| 同上带 `--cov` | 76 passed |
| 第二次全量（`--no-cov`） | 全绿，**未复现** |
| 与本次改动的关系 | 该测试只依赖 `tmp_path` fixture 与 `evaluation.reporting`，**不碰**引用库/证据/文献源 |

**未确认**：机制尚不清楚（无 xdist、无随机顺序）。如实记录，不写成「偶发，已忽略」。

