import { describe, expect, it } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";

import PrivacyView from "./PrivacyView.vue";
import TermsView from "./TermsView.vue";

function buildRouter() {
  const stub = { template: "<div />" };
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "landing", component: stub },
      { path: "/privacy", name: "privacy", component: PrivacyView },
      { path: "/terms", name: "terms", component: TermsView },
    ],
  });
}

describe("PrivacyView & TermsView（隐私政策与用户协议 · Issue #61 P1-5）", () => {
  it("隐私政策页讲清文献存储位置、隔离方式与绝不用于训练模型", async () => {
    const router = buildRouter();
    await router.push("/privacy");
    await router.isReady();
    const wrapper = mount(PrivacyView, { global: { plugins: [router] } });
    await flushPromises();

    expect(wrapper.get('[data-test="privacy-title"]').text()).toContain("隐私政策");
    const text = wrapper.text();
    expect(text).toContain("绝不");
    expect(text).toContain("训练");
    expect(text).toContain("PostgreSQL");
    expect(text).toContain("加密");
    wrapper.unmount();
  });

  it("用户协议页渲染免费体验额度与学术合规责任条款", async () => {
    const router = buildRouter();
    await router.push("/terms");
    await router.isReady();
    const wrapper = mount(TermsView, { global: { plugins: [router] } });
    await flushPromises();

    expect(wrapper.get('[data-test="terms-title"]').text()).toContain("用户协议");
    expect(wrapper.text()).toContain("每月 3 篇");
    expect(wrapper.text()).toContain("学术");
    wrapper.unmount();
  });
});
