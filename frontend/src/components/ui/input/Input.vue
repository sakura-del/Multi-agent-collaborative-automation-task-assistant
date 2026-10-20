<script setup lang="ts">
import type { HTMLAttributes } from "vue";
import { useVModel } from "@vueuse/core";

import { cn } from "@/lib/utils";

/**
 * 文本输入框（shadcn-vue 风格）。
 *
 * 支持 `v-model`（内部用 `useVModel` 双向绑定）。
 * 统一焦点环、禁用态、错误态（`aria-invalid`）。
 */
const props = withDefaults(
  defineProps<{
    modelValue?: string | number;
    defaultValue?: string | number;
    class?: HTMLAttributes["class"];
    type?: string;
    disabled?: boolean;
    placeholder?: string;
    id?: string;
    "aria-invalid"?: boolean | "true" | "false";
    "aria-label"?: string;
  }>(),
  {
    modelValue: undefined,
    defaultValue: undefined,
    class: undefined,
    type: "text",
    disabled: false,
    placeholder: undefined,
    id: undefined,
    "aria-invalid": undefined,
    "aria-label": undefined,
  }
);

const emits = defineEmits<{
  "update:modelValue": [value: string | number];
}>();

const model = useVModel(props, "modelValue", emits, {
  defaultValue: props.defaultValue,
  passive: true,
});
</script>

<template>
  <input
    :id="props.id"
    v-model="model"
    :type="props.type"
    :disabled="props.disabled"
    :placeholder="props.placeholder"
    :aria-invalid="props['aria-invalid']"
    :aria-label="props['aria-label']"
    :class="
      cn(
        'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-xs',
        'transition-colors placeholder:text-muted-foreground',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background',
        'disabled:cursor-not-allowed disabled:opacity-50',
        'aria-[invalid=true]:border-destructive aria-[invalid=true]:ring-destructive',
        props.class
      )
    "
  />
</template>
