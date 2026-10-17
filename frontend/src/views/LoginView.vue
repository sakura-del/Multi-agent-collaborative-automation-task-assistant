<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { useAuthStore } from "@/stores/auth";

// 登录/注册页：成功后回到 redirect 指向的页面（默认任务列表）
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

const submitLabel = computed(() =>
  mode.value === "login" ? "登录" : "注册并登录"
);

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
  <div class="login-page">
    <form class="login-card" @submit.prevent="handleSubmit">
      <h1>多智能体协作自动化任务助手</h1>
      <p class="login-card__subtitle">文献综述工作台</p>

      <div class="tabs">
        <button
          type="button"
          :class="{ active: mode === 'login' }"
          @click="mode = 'login'"
        >
          登录
        </button>
        <button
          type="button"
          :class="{ active: mode === 'register' }"
          @click="mode = 'register'"
        >
          注册
        </button>
      </div>

      <div v-if="mode === 'login'" class="field">
        <label for="account">用户名 / 邮箱</label>
        <input id="account" v-model="account" class="input" type="text" required />
      </div>
      <template v-else>
        <div class="field">
          <label for="username">用户名</label>
          <input
            id="username"
            v-model="username"
            class="input"
            type="text"
            required
            minlength="3"
          />
        </div>
        <div class="field">
          <label for="email">邮箱</label>
          <input id="email" v-model="email" class="input" type="email" required />
        </div>
      </template>

      <div class="field">
        <label for="password">密码</label>
        <input
          id="password"
          v-model="password"
          class="input"
          type="password"
          required
          minlength="8"
          placeholder="至少 8 位"
        />
      </div>

      <p v-if="errorMessage" class="banner banner--error" role="alert">
        {{ errorMessage }}
      </p>

      <button class="btn btn--primary login-card__submit" type="submit" :disabled="loading">
        {{ loading ? "处理中…" : submitLabel }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
}

.login-card {
  width: 100%;
  max-width: 400px;
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 2rem;
}

.login-card h1 {
  font-size: 1.15rem;
}

.login-card__subtitle {
  color: #6b7280;
  font-size: 0.875rem;
  margin: 0.25rem 0 1.5rem;
}

.login-card__submit {
  width: 100%;
  margin-top: 0.5rem;
}

.tabs {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1.25rem;
}

.tabs button {
  flex: 1;
  padding: 0.5rem;
  border: 1px solid var(--color-border);
  background: transparent;
  border-radius: 8px;
  font-size: 0.9rem;
}

.tabs button.active {
  background: var(--color-primary);
  border-color: var(--color-primary);
  color: #fff;
}

.field {
  margin-bottom: 1rem;
}

.field label {
  display: block;
  font-size: 0.85rem;
  margin-bottom: 0.25rem;
}
</style>
