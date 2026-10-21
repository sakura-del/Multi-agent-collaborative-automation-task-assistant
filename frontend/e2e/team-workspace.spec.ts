import { expect, test, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// 团队工作区 + 席位 + 导师批注走查（Phase 2）
//
// ## 两条必须遵守的写法
//
// 1. **所有测试数据带时间戳**：双视口（chromium + mobile-chrome）串行跑，
//    且失败重跑会留下残留。同名数据会让 `filter({ hasText })` 命中多个元素
//    （strict mode violation）。首版就踩了：mobile 因上一轮残留的
//    「E2E 验收团队」而匹配到 2 个。
// 2. **先清理再断言**：批注用唯一内容，删除后按 `data-test` 精确断言
//    `toHaveCount(0)`，不用 `not.toContainText`（后者在行被复用时不可靠）。
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");
const API = "http://10.0.10.2:48080/api/v1";

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: resolve(SHOTS, `team-${name}-${test.info().project.name}.png`),
    fullPage: true,
  });
}

async function login(page: Page, account: string, password: string) {
  await page.goto("/login");
  await page.locator("#account").fill(account);
  await page.locator("#password").fill(password);
  await page.locator('form button[type="submit"]').click();
  await page.getByRole("link", { name: /任务/ }).waitFor({ timeout: 30_000 });
}

test("团队页：创建团队 + 席位用量展示", async ({ page }) => {
  // 唯一名字（双视口 + 重跑都不冲突）
  const stamp = `${Date.now()}-${test.info().project.name}`;
  const teamName = `E2E 团队 ${stamp}`;
  const slug = `e2e-team-${stamp}`.toLowerCase().replace(/[^a-z0-9-]/g, "-");

  await login(page, "demo", "demo12345");
  await page.goto("/workspaces");

  await expect(page.getByRole("heading", { name: "团队" })).toBeVisible({ timeout: 30_000 });
  await shot(page, "list");

  await page.getByRole("button", { name: /创建团队/ }).click();
  const form = page.locator('[data-test="workspace-create-form"]');
  await expect(form).toBeVisible();

  await page.locator("#ws-name").fill(teamName);
  await page.locator("#ws-slug").fill(slug);
  await form.getByRole("button", { name: "创建" }).click();

  // 用唯一名字定位，绝不会匹配到多个
  const row = page.locator('[data-test^="workspace-row-"]').filter({ hasText: teamName });
  await expect(row).toHaveCount(1, { timeout: 30_000 });
  await expect(row).toContainText("席位 1/");
  await shot(page, "created");

  await row.getByRole("link", { name: "管理成员" }).click();
  await expect(page.locator('[data-test="workspace-detail-name"]')).toHaveText(teamName);
  await expect(page.locator('[data-test="workspace-seat-usage"]')).toContainText("席位 1/");
  await shot(page, "detail");

  // 生成邀请码（只显示一次）
  await page.locator('[data-test="workspace-generate-invite"]').click();
  const invite = page.locator('[data-test="workspace-detail-invite"]');
  await expect(invite).toBeVisible({ timeout: 30_000 });
  await expect(invite).toContainText("只显示这一次");
  const code = (await invite.locator("code").textContent())?.trim() ?? "";
  expect(code.length).toBeGreaterThan(10);
  await shot(page, "invite");

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  expect(overflow).toBeLessThanOrEqual(1);
});

test("任务详情页展示导师批注面板", async ({ page, request }) => {
  const stamp = `${Date.now()}-${test.info().project.name}`;
  const content = `E2E 批注 ${stamp}`;

  const loginResp = await request.post(`${API}/auth/login`, {
    data: { account: "demo", password: "demo12345" },
  });
  const token = (await loginResp.json()).access_token as string;
  const headers = { Authorization: `Bearer ${token}` };

  const tasks = await request.get(`${API}/tasks?limit=50`, { headers });
  const list = (await tasks.json()) as { task_id: string; status: string }[];
  const completed = list.find((item) => item.status === "completed");
  expect(completed, "demo 账号应有已完成任务").toBeTruthy();
  const taskId = completed!.task_id;

  await login(page, "demo", "demo12345");
  await page.goto(`/tasks/${taskId}`);

  await expect(page.getByRole("heading", { name: "导师批注" })).toBeVisible({
    timeout: 30_000,
  });
  await shot(page, "annotation-panel");

  // 写一条批注（内容唯一）
  await page.locator('[data-test="annotation-input"]').fill(content);
  await page.locator('[data-test="annotation-submit"]').click();

  const listLocator = page.locator('[data-test="annotation-list"]');
  await expect(listLocator).toContainText(content, { timeout: 30_000 });
  await shot(page, "annotation-created");

  // 用唯一内容定位这一行（不用位置索引，避免受历史批注影响）
  const row = listLocator.locator("li").filter({ hasText: content });
  await expect(row).toHaveCount(1);

  // 标记已解决 → 徽标出现
  await row.getByRole("button", { name: "标记已解决" }).click();
  await expect(row).toContainText("已解决", { timeout: 30_000 });
  await shot(page, "annotation-resolved");

  // 删除 → 该行消失（按唯一内容断言，不用 not.toContainText）
  await row.getByRole("button", { name: "删除" }).click();
  await expect(listLocator.locator("li").filter({ hasText: content })).toHaveCount(0, {
    timeout: 30_000,
  });
});
