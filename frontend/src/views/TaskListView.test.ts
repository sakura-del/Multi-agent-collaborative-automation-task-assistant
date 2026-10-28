import { describe, expect, it, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createRouter, createMemoryHistory } from "vue-router";

import TaskListView from "@/views/TaskListView.vue";
import type { TaskSummary } from "@/api/types";

/**
 * 任务列表页重设计（2026-10-23 第二阶段）。
 *
 * ## 为什么要重设计
 *
 * 实测：20 行里 19 行标题几乎一样，唯一区分是括号后缀，而它被排成普通正文
 * ——视觉上是一堵同质化的墙。更严重的是「进度」列：
 *
 * ```ts
 * failed: 100,      // 失败也显示 100% 满格蓝
 * completed: 100,   // 与完成视觉完全相同
 * ```
 *
 * `taskProgress()` 是从状态映射的百分比，不是真实进度 —— 这一列几乎不传递信息。
 *
 * ## 本文件锁什么
 *
 * 锁**信息架构与可操作性**，不锁版式细节：
 * 1. 标题拆成「主标题 + 次级标记」（括号后缀降级，不再与主题同权重）；
 * 2. 进度条移除（不再有说谎的百分比），改用状态图标区分终态；
 * 3. 有汇总统计（「待我确认」单独突出 —— 它是唯一需要用户行动的）；
 * 4. 有状态筛选与标题搜索；
 * 5. 保留全部既有测试契约（`data-test`）。
 */

const stub = { template: "<div />" };

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: stub },
      { path: "/tasks", name: "tasks", component: stub },
      { path: "/tasks/:taskId", name: "task-detail", component: stub },
    ],
  });
}

function task(overrides: Partial<TaskSummary> = {}): TaskSummary {
  return {
    task_id: "t1",
    title: "检索增强生成评估方法综述（Issue #6 复验 #3）",
    status: "completed",
    plan_version: 1,
    revision: 2,
    created_at: "2026-10-19T11:28:17Z",
    updated_at: "2026-10-19T11:28:17Z",
    ...overrides,
  } as TaskSummary;
}

async function mountWith(tasks: TaskSummary[]) {
  setActivePinia(createPinia());
  const router = makeRouter();
  await router.push("/tasks");
  await router.isReady();

  // 直接注入 store 状态，避免打网络。
  // `loadList` 必须 stub 掉：组件 `onMounted` 会调它，真跑会尝试连后端
  // （ECONNREFUSED 后把 items 清空，空态用例因此看不到 empty-state）。
  const { useTaskStore } = await import("@/stores/tasks");
  const store = useTaskStore();
  store.items = tasks;
  store.loadList = async () => {};
  store.listLoading = false;

  const wrapper = mount(TaskListView, {
    global: { plugins: [router], stubs: { TaskCreateForm: true } },
  });
  return wrapper;
}

describe("标题拆分：主标题 + 次级标记", () => {
  it("把括号后缀拆到次级标记里，不与主题同量级", async () => {
    const wrapper = await mountWith([task()]);

    // 主标题只保留主题部分
    const main = wrapper.find('[data-test="task-title-main"]');
    expect(main.exists()).toBe(true);
    expect(main.text()).toContain("检索增强生成评估方法综述");
    expect(main.text()).not.toContain("Issue #6");

    // 后缀进次级标记（更小、更淡）
    const meta = wrapper.find('[data-test="task-title-meta"]');
    expect(meta.exists()).toBe(true);
    expect(meta.text()).toContain("Issue #6 复验 #3");
    expect(meta.classes().some((c) => c.includes("muted-foreground"))).toBe(true);
  });

  it("没有括号后缀时，次级标记不渲染（不留空占位）", async () => {
    const wrapper = await mountWith([task({ title: "P1D 配额验收任务 481869" })]);

    expect(wrapper.find('[data-test="task-title-main"]').text()).toBe("P1D 配额验收任务 481869");
    expect(wrapper.find('[data-test="task-title-meta"]').exists()).toBe(false);
  });

  it("嵌套括号也只拆最外层（不能把主题也切碎）", async () => {
    const wrapper = await mountWith([
      task({ title: "综述（第一轮（含附录））" }),
    ]);
    expect(wrapper.find('[data-test="task-title-main"]').text()).toBe("综述");
    expect(wrapper.find('[data-test="task-title-meta"]').text()).toContain("第一轮（含附录）");
  });
});

describe("进度列：不再有说谎的百分比进度条", () => {
  it("不再渲染 progressbar（旧实现失败状态也显示 100%）", async () => {
    const wrapper = await mountWith([
      task({ task_id: "a", status: "failed", title: "失败的（x）" }),
      task({ task_id: "b", status: "completed", title: "完成的（y）" }),
    ]);

    expect(wrapper.findAll('[role="progressbar"]')).toHaveLength(0);
  });

  it("失败与完成在视觉上可区分（终态用图标）", async () => {
    const wrapper = await mountWith([
      task({ task_id: "a", status: "failed", title: "失败的（x）" }),
      task({ task_id: "b", status: "completed", title: "完成的（y）" }),
    ]);

    const failedRow = wrapper.find('[data-test="task-row-a"]');
    const doneRow = wrapper.find('[data-test="task-row-b"]');

    // 两个终态都必须渲染状态图标，且不能是同一个图标
    const failedIcon = failedRow.find('[data-test="status-icon"]');
    const doneIcon = doneRow.find('[data-test="status-icon"]');
    expect(failedIcon.exists()).toBe(true);
    expect(doneIcon.exists()).toBe(true);
    expect(failedIcon.attributes("data-state")).toBe("failed");
    expect(doneIcon.attributes("data-state")).toBe("completed");
  });

  it("进行中才显示细进度条（此时百分比有意义）", async () => {
    const wrapper = await mountWith([task({ status: "running" })]);
    expect(wrapper.findAll('[role="progressbar"]')).toHaveLength(1);
  });

  it("终态同时显示形状图标与可见文字（2026-10-27 设计变更）", async () => {
    // 旧设计只显示绿色勾，扫读长列表时语义不够明确；新设计恢复可见文字。
    // 既有 E2E 仍按行文本含「已完成」定位，因此这一变更同时保留 E2E 契约。
    const wrapper = await mountWith([
      task({ task_id: "done", status: "completed", title: "完成的（x）" }),
      task({ task_id: "wait", status: "waiting_approval", title: "待确认的（y）" }),
    ]);

    const doneRow = wrapper.find('[data-test="task-row-done"]');
    const visibleLabel = doneRow.find('[data-test="terminal-status-label"]');
    expect(visibleLabel.exists()).toBe(true);
    expect(visibleLabel.classes()).not.toContain("sr-only");
    expect(visibleLabel.text()).toBe("已完成");
    expect(doneRow.find('[data-test="status-icon"]').attributes("data-state")).toBe("completed");

    // 非终态仍需可见的文字徽标（用户要看懂系统正在做什么）
    expect(wrapper.find('[data-test="task-row-wait"]').text()).toContain("待确认规划");
  });
});

describe("汇总统计", () => {
  it("待人工介入时说明处理暂停原因，不误导为尚未开始", async () => {
    const wrapper = await mountWith([task({ task_id: "human", status: "waiting_human" })]);
    const action = wrapper.find('[data-test="attention-needs-me"]');

    expect(action.text()).toContain("暂停原因");
    expect(action.text()).not.toContain("开始检索");
  });

  it("待办行动区只筛选需要我处理的任务", async () => {
    const wrapper = await mountWith([
      task({ task_id: "wait", status: "waiting_approval" }),
      task({ task_id: "done", status: "completed" }),
    ]);

    const action = wrapper.find('[data-test="attention-needs-me"]');
    expect(action.exists()).toBe(true);
    expect(action.text()).toContain("1");
    await action.trigger("click");

    expect(wrapper.find('[data-test="task-row-wait"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="task-row-done"]').exists()).toBe(false);
  });

  it("把「待我确认」单独统计（唯一需要用户行动的状态）", async () => {
    const wrapper = await mountWith([
      task({ task_id: "a", status: "waiting_approval" }),
      task({ task_id: "b", status: "waiting_human" }),
      task({ task_id: "c", status: "running" }),
      task({ task_id: "d", status: "completed" }),
    ]);

    const needsMe = wrapper.find('[data-test="stat-needs-me"]');
    expect(needsMe.exists()).toBe(true);
    expect(needsMe.text()).toContain("2");
  });

  it("展示进行中 / 已完成 / 全部三个计数", async () => {
    const wrapper = await mountWith([
      task({ task_id: "a", status: "running" }),
      task({ task_id: "b", status: "planning" }),
      task({ task_id: "c", status: "completed" }),
    ]);

    expect(wrapper.find('[data-test="stat-active"]').text()).toContain("2");
    expect(wrapper.find('[data-test="stat-done"]').text()).toContain("1");
    expect(wrapper.find('[data-test="stat-total"]').text()).toContain("3");
  });

  it("统计为 0 时也显示（不能因空值隐藏卡片）", async () => {
    const wrapper = await mountWith([task({ status: "completed" })]);
    expect(wrapper.find('[data-test="stat-needs-me"]').text()).toContain("0");
  });
});

describe("筛选与搜索", () => {
  it("有「待我确认」快捷筛选，且只显示该状态", async () => {
    const wrapper = await mountWith([
      task({ task_id: "a", status: "waiting_approval", title: "待确认的（x）" }),
      task({ task_id: "b", status: "completed", title: "完成的（y）" }),
    ]);

    const chip = wrapper.find('[data-test="filter-needs-me"]');
    expect(chip.exists()).toBe(true);
    await chip.trigger("click");

    expect(wrapper.find('[data-test="task-row-a"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="task-row-b"]').exists()).toBe(false);
  });

  it("搜索按标题过滤（含被拆到次级标记里的后缀）", async () => {
    const wrapper = await mountWith([
      task({ task_id: "a", title: "综述甲（Issue #6）" }),
      task({ task_id: "b", title: "综述乙（Issue #7）" }),
    ]);

    const input = wrapper.find('[data-test="task-search"]');
    expect(input.exists()).toBe(true);
    await input.setValue("Issue #7");

    expect(wrapper.find('[data-test="task-row-a"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="task-row-b"]').exists()).toBe(true);
  });

  it("筛选后无结果时给出可读提示（不是空白表格）", async () => {
    const wrapper = await mountWith([task({ status: "completed" })]);
    await wrapper.find('[data-test="filter-needs-me"]').trigger("click");

    const empty = wrapper.find('[data-test="filtered-empty"]');
    expect(empty.exists()).toBe(true);
    expect(empty.text()).toContain("没有");
  });
});

describe("既有测试契约必须保留", () => {
  it("保留 new-task / task-row-<id> / empty-state", async () => {
    const wrapper = await mountWith([task({ task_id: "keep-me" })]);

    expect(wrapper.find('[data-test="new-task"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="task-row-keep-me"]').exists()).toBe(true);
  });

  it("真正无任务时仍用 empty-state（区别于筛选无结果）", async () => {
    const wrapper = await mountWith([]);
    expect(wrapper.find('[data-test="empty-state"]').exists()).toBe(true);
  });

  it("整行可点击进入详情，且行内有具名的「打开」按钮", async () => {
    const wrapper = await mountWith([task({ task_id: "click-me" })]);
    const row = wrapper.find('[data-test="task-row-click-me"]');

    // 行本身可点击（cursor-pointer + @click）
    expect(row.classes()).toContain("cursor-pointer");

    // **关键**：必须有一个可访问名称是「打开」的按钮。
    // E2E（m2-result-preview / m2-all-preview）用
    // `getByRole("button", { name: "打开" })` 定位，这是既有契约。
    const openButton = row.find('button[aria-label="打开"]');
    expect(openButton.exists()).toBe(true);

    // 行本身**不能**是 role=button：否则同一行有两个 button 角色，
    // `getByRole` 会命中多个，E2E 报二义性（实测踩过）。
    expect(row.attributes("role")).not.toBe("button");
  });
});

describe("蓝色编辑感设计签名", () => {
  it("搜索框使用大胶囊容器，搜索语义与数据流不变", async () => {
    const wrapper = await mountWith([task()]);
    const shell = wrapper.find('[data-test="task-search-shell"]');

    expect(shell.exists()).toBe(true);
    expect(shell.classes()).toContain("rounded-full");
    expect(wrapper.find('[data-test="task-search"]').attributes("aria-label")).toBe("搜索任务标题");
  });
});
