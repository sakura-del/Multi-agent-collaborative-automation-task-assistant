<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ArrowRight, BookMarked, Check, Loader2, LockKeyhole } from "lucide-vue-next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { useAuthStore } from "@/stores/auth";

// 登录/注册页：成功后回到 redirect 指向的页面（默认任务列表）。
// 视觉采用「学术编辑感」：衬线标题 + 正文证据示例，直接表达产品的差异化。
// 所有认证逻辑、字段 id 和账号生命周期入口保持既有契约。
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

const submitLabel = computed(() => (mode.value === "login" ? "登录" : "注册并登录"));

/** 页脚能力点：三条都对应系统里真实存在的功能，不是营销词。 */
const CAPABILITIES = ["证据约束", "引用可核验", "人机协同"] as const;

async function handleSubmit() {
  loading.value = true;
  errorMessage.value = "";
  try {
    if (mode.value === "register") {
      await auth.register({
        username: username.value,
        email: email.value,
        password: password.value,
      });
    } else {
      await auth.login({ account: account.value, password: password.value });
    }
    const redirect = route.query.redirect;
    await router.push(typeof redirect === "string" ? redirect : { name: "tasks" });
  } catch (error) {
    errorMessage.value = (error as Error).message;
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div data-test="login-air-surface" class="grid min-h-dvh bg-air-surface lg:grid-cols-[1.15fr_1fr]">
    <!-- 左侧：产品身份与一张明确标注的证据示例。窄屏整栏隐藏，避免挤压表单。 -->
    <section
      class="relative hidden flex-col overflow-hidden border-r border-border bg-surface-brand px-10 py-8 lg:flex xl:px-14 xl:py-10"
    >
      <div
        class="pointer-events-none absolute top-32 right-12 h-28 w-px bg-air-primary/25"
        aria-hidden="true"
      />
      <div class="relative flex items-center gap-2.5">
        <span
          class="flex size-9 items-center justify-center rounded-lg bg-foreground text-background"
          aria-hidden="true"
        >
          <BookMarked :size="18" :stroke-width="1.8" />
        </span>
        <div>
          <p class="text-sm font-semibold tracking-tight">文献综述工作台</p>
          <p class="mt-0.5 text-[8px] tracking-[0.18em] text-muted-foreground">
            EVIDENCE-LED WRITING
          </p>
        </div>
      </div>

      <div class="relative my-auto max-w-2xl py-10">
        <p class="text-[10px] font-semibold tracking-[0.2em] text-air-primary uppercase">
          Research, grounded in evidence
        </p>
        <h1 class="mt-5 font-display text-5xl leading-[1.3] font-medium tracking-[-0.045em] xl:text-6xl">
          让每一句论断，<br />都<span class="text-air-primary">有迹可循</span>。
        </h1>
        <p class="mt-6 max-w-lg text-sm leading-7 text-muted-foreground">
          从文献检索、证据筛选到综述成稿，多智能体协同完成研究流程。你负责判断，
          系统负责让每条引用都能回到原始证据。
        </p>

        <!-- 这里只是登录页的静态产品示例，明确标注，绝不冒充真实任务核验结果。 -->
        <div
          data-test="login-evidence-proof"
          class="relative mt-10 max-w-xl rounded-2xl border border-air-accent bg-card/85 p-5 shadow-raised"
        >
          <div
            class="pointer-events-none absolute -right-2 -bottom-2 -z-10 h-full w-full rotate-[-1deg] rounded-xl border border-border bg-card/35"
            aria-hidden="true"
          />
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-3 text-xs">
            <span class="font-medium text-muted-foreground">正文证据片段 · 示例</span>
            <span class="inline-flex items-center gap-1 text-success">
              <Check :size="13" :stroke-width="2.2" aria-hidden="true" />
              引用核验通过
            </span>
          </div>
          <p class="mt-4 font-display text-sm leading-7 text-card-foreground">
            检索增强生成的评估正从单一答案准确率，转向对忠实度、证据完整性与上下文利用效率的综合判断。
          </p>
          <div class="mt-4 flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
            <span>GB/T 7714—2025 · 顺序编码制</span>
            <span class="text-air-primary">证据可回溯</span>
          </div>
        </div>
      </div>

      <div class="relative flex flex-wrap gap-x-6 gap-y-2 text-[10px] text-muted-foreground">
        <span v-for="item in CAPABILITIES" :key="item">{{ item }}</span>
        <span>Markdown / DOCX</span>
      </div>
    </section>

    <!-- 右侧：登录/注册表单。保留原字段与提交流程，只提升排版层级。 -->
    <section class="flex items-center justify-center bg-card/45 px-4 py-10 sm:px-8 lg:bg-card/35">
      <div class="w-full max-w-sm">
        <div class="mb-9 lg:hidden">
          <div class="mb-5 flex items-center gap-2.5">
            <span
              class="flex size-8 items-center justify-center rounded-lg bg-foreground text-background"
              aria-hidden="true"
            >
              <BookMarked :size="16" :stroke-width="1.8" />
            </span>
            <span class="text-sm font-semibold tracking-tight">文献综述工作台</span>
          </div>
          <h1 class="font-display text-3xl leading-snug font-medium tracking-[-0.035em]">
            让每一句论断，<br />都有迹可循。
          </h1>
        </div>

        <div class="hidden lg:block">
          <p class="text-[10px] font-semibold tracking-[0.18em] text-air-primary uppercase">Welcome back</p>
          <h2 class="mt-3 font-display text-3xl font-medium tracking-[-0.025em]">继续你的研究</h2>
          <p class="mt-2 text-sm text-muted-foreground">登录以访问任务、证据与引用库。</p>
        </div>

        <form class="mt-7 flex flex-col gap-4" @submit.prevent="handleSubmit">
          <div class="grid grid-cols-2 gap-1 rounded-full border border-border bg-muted p-1" role="tablist">
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
                'rounded-full px-3 py-2 text-sm font-medium transition-colors',
                mode === option.value
                  ? 'bg-card text-foreground shadow-xs'
                  : 'text-muted-foreground hover:text-foreground',
              ]"
              @click="mode = option.value"
            >
              {{ option.label }}
            </button>
          </div>

          <div v-if="mode === 'login'" class="grid gap-1.5">
            <Label for="account">用户名 / 邮箱</Label>
            <Input
              id="account"
              v-model="account"
              type="text"
              required
              autocomplete="username"
              placeholder="请输入用户名或邮箱"
              class="h-11 rounded-xl bg-card/70"
            />
          </div>
          <template v-else>
            <div class="grid gap-1.5">
              <Label for="username">用户名</Label>
              <Input
                id="username"
                v-model="username"
                type="text"
                required
                minlength="3"
                autocomplete="username"
                class="h-11 rounded-xl bg-card/70"
              />
            </div>
            <div class="grid gap-1.5">
              <Label for="email">邮箱</Label>
              <Input
                id="email"
                v-model="email"
                type="email"
                required
                autocomplete="email"
                class="h-11 rounded-xl bg-card/70"
              />
            </div>
          </template>

          <div class="grid gap-1.5">
            <Label for="password">密码</Label>
            <Input
              id="password"
              v-model="password"
              type="password"
              required
              minlength="8"
              placeholder="至少 8 位"
              :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
              class="h-11 rounded-xl bg-card/70"
            />
          </div>

          <ErrorBanner :message="errorMessage" />

          <Button
            type="submit"
            :disabled="loading"
            class="h-11 w-full rounded-full bg-air-primary text-air-primary-foreground hover:bg-air-primary/90"
          >
            <Loader2 v-if="loading" class="animate-spin" aria-hidden="true" />
            <span>{{ loading ? "处理中…" : submitLabel }}</span>
            <ArrowRight v-if="!loading" :size="15" aria-hidden="true" />
          </Button>

          <p v-if="mode === 'login'" class="text-center text-sm">
            <RouterLink
              :to="{ name: 'password-reset' }"
              data-test="forgot-password"
              class="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            >
              忘记密码？
            </RouterLink>
          </p>
        </form>

        <p class="mt-8 flex items-center justify-center gap-1.5 text-[10px] text-muted-foreground">
          <LockKeyhole :size="12" aria-hidden="true" />
          你的研究数据仅对当前工作空间可见
        </p>
      </div>
    </section>
  </div>
</template>
