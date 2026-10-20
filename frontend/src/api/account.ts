import api from "./client";

// 账号生命周期 API（Phase 1 P1-C 端点接线）：
//   邮箱验证（request / confirm）+ 密码重置（request / confirm）
// 全部 Bearer 鉴权由 client.ts 的请求拦截器统一注入；confirm 类端点无需登录。

/** 后端统一的文案响应结构（4 个端点共用） */
export interface LifecycleMessage {
  message: string;
}

/**
 * 重发邮箱验证邮件（需登录）。
 *
 * 后端在「已发送」与「已验证」两种情况返回不同文案，但都是 200——
 * 调用方直接展示 `message` 即可。
 */
export function requestEmailVerification(): Promise<LifecycleMessage> {
  return api
    .post<LifecycleMessage>("/auth/verify-email/request")
    .then((response) => response.data);
}

/** 消费邮件里的验证令牌，把账号标记为「邮箱已验证」 */
export function confirmEmailVerification(token: string): Promise<LifecycleMessage> {
  return api
    .post<LifecycleMessage>("/auth/verify-email/confirm", { token })
    .then((response) => response.data);
}

/**
 * 申请密码重置。
 *
 * **恒定响应**：无论账号是否存在，后端都返回同样的状态码与响应体
 * （防账号枚举），因此前端只需原样展示 `message`，不要据此推断账号存在性。
 */
export function requestPasswordReset(email: string): Promise<LifecycleMessage> {
  return api
    .post<LifecycleMessage>("/auth/password-reset/request", { email })
    .then((response) => response.data);
}

export interface PasswordResetPayload {
  token: string;
  new_password: string;
}

/** 消费重置令牌并设置新密码；成功后旧 token 全部失效（需重新登录） */
export function confirmPasswordReset(payload: PasswordResetPayload): Promise<LifecycleMessage> {
  return api
    .post<LifecycleMessage>("/auth/password-reset/confirm", payload)
    .then((response) => response.data);
}
