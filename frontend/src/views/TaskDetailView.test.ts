import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";

import { taskApi } from "@/api/tasks";
import type { TaskSnapshot } from "@/api/types";
import TaskDetailView from "@/views/TaskDetailView.vue";

vi.mock("@/api/tasks", () => ({
  taskApi: {
    get: vi.fn(),
    approve: vi.fn(),
    control: vi.fn(),
    events: vi.fn(),
    wsTicket: vi.fn(),
    refreshes: vi.fn().mockResolvedValue([]),
    routine: vi.fn().mockResolvedValue(null),
  },
}));

vi.mock("@/services/task-events", () => ({
  createTaskEventSession: () => ({
    start: vi.fn(async () => undefined),
    stop: vi.fn(),
  }),
}));

const COMPLETED_SNAPSHOT: TaskSnapshot = {
  task_id: "task-done-1",
  status: "completed",
  title: "RAG 综述完成态回归验证",
  plan_version: 1,
  revision: 6,
  plan: {
    version: 1,
    topic: "RAG 综述完成态回归验证",
    research_questions: ["RAG 评估基准有哪些？"],
    search_queries: ["RAG evaluation"],
    acceptance_criteria: ["引用可追溯"],
    nodes: [
      { id: "n1", type: "search", title: "检索学术文献", dependencies: [], input: {}, required_evidence: 0 },
      { id: "n2", type: "write", title: "撰写综述正文", dependencies: ["n1"], input: {}, required_evidence: 1 },
    ],
  },
  subtasks: [
    {
      node_id: "n1",
      title: "检索学术文献",
      status: "completed",
      retry_count: 0,
      output: null,
      error: null,
      started_at: "2026-11-24T01:00:00Z",
      completed_at: "2026-11-24T01:00:05Z",
    },
    {
      node_id: "n2",
      title: "撰写综述正文",
      status: "completed",
      retry_count: 0,
      output: null,
      error: null,
      started_at: "2026-11-24T01:00:05Z",
      completed_at: "2026-11-24T01:00:15Z",
    },
  ],
  artifacts: [],
  review_report: {
    final_markdown: "# 综述",
    bibliography: [],
    scores: {},
    issues: [],
    stats: {},
  },
  created_at: "2026-11-24T01:00:00Z",
  updated_at: "2026-11-24T01:00:15Z",
};

describe("TaskDetailView 规划 Tab 状态一致性（Issue #55）", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    vi.mocked(taskApi.get).mockResolvedValue(COMPLETED_SNAPSHOT);
  });

  it("已完成任务进入规划 Tab 时隐藏确认按钮并同步真实步骤与 Completed 状态", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: "/tasks/:taskId",
          name: "task-detail",
          component: TaskDetailView,
          props: true,
        },
        {
          path: "/tasks/:taskId/screening",
          name: "task-screening",
          component: { template: "<div />" },
        },
      ],
    });
    await router.push({ name: "task-detail", params: { taskId: "task-done-1" }, query: { tab: "plan" } });
    await router.isReady();

    const wrapper = mount(TaskDetailView, {
      props: { taskId: "task-done-1" },
      global: {
        plugins: [router],
        stubs: {
          TaskRefreshPanel: true,
          AnnotationPanel: true,
          BeTimeline: true,
        },
      },
    });
    await flushPromises();

    // 不得再显示「确认规划」或「重新生成」按钮
    expect(wrapper.find('[data-test="plan-approve"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="plan-regenerate"]').exists()).toBe(false);

    // BeAgentPlan 必须展示 Completed 与 2 个步骤，不再出现 Awaiting approval / No steps yet
    const agentPlan = wrapper.find('[data-test="agent-plan"]');
    expect(agentPlan.exists()).toBe(true);
    expect(agentPlan.find('[data-test="agent-plan-status"]').text()).toBe("Completed");
    expect(agentPlan.text()).toContain("检索学术文献");
    expect(agentPlan.text()).toContain("撰写综述正文");
    expect(agentPlan.text()).not.toContain("Awaiting approval");
    expect(agentPlan.text()).not.toContain("No steps yet");
    expect(agentPlan.text()).not.toContain("0/0 complete");

    // Issue #57：Tab 栏避免 -mb-px 触发纵向滚动箭头
    const tablist = wrapper.get('nav[role="tablist"]');
    expect(tablist.attributes("class")).toContain("overflow-y-hidden");
    expect(wrapper.get('[data-test="tab-plan"]').attributes("class")).not.toContain("-mb-px");
  });
});
