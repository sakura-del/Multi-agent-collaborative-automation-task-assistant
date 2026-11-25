import { expect, test, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { demoCreds } from "./support/env";
import { seedAuthByApi } from "./support/auth";

// M2 迁移后的视觉验收截图
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: resolve(SHOTS, `m2-${name}-${test.info().project.name}.png`),
    fullPage: true,
  });
}

test("M2 视觉验收：登录 + 任务列表", async ({ page, request }) => {
  // 截「登录页」要的是默认入口（邮箱验证码一体流），不是密码兜底态
  await page.goto("/login");
  await expect(page.locator('[data-test="auth-title"]')).toBeVisible();
  await shot(page, "login");

  const { account, password } = demoCreds();
  await seedAuthByApi(page, request, account, password, "/tasks");
  // 等列表真正渲染出来再截图：登录成功 ≠ 列表加载完（loadList 是异步的，
  // 早截图会拍到「加载中…」——我第一次就踩到这个，误以为页面坏了）
  await expect(page.locator('[data-test^="task-row-"]').first()).toBeVisible({
    timeout: 30_000,
  });
  await shot(page, "tasks");

  // 新建任务表单展开态
  await page.locator('[data-test="new-task"]').click();
  await page.waitForTimeout(400);
  await shot(page, "task-form");
});
