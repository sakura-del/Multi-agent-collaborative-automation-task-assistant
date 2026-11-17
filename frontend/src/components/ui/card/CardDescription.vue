<script setup lang="ts">
import { computed, inject } from "vue";
import type { HTMLAttributes } from "vue";

import { cn } from "@/lib/utils";

import { CARD_SIZE_KEY, INSET_CLASS } from "./card-context";

const props = defineProps<{ class?: HTMLAttributes["class"] }>();

const size = inject(CARD_SIZE_KEY, computed(() => "default" as const));
</script>

<template>
  <p
    data-slot="card-description"
    :class="cn('text-sm leading-relaxed text-muted-foreground', INSET_CLASS[size], props.class)"
  >
    <slot />
  </p>
</template>
