import { defineStore } from "pinia";
import { computed, ref, watch } from "vue";

import {
  conversationApi,
  streamChatMessage,
  type ChatAttachment,
  type ChatPendingTool,
  type ChatConversation,
  type ChatConversationDetail,
  type ChatMessage,
  type ChatToolCall,
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
  // ===== 工具（P3）：默认开启；关闭后回到纯聊天（更快、不调工具）=====
  const TOOLS_KEY = "vp:chat:tools";
  const toolsEnabled = ref(localStorage.getItem(TOOLS_KEY) !== "0");
  watch(toolsEnabled, (value) => localStorage.setItem(TOOLS_KEY, value ? "1" : "0"));
  /** 本轮正在执行的工具轨迹（流式结束后合并进 message.tool_calls） */
  const streamingTools = ref<ChatToolCall[]>([]);

  const streamingMessageId = ref<string | null>(null);
  const streamingText = ref("");
  /** 发送阶段互斥锁：覆盖 sendMessage 网络往返窗口，防止双击/连按触发重复入库（Issue #56）。 */
  const sending = ref(false);
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
  /** 按 id 幂等写入消息，避免并发/幂等响应导致同一条消息在列表中重复出现。 */
  function upsertMessage(incoming: ChatMessage): boolean {
    if (!detail.value) return false;
    const index = detail.value.messages.findIndex((item) => item.id === incoming.id);
    if (index >= 0) {
      detail.value.messages[index] = incoming;
      return false;
    }
    detail.value.messages.push(incoming);
    return true;
  }

  async function send(
    content: string,
    parentId?: string | null,
    attachments?: ChatAttachment[],
  ) {
    const conversationId = detail.value?.id;
    if (!conversationId || sending.value || streamingMessageId.value) return;
    sending.value = true;
    error.value = null;
    // undefined = 接当前分支末端；null = 明确挂回根（编辑首条消息的分支）
    const effectiveParent =
      parentId === undefined ? (activeLeafId.value ?? undefined) : parentId;
    let assistantMessageId: string | null = null;
    try {
      const pair = await conversationApi.sendMessage(
        conversationId,
        content,
        effectiveParent,
        attachments
      );
      const addedUser = upsertMessage(pair.user_message);
      const addedAssistant = upsertMessage(pair.assistant_message);
      if (detail.value) {
        detail.value.message_count += Number(addedUser) + Number(addedAssistant);
      }
      activeLeafId.value = pair.assistant_message.id;
      assistantMessageId = pair.assistant_message.id;
      // 分支切换/新消息都回写服务端 leaf（下次打开会话停在这里）
      void conversationApi
        .update(conversationId, { leaf_id: pair.assistant_message.id })
        .catch(() => undefined);
    } catch (err) {
      error.value = (err as Error).message;
    } finally {
      sending.value = false;
    }
    if (assistantMessageId) {
      await runStream(assistantMessageId);
    }
  }

  /**
   * 流式生成一条 assistant 消息（发送与确认后续跑共用）。
   *
   * `resume=true`：写操作确认后的续跑。**不能**清空正文与工具轨迹——
   * 它们是模型继续生成的基础（服务端把它们回灌成上下文），也是执行过程
   * 时间线在界面上不闪断的原因。
   */
  async function runStream(
    assistantMessageId: string,
    options?: { resume?: boolean }
  ) {
    const conversationId = detail.value?.id;
    if (!conversationId) return;
    const resume = options?.resume === true;
    const message = detail.value?.messages.find((m) => m.id === assistantMessageId);
    streamingMessageId.value = assistantMessageId;
    streamingText.value = resume ? (message?.content ?? "") : "";
    streamingTools.value = resume ? (message?.tool_calls ?? []).slice() : [];
    controller = new AbortController();
    if (message) {
      message.status = "streaming";
      message.pending_tool = null;
      if (!resume) {
        message.tool_calls = [];
      }
    }

    await streamChatMessage(
      conversationId,
      assistantMessageId,
      {
        onDelta: (text) => {
          streamingText.value += text;
        },
        onToolCall: (data) => {
          streamingTools.value.push({
            id: data.id,
            name: data.name,
            summary: data.summary,
            status: data.write ? "pending_approval" : "ok",
            arguments: {},
            result_preview: null,
          });
        },
        onToolResult: (data) => {
          const record = streamingTools.value.find((item) => item.id === data.id);
          if (record) {
            record.status = data.ok ? "ok" : "error";
            record.result_preview = data.preview;
          }
        },
        onToolWait: (data) => {
          const record = streamingTools.value.find((item) => item.id === data.id);
          if (record) {
            record.result_preview = `等待任务状态变化…已等 ${data.waited_seconds}s`;
          }
        },
        onToolApproval: (data) => {
          const record = streamingTools.value.find((item) => item.id === data.id);
          if (record) {
            record.arguments = data.arguments;
            record.status = "pending_approval";
          }
          const target = detail.value?.messages.find((m) => m.id === assistantMessageId);
          if (target) {
            target.status = "waiting_approval";
            target.pending_tool = {
              id: data.id,
              name: data.name,
              summary: data.summary,
              arguments: data.arguments,
            };
          }
        },
        onDone: () => undefined,
        onError: (data) => {
          error.value = data.message;
        },
      },
      controller.signal,
      toolsEnabled.value,
      resume
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
      target.tool_calls = streamingTools.value.slice();
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
   * 确认/取消写工具（P3）。
   *
   * 服务端是唯一执行入口；前端只负责把用户的决定传过去，然后用返回的
   * 最新消息替换本地副本（含结果摘要与轨迹状态）。
   */
  async function confirmTool(messageId: string, approve: boolean) {
    const conversationId = detail.value?.id;
    if (!conversationId) return;
    const updated = await conversationApi.confirmTool(conversationId, messageId, approve);
    const index = detail.value?.messages.findIndex((m) => m.id === messageId) ?? -1;
    if (index >= 0 && detail.value) {
      detail.value.messages[index] = updated;
    }
    void loadConversations();
    // 确认执行后**自动续跑**同一轮生成：服务端已把工具结果写进轨迹，
    // resume 流会把结果回灌给模型继续——多步写链（建任务 → 确认规划 →
    // 重试节点）每步都这样衔接，用户只管逐张确认卡。
    // 拒绝不续跑：操作没有发生，模型无需再对结果发表意见。
    if (approve && (updated.tool_calls?.length ?? 0) > 0) {
      await runStream(messageId, { resume: true });
    }
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
  /**
   * 沿指定节点向下走到最深叶子（分支切换的正确落点）。
   *
   * 为什么不能直接把 activeLeafId 设成点中的消息：分支按钮挂在**用户消息**上
   * （同父兄弟是两条提问），而 `activePath` 是从叶节点回溯——若把叶设成用户
   * 消息，那条分支的助手回答会被整段隐藏（生产实测 2026-11-19）。
   * 统一向下取最后一个子节点，用户消息与助手消息两种入口都成立。
   */
  function deepestLeaf(nodeId: string): string {
    let cursor = nodeId;
    // 防御异常数据成环：上限远大于正常对话深度
    for (let guard = 0; guard < 500; guard += 1) {
      const children = childrenByParent.value.get(cursor) ?? [];
      if (!children.length) return cursor;
      cursor = children[children.length - 1]!.id;
    }
    return cursor;
  }

  function switchBranch(messageId: string) {
    const leaf = deepestLeaf(messageId);
    activeLeafId.value = leaf;
    const conversationId = detail.value?.id;
    if (conversationId) {
      void conversationApi
        .update(conversationId, { leaf_id: leaf })
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
    sending,
    error,
    streamingMessageId,
    streamingText,
    streamingTools,
    toolsEnabled,
    confirmTool,
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
