<script setup lang="ts">
// 任务详情：Plan 4 Task 4/5 接入规划确认、DAG 与实时事件；此处先给出页面骨架
defineProps<{ taskId: string }>();
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>任务详情</h1>
      <p class="page__hint">任务 {{ taskId }}：规划确认与 DAG 监控将在 Plan 4 Task 4/5 接入。</p>
    </header>
  </section>
</template>
