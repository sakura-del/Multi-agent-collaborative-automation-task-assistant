import { describe, expect, it, vi } from "vitest";

import {
  createDecision,
  getPrismaCounts,
  listScreeningRecords,
  recordSearch,
} from "@/api/screening";

vi.mock("@/api/client", () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));

import api from "@/api/client";

const EMPTY_PRISMA = {
  identified_total: 0,
  duplicates_removed: 0,
  records_screened: 0,
  included: 0,
  identified_by_source: {},
  title_abstract: { screened: 0, excluded: 0 },
  fulltext: { assessed: 0, excluded: 0 },
  exclusion_reasons: {},
  markdown: "",
  mermaid: "",
};

describe("screening api", () => {
  it("recordSearch 提交检索并返回日志", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 201,
      data: {
        search_log_id: "log-1",
        task_id: "task-1",
        query: "rag",
        sources: ["arxiv"],
        hits_total: 2,
        hits_deduped: 1,
        executed_at: "2026-10-20T00:00:00Z",
      },
    });

    const result = await recordSearch({
      task_id: "task-1",
      query: "rag",
      sources: ["arxiv"],
      records: [{ source_id: "a1", title: "T1", authors: [], abstract: "" }],
    });

    expect(api.post).toHaveBeenCalledWith("/screening/search-logs", expect.any(Object));
    expect(result.hits_deduped).toBe(1);
  });

  it("listScreeningRecords 按 task_id 查询", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { items: [] } });

    await listScreeningRecords("task-1");

    expect(api.get).toHaveBeenCalledWith("/screening/records?task_id=task-1");
  });

  it("listScreeningRecords 带阶段过滤时附加 stage 参数", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { items: [] } });

    await listScreeningRecords("task-1", "fulltext");

    expect(api.get).toHaveBeenCalledWith("/screening/records?task_id=task-1&stage=fulltext");
  });

  it("listScreeningRecords 对 task_id 做 URL 编码", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { items: [] } });

    await listScreeningRecords("task/1 a");

    // URLSearchParams 把空格编码为 `+`（查询串的标准编码），`/` 为 %2F
    expect(api.get).toHaveBeenCalledWith(
      "/screening/records?task_id=task%2F1+a"
    );
  });

  it("createDecision 提交阶段与结论", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 201,
      data: { record_id: "r1", current_stage: "fulltext" },
    });

    const result = await createDecision("r1", { stage: "title_abstract", verdict: "include" });

    expect(api.post).toHaveBeenCalledWith(
      "/screening/records/r1/decisions",
      { stage: "title_abstract", verdict: "include" },
      expect.objectContaining({ validateStatus: expect.any(Function) })
    );
    expect(result.current_stage).toBe("fulltext");
  });

  it("createDecision 把 400 的 detail 作为可展示错误抛出", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 400,
      data: { detail: "排除决策必须提供 exclusion_reason（PRISMA 2020 要求）" },
    });

    await expect(
      createDecision("r1", { stage: "title_abstract", verdict: "exclude" })
    ).rejects.toThrow("exclusion_reason");
  });

  it("createDecision 的 400 没有 detail 时给通用可读文案", async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 400, data: {} });

    await expect(
      createDecision("r1", { stage: "title_abstract", verdict: "exclude" })
    ).rejects.toThrow("筛选决策提交失败");
  });

  it("getPrismaCounts 返回计数与流程图", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        ...EMPTY_PRISMA,
        identified_total: 2,
        records_screened: 2,
        identified_by_source: { arxiv: 2 },
        markdown: "# PRISMA",
        mermaid: "flowchart TD",
      },
    });

    const result = await getPrismaCounts("task-1");

    // language 显式传默认值，让后端无需区分「未传」与「zh」
    expect(result.mermaid).toContain("flowchart");
    expect(api.get).toHaveBeenCalledWith("/screening/prisma?task_id=task-1&language=zh");
  });

  it("getPrismaCounts 支持英文输出", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: EMPTY_PRISMA });

    await getPrismaCounts("task-1", "en");

    expect(api.get).toHaveBeenCalledWith("/screening/prisma?task_id=task-1&language=en");
  });
});
