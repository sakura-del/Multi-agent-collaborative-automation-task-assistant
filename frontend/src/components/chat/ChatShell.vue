<script setup lang="ts">
/**
 * 会话外壳（Issue #8 P0）：三形态统一入口 —— 小窗 / 侧边 dock / 全屏页。
 *
 * ## 形态与状态
 *
 * - `floating`：右下角 FAB → 380×580 悬浮面板（默认，任何页面可用）；
 * - `dock`：贴右侧全高的 440px 列（一边看任务一边指挥）；
 * - `full`：占满父容器（`/chat` 全屏页）。
 *
 * 三种形态共用同一个 Pinia store（`useChatStore`）与同一套子组件，
 * 形态/开关记忆在 localStorage（`vp:chat:*`）——切形态不丢会话与草稿。
 *
 * ## 三形态切换
 *
 * 头部按钮：小窗 ⇄ dock ⇄ 全屏（全屏通过路由 `/chat` 实现，
 * 从小窗点「全屏」会导航过去并记住形态）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  FileText,
  Loader2,
  Maximize2,
  Mic,
  MicOff,
  MessageSquare,
  Minimize2,
  PanelRight,
  Paperclip,
  Plus,
  Square,
  Trash2,
  X,
} from "lucide-vue-next";

import BePromptInput from "@/components/motion/BePromptInput.vue";
import ChatMessageItem from "@/components/chat/ChatMessageItem.vue";
import { useChatStore } from "@/stores/chat";
import { knowledgeApi } from "@/api/knowledge";
import { conversationApi } from "@/api/conversations";
import type {
  ChatAttachment,
  ChatMessage,
  ConversationCapabilities,
} from "@/api/conversations";

const props = withDefaults(
  defineProps<{
    /** 初始形态；`full` 用于 /chat 页 */
    variant?: "floating" | "dock" | "full";
  }>(),
  { variant: "floating" }
);

const OPEN_KEY = "vp:chat:open";
const VARIANT_KEY = "vp:chat:variant";

const router = useRouter();
const store = useChatStore();

const open = ref(props.variant === "full" || localStorage.getItem(OPEN_KEY) === "1");
// ⚠️ 非 /chat 页**绝不采纳持久化的 full**（2026-11-19 线上布局事故）：
// 全屏形态会 `flex h-full w-full` 作为布局 flex 子项参与排版，把主内容
// 挤成一条竖线；而 full 只应由 `/chat` 路由通过 props 提供。
const storedVariant = localStorage.getItem(VARIANT_KEY);
const variant = ref<"floating" | "dock" | "full">(
  props.variant === "full" ? "full" : storedVariant === "dock" ? "dock" : "floating"
);
const showList = ref(false);
const draft = ref("");

// ===== 附件（P2）：文档上传走知识库管线，消息只带引用 =====
const DEFAULT_MAX_ATTACHMENTS = 5;
const DEFAULT_MAX_FILE_MB = 30;
const DOC_EXTENSIONS = [".pdf", ".docx", ".md", ".markdown", ".txt"];
const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".gif"];
//: 后端能力（图片是否可用由平台模型决定；未取到时不误报，按文档处理）
const capabilities = ref<ConversationCapabilities | null>(null);
const attachments = ref<ChatAttachment[]>([]);
const uploadingCount = ref(0);
const attachmentError = ref<string | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const dragActive = ref(false);

function isSupportedDocument(name: string): boolean {
  const lower = name.toLowerCase();
  return DOC_EXTENSIONS.some((extension) => lower.endsWith(extension));
}

async function addFiles(files: FileList | File[]): Promise<void> {
  attachmentError.value = null;
  for (const file of Array.from(files)) {
    const maxAttachments = capabilities.value?.max_attachments ?? DEFAULT_MAX_ATTACHMENTS;
    const maxFileMb = capabilities.value?.max_file_mb ?? DEFAULT_MAX_FILE_MB;
    if (attachments.value.length + uploadingCount.value >= maxAttachments) {
      attachmentError.value = `最多 ${maxAttachments} 个附件`;
      break;
    }
    const lowerName = file.name.toLowerCase();
    if (IMAGE_EXTENSIONS.some((extension) => lowerName.endsWith(extension))) {
      // 图片：平台模型是否支持视觉由后端能力决定，不在这里假装能理解
      attachmentError.value =
        capabilities.value?.image_reason ??
        "当前模型不支持图片输入；请改用文档附件（PDF/DOCX/MD/TXT）";
      continue;
    }
    if (!isSupportedDocument(file.name)) {
      attachmentError.value = `不支持的文件类型：${file.name}（支持 PDF/DOCX/MD/TXT）`;
      continue;
    }
    if (file.size > maxFileMb * 1024 * 1024) {
      attachmentError.value = `文件超过 ${maxFileMb}MB：${file.name}`;
      continue;
    }
    uploadingCount.value += 1;
    try {
      // 上传即入库（解析/分块/索引由知识库服务完成），失败会带可读原因
      const doc = await knowledgeApi.upload(
        file,
        file.name.replace(/\.[^.]+$/, "").slice(0, 200) || file.name,
      );
      attachments.value.push({
        kind: "document",
        document_id: doc.id,
        title: doc.title,
      });
    } catch (err) {
      attachmentError.value = (err as Error).message || `上传失败：${file.name}`;
    } finally {
      uploadingCount.value -= 1;
    }
  }
  if (fileInput.value) fileInput.value.value = "";
}

function onFilePicked(event: Event): void {
  const input = event.target as HTMLInputElement;
  if (input.files?.length) void addFiles(input.files);
}

function onDrop(event: DragEvent): void {
  dragActive.value = false;
  if (event.dataTransfer?.files?.length) void addFiles(event.dataTransfer.files);
}

function removeAttachment(index: number): void {
  attachments.value.splice(index, 1);
}

// ===== 语音输入（P2）：浏览器 Web Speech API，不上传音频 =====
interface RecognitionResultLike {
  readonly isFinal: boolean;
  readonly 0: { transcript: string };
}
interface RecognitionEventLike {
  readonly resultIndex: number;
  readonly results: ArrayLike<RecognitionResultLike>;
}
interface RecognitionLike {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  start(): void;
  stop(): void;
  onresult: ((event: RecognitionEventLike) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
}
type RecognitionCtor = new () => RecognitionLike;

const voiceSupported = ref(false);
const listening = ref(false);
let recognition: RecognitionLike | null = null;

function getRecognitionCtor(): RecognitionCtor | null {
  const scope = window as unknown as {
    SpeechRecognition?: RecognitionCtor;
    webkitSpeechRecognition?: RecognitionCtor;
  };
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null;
}

function toggleVoice(): void {
  if (listening.value) {
    recognition?.stop();
    return;
  }
  const Ctor = getRecognitionCtor();
  if (!Ctor) return;
  attachmentError.value = null;
  const instance = new Ctor();
  instance.lang = "zh-CN";
  instance.interimResults = false;
  instance.continuous = false;
  instance.onresult = (event) => {
    let transcript = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const result = event.results[index];
      if (result?.isFinal) transcript += result[0]?.transcript ?? "";
    }
    if (transcript) draft.value = `${draft.value}${transcript}`.trim();
  };
  instance.onerror = (event) => {
    attachmentError.value =
      event.error === "not-allowed" ? "麦克风权限被拒绝" : "语音识别失败，请重试";
    listening.value = false;
  };
  instance.onend = () => {
    listening.value = false;
    recognition = null;
  };
  recognition = instance;
  listening.value = true;
  try {
    instance.start();
  } catch {
    listening.value = false;
    recognition = null;
    attachmentError.value = "语音识别启动失败";
  }
}

watch(variant, (value) => {
  // full 不落盘：它属于 `/chat` 路由，普通页从 localStorage 读到会挤坏布局
  if (value !== "full") localStorage.setItem(VARIANT_KEY, value);
});
watch(open, (value) => localStorage.setItem(OPEN_KEY, value ? "1" : "0"));

onMounted(async () => {
  voiceSupported.value = getRecognitionCtor() !== null;
  void conversationApi
    .capabilities()
    .then((value) => (capabilities.value = value))
    .catch(() => undefined);
  await store.loadConversations();
  if (open.value) {
    const target = store.conversations[0]?.id;
    if (target && !store.detail) void store.open(target);
  }
});

onBeforeUnmount(() => {
  recognition?.stop();
});

const streamingId = computed(() => store.streamingMessageId);
const isStreaming = computed(() => Boolean(streamingId.value));

function isStreamingMessage(message: ChatMessage): boolean {
  return streamingId.value === message.id;
}

async function ensureConversation(): Promise<string | null> {
  if (store.detail) return store.detail.id;
  return store.create();
}

async function submit(value: string) {
  const text = value.trim();
  if (!text || isStreaming.value) return;
  if (uploadingCount.value > 0) {
    attachmentError.value = "附件还在上传，请稍候再发送";
    return;
  }
  const conversationId = await ensureConversation();
  if (!conversationId) return;
  const sending = attachments.value.slice();
  draft.value = "";
  attachments.value = [];
  attachmentError.value = null;
  await store.send(text, undefined, sending);
}

function editResend(messageId: string, content: string) {
  const target = store.detail?.messages.find((m) => m.id === messageId);
  if (!target) return;
  void store.send(content, target.parent_id, target.attachments ?? []);
}

function retryAssistant(assistantId: string) {
  // 重试 = 同一条 user 消息再生成一个 assistant 分支：
  // 用 user 的内容在它的父节点下重新发送（产生新分支，旧分支保留可切回）
  const assistant = store.detail?.messages.find((m) => m.id === assistantId);
  const userMessage = store.detail?.messages.find((m) => m.id === assistant?.parent_id);
  if (!assistant || !userMessage) return;
  void store.send(
    userMessage.content,
    userMessage.parent_id,
    userMessage.attachments ?? [],
  );
}

async function openConversation(conversationId: string) {
  showList.value = false;
  await store.open(conversationId);
}

async function newConversation() {
  showList.value = false;
  await store.create();
}

async function goFullscreen() {
  // 不在这里预设 full：/chat 的新壳由 props.variant="full" 提供。
  // 先设 full 再导航会让普通页闪一帧「w-full 挤占布局」。
  await router.push("/chat");
}

function toDock() {
  if (props.variant === "full") {
    variant.value = "dock";
    void router.push("/tasks");
    open.value = true;
    return;
  }
  variant.value = variant.value === "dock" ? "floating" : "dock";
}

const panelClass = computed(() => {
  // ⚠️ 只有 full 需要 `relative` 作为拖放遮罩的定位基准；
  // floating/dock 靠 `fixed` 自身就建立了定位上下文。
  // 曾经无条件追加 `relative`：Tailwind 的 .relative 声明在 .fixed 之后，
  // 直接把浮层变成布局内 flex 子项，把主内容挤成竖排（2026-11-19 线上事故）。
  if (variant.value === "full") return "relative flex h-full w-full flex-col";
  if (variant.value === "dock")
    return "fixed right-0 top-0 z-40 flex h-svh w-[440px] flex-col border-l border-border bg-background shadow-xl";
  return "fixed bottom-4 right-4 z-40 flex h-[580px] w-[380px] flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-2xl";
});
</script>

<template>
  <!-- FAB（仅 floating 且未打开） -->
  <button
    v-if="props.variant !== 'full' && variant !== 'full' && !open"
    type="button"
    data-test="chat-fab"
    class="fixed bottom-5 right-5 z-40 flex size-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition-transform hover:scale-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    aria-label="打开会话"
    title="打开会话"
    @click="open = true"
  >
    <MessageSquare class="size-5" />
    <span
      v-if="store.conversations.length && store.conversations[0]?.message_count"
      class="absolute -right-0.5 -top-0.5 size-2.5 rounded-full border-2 border-background bg-emerald-500"
    />
  </button>

  <!-- 面板 -->
  <section
    v-if="open"
    :class="panelClass"
    data-test="chat-shell"
    @dragover.prevent="dragActive = true"
    @dragleave.prevent="dragActive = false"
    @drop.prevent="onDrop"
  >
    <!-- 拖放遮罩（P2）：松手即上传到知识库 -->
    <div
      v-if="dragActive"
      data-test="chat-dropzone"
      class="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-2xl border-2 border-dashed border-primary bg-background/90 text-sm font-medium text-primary"
    >
      松开上传到知识库
    </div>
    <!-- 头部 -->
    <header
      class="flex items-center gap-1.5 border-b border-border px-3 py-2"
      :class="variant === 'full' ? 'shrink-0' : ''"
    >
      <button
        type="button"
        data-test="chat-toggle-list"
        class="inline-flex size-7 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        aria-label="会话列表"
        @click="showList = !showList"
      >
        <MessageSquare class="size-4" />
      </button>
      <h2 class="min-w-0 flex-1 truncate text-sm font-medium">
        {{ store.detail?.title ?? "新会话" }}
      </h2>
      <span
        v-if="isStreaming"
        class="inline-flex items-center gap-1 text-[10px] text-muted-foreground"
      >
        <Loader2 class="size-3 animate-spin" /> 生成中
      </span>
      <button
        v-if="variant !== 'full'"
        type="button"
        data-test="chat-variant-toggle"
        class="inline-flex size-7 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        :aria-label="variant === 'dock' ? '切回小窗' : '切换到侧边栏'"
        :title="variant === 'dock' ? '切回小窗' : '停靠到右侧'"
        @click="toDock"
      >
        <Minimize2 v-if="variant === 'dock'" class="size-3.5" />
        <PanelRight v-else class="size-3.5" />
      </button>
      <button
        v-if="variant !== 'full'"
        type="button"
        data-test="chat-fullscreen"
        class="inline-flex size-7 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        aria-label="全屏打开"
        title="全屏打开"
        @click="goFullscreen"
      >
        <Maximize2 class="size-3.5" />
      </button>
      <button
        v-if="variant !== 'full'"
        type="button"
        data-test="chat-close"
        class="inline-flex size-7 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        aria-label="收起会话"
        title="收起"
        @click="open = false"
      >
        <X class="size-3.5" />
      </button>
    </header>

    <!-- 会话列表覆盖层 -->
    <div v-if="showList" data-test="chat-conversation-list" class="border-b border-border bg-muted/30 p-2">
      <div class="mb-1.5 flex items-center gap-1.5">
        <button
          type="button"
          data-test="chat-new-conversation"
          class="inline-flex flex-1 items-center justify-center gap-1 rounded-lg border border-border bg-background px-2 py-1.5 text-xs transition-colors hover:bg-muted"
          @click="newConversation"
        >
          <Plus class="size-3.5" /> 新会话
        </button>
      </div>
      <ul class="max-h-48 overflow-y-auto">
        <li v-for="item in store.conversations" :key="item.id" class="group flex items-center">
          <button
            type="button"
            class="min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-muted"
            :class="store.detail?.id === item.id ? 'bg-muted font-medium' : ''"
            :title="item.title"
            @click="openConversation(item.id)"
          >
            {{ item.title }}
            <span class="ml-1 font-mono text-[10px] text-muted-foreground"
              >{{ item.message_count }}</span
            >
          </button>
          <button
            type="button"
            class="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
            aria-label="删除会话"
            @click="store.remove(item.id)"
          >
            <Trash2 class="size-3" />
          </button>
        </li>
        <li v-if="!store.conversations.length" class="px-2 py-2 text-xs text-muted-foreground">
          还没有会话，说一句开始吧。
        </li>
      </ul>
    </div>

    <!-- 消息区 -->
    <div
      data-test="chat-messages"
      class="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-3 py-3"
    >
      <div
        v-if="!store.activePath.length"
        class="flex flex-1 flex-col items-center justify-center gap-2 text-center"
      >
        <MessageSquare class="size-8 text-muted-foreground/50" />
        <p class="text-sm font-medium">说一句话开始</p>
        <p class="max-w-[26rem] text-xs leading-5 text-muted-foreground">
          可以直接让我写一篇文献综述（会自动检索、规划并执行），也可以问检索方法、查配额、管理引用库。
        </p>
        <div class="mt-1 flex flex-wrap justify-center gap-1.5">
          <button
            v-for="seed in ['综述 RAG 质量评估方法', '我的套餐还剩几篇？', '导入这篇 BibTeX']"
            :key="seed"
            type="button"
            class="rounded-full border border-border px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:border-primary hover:text-primary"
            @click="submit(seed)"
          >
            {{ seed }}
          </button>
        </div>
      </div>

      <ChatMessageItem
        v-for="message in store.activePath"
        :key="message.id"
        :message="message"
        :branch="store.branchInfo(message.id)"
        :streaming-text="isStreamingMessage(message) ? store.streamingText : null"
        :editable="message.role === 'user' && !isStreaming"
        @switch-branch="store.switchBranch"
        @edit-resend="editResend"
        @retry="retryAssistant"
      />
    </div>

    <!-- 错误 -->
    <p
      v-if="store.error"
      data-test="chat-error"
      class="mx-3 mb-1 truncate rounded-md bg-destructive/5 px-2 py-1 text-[11px] text-destructive"
      :title="store.error"
    >
      {{ store.error }}
    </p>

    <!-- 输入区 -->
    <div class="border-t border-border p-2">
      <!-- 已选附件 / 上传状态 / 错误（P2） -->
      <div v-if="attachments.length || uploadingCount || attachmentError" class="mb-1.5 px-1">
        <div v-if="attachments.length" class="flex flex-wrap gap-1">
          <span
            v-for="(attachment, index) in attachments"
            :key="attachment.document_id"
            data-test="chat-attachment-chip"
            class="inline-flex max-w-full items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
            :title="attachment.title"
          >
            <FileText class="size-3 shrink-0" />
            <span class="truncate">{{ attachment.title }}</span>
            <button
              type="button"
              class="ml-0.5 text-muted-foreground hover:text-destructive"
              :aria-label="`移除附件 ${attachment.title}`"
              @click="removeAttachment(index)"
            >
              <X class="size-3" />
            </button>
          </span>
          <span v-if="uploadingCount" class="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
            <Loader2 class="size-3 animate-spin" /> 上传中 {{ uploadingCount }}
          </span>
        </div>
        <p
          v-if="attachmentError"
          data-test="chat-attachment-error"
          class="mt-1 truncate text-[10px] text-destructive"
          :title="attachmentError"
        >
          {{ attachmentError }}
        </p>
      </div>

      <div class="mb-1 flex items-center gap-1 px-1">
        <input
          ref="fileInput"
          data-test="chat-file-input"
          type="file"
          class="hidden"
          multiple
          accept=".pdf,.docx,.md,.markdown,.txt"
          @change="onFilePicked"
        />
        <button
          type="button"
          data-test="chat-attach"
          class="inline-flex items-center gap-1 rounded-lg border border-border px-1.5 py-1 text-[10px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40"
          :disabled="isStreaming || uploadingCount > 0"
          title="上传文档到知识库（也可直接拖进窗口）"
          @click="fileInput?.click()"
        >
          <Paperclip class="size-3" /> 附件
        </button>
        <button
          v-if="voiceSupported"
          type="button"
          data-test="chat-voice"
          class="inline-flex items-center gap-1 rounded-lg border px-1.5 py-1 text-[10px] transition-colors disabled:opacity-40"
          :class="
            listening
              ? 'border-destructive text-destructive'
              : 'border-border text-muted-foreground hover:text-foreground'
          "
          :disabled="isStreaming"
          :aria-pressed="listening"
          :title="listening ? '停止语音输入' : '语音输入（浏览器识别，不上传音频）'"
          @click="toggleVoice"
        >
          <MicOff v-if="listening" class="size-3" />
          <Mic v-else class="size-3" />
          {{ listening ? "停止" : "语音" }}
        </button>
        <span class="text-[10px] text-muted-foreground">
          {{ voiceSupported ? "语音仅本机识别" : "当前浏览器不支持语音" }}
        </span>
      </div>

      <BePromptInput
        v-model="draft"
        data-test="chat-input"
        placeholder="说一句话…"
        hint="Enter 发送 · Shift+Enter 换行"
        min-height="4rem"
        :submit-on-enter="true"
        :submitting="isStreaming"
        @submit="submit"
      />
      <div class="mt-1.5 flex items-center justify-between px-1">
        <span class="font-mono text-[10px] text-muted-foreground">
          {{ store.detail?.message_count ?? 0 }} 条 · 平台模型
        </span>
        <button
          v-if="isStreaming"
          type="button"
          data-test="chat-stop"
          class="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
          @click="store.stopStream"
        >
          <Square class="size-3" /> 停止
        </button>
      </div>
    </div>
  </section>
</template>
