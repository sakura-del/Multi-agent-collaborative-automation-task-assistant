# 引用库（Reference Library）计划 — 让导入的文献真正能用

> 立项日期：2026-10-26
> 起因：导入文献页重设计取证时发现的能力缺口（不是版式问题）

---

## 1. 为什么做这个

### 取证得到的事实

`/references/import` 页能**正确**解析 BibTeX / RIS（实测两种格式解析结果完全一致，
错误定位到具体条目，如「`@article{a}` 缺少结束花括号」）。但：

| 取证项 | 结果 | 含义 |
| --- | --- | --- |
| `AFTER_CLICKABLE` | `[解析预览, 重新开始]` | 解析完**没有任何出口** |
| `HAS_NEXT_STEP_HINT` | `false` | 页面不说「接下来能干什么」 |
| 后端 `POST /references/import` | 纯解析，**不落库** | 无保存路径 |
| `app/db/models/` 中文献相关表 | 只有 `citation_style` | **没有引用库表** |
| `literature_review._execute_search` | `self._source.search(...)` | 文献来自检索源（arXiv/OpenAlex/PubMed） |
| `importReferences` 的调用方 | 仅 `ReferenceImportView.vue` | 导入结果**不进入任何流程** |

页面注释自己写着：「后端**只解析不落库**，所以这里不提供保存按钮
（保存路径属后续工作）」。

### 结论

这一页不是「界面不好看」，而是**功能只做了一半**：用户把 Zotero 导出的
`.bib` 粘进来，看到解析结果，然后什么也做不了。

产品定位是「生成即合规、引用可核验的学术综述工作台」。用户自带文献库
（Zotero / EndNote）是真实需求 —— 综述要引的往往就是自己读过的那批，
而不是检索源随机返回的。

---

## 2. 目标与非目标

### 目标（本轮范围）

1. **引用库持久化**：用户能把导入的文献存进自己的引用库，可增删查；
2. **任务可用它作文献来源**：创建任务时可选「用我的引用库」，
   检索节点从引用库取文献（**不再外呼**，天然可复现）；
3. **导入页有真实出口**：「保存到引用库」按钮，保存后能跳到引用库查看。

### 非目标（本轮**不做**，明确记录避免误解）

| 不做 | 原因 |
| --- | --- |
| 引用库全文/PDF 存储 | 版权与存储成本，且综述只需要元数据 |
| 引用库去重合并 UI | 需要人工判定冲突字段取舍，单独立项 |
| 引用库与知识库联动（全文入库→RAG） | 知识库是「上传 PDF 建索引」，语义不同 |
| 引用库条目编辑 | 先做「导入→用」，编辑可后续加 |
| 任务里「混合来源」（引用库 + 检索源） | 会让引用可追溯性变复杂，先做纯引用库模式 |

---

## 3. 设计

### 3.1 数据模型

新表 `reference_library_item`（**不是**「一个用户一张库表」——一个用户一个库，
条目多行）：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID PK | 条目 ID |
| `user_id` | UUID FK→user ON DELETE CASCADE | 所有者（owner-scoped，跨用户 404） |
| `ref_id` | String(200) | 稳定标识（沿用 `CslItem.id` 规则：优先 `doi:<归一化 DOI>`） |
| `payload` | JSON | **完整 `CslItem.model_dump(mode="json")`** |
| `title` / `issued_year` / `doi` | 冗余列 | 仅用于列表展示与排序，**不是真源** |
| `created_at` / `updated_at` | Base 提供 | — |

约束：
- `UniqueConstraint("user_id", "ref_id")`：同一用户内 `ref_id` 唯一 → **重复导入幂等**；
- `Index("ix_reference_library_item_user_created", "user_id", "created_at")`：
  列表按 `(created_at, id)` 排序（AGENTS §5 #14：同事务批量插入时间戳相同，必须带唯一列）。

**为什么 `payload` 存整份 CslItem 而不是拆成 15 个列**：
`CslItem` 是 `extra="forbid"` 的严格模型，字段会随 CSL 支持演进而增加
（`medium` 就是后加的）。拆列意味着每次加字段都要迁移；存 JSON 则
「导出/渲染直接 `from_csl_json(payload)` 还原」，与现有 CSL 管线零阻抗。
冗余 3 列只为列表查询，不做真源。

### 3.2 文献源接入

`app/tools/literature/` 下新增 `LibraryLiteratureSource`，实现与
`ArxivLiteratureSource` 相同的 `search(LiteratureSearch) -> list[LiteratureRecord]`。

**关键约束：`LiteratureRecord` 的字段是冻结的**（`source_id/title/authors/abstract/
published_at/source_url/pdf_url/categories`，`extra="forbid"`）。从 `CslItem` 映射时：

| LiteratureRecord 字段 | 来源 | 缺失时 |
| --- | --- | --- |
| `source_id` | `CslItem.id` | 必有 |
| `title` | `CslItem.title` | 必有 |
| `authors` | `CslName` → `"Family, Given"` | 可为空列表 |
| `abstract` | 无 → `""` | — |
| `published_at` | `issued_year` → `1 月 1 日` UTC | **无年份 → 用 `1970-01-01`？不行**，见下 |
| `source_url` | `CslItem.url` 或 `https://doi.org/<doi>` | 都没有 → 见下 |
| `pdf_url` | 同 `source_url` | 同上 |
| `categories` | `[]` | — |

**两个必须处理的硬点**（否则会静默产生假数据）：

1. **`published_at` 必填且是 `datetime`**：引用库条目可能没有年份
   （`issued_year=None`）。**不能编一个 1970** —— 那会让「按时间排序」的检索
   逻辑把无年份条目排到最前，且用户看到 1970 会以为数据错了。
   → 需要给 `LiteratureRecord.published_at` 一个明确语义：**改用可选**，
   或引用库条目在入库时就**拒绝无年份条目**。
   决策见 §3.4（我选后者，并在导入保存时给出可读原因）。
2. **`source_url` / `pdf_url` 是 `HttpUrl`（必填）**：没有 URL 也没有 DOI 的条目
   无法构造。→ 同上，入库时校验。

### 3.3 任务接线

任务 `config` 新增可选键 `reference_library: true`（布尔，不用 `library_id`：
一个用户一个库，用布尔更简单且不会出现「选了别人的库」这类越权面）。

`resolve_literature_source(task, *, session=..., ...)`：

```
if config.get("reference_library"):
    → LibraryLiteratureSource(user_id, session)
elif config.get("offline_dataset"):
    → ManifestLiteratureSource
else:
    → 多源检索（现状）
```

**为什么 `reference_library` 优先于 `offline_dataset`**：用户显式选了引用库，
就该用他的库；离线清单是演示兜底，两者同时为真说明前端传错了，此时
「用户显式选择」优先级更高。**但要在日志里记一条 warning**，便于发现前端 bug。

**依赖注入问题**：`resolve_literature_source` 现在是纯函数（只读 `task.config`），
接引用库需要 DB session 与 `user_id`。→ 改为接受可选 `session` 参数；
无 session 且要求引用库时**抛明确异常**（不能静默回退到外呼检索：
用户以为在用自己 20 篇，实际去 arXiv 拉了 10 篇无关的）。

### 3.4 准入决策（必须在入库时把关）

引用库条目要能当文献源用，入库时必须满足 `LiteratureRecord` 的硬约束：

| 校验 | 不合规时 |
| --- | --- |
| 有 `issued_year` | 400「条目 X 缺出版年份，无法作为文献源使用」 |
| 有 `url` 或 `doi`（能构造 source_url） | 400「条目 X 缺 URL 与 DOI」 |
| `title` 非空 | 400（`CslItem` 已保证） |

**这是本计划最重要的一个取舍**：宁可入库失败并说清原因，也不要存进去之后
在任务执行时炸掉或产生假 `published_at`。

### 3.5 API

```
GET    /api/v1/reference-library            列出（分页：limit/offset）
POST   /api/v1/reference-library            保存（body: items[]，来自导入预览）
DELETE /api/v1/reference-library/{item_id}  删除单条
GET    /api/v1/reference-library/summary    条数 + 可作文献源的条数
```

保存语义：
- 批量保存，**按 `ref_id` 幂等**（重复导入同一批 → `created=0, skipped=N`）；
- 返回 `created` / `skipped` / `rejected[{ref_id, title, reason}]`，
  **部分成功要如实报告**（不能「都成功」也不能「全失败」）；
- 全部被拒 → 400 且列出原因。

### 3.6 前端

| 页面 | 改动 |
| --- | --- |
| `/references/import` | 预览后出现「保存到引用库」；保存结果分三态展示（新增/已存在/被拒及原因）；保存后给「去引用库查看」链接 |
| `/references/library`（新页） | 列表（标题/作者/年份/DOI）+ 删除 + 空态引导 |
| 任务创建表单 | 「文献来源」选择：检索源（默认）/ 我的引用库；选引用库时提示「将从你的 N 篇文献中选取，不外呼」 |

---

## 4. 验收标准

**必须全部满足才算完成：**

1. 后端：`reference_library_item` 表 + 迁移（`upgrade`/`downgrade` 都验证）；
2. 后端：4 个端点 + owner 隔离（跨用户 404）+ 幂等 + 部分成功如实报告；
3. 后端：入库准入（缺年份/缺 URL 拒绝并给可读原因）；
4. 后端：`LibraryLiteratureSource` 单测（含无年份条目**不会**变成 1970）；
5. 后端：`resolve_literature_source` 在 `reference_library=true` 且无 session 时
   **抛明确异常**，不回退外呼；
6. **端到端**：真实浏览器导入 → 保存 → 创建任务选引用库 → 任务跑完 →
   参考文献**确实来自引用库**（用真实任务验证，不能只看单测）；
7. 前端：全量 vitest + `vue-tsc -b` + build；
8. **诚实性验证**：回退关键修复 → 对应测试必须变红；
9. 数据清理：验证产生的任务/条目要删干净并**回查**。

---

## 5. 风险与应对

| 风险 | 应对 |
| --- | --- |
| 真实任务跑完需要 LLM 余额（DeepSeek 曾为 -0.08 CNY） | 先用「引用库文献源」的**纯后端集成测试**验证取数正确；真实任务作为加分项，余额不足时如实标注未验证 |
| `LiteratureRecord` 是冻结契约，改动会波及 Issue #3 核验链路 | **不改它**。用入库准入保证能构造出合法记录 |
| 引用库条目没有摘要，写综述时证据不足 | 明确记录为非目标；页面上说明「引用库只提供元数据」 |
| 迁移与现有 `citation_style` 风格不一致 | 照抄 `citation_style.py` 的 owner-scoped + 唯一约束写法 |

---

## 6. 执行顺序

1. 模型 + 迁移（含 `upgrade`/`downgrade` 验证）
2. 领域服务（保存/列表/删除/准入）+ 单测
3. `LibraryLiteratureSource` + 单测（重点：无年份不造假）
4. 端点 + owner 隔离测试
5. `resolve_literature_source` 接线 + 测试
6. 前端：导入页保存 + 引用库页 + 任务表单选择
7. 门禁 → 部署 → E2E → 诚实性验证 → 文档 → 提交

---

## 7. 完成后要更新的文档

- `docs/superpowers/specs/2026-10-23-frontend-redesign-design.md` §4.18（导入页 + 引用库）
- `docs/superpowers/plans/2026-08-09-00-execution-notes.md` 末节
- `AGENTS.md`：§2 快照、§6.1 P1-E 状态、迁移 head
- `docs/runbooks/2026-10-26-handoff.md`：页面清单清空，改为「引用库已交付」
