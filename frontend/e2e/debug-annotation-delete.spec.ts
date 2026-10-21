import { expect, test } from "@playwright/test";

const API = "http://10.0.10.2:48080/api/v1";

test("批注删除：浏览器实测", async ({ page, request }) => {
  const loginResp = await request.post(`${API}/auth/login`, {
    data: { account: "demo", password: "demo12345" },
  });
  const token = (await loginResp.json()).access_token as string;
  const headers = { Authorization: `Bearer ${token}` };

  const tasks = await request.get(`${API}/tasks?limit=50`, { headers });
  const list = (await tasks.json()) as { task_id: string; status: string }[];
  const completed = list.find((item) => item.status === "completed");
  expect(completed).toBeTruthy();
  const taskId = completed!.task_id;

  // 通过 API 预置一条批注（避免依赖 UI 创建）
  const created = await request.post(`${API}/tasks/${taskId}/annotations`, {
    headers,
    data: { anchor_type: "task", content: "待删除的批注" },
  });
  expect(created.status()).toBe(201);
  const annotationId = (await created.json()).annotation_id as string;

  // 监听网络请求，确认 DELETE 真的发出去了
  const deleteCalls: { url: string; status: number }[] = [];
  page.on("response", async (response) => {
    if (response.request().method() === "DELETE" && response.url().includes("/annotations/")) {
      deleteCalls.push({ url: response.url(), status: response.status() });
    }
  });
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });

  await page.goto("/login");
  await page.locator("#account").fill("demo");
  await page.locator("#password").fill("demo12345");
  await page.locator('form button[type="submit"]').click();
  await page.getByRole("link", { name: /任务/ }).waitFor({ timeout: 30_000 });

  await page.goto(`/tasks/${taskId}`);
  await expect(page.getByRole("heading", { name: "导师批注" })).toBeVisible({
    timeout: 30_000,
  });

  const row = page.locator(`[data-test="annotation-row-${annotationId}"]`);
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(row).toContainText("待删除的批注");

  const deleteBtn = page.locator(`[data-test="annotation-delete-${annotationId}"]`);
  await expect(deleteBtn).toBeVisible();
  await deleteBtn.click();

  // 等网络真正返回
  await page.waitForTimeout(3000);

  console.log("DELETE calls:", JSON.stringify(deleteCalls));
  console.log("console errors:", JSON.stringify(consoleErrors));
  const errorBanner = page.locator('[data-test="annotation-error"]');
  const bannerText = (await errorBanner.count()) ? await errorBanner.textContent() : "(无)";
  console.log("error banner:", bannerText);
  console.log("row still visible:", await row.count());

  expect(deleteCalls.length, "DELETE 请求必须发出").toBeGreaterThan(0);
  expect(deleteCalls[0].status, "DELETE 必须返回 204").toBe(204);
  await expect(row).toHaveCount(0, { timeout: 10_000 });
});
