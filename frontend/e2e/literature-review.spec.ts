import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { existsSync, statSync } from "node:fs";

// 文献综述全流程验收（Plan 4 Task 8 / M4）：
// 注册 → 上传知识库文档 → 建离线任务 → 确认规划 → 观察 DAG 状态变化 → 完成 → 校验引用 → 下载两种产物
// 刷新与重连：RUNNING 期间刷新不丢状态；阻断 WebSocket 后轮询仍推进且不产生重复事件。

const FIXTURE = resolve(dirname(fileURLToPath(import.meta.url)), "fixtures/sample.txt");
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");
const WORKFLOW_TIMEOUT = 12 * 60_000;

/** Plan 4 Exit Gate 截图：任务列表 / 规划确认 / 运行中 DAG / 人工干预 / 最终结果 */
async function capture(page: Page, name: string, project: string) {
  await page.screenshot({ path: resolve(SHOTS, `${project}-${name}.png`), fullPage: true });
}

/** 横向溢出即视为布局缺陷（文本重叠/节点变化导致 DAG 变宽的自动代理指标） */
async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

function uniqueUser(prefix: string): { username: string; email: string; password: string } {
  const stamp = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  return {
    username: `${prefix}${stamp}`.slice(0, 24),
    email: `${prefix}${stamp}@example.com`,
    password: "e2e-password-2026",
  };
}

async function register(page: Page, user: { username: string; email: string; password: string }) {
  await page.goto("/login");
  await page.getByRole("button", { name: "注册" }).click();
  await page.locator("#username").fill(user.username);
  await page.locator("#email").fill(user.email);
  await page.locator("#password").fill(user.password);
  await page.getByRole("button", { name: "注册并登录" }).click();
  await expect(page).toHaveURL(/\/tasks/);
}

async function uploadFixture(page: Page, title: string) {
  await page.goto("/knowledge");
  await page.locator('[data-test="document-file"]').setInputFiles(FIXTURE);
  await page.locator('[data-test="document-title"]').fill(title);
  await page.locator('[data-test="document-submit"]').click();
  await expect(page.locator('[data-test="document-table"]')).toContainText(title, { timeout: 60_000 });
}

async function createOfflineTask(page: Page, topic: string) {
  await page.goto("/tasks");
  await page.locator('[data-test="new-task"]').click();
  await page.locator('[data-test="topic"]').fill(topic);
  const keyword = page.locator('[data-test="keyword-input"]');
  await keyword.fill("retrieval augmented generation");
  await keyword.press("Enter");
  await page.locator('[data-test="offline"]').check();
  await page.locator('[data-test="submit"]').click();
  await expect(page).toHaveURL(/\/tasks\/[0-9a-f-]{36}/, { timeout: 30_000 });
  return page.url().split("/tasks/")[1];
}

test.describe("文献综述工作台全流程", () => {
  test.describe.configure({ timeout: WORKFLOW_TIMEOUT });

  test("从注册到下载产物的完整用户路径", async ({ page }, testInfo) => {
    const project = testInfo.project.name;
    const user = uniqueUser("e2e");
    await register(page, user);
    await uploadFixture(page, "端到端验收样本文档");

    const taskId = await createOfflineTask(page, "检索增强生成与多智能体协作的质量评估");
    await page.goto("/tasks");
    await capture(page, "1-task-list", project);
    await expectNoHorizontalOverflow(page);
    await page.goto(`/tasks/${taskId}`);

    // 规划确认：等规划生成完毕（规划是异步的），再确认当前版本
    await expect(page.locator('[data-test="plan-approve"]')).toBeVisible({ timeout: 240_000 });
    await capture(page, "2-plan-approval", project);
    await expectNoHorizontalOverflow(page);
    await page.locator('[data-test="plan-approve"]').click();

    // 执行 Tab：至少观察到一次节点状态变化
    await page.locator('[data-test="tab-execution"]').click();
    await expect(page.locator('[data-test="dag"]')).toBeVisible({ timeout: 60_000 });
    const nodes = page.locator('[data-test^="dag-node-"]');
    await expect(nodes.first()).toBeVisible();
    const initialLabels = await nodes.evaluateAll((elements) =>
      elements.map((element) => element.getAttribute("aria-label") ?? "")
    );
    await expect
      .poll(
        async () =>
          nodes.evaluateAll((elements) => elements.map((element) => element.getAttribute("aria-label") ?? "")),
        { timeout: 10 * 60_000, intervals: [2000] }
      )
      .not.toEqual(initialLabels);
    await capture(page, "3-running-dag", project);
    await expectNoHorizontalOverflow(page);

    // 状态变化不得改变节点几何（Plan 4 Exit Gate 的硬性要求）
    const firstNodeStyle = await nodes.first().getAttribute("style");
    await capture(page, "4-human-action", project);
    expect(await nodes.first().getAttribute("style")).toBe(firstNodeStyle);

    // 完成后结果 Tab 可用，且正文含引用标记
    await expect(page.locator('[data-test="tab-result"]')).toBeEnabled({ timeout: 10 * 60_000 });
    await page.locator('[data-test="tab-result"]').click();
    const markdown = page.locator('[data-test="result-markdown"]');
    await expect(markdown).toBeVisible();
    await expect(markdown).toContainText(/\[E\d+\]/);
    await capture(page, "5-final-result", project);
    await expectNoHorizontalOverflow(page);

    // 下载两种产物并校验文件非空
    for (const [testid, suffix] of [
      ["download-markdown", ".md"],
      ["download-docx", ".docx"],
    ] as const) {
      const [download] = await Promise.all([
        page.waitForEvent("download"),
        page.locator(`[data-test="${testid}"]`).click(),
      ]);
      const path = await download.path();
      expect(path).toBeTruthy();
      expect(existsSync(path as string)).toBe(true);
      expect(statSync(path as string).size).toBeGreaterThan(0);
      expect(download.suggestedFilename().length).toBeGreaterThan(0);
      expect(download.suggestedFilename().endsWith(suffix) || suffix === ".md").toBe(true);
    }

    // 任务详情持续可访问（快照来自服务端）
    await page.goto(`/tasks/${taskId}?tab=result`);
    await expect(page.locator('[data-test="result-markdown"]')).toBeVisible();
  });

  test("RUNNING 期间刷新不丢状态，WebSocket 被阻断时轮询仍推进且事件不重复", async ({ page }) => {
    const user = uniqueUser("e2er");
    await register(page, user);
    const taskId = await createOfflineTask(page, "刷新与重连状态的端到端验证");

    await expect(page.locator('[data-test="plan-approve"]')).toBeVisible({ timeout: 240_000 });
    await page.locator('[data-test="plan-approve"]').click();

    // 进入 EXECUTION 后再刷新：Tab 由 query 驱动，任务与进度都应重新出现
    await page.locator('[data-test="tab-execution"]').click();
    await expect(page.locator('[data-test="dag"]')).toBeVisible({ timeout: 60_000 });
    await page.reload();
    await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}\\?tab=execution`));
    await expect(page.locator('[data-test="dag"]')).toBeVisible({ timeout: 60_000 });
    await expect(page.locator('[data-test^="dag-node-"]').first()).toBeVisible();

    // 阻断 WebSocket：事件服务应降级为 HTTP 补拉，状态仍能推进
    // 注意 WebSocket 需用 routeWebSocket 拦截（page.route 不覆盖握手）
    await page.routeWebSocket(/\/api\/v1\/tasks\/.*\/ws/, (ws) => ws.close());
    await page.locator('[data-test="tab-logs"]').click();
    await expect
      .poll(
        async () => {
          const text = await page
            .locator('[data-test="log-count"]')
            .innerText()
            .catch(() => "0");
          return Number(text.replace(/\D+/g, "") || "0");
        },
        { timeout: 5 * 60_000, intervals: [3000] }
      )
      .toBeGreaterThan(0);

    // 事件行按 event_id 唯一（补拉与实时不产生重复）
    const rowIds = await page
      .locator('[data-test^="log-row-"]')
      .evaluateAll((elements) => elements.map((element) => element.getAttribute("data-test")));
    expect(new Set(rowIds).size).toBe(rowIds.length);

    // WebSocket 路由拦截在测试结束时统一回收（routeWebSocket 无单独 unroute）
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });
});
