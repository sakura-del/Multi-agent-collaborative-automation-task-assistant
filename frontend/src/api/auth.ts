import api from "./client";
import type { LoginInput, RegisterInput, TokenOut, UserOut } from "./types";

// 鉴权 API：对接后端 /api/v1/auth/*
export const authApi = {
  register: (data: RegisterInput) =>
    api.post<UserOut>("/auth/register", data).then((response) => response.data),

  login: (data: LoginInput) =>
    api.post<TokenOut>("/auth/login", data).then((response) => response.data),

  refresh: (refreshToken: string) =>
    api
      .post<TokenOut>("/auth/refresh", { refresh_token: refreshToken })
      .then((response) => response.data),

  me: () => api.get<UserOut>("/auth/me").then((response) => response.data),
};

export default authApi;
