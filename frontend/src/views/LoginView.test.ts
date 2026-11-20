import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";

// ALTCHA 是 Web Component（自带 fetch 挑战、注册自定义元素）；单测只关心文案与路由，
// 不加载真实实现，否则 happy-dom 里会去打网络。
vi.mock("altcha", () => ({}));
vi.mock("altcha/i18n/zh-cn", () => ({}));

const state = vi.hoisted(() => ({ captchaEnabled: false }));

vi.mock("@/api/client", () => ({
  default: {
    get: () => Promise.resolve({ data: { enabled: state.captchaEnabled } }),
    defaults: { baseURL: "http://api.test" },
  },
  getAccessToken: () => null,
  getRefreshToken: () => null,
  persistTokens: vi.fn(),
  clearTokens: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}));

vi.mock("@/api/account", () => ({ requestEmailVerification: vi.fn() }));

import LoginView from "@/views/LoginView.vue";

/**
 * 公开页 UI 打磨（Issue #26）的前端契约：
 * - 顶部文案随页签切换（注册态不能显示登录引导）；
 * - `/register` 可直达，页签与地址栏是同一个事实来源；
 * - Logo 是链接、「证据可回溯」是标签不是假链接；
 * - 中文文案里没有「， 系统」这种逗号后空格。
 */
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

const title = (wrapper: Awaited<ReturnType<typeof mountLogin>>["wrapper"]) =>
  wrapper.get('[data-test="auth-title"]').text();

beforeEach(() => {
  state.captchaEnabled = false;
});

describe("登录/注册页", () => {
  it("登录态：顶部是登录引导", async () => {
    const { wrapper } = await mountLogin();

    expect(wrapper.get('[data-test="auth-eyebrow"]').text()).toBe("Welcome back");
    expect(title(wrapper)).toBe("继续你的研究");
    expect(wrapper.get('[data-test="auth-hint"]').text()).toContain("登录以访问");
  });

  it("切到注册页签：文案改成注册，地址栏同步成 /register", async () => {
    const { wrapper, router } = await mountLogin();

    await wrapper.findAll('[role="tab"]')[1].trigger("click");
    await flushPromises();

    expect(router.currentRoute.value.name).toBe("register");
    expect(wrapper.get('[data-test="auth-eyebrow"]').text()).toBe("Create account");
    expect(title(wrapper)).toBe("创建你的账号");
    expect(wrapper.get('[data-test="auth-hint"]').text()).toContain("第一篇综述");
    // 注册不再自动登录（邮箱验证是门禁），按钮不能再说「注册并登录」
    expect(wrapper.get('form button[type="submit"]').text()).toBe("创建账号");
  });

  it("直接访问 /register：落地即注册表单，字段有占位提示", async () => {
    const { wrapper, router } = await mountLogin("/register");

    expect(router.currentRoute.value.name).toBe("register");
    expect(title(wrapper)).toBe("创建你的账号");
    expect(wrapper.get("#username").attributes("placeholder")).toBeTruthy();
    expect(wrapper.get("#email").attributes("placeholder")).toBeTruthy();
    expect(wrapper.get("#password").attributes("placeholder")).toBe("至少 8 位");
  });

  it("从注册页签切回登录：地址栏也回到 /login", async () => {
    const { wrapper, router } = await mountLogin("/register");

    await wrapper.findAll('[role="tab"]')[0].trigger("click");
    await flushPromises();

    expect(router.currentRoute.value.name).toBe("login");
    expect(title(wrapper)).toBe("继续你的研究");
  });

  it("Logo 是可点的首页链接，「证据可回溯」只是标签", async () => {
    const { wrapper } = await mountLogin();

    const brand = wrapper.get('[data-test="brand-home"]');
    expect(brand.element.tagName).toBe("A");
    expect(brand.attributes("href")).toBe("/");

    const tag = wrapper.get('[data-test="proof-traceable-tag"]');
    expect(tag.element.tagName).toBe("SPAN");
    expect(tag.element.closest("a")).toBeNull();
  });

  it("中文正文没有逗号后空格（Vue 会把源码换行压成空格）", async () => {
    const { wrapper } = await mountLogin();

    expect(wrapper.text()).not.toMatch(/[\u4e00-\u9fa5]，\s/);
    expect(wrapper.text()).toContain("你负责判断，系统负责");
  });

  it("注册态渲染人机验证时，保护文案只有一处且大小写统一", async () => {
    state.captchaEnabled = true;
    const { wrapper } = await mountLogin("/register");

    const caption = wrapper.get('[data-test="captcha-caption"]');
    expect(caption.text()).toBe("由 Altcha 保护");
    expect(wrapper.text().match(/Altcha/gi)?.length).toBe(1);
    // widget 自带页脚必须真的关掉：v3 不认 hideFooter 属性，只能走 configuration
    const configuration = JSON.parse(wrapper.get("altcha-widget").attributes("configuration") ?? "{}");
    expect(configuration).toEqual({ hideFooter: true });
  });

  it("登录态不渲染人机验证", async () => {
    state.captchaEnabled = true;
    const { wrapper } = await mountLogin("/login");

    expect(wrapper.find('[data-test="captcha-caption"]').exists()).toBe(false);
  });
});
