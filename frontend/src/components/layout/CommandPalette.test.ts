import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { defineComponent, ref } from "vue";

import CommandPalette from "@/components/layout/CommandPalette.vue";

function buildRouter() {
  const stub = { template: "<div />" };
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/tasks", name: "tasks", component: stub },
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

describe("CommandPalette 命令面板", () => {
  const wrappers: Array<{ unmount: () => void }> = [];

  beforeEach(() => {
    setActivePinia(createPinia());
  });

  afterEach(() => {
    while (wrappers.length) {
      wrappers.pop()?.unmount();
    }
    document.body.style.overflow = "";
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("接入 MotionDialog：role='dialog'、body 滚动锁、自动聚焦输入框，按 Esc 关闭并还原焦点", async () => {
    const router = buildRouter();
    await router.push("/tasks");
    await router.isReady();

    const Host = defineComponent({
      components: { CommandPalette },
      setup() {
        const open = ref(false);
        return { open };
      },
      template: `
        <div>
          <button type="button" data-test="palette-trigger" @click="open = true">打开</button>
          <CommandPalette v-model:open="open" />
        </div>
      `,
    });

    const hostEl = document.createElement("div");
    document.body.appendChild(hostEl);
    const wrapper = mount(Host, {
      attachTo: hostEl,
      global: { plugins: [router] },
    });
    wrappers.push(wrapper);

    const trigger = wrapper.get('[data-test="palette-trigger"]').element as HTMLButtonElement;
    trigger.focus();

    await wrapper.get('[data-test="palette-trigger"]').trigger("click");
    await flushPromises();

    const dialog = document.body.querySelector('[data-test="command-palette"]') as HTMLElement;
    expect(dialog).not.toBeNull();
    expect(dialog.getAttribute("role")).toBe("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toBe("命令面板");
    expect(document.body.style.overflow).toBe("hidden");

    const input = dialog.querySelector("input") as HTMLInputElement;
    expect(document.activeElement).toBe(input);

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    await flushPromises();

    expect(wrapper.vm.open).toBe(false);
    expect(document.activeElement).toBe(trigger);
    expect(document.body.style.overflow).toBe("");
  });

  it("按 group 分组展示标题且分组标题不占 role='option'", async () => {
    const router = buildRouter();
    await router.push("/tasks");
    await router.isReady();

    const wrapper = mount(CommandPalette, {
      props: { open: true },
      global: { plugins: [router] },
    });
    wrappers.push(wrapper);
    await flushPromises();

    const dialog = document.body.querySelector('[data-test="command-palette"]') as HTMLElement;
    const headers = Array.from(dialog.querySelectorAll('li[role="presentation"]')).map((el) =>
      el.textContent?.trim(),
    );
    expect(headers).toContain("页面跳转");
    expect(headers).toContain("设置");
    expect(headers).toContain("快捷操作");

    const options = Array.from(dialog.querySelectorAll('li[role="option"]'));
    expect(options.length).toBeGreaterThan(5);
    for (const opt of options) {
      expect(["页面跳转", "设置", "管理", "快捷操作"]).not.toContain(opt.textContent?.trim());
    }
  });

  it("ArrowDown / ArrowUp 切换活跃项时调用 scrollIntoView({ block: 'nearest' })", async () => {
    const router = buildRouter();
    await router.push("/tasks");
    await router.isReady();

    const scrollSpy = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollSpy;

    const wrapper = mount(CommandPalette, {
      props: { open: true },
      global: { plugins: [router] },
    });
    wrappers.push(wrapper);
    await flushPromises();

    const dialog = document.body.querySelector('[data-test="command-palette"]') as HTMLElement;
    const input = dialog.querySelector("input") as HTMLInputElement;

    input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
    await flushPromises();
    expect(scrollSpy).toHaveBeenCalledWith({ block: "nearest" });

    scrollSpy.mockClear();
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true, cancelable: true }));
    await flushPromises();
    expect(scrollSpy).toHaveBeenCalledWith({ block: "nearest" });
  });

  it("IME 组合输入（isComposing / keyCode=229）按 Enter 不误触发命令执行", async () => {
    const router = buildRouter();
    await router.push("/tasks");
    await router.isReady();
    const pushSpy = vi.spyOn(router, "push");

    const wrapper = mount(CommandPalette, {
      props: { open: true },
      global: { plugins: [router] },
    });
    wrappers.push(wrapper);
    await flushPromises();

    const dialog = document.body.querySelector('[data-test="command-palette"]') as HTMLElement;
    const input = dialog.querySelector("input") as HTMLInputElement;

    input.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true, cancelable: true }),
    );
    await flushPromises();
    expect(pushSpy).not.toHaveBeenCalled();

    const safariIme = new KeyboardEvent("keydown", {
      key: "Enter",
      isComposing: false,
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(safariIme, "keyCode", { value: 229 });
    input.dispatchEvent(safariIme);
    await flushPromises();
    expect(pushSpy).not.toHaveBeenCalled();
  });

  it("通过 Tab 聚焦选项按钮时同步 activeIndex，按 Enter 执行当前聚焦项", async () => {
    const router = buildRouter();
    await router.push("/tasks");
    await router.isReady();
    const pushSpy = vi.spyOn(router, "push");

    const wrapper = mount(CommandPalette, {
      props: { open: true },
      global: { plugins: [router] },
    });
    wrappers.push(wrapper);
    await flushPromises();

    const dialog = document.body.querySelector('[data-test="command-palette"]') as HTMLElement;
    const options = Array.from(dialog.querySelectorAll('li[role="option"]'));
    const knowledgeBtn = options[2]?.querySelector("button") as HTMLButtonElement;
    expect(knowledgeBtn.textContent).toContain("知识库");

    knowledgeBtn.dispatchEvent(new FocusEvent("focus"));
    await flushPromises();

    expect(options[2].getAttribute("aria-selected")).toBe("true");
    knowledgeBtn.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }),
    );
    await flushPromises();

    expect(pushSpy).toHaveBeenCalledWith({ name: "knowledge" });
  });
});
