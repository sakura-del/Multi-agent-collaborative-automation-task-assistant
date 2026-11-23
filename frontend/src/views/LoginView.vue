<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ArrowRight, Check, Loader2, LockKeyhole, MailCheck } from "lucide-vue-next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Alert } from "@/components/ui/alert";
import "altcha"; // 注册 <altcha-widget>（ALTCHA PoW 自托管，零第三方请求）
import "altcha/i18n/zh-cn"; // 界面文案中文（组件只自带 en）

import { requestEmailVerification } from "@/api/account";
import api from "@/api/client";
import { useAuthStore } from "@/stores/auth";

// 登录/注册页。2026-11-13 起邮箱验证是登录门禁（EMAIL__REQUIRE_VERIFIED_LOGIN）：
// - 注册成功 → 不再自动进系统，改为引导去邮箱点验证链接（本页直接提供重发）；
// - 登录遇 403 EMAIL_NOT_VERIFIED → 展示未验证提示 + 重发按钮，而不是让用户反复试密码。
// 视觉采用「学术编辑感」：衬线标题 + 正文证据示例，直接表达产品的差异化。
const auth = useAuthStore();
const route = useRoute();
const router = useRouter();

const mode = ref<"login" | "register">("login");
const account = ref("");
const username = ref("");
const email = ref("");
const password = ref("");
const loading = ref(false);
const errorMessage = ref("");
// 注册成功后待验证的邮箱；非空时显示「查收邮件」引导卡
const pendingVerificationEmail = ref("");
// 登录被门禁拦下时后端回传的邮箱（密码已验证，给本人无碍）
const unverifiedEmail = ref("");
const resending = ref(false);

// 注册人机验证（ALTCHA）：启动时探测一次后端是否启用，避免给国内用户渲染无用的空组件
const captchaEnabled = ref(false);
const captchaSolved = ref(false);
const captchaPayload = ref("");
const captchaError = ref("");
// widget 自己 fetch 挑战（v3 的 `challenge` 属性接受 URL），必须给绝对地址
// （相对路径会打到 Pages 域名 → 拿到 index.html）
const captchaChallengeUrl = computed(
  () => `${api.defaults.baseURL ?? ""}/auth/captcha/challenge`,
);

function onCaptchaStateChange(event: Event) {
  const detail = (event as CustomEvent<{ state: string; payload?: string }>).detail;
  captchaSolved.value = detail?.state === "verified";
  captchaPayload.value = detail?.state === "verified" ? (detail.payload ?? "") : "";
  captchaError.value = detail?.state === "error" ? "人机验证加载失败，请刷新页面重试" : "";
}

onMounted(async () => {
  try {
    // 用统一 axios 实例：baseURL 指向 API 子域（相对路径会打到 Pages 域名 → 404）
    const { data } = await api.get<{ enabled?: boolean }>("/auth/captcha/challenge");
    captchaEnabled.value = data.enabled === true;
  } catch {
    // 探测失败按「不启用」处理：宁可少一层防护，也不能让注册页打不开
    captchaEnabled.value = false;
  }
});

const submitLabel = computed(() => (mode.value === "login" ? "登录" : "注册并登录"));

/** 页脚能力点：三条都对应系统里真实存在的功能，不是营销词。 */
const CAPABILITIES = ["证据约束", "引用可核验", "人机协同"] as const;

async function handleSubmit() {
  loading.value = true;
  errorMessage.value = "";
  pendingVerificationEmail.value = "";
  unverifiedEmail.value = "";
  try {
    if (mode.value === "register") {
      if (captchaEnabled.value && !captchaSolved.value) {
        errorMessage.value = "请先完成人机验证";
        return;
      }
      await auth.register({
        username: username.value,
        email: email.value,
        password: password.value,
        ...(captchaEnabled.value ? { captcha: captchaPayload.value } : {}),
      });
      // 注册成功 ≠ 进系统：邮箱验证链接已发出，验证后再登录
      pendingVerificationEmail.value = email.value;
      mode.value = "login";
      account.value = email.value;
      return;
    }
    await auth.login({ account: account.value, password: password.value });
    const redirect = route.query.redirect;
    await router.push(typeof redirect === "string" ? redirect : { name: "tasks" });
  } catch (error) {
    const apiError = error as Error & {
      code?: string;
      details?: { email?: string };
    };
    if (apiError.code === "EMAIL_NOT_VERIFIED") {
      unverifiedEmail.value = apiError.details?.email ?? "";
      errorMessage.value = "";
    } else {
      errorMessage.value = apiError.message;
    }
  } finally {
    loading.value = false;
  }
}

/** 重发验证邮件。后端响应恒定（防邮箱枚举），总是展示同样的成功文案。 */
async function handleResend() {
  const target = unverifiedEmail.value || pendingVerificationEmail.value;
  if (!target) return;
  resending.value = true;
  try {
    await requestEmailVerification(target);
    errorMessage.value = "";
  } catch (error) {
    errorMessage.value = (error as Error).message;
  } finally {
    resending.value = false;
  }
}
</script>

<template>
  <div class="grid min-h-dvh bg-background text-foreground lg:grid-cols-[1.05fr_1fr]">
    <!-- 左侧：Notion / Linear 极简产品声明与单卡证据示例（窄屏隐藏） -->
    <section
      class="hidden flex-col justify-between border-r border-border bg-sidebar px-10 py-8 lg:flex xl:px-14"
    >
      <div class="flex items-center gap-2.5">
        <img src="/logo.svg" alt="" aria-hidden="true" class="size-7" />
        <span class="text-sm font-semibold tracking-tight text-foreground">VeriPaper</span>
      </div>

      <div class="my-auto max-w-lg py-8">
        <p class="text-[11px] font-medium tracking-wider text-muted-foreground uppercase">
          Evidence-led Research Workspace
        </p>
        <h1 class="mt-3 text-3xl leading-tight font-semibold tracking-tight text-foreground xl:text-4xl">
          让每一句论断，都有迹可循。
        </h1>
        <p class="mt-3 text-sm leading-relaxed text-muted-foreground">
          从文献检索、证据筛选到综述成稿，多智能体协同完成研究流程。你负责判断，系统负责让每条引用都能回到原始证据。
        </p>

        <!-- 静态产品示例卡片：1px 极简边框，无斜置层叠 -->
        <div
          data-test="login-evidence-proof"
          class="mt-8 rounded-lg border border-border bg-card p-4"
        >
          <div class="flex items-center justify-between gap-2 border-b border-border pb-2.5 text-xs">
            <span class="font-medium text-muted-foreground">正文证据片段 · 示例</span>
            <span class="inline-flex items-center gap-1 font-medium text-success">
              <Check :size="12" :stroke-width="2.2" aria-hidden="true" />
              引用核验通过
            </span>
          </div>
          <p class="mt-3 font-display text-sm leading-7 text-card-foreground">
            检索增强生成的评估正从单一答案准确率，转向对忠实度、证据完整性与上下文利用效率的综合判断。
          </p>
          <div class="mt-3 flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
            <span>GB/T 7714—2025 · 顺序编码制</span>
            <span class="text-primary">证据可回溯</span>
          </div>
        </div>
      </div>

      <div class="flex flex-wrap gap-x-5 gap-y-2 text-[11px] text-muted-foreground">
        <span v-for="item in CAPABILITIES" :key="item">{{ item }}</span>
        <span>Markdown / DOCX</span>
      </div>
    </section>

    <!-- 右侧：极简居中登录/注册表单 -->
    <section class="flex items-center justify-center bg-background px-4 py-10 sm:px-8">
      <div class="w-full max-w-[22rem]">
        <div class="mb-7 lg:hidden">
          <div class="mb-4 flex items-center gap-2">
            <img src="/logo.svg" alt="" aria-hidden="true" class="size-7" />
            <span class="text-sm font-semibold tracking-tight">VeriPaper</span>
          </div>
          <h1 class="text-2xl font-semibold tracking-tight">
            让每一句论断，都有迹可循。
          </h1>
        </div>

        <div class="hidden lg:block">
          <h2 class="text-xl font-semibold tracking-tight">继续你的研究</h2>
          <p class="mt-1 text-xs text-muted-foreground">登录以访问研究任务、知识库与引用库。</p>
        </div>

        <form class="mt-6 flex flex-col gap-3.5" @submit.prevent="handleSubmit">
          <div class="grid grid-cols-2 gap-1 rounded-md border border-border bg-muted p-0.5" role="tablist">
            <button
              v-for="option in [
                { value: 'login', label: '登录' },
                { value: 'register', label: '注册' },
              ] as const"
              :key="option.value"
              type="button"
              role="tab"
              :aria-selected="mode === option.value"
              :class="[
                'rounded-sm px-3 py-1.5 text-xs font-medium transition-colors',
                mode === option.value
                  ? 'bg-background text-foreground'
                  : 'text-muted-foreground hover:text-foreground',
              ]"
              @click="mode = option.value"
            >
              {{ option.label }}
            </button>
          </div>

          <div v-if="mode === 'login'" class="grid gap-1.5">
            <Label for="account" class="text-xs">用户名 / 邮箱</Label>
            <Input
              id="account"
              v-model="account"
              type="text"
              required
              autocomplete="username"
              placeholder="请输入用户名或邮箱"
              class="h-9"
            />
          </div>
          <template v-else>
            <div class="grid gap-1.5">
              <Label for="username" class="text-xs">用户名</Label>
              <Input
                id="username"
                v-model="username"
                type="text"
                required
                minlength="3"
                autocomplete="username"
                class="h-9"
              />
            </div>
            <div class="grid gap-1.5">
              <Label for="email" class="text-xs">邮箱</Label>
              <Input
                id="email"
                v-model="email"
                type="email"
                required
                autocomplete="email"
                class="h-9"
              />
            </div>
          </template>

          <div class="grid gap-1.5">
            <Label for="password" class="text-xs">密码</Label>
            <Input
              id="password"
              v-model="password"
              type="password"
              required
              minlength="8"
              placeholder="至少 8 位"
              :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
              class="h-9"
            />
          </div>

          <Alert
            v-if="pendingVerificationEmail"
            variant="success"
            class="mb-1"
            data-test="register-pending-verification"
          >
            <MailCheck />
            <span>
              验证邮件已发送至
              <strong>{{ pendingVerificationEmail }}</strong>
              。请点击邮件里的链接完成验证，再回来登录。
            </span>
          </Alert>
          <div
            v-if="unverifiedEmail"
            class="rounded-md border border-warning/40 bg-warning/10 p-3 text-xs"
            data-test="login-unverified"
          >
            <p class="font-medium">邮箱还没验证</p>
            <p class="mt-1 text-muted-foreground">
              我们已向
              <strong>{{ unverifiedEmail }}</strong>
              发送了验证邮件，点击邮件里的链接后即可登录。
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              class="mt-2"
              data-test="resend-verification"
              :disabled="resending"
              @click="handleResend"
            >
              <Loader2 v-if="resending" class="size-3.5 animate-spin" aria-hidden="true" />
              <MailCheck v-else :size="13" aria-hidden="true" />
              {{ resending ? "发送中…" : "重新发送验证邮件" }}
            </Button>
          </div>

          <div v-if="mode === 'register' && captchaEnabled" class="grid gap-1.5">
            <altcha-widget
              :challenge="captchaChallengeUrl"
              auto="onload"
              language="zh-cn"
              hideFooter
              @statechange="onCaptchaStateChange"
            />
            <p v-if="captchaError" class="text-xs text-destructive">{{ captchaError }}</p>
          </div>

          <ErrorBanner :message="errorMessage" />

          <Button
            type="submit"
            :disabled="loading || (mode === 'register' && captchaEnabled && !captchaSolved)"
            class="h-9 w-full"
          >
            <Loader2 v-if="loading" class="animate-spin" aria-hidden="true" />
            <span>{{ loading ? "处理中…" : submitLabel }}</span>
            <ArrowRight v-if="!loading" :size="14" aria-hidden="true" />
          </Button>

          <p v-if="mode === 'login'" class="text-center text-xs">
            <RouterLink
              :to="{ name: 'password-reset' }"
              data-test="forgot-password"
              class="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            >
              忘记密码？
            </RouterLink>
          </p>
        </form>

        <p class="mt-8 flex items-center justify-center gap-1.5 text-[11px] text-muted-foreground">
          <LockKeyhole :size="12" aria-hidden="true" />
          你的研究数据仅对当前工作空间可见
        </p>
      </div>
    </section>
  </div>
</template>
