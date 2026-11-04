import { describe, expect, it } from "vitest";

import { API_PREFIX, resolveApiBase, resolveWsBase } from "@/config/endpoint";

/**
 * 端点配置契约（Cloudflare Pages 部署前置）。
 *
 * 三条铁律：
 * 1. **不配环境变量时行为必须与现在完全一致**（相对 `/api/v1` + 同源 WS）——
 *    nginx 同源部署与既有 E2E 不能被这次改造影响；
 * 2. 配了站点根地址时自动补 `/api/v1`，但用户显式写了（带不带尾斜杠）都不能重复拼；
 * 3. WS origin 优先取显式配置，否则由 API 地址推导（https→wss / http→ws）。
 */

describe("resolveApiBase", () => {
  it("未配置时返回相对前缀（同源部署零回归）", () => {
    expect(resolveApiBase({})).toBe("/api/v1");
    expect(API_PREFIX).toBe("/api/v1");
  });

  it("只给站点根地址时自动补前缀", () => {
    expect(resolveApiBase({ VITE_API_BASE_URL: "https://api.example.com" })).toBe(
      "https://api.example.com/api/v1"
    );
  });

  it("已含前缀（含尾斜杠）不重复拼接", () => {
    expect(resolveApiBase({ VITE_API_BASE_URL: "https://api.example.com/api/v1" })).toBe(
      "https://api.example.com/api/v1"
    );
    expect(resolveApiBase({ VITE_API_BASE_URL: "https://api.example.com/api/v1/" })).toBe(
      "https://api.example.com/api/v1"
    );
  });

  it("纯空白按未配置处理", () => {
    expect(resolveApiBase({ VITE_API_BASE_URL: "   " })).toBe("/api/v1");
  });

  it("保留端口与子路径", () => {
    expect(resolveApiBase({ VITE_API_BASE_URL: "http://127.0.0.1:48080" })).toBe(
      "http://127.0.0.1:48080/api/v1"
    );
  });
});

describe("resolveWsBase", () => {
  it("未配置时返回空串，调用方回退 window.location.host", () => {
    expect(resolveWsBase({})).toBe("");
    expect(resolveWsBase({ VITE_API_BASE_URL: "   " })).toBe("");
  });

  it("由 https 推导 wss", () => {
    expect(resolveWsBase({ VITE_API_BASE_URL: "https://api.example.com" })).toBe(
      "wss://api.example.com"
    );
  });

  it("由 http 推导 ws 并保留端口", () => {
    expect(resolveWsBase({ VITE_API_BASE_URL: "http://127.0.0.1:48080" })).toBe(
      "ws://127.0.0.1:48080"
    );
  });

  it("显式 VITE_WS_BASE_URL 优先，并去掉尾斜杠", () => {
    expect(
      resolveWsBase({
        VITE_API_BASE_URL: "https://api.example.com",
        VITE_WS_BASE_URL: "wss://ws.example.com/",
      })
    ).toBe("wss://ws.example.com");
  });
});
