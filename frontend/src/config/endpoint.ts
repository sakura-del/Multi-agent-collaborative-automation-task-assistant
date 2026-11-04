/**
 * 后端端点解析：把「请求往哪发」收敛到一处，供 axios 与 WebSocket 共用。
 *
 * 为什么需要它：前端原本写死相对路径（`/api/v1`）与 `window.location.host`，
 * 假设前后端**同源**（nginx 反代）。部署到 Cloudflare Pages 后前端在
 * `https://<project>.pages.dev`，API 在另一域名（走 Cloudflare Tunnel），
 * 同源假设失效，且 HTTPS 页面不能调 HTTP 接口。
 *
 * 设计约束（务必保持）：
 * - **不配环境变量时行为与改造前完全一致**：相对 `/api/v1` + 同源 WS；
 * - 配了 `VITE_API_BASE_URL` 时允许写站点根（自动补 `/api/v1`），
 *   也允许写完整前缀（不能重复拼接）；
 * - WS 优先取 `VITE_WS_BASE_URL`，否则由 API 地址推导（https→wss / http→ws）。
 *
 * ⚠️ VITE_ 变量是**构建期注入**：换域名必须重新构建，不能只在运行期改配置。
 */

/** 后端 API 版本前缀（与后端 `app/api/v1/router.py` 一致） */
export const API_PREFIX = "/api/v1";

type EnvLike = Record<string, string | undefined>;

function readEnv(env: EnvLike, key: string): string {
  const raw = env[key];
  return typeof raw === "string" ? raw.trim() : "";
}

/** 去掉尾部所有斜杠：`https://a.com/` → `https://a.com` */
function stripTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

/**
 * axios 的 `baseURL`。
 *
 * - 未配置 → `/api/v1`（相对路径，nginx 同源部署零回归）
 * - `https://api.example.com` → `https://api.example.com/api/v1`
 * - `https://api.example.com/api/v1/` → 不重复拼接
 */
export function resolveApiBase(env: EnvLike = import.meta.env as EnvLike): string {
  const raw = readEnv(env, "VITE_API_BASE_URL");
  if (!raw) {
    return API_PREFIX;
  }
  const base = stripTrailingSlash(raw);
  return base.endsWith(API_PREFIX) ? base : `${base}${API_PREFIX}`;
}

/**
 * WebSocket 的 origin（不含 `/api/v1` 前缀）。
 *
 * 返回空串表示「同源」——调用方用 `window.location.host` 与当前协议自行拼接，
 * 与改造前完全一致。
 */
export function resolveWsBase(env: EnvLike = import.meta.env as EnvLike): string {
  const explicit = readEnv(env, "VITE_WS_BASE_URL");
  if (explicit) {
    return stripTrailingSlash(explicit);
  }

  const apiRaw = readEnv(env, "VITE_API_BASE_URL");
  if (!apiRaw) {
    return "";
  }

  const apiBase = resolveApiBase(env);
  const origin = stripTrailingSlash(apiBase.slice(0, apiBase.length - API_PREFIX.length));
  if (origin.startsWith("https://")) {
    return `wss://${origin.slice("https://".length)}`;
  }
  if (origin.startsWith("http://")) {
    return `ws://${origin.slice("http://".length)}`;
  }
  // 既不是 http(s) 也没显式配置 WS：退回同源，避免拼出非法 URL
  return "";
}
