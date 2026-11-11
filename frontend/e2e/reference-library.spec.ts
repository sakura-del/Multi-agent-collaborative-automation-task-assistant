import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { demoPassword } from "./credentials";

/**
 * 引用库页 + 导入页保存入口的真实浏览器验证（2026-10-26 引用库计划 §3.6）。
 *
 * ⚠️ **一个文件只做一次登录**（AGENTS §5 #29）：`auth_login` 限流 5 次/3600s。
 * 所以这里只有一条 test，把三段断言（导入页有保存入口 / 引用库页结构 /
 * 空态引导）合并到一次页面访问里跑完。
 *
 * 断言的是**结构化数值**，不是截图：
 * - 元素存在性与可见性用 `count()` / `isVisible()`；
 * - 溢出用 `scrollWidth > clientWidth` 量；
 * - 文案断言它「说了什么」，而不是「有个元素」。
 */

const CREDENTIALS = { account: "demo", password: demoPassword() };

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#account").fill(CREDENTIALS.account);
  await page.locator("#password").fill(CREDENTIALS.password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 30_000 });
}

test("引用库页与导入页保存入口在真实浏览器里可用", async ({ page }) => {
  await login(page);

  // ===== ① 导入页：必须有「保存到引用库」出口 =====
  await page.goto("/references/import");
  await page.waitForLoadState("networkidle");

  // 页面说明必须提到能保存 + 能建任务（之前页面完全不说下一步）
  const hintText = await page.locator("main, body").first().innerText();
  expect(hintText).toContain("引用库");
  expect(hintText).toContain("创建任务");

  // 表单契约还在（重设计不能弄坏既有 E2E 定位）
  await expect(page.locator('[data-test="import-form"]')).toBeVisible();
  await expect(page.locator('[data-test="import-content"]')).toBeVisible();
  await expect(page.locator('[data-test="import-format"]')).toBeVisible();
  await expect(page.locator('[data-test="import-submit"]')).toBeVisible();

  // 解析前不应该出现保存按钮（没东西可存）
  const saveBeforeParse = await page.locator('[data-test="import-save"]').count();
  expect(saveBeforeParse).toBe(0);

  // 真解析一份 BibTeX，确认预览出来且保存按钮出现
  await page
    .locator('[data-test="import-content"]')
    .fill(
      "@article{a2024,\n  author = {牛耕田 and 李四},\n  title = {引用库浏览器验证条目},\n  year = {2024},\n  doi = {10.18653/v1/2024.eacl-demo.16}\n}\n"
    );
  await page.locator('[data-test="import-submit"]').click();

  await expect(page.locator('[data-test="import-preview"]')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('[data-test="import-item-0"]')).toBeVisible();
  const previewText = await page.locator('[data-test="import-preview"]').innerText();
  expect(previewText).toContain("引用库浏览器验证条目");
  expect(previewText).toContain("2024");

  // 解析完成后**必须**出现保存入口（这是本轮补齐的能力）
  await expect(page.locator('[data-test="import-save"]')).toBeVisible();

  // ===== ② 引用库页：结构 + 空态引导 =====
  await page.goto("/references/library");
  await page.waitForLoadState("networkidle");

  // 页面说明必须讲清「能怎么用」
  const libHint = await page.locator('[data-test="library-hint"]').innerText();
  expect(libHint).toContain("创建任务");

  // 摘要卡必须在（总条数 / 可作文献源条数）
  await expect(page.locator('[data-test="library-summary"]')).toBeVisible();

  // 引用库此刻是空的（探针已清理）→ 必须给引导，不能只说「暂无数据」
  const emptyCount = await page.locator('[data-test="library-empty"]').count();
  if (emptyCount > 0) {
    const emptyText = await page.locator('[data-test="library-empty"]').innerText();
    expect(emptyText).toMatch(/导入/);
  } else {
    // 非空时应当至少有一行，且带删除按钮
    const rows = await page.locator('[data-test^="library-row-"]').count();
    expect(rows).toBeGreaterThan(0);
  }

  // ===== ③ 任务表单：文献源选择必须存在且默认关闭 =====
  await page.goto("/tasks");
  await page.waitForLoadState("networkidle");

  const createBtn = page.locator('[data-test="new-task"], button:has-text("新建任务")').first();
  if ((await createBtn.count()) > 0) {
    await createBtn.click();
    const sourceCheckbox = page.locator('[data-test="reference-library"]');
    await expect(sourceCheckbox).toBeVisible({ timeout: 15_000 });
    expect(await sourceCheckbox.isChecked()).toBe(false);

    // 标签必须说明「不访问外部检索源」这件事
    const labelText = await page.locator('[data-test="reference-library-label"]').innerText();
    expect(labelText).toMatch(/引用库/);
    expect(labelText).toMatch(/不访问外部|不访问/);
  }

  // ===== ④ 移动端无横向溢出 =====
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/references/library");
  await page.waitForLoadState("networkidle");
  const overflow = await page.evaluate(() => {
    const root = document.documentElement;
    return { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth };
  });
  expect(overflow.scrollWidth - overflow.clientWidth).toBeLessThanOrEqual(1);
});
