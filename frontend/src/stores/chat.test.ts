import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { conversationApi, parseSseChunk, streamChatMessage } from "@/api/conversations";
import { useChatStore } from "@/stores/chat";

vi.mock("@/api/conversations", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/conversations")>();
  return {
    ...actual,
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
  };
});

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

  it("SSE 断连（network error）时自动回拉服务端已落库回复并清除错误（Issue #58）", async () => {
    const store = useChatStore();
    store.detail = {
      id: "conv-sse-1",
      title: "新会话",
      archived: false,
      message_count: 0,
      leaf_id: null,
      last_message_at: null,
      created_at: "2026-11-25T00:00:00Z",
      updated_at: "2026-11-25T00:00:00Z",
      messages: [],
    };

    const userMsg = {
      id: "m-user-sse",
      conversation_id: "conv-sse-1",
      parent_id: null,
      role: "user" as const,
      content: "请用一句话说明什么是 RAG",
      status: "completed" as const,
      model: null,
      prompt_tokens: 0,
      completion_tokens: 0,
      error: null,
      created_at: "2026-11-25T00:00:01Z",
    };
    const pendingAssistant = {
      id: "m-asst-sse",
      conversation_id: "conv-sse-1",
      parent_id: "m-user-sse",
      role: "assistant" as const,
      content: "",
      status: "pending" as const,
      model: null,
      prompt_tokens: 0,
      completion_tokens: 0,
      error: null,
      created_at: "2026-11-25T00:00:01Z",
    };

    vi.mocked(conversationApi.sendMessage).mockResolvedValueOnce({
      user_message: userMsg,
      assistant_message: pendingAssistant,
    });
    vi.mocked(streamChatMessage).mockRejectedValueOnce(new TypeError("network error"));
    vi.mocked(conversationApi.get).mockResolvedValueOnce({
      ...store.detail,
      message_count: 2,
      leaf_id: "m-asst-sse",
      messages: [
        userMsg,
        {
          ...pendingAssistant,
          status: "completed",
          content: "RAG 是检索增强生成，通过先检索外部知识再生成回答来提升准确性。",
        },
      ],
    });

    await store.send("请用一句话说明什么是 RAG");

    expect(conversationApi.get).toHaveBeenCalledWith("conv-sse-1");
    expect(store.error).toBeNull();
    const asst = store.detail.messages.find((item) => item.id === "m-asst-sse");
    expect(asst?.status).toBe("completed");
    expect(asst?.content).toContain("检索增强生成");
  });

  it("SSE 断连且服务端未产出正文时，状态记为 interrupted 并给出可读中文提示（Issue #58）", async () => {
    const store = useChatStore();
    store.detail = {
      id: "conv-sse-2",
      title: "新会话",
      archived: false,
      message_count: 0,
      leaf_id: null,
      last_message_at: null,
      created_at: "2026-11-25T00:00:00Z",
      updated_at: "2026-11-25T00:00:00Z",
      messages: [],
    };

    const userMsg = {
      id: "m-user-sse2",
      conversation_id: "conv-sse-2",
      parent_id: null,
      role: "user" as const,
      content: "你好",
      status: "completed" as const,
      model: null,
      prompt_tokens: 0,
      completion_tokens: 0,
      error: null,
      created_at: "2026-11-25T00:00:01Z",
    };
    const pendingAssistant = {
      id: "m-asst-sse2",
      conversation_id: "conv-sse-2",
      parent_id: "m-user-sse2",
      role: "assistant" as const,
      content: "",
      status: "pending" as const,
      model: null,
      prompt_tokens: 0,
      completion_tokens: 0,
      error: null,
      created_at: "2026-11-25T00:00:01Z",
    };

    vi.mocked(conversationApi.sendMessage).mockResolvedValueOnce({
      user_message: userMsg,
      assistant_message: pendingAssistant,
    });
    vi.mocked(streamChatMessage).mockRejectedValueOnce(new TypeError("network error"));
    vi.mocked(conversationApi.get).mockResolvedValue({
      ...store.detail,
      message_count: 2,
      leaf_id: "m-asst-sse2",
      messages: [
        userMsg,
        {
          ...pendingAssistant,
          status: "interrupted",
          content: "",
        },
      ],
    });

    await store.send("你好");

    const asst = store.detail.messages.find((item) => item.id === "m-asst-sse2");
    expect(asst?.status).toBe("interrupted");
    expect(store.error).toContain("流式连接中断");
  });

  it("parseSseChunk 兼容 CRLF（\\r\\n\\r\\n）分隔帧（Issue #58）", () => {
    const deltas: string[] = [];
    let doneCount = 0;
    const tail = parseSseChunk(
      'event: delta\r\ndata: {"text":"你好"}\r\n\r\nevent: done\r\ndata: {"message_id":"m1"}\r\n\r\n',
      {
        onDelta: (text) => deltas.push(text),
        onDone: () => {
          doneCount += 1;
        },
      },
      true,
    );
    expect(deltas).toEqual(["你好"]);
    expect(doneCount).toBe(1);
    expect(tail).toBe("");
  });
});
