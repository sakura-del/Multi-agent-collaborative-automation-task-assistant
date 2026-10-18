<script setup lang="ts">
// 通用错误横幅：表单/页面级错误统一展示，带 role="alert" 便于无障碍与测试
defineProps<{ message: string | null | undefined; testid?: string }>();
</script>

<template>
  <p v-if="message" class="banner banner--error" role="alert" :data-test="testid ?? 'error-banner'">
    {{ message }}
  </p>
</template>
