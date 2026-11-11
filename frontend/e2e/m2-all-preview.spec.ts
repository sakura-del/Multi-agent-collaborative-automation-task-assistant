import { expect, test, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { demoPassword } from "./credentials";

// M2 全部页面迁移后的视觉验收截图
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");

async function shot(page: Page, name: string, fullPage = true) {
  await page.screenshot({
    path: resolve(SHOTS, `m2all-${name}-${test.info().project.name}.png`),
    fullPage,
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

test("登录页", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await shot(page, "login");
});

test("任务列表", async ({ page }) => {
  await login(page);
  await expect(page.locator('[data-test^="task-row-"]').first()).toBeVisible({
    timeout: 30_000,
  });
  await shot(page, "tasks");
});

test("任务详情 · 规划 + 执行 + 日志 + 结果", async ({ page }) => {
  await login(page);
  const firstRow = page.locator('[data-test^="task-row-"]').first();
  await firstRow.getByRole("button", { name: "打开" }).click();

  // 规划页
  await expect(page.locator('[data-test="plan-panel"], [data-test="tab-plan"]').first()).toBeVisible({
    timeout: 30_000,
  });
  await page.waitForTimeout(800);
  await shot(page, "task-detail-plan");

  // 执行页（DAG）
  await page.locator('[data-test="tab-execution"]').click();
  await page.waitForTimeout(600);
  await shot(page, "task-detail-execution");

  // 日志页
  await page.locator('[data-test="tab-logs"]').click();
  await page.waitForTimeout(400);
  await shot(page, "task-detail-logs");

  // 结果页（若有）
  const resultTab = page.locator('[data-test="tab-result"]');
  if (await resultTab.isEnabled().catch(() => false)) {
    await resultTab.click();
    await page.waitForTimeout(600);
    await shot(page, "task-detail-result");
  }
});

test("知识库", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: /知识库/ }).click();
  await page.waitForTimeout(1200);
  await shot(page, "knowledge");
});

test("引用样式", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: /引用样式/ }).click();
  await page.waitForTimeout(1000);
  await shot(page, "styles");
});

test("Word 模板", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: /Word 模板/ }).click();
  await page.waitForTimeout(1000);
  await shot(page, "templates");
});

test("导入文献", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: /导入文献/ }).click();
  await page.waitForTimeout(600);
  await shot(page, "import");
});

test("密码重置页", async ({ page }) => {
  await page.goto("/password-reset");
  await page.waitForTimeout(500);
  await shot(page, "password-reset");
});
