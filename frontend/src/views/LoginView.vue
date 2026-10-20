<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Loader2 } from "lucide-vue-next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { useAuthStore } from "@/stores/auth";

// 登录/注册页：成功后回到 redirect 指向的页面（默认任务列表）
// 样式走 shadcn-vue / Nova 规范（AGENTS.md §6.5），不再写 scoped CSS。
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
  <div class="flex min-h-screen items-center justify-center bg-background p-4">
    <Card class="w-full max-w-sm p-6">
      <form class="flex flex-col gap-4" @submit.prevent="handleSubmit">
        <div class="space-y-1">
          <h1 class="text-lg font-semibold tracking-tight">多智能体协作自动化任务助手</h1>
          <p class="text-sm text-muted-foreground">文献综述工作台</p>
        </div>

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
                ? 'bg-background text-foreground shadow-xs'
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
            class="text-primary underline-offset-4 hover:underline"
          >
            忘记密码？
          </RouterLink>
        </p>
      </form>
    </Card>
  </div>
</template>
