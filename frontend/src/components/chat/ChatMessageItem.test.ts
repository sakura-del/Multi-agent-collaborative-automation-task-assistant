import fs from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

const toastSuccessMock = vi.fn();
vi.mock("@/lib/toast", () => ({
  toastSuccess: (...args: unknown[]) => toastSuccessMock(...args),
}));

import ChatMessageItem from "@/components/chat/ChatMessageItem.vue";
import type { ChatMessage } from "@/api/conversations";

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "msg-1",
    conversation_id: "conv-1",
    parent_id: null,
    role: "assistant",
    content: "这是测试响应正文",
    status: "completed",
    model: "deepseek-v4.1-flash",
    prompt_tokens: 0,
    completion_tokens: 0,
    created_at: "2026-11-25T00:00:00Z",
    attachments: [],
    tool_calls: [],
    pending_tool: null,
    error: null,
    ...overrides,
  };
}

describe("ChatMessageItem 单条消息", () => {
  afterEach(() => {
    toastSuccessMock.mockReset();
    vi.restoreAllMocks();
  });

  it("源码不含 <style> 标签，Markdown 保留 .chat-markdown 与 .chat-markdown--user 类", () => {
    const source = fs.readFileSync(
      path.resolve(process.cwd(), "src/components/chat/ChatMessageItem.vue"),
      "utf-8",
    );
    expect(source).not.toContain("<style");

    const assistant = mount(ChatMessageItem, {
      props: { message: makeMessage({ role: "assistant", content: "## 标题\n正文" }) },
    });
    expect(assistant.get('[data-test="chat-body-assistant"]').classes()).toContain("chat-markdown");

    const user = mount(ChatMessageItem, {
      props: { message: makeMessage({ role: "user", content: "用户消息" }) },
    });
    const userBody = user.get('[data-test="chat-body-user"]');
    expect(userBody.classes()).toContain("chat-markdown");
    expect(userBody.classes()).toContain("chat-markdown--user");
  });

  it("使用语义化状态色 token 与减动效类（无裸 amber/emerald）", () => {
    const interrupted = mount(ChatMessageItem, {
      props: { message: makeMessage({ status: "interrupted" }) },
    });
    expect(interrupted.html()).toContain("text-warning");
    expect(interrupted.html()).not.toContain("text-amber-600");

    const streaming = mount(ChatMessageItem, {
      props: {
        message: makeMessage({ status: "streaming" }),
        streamingText: "流式输出中",
      },
    });
    expect(streaming.get('[data-test="chat-streaming-cursor"]').classes()).toContain(
      "motion-reduce:animate-none",
    );

    const approval = mount(ChatMessageItem, {
      props: {
        message: makeMessage({
          status: "waiting_approval",
          tool_calls: [
            {
              id: "t1",
              name: "create_task",
              summary: "创建综述任务",
              status: "pending_approval",
              arguments: {},
              result_preview: null,
            },
          ],
          pending_tool: {
            id: "t1",
            name: "create_task",
            summary: "创建综述任务",
            arguments: {},
          },
        }),
      },
    });
    const timeline = approval.get('[data-test="chat-tool-timeline"]');
    expect(timeline.find("svg.motion-reduce\\:animate-none").exists()).toBe(true);
    expect(approval.html()).not.toContain("text-amber-600");
    expect(approval.html()).not.toContain("text-amber-700");
  });

  it("消息操作条触屏可达（opacity-75 + sm:opacity-0 sm:group-hover/focus-within）", () => {
    const wrapper = mount(ChatMessageItem, {
      props: { message: makeMessage() },
    });
    const copyBtn = wrapper.get('[data-test="chat-copy"]');
    const toolbar = copyBtn.element.parentElement as HTMLElement;
    expect(toolbar.className).toContain("opacity-75");
    expect(toolbar.className).toContain("sm:opacity-0");
    expect(toolbar.className).toContain("sm:group-hover:opacity-100");
    expect(toolbar.className).toContain("sm:group-focus-within:opacity-100");
  });

  it("复制消息调用 clipboard 与 toastSuccess('已复制')，失败时不误报成功", async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: writeTextMock },
      configurable: true,
    });

    const wrapper = mount(ChatMessageItem, {
      props: { message: makeMessage({ content: "待复制正文" }) },
    });

    await wrapper.get('[data-test="chat-copy"]').trigger("click");
    await flushPromises();
    expect(writeTextMock).toHaveBeenCalledWith("待复制正文");
    expect(toastSuccessMock).toHaveBeenCalledWith("已复制");

    toastSuccessMock.mockReset();
    writeTextMock.mockRejectedValueOnce(new Error("Clipboard denied"));
    await wrapper.get('[data-test="chat-copy"]').trigger("click");
    await flushPromises();
    expect(toastSuccessMock).not.toHaveBeenCalled();
  });

  it("异步 writeText 在组件卸载后 resolve 时不误弹 toast 且不泄漏定时器（探针 7B）", async () => {
    vi.useFakeTimers();
    let resolveCopy!: () => void;
    const writeTextMock = vi.fn().mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveCopy = resolve;
        }),
    );
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: writeTextMock },
      configurable: true,
    });

    const wrapper = mount(ChatMessageItem, {
      props: { message: makeMessage({ content: "待复制正文" }) },
    });

    await wrapper.get('[data-test="chat-copy"]').trigger("click");
    wrapper.unmount();
    resolveCopy();
    await flushPromises();

    expect(toastSuccessMock).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
    vi.useRealTimers();
  });
});
