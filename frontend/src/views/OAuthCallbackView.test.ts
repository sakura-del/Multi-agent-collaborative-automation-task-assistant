import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";

const mocks = vi.hoisted(() => ({
  exchangeOAuthCode: vi.fn(),
  me: vi.fn(),
}));

vi.mock("@/api/auth", () => ({
  authApi: {
    exchangeOAuthCode: mocks.exchangeOAuthCode,
    me: mocks.me,
    getOAuthAuthorizeUrl: (p: string) => `/oauth/${p}`,
  },
}));

vi.mock("@/api/client", () => ({
  default: { get: vi.fn(), defaults: { baseURL: "http://api.test" } },
  getAccessToken: () => null,
  getRefreshToken: () => null,
  persistTokens: vi.fn(),
  clearTokens: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}));

import OAuthCallbackView from "@/views/OAuthCallbackView.vue";

async function mountCallback(path: string) {
  setActivePinia(createPinia());
  const stub = { template: "<div />" };
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/oauth/callback", name: "oauth-callback", component: OAuthCallbackView },
      { path: "/login", name: "login", component: stub },
      { path: "/tasks", name: "tasks", component: stub },
    ],
  });
  await router.push(path);
  await router.isReady();
  const wrapper = mount(OAuthCallbackView, { global: { plugins: [router] } });
  await flushPromises();
  return { wrapper, router };
}

describe("OAuthCallbackView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("缺少 code 时展示 ErrorBanner 且 @dismiss 清空错误，返回链接支持深色和聚焦环", async () => {
    const { wrapper } = await mountCallback("/oauth/callback");

    const alert = wrapper.find('[role="alert"]');
    expect(alert.exists()).toBe(true);
    expect(alert.text()).toContain("缺少第三方授权交换码");

    const link = wrapper.get("a");
    expect(link.classes()).toContain("dark:text-primary");
    expect(link.classes()).toContain("focus-visible:ring-2");

    // 点击关闭后错误清零（切回加载提示态，不再卡在错误态）
    await wrapper.get('[role="alert"] button').trigger("click");
    await flushPromises();
    expect(wrapper.find("svg.animate-spin").classes()).toContain("motion-reduce:animate-none");
  });
});
