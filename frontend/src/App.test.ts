import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { defineComponent, onMounted, onUnmounted } from "vue";
import { Motion } from "motion-v";

import App from "@/App.vue";

function mockReducedMotion(reduced: boolean) {
  return vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    matches: query.includes("prefers-reduced-motion") ? reduced : false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(() => true),
  }));
}

describe("App 全局壳层与路由过渡", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.body.innerHTML = "";
  });

  it("挂载 Toaster、路由过渡 Motion（reduced-motion 下 y 归零）且工作台内部切页时不重挂载 AppLayout", async () => {
    mockReducedMotion(true);
    setActivePinia(createPinia());

    let layoutMountCount = 0;
    let layoutUnmountCount = 0;

    const StubLayout = defineComponent({
      setup() {
        onMounted(() => {
          layoutMountCount += 1;
        });
        onUnmounted(() => {
          layoutUnmountCount += 1;
        });
      },
      template: `<div data-test="stub-app-layout"><RouterView /></div>`,
    });

    const StubLogin = defineComponent({ template: `<div data-test="stub-login">登录</div>` });
    const StubTasks = defineComponent({ template: `<div data-test="stub-tasks">任务</div>` });
    const StubKnowledge = defineComponent({ template: `<div data-test="stub-knowledge">知识库</div>` });

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/login", name: "login", component: StubLogin },
        {
          path: "/",
          component: StubLayout,
          children: [
            { path: "tasks", name: "tasks", component: StubTasks },
            { path: "knowledge", name: "knowledge", component: StubKnowledge },
          ],
        },
      ],
    });

    await router.push("/login");
    await router.isReady();

    const wrapper = mount(App, {
      global: { plugins: [router] },
    });
    await flushPromises();

    // 验证 Toaster 已挂载且 App 外层路由使用 Motion 过渡并在 reduced-motion 下 y=0
    expect(wrapper.find('[data-test="stub-login"]').exists()).toBe(true);
    expect(wrapper.findComponent({ name: "Toaster" }).exists()).toBe(true);
    const motionComp = wrapper.findComponent(Motion);
    expect(motionComp.exists()).toBe(true);
    const motionInitial = (
      motionComp as unknown as { props: (k: string) => { y?: number } | undefined }
    ).props("initial");
    expect(motionInitial?.y).toBe(0);

    // 切入工作台
    await router.push("/tasks");
    await flushPromises();
    await new Promise((resolve) => setTimeout(resolve, 160));
    await flushPromises();
    expect(layoutMountCount).toBe(1);
    expect(layoutUnmountCount).toBe(0);
    expect(wrapper.find('[data-test="stub-tasks"]').exists()).toBe(true);

    // 工作台内部从 /tasks 切到 /knowledge：AppLayout 不得卸载重建
    await router.push("/knowledge");
    await flushPromises();
    await new Promise((resolve) => setTimeout(resolve, 160));
    await flushPromises();
    expect(layoutMountCount).toBe(1);
    expect(layoutUnmountCount).toBe(0);
    expect(wrapper.find('[data-test="stub-knowledge"]').exists()).toBe(true);

    wrapper.unmount();
  });
});
