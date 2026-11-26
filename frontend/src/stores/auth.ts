import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { authApi } from "@/api/auth";
import { clearTokens, getAccessToken, getRefreshToken, persistTokens } from "@/api/client";
import type { LoginInput, RegisterInput, UserOut } from "@/api/types";

const USER_CACHE_KEY = "veripaper_user";
const WELCOME_BANNER_KEY = "vp:welcome_banner";

function readWelcomeFlag(): boolean {
  try {
    return typeof localStorage !== "undefined" && localStorage.getItem(WELCOME_BANNER_KEY) === "1";
  } catch {
    return false;
  }
}

function readCachedUser(): UserOut | null {
  try {
    const raw = localStorage.getItem(USER_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as UserOut;
    return parsed && typeof parsed.id === "string" ? parsed : null;
  } catch {
    return null;
  }
}

function writeCachedUser(value: UserOut | null): void {
  if (value) {
    localStorage.setItem(USER_CACHE_KEY, JSON.stringify(value));
  } else {
    localStorage.removeItem(USER_CACHE_KEY);
  }
}

/** 判断 JWT 是否已过期（或将在 30 秒内过期），用于启动时提前静默刷新 */
export function isJwtExpired(token: string): boolean {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return false;
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(base64)) as { exp?: number };
    if (typeof payload.exp !== "number") return false;
    return payload.exp * 1000 <= Date.now() + 30_000;
  } catch {
    return false;
  }
}

// 鉴权状态：token 与轻量用户信息缓存存 localStorage，支持零阻塞乐观恢复 + 后台校验
export const useAuthStore = defineStore("auth", () => {
  const user = ref<UserOut | null>(null);
  const ready = ref(false);
  const justRegistered = ref(readWelcomeFlag());

  const authenticated = computed(() => user.value !== null);

  function markNewUserIfNeeded(tokens: { is_new_user?: boolean }) {
    if (tokens.is_new_user) {
      justRegistered.value = true;
      try {
        localStorage.setItem(WELCOME_BANNER_KEY, "1");
      } catch {
        /* 忽略存储受限环境 */
      }
    }
  }

  function dismissWelcome(): void {
    justRegistered.value = false;
    try {
      localStorage.removeItem(WELCOME_BANNER_KEY);
    } catch {
      /* 忽略 */
    }
  }

  async function fetchAndCacheMe(): Promise<UserOut> {
    const me = await authApi.me();
    user.value = me;
    writeCachedUser(me);
    return me;
  }

  /** 应用启动时恢复会话：有缓存且令牌有效时零阻塞放行并在后台校验，否则走串行恢复 */
  async function bootstrap(): Promise<void> {
    try {
      let accessToken = getAccessToken();
      const refreshToken = getRefreshToken();
      if (refreshToken && (!accessToken || isJwtExpired(accessToken))) {
        const rotated = await authApi.refresh(refreshToken);
        persistTokens(rotated);
        accessToken = rotated.access_token;
      }
      if (!accessToken) {
        writeCachedUser(null);
        return;
      }

      const cached = readCachedUser();
      if (cached) {
        // 乐观恢复：立即放行路由让页面业务请求与 /auth/me 完全并发
        user.value = cached;
        ready.value = true;
        void fetchAndCacheMe().catch(() => {
          clearTokens();
          writeCachedUser(null);
          user.value = null;
        });
        return;
      }

      await fetchAndCacheMe();
    } catch {
      // token 失效/网络异常：清空本地会话，交由路由守卫跳转登录
      clearTokens();
      writeCachedUser(null);
      user.value = null;
    } finally {
      ready.value = true;
    }
  }

  async function login(input: LoginInput): Promise<void> {
    persistTokens(await authApi.login(input));
    await fetchAndCacheMe();
  }

  async function loginWithEmailOtp(email: string, code: string): Promise<void> {
    const tokens = await authApi.verifyEmailOtp({ email, code });
    persistTokens(tokens);
    markNewUserIfNeeded(tokens);
    await fetchAndCacheMe();
  }

  async function loginWithMagicToken(magicToken: string): Promise<void> {
    const tokens = await authApi.verifyEmailOtp({ magic_token: magicToken });
    persistTokens(tokens);
    markNewUserIfNeeded(tokens);
    await fetchAndCacheMe();
  }

  async function loginWithOAuthCode(code: string): Promise<void> {
    const tokens = await authApi.exchangeOAuthCode(code);
    persistTokens(tokens);
    markNewUserIfNeeded(tokens);
    await fetchAndCacheMe();
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
      writeCachedUser(null);
      user.value = null;
      throw error;
    }
  }

  function logout(): void {
    clearTokens();
    writeCachedUser(null);
    user.value = null;
    dismissWelcome();
  }

  return {
    user,
    ready,
    authenticated,
    justRegistered,
    dismissWelcome,
    bootstrap,
    login,
    loginWithEmailOtp,
    loginWithMagicToken,
    loginWithOAuthCode,
    register,
    refresh,
    logout,
  };
});
