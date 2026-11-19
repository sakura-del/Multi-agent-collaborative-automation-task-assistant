<script setup lang="ts">
/**
 * 单条会话消息（Issue #8 P0）：Markdown + KaTeX + Mermaid + 分支切换 + 编辑重发。
 *
 * 渲染走 `chat-markdown.ts` 的两段式安全链（marked → DOMPurify），
 * Mermaid 在挂载后用动态 import 渲染（体积大，只有出现图表才加载）。
 */
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { Check, ChevronLeft, ChevronRight, Copy, FileText, Pencil, RotateCcw } from "lucide-vue-next";

import type { ChatMessage } from "@/api/conversations";
import { renderChatMarkdown, renderMermaidBlocks } from "@/utils/chat-markdown";

const props = defineProps<{
  message: ChatMessage;
  /** 分支信息：同一父节点下第几条 / 共几条 */
  branch?: { index: number; total: number; siblings: string[] };
  /** 是否为当前分支正在查看的消息 */
  active?: boolean;
  /** 流式中的实时文本（仅 assistant 且正在生成时传入） */
  streamingText?: string | null;
  /** 是否可以编辑重发（user 消息且非流式中） */
  editable?: boolean;
}>();

const emit = defineEmits<{
  switchBranch: [messageId: string];
  editResend: [messageId: string, content: string];
  retry: [messageId: string];
}>();

const root = ref<HTMLElement | null>(null);
const copied = ref(false);
const editing = ref(false);
const draft = ref("");

const isUser = computed(() => props.message.role === "user");
const isStreaming = computed(() => props.streamingText !== null && props.streamingText !== undefined);
const displayContent = computed(() =>
  isStreaming.value ? (props.streamingText ?? "") : props.message.content
);
const html = computed(() => renderChatMarkdown(displayContent.value));

async function hydrateMermaid() {
  await nextTick();
  if (root.value) {
    await renderMermaidBlocks(root.value);
  }
}

onMounted(() => {
  void hydrateMermaid();
});

watch(displayContent, () => {
  void hydrateMermaid();
});

async function copyContent() {
  try {
    await navigator.clipboard.writeText(displayContent.value);
    copied.value = true;
    window.setTimeout(() => (copied.value = false), 1500);
  } catch {
    // 剪贴板不可用（非 https / 无权限）：静默，不做假成功提示
  }
}

function startEdit() {
  draft.value = props.message.content;
  editing.value = true;
}

function submitEdit() {
  const text = draft.value.trim();
  if (!text) return;
  editing.value = false;
  emit("editResend", props.message.id, text);
}
</script>

<template>
  <article
    ref="root"
    :data-test="`chat-message-${message.role}`"
    class="group flex flex-col gap-1.5"
    :class="isUser ? 'items-end' : 'items-start'"
  >
    <!-- 元信息行 -->
    <div class="flex items-center gap-1.5 px-1 text-[10px] text-muted-foreground">
      <span class="font-medium">{{ isUser ? "我" : (message.model ?? "助手") }}</span>
      <span
        v-if="message.status === 'interrupted'"
        class="rounded bg-amber-500/10 px-1 text-amber-600"
        >已中断</span
      >
      <span
        v-else-if="message.status === 'failed'"
        class="rounded bg-destructive/10 px-1 text-destructive"
        >失败</span
      >
    </div>

    <!-- 气泡 -->
    <div
      class="max-w-[92%] rounded-2xl px-3.5 py-2.5 text-sm leading-6"
      :class="
        isUser
          ? 'bg-primary text-primary-foreground'
          : 'border border-border bg-background text-foreground'
      "
    >
      <!-- 编辑态（user 消息） -->
      <template v-if="editing">
        <textarea
          v-model="draft"
          data-test="chat-edit-input"
          class="w-full resize-y rounded-lg border border-border bg-background p-2 font-mono text-xs text-foreground"
          rows="4"
        />
        <div class="mt-1.5 flex items-center gap-2">
          <button
            type="button"
            data-test="chat-edit-submit"
            class="rounded-md bg-primary px-2.5 py-1 text-xs text-primary-foreground"
            @click="submitEdit"
          >
            重发
          </button>
          <button
            type="button"
            class="text-xs text-muted-foreground"
            @click="editing = false"
          >
            取消
          </button>
        </div>
      </template>

      <!-- 渲染态 -->
      <template v-else>
        <!-- 附件（P2）：用户消息携带的知识库文档；只展示标题快照 -->
        <div
          v-if="message.attachments?.length"
          data-test="chat-attachments"
          class="mb-1.5 flex flex-wrap gap-1"
        >
          <span
            v-for="attachment in message.attachments"
            :key="attachment.document_id"
            data-test="chat-attachment"
            class="inline-flex max-w-full items-center gap-1 rounded-md bg-background/20 px-1.5 py-0.5 text-[10px]"
            :class="isUser ? 'text-primary-foreground' : 'bg-muted text-muted-foreground'"
            :title="attachment.title"
          >
            <FileText class="size-3 shrink-0" />
            <span class="truncate">{{ attachment.title }}</span>
          </span>
        </div>
        <div
          v-if="displayContent"
          :data-test="`chat-body-${message.role}`"
          class="chat-markdown"
          :class="isUser ? 'chat-markdown--user' : ''"
          v-html="html"
        />
        <span
          v-if="isStreaming"
          data-test="chat-streaming-cursor"
          class="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-primary align-middle"
        />
        <p v-else-if="!displayContent" class="text-xs text-muted-foreground">（空）</p>
      </template>

      <!-- 失败原因 -->
      <p
        v-if="message.error"
        data-test="chat-message-error"
        class="mt-1 text-[11px] leading-4 text-destructive"
      >
        {{ message.error }}
      </p>
    </div>

    <!-- 工具条（悬停可见） -->
    <div
      class="flex items-center gap-1.5 px-1 opacity-0 transition-opacity group-hover:opacity-100"
    >
      <!-- 分支切换 -->
      <template v-if="branch && branch.total > 1">
        <button
          type="button"
          data-test="chat-branch-prev"
          class="rounded p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
          :disabled="branch.index <= 1"
          :aria-label="`上一个分支（${branch.index}/${branch.total}）`"
          @click="emit('switchBranch', branch.siblings[branch.index - 2] ?? message.id)"
        >
          <ChevronLeft class="size-3.5" />
        </button>
        <span data-test="chat-branch-count" class="font-mono text-[10px] tabular-nums"
          >{{ branch.index }}/{{ branch.total }}</span
        >
        <button
          type="button"
          data-test="chat-branch-next"
          class="rounded p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
          :disabled="branch.index >= branch.total"
          :aria-label="`下一个分支（${branch.index}/${branch.total}）`"
          @click="emit('switchBranch', branch.siblings[branch.index] ?? message.id)"
        >
          <ChevronRight class="size-3.5" />
        </button>
      </template>

      <button
        type="button"
        data-test="chat-copy"
        class="rounded p-0.5 text-muted-foreground hover:text-foreground"
        aria-label="复制"
        @click="copyContent"
      >
        <Check v-if="copied" class="size-3.5" />
        <Copy v-else class="size-3.5" />
      </button>

      <button
        v-if="isUser && editable"
        type="button"
        data-test="chat-edit"
        class="rounded p-0.5 text-muted-foreground hover:text-foreground"
        aria-label="编辑重发"
        @click="startEdit"
      >
        <Pencil class="size-3.5" />
      </button>

      <button
        v-if="!isUser && message.status !== 'streaming'"
        type="button"
        data-test="chat-retry"
        class="rounded p-0.5 text-muted-foreground hover:text-foreground"
        aria-label="重新生成"
        @click="emit('retry', message.id)"
      >
        <RotateCcw class="size-3.5" />
      </button>
    </div>
  </article>
</template>

<style scoped>
/* 会话气泡内的 Markdown：收紧段落间距，避免消息看起来过度留白 */
.chat-markdown :deep(p) {
  margin: 0.35em 0;
}
.chat-markdown :deep(h1),
.chat-markdown :deep(h2),
.chat-markdown :deep(h3) {
  margin: 0.5em 0 0.25em;
  font-size: 0.95em;
  font-weight: 600;
}
.chat-markdown :deep(pre) {
  margin: 0.5em 0;
  overflow-x: auto;
  border-radius: 0.5rem;
  background: hsl(var(--muted));
  padding: 0.5rem 0.75rem;
  font-size: 0.75rem;
}
.chat-markdown :deep(code) {
  font-size: 0.8em;
}
.chat-markdown :deep(table) {
  border-collapse: collapse;
  font-size: 0.75rem;
}
.chat-markdown :deep(th),
.chat-markdown :deep(td) {
  border: 1px solid hsl(var(--border));
  padding: 0.25rem 0.5rem;
}
.chat-markdown :deep(a) {
  text-decoration: underline;
  text-underline-offset: 3px;
}
/* 用户气泡里链接与行内代码反色，避免绿底绿字 */
.chat-markdown--user :deep(a),
.chat-markdown--user :deep(code) {
  color: inherit;
}
.chat-markdown :deep(.mermaid-figure svg) {
  max-width: 100%;
  height: auto;
}
</style>
