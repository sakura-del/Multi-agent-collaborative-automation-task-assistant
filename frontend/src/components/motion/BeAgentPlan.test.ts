import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import BeAgentPlan, { type AgentPlanStep } from "@/components/motion/BeAgentPlan.vue";

const STEPS: AgentPlanStep[] = [
  { id: "n1", title: "检索学术文献", kind: "search", status: "completed", durationMs: 1200 },
  {
    id: "n2",
    title: "撰写综述正文",
    kind: "write",
    status: "failed",
    durationMs: 800,
    error: "上下文超限",
  },
];

describe("BeAgentPlan", () => {
  it("渲染全中文标题、角色徽章、状态徽章与宽版容器", () => {
    const wrapper = mount(BeAgentPlan, {
      props: { steps: STEPS, planStatus: "failed", revision: 2 },
    });

    expect(wrapper.find('[data-test="agent-plan"]').classes()).toContain("max-w-2xl");
    expect(wrapper.text()).toContain("智能体 · 执行计划");
    expect(wrapper.text()).toContain("执行计划");
    expect(wrapper.text()).toContain("检索");
    expect(wrapper.text()).toContain("写作");
    expect(wrapper.find('[data-test="agent-plan-status"]').text()).toBe("需要处理");
    expect(wrapper.text()).toContain("执行遇阻，请展开查看详情");
    expect(wrapper.text()).not.toContain("Agent · Execution plan");
  });

  it("支持右上角折叠按钮收起和展开步骤列表", async () => {
    const wrapper = mount(BeAgentPlan, {
      props: { steps: STEPS, planStatus: "running" },
    });

    const toggle = wrapper.get('[data-test="agent-plan-collapse"]');
    expect(toggle.attributes("aria-expanded")).toBe("true");

    await toggle.trigger("click");
    expect(toggle.attributes("aria-expanded")).toBe("false");

    await toggle.trigger("click");
    expect(toggle.attributes("aria-expanded")).toBe("true");
  });

  it("空步骤列表展示中文提示「暂无执行步骤」", () => {
    const wrapper = mount(BeAgentPlan, {
      props: { steps: [], planStatus: "draft" },
    });

    expect(wrapper.text()).toContain("暂无执行步骤");
    expect(wrapper.text()).not.toContain("No steps yet");
    expect(wrapper.find('[data-test="agent-plan-status"]').text()).toBe("草稿");
  });
});
