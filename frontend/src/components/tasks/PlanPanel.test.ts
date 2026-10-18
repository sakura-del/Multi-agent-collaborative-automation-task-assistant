import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import PlanPanel from "@/components/tasks/PlanPanel.vue";
import type { TaskPlan } from "@/api/types";

const PLAN: TaskPlan = {
  version: 3,
  topic: "检索增强生成与多智能体协作的质量评估",
  research_questions: ["RAG 的评估指标有哪些？", "多智能体协作如何提升引用可靠性？"],
  search_queries: ["retrieval augmented generation evaluation"],
  acceptance_criteria: ["引用可回溯", "覆盖 10 篇文献"],
  nodes: [
    { id: "n1", type: "search", title: "检索", dependencies: [], input: {}, required_evidence: 0 },
    { id: "n2", type: "write", title: "撰写", dependencies: ["n1"], input: {}, required_evidence: 2 },
  ],
};

describe("PlanPanel", () => {
  it("renders plan sections and the read-only DAG", () => {
    const wrapper = mount(PlanPanel, { props: { plan: PLAN } });

    expect(wrapper.text()).toContain("RAG 的评估指标有哪些？");
    expect(wrapper.text()).toContain("retrieval augmented generation evaluation");
    expect(wrapper.text()).toContain("引用可回溯");
    expect(wrapper.find('[data-test="dag"]').exists()).toBe(true);
    expect(wrapper.findAll('[data-test^="dag-node-"]')).toHaveLength(2);
  });

  it("offers only regenerate and approve commands", () => {
    const wrapper = mount(PlanPanel, { props: { plan: PLAN } });
    const buttons = wrapper.findAll(".plan-panel__actions button");

    expect(buttons).toHaveLength(2);
    expect(wrapper.find('[data-test="plan-regenerate"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="plan-approve"]').exists()).toBe(true);
  });

  it("approves with the current plan version", async () => {
    const wrapper = mount(PlanPanel, { props: { plan: PLAN } });

    await wrapper.find('[data-test="plan-approve"]').trigger("click");

    expect(wrapper.emitted("approve")).toEqual([[3]]);
  });

  it("disables commands while pending and shows a conflict banner", () => {
    const wrapper = mount(PlanPanel, {
      props: { plan: PLAN, pending: true, conflict: "规划已被更新，已重新加载" },
    });

    expect(wrapper.find('[data-test="plan-approve"]').attributes("disabled")).toBeDefined();
    expect(wrapper.find('[data-test="plan-regenerate"]').attributes("disabled")).toBeDefined();
    expect(wrapper.find('[data-test="plan-conflict"]').text()).toContain("规划已被更新");
  });

  it("emits regenerate without a payload", async () => {
    const wrapper = mount(PlanPanel, { props: { plan: PLAN } });

    await wrapper.find('[data-test="plan-regenerate"]').trigger("click");

    expect(wrapper.emitted("regenerate")).toEqual([[]]);
  });
});
