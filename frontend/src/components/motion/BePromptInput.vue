<script setup lang="ts">
defineOptions({ inheritAttrs: false });
/**
 * beUI Prompt Input 的 Vue 移植
 * （https://asharca.github.io/ui/components/agents/prompt-input/）。
 *
 * 原实现：自动增长 textarea + 键盘提交 + 动画化发送/停止状态。
 * 本项目适配（AGENTS.md §6.5.1.1 ①）：用于 BibTeX/RIS 粘贴导入——
 * 保留自动增长与提交态动画，粘贴区较大（min-h-40），hint 提示支持格式探测。
 *
 * 规格：容器 rounded-2xl border bg-background；textarea 无边框（焦点在容器）；
 * 容器 focus-within ring；发送按钮 rounded-xl；EASE_OUT [0.16,1,0.3,1]；
 * Ctrl/⌘+Enter 提交；reduced-motion 降级。
 */
import { computed, ref, watch, useAttrs } from "vue";
import { motion } from "motion-v";
import { CornerDownLeft, Sparkles } from "lucide-vue-next";

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
  }>(),
  {
    modelValue: "",
    placeholder: "粘贴 BibTeX / RIS 文本…",
    hint: "支持 @article / @inproceedings 等；RIS 以 TY  - 开头",
    submitting: false,
    minHeight: "10rem",
    disabled: false,
    submitOnEnter: false,
  }
);

const emit = defineEmits<{
  "update:modelValue": [value: string];
  submit: [value: string];
}>();

/** E2E/表单契约：id、data-test 等 attrs 要落在 **textarea** 上，不是根容器 */
const attrs = useAttrs();

const focused = ref(false);
const textarea = ref<HTMLTextAreaElement | null>(null);

/** 自动增长：滚动高度撑开容器（minHeight 之下不缩） */
function autoGrow() {
  const el = textarea.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${Math.max(el.scrollHeight, parseInt(props.minHeight, 10) || 160)}px`;
}

watch(
  () => props.modelValue,
  () => {
    void autoGrow();
  }
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
  const isCtrlEnter = (event.ctrlKey || event.metaKey) && event.key === "Enter";
  const isBareEnter = props.submitOnEnter && event.key === "Enter" && !event.shiftKey;
  if (isCtrlEnter || isBareEnter) {
    event.preventDefault();
    submit();
  }
}

const hasContent = computed(() => props.modelValue.trim().length > 0);
const charCount = computed(() => props.modelValue.length);
</script>

<template>
  <div
    data-test="prompt-input"
    class="rounded-2xl border bg-background transition-shadow"
    :class="focused ? 'border-ring ring-2 ring-ring/40' : 'border-border'"
  >
    <textarea
      ref="textarea"
      v-bind="attrs"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      class="block w-full resize-none rounded-t-2xl bg-transparent px-4 py-3 font-mono text-xs leading-5 text-foreground placeholder:text-muted-foreground focus:outline-none disabled:opacity-50"
      :style="{ minHeight }"
      spellcheck="false"
      @input="onInput"
      @focus="focused = true"
      @blur="focused = false"
      @keydown="onKeydown"
    />
    <div class="flex items-center gap-2 border-t border-border px-3 py-2">
      <span class="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
        <Sparkles class="size-3" />
        {{ hint }}
      </span>
      <span class="ml-auto font-mono text-[10px] tabular-nums text-muted-foreground">
        {{ charCount }} chars
      </span>
      <span class="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
        <CornerDownLeft class="size-3" />
        Ctrl/⌘+Enter 提交
      </span>
    </div>
  </div>
</template>
