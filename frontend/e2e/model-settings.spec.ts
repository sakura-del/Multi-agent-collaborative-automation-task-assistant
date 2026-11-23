import { expect, test, type Page, type APIRequestContext } from "@playwright/test";

import { demoPassword, E2E_DEMO_ACCOUNT } from "./credentials";
import { gotoPasswordLogin } from "./support/auth";

// 我的模型页 E2E（Issue #15 BYOK）。
//
// 验的是**用户真实路径**：登录 → 接入一把 key → 列表里能看到脱敏值 →
// 「测试连接」失败时不假装成功 → 删除需要二次确认。
//
// 刻意不依赖任何真实厂商：端点用 `.invalid` 顶级域（RFC 2606 保证不解析），
// 因此「测试连接」必然失败——这恰好用来验证失败路径不会被伪装成成功。
//
// 清理：用例前后都调用 API 删除本 spec 创建的凭据，避免污染目标库。

const API = "/api/v1";
const NAME_PREFIX = "E2E 模型凭据";
const PLAINTEXT = "sk-e2e-0123456789abcdefghij";

async function login(page: Page) {
  await gotoPasswordLogin(page);
  await page.locator("#account").fill(E2E_DEMO_ACCOUNT);
  await page.locator("#password").fill(demoPassword());
  await page.locator('form button[type="submit"]').click();
  // 不能用「任务」链接判断登录完成：移动端侧栏是抽屉，登录后链接不可见。
  await page.waitForURL(/\/tasks/, { timeout: 30_000 });
}

async function bearer(request: APIRequestContext): Promise<string> {
  const resp = await request.post(`${API}/auth/login`, {
    data: { account: E2E_DEMO_ACCOUNT, password: demoPassword() },
  });
  expect(resp.ok()).toBeTruthy();
  return (await resp.json()).access_token as string;
}

/** 清掉历史残留 + 本次创建（上次跑挂时可能留下）。 */
async function cleanup(request: APIRequestContext) {
  const token = await bearer(request);
  const headers = { Authorization: `Bearer ${token}` };
  const resp = await request.get(`${API}/llm-credentials`, { headers });
  if (!resp.ok()) {
    return;
  }
  for (const item of (await resp.json()) as { credential_id: string; name: string }[]) {
    if (item.name.startsWith(NAME_PREFIX)) {
      await request.delete(`${API}/llm-credentials/${item.credential_id}`, { headers });
    }
  }
}

test("我的模型：接入 / 脱敏 / 测试失败不伪装 / 二次确认删除", async ({ page, request }) => {
  const name = `${NAME_PREFIX} ${Date.now()}`;

  await cleanup(request);
  try {
    await login(page);
    await page.goto("/settings/models");
    await expect(page.locator('[data-test="model-create-open"]')).toBeVisible({ timeout: 30_000 });

    // ① 接入：自定义端点 + 假 key（保存不做联网校验，所以能存进去）
    await page.locator('[data-test="model-create-open"]').click();
    await page.locator('[data-test="model-name"]').fill(name);
    await page.locator('[data-test="model-provider"]').selectOption("custom");
    await page.locator('[data-test="model-base-url"]').fill("https://byok-e2e.invalid/v1");
    await page.locator('[data-test="model-id"]').fill("e2e-model");
    await page.locator('[data-test="model-key"]').fill(PLAINTEXT);

    // ② 测试连接：域名不解析 → 必须明确显示失败，而不是弹「连接成功」
    await page.locator('[data-test="model-probe"]').click();
    const probeResult = page.locator('[data-test="model-probe-result"]');
    await expect(probeResult).toBeVisible({ timeout: 30_000 });
    await expect(probeResult).toContainText("连接失败");

    await page.locator('[data-test="model-submit"]').click();

    // ③ 列表：名称在、明文不在、只显示脱敏值
    const row = page.locator('[data-test="models-list"] li', { hasText: name });
    await expect(row).toBeVisible({ timeout: 30_000 });
    await expect(row).toContainText("e2e-model");
    await expect(row).not.toContainText(PLAINTEXT);
    await expect(row).toContainText("sk-");

    // ④ 删除：第一次点击只进入确认态，第二次才真的删
    await row.getByRole("button", { name: "删除" }).click();
    await expect(row.getByRole("button", { name: /确认删除/ })).toBeVisible();
    await row.getByRole("button", { name: /确认删除/ }).click();
    await expect(page.locator('[data-test="models-list"] li', { hasText: name })).toHaveCount(0, {
      timeout: 30_000,
    });
  } finally {
    await cleanup(request);
  }
});

test("侧栏：折叠成图标轨，图标可切页", async ({ page }) => {
  // 移动端侧栏是抽屉（nav-mobile-trigger 打开），没有「折叠成图标轨」这个形态
  test.skip(test.info().project.name === "mobile-chrome", "移动端侧栏为抽屉，无折叠轨");
  await login(page);

  const sidebar = page.locator('[data-test="app-sidebar"]');
  await expect(sidebar).toBeVisible();
  const expanded = await sidebar.boundingBox();

  await page.locator('[data-test="nav-collapse"]').click();
  await expect(page.locator('[data-test="nav-expand"]')).toBeVisible();
  await page.waitForTimeout(700);
  const collapsed = await sidebar.boundingBox();
  expect(collapsed?.width ?? 0).toBeLessThan(expanded?.width ?? 0);

  // 折叠态图标仍是链接：直接点就能切页（快速导航已移除）
  await expect(page.locator('[data-test="nav-picker-trigger"]')).toHaveCount(0);
  await page.locator('[data-test="nav-knowledge"]').click();
  await page.waitForURL("**/knowledge", { timeout: 10_000 });

  // Ctrl/Cmd+B 展开
  await page.keyboard.press("Control+b");
  await expect(page.locator('[data-test="nav-collapse"]')).toBeVisible({ timeout: 5_000 });
});
