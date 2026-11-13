import { describe, expect, it, vi } from "vitest";

import {
  confirmEmailVerification,
  confirmPasswordReset,
  requestEmailVerification,
  requestPasswordReset,
} from "@/api/account";

vi.mock("@/api/client", () => ({
  default: {
    post: vi.fn(),
  },
}));

import api from "@/api/client";

// 账号生命周期契约（Phase 1 P1-C 端点接线）：
//   4 个端点均返回 { message: string }
//   - verify-email/request 需登录
//   - password-reset/request 恒定响应（防账号枚举）

describe("account api", () => {
  it("requestEmailVerification 调 /auth/verify-email/request", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 200,
      data: { message: "验证邮件已发送，请查收并在有效期内完成验证。" },
    });

    const result = await requestEmailVerification("ylin@example.com");

    expect(api.post).toHaveBeenCalledWith("/auth/verify-email/request", {
      email: "ylin@example.com",
    });
    expect(result.message).toContain("验证邮件已发送");
  });

  it("confirmEmailVerification 提交 token", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 200,
      data: { message: "邮箱验证成功。" },
    });

    const result = await confirmEmailVerification("tok-1234567890");

    expect(api.post).toHaveBeenCalledWith("/auth/verify-email/confirm", {
      token: "tok-1234567890",
    });
    expect(result.message).toContain("验证成功");
  });

  it("requestPasswordReset 提交 email 并返回恒定文案", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 200,
      data: { message: "如果该邮箱已注册，我们已发送密码重置邮件，请查收。" },
    });

    const result = await requestPasswordReset("user@example.com");

    expect(api.post).toHaveBeenCalledWith("/auth/password-reset/request", {
      email: "user@example.com",
    });
    expect(result.message).toContain("如果该邮箱已注册");
  });

  it("confirmPasswordReset 提交 token 与新密码", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 200,
      data: { message: "密码已重置，请用新密码登录。" },
    });

    const result = await confirmPasswordReset({
      token: "tok-abcdefghij",
      new_password: "new-password-123",
    });

    expect(api.post).toHaveBeenCalledWith("/auth/password-reset/confirm", {
      token: "tok-abcdefghij",
      new_password: "new-password-123",
    });
    expect(result.message).toContain("密码已重置");
  });

  it("把 400 的 detail 作为可展示错误抛出（如令牌过期）", async () => {
    vi.mocked(api.post).mockRejectedValue(new Error("该令牌已过期，请重新申请。"));

    await expect(confirmEmailVerification("expired-token")).rejects.toThrow("已过期");
  });
});
