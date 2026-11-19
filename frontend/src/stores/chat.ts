import { defineStore } from "pinia";
import { computed, ref } from "vue";

import {
  conversationApi,
  streamChatMessage,
  type ChatConversation,
  type ChatConversationDetail,
  type ChatMessage,
} from "@/api/conversations";

/**
 * 会话内核状态（Issue #8 P0）。
 *
 * ## 为什么消息用「扁平 + 前端建树」
 *
 * 后端返回全部消息（含所有分支）的扁平列表；树只有前端需要（渲染与分支切换），
 * 后端存 `parent_id` 就够。把建树放前端意味着切分支零请求（只是换 leaf）。
 *
 * ## 流式文本放在哪里
 *
 * `streamingText` 单独存，不逐 delta 写回 tree——每次 delta 触发整棵树响应式
 * 更新会把列表里所有消息重渲染一遍（长会话会卡）。消息面板只订阅
 * `streamingText`，结束后一次性合并进 tree。
 */
export const useChatStore = defineStore("chat", () => {
  const conversations = ref<ChatConversation[]>([]);
  const detail = ref<ChatConversationDetail | null>(null);
  /** 当前查看的分支末端（决定渲染哪条路径） */
  const activeLeafId = ref<string | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);

  /** 正在流式生成的消息与累计文本 */
  const streamingMessageId = ref<string | null>(null);
  const streamingText = ref("");
  let controller: AbortController | null = null;

  const activeConversation = computed(() => detail.value);

  /** parent_id → 子消息（按创建时间排序） */
  const childrenByParent = computed(() => {
    const map = new Map<string | null, ChatMessage[]>();
    for (const message of detail.value?.messages ?? []) {
      const key = message.parent_id;
      const list = map.get(key) ?? [];
      list.push(message);
      map.set(key, list);
    }
    return map;
  });

  /** 从叶子回溯的当前路径（渲染用的主列表） */
  const activePath = computed<ChatMessage[]>(() => {
    const byId = new Map((detail.value?.messages ?? []).map((m) => [m.id, m]));
    let cursor = activeLeafId.value;
    const path: ChatMessage[] = [];
    while (cursor) {
      const node = byId.get(cursor);
      if (!node) break;
      path.push(node);
      cursor = node.parent_id;
    }
    return path.reverse();
  });

  /** 某条消息的分支信息：父节点下第几条 / 共几条 */
  function branchInfo(messageId: string): { index: number; total: number; siblings: string[] } {
    const byId = new Map((detail.value?.messages ?? []).map((m) => [m.id, m]));
    const node = byId.get(messageId);
    if (!node) return { index: 1, total: 1, siblings: [] };
    const siblings = (childrenByParent.value.get(node.parent_id) ?? []).map((m) => m.id);
    const index = Math.max(1, siblings.indexOf(messageId) + 1);
    return { index, total: siblings.length, siblings };
  }

  async function loadConversations() {
    try {
      conversations.value = await conversationApi.list();
    } catch (err) {
      error.value = (err as Error).message;
    }
  }

  async function open(conversationId: string) {
    loading.value = true;
    error.value = null;
    try {
      detail.value = await conversationApi.get(conversationId);
      activeLeafId.value = detail.value.leaf_id ?? latestLeafOf(detail.value.messages);
    } catch (err) {
      error.value = (err as Error).message;
      detail.value = null;
    } finally {
      loading.value = false;
    }
  }

  async function create(title?: string): Promise<string | null> {
    try {
      const created = await conversationApi.create(title);
      conversations.value.unshift(created);
      detail.value = { ...created, messages: [] };
      activeLeafId.value = null;
      return created.id;
    } catch (err) {
      error.value = (err as Error).message;
      return null;
    }
  }

  /**
   * 发送用户消息并开始流式生成。
   *
   * @param content 用户输入
   * @param parentId 从任意节点继续（编辑重发/分支）；缺省 = 当前分支末端
   */
  async function send(content: string, parentId?: string) {
    const conversationId = detail.value?.id;
    if (!conversationId) return;
    error.value = null;
    try {
      const pair = await conversationApi.sendMessage(
        conversationId,
        content,
        parentId ?? activeLeafId.value ?? undefined
      );
      detail.value?.messages.push(pair.user_message, pair.assistant_message);
      if (detail.value) detail.value.message_count += 2;
      activeLeafId.value = pair.assistant_message.id;
      // 分支切换/新消息都回写服务端 leaf（下次打开会话停在这里）
      void conversationApi
        .update(conversationId, { leaf_id: pair.assistant_message.id })
        .catch(() => undefined);
      await runStream(pair.assistant_message.id);
    } catch (err) {
      error.value = (err as Error).message;
    }
  }

  async function runStream(assistantMessageId: string) {
    const conversationId = detail.value?.id;
    if (!conversationId) return;
    streamingMessageId.value = assistantMessageId;
    streamingText.value = "";
    controller = new AbortController();
    const message = detail.value?.messages.find((m) => m.id === assistantMessageId);
    if (message) message.status = "streaming";

    await streamChatMessage(
      conversationId,
      assistantMessageId,
      {
        onDelta: (text) => {
          streamingText.value += text;
        },
        onDone: () => undefined,
        onError: (data) => {
          error.value = data.message;
        },
      },
      controller.signal
    ).catch((err: unknown) => {
      // AbortError 是「用户点了停止」的正常路径，不报错
      const name = (err as Error)?.name;
      if (name !== "AbortError") {
        error.value = (err as Error).message || "流式生成中断";
      }
    });

    const finalText = streamingText.value;
    const target = detail.value?.messages.find((m) => m.id === assistantMessageId);
    if (target) {
      target.content = finalText || target.content;
      target.status = controller?.signal.aborted ? "interrupted" : target.status;
      if (!controller?.signal.aborted && target.status === "streaming") {
        target.status = "completed";
      }
    }
    streamingMessageId.value = null;
    streamingText.value = "";
    controller = null;
    void loadConversations();
  }

  /**
   * 停止生成：**双保险**。
   *
   * - abort 连接：UI 立刻停、不再收 delta；
   * - 调 stop 端点：服务端生成循环挂在 LLM await 上感知不到断开（生产实测），
   *   只有协同标志能让它在下一个 delta 内退出并把状态收敛为 interrupted。
   */
  async function stopStream() {
    const conversationId = detail.value?.id;
    const messageId = streamingMessageId.value;
    controller?.abort();
    if (conversationId && messageId) {
      try {
        await conversationApi.requestStop(conversationId, messageId);
      } catch {
        // 停止失败不阻塞 UI：退出流时仍会按 abort 标记 interrupted
      }
    }
  }

  /** 切换分支：显示某个兄弟节点所在的路径 */
  function switchBranch(messageId: string) {
    activeLeafId.value = messageId;
    const conversationId = detail.value?.id;
    if (conversationId) {
      void conversationApi
        .update(conversationId, { leaf_id: messageId })
        .catch(() => undefined);
    }
  }

  async function remove(conversationId: string) {
    await conversationApi.remove(conversationId);
    conversations.value = conversations.value.filter((item) => item.id !== conversationId);
    if (detail.value?.id === conversationId) {
      detail.value = null;
      activeLeafId.value = null;
    }
  }

  function latestLeafOf(messages: ChatMessage[]): string | null {
    const parents = new Set(messages.map((m) => m.parent_id).filter(Boolean) as string[]);
    const leaves = messages.filter((m) => !parents.has(m.id));
    return leaves.length ? (leaves[leaves.length - 1]?.id ?? null) : null;
  }

  return {
    conversations,
    detail,
    activeLeafId,
    activeConversation,
    activePath,
    loading,
    error,
    streamingMessageId,
    streamingText,
    loadConversations,
    open,
    create,
    send,
    stopStream,
    switchBranch,
    branchInfo,
    remove,
  };
});
