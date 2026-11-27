import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import TaskResult from "@/components/tasks/TaskResult.vue";
import type { ReviewReport, SubtaskSnapshot } from "@/api/types";

// 全文获取 Phase B · Task B5：结果页的证据占比展示与「上传 PDF 升级全文核验」。
// 只覆盖新增契约：既有结果页行为由 TaskResult.test.ts 负责。

const uploadPdf = vi.fn();
const toastErrorMock = vi.fn();
const toastSuccessMock = vi.fn();

vi.mock("@/api/knowledge", () => ({
  knowledgeApi: {
    upload: (...args: unknown[]) => uploadPdf(...args),
  },
}));
vi.mock("@/api/tasks", () => ({
  taskApi: {
    list: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    approve: vi.fn(),
    control: vi.fn(),
    events: vi.fn(),
    wsTicket: vi.fn(),
    downloadArtifact: vi.fn(),
    fillMatrixGap: vi.fn(),
  },
}));
vi.mock("@/api/templates", () => ({ renderTemplate: vi.fn() }));
vi.mock("@/lib/toast", () => ({
  toastError: (...args: unknown[]) => toastErrorMock(...args),
  toastSuccess: (...args: unknown[]) => toastSuccessMock(...args),
  toastInfo: vi.fn(),
  toastWarning: vi.fn(),
  toast: vi.fn(),
}));

const SUMMARY = {
  attempted: 6,
  succeeded: 4,
  fallback_metadata: 2,
  scanned_skipped: 1,
  failed: 0,
};

function report(overrides: Partial<ReviewReport> = {}): ReviewReport {
  return {
    final_markdown: "# 检索增强生成综述\n\n[E1] 结论",
    bibliography: [
      { index: 1, title: "RAG 评估", doi: "10.1000/XYZ.1", source_url: "https://example.org/1" },
      { index: 2, title: "无 DOI 文献" },
    ],
    scores: { citation_traceability: 0.96, quality: 0.88 },
    issues: [],
    stats: {},
    ...overrides,
  };
}

function subtask(output: Record<string, unknown> | null): SubtaskSnapshot {
  return {
    node_id: "search-1",
    title: "检索文献",
    status: "completed",
    retry_count: 0,
    started_at: "2026-10-18T04:00:00Z",
    completed_at: "2026-10-18T04:01:00Z",
    output,
    error: null,
  };
}

function subtaskWithSummary(
  summary: Record<string, number>,
  nested: "execution_result" | "stats" = "execution_result",
): SubtaskSnapshot {
  const output =
    nested === "execution_result"
      ? { execution_result: { stats: { fulltext_summary: summary } } }
      : { stats: { fulltext_summary: summary } };
  return subtask(output);
}

function mountResult(props: Record<string, unknown> = {}) {
  return mount(TaskResult, {
    props: {
      taskId: "task-1",
      report: report(),
      artifacts: [],
      ...props,
    },
  });
}

/** happy-dom 下无法通过 trigger 设置 target.files，直接挂到 input 元素上。 */
async function chooseUpgradeFile(wrapper: ReturnType<typeof mount>, file: File) {
  const input = wrapper.find('[data-test="upload-pdf-input"]');
  Object.defineProperty(input.element, "files", {
    value: [file],
    writable: false,
    configurable: true,
  });
  await input.trigger("change");
  await flushPromises();
}

function pdfFile(name = "paper.pdf", sizeInBytes = 1024): File {
  const file = new File(["x"], name, { type: "application/pdf" });
  Object.defineProperty(file, "size", { value: sizeInBytes });
  return file;
}

describe("TaskResult fulltext evidence", () => {
  beforeEach(() => {
    uploadPdf.mockReset();
    uploadPdf.mockResolvedValue({ id: "doc-1" });
    toastErrorMock.mockReset();
    toastSuccessMock.mockReset();
  });

  it("renders the full-text ratio from the search node stats", () => {
    const wrapper = mountResult({ subtasks: [subtaskWithSummary(SUMMARY)] });

    const card = wrapper.find('[data-test="fulltext-summary"]');
    expect(card.exists()).toBe(true);
    expect(card.text()).toContain("全文证据占比");
    // M = succeeded（已有全文），K = fallback_metadata（仅题录摘要）
    expect(wrapper.find('[data-test="fulltext-summary-counts"]').text()).toContain(
      "全文证据 4 篇",
    );
    expect(wrapper.find('[data-test="fulltext-summary-counts"]').text()).toContain(
      "仅题录摘要 2 篇",
    );
    expect(wrapper.find('[data-test="fulltext-summary-counts"]').text()).toContain(
      "扫描件跳过 1 篇",
    );
  });

  it("reads legacy node output stats as well", () => {
    const wrapper = mountResult({ subtasks: [subtaskWithSummary(SUMMARY, "stats")] });

    expect(wrapper.find('[data-test="fulltext-summary"]').exists()).toBe(true);
  });

  it("hides the whole section when no fulltext_summary exists", () => {
    const wrapper = mountResult({
      subtasks: [subtask({ execution_result: { stats: { documents: 3 } } })],
    });

    expect(wrapper.find('[data-test="fulltext-summary"]').exists()).toBe(false);
  });

  it("does not invent a summary when subtasks are missing", () => {
    const wrapper = mountResult();

    expect(wrapper.find('[data-test="fulltext-summary"]').exists()).toBe(false);
  });

  it("uploads a PDF for a DOI entry and forwards the ref_id", async () => {
    const wrapper = mountResult();

    await wrapper.find('[data-test="upload-pdf-upgrade-1"]').trigger("click");
    await chooseUpgradeFile(wrapper, pdfFile());

    expect(uploadPdf).toHaveBeenCalledTimes(1);
    const [file, title, strategy, onProgress, refId] = uploadPdf.mock.calls[0];
    expect((file as File).name).toBe("paper.pdf");
    expect(title).toBe("RAG 评估");
    expect(strategy).toBe("recursive");
    expect(onProgress).toBeUndefined();
    // 规则：有 DOI → doi:<归一化 DOI>（小写、去 doi: 前缀）
    expect(refId).toBe("doi:10.1000/xyz.1");
    expect(toastSuccessMock).toHaveBeenCalledWith(
      "已上传并关联，后续任务该文献将使用全文证据",
    );
    expect(wrapper.emitted("refresh")).toHaveLength(1);
  });

  it("uploads without a ref_id when the entry has no DOI", async () => {
    const wrapper = mountResult();

    await wrapper.find('[data-test="upload-pdf-upgrade-2"]').trigger("click");
    await chooseUpgradeFile(wrapper, pdfFile());

    expect(uploadPdf).toHaveBeenCalledTimes(1);
    expect(uploadPdf.mock.calls[0][4]).toBeNull();
  });

  it("blocks .caj files with a readable hint and never calls the API", async () => {
    const wrapper = mountResult();

    await wrapper.find('[data-test="upload-pdf-upgrade-1"]').trigger("click");
    await chooseUpgradeFile(wrapper, pdfFile("知网文献.caj"));

    expect(uploadPdf).not.toHaveBeenCalled();
    expect(toastErrorMock).toHaveBeenCalledWith(
      "知网 CAJ 格式暂不支持，请先在官网下载 PDF 再上传",
    );
  });

  it("rejects oversized PDFs before uploading", async () => {
    const wrapper = mountResult();

    await wrapper.find('[data-test="upload-pdf-upgrade-1"]').trigger("click");
    await chooseUpgradeFile(wrapper, pdfFile("big.pdf", 31 * 1024 * 1024));

    expect(uploadPdf).not.toHaveBeenCalled();
    expect(toastErrorMock).toHaveBeenCalledWith("文件超过 30 MB 上限");
  });
});
