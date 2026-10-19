# 从演示到产品：功能蓝图与竞品分析

> 调研日期：2026-10-18 ｜ 结论面向：把「多智能体协作自动化任务助手」从毕业设计演示推进为可上线的学术综述服务
>
> 配套调研（同目录 `research/`）：
> - [国际竞品明细](research/2026-10-18-international-competitors.md)（18 个产品逐项）
> - [国内竞品与格式规范生态](research/2026-10-18-china-market.md)（17 个产品 + 高校格式）
> - [模板导出与引用格式技术调研](research/2026-10-18-template-and-csl-tech.md)（选型 + 15 个工程坑）

---

## 0. 结论摘要

### 三句话

1. **市场最缺的不是"排版"，而是"可信"。** 排版这块国内已被知网占住（[格式精灵](https://pb.cnki.net/typeset/wap/index.html)覆盖 3000–3500 种期刊模板 + 全部 985/211/普通本科的学位论文模板，1–8 分钟出规范稿，宣称准确率 97%+），但它**只排版不生成**；而 AI 生成侧最大的问题是**引用造假**——[JMIR Mental Health 2025 研究](https://mental.jmir.org/2025/1/e80371) 实测 GPT-4o 生成 6 篇综述，**19.9% 引用完全伪造**、真实引用中 **45.4% 有书目错误**，合计近 **2/3** 有问题。

2. **我们手里恰好有解药。** 现有的「证据约束执行 + `[E{n}]` 回溯到文档/分块 + 确定性引用校验器」正好命中行业最大的不信任点。这不是学生作业的巧合，而是**可以做成壁垒**的东西。

3. **定位应当是**：**生成即合规、引用可核验的学术综述工作台**——不做"又一个 AI 写手"，也不跟知网拼模板数量，而是做**从检索到成稿的可信闭环**。

### 一个确定性时间窗

**GB/T 7714—2025 已于 2026-07-01 实施**，全面替代 2015 版（[国家标准全文公开系统](https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=C6CE52E55AC09B9C79A20AEA77CEDD14)）。变化要点：英文作者从"姓氏全大写"改为**仅首字母大写**；新增数据集 `DS`、预印本 `PP`、网页 `EB`、缩微资料 `MM` 等类型（[东华理工图书馆整理](https://lib.ecut.edu.cn/1c/e7/c2242a138471/page.htm)）。

**但存量模板与 Zotero/EndNote 样式普遍仍是 2015 版**，官方 CSL 仓库里也只有 `gb-t-7714-2015` 两个样式（**2005 版实测 404，官方未收录**）。这意味着：**现在就把 2025 版设为默认，就是实打实的先发优势**——而且是一个刚好在答辩/上线窗口内的换挡机会。

---

## 1. 现状审计

### 1.1 已经具备的真本事（护城河素材）

| 能力 | 现状 | 竞品对比 |
| --- | --- | --- |
| **证据约束执行** | 每个 `[E{n}]` 可回溯到 `doc_id` + `chunk_id`，执行器只依据检索分块生成 | Elicit/Consensus 定位不到句；[CASRAI](https://casrai.org/guides/connected-papers) 指出部分工具权重不公开、结果不可复现 |
| **确定性引用校验** | `citation_validator` 校验引用可解析率与证据池覆盖率，**非 LLM 自评** | 竞品普遍无独立校验层 |
| **链接安全** | 导出拒绝 `javascript:`/`data:`/`file:` 等非 HTTP(S) 链接 | 少见 |
| **任务运行时** | 断点恢复、人工干预、事件流回放、owner 级隔离 | 竞品少见 |
| **模型容灾** | 瞬态 5xx 重试 + DeepSeek 降级链（已实测接管） | 未公开 |
| **可复现实验框架** | 4 组 × 15 课题 × 2 次 = 120 runs，resume-safe，论文级图表 + 置信区间 | **罕见** |
| **工程基座** | CI 全阻断（mypy 0 错 / 覆盖率 88% / 6 作业）、离线冻结语料、齐全运行手册 | 学生项目中属头部 |

### 1.2 产品化硬缺口（诚实清单，代码级证据）

| 缺口 | 证据 | 严重度 |
| --- | --- | --- |
| **无引用格式引擎** | `citation_style` **只被拼进提示词**（`task_execution.py:1081`），`exporter.py:121` 的 `_bibliography_line()` 只输出 `[n] 标题 (source_id) — url`。**没有 CSL 渲染，格式由模型自由发挥** | 🔴 致命 |
| **无模板能力** | `exporter.py:160` 的 `_build_docx_bytes()` 用 python-docx 从零 `add_heading`/`add_paragraph`——不读模板、不设中文字体、无目录域、无页码页眉 | 🔴 致命 |
| **在线检索只有 arXiv** | 仅 `ArxivLiteratureSource`（`literature_source.py:24`）。arXiv 是预印本库，无同行评审覆盖 | 🔴 高 |
| **无速率限制** | 全库无 `rate_limit` 实现 | 🟠 上线即被打爆 |
| **配额未强制** | 配置有 `cost_budget`，但无按用户扣减与拦截 | 🟠 成本失控 |
| **无账号生命周期** | 无邮箱验证、无密码重置 | 🟠 无法公开注册 |
| **无开放接口** | 无 API Key、无 MCP。而 Elicit/Consensus/Scite/Undermind **都已上线 MCP** | 🟠 已变追赶项 |
| **无协作** | 仅 owner 隔离，无团队/席位/批注 | 🟡 高校采购硬门槛 |
| **前端单薄** | 4 视图 / 16 组件 / 无 UI 库 / 无设置页 | 🟡 加功能需先补壳 |

---

## 2. 竞品格局

### 2.1 四类玩家与它们的断点

| 类别 | 代表 | 做什么 | 断点 |
| --- | --- | --- | --- |
| **AI 综述生成（国际）** | Elicit、Consensus、SciSpace、Undermind、STORM、Gemini Notebook | 自然语言检索 → 抽取 → 带引用长文 | 不做格式合规；引用真实性无保障 |
| **AI 学术写作（国内）** | 知网研学+AI助手、万方智研、维普智图、秘塔、AMiner 沉思、星火科研助手、玻尔 SciMaster | 中文全文库检索 / 长文生成 | 数据商吃机构预算但生成弱；深研系拿不到知网全文 |
| **系统综述流程** | Covidence、Rayyan、DistillerSR、ASReview、EPPI-Reviewer | PRISMA 筛选、双人复核 | **自己不检索文献**（Rayyan 明说不检索） |
| **排版合规** | **知网格式精灵**、LaTeX 社区模板（thuthesis/sjtutex 等） | 上传 Word → 按学校/期刊模板排版 | **不生成内容**；只做"事后排版" |
| **引用管理** | Zotero、EndNote、NoteExpress、Mendeley | 管理文献、插入引用 | 与生成/合规链路割裂 |

**结构性机会**：用户今天要拼 **4–5 个工具**才能完成一篇合规综述（Elicit 找文献 → Rayyan 筛 → Zotero 管 → 格式精灵排版 → 手动核对引用）。**没有任何一家把"生成"与"合规 + 可核验"打通。**

### 2.2 关键竞品事实（用于定位）

**Elicit**（[官方限制说明](https://support.elicit.com/en/articles/14757928-elicit-s-limitations)）：
- 自认"**无法评估一篇论文是否比另一篇更可信**"
- 自认模型并非天生忠实于文本，会漏掉细节或误解数字
- 斯坦福医学图书馆评估：**语义检索不透明，不符合 PRISMA**；不检索 Embase/Web of Science/Scopus（[来源](https://laneguides.stanford.edu/c.php?g=1510721&p=11361998)）
- **导出参考文献要自己导 .bib 到 Zotero 再做**——平台**没有样式选择器**（[官方导出文档](https://support.elicit.com/en/articles/14758189-export-your-data-from-elicit)）

**知网格式精灵**（[介绍](https://library.ougd.cn/info/1128/15252.htm)）：覆盖 3000–3500 期刊模板 + 全部 985/211/普通本科论文模板，1–8 分钟出稿，宣称准确率 97%+，已进高校图书馆采购清单。**只排版，不生成。**

**维普**：2026-05 才上线[引文查证 AI](https://www.vipslib.com/newsdetail893.html)，且仅机构门户内可用——说明"引文核验"连数据商都刚起步。

### 2.3 定价基准

| 产品 | 免费档 | 主力付费档 | 用户抱怨 |
| --- | --- | --- | --- |
| Elicit | Basic | 学术 Plus $11/mo 年付 · 产业 Pro $49/mo 年付 | 用量池**不结转**，超额按 **$100 增量**扣 |
| Consensus | 无限搜索 + 少量 Pro | Pro $20/mo 或 $144/年 | Deep reviews 硬上限 |
| SciSpace | 有限 | Premium $20/mo（年付 $12） | **credit 月中耗尽** |
| Undermind | 有 | Pro $16/mo 年付 | — |
| Scite | Connect（核心用不了） | Basic $12/mo 年付 | 免费档形同虚设 |
| NotebookLM | 100 笔记本 | Google AI Pro $19.99/mo | 消费端**无 API/MCP** |
| Rayyan | 3 review | Essential $4.99/席/mo | **不检索**；机构版 $2,500/年 |
| Covidence | 试用 | **$339/年**（1 review）· **$907/年**（3） | 无学生折扣 |
| **知网研学** | — | 标准 88 元/年 · 高级 158 元/年 · 超级 2088 元/年（含 AI 综述）※第三方口径 | — |
| NoteExpress | — | 个人永久授权 ¥1980 | — |
| 查重参考价 | — | 知网 1.5 元/千字符 · AIGC 检测 2 元/千字符 · 维普 3.00 元/千字 | — |

> 来源：Stanford Lane [价格页](https://laneguides.stanford.edu/c.php?g=1510721&p=11401116)（2026-05 更新）+ 国内公开公告。Elicit 档位在不同页面有差异（$10/$42 vs $49/$79），以官网为准。

### 2.4 缺口重排（**已按国内调研修正**）

原先我认为"模板排版是最大空白"——**这个判断是错的**。修正后的排序：

| 排名 | 缺口 | 现状 | 我们的机会 |
| --- | --- | --- | --- |
| **1** | **引文可核验** | 2025 年 **2.6%** 论文含至少一条幻觉引文（2024 年仅 0.3%）；维普 2026-05 才起步且限机构 | ⭐ **最大且唯一能对抗知网的差异点** |
| **2** | **生成即合规**（模板规则前置为生成约束） | 知网格式精灵是**事后排版**；Elicit 等根本不输出合规稿 | ⭐ 把"排版"变成"生成时就对" |
| **3** | **检索↔筛选断层** | Elicit 检索不跑 PRISMA；Rayyan/Covidence 跑流程不检索 | 打通中间段 |
| **4** | **可复现检索日志** | PRISMA 方法学刚需，现有工具覆盖不全 | 带时间戳检索式存档 + 流程图导出 |
| **5** | **新国标先发** | GB/T 7714—2025 已实施，存量模板/样式普遍未跟进 | 默认 2025 版 |
| 6 | 中文优先 | Elicit 官方承认非英文质量差且更贵 | 中文为一等公民 |
| 7 | 可预测定价 | 用量池不结转、credit 月中耗尽 | 按成稿篇数计价 |
| 8 | 一站式 | 现在要拼 4–5 个工具 | 全链路 |

**已从差异化变追赶项**：MCP server、行内引用溯源到句、SOC 2 / 隐私合规。

---

## 3. 功能蓝图：五条产品线

### L1 · 可信引用层（Trust Layer）★核心壁垒

| 功能 | 说明 | 竞品 |
| --- | --- | --- |
| **引用真实性校验** | 每条参考文献调 CrossRef/OpenAlex 核对 DOI/标题/作者/年份，标记「已验证 / 未找到 / 元数据不符」 | **无**（Elicit 自认不做） |
| **幻觉引用拦截** | 校验不通过**禁止进入参考文献列表**，报告标注扣除了多少条 | 无 |
| **引用可信度徽章** | 每个 `[E{n}]` 显示来源类型（同行评审/预印本）、被引量 | 无 |
| **方法学可复现日志** | 记录检索式、数据库、时间戳、命中数、筛选决策 → 一键导出 PRISMA 流程图 | 部分 |
| **质量/偏倚评估** | 按研究类型套清单（RCT→RoB 2、观察性→NOS），生成质量表 | Elicit 明确没有 |

### L2 · 格式合规层（Format Layer）★用户点名

| 功能 | 说明 |
| --- | --- |
| **CSL 引用引擎** | CSL 1.0.2；预置 **GB/T 7714—2025**（默认）、GB/T 7714—2015、APA 7、IEEE、Nature |
| **`.csl` 上传** | 用户自带期刊/学校样式 |
| **Word 模板导入** | 上传 `.docx` → 自动扫描占位符 + 样式映射 → 填充生成成稿 |
| **生成即合规** | 模板规则**前置**为生成约束（标题层级、题注、文献样式），而非事后排版 |
| **一键切换** | 换模板/换样式**只重渲染**，正文与 `[E{n}]` 标记不变 |
| **LaTeX 导出** | 面向理工科（国内盲审多只收 docx/PDF，故作为高级选项） |
| **AI 使用声明** | 随稿导出，符合高校学术规范趋势 |

### L3 · 检索与筛选层（Discovery & Screening）

多源检索（OpenAlex + Semantic Scholar + PubMed + CrossRef + arXiv，统一去重）· 布尔检索式编辑器 · PRISMA 筛选工作台（标题摘要 → 全文 → 纳入，双人独立 + 分歧解决）· 引用图谱（前向/后向追踪）· PDF 表格/图表抽取

### L4 · 协作与交付层（Collaboration）

团队工作区（角色权限）· 导师行内批注（可导出 Word 批注）· 版本历史与差异对比 · 通知

### L5 · 平台化（Platform）

MCP server · 开放 API + Key · 配额与计费 · 可观测性（指标/追踪/成本看板）· 账号体系（邮箱验证/密码重置/OAuth）

---

## 4. 用户点名功能详解：模板导入与格式切换

### 4.1 需求判断（按调研修正）

**需求真实，但不是空白，赢点在"闭环"不在"排版数量"。**

- 正面拼模板数量**必输给知网**（它有全部 985/211 模板且已进采购清单）
- 更现实的定位：**做知网/维普的上游——内容生成 + 合规层**，而不是替代品
- 三个可赢的点：① **生成即合规**（模板规则前置）② **引文可核验** ③ **新国标先发**
- 风险：模板获取与**院级变体**维护成本高（上交明文写"学院可自行制定格式"，[来源](https://xsb.seiee.sjtu.edu.cn/seiee_mobile/info/13051.htm)）；格式错误责任重；知网"模板库 + 全文库"双壁垒

### 4.2 技术选型（已按技术调研修正）

| 环节 | 推荐 | 关键理由 / 风险 |
| --- | --- | --- |
| 模板渲染 | **python-docx 1.2.0 + docxtpl 0.20.2** | 唯一成熟的"docx 当模板"方案。⚠️ **docxtpl 是 LGPL-2.1-only（不是 MIT）**，闭源 SaaS 以库形式 import 通常可接受，但打包分发/改源码前须做许可证评审 |
| 引用引擎 | **内部以 CSL-JSON 为唯一真源** + 服务端**标准 CSL 1.0.2 渲染器（`citeproc-py`，纯 Python）**；Zotero 服务端渲染兜底 | ✅ 已定案（[ADR 0001](../adr/0001-csl-bilingual-strategy.md)，2026-10-19）：双语靠**应用层逐条 locale 切换**，实测可行；**不引入 citeproc-js / Node**。⚠️ 更正旧结论：npm 包名是 `citeproc`（`citeproc-js` 不存在）；`citeproc-py` **并未停滞**（0.11.1 / 2026-09-01）；citeproc-js 许可为 **CPAL-1.0 OR AGPL-1.0**（非"未确认"），且 CPAL §15 把网络部署视为分发 |
| 样式来源 | 官方 CSL 仓库（**10,000+ 样式**，CC BY-SA 3.0，须署名）+ **自研 GB/T 7714—2025** + 归一化后的社区样式 | ⚠️ **官方仓库只有 GB/T 7714-2015（2005 版实测 404），2025 版须自研**（社区库有 9 个变体可参考）。社区双语样式含 1–4 类 CSL-M 构造 → 官方 schema 判定 **invalid**，但 citeproc-js 会**静默接受、不告警**；本产品采用**显式拒绝 + 归一化**策略，不静默降级（见 ADR §4.1） |
| 在线预览 | docx-preview（MIT，纯前端） | 不渲染 TOC/REF 域 |
| PDF | Playwright/Chromium 打印为主，LibreOffice headless 兜底 | 需随包分发中文字体 |
| 不推荐 | Spire.Doc（免费版 **500 段落**限制，综述直接击穿）· Aspose（**$1,199 档明确禁止 Web/SaaS**，SaaS 从 **$3,597** 起） | — |

**必须做的架构决策（✅ 已定案 2026-10-19）**：

> 决策见 **[ADR 0001：中文双语 CSL-M 支持策略](../adr/0001-csl-bilingual-strategy.md)**。
> 结论：**Option C —— 标准 CSL 1.0.2 + 按条目语言逐条切换 locale**（纯 Python，无 Node、无 copyleft 义务），
> 同时满足「中英混排（等 / et al.）」与「样式保持标准合法」；**否决 Option B（citeproc-js / CSL-M）**——
> 其代价（CPAL 网络部署条款 + 署名义务 + 许可元数据自相矛盾、样式非法却被静默接受、第二运行时）
> 换来的能力已被实测证明可在应用层等价实现。

### 4.3 架构（最小可行）

```
Template(id, name, version, source_docx_uri, kind,
         placeholder_contract: JSON, style_map: JSON, owner_id, is_public, checksum)
PlaceholderContract: [{key, label, type, required, sample, word_style}]
CitationStyle(id, name, csl_id, csl_xml, is_builtin, source)
Project(id, template_id, citation_style_id, locale, status)   # 切换入口只在这张表
Reference(id, csl_json: JSON, doi, source, zotero_key)         # 唯一真源
SectionDoc(id, project_id, ast_json, cite_marks)               # 引用标记存 [[cite:ref_id]]
RenderJob(id, project_id, status, output_uri, error, trace)
```

**三个关键设计**：

1. **占位符契约自研**：docxtpl **不解析** `w:sdt`（内容控件）——SDT 只是承载 `w:t` 的普通容器，没有"按控件 tag 取值"的 API。正确做法是约定 `{{ key }}` 命名规则，用 `get_undeclared_template_variables()` 扫描出待填字段清单，让用户在 UI 上映射到综述章节。
2. **`style_map` 做角色映射**：`Word 样式 ID → 语义角色`（H1/H2/H3/正文/图注/参考文献）。切换模板只做"角色→角色"重映射，**正文 AST 完全不动**——这是"一键切换"的架构前提。
3. **引用标记存 `[[cite:ref_id]]` 占位，不存硬编码数字**：否则从"顺序编码制"切到"著者-出版年制"时正文编号全废。

**渲染流水线**：`AST → 组装 docxtpl context + Subdoc → citeproc 渲染引用块为编号 RichText 注入 → TOC/REF 域原样保留 → 可选 LibreOffice 刷新域 → 落盘 + 版本记录`

### 4.4 已知工程坑（必须提前规避，节选最关键 8 条）

| # | 坑 | 规避 |
| --- | --- | --- |
| 1 | **`w:rFonts` 必须同时设 `ascii/hAnsi/eastAsia/cs`** | 只设 ascii 中文会回退宋体；python-docx 无 API，需手写 rPr XML |
| 2 | **TOC/REF/PAGE 域无法程序化创建或刷新** | 生成的目录仍是旧内容，需 Word 内 F9 或 LibreOffice headless 刷新；**交付前必须处理，否则用户看到空目录** |
| 3 | **样式名本地化** | 中文版 Word 的"标题 1"与 `Heading 1` 的 name/styleId 不一致 → 一律按 **styleId** 查找，不要按 name |
| 4 | **`numbering.xml` 无法生成** | python-docx 不支持，只能用模板预置列表样式；docxcompose 合并时 `numId` 重映射易错乱 |
| 5 | **docxtpl 语法陷阱** | `{%p%}/{%tr%}` 必须独占段落；必须写 `{{ x }}` 而非 `{{x}}`；内容含 `<` `>` `&` 必须 `autoescape=True` |
| 6 | **双语混排需 CSL-M** | 见 4.2 的架构决策 |
| 7 | **模板是不可信输入** | 必须拒绝 `.docm`、剥离 `w:attachedTemplate` 等外部关系、限制 OLE、防 zip 炸弹 |
| 8 | **样式许可** | CSL 样式 CC BY-SA 3.0 **必须署名 CSL 项目并链接 citationstyles.org**；用户上传自建样式需注意 ShareAlike 再分发限制 |

### 4.5 分阶段实施

| 阶段 | 交付 | 估计 |
| --- | --- | --- |
| **M1** | CSL 引擎 + 预置样式（**GB/T 7714—2025 默认**、2015、APA、IEEE、Nature）+ 导出时可选 | 1–1.5 周 |
| **M2** | `.docx` 模板导入（占位符 + style_map）+ 模板管理界面 | 2–3 周 |
| **M3** | `.csl` 上传 + 一键切换 + LaTeX 导出 | 1.5–2 周 |
| **M4** | 模板库（用户上传私有 + 社区共建公开；**不主动收录未授权模板**） | 2 周 + 运营 |

---

## 5. 优先级路线图

### Phase 1 · 打地基（6–8 周）——从"能演示"到"能交付"

| 优先级 | 功能 | 价值 | 依赖 |
| --- | --- | --- | --- |
| **P0** | ✅ 引用真实性校验（CrossRef/OpenAlex）——**已完成**（`42b5e54`，见 [实施计划](../superpowers/plans/2026-10-18-03-reference-authenticity-plan.md)） | ⭐ 核心护城河，唯一对抗知网的差异点 | 无 |
| **P0** | CSL 引用引擎 + **GB/T 7714—2025 默认** | 用户点名 + 新国标先发 | 无 |
| **P0** | Word 模板导入 + 一键切换 | 用户点名；生成即合规 | CSL 引擎 |
| **P0** | 检索源扩展（OpenAlex + S2 + PubMed） | 摆脱 arXiv 单一源 | 无 |
| P1 | 账号体系（邮箱验证/密码重置） | 才能开放注册 | 无 |
| P1 | 速率限制 + 按用户配额 | 上线必备 | 无 |
| P2 | RIS/BibTeX 导入导出 | 打通 Zotero/EndNote | 无 |

**Issue #3 完成情况**（2026-10-19）：后端核验链路已交付——CrossRef/OpenAlex 适配器、
四态判定（已验证 / 元数据不符 / 未找到 / 未验证降级）、核验缓存、校验报告检查项、
导出报告统计与「拦截未通过条目」开关；本地全量门禁 1159 passed / 覆盖率 88.83%，
真实 API 验收 6/6。**尚未做**：前端展示核验结果（Phase 1 后续）、`contact_email` 的
`.env` 注入（被既有配置优先级缺陷阻塞，见 execution-notes）。

### Phase 2 · 做闭环（约 3 个月）

PRISMA 筛选工作台 · 可复现检索日志 + PRISMA 流程图导出 · 团队工作区 + 席位 + 导师批注 · MCP server + 开放 API · 计费套餐 · 可观测性

### Phase 3 · 建生态（约 6 个月）

模板库与市场 · 质量/偏倚评估（RoB 2 / NOS）· 引用图谱 · PDF 表格抽取 · 多语言 · 机构版（统一身份、数据隔离、审计日志）

---

## 6. 商业化建议

**定价原则**（针对竞品被骂的点反向设计）：

| 竞品痛点 | 我们的做法 |
| --- | --- |
| 用量池不结转、超额 $100 增量扣款 | **按成稿篇数计价**，额度可结转 |
| credit 月中耗尽 | 执行前明示预估消耗 |
| 免费档形同虚设 | 免费档给**完整流程 1 篇/月**（含模板导出） |
| 学生用不起（Covidence $339/年） | 学生档低价（月付 ≤ ¥30），校邮箱验证 |
| 知网按字符收费（查重 1.5 元/千字符） | 不碰查重，只做生成 + 合规 |

**建议档位**（待验证）：免费 1 篇/月（基础模板 + GB/T 7714—2025）· 学生 ¥19–29/月（10 篇）· 专业 ¥69–99/月（协作）· 机构版按席位（对标 Rayyan $2,500/年 的 1/3–1/2）

---

## 7. 风险与合规

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| **出版商版权** | Elsevier/Springer 全文不可再分发 | 只索引**元数据 + 摘要**（CrossRef/OpenAlex 允许），全文由用户自行上传 |
| **各源限流** | arXiv 官方要求请求间隔 **≥3 秒**；PubMed E-utilities **3 req/s**（带 key 10）；Unpaywall **必须带 email**；OpenAlex 建议带 `mailto` 进 polite pool | 全部走适配器 + 限流器 + 缓存 |
| **Zotero 集成** | Web API **仅 OAuth 1.0a**（不支持 OAuth 2.0），分页上限 100，需遵守 `Backoff` 头、并发 **≤4**；`format=bib` 服务端渲染**上限 150 条** | 桌面端可优先用 Local API（`localhost:23119`，读无速率限制） |
| **查重接口** | 知网/维普**无公开 API**，只做机构授权 | ⛔ **不设计进主线** |
| **模板版权** | 学校模板可能声明版权；院级变体无统一仓库 | 用户上传私有 + 社区共建；平台不主动收录未授权模板 |
| **学术诚信** | 可能被用于代写 | 定位"辅助工具"；强制导出 AI 使用声明；引用必须可验证（天然抑制编造） |
| **数据训练** | 用户论文是敏感数据 | 明确承诺不训练；提供数据删除；国内先做隐私政策 |
| **许可证** | docxtpl **LGPL-2.1**；OnlyOffice/Collabora **AGPL-3.0**（SaaS 用需提供源码获取入口，**改一行即触发**）；Spire/Aspose 商业条款 | 上线前做一轮许可证评审；OnlyOffice 若用则零改动独立容器部署 |

---

## 8. 下一步建议（三件事）

1. ~~先做 P0 里的"引用真实性校验"（约 1 周）~~ ✅ **已完成**（Issue #3，`42b5e54`，2026-10-19）
   - 复用现有 `ArxivClient` 适配器模式接 CrossRef/OpenAlex
   - 在 `citation_validator` 新增一个 check，输出「已验证 / 未找到 / 元数据不符」
   - **理由**：这是最容易讲清、最容易验证、论文里最好写的创新点，且直击行业最大痛点

2. ~~定夺一个架构决策~~ ✅ **已定案**（Issue #5 → [ADR 0001](../adr/0001-csl-bilingual-strategy.md)，2026-10-19）
   - 结论：**标准 CSL 1.0.2 + 应用层逐条 locale 切换**（纯 Python），**不引入 citeproc-js / Node**
   - 双语（中文"等" / 英文 "et al."）实测成立，样式保持标准 1.0.2 合法

3. **下一步：M1 — CSL 引用引擎 + GB/T 7714—2025 默认**（1–1.5 周，Issue #4）
   - 自研 GB/T 7714—2025 标准 CSL 1.0.2 样式（顺序编码制 + 著者-出版年制）+ golden 测试
   - 新增 `CitationRenderer` 适配器：CSL-JSON → 逐条 locale 分组渲染 → 应用层编号/排序 → 注入 Markdown/DOCX
   - 样式准入服务：RELAX NG 校验 + CSL-M 扫描 + 显式拒绝文案（ADR §4.1）
   - 引用层数据改造：`language` 字段补齐、`[[cite:ref_id]]` 占位符（切换制式不再废编号）
   - 让"引用格式切换"从**提示词愿望**变成**确定性能力**；`citation_style` 字段语义改为"渲染时选择样式"

**并行（非开发）**：找 5–10 位在读研究生，用真实学校模板走一遍"上传模板 → 导出成稿"，重点验证占位符契约是否够用、哪些格式细节最容易翻车。

---

## 附录：主要来源

**行业问题**
- [JMIR Mental Health 2025：LLM 生成综述的引用伪造研究](https://mental.jmir.org/2025/1/e80371) ｜ [EurekAlert 报道](https://www.eurekalert.org/news-releases/1106130)
- [科技日报：2025 年 2.6% 论文含幻觉引文](https://finance.sina.cn/2026-04-13/detail-inhuitrc9739255.d.html)

**国际竞品**
- [Elicit 官方限制说明](https://support.elicit.com/en/articles/14757928-elicit-s-limitations) ｜ [官方导出文档](https://support.elicit.com/en/articles/14758189-export-your-data-from-elicit)
- [Stanford Lane：证据合成平台指南](https://laneguides.stanford.edu/c.php?g=1510721) ｜ [价格对比](https://laneguides.stanford.edu/c.php?g=1510721&p=11401116) ｜ [Elicit 评估](https://laneguides.stanford.edu/c.php?g=1510721&p=11361998)
- [CASRAI：Connected Papers 可复现性](https://casrai.org/guides/connected-papers)

**国内竞品与规范**
- [知网格式精灵介绍（高校图书馆公告）](https://library.ougd.cn/info/1128/15252.htm) ｜ [官网](https://pb.cnki.net/typeset/wap/index.html)
- [GB/T 7714—2025 国家标准全文公开系统](https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=C6CE52E55AC09B9C79A20AEA77CEDD14) ｜ [东华理工图书馆：新旧版差异](https://lib.ecut.edu.cn/1c/e7/c2242a138471/page.htm)
- [维普引文查证 AI](https://www.vipslib.com/newsdetail893.html) ｜ [上交学位论文格式指南](https://xsb.seiee.sjtu.edu.cn/seiee_mobile/info/13051.htm)

**技术生态**
- [CSL 官方样式库（10,000+，CC BY-SA 3.0）](https://github.com/citation-style-language/styles) ｜ [Zotero 样式说明](https://www.zotero.org/support/styles)
- [Zotero 中文社区样式库](https://github.com/zotero-chinese/styles) ｜ [中文 CSL 样式商店](https://zotero-chinese.com/styles/)
- [docxtpl 文档](https://docxtpl.readthedocs.io/en/latest/) ｜ [python-docx](https://pypi.org/project/python-docx/) ｜ [citeproc-js](https://github.com/Juris-M/citeproc-js)
- [Zotero Web API v3](https://www.zotero.org/support/dev/web_api/v3/basics) ｜ [Local API](https://www.zotero.org/support/dev/web_api/v3/local_api)
