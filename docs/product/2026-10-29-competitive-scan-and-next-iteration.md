# 竞品扫描与下一轮迭代建议（2026-10-29）

> 范围：AI 文献检索/综述、引文核验、PRISMA、格式与文献管理。
>
> 方法：先核对既有路线图与当前交付，再用产品官网、官方文档和 GitHub API 复核功能、许可证与维护状态。价格为易变信息，不作为本轮优先级依据。

## 1. 结论先行

1. **我们的差异不是“会生成”，而是“可核验 + 可恢复 + 可导出闭环”。** 继续强化这条线，比再做一个通用 AI 写手更有价值。
2. **当前最值得补的是引文语境、撤稿/编辑通知和 Zotero 生态。** 竞品已经证明「支持/反驳/提及的引文语境」与「直接同步个人文献库」的用户价值，而我们目前主要核验文献存在性和书目元数据。
3. **主动筛选与文档结构化是第二优先级。** ASReview 可补 PRISMA 的候选排序与模拟评估，GROBID 可补 PDF 书目、摘要、参考文献等结构信息。
4. **不建议重写现有 DAG/TaskExecution，也不建议与知网拼模板数量或引入重型 Agent 框架。** 已有五层架构、状态恢复、MCP、团队、套餐和测试基础是资产，不应推倒重来。

## 2. 当前交付基线（不再当新机会重复建设）

已具备：

- 五层架构、Planner/Executor/Validator/Reviewer、DAG、持久化任务状态、暂停/恢复/重试/人工介入；
- 证据约束执行，`[E{n}]` 回溯文档/分块/来源 URL，原文与元数据证据区分；
- 确定性引用校验、CrossRef/OpenAlex 四态元数据核验及可选拦截；
- CSL 引用引擎、GB/T 7714—2025、样式上传与真实预览、RIS/BibTeX、引用库；
- Word 模板上传与校验、Markdown/DOCX 导出、用户/任务隔离；
- 多源检索、PRISMA、可复现检索日志；
- 团队工作区与批注、开放 API + MCP、套餐配额、可观测性、账号生命周期；
- 60-run 预实验框架、150 对 qrels 及完整复核/版本/哈希制品。

因此，CSL、模板、PRISMA、MCP、团队、套餐、普通 RAG 检索和通用文档上传不再作为“新增功能”重复推荐。

## 3. 主要竞品与我们的真实差异

| 竞品 | 对方当前强项（官方来源） | 我们的优势 | 我们仍需补的缺口 |
| --- | --- | --- | --- |
| [Elicit](https://support.elicit.com/en/articles/14757928-elicit-s-limitations) | 系统综述、抽取与研究 Agent；官方明确承认不能判断研究可信度，模型可能漏细节或误解数字 | 证据/引用校验、任务恢复、CSL 与模板、可复现离线实验更完整 | 需要更丰富的研究质量/偏倚评估，不能只停留在“文献存在且可解析” |
| [Consensus](https://help.consensus.app/en/articles/9922673-how-consensus-works) | Consensus Meter、Citation Graph、Deep Review、全文库问答；已支持 Zotero 导入 | 更可控的执行、模板/格式、独立校验和可复现任务记录 | 缺少引文图谱、直接 Zotero 同步和面向研究分歧的可视化 |
| [SciSpace](https://scispace.com/help/en/articles/10864812-how-to-use-deep-review-for-advanced-literature-analysis-in-scispace) | Deep Review、多步检索、论文库与数据抽取表 | 本地/私有流程、状态机、引用与格式确定性校验 | 需要结构化数据抽取列、全文书目/表格提取和更强的跨论文比较 |
| [Undermind](https://www.undermind.ai/) | 追问式深搜、引文追踪、相关性判断、后续提醒、行内引用 | 任务审批、证据闭环、可恢复执行和输出合规 | 缺少持续跟踪/提醒、发现深度和更丰富的检索后处理能力 |
| [Scite](https://scite.ai/features) / [Literature API](https://docs.scite.ai/mcp/tools/literature) | Smart Citations 将引文分为 supporting / contrasting / mentioning，提供引文片段与编辑通知 | 我们可做独立元数据核验、证据池和完整生成/导出，不依赖单一数据商 | **明显差距：没有引文语境、撤稿/更正/关注通知和引文意图**；可接 Scite 或先用 OpenAlex 做基础版 |
| [ASReview](https://asreview.nl/) | 主动学习筛选、模拟与基准比较、团队筛选 | 我们已有检索日志、PRISMA 和任务/权限体系，接入时无需重建工作流 | 缺少候选文献优先级、模型模拟评估与主动学习推荐 |
| [STORM / Co-STORM](https://github.com/stanford-oval/storm) | 多视角提问、提纲驱动长文、知识组织与动态 mind map | 我们的任务持久化、证据校验、PRISMA、引用/格式和交付链更完整 | 可复用多视角提问与知识地图，但不替换现有执行器 |
| [知网格式精灵](https://pb.cnki.net/typeset/wap/index.html) | 学校/期刊模板规模与高校入口 | 我们做内容生成 + 引文核验 + 格式闭环，可位于其上游 | 不应拼模板数量；应提升模板映射、真实成稿和可核验内容质量 |
| Zotero | 个人文献库、样式、标签、PDF 与桌面工作流 | 我们可把用户库变成任务证据并继续生成/校验/导出 | 目前只有 RIS/BibTeX，缺少直接 Zotero 同步与回写能力 |

**结构性判断**：竞品主要在「检索更广、引文语境更丰富、主动筛选更省力、个人库生态更成熟」上领先；我们在「任务可恢复、证据可核验、格式可交付、实验可复现」上更有辨识度。下一轮应补差异，而不是复制所有生成能力。

## 4. 推荐的三条迭代路线

### 路线 A（优先推荐）：可信引文 + Zotero 互操作

**目标**：把“元数据核验”升级为“文献状态与引文语境可信”，同时消除用户文献库孤岛。

1. **撤稿与编辑通知**
   - 用 OpenAlex `is_retracted` 做撤稿基础检查；其数据说明该字段来自 Retraction Watch。
   - 可接 Scite Premium 时补充 supporting / contrasting / mentioning、引文片段和 editorial notices。
   - 报告区分：撤稿确认、元数据不符、未找到、未核验；不得把“未找到”写成“安全”。
2. **引文语境基础版**
   - 用 OpenAlex `referenced_works` 构建后向参考文献网络，再补前向 citing works。
   - 先展示“谁引用谁、原文链接、来源和可核验状态”，不伪造 Scite 式意图分类。
3. **Zotero Local API 同步**
   - 读取用户本地库、集合、条目和 CSL 元数据，支持选择集合并导入引用库。
   - 回写综述参考文献/新增集合时，采用用户确认，避免静默改库。
   - 官方 Local API 在 `localhost:23119/api`，离线、无速率限制、读取无需认证；不得转发或暴露该端口。

**收益**：直接强化护城河，且能与现有 `reference_verifier`、引用库、CSL 和导出复用。
**成本**：中等，主要是适配器、数据映射、错误语义和真实 Zotero 端到端测试。
**不建议**：自己训练“支持/反驳”分类器作为首期目标；那会引入新的评估负担，且很难达到 Scite 的覆盖度。

### 路线 B：ASReview 主动筛选 + GROBID 文档结构化

**目标**：减少人工筛选工作量，并把 PDF 从“文本块”升级为可利用的学术结构数据。

1. **ASReview 侧车式接入**
   - 将去重后的候选记录映射为 ASReview 数据集，保留我们的 `search_log_id/source_id`。
   - 用主动学习给候选排序，人工仍做最终纳入/排除与分歧裁决。
   - 用模拟功能比较模型/参数，记录数据版本与评估结果，避免只给一个不可解释的 AI 分数。
2. **GROBID 独立服务**
   - 用其 TEI 输出补充题名、作者、摘要、DOI 和参考文献结构。
   - 解析后的结构再进入现有分块、证据池和引用库，不绕过 owner 隔离。
   - PDF 原文仍由用户上传，避免出版商全文再分发风险。

**收益**：直接提升 PRISMA 效率和全文利用能力，且两个项目均为 Apache-2.0。
**成本**：ASReview 数据/模型映射中等；GROBID 是 Java 服务，部署与监控成本高于普通 Python 包。
**不建议**：把 ASReview 的推荐当成自动纳入，或让 GROBID 的解析输出绕过人工核验。

### 路线 C：研究分歧可视化 + 质量/偏倚评估

**目标**：回应 Elicit 官方承认的“不能评价研究可信度”这一空白，但避免让 LLM 随意给研究打分。

1. **证据立场与争议图**
   - 引文图谱只展示可核验关系、引文语境片段和人工确认标签。
   - Scite 可用时引入其 supporting/contrasting 标签，否则保留“未分类”，不把引用次数当支持率。
2. **质量/偏倚清单工作流**
   - 按研究类型提供 RoB 2、NOS 等清单模板，逐项要求证据定位与人工确认。
   - LLM 只做候选提示，最终等级由用户确认并保留审计记录。

**收益**：形成与普通 AI 综述生成更明显的方法学差异。
**成本**：需要领域专家参与规则设计，前端与数据模型工作量较大。
**风险**：不能将 RoB/NOS 简化成“LLM 自动判高质量”，否则与可信定位冲突。

## 5. GitHub 可复用项目评估

| 项目 | 可复用部分 | 许可证（已核验） | 接入判断 |
| --- | --- | --- | --- |
| [ASReview](https://github.com/ASReview/ASReview) | 主动学习、筛选排序、模拟与基准 | Apache-2.0 | **优先**。与 PRISMA 自然互补，建议独立适配层，不改现有状态机 |
| [GROBID](https://github.com/kermitt2/grobid) | PDF 书目、摘要、参考文献、TEI 结构 | Apache-2.0 | **优先但需运维**。独立 Java 服务，适合结构提取而非替代现有解析器 |
| [PaperQA](https://github.com/Future-House/paper-qa) | 元数据感知检索、重排与上下文摘要、全文搜索 | Apache-2.0 | **选择性复用**。可研究 RCS/重排与元数据，不替换现有证据约束和 TaskExecution |
| [STORM](https://github.com/stanford-oval/storm) | 多视角提问、提纲生成、Co-STORM 知识组织 | MIT | **选择性复用**。可借多视角规划与 mind map，不整套替换工作流 |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | 扫描 PDF OCR、版面/表格结构、LLM-ready 数据 | Apache-2.0 | **按需**。适合扫描件与结构提取，模型和算力成本需实测 |
| [MarkItDown](https://github.com/microsoft/markitdown) | Office/PDF 到 Markdown 的通用转换 | MIT | **轻量备选**。可作通用文档转换兜底，不代替 GROBID 的学术结构提取 |
| [BEIR](https://github.com/beir-cellar/beir) | 检索数据格式、评估工具与基准思路 | Apache-2.0 | **参考复用**。已有 qrels 与 runner，优先复用评估思想而非重写框架 |
| [CSL 官方样式库](https://github.com/citation-style-language/styles) | 合法期刊样式与元数据 | CC BY-SA 3.0 | **按需引入并署名**。保留 CSL 项目与 citationstyles.org 链接，不引入非法 CSL-M 变体 |
| [MinerU](https://github.com/opendatalab/MinerU) | 复杂 PDF 版面、表格、公式与 Markdown/JSON | 自定义许可证（Apache-2.0 + 附加条款） | **谨慎可选**。在线服务需显著标注 MinerU；大规模商业阈值需另许可，不能当普通 Apache 处理 |
| LangGraph / Haystack | 通用 Agent/RAG 编排 | 各自开源许可 | **不建议为引入而引入**。会替换或重复现有 DAG、状态恢复和证据契约 |

### 非开源但值得接的 API

- **Scite Literature API**：能提供 Smart Citations、引文片段与 editorial notices，但官方文档注明需要 Premium。适合作为增强适配器，不应成为系统可用的单点依赖。
- **OpenAlex**：`is_retracted` 可用于撤稿基础核验，`referenced_works` 可用于引文网络，`study_designs` 可辅助后续质量/偏倚清单路由。应继续使用 polite pool、缓存和失败降级。

## 6. 建议顺序与取舍

若只做一条，建议先做 **路线 A（可信引文 + Zotero）**，原因：

- 直接强化现有“可信”差异，而不是复制生成能力；
- 与 `reference_verifier`、引用库、CSL、导出和 MCP 的接口复用最多；
- 可先以 OpenAlex 做无额外订阅的基础版，再按预算接 Scite；
- Zotero 同步能立刻改善真实用户工作流，而不是增加一个孤立的演示页面。

第二阶段做 **路线 B（ASReview + GROBID）**，解决筛选效率和全文结构利用。
第三阶段做 **路线 C（争议图 + 偏倚评估）**，但必须先明确人工确认与方法学边界。

## 7. 明确不做

- 不重写多 Agent 调度框架，不引入第二套任务状态机；
- 不拼知网模板数量，不做通用 AI 写手或泛化聊天首页；
- 不把引用次数当质量，不把“支持/反驳”交给未验证的 LLM 分类；
- 不做无授权全文聚合、查重接口或出版商全文再分发；
- 不为了“有图”而做引文图谱，先保证节点、边和语境可核验；
- 不引入 citeproc-js/CSL-M 或 OnlyOffice 等与现有许可策略冲突的组件；
- 不用重型模型替换现有可解释、可测试的规则与确定性校验。

## 8. 待用户确认的优先方向

请从下面三项中选择下一迭代的主线：

1. **可信引文 + Zotero（推荐）**：撤稿/编辑通知、引文语境基础版、Zotero 同步；
2. **主动筛选 + 文档结构化**：ASReview + GROBID；
3. **争议图 + 质量/偏倚评估**：引文图谱 + RoB/NOS 人工确认工作流。

选定后，再对对应模块做单一设计，避免把三条路线同时展开成不可交付的大项目。
