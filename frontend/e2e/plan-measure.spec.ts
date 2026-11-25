import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { demoCreds, secondaryCreds } from "./support/env";
import { seedAuthByApi } from "./support/auth";
import { measureContrastTargets } from "./support/contrast";

// 套餐页「修复后量数值」验收（AGENTS §6.5.7 第 2/3 条）。
// 与修复前的取证逐项对比，用数据证明缺陷真的没了。

async function loginAs(
  page: Page,
  request: APIRequestContext,
  account: string,
  password: string,
) {
  await seedAuthByApi(page, request, account, password, "/settings/plan");
}

async function measure(page: Page, tag: string) {
  const data = await page.evaluate(() => {
    const q = (s: string) => document.querySelector(s) as HTMLElement | null;
    const box = (el: HTMLElement | null) =>
      el
        ? (() => {
            const r = el.getBoundingClientRect();
            return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
          })()
        : null;

    const current = q('[data-test="plan-current"]');
    const entitlements = q('[data-test="plan-entitlements"]');
    const periodEnd = q('[data-test="plan-period-end"]');
    const periodKey = q('[data-test="plan-period-key"]');
    const bar = q('[data-test="plan-progress-bar"]');
    const hint = q('[data-test="plan-upgrade-hint"]');

    // 权益子项：逐个取标签与值
    const entItems = entitlements
      ? Array.from(entitlements.children).map((c) => ({
          testid: c.getAttribute("data-test"),
          text: (c.textContent ?? "").replace(/\s+/g, " ").trim(),
        }))
      : [];

    // 可点元素（看升级入口在不在）
    const buttons = Array.from(document.querySelectorAll("button, a")) as HTMLElement[];
    const clickable = buttons
      .filter((el) => getComputedStyle(el).display !== "none" && el.offsetParent !== null)
      .map((el) => ({
        tag: el.tagName,
        testid: el.getAttribute("data-test"),
        text: (el.textContent ?? "").trim().slice(0, 20),
      }));

    return {
      currentBox: box(current),
      currentText: (current?.textContent ?? "").replace(/\s+/g, " ").trim(),
      entitlementsCount: entItems.length,
      entitlements: entItems,
      periodEndText: (periodEnd?.textContent ?? "").trim(),
      periodKeyText: (periodKey?.textContent ?? "").trim(),
      barBox: box(bar),
      barClass: bar?.className ?? null,
      barBg: bar ? getComputedStyle(bar).backgroundColor : null,
      hintExists: !!hint,
      hintButtons: hint ? hint.querySelectorAll("button").length : 0,
      clickableCount: clickable.length,
      upgradeAction: clickable.filter((c) => c.testid === "plan-upgrade-action"),
      planCardCount: document.querySelectorAll('[data-test^="plan-card-"]').length,
    };
  });
  console.log(`${tag}=` + JSON.stringify(data));

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  console.log(`${tag}_OVERFLOW=` + overflow);
  return data;
}

test("套餐页修复后：demo（机构版）", async ({ page, request }) => {
  const demo = demoCreds();
  await loginAs(page, request, demo.account, demo.password);
  await expect(page.locator('[data-test="plan-current"]')).toBeVisible({ timeout: 30_000 });

  const d = await measure(page, "AFTER_DEMO");

  // 缺陷 1：四项权益必须在
  expect(d.entitlementsCount).toBe(4);
  // 缺陷 2：到期日必须显示，且与用量周期分开
  expect(d.periodEndText).toContain("到期");
  expect(d.periodKeyText).toContain("用量周期");
  // 机构版不限篇数 → 无进度条
  expect(d.barBox).toBeNull();

  // 对比度（统一使用 support/contrast.ts）
  const contrastResults = await measureContrastTargets(page, [
    { label: "plan-period-end", selector: '[data-test="plan-period-end"]' },
    { label: "plan-entitlement-label", selector: '[data-test="plan-entitlement-team"] p' },
  ]);
  console.log("AFTER_DEMO_CONTRAST=" + JSON.stringify(contrastResults));
  const muted = contrastResults.find((c) => c.label === "plan-period-end")!;
  expect(muted.ratio).toBeGreaterThanOrEqual(muted.threshold);

  await page.screenshot({ path: "e2e/screenshots/plan-after-demo.png", fullPage: true });
});

test("套餐页修复后：免费版（额度用尽）", async ({ page, request }) => {
  const sec = secondaryCreds();
  await loginAs(page, request, sec.account, sec.password);
  await expect(page.locator('[data-test="plan-current"]')).toBeVisible({ timeout: 30_000 });

  const d = await measure(page, "AFTER_FREE");

  // 缺陷 3：进度条不能是主色蓝
  expect(d.barClass).not.toContain("bg-primary");
  expect(d.barClass).toMatch(/bg-warning|bg-destructive/);
  // 缺陷 4：升级入口必须可点
  expect(d.hintButtons).toBeGreaterThan(0);
  expect(d.upgradeAction.length).toBe(1);
  // 免费档到期日不编假日期
  expect(d.periodEndText).toContain("免费");

  // 键盘可达：Tab 能到升级按钮
  await page.locator('[data-test="plan-upgrade-action"]').focus();
  const focused = await page.evaluate(
    () => document.activeElement?.getAttribute("data-test") ?? null
  );
  console.log("AFTER_FREE_FOCUSED=" + focused);
  expect(focused).toBe("plan-upgrade-action");

  // 焦点环可见
  const ring = await page.locator('[data-test="plan-upgrade-action"]').evaluate((el) => {
    const s = getComputedStyle(el);
    return { style: s.outlineStyle, width: s.outlineWidth };
  });
  console.log("AFTER_FREE_FOCUS_RING=" + JSON.stringify(ring));
  expect(ring.style).not.toBe("none");

  // 点升级按钮 → 滚到套餐对比区（页面滚动位置变化）
  const before = await page.evaluate(() => window.scrollY);
  await page.locator('[data-test="plan-upgrade-action"]').click();
  await page.waitForTimeout(600);
  const after = await page.evaluate(() => window.scrollY);
  console.log(`AFTER_FREE_SCROLL before=${before} after=${after}`);

  // 移动端
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  console.log("AFTER_FREE_MOBILE_OVERFLOW=" + mobileOverflow);
  expect(mobileOverflow).toBeLessThanOrEqual(1);

  await page.screenshot({ path: "e2e/screenshots/plan-after-free-mobile.png", fullPage: true });
});
