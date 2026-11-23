/**
 * 边缘代理：把 veripaper.me/api/* 转发到后端（Issue #29）。
 *
 * ## 为什么不在 Pages 的 _redirects 里做
 *
 * Cloudflare Pages 的 `_redirects` **只能代理站内相对路径**（官方原文：
 * "Proxying will only support relative URLs on your site. You cannot proxy
 * external domains."）。写 `/api/* https://api.veripaper.me/... 200` 不会报错，
 * 但也不会生效——请求继续落到 `/* /index.html 200`，于是 `/api/health` 返回
 * 200 + 首页 HTML，后端挂了探针照样绿（正是 #29 的假阳性）。
 *
 * ## 这里做什么
 *
 * 只接管 zone 路由 `veripaper.me/api/*`：原样透传方法/头/请求体，把后端响应
 * **连同状态码**返回。其他路径完全不经过本 Worker（路由限定），static 资源
 * 仍由 Pages 提供。
 *
 * 部署：`node ops/cloudflare-api-proxy.mjs`（幂等，可重复执行）。
 */

const BACKEND = "https://api.veripaper.me";

/**
 * 回源上限。这条路由只服务探针/工具调用（前端直连 api 子域），
 * 而「后端挂了」时实测回源会挂住十几秒才断开——探针需要**确定的**失败时间。
 * 超时统一回 504 JSON，监控按状态码判定即可。
 */
const UPSTREAM_TIMEOUT_MS = 8000;

export default {
  async fetch(request) {
    const incoming = new URL(request.url);
    const target = new URL(BACKEND);
    target.pathname = incoming.pathname;
    target.search = incoming.search;

    // 用 Request 作为 init：方法/头/请求体原样带过去；Host 由 fetch 按 URL 生成，
    // 手动设 Host 在 Workers 里属于不可修改头，会直接抛错。
    let response;
    try {
      response = await fetch(new Request(target.toString(), request), {
        signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      });
    } catch (error) {
      // 后端/回源不可达时不能把 Worker 的异常页（1101）甩给探针：
      // 明确回 502 + JSON，监控按状态码判定即可，也不会出现「连接被重置」
      const timedOut = error?.name === "TimeoutError" || error?.name === "AbortError";
      return new Response(
        JSON.stringify({
          status: timedOut ? "timeout" : "unavailable",
          error: timedOut ? "upstream timeout" : "upstream unreachable",
        }),
        { status: timedOut ? 504 : 502, headers: { "content-type": "application/json" } },
      );
    }
    const headers = new Headers(response.headers);
    // 边缘不缓存：健康检查必须反映「此刻」的后端状态
    headers.set("Cache-Control", "no-store");
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  },
};
