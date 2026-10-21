import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import ObservabilityView from "@/views/ObservabilityView.vue";

vi.mock("@/api/observability", () => ({
  getObservabilityOverview: vi.fn(),
}));

import { getObservabilityOverview } from "@/api/observability";

function overview(overrides: Record<string, unknown> = {}) {
  return {
    tasks: { total: 4, completed: 2, failed: 0, cancelled: 1, waiting_human: 1 },
    success_rate: 0.5,
    tokens: { total_used: 45_000, avg_per_task: 11_250 },
    recent: [
      { task_id: "aaaaaaaa-1111", status: "completed", used_tokens: 30_000, duration_seconds: 92 },
      { task_id: "bbbbbbbb-2222", status: "waiting_human", used_tokens: 5_000, duration_seconds: 300 },
    ],
    ...overrides,
  };
}

function mountView() {
  return mount(ObservabilityView);
}

beforeEach(() => {
  vi.mocked(getObservabilityOverview).mockReset();
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
    expect(wrapper.find('[data-test="observability-token-avg"]').text()).toBe("11,250");
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
        success_rate: 0,
        tokens: { total_used: 0, avg_per_task: 0 },
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
      overview({ success_rate: 0, tasks: { total: 2, completed: 0, failed: 2, cancelled: 0, waiting_human: 0 } }) as never
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
    expect(badges.text()).toContain("待人工 1");
    // failed=0，不该出现
    expect(badges.text()).not.toContain("失败");
  });
});
