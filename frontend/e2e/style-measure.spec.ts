import { expect, test } from "@playwright/test";
import { demoCreds } from "./support/env";
import { seedAuthByApi } from "./support/auth";
import { measureContrastTargets } from "./support/contrast";

// 样式页「修复后量数值」验收（AGENTS §6.5.7）：与修复前取证逐项对照。

test("样式页修复后：预览/差异/告警/对比度", async ({ page, request }) => {
  const { account, password } = demoCreds();
  await seedAuthByApi(page, request, account, password, "/styles");
  await expect(page.locator('[data-test^="style-row-"]').first()).toBeVisible({
    timeout: 30_000,
  });

  // ① 预览数量与可见性
  const previewCount = await page.locator('[data-test^="style-preview-"]').count();
  console.log("AFTER_PREVIEW_SELECTOR_COUNT=" + previewCount);

  // ② 每个预览的几何（是否真的占了版面，不是 0 高度）
  const previewGeometry = await page
    .locator('[data-test^="style-preview-"]:not([data-test="style-preview-error"]):not([data-test="style-preview-note"])')
    .evaluateAll((nodes) =>
      nodes.map((n) => {
        const el = n as HTMLElement;
        const r = el.getBoundingClientRect();
        return {
          testid: el.getAttribute("data-test"),
          w: Math.round(r.width),
          h: Math.round(r.height),
          textLen: (el.textContent ?? "").trim().length,
        };
      })
    );
  console.log("AFTER_PREVIEW_GEOMETRY=" + JSON.stringify(previewGeometry));

  // ③ 预览文本去重（核心：6 种样式必须 6 种输出）
  const texts = await page
    .locator('[data-test^="style-preview-"]:not([data-test="style-preview-error"]):not([data-test="style-preview-note"])')
    .evaluateAll((nodes) =>
      nodes.map((n) => (n.textContent ?? "").replace(/\s+/g, " ").trim())
    );
  const distinct = new Set(texts);
  console.log("AFTER_DISTINCT=" + distinct.size + " / " + texts.length);

  // ④ 兜底告警数（应为 0）
  const warnings = await page.locator('[data-test^="style-preview-warning-"]').count();
  console.log("AFTER_FALLBACK_WARNINGS=" + warnings);

  // ⑤ 预览口径说明
  const note = await page.locator('[data-test="style-preview-note"]').innerText();
  console.log("AFTER_NOTE=" + note.replace(/\s+/g, " ").slice(0, 120));

  // ⑥ 溢出
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  console.log("AFTER_DESKTOP_OVERFLOW=" + overflow);

  // ⑦ 对比度（统一使用 support/contrast.ts）
  const contrastResults = await measureContrastTargets(page, [
    {
      label: "gbt7714-2025-preview",
      selector: '[data-test="style-preview-gbt7714-2025-numeric"] p',
    },
  ]);
  const contrast = contrastResults[0]!;
  console.log("AFTER_CONTRAST=" + JSON.stringify(contrast));

  // ⑧ 键盘可达性：预览区是否需要交互（只读内容，不应抢 Tab）
  const focusable = await page.evaluate(() => {
    const els = Array.from(
      document.querySelectorAll("button, a, [tabindex], input")
    ) as HTMLElement[];
    return els
      .filter((el) => getComputedStyle(el).display !== "none" && el.offsetParent !== null)
      .map((el) => ({
        tag: el.tagName,
        testid: el.getAttribute("data-test"),
        label: el.getAttribute("aria-label") || (el.textContent ?? "").trim().slice(0, 16),
      }));
  });
  console.log("AFTER_FOCUSABLE_COUNT=" + focusable.length);
  console.log("AFTER_FOCUSABLE=" + JSON.stringify(focusable.slice(-4)));

  // 移动端
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  console.log("AFTER_MOBILE_OVERFLOW=" + mobileOverflow);

  await page.screenshot({ path: "e2e/screenshots/style-after-mobile.png", fullPage: true });

  // 硬断言
  expect(distinct.size).toBe(texts.length);
  expect(warnings).toBe(0);
  expect(overflow).toBeLessThanOrEqual(1);
  expect(mobileOverflow).toBeLessThanOrEqual(1);
  expect(contrast.ratio).toBeGreaterThanOrEqual(4.5);
});
