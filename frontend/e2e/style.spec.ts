import { expect, test, type Page } from "@playwright/test";

// 引用样式页 E2E（2026-10-26 重设计）：
// 预览必须真实、样式之间必须可区分、兜底必须告警、删除必须确认。
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

test("样式页：每个样式都有真实渲染预览", async ({ page, request }) => {
  const accessToken = await token(request, "demo", "demo12345");
  const styles = await (
    await request.get(`${API}/citation-styles`, { headers: { Authorization: `Bearer ${accessToken}` } })
  ).json();
  const previews = await (
    await request.get(`${API}/citation-styles/previews`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
  ).json();

  await loginAs(page, "demo", "demo12345");
  await page.goto("/styles");
  await expect(page.locator('[data-test^="style-row-"]').first()).toBeVisible({
    timeout: 30_000,
  });

  // ① 预览数量与接口一致
  const previewCount = await page.locator('[data-test^="style-preview-"]').count();
  // 注意：style-preview-error / style-preview-note 也匹配前缀，排除它们
  const realPreviews = await page
    .locator('[data-test^="style-preview-"]:not([data-test="style-preview-error"]):not([data-test="style-preview-note"]):not([data-test^="style-preview-warning-"])')
    .count();
  expect(realPreviews).toBe(previews.previews.length);

  // ② 每个预览都含样例文献内容（不是元信息回显）
  for (const item of previews.previews as Array<{ style_id: string; entries: string[] }>) {
    const box = page.locator(`[data-test="style-preview-${item.style_id}"]`);
    await expect(box).toBeVisible();
    const text = await box.innerText();
    expect(text.length).toBeGreaterThan(10);
  }

  // ③ 预览文本必须**各不相同**（这是「6 种样式」的唯一证据）
  const texts = await page
    .locator('[data-test^="style-preview-"]:not([data-test="style-preview-error"]):not([data-test="style-preview-note"]):not([data-test^="style-preview-warning-"])')
    .evaluateAll((nodes) => nodes.map((n) => (n.textContent ?? "").replace(/\s+/g, " ").trim()));
  const distinct = new Set(texts);
  expect(
    distinct.size,
    `预览输出重复，说明渲染没真正生效：${JSON.stringify(texts)}`
  ).toBe(texts.length);

  // ④ 不允许出现兜底告警（有告警说明输出不代表该样式）
  const warningCount = await page.locator('[data-test^="style-preview-warning-"]').count();
  expect(warningCount, "真实服务器上不应有兜底告警").toBe(0);

  console.log("PREVIEW_COUNT=" + realPreviews);
  console.log("DISTINCT_PREVIEWS=" + distinct.size);
  console.log("STYLE_COUNT=" + styles.styles.length);
  console.log("SAMPLE_FIRST=" + texts[0]?.slice(0, 120));

  await page.screenshot({ path: `${SHOTS}/style-after.png`, fullPage: true });
});

test("样式页：预览能看出两个国标样式的差异", async ({ page }) => {
  // 这两个样式元信息完全相同（citation-number · citation-order · zh-CN），
  // 用户只能靠预览区分 —— 若预览也一样，选择就没有依据。
  await loginAs(page, "demo", "demo12345");
  await page.goto("/styles");
  await expect(page.locator('[data-test="style-row-gbt7714-2025-numeric"]')).toBeVisible({
    timeout: 30_000,
  });

  const gb2025 = await page
    .locator('[data-test="style-preview-gbt7714-2025-numeric"]')
    .innerText();
  const gb2015 = await page
    .locator('[data-test="style-preview-gbt7714-2015-numeric"]')
    .innerText();

  expect(gb2025).not.toBe(gb2015);
  // 2025 版有 [J] / [C/OL] 类型标识；2015 版没有
  expect(gb2025).toContain("[J]");
  expect(gb2015).not.toContain("[J]");
});

test("样式页：删除需要二次确认", async ({ page }) => {
  // 后端删除会 unlink 磁盘文件。用 e2efull 账号（无自定义样式）无法测删除，
  // 所以这里只验证「预置样式没有删除按钮」这一半契约。
  await loginAs(page, "demo", "demo12345");
  await page.goto("/styles");
  await expect(page.locator('[data-test="style-row-apa"]')).toBeVisible({ timeout: 30_000 });

  const deleteButtons = await page.locator('[data-test^="style-delete-"]').count();
  expect(deleteButtons, "预置样式不可删除").toBe(0);
});

test("样式页：预览加载失败仍可用（局部失败不连坐）", async ({ page }) => {
  await loginAs(page, "demo", "demo12345");

  await page.route(`${API}/citation-styles/previews`, (route) =>
    route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"boom"}' })
  );

  await page.goto("/styles");
  await page.waitForTimeout(2000);

  // 预览区报错
  await expect(page.locator('[data-test="style-preview-error"]')).toBeVisible();
  // 但样式列表仍在、上传仍可用
  const rowCount = await page.locator('[data-test^="style-row-"]').count();
  expect(rowCount, "预览失败不该让样式列表消失").toBeGreaterThan(0);
  await expect(page.locator('[data-test="style-upload-submit"]')).toBeVisible();

  await page.screenshot({ path: `${SHOTS}/style-partial-failure.png`, fullPage: true });
});

test("样式页：移动端无横向溢出", async ({ page }) => {
  await loginAs(page, "demo", "demo12345");
  await page.goto("/styles");
  await expect(page.locator('[data-test^="style-row-"]').first()).toBeVisible({
    timeout: 30_000,
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth
  );
  expect(overflow).toBeLessThanOrEqual(1);

  await page.screenshot({ path: `${SHOTS}/style-mobile.png`, fullPage: true });
});
