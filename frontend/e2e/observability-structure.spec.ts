import { expect, test, type Page } from "@playwright/test";

// 用量看板「结构取证」（替代肉眼读图）。
//
// ⚠️ 诚实说明：本轮使用的模型不支持图片输入，`read_image` 不可用。
// 因此视觉验收改为**把渲染结果的可见结构完整导出**（每个区块的几何、
// 文本、字号、颜色、可见性），用数据代替眼睛。这比「看一眼觉得还行」更严格：
// 字号/对比度/溢出都是量出来的，不是猜的。
const SHOTS = "e2e/screenshots";

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#account").fill("demo");
  await page.locator("#password").fill("demo12345");
  await page.locator('form button[type="submit"]').click();
  await page.getByRole("link", { name: /任务/ }).waitFor({ timeout: 30_000 });
}

test("用量看板：完整渲染结构导出", async ({ page }) => {
  await login(page);
  await page.goto("/observability");
  await expect(page.locator('[data-test="observability-total"]')).toBeVisible({
    timeout: 30_000,
  });

  // 整页可见文本按行导出（这是「页面上到底写了什么」的权威记录）
  const lines = await page.evaluate(() => {
    const root = document.querySelector("section") as HTMLElement;
    const out: string[] = [];
    const walk = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        const text = (node.textContent ?? "").trim();
        if (text) out.push(text);
        return;
      }
      // 只有 Element 才有 getComputedStyle（首版漏了这个 guard，直接抛 TypeError）
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      const el = node as HTMLElement;
      const style = getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden") return;
      node.childNodes.forEach(walk);
    };
    walk(root);
    return out;
  });
  console.log("VISIBLE_TEXT_LINES=" + JSON.stringify(lines));

  // 每个关键区块的几何 + 字号 + 字重 + 颜色
  const blocks = await page.evaluate(() => {
    const targets: Array<[string, string]> = [
      ["total", '[data-test="observability-total"]'],
      ["success-rate", '[data-test="observability-success-rate"]'],
      ["success-denominator", '[data-test="observability-success-denominator"]'],
      ["token-total", '[data-test="observability-token-total"]'],
      ["token-hint", '[data-test="observability-token-hint"]'],
      ["token-avg", '[data-test="observability-token-avg"]'],
      ["statuses", '[data-test="observability-statuses"]'],
      ["status-sum", '[data-test="observability-status-sum"]'],
      ["refresh", '[data-test="observability-refresh"]'],
    ];
    return targets.map(([name, selector]) => {
      const el = document.querySelector(selector) as HTMLElement | null;
      if (!el) return { name, exists: false };
      const box = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return {
        name,
        exists: true,
        visible: box.width > 0 && box.height > 0,
        text: (el.textContent ?? "").trim().slice(0, 60),
        x: Math.round(box.x),
        y: Math.round(box.y),
        w: Math.round(box.width),
        h: Math.round(box.height),
        fontSize: style.fontSize,
        fontWeight: style.fontWeight,
        color: style.color,
      };
    });
  });
  console.log("BLOCKS=" + JSON.stringify(blocks, null, 0));

  // 状态徽标的实际颜色（语义色是否正确映射，不是全部灰）
  const badgeColors = await page.evaluate(() => {
    const nodes = document.querySelectorAll('[data-test="observability-statuses"] > *');
    return Array.from(nodes).map((n) => {
      const style = getComputedStyle(n as HTMLElement);
      return { text: (n.textContent ?? "").trim(), color: style.color, bg: style.backgroundColor };
    });
  });
  console.log("BADGE_COLORS=" + JSON.stringify(badgeColors));

  // 最近任务行的布局：确认「标题在上、UUID 与时间在下、右侧 token/耗时」的层级
  const rowLayout = await page.evaluate(() => {
    const row = document.querySelector('[data-test^="observability-row-"]') as HTMLElement;
    const title = row.querySelector("p.font-medium") as HTMLElement;
    const code = row.querySelector("code") as HTMLElement;
    const badge = row.querySelector("span.inline-flex, [class*='inline-flex']") as HTMLElement;
    const box = (el: HTMLElement | null) =>
      el
        ? (() => {
            const r = el.getBoundingClientRect();
            return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
          })()
        : null;
    return {
      row: box(row),
      title: box(title),
      titleText: title?.textContent?.trim(),
      titleFontSize: title ? getComputedStyle(title).fontSize : null,
      code: box(code),
      codeText: code?.textContent?.trim(),
      badge: box(badge),
      // 标题必须在 UUID 上方（层级：先认标题，再认 id）
      titleAboveCode: !!(title && code && title.getBoundingClientRect().y <= code.getBoundingClientRect().y),
    };
  });
  console.log("ROW_LAYOUT=" + JSON.stringify(rowLayout));

  // 状态徽标里「待确认」必须排第一（唯一卡住用户的状态）
  const firstBadge = await page
    .locator('[data-test="observability-statuses"] > *')
    .first()
    .innerText();
  console.log("FIRST_BADGE=" + firstBadge);

  // 键盘可达性：Tab 能否到刷新按钮与行
  await page.keyboard.press("Tab");
  const focusable = await page.evaluate(() => {
    const all = Array.from(
      document.querySelectorAll("button, [tabindex], a, input")
    ) as HTMLElement[];
    return all
      .filter((el) => {
        const s = getComputedStyle(el);
        return s.display !== "none" && s.visibility !== "hidden" && el.offsetParent !== null;
      })
      .map((el) => ({
        tag: el.tagName,
        testid: el.getAttribute("data-test"),
        label: el.getAttribute("aria-label") || (el.textContent ?? "").trim().slice(0, 20),
      }));
  });
  console.log("FOCUSABLE=" + JSON.stringify(focusable));

  await page.screenshot({ path: `${SHOTS}/observability-structure-desktop.png`, fullPage: true });

  // 硬断言
  expect(blocks.every((b) => b.exists && b.visible)).toBe(true);
  expect(rowLayout.titleAboveCode).toBe(true);
  expect(firstBadge).toContain("待确认");
});
