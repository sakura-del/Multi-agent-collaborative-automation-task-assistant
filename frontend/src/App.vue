<script setup lang="ts">
import { ref, onMounted } from "vue";
import { authApi, type UserOut } from "./api";

// 阶段0：最小登录页，验证前后端鉴权链路联通
// 后续阶段会替换为完整的路由 + 布局

const mode = ref<"login" | "register">("login");
const account = ref("");
const username = ref("");
const email = ref("");
const password = ref("");
const loading = ref(false);
const errorMsg = ref("");
const currentUser = ref<UserOut | null>(null);

// 挂载时若已有 token，尝试获取当前用户
onMounted(async () => {
  const token = localStorage.getItem("access_token");
  if (token) {
    try {
      currentUser.value = await authApi.me();
    } catch {
      localStorage.removeItem("access_token");
      localStorage.removeItem("refresh_token");
    }
  }
});

async function handleSubmit() {
  loading.value = true;
  errorMsg.value = "";
  try {
    if (mode.value === "register") {
      await authApi.register({
        username: username.value,
        email: email.value,
        password: password.value,
      });
      // 注册成功后自动登录
      const tok = await authApi.login({
        account: username.value,
        password: password.value,
      });
      saveToken(tok.access_token, tok.refresh_token);
      currentUser.value = await authApi.me();
    } else {
      const tok = await authApi.login({
        account: account.value,
        password: password.value,
      });
      saveToken(tok.access_token, tok.refresh_token);
      currentUser.value = await authApi.me();
    }
  } catch (e) {
    errorMsg.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}

function saveToken(access: string, refresh: string) {
  localStorage.setItem("access_token", access);
  localStorage.setItem("refresh_token", refresh);
}

function logout() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  currentUser.value = null;
  account.value = "";
  password.value = "";
}
</script>

<template>
  <div class="container">
    <div class="card">
      <h1>多智能体协作自动化任务助手</h1>
      <p class="subtitle">阶段0 · 鉴权联调验证</p>

      <!-- 已登录状态 -->
      <div v-if="currentUser" class="profile">
        <p>登录成功</p>
        <ul>
          <li>ID: {{ currentUser.id }}</li>
          <li>用户名: {{ currentUser.username }}</li>
          <li>邮箱: {{ currentUser.email }}</li>
          <li>角色: {{ currentUser.role }}</li>
        </ul>
        <button @click="logout">退出登录</button>
      </div>

      <!-- 未登录：登录/注册表单 -->
      <form v-else @submit.prevent="handleSubmit">
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
          <label>用户名 / 邮箱</label>
          <input v-model="account" type="text" required placeholder="alice" />
        </div>
        <template v-else>
          <div class="field">
            <label>用户名</label>
            <input v-model="username" type="text" required minlength="3" />
          </div>
          <div class="field">
            <label>邮箱</label>
            <input v-model="email" type="email" required />
          </div>
        </template>

        <div class="field">
          <label>密码</label>
          <input
            v-model="password"
            type="password"
            required
            minlength="8"
            placeholder="至少 8 位"
          />
        </div>

        <p v-if="errorMsg" class="error">{{ errorMsg }}</p>

        <button type="submit" :disabled="loading">
          {{ loading ? "处理中..." : mode === "login" ? "登录" : "注册并登录" }}
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
}
.card {
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 2rem;
  width: 100%;
  max-width: 420px;
  box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
}
h1 {
  font-size: 1.25rem;
  margin-bottom: 0.25rem;
}
.subtitle {
  color: #6b7280;
  font-size: 0.875rem;
  margin-bottom: 1.5rem;
}
.tabs {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 1.5rem;
}
.tabs button {
  flex: 1;
  padding: 0.5rem;
  border: 1px solid var(--color-border);
  background: transparent;
  border-radius: 8px;
}
.tabs button.active {
  background: var(--color-primary);
  color: white;
  border-color: var(--color-primary);
}
.field {
  margin-bottom: 1rem;
}
.field label {
  display: block;
  font-size: 0.875rem;
  margin-bottom: 0.25rem;
}
.field input {
  width: 100%;
  padding: 0.625rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  font-size: 0.95rem;
}
form button[type="submit"] {
  width: 100%;
  padding: 0.625rem;
  background: var(--color-primary);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 1rem;
  margin-top: 0.5rem;
}
form button[type="submit"]:disabled {
  opacity: 0.6;
}
.error {
  color: var(--color-error);
  font-size: 0.85rem;
  margin: 0.5rem 0;
}
.profile ul {
  list-style: none;
  margin: 1rem 0;
  font-size: 0.9rem;
}
.profile li {
  padding: 0.25rem 0;
  border-bottom: 1px solid var(--color-border);
}
.profile button {
  padding: 0.5rem 1rem;
  background: var(--color-border);
  border: none;
  border-radius: 8px;
  width: 100%;
}
</style>
