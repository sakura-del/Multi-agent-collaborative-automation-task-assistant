<script setup lang="ts">
import type { HTMLAttributes } from "vue";

import { cn } from "@/lib/utils";
import { type AlertVariants, alertVariants } from "./variants";

const props = withDefaults(
  defineProps<{
    variant?: AlertVariants["variant"];
    class?: HTMLAttributes["class"];
  }>(),
  { variant: "default", class: undefined }
);
</script>

<template>
  <div
    role="alert"
    :class="cn(alertVariants({ variant: props.variant }), 'flex items-center gap-2', props.class)"
  >
    <slot />
  </div>
</template>
