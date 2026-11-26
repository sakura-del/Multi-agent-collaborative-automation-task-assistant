import { afterEach, describe, expect, it, vi } from "vitest";
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
    // 用 data-test 定位而非样式类：类名属于实现细节，改版式不应弄坏测试
    const commands = wrapper.findAll(
      '[data-test="plan-regenerate"], [data-test="plan-approve"]'
    );

    expect(commands).toHaveLength(2);
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

  // 回归（2026-11-17 生产）：规划失败时没有任何节点，页面只给了一句
  // 「点重新生成」却没有按钮，用户无路可走。
  it("offers 重新规划 in the blocked empty state", async () => {
    const wrapper = mount(PlanPanel, { props: { plan: null, blocked: true } });

    expect(wrapper.text()).toContain("规划没有生成");
    const button = wrapper.find('[data-test="plan-regenerate"]');
    expect(button.exists()).toBe(true);
    expect(button.text()).toContain("重新规划");

    await button.trigger("click");
    expect(wrapper.emitted("regenerate")).toEqual([[]]);
  });

  it("hides regenerate when the status forbids replanning", () => {
    const wrapper = mount(PlanPanel, { props: { plan: PLAN, canReplan: false } });

    expect(wrapper.find('[data-test="plan-regenerate"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="plan-approve"]').exists()).toBe(true);
  });

  it("hides approve and renders completed steps and hint for completed tasks (Issue #55)", () => {
    const wrapper = mount(PlanPanel, {
      props: {
        plan: PLAN,
        canApprove: false,
        canReplan: false,
        planStatus: "completed",
        steps: [
          { id: "n1", title: "检索", kind: "search", status: "completed", durationMs: 1200 },
          { id: "n2", title: "撰写", kind: "write", status: "completed", durationMs: 2800 },
        ],
      },
    });

    expect(wrapper.find('[data-test="plan-approve"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="plan-regenerate"]').exists()).toBe(false);
    const hint = wrapper.find('[data-test="plan-approve-hint"]').text();
    expect(hint).toContain("执行完成");
    expect(hint).not.toContain("已暂停在执行阶段");

    const agentPlan = wrapper.find('[data-test="agent-plan"]');
    expect(agentPlan.find('[data-test="agent-plan-status"]').text()).toBe("已完成");
    expect(agentPlan.text()).not.toContain("Awaiting approval");
    expect(agentPlan.text()).not.toContain("等待确认");
    expect(agentPlan.text()).not.toContain("No steps yet");
    expect(agentPlan.text()).not.toContain("暂无执行步骤");
    expect(agentPlan.text()).toContain("共 2 步");
    expect(wrapper.text()).not.toContain("beUI");
  });

  it("renders running and failed hints accurately when approval is disabled", () => {
    const running = mount(PlanPanel, {
      props: { plan: PLAN, canApprove: false, canReplan: false, planStatus: "running" },
    });
    expect(running.find('[data-test="plan-approve-hint"]').text()).toContain("正在执行中");

    const failed = mount(PlanPanel, {
      props: { plan: PLAN, canApprove: false, canReplan: true, planStatus: "failed" },
    });
    expect(failed.find('[data-test="plan-approve-hint"]').text()).toContain("重试失败的节点");
  });

  describe("规划中实时进度卡片（Issue #69）", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    it("planning=true 且无规划时展示进度卡片与实时计时器（30s 后出现温和提示），不显示暂无规划", async () => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-11-26T10:00:00Z"));

      const wrapper = mount(PlanPanel, {
        props: {
          plan: null,
          planning: true,
          planningStartedAt: "2026-11-26T10:00:00Z",
        },
      });

      expect(wrapper.find('[data-test="plan-progress"]').exists()).toBe(true);
      expect(wrapper.find('[data-test="plan-progress-elapsed"]').text()).toContain("已等待 0s");
      expect(wrapper.text()).not.toContain("暂无规划");

      await vi.advanceTimersByTimeAsync(31000);
      expect(wrapper.find('[data-test="plan-progress-elapsed"]').text()).toContain("已等待 31s");
      expect(wrapper.text()).toContain("可离开页面稍后回来");
    });

    it("传入 repairing_plan 阶段事件时展示对应提示并高亮第 3 步（契约校验与修复）", () => {
      const wrapper = mount(PlanPanel, {
        props: {
          plan: null,
          planning: true,
          planningProgress: {
            stage: "repairing_plan",
            message: "首次规划未通过格式契约校验，正在自动修复（第 1/1 次）…",
          },
        },
      });

      expect(wrapper.find('[data-test="plan-progress-message"]').text()).toContain("自动修复");
      const step3 = wrapper.find('[data-test="plan-progress-step-validating_contract"]');
      expect(step3.exists()).toBe(true);
      expect(step3.attributes("data-state")).toBe("active");
    });

    it("planning 退出或组件卸载时正确清理定时器", async () => {
      vi.useFakeTimers();
      const clearSpy = vi.spyOn(globalThis, "clearInterval");

      const wrapper = mount(PlanPanel, {
        props: {
          plan: null,
          planning: true,
          planningStartedAt: new Date().toISOString(),
        },
      });

      await wrapper.setProps({ planning: false, plan: PLAN });
      expect(clearSpy).toHaveBeenCalled();

      wrapper.unmount();
      clearSpy.mockRestore();
    });
  });
});
