<script setup lang="ts">
import "vue-sonner/style.css";
import { AnimatePresence, Motion, useReducedMotion } from "motion-v";
import type { RouteLocationNormalizedLoaded } from "vue-router";
import { Toaster } from "vue-sonner";

import { useTheme } from "@/composables/useTheme";
import { DURATION, EASE_IN, EASE_OUT, OFFSET } from "@/lib/ease";

const { isDark } = useTheme();
const reduced = useReducedMotion();

/**
 * 顶层路由过渡键：
 * 工作台内部路由共享稳定 key `"app-layout"`，确保工作台切页时不重挂载 AppLayout 壳层。
 */
function topRouteKey(route: RouteLocationNormalizedLoaded): string {
  if (route.matched.length > 1) return "app-layout";
  return String(route.name ?? route.path);
}
</script>

<template>
  <RouterView v-slot="{ Component, route }">
    <AnimatePresence mode="wait" :initial="false">
      <Motion
        v-if="Component"
        :key="topRouteKey(route)"
        :initial="{ opacity: 0, y: reduced ? 0 : OFFSET.microY }"
        :animate="{
          opacity: 1,
          y: 0,
          transition: { duration: reduced ? DURATION.micro : 0.18, ease: EASE_OUT },
        }"
        :exit="{
          opacity: 0,
          transition: { duration: reduced ? DURATION.micro : 0.1, ease: EASE_IN },
        }"
      >
        <component :is="Component" />
      </Motion>
    </AnimatePresence>
  </RouterView>
  <Toaster
    position="top-right"
    :theme="isDark ? 'dark' : 'light'"
    rich-colors
    :duration="2800"
    :visible-toasts="4"
    :toast-options="{ style: { borderRadius: 'var(--radius)' } }"
  />
</template>
