<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  ArrowLeft,
  ArrowRight,
  Github,
  KeyRound,
  Loader2,
  LockKeyhole,
  MailCheck,
} from "lucide-vue-next";

import AuthBrandPanel from "@/components/auth/AuthBrandPanel.vue";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { authApi } from "@/api/auth";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();

type AuthStep = "email" | "code" | "password";

const step = ref<AuthStep>(route.query.mode === "password" ? "password" : "email");
const otpEmail = ref("");
const otpCode = ref("");
const cooldown = ref(0);
let cooldownTimer: ReturnType<typeof setInterval> | null = null;

// 密码兜底登录（保留给管理员或老账号）
const account = ref("");
const password = ref("");

const loading = ref(false);
const magicChecking = ref(false);
const errorMessage = ref("");
const statusMessage = ref("");

const heading = computed(() => {
  if (step.value === "code") {
    return {
      title: "输入邮箱验证码",
      hint: `已向 ${otpEmail.value} 发送 6 位验证码与一键登录链接`,
    };
  }
  if (step.value === "password") {
    return {
      title: "账号密码登录",
      hint: "使用已有用户名/邮箱与密码访问工作台",
    };
  }
  return {
    title: "登录或创建账号",
    hint: "输入邮箱获取验证码或一键登录链接，新邮箱将自动建号",
  };
});

function startCooldown(seconds: number) {
  cooldown.value = Math.max(0, seconds);
  if (cooldownTimer) clearInterval(cooldownTimer);
  if (cooldown.value <= 0) return;
  cooldownTimer = setInterval(() => {
    cooldown.value = Math.max(0, cooldown.value - 1);
    if (cooldown.value === 0 && cooldownTimer) {
      clearInterval(cooldownTimer);
      cooldownTimer = null;
    }
  }, 1000);
}

async function redirectAfterAuth() {
  const redirect = route.query.redirect;
  await router.push(typeof redirect === "string" && redirect.startsWith("/") ? redirect : { name: "tasks" });
}

async function handleSendOtp() {
  const email = otpEmail.value.trim();
  if (!email) return;
  loading.value = true;
  errorMessage.value = "";
  statusMessage.value = "";
  try {
    const res = await authApi.requestEmailOtp(email);
    otpEmail.value = email;
    otpCode.value = "";
    step.value = "code";
    statusMessage.value = res.message;
    startCooldown(res.cooldown_seconds || 60);
  } catch (error) {
    errorMessage.value = (error as Error).message || "验证码发送失败";
  } finally {
    loading.value = false;
  }
}

async function handleVerifyOtp() {
  const code = otpCode.value.trim();
  if (!code) return;
  loading.value = true;
  errorMessage.value = "";
  try {
    await auth.loginWithEmailOtp(otpEmail.value, code);
    await redirectAfterAuth();
  } catch (error) {
    errorMessage.value = (error as Error).message || "验证码无效或已过期";
  } finally {
    loading.value = false;
  }
}

async function handleResendOtp() {
  if (cooldown.value > 0 || loading.value) return;
  await handleSendOtp();
}

async function handlePasswordLogin() {
  loading.value = true;
  errorMessage.value = "";
  try {
    await auth.login({ account: account.value, password: password.value });
    await redirectAfterAuth();
  } catch (error) {
    errorMessage.value = (error as Error).message || "账号或密码错误";
  } finally {
    loading.value = false;
  }
}

function switchStep(next: AuthStep) {
  errorMessage.value = "";
  statusMessage.value = "";
  step.value = next;
}

const googleAuthUrl = computed(() => authApi.getOAuthAuthorizeUrl("google"));
const githubAuthUrl = computed(() => authApi.getOAuthAuthorizeUrl("github"));

onMounted(async () => {
  const oauthErr = typeof route.query.oauth_error === "string" ? route.query.oauth_error.trim() : "";
  if (oauthErr) {
    errorMessage.value = oauthErr;
  }

  const magic = typeof route.query.magic_token === "string" ? route.query.magic_token.trim() : "";
  if (!magic) return;
  magicChecking.value = true;
  errorMessage.value = "";
  try {
    await auth.loginWithMagicToken(magic);
    await redirectAfterAuth();
  } catch (error) {
    errorMessage.value = (error as Error).message || "一键登录链接无效或已过期，请重新获取验证码";
  } finally {
    magicChecking.value = false;
  }
});

onBeforeUnmount(() => {
  if (cooldownTimer) {
    clearInterval(cooldownTimer);
    cooldownTimer = null;
  }
});
</script>

<template>
  <div class="grid min-h-dvh bg-background text-foreground lg:grid-cols-[1.05fr_1fr]">
    <AuthBrandPanel heading-tag="p" />

    <!-- 右侧：极简邮箱验证码 / 一键链接登录注册一体流 -->
    <section class="flex items-center justify-center bg-background px-4 py-10 sm:px-8">
      <div class="w-full max-w-[22rem]">
        <div class="mb-6 lg:hidden">
          <RouterLink
            to="/"
            aria-label="VeriPaper 首页"
            class="flex w-fit items-center gap-2 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-[#0F4C3A] dark:focus-visible:ring-ring"
          >
            <img src="/logo.svg" alt="" aria-hidden="true" class="size-7" />
            <span class="text-sm font-semibold tracking-tight">VeriPaper</span>
          </RouterLink>
        </div>

        <header>
          <h1
            data-test="auth-title"
            class="text-xl font-semibold tracking-tight text-foreground"
          >
            {{ heading.title }}
          </h1>
          <p data-test="auth-hint" class="mt-1 text-xs leading-relaxed text-muted-foreground">
            {{ heading.hint }}
          </p>
        </header>

        <div
          v-if="magicChecking"
          data-test="magic-checking"
          class="mt-6 flex items-center gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-5 text-sm text-muted-foreground"
        >
          <!-- Magic Link 自动核验态 -->
          <Loader2 class="size-4 animate-spin text-[#0F4C3A] dark:text-primary motion-reduce:animate-none" aria-hidden="true" />
          <span>正在验证一键登录链接…</span>
        </div>

        <div v-else-if="step === 'email'" class="mt-6 space-y-4">
          <!-- Step 1：OAuth 一键登录 + 单输入框邮箱登录注册一体流 -->
          <div class="grid gap-2">
            <a
              :href="googleAuthUrl"
              data-test="oauth-google-btn"
              class="inline-flex h-10 w-full items-center justify-center gap-2.5 rounded-lg border border-neutral-200 bg-card px-4 text-xs font-medium text-foreground transition-colors hover:bg-muted/60 focus-visible:outline-2 focus-visible:outline-[#0F4C3A] dark:border-input dark:focus-visible:outline-ring"
            >
              <svg class="size-4 shrink-0" viewBox="0 0 24 24" aria-hidden="true">
                <path
                  fill="#4285F4"
                  d="M23.745 12.27c0-.7-.06-1.4-.19-2.07H12v4.51h6.6c-.29 1.52-1.14 2.82-2.4 3.68v3.05h3.88c2.27-2.09 3.665-5.17 3.665-9.17Z"
                />
                <path
                  fill="#34A853"
                  d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.88-3.05c-1.08.72-2.45 1.16-4.05 1.16-3.12 0-5.77-2.11-6.72-4.96H1.29v3.14C3.26 21.3 7.31 24 12 24Z"
                />
                <path
                  fill="#FBBC05"
                  d="M5.28 14.24c-.24-.72-.38-1.49-.38-2.24s.14-1.52.38-2.24V6.62H1.29a11.98 11.98 0 0 0 0 10.76l3.99-3.14Z"
                />
                <path
                  fill="#EA4335"
                  d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.31 0 3.26 2.7 1.29 6.62l3.99 3.14c.95-2.85 3.6-4.96 6.72-4.96Z"
                />
              </svg>
              <span>使用 Google 账号继续</span>
            </a>

            <a
              :href="githubAuthUrl"
              data-test="oauth-github-btn"
              class="inline-flex h-10 w-full items-center justify-center gap-2.5 rounded-lg border border-neutral-200 bg-card px-4 text-xs font-medium text-foreground transition-colors hover:bg-muted/60 focus-visible:outline-2 focus-visible:outline-[#0F4C3A] dark:border-input dark:focus-visible:outline-ring"
            >
              <Github :size="15" aria-hidden="true" />
              <span>使用 GitHub 账号继续</span>
            </a>
          </div>

          <div class="relative flex items-center py-0.5">
            <div class="h-px flex-1 bg-border" />
            <span class="px-3 text-[11px] text-muted-foreground">或</span>
            <div class="h-px flex-1 bg-border" />
          </div>

          <form class="flex flex-col gap-3.5" @submit.prevent="handleSendOtp">
            <div class="grid gap-1.5">
              <Label for="otp-email" class="text-xs">邮箱地址</Label>
              <Input
                id="otp-email"
                v-model="otpEmail"
                data-test="otp-email-input"
                type="email"
                required
                autocomplete="email"
                placeholder="name@university.edu"
                class="h-10 rounded-lg border-neutral-200 focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input dark:focus-visible:border-ring dark:focus-visible:ring-ring/20"
              />
            </div>

            <ErrorBanner :message="errorMessage" @dismiss="errorMessage = ''" />

            <Button
              type="submit"
              data-test="otp-send-btn"
              :disabled="loading"
              class="h-10 w-full rounded-lg bg-[#0F4C3A] text-white hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
            >
              <Loader2 v-if="loading" class="animate-spin motion-reduce:animate-none" aria-hidden="true" />
              <span>{{ loading ? "发送中…" : "继续" }}</span>
              <ArrowRight v-if="!loading" :size="14" aria-hidden="true" />
            </Button>
          </form>
        </div>

        <form
          v-else-if="step === 'code'"
          class="mt-6 flex flex-col gap-3.5"
          @submit.prevent="handleVerifyOtp"
        >
          <!-- Step 2：输入 6 位验证码 -->
          <div
            v-if="statusMessage"
            data-test="otp-sent-notice"
            class="flex items-start gap-2 rounded-lg border border-[#0F4C3A]/20 bg-[#0F4C3A]/[0.05] px-3 py-2.5 text-xs text-[#0F4C3A] dark:border-success/30 dark:bg-success/10 dark:text-success"
          >
            <MailCheck :size="14" class="mt-0.5 shrink-0" aria-hidden="true" />
            <span>{{ statusMessage }}</span>
          </div>

          <div class="grid gap-1.5">
            <Label for="otp-code" class="text-xs">6 位数字验证码</Label>
            <Input
              id="otp-code"
              v-model="otpCode"
              data-test="otp-code-input"
              type="text"
              inputmode="numeric"
              autocomplete="one-time-code"
              required
              minlength="6"
              maxlength="6"
              placeholder="请输入 6 位验证码"
              class="h-10 rounded-lg border-neutral-200 font-mono text-base tracking-[0.35em] focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input dark:focus-visible:border-ring dark:focus-visible:ring-ring/20"
            />
          </div>

          <ErrorBanner :message="errorMessage" @dismiss="errorMessage = ''" />

          <Button
            type="submit"
            data-test="otp-verify-btn"
            :disabled="loading"
            class="h-10 w-full rounded-lg bg-[#0F4C3A] text-white hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
          >
            <Loader2 v-if="loading" class="animate-spin motion-reduce:animate-none" aria-hidden="true" />
            <span>{{ loading ? "验证中…" : "验证并进入工作台" }}</span>
            <ArrowRight v-if="!loading" :size="14" aria-hidden="true" />
          </Button>

          <div class="flex items-center justify-between text-xs">
            <button
              type="button"
              data-test="otp-change-email"
              class="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
              @click="switchStep('email')"
            >
              <ArrowLeft :size="12" aria-hidden="true" />
              更换邮箱
            </button>
            <button
              type="button"
              data-test="otp-resend-btn"
              :disabled="cooldown > 0 || loading"
              class="min-w-[6.5rem] text-right tabular-nums text-muted-foreground hover:text-foreground disabled:opacity-50"
              @click="handleResendOtp"
            >
              {{ cooldown > 0 ? `${cooldown}s 后可重发` : "重新发送验证码" }}
            </button>
          </div>
        </form>

        <form
          v-else
          class="mt-6 flex flex-col gap-3.5"
          @submit.prevent="handlePasswordLogin"
        >
          <!-- 密码登录兜底（兼容管理员或老密码账号） -->
          <div class="grid gap-1.5">
            <Label for="account" class="text-xs">用户名 / 邮箱</Label>
            <Input
              id="account"
              v-model="account"
              type="text"
              required
              autocomplete="username"
              placeholder="请输入用户名或邮箱"
              class="h-10 rounded-lg border-neutral-200 focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input dark:focus-visible:border-ring dark:focus-visible:ring-ring/20"
            />
          </div>

          <div class="grid gap-1.5">
            <Label for="password" class="text-xs">密码</Label>
            <Input
              id="password"
              v-model="password"
              type="password"
              required
              placeholder="请输入密码"
              autocomplete="current-password"
              class="h-10 rounded-lg border-neutral-200 focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input dark:focus-visible:border-ring dark:focus-visible:ring-ring/20"
            />
          </div>

          <ErrorBanner :message="errorMessage" @dismiss="errorMessage = ''" />

          <Button
            type="submit"
            :disabled="loading"
            class="h-10 w-full rounded-lg bg-[#0F4C3A] text-white hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
          >
            <Loader2 v-if="loading" class="animate-spin motion-reduce:animate-none" aria-hidden="true" />
            <span>{{ loading ? "登录中…" : "登录" }}</span>
            <ArrowRight v-if="!loading" :size="14" aria-hidden="true" />
          </Button>

          <div class="flex items-center justify-between text-xs">
            <button
              type="button"
              data-test="back-to-otp"
              class="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
              @click="switchStep('email')"
            >
              <ArrowLeft :size="12" aria-hidden="true" />
              返回邮箱验证码登录
            </button>
            <RouterLink
              :to="{ name: 'password-reset' }"
              data-test="forgot-password"
              class="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            >
              忘记密码？
            </RouterLink>
          </div>
        </form>

        <!-- 底部密码登录切换与安全声明 -->
        <div class="mt-6 border-t border-border pt-4 text-center">
          <button
            v-if="step !== 'password'"
            type="button"
            data-test="toggle-password-login"
            class="inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
            @click="switchStep('password')"
          >
            <KeyRound :size="12" aria-hidden="true" />
            使用账号密码登录
          </button>
          <p class="mt-3 flex items-center justify-center gap-1.5 text-[11px] text-muted-foreground">
            <LockKeyhole :size="12" aria-hidden="true" />
            你的研究数据仅对当前工作空间可见
          </p>
        </div>
      </div>
    </section>
  </div>
</template>
