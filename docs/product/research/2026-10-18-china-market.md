# 国内 AI 学术写作 / 文献综述竞品调研（2026-10）

## 0. 结论

- 生成能力已同质化；**格式合规仍是断点**，但非空白——知网[格式精灵](https://pb.cnki.net/typeset/wap/index.html)已覆盖 1000+ 所高校模板，需求已验证，入口在数据商手里。
- 真正未解的是**引文可核验**：2025 年 2.6% 的论文含至少一条「幻觉引文」，2024 年仅 0.3%（[科技日报](https://finance.sina.cn/2026-04-13/detail-inhuitrc9739255.d.html)）。
- 中文全文库是第三方 AI 拿不到的护城河。

## 1. 产品图谱

### 1.1 数据商系（壁垒＝中文全文库）

| 产品 | 定位与能力 | 定价 | 模板/格式 | 抱怨 |
|---|---|---|---|---|
| [知网研学](https://blog.csdn.net/weixin_68324476/article/details/147398648) | 个人科研工作台；AI 检索/研读/选题/综述、观点对比，Word 插件免费 | 标准 88 元/年、高级 158 元/年、超级会员 2088 元/年（含 AI 综述，第三方口径） | 内置数千种期刊与高校样式，一键生成参考文献 | 引文更新卡顿（[用户](https://m.weibo.cn/detail/5156826753011892)）；AI 综述模板化，需逐条核对 |
| [知网 AI 学术助手](https://ai.cnki.net/study/) | CNKI AI 问答，另有[文献综述](https://aiplus.cnki.net/sumup/professionalSumup)专入口 | 随会员/机构，个人价未确认 | 输出纯文本 | 未确认 |
| [万方「智研 AI+」](https://www.glmu.edu.cn/tsg/info/5749/5416.htm) | 基于 DeepSeek 的"搜读写研"＋申报书生成，另有"慧写" | 机构 IP 开通 | 无排版 | C 端感知弱 |
| [维普智图](https://www.vipslib.com/newsdetail883.html) | 图书馆 AI 门户：智搜、AI 阅读、**AI 综述/开题报告**；2026-05 上线[引文查证 AI](https://www.vipslib.com/newsdetail893.html) | 机构采购 | [查重附带格式分析报告](https://check.weipu.com.cn/) | 仅校园门户内可用 |

### 1.2 深研 / 学术搜索系

| 产品 | 定位与能力 | 定价 | 模板 | 抱怨 |
|---|---|---|---|---|
| [秘塔 AI 搜索](https://metaso.cn) | 无广告带来源搜索，自研 MetaLLM；大纲＋脑图、三档深度、事实核验 | 基础免费；会员价需登录（[未确认](https://www.readaitime.com/tools/metaso)） | 无 | 学术库覆盖不全，引文需回查 |
| [AMiner 沉思](https://www.aminer.cn/aboutus/zh-CN/articles/684652a7da2bd83ee0108a30) | GLM-Z1-Rumination 深研，3 亿+文献、20+ 步推理，5 分钟万字报告 | 免费 | 可导 Word/Markdown，**不适配学校模板** | 报告是"综述式综述"，非学位论文体例 |
| [星火科研助手](http://lib.hitwh.edu.cn/2025/1203/c3547a208082/page.htm) | 讯飞：成果调研、综述生成、2–100 篇问答溯源、翻译润色 | 高校统一认证免费 | 无 | 仅校内 |
| [玻尔 Bohrium](https://bohrium-doc.dp.tech/docs/bohrctl/pricing/) | 科研计算＋智能体：[SciMaster](https://bohrium-doc.dp.tech/docs/bohrctl/scimaster/)（检索＋模拟审稿）、PDF 解析 | 光子：解析 5/页、检索 5–50/次、机时另计 | 无 | 偏理工计算，人文弱 |

### 1.3 通用大模型

| 产品 | 学术/长文档 | 定价 | 格式 | 抱怨 |
|---|---|---|---|---|
| [Kimi](https://www.kimi.com/resources/research-writing-skills) | 百万级上下文、PDF 解析、研究写作技能 | 免费为主 | 纯文本 | 长篇需人肉重构 |
| [豆包](https://www.chinaz.com/2025/0630/1695103.shtml) | 2025-06 上线「深入研究」；专业版 | 68 元档起，包年最高 5088 元（[搜狐](https://www.sohu.com/a/1041308190_255783)） | 无 | 参考文献可能虚构（[测评](https://cloud.tencent.cn/developer/article/2547068)） |
| [通义](https://help.aliyun.com/zh/model-studio/qwen-deep-research) | Qwen-Deep-Research；千问 Skills 有[综述助手](https://www.qianwenai.com/hub/skills/@bailian/ai-powered-literature) | 免费＋API | 无 | 同上 |
| [智谱清言](https://mp.weixin.qq.com/s/FFikcqfkznHFfX6aUEydhQ) | 「研究模式」，沉思模型亦为 AMiner 底座 | 免费＋会员 | 无 | 同上 |

**华为/MindSpore**：MindSpore 为 AI 框架，未发现华为有消费级学术写作产品。

### 1.4 代写 / 降重（价格战与信任危机区）

| 产品 | 定价 | 格式 | 抱怨 |
|---|---|---|---|
| [笔灵](https://www.partnershare.cn/product/bi-ling-ai/details) | 约 29 元/月（第三方，未确认） | 通用模板 | 套话化 |
| [火龙果](https://www.mypitaya.cn/uEhZ04EBaC49W3xjeWx6.html) | 免费；标准 168 元/年；专业 1998 元/年；查重另购 | 校对强、排版弱 | 学术深度不足 |
| [万能小in](https://apps.apple.com/cn/app/id6461309832?see-all=reviews) | 会员＋按篇 | 一键初稿 | 3.8/5（27 评）："论文是套空话""充值后还要再充" |
| [PaperYY](https://www.paperyy.cn/qa?id=6&typeId=2) | 免费额度＋付费 | 无 | 运营方为个体工商户 |
| PaperPass | 约 1.5 元/千字（未确认） | 无 | 与校方结果偏差 |
| [学术志](https://tousu.sina.com.cn/complaint/view/17367234644/) | 1v1 辅导 3.5 万元级 | 人工 | 黑猫：退款扣 1.77 万，"改论文只删一句话" |

## 2. 学位论文格式规范生态

### 2.1 综述章节的硬性要求

综述一般不独立成章，而写在「绪论·国内外研究现状」下，作为二级/三级标题，故格式＝全校体例＋学院补充。以上交为例：一级标题三号粗黑体、二级四号粗黑体、三级小四粗黑体、正文小四或五号宋体，A4 双面（[上交指南](https://xsb.seiee.sjtu.edu.cn/seiee_mobile/info/13051.htm)）。行距、页边距、题注位置各校不一，须以本校文件为准；参考文献普遍要求 GB/T 7714 顺序编码制。

### 2.2 GB/T 7714 现行版本

**GB/T 7714—2025**，2025-12-02 发布、**2026-07-01 实施，全面替代 2015 版**（[国家标准全文公开系统](https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=C6CE52E55AC09B9C79A20AEA77CEDD14)），非等效采纳 ISO 690:2021。主要变化（[东华理工图书馆](https://lib.ecut.edu.cn/1c/e7/c2242a138471/page.htm)）：

- 新增文献类型：会议录、学位论文、报告、标准、网站/网页、档案、地图、**数据集 DS**、**预印本 PP**；新增载体 **MM**；`EB` 改专指网站/网页。
- 术语删「主要责任者」「专著」「数字对象唯一标识符」，增「永久标识符」「预印本」。
- **英文作者由"姓氏全大写"改为"仅首字母大写"**——对参考文献影响最大。

存量模板与 CSL 样式将在 2026 下半年集体失效。

### 2.3 高校差异与模板公开性

差异集中在三处：**标题字号字重、行距页边距、参考文献样式与图表题注**。上交明确"学院可自行制定格式"，即校级之下还有院级变体。多数学校只公开 PDF 规范（[北大](https://sai.pku.edu.cn/)、[浙大](http://grs.zju.edu.cn/)、上交），**Word 模板靠院系线下分发**，无统一仓库；于是社区转做 LaTeX：[thuthesis](https://mirrors.sustech.edu.cn/CTAN/macros/latex/contrib/thuthesis/thuthesis.pdf)、[sjtutex](https://mirror.ox.ac.uk/sites/ctan.org/macros/latex/contrib/sjtutex/sjtutex.pdf)、[hustthesis](https://es.overleaf.com/latex/templates/hustthesis-hua-zhong-ke-ji-da-xue-yan-jiu-sheng-xue-wei-lun-wen-mo-ban/shkvgmnkhcxc)、[zjuthesis](https://github.com/TheNetAdmin/zjuthesis)。**"官方 Word 模板难获取"即需求信号。**

### 2.4 文献管理工具

- [NoteExpress](https://www.inoteexpress.com/aegean/index.php/home/buy/)：个人永久 **¥1980**，内置数千样式、支持分章节管理，国内事实标准，但贵且老。
- 知网研学：文献管理＋Word 插件**免费**，绑定知网库，是最大替代者。
- EndNote：靠机构授权（如[复旦 2026.10–2027.9](https://cz.fudan.edu.cn/fudanstatic/zbggfw/20260922/6476.html)）。
- Zotero：中文靠社区 CSL（[zotero-chinese-styles](https://github.com/starrycw/zotero-chinese-styles)），**新国标落地后样式普遍未跟进**。

### 2.5 「按学校模板自动排版」已有现成解

- **[知网格式精灵](https://library.ougd.cn/info/1128/15252.htm)**（author.cnki.net）：3000–3500 种期刊模板＋**全部 985/211/普通本科的学位论文模板**，上传 Word 后 1–8 分钟出规范稿，宣称准确率 97%+，覆盖版心、页眉页脚、标题、图表公式、参考文献与附录；已进入高校图书馆采购清单。
- 维普查重附带格式分析报告，可校验封面/目录/参考文献合规。
- 短板：它是**排版工具而非写作工具**，不生成内容、不保证引文真实，模板准确率仍靠人工兜底。

### 2.6 查重生态

- 知网个人查重 **1.5 元/千字符**，AIGC 检测 **2 元/千字符**（[国开通道公告](https://www.hfou.net.cn/html/msg/52652.html)）。
- 维普：大学生/研究生/编辑部版均 **3.00 元/千字**（[官方检测站](https://check.weipu.com.cn/)）。
- PaperPass / PaperYY 主打低价 C 端。
- **接口**：知网、维普查重不对外零售开放，只做机构授权与机构专属入口。第三方现实路径是与之做机构联调，或只做引文/格式核验而不做相似度。**结论：查重接口基本拿不到，不应设计进主线。**

## 3. 痛点排序（最痛且未被解决）

1. **引文真实性核验**——格式完美但查无此文，已构成学术不端；维普刚起步且只在机构内。
2. **「生成 → 学校模板」闭环**——格式精灵只做后半段，大模型只做前半段，中间靠复制粘贴与域代码修复。
3. **中文文献可达性**——拿不到知网/万方全文，综述只能基于英文文献与摘要。
4. **综述学理结构**——多为罗列式，缺流派划分、争议点、研究缺口。
5. **新国标迁移**——2026-07-01 生效，全行业样式需重做。
6. **价格与信任**——代写类退款纠纷拉低品类信任。

## 4. 对「导入学校模板 → 导出综述」的判断

**需求真实，但不是空白；赢点在闭环而非排版。**

- 需求侧：格式精灵覆盖 1000+ 模板已证明刚需，但学生端缺"从选题到定稿都合规"的工具。
- 竞争侧：知网已用格式精灵占住排版格，且有模板库＋全文库。正面拼模板数量必输。
- 切口：①**生成即合规**，把模板规则前置为生成约束（标题层级、题注、参考文献样式），而非事后排版；②**引文可核验**，每条文献回链知网/万方/Crossref，输出"可核验率"；③**新国标先发**，抢在 2026-07-01 前把 GB/T 7714—2025 设为默认。
- 风险：模板获取与维护成本高（院级还有变体）；格式错误责任重；知网双壁垒难绕，更现实是**做其上游的内容与合规层，而非替代品**。

## 5. 未确认

秘塔会员价、知网 AI 助手/格式精灵个人定价、万方智研个人价、笔灵与 PaperPass 现价、各校是否已按 2025 版更新模板。
