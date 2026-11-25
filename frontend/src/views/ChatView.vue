<script setup lang="ts">
/**
 * 会话全屏页（Issue #8 P0 三形态之一）：`/chat`。
 *
 * 与右下小窗共用同一个 store——从任务页「全屏打开」跳到这里的会话不丢。
 * 配合 AppLayout 在 route.name === 'chat' 去除上下 padding，消除外层双滚动条。
 */
import ChatShell from "@/components/chat/ChatShell.vue";
</script>

<template>
  <div class="flex h-[calc(100dvh-2.75rem)] flex-col overflow-hidden">
    <ChatShell variant="full" class="min-h-0 flex-1" />
  </div>
</template>
