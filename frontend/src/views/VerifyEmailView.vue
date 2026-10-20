<script setup lang="ts">
import { onMounted, ref } from "vue";
import { MailCheck } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { confirmEmailVerification, requestEmailVerification } from "@/api/account";

// 邮箱验证页（Phase 1 P1-C）：
//   - 带 token（从邮件链接进入）→ 自动确认
//   - 无 token → 展示「重发验证邮件」（需登录态）
const props = withDefaults(defineProps<{ token?: string }>(), { token: "" });

const confirming = ref(false);
const requesting = ref(false);
const message = ref<string | null>(null);
const error = ref<string | null>(null);
const confirmed = ref(false);

onMounted(() => {
  if (props.token) {
    void handleConfirm();
  }
});

async function handleConfirm() {
  confirming.value = true;
  error.value = null;
  try {
    const result = await confirmEmailVerification(props.token);
    message.value = result.message;
    confirmed.value = true;
  } catch (err) {
    error.value = (err as Error).message || "邮箱验证失败";
  } finally {
    confirming.value = false;
  }
}

async function handleRequest() {
  requesting.value = true;
  error.value = null;
  message.value = null;
  try {
    const result = await requestEmailVerification();
    message.value = result.message;
  } catch (err) {
    error.value = (err as Error).message || "发送验证邮件失败";
  } finally {
    requesting.value = false;
  }
}
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>邮箱验证</h1>
      <p class="page__hint">
        验证邮箱后即可完整使用账号功能。邮件里的链接有效期 24 小时，过期可重新发送。
      </p>
    </header>

    <ErrorBanner :message="error" testid="verify-error" />

    <p v-if="message && confirmed" class="banner banner--ok" data-test="verify-success">
      {{ message }}
    </p>
    <p v-else-if="message" class="banner banner--ok" data-test="verify-message">
      {{ message }}
    </p>

    <p v-if="confirming" class="page__hint">正在验证…</p>

    <button
      v-if="!props.token"
      class="btn btn--primary"
      type="button"
      data-test="verify-request"
      :disabled="requesting"
      @click="handleRequest"
    >
      <MailCheck :size="15" />
      <span>{{ requesting ? "发送中…" : "重发验证邮件" }}</span>
    </button>
  </section>
</template>

<style scoped>
.page__header {
  margin-bottom: 1rem;
}

.banner--ok {
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  background: #ecfdf5;
  color: #047857;
  font-size: 0.88rem;
  margin-bottom: 0.75rem;
}
</style>
