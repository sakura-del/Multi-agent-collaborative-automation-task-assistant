<script setup lang="ts">
defineOptions({ inheritAttrs: false });
/**
 * beUI Prompt Input 的 Vue 移植
 * （https://asharca.github.io/ui/components/agents/prompt-input/）。
 *
 * 支持自动增长 textarea（rAF 合批 + max-h-72 滚动上限）、IME 组合输入防误触、
 * Enter / Ctrl+Enter 快捷键与显式提交按钮。
 */
import { computed, onBeforeUnmount, ref, useAttrs, watch } from "vue";
import { CornerDownLeft, Loader2, Sparkles } from "lucide-vue-next";

import { Button } from "@/components/ui/button";

const props = withDefaults(
  defineProps<{
    modelValue?: string;
    placeholder?: string;
    hint?: string;
    submitting?: boolean;
    minHeight?: string;
    disabled?: boolean;
    /** Enter 直提交（一句话入口）；false = Ctrl/⌘+Enter（粘贴区多行） */
    submitOnEnter?: boolean;
    /** 紧凑模式（侧栏窄容器）：底部单行展示提示与字数，避免换行挤压（Issue #57） */
    compact?: boolean;
    /** 提交按钮自定义文案 */
    submitLabel?: string;
  }>(),
  {
    modelValue: "",
    placeholder: "粘贴 BibTeX / RIS 文本…",
    hint: "支持 @article / @inproceedings 等；RIS 以 TY  - 开头",
    submitting: false,
    minHeight: "10rem",
    disabled: false,
    submitOnEnter: false,
    compact: false,
    submitLabel: "发送",
  },
);

const emit = defineEmits<{
  "update:modelValue": [value: string];
  submit: [value: string];
}>();

/** E2E/表单契约：id、data-test 等 attrs 要落在 **textarea** 上，不是根容器 */
const attrs = useAttrs();

const focused = ref(false);
const textarea = ref<HTMLTextAreaElement | null>(null);
let growRafId: number | null = null;

/** 自动增长：由 CSS minHeight 兜底并在 rAF 内合批读取 scrollHeight，避免单位解析错误与同步重排 */
function applyAutoGrow() {
  const el = textarea.value;
  if (!el) return;
  const rawMin = typeof window !== "undefined" ? getComputedStyle(el).minHeight : "";
  const minPx = rawMin.endsWith("px") ? Number.parseFloat(rawMin) || 0 : 0;
  el.style.height = "auto";
  const nextHeight = Math.max(el.scrollHeight, minPx);
  if (nextHeight > 0) {
    el.style.height = `${nextHeight}px`;
  } else {
    el.style.height = "";
  }
}

function scheduleAutoGrow() {
  if (typeof window === "undefined" || typeof requestAnimationFrame !== "function") {
    applyAutoGrow();
    return;
  }
  if (growRafId !== null) cancelAnimationFrame(growRafId);
  growRafId = requestAnimationFrame(() => {
    growRafId = null;
    applyAutoGrow();
  });
}

onBeforeUnmount(() => {
  if (growRafId !== null && typeof cancelAnimationFrame === "function") {
    cancelAnimationFrame(growRafId);
  }
});

watch(
  () => props.modelValue,
  () => {
    scheduleAutoGrow();
  },
);

function onInput(event: Event) {
  const value = (event.target as HTMLTextAreaElement).value;
  emit("update:modelValue", value);
}

function submit() {
  if (props.disabled || props.submitting || !props.modelValue.trim()) return;
  emit("submit", props.modelValue);
}

function onKeydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return;
  const isCtrlEnter = (event.ctrlKey || event.metaKey) && event.key === "Enter";
  const isBareEnter = props.submitOnEnter && event.key === "Enter" && !event.shiftKey;
  if (isCtrlEnter || isBareEnter) {
    event.preventDefault();
    submit();
  }
}

const hasContent = computed(() => props.modelValue.trim().length > 0);
const charCount = computed(() => props.modelValue.length);

defineExpose({
  focus: () => textarea.value?.focus(),
});
</script>

<template>
  <div
    data-test="prompt-input"
    class="min-w-0 w-full max-w-full overflow-hidden rounded-2xl border bg-background transition-shadow"
    :class="focused ? 'border-ring ring-2 ring-ring/40' : 'border-border'"
  >
    <textarea
      ref="textarea"
      v-bind="attrs"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      class="block max-h-72 w-full resize-none overflow-y-auto rounded-t-2xl bg-transparent px-4 py-3 font-mono text-xs leading-5 text-foreground placeholder:text-muted-foreground focus:outline-none disabled:opacity-50"
      :style="{ minHeight }"
      spellcheck="false"
      @input="onInput"
      @focus="focused = true"
      @blur="focused = false"
      @keydown="onKeydown"
    />
    <div
      class="flex items-center justify-between gap-x-2 gap-y-1 border-t border-border px-3 py-2"
      :class="compact ? 'flex-nowrap' : 'flex-wrap'"
      data-test="prompt-input-footer"
    >
      <span
        class="inline-flex min-w-0 flex-1 items-center gap-1 text-[10px] text-muted-foreground"
        :class="compact ? 'truncate' : ''"
      >
        <Sparkles class="size-3 shrink-0" />
        <span :class="compact ? 'truncate' : 'break-words'">{{ hint }}</span>
      </span>
      <div class="flex shrink-0 items-center gap-2 whitespace-nowrap">
        <span class="font-mono text-[10px] tabular-nums text-muted-foreground">
          {{ `${charCount} 字` }}
        </span>
        <span
          v-if="!compact"
          class="hidden items-center gap-1 text-[10px] text-muted-foreground sm:inline-flex"
        >
          <CornerDownLeft class="size-3 shrink-0" />
          {{ submitOnEnter ? "Enter 提交" : "Ctrl/⌘+Enter 提交" }}
        </span>
        <Button
          type="button"
          size="sm"
          data-test="prompt-submit"
          :disabled="disabled || submitting || !hasContent"
          :class="compact ? 'h-6 px-2 text-[11px]' : 'h-7 px-2.5 text-xs'"
          @click="submit"
        >
          <Loader2
            v-if="submitting"
            class="size-3 animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
          <span>{{ submitLabel }}</span>
        </Button>
      </div>
    </div>
  </div>
</template>
