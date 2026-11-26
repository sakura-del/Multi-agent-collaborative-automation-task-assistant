import fs from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";

vi.mock("@/api/growth", () => ({
  trackEvent: vi.fn(),
  initUmami: vi.fn(),
  submitFeedback: vi.fn(),
}));

import DemoReviewView from "./DemoReviewView.vue";
import { trackEvent } from "@/api/growth";

async function mountDemo() {
  const stub = { template: "<div />" };
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "landing", component: stub },
      { path: "/demo", name: "demo", component: DemoReviewView },
      { path: "/login", name: "login", component: stub },
      { path: "/register", name: "register", component: stub },
      { path: "/privacy", name: "privacy", component: stub },
      { path: "/terms", name: "terms", component: stub },
    ],
  });
  await router.push("/demo");
  await router.isReady();
  const wrapper = mount(DemoReviewView, { global: { plugins: [router] } });
  await flushPromises();
  return wrapper;
}

describe("DemoReviewView（免登录只读示例综述页 · Issue #61 P0-2）", () => {
  it("渲染只读示范标牌、完整综述正文、三视角切换与引文交互", async () => {
    const wrapper = await mountDemo();

    expect(trackEvent).toHaveBeenCalledWith("demo_view");
    expect(wrapper.get('[data-test="demo-readonly-badge"]').text()).toContain("免登录只读示例");
    expect(wrapper.get('[data-test="demo-report-title"]').text()).toContain("检索增强生成");

    // 切换角标 [2] 联动右侧证据详情
    await wrapper.get('[data-test="demo-page-marker-2"]').trigger("click");
    expect(wrapper.get('[data-test="demo-page-verifier"]').text()).toContain("10.1016/j.jbi.2025.104612");

    // 切换 CSL 格式到 APA
    await wrapper.get('[data-test="demo-page-csl-select"]').setValue("apa");
    expect(wrapper.get('[data-test="demo-page-citation-text"]').text()).toContain("Lin, Y., Chen, W., & Patel, R.");

    // 切到 PRISMA 与 矩阵视角
    await wrapper.get('[data-test="demo-tab-prisma"]').trigger("click");
    expect(wrapper.find('[data-test="demo-panel-prisma"]').exists()).toBe(true);
    await wrapper.get('[data-test="demo-tab-matrix"]').trigger("click");
    expect(wrapper.find('[data-test="demo-panel-matrix"]').exists()).toBe(true);

    wrapper.unmount();
  });

  it("index.html 包含首屏骨架壳与微信/QQ 分享 OG 标签，且 public/og-cover.png 存在（P0-1 & P2-9）", () => {
    const htmlPath = path.resolve(__dirname, "../../index.html");
    const html = fs.readFileSync(htmlPath, "utf-8");
    expect(html).toContain('class="vp-boot"');
    expect(html).toContain('property="og:title"');
    expect(html).toContain('property="og:description"');
    expect(html).toContain('property="og:image"');
    expect(html).toContain("og-cover.png");

    const ogImg = path.resolve(__dirname, "../../public/og-cover.png");
    expect(fs.existsSync(ogImg)).toBe(true);
    expect(fs.statSync(ogImg).size).toBeGreaterThan(1000);
  });
});
