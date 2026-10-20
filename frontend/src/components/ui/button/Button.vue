<script setup lang="ts">
import type { HTMLAttributes } from "vue";

import { cn } from "@/lib/utils";
import { type ButtonVariants, buttonVariants } from "./variants";

/**
 * 按钮（shadcn-vue 风格）。
 *
 * 用法：
 *   <Button>保存</Button>
 *   <Button variant="ghost" size="icon" aria-label="删除"><Trash2 /></Button>
 *
 * 注意：仅图标时必须给 `aria-label`（无障碍硬要求，AGENTS.md §6.5.3）。
 */
const props = withDefaults(
  defineProps<{
    variant?: ButtonVariants["variant"];
    size?: ButtonVariants["size"];
    type?: "button" | "submit" | "reset";
    disabled?: boolean;
    class?: HTMLAttributes["class"];
  }>(),
  {
    variant: "default",
    size: "default",
    type: "button",
    disabled: false,
    class: undefined,
  }
);
</script>

<template>
  <button
    :type="props.type"
    :disabled="props.disabled"
    :class="cn(buttonVariants({ variant: props.variant, size: props.size }), props.class)"
  >
    <slot />
  </button>
</template>
