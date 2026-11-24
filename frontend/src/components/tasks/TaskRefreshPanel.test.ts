import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import TaskRefreshPanel from "@/components/tasks/TaskRefreshPanel.vue";
import type { TaskRefreshRun, TaskRoutine } from "@/api/types";

// Issue #27：持续更新面板的交互契约。
// 锁的是「什么时候出现、点下去打哪个接口、历史怎么读」，不锁版式细节。
vi.mock("@/api/tasks", () => ({
  taskApi: {
    refreshes: vi.fn(),
    routine: vi.fn(),
    startRefresh: vi.fn(),
    saveRoutine: vi.fn(),
  },
}));

import { taskApi } from "@/api/tasks";

const mocked = vi.mocked(taskApi);

function run(overrides: Partial<TaskRefreshRun> = {}): TaskRefreshRun {
  return {
    id: "r1",
    trigger: "manual",
    status: "completed",
    window_from: "2026-11-01T00:00:00Z",
    window_to: "2026-11-20T00:00:00Z",
    hits_total: 12,
    new_count: 3,
    duplicate_count: 9,
    sources: { arxiv: 12 },
    summary_markdown: "## 新增证据摘要\n\n- 要点 [E1]\n\n1. [E1] Paper 1",
    citation_ok: true,
    tokens_used: 120,
    error: null,
    started_at: "2026-11-20T03:00:00Z",
    finished_at: "2026-11-20T03:01:00Z",
    created_at: "2026-11-20T03:00:00Z",
    ...overrides,
  };
}

const routine: TaskRoutine = {
  task_id: "t1",
  enabled: true,
  cadence: "weekly",
  notify: "email",
  next_run_at: "2026-11-27T03:00:00Z",
  last_run_at: "2026-11-20T03:00:00Z",
};

function mountPanel(status = "completed") {
  return mount(TaskRefreshPanel, { props: { taskId: "t1", status } });
}

beforeEach(() => {
  mocked.refreshes.mockResolvedValue([]);
  mocked.routine.mockResolvedValue(null);
});

describe("持续更新面板", () => {
  it("未完成的任务不渲染面板", async () => {
    const wrapper = mountPanel("running");
    await flushPromises();
    expect(wrapper.find('[data-test="task-refresh"]').exists()).toBe(false);
  });

  it("已完成任务：显示按钮与排期控件", async () => {
    mocked.routine.mockResolvedValue(routine);
    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.find('[data-test="task-refresh"]').exists()).toBe(true);
    expect(wrapper.get('[data-test="refresh-now"]').text()).toContain("再次检索新文献");
    expect(wrapper.get('[data-test="routine-enabled"]').element).toHaveProperty("checked", true);
    expect(wrapper.get('[data-test="routine-next"]').text()).toContain("下次");
    wrapper.unmount();
  });

  it("点「再次检索新文献」会调用接口并入列一条进行中记录", async () => {
    mocked.startRefresh.mockResolvedValue(run({ status: "running", new_count: 0 }));
    const wrapper = mountPanel();
    await flushPromises();

    await wrapper.get('[data-test="refresh-now"]').trigger("click");
    await flushPromises();

    expect(mocked.startRefresh).toHaveBeenCalledWith("t1");
    expect(wrapper.get('[data-test="refresh-now"]').text()).toContain("检索中");
    wrapper.unmount();
  });

  it("历史记录：新增篇数与「本期无新增」文案不同", async () => {
    mocked.refreshes.mockResolvedValue([
      run({ id: "r2", new_count: 0, hits_total: 5, duplicate_count: 5 }),
      run({ id: "r1" }),
    ]);
    const wrapper = mountPanel();
    await flushPromises();

    const items = wrapper.findAll('[data-test="refresh-runs"] li');
    expect(items).toHaveLength(2);
    expect(items[0].text()).toContain("本期无新增");
    expect(items[1].text()).toContain("新增 3 篇");
    wrapper.unmount();
  });

  it("点历史行可以展开/收起摘要", async () => {
    // 最新一次「无新增」不会自动展开，点一下才显示
    mocked.refreshes.mockResolvedValue([run({ new_count: 0 })] as never);
    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.find('[data-test="refresh-summary"]').exists()).toBe(false);
    await wrapper.find('[data-test="refresh-runs"] li button').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-test="refresh-summary"]').exists()).toBe(true);

    await wrapper.find('[data-test="refresh-runs"] li button').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-test="refresh-summary"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("切换排期频率会保存 cadence", async () => {
    mocked.routine.mockResolvedValue(routine);
    mocked.saveRoutine.mockResolvedValue({ ...routine, cadence: "monthly" });
    const wrapper = mountPanel();
    await flushPromises();

    await wrapper.get('[data-test="routine-cadence"]').setValue("monthly");
    await flushPromises();

    expect(mocked.saveRoutine).toHaveBeenCalledWith("t1", {
      enabled: true,
      cadence: "monthly",
      notify: "email",
    });
    wrapper.unmount();
  });

  it("关闭「定时重跑」会保存 enabled=false", async () => {
    mocked.routine.mockResolvedValue(routine);
    mocked.saveRoutine.mockResolvedValue({ ...routine, enabled: false });
    const wrapper = mountPanel();
    await flushPromises();

    await wrapper.get('[data-test="routine-enabled"]').setValue(false);
    await flushPromises();

    expect(mocked.saveRoutine).toHaveBeenCalledWith("t1", expect.objectContaining({ enabled: false }));
    wrapper.unmount();
  });

  it("监控记忆：存在 memory_brief 时展示待补空白与失效检索式，旧接口无该字段时不渲染", async () => {
    // 旧接口无 memory_brief 时不渲染也不报错
    mocked.routine.mockResolvedValue(routine);
    const legacy = mountPanel();
    await flushPromises();
    expect(legacy.find('[data-test="routine-memory-strip"]').exists()).toBe(false);
    legacy.unmount();

    // 带 open_gaps 与 stale_queries 时正确渲染提示条
    mocked.routine.mockResolvedValue({
      ...routine,
      memory_brief: {
        topic_summary: "原综述基线",
        open_gaps: ["长上下文延迟瓶颈", "跨模态幻觉实证"],
        recent_updates: ["本期新增 2 篇"],
        stale_queries: ["old narrow query"],
        updated_at: "2026-11-24T08:00:00Z",
      },
    });
    const wrapper = mountPanel();
    await flushPromises();

    const strip = wrapper.get('[data-test="routine-memory-strip"]');
    expect(strip.text()).toContain("已跟踪 2 项待补研究空白");
    const stale = wrapper.get('[data-test="routine-stale-queries"]');
    expect(stale.text()).toContain("old narrow query");
    wrapper.unmount();
  });
});
