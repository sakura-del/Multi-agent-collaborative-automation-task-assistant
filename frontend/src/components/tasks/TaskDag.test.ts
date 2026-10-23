import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

import TaskDag from "@/components/tasks/TaskDag.vue";
import type { PlanNode, SubtaskStatus } from "@/api/types";
import { DAG_NODE_HEIGHT, DAG_NODE_WIDTH, layoutDag, nodeAriaLabel } from "@/utils/dag-layout";

// 菱形图：n1 → (n2, n3) → n4
const PLAN_NODES: PlanNode[] = [
  { id: "n1", type: "search", title: "检索", dependencies: [], input: {}, required_evidence: 0 },
  { id: "n2", type: "extract", title: "抽取", dependencies: ["n1"], input: {}, required_evidence: 3 },
  { id: "n3", type: "write", title: "撰写", dependencies: ["n1"], input: {}, required_evidence: 2 },
  { id: "n4", type: "review", title: "复盘", dependencies: ["n2", "n3"], input: {}, required_evidence: 0 },
];

function statuses(values: Partial<Record<string, SubtaskStatus>>): Record<string, SubtaskStatus> {
  return {
    n1: "completed",
    n2: "completed",
    n3: "running",
    n4: "pending",
    ...values,
  };
}

describe("dag layout", () => {
  it("assigns deterministic positions by depth and sibling index", () => {
    const first = layoutDag(PLAN_NODES);
    const second = layoutDag(PLAN_NODES);

    expect(first).toEqual(second);
    // 同一层同列 x 相同，纵向按次序排开
    expect(first.n1.x).toBe(0);
    expect(first.n2.x).toBe(DAG_NODE_WIDTH + 96);
    expect(first.n3.x).toBe(first.n2.x);
    expect(first.n2.y).not.toBe(first.n3.y);
    expect(first.n4.x).toBe((DAG_NODE_WIDTH + 96) * 2);
  });

  it("keeps node geometry stable when statuses change", () => {
    const before = layoutDag(PLAN_NODES);
    const after = layoutDag(PLAN_NODES);

    // 状态只影响标签/色调，不参与布局
    expect(Object.keys(after)).toEqual(Object.keys(before));
    expect(after).toEqual(before);
    expect(DAG_NODE_HEIGHT).toBeGreaterThan(0);
  });

  it("maps each status to a distinct accessible label", () => {
    const labels = (
      ["completed", "running", "waiting_human", "failed"] as SubtaskStatus[]
    ).map((status) => nodeAriaLabel("n1", "检索", status));

    expect(new Set(labels).size).toBe(4);
    expect(labels[0]).toContain("已完成");
    expect(labels[1]).toContain("执行中");
    expect(labels[2]).toContain("待人工介入");
    expect(labels[3]).toContain("失败");
  });
});

describe("TaskDag", () => {
  it("renders one element per node with an accessible label", () => {
    const wrapper = mount(TaskDag, {
      props: { nodes: PLAN_NODES, statuses: statuses({}) },
    });

    const rendered = wrapper.findAll('[data-test^="dag-node-"]');
    expect(rendered).toHaveLength(4);
    const n3 = wrapper.find('[data-test="dag-node-n3"]');
    expect(n3.attributes("aria-label")).toContain("执行中");
    expect(n3.text()).toContain("撰写");
  });

  it("does not move nodes when only statuses change", async () => {
    const wrapper = mount(TaskDag, {
      props: { nodes: PLAN_NODES, statuses: statuses({}) },
    });
    const before = wrapper.find('[data-test="dag-node-n2"]').attributes("style");

    await wrapper.setProps({ statuses: statuses({ n2: "waiting_human" }) });

    const after = wrapper.find('[data-test="dag-node-n2"]').attributes("style");
    expect(after).toBe(before);
    expect(wrapper.find('[data-test="dag-node-n2"]').attributes("aria-label")).toContain(
      "待人工介入"
    );
  });

  it("emits the node id when a node is selected", async () => {
    const wrapper = mount(TaskDag, {
      props: { nodes: PLAN_NODES, statuses: statuses({}) },
    });

    await wrapper.find('[data-test="dag-node-n2"]').trigger("click");
    await wrapper.find('[data-test="dag-node-n4"]').trigger("keydown.enter");

    expect(wrapper.emitted("select")).toEqual([["n2"], ["n4"]]);
  });

  it("每条依赖画一条边", async () => {
    const wrapper = mount(TaskDag, {
      props: { nodes: PLAN_NODES, statuses: statuses({}) },
    });

    // 依赖边：n2→n1、n3→n1、n4→n2、n4→n3
    expect(wrapper.findAll('[data-test^="dag-edge-"]')).toHaveLength(4);
  });

  it("提供可滚动视口，并暴露当前布局方向（替代旧的「适应窗口」缩放）", async () => {
    // 2026-10-23 重设计：旧实现的「适应窗口」把 DAG 整体缩放，节点字随之变小。
    // 改为按容器宽度自适应换向（横向放不下 → 纵向），并把方向暴露到 DOM
    // 供测试与样式使用。链式任务因此不再被横向裁切。
    const wrapper = mount(TaskDag, {
      props: { nodes: PLAN_NODES, statuses: statuses({}) },
    });

    const viewport = wrapper.find('[data-test="dag-viewport"]');
    expect(viewport.exists()).toBe(true);
    expect(viewport.classes().some((c) => c.includes("overflow-auto"))).toBe(true);
    expect(["horizontal", "vertical"]).toContain(viewport.attributes("data-orientation"));
  });

  it("keeps dragging and connection editing disabled", () => {
    const wrapper = mount(TaskDag, {
      props: { nodes: PLAN_NODES, statuses: statuses({}) },
    });

    expect(wrapper.attributes("data-draggable")).toBe("false");
    expect(wrapper.attributes("data-connectable")).toBe("false");
  });
});

describe("TaskDag selection plumbing", () => {
  it("notifies an external listener exactly once per selection", async () => {
    const onSelect = vi.fn();
    const wrapper = mount(TaskDag, {
      props: { nodes: PLAN_NODES, statuses: statuses({}), onSelect },
    });

    await wrapper.find('[data-test="dag-node-n1"]').trigger("click");

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith("n1");
  });
});
