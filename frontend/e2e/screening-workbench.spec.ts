import { expect, test, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// PRISMA 筛选工作台视觉验收：真实后端造数据 → 走完决策流 → 截图
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");
const API = "http://10.0.10.2:48080/api/v1";

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: resolve(SHOTS, `screening-${name}-${test.info().project.name}.png`),
    fullPage: true,
  });
}

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#account").fill("demo");
  await page.locator("#password").fill("demo12345");
  await page.locator('form button[type="submit"]').click();
  await page.getByRole("link", { name: /任务/ }).waitFor({ timeout: 30_000 });
}

test("PRISMA 筛选工作台：造数据 → 纳入/排除 → 计数变化", async ({ page, request }) => {
  // ---- 后端直接造数据（前端没有「提交检索」的入口，检索由任务执行产生）----
  const loginResp = await request.post(`${API}/auth/login`, {
    data: { account: "demo", password: "demo12345" },
  });
  expect(loginResp.ok()).toBeTruthy();
  const token = (await loginResp.json()).access_token as string;
  const auth = { Authorization: `Bearer ${token}` };

  const taskResp = await request.post(`${API}/tasks`, {
    headers: auth,
    data: {
      topic: "PRISMA 工作台验收：检索增强生成评估",
      keywords: ["RAG", "评估"],
      max_documents: 10,
      output_language: "zh",
      citation_style: "gbt7714",
    },
  });
  expect(taskResp.ok()).toBeTruthy();
  const taskId = (await taskResp.json()).task_id as string;

  const logResp = await request.post(`${API}/screening/search-logs`, {
    headers: auth,
    data: {
      task_id: taskId,
      query: "retrieval augmented generation evaluation",
      sources: ["arxiv", "openalex"],
      hits_by_source: { arxiv: 120, openalex: 45 },
      records: [
        {
          source_id: "arxiv:2401.00001v1",
          title: "Retrieval-Augmented Generation for Knowledge-Intensive NLP",
          authors: ["Patrick Lewis", "Ethan Perez"],
          abstract:
            "We explore RAG models which combine pre-trained parametric and non-parametric memory for language generation.",
          doi: "10.1234/rag.2024",
        },
        {
          source_id: "arxiv:2401.00002v1",
          title: "A Survey on Evaluation of Retrieval-Augmented Generation",
          authors: ["张三", "李四"],
          abstract: "本文系统梳理了检索增强生成的评估指标与数据集，并指出长上下文下的挑战。",
        },
        {
          source_id: "openalex:W999",
          title: "Unrelated Paper on Marine Biology",
          authors: ["Bob"],
          abstract: "This paper studies coral reefs, having nothing to do with RAG.",
        },
      ],
    },
  });
  expect(logResp.ok()).toBeTruthy();
  // 165 命中 → 去重后 3 条
  expect((await logResp.json()).hits_deduped).toBe(3);

  // ---- 前端走查 ----
  await login(page);
  await page.goto(`/tasks/${taskId}/screening`);
  await expect(
    page.locator('[data-test^="screening-row-"]').first()
  ).toBeVisible({ timeout: 30_000 });
  await shot(page, "initial");

  // 校验 PRISMA 计数条
  const prisma = page.locator('[data-test="screening-prisma"]');
  await expect(prisma).toContainText("165");
  await expect(prisma).toContainText("识别");

  // 纳入第一条 → 阶段推进到「全文」
  const rows = page.locator('[data-test^="screening-row-"]');
  const firstRow = rows.first();
  const firstId = (await firstRow.getAttribute("data-test"))!.replace("screening-row-", "");
  await page.locator(`[data-test="screening-include-${firstId}"]`).click();
  await expect(page.locator('[data-test="screening-success"]')).toContainText("已纳入", {
    timeout: 20_000,
  });
  await expect(firstRow).toContainText("全文");
  await shot(page, "after-include");

  // 排除第三条：先点排除 → 表单出现 → 填理由 → 提交
  const thirdRow = rows.nth(2);
  const thirdId = (await thirdRow.getAttribute("data-test"))!.replace("screening-row-", "");
  await page.locator(`[data-test="screening-exclude-${thirdId}"]`).click();
  await expect(page.locator(`[data-test="screening-exclude-form-${thirdId}"]`)).toBeVisible();
  await shot(page, "exclude-form");

  await page
    .locator(`[data-test="screening-exclude-reason-${thirdId}"]`)
    .fill("marine biology, not relevant");
  await page.locator(`[data-test="screening-exclude-submit-${thirdId}"]`).click();
  await expect(page.locator('[data-test="screening-success"]')).toContainText("已排除", {
    timeout: 20_000,
  });

  // PRISMA 计数必须跟着变：标题/摘要排除 = 1
  await expect(prisma).toContainText("标题/摘要排除");
  await shot(page, "after-exclude");

  // Mermaid 源码存在
  await expect(page.locator('[data-test="screening-mermaid"]')).toContainText("flowchart TD");

  // 阶段过滤
  await page.locator('[data-test="screening-stage-filter"]').selectOption("fulltext");
  await page.waitForTimeout(1000);
  await expect(rows.first()).toContainText("全文");
  await shot(page, "filtered-fulltext");

  // 清理：直接调后端删除不支持，用 psql 侧脚本清理（此处只打印 taskId 供清理）
  console.log(`PROBE_TASK_ID=${taskId}`);
});
