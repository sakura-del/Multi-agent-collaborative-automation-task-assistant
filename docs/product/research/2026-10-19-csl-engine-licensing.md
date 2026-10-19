# CSL 引用引擎许可证与维护状态核实（一手来源）

> **用途**：Issue #5「CSL-M 双语策略架构决策（ADR）」**决策标准 3** ——「citeproc-js 许可证核实结果是否可商用」。
> **检索日期**：2026-10-19（Asia/Shanghai）。所有条目均标注检索日期与一手来源 URL。
> **方法**：`curl` 直取仓库 LICENSE / 官方 registry API（npm registry、PyPI JSON API、crates.io API、Hackage、GitHub REST API）+ SPDX 官方 `license-list-data`。
> **免责**：本文**只陈述许可证条款原文、元数据事实与维护状态**，**不构成法律意见**；任何"适用性"判断一律标注「未确认」。
> **关联文档**：[2026-10-18-template-and-csl-tech.md](2026-10-18-template-and-csl-tech.md) §B（本文更正其中 2 处与一手证据不符的表述）；[2026-10-19-csl-standard-pipeline-spike.md](2026-10-19-csl-standard-pipeline-spike.md)（样式归一化实测，与本文件互补）。

---

## 0. 结论速览

| 引擎 | 一手版本 / 日期 | 许可证（一手来源） | 维护状态（一手） | 闭源 SaaS 需注意的条款要点（只列条款，不作法律判断） |
| --- | --- | --- | --- | --- |
| **citeproc-js**（Juris-M） | npm `citeproc@2.4.63`，2023-04-17 发布；仓库最后提交 2026-07-05 | **双许可：CPAL-1.0（或更高版本）OR AGPL-3.0（或更高版本）**（仓库 `LICENSE` 原文） | 活跃（未归档；2026-07-05 仍有合并）；但 **npm 已 3 年未再发布** | 两种分支都非"无义务"：CPAL §14 署名 + Exhibit B 已填写（§14(c) 免署名**不适用**）；CPAL §15 把「External Deployment（含网络提供）」视为分发 → 须提供源码；AGPL-3.0 §13 网络交互须提供对应源码 |
| **citeproc-rs** | **无任何发布版本**（crates.io 无 `citeproc` crate）；仓库最后提交 2021-12-14 | **MPL-2.0**（`LICENSE.md` 原文） | **已归档**（`archived: true`，2024-08-06 后无推送） | 文件级弱 copyleft（§3.2 源码形式义务、§3.3 Larger Work 可自有条款）；本项目不可用（无发布物） |
| **pandoc** | 3.12.1，release 2026-10-08 | **GPL-2.0-or-later**（`COPYING.md` 为 GPLv2 正文；`pandoc.cabal` license 字段） | 活跃（2026-10-18 有推送） | GPL-2.0 §2(b)：分发/发布衍生整体须以 GPL 授权；**GPLv2 无网络服务条款**（与 AGPL 不同）。其调用的 `citeproc` 库为 BSD-2-Clause（宽松） |
| **Haskell `citeproc`**（jgm） | 0.14.1，Hackage 上传 2026-10-05 | **BSD-2-Clause**（`LICENSE` 原文） | 活跃（2026-10-15 有提交） | 宽松：保留版权声明与免责声明即可 |
| **citeproc-py** | PyPI 0.11.1，2026-09-01 发布 | **BSD-2-Clause-Views**（PyPI `license_expression` + 仓库 `LICENSE`） | **活跃**（2026 年 ≥12 个版本；最后提交/issue 2026-09）——**更正既有"停滞"判断** | 宽松；真正限制是 CSL 覆盖率（自述 ~60%）与无 CSL-M 声明 |
| **citation-js** | npm 0.9.0，2026-09-18 发布 | 自身 **MIT**；但**依赖 npm `citeproc`（= citeproc-js）** | 活跃（2026-10-11 推送） | 整体分发会同时携带 MIT 与 CPAL/AGPL 组件，义务按组件计 |
| **Zotero**（对照） | 7.0.0 | **AGPL-3.0**（`COPYING` 原文） | 活跃 | 说明「Zotero 用 citeproc-js 是在 AGPL 生态内」：其自身即 AGPLv3 |

**对决策标准 3 的事实性回答**：citeproc-js 的许可条款**允许商业使用**（CPAL-1.0 与 AGPL-3.0 均为 OSI 认可的开源许可，SPDX 标注 `isOsiApproved: true`），**但两条分支都附带条件**——CPAL 分支有署名与网络使用（源码可得）义务，AGPL 分支有网络交互源码提供义务。是否接受这些条件属于商业/法务决策，**本文不给结论**。

---

## 1. 逐项证据（URL + 原文引用 + 检索日期）

### 1.1 citeproc-js（Juris-M/citeproc-js）

#### (a) GitHub 的 NOASSERTION 是真的

- **来源**：`https://api.github.com/repos/Juris-M/citeproc-js/license`（检索日期 2026-10-19）
- **原文**：

```json
{"license":{"key":"other","name":"Other","spdx_id":"NOASSERTION","url":null,"node_id":"MDc6TGljZW5zZTA="},"path":"LICENSE","size":1017}
```

- 说明：`spdx_id` 为 `NOASSERTION`，`path` 为 `LICENSE`（1017 字节）。GitHub 之所以识别不出，是因为该文件是**双许可声明**而非某一份标准许可全文（见下）。

#### (b) 仓库 `LICENSE` 原文 = CPAL-1.0+ 或 AGPL-3.0+（双许可）

- **来源**：`https://raw.githubusercontent.com/Juris-M/citeproc-js/master/LICENSE`（默认分支 `master`；检索日期 2026-10-19）
- **原文（全文，22 行）关键段**：

```text
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

- 仓库根目录同时含两份许可全文（来源 `https://api.github.com/repos/Juris-M/citeproc-js/contents`，检索日期 2026-10-19）：`AGPLv3`、`CPAL`、`LICENSE`、`README.rst`。
- CPAL 全文中的 Exhibit A 原文（`https://raw.githubusercontent.com/Juris-M/citeproc-js/master/CPAL`）：

```text
    The Original Code is citeproc-js.

    Alternatively, the contents of this file may be used under the terms of the GNU AFFERO GENERAL PUBLIC LICENSE (the AGPLv3 License), in which case the provisions of AGPLv3 License are applicable instead of those above.
```

#### (c) CPAL 关键条款（归属 / 网络使用）

- **来源**：`https://raw.githubusercontent.com/Juris-M/citeproc-js/master/CPAL`（检索日期 2026-10-19）
- **CPAL §14 ADDITIONAL TERM: ATTRIBUTION (a)**：

```text
the Original Developer may include in Exhibit B (“Attribution Information”) a requirement that each time an Executable and Source Code or a Larger Work is launched or initially run (which includes initiating a session), a prominent display of the Original Developer’s Attribution Information ... must occur on the graphic user interface employed by the end user to access such Covered Code (which may include display on a splash screen), if any.
```

- **CPAL §14 (c)**：

```text
If Exhibit B does not include any Attribution Information, then there are no requirements for You to display any Attribution Information of the Original Developer.
```

- **CPAL EXHIBIT B（citeproc-js 已填写，故 §14(c) 免署名不适用）**：

```text
EXHIBIT B. Attribution Information

    Attribution Copyright Notice: (c) Frank Bennett

    Attribution Phrase (not exceeding 10 words): citeproc-js implements the Citation Style Language

    Attribution URL: https://citationstyles.org/

    Graphic Image as provided in the Covered Code, if any.

    Display of Attribution Information is required in Larger Works which are defined in the CPAL as a work which combines Covered Code or portions thereof with code not governed by the terms of the CPAL.
```

- **CPAL §15 ADDITIONAL TERM: NETWORK USE**：

```text
    The term “External Deployment” means the use, distribution, or communication of the Original Code or Modifications in any way such that the Original Code or Modifications may be used by anyone other than You, whether those works are distributed or communicated to those persons or made available as an application intended for use over a network. As an express condition for the grants of license hereunder, You must treat any External Deployment by You of the Original Code or Modifications as a distribution under section 3.1 and make Source Code available under Section 3.2.
```

- **CPAL §3.1（分发源码形式的义务）**：`All distribution of Covered Software in Source Code Form ... must be under the terms of this License.`；**§3.2（分发可执行形式）**：`such Covered Software must also be made available in Source Code Form`。

#### (d) AGPL-3.0 网络服务条款（第 13 条）原文

- **来源**：仓库内 `AGPLv3` 全文 `https://raw.githubusercontent.com/Juris-M/citeproc-js/master/AGPLv3`（检索日期 2026-10-19）
- **原文**：

```text
  13. Remote Network Interaction; Use with the GNU General Public License.

  Notwithstanding any other provision of this License, if you modify the
Program, your modified version must prominently offer all users
interacting with it remotely through a computer network (if your version
supports such interaction) an opportunity to receive the Corresponding
Source of your version by providing access to the Corresponding Source
from a network server at no charge, through some standard or customary
means of facilitating copying of software.
```

#### (e) npm 包的真实名字是 `citeproc`（不是 `citeproc-js`）

- **来源**：`npm view citeproc license version time.modified`（检索日期 2026-10-19，只读命令）
- **原文输出**：

```text
license = 'CPAL-1.0 OR AGPL-1.0'
version = '2.4.63'
time.modified = '2023-07-29T14:33:00.727Z'
```

- **来源**：`https://registry.npmjs.org/citeproc/latest`（检索日期 2026-10-19）关键字段原文：

```json
{"name":"citeproc","version":"2.4.63","license":"CPAL-1.0 OR AGPL-1.0",
 "description":"The citeproc-js citation formatting module, in CommonJS format. This version is based on citeproc-js 1.4.63",
 "repository":{"url":"git+https://github.com/juris-m/citeproc-js.git","type":"git"},
 "gitHead":"73bc1b44bc7d54d0bfec4e070fd27f5efe024ff9",
 "dist":{"tarball":"https://registry.npmjs.org/citeproc/-/citeproc-2.4.63.tgz"}}
```

- **npm 包名 `citeproc-js` 不存在**：`npm view citeproc-js` 返回 `npm error code E404 / 404 Not Found - GET https://registry.npmjs.org/citeproc-js`（检索日期 2026-10-19）。
- **发布历史（`npm view citeproc time --json`）**：`2.4.63` 发布于 `2023-04-17T12:09:09.316Z`（**最后发布**）；此前 `2.4.62` 2021-11-03、`2.4.59` 2021-01-05、`2.4.43` 2020-10-19；最早 `1.0.0` 2015-04-21。
- **仓库自身 `package.json`**（`https://raw.githubusercontent.com/Juris-M/citeproc-js/master/package.json`，检索日期 2026-10-19）：`"name": "citeproc"`、`"version": "2.4.63"`、`"license": "CPAL-1.0 OR AGPL-1.0"`。

#### (f) ⚠️ 元数据冲突（重要）

| 来源 | AGPL 版本表述 | 检索日期 |
| --- | --- | --- |
| 仓库 `LICENSE` 全文 | `either version 3 of the AGPL, or (at your option) any later version` → `AGPL-3.0-or-later` | 2026-10-19 |
| npm registry `license` 字段 / 仓库 `package.json` | `CPAL-1.0 OR AGPL-1.0`（SPDX `AGPL-1.0-only`，SPDX 官方标注 `isOsiApproved: false`） | 2026-10-19 |
| GitHub license API | `NOASSERTION` | 2026-10-19 |

**以哪一份为准：未确认**（未检索到权利人 Frank Bennett / Juris-M 的公开澄清）。事实层面仅可确认：**仓库 LICENSE 原文写的是 AGPL v3 或更高版本**，而 npm 元数据写的是 `AGPL-1.0`。

#### (g) CSL-M 支持（官方自述）

- **来源**：`https://raw.githubusercontent.com/Juris-M/citeproc-js/master/README.rst`（检索日期 2026-10-19）
- **原文**：

```text
``citeproc-js`` passes a suite of over 1,300 integration tests with flying
colors. When run in CSL-M mode [1]_ it can handle multilingual and
legal content with a flexibility and precision unrivaled by any other
tool at any price. And it has been quite heavily field-tested, as the
citation formatter driving word processor integration in both
`Mendeley`_ and `Zotero`_.
```

```text
.. [1] CSL-M is set of private extensions to official CSL used by the
       `Jurism <https://juris-m.github.io>`_ reference manager, a
       variant of Zotero.
```

#### (h) 维护状态（仓库）

- **来源**：`https://api.github.com/repos/Juris-M/citeproc-js`（检索日期 2026-10-19）：`default_branch=master`、`archived=false`、`pushed_at=2026-07-05T22:23:24Z`、`created_at=2016-03-04`、`stargazers_count=365`、`open_issues_count=80`、`license.spdx_id=NOASSERTION`。
- **最近提交**（`https://api.github.com/repos/Juris-M/citeproc-js/commits?per_page=5`，检索日期 2026-10-19）：

```text
cc9153c4  2026-07-05T22:23:24Z  Merge pull request #281 from dstillman/restore-state-after-preview-error
49f740f5  2026-07-03T19:29:04Z  Restore processor state when previewCitationCluster() fails
6f5f5797  2026-04-08T21:47:14Z  Merge pull request #275 from dstillman/chicago15-inclusive-page-ranges-over-1000
eb8f219e  2026-04-08T14:36:18Z  Fix chicago-15 page-range abbreviation for 5+ digit numbers
c3a03ebf  2026-02-06T01:32:34Z  Merge pull request #270 from AbeJellinek/fail-ci-when-tests-fail
```

- 推论（事实层面）：**代码仍在维护，但 npm 发布停在 2023-04-17**（`2.4.63`）——若采用 npm 安装，拿到的是 3 年前的构建产物。这一点对"是否可用/是否安全"的影响**未确认**。

---

### 1.2 citeproc-rs（cormacrelf/citeproc-rs → 已转移至 zotero/citeproc-rs）

- **来源**：`https://api.github.com/repos/cormacrelf/citeproc-rs`（会自动重定向，检索日期 2026-10-19）
- **原文（关键字段）**：

```json
{"full_name":"zotero/citeproc-rs","default_branch":"master","archived":true,
 "pushed_at":"2024-08-06T01:16:32Z","created_at":"2018-11-28T04:11:41Z",
 "stargazers_count":82,"open_issues_count":28,
 "license":{"key":"other","name":"Other","spdx_id":"NOASSERTION"},
 "description":"CSL processor in Rust."}
```

- **许可证**：仓库 `LICENSE.md` 原文（`https://raw.githubusercontent.com/zotero/citeproc-rs/master/LICENSE.md`，检索日期 2026-10-19）：

```text
Copyright © 2019, Corporation for Digital Scholarship,
Vienna, Virginia, USA http://digitalscholar.org.

Originally Copyright © 2018, Cormac Relf.

The Corporation for Digital Scholarship distributes the citeproc-rs source code 
under the Mozilla Public License Version 2.0 (MPL 2.0). The full text of this 
license is given below.
```

- 该文件其余部分为 MPL-2.0 全文；其中 §3.2（`Distribution of Executable Form`）要求可执行形式分发时同时提供源码形式，§3.3（`Distribution of a Larger Work`）允许以自有条款分发 Larger Work。**注意 GitHub 对本仓库同样标注 `NOASSERTION`**，尽管文件首行即写明 MPL-2.0。
- **crates.io：不存在名为 `citeproc` 的 crate**（检索日期 2026-10-19）：
  - `https://crates.io/api/v1/crates/citeproc` → `{"errors":[{"detail":"crate `citeproc` does not exist"}]}`（HTTP 404）
  - `https://index.crates.io/ci/te/citeproc` → `NoSuchKey`
  - `https://crates.io/api/v1/crates?q=citeproc&per_page=10` → `total: 10`，**无一条名为 `citeproc`**
- **README 自述（`https://raw.githubusercontent.com/zotero/citeproc-rs/master/README.md`，检索日期 2026-10-19）**：

```text
A work-in-progress implementation of [CSL][] and [CSL-M][] in [Rust][].
```

```text
For Rust users, this package will available at some point on crates.io. The 
`csl` crate is already available, if all you wanted to do was parse/validate 
styles and locales.
```

- **最近提交**（`https://api.github.com/repos/zotero/citeproc-rs/commits?per_page=5`，检索日期 2026-10-19）：最新 `2ab195a1  2021-12-14T16:02:21Z  [skip-ci] add changelog for wasm-v0.2.0`；tags 最新为 `wasm-v0.2.0`（2021-12）。
- 结论（事实）：**MPL-2.0 许可 + 仓库已归档 + 最后提交 2021-12 + 从未发布到 crates.io** → 无可用的正式发布物。

---

### 1.3 pandoc 与 Haskell `citeproc`

#### (a) pandoc 本体 = GPL-2.0-or-later

- **来源**：`https://raw.githubusercontent.com/jgm/pandoc/main/pandoc.cabal`（检索日期 2026-10-19）原文：

```text
version:         3.12.1
license:         GPL-2.0-or-later
```

```text
                 citeproc              >= 0.14.1   && < 0.15,
```

- **来源**：`https://raw.githubusercontent.com/jgm/pandoc/main/COPYING.md`（检索日期 2026-10-19）首行原文：`### GNU GENERAL PUBLIC LICENSE` / `Version 2, June 1991`（即 GPLv2 全文）。其中 §2(b) 原文：

```text
**b)** You must cause any work that you distribute or publish, that in
whole or in part contains or is derived from the Program or any part
thereof, to be licensed as a whole at no charge to all third parties
under the terms of this License.
```

- **GPLv2 无网络服务/远程交互条款**（全文检索 `Remote Network Interaction` 无命中；该条款是 AGPLv3 §13 与 GPLv3 §13 才有的）——这是与 citeproc-js 的 AGPL 分支的关键差别。
- **版本与日期**：GitHub Release `3.12.1`，`published_at = 2026-10-08T01:48:41Z`（`https://api.github.com/repos/jgm/pandoc/releases/latest`，检索日期 2026-10-19）；仓库 `pushed_at = 2026-10-18T19:58:52Z`，GitHub license 判定 `GPL-2.0`。

#### (b) `pandoc --citeproc` 的 CSL 版本 / CSL-M 支持

- **来源**：pandoc 官方手册 `https://raw.githubusercontent.com/jgm/pandoc/main/MANUAL.txt`（检索日期 2026-10-19）
  - 选项存在：第 1609 行 `` `-C`, `--citeproc` ``；第 6504 行 `When the `--citeproc` option is used, pandoc can automatically generate ...`
  - 样式由 CSL 文件控制：第 1618 行 `The style is controlled by a [CSL]`；CSL 规范链接指向 `https://docs.citationstyles.org/en/stable/specification.html`
  - **全文检索 `CSL-M`：0 命中**；**未出现 `CSL 1.0.2` 字样**。
- 因此（事实）：**pandoc 官方文档未声明支持 CSL-M，也未声明所支持的 CSL 规范版本号**。"技术上是否完全无法处理 CSL-M 样式" → **未确认**（需实测，不在本任务范围）。
- 关于 CSL 1.0.2 的**可确认链条**：pandoc 本体不自行实现 CSL，而是依赖 Haskell 库 `citeproc >= 0.14.1 && < 0.15`（`pandoc.cabal`），而该库 README 自述 `Currently version 1.0.2 of the CSL spec is targeted`。故可确认的是"**pandoc 通过依赖 jgm/citeproc 间接面向 CSL 1.0.2**"；而"pandoc 自身文档承诺 CSL 1.0.2"**不成立**（MANUAL 无版本声明）。

#### (c) Haskell `citeproc` 库 = BSD-2-Clause

- **来源**：`https://raw.githubusercontent.com/jgm/citeproc/master/LICENSE`（检索日期 2026-10-19）原文首行：

```text
BSD 2-Clause License

Copyright (c) 2020, John MacFarlane
```

- **来源**：`https://raw.githubusercontent.com/jgm/citeproc/master/README.md`（检索日期 2026-10-19）原文：

```text
This library generates citations and bibliography formatted
according to a [CSL] style.  Currently version 1.0.2 of the CSL
spec is targeted.
```

```text
CSL spec than pandoc-citeproc was, it still fails some of the
tests from the CSL test suite (62/818).
```

- **版本/日期**：Hackage `https://hackage.haskell.org/package/citeproc`（检索日期 2026-10-19）→ 最新 `0.14.1`，`license: BSD-2-Clause`，`Uploaded by JohnMacFarlane at 2026-10-05T04:12:55Z`；仓库 `pushed_at = 2026-10-15T21:47:04Z`，最近提交 `8ea434f9 2026-10-15T21:46:39Z Add some expected failures.`、`6dcaa4d0 2026-10-05T04:07:48Z Bump to 0.14.1, update changelog`（`https://api.github.com/repos/jgm/citeproc/commits?per_page=5`）。
- 附带印证：既有调研提到的「pandoc 3.12.1 起改变姓名 particle 解析」对应上游提交 `b99816d9 2026-10-04T00:26:39Z  Stop extracting particles from given and family names.`

---

### 1.4 citeproc-py

#### (a) PyPI 元数据（`https://pypi.org/pypi/citeproc-py/json`，检索日期 2026-10-19）

```text
name: citeproc-py
version: 0.11.1
license: None
license_expression: BSD-2-Clause-Views
requires_python: >=3.9
project_urls: {'homepage': 'https://github.com/citeproc-py/citeproc-py'}
classifiers 含: 'Development Status :: 3 - Alpha'
```

- **最后发布日期**：`0.11.1` 上传于 `2026-09-01T20:52:26.775988Z`（wheel）/ `20:52:27.988728Z`（sdist）。
- **发布节奏（一手统计）**：总发布数 **25**，首个 `2013-09-16`；**2026-04-21 至 2026-09-01 之间有 12 个版本**（`0.9.2, 0.9.3, 0.10.0, 0.10.1, 0.10.2, 0.10.3, 0.10.4, 0.10.5, 0.10.6, 0.10.7, 0.11.0, 0.11.1`）。

#### (b) 仓库与许可证

- **来源**：`https://api.github.com/repos/brechtm/citeproc-py`（自动重定向；检索日期 2026-10-19）：

```json
{"full_name":"citeproc-py/citeproc-py","default_branch":"master","archived":false,
 "pushed_at":"2026-09-01T20:52:18Z","created_at":"2012-01-05T21:20:50Z",
 "stargazers_count":170,"open_issues_count":40,
 "license":{"key":"other","name":"Other","spdx_id":"NOASSERTION"},
 "description":"Yet another Python CSL Processor"}
```

- **LICENSE 原文**（`https://raw.githubusercontent.com/citeproc-py/citeproc-py/master/LICENSE`，检索日期 2026-10-19）为 BSD 2 条款文本：

```text
Copyright 2011-2013 Brecht Machiels. All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:
   1. Redistributions of source code must retain the above copyright notice, ...
   2. Redistributions in binary form must reproduce the above copyright notice, ...
```

并以 views 句结尾（这使 SPDX 识别为 `BSD-2-Clause-Views` 而非 `BSD-2-Clause`）：

```text
The views and conclusions contained in the software and documentation are those
of the authors and should not be interpreted as representing official policies,
either expressed or implied, of the copyright holder.
```

#### (c) 能力自述（CSL 1.0.2 与 CSL-M）

- **来源**：`https://raw.githubusercontent.com/citeproc-py/citeproc-py/master/README.md`（检索日期 2026-10-19）原文：

```text
citeproc-py is a [CSL](https://citationstyles.org/) processor for Python. It aims to implement the
[CSL 1.0.2 specification](https://docs.citationstyles.org/en/v1.0.2/specification.html).
```

```text
## CSL Compatibility

Currently, citeproc-py passes about 60% of the tests in the
[citeproc-test suite](https://github.com/citation-style-language/test-suite).
A non-exhaustive list of functionality that is missing includes:

-  disambiguation/year-suffix
-  et-al-subsequent-min/et-al-subsequent-use-first
-  collapsing
-  punctuation-in-quote
-  display
```

```text
citeproc-py is 100% volunteer maintained, and new contributions are always welcome.
```

- README/MANUAL 层面对 **CSL-M 无任何声明** → "citeproc-py 是否支持 CSL-M" **未确认**（但其"aims to implement CSL 1.0.2"的定位与缺失清单均未提及 CSL-M 扩展）。

#### (d) ⚠️ 更正：「维护停滞」的说法没有一手证据支持

| 证据 | 一手数据 | 检索日期 |
| --- | --- | --- |
| 最后提交 | `eb712487  2026-09-01T20:52:17Z  Update CHANGELOG.md [skip ci]` | 2026-10-19 |
| 最近提交（前 5） | 2026-09-01 / 2026-08-26 / 2026-08-25 / 2026-08-25 / 2026-08-20 | 2026-10-19 |
| 最近 issue/PR | #216（2026-09-12，open）、#215（2026-09-01，open）、#214（2026-08-25，closed）、#213（2026-08-20，closed）、#212（2026-08-18，open）、#211（2026-08-06，open） | 2026-10-19 |
| CHANGELOG | `# v0.11.1 (Tue Sep 01 2026)`、`# v0.11.0 (Thu Aug 20 2026)`、`# v0.10.7 (Mon Aug 10 2026)` … | 2026-10-19 |
| PyPI | 2026 年内 ≥12 个版本，最新 2026-09-01 | 2026-10-19 |

- **结论**：`docs/product/2026-10-18-demo-to-product-roadmap.md` / `AGENTS.md` §6.1 中「citeproc-py 维护停滞、不要用」以及既有调研 §B「长期维护薄弱」的表述，**与 2026-10-19 的一手时间线不符**。真正可确认的短板是：**自述 CSL 测试通过率约 60%、Development Status = Alpha、无 CSL-M 声明**。
- 「社区在 CSL Discourse 多次讨论其停滞」的**具体链接：未找到**（未检索到可作为一手证据的讨论帖）→ 标注**未确认**。

---

### 1.5 citation-js（npm）与它内嵌的 citeproc-js

- **来源**：`npm view citation-js license version time.modified dependencies`（检索日期 2026-10-19）原文：

```text
license = 'MIT'
version = '0.9.0'
time.modified = '2026-09-18T15:10:34.476Z'
dependencies = {
  citeproc: '^2.4.59',
  ...
  '@citation-js/plugin-csl': '0.9.0',
  ...
}
```

- **来源**：`npm view @citation-js/plugin-csl license version dependencies`（检索日期 2026-10-19）原文：

```text
license = 'MIT'
version = '0.9.0'
dependencies = { citeproc: '^2.4.6', '@citation-js/date': '^0.5.0' }
```

- **是否内嵌 citeproc-js：是（依赖 npm `citeproc`，即 citeproc-js 的 CommonJS 构建）**。印证：npm `citeproc` 自述 `The citeproc-js citation formatting module, in CommonJS format. This version is based on citeproc-js 1.4.63`（见 §1.1(e)）；`citation-js` 的 `dependencies` 直接列出 `citeproc: ^2.4.59`。
- **自身代码许可**：仓库 `https://github.com/citation-js/citation-js`（默认分支 `main`，GitHub license 判定 `MIT`，`pushed_at = 2026-10-11T16:57:50Z`，`archived=false`）；`LICENSE.md` 原文首行 `The MIT License (MIT)` / `Copyright (c) 2015-2026 Lars Willighagen`（`https://raw.githubusercontent.com/citation-js/citation-js/main/LICENSE.md`，检索日期 2026-10-19）。
- 事实层面提醒：**citation-js 自身 MIT 不等于整包无 copyleft**——它分发的依赖 `citeproc` 携带的是 `CPAL-1.0 OR AGPL-1.0`（见 §1.1(e)(f)）。义务按组件计，**具体结论需法务判断**。

---

### 1.6 Zotero 自身（对照：AGPL 生态内使用 citeproc-js）

- **来源**：`https://raw.githubusercontent.com/zotero/zotero/main/COPYING`（检索日期 2026-10-19）原文开头：

```text
Zotero is Copyright © 2018 Corporation for Digital Scholarship, 
Vienna, Virginia, USA http://digitalscholar.org
...
The Corporation for Digital Scholarship distributes the Zotero source code
under the GNU Affero General Public License, version 3 (AGPLv3). The full text
of this license is given below.

The Zotero name is a registered trademark of the Corporation for Digital Scholarship.
See http://zotero.org/trademark for more information.
```

- 注意：**GitHub API 对 zotero/zotero 同样给出 `NOASSERTION`**（`https://api.github.com/repos/zotero/zotero`，检索日期 2026-10-19），原因是 `COPYING` 文件在许可全文前加了版权/商标说明段，而非许可不明。仓库根目录的许可文件名为 `COPYING`（非 `LICENSE`）。仓库 `pushed_at = 2026-10-15T21:58:16Z`。
- **Zotero 确实使用 citeproc-js**（一手代码引用）：`https://raw.githubusercontent.com/zotero/zotero/main/chrome/content/zotero/xpcom/cite.js`（检索日期 2026-10-19）含 `Replace getAbbreviation on citeproc-js with our own handler.`（L505）、`citeproc-js bibliography object`（L134/L136/L156/L158）、`new Zotero.CiteProc.CSL.AbbreviationSegments()`（L603）、`citeproc-js system object`（L610）。
- 另有一手旁证：citeproc-js README 自述其为 `the citation formatter driving word processor integration in both Mendeley and Zotero`（见 §1.1(g)）。
- 事实对照：Zotero 本体即 AGPLv3，故其使用 citeproc-js（AGPL/CPAL 双许可）不存在"闭源 + copyleft"的许可组合张力；**本项目为闭源产品化目标，组合前提不同**（是否兼容由法务判断，本文不给结论）。

---

### 1.7 SPDX 官方许可清单（用于确认 OSI 认可状态与标识符）

- **来源**：`https://raw.githubusercontent.com/spdx/license-list-data/main/json/details/<ID>.json`（检索日期 2026-10-19）

| SPDX ID | 名称 | `isOsiApproved` | `isFsfLibre` | 备注 |
| --- | --- | --- | --- | --- |
| `CPAL-1.0` | Common Public Attribution License 1.0 | **true** | true | citeproc-js 的双许可分支之一 |
| `AGPL-3.0-or-later` | GNU Affero General Public License v3.0 or later | **true** | true | 仓库 LICENSE 原文所述分支 |
| `AGPL-1.0-only` | Affero General Public License v1.0 only | **false** | — | npm `license` 字段写的是这个版本线 |
| `MPL-2.0` | Mozilla Public License 2.0 | **true** | true | citeproc-rs |
| `BSD-2-Clause-Views` | BSD 2-Clause with views sentence | **false** | — | citeproc-py（OSI 未收录，但属 BSD 系宽松许可变体） |

---

## 2. 事实汇总

### 2.1 已确认（均有一手来源，检索日期 2026-10-19）

| # | 事实 | 一手来源 |
| --- | --- | --- |
| 1 | citeproc-js 仓库被 GitHub 标注 `NOASSERTION`（key=other） | `api.github.com/repos/Juris-M/citeproc-js/license` |
| 2 | citeproc-js 采用**双许可**：CPAL v1.0（或更高）**或** AGPL v3（或更高） | 仓库 `LICENSE` 原文 |
| 3 | citeproc-js 的 **CPAL Exhibit B 已填写**（版权 "(c) Frank Bennett"、短语 "citeproc-js implements the Citation Style Language"、URL citationstyles.org），且明确 "Display of Attribution Information is required in Larger Works" → CPAL §14(c) 的免署名条款**不适用** | 仓库 `CPAL` 原文 |
| 4 | CPAL §15 定义 "External Deployment"（含"as an application intended for use over a network"）并规定须按 §3.1/§3.2 提供 Source Code | 仓库 `CPAL` 原文 |
| 5 | AGPLv3 §13 要求：若修改程序，须向远程网络交互用户提供 Corresponding Source 获取途径 | 仓库 `AGPLv3` 原文 |
| 6 | npm 包名为 **`citeproc`**（`citeproc-js` 在 npm 上 404），最新 `2.4.63`，发布于 **2023-04-17**（`time.modified` 2023-07-29） | `npm view citeproc`、`registry.npmjs.org/citeproc/latest` |
| 7 | npm/`package.json` 的 `license` 字段为 `CPAL-1.0 OR AGPL-1.0`，与仓库 LICENSE 的 "AGPL v3 or later" **不一致** | npm registry + 仓库 `package.json` + 仓库 `LICENSE` |
| 8 | citeproc-js 仓库代码仍活跃（最后提交 2026-07-05，未归档），但**代码更新未反映到 npm 发布** | GitHub repo/commits API |
| 9 | citeproc-js README 自述 CSL-M 模式可处理多语言与法律内容；CSL-M 是 Jurism 使用的 CSL 私有扩展 | 仓库 `README.rst` |
| 10 | citeproc-rs 为 **MPL-2.0**，仓库**已归档**，最后提交 2021-12-14，最新 tag `wasm-v0.2.0` | 仓库 `LICENSE.md` + GitHub API |
| 11 | crates.io **不存在** `citeproc` crate（API 404、sparse index NoSuchKey、搜索无同名） | `crates.io/api/v1/crates/citeproc`、`index.crates.io`、crates.io 搜索 |
| 12 | citeproc-rs README 自述 "work-in-progress"，且"将在某个时候发布到 crates.io"（即尚未发布） | 仓库 `README.md` |
| 13 | pandoc 本体 **GPL-2.0-or-later**（`COPYING.md` = GPLv2 正文），当前版本 **3.12.1**（release 2026-10-08），依赖 `citeproc >= 0.14.1 && < 0.15` | `pandoc.cabal`、`COPYING.md`、release API |
| 14 | pandoc 官方 MANUAL **全文无 "CSL-M"（0 命中）**，也未声明 CSL 规范版本号 | `MANUAL.txt` 全文检索 |
| 15 | Haskell `citeproc` 库为 **BSD-2-Clause**（Copyright 2020 John MacFarlane），Hackage 最新 **0.14.1**（上传 2026-10-05），README 自述 **targeting CSL 1.0.2**、自测 818 项中失败 62 项 | 仓库 `LICENSE`/`README.md`、Hackage 页面与 `.cabal` |
| 16 | citeproc-py 最新 **0.11.1**（PyPI 2026-09-01），`license_expression` = **BSD-2-Clause-Views**、`license` 字段为空、classifier 为 `Development Status :: 3 - Alpha` | PyPI JSON API |
| 17 | citeproc-py 仓库 LICENSE 为 BSD-2 条款 + views 句（Copyright 2011-2013 Brecht Machiels）；GitHub 标 `NOASSERTION` | 仓库 `LICENSE`、GitHub API |
| 18 | citeproc-py **维护活跃**：2026 年内 ≥12 个发布、最后提交 2026-09-01、最近 issue #216（2026-09-12） | PyPI JSON、GitHub commits/issues API、CHANGELOG |
| 19 | citeproc-py README 自述 aims to implement **CSL 1.0.2**、**约 60% 测试通过**，缺失 disambiguation/year-suffix、collapsing、punctuation-in-quote、display 等 | 仓库 `README.md` |
| 20 | citation-js 自身 **MIT**（0.9.0，2026-09-18），但 **dependencies 直接依赖 `citeproc: ^2.4.59`**（plugin-csl 依赖 `citeproc: ^2.4.6`）→ 内嵌 citeproc-js | npm registry（`citation-js`、`@citation-js/plugin-csl`）、仓库 `LICENSE.md` |
| 21 | Zotero 本体 **AGPL-3.0**（`COPYING` 原文），GitHub 亦为 `NOASSERTION`；其代码 `cite.js` 明确引用 citeproc-js 与 `Zotero.CiteProc.CSL` | 仓库 `COPYING`、`cite.js`、GitHub API |
| 22 | SPDX 认可状态：`CPAL-1.0` OSI 认可、`AGPL-3.0-or-later` OSI 认可、`AGPL-1.0-only` **非** OSI 认可、`MPL-2.0` OSI 认可、`BSD-2-Clause-Views` **非** OSI 认可 | SPDX `license-list-data` |

### 2.2 未确认（**不得当作结论使用**）

| # | 未确认事项 | 已知的一手事实 |
| --- | --- | --- |
| 1 | citeproc-js 双许可**以哪一份为准**（npm `AGPL-1.0` vs 仓库 LICENSE `AGPL v3+`）；是否存在权利人的公开澄清 | 两份元数据并存且互相冲突（§1.1(f)）；未检索到澄清 |
| 2 | CPAL §14 署名义务在**本产品 UI**（SaaS 页面/桌面端）下是否必须展示 | Exhibit B 已填写、条款原文已确认；**适用性未确认** |
| 3 | AGPLv3 §13 中 "if you modify the Program" 在「**零改动、以独立 Node 子进程调用** citeproc-js」情形下是否触发 | 条款原文已确认；**适用性未确认**（须法务判断） |
| 4 | citeproc-js 自 2023-04 后是否还会更新 npm 包，或已改用其他发布渠道 | npm 停在 `2.4.63`，但仓库 2026 年仍有提交 |
| 5 | pandoc 是否**技术上**能处理 CSL-M 样式 | 官方文档 0 处提及 CSL-M；未找到官方支持声明 |
| 6 | citeproc-py 是否支持 CSL-M | README 无声明；缺失功能清单中也未列 CSL-M |
| 7 | citeproc-js / citeproc-rs 是否提供**可购买的商业许可**（豁免 copyleft 义务） | 未检索到官方商业许可页面 |
| 8 | "citeproc-py 停滞"（含 CSL Discourse 讨论）的证据 | **未找到**任何一手来源；且与 §1.4(d) 的活跃时间线冲突 |
| 9 | citeproc-js 仓库 tag 线与 npm 版本线的对应关系（tags 见 `1.4.61` 等，npm 为 `2.4.63`） | 两条线并存，对应规则未确认 |
| 10 | 各库的许可证与**本项目具体分发形态**（容器镜像 + 网络服务 + 是否修改）的兼容性结论 | 属法律判断，本文不作结论 |

---

## 3. 关键条款风险点（只列条款原文与触发条件，**不构成法律意见**）

1. **citeproc-js · AGPL-3.0 分支的网络服务条款（§13）**：条款文本要求"若修改程序"，则须让远程交互用户能获取 Corresponding Source。**注意**：AGPL 的义务不是"只要用了就必须开源"，而是与"修改""提供网络交互"等条件挂钩；本项目的具体调用形态（独立子进程 / 零改动 / 是否修改）决定适用性 → **未确认，需法务判断**。
2. **citeproc-js · CPAL 分支的归属（署名）条款（§14 + Exhibit B）**：该仓库的 Exhibit B **已填写**，因此 §14(c)「Exhibit B 为空则无署名义务」**不适用**；且明确写明署名要求**适用于 Larger Works**。条款允许的署名内容有上限（版权声明 + ≤10 词短语 + 一张图 + 一个 URL，即 "Attribution Limits"），且要求展示"prominent"。
3. **citeproc-js · CPAL 分支的网络使用条款（§15）**：把"External Deployment"（任何让自身以外的人可使用、包括"作为意图通过网络使用的应用提供"）**视为分发**，从而触发 §3.1/§3.2 的源码可得义务。**这与 AGPL §13 是不同的触发路径：CPAL 的触发点不限于"修改"**。
4. **CPAL 的源码义务（§3.1/§3.2）**：源码形式分发须以本许可进行；可执行形式分发须同时提供源码形式并说明获取方式。
5. **GPL-2.0-or-later（pandoc 本体）**：§2(b) 要求"整体"以本许可授权；**GPLv2 无网络服务条款**（对比 AGPLv3 §13 / GPLv3 §13）。pandoc 所调用的 `citeproc` 库为 BSD-2-Clause（宽松，仅需保留版权与免责声明）。
6. **MPL-2.0（citeproc-rs）**：文件级弱 copyleft——§3.2 要求可执行形式分发时提供源码形式，§3.3 允许把 Covered Software 作为 Larger Work 以自有条款分发（须继续满足 MPL 对 Covered Software 的要求）。**但该项目已归档且无 crates.io 发布物，工程上不可选**。
7. **BSD-2-Clause（Haskell citeproc）/ BSD-2-Clause-Views（citeproc-py）**：宽松许可，实质义务为**保留版权声明与免责声明**（源码与二进制分发形态各一条）；`BSD-2-Clause-Views` 多出的 views 句不改变宽松属性，但 SPDX 未将其列为 OSI 认可。
8. **组合分发（citation-js）**：外层 MIT + 内层 citeproc-js（CPAL/AGPL）→ 不能因外层是 MIT 就认为整体无 copyleft 义务。
9. **元数据不一致本身即是风险**：GitHub 的 `NOASSERTION`（citeproc-js、citeproc-rs、citeproc-py、zotero 四处）+ npm 与仓库 LICENSE 的 AGPL 版本冲突，意味着**自动化许可证扫描工具（SCA）大概率无法给出可用结论**，需人工留证（本文件即为此用途）。

---

## 4. 检索方法与可复现命令

> 全部为**只读**命令，未安装任何依赖到项目 venv，未做任何写入（本文件除外）。

```powershell
# 1) 仓库 LICENSE / README 原文（curl，写入临时目录）
curl.exe -sSL -o "$env:TEMP\citeproc-js-LICENSE.txt" https://raw.githubusercontent.com/Juris-M/citeproc-js/master/LICENSE
curl.exe -sSL -o "$env:TEMP\citeproc-js-CPAL.txt"    https://raw.githubusercontent.com/Juris-M/citeproc-js/master/CPAL
curl.exe -sSL -o "$env:TEMP\citeproc-js-AGPLv3.txt"  https://raw.githubusercontent.com/Juris-M/citeproc-js/master/AGPLv3
curl.exe -sSL -o "$env:TEMP\citeproc-rs-LICENSE.md"  https://raw.githubusercontent.com/zotero/citeproc-rs/master/LICENSE.md
curl.exe -sSL -o "$env:TEMP\zotero-COPYING.txt"      https://raw.githubusercontent.com/zotero/zotero/main/COPYING
curl.exe -sSL -o "$env:TEMP\pandoc-COPYING.md"       https://raw.githubusercontent.com/jgm/pandoc/main/COPYING.md
curl.exe -sSL -o "$env:TEMP\haskell-citeproc-LICENSE.txt" https://raw.githubusercontent.com/jgm/citeproc/master/LICENSE
curl.exe -sSL -o "$env:TEMP\citeproc-py-LICENSE.txt" https://raw.githubusercontent.com/citeproc-py/citeproc-py/master/LICENSE

# 2) registry 元数据
npm view citeproc license version time.modified              # CPAL-1.0 OR AGPL-1.0 / 2.4.63 / 2023-07-29
npm view citeproc time --json                                 # 全部版本发布时间
npm view citation-js license version dependencies             # MIT / 0.9.0 / citeproc ^2.4.59
npm view '@citation-js/plugin-csl' license version dependencies
curl.exe -sSL https://pypi.org/pypi/citeproc-py/json          # license_expression = BSD-2-Clause-Views
curl.exe -sSL -H "User-Agent: maa-csl-license-audit/1.0" https://crates.io/api/v1/crates/citeproc   # 404: does not exist

# 3) GitHub 元数据（gh 已登录；只读）
gh api repos/Juris-M/citeproc-js --jq '.pushed_at, .archived, .license.spdx_id'
gh api repos/zotero/citeproc-rs   --jq '.full_name, .archived, .pushed_at'
gh api repos/jgm/pandoc/releases/latest --jq '.tag_name, .published_at'

# 4) SPDX 许可清单
curl.exe -sSL https://raw.githubusercontent.com/spdx/license-list-data/main/json/details/CPAL-1.0.json
```

**踩坑记录（供后续复现）**：① npm 包名是 `citeproc`，查 `citeproc-js` 会 404；② `crates.io` 无 UA 头会返回 **403**（不是"crate 不存在"），加 `User-Agent` 后才是可信的 **404**；③ `gh api --jq` 中带内层双引号的复杂表达式在 PowerShell 下会被拆参数（报 `accepts 1 arg(s), received N`），改用简单 jq 或落地为文件后用 Python 解析；④ GitHub 未认证 API 限额 60 次/小时，本次已触发 403。

---

## 5. 主要来源（全部为检索日期 2026-10-19 访问的一手来源）

- [Juris-M/citeproc-js 仓库](https://github.com/Juris-M/citeproc-js)｜[LICENSE 原文](https://raw.githubusercontent.com/Juris-M/citeproc-js/master/LICENSE)｜[CPAL 全文（含 Exhibit B）](https://raw.githubusercontent.com/Juris-M/citeproc-js/master/CPAL)｜[AGPLv3 全文](https://raw.githubusercontent.com/Juris-M/citeproc-js/master/AGPLv3)｜[README.rst](https://raw.githubusercontent.com/Juris-M/citeproc-js/master/README.rst)｜[license API（NOASSERTION）](https://api.github.com/repos/Juris-M/citeproc-js/license)
- [npm `citeproc` 包元数据](https://registry.npmjs.org/citeproc/latest)｜[npm 包页面](https://www.npmjs.com/package/citeproc)
- [zotero/citeproc-rs 仓库](https://github.com/zotero/citeproc-rs)｜[LICENSE.md（MPL-2.0）](https://raw.githubusercontent.com/zotero/citeproc-rs/master/LICENSE.md)｜[crates.io `citeproc`（不存在）](https://crates.io/api/v1/crates/citeproc)
- [jgm/pandoc `COPYING.md`（GPLv2）](https://raw.githubusercontent.com/jgm/pandoc/main/COPYING.md)｜[`pandoc.cabal`（GPL-2.0-or-later）](https://raw.githubusercontent.com/jgm/pandoc/main/pandoc.cabal)｜[`MANUAL.txt`](https://raw.githubusercontent.com/jgm/pandoc/main/MANUAL.txt)｜[pandoc 3.12.1 release](https://github.com/jgm/pandoc/releases/tag/3.12.1)
- [jgm/citeproc LICENSE（BSD-2）](https://raw.githubusercontent.com/jgm/citeproc/master/LICENSE)｜[README（CSL 1.0.2）](https://raw.githubusercontent.com/jgm/citeproc/master/README.md)｜[Hackage `citeproc` 0.14.1](https://hackage.haskell.org/package/citeproc)
- [PyPI `citeproc-py` JSON API](https://pypi.org/pypi/citeproc-py/json)｜[仓库 LICENSE](https://raw.githubusercontent.com/citeproc-py/citeproc-py/master/LICENSE)｜[README（CSL 兼容性）](https://raw.githubusercontent.com/citeproc-py/citeproc-py/master/README.md)｜[CHANGELOG](https://raw.githubusercontent.com/citeproc-py/citeproc-py/master/CHANGELOG.md)
- [npm `citation-js`](https://registry.npmjs.org/citation-js)｜[npm `@citation-js/plugin-csl`](https://registry.npmjs.org/@citation-js/plugin-csl)｜[citation-js LICENSE.md（MIT）](https://raw.githubusercontent.com/citation-js/citation-js/main/LICENSE.md)
- [zotero/zotero COPYING（AGPL-3.0）](https://raw.githubusercontent.com/zotero/zotero/main/COPYING)｜[zotero `cite.js`（citeproc-js 引用）](https://raw.githubusercontent.com/zotero/zotero/main/chrome/content/zotero/xpcom/cite.js)
- [SPDX license-list-data](https://github.com/spdx/license-list-data)：[CPAL-1.0](https://raw.githubusercontent.com/spdx/license-list-data/main/json/details/CPAL-1.0.json)｜[AGPL-3.0-or-later](https://raw.githubusercontent.com/spdx/license-list-data/main/json/details/AGPL-3.0-or-later.json)｜[AGPL-1.0-only](https://raw.githubusercontent.com/spdx/license-list-data/main/json/details/AGPL-1.0-only.json)｜[MPL-2.0](https://raw.githubusercontent.com/spdx/license-list-data/main/json/details/MPL-2.0.json)｜[BSD-2-Clause-Views](https://raw.githubusercontent.com/spdx/license-list-data/main/json/details/BSD-2-Clause-Views.json)
