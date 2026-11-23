import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

// Cloudflare Pages 路由规则的顺序契约（Issue #29）。
//
// ## 为什么要测一个静态文件
//
// `_redirects` 是**首个匹配生效**，而且写错没有任何构建期报错：之前只有
// `/* /index.html 200`，`/api/health` 就被 SPA 兜底吞成 200 HTML，后端挂了
// 外部探针照样显示绿。这个错误只能靠线上 curl 发现——除非有人盯着。
// 把它变成一条断言，以后谁调整顺序都会先红。
const raw = readFileSync(resolve(process.cwd(), "public/_redirects"), "utf-8");
const rules = raw
  .split("\n")
  .map((line) => line.trim())
  .filter((line) => line.length > 0 && !line.startsWith("#"));

describe("Pages _redirects", () => {
  it("API 反代排在 SPA 兜底之前", () => {
    const apiIndex = rules.findIndex((rule) => rule.startsWith("/api/*"));
    const spaIndex = rules.findIndex((rule) => rule.startsWith("/* /index.html"));

    expect(apiIndex).toBeGreaterThanOrEqual(0);
    expect(spaIndex).toBeGreaterThanOrEqual(0);
    expect(apiIndex).toBeLessThan(spaIndex);
  });

  it("API 反代指向 api 子域并保留路径", () => {
    const rule = rules.find((line) => line.startsWith("/api/*"));
    expect(rule).toContain("https://api.veripaper.me/api/:splat");
    expect(rule?.endsWith(" 200")).toBe(true);
  });

  it("SPA 兜底仍是 200 重写（不改成 301/302，会破坏前端路由）", () => {
    const rule = rules.find((line) => line.startsWith("/* "));
    expect(rule).toBe("/* /index.html 200");
  });
});
