import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { conversationApi, streamChatMessage } from "@/api/conversations";
import { useChatStore } from "@/stores/chat";

vi.mock("@/api/conversations", () => ({
  conversationApi: {
    list: vi.fn().mockResolvedValue([]),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn().mockResolvedValue({}),
    remove: vi.fn(),
    sendMessage: vi.fn(),
    confirmTool: vi.fn(),
    requestStop: vi.fn(),
    capabilities: vi.fn(),
  },
  streamChatMessage: vi.fn().mockResolvedValue(undefined),
}));

describe("chat store 并发发送防重（Issue #56）", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("sendMessage 在途期间连点两次只发出一次请求，且不重复追加消息", async () => {
    const store = useChatStore();
    store.detail = {
      id: "conv-1",
      title: "新会话",
      archived: false,
      message_count: 0,
      leaf_id: null,
      last_message_at: null,
      created_at: "2026-11-24T00:00:00Z",
      updated_at: "2026-11-24T00:00:00Z",
      messages: [],
    };

    let resolveSend!: (value: unknown) => void;
    vi.mocked(conversationApi.sendMessage).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSend = resolve;
        }) as never,
    );

    const first = store.send("我的套餐还剩几篇？");
    const second = store.send("我的套餐还剩几篇？");

    expect(store.sending).toBe(true);
    expect(conversationApi.sendMessage).toHaveBeenCalledTimes(1);

    resolveSend({
      user_message: {
        id: "m-user-1",
        conversation_id: "conv-1",
        parent_id: null,
        role: "user",
        content: "我的套餐还剩几篇？",
        status: "completed",
        model: null,
        prompt_tokens: 0,
        completion_tokens: 0,
        error: null,
        created_at: "2026-11-24T00:00:01Z",
      },
      assistant_message: {
        id: "m-asst-1",
        conversation_id: "conv-1",
        parent_id: "m-user-1",
        role: "assistant",
        content: "你当前是免费版。",
        status: "pending",
        model: null,
        prompt_tokens: 0,
        completion_tokens: 0,
        error: null,
        created_at: "2026-11-24T00:00:01Z",
      },
    });

    await Promise.all([first, second]);

    expect(conversationApi.sendMessage).toHaveBeenCalledTimes(1);
    expect(streamChatMessage).toHaveBeenCalledTimes(1);
    expect(store.detail.messages).toHaveLength(2);
    expect(store.detail.message_count).toBe(2);
    expect(store.sending).toBe(false);
  });
});
