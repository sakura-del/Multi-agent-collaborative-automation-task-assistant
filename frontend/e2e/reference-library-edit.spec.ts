import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { demoPassword } from "./credentials";

/**
 * 引用库条目编辑的真实浏览器验证（2026-10-27）。
 *
 * 背景：编辑功能上一轮只有单元测试，浏览器层完全未验证（交接 §3.8 验证限制）。
 * 单测能证明「调用了 updateLibraryItem」，但证明不了：
 * - 点铅笔按钮真的能打开表单、且**预填当前值**；
 * - 改完点保存，**列表上的字真的变了**（不是只有内存 ref 变了）；
 * - 刷新后仍在（说明真的落库了，不是前端乐观更新）；
 * - 后端准入检查真的把非法值拦住了，并且**原因展示给用户**。
 *
 * ⚠️ **一个文件只做一次登录**（AGENTS §5 #29）：`auth_login` 限流 5 次/3600s。
 * 所以这里只有一条 test，把五个场景合并到一次页面访问里跑完。
 *
 * ## 写这条 E2E 时踩到的三个坑（都实测踩过）
 *
 * 1. **`page.request` 不会自动带鉴权**：本项目 token 存 `localStorage.access_token`
 *    （见 `src/api/client.ts`），**不是 cookie** → 直接打接口 401。
 *    必须 `page.evaluate` 读出 token 再注入 `Authorization`。
 * 2. **造数据前必须先清空**：后端按归一化 DOI 去重合并，
 *    上次运行留下的同 DOI 条目会让新条目变成 `merged`（created=0），
 *    于是按题名找不到 → 测试自己把自己搞挂。
 * 3. **不能写 `not.toContain(旧题名)`**：新题名是 `${旧题名}-已改`，
 *    **旧题名是新题名的前缀**，这条断言永远失败。要验「整体替换」应断言
 *    「行里的题名恰好等于新题名」。
 *
 * ## 诚实性验证（做过）
 *
 * 把 `saveEdit` 里的 `await refresh()` 去掉并**真部署**后，本测试变红：
 * `Expected substring: "...-已改" / Received string: "...（旧题名）... 2024"`。
 * 说明它抓的是「页面真的更新了」，不是「接口被调用了」。
 */

const CREDENTIALS = { account: "demo", password: demoPassword() };

/** 造一条引用库条目；返回 item_id。 */
async function seedItem(page: Page, title: string): Promise<string> {
  const token = await page.evaluate(() => localStorage.getItem("access_token"));
  expect(token, "登录后 localStorage 里应有 access_token").toBeTruthy();
  const headers = { Authorization: `Bearer ${token}` };

  // 坑 2：先清空，避免同 DOI 被合并成 created=0
  const cleared = await page.request.delete("/api/v1/reference-library", { headers });
  expect(cleared.ok(), `清空失败：${cleared.status()}`).toBe(true);

  const response = await page.request.post("/api/v1/reference-library", {
    headers,
    data: {
      items: [
        {
          id: "doi:10.18653/v1/2024.eacl-demo.16",
          type: "paper-conference",
          title,
          author: [{ family: "Es", given: "Shahul" }],
          issued_year: 2024,
          container_title: "Proceedings of EACL",
          doi: "10.18653/v1/2024.eacl-demo.16",
        },
      ],
    },
  });
  expect(response.ok(), `造数据失败：${response.status()} ${await response.text()}`).toBe(true);
  const body = await response.json();
  expect(body.created, `清空后应新建 1 条，实际 created=${body.created}`).toBe(1);

  const list = await page.request.get("/api/v1/reference-library?limit=100", { headers });
  expect(list.ok(), `列表失败：${list.status()}`).toBe(true);
  const payload = await list.json();
  const items = payload.items ?? payload;
  const found = items.find((item: { title: string }) => item.title === title);
  expect(found, `列表里找不到刚造的条目「${title}」`).toBeTruthy();
  return found.item_id as string;
}

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#account").fill(CREDENTIALS.account);
  await page.locator("#password").fill(CREDENTIALS.password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 30_000 });
}

test("引用库条目编辑在真实浏览器里可用，且后端准入检查拦住非法值", async ({ page }) => {
  await login(page);

  const stamp = Date.now();
  const originalTitle = `编辑验证条目-${stamp}`;
  const itemId = await seedItem(page, originalTitle);

  await page.goto("/references/library");
  await page.waitForLoadState("networkidle");

  const row = page.locator(`[data-test="library-row-${itemId}"]`);
  await expect(row).toBeVisible({ timeout: 30_000 });
  expect(await row.innerText()).toContain(originalTitle);

  // ===== ① 点铅笔按钮 → 表单出现，且**预填当前值** =====
  await page.locator(`[data-test="library-edit-${itemId}"]`).click();

  const form = page.locator(`[data-test="library-edit-form-${itemId}"]`);
  await expect(form).toBeVisible();

  const titleInput = page.locator(`[data-test="library-edit-title-${itemId}"]`);
  const yearInput = page.locator(`[data-test="library-edit-year-${itemId}"]`);
  const containerInput = page.locator(`[data-test="library-edit-container-${itemId}"]`);

  // 预填是编辑功能的基本要求：空表单会让用户以为数据丢了
  expect(await titleInput.inputValue()).toBe(originalTitle);
  expect(await yearInput.inputValue()).toBe("2024");
  expect(await containerInput.inputValue()).toBe("Proceedings of EACL");

  // ===== ② 改题名 + 年份 → 保存 → 列表上的字**真的变了** =====
  const newTitle = `${originalTitle}-已改`;
  await titleInput.fill(newTitle);
  await yearInput.fill("2023");
  await page.locator(`[data-test="library-edit-save-${itemId}"]`).click();

  // 保存成功后表单应关闭
  await expect(form).toHaveCount(0, { timeout: 30_000 });

  // 成功提示要说明改了什么
  const success = page.locator('[data-test="library-success"]');
  await expect(success).toBeVisible({ timeout: 15_000 });
  expect(await success.innerText()).toContain("已更新");

  // 列表行文案必须变成新题名（这是「真的生效了」而不是「调了接口」）
  await expect(row).toContainText(newTitle, { timeout: 15_000 });
  expect(await row.innerText()).toContain("2023");

  // 坑 3：不能断言 `not.toContain(originalTitle)`（旧题名是新题名的前缀）。
  // 要验「题名整体被替换」，断言行里的题名恰好等于新题名。
  const rowTitleText = (await row.innerText()).split("\n")[0].trim();
  expect(rowTitleText).toBe(newTitle);

  // ===== ③ 刷新页面 → 改动仍在（证明落库，不是前端乐观更新）=====
  await page.reload();
  await page.waitForLoadState("networkidle");
  const rowAfterReload = page.locator(`[data-test="library-row-${itemId}"]`);
  await expect(rowAfterReload).toBeVisible({ timeout: 30_000 });
  expect(await rowAfterReload.innerText()).toContain(newTitle);
  expect(await rowAfterReload.innerText()).toContain("2023");

  // ===== ④ 把年份改空 → 后端准入检查必须拦住，并展示原因 =====
  // 入库规则：缺 issued_year 的条目不能作为文献源（否则要伪造 published_at）
  await page.locator(`[data-test="library-edit-${itemId}"]`).click();
  await expect(form).toBeVisible();
  await yearInput.fill("");
  await page.locator(`[data-test="library-edit-save-${itemId}"]`).click();

  const actionError = page.locator('[data-test="library-action-error"]');
  await expect(actionError).toBeVisible({ timeout: 30_000 });
  const errorText = await actionError.innerText();
  // 原因要能看懂：不能只说「保存失败」
  expect(errorText.length).toBeGreaterThan(4);
  expect(errorText).toMatch(/年份|入库|条件/);

  // 失败后表单应保持打开（用户要能继续改，而不是重填一遍）
  await expect(form).toBeVisible();

  // 取消编辑，确认不会误保存
  await page.locator(`[data-test="library-edit-cancel-${itemId}"]`).click();
  await expect(form).toHaveCount(0);

  // 库里仍是上一步成功改的值（失败的那次没有写进去）
  expect(await rowAfterReload.innerText()).toContain(newTitle);
  expect(await rowAfterReload.innerText()).toContain("2023");

  // ===== ⑤ 移动端：编辑表单不横向溢出 =====
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await page.waitForLoadState("networkidle");
  await page.locator(`[data-test="library-edit-${itemId}"]`).click();
  await expect(form).toBeVisible();

  const overflow = await page.evaluate(() => {
    const root = document.documentElement;
    return { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth };
  });
  expect(overflow.scrollWidth - overflow.clientWidth).toBeLessThanOrEqual(1);
});
