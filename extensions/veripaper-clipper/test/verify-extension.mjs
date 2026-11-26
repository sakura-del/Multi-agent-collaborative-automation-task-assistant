#!/usr/bin/env node
/**
 * VeriPaper Clipper 自动化冒烟（不依赖知网/维普账号）：
 *
 * 在真实 Chromium + 已加载扩展的环境里，用本地 fixture 页面模拟
 * 知网（meta 元数据路径）与维普（DOM 兜底路径），验证：
 *   1. Manifest V3 扩展能正常加载、Service Worker 启动；
 *   2. content.js 能从页面提取 CslItem（题名/作者/刊名/年份/DOI/摘要）；
 *   3. background.js 用 `X-API-Key` 头把题录 POST 到 /api/v1/reference-library；
 *   4. popup.html 能正常加载（无 JS 报错、关键控件存在）。
 *
 * 运行（需要 Playwright + Linux 无头环境用 Xvfb）：
 *   PLAYWRIGHT_ROOT=/home/ylin/vp-test/frontend \
 *   xvfb-run -a /tmp/node22/bin/node extensions/veripaper-clipper/test/verify-extension.mjs
 *
 * 只在测试机（10.0.10.2）运行；本机不跑测试。
 */

import { createRequire } from "node:module";
import { createServer } from "node:http";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(here, "..");
const playwrightRoot = process.env.PLAYWRIGHT_ROOT || process.cwd();
const requireFromPlaywrightRoot = createRequire(pathToFileURL(join(playwrightRoot, "package.json")));
const { chromium } = requireFromPlaywrightRoot("playwright");

const CNKI_URL = "https://kns.cnki.net/kcms2/article/abstract?v=ext-smoke";
const CQVIP_URL = "https://qikan.cqvip.com/Qikan/Article/Detail?id=ext-smoke";

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

/**
 * 取当前活动标签页 id。
 *
 * 扩展没有把知网/维普写进 host_permissions（真实使用靠 popup 的 activeTab
 * 临时授权），所以 chrome.tabs.query 对这两个站点拿不到 url/title；tab id
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
  // ---- 本地 mock：模拟 VeriPaper /api/v1/reference-library ----
  let mockPort = 0;
  const captured = [];
  const server = createServer((request, response) => {
    if (request.method !== "POST" || !request.url?.startsWith("/api/v1/reference-library")) {
      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ detail: "not found" }));
      return;
    }
    let body = "";
    request.on("data", (chunk) => {
      body += chunk;
    });
    request.on("end", () => {
      let parsed = null;
      try {
        parsed = JSON.parse(body);
      } catch {
        parsed = null;
      }
      captured.push({ headers: request.headers, body: parsed });
      if (request.headers["x-api-key"] !== "maa_smoke_test") {
        response.writeHead(401, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ detail: "invalid api key" }));
        return;
      }
      response.writeHead(201, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ created: 1, skipped: 0, merged: 0, rejected: [] }));
    });
  });
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  mockPort = server.address().port;
  const apiBase = `http://127.0.0.1:${mockPort}`;

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
    const cnkiPage = await context.newPage();
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

    // ---- 3. 维普 DOM 兜底路径提取 ----
    const cqvipPage = await context.newPage();
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

    // ---- 4. background.js 保存路径：X-API-Key + 幂等响应 ----
    const extPage = await context.newPage();
    await extPage.goto(`chrome-extension://${extensionId}/popup.html`);
    check(
      "popup page renders controls",
      (await extPage.locator("#extract").count()) === 1 &&
        (await extPage.locator("#save").count()) === 1 &&
        (await extPage.locator("#api-base").count()) === 1,
    );
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
      "mock server received X-API-Key",
      captured.length === 1 && captured[0].headers["x-api-key"] === "maa_smoke_test",
      JSON.stringify(captured[0]?.headers?.["x-api-key"]),
    );
    const posted = captured[0]?.body?.items?.[0] || {};
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

    // ---- 5. 失败路径：无 API Key 时 mock 返回 401，扩展应回传 status 401 ----
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

    // 关闭浏览器前收集页面异常
    const pageErrors = [];
    cnkiPage.on("pageerror", (error) => pageErrors.push(String(error)));
    cqvipPage.on("pageerror", (error) => pageErrors.push(String(error)));
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
