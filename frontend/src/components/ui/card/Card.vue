<script setup lang="ts">
import type { HTMLAttributes } from "vue";

import { cn } from "@/lib/utils";

/** 卡片容器（shadcn-vue 风格）：统一边框、圆角、内边距基调。 */
const props = defineProps<{ class?: HTMLAttributes["class"] }>();
</script>

<template>
  <div
    :class="cn('rounded-lg border border-border bg-card text-card-foreground', props.class)"
  >
    <slot />
  </div>
</template>
