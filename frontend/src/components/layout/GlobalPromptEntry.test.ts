import { describe, expect, it, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

// 创建 API 与路由都 mock：入口组件只关心「提交 → 跳转 / 错误展示」
const createTaskMock = vi.fn();
const pushMock = vi.fn();

vi.mock("@/api/tasks", () => ({
  taskApi: { create: (...args: unknown[]) => createTaskMock(...args) },
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushMock }),
}));

import GlobalPromptEntry from "@/components/layout/GlobalPromptEntry.vue";

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
    expect(createTaskMock).toHaveBeenCalledWith({ topic: "帮我综述 RAG 质量评估方法" });
    expect(pushMock).toHaveBeenCalledWith("/tasks/tid-1");
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
});
