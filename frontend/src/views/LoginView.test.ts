import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";

const mocks = vi.hoisted(() => ({
  requestEmailOtp: vi.fn(),
  verifyEmailOtp: vi.fn(),
  login: vi.fn(),
  me: vi.fn(),
}));

vi.mock("@/api/auth", () => ({
  authApi: {
    requestEmailOtp: mocks.requestEmailOtp,
    verifyEmailOtp: mocks.verifyEmailOtp,
    getOAuthAuthorizeUrl: (provider: string) => `/api/v1/auth/oauth/${provider}/authorize`,
    exchangeOAuthCode: vi.fn(),
    login: mocks.login,
    me: mocks.me,
    register: vi.fn(),
    refresh: vi.fn(),
  },
  default: {
    requestEmailOtp: mocks.requestEmailOtp,
    verifyEmailOtp: mocks.verifyEmailOtp,
    getOAuthAuthorizeUrl: (provider: string) => `/api/v1/auth/oauth/${provider}/authorize`,
    login: mocks.login,
    me: mocks.me,
  },
}));

vi.mock("@/api/client", () => ({
  default: {
    get: vi.fn(),
    defaults: { baseURL: "http://api.test" },
  },
  getAccessToken: () => null,
  getRefreshToken: () => null,
  persistTokens: vi.fn(),
  clearTokens: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}));

import LoginView from "@/views/LoginView.vue";

function buildRouter() {
  const stub = { template: "<div />" };
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "home", component: stub },
      { path: "/login", name: "login", component: stub },
      { path: "/register", name: "register", component: stub },
      { path: "/password-reset", name: "password-reset", component: stub },
      { path: "/tasks", name: "tasks", component: stub },
    ],
  });
}

async function mountLogin(path = "/login") {
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = buildRouter();
  await router.push(path);
  await router.isReady();
  const wrapper = mount(LoginView, {
    global: { plugins: [pinia, router] },
  });
  await flushPromises();
  return { wrapper, router };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.requestEmailOtp.mockResolvedValue({
    message: "验证码已发送至你的邮箱，请查收。",
    cooldown_seconds: 60,
  });
  mocks.verifyEmailOtp.mockResolvedValue({
    access_token: "acc-123",
    refresh_token: "ref-123",
    token_type: "bearer",
    expires_in: 1800,
  });
  mocks.me.mockResolvedValue({
    id: "u1",
    username: "alice_1234",
    email: "alice@example.com",
    role: "user",
    is_active: true,
    email_verified: true,
  });
});

describe("登录/注册一体流（Email OTP + Magic Link）", () => {
  it("首屏展示 Google / GitHub OAuth 入口与统一邮箱输入，不再区分登录/注册 Tab", async () => {
    const { wrapper } = await mountLogin();

    expect(wrapper.get('[data-test="auth-title"]').text()).toBe("登录或创建账号");
    expect(wrapper.get('[data-test="oauth-google-btn"]').attributes("href")).toBe(
      "/api/v1/auth/oauth/google/authorize",
    );
    expect(wrapper.get('[data-test="oauth-github-btn"]').attributes("href")).toBe(
      "/api/v1/auth/oauth/github/authorize",
    );
    expect(wrapper.find('[data-test="otp-email-input"]').exists()).toBe(true);
    expect(wrapper.find('[role="tab"]').exists()).toBe(false);
  });

  it("提交邮箱后发送验证码并进入 6 位验证码输入态，验证通过跳转工作台", async () => {
    const { wrapper, router } = await mountLogin();

    await wrapper.get('[data-test="otp-email-input"]').setValue("alice@example.com");
    await wrapper.get("form").trigger("submit.prevent");
    await flushPromises();

    expect(mocks.requestEmailOtp).toHaveBeenCalledWith("alice@example.com");
    expect(wrapper.get('[data-test="auth-title"]').text()).toBe("输入邮箱验证码");
    expect(wrapper.get('[data-test="otp-sent-notice"]').text()).toContain("验证码已发送");

    await wrapper.get('[data-test="otp-code-input"]').setValue("123456");
    await wrapper.get("form").trigger("submit.prevent");
    await flushPromises();

    expect(mocks.verifyEmailOtp).toHaveBeenCalledWith({
      email: "alice@example.com",
      code: "123456",
    });
    expect(router.currentRoute.value.name).toBe("tasks");
  });

  it("URL 带 magic_token 时自动核验并跳转工作台", async () => {
    const { router } = await mountLogin("/login?magic_token=token-abc-1234567890");
    expect(mocks.verifyEmailOtp).toHaveBeenCalledWith({
      magic_token: "token-abc-1234567890",
    });
    expect(router.currentRoute.value.name).toBe("tasks");
  });

  it("点击底部「使用账号密码登录」可展开密码表单（兼容管理员与忘记密码）", async () => {
    const { wrapper } = await mountLogin();

    await wrapper.get('[data-test="toggle-password-login"]').trigger("click");
    await flushPromises();

    expect(wrapper.find("#account").exists()).toBe(true);
    expect(wrapper.find("#password").exists()).toBe(true);
    expect(wrapper.get("#password").attributes("placeholder")).toBe("请输入密码");
    expect(wrapper.find('[data-test="forgot-password"]').exists()).toBe(true);
  });

  it("Logo 是可点的首页链接，左侧双层溯源卡支持 [1]/[2] 切换且引用不硬截断", async () => {
    const { wrapper } = await mountLogin();

    const brand = wrapper.get('[data-test="brand-home"]');
    expect(brand.element.tagName).toBe("A");
    expect(brand.attributes("href")).toBe("/");

    const tag = wrapper.get('[data-test="proof-traceable-tag"]');
    expect(tag.element.tagName).toBe("SPAN");
    expect(tag.element.closest("a")).toBeNull();

    const footer = wrapper.get('[data-test="login-proof-footer"]');
    expect(footer.text()).toContain("ACL Findings 2025");
    expect(footer.text()).not.toContain("...");
    await wrapper.get('[data-test="login-proof-marker-2"]').trigger("click");
    expect(wrapper.get('[data-test="login-proof-footer"]').text()).toContain("J. Biomed. Inform. 2025");
    expect(wrapper.text()).toContain("你负责判断，系统负责");
  });

  it("OAuth 入口文案全中文化，验证码重发按钮带 tabular-nums 与最小宽度防抖，ErrorBanner 可关闭复位", async () => {
    const { wrapper } = await mountLogin();

    expect(wrapper.get('[data-test="oauth-google-btn"]').text()).toBe("使用 Google 账号继续");
    expect(wrapper.get('[data-test="oauth-github-btn"]').text()).toBe("使用 GitHub 账号继续");

    // 验证码步骤检查倒计时防抖类与 ErrorBanner @dismiss
    mocks.verifyEmailOtp.mockRejectedValue(new Error("验证码错误"));
    await wrapper.get('[data-test="otp-email-input"]').setValue("alice@example.com");
    await wrapper.get("form").trigger("submit.prevent");
    await flushPromises();

    const resendBtn = wrapper.get('[data-test="otp-resend-btn"]');
    expect(resendBtn.classes()).toContain("tabular-nums");
    expect(resendBtn.classes()).toContain("min-w-[6.5rem]");

    await wrapper.get('[data-test="otp-code-input"]').setValue("000000");
    await wrapper.get("form").trigger("submit.prevent");
    await flushPromises();
    expect(wrapper.find('[role="alert"]').text()).toContain("验证码错误");

    await wrapper.get('[role="alert"] button').trigger("click");
    await flushPromises();
    await wrapper.get("form").trigger("submit.prevent");
    await flushPromises();
    expect(wrapper.find('[role="alert"]').text()).toContain("验证码错误");
  });

  it("从 Step 2（验证码）点击「更换邮箱」返回 Step 1 时不触发运行时崩溃", async () => {
    const { wrapper } = await mountLogin();

    await wrapper.get('[data-test="otp-email-input"]').setValue("alice@example.com");
    await wrapper.get("form").trigger("submit.prevent");
    await flushPromises();
    expect(wrapper.find('[data-test="otp-code-input"]').exists()).toBe(true);

    await wrapper.get('[data-test="otp-change-email"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="otp-email-input"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="otp-code-input"]').exists()).toBe(false);
    expect(wrapper.get('[data-test="auth-title"]').text()).toBe("登录或创建账号");
  });

  it("从 Step 2 切换至 Step 3（账号密码）再返回 Step 1 时不触发运行时崩溃", async () => {
    const { wrapper } = await mountLogin();

    await wrapper.get('[data-test="otp-email-input"]').setValue("alice@example.com");
    await wrapper.get("form").trigger("submit.prevent");
    await flushPromises();
    expect(wrapper.find('[data-test="otp-code-input"]').exists()).toBe(true);

    await wrapper.get('[data-test="toggle-password-login"]').trigger("click");
    await flushPromises();
    expect(wrapper.find("#account").exists()).toBe(true);
    expect(wrapper.find('[data-test="otp-code-input"]').exists()).toBe(false);

    await wrapper.get('[data-test="back-to-otp"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-test="otp-email-input"]').exists()).toBe(true);
    expect(wrapper.find("#account").exists()).toBe(false);
    expect(wrapper.get('[data-test="auth-title"]').text()).toBe("登录或创建账号");
  });

  it("页面仅存在 1 个主标题 <h1>（左侧品牌面板降级为 <p>，W7）", async () => {
    const { wrapper } = await mountLogin("/login");
    expect(wrapper.findAll("h1")).toHaveLength(1);
    expect(wrapper.get("h1").attributes("data-test")).toBe("auth-title");
  });
});
