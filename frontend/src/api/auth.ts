import { resolveApiBase } from "@/config/endpoint";
import api from "./client";
import type { LoginInput, RegisterInput, TokenOut, UserOut } from "./types";

export type OAuthProvider = "google" | "github";

// 鉴权 API：对接后端 /api/v1/auth/*
export const authApi = {
  register: (data: RegisterInput) =>
    api.post<UserOut>("/auth/register", data).then((response) => response.data),

  login: (data: LoginInput) =>
    api.post<TokenOut>("/auth/login", data).then((response) => response.data),

  requestEmailOtp: (email: string) =>
    api
      .post<{ message: string; cooldown_seconds: number }>("/auth/email-otp/request", { email })
      .then((response) => response.data),

  verifyEmailOtp: (payload: { email?: string; code?: string; magic_token?: string }) =>
    api.post<TokenOut>("/auth/email-otp/verify", payload).then((response) => response.data),

  exchangeOAuthCode: (code: string) =>
    api.post<TokenOut>("/auth/oauth/exchange", { code }).then((response) => response.data),

  getOAuthAuthorizeUrl: (provider: OAuthProvider) =>
    `${resolveApiBase()}/auth/oauth/${provider}/authorize`,

  refresh: (refreshToken: string) =>
    api
      .post<TokenOut>("/auth/refresh", { refresh_token: refreshToken })
      .then((response) => response.data),

  me: () => api.get<UserOut>("/auth/me").then((response) => response.data),
};

export default authApi;
