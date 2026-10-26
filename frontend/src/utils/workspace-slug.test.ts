import { describe, expect, it } from "vitest";

import { deriveSlug, deriveSlugOrFallback, fallbackSlug, SLUG_MAX_LENGTH } from "@/utils/workspace-slug";

/**
 * 团队标识推导（2026-10-26 团队页重设计）。
 *
 * ## 为什么这些用例重要
 *
 * 缺陷现场证据：`@input` 与 `v-model` 的顺序让派生读到旧值 ——
 * `fill()` 读到空串（创建直接失败）、逐字输入少最后一个字符。
 * 抽出纯函数后，边界行为可以脱离事件时序直接验证。
 */
describe("deriveSlug", () => {
  it("英文名转小写连字符", () => {
    expect(deriveSlug("Zhang Lab 2026")).toBe("zhang-lab-2026");
  });

  it("已带连字符的名字保持单连字符（不产生 --）", () => {
    expect(deriveSlug("zhang  --  lab")).toBe("zhang-lab");
  });

  it("去首尾连字符", () => {
    expect(deriveSlug("  -Lab-  ")).toBe("lab");
  });

  it("中文名推导为空（由调用方兜底）", () => {
    expect(deriveSlug("巡检课题组")).toBe("");
  });

  it("中英混排保留英文部分", () => {
    expect(deriveSlug("张三 Lab")).toBe("lab");
  });

  it("全角符号被折叠", () => {
    expect(deriveSlug("Lab（2026）")).toBe("lab-2026");
  });

  it("超长名截断到 64 且不带尾部连字符", () => {
    const slug = deriveSlug("a".repeat(80));
    expect(slug).toHaveLength(SLUG_MAX_LENGTH);
    expect(slug.endsWith("-")).toBe(false);
  });

  it("截断后如果正好落在连字符上要去掉", () => {
    // 64 个字符处是 '-' 的情况：63 个 a + '-' + 更多
    const slug = deriveSlug(`${"a".repeat(SLUG_MAX_LENGTH - 1)}-b`);
    expect(slug.endsWith("-")).toBe(false);
    expect(slug.length).toBeLessThanOrEqual(SLUG_MAX_LENGTH);
  });

  it("空与空白安全", () => {
    expect(deriveSlug("")).toBe("");
    expect(deriveSlug("   ")).toBe("");
    expect(deriveSlug(null as unknown as string)).toBe("");
  });
});

describe("fallbackSlug", () => {
  it("形态合法且带 team- 前缀", () => {
    const slug = fallbackSlug();
    expect(slug).toMatch(/^team-[a-z0-9]{6}$/);
  });

  it("多次调用不总是相同（避免同一秒多人创建撞 slug）", () => {
    const values = new Set(Array.from({ length: 20 }, () => fallbackSlug()));
    expect(values.size).toBeGreaterThan(1);
  });
});

describe("deriveSlugOrFallback", () => {
  it("能推导时用推导值（不兜底）", () => {
    expect(deriveSlugOrFallback("Zhang Lab")).toBe("zhang-lab");
  });

  it("推导为空时兜底（保证「只填名字也能创建」）", () => {
    const slug = deriveSlugOrFallback("巡检课题组");
    expect(slug).not.toBe("");
    expect(slug).toMatch(/^[a-z0-9-]+$/);
    // 必须含字母/数字：纯中文折叠出来是 `"----"`，正则合法但**不可用**
    expect(slug).toMatch(/[a-z0-9]/);
  });
});
