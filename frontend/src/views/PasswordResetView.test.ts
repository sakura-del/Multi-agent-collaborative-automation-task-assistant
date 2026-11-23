import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";

vi.mock("@/api/account", () => ({
  requestPasswordReset: vi.fn(),
  confirmPasswordReset: vi.fn(),
}));

import PasswordResetView from "@/views/PasswordResetView.vue";

/**
 * 重置密码页不再是「孤岛」（Issue #26-3）：有品牌页头，也有明确的返回登录入口。
 */
function buildRouter() {
  const stub = { template: "<div />" };
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "home", component: stub },
      { path: "/login", name: "login", component: stub },
      { path: "/password-reset", name: "password-reset", component: stub },
    ],
  });
}

async function mountReset() {
  const router = buildRouter();
  await router.push("/password-reset");
  await router.isReady();
  const wrapper = mount(PasswordResetView, { global: { plugins: [router] } });
  await flushPromises();
  return { wrapper, router };
}

describe("重置密码页", () => {
  it("保留原表单与标题（E2E 依赖 heading=重置密码）", async () => {
    const { wrapper } = await mountReset();

    expect(wrapper.get("h1").text()).toBe("重置密码");
    expect(wrapper.find('[data-test="reset-email"]').exists()).toBe(true);
  });

  it("有品牌页头与「返回登录」入口", async () => {
    const { wrapper } = await mountReset();

    const brand = wrapper.get('a[aria-label="VeriPaper 首页"]');
    expect(brand.attributes("href")).toBe("/");

    const back = wrapper.get('[data-test="reset-back-to-login"]');
    expect(back.text()).toContain("返回登录");
    expect(back.attributes("href")).toBe("/login");
  });

  it("点「返回登录」回到登录页", async () => {
    const { wrapper, router } = await mountReset();

    await wrapper.get('[data-test="reset-back-to-login"]').trigger("click");
    await flushPromises();

    expect(router.currentRoute.value.name).toBe("login");
  });
});
