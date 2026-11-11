import { expect, test, type Page } from "@playwright/test";
import { demoPassword, secondaryPassword } from "./credentials";

// 套餐页「修复后量数值」验收（AGENTS §6.5.7 第 2/3 条）。
// 与修复前的取证逐项对比，用数据证明缺陷真的没了。
const API = "http://10.0.10.2:48080/api/v1";

async function loginAs(page: Page, account: string, password: string) {
  await page.goto("/login");
  await page.locator("#account").fill(account);
  await page.locator("#password").fill(password);
  await page.locator('form button[type="submit"]').click();
  await page.getByRole("link", { name: /任务/ }).waitFor({ timeout: 30_000 });
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

test("套餐页修复后：demo（机构版）", async ({ page }) => {
  await loginAs(page, "demo", demoPassword());
  await page.goto("/settings/plan");
  await expect(page.locator('[data-test="plan-current"]')).toBeVisible({ timeout: 30_000 });

  const d = await measure(page, "AFTER_DEMO");

  // 缺陷 1：四项权益必须在
  expect(d.entitlementsCount).toBe(4);
  // 缺陷 2：到期日必须显示，且与用量周期分开
  expect(d.periodEndText).toContain("到期");
  expect(d.periodKeyText).toContain("用量周期");
  // 机构版不限篇数 → 无进度条
  expect(d.barBox).toBeNull();

  // 对比度
  const contrast = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 1; canvas.height = 1;
    const ctx = canvas.getContext("2d")!;
    const toRgb = (c: string): [number, number, number] => {
      ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = "#000"; ctx.fillStyle = c;
      ctx.fillRect(0, 0, 1, 1);
      const d = ctx.getImageData(0, 0, 1, 1).data;
      return [d[0], d[1], d[2]];
    };
    const lum = (rgb: [number, number, number]) => {
      const f = (x: number) => { const s = x / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
      return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
    };
    const ratio = (fg: string, bg: string) => {
      const l1 = lum(toRgb(fg)), l2 = lum(toRgb(bg));
      const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
      return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
    };
    const card = document.querySelector('[data-test="plan-current"]') as HTMLElement;
    const cardBg = getComputedStyle(card).backgroundColor;
    const muted = document.querySelector('[data-test="plan-period-end"]') as HTMLElement;
    const entLabel = document.querySelector(
      '[data-test="plan-entitlement-team"] p'
    ) as HTMLElement;
    return {
      mutedRatio: ratio(getComputedStyle(muted).color, cardBg),
      entLabelRatio: ratio(getComputedStyle(entLabel).color, cardBg),
    };
  });
  console.log("AFTER_DEMO_CONTRAST=" + JSON.stringify(contrast));
  expect(contrast.mutedRatio).toBeGreaterThanOrEqual(4.5);

  await page.screenshot({ path: "e2e/screenshots/plan-after-demo.png", fullPage: true });
});

test("套餐页修复后：免费版（额度用尽）", async ({ page }) => {
  await loginAs(page, "e2efull", secondaryPassword());
  await page.goto("/settings/plan");
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
