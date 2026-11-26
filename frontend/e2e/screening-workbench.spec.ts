import { expect, test, type Page } from "@playwright/test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { apiBase, demoCreds } from "./support/env";
import { fetchAuthToken, seedAuthByApi } from "./support/auth";

// PRISMA 筛选工作台视觉验收：真实后端造数据 → 走完决策流 → 截图
const SHOTS = resolve(dirname(fileURLToPath(import.meta.url)), "screenshots");
const API = apiBase();

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: resolve(SHOTS, `screening-${name}-${test.info().project.name}.png`),
    fullPage: true,
  });
}

test("PRISMA 筛选工作台：造数据 → 纳入/排除 → 计数变化与 Pin-in-Place", async ({
  page,
  request,
}) => {
  const { account, password } = demoCreds();
  const token = (await fetchAuthToken(request, account, password)).access_token;
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
  await seedAuthByApi(page, request, account, password, `/tasks/${taskId}/screening`);
  await expect(
    page.locator('[data-test^="screening-row-"]').first()
  ).toBeVisible({ timeout: 30_000 });
  await shot(page, "initial");

  // 校验 PRISMA 漏斗计数
  const prisma = page.locator('[data-test="screening-prisma"]');
  await expect(prisma).toContainText("165");
  await expect(prisma).toContainText("识别");

  // 纳入第一条 → 阶段推进到「全文」，且 Pin-in-Place 保持列表顺序与 scrollY 稳定
  const rows = page.locator('[data-test^="screening-row-"]');
  const initialOrder = await rows.evaluateAll((els) =>
    els.map((el) => el.getAttribute("data-test")),
  );
  const scrollBefore = await page.evaluate(() => window.scrollY);
  const firstId = initialOrder[0]!.replace("screening-row-", "");
  const firstRow = page.locator(`[data-test="screening-row-${firstId}"]`);
  await page.locator(`[data-test="screening-include-${firstId}"]`).click();
  await expect(page.locator('[data-test="screening-success"]')).toContainText("已纳入", {
    timeout: 20_000,
  });
  await expect(firstRow).toContainText("全文");
  const afterOrder = await rows.evaluateAll((els) =>
    els.map((el) => el.getAttribute("data-test")),
  );
  expect(afterOrder).toEqual(initialOrder);
  const scrollAfter = await page.evaluate(() => window.scrollY);
  expect(Math.abs(scrollAfter - scrollBefore)).toBeLessThanOrEqual(1);
  await shot(page, "after-include");

  // 排除第三条（同样按 id 定位）：
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

  // 排除是终态：按钮必须消失，改显示「已排除：理由」徽标
  await expect(page.locator(`[data-test="screening-include-${thirdId}"]`)).toHaveCount(0);
  await expect(page.locator(`[data-test="screening-exclude-${thirdId}"]`)).toHaveCount(0);
  const excludedBadge = page.locator(`[data-test="screening-excluded-badge-${thirdId}"]`);
  await expect(excludedBadge).toBeVisible();
  await expect(excludedBadge).toContainText("marine biology, not relevant");

  // M4 FunnelChart 校验 + 后端 /prisma 接口精确核对（title_abstract.excluded === 1）
  await expect(prisma).toContainText("总体转化");
  const prismaResp = await request.get(`${API}/screening/prisma?task_id=${taskId}`, {
    headers: auth,
  });
  expect(prismaResp.ok()).toBeTruthy();
  const prismaJson = (await prismaResp.json()) as {
    title_abstract?: { excluded?: number };
  };
  expect(prismaJson.title_abstract?.excluded).toBe(1);
  await shot(page, "after-exclude");

  // Mermaid 源码与真实 SVG 节点文字存在（W1 回归：不得为空白框）
  await expect(page.locator('[data-test="screening-mermaid"]')).toContainText("flowchart TD");
  const svgTexts = page.locator('[data-test="screening-mermaid-diagram"] svg text');
  await expect
    .poll(async () => await svgTexts.count(), { timeout: 15_000 })
    .toBeGreaterThan(0);
  const diagramText = await page.locator('[data-test="screening-mermaid-diagram"] svg').textContent();
  expect(diagramText).toContain("识别");
  expect(diagramText).toContain("纳入");

  // 阶段过滤：只剩「全文」阶段那一条（纳入的那条），且不再有标题/摘要的行
  await page.locator('[data-test="screening-stage-filter"]').selectOption("fulltext");
  await expect(rows).toHaveCount(1, { timeout: 20_000 });
  await expect(rows.first()).toContainText("全文");
  await expect(page.locator(`[data-test="screening-row-${thirdId}"]`)).toHaveCount(0);
  await shot(page, "filtered-fulltext");

  // 清理：直接调后端删除不支持，用 psql 侧脚本清理（此处只打印 taskId 供清理）
  console.log(`PROBE_TASK_ID=${taskId}`);
});
