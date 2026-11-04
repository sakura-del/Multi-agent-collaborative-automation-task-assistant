#!/usr/bin/env node
/**
 * 构建前端并部署到 Cloudflare Pages（零依赖：只用 Node 内置能力）。
 *
 * ## 为什么不用 wrangler
 *
 * 这台机器上**没有 npm/npx**（只有 node.exe），装不了 wrangler。
 * 所以直接用 Cloudflare Pages 的 **Direct Upload REST API**：
 * 先算每个文件的 SHA-256 作为表单字段名，再连同 `manifest` 一起 multipart 提交。
 *
 * ## 为什么环境变量要在构建时注入
 *
 * `VITE_API_BASE_URL` / `VITE_WS_BASE_URL` 会被 Vite **内联进 JS**，
 * 换域名必须重新构建，运行期改配置无效。
 *
 * ## 用法
 *
 *   $env:API_BASE_URL="https://api.example.com"
 *   $env:CF_API_TOKEN="..."        # 从 600 文件读入，别贴在命令行
 *   $env:CF_ACCOUNT_ID="..."
 *   $env:PAGES_PROJECT="multi-agent-assistant"
 *   node scripts/deploy-pages.mjs
 *
 * 可选：
 *   WS_BASE_URL=wss://api.example.com   不填则由 API_BASE_URL 推导
 *   --dry-run                            只构建、不部署
 */

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const FRONTEND = join(ROOT, "frontend");
const DIST = join(FRONTEND, "dist");
const DRY_RUN = process.argv.includes("--dry-run");

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

/** 与 src/config/endpoint.ts 同规则：允许站点根或完整前缀 */
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
    if (statSync(full).isDirectory()) {
      out.push(...walkFiles(full));
    } else {
      out.push(full);
    }
  }
  return out;
}

function sha256(content) {
  return createHash("sha256").update(content).digest("hex");
}

async function api(token, path, init = {}) {
  const response = await fetch(`https://api.cloudflare.com/client/v4${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, ...(init.headers ?? {}) },
  });
  const text = await response.text();
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    body = { raw: text.slice(0, 500) };
  }
  return { status: response.status, body };
}

const apiBase = normalizeApiBase(requireEnv("API_BASE_URL"));
const wsBase = (process.env.WS_BASE_URL ?? "").trim() || deriveWsBase(apiBase);
if (!wsBase) fail("WS_BASE_URL 无法从 API_BASE_URL 推导（必须是 http(s) 地址）");

console.log(`▶ API base : ${apiBase}`);
console.log(`▶ WS base  : ${wsBase}`);

if (!existsSync(join(FRONTEND, "node_modules"))) {
  fail("frontend/node_modules 不存在：需要先有依赖才能构建");
}

console.log("▶ 类型检查（vue-tsc -b）…");
runNode([join(FRONTEND, "node_modules", "vue-tsc", "bin", "vue-tsc.js"), "-b"], {
  cwd: FRONTEND,
});

console.log("▶ 构建（vite build）…");
runNode([join(FRONTEND, "node_modules", "vite", "bin", "vite.js"), "build"], {
  cwd: FRONTEND,
  env: { VITE_API_BASE_URL: apiBase, VITE_WS_BASE_URL: wsBase, CI: "1" },
});

const indexFile = join(DIST, "index.html");
const redirectsFile = join(DIST, "_redirects");
if (!existsSync(indexFile)) fail("dist/index.html 不存在：构建产物异常");
if (!existsSync(redirectsFile)) fail("dist/_redirects 缺失：SPA 深链会 404");

if (DRY_RUN) {
  console.log("✔ 构建完成（--dry-run：跳过部署）");
  process.exit(0);
}

const token = requireEnv("CF_API_TOKEN");
const accountId = requireEnv("CF_ACCOUNT_ID");
const project = requireEnv("PAGES_PROJECT");

// 1) 项目不存在就创建（首次部署）
const existing = await api(token, `/accounts/${accountId}/pages/projects/${project}`);
if (existing.status === 404) {
  console.log(`▶ 创建 Pages 项目 ${project} …`);
  const created = await api(token, `/accounts/${accountId}/pages/projects`, {
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

// 2) 组装 manifest + multipart（字段名 = 文件 SHA-256）
const files = walkFiles(DIST);
const manifest = {};
const form = new FormData();
const uploaded = new Set();

for (const file of files) {
  const urlPath = "/" + relative(DIST, file).split(sep).join("/");
  const content = readFileSync(file);
  const hash = sha256(content);
  manifest[urlPath] = hash;
  if (!uploaded.has(hash)) {
    const type = CONTENT_TYPES[extname(file).toLowerCase()] ?? "application/octet-stream";
    form.append(hash, new Blob([content], { type }), hash);
    uploaded.add(hash);
  }
}

form.append(
  "manifest",
  new Blob([JSON.stringify(manifest)], { type: "application/json" }),
  "manifest.json"
);
form.append("branch", "main");

console.log(`▶ 上传 ${files.length} 个文件（去重后 ${uploaded.size} 个资源）…`);
const deployment = await api(
  token,
  `/accounts/${accountId}/pages/projects/${project}/deployments`,
  { method: "POST", body: form }
);

if (!deployment.body?.success) {
  fail(
    `部署失败（HTTP ${deployment.status}）：${JSON.stringify(deployment.body).slice(0, 600)}`
  );
}

const result = deployment.body.result ?? {};
console.log(`✔ 部署完成：${result.url ?? "(未返回 URL)"}`);
if (result.aliases?.length) {
  console.log(`  别名：${result.aliases.join(", ")}`);
}
console.log("⚠ 检查后端 CORS__ALLOW_ORIGIN_REGEX 是否覆盖该域名，否则页面会跨域失败");
