<script setup lang="ts">
import { computed, ref } from "vue";
import { CircleCheck, KeyRound, Loader2, Send } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  <section class="mx-auto mt-10 w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-xs sm:p-7">
    <header class="mb-4">
      <h1 class="font-display text-2xl font-medium tracking-[-0.025em]">重置密码</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">
        <template v-if="isConfirmPhase">
          设置新密码后，此前签发的登录令牌会全部失效，需要重新登录。
        </template>
        <template v-else>
          输入注册邮箱，我们会发送重置链接（有效期 24 小时）。
        </template>
      </p>
    </header>

    <ErrorBanner :message="error" testid="reset-error" />

    <Alert
      v-if="message && isConfirmPhase"
      variant="success"
      class="mb-3"
      data-test="reset-success"
    >
      <CircleCheck />
      <span>{{ message }}</span>
    </Alert>
    <Alert v-else-if="message" variant="success" class="mb-3" data-test="reset-requested">
      <CircleCheck />
      <span>{{ message }}</span>
    </Alert>

    <!-- 申请阶段 -->
    <form
      v-if="!isConfirmPhase"
      class="flex max-w-sm flex-col gap-3"
      data-test="reset-request-form"
      @submit.prevent="handleRequest"
    >
      <div class="grid gap-1.5">
        <Label for="reset-email">注册邮箱</Label>
        <Input
          id="reset-email"
          v-model="email"
          data-test="reset-email"
          type="email"
          placeholder="you@example.com"
          required
        />
      </div>
      <Button type="submit" data-test="reset-request-submit" :disabled="requesting" class="w-fit">
        <Loader2 v-if="requesting" class="animate-spin" aria-hidden="true" />
        <Send v-else :size="15" aria-hidden="true" />
        <span>{{ requesting ? "发送中…" : "发送重置邮件" }}</span>
      </Button>
    </form>

    <!-- 确认阶段 -->
    <form
      v-else
      class="flex max-w-sm flex-col gap-3"
      data-test="reset-confirm-form"
      @submit.prevent="handleConfirm"
    >
      <div class="grid gap-1.5">
        <Label for="reset-password">新密码（至少 {{ MIN_PASSWORD }} 位）</Label>
        <Input
          id="reset-password"
          v-model="password"
          data-test="reset-password"
          type="password"
          :minlength="MIN_PASSWORD"
          placeholder="至少 8 位"
          required
        />
      </div>
      <Button type="submit" data-test="reset-confirm-submit" :disabled="confirming" class="w-fit">
        <Loader2 v-if="confirming" class="animate-spin" aria-hidden="true" />
        <KeyRound v-else :size="15" aria-hidden="true" />
        <span>{{ confirming ? "提交中…" : "设置新密码" }}</span>
      </Button>
    </form>
  </section>
</template>
