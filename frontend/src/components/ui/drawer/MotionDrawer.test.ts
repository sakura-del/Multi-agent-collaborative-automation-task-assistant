import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, ref } from "vue";

import { MotionDialog } from "@/components/ui/dialog";
import { MotionDrawer } from "./index";

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

describe("MotionDrawer 侧滑抽屉原语", () => {
  const mountedWrappers: Array<{ unmount: () => void }> = [];

  afterEach(() => {
    while (mountedWrappers.length > 0) {
      mountedWrappers.pop()?.unmount();
    }
    document.body.style.overflow = "";
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("支持 side='right' / 'bottom' 布局、widthClass 与 backdropAttrs / panelAttrs 透传", async () => {
    const rightWrapper = mount(MotionDrawer, {
      props: {
        open: true,
        side: "right",
        widthClass: "max-w-2xl",
        ariaLabel: "批注抽屉",
        backdropAttrs: { "data-test": "annotation-backdrop" },
        panelAttrs: { "data-test": "annotation-drawer" },
      },
      slots: { default: "<p>批注内容</p>" },
    });
    mountedWrappers.push(rightWrapper);
    await flushPromises();

    const backdrop = document.body.querySelector('[data-test="annotation-backdrop"]');
    const panel = document.body.querySelector('[data-test="annotation-drawer"]');
    expect(backdrop).not.toBeNull();
    expect(panel).not.toBeNull();
    expect(panel?.tagName).toBe("ASIDE");
    expect(panel?.getAttribute("role")).toBe("dialog");
    expect(panel?.getAttribute("aria-modal")).toBe("true");
    expect(panel?.className).toContain("max-w-2xl");

    const bottomWrapper = mount(MotionDrawer, {
      props: {
        open: true,
        side: "bottom",
        ariaLabel: "底部抽屉",
        panelAttrs: { "data-test": "bottom-drawer" },
      },
      slots: { default: "<p>底部内容</p>" },
    });
    mountedWrappers.push(bottomWrapper);
    await flushPromises();

    const bottomPanel = document.body.querySelector('[data-test="bottom-drawer"]');
    expect(bottomPanel).not.toBeNull();
    expect(bottomPanel?.className).toContain("max-h-");
  });

  it("支持遮罩点击、Esc 关闭、焦点管理、Tab 陷阱及嵌套滚动锁还原", async () => {
    document.body.style.overflow = "scroll";

    const Host = defineComponent({
      components: { MotionDrawer },
      setup() {
        const open = ref(false);
        return { open };
      },
      template: `
        <div>
          <button type="button" data-test="open-btn" @click="open = true">打开抽屉</button>
          <MotionDrawer
            v-model:open="open"
            aria-label="溯源抽屉"
            :backdrop-attrs="{ 'data-test': 'drawer-backdrop' }"
            :panel-attrs="{ 'data-test': 'drawer-panel' }"
          >
            <button type="button" data-test="d-btn-1">按钮1</button>
            <button type="button" data-test="d-btn-2">按钮2</button>
          </MotionDrawer>
        </div>
      `,
    });

    const hostEl = document.createElement("div");
    document.body.appendChild(hostEl);
    const wrapper = mount(Host, { attachTo: hostEl });
    mountedWrappers.push(wrapper);

    const openBtn = wrapper.get('[data-test="open-btn"]').element as HTMLButtonElement;
    openBtn.focus();
    await wrapper.get('[data-test="open-btn"]').trigger("click");
    await flushPromises();

    expect(document.body.style.overflow).toBe("hidden");
    const btn1 = document.body.querySelector('[data-test="d-btn-1"]') as HTMLButtonElement;
    const btn2 = document.body.querySelector('[data-test="d-btn-2"]') as HTMLButtonElement;
    expect(document.activeElement).toBe(btn1);

    btn2.focus();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(btn1);

    // 点击遮罩关闭抽屉并还原焦点与 overflow
    const backdrop = document.body.querySelector('[data-test="drawer-backdrop"]') as HTMLElement;
    backdrop.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flushPromises();

    expect(document.activeElement).toBe(openBtn);
    expect(document.body.style.overflow).toBe("scroll");
  });

  it("prefers-reduced-motion: reduce 下抽屉开关与 Esc 关闭正常", async () => {
    mockReducedMotion(true);
    const wrapper = mount(MotionDrawer, {
      props: {
        open: true,
        ariaLabel: "减动效抽屉",
        panelAttrs: { "data-test": "reduced-drawer" },
      },
      slots: { default: "<button type='button'>关闭</button>" },
    });
    mountedWrappers.push(wrapper);
    await flushPromises();

    expect(document.body.querySelector('[data-test="reduced-drawer"]')).not.toBeNull();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it("非 LIFO 嵌套关闭：先关底层 MotionDialog 不偷取顶层 MotionDrawer 焦点，待 Drawer 关闭后才还原外部按钮", async () => {
    const Host = defineComponent({
      components: { MotionDialog, MotionDrawer },
      setup() {
        const dialogOpen = ref(false);
        const drawerOpen = ref(false);
        return { dialogOpen, drawerOpen };
      },
      template: `
        <div>
          <button type="button" data-test="outside-btn" @click="dialogOpen = true">外部打开</button>
          <MotionDialog v-model:open="dialogOpen" aria-label="底层弹窗" data-test="base-dialog">
            <button type="button" data-test="open-drawer-btn" @click="drawerOpen = true">打开抽屉</button>
          </MotionDialog>
          <MotionDrawer
            v-model:open="drawerOpen"
            aria-label="顶层抽屉"
            :panel-attrs="{ 'data-test': 'top-drawer' }"
          >
            <button type="button" data-test="drawer-btn">抽屉内部按钮</button>
          </MotionDrawer>
        </div>
      `,
    });

    const hostEl = document.createElement("div");
    document.body.appendChild(hostEl);
    const wrapper = mount(Host, { attachTo: hostEl });
    mountedWrappers.push(wrapper);

    const outsideBtn = wrapper.get('[data-test="outside-btn"]').element as HTMLButtonElement;
    outsideBtn.focus();
    expect(document.activeElement).toBe(outsideBtn);

    await wrapper.get('[data-test="outside-btn"]').trigger("click");
    await flushPromises();

    const openDrawerBtn = document.body.querySelector('[data-test="open-drawer-btn"]') as HTMLButtonElement;
    expect(document.activeElement).toBe(openDrawerBtn);
    openDrawerBtn.click();
    await flushPromises();

    const drawerBtn = document.body.querySelector('[data-test="drawer-btn"]') as HTMLButtonElement;
    expect(document.activeElement).toBe(drawerBtn);

    // 非 LIFO：先程序化关闭底层 MotionDialog
    wrapper.vm.dialogOpen = false;
    await flushPromises();

    // 焦点必须仍留在仍打开的顶层 MotionDrawer 内
    expect(document.activeElement).toBe(drawerBtn);

    // 再关闭顶层 MotionDrawer，此时全局已无活跃弹层，应还原回最初外部按钮
    wrapper.vm.drawerOpen = false;
    await flushPromises();

    expect(document.activeElement).toBe(outsideBtn);
  });

  it("IME 组合输入（isComposing / keyCode=229）按 Escape 不误关闭抽屉，结束后正常关闭", async () => {
    const hostEl = document.createElement("div");
    document.body.appendChild(hostEl);

    const wrapper = mount(MotionDrawer, {
      attachTo: hostEl,
      props: {
        open: true,
        ariaLabel: "IME 输入抽屉",
        panelAttrs: { "data-test": "ime-drawer" },
      },
      slots: {
        default: '<textarea data-test="ime-textarea" />',
      },
    });
    mountedWrappers.push(wrapper);
    await flushPromises();

    const textarea = document.body.querySelector('[data-test="ime-textarea"]') as HTMLTextAreaElement;
    expect(textarea).not.toBeNull();
    textarea.focus();
    expect(document.activeElement).toBe(textarea);

    // 1) isComposing: true 时按 Esc 不得误关抽屉
    window.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Escape",
        isComposing: true,
        bubbles: true,
        cancelable: true,
      }),
    );
    await flushPromises();
    expect(wrapper.emitted("close")).toBeUndefined();
    expect(document.body.querySelector('[data-test="ime-drawer"]')).not.toBeNull();

    // 2) keyCode === 229（兼容老式/Safari IME）同样不得关闭抽屉
    const safariImeEvent = new KeyboardEvent("keydown", {
      key: "Escape",
      isComposing: false,
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(safariImeEvent, "keyCode", { value: 229 });
    window.dispatchEvent(safariImeEvent);
    await flushPromises();
    expect(wrapper.emitted("close")).toBeUndefined();

    // 3) 组合输入结束后按 isComposing: false 的 Esc 正常关闭
    window.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Escape",
        isComposing: false,
        bubbles: true,
        cancelable: true,
      }),
    );
    await flushPromises();
    expect(wrapper.emitted("close")).toHaveLength(1);
    expect(wrapper.emitted("update:open")?.[0]).toEqual([false]);
  });
});
