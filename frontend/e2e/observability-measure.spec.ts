import { expect, test, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// 用量看板「量数值」验收（AGENTS §6.5.7 第 2/3 条）：
// 不靠推理判断「看起来没问题」，而是把关键几何与文本全量取出来打印。
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#account").fill("demo");
  await page.locator("#password").fill("demo12345");
  await page.locator('form button[type="submit"]').click();
  await page.getByRole("link", { name: /任务/ }).waitFor({ timeout: 30_000 });
}

test("用量看板：量数值（几何 / 文本 / 溢出 / 对比度）", async ({ page }) => {
  await login(page);
  await page.goto("/observability");
  await expect(page.locator('[data-test="observability-total"]')).toBeVisible({
    timeout: 30_000,
  });

  // ① 四张概览卡的几何：等宽、无重叠、无溢出
  const cards = await page
    .locator('[data-test="observability-total"], [data-test="observability-success-rate"], [data-test="observability-token-total"], [data-test="observability-token-avg"]')
    .evaluateAll((nodes) =>
      nodes.map((node) => {
        const box = (node.parentElement as HTMLElement).getBoundingClientRect();
        return {
          test: node.getAttribute("data-test"),
          text: (node.textContent ?? "").trim(),
          x: Math.round(box.x),
          y: Math.round(box.y),
          w: Math.round(box.width),
          h: Math.round(box.height),
        };
      })
    );
  console.log("CARD_GEOMETRY=" + JSON.stringify(cards));

  // ② 状态分布徽标：逐个列出（状态名 + 计数），确认没有漏状态
  const badges = await page
    .locator('[data-test="observability-statuses"] > *')
    .evaluateAll((nodes) => nodes.map((n) => (n.textContent ?? "").trim()));
  console.log("STATUS_BADGES=" + JSON.stringify(badges));
  console.log("STATUS_BADGE_COUNT=" + badges.length);

  // ③ 合计文本（自洽证据）
  const sumText = await page.locator('[data-test="observability-status-sum"]').innerText();
  console.log("STATUS_SUM_TEXT=" + sumText);

  // ④ 成功率分母文本
  const denomText = await page
    .locator('[data-test="observability-success-denominator"]')
    .innerText();
  console.log("SUCCESS_DENOMINATOR=" + denomText);

  // ⑤ token 提示文本（0 与「有记录」两种形态都要能读到）
  const tokenHint = await page.locator('[data-test="observability-token-hint"]').innerText();
  console.log("TOKEN_HINT=" + tokenHint);

  // ⑥ 最近任务行数 + 每行标题是否非空（标题为空说明退回 UUID，属于降级）
  const rows = await page
    .locator('[data-test^="observability-row-"]')
    .evaluateAll((nodes) =>
      nodes.map((n) => {
        const title = n.querySelector("p.font-medium")?.textContent?.trim() ?? "";
        return { title, hasTitle: title.length > 0, text: (n.textContent ?? "").trim().slice(0, 90) };
      })
    );
  console.log("ROW_COUNT=" + rows.length);
  console.log("ROWS_WITH_TITLE=" + rows.filter((r) => r.hasTitle).length);
  console.log("FIRST_ROW=" + JSON.stringify(rows[0]));

  // ⑦ 横向溢出（桌面）
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  console.log("DESKTOP_OVERFLOW_PX=" + overflow);

  // ⑧ 文字对比度：正文与次要文字相对卡片底色（WCAG AA 要求 ≥4.5:1）
  //
  // ⚠️ 本项目 tokens 用 oklch 定义（main.css），getComputedStyle 返回的就是
  // `oklch(0.15 0 0)` 字符串，不是 rgb。首版解析器只认 rgb → 拿到 NaN → 比值算成 1
  // 并误报失败。这里改成**用 canvas 把任意 CSS 颜色转成 sRGB**再算亮度。
  const contrast = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 1;
    canvas.height = 1;
    const ctx = canvas.getContext("2d")!;

    function toRgb(color: string): [number, number, number] {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = "#000";
      ctx.fillStyle = color;
      ctx.fillRect(0, 0, 1, 1);
      const data = ctx.getImageData(0, 0, 1, 1).data;
      return [data[0], data[1], data[2]];
    }
    function luminance(rgb: [number, number, number]): number {
      const toLin = (c: number) => {
        const s = c / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * toLin(rgb[0]) + 0.7152 * toLin(rgb[1]) + 0.0722 * toLin(rgb[2]);
    }
    function ratio(fg: string, bg: string): number {
      const l1 = luminance(toRgb(fg));
      const l2 = luminance(toRgb(bg));
      const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
      return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
    }
    const bodyBg = getComputedStyle(document.body).backgroundColor;
    const total = document.querySelector('[data-test="observability-total"]') as HTMLElement;
    const muted = document.querySelector(
      '[data-test="observability-success-denominator"]'
    ) as HTMLElement;
    const card = total.parentElement as HTMLElement;
    const cardBg = getComputedStyle(card).backgroundColor;
    return {
      bodyBg,
      cardBg,
      bodyRgb: toRgb(bodyBg),
      cardRgb: toRgb(cardBg),
      totalColor: getComputedStyle(total).color,
      totalRgb: toRgb(getComputedStyle(total).color),
      totalRatio: ratio(getComputedStyle(total).color, cardBg),
      mutedColor: getComputedStyle(muted).color,
      mutedRatio: ratio(getComputedStyle(muted).color, cardBg),
      mutedRatioOnBody: ratio(getComputedStyle(muted).color, bodyBg),
    };
  });
  console.log("CONTRAST=" + JSON.stringify(contrast));

  // ⑨ 移动端：无横向溢出，且卡片纵向堆叠
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  console.log("MOBILE_OVERFLOW_PX=" + mobileOverflow);
  await page.screenshot({
    path: resolve(SHOTS, `observability-measured-mobile-${test.info().project.name}.png`),
    fullPage: true,
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(300);
  await page.screenshot({
    path: resolve(SHOTS, `observability-measured-desktop-${test.info().project.name}.png`),
    fullPage: true,
  });

  // 硬断言：溢出必须为 0（不是"看起来没问题"）
  expect(overflow).toBeLessThanOrEqual(1);
  expect(mobileOverflow).toBeLessThanOrEqual(1);
  // 硬断言：每行都有标题（不是 UUID）
  expect(rows.every((r) => r.hasTitle)).toBe(true);
  // 硬断言：正文对比度 ≥ AA
  expect(contrast.totalRatio).toBeGreaterThanOrEqual(4.5);
});
