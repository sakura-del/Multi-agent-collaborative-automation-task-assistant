<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  ArrowRight,
  Check,
  Loader2,
  LockKeyhole,
  MailCheck,
  ShieldCheck,
} from "lucide-vue-next";

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

type AuthMode = "login" | "register";

// 页签即路由（Issue #26-2）：`/register` 可直达、可分享，浏览器返回键也能回到上一个页签。
// 单一事实来源是 URL —— 组件用同一份实例在两个路由间复用，只有路由能同时喂给
// 顶部文案、表单字段和地址栏，避免「地址栏说 login、页签说 register」。
const mode = computed<AuthMode>({
  get: () => (route.name === "register" ? "register" : "login"),
  set: (next) => {
    if (route.name === next) return;
    void router.replace({ name: next, query: route.query });
  },
});

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
// 页脚（「由 ALTCHA 保护」）由统一的一行自己文案替代，见模板里的说明
const altchaConfiguration = JSON.stringify({ hideFooter: true });

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

/**
 * 顶部文案随页签切换（Issue #26-1）。
 * 注册态若沿用登录文案，新用户第一眼看到的是「继续你的研究 / 登录以…」，
 * 与刚刚点下的「注册」意图相反 —— 这里是纯文案对象，两个态各自说自己的事。
 */
const HEADINGS: Record<AuthMode, { title: string; hint: string }> = {
  login: {
    title: "回到工作台",
    hint: "访问你的文献库、筛选记录与综述文稿",
  },
  register: {
    title: "创建研究账号",
    hint: "开始你的第一篇可核验文献综述",
  },
};
const heading = computed(() => HEADINGS[mode.value]);

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

// 注册不再自动登录（邮箱验证是门禁），按钮文案不能再说「注册并登录」
const submitLabel = computed(() => (mode.value === "login" ? "登录" : "创建账号"));

/** 切页签：清掉上一个态的密码与提示，避免把登录口令带进注册表单。 */
function switchMode(next: AuthMode) {
  if (next === mode.value) return;
  password.value = "";
  errorMessage.value = "";
  unverifiedEmail.value = "";
  // 离开注册态就把人机验证结果丢掉：widget 会被 v-if 卸载，旧 payload 一次性、必然失效
  captchaSolved.value = false;
  captchaPayload.value = "";
  captchaError.value = "";
  mode.value = next;
}

/** 底部学术背书条 */
const TRUST_ITEMS = ["arXiv", "PubMed", "OpenAlex", "PRISMA 2020", "GB/T 7714-2025"] as const;

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
      // 回到登录态（路由同步成 /login）；人机验证已用过，payload 一次性，不能留
      captchaSolved.value = false;
      captchaPayload.value = "";
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
    <!-- 左侧：Elicit 风格学术点阵底纹 + 双层可交互溯源卡（窄屏隐藏） -->
    <section
      class="relative hidden flex-col justify-between overflow-hidden border-r border-[#E5E7EB] bg-[#FAF9F6] px-10 py-8 lg:flex xl:px-14 dark:border-border dark:bg-sidebar"
    >
      <!-- 学术方格点阵纹理 + 边缘淡出遮罩 -->
      <div
        class="pointer-events-none absolute inset-0 bg-[radial-gradient(#d6d3d1_1px,transparent_1px)] [background-size:24px_24px] [mask-image:radial-gradient(ellipse_at_center,black_45%,transparent_100%)] opacity-70 dark:opacity-15"
        aria-hidden="true"
      />

      <!-- Logo 是链接（Issue #26-5）：点击回到公开落地页首页 -->
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
        <!-- 中文正文不要跨行折在源码里：Vue 会把文本节点里的换行压成一个空格（Issue #26-4） -->
        <p class="mt-4 max-w-md text-sm leading-7 text-[#4B5563] dark:text-muted-foreground">你负责判断，系统负责把每一条引用锚定回原始文献。</p>

        <!-- 双层可交互溯源证据卡：上白正文（含 [1]/[2] 角标）+ 下浅灰溯源底栏 -->
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

          <!-- 溯源底栏 -->
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

    <!-- 右侧：极简居中登录/注册表单 -->
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
          <p data-test="auth-hint" class="mt-1 text-xs text-muted-foreground">{{ heading.hint }}</p>
        </header>

        <form class="mt-6 flex flex-col gap-3.5" @submit.prevent="handleSubmit">
          <div class="grid grid-cols-2 gap-1 rounded-lg border border-neutral-200 bg-muted p-0.5 dark:border-border" role="tablist">
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
                'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                mode === option.value
                  ? 'bg-background text-foreground shadow-2xs'
                  : 'text-muted-foreground hover:text-foreground',
              ]"
              @click="switchMode(option.value)"
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
              class="h-9 rounded-lg border-neutral-200 focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input"
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
                placeholder="请输入用户名（3 位以上）"
                class="h-9 rounded-lg border-neutral-200 focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input"
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
                placeholder="you@example.com"
                class="h-9 rounded-lg border-neutral-200 focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input"
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
              class="h-9 rounded-lg border-neutral-200 focus-visible:border-[#0F4C3A] focus-visible:ring-1 focus-visible:ring-[#0F4C3A]/20 focus-visible:outline-none dark:border-input"
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
            <!--
              ⚠️ v3 的属性是 `challenge`（URL 或 JSON），关页脚只能走 `configuration`。
            -->
            <altcha-widget
              :challenge="captchaChallengeUrl"
              :configuration="altchaConfiguration"
              auto="onload"
              language="zh-cn"
              @statechange="onCaptchaStateChange"
            />
            <!--
              统一由这一行给文案（Issue #26-6）：widget 自带页脚走语言包，
              中文包里是「由 ALTCHA 保护」，和别处大小写不一致；hideFooter 后只剩这一处。
            -->
            <p
              data-test="captcha-caption"
              class="flex items-center gap-1 text-[11px] text-muted-foreground"
            >
              <ShieldCheck :size="12" aria-hidden="true" />
              由 Altcha 保护
            </p>
            <p v-if="captchaError" class="text-xs text-destructive">{{ captchaError }}</p>
          </div>

          <ErrorBanner :message="errorMessage" />

          <Button
            type="submit"
            :disabled="loading || (mode === 'register' && captchaEnabled && !captchaSolved)"
            class="h-9 w-full bg-[#0F4C3A] text-white hover:bg-[#135D47] dark:bg-primary dark:text-primary-foreground dark:hover:bg-primary/90"
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
