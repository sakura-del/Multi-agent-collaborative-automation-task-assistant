import { describe, expect, it } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";

import LandingView from "./LandingView.vue";

async function mountLanding() {
  const stub = { template: "<div />" };
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "landing", component: LandingView },
      { path: "/login", name: "login", component: stub },
      { path: "/register", name: "register", component: stub },
    ],
  });
  await router.push("/");
  await router.isReady();
  const wrapper = mount(LandingView, {
    global: { plugins: [router] },
  });
  await flushPromises();
  return { wrapper, router };
}

describe("LandingView（Elicit 风格落地页）", () => {
  it("渲染四段式 Hero 文案与学术数据源信任条", async () => {
    const { wrapper } = await mountLanding();

    const eyebrow = wrapper.get('[data-test="landing-eyebrow"]').text();
    expect(eyebrow).toContain("arXiv");
    expect(eyebrow).toContain("GB/T 7714-2025");
    expect(eyebrow).not.toContain("支持");
    expect(wrapper.get('[data-test="landing-title"]').text()).toContain("每一句综述");
    expect(wrapper.get('[data-test="landing-title"]').text()).toContain("皆有出处");
    expect(wrapper.get('[data-test="landing-cta-primary"]').text()).toContain("开始综述");
    expect(wrapper.get('[data-test="landing-cta-primary"]').attributes("href")).toBe("/register");

    const trust = wrapper.get('[data-test="landing-trust-bar"]').text();
    expect(trust).toContain("arXiv");
    expect(trust).toContain("PubMed");
    expect(trust).toContain("PRISMA 2020");
    expect(trust).toContain("CSL 1.0");
  });

  it("Tab 1 PRISMA 筛选：点击纳入/排除可实时联动顶部最终纳入计数", async () => {
    const { wrapper } = await mountLanding();

    const counter = wrapper.get('[data-test="prisma-included-counter"]');
    expect(counter.text()).toContain("18");

    // 把原本「待定」的 p3 改为「纳入」→ 18 变 19
    await wrapper.get('[data-test="demo-verdict-p3-include"]').trigger("click");
    expect(counter.text()).toContain("19");

    // 把原本「纳入」的 p1 改为「排除」→ 19 变 18
    await wrapper.get('[data-test="demo-verdict-p1-exclude"]').trigger("click");
    expect(counter.text()).toContain("18");
  });

  it("Tab 2 矩阵提取：点击单元格弹出原文片段定位面板", async () => {
    const { wrapper } = await mountLanding();

    await wrapper.get('[data-test="showcase-tab-matrix"]').trigger("click");
    expect(wrapper.find('[data-test="showcase-panel-matrix"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="matrix-quote-panel"]').exists()).toBe(false);

    await wrapper.get('[data-test="matrix-cell-m1-finding"]').trigger("click");
    const quotePanel = wrapper.get('[data-test="matrix-quote-panel"]');
    expect(quotePanel.text()).toContain("18.6%");
    expect(quotePanel.text()).toContain("1.2%");
  });

  it("Tab 3 可溯源综述：支持切换引用角标与实时重渲染 CSL 格式", async () => {
    const { wrapper } = await mountLanding();

    await wrapper.get('[data-test="showcase-tab-review"]').trigger("click");
    expect(wrapper.find('[data-test="showcase-panel-review"]').exists()).toBe(true);

    // 默认高亮 [1] + GB/T 7714-2025
    expect(wrapper.get('[data-test="citation-verifier-panel"]').text()).toContain("10.18653/v1/2025.findings-acl.418");
    expect(wrapper.get('[data-test="demo-csl-output"]').text()).toContain("LIN Y");

    // 切换 CSL 到 APA 7th
    await wrapper.get('[data-test="demo-csl-select"]').setValue("apa");
    expect(wrapper.get('[data-test="demo-csl-output"]').text()).toContain("Lin, Y., Chen, W., & Patel, R. (2025)");

    // 点击正文 [2] 切换右侧核验文献
    await wrapper.get('[data-test="citation-marker-2"]').trigger("click");
    expect(wrapper.get('[data-test="citation-verifier-panel"]').text()).toContain("10.1016/j.jbi.2025.104612");
  });

  it("点击「阅读示范报告」打开真实示范报告抽屉，支持播放流程演示", async () => {
    const { wrapper } = await mountLanding();

    // 播放流程演示可启动与暂停
    const replayBtn = wrapper.get('[data-test="showcase-replay-toggle"]');
    expect(replayBtn.text()).toContain("播放流程演示");
    await replayBtn.trigger("click");
    expect(replayBtn.attributes("aria-pressed")).toBe("true");
    await replayBtn.trigger("click");
    expect(replayBtn.attributes("aria-pressed")).toBe("false");

    // 点击次按钮「阅读示范报告」打开右侧抽屉
    await wrapper.get('[data-test="landing-cta-demo"]').trigger("click");
    await flushPromises();
    const drawer = document.body.querySelector('[data-test="landing-report-drawer"]');
    expect(drawer).not.toBeNull();
    expect(drawer?.textContent).toContain("Crossref / OpenAlex 四态引文核验报告");
    wrapper.unmount();
  });

  it("Hero 顶部特性胶囊在窄屏不将绿点与文本拆成上下两行（Issue #63）", async () => {
    const { wrapper } = await mountLanding();

    const eyebrow = wrapper.get('[data-test="landing-eyebrow"]');
    const eyebrowClasses = (eyebrow.attributes("class") ?? "").split(/\s+/);
    expect(eyebrowClasses).toContain("inline-flex");
    expect(eyebrowClasses).toContain("max-w-full");
    expect(eyebrowClasses).not.toContain("flex-wrap");

    const dotClasses = (
      wrapper.get('[data-test="landing-eyebrow-dot"]').attributes("class") ?? ""
    ).split(/\s+/);
    expect(dotClasses).toContain("shrink-0");

    const textEl = wrapper.get('[data-test="landing-eyebrow-text"]');
    const textClasses = (textEl.attributes("class") ?? "").split(/\s+/);
    expect(textClasses).toContain("min-w-0");
    expect(textEl.text()).toContain("arXiv");
    expect(textEl.text()).toContain("GB/T 7714-2025");
  });

  it("支持三态主题模式选择（light / dark / system）且 Tab 3 导出提示支持关闭", async () => {
    document.documentElement.classList.remove("dark");
    localStorage.clear();
    const { wrapper } = await mountLanding();

    const modeTrigger = wrapper.get('[data-test="landing-theme-mode-select"]');
    await modeTrigger.trigger("click");
    await flushPromises();
    await wrapper.get('[data-test="landing-theme-mode-dark"]').trigger("click");
    expect(localStorage.getItem("vp:theme")).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    await modeTrigger.trigger("click");
    await flushPromises();
    await wrapper.get('[data-test="landing-theme-mode-light"]').trigger("click");
    expect(localStorage.getItem("vp:theme")).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    // 切到 Tab 3 并触发导出提示，可手动关闭
    await wrapper.get('[data-test="showcase-tab-review"]').trigger("click");
    await wrapper.get('[data-test="demo-export-docx"]').trigger("click");
    expect(wrapper.find('[data-test="demo-export-notice"]').exists()).toBe(true);
    await wrapper.get('[data-test="demo-export-notice-close"]').trigger("click");
    expect(wrapper.find('[data-test="demo-export-notice"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("顶栏导航按钮单行不折行，Hero 副标题使用 max-w-3xl text-balance 防孤立折行（W6）", async () => {
    const { wrapper } = await mountLanding();

    const brandClasses = (wrapper.get('[data-test="landing-brand"]').attributes("class") ?? "").split(/\s+/);
    expect(brandClasses).toContain("shrink-0");

    const loginClasses = (wrapper.get('[data-test="landing-login"]').attributes("class") ?? "").split(/\s+/);
    expect(loginClasses).toContain("whitespace-nowrap");
    expect(loginClasses).toContain("shrink-0");

    const registerClasses = (wrapper.get('[data-test="landing-register"]').attributes("class") ?? "").split(/\s+/);
    expect(registerClasses).toContain("whitespace-nowrap");
    expect(registerClasses).toContain("shrink-0");

    const subtitle = wrapper.find("main section p.mx-auto.mt-5");
    expect(subtitle.exists()).toBe(true);
    const subClasses = (subtitle.attributes("class") ?? "").split(/\s+/);
    expect(subClasses).toContain("max-w-3xl");
    expect(subClasses).toContain("text-balance");
    wrapper.unmount();
  });
});
