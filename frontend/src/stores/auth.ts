import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { authApi } from "@/api/auth";
import { clearTokens, getAccessToken, getRefreshToken, persistTokens } from "@/api/client";
import type { LoginInput, RegisterInput, UserOut } from "@/api/types";

// 鉴权状态：token 存 localStorage，用户信息每次启动通过 /auth/me 校验
export const useAuthStore = defineStore("auth", () => {
  const user = ref<UserOut | null>(null);
  const ready = ref(false);

  const authenticated = computed(() => user.value !== null);

  /** 应用启动时恢复会话：有 token 才请求 /auth/me */
  async function bootstrap(): Promise<void> {
    try {
      if (getAccessToken()) {
        user.value = await authApi.me();
      }
    } catch {
      // token 失效/网络异常：清空本地会话，交由路由守卫跳转登录
      clearTokens();
      user.value = null;
    } finally {
      ready.value = true;
    }
  }

  async function login(input: LoginInput): Promise<void> {
    persistTokens(await authApi.login(input));
    user.value = await authApi.me();
  }

  async function register(input: RegisterInput): Promise<void> {
    // 2026-11-13：不再自动登录——邮箱未验证就被门禁挡住（403 EMAIL_NOT_VERIFIED），
    // 这里的静默失败曾让用户「没验证邮箱也进了系统」。注册成功即返回，
    // 由 LoginView 引导去邮箱点验证链接。
    await authApi.register(input);
  }

  /** 主动轮换 token（401 拦截器内部有自己的单飞刷新） */
  async function refresh(): Promise<void> {
    try {
      persistTokens(await authApi.refresh(getRefreshToken() ?? ""));
    } catch (error) {
      clearTokens();
      user.value = null;
      throw error;
    }
  }

  function logout(): void {
    clearTokens();
    user.value = null;
  }

  return { user, ready, authenticated, bootstrap, login, register, refresh, logout };
});
