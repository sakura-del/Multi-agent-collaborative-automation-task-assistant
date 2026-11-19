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
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  Loader2,
  Maximize2,
  MessageSquare,
  Minimize2,
  PanelRight,
  Plus,
  Square,
  Trash2,
  X,
} from "lucide-vue-next";

import BePromptInput from "@/components/motion/BePromptInput.vue";
import ChatMessageItem from "@/components/chat/ChatMessageItem.vue";
import { useChatStore } from "@/stores/chat";
import type { ChatMessage } from "@/api/conversations";

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
const variant = ref<"floating" | "dock" | "full">(
  props.variant === "full"
    ? "full"
    : ((localStorage.getItem(VARIANT_KEY) as "floating" | "dock") ?? props.variant)
);
const showList = ref(false);
const draft = ref("");

watch(variant, (value) => localStorage.setItem(VARIANT_KEY, value));
watch(open, (value) => localStorage.setItem(OPEN_KEY, value ? "1" : "0"));

onMounted(async () => {
  await store.loadConversations();
  if (open.value) {
    const target = store.conversations[0]?.id;
    if (target && !store.detail) void store.open(target);
  }
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
  const conversationId = await ensureConversation();
  if (!conversationId) return;
  draft.value = "";
  await store.send(text);
}

function editResend(messageId: string, content: string) {
  const target = store.detail?.messages.find((m) => m.id === messageId);
  if (!target) return;
  void store.send(content, target.parent_id);
}

function retryAssistant(assistantId: string) {
  // 重试 = 同一条 user 消息再生成一个 assistant 分支：
  // 用 user 的内容在它的父节点下重新发送（产生新分支，旧分支保留可切回）
  const assistant = store.detail?.messages.find((m) => m.id === assistantId);
  const userMessage = store.detail?.messages.find((m) => m.id === assistant?.parent_id);
  if (!assistant || !userMessage) return;
  void store.send(userMessage.content, userMessage.parent_id);
}

async function openConversation(conversationId: string) {
  showList.value = false;
  await store.open(conversationId);
}

async function newConversation() {
  showList.value = false;
  await store.create();
}

function goFullscreen() {
  variant.value = "full";
  void router.push("/chat");
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
  if (variant.value === "full") return "flex h-full w-full flex-col";
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
  <section v-if="open" :class="panelClass" data-test="chat-shell">
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
