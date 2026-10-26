import { expect, test, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// 用量看板走查（Phase 2 可观测性 + 2026-10-26 重设计）：
// 真实后端 → 概览卡片 + 全状态分布 + 最近任务，并核对「看板的数字自洽」。
//
// ⚠️ 这一页的核心风险不是版式，是**数字与真实数据不符**。用户很难自己发现，
// 所以断言必须打在「数字能不能用」上（AGENTS §5 #2），不能只断言元素存在。
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

test("用量看板：数字必须与真实接口一致且自洽", async ({ page, request }) => {
  // 这一条是「看板不说谎」的核心验收：页面上的每个数字都要能在后端找到出处，
  // 并且**分布合计 == 任务总数**（旧版会漏 waiting_approval，差值不可见）。
  const loginResp = await request.post(`${API}/auth/login`, {
    data: { account: "demo", password: "demo12345" },
  });
  expect(loginResp.ok()).toBeTruthy();
  const token = (await loginResp.json()).access_token as string;
  const body = await (
    await request.get(`${API}/observability/overview`, {
      headers: { Authorization: `Bearer ${token}` },
    })
  ).json();

  await login(page);
  await page.goto("/observability");
  await expect(page.locator('[data-test="observability-total"]')).toBeVisible({
    timeout: 30_000,
  });

  // ① 状态分布合计 == 后端 total（后端保证 by_status 之和等于 total）
  const byStatusSum = Object.values(body.by_status as Record<string, number>).reduce(
    (sum, count) => sum + count,
    0
  );
  expect(byStatusSum, "后端 by_status 之和必须等于 total").toBe(body.tasks.total);

  // ② 页面上显示的合计与后端一致
  const statusSum = page.locator('[data-test="observability-status-sum"]');
  await expect(statusSum).toContainText(String(body.tasks.total));

  // ③ 成功率分母可见，且与后端 terminal_count 一致
  const denominator = page.locator('[data-test="observability-success-denominator"]');
  await expect(denominator).toBeVisible();
  await expect(denominator).toContainText(String(body.tasks.completed));
  await expect(denominator).toContainText(String(body.terminal_count));

  // ④ 最近任务第一行的 token 数 == 后端返回的第一行（不是本地算的）
  const firstRow = body.recent[0] as { task_id: string; used_tokens: number; title: string };
  const row = page.locator(`[data-test="observability-row-${firstRow.task_id}"]`);
  await expect(row).toBeVisible();
  // 标题必须显示（只有 8 位 UUID 的列表认不出是哪次运行）
  if (firstRow.title) {
    await expect(row).toContainText(firstRow.title);
  }
  await expect(row).toContainText(firstRow.used_tokens.toLocaleString("zh-CN"));

  await shot(page, "dashboard-consistency");
});

test("用量看板：点击最近任务行进入任务详情", async ({ page }) => {
  // 看板的价值在于「看到异常能立刻跳过去看」；行不可点就只是个报表。
  await login(page);
  await page.goto("/observability");

  const firstRow = page.locator('[data-test^="observability-row-"]').first();
  await expect(firstRow).toBeVisible({ timeout: 30_000 });
  await firstRow.click();

  await expect(page).toHaveURL(/\/tasks\/[0-9a-f-]{36}/, { timeout: 15_000 });
});
