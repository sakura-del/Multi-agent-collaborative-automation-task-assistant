import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import {
  confirmEmailVerification,
  confirmPasswordReset,
  requestEmailVerification,
  requestPasswordReset,
} from "@/api/account";
import VerifyEmailView from "@/views/VerifyEmailView.vue";
import PasswordResetView from "@/views/PasswordResetView.vue";

vi.mock("@/api/account", () => ({
  requestEmailVerification: vi.fn(),
  confirmEmailVerification: vi.fn(),
  requestPasswordReset: vi.fn(),
  confirmPasswordReset: vi.fn(),
}));

const routerPush = vi.fn();
vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: routerPush, replace: routerPush }),
}));

// VerifyEmailView 2026-11-13 起读 auth.user 预填重发邮箱；默认测试保持未登录
const mockAuthState: {
  user: {
    id: string;
    username: string;
    email: string;
    role: string;
    is_active: boolean;
    email_verified_at?: string | null;
    created_at: string;
  } | null;
} = { user: null };
vi.mock("@/stores/auth", () => ({
  useAuthStore: () => mockAuthState,
}));

describe("VerifyEmailView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAuthState.user = null;
  });

  it("有 token 时自动确认并展示成功", async () => {
    vi.mocked(confirmEmailVerification).mockResolvedValue({ message: "邮箱验证成功。" });

    const wrapper = mount(VerifyEmailView, { props: { token: "tok-1234567890" } });
    await flushPromises();

    expect(confirmEmailVerification).toHaveBeenCalledWith("tok-1234567890");
    expect(wrapper.find('[data-test="verify-success"]').text()).toContain("验证成功");
  });

  it("无 token 时展示重发入口（按邮箱，免登录）", async () => {
    const wrapper = mount(VerifyEmailView, { props: { token: "" } });
    await flushPromises();

    expect(wrapper.find('[data-test="verify-request"]').exists()).toBe(true);
    expect(confirmEmailVerification).not.toHaveBeenCalled();
  });

  it("填写邮箱后点击重发调用 requestEmailVerification 并展示后端文案", async () => {
    vi.mocked(requestEmailVerification).mockResolvedValue({
      message: "验证邮件已发送，请查收。",
    });

    const wrapper = mount(VerifyEmailView, { props: { token: "" } });
    await wrapper.find('[data-test="verify-email-input"]').setValue("ylin@example.com");
    await wrapper.find('[data-test="verify-request"]').trigger("click");
    await flushPromises();

    expect(requestEmailVerification).toHaveBeenCalledWith("ylin@example.com");
    expect(wrapper.find('[data-test="verify-message"]').text()).toContain("验证邮件已发送");
  });

  it("邮箱为空时点击重发不发请求", async () => {
    const wrapper = mount(VerifyEmailView, { props: { token: "" } });
    await wrapper.find('[data-test="verify-request"]').trigger("click");
    await flushPromises();

    expect(requestEmailVerification).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="verify-error"]').text()).toContain("邮箱");
  });

  it("确认失败时展示后端可读原因", async () => {
    vi.mocked(confirmEmailVerification).mockRejectedValue(new Error("该令牌已被使用。"));

    const wrapper = mount(VerifyEmailView, { props: { token: "used-token" } });
    await flushPromises();

    expect(wrapper.find('[data-test="verify-error"]').text()).toContain("已被使用");
  });

  it("已验证登录账号访问时不展示重发表单，明确提示当前邮箱已通过验证（Issue #56）", async () => {
    mockAuthState.user = {
      id: "u1",
      username: "Lingrove",
      email: "lingrove@example.com",
      role: "admin",
      is_active: true,
      email_verified_at: "2026-11-13T08:00:00Z",
      created_at: "2026-11-01T00:00:00Z",
    };

    const wrapper = mount(VerifyEmailView, { props: { token: "" } });
    await flushPromises();

    const banner = wrapper.find('[data-test="verify-already-verified"]');
    expect(banner.exists()).toBe(true);
    expect(banner.text()).toContain("已通过验证");
    expect(banner.text()).toContain("lingrove@example.com");
    expect(wrapper.find('[data-test="verify-request"]').exists()).toBe(false);
  });
});

describe("PasswordResetView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("申请阶段：提交邮箱后展示恒定文案（不泄漏账号是否存在）", async () => {
    vi.mocked(requestPasswordReset).mockResolvedValue({
      message: "如果该邮箱已注册，我们已发送密码重置邮件，请查收。",
    });

    const wrapper = mount(PasswordResetView);
    await wrapper.find('[data-test="reset-email"]').setValue("user@example.com");
    await wrapper.find('[data-test="reset-request-submit"]').trigger("submit");
    await flushPromises();

    expect(requestPasswordReset).toHaveBeenCalledWith("user@example.com");
    expect(wrapper.find('[data-test="reset-requested"]').text()).toContain("如果该邮箱已注册");
  });

  it("邮箱格式非法时前端先拦，不发请求", async () => {
    const wrapper = mount(PasswordResetView);
    await wrapper.find('[data-test="reset-email"]').setValue("not-an-email");
    await wrapper.find('[data-test="reset-request-submit"]').trigger("submit");
    await flushPromises();

    expect(requestPasswordReset).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="reset-error"]').text().length).toBeGreaterThan(0);
  });

  it("确认阶段：token + 新密码提交成功后可回登录页", async () => {
    vi.mocked(confirmPasswordReset).mockResolvedValue({ message: "密码已重置，请用新密码登录。" });

    const wrapper = mount(PasswordResetView, { props: { token: "tok-abcdefghij" } });
    await wrapper.find('[data-test="reset-password"]').setValue("new-password-123");
    await wrapper.find('[data-test="reset-confirm-submit"]').trigger("submit");
    await flushPromises();

    expect(confirmPasswordReset).toHaveBeenCalledWith({
      token: "tok-abcdefghij",
      new_password: "new-password-123",
    });
    expect(wrapper.find('[data-test="reset-success"]').text()).toContain("密码已重置");
  });

  it("确认阶段：新密码过短时前端先拦", async () => {
    const wrapper = mount(PasswordResetView, { props: { token: "tok-abcdefghij" } });
    await wrapper.find('[data-test="reset-password"]').setValue("short");
    await wrapper.find('[data-test="reset-confirm-submit"]').trigger("submit");
    await flushPromises();

    expect(confirmPasswordReset).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="reset-error"]').text()).toContain("8");
  });

  it("确认阶段：令牌失效时展示后端原因", async () => {
    vi.mocked(confirmPasswordReset).mockRejectedValue(new Error("该令牌已过期，请重新申请。"));

    const wrapper = mount(PasswordResetView, { props: { token: "expired" } });
    await wrapper.find('[data-test="reset-password"]').setValue("new-password-123");
    await wrapper.find('[data-test="reset-confirm-submit"]').trigger("submit");
    await flushPromises();

    expect(wrapper.find('[data-test="reset-error"]').text()).toContain("已过期");
  });
});
