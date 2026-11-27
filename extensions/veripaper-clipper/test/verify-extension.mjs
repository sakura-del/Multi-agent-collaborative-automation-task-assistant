#!/usr/bin/env node
/**
 * VeriPaper Clipper 自动化冒烟（不依赖知网/维普账号）：
 *
 * 在真实 Chromium + 已加载扩展的环境里，用本地 fixture 页面模拟：
 *   - 知网（`citation_*` meta 路径）
 *   - 维普（DOM 兜底路径）
 *   - 开放 HTML 正文页（无 `citation_*`，正文取 `<article>` / `<main>` 内 `<p>`）
 * 并用本地 mock 服务同时模拟两个后端入口：
 *   - `POST /api/v1/reference-library`（题录 JSON）
 *   - `POST /api/v1/knowledge/documents`（题录关联的 PDF multipart，字段 `ref_id` + `file`）
 *
 * 验证：
 *   1. Manifest V3 扩展能正常加载、Service Worker 启动；
 *   2. content.js 能提取 CslItem（题录/摘要），开放 HTML 页还能给出 `source.extracted_text`；
 *   3. background.js 用 `X-API-Key` 头先保存题录，再上传 PDF 并带 `ref_id` 关联；
 *   4. popup 选择 PDF 后一次点击保存，展示「新增题录 + 全文已关联」合并结果；
 *      未选 PDF 时行为与旧版一致（只保存题录）；选 `.caj` 时提示先转 PDF。
 *
 * 运行（需要 Playwright + Linux 无头环境用 Xvfb）：
 *   PLAYWRIGHT_ROOT=/home/ylin/vp-test/frontend \
 *   xvfb-run -a /tmp/node22/bin/node extensions/veripaper-clipper/test/verify-extension.mjs
 *
 * 只在测试机（10.0.10.2）运行；本机不跑测试。
 */

import { createRequire } from "node:module";
import { createServer } from "node:http";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(here, "..");
const playwrightRoot = process.env.PLAYWRIGHT_ROOT || process.cwd();
const requireFromPlaywrightRoot = createRequire(pathToFileURL(join(playwrightRoot, "package.json")));
const { chromium } = requireFromPlaywrightRoot("playwright");

const LIBRARY_PATH = "/api/v1/reference-library";
const DOCUMENTS_PATH = "/api/v1/knowledge/documents";

const CNKI_URL = "https://kns.cnki.net/kcms2/article/abstract?v=ext-smoke";
const CQVIP_URL = "https://qikan.cqvip.com/Qikan/Article/Detail?id=ext-smoke";
// 开放 HTML 正文页 fixture：无 citation_*；域名在 content_scripts 的 `*.wanfangdata.com.cn` 匹配里。
const OPEN_ACCESS_URL = "https://med.wanfangdata.com.cn/Paper/Detail?id=ext-open-access";
const OPEN_ACCESS_HTML = readFileSync(join(here, "fixture-open-access.html"), "utf8");
const OPEN_ACCESS_TITLE = "大语言模型在代码审查中的实证研究（开放获取冒烟）";

const CNKI_HTML = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8" />
<title>检索增强生成在软件工程中的应用综述（扩展冒烟）_知网</title>
<meta name="citation_title" content="检索增强生成在软件工程中的应用综述（扩展冒烟）" />
<meta name="citation_author" content="张犬俊" />
<meta name="citation_author" content="李四" />
<meta name="citation_journal_title" content="软件学报" />
<meta name="citation_publication_date" content="2026-03-15" />
<meta name="citation_doi" content="10.13328/j.cnki.jos.009999" />
<meta name="citation_abstract" content="扩展冒烟摘要：检索增强生成（RAG）已成为大语言模型落地软件工程的重要范式，本文覆盖代码生成、缺陷修复与测试三个方向。" />
</head><body><h1 class="title">检索增强生成在软件工程中的应用综述（扩展冒烟）</h1></body></html>`;

const CQVIP_HTML = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8" />
<title>维普 DOM 兜底冒烟页</title>
</head><body>
<h1 class="title">基于大模型的代码缺陷修复方法研究</h1>
<div class="author">王五;赵六</div>
<div class="journal-title">计算机工程与应用</div>
<div class="year">2025</div>
<div class="doi">10.3778/j.issn.1002-8331.2509-0001</div>
<div id="abstract">维普 DOM 兜底摘要：面向自动化缺陷修复的大模型方法综述。</div>
</body></html>`;

/** 冒烟用最小 PDF（mock 不解析 PDF，只断言 multipart 里的字段与文件名）。 */
const FAKE_PDF = Buffer.from(
  [
    "%PDF-1.4",
    "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj",
    "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj",
    "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj",
    "trailer<</Root 1 0 R>>",
    "%%EOF",
    "",
  ].join("\n"),
  "utf8",
);

const failures = [];

function check(name, condition, detail) {
  const ok = Boolean(condition);
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail === undefined ? "" : ` :: ${detail}`}`);
  if (!ok) {
    failures.push(name);
  }
}

function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`timeout: ${label}`)), ms)),
  ]);
}

function capturesOf(captured, path) {
  return captured.filter((entry) => entry.path === path);
}

/**
 * 取当前活动标签页 id。
 *
 * 扩展没有把知网/维普/万方写进 host_permissions（真实使用靠 popup 的 activeTab
 * 临时授权），所以 chrome.tabs.query 对这三个站点拿不到 url/title；tab id
 * 本身始终可读。这里统一用「把目标页 bringToFront 后查当前窗口的活动标签」。
 */
async function activeTabId(serviceWorker) {
  return serviceWorker.evaluate(async () => {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    return tab?.id;
  });
}

async function sendExtract(serviceWorker, tabId, attempts = 20) {
  let lastError;
  for (let index = 0; index < attempts; index += 1) {
    try {
      return await withTimeout(
        serviceWorker.evaluate(
          async (id) => chrome.tabs.sendMessage(id, { type: "VERIPAPER_EXTRACT" }),
          tabId,
        ),
        5000,
        "extract",
      );
    } catch (error) {
      lastError = error;
      await new Promise((done) => setTimeout(done, 300));
    }
  }
  throw lastError;
}

async function main() {
  // ---- 本地 mock：题录 JSON + PDF multipart 两个入口 ----
  const captured = [];
  const server = createServer((request, response) => {
    const path = (request.url || "").split("?")[0];
    const isLibrary = path === LIBRARY_PATH;
    const isDocuments = path === DOCUMENTS_PATH;
    if (request.method !== "POST" || (!isLibrary && !isDocuments)) {
      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ detail: "not found" }));
      return;
    }
    const chunks = [];
    request.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
    request.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      let body = null;
      if (isLibrary) {
        try {
          body = JSON.parse(raw);
        } catch {
          body = null;
        }
      }
      captured.push({ path, headers: request.headers, raw, body });

      if (request.headers["x-api-key"] !== "maa_smoke_test") {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ detail: "invalid api key" }));
        return;
      }

      response.writeHead(201, { "Content-Type": "application/json" });
      if (isDocuments) {
        response.end(
          JSON.stringify({
            id: "doc-smoke-0001",
            title: "剪藏 PDF",
            doc_type: "pdf",
            index_status: "ready",
            chunk_count: 3,
            created_at: "2026-11-27T00:00:00Z",
            source: "upload",
            original_name: "paper.pdf",
            failure_reason: null,
          }),
        );
        return;
      }
      response.end(JSON.stringify({ created: 1, skipped: 0, merged: 0, rejected: [] }));
    });
  });
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  const mockPort = server.address().port;
  const apiBase = `http://127.0.0.1:${mockPort}`;

  const pageErrors = [];
  const watchPage = (page) => {
    page.on("pageerror", (error) => pageErrors.push(String(error)));
    return page;
  };

  const userDataDir = mkdtempSync(join(tmpdir(), "vp-clipper-smoke-"));
  let context;
  try {
    context = await chromium.launchPersistentContext(userDataDir, {
      headless: false,
      viewport: { width: 1280, height: 900 },
      args: [`--disable-extensions-except=${extensionRoot}`, `--load-extension=${extensionRoot}`],
    });

    // fixture 页面路由：真实域名 + 本地 HTML，content_scripts 才会注入
    await context.route("https://kns.cnki.net/**", (route) =>
      route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: CNKI_HTML }),
    );
    await context.route("https://qikan.cqvip.com/**", (route) =>
      route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: CQVIP_HTML }),
    );
    await context.route("https://med.wanfangdata.com.cn/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/html; charset=utf-8",
        body: OPEN_ACCESS_HTML,
      }),
    );

    // ---- 1. 扩展加载 ----
    let serviceWorker = context.serviceWorkers()[0];
    if (!serviceWorker) {
      serviceWorker = await withTimeout(
        context.waitForEvent("serviceworker"),
        15000,
        "serviceworker",
      );
    }
    const extensionId = new URL(serviceWorker.url()).host;
    check("extension service worker loaded", Boolean(extensionId), extensionId);

    // ---- 2. 知网 meta 路径提取 ----
    const cnkiPage = watchPage(await context.newPage());
    await cnkiPage.goto(CNKI_URL, { waitUntil: "load" });
    await cnkiPage.bringToFront();
    const cnkiTabId = await activeTabId(serviceWorker);
    check("cnki fixture tab visible to extension", Boolean(cnkiTabId), String(cnkiTabId));

    const cnkiResult = await sendExtract(serviceWorker, cnkiTabId);
    check("cnki extract ok", cnkiResult?.ok === true, JSON.stringify(cnkiResult?.error));
    const cnkiItem = cnkiResult?.item || {};
    check(
      "cnki title from meta",
      cnkiItem.title === "检索增强生成在软件工程中的应用综述（扩展冒烟）",
      cnkiItem.title,
    );
    check("cnki doi normalized", cnkiItem.doi === "10.13328/j.cnki.jos.009999", cnkiItem.doi);
    check("cnki authors count", (cnkiItem.author || []).length === 2, JSON.stringify(cnkiItem.author));
    check(
      "cnki abstract captured",
      typeof cnkiItem.abstract === "string" &&
        cnkiItem.abstract.includes("扩展冒烟摘要") &&
        cnkiItem.abstract.length <= 4000,
      cnkiItem.abstract?.slice(0, 30),
    );
    check("cnki journal", cnkiItem.container_title === "软件学报", cnkiItem.container_title);
    check("cnki year", cnkiItem.issued_year === 2026, cnkiItem.issued_year);
    check(
      "cnki source flags",
      cnkiResult?.source?.has_doi === true &&
        cnkiResult?.source?.has_abstract === true &&
        cnkiResult?.source?.abstract_only === true,
      JSON.stringify(cnkiResult?.source),
    );
    check(
      "cnki meta page has no extracted body",
      cnkiResult?.source?.extracted_text === undefined,
      JSON.stringify(cnkiResult?.source?.extracted_text),
    );

    // ---- 3. 维普 DOM 兜底路径提取 ----
    const cqvipPage = watchPage(await context.newPage());
    await cqvipPage.goto(CQVIP_URL, { waitUntil: "load" });
    await cqvipPage.bringToFront();
    const cqvipTabId = await activeTabId(serviceWorker);
    check("cqvip fixture tab visible", Boolean(cqvipTabId), String(cqvipTabId));
    const cqvipResult = await sendExtract(serviceWorker, cqvipTabId);
    check("cqvip extract ok", cqvipResult?.ok === true, JSON.stringify(cqvipResult?.error));
    const cqvipItem = cqvipResult?.item || {};
    check(
      "cqvip title from dom",
      cqvipItem.title === "基于大模型的代码缺陷修复方法研究",
      cqvipItem.title,
    );
    check("cqvip authors from dom", (cqvipItem.author || []).length === 2, JSON.stringify(cqvipItem.author));
    check("cqvip doi from dom", cqvipItem.doi === "10.3778/j.issn.1002-8331.2509-0001", cqvipItem.doi);
    check(
      "cqvip abstract from dom",
      typeof cqvipItem.abstract === "string" && cqvipItem.abstract.includes("维普 DOM 兜底摘要"),
      cqvipItem.abstract?.slice(0, 20),
    );

    // ---- 4. 开放 HTML 正文页：无 citation_* 也能提题录 + 提取正文 ----
    const openPage = watchPage(await context.newPage());
    await openPage.goto(OPEN_ACCESS_URL, { waitUntil: "load" });
    await openPage.bringToFront();
    const openTabId = await activeTabId(serviceWorker);
    check("open access fixture tab visible", Boolean(openTabId), String(openTabId));
    const openResult = await sendExtract(serviceWorker, openTabId);
    check("open access extract ok", openResult?.ok === true, JSON.stringify(openResult?.error));
    const openItem = openResult?.item || {};
    check("open access title from h1", openItem.title === OPEN_ACCESS_TITLE, openItem.title);
    check("open access url kept", openItem.url === OPEN_ACCESS_URL, openItem.url);
    const openText = openResult?.source?.extracted_text;
    check(
      "open access body extracted",
      typeof openText === "string" &&
        openText.includes("正文标记BETA") &&
        openText.length <= 20000 &&
        openText.length >= 200,
      typeof openText === "string" ? `${openText.length} chars` : String(openText),
    );
    check(
      "open access abstract_only false",
      openResult?.source?.abstract_only === false,
      JSON.stringify(openResult?.source?.abstract_only),
    );

    // ---- 5. popup 页面与控件 ----
    const extPage = watchPage(await context.newPage());
    await extPage.goto(`chrome-extension://${extensionId}/popup.html`);
    check(
      "popup page renders controls",
      (await extPage.locator("#extract").count()) === 1 &&
        (await extPage.locator("#save").count()) === 1 &&
        (await extPage.locator("#api-base").count()) === 1 &&
        (await extPage.locator("#attach-pdf").count()) === 1 &&
        (await extPage.locator("#badge-fulltext").count()) === 1,
    );

    // ---- 6. background.js 保存路径：X-API-Key + 幂等响应（不带附件，旧行为） ----
    const saveResult = await extPage.evaluate(
      async ({ item, base }) =>
        chrome.runtime.sendMessage({
          type: "VERIPAPER_SAVE",
          item,
          apiBase: base,
          apiKey: "maa_smoke_test",
        }),
      { item: cnkiItem, base: apiBase },
    );
    check("background save ok", saveResult?.ok === true, JSON.stringify(saveResult));
    check("background save status 201", saveResult?.status === 201, String(saveResult?.status));
    check(
      "background save created count",
      saveResult?.body?.created === 1,
      JSON.stringify(saveResult?.body),
    );
    check(
      "background save without attachment uploads no document",
      saveResult?.document === null,
      JSON.stringify(saveResult?.document),
    );
    check(
      "mock server received X-API-Key",
      capturesOf(captured, LIBRARY_PATH).length === 1 &&
        capturesOf(captured, LIBRARY_PATH)[0].headers["x-api-key"] === "maa_smoke_test",
      JSON.stringify(capturesOf(captured, LIBRARY_PATH)[0]?.headers?.["x-api-key"]),
    );
    const posted = capturesOf(captured, LIBRARY_PATH)[0]?.body?.items?.[0] || {};
    check(
      "posted payload keeps abstract and doi",
      posted.title === cnkiItem.title &&
        posted.doi === cnkiItem.doi &&
        typeof posted.abstract === "string" &&
        posted.abstract.includes("扩展冒烟摘要"),
      JSON.stringify({ title: posted.title, doi: posted.doi, abstract: Boolean(posted.abstract) }),
    );
    check(
      "mock server rejected path not used",
      captured.every((entry) => entry.headers["x-api-key"] === "maa_smoke_test"),
    );

    // ---- 7. 失败路径：无 API Key 时 mock 返回 401，扩展应回传 status 401 ----
    const unauthorized = await extPage.evaluate(
      async ({ item, base }) =>
        chrome.runtime.sendMessage({
          type: "VERIPAPER_SAVE",
          item,
          apiBase: base,
          apiKey: "maa_wrong_key",
        }),
      { item: cnkiItem, base: apiBase },
    );
    check(
      "unauthorized save surfaces 401",
      unauthorized?.ok === false && unauthorized?.status === 401,
      JSON.stringify(unauthorized),
    );

    // ---- 8. background.js 附带 PDF：题录成功后 multipart 上传并带 ref_id ----
    const documentCapturesBefore = capturesOf(captured, DOCUMENTS_PATH).length;
    const attachSave = await extPage.evaluate(
      async ({ item, base, attachment }) =>
        chrome.runtime.sendMessage({
          type: "VERIPAPER_SAVE",
          item,
          apiBase: base,
          apiKey: "maa_smoke_test",
          attachment,
        }),
      {
        item: cnkiItem,
        base: apiBase,
        attachment: {
          name: "paper.pdf",
          type: "application/pdf",
          dataUrl: `data:application/pdf;base64,${FAKE_PDF.toString("base64")}`,
        },
      },
    );
    check(
      "background save with attachment ok",
      attachSave?.ok === true && attachSave?.document?.ok === true,
      JSON.stringify({ ok: attachSave?.ok, document: attachSave?.document }),
    );
    check(
      "background save attachment refId",
      attachSave?.refId === cnkiItem.id,
      String(attachSave?.refId),
    );
    check(
      "background save attachment response",
      attachSave?.document?.status === 201 &&
        attachSave?.document?.body?.index_status === "ready",
      JSON.stringify(attachSave?.document?.body),
    );
    const backgroundDoc =
      capturesOf(captured, DOCUMENTS_PATH)[documentCapturesBefore] || {};
    check(
      "document multipart includes ref_id and file",
      typeof backgroundDoc.raw === "string" &&
        backgroundDoc.raw.includes('name="ref_id"') &&
        backgroundDoc.raw.includes('name="file"; filename="paper.pdf"'),
      backgroundDoc.raw?.slice(0, 260),
    );
    check(
      "document multipart ref_id matches item id",
      typeof backgroundDoc.raw === "string" && backgroundDoc.raw.includes(cnkiItem.id),
      cnkiItem.id,
    );
    check(
      "document multipart carries title and pdf mime",
      typeof backgroundDoc.raw === "string" &&
        backgroundDoc.raw.includes('name="title"') &&
        backgroundDoc.raw.includes("application/pdf"),
    );
    check(
      "document request keeps X-API-Key",
      backgroundDoc.headers?.["x-api-key"] === "maa_smoke_test",
      JSON.stringify(backgroundDoc.headers?.["x-api-key"]),
    );
    check(
      "document request lets browser build multipart boundary",
      typeof backgroundDoc.headers?.["content-type"] === "string" &&
        backgroundDoc.headers["content-type"].startsWith("multipart/form-data; boundary="),
      backgroundDoc.headers?.["content-type"],
    );

    // ---- 9. popup：选择 PDF 后一次保存（题录 + 全文合并展示） ----
    await extPage.evaluate(
      async ({ base, key }) => {
        await chrome.storage.local.set({
          veripaper_clipper_settings: { apiBase: base, apiKey: key },
        });
      },
      { base: apiBase, key: "maa_smoke_test" },
    );
    await extPage.evaluate(async () => {
      await loadSettings();
    });
    const seeded = await extPage.evaluate((item) => {
      window.current = item;
      document.getElementById("preview").classList.add("visible");
      document.getElementById("preview-title").textContent = item.title || "（无题名）";
      document.getElementById("save").disabled = false;
      return Boolean(window.current);
    }, cnkiItem);
    check("popup state seeded from extract result", seeded === true);

    const libraryBeforeUi = capturesOf(captured, LIBRARY_PATH).length;
    const documentsBeforeUi = capturesOf(captured, DOCUMENTS_PATH).length;
    await extPage.bringToFront();
    await extPage.setInputFiles("#attach-pdf", {
      name: "paper.pdf",
      mimeType: "application/pdf",
      buffer: FAKE_PDF,
    });
    const attachInfo = await extPage.locator("#attach-info").textContent();
    check(
      "popup shows selected pdf",
      typeof attachInfo === "string" && attachInfo.includes("paper.pdf"),
      attachInfo,
    );

    await extPage.click("#save");
    await extPage.waitForFunction(
      () => /全文已关联/.test(document.getElementById("status").textContent || ""),
      null,
      { timeout: 20000 },
    );
    const statusAfterAttach = await extPage.locator("#status").textContent();
    check(
      "popup merged result shows record and fulltext",
      /已保存：新增题录 1 条/.test(statusAfterAttach || "") &&
        /全文已关联（doi:10\.13328\/j\.cnki\.jos\.009999）/.test(statusAfterAttach || ""),
      statusAfterAttach,
    );
    check(
      "popup save posted library once",
      capturesOf(captured, LIBRARY_PATH).length === libraryBeforeUi + 1,
      String(capturesOf(captured, LIBRARY_PATH).length - libraryBeforeUi),
    );
    check(
      "popup save posted document once",
      capturesOf(captured, DOCUMENTS_PATH).length === documentsBeforeUi + 1,
      String(capturesOf(captured, DOCUMENTS_PATH).length - documentsBeforeUi),
    );
    const uiDoc = capturesOf(captured, DOCUMENTS_PATH)[documentsBeforeUi] || {};
    check(
      "popup document multipart has ref_id + pdf",
      typeof uiDoc.raw === "string" &&
        uiDoc.raw.includes('name="ref_id"') &&
        uiDoc.raw.includes(cnkiItem.id) &&
        uiDoc.raw.includes('name="file"; filename="paper.pdf"'),
      cnkiItem.id,
    );

    // ---- 10. popup：不选 PDF 时行为与旧版一致（只保存题录） ----
    await extPage.setInputFiles("#attach-pdf", []);
    const documentsBeforeNoPdf = capturesOf(captured, DOCUMENTS_PATH).length;
    const libraryBeforeNoPdf = capturesOf(captured, LIBRARY_PATH).length;
    await extPage.click("#save");
    await extPage.waitForFunction(
      () => /^已保存：新增 \d+ 条/.test(document.getElementById("status").textContent || ""),
      null,
      { timeout: 20000 },
    );
    const statusNoPdf = await extPage.locator("#status").textContent();
    check(
      "no pdf keeps legacy saved message",
      /已保存：新增 1 条，已存在 0 条/.test(statusNoPdf || "") &&
        !/全文已关联/.test(statusNoPdf || ""),
      statusNoPdf,
    );
    check(
      "no pdf saves only the record",
      capturesOf(captured, LIBRARY_PATH).length === libraryBeforeNoPdf + 1 &&
        capturesOf(captured, DOCUMENTS_PATH).length === documentsBeforeNoPdf,
      JSON.stringify({
        library: capturesOf(captured, LIBRARY_PATH).length - libraryBeforeNoPdf,
        documents: capturesOf(captured, DOCUMENTS_PATH).length - documentsBeforeNoPdf,
      }),
    );

    // ---- 11. popup：选 .caj 时提示先转 PDF，且不把 .caj 当附件保存 ----
    await extPage.setInputFiles("#attach-pdf", {
      name: "论文.caj",
      mimeType: "application/octet-stream",
      buffer: Buffer.from("CAJ-FIXTURE", "utf8"),
    });
    await extPage.waitForFunction(
      () => /\.caj/.test(document.getElementById("status").textContent || ""),
      null,
      { timeout: 5000 },
    );
    const statusCaj = await extPage.locator("#status").textContent();
    check(
      "caj selection prompts convert to pdf",
      /\.caj/.test(statusCaj || "") && /转成 PDF|转 PDF/.test(statusCaj || ""),
      statusCaj,
    );
    await extPage.setInputFiles("#attach-pdf", []);

    // 关闭浏览器前核对页面异常
    check("no page errors", pageErrors.length === 0, JSON.stringify(pageErrors));
  } finally {
    if (context) {
      await context.close();
    }
    server.close();
    rmSync(userDataDir, { recursive: true, force: true });
  }

  if (failures.length > 0) {
    console.log(`RESULT=FAIL (${failures.length} failed)`);
    process.exitCode = 1;
    return;
  }
  console.log("RESULT=PASS");
}

main().catch((error) => {
  console.error("RESULT=ERROR", error);
  process.exitCode = 1;
});
