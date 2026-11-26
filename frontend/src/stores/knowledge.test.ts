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
});
