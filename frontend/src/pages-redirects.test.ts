import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

// Cloudflare Pages 路由规则的契约（Issue #29）。
//
// ## 为什么要测一个静态文件
//
// `_redirects` 是**首个匹配生效**，写错没有任何构建期报错。更坑的是
// 「跨域代理」：`/api/* https://api.veripaper.me/api/:splat 200` 语法合法、
// 部署成功、但**不生效**（Pages 只允许站内相对路径代理），请求静默落到
// `/* /index.html 200`，于是 `/api/health` 变成 200 HTML、后端挂了探针也绿。
//
// 所以这里锁两件事：SPA 兜底必须保留；**不允许**出现「看着像代理、其实无效」
// 的跨域规则——那会给人已经修好的错觉（#29 就是这么来的）。
const raw = readFileSync(resolve(process.cwd(), "public/_redirects"), "utf-8");
const rules = raw
  .split("\n")
  .map((line) => line.trim())
  .filter((line) => line.length > 0 && !line.startsWith("#"));

describe("Pages _redirects", () => {
  it("保留 SPA 兜底（200 重写，不能改成 301/302）", () => {
    expect(rules).toContain("/* /index.html 200");
  });

  it("不写跨域 /api 代理（Pages 不支持，会静默失效）", () => {
    const apiRules = rules.filter((line) => line.startsWith("/api/"));
    expect(apiRules).toEqual([]);
  });

  it("兜底规则排在最后（前面没有别的路径规则抢匹配）", () => {
    const spaIndex = rules.findIndex((line) => line.startsWith("/* "));
    expect(spaIndex).toBe(rules.length - 1);
  });
});
