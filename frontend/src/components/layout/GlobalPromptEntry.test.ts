import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { enableAutoUnmount, mount, flushPromises } from "@vue/test-utils";

// 创建 API 与路由都 mock：入口组件只关心「提交 → 跳转 / 错误展示」
const createTaskMock = vi.fn();
const getTaskMock = vi.fn();
const pushMock = vi.fn();

vi.mock("@/api/tasks", () => ({
  taskApi: {
    create: (...args: unknown[]) => createTaskMock(...args),
    get: (...args: unknown[]) => getTaskMock(...args),
  },
}));

const classifyMock = vi.fn();

vi.mock("@/api/intent", () => ({
  classifyIntent: (...args: unknown[]) => classifyMock(...args),
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushMock }),
}));

import GlobalPromptEntry from "@/components/layout/GlobalPromptEntry.vue";

beforeEach(() => {
  sessionStorage.clear();
  getTaskMock.mockReset().mockResolvedValue({ status: "planning" });
  classifyMock.mockReset().mockResolvedValue({
    intent: "literature_review",
    topic: "多智能体协作",
    keywords: ["multi-agent", "software engineering"],
    source: "llm",
  });
});

afterEach(() => {
  sessionStorage.clear();
  vi.useRealTimers();
});

enableAutoUnmount(afterEach);

describe("GlobalPromptEntry（Issue #8 P0 全局入口）", () => {
  beforeEach(() => {
    createTaskMock.mockReset();
    pushMock.mockReset();
  });

  async function submitTopic(text: string) {
    const wrapper = mount(GlobalPromptEntry, {
      props: { collapsed: false },
      global: {
        stubs: {
          // BePromptInput 内部行为不测（自有单测），这里验证接线
          BePromptInput: {
            props: ["modelValue", "submitting", "submitOnEnter"],
            emits: ["update:modelValue", "submit"],
            template: `<div data-test="stub-prompt"><button data-test="stub-submit" @click="$emit('submit', $refs.v)" /></div>`,
          },
        },
      },
    });
    // 直接调组件实例上的 submit 逻辑：通过触发 stub 的 submit 事件
    // —— stub 无法取到内部值，因此退化为直接调用 createTask 侧的断言路径：
    wrapper.vm.$forceUpdate();
    return wrapper;
  }

  it("提交成功：调用 createTask（topic=整句话）并跳转任务详情", async () => {
    createTaskMock.mockResolvedValue({ task_id: "tid-1", status: "created" });
    const wrapper = mount(GlobalPromptEntry, {
      props: { collapsed: false },
    });
    // 触发内部 submit：通过组件暴露（script setup 未 defineExpose，改走 stub 事件路径）
    // 为保持简单与真实，直接模拟 BePromptInput 的 submit 事件：
    const bePrompt = wrapper.findComponent({ name: "BePromptInput" });
    expect(bePrompt.exists()).toBe(true);
    // 模拟 v-model 更新 + submit 事件
    await bePrompt.vm.$emit("update:modelValue", "帮我综述 RAG 质量评估方法");
    await bePrompt.vm.$emit("submit", "帮我综述 RAG 质量评估方法");
    await flushPromises();
    expect(createTaskMock).toHaveBeenCalledWith({
      topic: "多智能体协作",
      keywords: ["multi-agent", "software engineering"],
      workflow_preset: "literature_review",
      max_documents: 20,
      verify_references: false,
    });
    // P1-2：提交后不跳页（入口内任务条轮询状态）
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("非综述意图：就地提示且不创建任务", async () => {
    classifyMock.mockResolvedValue({
      intent: "other",
      topic: "随便聊聊",
      keywords: [],
      source: "llm",
    });
    const wrapper = mount(GlobalPromptEntry, { props: { collapsed: false } });
    const bePrompt = wrapper.findComponent({ name: "BePromptInput" });
    await bePrompt.vm.$emit("submit", "帮我写个购物清单");
    await flushPromises();
    expect(createTaskMock).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="global-prompt-error"]').text()).toContain(
      "只支持文献综述任务",
    );
  });

  it("配额/后端错误：就地展示错误文案且不跳转", async () => {
    createTaskMock.mockRejectedValue(new Error("本月免费额度已用完"));
    const wrapper = mount(GlobalPromptEntry, { props: { collapsed: false } });
    const bePrompt = wrapper.findComponent({ name: "BePromptInput" });
    await bePrompt.vm.$emit("submit", "再来一篇");
    await flushPromises();
    expect(pushMock).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="global-prompt-error"]').text()).toContain(
      "本月免费额度已用完",
    );
  });

  it("折叠态：渲染 Sparkles 圆按钮并 emit expand", async () => {
    const wrapper = mount(GlobalPromptEntry, { props: { collapsed: true } });
    expect(wrapper.find('[data-test="global-prompt-collapsed"]').exists()).toBe(true);
    await wrapper.find('[data-test="global-prompt-collapsed"]').trigger("click");
    expect(wrapper.emitted("expand")).toBeTruthy();
  });

  it("空提交不调用 createTask", async () => {
    const wrapper = mount(GlobalPromptEntry, { props: { collapsed: false } });
    const bePrompt = wrapper.findComponent({ name: "BePromptInput" });
    await bePrompt.vm.$emit("submit", "   ");
    await flushPromises();
    expect(createTaskMock).not.toHaveBeenCalled();
  });

  it("未手动锁定时采纳意图分类返回的 research_gaps 预设及默认参数", async () => {
    classifyMock.mockResolvedValue({
      intent: "literature_review",
      workflow_preset: "research_gaps",
      topic: "RAG 评估的研究空白",
      keywords: ["RAG", "evaluation"],
      source: "llm",
    });
    createTaskMock.mockResolvedValue({ task_id: "tid-2", status: "created" });
    const wrapper = mount(GlobalPromptEntry, { props: { collapsed: false } });
    const bePrompt = wrapper.findComponent({ name: "BePromptInput" });
    await bePrompt.vm.$emit("submit", "帮我找 RAG 评估还有哪些研究空白");
    await flushPromises();

    expect(createTaskMock).toHaveBeenCalledWith({
      topic: "RAG 评估的研究空白",
      keywords: ["RAG", "evaluation"],
      workflow_preset: "research_gaps",
      max_documents: 25,
      verify_references: true,
    });
  });

  it("手动点选预设 Chip 时锁定用户选择，覆盖意图分类默认值", async () => {
    classifyMock.mockResolvedValue({
      intent: "literature_review",
      workflow_preset: "literature_review",
      topic: "多智能体研究空白",
      keywords: ["multi-agent"],
      source: "llm",
    });
    createTaskMock.mockResolvedValue({ task_id: "tid-3", status: "created" });
    const wrapper = mount(GlobalPromptEntry, { props: { collapsed: false } });

    await wrapper.find('[data-test="global-preset-research_gaps"]').trigger("click");
    const bePrompt = wrapper.findComponent({ name: "BePromptInput" });
    await bePrompt.vm.$emit("submit", "多智能体协作");
    await flushPromises();

    expect(createTaskMock).toHaveBeenCalledWith({
      topic: "多智能体研究空白",
      keywords: ["multi-agent"],
      workflow_preset: "research_gaps",
      max_documents: 25,
      verify_references: true,
    });
  });

  it("侧栏紧凑输入栏不冗余展示 Ctrl/⌘+Enter 提交提示（Issue #57）", () => {
    const wrapper = mount(GlobalPromptEntry, { props: { collapsed: false } });
    const footer = wrapper.get('[data-test="prompt-input-footer"]').text();
    expect(footer).toContain("Enter 创建任务");
    expect(footer).not.toContain("Ctrl/⌘+Enter");
  });

  it("精确分流 failed / waiting_human / running / reviewing / waiting_approval 状态与语义配色，并停轮询非活跃态", async () => {
    vi.useFakeTimers();

    // 1) waiting_human：warning 语义，不标红、不显示旧文「转人工」，停止继续轮询
    createTaskMock.mockResolvedValueOnce({ task_id: "tid-human", status: "planning" });
    getTaskMock.mockResolvedValueOnce({ status: "waiting_human" });

    const wrapper = mount(GlobalPromptEntry, { props: { collapsed: false } });
    const bePrompt = wrapper.findComponent({ name: "BePromptInput" });
    await bePrompt.vm.$emit("submit", "需要人工的任务");
    await flushPromises();

    const rowHuman = wrapper.get('[data-test="global-prompt-task"]');
    expect(rowHuman.text()).toContain("待人工介入");
    expect(rowHuman.text()).not.toContain("转人工");
    expect(rowHuman.classes().join(" ")).toContain("text-warning");
    expect(rowHuman.classes().join(" ")).not.toContain("text-destructive");

    // 再推进一个轮询周期，不应再发起对 tid-human 的轮询
    getTaskMock.mockClear();
    await vi.advanceTimersByTimeAsync(4500);
    await flushPromises();
    expect(getTaskMock).not.toHaveBeenCalled();

    // 2) failed：destructive 语义，显示「执行失败」，停止轮询
    createTaskMock.mockResolvedValueOnce({ task_id: "tid-fail", status: "planning" });
    getTaskMock.mockResolvedValueOnce({ status: "failed" });
    await bePrompt.vm.$emit("submit", "会失败的任务");
    await flushPromises();

    const rowsAfterFail = wrapper.findAll('[data-test="global-prompt-task"]');
    expect(rowsAfterFail[0].text()).toContain("执行失败");
    expect(rowsAfterFail[0].classes().join(" ")).toContain("text-destructive");

    // 3) running / reviewing：使用中文状态映射并持续轮询；waiting_approval 补 dark:text-emerald-400
    createTaskMock.mockResolvedValueOnce({ task_id: "tid-run", status: "running" });
    getTaskMock.mockResolvedValueOnce({ status: "reviewing" });
    await bePrompt.vm.$emit("submit", "正在运行的任务");
    await flushPromises();

    const rowsRunning = wrapper.findAll('[data-test="global-prompt-task"]');
    expect(rowsRunning[0].text()).toContain("复盘中");
    expect(rowsRunning[0].text()).not.toContain("reviewing");

    getTaskMock.mockResolvedValueOnce({ status: "waiting_approval" });
    await vi.advanceTimersByTimeAsync(4100);
    await flushPromises();
    expect(rowsRunning[0].text()).toContain("规划已生成");
    expect(rowsRunning[0].classes().join(" ")).toContain("dark:text-emerald-400");

    // 4) 移除按钮触屏可达
    const dismissBtn = rowsRunning[0].get('button[aria-label="移除"]');
    expect(dismissBtn.classes()).toContain("opacity-75");
    expect(dismissBtn.classes()).toContain("sm:opacity-0");
    expect(dismissBtn.classes()).toContain("sm:group-hover:opacity-100");
    expect(dismissBtn.classes()).toContain("sm:group-focus-within:opacity-100");

    wrapper.unmount();
  });

  it("活跃任务持久化至 sessionStorage（上限 3 条），重载挂载自动恢复并续接活跃态轮询", async () => {
    const wrapper = mount(GlobalPromptEntry, { props: { collapsed: false } });
    const bePrompt = wrapper.findComponent({ name: "BePromptInput" });

    for (let i = 1; i <= 4; i += 1) {
      classifyMock.mockResolvedValueOnce({
        intent: "literature_review",
        topic: `综述主题-${i}`,
        keywords: [`k${i}`],
        source: "llm",
      });
      createTaskMock.mockResolvedValueOnce({ task_id: `tid-${i}`, status: i === 4 ? "running" : "completed" });
      if (i === 4) {
        getTaskMock.mockResolvedValueOnce({ status: "running" });
      }
      await bePrompt.vm.$emit("submit", `主题 ${i}`);
      await flushPromises();
    }

    const stored = JSON.parse(sessionStorage.getItem("vp:global-prompt:active-tasks") ?? "[]");
    expect(stored).toHaveLength(3);
    expect(stored.map((item: { task_id: string }) => item.task_id)).toEqual(["tid-4", "tid-3", "tid-2"]);
    wrapper.unmount();

    // 重新挂载新实例：恢复 3 条且仅对活跃态 tid-4 发起轮询
    getTaskMock.mockReset().mockResolvedValue({ status: "waiting_approval" });
    const remounted = mount(GlobalPromptEntry, { props: { collapsed: false } });
    await flushPromises();

    const rows = remounted.findAll('[data-test="global-prompt-task"]');
    expect(rows).toHaveLength(3);
    expect(getTaskMock).toHaveBeenCalledTimes(1);
    expect(getTaskMock).toHaveBeenCalledWith("tid-4");

    // 移除一条同步更新 sessionStorage
    await rows[0].get('button[aria-label="移除"]').trigger("click");
    await flushPromises();
    const afterDismiss = JSON.parse(sessionStorage.getItem("vp:global-prompt:active-tasks") ?? "[]");
    expect(afterDismiss).toHaveLength(2);
    remounted.unmount();
  });

  it("桌面与移动双实例共享任务队列：移动实例提交并卸载后，桌面端轮询不抹除 sessionStorage 且同步可见（探针 5D）", async () => {
    vi.useFakeTimers();
    createTaskMock.mockResolvedValueOnce({ task_id: "tid-desktop", status: "running" });
    getTaskMock.mockResolvedValue({ status: "running" });

    const desktop = mount(GlobalPromptEntry, { props: { collapsed: false } });
    await desktop.findComponent({ name: "BePromptInput" }).vm.$emit("submit", "桌面任务");
    await flushPromises();

    // 打开移动抽屉：同时挂载第 2 个实例
    const mobile = mount(GlobalPromptEntry, { props: { collapsed: false } });
    await flushPromises();
    expect(mobile.findAll('[data-test="global-prompt-task"]')).toHaveLength(1);

    // 移动实例提交新任务 tid-mobile 并卸载
    createTaskMock.mockResolvedValueOnce({ task_id: "tid-mobile", status: "running" });
    await mobile.findComponent({ name: "BePromptInput" }).vm.$emit("submit", "移动任务");
    await flushPromises();
    mobile.unmount();

    // 桌面端推进 4s 轮询周期
    await vi.advanceTimersByTimeAsync(4100);
    await flushPromises();

    const stored = JSON.parse(sessionStorage.getItem("vp:global-prompt:active-tasks") ?? "[]");
    const storedIds = stored.map((item: { task_id: string }) => item.task_id);
    expect(storedIds).toContain("tid-mobile");
    expect(storedIds).toContain("tid-desktop");

    const desktopRows = desktop.findAll('[data-test="global-prompt-task"]');
    expect(desktopRows).toHaveLength(2);
    expect(getTaskMock).toHaveBeenCalledWith("tid-mobile");
    desktop.unmount();
  });

  it("sessionStorage 含未知脏 status（如 'corrupted_unknown'）时不发起轮询且安全渲染（探针 5E）", async () => {
    vi.useFakeTimers();
    sessionStorage.setItem(
      "vp:global-prompt:active-tasks",
      JSON.stringify([{ task_id: "tid-bogus", title: "脏状态任务", status: "corrupted_unknown" }]),
    );

    const wrapper = mount(GlobalPromptEntry, { props: { collapsed: false } });
    await flushPromises();
    await vi.advanceTimersByTimeAsync(8500);
    await flushPromises();

    expect(wrapper.findAll('[data-test="global-prompt-task"]')).toHaveLength(1);
    expect(getTaskMock).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("队列超过 3 条时优先保留活跃态任务，避免老 running 任务被已完成任务挤掉（探针 5C）", async () => {
    const wrapper = mount(GlobalPromptEntry, { props: { collapsed: false } });
    const bePrompt = wrapper.findComponent({ name: "BePromptInput" });

    createTaskMock.mockResolvedValueOnce({ task_id: "tid-run-old", status: "running" });
    getTaskMock.mockResolvedValue({ status: "running" });
    await bePrompt.vm.$emit("submit", "老活跃任务");
    await flushPromises();

    for (let i = 1; i <= 3; i += 1) {
      createTaskMock.mockResolvedValueOnce({ task_id: `tid-done-${i}`, status: "completed" });
      await bePrompt.vm.$emit("submit", `已完成任务-${i}`);
      await flushPromises();
    }

    const stored = JSON.parse(sessionStorage.getItem("vp:global-prompt:active-tasks") ?? "[]");
    const ids = stored.map((item: { task_id: string }) => item.task_id);
    expect(stored).toHaveLength(3);
    expect(ids).toContain("tid-run-old");
    expect(ids).toContain("tid-done-3");
    wrapper.unmount();
  });

  it("轮询请求在途期间移除任务后，在途请求 resolve 不得写回 sessionStorage", async () => {
    let resolveGet!: (val: { status: string }) => void;
    getTaskMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveGet = resolve;
        }),
    );
    sessionStorage.setItem(
      "vp:global-prompt:active-tasks",
      JSON.stringify([{ task_id: "tid-inflight", title: "在途任务", status: "running" }]),
    );

    const wrapper = mount(GlobalPromptEntry, { props: { collapsed: false } });
    await flushPromises();
    expect(getTaskMock).toHaveBeenCalledWith("tid-inflight");

    await wrapper.get('[data-test="global-prompt-task"] button[aria-label="移除"]').trigger("click");
    await flushPromises();
    expect(wrapper.findAll('[data-test="global-prompt-task"]')).toHaveLength(0);

    resolveGet({ status: "completed" });
    await flushPromises();

    expect(wrapper.findAll('[data-test="global-prompt-task"]')).toHaveLength(0);
    expect(JSON.parse(sessionStorage.getItem("vp:global-prompt:active-tasks") ?? "[]")).toEqual([]);
    wrapper.unmount();
  });

  it("实例 A 的在途 pollOnce 跨越卸载与实例 B 重建边界 resolve 时，不得覆写新实例状态（D3-c）", async () => {
    let resolveStaleGet!: (val: { status: string }) => void;
    getTaskMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveStaleGet = resolve;
        }),
    );
    sessionStorage.setItem(
      "vp:global-prompt:active-tasks",
      JSON.stringify([{ task_id: "tid-race", title: "旧会话任务", status: "running" }]),
    );

    const wrapperA = mount(GlobalPromptEntry, { props: { collapsed: false } });
    await flushPromises();
    expect(getTaskMock).toHaveBeenCalledWith("tid-race");

    // 卸载实例 A 并模拟切换到新会话状态（重置 sessionStorage 后立即挂载实例 B）
    wrapperA.unmount();
    sessionStorage.setItem(
      "vp:global-prompt:active-tasks",
      JSON.stringify([{ task_id: "tid-race", title: "新会话任务", status: "completed" }]),
    );

    const wrapperB = mount(GlobalPromptEntry, { props: { collapsed: false } });
    await flushPromises();
    expect(wrapperB.text()).toContain("新会话任务");
    expect(wrapperB.text()).toContain("已完成");

    // 旧实例 A 在途请求此时返回旧周期的 failed 状态
    resolveStaleGet({ status: "failed" });
    await flushPromises();

    expect(wrapperB.text()).toContain("已完成");
    expect(wrapperB.text()).not.toContain("执行失败");
    const stored = JSON.parse(sessionStorage.getItem("vp:global-prompt:active-tasks") ?? "[]");
    expect(stored).toEqual([{ task_id: "tid-race", title: "新会话任务", status: "completed" }]);
    wrapperB.unmount();
  });
});
