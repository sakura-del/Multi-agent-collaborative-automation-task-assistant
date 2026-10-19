# citeproc-js / CSL-M 双语渲染一手实测（Issue #5 · Option B 证据）

> 实测时间：2026-10-19 ｜ 执行者：teammate `csl-node-spike`（共享任务 task-10）
> 工作目录：`%TEMP%\csl-js-spike`（`C:\Users\Lenovo\AppData\Local\Temp\csl-js-spike`）——**仓库代码 / `backend/.venv` / 依赖 / 配置均未改动，无 git commit**
> 本机环境：Windows（`win32 x64`）、**Node v22.19.0**、npm 11.6.0、npm registry = `https://registry.npmmirror.com`
> 原始输出全文（未删节）：`$TEMP\csl-js-spike\out\render-report.txt`、`render-report-std.txt`、`validation-report.txt`、`variant-validation-report.txt`、`narrow-report.txt`、`delta-report.txt`

---

## 0. 结论速览（TL;DR）

| # | 待验证假设 | 实测结果 | 证据 |
|---|---|---|---|
| 1 | npm 上有 `citeproc-js` 包，`npm view citeproc-js ...` 可查到版本/许可 | ❌ **该包名不存在（E404）**。真实包名是 **`citeproc`**，最新 **2.4.63**（2023-07-29），`license` 字段 `CPAL-1.0 OR AGPL-1.0` | §1 |
| 2 | CSL-M 双语收益（中文「等」/ 英文 "et al."）真实存在 | ✅ **实测成立**。同一份样式、同一批 CSL-JSON：中文条目输出 `…张伟，等.`，英文条目输出 `…WILLIAMS M，et al.` | §3 |
| 3 | 启用 CSL-M 需要复杂开关 | ❌ **不需要**。citeproc-js 1.4.61 无条件支持样式里的 `<layout locale="en">`；构造函数 3 种调法输出**逐字节一致** | §3.2 |
| 4 | CSL-M 样式会被标准 CSL 1.0.2 校验拒绝 | ✅ **成立**。官方 `csl.rnc` RELAX NG 校验：双语样式 **invalid**，两份官方 GB/T 样式 **valid**，去掉 4 处 CSL-M 构造的等价样式 **valid** | §4 |
| 5 | Option A（纯标准 CSL 1.0.2）拿不到双语 | ✅ **成立**。用「去 CSL-M 的等价样式」渲染，英文条目变成 `…WILLIAMS M，等.` | §5 |
| 6 | 依赖体积是成本大头 | ❌ 体积很小（**1 个包 / 0 传递依赖 / 976 KB**）；成本在**第二个运行时（Node）**、**样式校验豁免**、**CPAL/AGPL 许可** | §6 |
| 7 | Zotero 条目没写 `language` 也能双语 | ❌ **不能**。缺 `language` 的英文条目回落到 `default-locale="zh-CN"` → 输出「等」 | §3.3 |

---

## 1. Step 1：npm 包名、版本、许可字段（**与原任务描述不符，已按实测修正**）

### 1.1 原命令直接失败（E404）

```powershell
PS> npm view citeproc-js version license time.modified
```

原始输出（节选，含 stderr）：

```
npm error code E404
npm error 404 Not Found - GET https://registry.npmjs.org/citeproc-js - Not found
npm error 404
npm error 404 The requested resource 'citeproc-js@*' could not be found or you do not have permission to access it.
npm error 404 Note that you can also install from a
npm error 404 tarball, folder, http url, or git url.
[exit code: 1]
```

补充核验：直连官方 registry 同样 404（排除镜像不同步的可能）：

```powershell
PS> npm view citeproc-js version license --registry=https://registry.npmjs.org --json
```
```
{
  "error": {
    "code": "E404",
    "summary": "Not Found - GET https://registry.npmjs.org/citeproc-js - Not found",
```

> 结论：**citeproc-js 没有发布到 npm**。Zotero/Juris-M 的仓库名是 `Juris-M/citeproc-js`，但 npm 上的包名是 `citeproc`。

### 1.2 真实包：`citeproc`

```powershell
PS> npm view citeproc version license time.modified --json
```
```
{
  "version": "2.4.63",
  "license": "CPAL-1.0 OR AGPL-1.0",
  "time.modified": "2023-07-29T14:33:00.727Z"
}
```

其它元数据（`npm view citeproc --json` 节选）：

```
"description": "The citeproc-js citation formatting module, in CommonJS format. This version is based on citeproc-js 1.4.63",
"homepage": "https://github.com/juris-m/citeproc-js#readme",
"repository": { "type": "git", "url": "git+https://github.com/juris-m/citeproc-js.git" },
"main": "citeproc_commonjs.js",
"dependencies": {},
"time": { "created": "2022-01-26T21:05:07.450Z", "modified": "2023-07-29T14:33:00.727Z" }
```

安装实测（这就是本次实测用的依赖）：

```powershell
PS> npm init -y; npm install citeproc --no-audit --no-fund
```
```
added 1 package in 2s
```

```powershell
PS> npm view citeproc dist.unpackedSize dist.fileCount dist.tarball --json
```
```
{
  "dist.unpackedSize": 975635,
  "dist.fileCount": 4,
  "dist.tarball": "https://registry.npmmirror.com/citeproc/-/citeproc-2.4.63.tgz"
}
```

`node_modules/citeproc` 实际落盘体积：**976,056 字节**（含 `citeproc_commonjs.js` 967,493 字节 / `LICENSE` / `package.json` / `README.rst`），**无任何传递依赖**。

### 1.3 许可（直接回答 Issue #5 里「SPDX 许可未确认」）

`node_modules/citeproc/package.json`：

```json
"license": "CPAL-1.0 OR AGPL-1.0"
```

`node_modules/citeproc/LICENSE`（**正文与字段不一致**，原文照抄）：

```
	Copyright (c) 2009-2019 Frank Bennett

	This program is free software: you can redistribute it and/or
	modify it under EITHER

      * the terms of the Common Public Attribution License (CPAL) as
	    published by the Open Source Initiative, either version 1 of
	    the CPAL, or (at your option) any later version; OR

      * the terms of the GNU Affero General Public License (AGPL)
        as published by the Free Software Foundation, either version
        3 of the AGPL, or (at your option) any later version.
```

- 双许可：**CPAL-1.0（或更新）或 AGPL-3.0（或更新）**，二选一。
- ⚠️ **元数据与正文冲突**：字段写 `AGPL-1.0`，正文写 `AGPL 3.0 or later`；做 ADR/法务结论前应以正文 + 上游仓库为准。
- 上游 `Juris-M/citeproc-js` master 的 `package.json` 与 npm 包**逐字节同源**（同为 `2.4.63`），`LICENSE` 文件 sha256 也相同（`b96ca8e1716f93449b480083c7510c18d129fa9afa4644d62c6347c06177ce30`）。
- 含义：**不是 MIT**。闭源 SaaS 若选 CPAL，需要履行署名/来源标注与网络使用相关义务；若选 AGPL，按 AGPL 处理。**具体法律结论超出本次实测范围**。

### 1.4 引擎真实版本号（易踩坑）

`CSL.PROCESSOR_VERSION` 实测输出为 **1.4.61**，而 npm 包版本是 **2.4.63**（描述里自称 "based on citeproc-js 1.4.63"）。即 **npm 版本号 ≠ 引擎版本号**，引用版本时必须写清是哪一个（见 `out/render-report.txt` 第 4–5 行）：

```
citeproc (npm) version: 2.4.63
citeproc-js PROCESSOR_VERSION: 1.4.61
```

---

## 2. Step 2：真实中文社区双语样式

### 2.1 拉取记录（URL + 字节数 + sha256）

`zotero-chinese/styles`（默认分支 `main`；GitHub API 元数据：`stars 6337`、`pushed_at 2026-08-23`；仓库 `license.spdx_id = NOASSERTION`）：

```
https://raw.githubusercontent.com/zotero-chinese/styles/main/src/GB-T-7714%E2%80%942015%EF%BC%88%E9%A1%BA%E5%BA%8F%E7%BC%96%E7%A0%81%EF%BC%8C%E5%8F%8C%E8%AF%AD%EF%BC%89/GB-T-7714%E2%80%942015%EF%BC%88%E9%A1%BA%E5%BA%8F%E7%BC%96%E7%A0%81%EF%BC%8C%E5%8F%8C%E8%AF%AD%EF%BC%89.csl
```

下载脚本原始输出（`node fetch-style.mjs`）：

```
path: src/GB-T-7714—2015（顺序编码，双语）/GB-T-7714—2015（顺序编码，双语）.csl
  URL: https://raw.githubusercontent.com/zotero-chinese/styles/main/src/GB-T-7714%E2%80%942015%EF%BC%88%E9%A1%BA%E5%BA%8F%E7%BC%96%E7%A0%81%EF%BC%8C%E5%8F%8C%E8%AF%AD%EF%BC%89/GB-T-7714%E2%80%942015%EF%BC%88%E9%A1%BA%E5%BA%8F%E7%BC%96%E7%A0%81%EF%BC%8C%E5%8F%8C%E8%AF%AD%EF%BC%89.csl
  bytes: 15000
  sha256: b1fca9249c1d9446e082703ae6f18a3baf78e647c3bd2e0ae5ec8032769617e2
  sha1(computed): eed91ad8099db5af5a5aa1791d7d791b1ad93d74
  git_blob_sha1(api): d3f18c30352af5abcd248f5767e83252576d3d86
```

同目录还拉取了样式自带测试语料：`items.json`（226 条真实 CSL-JSON，均带 `language` 字段）、`cites.json`、`metadata.json`。

样式元信息（文件内 `<info>`）：

- 标题 `GB/T 7714—2015（顺序编码，双语）`，`<summary>按照语言显示“等”或“et al.”</summary>`
- 作者 牛耕田 / 贡献者 Zeping Lee，`<updated>2026-08-09T17:03:53+08:00</updated>`
- `<rights license="http://creativecommons.org/licenses/by-sa/3.0/">`（CC BY-SA 3.0）
- 模板来源 `http://www.zotero.org/styles/china-national-standard-gb-t-7714-2015-numeric`

### 2.2 该样式用到的 CSL-M 特有构造（4 类，逐一定位）

| # | 构造 | 位置（行号） | 为什么非标准 1.0.2 |
|---|---|---|---|
| 1 | `<bibliography>` 里**两个 `<layout>`**，其一 `locale="en"` | 426–433 | `csl.rnc` 中 `bibliography.layout` 只允许**一个** `cs:layout`，且其内容模型只有 `affixes, font-formatting, rendering-element+`，**没有 `locale` 属性**（`schemas/styles/csl.rnc` 第 348–356 行） |
| 2 | `<institution/>`（3 处） | 49、185、195 | `institution` 在 CSL 1.0.2 schema 全部 7 个 `.rnc` 文件中**零匹配**（CSL-M 元素） |
| 3 | `<if has-day="issued">` | 315 | `condition` 取值集合只有 `disambiguate / is-numeric / is-uncertain-date / locator / position / type / variable`（`csl-choose.rnc` 第 15–57 行），无 `has-day` |
| 4 | `<locale>`（无 `xml:lang`）中的术语 `<term name="citation-range-delimiter">` | 39 | CSL 1.0.2 术语表只有 `page-range-delimiter`（`csl-terms.rnc` 第 77 行），**没有 `citation-range-delimiter`** |

> 注意：`<text variable="volume-title">`、`type="classic"`、`variable="language"` **都是**标准 1.0.2（分别在 `csl-variables.rnc` 第 82/111 行、`csl-types.rnc` 第 15 行），不要误判。

---

## 3. Step 3/4：CSL-M 模式渲染实测（**关键断言**）

### 3.1 调用方式与测试数据

```js
const CSL = require('citeproc');
const sys = {
  retrieveLocale: (lang) => fs.readFileSync(`locales/locales-${lang}.xml`, 'utf8'),
  retrieveItem: (id) => itemsById[id],
};
const engine = new CSL.Engine(sys, styleXml, 'zh-CN', true); // 第 4 参 = forceLang
engine.setOutputFormat('text');
engine.updateItems([...ids]);
const bib = engine.makeBibliography();   // bib[1] 即参考文献条目字符串数组
```

locale 文件同样取自官方仓库（`citation-style-language/locales` master）：`locales-zh-CN.xml`（sha256 `95cebc66…`）、`locales-en-US.xml`（sha256 `ac864c7c…`）。**locale 文件是必需的外部资源**（`sys.retrieveLocale` 必须实现）。

测试条目：受控中文条目（zh-CN，4 作者）、受控英文条目（en-US，4 作者）、无 `language` 的英文条目（4 作者）、以及样式自带真实语料 2 条（zh-CN 5 作者 / en-US 80 作者）。样式 `bibliography` 上设 `et-al-min="4" et-al-use-first="3"`，故 4 作者才会触发 et-al。

### 3.2 原始输出（A. `new CSL.Engine(sys, style, "zh-CN", true)`，text 格式）

```
processor_version=1.4.61 csl_version=1.0 style_version=1.0
default-locale=["zh-CN"]  multi_layout=[[{"base":"en-US","generic":true,"best":"en-US","bare":"en"}]]
entry_ids=[["spike-zh-4authors"],["spike-en-4authors"],["spike-en-4authors-nolang"],["gbt7714-2015.4.2.2:3"],["gbt7714-2015.4.4.2:9"]]  maxoffset=3

--- 处理器日志/告警: 无 ---

--- 原始条目输出 ---
[0] id=["spike-zh-4authors"]
[1] 王建国，李晓明，张伟，等. 多智能体协作的任务编排方法研究[J]. 计算机学报，2025，47（3）：1-12.

[1] id=["spike-en-4authors"]
[2] SMITH J，JOHNSON E，WILLIAMS M，et al. A survey of multi-agent task orchestration[J]. Journal of Artificial Intelligence Research，2024，80（2）：100-130.

[2] id=["spike-en-4authors-nolang"]
[3] MILLER A，DAVIS R，WILSON L，等. Multi-agent orchestration without a language field[J]. Journal of Artificial Intelligence Research，2024，81（1）：10-20.

[3] id=["gbt7714-2015.4.2.2:3"]
[4] 陈晋镳，张惠民，朱士兴，等. 蓟县震旦亚界的研究[M]//中国地质科学院天津地质矿产研究所. 中国震旦亚界. 天津：天津科学技术出版社，1980：56-114.

[4] id=["gbt7714-2015.4.4.2:9"]
[5] MYBURG A A，GRATTAPAGLIA D，TUSKAN G A，et al. The genome of Eucalyptus grandis[J/OL]. Nature，2014，510：356-362[2014-06-25]. http://www.nature.com/nature/journal/v510/n7505/pdf/nature13308.pdf. DOI:10.1038/nature13308.

--- 断言 ---
ASSERT 中文条目含「等」          : true
ASSERT 中文条目含 "et al."       : false
ASSERT 英文条目含 "et al."       : true
ASSERT 英文条目含「等」          : false
ASSERT 无 language 条目含 "et al.": false
ASSERT 无 language 条目含「等」   : true
```

**关键行（Option B 的收益证据）**：

```
中文：王建国，李晓明，张伟，等. 多智能体协作的任务编排方法研究[J]. …
英文：SMITH J，JOHNSON E，WILLIAMS M，et al. A survey of multi-agent task orchestration[J]. …
```

真实语料同样成立：`陈晋镳，张惠民，朱士兴，等.`（zh-CN，5 作者）/ `MYBURG A A，GRATTAPAGLIA D，TUSKAN G A，et al.`（en-US，80 作者）。

### 3.3 边界条件（集成时必须知道）

- **条目缺 `language` 字段 → 回落到样式 `default-locale`（zh-CN）→ 输出「等」**（上表 `spike-en-4authors-nolang` 行 + 断言 `无 language 条目含 "et al.": false`）。因此 Option B 的实际收益**依赖 CSL-JSON 里每条文献都有正确的 `language`**。
- et-al 触发阈值来自样式（`et-al-min="4"`）：3 位作者不会出现「等」/「et al.」。断言用的是 ≥4 作者条目。

### 3.4 「启用 CSL-M 需要什么配置」——实测结论

| 配置项 | 实测结论 |
|---|---|
| 构造函数 4 参 `forceLang` | **不是必需**。`true` / `false` / 完全不传 `lang` 三种调法输出**完全一致**（报告 A / B / C 三节逐行相同） |
| 样式 `version="1.1m…"` | **不需要**。本样式声明 `version="1.0"`；引擎只在 `version` 以 `1.1m` 开头时额外开启 legal-citation 系列扩展（bundle 第 3831–3839 行），与 `layout@locale` 无关 |
| `development_extensions` 开关 | **不需要**。bundled 源码里 `@locale` 属性处理器（第 16458 行起）与 `CSL.Node.layout` 的 `locale_raw` 分支（第 11406 行）**无条件生效** |
| 运行时自证 | `engine.opt.multi_layout` 实测为 `[[{"base":"en-US","generic":true,"best":"en-US","bare":"en"}]]`（非空即表示 CSL-M 布局已注册） |
| 需要的 `sys` 能力 | `retrieveLocale(lang)`（返回 locale XML 字符串）、`retrieveItem(id)`（返回 CSL-JSON 对象），另有 `updateItems` / `makeBibliography` |
| Node 版本要求 | 本次在 **Node v22.19.0** 通过；代码为 ES5 时代 CommonJS（自带 `Object.assign` polyfill），**未测最低版本** |

**html 输出格式（D 节，供导出/前端复用）**：

```html
[1] id=["spike-en-4authors"]
  <div class="csl-entry">
    <div class="csl-left-margin">[2]</div><div class="csl-right-inline">SMITH J，JOHNSON E，WILLIAMS M，et al. A survey of multi-agent task orchestration[J]. Journal of Artificial Intelligence Research，2024，80（2）：100-130.</div>
  </div>
```

---

## 4. Step 5：标准 / 严格模式下的行为（**「需容忍样式校验告警」的证据**）

### 4.1 citeproc-js 自己：**不报错、不告警**

四组运行（A/B/C/D）的 `--- 处理器日志/告警 ---` 全部为 **“无”**。也就是说 **citeproc-js 会静默接受含 CSL-M 构造的 `version="1.0"` 样式**——它容忍，但不会提示你这份样式已经偏离标准。这本身就是集成风险：**错误只在换处理器时才暴露**。

### 4.2 官方 CSL 1.0.2 RELAX NG 校验：**直接判不合格**

校验链路（可复现）：官方 `citation-style-language/schema` 的 `schemas/styles/csl.rnc`（33,190 字节，sha256 `0c9d4cbd65f02a80dfc02a688c1d7da104d31b0c8a29cde16c2bd330e21f318`）→ `rnc2rng` 2.7.0 转成 `csl.rng`（83,909 字节）→ `lxml 6.1.3` 的 `etree.RelaxNG` 校验。

`out/validation-report.txt` 原始输出：

```
==================== 校验结果 ====================
### 双语样式（zotero-chinese）
file: GB-T-7714—2015（顺序编码，双语）.csl
RELAX NG (CSL 1.0.2 csl.rnc) valid: False
error count: 5
  line 7: Element link failed to validate attributes
  line 0: Extra element link in interleave
  line 7: Element info failed to validate content
  line 23: Did not expect element locale there
  line 23: Element style has extra content: locale

### 官方标准样式 numeric（对照）
file: v1.0.2-china-national-standard-gb-t-7714-2015-numeric.csl
RELAX NG (CSL 1.0.2 csl.rnc) valid: True

### 官方标准样式 author-date（对照）
file: v1.0.2-china-national-standard-gb-t-7714-2015-author-date.csl
RELAX NG (CSL 1.0.2 csl.rnc) valid: True
```

⚠️ **诚实说明**：libxml2 的 Relax NG 实现在内容模型不匹配后会**级联报错、行号不可靠**（上面前 5 条错误在“只改一处”的各个变体里几乎原样复现）。因此下面用**控制变量实验**（valid/invalid 布尔值可靠）而不是行号来定位构造：

**(a) 逐个 CSL-M 构造注入「已知合法」的基线**（`out/narrow-report.txt`，每次重新编译 schema）：

```
### M0-de-CSL-M(标准1.0.2等价): valid=True
### M1-M0+locale布局: valid=False
### M2-M0+citation-range-delimiter术语: valid=False
### M3-M0+institution元素: valid=False
### M4-M0+has-day属性: valid=False
### N-original: valid=False
```

即：把 4 处 CSL-M 构造全部改成 1.0.2 写法后**校验通过**；任何**一处**注回都让整份样式**校验失败**。

**(b) 段落级 delta-debug**（以官方样式为基线，逐段替换为双语样式的同名段落，`out/delta-report.txt` 节选）：

```
### 用双语的 [bibliography||] 替换官方同名段落 -> valid=False
### 用双语的 [citation||] 替换官方同名段落 -> valid=True
### 用双语的 [info||] 替换官方同名段落 -> valid=True
### 用双语的 [locale||] 替换官方同名段落 -> valid=False
### 用双语的 [locale||zh] 替换官方同名段落 -> valid=True
### 用双语的 [macro|author|] 替换官方同名段落 -> valid=False
### 用双语的 [macro|container-contributors|] 替换官方同名段落 -> valid=False
### 用双语的 [macro|publisher|] 替换官方同名段落 -> valid=False
### 用双语的 [macro|secondary-contributors|] 替换官方同名段落 -> valid=False
### 用双语的 [macro|title|] 替换官方同名段落 -> valid=True
```

失败段落与 §2.2 的 4 类构造一一对应（`author` / `secondary-contributors` / `container-contributors` = `<institution/>`；`publisher` = `has-day`；`bibliography` = 双 layout；`locale`（无 xml:lang）= `citation-range-delimiter`）。`info` 段与 `citation` 段替换后仍 valid，说明失败**不是**元数据或正文样式问题。

### 4.3 旁证：官方标准样式自己把 CSL-M 段落「注释掉了」

`citation-style-language/styles` 的官方 `china-national-standard-gb-t-7714-2015-numeric.csl` 第 431–438 行（原文照抄）：

```xml
  <bibliography entry-spacing="0" et-al-min="4" et-al-use-first="3" second-field-align="flush">
    <!-- 取消这部分注释可以开启 CSL-M 的多语言功能：按照文献的语言输出“et al.”等术语 -->
    <!-- <layout suffix="." locale="en"><text variable="citation-number" prefix="[" suffix="]"/><text macro="entry-layout"/></layout>
    -->
    <layout suffix=".">
      <text variable="citation-number" prefix="[" suffix="]"/>
      <text macro="entry-layout"/>
    </layout>
  </bibliography>
```

同一文件里 `<institution/>` 同样处于注释状态（第 52、192、202 行 `<!-- <institution/> -->`）。
→ **官方仓库为了保住 CSL 1.0.2 校验，把双语能力整段注释掉了**；社区「双语」样式做的正是「取消注释 + 补上 CSL-M 术语」。这就是 Option B 与 Option A 的分水岭。

---

## 5. 反事实对照：Option A（纯标准 CSL 1.0.2）能拿到什么

把双语样式的 4 处 CSL-M 构造改写成 1.0.2 等价写法（`M0`，§4.2(a) 已证明其合法），用同一套条目、同一引擎渲染，原始输出（`out/render-report-std.txt`）：

```
style file: M0-de-CSL-M(标准1.0.2等价).csl
style 中是否还有 CSL-M 构造: false
processor_version=1.4.61 style_version=1.0
default-locale=["zh-CN"] multi_layout=undefined

--- 处理器日志/告警 ---
（无）

--- 原始条目输出 ---
[0] id=["spike-zh-4authors"]
[1] 王建国，李晓明，张伟，等. 多智能体协作的任务编排方法研究[J]. 计算机学报，2025，47（3）：1-12.

[1] id=["spike-en-4authors"]
[2] SMITH J，JOHNSON E，WILLIAMS M，等. A survey of multi-agent task orchestration[J]. Journal of Artificial Intelligence Research，2024，80（2）：100-130.

[3] id=["gbt7714-2015.4.4.2:9"]
[4] MYBURG A A，GRATTAPAGLIA D，TUSKAN G A，等. The genome of Eucalyptus grandis[J/OL]. Nature，2014，510：356-362[2014-06-25]. …

--- 断言 ---
ASSERT 中文条目含「等」    : true
ASSERT 英文条目含 "et al." : false
ASSERT 英文条目含「等」    : true
```

**结论：标准 CSL 1.0.2 下，英文文献也只能输出「等」**（`multi_layout=undefined` 说明 CSL-M 布局机制未启用）。除非为每种语言各做一份样式、或把语言值硬编码进样式（标准 CSL 无法按条目 `language` 的**取值**分支，只能判断该变量是否为空），否则 Option A 无法做到逐条双语。

---

## 6. Option B 启用成本清单（实测数字）

| 维度 | 实测值 | 说明 |
|---|---|---|
| 依赖体积 | **976,056 字节**（1 个包、0 传递依赖、4 个文件）；`citeproc_commonjs.js` 单文件 967,493 字节 | 有 `main` 入口，`require('citeproc')` 即用（npm `2.4.63`） |
| 额外资源 | locale XML：`locales-zh-CN.xml` 18,641 字节、`locales-en-US.xml` 32,649 字节（按需可再带其它语种） | 必须由 `sys.retrieveLocale` 提供 |
| Node 运行时 | 本次 **Node v22.19.0** 通过；未测最低版本 | **FastAPI 侧要么起 Node sidecar/子进程 + JSON IPC，要么引入嵌入式 JS 引擎——本次未实测任何 IPC/嵌入方案**，这部分成本需在 ADR 中单独估算 |
| 配置开关 | **无需 `forceLang`、无需 `version="1.1m"`、无需 `development_extensions`** | 双语能力由样式的 `<layout locale="en">` 承担 |
| 样式校验 | **必须容忍「不是合法 CSL 1.0.2」**：官方 schema 判 invalid（§4.2）；不能用标准校验做 CI 门禁，需改用 citeproc-js 自身容错或在本地维护白名单 | 4 类非标准构造见 §2.2 |
| 数据前提 | 每条 CSL-JSON **必须有 `language`**（否则回落 zh-CN → 「等」） | §3.3 |
| 许可 | `CPAL-1.0 OR AGPL-1.0`（字段）/ `CPAL-1.0+ OR AGPL-3.0+`（正文） | 需法务确认；**不是 MIT/BSD** |
| 维护活跃度 | npm 侧 `time.modified = 2023-07-29`；上游 master 的 `package.json` 与 npm 同版本 | 上游最近提交时间**未确认**（GitHub API 触发限流，见 §7） |

---

## 7. 未复现 / 未确认项（不臆造）

1. **`npm view citeproc-js` 无法复现**——该包名在 npm 与 npmjs 直连均 **404**，任务书中的第 1 条命令**不成立**；已改用真实包名 `citeproc` 完成后续全部实测。
2. **未测 Node 最低版本**，只验证了 Node v22.19.0。
3. **未实测其它标准处理器**（pandoc `--citeproc`、`citeproc-py`、`citeproc-rs`）对该样式的行为——“标准处理器会拒绝/无法双语”这一条**仅由官方 RELAX NG schema 证明样式非法**，未在第二款真实处理器上跑过。
4. **未实测 Node 与 Python 后端的集成方式**（sidecar / 子进程 / PyExecJS / quickjs），IPC 开销与部署复杂度未量化。
5. **上游 `Juris-M/citeproc-js` 最近提交时间未确认**（GitHub API 匿名限流 60 次/小时已用尽；npm 侧最后发布时间为 2023-07-29）。
6. **libxml2 Relax NG 的报错行号不可靠**（内容模型不匹配后级联），本报告的构造定位依据是「单构造注入 / 段落替换」的 valid/invalid 结果，不是报错行号。
7. 样式的 **`<term name="citation-range-delimiter">` 在标准 1.0.2 下非法**——依据是官方 `csl-terms.rnc` 术语表中查无此项 + 注入实验 invalid；未查证它是否为 CSL 1.1/CSL-M 的正式术语。

---

## 8. 复现步骤（全部命令）

工作目录：`$env:TEMP\csl-js-spike`（本次实测路径 `C:\Users\Lenovo\AppData\Local\Temp\csl-js-spike`）。

```powershell
# 0. 环境
node --version            # v22.19.0
npm --version             # 11.6.0

# 1. 包信息（citeproc-js 会 404，这是预期结果；真实包名是 citeproc）
npm view citeproc-js version license time.modified      # -> E404
npm view citeproc version license time.modified --json  # -> 2.4.63 / "CPAL-1.0 OR AGPL-1.0"

# 2. 安装（0 传递依赖，1 个包）
npm init -y; npm install citeproc --no-audit --no-fund

# 3. 拉取样式 + 语料 + locale（脚本内已含 URL 与 sha256 计算）
node fetch-style.mjs
#   -> style/GB-T-7714—2015（顺序编码，双语）.csl  sha256 b1fca924…17e2
#   -> style/{items,cites,metadata}.json
curl -sSL "https://raw.githubusercontent.com/citation-style-language/locales/master/locales-zh-CN.xml" -o locales/locales-zh-CN.xml
curl -sSL "https://raw.githubusercontent.com/citation-style-language/locales/master/locales-en-US.xml" -o locales/locales-en-US.xml

# 4. CSL-M 渲染（A/B/C/D 四种调法；输出 out/render-report.txt）
node render.mjs

# 5. 官方 schema 校验（csl.rnc -> csl.rng -> lxml RelaxNG）
curl -sSL "https://raw.githubusercontent.com/citation-style-language/schema/master/schemas/styles/csl.rnc" -o schema/styles/csl.rnc
#   （另需 csl-categories.rnc / csl-choose.rnc / csl-terms.rnc / csl-types.rnc / csl-variables.rnc）
python -m pip install --target pylibs rnc2rng
python validate_style.py       # -> out/validation-report.txt（双语 invalid，两份官方样式 valid）
python validate_variants.py    # -> out/variant-validation-report.txt
python narrow.py               # -> out/narrow-report.txt（M0 valid；M1–M4 各注入一处 -> invalid）
python delta_debug.py          # -> out/delta-report.txt（段落级定位）

# 6. 反事实对照：去 CSL-M 的「标准 1.0.2 等价样式」渲染
node render_std.mjs            # -> out/render-report-std.txt（英文条目变成「等」）
```

> 环境说明：本机 npm registry 为 `https://registry.npmmirror.com`；`raw.githubusercontent.com` 与 PyPI 均可直连（无代理）。上述 Python 用的是 DSH 自带 Python 3.12.14（含 lxml 6.1.3），**未使用也未改动 `backend/.venv`**。
