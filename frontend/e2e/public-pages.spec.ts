import { expect, test } from "@playwright/test";

// 公开页 UI 打磨（Issue #26）的浏览器级验收。
// 全部只读：不注册、不提交表单，不依赖任何账号。
//
// 运行：E2E_BASE_URL=http://10.0.10.2:48081 pnpm exec playwright test e2e/public-pages.spec.ts
test.describe("公开页（Issue #26）", () => {
  test("/register 直达注册表单，顶部文案与注册意图一致", async ({ page }) => {
    await page.goto("/register");

    // 对外分享的注册链接不能被弹回 /login（#26-2）
    await expect(page).toHaveURL(/\/register$/);
    await expect(page.locator('[data-test="auth-eyebrow"]')).toHaveText("Create account");
    await expect(page.locator('[data-test="auth-title"]')).toHaveText("创建你的账号");
    await expect(page.locator('[data-test="auth-hint"]')).toContainText("第一篇综述");

    // 注册字段有占位提示，与登录页一致（#26-6）
    await expect(page.locator("#username")).toBeVisible();
    await expect(page.locator("#username")).toHaveAttribute("placeholder", /.+/);
    await expect(page.locator("#email")).toHaveAttribute("placeholder", "you@example.com");
  });

  test("页签与地址栏同步，两种态的文案不串台", async ({ page }) => {
    await page.goto("/login");
    await expect(page.locator('[data-test="auth-title"]')).toHaveText("继续你的研究");

    await page.getByRole("tab", { name: "注册" }).click();
    await expect(page).toHaveURL(/\/register$/);
    await expect(page.locator('[data-test="auth-title"]')).toHaveText("创建你的账号");
    await expect(page.locator('[data-test="auth-eyebrow"]')).toHaveText("Create account");

    await page.getByRole("tab", { name: "登录" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.locator('[data-test="auth-title"]')).toHaveText("继续你的研究");
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

  test("注册页人机验证启用时，保护文案由我们自己给且只有这一处", async ({ page }) => {
    // 拦截探测端点，让 ALTCHA 分支真正渲染（隔离栈默认可能没开 CAPTCHA）：
    // 断言的是「我们自己的那行文案」。widget 自带页脚在中文包里写作「由 ALTCHA 保护」，
    // 已通过 configuration.hideFooter 关掉；关不掉时这条计数会变成 2（已复现过）。
    // 注意：mock 回的是 {enabled:true} 而不是合法挑战，widget 会显示「验证失败」——
    // 这正好也证明：即使 widget 报错，也不能多出一行保护文案。
    await page.route("**/api/v1/auth/captcha/challenge", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ enabled: true }),
      }),
    );

    await page.goto("/register");

    await expect(page.locator("altcha-widget")).toHaveCount(1);
    await expect(page.locator('[data-test="captcha-caption"]')).toHaveText("由 Altcha 保护");
    const text = (await page.locator("body").textContent()) ?? "";
    expect(text.match(/altcha/gi)?.length ?? 0).toBe(1);
  });

  test("重置密码页有品牌页头与「返回登录」入口", async ({ page }) => {
    await page.goto("/password-reset");

    await expect(page.getByRole("heading", { name: "重置密码" })).toBeVisible();
    await expect(page.locator('a[aria-label="VeriPaper 首页"]')).toBeVisible();

    const back = page.locator('[data-test="reset-back-to-login"]');
    await expect(back).toBeVisible();
    await back.click();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('[data-test="auth-title"]')).toHaveText("继续你的研究");
  });
});
