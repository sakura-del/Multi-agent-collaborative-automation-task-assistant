import { expect, test, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { demoPassword } from "./credentials";

// M2 迁移后的视觉验收截图
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: resolve(SHOTS, `m2-${name}-${test.info().project.name}.png`),
    fullPage: true,
  });
}

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#account").fill("demo");
  await page.locator("#password").fill(demoPassword());
  await page.locator('form button[type="submit"]').click();
  const loggedIn = page.getByRole("link", { name: /任务/ });
  for (let i = 0; i < 6; i += 1) {
    const settled = await Promise.race([
      loggedIn
        .waitFor({ state: "visible", timeout: 8_000 })
        .then(() => "ok" as const)
        .catch(() => null),
      page
        .locator("text=请求过于频繁")
        .waitFor({ state: "visible", timeout: 8_000 })
        .then(() => "limited" as const)
        .catch(() => null),
    ]);
    if (settled === "ok") return;
    await page.waitForTimeout(8_000);
    await page.locator('form button[type="submit"]').click().catch(() => undefined);
  }
  await expect(loggedIn).toBeVisible({ timeout: 30_000 });
}

test("M2 视觉验收：登录 + 任务列表", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await shot(page, "login");

  await login(page);
  // 等列表真正渲染出来再截图：登录成功 ≠ 列表加载完（loadList 是异步的，
  // 早截图会拍到「加载中…」——我第一次就踩到这个，误以为页面坏了）
  await expect(page.locator('[data-test^="task-row-"]').first()).toBeVisible({
    timeout: 30_000,
  });
  await shot(page, "tasks");

  // 新建任务表单展开态
  await page.locator('[data-test="new-task"]').click();
  await page.waitForTimeout(400);
  await shot(page, "task-form");
});
