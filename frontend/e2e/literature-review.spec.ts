import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { existsSync, statSync } from "node:fs";
import { secondaryPassword } from "./credentials";

// 文献综述全流程验收（Plan 4 Task 8 / M4）：
// 注册 → 上传知识库文档 → 建离线任务 → 确认规划 → 观察 DAG 状态变化 → 完成 → 校验引用 → 下载两种产物
// 刷新与重连：RUNNING 期间刷新不丢状态；阻断 WebSocket 后轮询仍推进且不产生重复事件。

const FIXTURE = resolve(dirname(fileURLToPath(import.meta.url)), "fixtures/sample.txt");
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");

/**
 * 整个工作流的超时。
 *
 * 2026-10-21 实测：跑服务器的真实模型流程（12 个节点）需要 **13 分钟以上**
 * （服务器 CPU 约为本机 1/28，见 AGENTS.md §4.2），原 12 分钟不够——
 * 用例在"等结果 Tab"处超时，但任务在服务器上其实已完成（completed）。
 * 提到 25 分钟：既覆盖慢机器，又不会让真正的卡死无限等待。
 */
const WORKFLOW_TIMEOUT = 25 * 60_000;

/**
 * 各阶段的内层等待上限（按 2026-10-21 服务器实测校准）。
 *
 * 实测数据（12 节点、离线语料、服务器真实模型）：
 * - 规划：约 1.5 分钟
 * - 执行（12 节点串并混合）：约 10 分钟，单节点 30s ~ 3min
 * - 复盘：约 2 分钟
 *
 * 原来的 10 分钟内层超时比执行阶段本身就短 → 用例必然超时，
 * 即使任务在服务器上正常推进（不是产品缺陷，是阈值设错）。
 */
const PLANNING_TIMEOUT = 5 * 60_000;
const EXECUTION_TIMEOUT = 20 * 60_000;
const STEP_TIMEOUT = 2 * 60_000;

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

/**
 * E2E 专用账号（**固定**，不每次新建）。
 *
 * 为什么不再 `uniqueUser()`：`auth_register` 是 5 次/3600s 的限流端点，
 * 而本 spec 有 2 个用例 × 2 个视口 = 4 次注册/轮次，连跑两轮就必然撞墙
 * （2026-10-21 实测：第二轮全部卡在登录页，`RATE_LIMITED remaining=0`）。
 * 重试也救不了 —— 窗口是 1 小时。
 *
 * 策略：**能登录就登录，登不进才注册**。首次运行时建账号，之后复用。
 * 两个用例用不同账号，避免互相干扰任务列表与知识库内容。
 */
const E2E_PASSWORD = secondaryPassword();
const E2E_USERS = {
  full: { username: "e2efull", email: "e2efull@example.com", password: E2E_PASSWORD },
  reconnect: { username: "e2ereconn", email: "e2ereconn@example.com", password: E2E_PASSWORD },
} as const;

type E2EUser = { username: string; email: string; password: string };

/**
 * 确保已登录：先试登录，账号不存在（或首次运行）才走注册。
 *
 * @returns `"login"` 表示复用既有账号（未消耗注册限流额度），`"register"` 表示本次新建。
 */
async function ensureSignedIn(page: Page, user: E2EUser): Promise<"login" | "register"> {
  await page.goto("/login");
  await page.locator("#account").fill(user.username);
  await page.locator("#password").fill(user.password);
  await page.getByRole("button", { name: "登录", exact: true }).click();

  const outcome = await Promise.race([
    page
      .waitForURL(/\/tasks/, { timeout: 20_000 })
      .then(() => "ok" as const)
      .catch(() => null),
    page
      .locator('[data-test="form-error"], [role="alert"]')
      .first()
      .waitFor({ state: "visible", timeout: 20_000 })
      .then(() => "failed" as const)
      .catch(() => null),
  ]);
  if (outcome === "ok") return "login";

  // 登录失败（账号不存在 / 密码不符 / 被限流）→ 走注册路径
  // 登录/注册切换在 M2 设计系统迁移后是 shadcn Tabs（role="tab"），不是 button。
  // 曾用 `getByRole("button", { name: "注册" })` → 迁移后该用例静默失效（找不到元素）。
  await page.goto("/login");
  await page.getByRole("tab", { name: "注册" }).click();
  await page.locator("#username").fill(user.username);
  await page.locator("#email").fill(user.email);
  await page.locator("#password").fill(user.password);
  await page.getByRole("button", { name: "注册并登录" }).click();

  // 注册仍可能被限流（窗口 3600s，重试无意义）→ 给出**可读的失败原因**，
  // 而不是含糊的 `toHaveURL` 超时，避免下次又把限流误判成产品缺陷。
  const registered = await page
    .waitForURL(/\/tasks/, { timeout: 30_000 })
    .then(() => true)
    .catch(() => false);
  if (!registered) {
    const alertText = await page
      .locator('[data-test="form-error"], [role="alert"]')
      .first()
      .textContent()
      .catch(() => null);
    throw new Error(
      `E2E 账号 ${user.username} 登录与注册均失败` +
        `${alertText ? `（页面提示：${alertText.trim()}）` : ""}。` +
        "若是「请求过于频繁」，说明 auth_register 限流（5 次/3600s）已用尽：" +
        "等窗口恢复，或在服务器上预建该账号。"
    );
  }
  return "register";
}

async function uploadFixture(page: Page, title: string) {
  await page.goto("/knowledge");
  await page.locator('[data-test="document-file"]').setInputFiles(FIXTURE);
  await page.locator('[data-test="document-title"]').fill(title);
  await page.locator('[data-test="document-submit"]').click();
  await expect(page.locator('[data-test="document-table"]')).toContainText(title, { timeout: STEP_TIMEOUT });
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
    await ensureSignedIn(page, E2E_USERS.full);
    await uploadFixture(page, "端到端验收样本文档");

    const taskId = await createOfflineTask(page, "检索增强生成与多智能体协作的质量评估");
    await page.goto("/tasks");
    await capture(page, "1-task-list", project);
    await expectNoHorizontalOverflow(page);
    await page.goto(`/tasks/${taskId}`);

    // 规划确认：等规划生成完毕（规划是异步的），再确认当前版本
    // Issue #2 回归：这里**全程不得 reload**——规划就绪必须由事件驱动自动呈现。
    // 旧缺陷下快照 status 已是 waiting_approval 但 plan 为 null，plan_ready 事件被
    // 「状态未变化」守卫跳过，只能手动 F5 才能看到规划。
    const navigationCount = await page.evaluate(
      () => performance.getEntriesByType("navigation").length
    );
    await expect(page.locator('[data-test="plan-approve"]')).toBeVisible({
      timeout: PLANNING_TIMEOUT,
    });
    // 断言期间没有发生整页重载（性能条目仍是首次导航）
    expect(
      await page.evaluate(() => performance.getEntriesByType("navigation").length)
    ).toBe(navigationCount);
    await expect(page.locator('[data-test="plan-panel"]')).toBeVisible();
    await capture(page, "2-plan-approval", project);
    await expectNoHorizontalOverflow(page);
    await page.locator('[data-test="plan-approve"]').click();

    // 执行 Tab：至少观察到一次节点状态变化
    await page.locator('[data-test="tab-execution"]').click();
    await expect(page.locator('[data-test="dag"]')).toBeVisible({ timeout: STEP_TIMEOUT });
    const nodes = page.locator('[data-test^="dag-node-"]');
    await expect(nodes.first()).toBeVisible();
    const initialLabels = await nodes.evaluateAll((elements) =>
      elements.map((element) => element.getAttribute("aria-label") ?? "")
    );
    await expect
      .poll(
        async () =>
          nodes.evaluateAll((elements) => elements.map((element) => element.getAttribute("aria-label") ?? "")),
        { timeout: EXECUTION_TIMEOUT, intervals: [2000] }
      )
      .not.toEqual(initialLabels);
    await capture(page, "3-running-dag", project);
    await expectNoHorizontalOverflow(page);

    // 状态变化不得改变节点几何（Plan 4 Exit Gate 的硬性要求）
    const firstNodeStyle = await nodes.first().getAttribute("style");
    await capture(page, "4-human-action", project);
    expect(await nodes.first().getAttribute("style")).toBe(firstNodeStyle);

    // 完成后结果 Tab 可用，且正文含引用标记
    // 超时给到执行阶段上限：这个断言实际覆盖的是「等整条流水线跑完」
    await expect(page.locator('[data-test="tab-result"]')).toBeEnabled({
      timeout: EXECUTION_TIMEOUT,
    });
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
    await ensureSignedIn(page, E2E_USERS.reconnect);
    const taskId = await createOfflineTask(page, "刷新与重连状态的端到端验证");

    await expect(page.locator('[data-test="plan-approve"]')).toBeVisible({
      timeout: PLANNING_TIMEOUT,
    });
    await page.locator('[data-test="plan-approve"]').click();

    // 进入 EXECUTION 后再刷新：Tab 由 query 驱动，任务与进度都应重新出现
    await page.locator('[data-test="tab-execution"]').click();
    await expect(page.locator('[data-test="dag"]')).toBeVisible({ timeout: STEP_TIMEOUT });
    await page.reload();
    await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}\\?tab=execution`));
    await expect(page.locator('[data-test="dag"]')).toBeVisible({ timeout: STEP_TIMEOUT });
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
        { timeout: EXECUTION_TIMEOUT, intervals: [3000] }
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
