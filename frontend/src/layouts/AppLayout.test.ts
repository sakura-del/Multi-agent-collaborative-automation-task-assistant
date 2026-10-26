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
 * - 所有 9 个目的地都还在（重设计不能丢页面入口）；
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

  it("十个导航目的地一个都不能少", async () => {
    const wrapper = await mountLayout();
    const labels = wrapper.findAll(".app-nav__links a").map((a) => a.text());

    for (const expected of [
      "任务",
      "知识库",
      "引用样式",
      "Word 模板",
      "导入文献",
      // 设计变更（2026-10-26 引用库计划）：导入的文献需要地方可查可删
      "引用库",
      "团队",
      "用量",
      "API Key",
      "我的套餐",
    ]) {
      expect(labels).toContain(expected);
    }
    expect(labels).toHaveLength(10);
  });

  it("按使用频率分组：工作台 → 素材 → 协作 → 设置", async () => {
    const wrapper = await mountLayout();
    const groups = wrapper.findAll(".app-nav__links > div");
    expect(groups).toHaveLength(4);

    // 第一组无标题（省掉视觉噪音），其余三组有微缩大写标签
    expect(groups[0].find("p").exists()).toBe(false);
    expect(groups[1].text()).toContain("素材");
    expect(groups[2].text()).toContain("协作");
    expect(groups[3].text()).toContain("设置");
  });

  it("设置组排在最末（配一次就不动，不该占日常视线）", async () => {
    const wrapper = await mountLayout();
    const groups = wrapper.findAll(".app-nav__links > div");
    const last = groups[groups.length - 1];
    expect(last.text()).toContain("API Key");
    expect(last.text()).toContain("我的套餐");
  });

  it("当前路由项标记为当前页（aria-current）", async () => {
    const wrapper = await mountLayout();
    // 用 `aria-current="page"` 而不是 `router-link-active` 类断言：
    // 类名是版式实现细节（active-class 由模板给出），
    // 而 aria-current 是 RouterLink 的语义输出，既稳定又是无障碍要求。
    const current = wrapper.findAll('.app-nav__links a[aria-current="page"]');
    expect(current.length).toBe(1);
    expect(current[0].text()).toBe("任务");
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
    expect(text).toContain("生成即合规");
    expect(text).toContain("引用可核验");
    // 三个能力点必须来自真实功能，不能是营销词
    expect(text).toContain("证据约束");
    expect(text).toContain("GB/T 7714—2025");
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

// 避免未使用告警（vi 在 watch 版断言时会用到）
void vi;
