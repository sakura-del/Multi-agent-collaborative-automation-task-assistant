<script setup lang="ts">
defineOptions({ inheritAttrs: false });
/**
 * 会话外壳（Issue #8 P0）：三形态统一入口 —— 小窗 / 侧边 dock / 全屏页。
 *
 * 三种形态共用同一个 Pinia store（`useChatStore`）与同一套子组件，
 * 形态/开关记忆在 localStorage（`vp:chat:*`）——切形态不丢会话与草稿。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { AnimatePresence, Motion, useReducedMotion } from "motion-v";
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
  Wrench,
  X,
} from "lucide-vue-next";

import AnimatedCollapse from "@/components/common/AnimatedCollapse.vue";
import ConfirmBar from "@/components/common/ConfirmBar.vue";
import BePromptInput from "@/components/motion/BePromptInput.vue";
import ChatMessageItem from "@/components/chat/ChatMessageItem.vue";
import { DURATION, EASE_DRAWER, EASE_IN, EASE_OUT } from "@/lib/ease";
import { toastError, toastSuccess } from "@/lib/toast";
import { useChatStore } from "@/stores/chat";
import { knowledgeApi } from "@/api/knowledge";
import {
  conversationApi,
  type ChatAttachment,
  type ChatConversation,
  type ChatMessage,
  type ConversationCapabilities,
} from "@/api/conversations";

const props = withDefaults(
  defineProps<{
    /** 初始形态；`full` 用于 /chat 页 */
    variant?: "floating" | "dock" | "full";
  }>(),
  { variant: "floating" },
);

const OPEN_KEY = "vp:chat:open";
const VARIANT_KEY = "vp:chat:variant";
const BIBTEX_TEMPLATE = `@article{example2024,
  title = {请替换为论文标题},
  author = {Author, A.},
  journal = {Journal Name},
  year = {2024}
}`;

const SEED_PROMPTS = [
  { label: "综述 RAG 质量评估方法", action: "submit" as const },
  { label: "我的套餐还剩几篇？", action: "submit" as const },
  { label: "导入这篇 BibTeX", action: "prefill" as const },
];

const router = useRouter();
const store = useChatStore();
const reduced = useReducedMotion();

const open = ref(props.variant === "full" || localStorage.getItem(OPEN_KEY) === "1");
// ⚠️ 非 /chat 页绝不采纳持久化的 full（2026-11-19 线上布局事故）
const storedVariant = localStorage.getItem(VARIANT_KEY);
const variant = ref<"floating" | "dock" | "full">(
  props.variant === "full" ? "full" : storedVariant === "dock" ? "dock" : "floating",
);
const showList = ref(false);
const draft = ref("");
const promptInputRef = ref<{ focus: () => void } | null>(null);
const confirmDeleteTarget = ref<ChatConversation | null>(null);
const deletingConversation = ref(false);

// ===== 流式滚底与上滚打断 =====
const messagesEl = ref<HTMLElement | null>(null);
const autoFollow = ref(true);
const showScrollBottom = ref(false);
let scrollRafId: number | null = null;

function onMessagesScroll() {
  const el = messagesEl.value;
  if (!el) return;
  const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
  if (distanceToBottom > 48) {
    autoFollow.value = false;
    showScrollBottom.value = true;
  } else if (distanceToBottom <= 16) {
    autoFollow.value = true;
    showScrollBottom.value = false;
  }
}

function scheduleScrollBottom() {
  if (!autoFollow.value) return;
  const apply = () => {
    const el = messagesEl.value;
    if (!el || !autoFollow.value) return;
    el.scrollTop = el.scrollHeight;
  };
  if (typeof window === "undefined" || typeof requestAnimationFrame !== "function") {
    apply();
    return;
  }
  if (scrollRafId !== null) cancelAnimationFrame(scrollRafId);
  scrollRafId = requestAnimationFrame(() => {
    scrollRafId = null;
    apply();
  });
}

function scrollToBottom() {
  autoFollow.value = true;
  showScrollBottom.value = false;
  const el = messagesEl.value;
  if (!el) return;
  const behavior: ScrollBehavior = reduced.value ? "auto" : "smooth";
  if (typeof el.scrollTo === "function") {
    el.scrollTo({ top: el.scrollHeight, behavior });
  } else {
    el.scrollTop = el.scrollHeight;
  }
}

watch(
  [() => store.streamingText, () => store.activePath.length, () => store.streamingTools.length],
  () => {
    if (!autoFollow.value) return;
    scheduleScrollBottom();
  },
  { flush: "post" },
);

watch(showList, (visible) => {
  if (!visible) confirmDeleteTarget.value = null;
});

// ===== 附件（P2）：文档上传走知识库管线，消息只带引用 =====
const DEFAULT_MAX_ATTACHMENTS = 5;
const DEFAULT_MAX_FILE_MB = 30;
const DOC_EXTENSIONS = [".pdf", ".docx", ".md", ".markdown", ".txt"];
const IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".gif"];
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
  if (scrollRafId !== null && typeof cancelAnimationFrame === "function") {
    cancelAnimationFrame(scrollRafId);
  }
});

const streamingId = computed(() => store.streamingMessageId);
const isStreaming = computed(() => Boolean(streamingId.value));
/** 覆盖 ensureConversation() 建会话窗口，与 store.sending 组成全链路并发互斥锁（Issue #56）。 */
const submittingLock = ref(false);
const isBusy = computed(() => submittingLock.value || store.sending || isStreaming.value);

function isStreamingMessage(message: ChatMessage): boolean {
  return streamingId.value === message.id;
}

async function ensureConversation(): Promise<string | null> {
  if (store.detail) return store.detail.id;
  return store.create();
}

async function submit(value: string) {
  const text = value.trim();
  if (!text || isBusy.value) return;
  if (uploadingCount.value > 0) {
    attachmentError.value = "附件还在上传，请稍候再发送";
    return;
  }
  autoFollow.value = true;
  showScrollBottom.value = false;
  submittingLock.value = true;
  try {
    const conversationId = await ensureConversation();
    if (!conversationId) return;
    const sending = attachments.value.slice();
    draft.value = "";
    attachments.value = [];
    attachmentError.value = null;
    await store.send(text, undefined, sending);
  } finally {
    submittingLock.value = false;
  }
}

async function handleSeed(seed: { label: string; action: "submit" | "prefill" }) {
  if (seed.action === "prefill") {
    draft.value = BIBTEX_TEMPLATE;
    await nextTick();
    promptInputRef.value?.focus();
    return;
  }
  await submit(seed.label);
}

function editResend(messageId: string, content: string) {
  if (isBusy.value) return;
  const target = store.detail?.messages.find((m) => m.id === messageId);
  if (!target) return;
  autoFollow.value = true;
  showScrollBottom.value = false;
  void store.send(content, target.parent_id, target.attachments ?? []);
}

function retryAssistant(assistantId: string) {
  if (isBusy.value) return;
  const assistant = store.detail?.messages.find((m) => m.id === assistantId);
  const userMessage = store.detail?.messages.find((m) => m.id === assistant?.parent_id);
  if (!assistant || !userMessage) return;
  autoFollow.value = true;
  showScrollBottom.value = false;
  void store.send(
    userMessage.content,
    userMessage.parent_id,
    userMessage.attachments ?? [],
  );
}

async function openConversation(conversationId: string) {
  showList.value = false;
  confirmDeleteTarget.value = null;
  autoFollow.value = true;
  showScrollBottom.value = false;
  await store.open(conversationId);
}

async function newConversation() {
  showList.value = false;
  confirmDeleteTarget.value = null;
  autoFollow.value = true;
  showScrollBottom.value = false;
  await store.create();
}

async function confirmDeleteConversation() {
  if (!confirmDeleteTarget.value || deletingConversation.value) return;
  deletingConversation.value = true;
  try {
    await store.remove(confirmDeleteTarget.value.id);
    confirmDeleteTarget.value = null;
    toastSuccess("已删除会话");
  } catch (err) {
    toastError((err as Error).message || "删除会话失败");
  } finally {
    deletingConversation.value = false;
  }
}

async function goFullscreen() {
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
  // floating/dock 靠 `fixed` 自身就建立了定位上下文，绝不追加 relative。
  if (variant.value === "full") return "relative flex min-h-0 h-full w-full flex-1 flex-col";
  if (variant.value === "dock") {
    return "fixed right-0 top-0 z-40 flex h-svh w-full md:w-[440px] flex-col border-l border-border-strong bg-background shadow-2xl";
  }
  return "fixed bottom-4 right-4 z-40 flex h-[min(580px,calc(100svh-2rem))] max-h-[min(580px,calc(100svh-2rem))] w-[min(380px,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-2xl";
});

const panelInitial = computed(() => {
  if (variant.value === "full") return false;
  if (reduced.value) return { opacity: 0 };
  if (variant.value === "dock") return { opacity: 0, x: "100%" };
  return { opacity: 0, scale: 0.94, y: 12 };
});

const panelExit = computed(() => {
  if (variant.value === "full") return undefined;
  if (reduced.value) {
    return { opacity: 0, transition: { duration: DURATION.micro, ease: EASE_IN } };
  }
  if (variant.value === "dock") {
    return { opacity: 0, x: "100%", transition: { duration: 0.16, ease: EASE_IN } };
  }
  return { opacity: 0, scale: 0.96, y: 8, transition: { duration: 0.16, ease: EASE_IN } };
});

const panelTransition = computed(() => {
  if (reduced.value) return { duration: DURATION.micro, ease: EASE_OUT };
  if (variant.value === "dock") return { duration: DURATION.panel, ease: EASE_DRAWER };
  return { duration: 0.22, ease: EASE_OUT };
});
</script>

<template>
  <!--
    ⚠️ 全局浮层 Teleport 到 body：聊天入口不能被页面布局/层叠上下文困住。
    full 形态属于 /chat 页面内容，保持原位（disabled）。
  -->
  <Teleport to="body" :disabled="props.variant === 'full'">
    <!-- FAB（仅 floating 且未打开） -->
    <AnimatePresence :initial="false">
      <Motion
        v-if="props.variant !== 'full' && variant !== 'full' && !open"
        key="chat-fab"
        as="button"
        type="button"
        data-test="chat-fab"
        :initial="{ opacity: 0, scale: reduced ? 1 : 0.9 }"
        :animate="{ opacity: 1, scale: 1 }"
        :exit="{ opacity: 0, scale: reduced ? 1 : 0.9 }"
        :transition="{ duration: reduced ? DURATION.micro : DURATION.fast, ease: EASE_OUT }"
        class="fixed bottom-5 right-5 z-30 flex size-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition-transform hover:scale-105 active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none motion-reduce:hover:scale-100 motion-reduce:active:scale-100"
        aria-label="打开会话"
        title="打开会话"
        @click="open = true"
      >
        <MessageSquare class="size-5" />
        <span
          v-if="store.conversations.length && store.conversations[0]?.message_count"
          class="absolute -right-0.5 -top-0.5 size-2.5 rounded-full border-2 border-background bg-success"
        />
      </Motion>
    </AnimatePresence>

    <!-- 面板（严禁把 :key 设为 variant，否则 floating ↔ dock 会短暂双实例冲突） -->
    <AnimatePresence :initial="props.variant !== 'full'">
      <Motion
        v-if="open"
        key="chat-shell"
        as="section"
        :initial="panelInitial"
        :animate="{ opacity: 1, scale: 1, x: 0, y: 0 }"
        :exit="panelExit"
        :transition="panelTransition"
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
            <Loader2 class="size-3 animate-spin motion-reduce:animate-none" /> 生成中
          </span>
          <button
            v-if="variant !== 'full'"
            type="button"
            data-test="chat-variant-toggle"
            class="inline-flex size-7 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            :aria-label="variant === 'dock' ? '切回浮窗' : '展开为右侧面板'"
            :title="variant === 'dock' ? '切回浮窗' : '右侧面板模式'"
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
        <AnimatedCollapse :open="showList">
          <div data-test="chat-conversation-list" class="border-b border-border bg-muted/30 p-2">
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
              <li v-for="item in store.conversations" :key="item.id" class="group flex items-center gap-1">
                <button
                  type="button"
                  class="min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-muted"
                  :class="store.detail?.id === item.id ? 'bg-muted font-medium' : ''"
                  :title="item.title"
                  @click="openConversation(item.id)"
                >
                  {{ item.title }}
                  <span class="ml-1 font-mono text-[10px] tabular-nums text-muted-foreground">{{
                    item.message_count
                  }}</span>
                </button>
                <button
                  type="button"
                  class="shrink-0 rounded p-1 text-muted-foreground opacity-75 transition-opacity hover:text-destructive focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
                  aria-label="删除会话"
                  @click="confirmDeleteTarget = item"
                >
                  <Trash2 class="size-3" />
                </button>
              </li>
              <li v-if="!store.conversations.length" class="px-2 py-2 text-xs text-muted-foreground">
                还没有会话，说一句开始吧。
              </li>
            </ul>
            <ConfirmBar
              v-if="confirmDeleteTarget"
              class="mt-2"
              :title="`确认删除会话「${confirmDeleteTarget.title}」？`"
              consequence="删除后该会话与全部分支记录将永久移除，无法恢复。"
              confirm-label="确认删除"
              cancel-label="取消"
              confirm-testid="chat-delete-confirm"
              cancel-testid="chat-delete-cancel"
              :loading="deletingConversation"
              @confirm="confirmDeleteConversation"
              @cancel="confirmDeleteTarget = null"
            />
          </div>
        </AnimatedCollapse>

        <!-- 消息区（含上滚打断与回到最新胶囊） -->
        <div class="relative flex min-h-0 flex-1 flex-col">
          <div
            ref="messagesEl"
            data-test="chat-messages"
            class="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-3 py-3"
            @scroll.passive="onMessagesScroll"
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
                  v-for="seed in SEED_PROMPTS"
                  :key="seed.label"
                  type="button"
                  :disabled="isBusy"
                  class="rounded-full border border-border px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:border-primary hover:text-primary disabled:opacity-50"
                  @click="handleSeed(seed)"
                >
                  {{ seed.label }}
                </button>
              </div>
            </div>

            <ChatMessageItem
              v-for="message in store.activePath"
              :key="message.id"
              :message="message"
              :branch="store.branchInfo(message.id)"
              :streaming-text="isStreamingMessage(message) ? store.streamingText : null"
              :streaming-tools="isStreamingMessage(message) ? store.streamingTools : null"
              :editable="message.role === 'user' && !isStreaming"
              @switch-branch="store.switchBranch"
              @edit-resend="editResend"
              @retry="retryAssistant"
              @confirm-tool="store.confirmTool"
            />
          </div>

          <AnimatePresence :initial="false">
            <div
              v-if="showScrollBottom"
              class="pointer-events-none absolute inset-x-0 bottom-2 z-10 flex justify-center"
            >
              <Motion
                as="button"
                type="button"
                data-test="chat-scroll-bottom"
                :initial="{ opacity: 0, y: reduced ? 0 : 6 }"
                :animate="{ opacity: 1, y: 0 }"
                :exit="{
                  opacity: 0,
                  y: reduced ? 0 : 6,
                  transition: { duration: reduced ? DURATION.micro : 0.12, ease: EASE_IN },
                }"
                :transition="{ duration: reduced ? DURATION.micro : 0.16, ease: EASE_OUT }"
                class="pointer-events-auto rounded-full border border-border-strong bg-popover px-3 py-1 text-[11px] font-medium text-popover-foreground shadow-raised transition-colors hover:bg-muted"
                @click="scrollToBottom"
              >
                回到最新 ↓
              </Motion>
            </div>
          </AnimatePresence>
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
            <div v-if="attachments.length || uploadingCount" class="flex flex-wrap gap-1">
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
                <Loader2 class="size-3 animate-spin motion-reduce:animate-none" /> 上传中 {{ uploadingCount }}
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
              type="button"
              data-test="chat-tools-toggle"
              class="inline-flex items-center gap-1 rounded-lg border px-1.5 py-1 text-[10px] transition-colors"
              :class="
                store.toolsEnabled
                  ? 'border-primary/50 bg-primary/10 text-primary'
                  : 'border-border text-muted-foreground hover:text-foreground'
              "
              :aria-pressed="store.toolsEnabled"
              :title="
                store.toolsEnabled
                  ? '工具已开启：可查询/建任务，写操作会先请你确认'
                  : '工具已关闭：纯聊天（更快）'
              "
              @click="store.toolsEnabled = !store.toolsEnabled"
            >
              <Wrench class="size-3" /> 工具{{ store.toolsEnabled ? "开" : "关" }}
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
            <span v-if="voiceSupported" class="text-[10px] text-muted-foreground">
              语音仅本机识别
            </span>
          </div>

          <BePromptInput
            ref="promptInputRef"
            v-model="draft"
            data-test="chat-input"
            placeholder="说一句话…"
            hint="Enter 发送 · Shift+Enter 换行"
            min-height="4rem"
            :submit-on-enter="true"
            :submitting="isBusy"
            @submit="submit"
          />
          <div class="mt-1.5 flex items-center justify-between px-1">
            <span class="font-mono text-[10px] tabular-nums text-muted-foreground">
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
      </Motion>
    </AnimatePresence>
  </Teleport>
</template>
