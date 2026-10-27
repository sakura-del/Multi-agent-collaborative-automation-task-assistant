import { expect, test } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// 结果页需要「已完成」任务才有 review_report，单独取一条已完成的
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");

test("任务结果页（引用核验 + 质量评分）", async ({ page }) => {
  await page.goto("/login");
  await page.locator("#account").fill("demo");
  await page.locator("#password").fill("demo12345");
  await page.locator('form button[type="submit"]').click();
  await page.getByRole("link", { name: /任务/ }).waitFor({ timeout: 30_000 });

  // 找第一条「已完成」任务打开
  const rows = page.locator('[data-test^="task-row-"]');
  await rows.first().waitFor({ timeout: 30_000 });
  const count = await rows.count();
  let opened = false;
  for (let i = 0; i < count; i += 1) {
    const row = rows.nth(i);
    if ((await row.innerText()).includes("已完成")) {
      // 结果页验收不依赖右箭头：移动端按钮按设计隐藏，整行才是统一入口。
      await row.click();
      opened = true;
      break;
    }
  }
  if (!opened) {
    await rows.first().click();
  }

  await page.locator('[data-test="tab-result"]').click({ timeout: 30_000 });
  await page.waitForTimeout(1200);
  await page.screenshot({
    path: resolve(SHOTS, `m2all-result-${test.info().project.name}.png`),
    fullPage: true,
  });
  await expect(page.locator('[data-test="task-result"]')).toBeVisible({ timeout: 20_000 });
});
