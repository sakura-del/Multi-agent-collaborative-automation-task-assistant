import { expect, test } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { demoCreds } from "./support/env";
import { seedAuthByApi } from "./support/auth";
import { measureContrastTargets } from "./support/contrast";

// 用量看板「量数值」验收（AGENTS §6.5.7 第 2/3 条）：
// 不靠推理判断「看起来没问题」，而是把关键几何与文本全量取出来打印。
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");

test("用量看板：量数值（几何 / 文本 / 溢出 / 对比度）", async ({ page, request }) => {
  const { account, password } = demoCreds();
  await seedAuthByApi(page, request, account, password, "/observability");
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
        // 行改成 <button> 后标题是 span.font-medium（首版选择器写死 p.font-medium，
        // 结果 ROWS_WITH_TITLE 全 0 —— 是测量脚本失效，不是页面缺陷）
        const title = n.querySelector("span.font-medium")?.textContent?.trim() ?? "";
        return {
          title,
          hasTitle: title.length > 0,
          text: (n.textContent ?? "").trim().slice(0, 90),
        };
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

  // ⑧ 文字对比度：统一使用 support/contrast.ts（Canvas OKLCH + 祖先 Alpha 合成）
  const contrastResults = await measureContrastTargets(page, [
    { label: "observability-total", selector: '[data-test="observability-total"]' },
    { label: "success-denominator", selector: '[data-test="observability-success-denominator"]' },
  ]);
  console.log("CONTRAST=" + JSON.stringify(contrastResults));
  const totalContrast = contrastResults.find((c) => c.label === "observability-total")!;
  const mutedContrast = contrastResults.find((c) => c.label === "success-denominator")!;

  // ⑨ 移动端：无横向溢出，且卡片纵向堆叠
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  const mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
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
  // 硬断言：正文与次要文案对比度 ≥ AA
  expect(totalContrast.ratio).toBeGreaterThanOrEqual(totalContrast.threshold);
  expect(mutedContrast.ratio).toBeGreaterThanOrEqual(mutedContrast.threshold);
});
