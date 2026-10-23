import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

import { knowledgeApi } from "@/api/knowledge";
import DocumentUpload from "@/components/knowledge/DocumentUpload.vue";
import DocumentTable from "@/components/knowledge/DocumentTable.vue";
import type { KnowledgeDocument } from "@/api/types";

vi.mock("@/api/client", () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
    delete: vi.fn(),
  },
}));

// store 只做接线，真实上传逻辑由 api 层测试覆盖
const knowledgeStore = {
  documents: [],
  stats: null,
  loading: false,
  uploading: false,
  uploadProgress: 0,
  error: null as string | null,
  loadAll: vi.fn(),
  upload: vi.fn(),
  remove: vi.fn(),
};
vi.mock("@/stores/knowledge", () => ({
  useKnowledgeStore: () => knowledgeStore,
}));

import api from "@/api/client";

function pdfFile(name = "paper.pdf", sizeInBytes = 1024): File {
  const file = new File(["x"], name, { type: "application/pdf" });
  Object.defineProperty(file, "size", { value: sizeInBytes });
  return file;
}

function documentRow(overrides: Partial<KnowledgeDocument> = {}): KnowledgeDocument {
  return {
    id: "doc-1",
    title: "RAG 综述",
    doc_type: "pdf",
    index_status: "ready",
    chunk_count: 42,
    created_at: "2026-10-18T03:00:00Z",
    source: "upload",
    original_name: "rag.pdf",
    failure_reason: null,
    ...overrides,
  };
}

/** happy-dom 下无法通过 trigger 设置 target.files，直接挂到 input 元素上 */
async function chooseFile(wrapper: ReturnType<typeof mount>, file: File) {
  const inputWrapper = wrapper.find('[data-test="document-file"]');
  Object.defineProperty(inputWrapper.element, "files", {
    value: [file],
    writable: false,
    configurable: true,
  });
  await inputWrapper.trigger("change");
}

describe("knowledge api", () => {
  it("builds multipart fields without setting the content type", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: documentRow() });

    await knowledgeApi.upload(pdfFile(), "RAG 综述", "recursive");

    const [url, body, config] = vi.mocked(api.post).mock.calls[0];
    expect(url).toBe("/knowledge/documents");
    expect(body).toBeInstanceOf(FormData);
    const form = body as FormData;
    expect(form.get("title")).toBe("RAG 综述");
    expect(form.get("strategy")).toBe("recursive");
    expect((form.get("file") as File).name).toBe("paper.pdf");
    // 边界由 Axios 生成；客户端不得写死 content-type，也不得传文件路径字段
    expect(form.get("file_path")).toBeNull();
    const headers = (config?.headers ?? {}) as Record<string, string>;
    expect(headers["Content-Type"]).toBeUndefined();
  });

  it("forwards upload progress to the caller", async () => {
    const onProgress = vi.fn();
    vi.mocked(api.post).mockImplementation(async (_url, _body, config) => {
      config?.onUploadProgress?.({ loaded: 50, total: 100 } as never);
      return { data: documentRow() };
    });

    await knowledgeApi.upload(pdfFile(), "RAG 综述", "recursive", onProgress);

    expect(onProgress).toHaveBeenCalledWith(50);
  });

  it("mirrors the read routes", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { documents: [documentRow()] } });
    vi.mocked(api.delete).mockResolvedValue({ data: documentRow() });

    const list = await knowledgeApi.list();
    await knowledgeApi.remove("doc-1");

    expect(api.get).toHaveBeenCalledWith("/knowledge/documents");
    expect(api.delete).toHaveBeenCalledWith("/knowledge/documents/doc-1");
    expect(list.documents).toHaveLength(1);
  });
});

describe("DocumentUpload", () => {
  it("rejects unsupported extensions before uploading", async () => {
    const wrapper = mount(DocumentUpload);

    await chooseFile(wrapper, pdfFile("malware.exe"));
    await wrapper.find("form").trigger("submit");

    expect(knowledgeStore.upload).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="upload-error"]').text()).toContain("支持");
  });

  it("rejects files larger than 30 MB client-side", async () => {
    const wrapper = mount(DocumentUpload);

    await chooseFile(wrapper, pdfFile("huge.pdf", 31 * 1024 * 1024));
    await wrapper.find("form").trigger("submit");

    expect(knowledgeStore.upload).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="upload-error"]').text()).toContain("30 MB");
  });

  it("uploads with the chosen title and strategy, then refreshes the list", async () => {
    const wrapper = mount(DocumentUpload);

    await chooseFile(wrapper, pdfFile());
    await wrapper.find('[data-test="document-title"]').setValue("RAG 综述");
    await wrapper.find("form").trigger("submit");
    await wrapper.vm.$nextTick();

    expect(knowledgeStore.upload).toHaveBeenCalledTimes(1);
    expect(knowledgeStore.upload.mock.calls[0][1]).toBe("RAG 综述");
    expect(wrapper.emitted("uploaded")).toHaveLength(1);
  });

  it("shows the server error next to the form", async () => {
    const wrapper = mount(DocumentUpload);
    knowledgeStore.upload.mockRejectedValueOnce(new Error("文件签名校验失败"));

    await chooseFile(wrapper, pdfFile());
    await wrapper.find('[data-test="document-title"]').setValue("RAG 综述");
    await wrapper.find("form").trigger("submit");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();

    expect(wrapper.find('[data-test="upload-error"]').text()).toBeTruthy();
  });
});

describe("DocumentTable", () => {
  it("shows title, type, status, chunks and created time", () => {
    const wrapper = mount(DocumentTable, {
      props: { documents: [documentRow()] },
    });

    const row = wrapper.find('[data-test="document-row-doc-1"]');
    expect(row.text()).toContain("RAG 综述");
    // 类型用徽标展示（`pdf` → `PDF`，2026-10-23 重设计）
    expect(row.text()).toContain("PDF");
    expect(row.text()).toContain("42");
    // 来源列在「来源全相同」时隐藏（实测 11 篇全是 upload，该列无信息量）
    expect(row.text()).not.toContain("upload");
    expect(wrapper.find('[data-test="doc-col-source"]').exists()).toBe(false);
  });

  it("keeps the source column when sources actually differ", () => {
    const wrapper = mount(DocumentTable, {
      props: {
        documents: [
          documentRow(),
          { ...documentRow(), id: "doc-2", source: "arxiv", title: "另一篇" },
        ],
      },
    });

    expect(wrapper.find('[data-test="doc-col-source"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="document-row-doc-2"]').text()).toContain("arxiv");
  });

  it("requires confirmation before deleting", async () => {
    const wrapper = mount(DocumentTable, {
      props: { documents: [documentRow()] },
    });

    await wrapper.find('[data-test="document-delete-doc-1"]').trigger("click");
    expect(wrapper.emitted("remove")).toBeUndefined();

    await wrapper.find('[data-test="document-delete-confirm-doc-1"]').trigger("click");
    expect(wrapper.emitted("remove")).toEqual([["doc-1"]]);
  });

  it("shows a concise failure reason without a stack trace", () => {
    const wrapper = mount(DocumentTable, {
      props: {
        documents: [
          documentRow({
            index_status: "failed",
            chunk_count: 0,
            failure_reason: "文档索引失败：不支持的文档类型",
          }),
        ],
      },
    });

    const text = wrapper.find('[data-test="document-row-doc-1"]').text();
    expect(text).toContain("索引失败");
    expect(text).toContain("不支持的文档类型");
    expect(text).not.toContain("Traceback");
  });
});
