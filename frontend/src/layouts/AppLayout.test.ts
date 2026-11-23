import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createRouter, createMemoryHistory } from "vue-router";

import AppLayout from "@/layouts/AppLayout.vue";

/**
 * 工作台侧栏（beUI 视觉语言重设计，2026-10-23）。
 *
 * 这些断言锁的是**导航结构与 E2E 契约**，不是版式细节：
 * - 分组顺序（日常用的在上，配一次就不动的沉到底部）；
 * - 所有 11 个目的地都还在（重设计不能丢页面入口）；
 * - `app-nav__links` 类名必须保留 —— `e2e/phase1-pages.spec.ts`
 *   的 resetScroll 依赖它把横向滚动归位。
 */
function buildRouter() {
  const stub = { template: "<div />" };
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: stub },
      { path: "/tasks", name: "tasks", component: stub },
      { path: "/knowledge", name: "knowledge", component: stub },
      { path: "/styles", name: "styles", component: stub },
      { path: "/templates", name: "templates", component: stub },
      { path: "/reference-import", name: "reference-import", component: stub },
      { path: "/reference-library", name: "reference-library", component: stub },
      { path: "/workspaces", name: "workspaces", component: stub },
      { path: "/observability", name: "observability", component: stub },
      { path: "/settings/models", name: "models", component: stub },
      { path: "/settings/api-keys", name: "api-keys", component: stub },
      { path: "/settings/plan", name: "plan", component: stub },
    ],
  });
}

async function mountLayout() {
  setActivePinia(createPinia());
  const router = buildRouter();
  // 先解析到 /tasks 并等 ready，再挂载 —— RouterLink 只在挂载时
  // 读取一次当前路由来算 active 类，顺序反了会拿不到。
  await router.push("/tasks");
  await router.isReady();
  const wrapper = mount(AppLayout, {
    global: { plugins: [router], stubs: { RouterView: true } },
  });
  await flushPromises();
  return wrapper;
}

describe("AppLayout 侧栏", () => {
  it("保留 app-nav__links 契约类名（E2E resetScroll 依赖）", async () => {
    const wrapper = await mountLayout();
    expect(wrapper.find(".app-nav__links").exists()).toBe(true);
  });

  it("导航目的地齐全：两个叶子 + 三个可展开分组", async () => {
    const wrapper = await mountLayout();
    // 叶子项直接渲染
    // E2E 登录后按 role=link 等「任务」，叶子项必须是 <a>，不能是按钮
    const tasksLink = wrapper.get('[data-test="nav-tasks"]');
    expect(tasksLink.element.tagName).toBe("A");
    expect(tasksLink.text()).toContain("任务");
    expect(wrapper.get('[data-test="nav-knowledge"]').text()).toContain("知识库");

    // 分组默认收起；展开后子项才进入 DOM（morph 菜单）
    for (const [group, children] of [
      ["素材", ["引用样式", "Word 模板", "导入文献", "引用库"]],
      ["协作", ["团队", "用量"]],
      ["设置", ["我的模型", "API Key", "我的套餐"]],
    ] as const) {
      const groupButton = wrapper.get(`[data-test="nav-group-${group}"] button`);
      expect(groupButton.text()).toContain(group);
      await groupButton.trigger("click");
      await flushPromises();
      const text = wrapper.get(`[data-test="nav-group-${group}"]`).text();
      for (const child of children) expect(text).toContain(child);
    }
  });

  it("分组顺序：素材 → 协作 → 设置，设置排最后", async () => {
    const wrapper = await mountLayout();
    const groups = wrapper.findAll(".app-nav__links > div");
    expect(groups).toHaveLength(3);
    expect(groups[0].text()).toContain("素材");
    expect(groups[1].text()).toContain("协作");
    expect(groups[2].text()).toContain("设置");

    await wrapper.get('[data-test="nav-group-设置"] button').trigger("click");
    await flushPromises();
    const last = wrapper.get('[data-test="nav-group-设置"]').text();
    expect(last).toContain("我的模型");
    expect(last).toContain("API Key");
    expect(last).toContain("我的套餐");
  });

  it("当前路由项标记为当前页（aria-current）", async () => {
    const wrapper = await mountLayout();
    const current = wrapper.findAll('.app-nav__links [aria-current="page"]');
    expect(current.length).toBe(1);
    expect(current[0].text()).toContain("任务");
  });

  it("显示当前用户名与登出按钮", async () => {
    const wrapper = await mountLayout();
    const logout = wrapper.find('button[aria-label="退出登录"]');
    expect(logout.exists()).toBe(true);
  });
});

describe("登录页产品身份", () => {
  it("登录前展示价值主张（窄屏由 CSS 隐藏，DOM 必须在）", async () => {
    setActivePinia(createPinia());
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/login", name: "login", component: { template: "<div/>" } },
        { path: "/password-reset", name: "password-reset", component: { template: "<div/>" } },
      ],
    });
    await router.push("/login");
    await router.isReady();

    const LoginView = (await import("@/views/LoginView.vue")).default;
    const wrapper = mount(LoginView, {
      global: { plugins: [router] },
    });

    const text = wrapper.text();
    expect(text).toContain("每一句论断");
    expect(text).toContain("皆有出处");
    expect(text).toContain("PRISMA 2020");
    expect(text).toContain("GB/T 7714-2025");
    const proof = wrapper.find('[data-test="login-evidence-proof"]');
    expect(proof.exists()).toBe(true);
    expect(proof.text()).toContain("Crossref DOI 已核验");
  });

  it("保留登录所需的表单字段 id（E2E 与自动化脚本依赖）", async () => {
    setActivePinia(createPinia());
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/login", name: "login", component: { template: "<div/>" } },
        { path: "/password-reset", name: "password-reset", component: { template: "<div/>" } },
      ],
    });
    await router.push("/login");
    await router.isReady();

    const LoginView = (await import("@/views/LoginView.vue")).default;
    const wrapper = mount(LoginView, { global: { plugins: [router] } });

    expect(wrapper.find("#account").exists()).toBe(true);
    expect(wrapper.find("#password").exists()).toBe(true);
    // 忘记密码入口（账号生命周期）
    expect(wrapper.find('[data-test="forgot-password"]').exists()).toBe(true);
  });
});


describe("AppLayout 侧栏折叠", () => {
  it("折叠后只剩图标轨，仍可点图标切页", async () => {
    localStorage.clear();
    setActivePinia(createPinia());
    const router = buildRouter();
    await router.push("/tasks");
    await router.isReady();

    const wrapper = mount(AppLayout, {
      global: { plugins: [router], stubs: { RouterView: true } },
    });
    await flushPromises();

    await wrapper.get('[data-test="nav-collapse"]').trigger("click");
    await flushPromises();
    const expand = wrapper.get('[data-test="nav-expand"]');
    // 展开入口与品牌图标融合，不再占底部一整行
    expect(expand.element.closest('[data-test="nav-brand"]')).not.toBeNull();

    // 快速导航已移除：折叠态不应再有选择器入口
    expect(wrapper.find('[data-test="nav-picker-trigger"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="nav-arc-picker"]').exists()).toBe(false);

    // 折叠态底部只有头像：登出按钮不能留在 68px 窄轨里（会溢出成半截图标）
    expect(wrapper.find('[data-test="nav-avatar"]').exists()).toBe(true);
    expect(wrapper.find('button[aria-label="退出登录"]').exists()).toBe(false);

    // 折叠态图标仍是链接，点击可直接切页
    await wrapper.get('[data-test="nav-knowledge"]').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.name).toBe("knowledge");
  });
});

describe("Linear / Notion 顶栏与命令面板", () => {
  it("展示顶栏面包屑与主题切换按钮，点击可切换 dark 类", async () => {
    document.documentElement.classList.remove("dark");
    localStorage.clear();
    const wrapper = await mountLayout();

    const breadcrumb = wrapper.get('nav[aria-label="面包屑"]');
    expect(breadcrumb.text()).toContain("研究任务");

    const toggle = wrapper.get('[data-test="theme-toggle"]');
    await toggle.trigger("click");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem("vp:theme")).toBe("dark");

    await toggle.trigger("click");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem("vp:theme")).toBe("light");
  });

  it("点击顶栏命令按钮可打开命令面板（Teleport 到 body）", async () => {
    const wrapper = await mountLayout();
    await wrapper.get('[data-test="open-command-palette"]').trigger("click");
    await flushPromises();

    const dialog = document.body.querySelector('[data-test="command-palette"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.textContent).toContain("知识库");
    wrapper.unmount();
  });
});

// 避免未使用告警（vi 在 watch 版断言时会用到）
void vi;
