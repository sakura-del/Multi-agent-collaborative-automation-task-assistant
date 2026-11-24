import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import ExtractionTableView from "@/views/ExtractionTableView.vue";
import type { ExtractionTableOut } from "@/api/extractions";

vi.mock("@/api/extractions", () => ({
  listExtractionTables: vi.fn(),
  getExtractionTable: vi.fn(),
  addExtractionColumn: vi.fn(),
  runExtractionTable: vi.fn(),
  deleteExtractionTable: vi.fn(),
  exportExtractionTableCsv: vi.fn(),
}));

import {
  addExtractionColumn,
  exportExtractionTableCsv,
  getExtractionTable,
  runExtractionTable,
} from "@/api/extractions";

const SAMPLE_TABLE: ExtractionTableOut = {
  id: "tbl-1",
  task_id: null,
  title: "RAG 论文对比表",
  status: "completed",
  error: null,
  columns: [
    { key: "sample_size", label: "样本量", prompt: "提取总样本量" },
    { key: "limitations", label: "局限性", prompt: "提取局限性" },
  ],
  sources: [
    {
      source_type: "knowledge_doc",
      source_id: "doc-1",
      title: "Multi-Agent RAG Benchmark",
      year: 2026,
      source_url: null,
    },
    {
      source_type: "screening_record",
      source_id: "rec-1",
      title: "Abstract Only Study",
      year: null,
      source_url: "https://doi.org/10.1000/test",
    },
  ],
  cells: [
    {
      source_type: "knowledge_doc",
      source_id: "doc-1",
      column_key: "sample_size",
      status: "extracted",
      value: "N=320",
      quote: "总样本量 N=320",
      passage: "在 3.2 实验设置中，Table 2 汇总了总样本量 N=320 的分组结果。",
      locator: {
        page_start: 5,
        page_end: 5,
        section_title: "3.2 实验设置",
        table_ref: "Table 2",
      },
      evidence_scope: "full_text",
      confidence: 0.94,
    },
    {
      source_type: "knowledge_doc",
      source_id: "doc-1",
      column_key: "limitations",
      status: "not_reported",
      value: null,
      quote: null,
      passage: null,
      locator: null,
      evidence_scope: "full_text",
      confidence: null,
    },
    {
      source_type: "screening_record",
      source_id: "rec-1",
      column_key: "sample_size",
      status: "not_reported",
      value: null,
      quote: null,
      passage: null,
      locator: null,
      evidence_scope: "abstract_only",
      confidence: null,
    },
  ],
  created_at: "2026-11-24T05:00:00Z",
  updated_at: "2026-11-24T05:05:00Z",
};

describe("ExtractionTableView（Issue #36）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getExtractionTable).mockResolvedValue(SAMPLE_TABLE);
  });

  it("渲染横向对比表与 not_reported 灰态文案，点击已抽取格滑出原文抽屉并高亮 quote 与页码定位", async () => {
    const wrapper = mount(ExtractionTableView, {
      props: { tableId: "tbl-1" },
      global: { stubs: { Teleport: true } },
    });
    await flushPromises();

    expect(wrapper.find('[data-test="extraction-table"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="extraction-row-doc-1"]').text()).toContain("全文");
    expect(wrapper.find('[data-test="extraction-row-rec-1"]').text()).toContain("摘要");

    const notReported = wrapper.findAll('[data-test="cell-not-reported"]');
    expect(notReported.length).toBe(2);
    expect(notReported[0].text()).toContain("未报告");
    expect(notReported[0].text()).toContain("基于全文");
    expect(notReported[1].text()).toContain("基于摘要");

    // 点击已抽取单元格，打开右侧原文抽屉
    await wrapper.find('[data-test="cell-extracted-doc-1-sample_size"]').trigger("click");
    const drawer = wrapper.find('[data-test="extraction-evidence-drawer"]');
    expect(drawer.exists()).toBe(true);
    expect(drawer.find('[data-test="extraction-drawer-value"]').text()).toBe("N=320");
    expect(drawer.find('[data-test="extraction-cell-locator"]').text()).toBe(
      "出处定位：PDF 第 5 页 · 3.2 实验设置 · Table 2",
    );
    expect(drawer.find('[data-test="claim-highlight"]').text()).toBe("总样本量 N=320");
  });

  it("点击导出 CSV 触发下载请求，并透传 include_quotes 选项", async () => {
    const createUrl = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:mock-csv");
    const revokeUrl = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.mocked(exportExtractionTableCsv).mockResolvedValue({
      blob: new Blob(["\ufeffa,b\n1,2"], { type: "text/csv" }),
      filename: "RAG 论文对比表.csv",
    });

    const wrapper = mount(ExtractionTableView, {
      props: { tableId: "tbl-1" },
      global: { stubs: { Teleport: true } },
    });
    await flushPromises();

    await wrapper.find('[data-test="extraction-export-csv"]').trigger("click");
    await flushPromises();
    expect(exportExtractionTableCsv).toHaveBeenCalledWith("tbl-1", true);

    await wrapper.find('[data-test="extraction-export-include-quotes"]').setValue(false);
    await wrapper.find('[data-test="extraction-export-csv"]').trigger("click");
    await flushPromises();
    expect(exportExtractionTableCsv).toHaveBeenLastCalledWith("tbl-1", false);

    createUrl.mockRestore();
    revokeUrl.mockRestore();
  });

  it("添加列后仅对新列调用 /run 补抽，不重跑旧列", async () => {
    const withNewCol: ExtractionTableOut = {
      ...SAMPLE_TABLE,
      columns: [
        ...SAMPLE_TABLE.columns,
        { key: "code_url", label: "开源代码", prompt: "提取代码仓库地址" },
      ],
    };
    vi.mocked(addExtractionColumn).mockResolvedValue(withNewCol);
    vi.mocked(runExtractionTable).mockResolvedValue({ ...withNewCol, status: "running" });

    const wrapper = mount(ExtractionTableView, {
      props: { tableId: "tbl-1" },
      global: { stubs: { Teleport: true } },
    });
    await flushPromises();

    await wrapper.find('[data-test="extraction-add-col-toggle"]').trigger("click");
    await wrapper.find('[data-test="new-col-label"]').setValue("开源代码");
    await wrapper.find('[data-test="new-col-key"]').setValue("code_url");
    await wrapper.find('[data-test="new-col-prompt"]').setValue("提取代码仓库地址");
    await wrapper.find('[data-test="new-col-submit"]').trigger("click");
    await flushPromises();

    expect(addExtractionColumn).toHaveBeenCalledWith("tbl-1", {
      column: { key: "code_url", label: "开源代码", prompt: "提取代码仓库地址" },
      auto_run: false,
    });
    expect(runExtractionTable).toHaveBeenCalledWith("tbl-1", {
      column_keys: ["code_url"],
    });
  });

  it("固定首列使用实色背景与清晰边框分割，不采用模糊渐隐（Issue #56）", async () => {
    const wrapper = mount(ExtractionTableView, {
      props: { tableId: "tbl-1" },
      global: { stubs: { Teleport: true } },
    });
    await flushPromises();

    const stickyHeader = wrapper.get('[data-test="extraction-sticky-header"]');
    const className = stickyHeader.attributes("class") ?? "";
    expect(className).toContain("bg-muted");
    expect(className).not.toContain("bg-muted/90");
    expect(className).not.toContain("backdrop-blur");
  });
});
