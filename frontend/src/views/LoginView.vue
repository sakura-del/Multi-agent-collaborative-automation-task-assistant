<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Check, Loader2 } from "lucide-vue-next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { useAuthStore } from "@/stores/auth";

// 登录/注册页：成功后回到 redirect 指向的页面（默认任务列表）
// 样式走 Tailwind 原子类 + beUI 视觉语言（见 main.css 头注释），不写 scoped CSS。
//
// ## 为什么左侧加一段产品说明
//
// 旧版是白底一张卡片，看不出这是个什么工具 —— 用户第一次打开不知道
// 「合规 / 可核验」是这个产品的差异点。左侧补一句价值主张 + 三个能力点，
// 让首次访问者在登录前就知道它解决什么问题。
// 窄屏隐藏（`lg:` 以下不显示），不占用手机上的宝贵空间。
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

/** 左侧能力点：三条都对应系统里真实存在的功能，不是营销词。 */
const CAPABILITIES = [
  { title: "证据约束", detail: "每条结论都能回溯到原文段落" },
  { title: "引用可核验", detail: "GB/T 7714—2025，支持 CrossRef 真实性校验" },
  { title: "人机协同", detail: "规划先确认，导师可逐段批注" },
] as const;

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
  <div class="grid min-h-screen bg-background lg:grid-cols-[1.1fr_1fr]">
    <!-- 左侧：产品身份（窄屏隐藏）。用 --surface-brand 明确分区，
         否则浅色下两栏几乎同色，分割线显得生硬。 -->
    <section class="relative hidden flex-col justify-between bg-surface-brand p-12 lg:flex">
      <div class="flex items-center gap-2.5">
        <span
          class="flex size-7 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-foreground"
          aria-hidden="true"
        >
          综
        </span>
        <span class="text-sm font-semibold tracking-tight">文献综述工作台</span>
      </div>

      <!-- 内容整体上移：底部留白过多会让视线落空 -->
      <div class="max-w-md pb-16">
        <h1 class="text-3xl leading-tight font-semibold tracking-tight">
          生成即合规，<br />引用可核验
        </h1>
        <p class="mt-4 text-sm leading-relaxed text-muted-foreground">
          多智能体协作完成文献检索、证据筛选与综述撰写。规划先由你确认，
          正文里的每条引用都能点回原文段落。
        </p>

        <ul class="mt-8 space-y-4">
          <li v-for="item in CAPABILITIES" :key="item.title" class="flex gap-3">
            <span
              class="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-success/12 text-success"
              aria-hidden="true"
            >
              <Check :size="12" :stroke-width="3" />
            </span>
            <div class="min-w-0">
              <p class="text-sm font-medium">{{ item.title }}</p>
              <p class="text-xs leading-relaxed text-muted-foreground">{{ item.detail }}</p>
            </div>
          </li>
        </ul>
      </div>

      <p class="text-xs text-muted-foreground">
        支持 Markdown / DOCX 导出 · GB/T 7714—2025 双制式
      </p>
    </section>

    <!-- 右侧：表单。窄屏时整页只有这块，卡片边框在小屏去掉更干净。
         垂直位置略高于居中（pt-[12vh]）：完全居中在大屏上会显得悬空。 -->
    <section class="flex items-start justify-center px-4 pt-[10vh] pb-8 lg:items-center lg:p-8">
      <Card class="w-full max-w-sm border-0 bg-transparent p-0 shadow-none lg:border lg:border-border-strong/60 lg:bg-card lg:p-6">
        <!-- 窄屏才显示品牌（左侧整块被隐藏了） -->
        <div class="mb-5 lg:hidden">
          <div class="mb-3 flex items-center gap-2">
            <span
              class="flex size-6 items-center justify-center rounded-md bg-primary text-[11px] font-bold text-primary-foreground"
              aria-hidden="true"
            >
              综
            </span>
            <span class="text-sm font-semibold tracking-tight">文献综述工作台</span>
          </div>
          <h1 class="text-xl font-semibold tracking-tight">生成即合规，引用可核验</h1>
        </div>

        <form class="flex flex-col gap-4" @submit.prevent="handleSubmit">
          <!-- 模式切换：用分段控件，而不是两个裸按钮 -->
          <div class="grid grid-cols-2 gap-1 rounded-lg bg-muted p-1" role="tablist">
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
                'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
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
            <Input id="account" v-model="account" type="text" required autocomplete="username" />
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
              />
            </div>
            <div class="grid gap-1.5">
              <Label for="email">邮箱</Label>
              <Input id="email" v-model="email" type="email" required autocomplete="email" />
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
            />
          </div>

          <ErrorBanner :message="errorMessage" />

          <Button type="submit" :disabled="loading" class="w-full">
            <Loader2 v-if="loading" class="animate-spin" aria-hidden="true" />
            <span>{{ loading ? "处理中…" : submitLabel }}</span>
          </Button>

          <!-- 账号生命周期入口（Phase 1 P1-C）：登录模式才展示忘记密码 -->
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
      </Card>
    </section>
  </div>
</template>
