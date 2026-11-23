import { expect, test } from "@playwright/test";

// 公开页 UI 打磨（Issue #26）+ 2026-11-23 登录入口统一的浏览器级验收。
// 全部只读：不发验证码、不提交表单，不依赖任何账号。
//
// 运行：E2E_BASE_URL=http://10.0.10.2:48081 pnpm exec playwright test e2e/public-pages.spec.ts
test.describe("公开页（Issue #26）", () => {
  test("/register 分享链接仍直达统一入口，不再有独立注册表单", async ({ page }) => {
    await page.goto("/register");

    // 对外分享的注册链接不能被弹回 /login（#26-2）
    await expect(page).toHaveURL(/\/register$/);
    // 注册已并入邮箱验证码一体流：新邮箱首次验证自动建号（2026-11-23）
    await expect(page.locator('[data-test="auth-title"]')).toHaveText("登录或创建账号");
    await expect(page.locator('[data-test="auth-hint"]')).toContainText("新邮箱将自动建号");
    await expect(page.locator('[data-test="otp-email-input"]')).toBeVisible();
    // 旧的「用户名 + 邮箱 + 密码」注册表单已退役，不能只改文案却留着旧字段
    await expect(page.locator("#username")).toHaveCount(0);
  });

  test("默认是邮箱验证码入口，密码表单是兜底且可往返", async ({ page }) => {
    await page.goto("/login");

    await expect(page.locator('[data-test="auth-title"]')).toHaveText("登录或创建账号");
    await expect(page.locator('[data-test="otp-email-input"]')).toBeVisible();
    await expect(page.locator('[data-test="oauth-google-btn"]')).toBeVisible();
    await expect(page.locator('[data-test="oauth-github-btn"]')).toBeVisible();
    // 密码表单不是默认步：未切换前不该渲染 #account（否则用户会以为系统仍要求密码）
    await expect(page.locator("#account")).toHaveCount(0);

    await page.locator('[data-test="toggle-password-login"]').click();
    await expect(page.locator("#account")).toBeVisible();
    await expect(page.locator("#password")).toBeVisible();
    await expect(page.locator('[data-test="forgot-password"]')).toBeVisible();

    await page.locator('[data-test="back-to-otp"]').click();
    await expect(page.locator('[data-test="otp-email-input"]')).toBeVisible();
    await expect(page.locator("#account")).toHaveCount(0);
  });

  test("Logo 是链接，「证据可回溯」是标签而不是假链接", async ({ page }) => {
    await page.goto("/login");

    await expect(page.locator('a[data-test="brand-home"]').first()).toHaveAttribute("href", "/");

    const tag = page.locator('[data-test="proof-traceable-tag"]');
    await expect(tag).toHaveCount(1);
    // 示例卡片在左栏，窄屏整栏隐藏；可见性只在桌面视口要求
    if (test.info().project.name === "chromium") {
      await expect(tag).toBeVisible();
    }
    expect(await tag.evaluate((el) => el.tagName)).toBe("SPAN");
    expect(await tag.evaluate((el) => el.closest("a") === null)).toBe(true);
    expect(await tag.evaluate((el) => getComputedStyle(el).cursor)).not.toBe("pointer");
  });

  test("中文文案没有逗号后空格", async ({ page }) => {
    await page.goto("/login");

    // 用 textContent 而不是 innerText：`<br>` 在 innerText 里是换行，
    // 会被 /，\s/ 误判成「逗号后空格」（这正是巡检报告里标题那条的来源）。
    const text = (await page.locator("body").textContent()) ?? "";
    expect(text).not.toMatch(/[\u4e00-\u9fa5]，\s/);
    expect(text).toContain("你负责判断，系统负责");
  });

  test("重置密码页有品牌页头与「返回登录」入口", async ({ page }) => {
    await page.goto("/password-reset");

    await expect(page.getByRole("heading", { name: "重置密码" })).toBeVisible();
    await expect(page.locator('a[aria-label="VeriPaper 首页"]')).toBeVisible();

    const back = page.locator('[data-test="reset-back-to-login"]');
    await expect(back).toBeVisible();
    await back.click();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('[data-test="auth-title"]')).toHaveText("登录或创建账号");
  });
});
