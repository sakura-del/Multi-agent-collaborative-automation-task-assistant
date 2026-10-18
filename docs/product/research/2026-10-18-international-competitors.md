# 国际主流 AI 学术综述/研究助手竞品调研

> 调研时间：2026-10-18 · 货币单位均为 USD · 标「未确认」= 未查到可靠公开信息
> 核心结论：**所有主流产品的"引用样式切换"都是伪需求陷阱——它们全部把这事甩给 Zotero/EndNote。原生支持 CSL / GB-T 7714 / 学校模板导入的产品，一个都没有。**

---

## 一、产品清单

### A. AI 综述生成（检索→带引用长文）

| 产品 | 定位与目标用户 | 核心能力 | 定价（2026-10） | 功能边界/抱怨 |
|---|---|---|---|---|
| **Elicit** | 面向做严肃综述的科研/药企研究员的 AI 研究助手 | 138M 论文（Semantic Scholar+PubMed+OpenAlex）+545K 临床试验；Research Agent（2026-08）、Reports、Systematic Review（支持 PRISMA 2020）、Library、Alerts、MCP+API；导入 Zotero/PDF/RIS/BIB | 学术档：Basic 免费、Plus $11/mo 年付（月付 $19）、Pro $39/$69、Scale $89/$149；产业档：Pro $49/mo 年付（$588/年）或 $75 月付、Scale $169/mo 年付或 $279 月付；Enterprise 询价 | 单一月度用量池**不结转**；超额按 **$100 增量**扣款（起跳门槛高）；仅 Enterprise 默认"不拿数据训练"；仅英文优化，非英文消耗更快 |
| **Consensus** | 快速判断"文献怎么说"的临床/学生用户 | 250M+ 论文；Consensus Meter（支持/反对比例）、Deep Review、Study Snapshot、API+MCP | 免费：无限 Papers 搜索+10 Pro 消息+3 Deep reviews+30 API 调用/月；Pro $20/mo 或 $144/年（=$12/mo）；Deep $65/mo 或 $540/年（=$45/mo，200 Deep reviews）；Teams/Enterprise 定制 | 深度检索次数是硬上限；不做完整 PRISMA 流程；导出只到 RIS/CSV，样式靠外部 |
| **SciSpace** | 全流程学术工作台（读写+综述+PRISMA） | 多 agent：SciSpace Agent / Biomedical Agent / Deep Review / PRISMA Systematic Research + Agent Gallery；集成 Zotero、Mendeley、Notion、GitHub、Drive | 免费档（有限）；Premium $20/mo（年付 $12），1200 credits/月；Advanced $90/mo（年付 $70），10000 credits；Max $200/mo（年付 $160），40000 credits | **credit 月中耗尽**是高频抱怨；Advanced 涨幅陡；无 MCP、API 需申请表；G2 仅 2 条评价 3.8 分；社区帖"AI 草拟的综述段落全是错的" |
| **Undermind** | 需要"宁可慢也不能漏"的深度检索科研人（GSK 1000+ 科学家在用） | 追引用链的 deep search；带行内引用的可溯源报告；Alerts；MCP 接入 Claude/ChatGPT | Free $0；Pro $16/mo 年付；Team $15/人/mo 年付；Enterprise 定制 | 单次检索要几分钟；**未查到**原生 CSL/样式导出能力 |
| **STORM**（Stanford） | 预写作阶段的知识梳理，偏 Wikipedia 式长文 | 两阶段（预写+写作）；视角引导提问+模拟对话；Co-STORM 加 moderator/人类协作+动态 mind map；`pip install knowledge-storm`；检索器支持 Bing/You/Serper/Brave/SearXNG/DuckDuckGo/Tavily/Google/Azure/VectorRM | 开源免费（论文级项目，`storm.genie.stanford.edu` 为 research preview） | 作者明说 **"cannot produce publication-ready articles"**；检索走**网页搜索而非学术库**，不适合做正式综述 |
| **NotebookLM**（2026-07-16 改名 **Gemini Notebook**） | 基于"自有资料"作答的学生/团队 | 只读用户上传源（PDF/Docs/YouTube/音频/图片），逐段引用回原文；Audio/Video Overviews、Slides、Reports、Mind maps、Flashcards、Quizzes；Source Discovery/Deep Research | 免费：100 笔记本/50 源/50 聊天/3 Audio Overviews 每日；Google AI Plus $4.99/mo 翻倍；Pro $19.99/mo（500/300/500/20）；Ultra $99.99–$199.99/mo；Workspace 各档含 | **消费端无公开 API/MCP**（仅 Enterprise on Google Cloud 预览）；答案仅限本笔记本源；Plus/Pro/Ultra 仅部分区域；付费只能捆 Google AI 计划 |

### B. 引用图谱 / 发现（不做综述正文）

| 产品 | 定位 | 核心能力 | 定价 | 边界/抱怨 |
|---|---|---|---|---|
| **ResearchRabbit** | 持续生长的可视化文献网络，免费优先 | 免费档：50 篇起始文章、引用/参考/相似浏览、引用地图、Collections/子集、Zotero 导入、**BibTeX 导入导出**、共享协作；RR+ 提到 300 篇起始、多项目、高级搜索、诚信监测告警 | 免费档功能完整；**RR+ 具体价格未确认** | 无 PRISMA 级可复现检索；无带引用综述正文生成 |
| **Connected Papers** | 从一篇"种子论文"快速摸清一个领域 | 共被引+文献耦合相似图（非直接引用排名）、Prior/Derivative Works、多 origin 图（2022 起）、列表视图、导出 Zotero/Mendeley/EndNote/Paperpile | 免费 **5 个新图/月**（无需账号也可用）；Academic/Business 付费解锁无限图，具体价格未确认 | 一次性快照、无监控；相似度权重**未公开**；CASRAI 明确判定：**不足以单独满足 PRISMA 2020 的可复现多库检索要求**；覆盖受限于 Semantic Scholar 语料 |
| **Litmaps** | 引用地图 + 新论文监控 | 无限 Litmaps/搜索/文章（Pro）、高级检索过滤器、Monitor 周报告警、Zotero Sync、Teams | 免费档有限；Pro 无限；**教育许可 75% off**；官网按国家 parity pricing，**具体价格未确认** | 价格不透明（需登录 app 查看）；不做综述正文 |
| **Scite** | 判断"某结论后来被支持还是被反驳" | 16 亿+ 引用语句（Smart Citations：support/contrast/mention）、300M+ 源、44+ 出版社全文、Reference Check、Collections、Alerts、Zotero 插件、浏览器扩展、API、官方 MCP | Connect 免费（**仅 MCP，且需试用或付费**）；Basic $12/mo 年付或 $20 月付（250 MCP credits）；Pro $40/mo 年付或 $50 月付（2500 credits+API）；Team $250/mo 8 席；Academic/Enterprise 询价；7 天试用 | 免费档**用不了** Assistant/Search；设计目标是查引用而非跑开放综述/跨研究抽数据；MCP 按 credit 限额 |

### C. 开源 / 免费基础设施

| 产品 | 定位 | 核心能力 | 定价 | 边界 |
|---|---|---|---|---|
| **PaperQA2**（FutureHouse） | 高精度科学文档 RAG | 从科学文档回答问题**并带引用** | 开源免费（自付 LLM 费用） | 是库不是产品，无 UI/协作/可视化 |
| **GPT Researcher** | 自主深度研究 agent | MIT 许可；gptr-mcp MCP server；默认 10–30 个源；任意 LLM + 任意检索器（Tavily/Bing/Google CSE/DuckDuckGo/SearXNG） | **$0**，无 SaaS 订阅、无 API 网关、无速率限制；自托管，只付 LLM+检索费（单次 deep research "几美分到几美元"） | 走网页检索为主；非学术库优先；无引用样式能力 |
| **Open Deep Research**（LangChain） | 开源深度研究 agent 框架 | supervisor + researcher 架构，模型与搜索 API 可替换 | 开源免费 | 是框架非产品；**具体功能细节未确认** |
| **Semantic Scholar API** | 学术图谱数据底座（Connected Papers/Litmaps/Elicit 都建在它上面） | 214M 论文 / 2.49B 引用 / 79M 作者；Academic Graph、Recommendations、Datasets；S2AG/S2ORC 数据集免费下载 | **免费**；未认证共享 1000 RPS 限流；带 key 起始 1 RPS（可申请提升） | 只是数据 API，无综述/无样式 |

### D. 系统性综述 / PRISMA 流程工具

| 产品 | 定位 | 核心能力 | 定价 | 边界/抱怨 |
|---|---|---|---|---|
| **Rayyan** | 团队筛文献的主力平台（1M+ 研究者） | 导入→去重→标题摘要筛选→全文筛选→数据抽取→偏倚风险→PRISMA 流程图；盲筛、抽样分工、iOS/Android 离线；ResearchPilot（AI Reviewer/Analyzer） | 免费：3 个活跃 review、2 个免费 reviewer；Essential $4.99/席/mo 年付（$59.88/年）或 $8.33 季付；Advanced $8.33/席/mo 年付（$99.99/年）或 $13.33 季付；Business/Academic $41.67/许可/mo 年付，5 许可起（$2,500/年）；Enterprise 定制 | **不替你检索文献**，只筛你已收集的；ResearchPilot 起价 $2,500/年；API/SSO 需 Enterprise；G2 无评价、Google Play 3.6 分 |
| **Covidence** | 非营利（Future Evidence Foundation），Cochrane 生态标准工具 | 导入→筛选→全文→数据抽取→偏倚风险；集成 CRS/EndNote/Zotero/RefWorks/Mendeley/RevMan；支持 RIS/CSV/PubMedXML；移动端筛选 | Single **$339/年**（1 个 review + 无限协作者，12 个月）；Package **$907/年**（3 个 review）；机构询价；免费试用 500 条记录 | **无学生折扣**（仅中低收入国家有优惠）；价格按 review 数而非人数，超出需再买包 |
| **DistillerSR** | 企业/监管级证据合成（药企、HTA、CRO、政府） | 全生命周期：文献管理、AI 辅助筛选、可配置表单、多用户协作、审计追踪、PRISMA 合规报告 | 有免费试用；**具体价格未公开，需询价** | 重企业销售，个人/小团队门槛高 |
| **ASReview LAB** | 开源主动学习筛选 | 主动学习优先排序文献（v2 已发布） | **开源免费** | 只做筛选排序，不做抽取/合成/可视化 |

---

## 二、对比表（关键能力 × 产品）

| 产品 | 学术库检索 | 生成带引用综述 | 证据溯源到句 | 引用图谱 | **原生 CSL/样式切换** | **GB/T 7714** | 导出格式 | 协作 |
|---|---|---|---|---|---|---|---|---|
| Elicit | ✅ 138M+ | ✅ Report/SR | ✅ | ❌ | ❌ 官方让你导 .bib 去 Zotero 选样式 | ❌ 未查到 | PDF/Word/CSV/Excel/RIS/BIB | ✅ Scale+ |
| Consensus | ✅ 250M+ | ⚠️ Deep Review | ✅ | ❌ | ❌ RIS/CSV 后外部处理 | ❌ | RIS/CSV | ⚠️ Teams |
| SciSpace | ✅ | ✅ | ✅ | ❌ | ⚠️ 有 Citation/Manuscript Formatter agent，**CSL 支持未确认**（help 页 403） | ❌ 未查到 | 未确认 | ✅ |
| Undermind | ✅ 深度追引用 | ✅ 报告 | ✅ 行内引用 | ❌ | ❌ 未查到 | ❌ 未查到 | 未确认 | ✅ 共享工作区 |
| STORM | ❌ 走网页搜索 | ✅ 但非发表级 | ✅ | ❌ | ❌ | ❌ | Markdown/文本 | ⚠️ Co-STORM 人机协作 |
| Gemini Notebook | ⚠️ 仅上传源 | ✅ 基于自有源 | ✅ 逐段 | ❌ | ❌ | ❌ | 未确认 | ✅ |
| ResearchRabbit | ⚠️ 图谱式 | ❌ | ❌ | ✅ | ❌（BibTeX 进 Zotero 后处理） | ❌ | **BibTeX** | ✅ 共享集合 |
| Connected Papers | ❌ 仅相似图 | ❌ | ❌ | ✅ | ❌ | ❌ | 到 Zotero/Mendeley/EndNote/Paperpile | ❌ |
| Litmaps | ⚠️ | ❌ | ❌ | ✅ | ❌ | ❌ | Zotero Sync | ✅ Teams |
| Scite | ✅ 全文 | ❌ | ✅ 到句 | ❌ | ❌ | ❌ | Zotero 插件/API | ✅ Teams |
| PaperQA2 | ❌ 自有文档 | ⚠️ 问答式 | ✅ | ❌ | ❌ | ❌ | 代码内 | ❌ |
| GPT Researcher | ❌ 网页 | ✅ | ✅ | ❌ | ❌ | ❌ | Markdown/PDF/Word | ❌ |
| Open Deep Research | ❌ 网页 | ✅ | ⚠️ | ❌ | ❌ | ❌ | 未确认 | ❌ |
| Semantic Scholar API | ✅ 底座 | ❌ | ❌ | ❌ | ❌ | ❌ | JSON | ❌ |
| Rayyan | ❌ 不检索 | ❌ | ❌ | ❌ | ❌ | ❌ | RIS/BibTeX/EndNote/PubMed | ✅ 强 |
| Covidence | ❌ 不检索 | ❌ | ❌ | ❌ | ❌ | ❌ | RIS/CSV/PubMedXML/RevMan | ✅ 强 |
| DistillerSR | ❌ 不检索 | ❌ | ⚠️ 审计追踪 | ❌ | ❌ | ❌ | 未确认 | ✅ 强 |
| ASReview | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | RIS/CSV | ⚠️ |

**样式支持一栏的真相**：Elicit 帮助文档《Export your data from Elicit》原文给的方案是"导出 .bib → 导入 Zotero → 全选 → 右键 Create Bibliography from Items → 选样式"。Consensus 的导出文档同样只给 CSV/RIS + Zotero Connector/Paperpile。**没有任何一个主流 AI 综述产品内置 CSL 样式选择器，更没有 GB/T 7714，也没有学校/期刊模板导入。** 中文学校的 GB/T 7714 样式目前事实上的来源是 Zotero 社区样式库（`zotero-chinese/styles`）。唯一查到的"可上传 .csl + 从示例生成样式"的产品是 Anara（`docs.anara.com`），不在本清单主流阵营——这恰好说明这是空白。

---

## 三、市场空白 / 机会点清单

1. **原生 CSL + GB/T 7714 + 学校模板**（最强空白）：所有人都在做"生成内容"，没人做"生成后直接符合我的格式要求"。用户真实工作流是"AI 出稿 → 手动导 Zotero → 重排参考文献 → 被导师打回改格式"。
2. **中文/非英文语境**：Elicit 官方承认非英文质量"差异很大"且消耗更快；中文用户的核心恐惧是"格式完美但 DOI 点开是假的"（知乎多篇）。
3. **引用真实性校验**：格式正确但文献不存在/被错引，是跨语言普遍痛点，现有产品只做"生成引用"不做"验证引用"。
4. **用量/额度透明**：Elicit 的"月度池不结转 + $100 增量扣款"、SciSpace 的"credit 月中耗尽"是最高频的价格抱怨。按结果付费或可预测额度是卖点。
5. **检索与筛选的断层**：Elicit/Consensus 会检索不会做完整 PRISMA 流程；Rayyan/Covidence/DistillerSR 会做流程但**不检索**。中间那段"从检索式到可筛选集"没人打通。
6. **单篇论文的可复现性**：CASRAI 明确指出 Connected Papers 权重未公开、结果不可复现，因此不能写进 PRISMA 方法学。可复现的检索日志（检索式+日期+库+命中数）是合规刚需。
7. **一个工具覆盖"检索→筛选→抽取→成稿→按格式导出"**：目前用户平均要拼 4–5 个工具（Elicit + Rayyan + Zotero + Word + 手动格式）。
8. **消费端 API/MCP**：NotebookLM 消费端没有 API/MCP；SciSpace 无 MCP 且 API 需审批。Elicit/Consensus/Scite/Undermind 都已上 MCP——这已经从差异化变成追赶项。

---

## 四、必备能力 vs 差异化机会

### 已经是标配（必须有，缺了就出局）
- 学术库检索，且**能说清覆盖哪些库**（Semantic Scholar/PubMed/OpenAlex 是事实标准底座）
- 生成**带行内引用**的综述正文，引用可点击溯源到原文
- RIS / BibTeX 导出（对接 Zotero/EndNote/Mendeley）
- PDF 上传与解析、表格化数据抽取（CSV/Excel 导出）
- 共享工作区 + 团队席位 + 机构计费
- 免费档（能力受限但不能是空壳）+ 7 天试用
- MCP server（Elicit/Consensus/Scite/Undermind/GPT Researcher 已有；NotebookLM/SciSpace 缺位）
- SOC 2 Type II / GDPR，"不拿客户数据训练"的明确声明

### 差异化机会（现有产品做得差或不做）
- **引用样式引擎**：原生 CSL 选择器 + 自定义 .csl 上传 + **GB/T 7714** + 学校/期刊模板（Word/LaTeX）导入 + 一键切换样式不重排内容 ← **最高优先级空白**
- **引用真实性验证**：DOI/PMID 实检、撤稿标记、引用上下文核对
- **PRISMA 全流程闭环 + 可复现检索日志**：带日期戳的检索式存档，可直接写进方法学章节
- **打通"检索→筛选→抽取→成稿→格式导出"全链路**（当前 4–5 个工具才能拼完）
- **中文优先**：中文文献库覆盖、GB/T 7714、中文学位论文模板、中文引用真实性校验
- **可预测定价**：不做 credit 黑箱，按结果/按项目计价
- **非英文语料不打折**：Elicit 明确非英文更贵更差，这是直接可攻击的软肋

---

## 五、主要来源

- Elicit 用量限制与导出：[support.elicit.com — Usage limits](https://support.elicit.com/en/articles/15646622-usage-limits-in-elicit) · [Export your data](https://support.elicit.com/en/articles/14758189-export-your-data-from-elicit) · [定价与局限综述](https://theaiagentindex.com/agents/elicit)
- Consensus：[订阅档位](https://help.consensus.app/en/articles/10087865-subscription-plans) · [导出到文献管理器](https://help.consensus.app/en/articles/9922811-how-to-export-consensus-results-to-reference-managers-endnote-zotero-paperpile)
- SciSpace：[功能与定价综述](https://theaiagentindex.com/agents/scispace) · [用户抱怨帖](https://communities.stackinsight.net/community/aitr-scispace/my-results-after-using-the-ai-to-draft-a-lit-review-section-it-was-all-wrong/)
- Undermind：[官网定价与基准](https://www.undermind.ai/#pricing)
- Scite：[功能与定价综述](https://theaiagentindex.com/agents/scite-ai)
- NotebookLM → Gemini Notebook：[功能、定价与局限](https://theaiagentindex.com/agents/notebooklm)
- STORM：[GitHub README](https://github.com/stanford-oval/storm) · [Research preview](https://storm.genie.stanford.edu/)
- ResearchRabbit：[Free Tier](https://learn.researchrabbit.ai/en/articles/12865509-researchrabbit-free-tier)
- Connected Papers：[CASRAI 独立评估](https://casrai.org/guides/connected-papers)
- Litmaps：[Litmaps Pro](https://docs.litmaps.com/en/articles/8115278-litmaps-pro)
- Rayyan：[功能与定价综述](https://theaiagentindex.com/agents/rayyan)
- Covidence：[官方定价页](https://www.covidence.org/pricing/)
- DistillerSR：[产品概览](https://techreviewer.co/products/distillersr)（价格未公开）
- GPT Researcher：[官方定价页](https://gptr.dev/pricing)
- PaperQA2：[GitHub](https://github.com/Future-House/paper-qa)
- Open Deep Research：[GitHub](https://github.com/langchain-ai/open_deep_research)
- Semantic Scholar API：[官方 API 页](https://www.semanticscholar.org/product/api)
- 中文引用格式生态：[zotero-chinese/styles](https://github.com/zotero-chinese/styles) · [GB/T 7714 CSL](https://github.com/G-Zixuan/Chinese-STD-GB-T-7714-related-csl) · 知乎痛点帖：[格式完美点开是假的](https://zhuanlan.zhihu.com/p/2085637380183216334)
