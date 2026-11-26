# VeriPaper Clipper（浏览器剪藏扩展）

把知网（CNKI）/ 维普（CQVIP）/ 万方（Wanfang）**文献详情页**的题录（含摘要）一键保存到
[VeriPaper](https://veripaper.me) 引用库。

> Issue #67 Step 2：**零爬虫、100% 合规** —— 只读用户自己打开的页面 DOM 与 `<meta>`，
> 不站内翻页、不批量抓取、不请求第三方接口。保存走用户自己的 API Key（BYOK）。

## 支持的页面

- 知网：`kns.cnki.net`
- 维普：`qikan.cqvip.com`
- 万方：`*.wanfangdata.com.cn`

## 安装（未上架商店，开发者模式加载）

1. 下载/克隆本仓库，进入 `extensions/veripaper-clipper/`
2. Chrome / Edge 打开 `chrome://extensions`（Edge 为 `edge://extensions`）
3. 打开右上角「开发者模式」→「加载已解压的扩展程序」→ 选择该目录
4. 点扩展图标 →「连接设置」：
   - **VeriPaper 地址**：`https://veripaper.me`（本地开发填 `http://localhost:5173` 等）
   - **API Key**：在 [VeriPaper 设置 · 开放 API](https://veripaper.me/settings/api-keys)
     生成（格式 `maa_...`）。Key 只保存在本机 `chrome.storage.local`，不会上传任何第三方。

## 使用

1. 在知网 / 维普 / 万方的文献详情页打开扩展
2. 点「提取当前页题录」→ 预览题名 / 期刊 / 年份，并显示「含摘要」「DOI」标记
3. 点「保存到引用库」→ 结果提示「新增 N 条 / 已存在 N 条」
4. 回到 VeriPaper「导入文献」页或「引用库」页即可看到条目；创建任务时勾选「我的引用库」

## 抓取哪些字段

优先读标准 `<meta name="citation_*">`（Google Scholar 方案），缺失字段再用各站 DOM 选择器补：

| 字段 | 来源 |
| --- | --- |
| 题名 | `citation_title` → 页面标题选择器 |
| 作者 | `citation_author`（多个） |
| 期刊 / 会议 | `citation_journal_title` / `citation_conference_title` |
| 年份 | `citation_publication_date` / `citation_year` |
| DOI | `citation_doi` / 页面文本正则 |
| 摘要 | `citation_abstract` → 摘要容器选择器（截断 4000 字） |
| URL | 当前页面地址（不伪造） |

- **拿不到年份或 URL/DOI 的条目**由后端准入逻辑拒绝（不会编造数据）；
- 摘要只作为**题录摘要证据**（`abstract_only`）入库，不会被当成全文分片；
- 抓取不到摘要时不填，避免把页面导航文本当摘要。

## 鉴权

扩展使用 VeriPaper 的**开放 API 通道**：`POST /api/v1/reference-library`，
请求头 **`X-API-Key: maa_...`**（不是 `Authorization: Bearer`）。

- Key 由用户自己生成、可随时在设置页吊销；
- 保存结果按 `ref_id`（DOI 优先，否则题名派生）幂等 —— 重复剪藏不会产生重复条目；
- 套餐不含「开放 API」时返回 403，扩展会提示检查套餐。

## 隐私

- 扩展只把**当前页题录 + 你配置的 API Key**发送到你自己填的 VeriPaper 地址；
- 无遥测、无第三方请求、无远程代码；
- 卸载扩展即清除本机 Key（`chrome.storage.local`）。

## 文件结构

```
extensions/veripaper-clipper/
├── manifest.json     Manifest V3
├── content.js        页面题录提取（只读 DOM/meta）
├── background.js     Service Worker：POST /api/v1/reference-library（X-API-Key）
├── popup.html        弹窗 UI
├── popup.js          设置 + 提取 + 保存流程
└── README.md
```
