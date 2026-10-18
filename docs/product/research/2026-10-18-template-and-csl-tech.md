# 多智能体文献综述系统：模板化导出 + 引用格式切换 技术调研

调研时间：2026-10-18 ｜ 目标栈：Python 3.11 + FastAPI + Vue3 ｜ 已有能力：Markdown / DOCX 导出

## A. Word / DOCX 模板化渲染

| 库 | 版本（发布日） | 许可证 | 能力与硬限制 |
|---|---|---|---|
| python-docx | 1.2.0（2025-06-16） | MIT | 读写 docx；**不能创建** numbering.xml、域、内容控件 |
| docxtpl | 0.20.2（2025-11-13） | **LGPL-2.1-only** | Jinja2 渲染 .docx；`{%p%}/{%tr%}/{%tc%}/{%r%}` 控制段落/行/列/run；RichText(R)、RichTextParagraph、InlineImage、Subdoc、replace_pic/media/zipname、get_undeclared_template_variables |
| docxcompose | 2.2.0（2026-06-02） | MIT | 合并多 docx；**只保留第一篇的页眉页脚**；样式默认取第一篇（`preserve_styles=True` 可改） |
| Spire.Doc | Free / Commercial | 商业 | 免费版硬限制 **500 段落 / 25 表格**，转 PDF 只出前 3 页；$799 Developer Small Business（1 人 1 部署点）、$1,298 Developer OEM、$2,499 Site OEM，含 1 年更新与支持 |
| Aspose.Words for Python via .NET | 25.x | 商业 | $1,199 档**明确禁止 Web/SaaS**（对比表 For use with web sites/apps = ✕），SaaS 必须 **$3,597** Developer OEM 起 |

**SDT（内容控件）与 Jinja**：docxtpl **不解析** `w:sdt`，SDT 只是承载 `w:t` 的普通容器，没有"按控件 tag 名取值"的 API；Jinja 标签必须落在 SDT 内部文本里才能被渲染。因此"用户上传 .docx 模板"的正确做法是**自研占位符契约**：约定 `{{ key }}` 命名规则，上传时用 python-docx 遍历段落/表格/页眉/页脚，并用 docxtpl 的 `get_undeclared_template_variables()` 直接产出"待填字段清单"，让用户在 UI 上映射到综述章节。

**保留样式**：字体、页眉页脚、页码（PAGE 域）、目录（TOC 域）、交叉引用（REF 域）都能原样保留在模板里不被破坏——但 python-docx **无法新建或刷新这些域**，生成的文档目录仍是旧内容，需 Word 内 Ctrl+A→F9 或 LibreOffice headless 刷新。

**LaTeX 路线**：pandoc 3.12.1（2026-10-08，GPL-2.0-or-later）可在 docx↔LaTeX 间转换，但自定义样式、域、浮动体会丢；国内高校盲审/提交平台多数只收 docx/PDF。结论：可作"高级导出"选项，不做主链路。

## B. 引用格式引擎（CSL）

- **规范**：CSL 1.0.2，2022-01-02 发布（[规范](https://docs.citationstyles.org/en/stable/specification.html)、[发布时间线](https://citationstyles.org/developers/)）；配套 RELAX NG schema，作者已决定改用 MIT，但旧发布文本尚未更新。
- **官方样式库**：[citation-style-language/styles](https://github.com/citation-style-language/styles)，**10,000+ 样式**（Zotero 官方文档口径 "over 10,000"），授权 **CC BY-SA 3.0**，使用必须署名 CSL 项目并链接 citationstyles.org，且需保留原作者列表。下游集成应从 `v1.0.2` 分支取样式而非 master。
- **中文样式**：`china-national-standard-gb-t-7714-2015-numeric.csl` 与 `...-author-date.csl` **官方已收录**（default-locale=zh-CN，2024-01 更新，作者牛耕田/Zeping Lee）。**GB/T 7714-2005 在官方仓库不存在**（`china-national-standard-gb-t-7714-2005-numeric.csl` 返回 404）；社区库 [zotero-chinese/styles](https://github.com/zotero-chinese/styles)（原 redleafnew/Chinese-STD-GB-T-7714-related-csl，CC BY-SA 3.0，6.3k star）覆盖 GB/T 7714 全系列与高校学位论文样式，但双语混排（"等"/"et al."）依赖 **CSL-M** 扩展，标准 1.0.2 处理器会报"不是有效 CSL 1.0.2 样式"。
- **处理器对比**：
  - `citeproc-js`（[Juris-M/citeproc-js](https://github.com/Juris-M/citeproc-js)）：Zotero/Mendeley 实际使用的引擎，支持 CSL-M 扩展；GitHub 未标注 SPDX（NOASSERTION），商用前需自行核对许可（**未确认**）。
  - `citeproc-rs`：Zotero 新一代 Rust 实现，性能好但生态/文档弱。
  - `pandoc --citeproc`：Haskell `citeproc`，pandoc **3.12.1** 起改变 CSL-JSON 姓名 particle 解析（`family: de Gaulle` 不再拆出 particle），可能影响既有书目输出。
  - `citeproc-py`：0.10.x，长期维护薄弱（社区在 CSL Discourse 多次讨论其停滞），生产环境慎用。
- **互通**：Zotero Web API 原生导出 `bibtex / biblatex / csljson / ris / mods / tei / refer / rdf_*`；也可 `format=bib&style=china-national-standard-gb-t-7714-2015-numeric&locale=zh-CN` 直接拿服务端渲染好的参考文献（**上限 150 条**）。**内部统一以 CSL-JSON 为唯一真源**是规避格式地狱的关键。

## C. 文献管理工具集成

- **Zotero Web API v3**（`https://api.zotero.org`，必须 HTTPS）：公开库只读免鉴权；读私有库/写库需 API key（`Zotero-API-Key` 头或 `Authorization: Bearer`）或 **OAuth 1.0a（尚不支持 OAuth 2.0）**。分页 `limit` 1–100（默认 25）；限流通过 `Backoff:` 响应头与 `429 + Retry-After`，官方建议**并发 ≤4**；支持 `If-Modified-Since-Version` 条件请求与 `?since=` 增量同步（[文档](https://www.zotero.org/support/dev/web_api/v3/basics)）。
- **Zotero Local API**（`http://localhost:23119/api/`）：读免鉴权且**不施加速率限制**，桌面端集成首选；**写请求需 Zotero 10+ 且用户在运行时授权本地 API key**。
- **EndNote / NoteExpress**：**无官方开放 API**（[NoteExpress 仅有非官方接口说明](http://www.inoteexpress.com/supportcn/cgi-bin/threaded_show.cgi?tid=6511)），只能用 EndNote XML / RIS 文件交换。Mendeley API 仍存在但现行状态**未确认**。
- **元数据源**（限流以官方文档为准，标注项为未逐字复核）：OpenAlex（免费，另有付费 Premium API；建议带 `mailto` 进 polite pool）、Crossref REST（免费，polite pool；公开限流**未确认**）、Semantic Scholar（无 key 共享池，有 key 提升配额；具体数值**未确认**）、arXiv（官方要求请求间隔 ≥3 秒，另提供 OAI-PMH 批量）、PubMed E-utilities（无 key 3 req/s，带 key 10 req/s）、Europe PMC（免 key）、Unpaywall（**必须带 email**，约 10 万次/日）。

## D. 其他导出与交付形态

| 方案 | 许可证 | 中文 / 保真 | 取舍 |
|---|---|---|---|
| WeasyPrint | BSD-3-Clause | 需自带 Noto Sans CJK；paged media 强，但**不认 docx 布局** | 适合"HTML 模板 → PDF" |
| Playwright / Chromium print-to-PDF | Apache-2.0 | 需打包中文字体；页码页眉靠 headerTemplate 或 Paged.js | **推荐为 PDF 主链路**（可复用现有 Vue 组件） |
| LibreOffice headless | MPL-2.0 | docx→PDF 保真最好，存在排版漂移 | 适合交付前的服务端预览 |
| docx-preview（npm 0.3.5） | MIT | 纯前端渲染 docx，保真中等，**不渲染 TOC/REF 域** | 在线预览首选 |
| mammoth.js | BSD-2-Clause | 语义化 HTML，样式大量丢失 | 仅适合内容提取 |
| OnlyOffice Docs 9.4 | **AGPL-3.0** | 原生 docx 预览 + 协作；2026-05 起社区版**取消 20 并发连接限制**，新增署名/版权/修改版本标识要求，商标单独授权 | 闭源 SaaS 需评估 AGPL |
| Collabora Online | **AGPL-3.0** | 基于 LibreOffice，商业版付费 | 同上 |

**AGPL 风险**：SaaS 场景下若修改源码并以网络服务对外提供，须向用户提供对应源码；"不改源码原样容器部署 + 保留版权声明和源码获取入口"通常可接受，但**改一行即触发**。建议 OnlyOffice/Collabora 独立容器、零改动部署，或购买商业授权。

**实时协作**：Yjs（MIT，CRDT）需自建 ProseMirror/Tiptap 富文本层并做 docx 双向转换——往返必然丢格式；OnlyOffice/Collabora 原生支持 docx 协作但受 AGPL 约束。综述系统当前阶段不必上 CRDT。

## 1. 技术选型建议

| 环节 | 推荐 | 理由 | 风险 / 许可证 |
|---|---|---|---|
| 模板渲染 | python-docx 1.2.0 + docxtpl 0.20.2 | 唯一成熟的 "docx 当模板" 方案；子文档、RichText、图片替换齐全 | docxtpl 为 **LGPL-2.1-only**：以库形式 import 通常可接受，但打包单文件 exe、修改源码或静态分发需开源对应部分或走商业咨询 |
| 文档合并 | docxcompose 2.2.0 | MIT，专治多章节合并 | 只保留第一篇页眉页脚；合并需预置同一套 `numbering.xml` |
| 引用引擎 | 内部 **CSL-JSON 唯一真源** + 服务端 citeproc-js（Node 子进程），Zotero `format=bib` 兜底 | 与 Zotero/EndNote 互通零成本，中文样式官方可得 | citeproc-js SPDX **未确认**；citeproc-py 不建议生产使用 |
| 在线预览 | docx-preview | MIT、纯前端、无服务端依赖 | 不渲染域，复杂浮动排版失真 |
| PDF | Playwright/Chromium 打印为主，LibreOffice headless 兜底 | 与现有 Vue3 前端共用渲染链路 | 需随包分发中文字体 |
| 高保真/协作 | OnlyOffice Docs 9.4（可选，后期） | 原生 docx | AGPL-3.0 |
| 不推荐 | Spire.Doc / Aspose.Words | Aspose 低价档禁止 Web/SaaS，合规成本高；Spire 免费版 500 段落限制会直接击穿综述长度 | — |

## 2. 最小可行架构：模板导入 + 模板切换 + 引用格式切换

```
Template(id, name, version, source_docx_uri, kind,              # 学位论文 / 期刊 / 报告
         placeholder_contract: JSON, style_map: JSON,
         owner_id, is_public, checksum)
PlaceholderContract: [{key, label, type(text|richtext|image|table|list|citation_block),
                       required, sample, word_style}]
CitationStyle(id, name, csl_id, csl_xml, is_builtin, source)     # 从 styles 仓库 v1.0.2 分支同步
Project(id, template_id, citation_style_id, locale, status)      # 切换入口只在这张表
Reference(id, csl_json: JSON, doi, source, zotero_key)           # 唯一真源
SectionDoc(id, project_id, ast_json, cite_marks)                 # 正文 AST，引用标记存 [[cite:ref_id]]
RenderJob(id, project_id, status, output_uri, error, trace)
```

**占位符契约**：上传模板即扫描（`get_undeclared_template_variables()`）→ 生成 `placeholder_contract` → 用户在 UI 把 `{{ literature_review }}` 绑到综述章节、`{{ refs }}` 绑到参考文献块。契约随 Template 版本化，模板改版必须重新校验。

**样式映射 `style_map`**：`Word 样式 ID → 语义角色`（H1/H2/H3/正文/图注/表注/参考文献）。切换模板时只做"角色→角色"重映射，正文 AST 完全不动，这是"模板可切换"的核心。

**渲染流水线**：`AST → (1) 组装 docxtpl context + Subdoc → (2) 引用块用 citeproc 渲染成编号 RichText 注入 → (3) TOC/REF 域原样保留不解析 → (4) 可选 LibreOffice 刷新域 → (5) 落盘 + 版本记录`。

**切换引用格式**：只改 `Project.citation_style_id` 并重跑第 (2) 步。前提是正文里的引用标记必须存成 `[[cite:ref_id]]` 占位而非硬编码数字——否则切换 GB/T 7714 顺序编码制 ↔ 著者-出版年制时正文编号全废。

## 3. 已知工程坑清单（尤其中文）

1. **`w:rFonts` 必须同时设 `ascii/hAnsi/eastAsia/cs`**。只设 ascii，中文会回退宋体。python-docx 无 API，需手写 rPr XML；docxtpl 官方解法是 `RichText('测试', font='eastAsia:微软雅黑')`。
2. **字体嵌入**（`w:embedRegular`）python-docx 不支持，换机器必掉字体。模板只允许宋体/黑体/微软雅黑，或最终交付 PDF。
3. **样式名本地化**：中文版 Word 的"标题 1"与 `Heading 1` 的 name/styleId 不一致，python-docx 按 name 查找易 `KeyError`，一律改用 styleId。
4. **编号列表**：python-docx 无法生成 `numbering.xml`，只能用模板预置的列表样式；docxcompose 合并时 `numId` 重映射容易错乱。
5. **TOC 域 / REF 交叉引用域 / PAGE 页码域**：python-docx 既不能创建也不能更新，生成的目录是旧内容，必须 Word 内 F9 或 LibreOffice 刷新。
6. **脚注/尾注**在 `footnotes.xml` / `endnotes.xml`，Subdoc 不处理，合并易丢。参考文献建议走正文末尾章节，而非真尾注。
7. **Jinja 标签被 Word 拆进多个 run**：docxtpl 会自动合并，但 `{%p%}/{%tr%}` 必须独占段落，且必须写 `{{ x }}` 而非 `{{x}}`，`{%p` 与 `{%tr` 不可在同一段落复用。
8. **含 `<` `>` `&` 的内容必须 `autoescape=True` 或 `|e`**，否则渲染直接崩溃（docx 是 XML）。
9. **Word 2016 会吞掉 `\t` 与行首空格**，需用 RichText 包裹。
10. **双语混排**（中文"等" / 英文 "et al."）需 CSL-M，标准 CSL 1.0.2 处理器不支持 → 要么接受单语言输出，要么上 citeproc-js 的 CSL-M 模式并接受样式校验告警。
11. **GB/T 7714 的 `[J]/[M]/[D]` 文献类型标识**依赖 CSL item type 映射，CNKI 导入题录类型常错，需在 Reference 层做类型校正。
12. **CSL 样式授权**：官方样式 CC BY-SA 3.0，必须署名 CSL 项目；若允许用户上传自建样式，注意 ShareAlike 条款与再分发限制。
13. **locale**：样式 `default-locale="zh-CN"`，需同时下发 zh-CN / zh-TW locale 文件，否则日期与术语回退英文。
14. **中文标点断行/挤压**（`w:kinsoku`、`w:overflowPunct`）python-docx 无 API，只能模板预设。
15. **模板是不可信输入**：必须拒绝 `.docm`、剥离 `w:attachedTemplate` 等外部关系、限制 OLE、防 zip 炸弹。

## 主要来源

- [python-docx (PyPI, 1.2.0, MIT)](https://pypi.org/project/python-docx/)
- [docxtpl (PyPI, 0.20.2, LGPL-2.1)](https://pypi.org/project/docxtpl/) ｜ [docxtpl 文档](https://docxtpl.readthedocs.io/en/latest/)
- [docxcompose (PyPI, 2.2.0, MIT)](https://pypi.org/project/docxcompose/)
- [Free Spire.Doc for Python 限制](https://cdn.e-iceblue.com/Introduce/free-doc-for-python.html) ｜ [Spire.Doc for Python 价格](https://d21t6da3t4l1k0.cloudfront.net/Buy/Spire.Doc-Python.html)
- [Aspose.Words for Python via .NET 价格](https://purchase.aspose.com/pricing/words/python-net/)
- [CSL Developers / 处理器列表 / 发布时间线](https://citationstyles.org/developers/)
- [citation-style-language/styles（10k+ 样式，CC BY-SA 3.0）](https://github.com/citation-style-language/styles) ｜ [Zotero 样式说明](https://www.zotero.org/support/styles)
- [zotero-chinese/styles（中文 CSL 样式库）](https://github.com/zotero-chinese/styles)
- [citeproc-js](https://github.com/Juris-M/citeproc-js) ｜ [pandoc 3.12.1 release notes](https://github.com/jgm/pandoc/releases/tag/3.12.1)
- [Zotero Web API v3 基础（鉴权 / 限流 / 导出格式）](https://www.zotero.org/support/dev/web_api/v3/basics) ｜ [Zotero Local API](https://www.zotero.org/support/dev/web_api/v3/local_api)
- [Unpaywall REST API](https://unpaywall.org/products/api) ｜ [Crossref 访问与鉴权](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)
- [ONLYOFFICE Docs 9.4：社区版取消 20 并发限制、AGPL 条款调整](https://www.ithome.com/0/953/867.htm) ｜ [ONLYOFFICE 9.4 官方博客](https://www.onlyoffice.com/blog/zh-hans/2026/05/onlyoffice-docs-9-4)
