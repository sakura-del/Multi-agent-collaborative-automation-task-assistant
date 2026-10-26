import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import ObservabilityView from "@/views/ObservabilityView.vue";

vi.mock("@/api/observability", () => ({
  getObservabilityOverview: vi.fn(),
}));

// 行点击要跳任务详情：把 router 换成可断言的 spy
const push = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push }),
}));

import { getObservabilityOverview } from "@/api/observability";

function overview(overrides: Record<string, unknown> = {}) {
  return {
    tasks: { total: 4, completed: 2, failed: 0, cancelled: 1, waiting_human: 1 },
    by_status: { completed: 2, cancelled: 1, waiting_human: 1 },
    terminal_count: 4,
    success_rate: 0.5,
    tokens: { total_used: 45_000, avg_per_task: 15_000, recorded_task_count: 3 },
    recent: [
      {
        task_id: "aaaaaaaa-1111",
        title: "检索增强生成评估方法综述",
        status: "completed",
        used_tokens: 30_000,
        duration_seconds: 92,
        created_at: "2026-10-23T13:00:00+08:00",
      },
      {
        task_id: "bbbbbbbb-2222",
        title: "大模型幻觉检测综述",
        status: "waiting_human",
        used_tokens: 5_000,
        duration_seconds: 300,
        created_at: "2026-10-22T09:00:00+08:00",
      },
    ],
    ...overrides,
  };
}

function mountView() {
  return mount(ObservabilityView);
}

beforeEach(() => {
  vi.mocked(getObservabilityOverview).mockReset();
  push.mockReset();
});

describe("ObservabilityView", () => {
  it("展示任务总数、成功率与 token 成本", async () => {
    vi.mocked(getObservabilityOverview).mockResolvedValue(overview() as never);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="observability-total"]').text()).toBe("4");
    // 后端给 0.5，前端显示 50.0%（格式化，不是重算口径）
    expect(wrapper.find('[data-test="observability-success-rate"]').text()).toBe("50.0%");
    expect(wrapper.find('[data-test="observability-token-total"]').text()).toBe("45,000");
    expect(wrapper.find('[data-test="observability-token-avg"]').text()).toBe("15,000");
  });

  it("成功率必须带上分母，否则用户无法核对", async () => {
    // 后端只给 0.5 时用户看不出分母是 4 还是 8；重设计后必须显示「2 / 4 终态任务」
    vi.mocked(getObservabilityOverview).mockResolvedValue(overview() as never);

    const wrapper = mountView();
    await flushPromises();

    const rate = wrapper.find('[data-test="observability-success-rate"]');
    expect(rate.text()).toContain("50.0%");
    const hint = wrapper.find('[data-test="observability-success-denominator"]');
    expect(hint.exists()).toBe(true);
    expect(hint.text()).toContain("2");
    expect(hint.text()).toContain("4");
  });

  it("状态分布覆盖全部状态，且合计等于任务总数（自洽）", async () => {
    // 实测缺陷：旧看板只列 4 个固定状态，demo 账号有 1 个 waiting_approval
    // 不在其中 → 页面出现「总数 5 / 分布合计 4」，差值不可见。
    vi.mocked(getObservabilityOverview).mockResolvedValue(
      overview({
        tasks: { total: 5, completed: 2, failed: 0, cancelled: 1, waiting_human: 1 },
        by_status: { completed: 2, cancelled: 1, waiting_human: 1, waiting_approval: 1 },
        terminal_count: 4,
        success_rate: 0.5,
        tokens: { total_used: 45_000, avg_per_task: 15_000, recorded_task_count: 3 },
      }) as never
    );

    const wrapper = mountView();
    await flushPromises();

    const badges = wrapper.find('[data-test="observability-statuses"]');
    // 待确认规划（waiting_approval）必须在分布里出现
    expect(badges.text()).toContain("待确认");
    // 计数合计 == total（这条是「看板不说谎」的硬断言）
    const sum = wrapper.find('[data-test="observability-status-sum"]');
    expect(sum.exists()).toBe(true);
    expect(sum.text()).toContain("5");
  });

  it("最近任务显示标题而不是只有 8 位 UUID", async () => {
    vi.mocked(getObservabilityOverview).mockResolvedValue(overview() as never);

    const wrapper = mountView();
    await flushPromises();

    const row = wrapper.find('[data-test="observability-row-aaaaaaaa-1111"]');
    expect(row.exists()).toBe(true);
    // 标题是唯一能让人认出「这是哪次运行」的信息
    expect(row.text()).toContain("检索增强生成评估方法综述");
  });

  it("点击最近任务行跳转到任务详情", async () => {
    vi.mocked(getObservabilityOverview).mockResolvedValue(overview() as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper.find('[data-test="observability-row-aaaaaaaa-1111"]').trigger("click");

    expect(push).toHaveBeenCalledWith({
      name: "task-detail",
      params: { taskId: "aaaaaaaa-1111" },
    });
  });

  it("最近任务行必须是可聚焦的按钮（键盘可达是硬要求）", async () => {
    // 实测缺陷：首版用 <li @click>，FOCUSABLE 列表里没有这些行 ——
    // 键盘用户完全进不去任务详情。AGENTS §6.5.3 要求所有可交互元素键盘可达。
    vi.mocked(getObservabilityOverview).mockResolvedValue(overview() as never);

    const wrapper = mountView();
    await flushPromises();

    const row = wrapper.find('[data-test="observability-row-aaaaaaaa-1111"]');
    expect(row.element.tagName).toBe("BUTTON");
    expect(row.attributes("type")).toBe("button");
  });

  it("展示最近任务行（按 task_id 定位）", async () => {
    vi.mocked(getObservabilityOverview).mockResolvedValue(overview() as never);

    const wrapper = mountView();
    await flushPromises();

    const row = wrapper.find('[data-test="observability-row-aaaaaaaa-1111"]');
    expect(row.exists()).toBe(true);
    expect(row.text()).toContain("已完成");
    expect(row.text()).toContain("30,000 token");
    // 92 秒 → 「1 分 32 秒」
    expect(row.text()).toContain("1 分 32 秒");
  });

  it("空数据显示引导空态，不显示概览卡片", async () => {
    vi.mocked(getObservabilityOverview).mockResolvedValue(
      {
        tasks: { total: 0, completed: 0, failed: 0, cancelled: 0, waiting_human: 0 },
        by_status: {},
        terminal_count: 0,
        success_rate: 0,
        tokens: { total_used: 0, avg_per_task: 0, recorded_task_count: 0 },
        recent: [],
      } as never
    );

    const wrapper = mountView();
    await flushPromises();

    const empty = wrapper.find('[data-test="observability-empty"]');
    expect(empty.exists()).toBe(true);
    // 空态必须给下一步引导（不能只写「暂无数据」）
    expect(empty.text()).toContain("创建");
    expect(wrapper.find('[data-test="observability-total"]').exists()).toBe(false);
  });

  it("成功率 0% 时也正常显示（不是空态、不崩）", async () => {
    vi.mocked(getObservabilityOverview).mockResolvedValue(
      overview({
        success_rate: 0,
        tasks: { total: 2, completed: 0, failed: 2, cancelled: 0, waiting_human: 0 },
        by_status: { failed: 2 },
        terminal_count: 2,
        tokens: { total_used: 0, avg_per_task: 0, recorded_task_count: 0 },
      }) as never
    );

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="observability-success-rate"]').text()).toBe("0.0%");
    expect(wrapper.find('[data-test="observability-empty"]').exists()).toBe(false);
  });

  it("加载失败时展示错误而非空态（避免把故障误报成没数据）", async () => {
    vi.mocked(getObservabilityOverview).mockRejectedValue(new Error("后端 500"));

    const wrapper = mountView();
    await flushPromises();

    const error = wrapper.find('[data-test="observability-error"]');
    expect(error.exists()).toBe(true);
    expect(error.text()).toContain("后端 500");
    // 关键：失败时**不能**显示空态，否则用户以为"我还没用过"
    expect(wrapper.find('[data-test="observability-empty"]').exists()).toBe(false);
  });

  it("状态分布只显示计数大于 0 的状态", async () => {
    vi.mocked(getObservabilityOverview).mockResolvedValue(overview() as never);

    const wrapper = mountView();
    await flushPromises();

    const badges = wrapper.find('[data-test="observability-statuses"]');
    expect(badges.text()).toContain("已完成 2");
    expect(badges.text()).toContain("待人工");
    // failed=0，不该出现
    expect(badges.text()).not.toContain("失败");
  });

  it("有历史任务但累计 token 为 0 时，要说明是「老数据未统计」而不是沉默", async () => {
    // 实测现状：demo 27 个任务，used_tokens 全是 0（修复前用量没落库）。
    // 只显示「累计 token 0」会让用户以为系统坏了 —— 必须区分「真没用过」与「没记录」。
    vi.mocked(getObservabilityOverview).mockResolvedValue(
      overview({
        tokens: { total_used: 0, avg_per_task: 0, recorded_task_count: 0 },
      }) as never
    );

    const wrapper = mountView();
    await flushPromises();

    const hint = wrapper.find('[data-test="observability-token-hint"]');
    expect(hint.exists()).toBe(true);
    expect(hint.text()).toContain("历史任务");
  });

  it("刷新按钮重新拉数据（看板是快照，不能只能靠 F5）", async () => {
    vi.mocked(getObservabilityOverview).mockResolvedValue(overview() as never);

    const wrapper = mountView();
    await flushPromises();
    expect(getObservabilityOverview).toHaveBeenCalledTimes(1);

    await wrapper.find('[data-test="observability-refresh"]').trigger("click");
    await flushPromises();

    expect(getObservabilityOverview).toHaveBeenCalledTimes(2);
  });
});
