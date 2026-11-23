<script setup lang="ts">
/**
 * 会话全屏页（Issue #8 P0 三形态之一）：`/chat`。
 *
 * 与右下小窗共用同一个 store——从任务页「全屏打开」跳到这里的会话不丢。
 * 形态按钮在壳内（dock ⇄ floating 会导航回任务页）。
 */
import ChatShell from "@/components/chat/ChatShell.vue";
</script>

<template>
  <div class="flex h-[calc(100svh-4rem)] flex-col">
    <ChatShell variant="full" class="flex-1" />
  </div>
</template>
