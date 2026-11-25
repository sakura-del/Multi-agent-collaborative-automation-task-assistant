<script setup lang="ts">
import type { HTMLAttributes } from "vue";
import { ref, watch } from "vue";
import { cn } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";

/**
 * 错误提示条（项目通用件，基于 shadcn 风格 `Alert`）。
 *
 * 统一封装「有消息才显示」+ 错误图标 + destructive 变体，
 * 并通过受控 `open` 与 `@dismiss` 修复关闭后同文案错误无法复现的问题。
 *
 * 兼容性（既有页面与测试依赖）：
 * - props：`message`（空则整条不渲染）、`testid`（默认 `error-banner`）；
 * - 渲染出 `role="alert"` 与 `data-test`，测试用它们定位；
 * - 事件：`dismiss`（用户点击关闭按钮时触发）。
 */
const props = withDefaults(
  defineProps<{
    message: string | null | undefined;
    testid?: string;
    class?: HTMLAttributes["class"];
  }>(),
  { testid: "error-banner", class: undefined },
);

const emit = defineEmits<{
  (event: "dismiss"): void;
}>();

const visible = ref(Boolean(props.message));
const displayMessage = ref(props.message ?? "");

watch(
  () => props.message,
  (msg) => {
    visible.value = Boolean(msg);
    if (msg) {
      displayMessage.value = msg;
    }
  },
);

function onUpdateOpen(open: boolean) {
  visible.value = open;
  if (!open) {
    emit("dismiss");
  }
}
</script>

<template>
  <Alert
    :open="Boolean(props.message) && visible"
    variant="destructive"
    :icon="true"
    :data-test="props.testid"
    :class="cn('mb-3', props.class)"
    @update:open="onUpdateOpen"
  >
    <span>{{ displayMessage }}</span>
  </Alert>
</template>
