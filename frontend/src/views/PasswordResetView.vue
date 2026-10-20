<script setup lang="ts">
import { computed, ref } from "vue";
import { KeyRound, Send } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { confirmPasswordReset, requestPasswordReset } from "@/api/account";

// 密码重置页（Phase 1 P1-C）：
//   - 无 token（从登录页「忘记密码」进入）→ 申请阶段：填邮箱
//   - 有 token（从邮件链接进入）→ 确认阶段：设新密码
const props = withDefaults(defineProps<{ token?: string }>(), { token: "" });

const email = ref("");
const password = ref("");
const requesting = ref(false);
const confirming = ref(false);
const message = ref<string | null>(null);
const error = ref<string | null>(null);

const isConfirmPhase = computed(() => Boolean(props.token));
const MIN_PASSWORD = 8;

function validEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}

async function handleRequest() {
  error.value = null;
  message.value = null;
  if (!validEmail(email.value)) {
    error.value = "请输入有效的邮箱地址";
    return;
  }
  requesting.value = true;
  try {
    // 后端恒定响应：不泄漏该邮箱是否已注册
    const result = await requestPasswordReset(email.value.trim());
    message.value = result.message;
  } catch (err) {
    error.value = (err as Error).message || "申请密码重置失败";
  } finally {
    requesting.value = false;
  }
}

async function handleConfirm() {
  error.value = null;
  message.value = null;
  if (password.value.length < MIN_PASSWORD) {
    error.value = `新密码至少 ${MIN_PASSWORD} 位`;
    return;
  }
  confirming.value = true;
  try {
    const result = await confirmPasswordReset({
      token: props.token,
      new_password: password.value,
    });
    message.value = result.message;
    password.value = "";
  } catch (err) {
    error.value = (err as Error).message || "密码重置失败";
  } finally {
    confirming.value = false;
  }
}
</script>

<template>
  <section class="page">
    <header class="page__header">
      <h1>重置密码</h1>
      <p class="page__hint">
        <template v-if="isConfirmPhase">
          设置新密码后，此前签发的登录令牌会全部失效，需要重新登录。
        </template>
        <template v-else>
          输入注册邮箱，我们会发送重置链接（有效期 24 小时）。
        </template>
      </p>
    </header>

    <ErrorBanner :message="error" testid="reset-error" />
    <p v-if="message && isConfirmPhase" class="banner banner--ok" data-test="reset-success">
      {{ message }}
    </p>
    <p v-else-if="message" class="banner banner--ok" data-test="reset-requested">
      {{ message }}
    </p>

    <!-- 申请阶段 -->
    <form
      v-if="!isConfirmPhase"
      class="form"
      data-test="reset-request-form"
      @submit.prevent="handleRequest"
    >
      <label class="field">
        <span>注册邮箱</span>
        <input
          v-model="email"
          data-test="reset-email"
          class="input"
          type="email"
          placeholder="you@example.com"
          required
        />
      </label>
      <button
        class="btn btn--primary"
        type="submit"
        data-test="reset-request-submit"
        :disabled="requesting"
      >
        <Send :size="15" />
        <span>{{ requesting ? "发送中…" : "发送重置邮件" }}</span>
      </button>
    </form>

    <!-- 确认阶段 -->
    <form v-else class="form" data-test="reset-confirm-form" @submit.prevent="handleConfirm">
      <label class="field">
        <span>新密码（至少 {{ MIN_PASSWORD }} 位）</span>
        <input
          v-model="password"
          data-test="reset-password"
          class="input"
          type="password"
          :minlength="MIN_PASSWORD"
          placeholder="至少 8 位"
          required
        />
      </label>
      <button
        class="btn btn--primary"
        type="submit"
        data-test="reset-confirm-submit"
        :disabled="confirming"
      >
        <KeyRound :size="15" />
        <span>{{ confirming ? "提交中…" : "设置新密码" }}</span>
      </button>
    </form>
  </section>
</template>

<style scoped>
.page__header {
  margin-bottom: 1rem;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  max-width: 24rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.85rem;
}

.field .input {
  padding: 0.4rem 0.55rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  font-size: 0.88rem;
}

.banner--ok {
  padding: 0.5rem 0.75rem;
  border-radius: 8px;
  background: #ecfdf5;
  color: #047857;
  font-size: 0.88rem;
  margin-bottom: 0.75rem;
}

@media (max-width: 720px) {
  .form {
    max-width: none;
  }
}
</style>
