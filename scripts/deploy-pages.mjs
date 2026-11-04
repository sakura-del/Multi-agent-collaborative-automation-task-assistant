#!/usr/bin/env node
/**
 * 构建前端并部署到 Cloudflare Pages（零依赖：只用 Node 内置能力）。
 *
 * ## 为什么不用 wrangler
 *
 * 这台机器上**没有 npm/npx**（只有 node.exe），装不了 wrangler。
 * 所以按 wrangler 的**两阶段协议**手写：
 *
 *   1) GET  /accounts/{acc}/pages/projects/{proj}/upload-token   → 资产上传 JWT
 *   2) POST /pages/assets/check-missing  {hashes:[...]}          → 哪些内容还没传
 *   3) POST /pages/assets/upload         [{key,value(base64),...}]→ 传内容
 *   4) POST /pages/assets/upsert-hashes  {hashes:[...]}           → 登记
 *   5) POST /accounts/{acc}/pages/projects/{proj}/deployments
 *        multipart: manifest=<JSON 字符串> + branch=main         → 真正部署
 *
 * ⚠️ 踩过的坑（2026-11-04 实测）：
 * - 把文件内容直接塞进 deployments 的 multipart（单阶段写法）会「部署成功但站点 404/500」；
 * - `manifest` 必须是**普通表单字段**（JSON 字符串），当文件部件上传会报
 *   `A "manifest" field was expected`；
 * - manifest 的 key 必须带前导 `/`（`/index.html`），value 是内容 SHA-256；
 * - 去掉前导 `/` 会变成整站 404。
 *
 * ## 为什么 VITE_ 变量要在构建时注入
 * 它们会被 Vite **内联进 JS**，换域名必须重新构建。
 *
 * ## 用法
 *   API_BASE_URL=https://api.example.com CF_API_TOKEN=... CF_ACCOUNT_ID=... \
 *   PAGES_PROJECT=my-app node scripts/deploy-pages.mjs [--dry-run] [--skip-build]
 */

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const FRONTEND = join(ROOT, "frontend");
const DIST = join(FRONTEND, "dist");
const API = "https://api.cloudflare.com/client/v4";

const DRY_RUN = process.argv.includes("--dry-run");
const SKIP_BUILD = process.argv.includes("--skip-build");

const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
  "": "text/plain; charset=utf-8",
};

function fail(message) {
  console.error(`✖ ${message}`);
  process.exit(1);
}

function requireEnv(name) {
  const value = (process.env[name] ?? "").trim();
  if (!value) fail(`缺少环境变量 ${name}`);
  return value;
}

function normalizeApiBase(raw) {
  const base = raw.replace(/\/+$/, "");
  return base.endsWith("/api/v1") ? base : `${base}/api/v1`;
}

function deriveWsBase(apiBase) {
  const origin = apiBase.replace(/\/api\/v1$/, "");
  if (origin.startsWith("https://")) return `wss://${origin.slice("https://".length)}`;
  if (origin.startsWith("http://")) return `ws://${origin.slice("http://".length)}`;
  return "";
}

function runNode(args, options = {}) {
  const result = spawnSync(process.execPath, args, {
    cwd: options.cwd ?? ROOT,
    stdio: "inherit",
    env: { ...process.env, ...(options.env ?? {}) },
  });
  if (result.status !== 0) {
    fail(`构建命令失败（exit ${result.status}）：node ${args.join(" ")}`);
  }
}

function walkFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walkFiles(full));
    else out.push(full);
  }
  return out;
}

function sha256(content) {
  return createHash("sha256").update(content).digest("hex");
}

async function cfa(path, { token, method = "GET", body, headers = {} } = {}) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...headers },
    body,
  });
  const text = await response.text();
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = { raw: text.slice(0, 400) };
  }
  return { status: response.status, body: parsed };
}

const apiBase = normalizeApiBase(requireEnv("API_BASE_URL"));
const wsBase = (process.env.WS_BASE_URL ?? "").trim() || deriveWsBase(apiBase);
if (!wsBase) fail("WS_BASE_URL 无法从 API_BASE_URL 推导（必须是 http(s) 地址）");

console.log(`▶ API base : ${apiBase}`);
console.log(`▶ WS base  : ${wsBase}`);

if (!existsSync(join(FRONTEND, "node_modules"))) {
  fail("frontend/node_modules 不存在：需要先有依赖才能构建");
}

if (SKIP_BUILD) {
  console.log("▶ --skip-build：复用现有 dist");
} else {
  console.log("▶ 类型检查（vue-tsc -b）…");
  runNode([join(FRONTEND, "node_modules", "vue-tsc", "bin", "vue-tsc.js"), "-b"], {
    cwd: FRONTEND,
  });

  console.log("▶ 构建（vite build）…");
  runNode([join(FRONTEND, "node_modules", "vite", "bin", "vite.js"), "build"], {
    cwd: FRONTEND,
    env: { VITE_API_BASE_URL: apiBase, VITE_WS_BASE_URL: wsBase, CI: "1" },
  });
}

const indexFile = join(DIST, "index.html");
const redirectsFile = join(DIST, "_redirects");
if (!existsSync(indexFile)) fail("dist/index.html 不存在：构建产物异常");
if (!existsSync(redirectsFile)) fail("dist/_redirects 缺失：SPA 深链会 404（public/_redirects 是否被删了？）");

if (DRY_RUN) {
  console.log("✔ 构建完成（--dry-run：跳过部署）");
  process.exit(0);
}

const token = requireEnv("CF_API_TOKEN");
const accountId = requireEnv("CF_ACCOUNT_ID");
const project = requireEnv("PAGES_PROJECT");

// 1) 项目不存在就创建
const existing = await cfa(`/accounts/${accountId}/pages/projects/${project}`, { token });
if (existing.status === 404) {
  console.log(`▶ 创建 Pages 项目 ${project} …`);
  const created = await cfa(`/accounts/${accountId}/pages/projects`, {
    token,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: project, production_branch: "main" }),
  });
  if (!created.body?.success) {
    fail(`创建项目失败：${JSON.stringify(created.body).slice(0, 400)}`);
  }
} else if (!existing.body?.success) {
  fail(`查询项目失败：${JSON.stringify(existing.body).slice(0, 400)}`);
}

// 2) 优先用官方 wrangler：手写 REST 直传实测会产出「部署成功但整站 500」的坏部署
const wranglerBin =
  process.env.WRANGLER_BIN ?? "C:/Users/Lenovo/cfdeploy/node_modules/wrangler/bin/wrangler.js";

if (existsSync(wranglerBin)) {
  console.log("▶ 用 wrangler 部署…");
  const result = spawnSync(
    process.execPath,
    [
      wranglerBin,
      "pages",
      "deploy",
      DIST,
      "--project-name",
      project,
      "--branch",
      "main",
      "--commit-dirty=true",
    ],
    {
      stdio: "inherit",
      env: {
        ...process.env,
        CLOUDFLARE_API_TOKEN: token,
        CLOUDFLARE_ACCOUNT_ID: accountId,
      },
    }
  );
  if (result.status !== 0) fail(`wrangler 部署失败（exit ${result.status}）`);
  console.log(`✔ 生产地址：https://${project}.pages.dev`);
  process.exit(0);
}

console.warn(
  "⚠ 未找到 wrangler（WRANGLER_BIN），回退到 REST 直传；该路径实测会产出 500 的空部署，仅供排查"
);

// 3) 逐个文件算 hash 与 base64（顺手收集 content-type）
const files = walkFiles(DIST).map((file) => {
  const rel = relative(DIST, file).split(sep).join("/");
  const content = readFileSync(file);
  return {
    rel,
    hash: sha256(content),
    size: content.length,
    base64: content.toString("base64"),
    contentType: CONTENT_TYPES[extname(file).toLowerCase()] ?? "application/octet-stream",
  };
});
const manifests = Object.fromEntries(files.map((f) => [`/${f.rel}`, f.hash]));
const hashes = [...new Set(files.map((f) => f.hash))];

// 3) 资产上传用项目级 JWT（拿不到就退回 API Token）
let assetsToken = token;
const uploadToken = await cfa(
  `/accounts/${accountId}/pages/projects/${project}/upload-token`,
  { token }
);
if (uploadToken.body?.result?.jwt) {
  assetsToken = String(uploadToken.body.result.jwt);
} else {
  console.log("   （未取到 upload-token，资产上传改用 API Token）");
}

const missingResp = await cfa("/pages/assets/check-missing", {
  token: assetsToken,
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ hashes }),
});
if (!missingResp.body?.success) {
  fail(`check-missing 失败：${JSON.stringify(missingResp.body).slice(0, 400)}`);
}
const missing = new Set(missingResp.body.result ?? []);
const toUpload = files.filter((f) => missing.has(f.hash));
console.log(`▶ 文件 ${files.length} 个（去重 ${hashes.length}），需上传 ${toUpload.length} 个`);

if (toUpload.length) {
  const payload = toUpload.map((f) => ({
    key: f.hash,
    value: f.base64,
    metadata: { contentType: f.contentType },
    base64: true,
  }));
  const uploadResp = await cfa("/pages/assets/upload", {
    token: assetsToken,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!uploadResp.body?.success) {
    fail(`assets/upload 失败：${JSON.stringify(uploadResp.body).slice(0, 400)}`);
  }
}

await cfa("/pages/assets/upsert-hashes", {
  token: assetsToken,
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ hashes }),
});

// 4) 兜底路径：REST 直传（仅排查用）
const form = new FormData();
form.append("manifest", JSON.stringify(manifests));
form.append("branch", "main");

console.log("▶ 触发部署…");
const deployment = await cfa(
  `/accounts/${accountId}/pages/projects/${project}/deployments`,
  { token, method: "POST", body: form }
);

if (!deployment.body?.success) {
  fail(
    `部署失败（HTTP ${deployment.status}）：${JSON.stringify(deployment.body).slice(0, 600)}`
  );
}

const result = deployment.body.result ?? {};
console.log(`✔ 部署完成：${result.url ?? "(未返回 URL)"}`);
if (result.aliases?.length) console.log(`  别名：${result.aliases.join(", ")}`);
console.log(`  生产地址通常是 https://${project}.pages.dev（DNS 生效可能需要几十秒）`);
console.log("⚠ 检查后端 CORS__ALLOW_ORIGIN_REGEX 是否覆盖该域名，否则页面会跨域失败");
