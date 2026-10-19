# 样式资产署名与许可（ATTRIBUTION）

本目录的 CSL 样式资产用于本项目的引用渲染（Issue #4 / ADR 0001 Option C）。
CSL 样式生态由 [CitationStyles.org](https://citationstyles.org/)（CSL 项目）维护，
样式文件默认以 **CC BY-SA 3.0**（<https://creativecommons.org/licenses/by-sa/3.0/>）发布。
本文件逐条记录来源、作者、版本日期与 sha256，满足署名（Attribution）与相同方式共享（ShareAlike）义务。

- 检索/下载日期：**2026-10-19**（Asia/Shanghai）
- 上游仓库：`citation-style-language/styles` @ `master` = `0e5503bd9a75668c402567a93e20b7327784e4fa`（2026-10-16）
- 参考仓库：`zotero-chinese/styles` @ `main` = `cf56b119812c83c91c99606405d7e7861685c3b7`（2026-08-23）

---

## 1. 自研样式（本项目原创）

| 文件 | 说明 | 许可 | sha256 | 大小 |
| --- | --- | --- | --- | --- |
| `gbt7714-2025-numeric.csl` | GB/T 7714—2025《信息与文献 参考文献著录规则》**顺序编码制**，标准 CSL 1.0.2 | CC BY-SA 3.0 | `8fdf685d6b013ec080fd766e37d1e973ca3437a29c9d8697d2b5da7a312b67f6` | 14877 B |
| `gbt7714-2025-author-date.csl` | GB/T 7714—2025 **著者-出版年制**，标准 CSL 1.0.2 | CC BY-SA 3.0 | `9f4d2b125652ef3723b9685396f602a76526c2de51a92b7c92afe559cfd96f9c` | 15341 B |

**独立实现声明**：上述两份自研样式均由本项目**独立实现**（宏结构、宏命名、分支组织均为自行编写），
**没有整段复制**任何上游样式；仅在编写前阅读了下列参考实现，用于核对 GB/T 7714 的著录格式与 2025 版变化：

| 参考实现 | 来源 | 作者 | 许可 | sha256 |
| --- | --- | --- | --- | --- |
| GB/T 7714—2015 顺序编码制（官方） | <https://github.com/citation-style-language/styles/blob/master/china-national-standard-gb-t-7714-2015-numeric.csl> | 牛耕田（buffalo_d@163.com）；贡献者 Zeping Lee | CC BY-SA 3.0 | `d9cd04f94bc21a99dc320d0a2230d5a1fb47e3634c17c5820cf975c40db3c0c7` |
| GB/T 7714—2015 著者-出版年制（官方） | <https://github.com/citation-style-language/styles/blob/master/china-national-standard-gb-t-7714-2015-author-date.csl> | 牛耕田（buffalo_d@163.com）；贡献者 Zeping Lee | CC BY-SA 3.0 | 仅作格式比对，未纳入本仓库 |
| GB/T 7714—2025（顺序编码，双语）（社区） | <https://github.com/zotero-chinese/styles/blob/main/src/GB-T-7714—2025（顺序编码，双语）/GB-T-7714—2025（顺序编码，双语）.csl> | Zeping Lee | CC BY-SA 3.0 | `e0c3a67c7346ad83fc55cb92c03385810387258887413da613d07f482c53476b` |
| GB/T 7714—2025（著者-出版年，双语）（社区） | <https://github.com/zotero-chinese/styles> | Zeping Lee | CC BY-SA 3.0 | 仅作格式比对，未纳入本仓库 |

自研样式对 2025 版变化的落地方式：

1. **英文著者仅首字母大写**：不再对 `family` 施加 `uppercase`（2015 版是全大写）；
   由数据层保证首字母大写（CSL 1.0.2 无「先小写再首字母大写」的链式变换，
   且 citeproc-py 0.11.1 在 name-part 上使用 capitalize-first 会抛 `AttributeError`）。
2. **文献类型标识为必备项**：题名后恒输出 `[J]/[M]/[C]/[D]/[R]/[S]/[P]/[N]/[PP]/[DS]/[EB]/[G]/[CM]/[CP]/[A]/[Z]`；
   其中预印本 `PP`、汇编 `G` 为 2025 版新增/明确项。
3. **载体标识（附录 A.2）**：有 URL/DOI 时追加 `/OL`，`medium` 字段给出 `MM`（缩微资料，2025 新增）等代码时按其输出，
   形成 `[J/OL]`、`[PP/OL]`、`[J/MM]`。
4. **双语术语**：中文条目切 `locale="zh-CN"` → 「等」，英文条目切 `locale="en-US"` → `et al.`，
   由应用层按条目 `language` 逐条切换（ADR 0001 §2.2），样式侧不做多 locale 布局。

自研样式以 **CC BY-SA 3.0** 发布，与本目录其余 CSL 资产一致。

---

## 2. vendored 官方样式（原样保留 `<info>` 块）

以下 4 个文件从 `citation-style-language/styles` **原样下载**，未做任何本地修改
（保留其 `<info>`、`<author>`、`<rights>` 与全部内容；sha256 即上游字节的 sha256）。

| 本地文件 | 上游路径 | 来源 URL | 作者 | 上游 `<updated>` | sha256 | 大小 |
| --- | --- | --- | --- | --- | --- | --- |
| `gbt7714-2015-numeric.csl` | `china-national-standard-gb-t-7714-2015-numeric.csl` | <https://github.com/citation-style-language/styles/blob/master/china-national-standard-gb-t-7714-2015-numeric.csl> | 牛耕田（贡献者 Zeping Lee） | 2024-01-22T22:07:03+08:00 | `d9cd04f94bc21a99dc320d0a2230d5a1fb47e3634c17c5820cf975c40db3c0c7` | 15530 B |
| `apa.csl` | `apa.csl` | <https://github.com/citation-style-language/styles/blob/master/apa.csl> | Brenton M. Wiernik、Andrew Dunning | 2026-02-07T00:00:00+00:00 | `3917def5fc951b104d64216c3373096042368b9704ba269ca83c14ebaba889fc` | 85817 B |
| `ieee.csl` | `ieee.csl` | <https://github.com/citation-style-language/styles/blob/master/ieee.csl> | Michael Berkowitz | 2024-03-27T11:41:27+00:00 | `b4c7619fc16c45a31e4cc3271eab94ffe83192d3b4c7fc729470a3b459448de3` | 17587 B |
| `nature.csl` | `nature.csl` | <https://github.com/citation-style-language/styles/blob/master/nature.csl> | Michael Berkowitz | 2025-09-10T18:26:21+00:00 | `15b1272a3c360168e0b51ab9257534b08ced67190ed57be7a1c32cea8fc87be8` | 6444 B |

下载地址（`{path}` 为上游路径）：`https://raw.githubusercontent.com/citation-style-language/styles/master/{path}`

> 本地文件名 `gbt7714-2015-numeric.csl` 是上游 `china-national-standard-gb-t-7714-2015-numeric.csl` 的重命名，
> 目的是让「样式文件名 = `{style_id}.csl`」在本项目内成立（lead 裁决 R-M1-1）；
> 文件内容逐字节未改动，可用 sha256 与上游比对。

### 许可与署名（CC BY-SA 3.0）

上述样式均为 CSL 项目（[CitationStyles.org](https://citationstyles.org/)）发布的 CSL 样式，
以 **CC BY-SA 3.0** 许可（<https://creativecommons.org/licenses/by-sa/3.0/>）。
本项目：
- 保留每个文件原有的 `<info>` 署名与 `<rights>` 许可声明（未删除、未改写作者信息）；
- 在本文件与项目文档中署名 CSL 项目并链接 <https://citationstyles.org/>；
- 若将来对这些样式做修改（归一化），将在本文件中标注「已修改」并同样以 CC BY-SA 3.0 发布。

---

## 3. 复现与校验

```powershell
cd backend
# 渲染冒烟（逐条 locale 切换；输出见测试与执行记录）
.\.venv\Scripts\python.exe -m pytest tests/test_csl_style_files.py -q --basetemp="$env:TEMP\pytest-dsh-csl1"
```

`tests/test_csl_style_files.py` 会校验：

1. 每个样式都能被 `citeproc-py` 加载（zh-CN / en-US 两种 locale）；
2. 自研样式 **零 CSL-M 命中**（`<layout locale=…>`、`<institution>`、`has-day`、
   引用范围分隔符术语、术语中的 `%s`）；
3. 自研样式通过 citeproc-py 自带的官方 CSL RELAX NG schema 校验（`validate=True`，0 warning）；
4. vendored 文件保留 `<info>`/`<author>`，且本文件记录的 **sha256 与实际文件一致**（资产未被人为改动）；
5. 冻结语料 `tests/fixtures/csl/corpus.json` 6 条逐条渲染不抛异常、输出非空，
   且每条都有文献类型标识（`[J]/[M]/[C]/[D]/[PP]`），英文条目 `et al.`、中文条目「等」。

## 4. 已知渲染器约束（citeproc-py 0.11.1，实测）

- `<name-part>` 上的 `text-case="capitalize-first"` 会抛 `AttributeError: 'str' object has no attribute 'capitalize_first'`
  （`str.upper()` 等内置方法可用）→ 自研样式不在 name-part 上做大小写变换。
- `cs:group` 的「变量调用」判定只看子元素的**静态**声明（`cs:if` 自身的 `variable` 属性不计入），
  恒输出的字面值与可选的变量字面量不可放在同一个 group 中，否则无 URL/DOI 的条目会整组被抑制
  （实测现象：`[M]`/`[D]` 丢失）→ 自研样式用 `choose` 分流类型标识与载体标识。
