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

  it("版式、品牌主色与邮箱占位与登录页统一（Issue #39）", async () => {
    const { wrapper } = await mountReset();

    expect(wrapper.find('[data-test="auth-brand-panel"]').exists()).toBe(true);
    expect(wrapper.findAll("h1")).toHaveLength(1);
    expect(wrapper.get('[data-test="reset-email"]').attributes("placeholder")).toBe(
      "name@university.edu",
    );
    const submitClasses = wrapper.get('[data-test="reset-request-submit"]').classes();
    expect(submitClasses).toContain("w-full");
    expect(submitClasses).toContain("bg-[#0F4C3A]");
  });

  it("ErrorBanner 绑定 @dismiss 关闭后可再现同文错误", async () => {
    const { wrapper } = await mountReset();

    await wrapper.get('[data-test="reset-email"]').setValue("invalid");
    await wrapper.get('[data-test="reset-request-form"]').trigger("submit");
    await flushPromises();
    expect(wrapper.find('[data-test="reset-error"]').text()).toContain("有效");

    await wrapper.get('[data-test="reset-error"] button').trigger("click");
    await flushPromises();
    await wrapper.get('[data-test="reset-request-form"]').trigger("submit");
    await flushPromises();
    expect(wrapper.find('[data-test="reset-error"]').text()).toContain("有效");
  });

  it("申请阶段与确认阶段双向切换及点击「返回登录」均不触发运行时崩溃", async () => {
    const { wrapper, router } = await mountReset();

    expect(wrapper.find('[data-test="reset-request-form"]').exists()).toBe(true);

    await wrapper.setProps({ token: "reset-tok-123" });
    await flushPromises();
    expect(wrapper.find('[data-test="reset-confirm-form"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="reset-request-form"]').exists()).toBe(false);

    await wrapper.setProps({ token: "" });
    await flushPromises();
    expect(wrapper.find('[data-test="reset-request-form"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="reset-confirm-form"]').exists()).toBe(false);

    await wrapper.setProps({ token: "reset-tok-456" });
    await flushPromises();
    expect(wrapper.find('[data-test="reset-confirm-form"]').exists()).toBe(true);

    await wrapper.get('[data-test="reset-back-to-login"]').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.name).toBe("login");
  });
});
