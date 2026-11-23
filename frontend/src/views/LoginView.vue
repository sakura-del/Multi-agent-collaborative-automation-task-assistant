<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  KeyRound,
  Loader2,
  LockKeyhole,
  MailCheck,
  ShieldCheck,
} from "lucide-vue-next";

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

const LOGIN_PROOFS = {
  1: {
    source: "ACL Findings 2025 · Section 5.1",
    badge: "Crossref DOI 已核验",
    citation: "GB/T 7714—2025 · LIN Y, CHEN W, PATEL R. Evaluating faithfulness and citation grounding...",
  },
  2: {
    source: "J. Biomed. Inform. 2025 · Section 3.3",
    badge: "PubMed / Crossref 已核验",
    citation: "GB/T 7714—2025 · ZHOU H, MILLER K, SATO T. Automated PRISMA 2020 screening...",
  },
} as const;

const activeProofIndex = ref<1 | 2>(1);
const activeProof = computed(() => LOGIN_PROOFS[activeProofIndex.value]);

const TRUST_ITEMS = ["arXiv", "PubMed", "OpenAlex", "PRISMA 2020", "GB/T 7714-2025"] as const;

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

onMounted(async () => {
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
    <!-- 左侧：Elicit 风格学术点阵底纹 + 双层可交互溯源卡（窄屏隐藏） -->
    <section
      class="relative hidden flex-col justify-between overflow-hidden border-r border-[#E5E7EB] bg-[#FAF9F6] px-10 py-8 lg:flex xl:px-14 dark:border-border dark:bg-sidebar"
    >
      <div
        class="pointer-events-none absolute inset-0 bg-[radial-gradient(#d6d3d1_1px,transparent_1px)] [background-size:24px_24px] [mask-image:radial-gradient(ellipse_at_center,black_45%,transparent_100%)] opacity-70 dark:opacity-15"
        aria-hidden="true"
      />

      <RouterLink
        to="/"
        data-test="brand-home"
        aria-label="VeriPaper 首页"
        class="relative flex w-fit items-center gap-2.5 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-[#0F4C3A]"
      >
        <img src="/logo.svg" alt="" aria-hidden="true" class="size-7" />
        <span class="text-sm font-semibold tracking-tight text-[#111827] dark:text-foreground">VeriPaper</span>
      </RouterLink>

      <div class="relative my-auto max-w-lg py-8">
        <h1 class="font-display text-4xl leading-[1.24] font-semibold tracking-tight xl:text-[2.75rem]">
          <span class="block text-[#111827] dark:text-foreground">每一句论断</span>
          <span class="block text-[#0F4C3A] dark:text-primary">皆有出处</span>
        </h1>
        <p class="mt-4 max-w-md text-sm leading-7 text-[#4B5563] dark:text-muted-foreground">你负责判断，系统负责把每一条引用锚定回原始文献。</p>

        <!-- 双层可交互溯源证据卡 -->
        <div
          data-test="login-evidence-proof"
          class="mt-8 overflow-hidden rounded-xl border border-[#E5E7EB] bg-white shadow-[0_12px_32px_-16px_rgba(15,76,58,0.12)] dark:border-border dark:bg-card"
        >
          <div class="p-4 sm:p-5">
            <div class="flex items-center justify-between gap-2 text-[11px] text-[#6B7280] dark:text-muted-foreground">
              <span class="font-medium">综述正文片段 · 点击角标切换溯源</span>
              <span
                data-test="proof-traceable-tag"
                class="inline-flex items-center gap-1 rounded-full border border-[#E5E7EB] bg-[#FAF9F6] px-2 py-0.5 text-[10px] text-[#4B5563] dark:border-border dark:bg-muted dark:text-muted-foreground"
              >
                <ShieldCheck :size="11" aria-hidden="true" />
                证据可回溯
              </span>
            </div>
            <p class="mt-3 font-display text-sm leading-7 text-[#1F2937] dark:text-card-foreground">
              检索增强生成的评估正从单一答案准确率，转向对忠实度、证据完整性与上下文利用效率的综合判断<button
                type="button"
                data-test="login-proof-marker-1"
                :aria-pressed="activeProofIndex === 1"
                class="mx-0.5 inline-flex items-center rounded px-1.5 py-0.2 align-baseline font-sans text-xs font-semibold transition-colors"
                :class="
                  activeProofIndex === 1
                    ? 'bg-[#0F4C3A] text-white dark:bg-primary dark:text-primary-foreground'
                    : 'bg-[#0F4C3A]/10 text-[#0F4C3A] hover:bg-[#0F4C3A]/20 dark:bg-primary/15 dark:text-primary'
                "
                @click="activeProofIndex = 1"
              >[1]</button>；而在多智能体筛选阶段，结构化审议协议将人工复核工时降低了 68.2%<button
                type="button"
                data-test="login-proof-marker-2"
                :aria-pressed="activeProofIndex === 2"
                class="mx-0.5 inline-flex items-center rounded px-1.5 py-0.2 align-baseline font-sans text-xs font-semibold transition-colors"
                :class="
                  activeProofIndex === 2
                    ? 'bg-[#0F4C3A] text-white dark:bg-primary dark:text-primary-foreground'
                    : 'bg-[#0F4C3A]/10 text-[#0F4C3A] hover:bg-[#0F4C3A]/20 dark:bg-primary/15 dark:text-primary'
                "
                @click="activeProofIndex = 2"
              >[2]</button>。
            </p>
          </div>

          <div
            data-test="login-proof-footer"
            class="border-t border-[#E5E7EB] bg-[#F9FAFB] px-4 py-3 text-xs dark:border-border dark:bg-muted/40"
          >
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="font-medium text-[#374151] dark:text-foreground">
                [{{ activeProofIndex }}] {{ activeProof.source }}
              </span>
              <span class="inline-flex items-center gap-1 rounded-full bg-[#0F4C3A]/10 px-2 py-0.5 text-[11px] font-medium text-[#0F4C3A] dark:bg-success/15 dark:text-success">
                <Check :size="11" :stroke-width="2.4" aria-hidden="true" />
                {{ activeProof.badge }}
              </span>
            </div>
            <p class="mt-1.5 truncate font-mono text-[10.5px] text-[#6B7280] dark:text-muted-foreground">
              {{ activeProof.citation }}
            </p>
          </div>
        </div>
      </div>

      <div class="relative flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-neutral-500 dark:text-muted-foreground">
        <template v-for="(item, idx) in TRUST_ITEMS" :key="item">
          <span>{{ item }}</span>
          <span v-if="idx < TRUST_ITEMS.length - 1" aria-hidden="true">·</span>
        </template>
      </div>
    </section>

    <!-- 右侧：极简邮箱验证码 / 一键链接登录注册一体流 -->
    <section class="flex items-center justify-center bg-background px-4 py-10 sm:px-8">
      <div class="w-full max-w-[22rem]">
        <div class="mb-6 lg:hidden">
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

        <!-- Magic Link 自动核验态 -->
        <div
          v-if="magicChecking"
          data-test="magic-checking"
          class="mt-6 flex items-center gap-2.5 rounded-lg border border-border bg-muted/40 px-4 py-5 text-sm text-muted-foreground"
        >
          <Loader2 class="size-4 animate-spin text-[#0F4C3A]" aria-hidden="true" />
          <span>正在验证一键登录链接…</span>
        </div>

        <!-- Step 1：单输入框邮箱登录注册一体流 -->
        <form
          v-else-if="step === 'email'"
          class="mt-6 flex flex-col gap-3.5"
          @submit.prevent="handleSendOtp"
        >
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
              class="h-10 rounded-lg border-neutral-200 focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input"
            />
          </div>

          <ErrorBanner :message="errorMessage" />

          <Button
            type="submit"
            data-test="otp-send-btn"
            :disabled="loading"
            class="h-10 w-full rounded-lg bg-[#0F4C3A] text-white hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
          >
            <Loader2 v-if="loading" class="animate-spin" aria-hidden="true" />
            <span>{{ loading ? "发送中…" : "继续" }}</span>
            <ArrowRight v-if="!loading" :size="14" aria-hidden="true" />
          </Button>
        </form>

        <!-- Step 2：输入 6 位验证码 -->
        <form
          v-else-if="step === 'code'"
          class="mt-6 flex flex-col gap-3.5"
          @submit.prevent="handleVerifyOtp"
        >
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
              class="h-10 rounded-lg border-neutral-200 font-mono text-base tracking-[0.35em] focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input"
            />
          </div>

          <ErrorBanner :message="errorMessage" />

          <Button
            type="submit"
            data-test="otp-verify-btn"
            :disabled="loading"
            class="h-10 w-full rounded-lg bg-[#0F4C3A] text-white hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
          >
            <Loader2 v-if="loading" class="animate-spin" aria-hidden="true" />
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
              class="text-muted-foreground hover:text-foreground disabled:opacity-50"
              @click="handleResendOtp"
            >
              {{ cooldown > 0 ? `${cooldown}s 后可重发` : "重新发送验证码" }}
            </button>
          </div>
        </form>

        <!-- 密码登录兜底（兼容管理员或老密码账号） -->
        <form
          v-else
          class="mt-6 flex flex-col gap-3.5"
          @submit.prevent="handlePasswordLogin"
        >
          <div class="grid gap-1.5">
            <Label for="account" class="text-xs">用户名 / 邮箱</Label>
            <Input
              id="account"
              v-model="account"
              type="text"
              required
              autocomplete="username"
              placeholder="请输入用户名或邮箱"
              class="h-10 rounded-lg border-neutral-200 focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input"
            />
          </div>

          <div class="grid gap-1.5">
            <Label for="password" class="text-xs">密码</Label>
            <Input
              id="password"
              v-model="password"
              type="password"
              required
              minlength="8"
              placeholder="至少 8 位"
              autocomplete="current-password"
              class="h-10 rounded-lg border-neutral-200 focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input"
            />
          </div>

          <ErrorBanner :message="errorMessage" />

          <Button
            type="submit"
            :disabled="loading"
            class="h-10 w-full rounded-lg bg-[#0F4C3A] text-white hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
          >
            <Loader2 v-if="loading" class="animate-spin" aria-hidden="true" />
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
