import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { apiBase, demoCreds } from "./support/env";
import { fetchAuthToken, seedAuthByApi } from "./support/auth";

// 用量看板走查（Phase 2 可观测性 + 2026-10-26 重设计）：
// 真实后端 → 概览卡片 + 全状态分布 + 最近任务，并核对「看板的数字自洽」。
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");
const API = apiBase();

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: resolve(SHOTS, `observability-${name}-${test.info().project.name}.png`),
    fullPage: true,
  });
}

async function login(page: Page, request: APIRequestContext) {
  const { account, password } = demoCreds();
  await seedAuthByApi(page, request, account, password, "/observability");
}

test("用量看板：概览卡片 + 状态分布 + 最近任务", async ({ page, request }) => {
  const { account, password } = demoCreds();
  const token = (await fetchAuthToken(request, account, password)).access_token;
  const overview = await request.get(`${API}/observability/overview`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(overview.ok()).toBeTruthy();
  const body = await overview.json();
  expect(body.tasks.total).toBeGreaterThan(0);

  await login(page, request);

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
  const { account, password } = demoCreds();
  const token = (await fetchAuthToken(request, account, password)).access_token;
  const body = await (
    await request.get(`${API}/observability/overview`, {
      headers: { Authorization: `Bearer ${token}` },
    })
  ).json();

  await login(page, request);
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

test("用量看板：点击最近任务行进入任务详情", async ({ page, request }) => {
  // 看板的价值在于「看到异常能立刻跳过去看」；行不可点就只是个报表。
  await login(page, request);

  const firstRow = page.locator('[data-test^="observability-row-"]').first();
  await expect(firstRow).toBeVisible({ timeout: 30_000 });
  await firstRow.click();

  await expect(page).toHaveURL(/\/tasks\/[0-9a-f-]{36}/, { timeout: 15_000 });
});
