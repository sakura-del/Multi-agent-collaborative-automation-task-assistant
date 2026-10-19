# ADR 0001：中文双语 CSL-M 支持策略

- **状态**：**Accepted（已决策）**，2026-10-19
- **议题**：GitHub Issue [#5](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/5)（P1，阻塞 [#4](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/4) 的双语部分）
- **决策者**：项目 lead（依据下列一手证据）
- **证据文档**：
  - [2026-10-19-csl-engine-licensing.md](../product/research/2026-10-19-csl-engine-licensing.md)（许可证与维护状态，一手来源）
  - [2026-10-19-csl-standard-pipeline-spike.md](../product/research/2026-10-19-csl-standard-pipeline-spike.md)（标准 1.0.2 管线 + 逐条 locale 实测）
  - [2026-10-19-citeproc-js-spike.md](../product/research/2026-10-19-citeproc-js-spike.md)（citeproc-js / CSL-M 实测）
  - 上游调研：[2026-10-18-template-and-csl-tech.md](../product/research/2026-10-18-template-and-csl-tech.md) §B

---

## 1. 背景

Issue #4 要做「CSL 引用引擎 + GB/T 7714—2025 默认」。学位论文场景中**中英文文献混排是常态**，
而中文社区 CSL 样式实现「中文条目显示『等』、英文条目显示 "et al."」依赖 **CSL-M 扩展**
（citeproc-js 的私有扩展，不属于 CSL 1.0.2 标准）。

Issue #5 因此把选择框定为两个选项：**A 接受单语言输出**（纯 Python、标准样式）或
**B 引入 citeproc-js 走 CSL-M**（支持双语，但要引入 Node 运行时并容忍样式校验告警，
且 citeproc-js 的 SPDX 许可证当时**未确认**）。

本 ADR 在调研中发现了**第三条路（C）**，并用一手实测证明它同时满足「双语」与「纯标准栈」。

## 2. 关键事实（均为一手实测/一手来源，非二手转述）

### 2.1 社区双语样式的 CSL-M 依赖到底有多大

对 `zotero-chinese/styles`（343 个 .csl，含 **9 个 GB/T 7714—2025**、6 个 2015 变体）抽样扫描，
CSL-M 特有构造**因变体而异，为 1–4 类**：

| 构造 | 2025 顺序编码双语（本次抽样） | 2015 各变体 |
| --- | --- | --- |
| `<layout locale="en">`（布局级 locale） | **1** | 1–2 |
| `<institution/>` | 0 | 0–3 |
| `<if has-day="issued">` | 0 | 1 |
| `<term name="citation-range-delimiter">` | 1 | 0–1 |
| `<term …>第%s卷</term>` | 1 | 0 |

**最关键的一点**：2025 顺序编码双语样式的两个 `<layout>` 块**除 `locale="en"` 属性外逐字节相同**——
该样式之所以是 CSL-M，唯一结构性原因是「想让英文条目走英文 locale」，
而这正是**应用层逐条 locale 切换**可以替代的能力（见 2.2）。

官方 `china-national-standard-gb-t-7714-2015-numeric.csl` 自己也把 CSL-M 段落**整段注释掉**，
注释原文写着「取消这部分注释可以开启 CSL-M 的多语言功能：按照文献的语言输出 "et al." 等术语」——
即上游作者清楚这是可选的非标准能力。

### 2.2 逐条 locale 切换实测（纯 Python 标准 CSL 1.0.2）

引擎：`citeproc-py` **0.11.1**（BSD-2-Clause-Views），Python 3.11。原始输出：

```
# 官方 2015 样式，逐条切换 locale
[zh-CN] 牛 耕田, 李 泽, 张 三, 等. 面向学位论文的中文参考文献著录规则研究. 中国科技期刊研究, 2024, 35(2): 100-110.
[en-US] ZHANG W, SMITH J, GARCIA M, et al. A Survey of Citation Style Engines for Bilingual Bibliographies. Journal of Scholarly Publishing, 2024, 12(4): 55-70.

# 社区 GB/T 7714—2025 双语样式「机械归一」（仅删除 <layout locale="en"> 块，16786 → 16620 字符）
[zh-CN] 牛 耕田，李 泽，张 三，等. 面向学位论文的中文参考文献著录规则研究. 中国科技期刊研究，2024，35（2）：100-110.
[en-US] Zhang W，Smith J，Garcia M，et al. A Survey of Citation Style Engines for Bilingual Bibliographies. Journal of Scholarly Publishing，2024，12（4）：55-70.
```

**结论：双语混排不需要 CSL-M，也不需要 Node。** 著者-出版年制式同样验证通过。

### 2.3 citeproc-js / CSL-M 路径（Option B）实测与成本

- 收益**真实存在**：citeproc-js 在 CSL-M 模式下确实输出「中文=等 / 英文=et al.」。
- 启用**不需要任何开关**：`forceLang` 传与不传输出逐字节一致，能力完全由样式的 `<layout locale="en">` 承担。
- 体积**不是问题**：npm 包名是 **`citeproc`**（`citeproc-js` 在 npm 不存在，E404），1 个包 / 0 传递依赖 / 976 KB。
- 真正成本有三项：
  1. **样式合法性降级**：官方 CSL 1.0.2 RELAX NG 判定双语样式 **invalid**（5 处错误），
     而两份官方 GB/T 样式 valid、去掉 CSL-M 的等价样式 valid；
  2. **风险是静默的**：citeproc-js 对非标准构造**不报错、不告警**（日志全空）——一旦将来更换处理器，
     问题才会暴露；这不满足 Issue #5 要求的「不能静默吞掉」；
  3. **需要第二个运行时**（Node 子进程/sidecar）与数据前提（每条文献必须有 `language` 字段，
     缺字段会回落 zh-CN 输出「等」，已实测）。

### 2.4 许可证（Issue #5 决策标准 3）

| 引擎 | 许可证（一手原文） | 版本 / 状态 |
| --- | --- | --- |
| **citeproc-js** | **CPAL-1.0（或更高）OR AGPL-3.0（或更高）**；npm/package.json 却写 `CPAL-1.0 OR AGPL-1.0`（**元数据自相矛盾**） | npm `citeproc@2.4.63`（2023） |
| citeproc-rs | MPL-2.0 | **已归档**（2021-12-14），crates.io 无发布 |
| **citeproc-py** | **BSD-2-Clause-Views** | **0.11.1，2026-09-01**（2026 年内 ≥12 个版本，**并非停滞**） |
| pandoc | GPL-2.0-or-later（GPLv2 **无**网络服务条款）；其 CSL 库 jgm/citeproc 为 BSD-2 | 3.12.1（2026-10-08） |
| citation-js | 外层 MIT，但依赖 `citeproc` → **仍内嵌 citeproc-js** | 0.9.0（2026-09-18） |

citeproc-js 的 **CPAL 分支触发点比 AGPL 更宽**：CPAL §15 把「作为意图通过网络使用的应用提供」
**明文视为分发**，从而触发 §3.1/§3.2 的源码可得义务（**不以「是否修改」为前提**）；
CPAL §14 的署名义务因上游 Exhibit B 已填写而**不适用豁免**，且明写 "required in Larger Works"。

> ⚠️ 本节只陈述条款事实，**不构成法律意见**；「零改动容器 + 独立进程调用是否触发上述义务」仍**未确认**，
> 如未来要采用 B 路线，必须先过法务。

## 3. 选项与权衡

| | A 仅标准 CSL 1.0.2（单语言） | B 引入 citeproc-js / CSL-M | **C 标准 CSL 1.0.2 + 逐条 locale 切换** |
| --- | --- | --- | --- |
| 中英混排（等 / et al.） | ❌ 全篇只能一种 | ✅ 实测成立 | ✅ **实测成立** |
| 运行时 | 纯 Python | **+ Node 第二运行时** | 纯 Python |
| 样式合法性 | ✅ 1.0.2 valid | ❌ 官方 schema invalid、且被静默接受 | ✅ 1.0.2 valid |
| 许可证 | BSD（citeproc-py） | **CPAL/AGPL 需法务**（且元数据矛盾） | BSD-2（citeproc-py） |
| 可用样式范围 | 官方 10,000+ + 自研 | 社区 CSL-M 样式可直接用 | 官方 + 自研 + **归一后的**社区样式 |
| 引擎编号 / 样式内排序 | 引擎负责 | 引擎负责 | **应用层负责**（见 §4.2） |
| 商用门槛 | 低 | **高（copyleft + 署名 + 法务）** | 低 |

**决定性权衡**：B 的唯一独有能力（CSL-M 双语）已被 C 用 20 行应用层逻辑等价替代（§2.2 实测），
而 B 的代价（copyleft 义务 + 元数据矛盾的许可 + 静默的样式非法 + 第二运行时 + 数据前提）
全部是**长期、真实、且落在商用主路径上**的成本。因此 B 的收益不足以覆盖其代价。

## 4. 决策

> **采用 Option C：以标准 CSL 1.0.2 为唯一渲染栈；双语通过「按条目语言逐条切换 locale」在应用层实现；
> 不引入 citeproc-js，不引入 Node 运行时。**

具体化：

1. **引擎**：`citeproc-py` 0.11.1（BSD-2-Clause-Views）为首选，锁版本；
   封装在我们自己的适配器接口之后（`CitationRenderer`），**不把 citeproc-py 的对象泄漏到业务层**。
2. **样式**：自研 `GB/T 7714—2025`（顺序编码制 + 著者-出版年制）为标准 CSL 1.0.2；
   社区样式（CC BY-SA 3.0）仅作**参考实现**，采用时须归一化并保留署名。
3. **双语**：按条目的 `language` 字段选择 locale 分组渲染，中文组用 `zh-CN`、英文组用 `en-US`
   （缺失 `language` 时按题名/期刊的字符集启发式判定，并记录告警）。
4. **编号与排序由应用层负责**（实测：CSL 引擎**忽略**预设 `citation-number`，见证据文档 §3.4）：
   顺序编码制的 `[n]` 由我们按引用顺序生成；著者-出版年制的排序由我们按 CSL-JSON 字段实现。
   → 因此正文引用标记必须存成 `[[cite:ref_id]]` 占位符，**不得**硬编码数字（否则切换制式即全废）。
5. **失效与升级路径**：若 golden 测试暴露 citeproc-py 在必需构造上的缺陷，
   **在同一 Option C 内**切换引擎到 `pandoc --citeproc`（GPL-2.0-or-later，独立进程；
   注意那是**分发 GPL 二进制**的义务，而非本次的 copyleft 传染问题），**不回到 Option B**。

### 4.1 样式准入与校验策略（对应 Issue #5「不能静默吞掉」的要求）

| 环节 | 策略 |
| --- | --- |
| 白名单 | ① 官方 `citation-style-language/styles`（CC BY-SA 3.0，署名 + 链接 citationstyles.org）；② 自研样式；③ 经归一化 + golden 测试通过的社区样式 |
| 上传校验 | 官方 **CSL 1.0.2 RELAX NG** schema 校验；**另加**自研的 CSL-M 构造扫描器（`<layout locale=…>`、`<institution>`、`has-day`、`citation-range-delimiter`、术语 `%s` 等） |
| 不合规处理 | **显式拒绝并逐条列出违规构造与行号**，提示「该样式依赖 CSL-M 私有扩展，本产品仅支持标准 CSL 1.0.2」；**绝不静默接受、绝不清默降级** |
| 归一化 | 允许对社区样式做机械归一（删除 `<layout locale="en">` 块等），归一后必须通过 schema 校验 + golden 测试，并保留原样式署名（CC BY-SA） |
| 运行时兜底 | 渲染失败时**回退到内置极简格式化器**并在报告中标注降级，而不是静默输出半成品 |

### 4.2 数据前提（必须在 #4 的引用层落实）

- 每条参考文献必须有 **`language`** 字段（`zh-CN` / `en-US` …）——它决定 locale 分组；
- 正文引用必须是 `[[cite:ref_id]]` 占位符，编号与排序在渲染期生成；
- 内部以 **CSL-JSON 为唯一真源**（与既有调研一致），`[J]/[M]/[D]` 类型标识走 CSL type 映射。

## 5. 后果

**正面**
- 双语达标（学位论文硬需求），且**没有任何 copyleft 义务**、无第二运行时、无 Node 运维面；
- 样式保持标准 1.0.2 → 与 Zotero / Word / 其他处理器可互通，未来换引擎成本低；
- 编号与排序握在自己手里（本来就要做，用于制式切换），不依赖引擎内部状态。

**负面 / 需要接受**
- 必须**自研 GB/T 7714—2025 样式**（Issue #4 已认定官方仓库未收录，本是计划内工作）；
- 逐条（分组）渲染**放弃整批特性**：引擎编号、样式内 `<sort>`、跨条目消歧/ibid 等，需应用层实现或明确不支持；
- `citeproc-py` 自述 CSL 测试通过率约 **60%**、Alpha 状态 → **必须用 golden 测试兜住我们真正用到的构造**；
- 不能直接接受任意 CSL-M 样式（有准入与拒绝策略），个别法律/机构样式需要归一化才能用。

**中性**
- 社区样式仍可用，但要过一次归一化与测试；署名义务（CC BY-SA 3.0）随之落到样式文件上。

## 6. 失效条件（何时应重开本决策）

1. 出现**用户硬需求**要使用的样式依赖 CSL-M 的结构性能力（如 `<institution>`、多 locale 布局字段集不同），
   且归一化会改变输出语义——此时须带着法务意见重新评估 Option B；
2. `citeproc-py` 被归档或 18 个月内无新版本，或 golden 测试在必需构造上失败——
   **先在 Option C 内部切到 `pandoc --citeproc`**，仍不满足才重开；
3. 产品定位变化为「必须支持 Zotero 全量社区样式的在线渲染」——这属于产品范围变更，需重新评估运行时与许可预算；
4. 上游 CSL 规范收录 CSL-M 能力（例如 1.1/2.0 正式纳入多语言）——届时可回到纯标准栈直接支持，属利好变更。

## 7. 对 Issue #4 的解锁与待办

本 ADR **解除 Issue #4 双语部分的阻塞**，并给出 #4 的实施要点：

1. 自研样式：`GB/T 7714—2025`（顺序编码制 + 著者-出版年制），标准 CSL 1.0.2，附 golden 测试；
2. 渲染适配器 `CitationRenderer`：CSL-JSON → 逐条 locale 分组渲染 → 应用层编号/排序 → 注入 Markdown/DOCX；
3. 样式准入服务：RELAX NG 校验 + CSL-M 扫描 + 显式拒绝文案（§4.1）；
4. 引用层数据改造：`language` 字段补齐、`[[cite:ref_id]]` 占位符、CSL-JSON 唯一真源；
5. 既有文档更正：npm 包名是 `citeproc`（非 `citeproc-js`）；「citeproc-py 维护停滞」与一手证据不符
   （0.11.1 / 2026-09-01，2026 年 ≥12 个版本），真实短板是**规格覆盖率**而非停滞。
