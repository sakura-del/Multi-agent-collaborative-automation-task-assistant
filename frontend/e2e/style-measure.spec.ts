import { expect, test, type Page } from "@playwright/test";

// 样式页「修复后量数值」验收（AGENTS §6.5.7）：与修复前取证逐项对照。
const API = "http://10.0.10.2:48080/api/v1";

async function loginAs(page: Page, account: string, password: string) {
  await page.goto("/login");
  await page.locator("#account").fill(account);
  await page.locator("#password").fill(password);
  await page.locator('form button[type="submit"]').click();
  await page.getByRole("link", { name: /任务/ }).waitFor({ timeout: 30_000 });
}

test("样式页修复后：预览/差异/告警/对比度", async ({ page }) => {
  await loginAs(page, "demo", "demo12345");
  await page.goto("/styles");
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

  // ⑦ 对比度（预览文字相对预览底色）
  const contrast = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 1;
    canvas.height = 1;
    const ctx = canvas.getContext("2d")!;
    const toRgb = (c: string): [number, number, number] => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = "#000";
      ctx.fillStyle = c;
      ctx.fillRect(0, 0, 1, 1);
      const d = ctx.getImageData(0, 0, 1, 1).data;
      return [d[0], d[1], d[2]];
    };
    const lum = (rgb: [number, number, number]) => {
      const f = (x: number) => {
        const s = x / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
    };
    const ratio = (fg: string, bg: string) => {
      const l1 = lum(toRgb(fg));
      const l2 = lum(toRgb(bg));
      const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
      return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
    };
    const box = document.querySelector(
      '[data-test="style-preview-gbt7714-2025-numeric"]'
    ) as HTMLElement;
    const p = box.querySelector("p") as HTMLElement;
    const boxBg = getComputedStyle(box).backgroundColor;
    return {
      boxBg,
      textColor: getComputedStyle(p).color,
      ratio: ratio(getComputedStyle(p).color, boxBg),
      fontSize: getComputedStyle(p).fontSize,
    };
  });
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
