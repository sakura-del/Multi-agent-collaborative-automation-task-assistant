import { expect, test, type Page } from "@playwright/test";

// 套餐页 E2E（2026-10-26 重设计）：
// 数字必须与真实接口一致；权益/到期日/警示色/升级入口/加载态/局部失败都要验。
const API = "http://10.0.10.2:48080/api/v1";
const SHOTS = "e2e/screenshots";

async function loginAs(page: Page, account: string, password: string) {
  await page.goto("/login");
  await page.locator("#account").fill(account);
  await page.locator("#password").fill(password);
  await page.locator('form button[type="submit"]').click();
  await page.getByRole("link", { name: /任务/ }).waitFor({ timeout: 30_000 });
}

async function token(request: Page["request"], account: string, password: string) {
  const resp = await request.post(`${API}/auth/login`, { data: { account, password } });
  expect(resp.ok()).toBeTruthy();
  return (await resp.json()).access_token as string;
}

test("套餐页：当前套餐数字与真实接口一致", async ({ page, request }) => {
  const accessToken = await token(request, "demo", "demo12345");
  const usage = await (
    await request.get(`${API}/billing/usage`, { headers: { Authorization: `Bearer ${accessToken}` } })
  ).json();
  const plans = await (await request.get(`${API}/billing/plans`)).json();

  await loginAs(page, "demo", "demo12345");
  await page.goto("/settings/plan");
  await expect(page.locator('[data-test="plan-current"]')).toBeVisible({ timeout: 30_000 });

  // ① 当前套餐名 + 用量与接口一致
  const current = page.locator('[data-test="plan-current"]');
  await expect(current).toContainText(usage.plan_name);
  await expect(current).toContainText(String(usage.reviews_used));
  // 机构版不限篇数
  await expect(current).toContainText("不限");

  // ② 四项权益必须在当前卡里，且数值来自接口
  const entitlements = page.locator('[data-test="plan-entitlements"]');
  await expect(entitlements).toBeVisible();
  await expect(entitlements).toContainText("团队");
  await expect(entitlements).toContainText("API");
  await expect(entitlements).toContainText(String(usage.max_workspaces));
  await expect(entitlements).toContainText(String(usage.max_seats_per_workspace));

  // ③ 订阅到期日必须显示（与用量周期分开）
  const periodEnd = page.locator('[data-test="plan-period-end"]');
  await expect(periodEnd).toBeVisible();
  await expect(periodEnd).toContainText("到期");
  // 用量周期是独立元素
  await expect(page.locator('[data-test="plan-period-key"]')).toContainText(usage.period_key);

  // ④ 套餐卡数量与接口一致
  const cardCount = await page.locator('[data-test^="plan-card-"]').count();
  expect(cardCount).toBe(plans.length);

  await page.screenshot({ path: `${SHOTS}/plan-demo.png`, fullPage: true });
});

test("套餐页：免费版额度用尽的警示与升级入口", async ({ page, request }) => {
  const account = "e2efull";
  const password = "e2e-password-2026";
  const accessToken = await token(request, account, password);
  const usage = await (
    await request.get(`${API}/billing/usage`, { headers: { Authorization: `Bearer ${accessToken}` } })
  ).json();

  await loginAs(page, account, password);
  await page.goto("/settings/plan");
  await expect(page.locator('[data-test="plan-current"]')).toBeVisible({ timeout: 30_000 });

  if (usage.reviews_remaining === 0) {
    // ① 额度用尽 → 进度条必须是警示色（不是主色蓝）
    const bar = page.locator('[data-test="plan-progress-bar"]');
    await expect(bar).toBeVisible();
    const bg = await bar.evaluate((el) => getComputedStyle(el).backgroundColor);
    const primary = await page.evaluate(() => {
      const probe = document.createElement("div");
      probe.className = "bg-primary";
      probe.style.position = "absolute";
      document.body.appendChild(probe);
      const color = getComputedStyle(probe).backgroundColor;
      probe.remove();
      return color;
    });
    expect(bg, "额度用尽时进度条不能还是主色蓝").not.toBe(primary);

    // ② 升级引导里必须有可点的按钮（旧版只有一句话）
    const hint = page.locator('[data-test="plan-upgrade-hint"]');
    await expect(hint).toBeVisible();
    const action = hint.locator("button");
    await expect(action).toBeVisible();
    // 按钮可聚焦（键盘可达）
    await action.focus();
    const focused = await page.evaluate(
      () => document.activeElement?.getAttribute("data-test") ?? null
    );
    expect(focused).toBe("plan-upgrade-action");
  }

  // ③ 免费档到期日显示「免费档」，不编假日期
  await expect(page.locator('[data-test="plan-period-end"]')).toContainText("免费");

  await page.screenshot({ path: `${SHOTS}/plan-free.png`, fullPage: true });
});

test("套餐页：加载中有明确加载态（不是空白）", async ({ page }) => {
  await loginAs(page, "e2efull", "e2e-password-2026");

  await page.route(`${API}/billing/usage`, async (route) => {
    await new Promise((r) => setTimeout(r, 3000));
    await route.continue();
  });

  await page.goto("/settings/plan");
  await page.waitForTimeout(800);

  // 加载窗口内必须能看到加载态
  const loading = page.locator('[data-test="plan-loading"]');
  await expect(loading).toBeVisible();
  await expect(loading).toContainText("加载");

  // 加载完成后消失
  await expect(page.locator('[data-test="plan-current"]')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('[data-test="plan-loading"]')).toBeHidden();
});

test("套餐页：usage 失败不连累套餐列表", async ({ page }) => {
  await loginAs(page, "e2efull", "e2e-password-2026");

  await page.route(`${API}/billing/usage`, (route) =>
    route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"boom"}' })
  );

  await page.goto("/settings/plan");
  await page.waitForTimeout(2500);

  // 错误要报
  await expect(page.locator('[data-test="error-banner"]')).toBeVisible();
  // 但套餐列表必须还在（plans 接口成功了）
  const cardCount = await page.locator('[data-test^="plan-card-"]').count();
  expect(cardCount, "usage 失败不该把套餐列表一起清空").toBeGreaterThan(0);
  // 不能谎报「暂无套餐信息」
  await expect(page.locator('[data-test="plan-empty"]')).toHaveCount(0);

  await page.screenshot({ path: `${SHOTS}/plan-partial-failure.png`, fullPage: true });
});

test("套餐页：移动端无横向溢出", async ({ page }) => {
  await loginAs(page, "demo", "demo12345");
  await page.goto("/settings/plan");
  await expect(page.locator('[data-test="plan-current"]')).toBeVisible({ timeout: 30_000 });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  expect(overflow).toBeLessThanOrEqual(1);

  // 卡片在窄屏纵向堆叠
  const cards = await page.locator('[data-test^="plan-card-"]').evaluateAll((nodes) =>
    nodes.map((n) => Math.round((n as HTMLElement).getBoundingClientRect().x))
  );
  const uniqueX = new Set(cards);
  expect(uniqueX.size, "窄屏应纵向堆叠（同一列 x 相同）").toBe(1);

  await page.screenshot({ path: `${SHOTS}/plan-mobile.png`, fullPage: true });
});
