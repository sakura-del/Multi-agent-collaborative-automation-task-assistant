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
  await expect(page.getByRole("link", { name: /任务/ })).toBeVisible({ timeout: 30_000 });
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
    await page.goto("/password-reset");
    // 已注册邮箱
    await page.locator('[data-test="reset-email"]').fill("demo@example.com");
    await page.locator('[data-test="reset-request-submit"]').click();
    const banner = page.locator('[data-test="reset-requested"]');
    await expect(banner).toBeVisible({ timeout: 30_000 });
    const existingText = await banner.textContent();

    // 未注册邮箱：必须得到**完全相同**的文案（防账号枚举）
    await page.goto("/password-reset");
    await page.locator('[data-test="reset-email"]').fill(`nobody-${Date.now()}@example.com`);
    await page.locator('[data-test="reset-request-submit"]').click();
    await expect(banner).toBeVisible({ timeout: 30_000 });
    const unknownText = await banner.textContent();

    expect(unknownText).toBe(existingText);
    expect(existingText).toContain("如果该邮箱已注册");

    await page.screenshot({
      path: resolve(SHOTS, `account-reset-${test.info().project.name}.png`),
      fullPage: true,
    });
  });

  test("密码重置申请：非法邮箱前端先拦", async ({ page }) => {
    await page.goto("/password-reset");
    await page.locator('[data-test="reset-email"]').fill("not-an-email");
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
    // 后端返回「已发送」或「已验证」，两者都是成功路径
    await expect(
      page.locator('[data-test="verify-message"], [data-test="verify-success"]')
    ).toBeVisible({ timeout: 30_000 });

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
