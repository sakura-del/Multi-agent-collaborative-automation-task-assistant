import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { knowledgeApi } from "@/api/knowledge";
import { useKnowledgeStore } from "./knowledge";

vi.mock("@/api/knowledge", () => ({
  knowledgeApi: {
    upload: vi.fn(),
    list: vi.fn(),
    stats: vi.fn(),
    remove: vi.fn(),
    search: vi.fn(),
  },
}));

describe("useKnowledgeStore 上传异常处理", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("上传中途断连或报错时仍刷新文档列表与统计，避免前端空状态诱导重复上传", async () => {
    vi.mocked(knowledgeApi.upload).mockRejectedValueOnce(new Error("网络连接中断"));
    vi.mocked(knowledgeApi.list).mockResolvedValueOnce({
      documents: [
        {
          id: "doc-server-saved",
          title: "已入库论文",
          doc_type: "docx",
          index_status: "ready",
          chunk_count: 67,
          created_at: "2026-11-26T03:58:45Z",
          source: "upload",
          original_name: "paper.docx",
          failure_reason: null,
        },
      ],
    });
    vi.mocked(knowledgeApi.stats).mockResolvedValueOnce({
      total_docs: 1,
      total_chunks: 67,
      vector_index_ready: true,
      bm25_index_ready: true,
      index_failed_count: 0,
      indexing_count: 0,
    });

    const store = useKnowledgeStore();
    const file = new File(["test"], "paper.docx");

    await expect(store.upload(file, "已入库论文", "recursive")).rejects.toThrow("网络连接中断");

    expect(knowledgeApi.list).toHaveBeenCalledTimes(1);
    expect(knowledgeApi.stats).toHaveBeenCalledTimes(1);
    expect(store.documents).toHaveLength(1);
    expect(store.stats?.total_docs).toBe(1);
    expect(store.error).toBe("网络连接中断");
  });

  it("uploadBatch 支持有限并发、单篇失败隔离、进度更新与结束统一刷新一次", async () => {
    let active = 0;
    let maxActive = 0;
    vi.mocked(knowledgeApi.upload).mockImplementation(async (_file, title, _strategy, onProgress) => {
      active += 1;
      maxActive = Math.max(maxActive, active);
      onProgress?.(50);
      await new Promise((r) => setTimeout(r, 15));
      try {
        if (title === "paper-2") {
          throw new Error("解析失败");
        }
        onProgress?.(100);
        return {
          id: `id-${title}`,
          title,
          doc_type: "pdf",
          index_status: "ready",
          chunk_count: 10,
          created_at: "2026-11-26T00:00:00Z",
          source: "upload",
          original_name: `${title}.pdf`,
          failure_reason: null,
        };
      } finally {
        active -= 1;
      }
    });
    vi.mocked(knowledgeApi.list).mockResolvedValueOnce({ documents: [] });
    vi.mocked(knowledgeApi.stats).mockResolvedValueOnce({
      total_docs: 2,
      total_chunks: 20,
      vector_index_ready: true,
      bm25_index_ready: true,
      index_failed_count: 0,
      indexing_count: 0,
    });

    const store = useKnowledgeStore();
    const progressCalls: Array<[string, number]> = [];
    const doneCalls: Array<[string, boolean, string | undefined]> = [];

    const result = await store.uploadBatch(
      [
        { id: "1", file: new File(["1"], "1.pdf"), title: "paper-1" },
        { id: "2", file: new File(["2"], "2.pdf"), title: "paper-2" },
        { id: "3", file: new File(["3"], "3.pdf"), title: "paper-3" },
      ],
      "recursive",
      {
        concurrency: 2,
        onProgress: (id, pct) => progressCalls.push([id, pct]),
        onItemDone: (id, ok, msg) => doneCalls.push([id, ok, msg]),
      },
    );

    expect(maxActive).toBeLessThanOrEqual(2);
    expect(result).toEqual({ succeeded: 2, failed: 1 });
    expect(progressCalls.some(([id, pct]) => id === "1" && pct === 50)).toBe(true);
    expect(doneCalls).toEqual(
      expect.arrayContaining([
        ["1", true, undefined],
        ["2", false, "解析失败"],
        ["3", true, undefined],
      ]),
    );
    expect(knowledgeApi.list).toHaveBeenCalledTimes(1);
    expect(knowledgeApi.stats).toHaveBeenCalledTimes(1);
    expect(store.uploading).toBe(false);
  });
});
