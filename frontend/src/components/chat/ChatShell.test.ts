import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";

const pushMock = vi.fn();
const toastSuccessMock = vi.fn();
const toastErrorMock = vi.fn();

const listMock = vi.fn();
const getMock = vi.fn();
const createMock = vi.fn();
const removeMock = vi.fn();
const capabilitiesMock = vi.fn();
const sendMessageMock = vi.fn();

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@/lib/toast", () => ({
  toastSuccess: (...args: unknown[]) => toastSuccessMock(...args),
  toastError: (...args: unknown[]) => toastErrorMock(...args),
}));

vi.mock("@/api/conversations", () => ({
  conversationApi: {
    list: (...args: unknown[]) => listMock(...args),
    get: (...args: unknown[]) => getMock(...args),
    create: (...args: unknown[]) => createMock(...args),
    remove: (...args: unknown[]) => removeMock(...args),
    capabilities: (...args: unknown[]) => capabilitiesMock(...args),
    sendMessage: (...args: unknown[]) => sendMessageMock(...args),
    update: vi.fn().mockResolvedValue({}),
    requestStop: vi.fn().mockResolvedValue({}),
    confirmTool: vi.fn(),
  },
  streamChatMessage: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/api/knowledge", () => ({
  knowledgeApi: {
    upload: vi.fn(),
  },
}));

import ChatShell from "@/components/chat/ChatShell.vue";
import { useChatStore } from "@/stores/chat";

describe("ChatShell 会话内核", () => {
  const wrappers: Array<{ unmount: () => void }> = [];

  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    pushMock.mockReset();
    toastSuccessMock.mockReset();
    toastErrorMock.mockReset();
    listMock.mockReset().mockResolvedValue([]);
    getMock.mockReset();
    createMock.mockReset().mockResolvedValue({
      id: "conv-new",
      title: "新会话",
      archived: false,
      message_count: 0,
      leaf_id: null,
      last_message_at: null,
      created_at: "2026-11-25T00:00:00Z",
      updated_at: "2026-11-25T00:00:00Z",
    });
    removeMock.mockReset().mockResolvedValue(undefined);
    capabilitiesMock.mockReset().mockResolvedValue({
      max_attachments: 5,
      max_file_mb: 30,
      vision_enabled: false,
      image_reason: null,
    });
    sendMessageMock.mockReset();
    delete (window as unknown as Record<string, unknown>).SpeechRecognition;
    delete (window as unknown as Record<string, unknown>).webkitSpeechRecognition;
  });

  afterEach(() => {
    while (wrappers.length) {
      wrappers.pop()?.unmount();
    }
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("floating 与 dock 具备 393px 防溢出类且不包含 relative（守卫 position: fixed）", async () => {
    localStorage.setItem("vp:chat:open", "1");

    const wrapper = mount(ChatShell, {
      props: { variant: "floating" },
    });
    wrappers.push(wrapper);
    await flushPromises();

    const floatingShell = document.body.querySelector('[data-test="chat-shell"]') as HTMLElement;
    expect(floatingShell).not.toBeNull();
    expect(floatingShell.className).toContain("w-[min(380px,calc(100vw-2rem))]");
    expect(floatingShell.className).toContain("max-h-[min(580px,calc(100svh-2rem))]");
    expect(floatingShell.className.split(/\s+/)).toContain("z-40");
    expect(floatingShell.className).not.toContain("z-[200]");
    expect(floatingShell.className.split(/\s+/)).not.toContain("relative");
    expect(floatingShell.className.split(/\s+/)).not.toContain("w-[380px]");

    const toggleBtn = document.body.querySelector('[data-test="chat-variant-toggle"]') as HTMLButtonElement;
    toggleBtn.click();
    await flushPromises();

    const dockShell = document.body.querySelector('[data-test="chat-shell"]') as HTMLElement;
    expect(document.body.querySelectorAll('[data-test="chat-shell"]')).toHaveLength(1);
    expect(dockShell.className).toContain("w-full");
    expect(dockShell.className).toContain("md:w-[440px]");
    expect(dockShell.className.split(/\s+/)).toContain("z-40");
    expect(dockShell.className).not.toContain("z-[200]");
    expect(dockShell.className).toContain("border-border-strong");
    expect(dockShell.className).toContain("shadow-2xl");
    expect(dockShell.className.split(/\s+/)).not.toContain("relative");
    expect(dockShell.className.split(/\s+/)).not.toContain("w-[440px]");

    // W2：收起后渲染的 chat-fab 必须位于 z-30（低于弹层遮罩 z-50）
    const closeBtn = document.body.querySelector('[data-test="chat-close"]') as HTMLButtonElement;
    closeBtn.click();
    await flushPromises();
    const fab = document.body.querySelector('[data-test="chat-fab"]') as HTMLElement | null;
    expect(fab).not.toBeNull();
    expect(fab!.className.split(/\s+/)).toContain("z-30");
    expect(fab!.className).not.toContain("z-[200]");
  });

  it("不支持语音时不渲染「当前浏览器不支持语音」占位噪音，支持时显示「语音仅本机识别」", async () => {
    const wrapper1 = mount(ChatShell, { props: { variant: "full" } });
    wrappers.push(wrapper1);
    await flushPromises();
    expect(wrapper1.text()).not.toContain("当前浏览器不支持语音");
    expect(wrapper1.find('[data-test="chat-voice"]').exists()).toBe(false);

    (window as unknown as Record<string, unknown>).SpeechRecognition = function () {};
    const wrapper2 = mount(ChatShell, { props: { variant: "full" } });
    wrappers.push(wrapper2);
    await flushPromises();
    expect(wrapper2.find('[data-test="chat-voice"]').exists()).toBe(true);
    expect(wrapper2.text()).toContain("语音仅本机识别");
  });

  it("示例引导「导入这篇 BibTeX」只预填草稿并聚焦输入框而不直接发送；「我的套餐还剩几篇？」直接发送", async () => {
    const wrapper = mount(ChatShell, { props: { variant: "full" } });
    wrappers.push(wrapper);
    await flushPromises();

    const store = useChatStore();
    const sendSpy = vi.spyOn(store, "send").mockResolvedValue(undefined);

    const bibBtn = wrapper.findAll("button").find((b) => b.text().includes("导入这篇 BibTeX"));
    expect(bibBtn).toBeDefined();
    await bibBtn!.trigger("click");
    await flushPromises();

    expect(sendSpy).not.toHaveBeenCalled();
    const textarea = wrapper.get('[data-test="chat-input"]').element as HTMLTextAreaElement;
    expect(textarea.value).toContain("@article");

    const quotaBtn = wrapper.findAll("button").find((b) => b.text().includes("我的套餐还剩几篇？"));
    expect(quotaBtn).toBeDefined();
    await quotaBtn!.trigger("click");
    await flushPromises();
    expect(sendSpy).toHaveBeenCalledTimes(1);
  });

  it("删除整段会话接入 ConfirmBar（支持取消与 Esc），确认后才调用 store.remove 并弹 toast", async () => {
    listMock.mockResolvedValue([
      {
        id: "conv-1",
        title: "待删除会话",
        archived: false,
        message_count: 2,
        leaf_id: null,
        last_message_at: null,
        created_at: "2026-11-25T00:00:00Z",
        updated_at: "2026-11-25T00:00:00Z",
      },
    ]);
    getMock.mockResolvedValue({
      id: "conv-1",
      title: "待删除会话",
      archived: false,
      message_count: 0,
      leaf_id: null,
      last_message_at: null,
      created_at: "2026-11-25T00:00:00Z",
      updated_at: "2026-11-25T00:00:00Z",
      messages: [],
    });

    const wrapper = mount(ChatShell, { props: { variant: "full" } });
    wrappers.push(wrapper);
    await flushPromises();

    await wrapper.get('[data-test="chat-toggle-list"]').trigger("click");
    await flushPromises();

    const deleteTrigger = wrapper.get('button[aria-label="删除会话"]');
    expect(deleteTrigger.classes()).toContain("opacity-75");
    expect(deleteTrigger.classes()).toContain("sm:opacity-0");

    await deleteTrigger.trigger("click");
    await flushPromises();

    expect(removeMock).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="chat-delete-confirm"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="chat-delete-cancel"]').exists()).toBe(true);

    // 取消不删除
    await wrapper.get('[data-test="chat-delete-cancel"]').trigger("click");
    await flushPromises();
    expect(removeMock).not.toHaveBeenCalled();

    // 重新点击后按 Escape 取消确认条，且不关闭 ChatShell
    await deleteTrigger.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-test="chat-delete-confirm"]').exists()).toBe(true);
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }),
    );
    await flushPromises();
    expect(wrapper.find('[data-test="chat-delete-confirm"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="chat-shell"]').exists()).toBe(true);
    expect(removeMock).not.toHaveBeenCalled();

    // 重新点击并确认删除
    await deleteTrigger.trigger("click");
    await flushPromises();
    await wrapper.get('[data-test="chat-delete-confirm"]').trigger("click");
    await flushPromises();

    expect(removeMock).toHaveBeenCalledWith("conv-1");
    expect(toastSuccessMock).toHaveBeenCalledWith("已删除会话");
  });

  it("流式回复支持 autoFollow 跟底，用户上滚 >48px 打断并浮现「回到最新 ↓」胶囊", async () => {
    const rafCallbacks: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb: FrameRequestCallback) => {
      rafCallbacks.push(cb);
      return rafCallbacks.length;
    });

    const wrapper = mount(ChatShell, { props: { variant: "full" } });
    wrappers.push(wrapper);
    await flushPromises();

    const store = useChatStore();
    const messagesEl = wrapper.get('[data-test="chat-messages"]').element as HTMLElement;

    let scrollTopVal = 700;
    Object.defineProperty(messagesEl, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(messagesEl, "clientHeight", { value: 300, configurable: true });
    Object.defineProperty(messagesEl, "scrollTop", {
      get: () => scrollTopVal,
      set: (v: number) => {
        scrollTopVal = v;
      },
      configurable: true,
    });
    const scrollToMock = vi.fn();
    messagesEl.scrollTo = scrollToMock;

    // 处于底部时：流式文本变化应自动跟底
    store.streamingText = "流式片段1";
    await nextTick();
    for (const cb of rafCallbacks.splice(0)) cb(16);
    expect(scrollTopVal).toBe(1000);
    expect(wrapper.find('[data-test="chat-scroll-bottom"]').exists()).toBe(false);

    // 用户上滚至 distanceToBottom = 1000 - 500 - 300 = 200 > 48
    scrollTopVal = 500;
    await wrapper.get('[data-test="chat-messages"]').trigger("scroll");
    await flushPromises();

    const pill = wrapper.find('[data-test="chat-scroll-bottom"]');
    expect(pill.exists()).toBe(true);
    expect(pill.text()).toContain("回到最新");

    // 上滚后新的流式输出不得抢走滚动条
    store.streamingText = "流式片段1 + 更多";
    await nextTick();
    for (const cb of rafCallbacks.splice(0)) cb(16);
    expect(scrollTopVal).toBe(500);

    // 点击回到最新胶囊恢复 autoFollow 并滚底
    await pill.trigger("click");
    await flushPromises();
    expect(scrollToMock).toHaveBeenCalled();
    expect(wrapper.find('[data-test="chat-scroll-bottom"]').exists()).toBe(false);
  });

  it("流式与上传中的 Loader2 均具备 motion-reduce:animate-none", async () => {
    const wrapper = mount(ChatShell, { props: { variant: "full" } });
    wrappers.push(wrapper);
    await flushPromises();

    const store = useChatStore();
    store.streamingMessageId = "msg-stream";
    await flushPromises();

    expect(wrapper.find("header svg.motion-reduce\\:animate-none").exists()).toBe(true);
  });
});
