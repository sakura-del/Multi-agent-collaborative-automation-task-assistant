#!/usr/bin/env node
/**
 * 部署边缘 API 代理（Issue #29）：`veripaper.me/api/*` → `api.veripaper.me`。
 *
 * ## 为什么需要这一步
 *
 * veripaper.me 是 Cloudflare Pages 托管的前端，Pages 的 `_redirects` 只能代理
 * **站内相对路径**，跨域代理写了也不生效；于是 `/api/*` 落到 SPA 兜底
 * （`/* /index.html 200`），后端挂了外部探针仍拿到 200 HTML。
 * 修法是在 zone 上加一条 Worker 路由，只接管 `/api/*`，其余路径不经过 Worker。
 *
 * ## 用法
 *
 * ```bash
 * CF_API_TOKEN=... CF_ACCOUNT_ID=... node ops/cloudflare-api-proxy.mjs
 * # 或：CF_SECRETS_FILE=C:/Users/Lenovo/cf-secrets.env node ops/cloudflare-api-proxy.mjs
 * ```
 *
 * 幂等：脚本已存在则覆盖（PUT），路由已存在则更新（PUT），否则新建（POST）。
 * 幂等很关键——这条路由是生产流量的关键路径，重跑不能产生重复路由。
 */

import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SCRIPT_NAME = "veripaper-api-proxy";
const ROUTE_PATTERN = "veripaper.me/api/*";
const ZONE_NAME = "veripaper.me";

function loadEnvFile(path) {
  const out = {};
  for (const line of readFileSync(path, "utf-8").split("\n")) {
    if (!line.includes("=") || line.trim().startsWith("#")) continue;
    const idx = line.indexOf("=");
    out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim().replace(/^["']|["']$/g, "");
  }
  return out;
}

const secretsFile = process.env.CF_SECRETS_FILE;
const fileEnv = secretsFile ? loadEnvFile(secretsFile) : {};
const token = process.env.CF_API_TOKEN || fileEnv.CF_API_TOKEN;
const accountId = process.env.CF_ACCOUNT_ID || fileEnv.CF_ACCOUNT_ID;

if (!token || !accountId) {
  console.error("缺 CF_API_TOKEN / CF_ACCOUNT_ID（可用 CF_SECRETS_FILE 指向密钥文件）");
  process.exit(1);
}

const API = "https://api.cloudflare.com/client/v4";

async function call(method, path, init = {}) {
  const response = await fetch(API + path, {
    ...init,
    method,
    headers: { Authorization: `Bearer ${token}`, ...(init.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!body.success) {
    const message = (body.errors || []).map((e) => `${e.code}: ${e.message}`).join("; ");
    throw new Error(`${method} ${path} → HTTP ${response.status} ${message}`);
  }
  return body.result;
}

async function main() {
  const zones = await call("GET", `/zones?name=${encodeURIComponent(ZONE_NAME)}`);
  const zone = zones?.[0];
  if (!zone) throw new Error(`找不到 zone ${ZONE_NAME}`);
  console.log(`zone: ${zone.name} (${zone.id})`);

  const code = readFileSync(join(HERE, "cf-api-proxy.worker.js"), "utf-8");
  const metadata = { main_module: SCRIPT_NAME, compatibility_date: "2026-11-01" };
  const form = new FormData();
  form.set("metadata", new Blob([JSON.stringify(metadata)], { type: "application/json" }));
  form.set(SCRIPT_NAME, new Blob([code], { type: "application/javascript+module" }), SCRIPT_NAME);

  await call("PUT", `/accounts/${accountId}/workers/scripts/${SCRIPT_NAME}`, { body: form });
  console.log(`worker 已上传：${SCRIPT_NAME}`);

  const routes = await call("GET", `/zones/${zone.id}/workers/routes`);
  const existing = (routes || []).find((r) => r.pattern === ROUTE_PATTERN);
  if (existing) {
    await call("PUT", `/zones/${zone.id}/workers/routes/${existing.id}`, {
      body: JSON.stringify({ pattern: ROUTE_PATTERN, script: SCRIPT_NAME }),
      headers: { "Content-Type": "application/json" },
    });
    console.log(`路由已更新：${ROUTE_PATTERN} → ${SCRIPT_NAME}`);
  } else {
    await call("POST", `/zones/${zone.id}/workers/routes`, {
      body: JSON.stringify({ pattern: ROUTE_PATTERN, script: SCRIPT_NAME }),
      headers: { "Content-Type": "application/json" },
    });
    console.log(`路由已创建：${ROUTE_PATTERN} → ${SCRIPT_NAME}`);
  }
}

main().catch((error) => {
  console.error("部署失败：", error.message);
  process.exit(1);
});
