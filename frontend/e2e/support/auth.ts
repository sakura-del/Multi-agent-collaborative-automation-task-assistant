import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { apiBase } from "./env";

export interface TokenPair {
  access_token: string;
  refresh_token: string;
}

const tokenCache = new Map<string, TokenPair>();

/**
 * 进入「账号密码」登录态。
 *
 * 2026-11-23 起登录页默认是「邮箱验证码 / 一键登录链接」一体流
 * （`frontend/src/views/LoginView.vue` 的 `step` 初始值 = "email"）；
 * `#account` / `#password` / `[data-test="forgot-password"]` 只在密码态渲染，
 * 需要点底部的「使用账号密码登录」（`[data-test="toggle-password-login"]`）切换。
 */
export async function gotoPasswordLogin(page: Page): Promise<void> {
  await page.goto("/login");
  const toggle = page.locator('[data-test="toggle-password-login"]');
  await toggle.waitFor({ state: "visible", timeout: 30_000 });
  await toggle.click();
  await page.locator("#account").waitFor({ state: "visible", timeout: 10_000 });
}

/**
 * 通过 UI 密码表单登录，以 `waitForURL(/\/tasks/)` 为唯一成功信号（兼容桌面与 mobile-chrome），
 * 并内置 429 限流退避重试。
 */
export async function loginViaUi(page: Page, account: string, password: string): Promise<void> {
  await gotoPasswordLogin(page);
  await page.locator("#account").fill(account);
  await page.locator("#password").fill(password);
  await page.locator('form button[type="submit"]').click();

  for (let attempt = 0; attempt < 8; attempt += 1) {
    const settled = await Promise.race([
      page
        .waitForURL(/\/tasks/, { timeout: 12_000 })
        .then(() => "ok" as const)
        .catch(() => null),
      page
        .locator("text=请求过于频繁")
        .waitFor({ state: "visible", timeout: 12_000 })
        .then(() => "limited" as const)
        .catch(() => null),
    ]);
    if (settled === "ok") {
      return;
    }
    if (settled === "limited") {
      await page.waitForTimeout(8_000);
      await page.locator('form button[type="submit"]').click().catch(() => undefined);
      continue;
    }
    const alertText = await page
      .locator('[data-test="form-error"], [role="alert"]')
      .first()
      .textContent()
      .catch(() => null);
    if (alertText && !alertText.includes("请求过于频繁")) {
      throw new Error(`账号 ${account} UI 登录失败：${alertText.trim()}`);
    }
  }
  await expect(page).toHaveURL(/\/tasks/, { timeout: 30_000 });
}

/**
 * 通过 API 登录并缓存 token（同一 worker 内每账号只调 1 次 /auth/login）。
 */
export async function fetchAuthToken(
  request: APIRequestContext,
  account: string,
  password: string,
): Promise<TokenPair> {
  const cacheKey = `${apiBase()}::${account}`;
  const cached = tokenCache.get(cacheKey);
  if (cached) {
    return cached;
  }

  for (let attempt = 0; attempt < 6; attempt += 1) {
    const resp = await request.post(`${apiBase()}/auth/login`, {
      data: { account, password },
    });
    if (resp.ok()) {
      const json = (await resp.json()) as { access_token: string; refresh_token?: string };
      const pair: TokenPair = {
        access_token: json.access_token,
        refresh_token: json.refresh_token ?? "",
      };
      tokenCache.set(cacheKey, pair);
      return pair;
    }
    if (resp.status() === 429) {
      await new Promise((r) => setTimeout(r, 8_000));
      continue;
    }
    throw new Error(`API 登录失败 (${account}): HTTP ${resp.status()} ${await resp.text()}`);
  }
  throw new Error(`API 登录连续触发 429 限流 (${account})`);
}

/**
 * 通过 API 获取 JWT 并写入当前页面的 `localStorage`，同时清理 `veripaper_user` 缓存
 * 以避免跨账号测试串台。
 *
 * 注意：直接导航到同源路径（若尚未在同源域下则先 visit `/login`）后写入 `localStorage`，
 * 避免使用全局持久 `addInitScript` 污染后续切账号或未登录测试。
 */
export async function seedAuthByApi(
  page: Page,
  request: APIRequestContext,
  account: string,
  password: string,
  targetPath = "/tasks",
): Promise<TokenPair> {
  const tokens = await fetchAuthToken(request, account, password);

  const currentUrl = page.url();
  if (!currentUrl || currentUrl === "about:blank") {
    await page.goto("/login", { waitUntil: "domcontentloaded" });
  }

  await page.evaluate(
    ({ access, refresh }) => {
      localStorage.removeItem("veripaper_user");
      localStorage.setItem("access_token", access);
      if (refresh) {
        localStorage.setItem("refresh_token", refresh);
      } else {
        localStorage.removeItem("refresh_token");
      }
    },
    { access: tokens.access_token, refresh: tokens.refresh_token },
  );

  // 显式重新加载触发 auth.bootstrap()，使 Pinia store 同步拉取当前账号的 /auth/me
  await page.goto(targetPath, { waitUntil: "domcontentloaded" });
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 30_000 });
  return tokens;
}

/**
 * 清理当前页面登录态（供未登录/公开页测试复用同一 page 时防串台）。
 */
export async function clearAuthSession(page: Page): Promise<void> {
  const currentUrl = page.url();
  if (!currentUrl || currentUrl === "about:blank") {
    return;
  }
  await page.evaluate(() => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    localStorage.removeItem("veripaper_user");
  });
}
