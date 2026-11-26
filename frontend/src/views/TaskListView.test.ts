import { describe, expect, it, beforeEach, vi } from "vitest";
import { nextTick } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createRouter, createMemoryHistory } from "vue-router";

import TaskListView from "@/views/TaskListView.vue";
import type { TaskSummary } from "@/api/types";
import { saveTaskDraft, taskDraftStorageKey } from "@/lib/task-draft";

// 真实渲染 TaskCreateForm 时它会在 onMounted 拉「引用库可用篇数 / 引用样式 /
// 模型凭据」。单测里不能让它发真请求（happy-dom 会报 AsyncTaskManager 中止的
// 未处理拒绝）。
const getDraftSpy = vi.fn(async () => null as unknown);
vi.mock("@/api/tasks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/tasks")>();
  return {
    ...actual,
    taskApi: {
      ...actual.taskApi,
      getDraft: () => getDraftSpy(),
      saveDraft: vi.fn(async () => ({ payload: {}, revision: 1, updated_at: "" })),
      deleteDraft: vi.fn(async () => undefined),
    },
  };
});
vi.mock("@/api/reference-library", () => ({
  getLibrarySummary: vi.fn(async () => ({ total: 0, usable_as_source: 0 })),
}));
vi.mock("@/api/citation-styles", () => ({
  listCitationStyles: vi.fn(async () => []),
}));
vi.mock("@/api/llm-credentials", () => ({
  listLLMCredentials: vi.fn(async () => []),
}));
vi.mock("@/api/billing", () => ({
  getBillingUsage: vi.fn(async () => null),
}));
vi.mock("@/api/data-source-credentials", () => ({
  listDataSourceCredentials: vi.fn(async () => []),
}));

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

async function mountWith(
  tasks: TaskSummary[],
  options?: {
    total?: number;
    page?: number;
    /** 用真实 TaskCreateForm（默认为 stub），用于验证「新建表单」相关接线。 */
    realForm?: boolean;
    documents?: Array<{ id: string; title: string }>;
    /** 注入登录用户（草稿按用户 id 隔离，自动展开要在挂载时就能读到 auth.user）。 */
    user?: { id: string } | null;
  },
) {
  setActivePinia(createPinia());
  const router = makeRouter();
  await router.push("/tasks");
  await router.isReady();

  // 登录态：草稿命名空间与「有草稿 → 自动展开表单」都依赖 auth.user.id
  const { useAuthStore } = await import("@/stores/auth");
  const auth = useAuthStore();
  if (options?.user) {
    auth.user = options.user as never;
  }

  // 直接注入 store 状态，避免打网络。
  // `loadList` 必须 stub 掉：组件 `onMounted` 会调它，真跑会尝试连后端
  // （ECONNREFUSED 后把 items 清空，空态用例因此看不到 empty-state）。
  const { useTaskStore } = await import("@/stores/tasks");
  const { needsUserAction, isActive } = await import("@/utils/task-status");
  const store = useTaskStore();
  store.items = tasks;
  store.listTotal = options?.total ?? tasks.length;
  store.listPage = options?.page ?? 1;
  // 统计现在来自服务端全量；测试直接注入等价结果，避免打网络
  store.counts = {
    all: tasks.length,
    needs_me: tasks.filter((item) => needsUserAction(item.status)).length,
    active: tasks.filter((item) => isActive(item.status)).length,
    done: tasks.filter((item) => item.status === "completed").length,
  };
  store.loadList = async () => {};
  store.loadCounts = async () => {};
  store.listLoading = false;

  // 新建表单接线（P2 回归）：真实表单需要知识库文档列表；直接注入状态，
  // 避免展开表单时发请求。默认仍 stub 掉 TaskCreateForm，保证既有用例零网络。
  const { useKnowledgeStore } = await import("@/stores/knowledge");
  const knowledge = useKnowledgeStore();
  knowledge.loadAll = async () => {};
  if (options?.realForm) {
    knowledge.documents = (options.documents ?? []) as never;
  }

  const wrapper = mount(TaskListView, {
    global: {
      plugins: [router],
      stubs: options?.realForm ? {} : { TaskCreateForm: true },
    },
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
  it("待人工介入时明确等待判断，不误导为尚未开始", async () => {
    const wrapper = await mountWith([task({ task_id: "human", status: "waiting_human" })]);
    const action = wrapper.find('[data-test="attention-needs-me"]');

    // b629900 改版后文案为「N 项等待你的判断 / 筛选待办 →」，仍是「等你处理」而不是「未开始」
    expect(action.text()).toContain("等待你的判断");
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

describe("服务端分页", () => {
  it("总数超过一页时显示分页与真实总数", async () => {
    const wrapper = await mountWith([task()], { total: 61 });

    const summary = wrapper.find('[data-test="task-page-summary"]');
    expect(summary.exists()).toBe(true);
    // 必须是服务端总数，不能是当前页条数（1）
    expect(summary.text()).toContain("61");
    expect(summary.text()).toContain("第 1 / 4 页");
  });

  it("只有一页时不显示分页（避免 1/1 噪声）", async () => {
    const wrapper = await mountWith([task()], { total: 1 });
    expect(wrapper.find('[data-test="task-page-summary"]').exists()).toBe(false);
  });

  it("第 2 页时隐藏「最近任务」时间线（那里只该放全局最新）", async () => {
    const wrapper = await mountWith(
      [
        task({ task_id: "old-1", created_at: "2026-01-01T00:00:00Z" }),
        task({ task_id: "old-2", created_at: "2026-01-02T00:00:00Z" }),
      ],
      { total: 40, page: 2 },
    );

    expect(wrapper.find('[data-test="be-timeline"]').exists()).toBe(false);
  });
});

describe("新建表单：指定知识库文档（Issue #35 验收发现的 P2）", () => {
  it("展开表单时把可用的知识库文档传给表单，用户可以直接勾选", async () => {
    // 回归点：TaskListView 曾漏传 `:documents`，而 TaskCreateForm 的字段集是
    // `v-if="props.documents.length"` → 生产上这一整块永远不渲染。
    const wrapper = await mountWith([task()], {
      realForm: true,
      documents: [{ id: "doc-1", title: "RAG 原文" }],
    });

    await wrapper.find('[data-test="new-task"]').trigger("click");
    await nextTick();

    expect(wrapper.find('[data-test="doc-doc-1"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("指定知识库文档（可选）");
    expect(wrapper.text()).toContain("RAG 原文");
  });

  it("知识库没有文档时不渲染空字段集", async () => {
    const wrapper = await mountWith([task()], { realForm: true, documents: [] });

    await wrapper.find('[data-test="new-task"]').trigger("click");
    await nextTick();

    expect(wrapper.text()).not.toContain("指定知识库文档（可选）");
  });
});

describe("M3 任务列表 UX 与动效收口", () => {
  it("使用「待我处理」筛选且移除顶部重复统计条", async () => {
    const wrapper = await mountWith([
      task({ task_id: "a", status: "waiting_approval" }),
      task({ task_id: "b", status: "completed" }),
    ]);

    expect(wrapper.find('[data-test="filter-needs-me"]').text()).toContain("待我处理");
    expect(wrapper.findAll('[data-test="stat-needs-me"]')).toHaveLength(1);
    expect(wrapper.findAll('[data-test="stat-total"]')).toHaveLength(1);
  });

  it("待办为 0 时 attention-needs-me 仍保留「等待你的判断」语义锚点", async () => {
    const wrapper = await mountWith([task({ status: "completed" })]);
    const action = wrapper.find('[data-test="attention-needs-me"]');
    expect(action.exists()).toBe(true);
    expect(action.text()).toContain("等待你的判断");
  });

  it("筛选无结果时工具栏仍保持渲染（不消失）", async () => {
    const wrapper = await mountWith([task({ status: "completed" })]);
    await wrapper.find('[data-test="filter-needs-me"]').trigger("click");

    expect(wrapper.find('[data-test="filtered-empty"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="filter-all"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="task-search"]').exists()).toBe(true);
  });

  it("切换到时间线视图且在第 1 页有任务时不重复渲染下方表格", async () => {
    const wrapper = await mountWith([
      task({ task_id: "t-1", status: "running" }),
      task({ task_id: "t-2", status: "completed" }),
    ]);

    await wrapper.find('[data-test="view-mode-timeline"]').trigger("click");
    await nextTick();

    expect(wrapper.find('[data-test="be-timeline"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="task-row-t-1"]').exists()).toBe(false);
    // 时间线进度条采用 scaleX 变换
    const progressEl = wrapper.find('[data-test="be-timeline"] [style*="scaleX"]');
    expect(progressEl.exists()).toBe(true);
  });

  it("加载超过 180ms 显示骨架屏且不闪现空态", async () => {
    vi.useFakeTimers();
    setActivePinia(createPinia());
    const router = makeRouter();
    await router.push("/tasks");
    await router.isReady();

    const { useTaskStore } = await import("@/stores/tasks");
    const store = useTaskStore();
    store.items = [];
    store.listLoading = true;
    store.loadList = async () => {};
    store.loadCounts = async () => {};

    const wrapper = mount(TaskListView, {
      global: { plugins: [router], stubs: { TaskCreateForm: true } },
    });

    // 0ms 时：不显示空态也不闪烁骨架屏
    expect(wrapper.find('[data-test="empty-state"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="task-list-skeleton"]').exists()).toBe(false);

    await vi.advanceTimersByTimeAsync(200);
    await nextTick();
    expect(wrapper.find('[data-test="task-list-skeleton"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="empty-state"]').exists()).toBe(false);
    vi.useRealTimers();
  });

  it("展开新建表单提交失败时仅在表单内展示 1 个错误横幅，不与列表页顶部重复（C5）", async () => {
    const wrapper = await mountWith([task()], { realForm: true });
    const { useTaskStore } = await import("@/stores/tasks");
    const store = useTaskStore();
    store.create = vi.fn().mockRejectedValue(new Error("配额已满，请升级套餐"));

    await wrapper.get('[data-test="new-task"]').trigger("click");
    await nextTick();

    await wrapper.get('[data-test="topic"]').setValue("去重错误横幅验证主题");
    await wrapper.get('[data-test="keyword-input"]').setValue("RAG");
    await wrapper.get('[data-test="keyword-add-btn"]').trigger("click");
    await wrapper.get('[data-test="submit"]').trigger("click");
    await flushPromises();
    await nextTick();

    const alerts = wrapper.findAll('[role="alert"]');
    expect(alerts).toHaveLength(1);
    expect(alerts[0].attributes("data-test")).toBe("form-error");
    expect(alerts[0].text()).toContain("配额已满，请升级套餐");
  });

  it("新用户首次进入空任务列表时展示欢迎引导，并支持一键运行示例任务（Issue #61 P1-4/6）", async () => {
    localStorage.setItem("vp:welcome_banner", "1");
    const wrapper = await mountWith([]);
    const { useTaskStore } = await import("@/stores/tasks");
    const store = useTaskStore();
    const createSpy = vi.fn().mockResolvedValue({ task_id: "demo-1", status: "planning" });
    store.create = createSpy;

    expect(wrapper.find('[data-test="welcome-onboarding-banner"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="run-demo-task"]').exists()).toBe(true);

    await wrapper.get('[data-test="run-demo-task"]').trigger("click");
    await flushPromises();

    expect(createSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        offline_dataset: true,
        is_demo: true,
        verify_references: false,
      }),
    );
    expect(localStorage.getItem("vp:welcome_banner")).toBeNull();
    localStorage.clear();
  });
});

// 草稿保留（2026-11-26 用户反馈）：从导入文献页回来 / 刷新后，
// 建任务页要自动展开并回填上次未提交的内容；提交成功后清草稿。
describe("任务创建草稿保留", () => {
  const draftUser = { id: "user-draft-tasklist" };

  function draftValues(overrides: Record<string, unknown> = {}) {
    return {
      workflowPreset: "literature_review",
      topic: "导入文献回来后应当自动恢复的综述草稿主题",
      draftText: "",
      seedDois: [],
      seedDraft: "",
      snowballDirection: "both",
      keywords: ["RAG"],
      keywordDraft: "",
      maxDocuments: 15,
      dateFrom: "",
      dateTo: "",
      language: "zh",
      citationStyle: "gbt7714",
      offlineDataset: false,
      useReferenceLibrary: false,
      verifyReferences: false,
      modelSource: "platform",
      selectedCredential: "",
      selectedDocuments: [],
      ...overrides,
    };
  }

  beforeEach(() => {
    localStorage.clear();
    getDraftSpy.mockReset();
    getDraftSpy.mockResolvedValue(null);
  });

  it("存在未提交草稿时自动展开表单并回填内容", async () => {
    saveTaskDraft(draftUser.id, draftValues() as never);

    const wrapper = await mountWith([task()], { realForm: true, user: draftUser });
    await flushPromises();

    expect(wrapper.find('[data-test="draft-restored"]').exists()).toBe(true);
    expect(
      (wrapper.find('[data-test="topic"]').element as HTMLInputElement).value,
    ).toBe("导入文献回来后应当自动恢复的综述草稿主题");
  });

  it("没有草稿时保持收起，不凭空展开表单", async () => {
    const wrapper = await mountWith([task()], { realForm: true, user: draftUser });
    await flushPromises();

    expect(wrapper.find('[data-test="topic"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="draft-restored"]').exists()).toBe(false);
  });

  it("创建成功后清除草稿，下次新建不再回填旧内容", async () => {
    saveTaskDraft(draftUser.id, draftValues() as never);
    const wrapper = await mountWith([task()], { realForm: true, user: draftUser });
    await flushPromises();

    const { useTaskStore } = await import("@/stores/tasks");
    const store = useTaskStore();
    store.create = vi.fn().mockResolvedValue({ task_id: "t-created" }) as never;

    await wrapper.get('[data-test="submit"]').trigger("click");
    await flushPromises();

    expect(store.create).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(taskDraftStorageKey(draftUser.id))).toBeNull();
  });

  it("本地无草稿但云端存在非空草稿时自动展开表单并恢复内容（Issue #68）", async () => {
    getDraftSpy.mockResolvedValue({
      payload: {
        workflow_preset: "literature_review",
        topic: "另一台设备填写并保存到云端的主题",
        keywords: ["CrossDevice"],
      },
      revision: 2,
      updated_at: "2026-11-26T09:20:00Z",
    });

    const wrapper = await mountWith([task()], { realForm: true, user: draftUser });
    await flushPromises();

    expect(wrapper.find('[data-test="draft-restored"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="draft-restored"]').text()).toContain("已恢复云端草稿");
    expect((wrapper.get('[data-test="topic"]').element as HTMLInputElement).value).toBe(
      "另一台设备填写并保存到云端的主题",
    );
  });
});
