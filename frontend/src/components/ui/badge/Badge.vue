<script setup lang="ts">
import type { HTMLAttributes } from "vue";

import { cn } from "@/lib/utils";
import { type BadgeVariants, badgeVariants } from "./variants";

const props = withDefaults(
  defineProps<{
    variant?: BadgeVariants["variant"];
    class?: HTMLAttributes["class"];
  }>(),
  { variant: "default", class: undefined }
);
</script>

<template>
  <span :class="cn(badgeVariants({ variant: props.variant }), props.class)">
    <slot />
  </span>
</template>
