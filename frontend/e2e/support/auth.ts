import type { Page } from "@playwright/test";

/**
 * 进入「账号密码」登录态。
 *
 * 2026-11-23 起登录页默认是「邮箱验证码 / 一键登录链接」一体流
 * （`frontend/src/views/LoginView.vue` 的 `step` 初始值 = "email"）；
 * `#account` / `#password` / `[data-test="forgot-password"]` 只在密码态渲染，
 * 需要点底部的「使用账号密码登录」（`[data-test="toggle-password-login"]`）切换。
 *
 * E2E 账号是用户名 + 密码，隔离栈也没有可收信的邮箱，所以每条用例都必须先走这里。
 * 把这个前提收在一处：登录页再改版时只需改这一个文件，而不是二十个 spec。
 */
export async function gotoPasswordLogin(page: Page): Promise<void> {
  await page.goto("/login");
  const toggle = page.locator('[data-test="toggle-password-login"]');
  await toggle.waitFor({ state: "visible", timeout: 30_000 });
  await toggle.click();
  await page.locator("#account").waitFor({ state: "visible", timeout: 10_000 });
}
