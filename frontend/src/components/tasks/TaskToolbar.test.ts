import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import TaskLog from "@/components/tasks/TaskLog.vue";
import TaskToolbar from "@/components/tasks/TaskToolbar.vue";
import type { TaskEvent } from "@/api/types";

function event(eventId: number, overrides: Partial<TaskEvent> = {}): TaskEvent {
  return {
    event_id: eventId,
    task_id: "task-1",
    subtask_id: null,
    event_type: "subtask_state_transition",
    status: "running",
    occurred_at: "2026-10-18T02:00:00Z",
    payload: { agent_type: "executor", node_id: "n1", message: "节点开始执行" },
    ...overrides,
  };
}

describe("TaskToolbar", () => {
  it("renders only the commands allowed by the current state", () => {
    const running = mount(TaskToolbar, { props: { status: "running" } });
    expect(running.find('[data-test="control-pause"]').exists()).toBe(true);
    expect(running.find('[data-test="control-resume"]').exists()).toBe(false);
    expect(running.find('[data-test="control-retry-node"]').exists()).toBe(false);

    const paused = mount(TaskToolbar, { props: { status: "paused" } });
    expect(paused.find('[data-test="control-resume"]').exists()).toBe(true);
    expect(paused.find('[data-test="control-pause"]').exists()).toBe(false);

    const done = mount(TaskToolbar, { props: { status: "completed" } });
    expect(done.find('[data-test="control-cancel"]').exists()).toBe(false);
  });

  it("requires a failed or waiting node before retrying it", async () => {
    const wrapper = mount(TaskToolbar, {
      props: { status: "waiting_human", selectedNodeId: "n2", selectedNodeStatus: "completed" },
    });
    expect(wrapper.find('[data-test="control-retry-node"]').exists()).toBe(false);

    await wrapper.setProps({ selectedNodeStatus: "failed" });
    await wrapper.find('[data-test="control-retry-node"]').trigger("click");
    expect(wrapper.emitted("command")).toEqual([[{ command: "retry_node", node_id: "n2" }]]);
  });

  it("confirms before cancelling", async () => {
    const wrapper = mount(TaskToolbar, { props: { status: "running" } });

    await wrapper.find('[data-test="control-cancel"]').trigger("click");
    expect(wrapper.emitted("command")).toBeUndefined();

    await wrapper.find('[data-test="control-cancel"]').trigger("click");
    expect(wrapper.emitted("command")).toEqual([[{ command: "cancel" }]]);
  });

  it("validates the required text before supplying input", async () => {
    const wrapper = mount(TaskToolbar, {
      props: { status: "waiting_human", selectedNodeId: "n3", selectedNodeStatus: "waiting_human" },
    });

    await wrapper.find('[data-test="control-supply-input"]').trigger("click");
    await wrapper.find('[data-test="supply-input-submit"]').trigger("click");
    expect(wrapper.emitted("command")).toBeUndefined();
    expect(wrapper.text()).toContain("请输入要补充的内容");

    await wrapper.find('[data-test="supply-input-text"]').setValue("请补充 2024 年后的文献");
    await wrapper.find('[data-test="supply-input-submit"]').trigger("click");
    expect(wrapper.emitted("command")).toEqual([
      [{ command: "supply_input", node_id: "n3", input: { text: "请补充 2024 年后的文献" } }],
    ]);
  });

  it("disables pause and resume while a command is pending", () => {
    const wrapper = mount(TaskToolbar, { props: { status: "running", pending: true } });
    expect(wrapper.find('[data-test="control-pause"]').attributes("disabled")).toBeDefined();
  });
});

describe("TaskLog", () => {
  it("renders timestamp, type, status and message as text", () => {
    const wrapper = mount(TaskLog, {
      props: { events: [event(1, { payload: { agent_type: "executor", node_id: "n1", message: "<b>hi</b>" } })] },
    });

    const row = wrapper.find('[data-test="log-row-1"]');
    expect(row.text()).toContain("subtask_state_transition");
    expect(row.text()).toContain("running");
    expect(row.text()).toContain("<b>hi</b>"); // 文本插值，不解析为 HTML
    expect(row.find("b").exists()).toBe(false);
  });

  it("filters by agent, node and level", async () => {
    const events = [
      event(1, { payload: { agent_type: "executor", node_id: "n1", level: "info" } }),
      event(2, { payload: { agent_type: "validator", node_id: "n2", level: "error" } }),
    ];
    const wrapper = mount(TaskLog, { props: { events } });

    await wrapper.find('[data-test="log-filter-agent"]').setValue("validator");
    expect(wrapper.find('[data-test="log-row-1"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="log-row-2"]').exists()).toBe(true);

    await wrapper.find('[data-test="log-filter-agent"]').setValue("");
    await wrapper.find('[data-test="log-filter-level"]').setValue("error");
    expect(wrapper.find('[data-test="log-count"]').text()).toContain("1 条");
  });

  it("paginates at 100 rows", async () => {
    const events = Array.from({ length: 130 }, (_, index) => event(index + 1));
    const wrapper = mount(TaskLog, { props: { events } });

    expect(wrapper.findAll('[data-test^="log-row-"]')).toHaveLength(100);
    await wrapper.findAll(".log__pager button")[1].trigger("click");
    expect(wrapper.find('[data-test="log-page"]').text()).toContain("2 / 2");
    expect(wrapper.findAll('[data-test^="log-row-"]')).toHaveLength(30);
  });
});
