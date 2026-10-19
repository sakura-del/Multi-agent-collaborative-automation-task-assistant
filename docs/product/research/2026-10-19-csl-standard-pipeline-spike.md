# 纯 Python 标准 CSL 1.0.2 管线 + 逐条 locale 切换 实测（Option C 证据）

调研/实测时间：2026-10-19 ｜ 执行者：lead ｜ 环境：Windows + Python 3.11.2 + citeproc-py **0.11.1**（临时 venv，未污染项目依赖）

**目的**：Issue #5 把选择框定在「A 单语言」与「B 引入 citeproc-js/CSL-M」之间。本实测检验是否存在第三条路：
**用标准 CSL 1.0.2 引擎 + 逐条（per-entry）locale 切换**得到双语混排（中文"等" / 英文 "et al."）。

---

## 1. 取样材料（真实文件，非二手描述）

| 材料 | 来源 | 大小/校验 |
| --- | --- | --- |
| 官方 `china-national-standard-gb-t-7714-2015-numeric.csl` | `citation-style-language/styles` master | 15,530 字节 |
| 社区 `GB-T-7714—2025（顺序编码，双语）.csl` | `zotero-chinese/styles` main，路径含中文全角括号 | 16,786 字符 |
| 社区 `GB-T-7714—2025（著者-出版年，双语）.csl` | 同上 | 19,341 字符 |
| locales `zh-CN` / `en-US` | `citation-style-language/locales` master | 18,641 / 32,649 字节 |

社区库统计（GitHub Trees API，`truncated=false`）：**.csl 共 343 个；GB/T 7714—2025 共 9 个**（顺序编码/著者-出版年/注释 × 双语/无URL无DOI/全角符号等变体）；2015 共 6 个。

> 注意：Issue #4 备注写「GB/T 7714—2025 的 CSL 官方仓库未收录、需自行编写」——**官方仓库确实没有**，但**社区库已有 9 个 2025 变体**，可作为自研样式时的参考实现（授权 CC BY-SA 3.0，需署名）。

## 2. 社区「双语」样式的 CSL-M 依赖到底有多大？

对 `GB-T-7714—2025（顺序编码，双语）.csl` 做构造扫描：

| CSL-M 特有构造 | 出现次数 |
| --- | --- |
| `<layout locale="en">`（布局级 locale 属性） | **1** |
| `<term name="volume" form="short">第%s卷</term>`（`%s` 占位） | 1 |
| `<term name="citation-range-delimiter">` | 1 |
| `cs:institution` / `cs:conditions` / `cs:alternative` / `jurisdiction` | 0 |

**关键事实**：该样式的两个 `<layout>` 块**除 `locale="en"` 属性外逐字节相同**：

```xml
<bibliography entry-spacing="0" et-al-min="4" et-al-use-first="3" second-field-align="flush">
  <layout locale="en"> <!-- CSL-M 扩展 -->
    <text variable="citation-number" prefix="[" suffix="]"/>
    <text macro="entry-layout" suffix="."/>
  </layout>
  <layout>
    <text variable="citation-number" prefix="[" suffix="]"/>
    <text macro="entry-layout" suffix="."/>
  </layout>
</bibliography>
```

即：**该样式之所以是 CSL-M，唯一结构性原因是「想要英文条目走英文 locale」**——而这正是「逐条 locale 切换」在应用层可以替代的能力。

## 3. 实测结果（原始输出）

### 3.1 官方 2015 样式 + 逐条 locale

```
--- C1 中文条目单独用 zh-CN (locale=zh-CN)
    [1]牛 耕田, 李 泽, 张 三, 等. 面向学位论文的中文参考文献著录规则研究. 中国科技期刊研究, 2024, 35(2): 100-110.
--- C2 英文条目单独用 en-US (locale=en-US)
    [1]ZHANG W, SMITH J, GARCIA M, et al. A Survey of Citation Style Engines for Bilingual Bibliographies. Journal of Scholarly Publishing, 2024, 12(4): 55-70.
--- A 默认 locale（样式 default-locale=zh-CN，整批一次渲染，对照）
    [1]牛 耕田, 李 泽, 张 三, 等. 面向学位论文的中文参考文献著录规则研究. 中国科技期刊研究, 2024, 35(2): 100-110.
    [2]ZHANG W, SMITH J, GARCIA M, 等. A Survey of Citation Style Engines for Bilingual Bibliographies. Journal of Scholarly Publishing, 2024, 12(4): 55-70.
```

→ 整批渲染（A）英文条目错用「等」；**逐条切换 locale（C1/C2）双语正确**。

### 3.2 社区 2025 样式「机械归一」后再逐条渲染

归一操作：仅删除 `<layout locale="en">…</layout>` 块（16,786 → 16,620 字符），其余不动。

```
--- G1 社区 2025 归一版 · 中文条目 zh-CN
    [1]牛 耕田，李 泽，张 三，等. 面向学位论文的中文参考文献著录规则研究. 中国科技期刊研究，2024，35（2）：100-110.
--- G2 社区 2025 归一版 · 英文条目 en-US
    [1]Zhang W，Smith J，Garcia M，et al. A Survey of Citation Style Engines for Bilingual Bibliographies. Journal of Scholarly Publishing，2024，12（4）：55-70.
--- G3 社区 2025 归一版 · 整批 zh-CN（对照）
    [2]Zhang W，Smith J，Garcia M，等. A Survey of Citation Style Engines for Bilingual Bibliographies. …
```

→ **社区 GB/T 7714—2025 双语样式去掉 1 个 CSL-M 块后，在纯 Python 标准引擎下即得到正确的双语输出。**

### 3.3 著者-出版年样式同样可用（含已知代价）

```
GB-T-7714—2025（著者-出版年，双语）.csl: 原始 19341 字符 → 归一 19238 字符（移除 CSL-M layout 1 处）
含 <sort> 的 bibliography: True
--- H1 中文条目 (zh-CN):  牛 耕田，李 泽，2024. 中文文献甲. 中国科技期刊研究，35（2）：100-110.
--- H2 英文条目 (en-US):  Zhang W，2019. An English Entry. Journal of Scholarly Publishing，12（4）：55-70.
```

### 3.4 编号：预设 `citation-number` **不生效**（实测）

```
--- E1 预设 citation-number=3 的中文条目 (locale=zh-CN)
    [1]牛 耕田, 李 泽, 张 三, 等. …
```

→ 逐条渲染时引擎仍从 1 开始编号，**序号必须由应用层自己生成**（GB/T 7714 顺序编码制本就要求按引用顺序编号，应用层持有引用顺序，属于应有的职责）。

## 4. 由此确定的代价与边界（供 ADR 权衡）

| 能力 | 整批渲染 | 逐条渲染（Option C） |
| --- | --- | --- |
| 双语词条（等 / et al.） | ❌ 只能一种 | ✅ 逐条切换 locale |
| 引擎分配的连续编号 | ✅ | ❌ 需应用层编号（实测 `citation-number` 预设无效） |
| 样式内 `<sort>`（著者-出版年排序） | ✅ | ❌ 需应用层排序或「按 locale 分组渲染 + 合并」 |
| 依赖整批状态的特性（消歧、ibid、后续作者替代等） | ✅ | ❌ 不适用（GB/T 7714 两制式均不需要） |

## 5. 尚未验证（不写进结论）

- citeproc-py 对**全部**官方样式元数据的兼容面（本次只验证 GB/T 7714 系列 3 份样式）；
- 复杂类型（专利、标准、报纸、电子资源）在归一版样式下的输出正确性；
- 与 Word 域的联动（本项目走 Markdown/DOCX 直排，不依赖 Zotero 域）。

原始输出文件（临时目录，未入库）：`spike_out.md` / `spike_numbering.md` / `spike_community_normalized.md` / `spike_author_date.md`。
