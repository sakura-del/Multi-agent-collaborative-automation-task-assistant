import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import DOMPurify from "dompurify";

import { useTheme } from "@/composables/useTheme";
import ScreeningView from "@/views/ScreeningView.vue";

const { mermaidInitializeMock, mermaidRenderMock, toastSuccessMock, toastErrorMock } = vi.hoisted(() => ({
  mermaidInitializeMock: vi.fn(),
  mermaidRenderMock: vi.fn(async (_id: string, _code: string) => ({
    svg: '<svg data-mock="default"><g></g></svg>',
  })),
  toastSuccessMock: vi.fn(),
  toastErrorMock: vi.fn(),
}));

vi.mock("@/lib/toast", () => ({
  toastSuccess: toastSuccessMock,
  toastError: toastErrorMock,
}));

vi.mock("mermaid", () => ({
  default: {
    initialize: mermaidInitializeMock,
    render: mermaidRenderMock,
  },
}));

vi.mock("@/api/screening", () => ({
  recordSearch: vi.fn(),
  listScreeningRecords: vi.fn(),
  createDecision: vi.fn(),
  getPrismaCounts: vi.fn(),
  getScreeningPriority: vi.fn(),
}));

import {
  createDecision,
  getPrismaCounts,
  getScreeningPriority,
  listScreeningRecords,
} from "@/api/screening";
import type { ScreeningPriority } from "@/api/types";

function recordRow(overrides: Record<string, unknown> = {}) {
  return {
    record_id: "r1",
    source: "arxiv",
    source_id: "a1",
    title: "RAG 综述",
    authors: ["张三"],
    abstract: "这是一篇综述摘要",
    doi: "10.1234/rag",
    fulltext_status: "unavailable",
    current_stage: "title_abstract",
    current_verdict: null,
    current_exclusion_reason: null,
    ...overrides,
  };
}

const PRISMA = {
  identified_total: 50,
  duplicates_removed: 49,
  records_screened: 1,
  included: 0,
  identified_by_source: { arxiv: 30, openalex: 20 },
  title_abstract: { screened: 0, excluded: 0 },
  fulltext: { assessed: 0, excluded: 0 },
  exclusion_reasons: {},
  markdown: "# PRISMA 2020 流程图数据",
  mermaid: "flowchart TD\n    A[\"识别 n=50\"] --> E[\"纳入 n=0\"]",
};

describe("ScreeningView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listScreeningRecords).mockResolvedValue([recordRow()]);
    vi.mocked(getPrismaCounts).mockResolvedValue(PRISMA);
  });

  function priorityResult(overrides: Partial<ScreeningPriority> = {}): ScreeningPriority {
    return {
      status: "ready",
      algorithm: "tfidf-char-logreg-v1",
      stage: "title_abstract",
      items: [],
      labels: { positive: 1, negative: 1, ignored: 0 },
      candidates: 0,
      message: "基于任务内 1 条纳入 / 1 条排除决策排序",
      ...overrides,
    };
  }

  it("展示候选记录与当前阶段", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    const row = wrapper.find('[data-test="screening-row-r1"]');
    expect(row.exists()).toBe(true);
    expect(row.text()).toContain("RAG 综述");
    expect(row.text()).toContain("标题/摘要");
  });

  it("展示 DOI 与来源标识", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    const row = wrapper.find('[data-test="screening-row-r1"]');
    expect(row.text()).toContain("10.1234/rag");
    expect(row.text()).toContain("arxiv:a1");
  });

  it("纳入按钮调用 createDecision 并刷新", async () => {
    vi.mocked(createDecision).mockResolvedValue({
      record_id: "r1",
      current_stage: "fulltext",
    });

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    await wrapper.find('[data-test="screening-include-r1"]').trigger("click");
    await flushPromises();

    expect(createDecision).toHaveBeenCalledWith("r1", {
      stage: "title_abstract",
      verdict: "include",
    });
    expect(wrapper.text()).toContain("已纳入");
  });

  it("纳入失败时展示后端 detail（而非吞掉）", async () => {
    vi.mocked(createDecision).mockRejectedValue(new Error("网络错误"));

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    await wrapper.find('[data-test="screening-include-r1"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="screening-error"]').text()).toContain("网络错误");
  });

  it("排除必须填写理由后才提交", async () => {
    vi.mocked(createDecision).mockResolvedValue({
      record_id: "r1",
      current_stage: "title_abstract",
    });

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    await wrapper.find('[data-test="screening-exclude-r1"]').trigger("click");
    await flushPromises();

    expect(createDecision).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="screening-exclude-form-r1"]').exists()).toBe(true);

    await wrapper.find('[data-test="screening-exclude-reason-r1"]').setValue("不相关");
    await wrapper.find('[data-test="screening-exclude-submit-r1"]').trigger("click");
    await flushPromises();

    expect(createDecision).toHaveBeenCalledWith("r1", {
      stage: "title_abstract",
      verdict: "exclude",
      exclusion_reason: "不相关",
    });
  });

  it("排除理由为空时前端先拦（不发请求）", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    await wrapper.find('[data-test="screening-exclude-r1"]').trigger("click");
    await flushPromises();
    // 只输入空白字符
    await wrapper.find('[data-test="screening-exclude-reason-r1"]').setValue("   ");
    await wrapper.find('[data-test="screening-exclude-submit-r1"]').trigger("click");
    await flushPromises();

    expect(createDecision).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="screening-error"]').exists()).toBe(true);
  });

  it("maybe 按钮调用 createDecision 且不推进阶段", async () => {
    vi.mocked(createDecision).mockResolvedValue({
      record_id: "r1",
      current_stage: "title_abstract",
    });

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    await wrapper.find('[data-test="screening-maybe-r1"]').trigger("click");
    await flushPromises();

    expect(createDecision).toHaveBeenCalledWith("r1", {
      stage: "title_abstract",
      verdict: "maybe",
    });
  });

  it("展示 PRISMA 计数（识别/去重后/纳入）", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    const prisma = wrapper.find('[data-test="screening-prisma"]');
    expect(prisma.exists()).toBe(true);
    expect(prisma.text()).toContain("50");
    expect(prisma.text()).toContain("1");
  });

  it("展示 Mermaid 源码（可复制进论文）", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    const block = wrapper.find('[data-test="screening-mermaid"]');
    expect(block.text()).toContain("flowchart TD");
  });

  it("已纳入的记录不再显示决策按钮", async () => {
    vi.mocked(listScreeningRecords).mockResolvedValue([
      recordRow({ record_id: "r2", current_stage: "included" }),
    ]);

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    expect(wrapper.find('[data-test="screening-include-r2"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="screening-exclude-r2"]').exists()).toBe(false);
  });

  it("已被我排除的记录不再显示决策按钮，并标出排除理由", async () => {
    // 排除是终态（后端会返回 400）：不该给用户一个注定失败的按钮。
    vi.mocked(listScreeningRecords).mockResolvedValue([
      recordRow({
        record_id: "r3",
        current_verdict: "exclude",
        current_exclusion_reason: "not relevant",
      }),
    ]);

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    expect(wrapper.find('[data-test="screening-include-r3"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="screening-maybe-r3"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="screening-exclude-r3"]').exists()).toBe(false);
    const badge = wrapper.find('[data-test="screening-excluded-badge-r3"]');
    expect(badge.exists()).toBe(true);
    expect(badge.text()).toContain("not relevant");
  });

  it("待定的记录仍可继续决策，并显示待定徽标", async () => {
    vi.mocked(listScreeningRecords).mockResolvedValue([
      recordRow({ record_id: "r4", current_verdict: "maybe" }),
    ]);

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    expect(wrapper.find('[data-test="screening-include-r4"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="screening-maybe-badge-r4"]').exists()).toBe(true);
  });

  it("按阶段过滤候选记录", async () => {
    vi.mocked(listScreeningRecords).mockResolvedValue([
      recordRow({ record_id: "r1", current_stage: "title_abstract" }),
      recordRow({ record_id: "r2", current_stage: "fulltext", title: "另一篇" }),
    ]);

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    expect(wrapper.find('[data-test="screening-row-r1"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="screening-row-r2"]').exists()).toBe(true);

    await wrapper.find('[data-test="screening-stage-filter"]').setValue("fulltext");
    await flushPromises();

    expect(listScreeningRecords).toHaveBeenLastCalledWith("task-1", "fulltext");
  });

  it("空记录时展示明确引导", async () => {
    vi.mocked(listScreeningRecords).mockResolvedValue([]);

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    const empty = wrapper.find('[data-test="screening-empty"]');
    expect(empty.exists()).toBe(true);
    expect(empty.text()).not.toContain("暂无数据");
  });

  it("排除成功后收起表单（不重复挂在那里）", async () => {
    vi.mocked(createDecision).mockResolvedValue({
      record_id: "r1",
      current_stage: "title_abstract",
    });

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    await wrapper.find('[data-test="screening-exclude-r1"]').trigger("click");
    await wrapper.find('[data-test="screening-exclude-reason-r1"]').setValue("不相关");

    // 关键：在「提交后、刷新完成前」这一瞬间断言表单已收起。
    // 若实现是「刷新完才收起」，此时表单仍在（用户会以为没提交、重复点击）。
    // 不能让 createDecision 立刻 resolve：用一个可控的 deferred 卡住那一帧。
    let releaseDecision!: () => void;
    vi.mocked(createDecision).mockReturnValue(
      new Promise((resolve) => {
        releaseDecision = () =>
          resolve({ record_id: "r1", current_stage: "title_abstract" });
      })
    );

    await wrapper.find('[data-test="screening-exclude-submit-r1"]').trigger("click");
    await wrapper.vm.$nextTick();

    expect(
      wrapper.find('[data-test="screening-exclude-form-r1"]').exists(),
      "提交后表单必须立刻收起，不能等刷新回来"
    ).toBe(false);

    releaseDecision();
    await flushPromises();
    expect(createDecision).toHaveBeenCalled();
  });

  it("Pin-in-Place：决策成功后不整表刷新记录，仅原地更新并原子刷新 PRISMA 计数", async () => {
    vi.mocked(createDecision).mockResolvedValue({
      record_id: "r1",
      current_stage: "fulltext",
    });
    const updatedPrisma = { ...PRISMA, title_abstract: { screened: 1, excluded: 0 } };
    vi.mocked(getPrismaCounts)
      .mockResolvedValueOnce(PRISMA)
      .mockResolvedValueOnce(updatedPrisma);

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    const recordsCallsBefore = vi.mocked(listScreeningRecords).mock.calls.length;
    const prismaCallsBefore = vi.mocked(getPrismaCounts).mock.calls.length;

    await wrapper.find('[data-test="screening-include-r1"]').trigger("click");
    await flushPromises();

    // 不整表重刷列表（避免滚动跳位）
    expect(vi.mocked(listScreeningRecords).mock.calls.length).toBe(recordsCallsBefore);
    // 独立静默刷新 PRISMA 计数
    expect(vi.mocked(getPrismaCounts).mock.calls.length).toBe(prismaCallsBefore + 1);
    // 原地更新阶段徽标
    expect(wrapper.find('[data-test="screening-row-r1"]').text()).toContain("全文");
  });

  it("默认关闭时不请求主动排序", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    expect(getScreeningPriority).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="screening-priority-toggle"]').attributes("aria-pressed")).toBe("false");
  });

  it("开启后把推荐候选置顶并保留其他记录", async () => {
    vi.mocked(listScreeningRecords).mockResolvedValue([
      recordRow({ record_id: "r1", title: "第一篇" }),
      recordRow({ record_id: "r2", title: "第二篇" }),
      recordRow({ record_id: "r3", title: "第三篇" }),
    ]);
    vi.mocked(getScreeningPriority).mockResolvedValue(
      priorityResult({
        items: [
          { record_id: "r2", rank: 1 },
          { record_id: "r1", rank: 2 },
        ],
        candidates: 2,
      })
    );

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    await wrapper.find('[data-test="screening-priority-toggle"]').trigger("click");
    await flushPromises();

    const ids = wrapper
      .findAll('[data-test^="screening-row-"]')
      .map((row) => row.attributes("data-test"));
    expect(ids).toEqual(["screening-row-r2", "screening-row-r1", "screening-row-r3"]);
    expect(wrapper.find('[data-test="screening-priority-rank-r2"]').text()).toContain("#1");
    expect(wrapper.find('[data-test="screening-priority-rank-r3"]').exists()).toBe(false);
  });

  it("标签不足时保持原序并显示提示", async () => {
    vi.mocked(listScreeningRecords).mockResolvedValue([
      recordRow({ record_id: "r1", title: "第一篇" }),
      recordRow({ record_id: "r2", title: "第二篇" }),
    ]);
    vi.mocked(getScreeningPriority).mockResolvedValue(
      priorityResult({
        status: "insufficient_labels",
        items: [],
        candidates: 2,
        message: "至少需要 1 条纳入和 1 条排除决策（当前 1 / 0）",
      })
    );

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    await wrapper.find('[data-test="screening-priority-toggle"]').trigger("click");
    await flushPromises();

    const ids = wrapper
      .findAll('[data-test^="screening-row-"]')
      .map((row) => row.attributes("data-test"));
    expect(ids).toEqual(["screening-row-r1", "screening-row-r2"]);
    expect(wrapper.find('[data-test="screening-priority-status"]').text()).toContain("至少需要");
  });

  it("排序请求失败时保持原序并显示错误", async () => {
    vi.mocked(getScreeningPriority).mockRejectedValue(new Error("排序服务不可用"));

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    await wrapper.find('[data-test="screening-priority-toggle"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="screening-priority-status"]').text()).toContain("排序服务不可用");
    expect(wrapper.find('[data-test="screening-row-r1"]').exists()).toBe(true);
  });

  it("开启后提交决策不自动重排，显示手动重排按钮，点击后才刷新排序", async () => {
    vi.mocked(getScreeningPriority).mockResolvedValue(priorityResult());
    vi.mocked(createDecision).mockResolvedValue({
      record_id: "r1",
      current_stage: "fulltext",
    });

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    await wrapper.find('[data-test="screening-priority-toggle"]').trigger("click");
    await flushPromises();
    const before = vi.mocked(getScreeningPriority).mock.calls.length;

    await wrapper.find('[data-test="screening-include-r1"]').trigger("click");
    await flushPromises();

    // 不打断用户：提交决策后不自动重排
    expect(vi.mocked(getScreeningPriority).mock.calls.length).toBe(before);
    const reorderBtn = wrapper.find('[data-test="screening-priority-reorder"]');
    expect(reorderBtn.exists()).toBe(true);
    expect(reorderBtn.text()).toContain("点击重排");

    await reorderBtn.trigger("click");
    await flushPromises();
    expect(vi.mocked(getScreeningPriority).mock.calls.length).toBe(before + 1);
    expect(wrapper.find('[data-test="screening-priority-reorder"]').exists()).toBe(false);
  });

  it("切到全文阶段会关闭并禁用智能排序", async () => {
    vi.mocked(getScreeningPriority).mockResolvedValue(priorityResult());

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    await wrapper.find('[data-test="screening-priority-toggle"]').trigger("click");
    await flushPromises();

    await wrapper.find('[data-test="screening-stage-filter"]').setValue("fulltext");
    await flushPromises();

    const toggle = wrapper.find('[data-test="screening-priority-toggle"]');
    expect(toggle.attributes("aria-pressed")).toBe("false");
    expect(toggle.attributes("disabled")).toBeDefined();
    expect(wrapper.find('[data-test="screening-priority-status"]').exists()).toBe(false);
  });

  it("存在已纳入文献时显示「生成数据抽取表」按钮，点击后弹窗展示摘要抽取提示", async () => {
    const includedRow = recordRow({ record_id: "r-inc", current_stage: "included", title: "纳入文献 A" });
    vi.mocked(listScreeningRecords).mockResolvedValue([includedRow]);
    vi.mocked(getPrismaCounts).mockResolvedValue({ ...PRISMA, included: 1 });

    const wrapper = mount(ScreeningView, {
      props: { taskId: "task-1" },
      global: { stubs: { Teleport: true } },
    });
    await flushPromises();

    const trigger = wrapper.find('[data-test="screening-create-extraction"]');
    expect(trigger.exists()).toBe(true);
    expect(trigger.text()).toContain("生成数据抽取表");

    await trigger.trigger("click");
    await flushPromises();

    const dialog = wrapper.find('[data-test="create-extraction-dialog"]');
    expect(dialog.exists()).toBe(true);
    expect(dialog.find('[data-test="extraction-abstract-warning"]').text()).toContain(
      "外部检索文献仅基于摘要抽取，未提及字段将标为未报告",
    );
  });

  it("source_id 已带前缀时不重复拼接 source（避免出现 arxiv:doi:...，Issue #59）", async () => {
    const row = recordRow({
      record_id: "r-doi",
      source: "arxiv",
      source_id: "doi:10.48550/arxiv.2005.11401",
      title: "RAG Paper",
    });
    vi.mocked(listScreeningRecords).mockResolvedValue([row]);
    vi.mocked(getPrismaCounts).mockResolvedValue(PRISMA);

    const wrapper = mount(ScreeningView, {
      props: { taskId: "task-1" },
      global: { stubs: { Teleport: true } },
    });
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("doi:10.48550/arxiv.2005.11401");
    expect(text).not.toContain("arxiv:doi:");
  });

  it("Pin-in-Place 决策失败时回滚乐观状态并展示行级温和回弹提示", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(createDecision).mockRejectedValue(new Error("服务器繁忙"));

      const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
      await flushPromises();

      const row = wrapper.find('[data-test="screening-row-r1"]');
      expect(row.text()).toContain("标题/摘要");

      await wrapper.find('[data-test="screening-include-r1"]').trigger("click");
      await flushPromises();

      expect(row.text()).toContain("标题/摘要");
      expect(row.text()).not.toContain("全文");
      expect(row.classes()).toContain("ring-destructive/60");
      expect(wrapper.find('[data-test="screening-error"]').text()).toContain("服务器繁忙");

      vi.advanceTimersByTime(850);
      await flushPromises();
      expect(row.classes()).not.toContain("ring-destructive/60");
    } finally {
      vi.useRealTimers();
    }
  });

  it("排除表单支持取消按钮与 Esc 键收起，且不发请求", async () => {
    vi.useFakeTimers();
    try {
      const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
      await flushPromises();

      await wrapper.find('[data-test="screening-exclude-r1"]').trigger("click");
      await flushPromises();
      expect(wrapper.find('[data-test="screening-exclude-form-r1"]').exists()).toBe(true);

      const cancelBtn = wrapper.find('[data-test="screening-exclude-cancel-r1"]');
      expect(cancelBtn.exists()).toBe(true);
      await cancelBtn.trigger("click");
      vi.advanceTimersByTime(220);
      await flushPromises();
      expect(wrapper.find('[data-test="screening-exclude-form-r1"]').exists()).toBe(false);
      expect(createDecision).not.toHaveBeenCalled();

      // 再次打开并用 Esc 关闭
      await wrapper.find('[data-test="screening-exclude-r1"]').trigger("click");
      await flushPromises();
      await wrapper.find('[data-test="screening-exclude-reason-r1"]').trigger("keydown", { key: "Escape" });
      vi.advanceTimersByTime(220);
      await flushPromises();
      expect(wrapper.find('[data-test="screening-exclude-form-r1"]').exists()).toBe(false);
      expect(createDecision).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("加载挂起期间不显示空态（!loading 守卫）", async () => {
    vi.mocked(listScreeningRecords).mockReturnValue(new Promise(() => {}) as never);
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    expect(wrapper.find('[data-test="screening-empty"]').exists()).toBe(false);
  });

  it("IME 组合输入（isComposing / keyCode=229）按 Esc 与 Enter 不误关闭或误提交排除表单", async () => {
    vi.mocked(createDecision).mockResolvedValue({
      record_id: "r1",
      current_stage: "title_abstract",
    });

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    await wrapper.find('[data-test="screening-exclude-r1"]').trigger("click");
    await flushPromises();
    const reasonInput = wrapper.get('[data-test="screening-exclude-reason-r1"]');
    await reasonInput.setValue("中文输入中");

    // 1) isComposing: true 按 Esc 不得关闭表单
    await reasonInput.trigger("keydown", { key: "Escape", isComposing: true });
    await flushPromises();
    expect(reasonInput.element.closest('[aria-hidden="true"]')).toBeNull();
    expect(createDecision).not.toHaveBeenCalled();

    // 2) isComposing: true 按 Enter 不得提交
    await reasonInput.trigger("keydown", { key: "Enter", isComposing: true });
    await flushPromises();
    expect(createDecision).not.toHaveBeenCalled();
    expect(reasonInput.element.closest('[aria-hidden="true"]')).toBeNull();

    // 3) keyCode === 229 按 Esc / Enter 同样不得关闭或提交
    await reasonInput.trigger("keydown", { key: "Escape", keyCode: 229 });
    await reasonInput.trigger("keydown", { key: "Enter", keyCode: 229 });
    await flushPromises();
    expect(createDecision).not.toHaveBeenCalled();
    expect(reasonInput.element.closest('[aria-hidden="true"]')).toBeNull();

    // 4) 结束组合后按 Enter 正常提交
    await reasonInput.trigger("keydown", { key: "Enter", isComposing: false });
    await flushPromises();
    expect(createDecision).toHaveBeenCalledWith("r1", {
      stage: "title_abstract",
      verdict: "exclude",
      exclusion_reason: "中文输入中",
    });
  });

  it("并发操作多条记录时保持各自独立的禁用锁，互不提前解锁", async () => {
    vi.mocked(listScreeningRecords).mockResolvedValue([
      recordRow({ record_id: "r1", title: "第一篇" }),
      recordRow({ record_id: "r2", title: "第二篇" }),
    ]);

    let resolveR1!: (v: { record_id: string; current_stage: string }) => void;
    let resolveR2!: (v: { record_id: string; current_stage: string }) => void;
    vi.mocked(createDecision)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveR1 = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveR2 = resolve;
          }),
      );

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    const r1Include = wrapper.get('[data-test="screening-include-r1"]');
    const r2Include = wrapper.get('[data-test="screening-include-r2"]');

    await r1Include.trigger("click");
    await r2Include.trigger("click");
    await wrapper.vm.$nextTick();

    // 两条记录同时在请求中：各自按钮均保持 disabled
    expect(r1Include.attributes("disabled")).toBeDefined();
    expect(r2Include.attributes("disabled")).toBeDefined();

    // r2 先返回：r2 解锁，r1 必须仍然保持 disabled
    resolveR2({ record_id: "r2", current_stage: "fulltext" });
    await flushPromises();
    expect(r2Include.attributes("disabled")).toBeUndefined();
    expect(r1Include.attributes("disabled")).toBeDefined();

    // r1 返回后才解锁
    resolveR1({ record_id: "r1", current_stage: "fulltext" });
    await flushPromises();
    expect(r1Include.attributes("disabled")).toBeUndefined();
  });

  it("排除理由为空提交时渲染 aria-invalid=\"true\" 并聚焦到输入框（R4）", async () => {
    const hostEl = document.createElement("div");
    document.body.appendChild(hostEl);
    const wrapper = mount(ScreeningView, {
      attachTo: hostEl,
      props: { taskId: "task-1" },
    });
    try {
      await flushPromises();

      await wrapper.get('[data-test="screening-exclude-r1"]').trigger("click");
      await flushPromises();

      const reasonInput = wrapper.get('[data-test="screening-exclude-reason-r1"]');
      await wrapper.get('[data-test="screening-exclude-submit-r1"]').trigger("click");
      await flushPromises();

      expect(reasonInput.attributes("aria-invalid")).toBe("true");
      expect(document.activeElement).toBe(reasonInput.element);
    } finally {
      wrapper.unmount();
      hostEl.remove();
    }
  });

  it("成功提示 [data-test=\"screening-success\"] 留在组件子树内且避开右上角 Toaster 与右下角悬浮球", async () => {
    vi.mocked(createDecision).mockResolvedValue({
      record_id: "r1",
      current_stage: "fulltext",
    });

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    await wrapper.get('[data-test="screening-include-r1"]').trigger("click");
    await flushPromises();

    const banner = wrapper.find('[data-test="screening-success"]');
    expect(banner.exists()).toBe(true);
    const cls = (banner.attributes("class") ?? "").split(/\s+/);
    expect(cls).not.toContain("top-16");
    expect(cls).toContain("fixed");
    expect(cls).toContain("bottom-5");
    expect(cls).toContain("right-20");
  });

  it("连续筛选决策仅更新底部 screening-success，不重复弹顶部 toastSuccess 避免移动端遮挡", async () => {
    vi.mocked(listScreeningRecords).mockResolvedValue([
      recordRow({ record_id: "r1", title: "第一篇" }),
      recordRow({ record_id: "r2", title: "第二篇" }),
    ]);
    vi.mocked(createDecision).mockResolvedValue({
      record_id: "r1",
      current_stage: "title_abstract",
    });

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    await wrapper.get('[data-test="screening-maybe-r1"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-test="screening-success"]').text()).toContain("待定「第一篇」");

    await wrapper.get('[data-test="screening-include-r2"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-test="screening-success"]').text()).toContain("已纳入「第二篇」");

    await wrapper.get('[data-test="screening-exclude-r1"]').trigger("click");
    await wrapper.get('[data-test="screening-exclude-reason-r1"]').setValue("主题无关");
    await wrapper.get('[data-test="screening-exclude-submit-r1"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-test="screening-success"]').text()).toContain("已排除「第一篇」");

    expect(toastSuccessMock).not.toHaveBeenCalled();

    // 失败场景仍触发 toastError；复制 Mermaid 源码仍正常使用 toastSuccess
    vi.mocked(createDecision).mockRejectedValueOnce(new Error("网络异常"));
    await wrapper.get('[data-test="screening-maybe-r2"]').trigger("click");
    await flushPromises();
    expect(toastErrorMock).toHaveBeenCalledWith("网络异常");

    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: writeTextMock },
      configurable: true,
    });
    await wrapper.get('[data-test="screening-mermaid-copy"]').trigger("click");
    await flushPromises();
    expect(toastSuccessMock).toHaveBeenCalledWith("已复制 Mermaid 源码");
  });

  it("Mermaid 异步渲染响应深浅主题并丢弃过期请求结果", async () => {
    const { setTheme } = useTheme();
    setTheme("light");
    const sanitizeSpy = vi
      .spyOn(DOMPurify, "sanitize")
      .mockImplementation((dirty) => String(dirty));

    const raceMermaid = "flowchart TD\n    RACE_START --> RACE_END";
    vi.mocked(getPrismaCounts).mockResolvedValue({ ...PRISMA, mermaid: raceMermaid });

    let wrapper: ReturnType<typeof mount> | null = null;
    try {
      let rejectFirst!: (err: Error) => void;
      let resolveSecond!: (v: { svg: string }) => void;
      let resolveThird!: (v: { svg: string }) => void;
      let resolveFourth!: (v: { svg: string }) => void;
      let raceCall = 0;

      mermaidRenderMock.mockImplementation((_id: string, code: string) => {
        if (code !== raceMermaid) {
          return Promise.resolve({ svg: '<svg data-mock="other"><g></g></svg>' });
        }
        raceCall += 1;
        if (raceCall === 1) {
          return new Promise<{ svg: string }>((_, reject) => {
            rejectFirst = reject;
          });
        }
        if (raceCall === 2) {
          return new Promise<{ svg: string }>((resolve) => {
            resolveSecond = resolve;
          });
        }
        if (raceCall === 3) {
          return new Promise<{ svg: string }>((resolve) => {
            resolveThird = resolve;
          });
        }
        return new Promise<{ svg: string }>((resolve) => {
          resolveFourth = resolve;
        });
      });

      wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
      await vi.waitFor(() => expect(raceCall).toBe(1));
      expect(mermaidInitializeMock).toHaveBeenCalledWith(
        expect.objectContaining({ securityLevel: "strict", theme: "neutral" }),
      );

      // 在首次渲染尚未返回时切换为深色模式触发第二次渲染
      setTheme("dark");
      await vi.waitFor(() => expect(raceCall).toBe(2));
      expect(mermaidInitializeMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ securityLevel: "strict", theme: "dark" }),
      );

      // 第二次（较新）先返回
      resolveSecond({ svg: '<svg data-ver="latest"><text>latest</text></svg>' });
      await flushPromises();
      expect(sanitizeSpy).toHaveBeenCalledWith(
        '<svg data-ver="latest"><text>latest</text></svg>',
        { USE_PROFILES: { svg: true, svgFilters: true } },
      );
      expect(wrapper.find('[data-test="screening-mermaid-diagram"]').html()).toContain("latest");

      // 第一次（过期）后返回报错，不得覆盖最新渲染或置错状态
      rejectFirst(new Error("stale mermaid error"));
      await flushPromises();
      const diagramHtml = wrapper.find('[data-test="screening-mermaid-diagram"]').html();
      expect(diagramHtml).toContain("latest");
      expect(wrapper.text()).not.toContain("流程图预览暂不可用");

      // 再验证过期成功返回同样被丢弃
      setTheme("light");
      await vi.waitFor(() => expect(raceCall).toBe(3));
      setTheme("dark");
      await vi.waitFor(() => expect(raceCall).toBe(4));

      resolveFourth({ svg: '<svg data-ver="v4"><text>v4-fresh</text></svg>' });
      await flushPromises();
      resolveThird({ svg: '<svg data-ver="v3"><text>v3-stale</text></svg>' });
      await flushPromises();

      const updatedHtml = wrapper.find('[data-test="screening-mermaid-diagram"]').html();
      expect(updatedHtml).toContain("v4-fresh");
      expect(updatedHtml).not.toContain("v3-stale");
    } finally {
      wrapper?.unmount();
      mermaidRenderMock.mockReset();
      mermaidRenderMock.mockImplementation(async () => ({
        svg: '<svg data-mock="default"><g></g></svg>',
      }));
      sanitizeSpy.mockRestore();
      setTheme("light");
    }
  });
});
