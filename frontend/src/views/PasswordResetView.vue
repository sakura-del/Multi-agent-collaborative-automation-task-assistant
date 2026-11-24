<script setup lang="ts">
import { computed, ref } from "vue";
import { ArrowLeft, CircleCheck, KeyRound, Loader2, LockKeyhole, Send } from "lucide-vue-next";

import AuthBrandPanel from "@/components/auth/AuthBrandPanel.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { confirmPasswordReset, requestPasswordReset } from "@/api/account";

// 密码重置页（Phase 1 P1-C；Issue #39 统一至登录页双栏品牌版式与主色）：
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
  <!-- 与登录/注册页共用双栏品牌版式、全宽深绿主按钮与留白节奏（Issue #39） -->
  <div class="grid min-h-dvh bg-background text-foreground lg:grid-cols-[1.05fr_1fr]">
    <AuthBrandPanel heading-tag="p" />

    <section class="flex items-center justify-center bg-background px-4 py-10 sm:px-8">
      <div class="w-full max-w-[22rem]">
        <div class="mb-6 flex items-center justify-between gap-4 lg:hidden">
          <RouterLink
            to="/"
            aria-label="VeriPaper 首页"
            class="flex w-fit items-center gap-2 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-[#0F4C3A]"
          >
            <img src="/logo.svg" alt="" aria-hidden="true" class="size-7" />
            <span class="text-sm font-semibold tracking-tight">VeriPaper</span>
          </RouterLink>
        </div>

        <header>
          <h1 class="text-xl font-semibold tracking-tight text-foreground">重置密码</h1>
          <p class="mt-1 text-xs leading-relaxed text-muted-foreground">
            <template v-if="isConfirmPhase">
              设置新密码后，此前签发的登录令牌会全部失效，需要重新登录。
            </template>
            <template v-else>
              输入注册邮箱，我们会发送重置链接（有效期 24 小时）。
            </template>
          </p>
        </header>

        <div class="mt-6 space-y-4">
          <ErrorBanner :message="error" testid="reset-error" />

          <Alert
            v-if="message && isConfirmPhase"
            variant="success"
            data-test="reset-success"
          >
            <CircleCheck />
            <span>{{ message }}</span>
          </Alert>
          <Alert v-else-if="message" variant="success" data-test="reset-requested">
            <CircleCheck />
            <span>{{ message }}</span>
          </Alert>

          <!-- 申请阶段 -->
          <form
            v-if="!isConfirmPhase"
            class="flex flex-col gap-3.5"
            data-test="reset-request-form"
            @submit.prevent="handleRequest"
          >
            <div class="grid gap-1.5">
              <Label for="reset-email" class="text-xs">注册邮箱</Label>
              <Input
                id="reset-email"
                v-model="email"
                data-test="reset-email"
                type="email"
                autocomplete="email"
                placeholder="name@university.edu"
                required
                class="h-10 rounded-lg border-neutral-200 focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input"
              />
            </div>
            <Button
              type="submit"
              data-test="reset-request-submit"
              :disabled="requesting"
              class="h-10 w-full rounded-lg bg-[#0F4C3A] text-white hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
            >
              <Loader2 v-if="requesting" class="animate-spin" aria-hidden="true" />
              <Send v-else :size="15" aria-hidden="true" />
              <span>{{ requesting ? "发送中…" : "发送重置邮件" }}</span>
            </Button>
          </form>

          <!-- 确认阶段 -->
          <form
            v-else
            class="flex flex-col gap-3.5"
            data-test="reset-confirm-form"
            @submit.prevent="handleConfirm"
          >
            <div class="grid gap-1.5">
              <Label for="reset-password" class="text-xs">新密码（至少 {{ MIN_PASSWORD }} 位）</Label>
              <Input
                id="reset-password"
                v-model="password"
                data-test="reset-password"
                type="password"
                :minlength="MIN_PASSWORD"
                autocomplete="new-password"
                placeholder="至少 8 位"
                required
                class="h-10 rounded-lg border-neutral-200 focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input"
              />
            </div>
            <Button
              type="submit"
              data-test="reset-confirm-submit"
              :disabled="confirming"
              class="h-10 w-full rounded-lg bg-[#0F4C3A] text-white hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
            >
              <Loader2 v-if="confirming" class="animate-spin" aria-hidden="true" />
              <KeyRound v-else :size="15" aria-hidden="true" />
              <span>{{ confirming ? "提交中…" : "设置新密码" }}</span>
            </Button>
          </form>

          <!-- 重置成功后直接给下一步：此前用户要自己想起再回 /login -->
          <p v-if="message && isConfirmPhase" class="text-center text-xs">
            <RouterLink
              :to="{ name: 'login' }"
              data-test="reset-go-login"
              class="font-medium text-[#0F4C3A] underline-offset-4 hover:underline dark:text-primary"
            >
              用新密码登录
            </RouterLink>
          </p>
        </div>

        <div class="mt-6 border-t border-border pt-4 text-center">
          <RouterLink
            :to="{ name: 'login' }"
            data-test="reset-back-to-login"
            class="inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft :size="12" aria-hidden="true" />
            返回登录
          </RouterLink>
          <p class="mt-3 flex items-center justify-center gap-1.5 text-[11px] text-muted-foreground">
            <LockKeyhole :size="12" aria-hidden="true" />
            你的研究数据仅对当前工作空间可见
          </p>
        </div>
      </div>
    </section>
  </div>
</template>
