import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { demoCreds } from "./support/env";
import { seedAuthByApi } from "./support/auth";

// M2 全部页面迁移后的视觉验收截图
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");

async function shot(page: Page, name: string, fullPage = true) {
  await page.screenshot({
    path: resolve(SHOTS, `m2all-${name}-${test.info().project.name}.png`),
    fullPage,
  });
}

async function login(page: Page, request: APIRequestContext) {
  const { account, password } = demoCreds();
  await seedAuthByApi(page, request, account, password, "/tasks");
}

test("登录页", async ({ page }) => {
  // 截「登录页」要的是默认入口（邮箱验证码一体流），不是密码兜底态
  await page.goto("/login");
  await expect(page.locator('[data-test="auth-title"]')).toBeVisible();
  await shot(page, "login");
});

test("任务列表", async ({ page, request }) => {
  await login(page, request);
  await expect(page.locator('[data-test^="task-row-"]').first()).toBeVisible({
    timeout: 30_000,
  });
  await shot(page, "tasks");
});

test("任务详情 · 规划 + 执行 + 日志 + 结果", async ({ page, request }) => {
  await login(page, request);
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

test("知识库", async ({ page, request }) => {
  await login(page, request);
  await page.goto("/knowledge");
  await expect(page.getByRole("heading", { name: "知识库" })).toBeVisible({ timeout: 30_000 });
  await shot(page, "knowledge");
});

test("引用样式", async ({ page, request }) => {
  await login(page, request);
  await page.goto("/styles");
  await expect(page.getByRole("heading", { name: "引用样式" })).toBeVisible({ timeout: 30_000 });
  await shot(page, "styles");
});

test("Word 模板", async ({ page, request }) => {
  await login(page, request);
  await page.goto("/templates");
  await expect(page.getByRole("heading", { name: "Word 模板" })).toBeVisible({ timeout: 30_000 });
  await shot(page, "templates");
});

test("导入文献", async ({ page, request }) => {
  await login(page, request);
  await page.goto("/references/import");
  await expect(page.getByRole("heading", { name: "导入参考文献" })).toBeVisible({ timeout: 30_000 });
  await shot(page, "import");
});

test("密码重置页", async ({ page }) => {
  await page.goto("/password-reset");
  await page.waitForTimeout(500);
  await shot(page, "password-reset");
});
