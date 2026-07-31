// Axios 实例：统一 baseURL、请求/响应拦截、token 注入
// 阶段0：最小化封装，后续阶段按需扩展错误处理与刷新逻辑
import axios from "axios";

const api = axios.create({
  // 开发期 vite proxy 转发 /api 到后端；生产期 nginx 反代
  baseURL: "/api/v1",
  timeout: 15000,
});

// 请求拦截：自动注入 access_token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截：统一错误格式
api.interceptors.response.use(
  (response) => response,
  (error) => {
    // 后端返回的统一错误结构：{ error_code, message, details }
    const detail = error.response?.data?.message ?? error.message;
    return Promise.reject(new Error(detail));
  }
);

// ===== 鉴权 API（对接后端 /api/v1/auth/*）=====

export interface UserOut {
  id: string;
  username: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

export interface TokenOut {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export const authApi = {
  register: (data: { username: string; email: string; password: string }) =>
    api.post<UserOut>("/auth/register", data).then((r) => r.data),

  login: (data: { account: string; password: string }) =>
    api.post<TokenOut>("/auth/login", data).then((r) => r.data),

  refresh: (refreshToken: string) =>
    api.post<TokenOut>("/auth/refresh", { refresh_token: refreshToken }).then(
      (r) => r.data
    ),

  me: () => api.get<UserOut>("/auth/me").then((r) => r.data),
};

export default api;
