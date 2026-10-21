import { expect, test, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// 可观测性看板走查（Phase 2）：真实后端 → 概览卡片 + 状态分布 + 最近任务
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");
const API = "http://10.0.10.2:48080/api/v1";

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: resolve(SHOTS, `observability-${name}-${test.info().project.name}.png`),
    fullPage: true,
  });
}

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#account").fill("demo");
  await page.locator("#password").fill("demo12345");
  await page.locator('form button[type="submit"]').click();
  await page.getByRole("link", { name: /任务/ }).waitFor({ timeout: 30_000 });
}

test("用量看板：概览卡片 + 状态分布 + 最近任务", async ({ page, request }) => {
  // 确认后端有数据（demo 账号有历史任务）
  const loginResp = await request.post(`${API}/auth/login`, {
    data: { account: "demo", password: "demo12345" },
  });
  expect(loginResp.ok()).toBeTruthy();
  const token = (await loginResp.json()).access_token as string;
  const overview = await request.get(`${API}/observability/overview`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(overview.ok()).toBeTruthy();
  const body = await overview.json();
  expect(body.tasks.total).toBeGreaterThan(0);

  await login(page);
  await page.goto("/observability");

  // 概览卡片
  const total = page.locator('[data-test="observability-total"]');
  await expect(total).toBeVisible({ timeout: 30_000 });
  await expect(total).toHaveText(String(body.tasks.total));
  await expect(page.locator('[data-test="observability-success-rate"]')).toContainText("%");
  await expect(page.locator('[data-test="observability-token-total"]')).toBeVisible();
  await expect(page.locator('[data-test="observability-token-avg"]')).toBeVisible();

  // 状态分布徽标存在
  await expect(page.locator('[data-test="observability-statuses"]')).toBeVisible();

  // 最近任务行
  await expect(page.locator('[data-test^="observability-row-"]').first()).toBeVisible();

  await shot(page, "dashboard");

  // 无横向溢出（沿用既有移动端验收思路）
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  expect(overflow).toBeLessThanOrEqual(1);
});
