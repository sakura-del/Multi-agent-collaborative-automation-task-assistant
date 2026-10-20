import { expect, test } from "@playwright/test";

// 一次性诊断：移动端视口下模板行按钮的几何与可见性状态
test("诊断移动端模板按钮", async ({ page }) => {
  await page.goto("/login");
  await page.locator("#account").fill("demo");
  await page.locator("#password").fill("demo12345");
  await page.locator('form button[type="submit"]').click();
  await expect(page.getByRole("link", { name: /任务/ })).toBeVisible({ timeout: 30_000 });

  await page.goto("/templates");
  await page.waitForTimeout(2500);

  const info = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('[data-test^="template-row-"]'));
    // 找出所有超出视口右边界的元素，定位真正的溢出源
    const overflowing: {
      tag: string;
      cls: string;
      right: number;
      width: number;
      text: string;
    }[] = [];
    for (const el of Array.from(document.querySelectorAll("body *"))) {
      const rect = el.getBoundingClientRect();
      if (rect.right > window.innerWidth + 1 && rect.width > 0) {
        overflowing.push({
          tag: el.tagName,
          cls: typeof (el as HTMLElement).className === "string" ? (el as HTMLElement).className : "",
          right: Math.round(rect.right),
          width: Math.round(rect.width),
          text: (el.textContent || "").trim().slice(0, 40),
        });
      }
    }
    return {
      viewport: { w: window.innerWidth, h: window.innerHeight },
      doc: {
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      },
      rowFlexDirection: rows.length
        ? getComputedStyle(rows[0] as Element).flexDirection
        : null,
      mediaMatches: window.matchMedia("(max-width: 720px)").matches,
      overflowing: overflowing.slice(0, 12),
    };
  });

  console.log("DIAG=" + JSON.stringify(info, null, 2));
});
