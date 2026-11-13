<script setup lang="ts">
import { onMounted, ref } from "vue";
import { CircleCheck, Loader2, MailCheck } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { confirmEmailVerification, requestEmailVerification } from "@/api/account";
import { useAuthStore } from "@/stores/auth";

// 邮箱验证页（Phase 1 P1-C）：
//   - 带 token（从邮件链接进入）→ 自动确认
//   - 无 token → 展示「重发验证邮件」：填邮箱即可，**不需要登录态**
//     （2026-11-13 契约：未验证用户没有 token，旧版要求登录是死锁）
// 样式走 shadcn-vue / Nova 规范（AGENTS.md §6.5）。
//
// ⚠️ 测试契约（不要改）：data-test="verify-error" / "verify-success" /
// "verify-message" / "verify-request"。
const props = withDefaults(defineProps<{ token?: string }>(), { token: "" });

const auth = useAuthStore();
const confirming = ref(false);
const requesting = ref(false);
const message = ref<string | null>(null);
const error = ref<string | null>(null);
const confirmed = ref(false);
// 登录态下预填当前账号邮箱；未登录时（邮件链接过期场景）留空由用户填写
const email = ref(auth.user?.email ?? "");

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
  if (!email.value.trim()) {
    error.value = "请先填写注册时的邮箱";
    return;
  }
  requesting.value = true;
  error.value = null;
  message.value = null;
  try {
    // 响应恒定（防邮箱枚举）：无论该邮箱是否注册，展示同样的成功文案
    const result = await requestEmailVerification(email.value.trim());
    message.value = result.message;
  } catch (err) {
    error.value = (err as Error).message || "发送验证邮件失败";
  } finally {
    requesting.value = false;
  }
}
</script>

<template>
  <section class="mx-auto mt-10 w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-xs sm:p-7">
    <header class="mb-4">
      <h1 class="font-display text-2xl font-medium tracking-[-0.025em]">邮箱验证</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">
        验证邮箱后即可完整使用账号功能。邮件里的链接有效期 24 小时，过期可重新发送。
      </p>
    </header>

    <ErrorBanner :message="error" testid="verify-error" />

    <Alert
      v-if="message && confirmed"
      variant="success"
      class="mb-3"
      data-test="verify-success"
    >
      <CircleCheck />
      <span>{{ message }}</span>
    </Alert>
    <Alert v-else-if="message" variant="success" class="mb-3" data-test="verify-message">
      <CircleCheck />
      <span>{{ message }}</span>
    </Alert>

    <p v-if="confirming" class="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 class="size-4 animate-spin" aria-hidden="true" />
      <span>正在验证…</span>
    </p>

    <div v-if="!props.token" class="mb-3 space-y-1.5">
      <Label for="verify-email">注册邮箱</Label>
      <Input
        id="verify-email"
        v-model="email"
        type="email"
        placeholder="you@example.com"
        autocomplete="email"
        data-test="verify-email-input"
      />
    </div>

    <Button
      v-if="!props.token"
      type="button"
      data-test="verify-request"
      :disabled="requesting"
      @click="handleRequest"
    >
      <Loader2 v-if="requesting" class="animate-spin" aria-hidden="true" />
      <MailCheck v-else :size="15" aria-hidden="true" />
      <span>{{ requesting ? "发送中…" : "重发验证邮件" }}</span>
    </Button>
  </section>
</template>
