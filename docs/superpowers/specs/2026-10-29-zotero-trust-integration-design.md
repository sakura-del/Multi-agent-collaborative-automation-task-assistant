# 方案 A：OpenAlex 撤稿状态 + Zotero 本地只读导入设计

> 日期：2026-10-29 ｜ 状态：已获用户方向确认，待设计审核
>
> 背景调研：[竞品扫描与下一轮迭代建议](../../product/2026-10-29-competitive-scan-and-next-iteration.md)

## 1. 目标与范围

### 1.1 目标

在不改变现有五层架构、任务状态机和引用库契约的前提下，完成一个可独立验收的垂直切片：

1. 使用 OpenAlex `is_retracted` 为参考文献增加独立的撤稿状态；
2. 通过本机导出的方式，将 Zotero 条目转换为现有导入器可解析的 BibTeX，复用引用库的预览、去重、准入和导出链路。

本切片强化「可信引文」主线，不同时展开引文图谱、云端同步或 Scite 全量接入。

### 1.2 明确不做

- 不增加第五种 `ReferenceVerdict`；
- 不做引文图谱、支持/反驳自动分类、实时同步或 Zotero 回写；
- 不让远程 FastAPI 直接访问用户电脑上的 `localhost`；
- 不引入第二套引用库、DOI 去重逻辑、导入格式或任务状态机；
- 不改变 Alembic head，不新增数据库迁移；
- 不把「未标记撤稿」写成「安全」，也不把「未找到」写成「未撤稿」；
- 不要求 Scite 订阅，不替换现有 `TaskExecution` / DAG / RAG / CSL 实现。

后续候选：OpenAlex 引文关系图、Scite supporting/contrasting/editorial notices、Zotero Web API OAuth 同步。

## 2. 复用现状与边界

### 2.1 直接复用

- `app.tools.literature.openalex.OpenAlexClient` 与 `MetadataSource` 协议；
- `MetadataRecord`、`ReferenceVerification` 和现有四态核验语义；
- `ReferenceVerificationPanel` 与导出报告的展示位置；
- `ReferenceLibraryService.save()`、归一化 DOI 去重、`skipped` / `merged` / `rejected` 统计；
- `POST /api/v1/references/import` 的 BibTeX 解析和预览语义；
- `CslItem`、CSL 渲染、Markdown/DOCX 导出与 owner 隔离。

### 2.2 关键架构约束

Zotero Local API 默认位于用户电脑的 `localhost:23119/api`，生产后端位于远程服务器，不能直接访问该地址。第一阶段必须使用**本机导出文件**而不是服务器直连，以避免错误的部署假设、SSRF 和本地端口暴露。

## 3. 方案选择

| 方案 | 判断 | 原因 |
| --- | --- | --- |
| **A：OpenAlex 撤稿 + Zotero 本地导出（选定）** | 采用 | 复用现有导入/核验链，风险小，能独立验收，不需要新增密钥或云端授权 |
| B：Zotero Web API 云同步 | 暂缓 | OAuth 1.0a、密钥、分页和限流成本高，适合后续 |
| C：Scite 引文语境优先 | 暂缓 | 需要 Premium 且引入数据商依赖，作为后续增强 |

## 4. 撤稿状态设计

### 4.1 数据模型

在两个现有模型上分别增加可选字段，**不新增数据库列**：

```text
MetadataRecord.is_retracted: bool | None = None
ReferenceVerification.is_retracted: bool | None = None
```

字段只来自 OpenAlex `is_retracted`。CrossRef 没有对应字段时保持 `None`。

### 4.2 显示语义

| 数据值 | 界面语义 | 禁止误写 |
| --- | --- | --- |
| `True` | **已撤稿** | 不得只显示一般元数据正确而隐藏撤稿 |
| `False` | **未标记撤稿** | 不得写成「安全」「无问题」「未撤稿已证实」 |
| `None` | **未知** | 不得写成「未撤稿」或「安全」 |

撤稿状态与 `VERIFIED / METADATA_MISMATCH / NOT_FOUND / UNVERIFIED` 四态正交：文献的题名、作者和年份可以匹配，同时仍被标记为已撤稿。

### 4.3 缓存

核验缓存键升为新版本（例如 `refverify:v2:<key>`），避免把不包含撤稿字段的旧缓存当作新语义使用。新缓存必须保存 `is_retracted` 原值，包括 `None`。

### 4.4 展示与导出

- 在现有引用核验面板为单条结论增加独立撤稿徽标；
- 不改变现有四态计数和通过/拦截规则；
- 默认只提示，不因撤稿自动拦截任务；
- 如需后续增加「撤稿禁止进入参考文献」策略，必须另做用户可配置的独立开关，不在本切片隐式改变行为。

## 5. Zotero 本地导出设计

### 5.1 组件

新增本机脚本 `scripts/zotero_export.py`，职责仅限：

1. 访问本机 Zotero Local API，检查可用性和 API 版本；
2. 读取集合、条目及必要的集合元数据；
3. 将支持的 Zotero item type 映射为 BibTeX 类型；
4. 生成可由现有 `parse_bibtex()` 直接解析的 `.bib` 文件；
5. 打印条目数、跳过数和警告，不连接生产数据库。

该脚本属于客户端工具，不进入服务器任务执行路径。

### 5.2 调用流程

```text
用户在 Zotero 设置中启用本机 API
  → python scripts/zotero_export.py --collection <key> --output zotero.bib
  → 现有导入页上传 zotero.bib（format=bibtex）
  → 现有解析预览
  → 用户确认
  → ReferenceLibraryService.save()
  → 后续任务与核验显示 OpenAlex 撤稿状态
```

### 5.3 类型映射

首版只保证常见类型；未知类型降级为 `@misc`，不得因类型未知而丢弃整批：

| Zotero item type | BibTeX |
| --- | --- |
| `journalArticle` | `@article` |
| `book` | `@book` |
| `conferencePaper` | `@inproceedings` |
| `thesis` | `@phdthesis` 或 `@mastersthesis` |
| `report` | `@techreport` |
| `webpage` | `@misc` + `url` |
| 其他/未知 | `@misc` |

标题、作者、年份、DOI、URL 为优先保留字段。摘要只有存在且适合现有契约时才保留，不为缺失字段编造内容。

### 5.4 转义与准入

- 对 BibTeX 特殊字符、换行和括号做统一转义，防止格式注入；
- 输出后必须再经现有 `parse_bibtex()` 验证，不能只写文件就算成功；
- 超过现有 `MAX_IMPORT_BYTES` 时明确报出并建议按集合拆分，不静默截断；
- 缺年份或缺来源链接的条目交由现有准入规则返回可读原因，不伪造 `1970` 或示例 URL；
- 重复 DOI 继续由现有 `same_work()` 合并，不在脚本中另做一套判重。

## 6. 错误处理

| 情况 | 处理 |
| --- | --- |
| Zotero 未启用 Local API / 403 | 提示用户在 Zotero 设置中启用，脚本非零退出，不生成空的成功文件 |
| API 版本或 `Zotero-Server-ID` 不支持 | 明确报出实际版本，停止而不是猜测解析 |
| 集合不存在或为空 | 给出集合 key 与可选集合列表，区分「空集合」与「读取失败」 |
| 单条类型未知/字段缺失 | 跳过或降级并记录警告，其他条目继续 |
| OpenAlex 超时/限流/不可用 | 撤稿状态为 `None`（未知），不阻断现有任务 |
| 旧缓存或外部数据缺少撤稿字段 | 按未知处理，不推断为未撤稿 |

## 7. 验收标准

| # | 验收项 | 必需证据 |
| --- | --- | --- |
| A1 | OpenAlex 撤稿三态映射 | `True` / `False` / 缺字段均有单测；`False` 不显示为「安全」 |
| A2 | 缓存语义升级 | 旧版本缓存不冒充含撤稿状态的新结果；`None` 可正确缓存 |
| A3 | BibTeX 可直接复用 | 脚本产物经现有 `parse_bibtex()` 成功解析，无需手工修改 |
| A4 | 去重与准入不重复实现 | 重复 DOI 走现有 `merged`；缺年份/链接沿用现有 `rejected` 原因 |
| A5 | 端到端展示 | 导入 → 引用库 → 核验 → 撤稿徽标 → Markdown/DOCX 全链路有实测证据 |
| A6 | 降级诚实 | OpenAlex 不可用时显示「未知」，不阻断任务，不显示「未撤稿」 |
| A7 | 本地边界 | 无远程 `localhost` 请求、无 Zotero 写操作、无端口暴露、无新迁移 |

## 8. 测试计划

### 8.1 单元测试

- OpenAlex `is_retracted` 为 true/false/缺失时的模型映射与展示语义；
- CrossRef 无对应字段时保持 `None`；
- 新旧缓存键隔离及 `None` 缓存；
- Zotero 常见 item type、未知 type、DOI/URL 变体、特殊字符、空集合、403、版本不符；
- 输出大小上限、BibTeX 注入样例、Unicode 中文作者与标题；
- 现有 DOI 去重和拒绝规则不被绕过。

### 8.2 集成测试

- 本地 fixture → 脚本输出 → 现有导入预览 → 保存 → 重复导入合并；
- OpenAlex fake → 核验面板与导出同时显示撤稿状态，四态统计不变；
- OpenAlex 不可用 → 任务继续，撤稿状态为未知；
- 本地 Zotero 可用时执行一次只读冒烟；不可用则明确标记未执行，不伪造真实验证。

### 8.3 门禁

按变更范围运行：后端 Black / Ruff / mypy / 定向 pytest，前端 Vitest 与 `vue-tsc -b`（若改面板），以及一次导入至导出的真实路径验证。不得在服务器跑全量 pytest。

## 9. 后续扩展（不在本切片）

- OpenAlex `referenced_works` / citing works 引文图谱；
- Scite supporting/contrasting/editorial notices 与原文片段；
- Zotero Web API OAuth 云同步、回写与冲突处理；
- RoB 2 / NOS 质量与偏倚清单工作流。

这些扩展必须各自有独立设计与验收，不因本切片完成而自动启动。

## 10. 官方依据

- [OpenAlex Works 属性：`is_retracted`、`referenced_works`](https://help.openalex.org/data/works/attributes/)
- [Zotero Local API：本机地址、启用方式与读写边界](https://www.zotero.org/support/dev/web_api/v3/local_api)
- [Scite Literature API：引文语境与编辑通知（后续增强）](https://docs.scite.ai/mcp/tools/literature)
