<script setup lang="ts">
import "vue-sonner/style.css";
import { Toaster } from "vue-sonner";

import { useTheme } from "@/composables/useTheme";

const { isDark } = useTheme();
</script>

<template>
  <RouterView />
  <Toaster
    position="top-right"
    :theme="isDark ? 'dark' : 'light'"
    rich-colors
    :duration="2800"
    :visible-toasts="4"
    :toast-options="{ style: { borderRadius: 'var(--radius)' } }"
  />
</template>
