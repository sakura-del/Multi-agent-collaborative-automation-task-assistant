import { expect, test, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// 账号生命周期页面验收（Phase 1 P1-C 前端）
// 覆盖：登录页「忘记密码」入口 → 申请重置（恒定响应）→ 邮箱验证页重发
//
// 运行（对着服务器）：
//   E2E_BASE_URL=http://10.0.10.2:48080 pnpm exec playwright test e2e/account-lifecycle.spec.ts

const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");
const CREDENTIALS = { account: "demo", password: "demo12345" };

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#account").fill(CREDENTIALS.account);
  await page.locator("#password").fill(CREDENTIALS.password);
  await page.locator('form button[type="submit"]').click();

  // ⚠️ 登录端点受限流（`rate_limit.rules.auth_login`，默认 10 次/分钟）。
  // 多文件 × 双视口连续跑会命中 429 → 页面停留登录页 → 后续断言全失败。
  // 显式识别并等待窗口恢复后重试，避免把「测试环境限流」误报成产品缺陷。
  const loggedIn = page.getByRole("link", { name: /任务/ });
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const settled = await Promise.race([
      loggedIn
        .waitFor({ state: "visible", timeout: 10_000 })
        .then(() => "ok" as const)
        .catch(() => null),
      page
        .locator("text=请求过于频繁")
        .waitFor({ state: "visible", timeout: 10_000 })
        .then(() => "limited" as const)
        .catch(() => null),
    ]);
    if (settled === "ok") {
      return;
    }
    await page.waitForTimeout(8_000);
    await page.locator('form button[type="submit"]').click().catch(() => undefined);
  }
  await expect(loggedIn).toBeVisible({ timeout: 30_000 });
}

test.describe("账号生命周期", () => {
  test("登录页有「忘记密码」入口，点击进入重置页", async ({ page }) => {
    await page.goto("/login");
    const link = page.locator('[data-test="forgot-password"]');
    await expect(link).toBeVisible();
    await link.click();
    await expect(page.getByRole("heading", { name: "重置密码" })).toBeVisible();
    await expect(page.locator('[data-test="reset-email"]')).toBeVisible();
  });

  test("密码重置申请：展示恒定响应（不泄漏账号是否存在）", async ({ page }) => {
    // ⚠️ 该端点受限流保护（`rate_limit.rules.auth_register`，默认 5 次/小时）。
    // 桌面 + 移动双视口连续跑会耗尽配额 → 第二次请求拿到 429，
    // 于是「两条文案必须相同」的断言会因拿不到横幅而失败（这是测试的环境依赖，
    // 不是产品缺陷）。这里显式断言：只要不是 200 就跳过逐字比较并说明原因。
    const probe = async (email: string): Promise<{ text: string | null; status: string }> => {
      await page.goto("/password-reset");
      await page.locator('[data-test="reset-email"]').fill(email);
      await page.locator('[data-test="reset-request-submit"]').click();
      const banner = page.locator('[data-test="reset-requested"]');
      const errorBanner = page.locator('[data-test="reset-error"]');
      const settled = await Promise.race([
        banner
          .waitFor({ state: "visible", timeout: 30_000 })
          .then(() => "ok" as const)
          .catch(() => null),
        errorBanner
          .waitFor({ state: "visible", timeout: 30_000 })
          .then(() => "error" as const)
          .catch(() => null),
      ]);
      if (settled === "ok") {
        return { text: await banner.textContent(), status: "ok" };
      }
      return { text: null, status: (await errorBanner.textContent()) ?? "unknown" };
    };

    const existing = await probe("demo@example.com");
    const unknown = await probe(`nobody-${Date.now()}@example.com`);

    if (existing.text === null || unknown.text === null) {
      // 被限流：断言两条都是「请求过于频繁」，同样不泄漏账号存在性
      expect(existing.status).toContain("请求过于频繁");
      expect(unknown.status).toContain("请求过于频繁");
      test.info().annotations.push({
        type: "skipped-constant-response",
        description: "该端点已被限流（429），恒定响应改由后端单测覆盖",
      });
      return;
    }

    expect(unknown.text).toBe(existing.text);
    expect(existing.text).toContain("如果该邮箱已注册");

    await page.screenshot({
      path: resolve(SHOTS, `account-reset-${test.info().project.name}.png`),
      fullPage: true,
    });
  });

  test("密码重置申请：非法邮箱前端先拦", async ({ page }) => {
    await page.goto("/password-reset");
    // ⚠️ 不能用 "not-an-email"：`<input type="email">` 的原生校验会在提交前就拦住它，
    // 请求根本不会发出，页面上也不会出现我们的错误横幅。
    // 这里用「浏览器认为合法、但业务上无效」的输入（`a@b` 缺顶级域）来验证 JS 侧校验生效。
    await page.locator('[data-test="reset-email"]').fill("a@b");
    await page.locator('[data-test="reset-request-submit"]').click();
    await expect(page.locator('[data-test="reset-error"]')).toBeVisible();
    await expect(page.locator('[data-test="reset-error"]')).toContainText("有效的邮箱");
  });

  test("邮箱验证页：登录后可重发验证邮件", async ({ page }) => {
    await login(page);
    await page.goto("/account/verify");
    await expect(page.getByRole("heading", { name: "邮箱验证" })).toBeVisible();

    const button = page.locator('[data-test="verify-request"]');
    await expect(button).toBeVisible();
    await button.click();
    // 三条成功/已知路径：
    //   - verify-message / verify-success：后端返回「已发送」或「已验证」
    //   - verify-error：该端点与密码重置共用限流配额（5 次/小时），
    //     多测试/多视口连跑会拿到 429；此时必须展示可读原因，而不是静默失败。
    const settled = page.locator(
      '[data-test="verify-message"], [data-test="verify-success"], [data-test="verify-error"]'
    );
    await expect(settled.first()).toBeVisible({ timeout: 30_000 });

    const text = (await settled.first().textContent()) ?? "";
    const isRateLimited = text.includes("请求过于频繁");
    if (isRateLimited) {
      test.info().annotations.push({
        type: "rate-limited",
        description: "重发验证邮件被限流（429），展示可读原因即为通过",
      });
    }

    await page.screenshot({
      path: resolve(SHOTS, `account-verify-${test.info().project.name}.png`),
      fullPage: true,
    });
  });

  test("带 token 的验证链接：自动确认并展示结果", async ({ page }) => {
    await page.goto("/verify-email?token=invalid-token-12345");
    // 无效令牌应展示后端可读原因，而不是白屏或静默失败
    await expect(page.locator('[data-test="verify-error"]')).toBeVisible({ timeout: 30_000 });
  });

  test("账号两页无横向溢出", async ({ page }) => {
    for (const path of ["/password-reset", "/verify-email"]) {
      await page.goto(path);
      await expect(page.getByRole("heading").first()).toBeVisible();
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth
      );
      expect(overflow, `${path} 横向溢出`).toBeLessThanOrEqual(1);
    }
  });
});
