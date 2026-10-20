import { describe, expect, it, vi } from "vitest";

import { importReferences, importRejectionDetail } from "@/api/references";
import type { ReferenceImportResponse } from "@/api/types";

vi.mock("@/api/client", () => ({
  default: {
    post: vi.fn(),
  },
}));

import api from "@/api/client";

function importResult(overrides: Partial<ReferenceImportResponse> = {}): ReferenceImportResponse {
  return {
    format: "bibtex",
    count: 1,
    items: [
      {
        id: "doi:10.18653/v1/2023.evalnlp-1.16",
        type: "article-journal",
        title: "Ragas: Automated Evaluation of RAG",
        authors: ["Es, Shahul", "James, Jithin"],
        year: 2023,
        container_title: "arXiv preprint",
        volume: null,
        issue: null,
        page: null,
        publisher: null,
        doi: "10.18653/v1/2023.evalnlp-1.16",
        url: null,
      },
    ],
    ...overrides,
  };
}

describe("references api", () => {
  it("importReferences 提交内容与格式并返回解析结果", async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 200, data: importResult() });

    const result = await importReferences({ content: "@article{a, title={T}}", format: "bibtex" });

    expect(api.post).toHaveBeenCalledTimes(1);
    const [url, body] = vi.mocked(api.post).mock.calls[0];
    expect(url).toBe("/references/import");
    expect(body).toEqual({ content: "@article{a, title={T}}", format: "bibtex" });
    expect(result.count).toBe(1);
    expect(result.items[0].title).toContain("Ragas");
  });

  it("importReferences 默认 format 为 auto（交给后端探测）", async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 200, data: importResult({ format: "ris" }) });

    await importReferences({ content: "TY  - JOUR\nER  - " });

    const [, body] = vi.mocked(api.post).mock.calls[0];
    expect((body as { format: string }).format).toBe("auto");
  });

  it("importReferences 把 400 的 detail 作为可展示错误抛出", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 400,
      data: { detail: "无法识别内容格式：请显式指定 format=bibtex 或 format=ris" },
    });

    await expect(importReferences({ content: "垃圾内容" })).rejects.toThrow("无法识别内容格式");
  });

  it("importReferences 在 400 且无 detail 时给出兜底文案", async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 400, data: {} });

    await expect(importReferences({ content: "x" })).rejects.toThrow("导入失败（400）");
  });

  it("importRejectionDetail 兼容字符串与数组 detail", () => {
    expect(importRejectionDetail({ detail: "原因" })).toBe("原因");
    expect(importRejectionDetail({ detail: [{ msg: "A" }, { msg: "B" }] })).toBe("A；B");
    expect(importRejectionDetail({})).toBeNull();
    expect(importRejectionDetail(undefined)).toBeNull();
  });
});
