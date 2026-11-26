<script setup lang="ts">
import "vue-sonner/style.css";
import type { RouteLocationNormalizedLoaded } from "vue-router";
import { Toaster } from "vue-sonner";

import { useTheme } from "@/composables/useTheme";

const { isDark } = useTheme();

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
    <Transition name="vp-route" mode="out-in">
      <component :is="Component" v-if="Component" :key="topRouteKey(route)" />
    </Transition>
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
