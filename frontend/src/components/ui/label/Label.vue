<script setup lang="ts">
import type { HTMLAttributes } from "vue";

import { cn } from "@/lib/utils";

/** 表单标签（shadcn-vue 风格）。 */
const props = defineProps<{
  for?: string;
  class?: HTMLAttributes["class"];
}>();
</script>

<template>
  <label
    :for="props.for"
    :class="cn('text-sm font-medium leading-none text-foreground', props.class)"
  >
    <slot />
  </label>
</template>
