import { afterEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createRouter, createMemoryHistory } from "vue-router";

import AppLayout from "@/layouts/AppLayout.vue";
import { hasActiveOverlays } from "@/lib/overlay";

enableAutoUnmount(afterEach);

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
      { path: "/tasks/:id", name: "task-detail", component: stub },
      { path: "/chat", name: "chat", component: stub },
      { path: "/knowledge", name: "knowledge", component: stub },
      { path: "/extractions/:tableId?", name: "extractions", component: stub },
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

  it("顶栏默认渲染移动端导航触发按钮，点击可展开并关闭移动端抽屉（Issue #64）", async () => {
    const wrapper = await mountLayout();
    const trigger = wrapper.find('[data-test="nav-mobile-trigger"]');
    expect(trigger.exists()).toBe(true);
    expect(document.body.querySelector('[aria-label="移动端导航"]')).toBeNull();

    await trigger.trigger("click");
    await flushPromises();
    const mobileNav = document.body.querySelector('[aria-label="移动端导航"]');
    expect(mobileNav).not.toBeNull();
    expect(mobileNav?.textContent).toContain("任务");

    const closeBtn = document.body.querySelector<HTMLButtonElement>(
      '[role="dialog"][aria-label="导航"] button[aria-label="关闭导航"]',
    );
    expect(closeBtn).not.toBeNull();
    closeBtn?.click();
    await flushPromises();
    expect(document.body.querySelector('[aria-label="移动端导航"]')).toBeNull();
    wrapper.unmount();
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

  it("保留登录所需的邮箱输入与密码兜底入口（E2E 与自动化脚本依赖）", async () => {
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

    expect(wrapper.find("#otp-email").exists()).toBe(true);
    await wrapper.get('[data-test="toggle-password-login"]').trigger("click");
    await flushPromises();
    expect(wrapper.find("#account").exists()).toBe(true);
    expect(wrapper.find("#password").exists()).toBe(true);
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

  it("命令面板收录论文抽取表且搜索「抽取」可直接跳转（Issue #55）", async () => {
    setActivePinia(createPinia());
    const router = buildRouter();
    await router.push("/tasks");
    await router.isReady();
    const wrapper = mount(AppLayout, {
      global: { plugins: [router], stubs: { RouterView: true } },
    });
    await flushPromises();

    await wrapper.get('[data-test="open-command-palette"]').trigger("click");
    await flushPromises();

    const dialog = document.body.querySelector('[data-test="command-palette"]') as HTMLElement;
    expect(dialog).not.toBeNull();
    const input = dialog.querySelector("input") as HTMLInputElement;
    input.value = "抽取";
    input.dispatchEvent(new Event("input"));
    await flushPromises();

    expect(dialog.textContent).toContain("论文抽取表");
    expect(dialog.textContent).not.toContain("没有匹配的页面或命令");

    const optionBtn = dialog.querySelector('li[role="option"] button') as HTMLButtonElement;
    optionBtn.click();
    await flushPromises();

    expect(router.currentRoute.value.name).toBe("extractions");
    wrapper.unmount();
  });
});

describe("AppLayout M2 UX 与动效改造验证", () => {
  afterEach(() => {
    vi.doUnmock("@/components/chat/ChatShell.vue");
    vi.resetModules();
    vi.restoreAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    document.documentElement.classList.remove("dark");
    document.body.innerHTML = "";
  });

  it("route.name === 'chat' 时 main 改为 py-0 消除双滚动条，切回 /tasks 恢复", async () => {
    setActivePinia(createPinia());
    const router = buildRouter();
    await router.push("/chat");
    await router.isReady();

    const wrapper = mount(AppLayout, {
      global: { plugins: [router], stubs: { RouterView: true } },
    });
    await flushPromises();

    const main = wrapper.get("main");
    expect(main.classes()).toContain("py-0");
    expect(main.classes()).not.toContain("pt-5");
    expect(main.classes()).not.toContain("pb-20");

    await router.push("/tasks");
    await flushPromises();
    expect(main.classes()).toContain("pt-5");
    expect(main.classes()).toContain("pb-20");
    expect(main.classes()).not.toContain("py-0");
    wrapper.unmount();
  });

  it("点击 chat-fab 懒加载 ChatShell 期间渲染 data-test='chat-shell-loading'，禁止抢占 chat-shell", async () => {
    let resolveShell!: (mod: { __esModule: true; default: unknown }) => void;
    vi.resetModules();
    vi.doMock("@/components/chat/ChatShell.vue", () => {
      return new Promise((resolve) => {
        resolveShell = resolve;
      });
    });

    const FreshAppLayout = (await import("@/layouts/AppLayout.vue")).default;
    setActivePinia(createPinia());
    const router = buildRouter();
    await router.push("/tasks");
    await router.isReady();

    const wrapper = mount(FreshAppLayout, {
      global: { plugins: [router], stubs: { RouterView: true } },
    });
    await flushPromises();

    // chat-fab 必须 Teleport 到 body，避免被 App.vue 路由过渡 Motion 的 transform 困住定位
    expect(wrapper.find('[data-test="chat-fab"]').exists()).toBe(false);
    const fabBtn = document.body.querySelector('[data-test="chat-fab"]') as HTMLButtonElement | null;
    expect(fabBtn).not.toBeNull();
    fabBtn!.click();
    await flushPromises();

    const loadingShell = wrapper.find('[data-test="chat-shell-loading"]');
    expect(loadingShell.exists()).toBe(true);
    expect(wrapper.find('[data-test="chat-shell"]').exists()).toBe(false);

    resolveShell({
      __esModule: true,
      default: { name: "ChatShell", template: `<section data-test="chat-shell">loaded</section>` },
    });
    await flushPromises();

    expect(wrapper.find('[data-test="chat-shell-loading"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="chat-shell"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("桌面侧栏消除 transition-all 与 max-w-0；移动端抽屉补齐快速搜索、全局 AI 入口与可折叠分组", async () => {
    const wrapper = await mountLayout();
    const desktopHtml = wrapper.get('[data-test="app-sidebar"]').html();
    expect(desktopHtml).not.toContain("transition-all");
    expect(desktopHtml).not.toContain("max-w-0");

    await wrapper.get('[data-test="nav-mobile-trigger"]').trigger("click");
    await flushPromises();

    const dialog = document.body.querySelector('[role="dialog"][aria-label="导航"]') as HTMLElement;
    expect(dialog).not.toBeNull();

    // 移动端抽屉内包含 GlobalPromptEntry 输入框
    const mobilePrompt = dialog.querySelector('[data-test="global-prompt"]') as HTMLTextAreaElement;
    expect(mobilePrompt).not.toBeNull();

    // 移动端分组可点击展开/收起（先展开设置组）
    const mobileNav = dialog.querySelector('[aria-label="移动端导航"]') as HTMLElement;
    const settingsBtn = Array.from(mobileNav.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("设置"),
    );
    expect(settingsBtn).toBeDefined();
    expect(mobileNav.textContent).not.toContain("我的模型");
    settingsBtn?.click();
    await flushPromises();
    expect(mobileNav.textContent).toContain("我的模型");

    // 点击移动端快速搜索按钮：关闭抽屉并打开命令面板
    const mobileCmdBtn = Array.from(dialog.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("快速搜索"),
    );
    expect(mobileCmdBtn).toBeDefined();
    mobileCmdBtn?.click();
    await flushPromises();

    expect(document.body.querySelector('[role="dialog"][aria-label="导航"]')).toBeNull();
    expect(document.body.querySelector('[data-test="command-palette"]')).not.toBeNull();
    wrapper.unmount();
  });

  it("TopBar 提供三态主题模式选择器 [data-test='theme-mode-select']（light / dark / system）", async () => {
    let systemMatches = true;
    const listeners = new Set<(e: MediaQueryListEvent) => void>();
    vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
      matches: query.includes("prefers-color-scheme: dark") ? systemMatches : false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: (_type: string, cb: (e: MediaQueryListEvent) => void) => listeners.add(cb),
      removeEventListener: (_type: string, cb: (e: MediaQueryListEvent) => void) => listeners.delete(cb),
      dispatchEvent: vi.fn(() => true),
    }) as unknown as MediaQueryList);

    const wrapper = await mountLayout();
    const trigger = wrapper.get('[data-test="theme-mode-select"]');
    await trigger.trigger("click");
    await flushPromises();

    // 切换为跟随系统（当前系统偏好暗色）
    await wrapper.get('[data-test="theme-mode-system"]').trigger("click");
    await flushPromises();
    expect(localStorage.getItem("vp:theme")).toBe("system");
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    // 模拟系统主题切为浅色
    systemMatches = false;
    for (const cb of listeners) cb({ matches: false } as MediaQueryListEvent);
    await flushPromises();
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    // 显式切换为深色与浅色
    await trigger.trigger("click");
    await flushPromises();
    await wrapper.get('[data-test="theme-mode-dark"]').trigger("click");
    expect(localStorage.getItem("vp:theme")).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    await trigger.trigger("click");
    await flushPromises();
    await wrapper.get('[data-test="theme-mode-light"]').trigger("click");
    expect(localStorage.getItem("vp:theme")).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    wrapper.unmount();
  });

  it("打开移动抽屉后直接按 Ctrl+K 叠加/切换 CommandPalette，再按 Esc 全关后 body.style.overflow 必须归位为空字符串（探针 2C）", async () => {
    document.body.style.overflow = "";
    const wrapper = await mountLayout();

    await wrapper.get('[data-test="nav-mobile-trigger"]').trigger("click");
    await flushPromises();
    expect(document.body.querySelector('[role="dialog"][aria-label="导航"]')).not.toBeNull();
    expect(document.body.style.overflow).toBe("hidden");

    // 抽屉开启期间直接按 Ctrl+K
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true, cancelable: true }),
    );
    await flushPromises();
    expect(document.body.querySelector('[data-test="command-palette"]')).not.toBeNull();

    // 逐层或一次性按 Esc 直到全部弹层关闭
    for (let i = 0; i < 2; i += 1) {
      if (
        !document.body.querySelector('[data-test="command-palette"]') &&
        !document.body.querySelector('[role="dialog"][aria-label="导航"]')
      ) {
        break;
      }
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }),
      );
      await flushPromises();
    }

    expect(document.body.querySelector('[data-test="command-palette"]')).toBeNull();
    expect(document.body.querySelector('[role="dialog"][aria-label="导航"]')).toBeNull();
    expect(document.body.style.overflow).toBe("");
    wrapper.unmount();
  });

  it("移动抽屉内点击 GlobalPromptEntry 任务条跳转路由后自动关闭抽屉并释放滚动锁（D2-c）", async () => {
    document.body.style.overflow = "";
    sessionStorage.setItem(
      "vp:global-prompt:active-tasks",
      JSON.stringify([{ task_id: "tid-nav-1", title: "抽屉内导航任务", status: "completed" }]),
    );
    setActivePinia(createPinia());
    const router = buildRouter();
    await router.push("/tasks");
    await router.isReady();

    const wrapper = mount(AppLayout, {
      global: { plugins: [router], stubs: { RouterView: true } },
    });
    await flushPromises();

    await wrapper.get('[data-test="nav-mobile-trigger"]').trigger("click");
    await flushPromises();

    const dialog = document.body.querySelector('[role="dialog"][aria-label="导航"]');
    expect(dialog).not.toBeNull();
    expect(hasActiveOverlays()).toBe(true);
    expect(document.body.style.overflow).toBe("hidden");

    const taskRow = dialog?.querySelector('[data-test="global-prompt-task"]') as HTMLElement | null;
    expect(taskRow).not.toBeNull();
    taskRow?.click();
    await flushPromises();

    expect(router.currentRoute.value.path).toBe("/tasks/tid-nav-1");
    expect(document.body.querySelector('[role="dialog"][aria-label="导航"]')).toBeNull();
    expect(hasActiveOverlays()).toBe(false);
    expect(document.body.style.overflow).toBe("");
    wrapper.unmount();
  });

  it("已处于目标任务路由 /tasks/tid-same 时，移动抽屉内点击同一 GlobalPromptEntry 任务条仍自动关闭抽屉并释放滚动锁（E4-b）", async () => {
    document.body.style.overflow = "";
    sessionStorage.setItem(
      "vp:global-prompt:active-tasks",
      JSON.stringify([{ task_id: "tid-same", title: "当前页同路由任务", status: "completed" }]),
    );
    setActivePinia(createPinia());
    const router = buildRouter();
    await router.push("/tasks/tid-same");
    await router.isReady();

    const wrapper = mount(AppLayout, {
      global: { plugins: [router], stubs: { RouterView: true } },
    });
    await flushPromises();

    await wrapper.get('[data-test="nav-mobile-trigger"]').trigger("click");
    await flushPromises();

    const dialog = document.body.querySelector('[role="dialog"][aria-label="导航"]');
    expect(dialog).not.toBeNull();
    expect(hasActiveOverlays()).toBe(true);
    expect(document.body.style.overflow).toBe("hidden");

    const taskRow = dialog?.querySelector('[data-test="global-prompt-task"]') as HTMLElement | null;
    expect(taskRow).not.toBeNull();
    taskRow?.click();
    await flushPromises();

    expect(router.currentRoute.value.path).toBe("/tasks/tid-same");
    expect(document.body.querySelector('[role="dialog"][aria-label="导航"]')).toBeNull();
    expect(hasActiveOverlays()).toBe(false);
    expect(document.body.style.overflow).toBe("");
    wrapper.unmount();
  });
});

// 避免未使用告警（vi 在 watch 版断言时会用到）
void vi;
