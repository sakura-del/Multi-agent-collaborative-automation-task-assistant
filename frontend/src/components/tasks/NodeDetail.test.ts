import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import NodeDetail from "@/components/tasks/NodeDetail.vue";
import type { PlanNode, SubtaskSnapshot } from "@/api/types";

const NODE: PlanNode = {
  id: "n2",
  type: "search",
  title: "检索核心文献",
  dependencies: ["n1"],
  input: {},
  required_evidence: 3,
};

const SUBTASK: SubtaskSnapshot = {
  node_id: "n2",
  title: "检索核心文献",
  status: "completed",
  retry_count: 1,
  started_at: "2026-11-24T01:00:05Z",
  completed_at: "2026-11-24T01:00:15Z",
  error: null,
  output: { artifacts: [] },
};

describe("NodeDetail", () => {
  it("渲染全中文标签与 393px 自适应宽度类", () => {
    const wrapper = mount(NodeDetail, {
      props: {
        node: NODE,
        subtask: SUBTASK,
        siblings: [
          {
            node_id: "n1",
            title: "规划",
            status: "completed",
            retry_count: 0,
            started_at: "2026-11-24T01:00:00Z",
            completed_at: "2026-11-24T01:00:05Z",
            error: null,
            output: null,
          },
          SUBTASK,
        ],
      },
    });

    const aside = wrapper.get('[data-test="node-detail"]');
    expect(aside.classes()).toContain("w-full");
    expect(aside.classes()).toContain("min-w-0");

    expect(wrapper.text()).toContain("文献检索");
    expect(wrapper.text()).toContain("执行耗时");
    expect(wrapper.text()).toContain("开始时间");
    expect(wrapper.text()).toContain("结束时间");
    expect(wrapper.text()).toContain("持续时长");
    expect(wrapper.text()).toContain("节点属性");
    expect(wrapper.text()).toContain("节点标识");
    expect(wrapper.text()).toContain("节点类型");
    expect(wrapper.text()).toContain("输出字段");

    expect(wrapper.text()).not.toContain("Timing");
    expect(wrapper.text()).not.toContain("Attributes");
    expect(wrapper.text()).not.toContain("span id");
  });
});
