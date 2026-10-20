<script setup lang="ts">
import type { HTMLAttributes } from "vue";
import { CircleAlert } from "lucide-vue-next";

import { cn } from "@/lib/utils";
import { Alert } from "@/components/ui/alert";

/**
 * 错误提示条（项目通用件，基于 shadcn 风格 `Alert`）。
 *
 * 统一封装「有消息才显示」+ 错误图标 + destructive 变体，
 * 各页面不用重复写 `v-if="error"` 与图标。
 *
 * 兼容性（既有页面与测试依赖，**不要改**）：
 * - props：`message`（空则整条不渲染）、`testid`（默认 `error-banner`）；
 * - 渲染出 `role="alert"` 与 `data-test`，测试用它们定位。
 */
const props = withDefaults(
  defineProps<{
    message: string | null | undefined;
    testid?: string;
    class?: HTMLAttributes["class"];
  }>(),
  { testid: "error-banner", class: undefined }
);
</script>

<template>
  <Alert
    v-if="props.message"
    variant="destructive"
    :data-test="props.testid"
    :class="cn('mb-3', props.class)"
  >
    <CircleAlert />
    <span>{{ props.message }}</span>
  </Alert>
</template>
