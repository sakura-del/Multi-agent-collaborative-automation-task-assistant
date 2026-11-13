import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";

import { resolveApiBase } from "@/config/endpoint";

import type { TokenOut } from "./types";

// 单一 Axios 实例：统一 baseURL、token 注入与 401 单飞刷新
export const ACCESS_TOKEN_KEY = "access_token";
export const REFRESH_TOKEN_KEY = "refresh_token";

/** 标记已重试过的请求，避免 401 → 刷新 → 401 的死循环 */
type RetriableConfig = InternalAxiosRequestConfig & { _retry?: boolean };

// 基地址集中解析（见 @/config/endpoint）：
// 未配置环境变量时是相对路径 `/api/v1`，保持 nginx 同源部署不变；
// 部署到 Cloudflare Pages 时由 VITE_API_BASE_URL 注入绝对地址。
const API_BASE = resolveApiBase();

/** 刷新专用实例：不带拦截器，避免 401 触发递归刷新 */
const refreshClient = axios.create({ baseURL: API_BASE, timeout: 15000 });

let refreshPromise: Promise<string> | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function persistTokens(tokens: TokenOut): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

/** 刷新失败时由应用注册的登出副作用（清状态 + 跳登录页） */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

/**
 * 归一化 API 错误：message 之外保留后端的 `error_code` 与 `details`。
 *
 * 登录页要据 `code === "EMAIL_NOT_VERIFIED"` 展示「查收验证邮件 + 重发」，
 * 而不是引导用户反复试密码（2026-11-13）。
 */
export interface ApiError extends Error {
  code?: string;
  details?: Record<string, unknown>;
}

function normalizeError(error: unknown): ApiError {
  if (error instanceof AxiosError) {
    const data = (error.response?.data ?? {}) as {
      message?: unknown;
      error_code?: unknown;
      details?: Record<string, unknown>;
    };
    const apiError = new Error(String(data.message ?? error.message)) as ApiError;
    if (typeof data.error_code === "string") apiError.code = data.error_code;
    if (data.details && typeof data.details === "object") apiError.details = data.details;
    return apiError;
  }
  return error instanceof Error ? (error as ApiError) : new Error(String(error));
}

async function rotateTokens(): Promise<string> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    throw new Error("缺少 refresh_token，请重新登录");
  }
  const { data } = await refreshClient.post<TokenOut>("/auth/refresh", {
    refresh_token: refreshToken,
  });
  persistTokens(data);
  return data.access_token;
}

export const api = axios.create({
  // 开发期 vite proxy 转发 /api 到后端；生产期 nginx 反代；
  // 跨域部署（Cloudflare Pages）时由 VITE_API_BASE_URL 提供绝对地址
  baseURL: API_BASE,
  timeout: 15000,
});

api.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as RetriableConfig | undefined;
    const isRefreshCall = Boolean(config?.url?.includes("/auth/refresh"));

    if (error.response?.status === 401 && config && !config._retry && !isRefreshCall) {
      config._retry = true;
      try {
        // 单飞：并发 401 只发起一次刷新，其余请求等待同一个 Promise
        refreshPromise = refreshPromise ?? rotateTokens().finally(() => {
          refreshPromise = null;
        });
        const accessToken = await refreshPromise;
        config.headers.Authorization = `Bearer ${accessToken}`;
        return await api.request(config);
      } catch (refreshError) {
        clearTokens();
        unauthorizedHandler?.();
        return Promise.reject(normalizeError(refreshError));
      }
    }

    return Promise.reject(normalizeError(error));
  }
);

export default api;
