<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Loader2 } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { trackEvent } from "@/api/growth";
import { useAuthStore } from "@/stores/auth";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const errorMessage = ref("");

onMounted(async () => {
  const code = typeof route.query.code === "string" ? route.query.code.trim() : "";
  if (!code) {
    errorMessage.value = "缺少第三方授权交换码，请返回登录页重试";
    return;
  }
  try {
    await auth.loginWithOAuthCode(code);
    trackEvent("auth_success", { method: "oauth", is_new_user: auth.justRegistered });
    await router.replace({ name: "tasks" });
  } catch (error) {
    errorMessage.value = (error as Error).message || "第三方登录失败，请重试";
  }
});
</script>

<template>
  <div class="flex min-h-dvh items-center justify-center bg-background px-4">
    <div class="w-full max-w-sm rounded-lg border border-border bg-card p-6 text-center">
      <template v-if="!errorMessage">
        <Loader2 class="mx-auto size-6 animate-spin text-[#0F4C3A] dark:text-primary motion-reduce:animate-none" aria-hidden="true" />
        <p class="mt-3 text-sm font-medium text-foreground">正在完成第三方登录…</p>
        <p class="mt-1 text-xs text-muted-foreground">即将进入 VeriPaper 工作台</p>
      </template>
      <template v-else>
        <ErrorBanner :message="errorMessage" @dismiss="errorMessage = ''" />
        <RouterLink
          :to="{ name: 'login' }"
          class="mt-3 inline-block rounded text-xs font-medium text-[#0F4C3A] underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:text-primary"
        >
          返回登录页
        </RouterLink>
      </template>
    </div>
  </div>
</template>
