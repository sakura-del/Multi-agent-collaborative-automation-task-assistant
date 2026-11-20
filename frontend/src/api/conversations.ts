import api, { getAccessToken } from "./client";
import { resolveApiBase } from "@/config/endpoint";

// 会话内核（Issue #8 P0）：会话 CRUD + 消息树 + SSE 流。
//
// 停止生成的约定：**abort 掉 SSE 连接即为停止**——服务端把已生成内容
// 落库为 interrupted。所以客户端只需要 AbortController，不需要 stop 端点。

export type MessageRole = "user" | "assistant" | "system";
export type MessageStatus =
  | "pending"
  | "streaming"
  | "completed"
  | "interrupted"
  | "failed"
  | "waiting_approval";

export type ToolCallStatus = "pending_approval" | "ok" | "error" | "rejected";

export interface ChatToolCall {
  id: string;
  name: string;
  summary: string;
  status: ToolCallStatus;
  arguments?: Record<string, unknown>;
  result_preview?: string | null;
}

export interface ChatPendingTool {
  id: string;
  name: string;
  summary: string;
  arguments: Record<string, unknown>;
}

/** 消息附件（P2）：知识库文档引用；文件真源在知识库，消息只存引用 + 标题快照 */
export interface ChatAttachment {
  kind: "document";
  document_id: string;
  title: string;
}

export interface ConversationCapabilities {
  documents: boolean;
  images: boolean;
  image_reason: string | null;
  max_attachments: number;
  max_file_mb: number;
  voice: string;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  parent_id: string | null;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  model: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  error: string | null;
  created_at: string;
  attachments?: ChatAttachment[];
  tool_calls?: ChatToolCall[];
  pending_tool?: ChatPendingTool | null;
}

export interface ChatConversation {
  id: string;
  title: string;
  archived: boolean;
  message_count: number;
  leaf_id: string | null;
  last_message_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatConversationDetail extends ChatConversation {
  messages: ChatMessage[];
}

export interface MessagePair {
  user_message: ChatMessage;
  assistant_message: ChatMessage;
}

export const conversationApi = {
  create: (title?: string) =>
    api
      .post<ChatConversation>("/conversations", title ? { title } : {})
      .then((response) => response.data),

  list: (includeArchived = false) =>
    api
      .get<ChatConversation[]>("/conversations", { params: { include_archived: includeArchived } })
      .then((response) => response.data),

  get: (conversationId: string) =>
    api
      .get<ChatConversationDetail>(`/conversations/${conversationId}`)
      .then((response) => response.data),

  update: (conversationId: string, payload: { title?: string; archived?: boolean; leaf_id?: string }) =>
    api
      .patch<ChatConversation>(`/conversations/${conversationId}`, payload)
      .then((response) => response.data),

  remove: (conversationId: string) => api.delete(`/conversations/${conversationId}`),

  sendMessage: (
    conversationId: string,
    content: string,
    parentId?: string | null,
    attachments?: ChatAttachment[]
  ) =>
    api
      .post<MessagePair>(`/conversations/${conversationId}/messages`, {
        content,
        // undefined = 接当前分支末端；null = 明确挂回根（编辑首条消息）
        ...(parentId === undefined ? {} : { parent_id: parentId }),
        ...(attachments && attachments.length ? { attachments } : {}),
      })
      .then((response) => response.data),

  /** 附件/语音能力（图片由平台模型能力决定，前端不猜） */
  capabilities: () =>
    api
      .get<ConversationCapabilities>("/conversations/capabilities")
      .then((response) => response.data),

  /** 停止生成：协同取消（先 abort 连接即时反馈，再由服务端收敛状态） */
  requestStop: (conversationId: string, messageId: string) =>
    api.post(`/conversations/${conversationId}/messages/${messageId}/stop`).then((r) => r.data),

  /** 确认/取消待执行的写工具（P3） */
  confirmTool: (conversationId: string, messageId: string, approve: boolean) =>
    api
      .post<ChatMessage>(
        `/conversations/${conversationId}/messages/${messageId}/tool-confirm`,
        { approve }
      )
      .then((response) => response.data),

  /** SSE 地址（EventSource 不支持 Authorization 头 → 用 fetch + ReadableStream 自建） */
  streamUrl: (conversationId: string, messageId: string, tools = false, resume = false) => {
    const query = [
      tools ? "tools=1" : "",
      resume ? "resume=1" : "",
    ]
      .filter(Boolean)
      .join("&");
    return `/conversations/${conversationId}/messages/${messageId}/stream${query ? `?${query}` : ""}`;
  },
};

export interface StreamHandlers {
  onStart?: (data: {
    message_id: string;
    model: string;
    tools?: boolean;
    resumed?: boolean;
  }) => void;
  onDelta?: (text: string) => void;
  onToolCall?: (data: {
    id: string;
    name: string;
    summary: string;
    write: boolean;
  }) => void;
  onToolResult?: (data: { id: string; name: string; ok: boolean; preview: string }) => void;
  onToolApproval?: (data: {
    id: string;
    name: string;
    summary: string;
    arguments: Record<string, unknown>;
  }) => void;
  onDone?: (data: {
    message_id: string;
    chars?: number;
    status?: string;
    interrupted?: boolean;
  }) => void;
  onError?: (data: { code: string; message: string }) => void;
}

/**
 * 手写 SSE 消费（fetch + ReadableStream）。
 *
 * 为什么不用 EventSource：它不能带 Authorization 头（我们的 SSE 端点强制 JWT）。
 * 为什么手写解析：SSE 帧是 `event: X\ndata: {...}\n\n`，跨 chunk 会被切开——
 * 必须缓冲到空行再分帧，否则中文多字节字符会被切碎成乱码。
 */
export async function streamChatMessage(
  conversationId: string,
  messageId: string,
  handlers: StreamHandlers,
  signal: AbortSignal,
  tools = false,
  resume = false
): Promise<void> {
  // 原生 fetch（不走 axios）：只有它能拿到 ReadableStream 做增量解析。
  // axios 的 fetch adapter 在 responseType=text 时会把整个响应读完才 resolve，
  // 那样「流式」就名存实亡。
  const token = getAccessToken();
  const response = await fetch(`${resolveApiBase()}${conversationApi.streamUrl(conversationId, messageId, tools, resume)}`, {
    method: "GET",
    headers: {
      Accept: "text/event-stream",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    signal,
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    handlers.onError?.({
      code: String(response.status),
      message: detail.slice(0, 300) || `流式请求失败（HTTP ${response.status}）`,
    });
    return;
  }
  const body = response.body;
  if (!body) {
    // 无流能力的环境（极旧浏览器/测试 stub）：整体解析
    parseSseChunk(await response.text(), handlers);
    return;
  }

  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = parseSseChunk(buffer, handlers, true);
  }
  buffer += decoder.decode();
  if (buffer.trim()) parseSseChunk(buffer, handlers);
}

/**
 * 解析 SSE 缓冲：返回**未完成的尾部**（供下一次 append）。
 *
 * @param final true = 保留不完整帧等待下一片；false = 全量解析（收尾）
 */
export function parseSseChunk(buffer: string, handlers: StreamHandlers, keepTail = false): string {
  const frames = buffer.split("\n\n");
  const tail = keepTail ? (frames.pop() ?? "") : "";
  for (const frame of frames) {
    if (!frame.trim()) continue;
    const lines = frame.split("\n");
    const eventLine = lines.find((line) => line.startsWith("event: "));
    const dataLine = lines.find((line) => line.startsWith("data: "));
    if (!eventLine || !dataLine) continue;
    const event = eventLine.slice("event: ".length).trim();
    let payload: unknown = {};
    try {
      payload = JSON.parse(dataLine.slice("data: ".length));
    } catch {
      continue;
    }
    const data = payload as Record<string, unknown>;
    if (event === "start") handlers.onStart?.(data as never);
    else if (event === "delta") handlers.onDelta?.(String(data.text ?? ""));
    else if (event === "tool_call") handlers.onToolCall?.(data as never);
    else if (event === "tool_result") handlers.onToolResult?.(data as never);
    else if (event === "tool_approval") handlers.onToolApproval?.(data as never);
    else if (event === "done") handlers.onDone?.(data as never);
    else if (event === "error") handlers.onError?.(data as never);
  }
  return tail;
}
