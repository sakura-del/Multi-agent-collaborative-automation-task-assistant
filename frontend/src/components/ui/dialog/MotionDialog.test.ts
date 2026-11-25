import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, ref } from "vue";

import { getFocusableElements } from "@/lib/overlay";
import { MotionDialog } from "./index";

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

describe("MotionDialog 居中弹窗原语", () => {
  const mountedWrappers: Array<{ unmount: () => void }> = [];

  afterEach(() => {
    while (mountedWrappers.length > 0) {
      mountedWrappers.pop()?.unmount();
    }
    document.body.style.overflow = "";
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("Teleport 到 body 并透传 data-test、class、role='dialog' 与 aria-modal", async () => {
    const wrapper = mount(MotionDialog, {
      props: {
        open: true,
        ariaLabel: "新建项目",
        class: "max-w-2xl",
      },
      attrs: {
        "data-test": "my-dialog",
      },
      slots: {
        default: "<p>弹窗正文</p>",
      },
    });
    mountedWrappers.push(wrapper);
    await flushPromises();

    const dialog = document.body.querySelector('[data-test="my-dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.getAttribute("role")).toBe("dialog");
    expect(dialog?.getAttribute("aria-modal")).toBe("true");
    expect(dialog?.getAttribute("aria-label")).toBe("新建项目");
    expect(dialog?.className).toContain("max-w-2xl");
    expect(dialog?.textContent).toContain("弹窗正文");
  });

  it("支持遮罩点击与 Esc 关闭，且可通过 closeOnBackdrop / closeOnEsc 禁用", async () => {
    const wrapper = mount(MotionDialog, {
      props: {
        open: true,
        ariaLabel: "可关闭弹窗",
        backdropAttrs: { "data-test": "my-backdrop" },
      },
      attrs: { "data-test": "closable-dialog" },
      slots: { default: "<button type='button'>内部按钮</button>" },
    });
    mountedWrappers.push(wrapper);
    await flushPromises();

    // 点击面板内部不关闭
    const panel = document.body.querySelector('[data-test="closable-dialog"]') as HTMLElement;
    panel.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted("close")).toBeUndefined();

    // 点击遮罩触发关闭
    const backdrop = document.body.querySelector('[data-test="my-backdrop"]') as HTMLElement;
    backdrop.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted("update:open")?.[0]).toEqual([false]);
    expect(wrapper.emitted("close")).toHaveLength(1);

    // 禁用遮罩和 Esc 关闭
    await wrapper.setProps({ closeOnBackdrop: false, closeOnEsc: false });
    backdrop.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted("close")).toHaveLength(1);

    // 开启 Esc 关闭
    await wrapper.setProps({ closeOnEsc: true });
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted("close")).toHaveLength(2);
  });

  it("打开自动聚焦首控件、关闭还原原触发按钮焦点，并实现 Tab / Shift+Tab 焦点陷阱", async () => {
    const Host = defineComponent({
      components: { MotionDialog },
      setup() {
        const open = ref(false);
        return { open };
      },
      template: `
        <div>
          <button type="button" data-test="trigger" @click="open = true">打开</button>
          <MotionDialog v-model:open="open" aria-label="焦点弹窗" data-test="focus-dialog">
            <button type="button" data-test="first-btn">第一个</button>
            <button type="button" data-test="second-btn">第二个</button>
          </MotionDialog>
        </div>
      `,
    });

    const hostEl = document.createElement("div");
    document.body.appendChild(hostEl);
    const wrapper = mount(Host, { attachTo: hostEl });
    mountedWrappers.push(wrapper);

    const trigger = wrapper.get('[data-test="trigger"]').element as HTMLButtonElement;
    trigger.focus();
    expect(document.activeElement).toBe(trigger);

    await wrapper.get('[data-test="trigger"]').trigger("click");
    await flushPromises();

    const firstBtn = document.body.querySelector('[data-test="first-btn"]') as HTMLButtonElement;
    const secondBtn = document.body.querySelector('[data-test="second-btn"]') as HTMLButtonElement;
    expect(document.activeElement).toBe(firstBtn);

    // 焦点在末控件按 Tab 循环回首控件
    secondBtn.focus();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(firstBtn);

    // 焦点在首控件按 Shift+Tab 循环回末控件
    firstBtn.focus();
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true }),
    );
    expect(document.activeElement).toBe(secondBtn);

    // Esc 关闭后还原焦点到触发按钮
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();
    expect(document.activeElement).toBe(trigger);
  });

  it("多层弹层叠加保持 body 滚动锁引用计数与 unmount 兜底还原", async () => {
    document.body.style.overflow = "auto";

    const dialogA = mount(MotionDialog, {
      props: { open: true, ariaLabel: "A" },
      slots: { default: "<p>A</p>" },
    });
    mountedWrappers.push(dialogA);
    await flushPromises();
    expect(document.body.style.overflow).toBe("hidden");

    const dialogB = mount(MotionDialog, {
      props: { open: true, ariaLabel: "B" },
      slots: { default: "<p>B</p>" },
    });
    mountedWrappers.push(dialogB);
    await flushPromises();
    expect(document.body.style.overflow).toBe("hidden");

    // 先关闭 B，因为 A 仍打开，overflow 必须仍为 hidden
    await dialogB.setProps({ open: false });
    await flushPromises();
    expect(document.body.style.overflow).toBe("hidden");

    // 卸载 A 后计数归零，必须准确恢复打开前的 "auto"
    dialogA.unmount();
    await flushPromises();
    expect(document.body.style.overflow).toBe("auto");
  });

  it("prefers-reduced-motion: reduce 下弹窗开关与关闭行为完整可用", async () => {
    mockReducedMotion(true);
    const wrapper = mount(MotionDialog, {
      props: { open: true, ariaLabel: "减动效弹窗" },
      attrs: { "data-test": "reduced-dialog" },
      slots: { default: "<button type='button'>确定</button>" },
    });
    mountedWrappers.push(wrapper);
    await flushPromises();

    expect(document.body.querySelector('[data-test="reduced-dialog"]')).not.toBeNull();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it("焦点陷阱跳过 display:none / [hidden] / visibility:hidden 祖先元素及 tabindex='-1' 控件", async () => {
    const hostEl = document.createElement("div");
    document.body.appendChild(hostEl);

    const wrapper = mount(MotionDialog, {
      attachTo: hostEl,
      props: { open: true, ariaLabel: "焦点过滤测试" },
      attrs: { "data-test": "filter-dialog" },
      slots: {
        default: `
          <div style="display: none">
            <button type="button" data-test="in-display-none">不可见1</button>
          </div>
          <div hidden>
            <button type="button" data-test="in-hidden-attr">不可见2</button>
          </div>
          <div style="visibility: hidden">
            <button type="button" data-test="in-visibility-hidden">不可见3</button>
          </div>
          <button type="button" tabindex="-1" data-test="neg-tab-btn">负tabindex按钮</button>
          <a href="#ignore" tabindex="-1" data-test="neg-tab-link">负tabindex链接</a>
          <button type="button" data-test="real-ok-1">有效1</button>
          <button type="button" data-test="real-ok-2">有效2</button>
        `,
      },
    });
    mountedWrappers.push(wrapper);
    await flushPromises();

    const panel = document.body.querySelector('[data-test="filter-dialog"]') as HTMLElement;
    const focusables = getFocusableElements(panel);
    expect(focusables.map((el) => el.getAttribute("data-test"))).toEqual(["real-ok-1", "real-ok-2"]);

    const ok1 = document.body.querySelector('[data-test="real-ok-1"]') as HTMLButtonElement;
    const ok2 = document.body.querySelector('[data-test="real-ok-2"]') as HTMLButtonElement;
    expect(document.activeElement).toBe(ok1);

    ok2.focus();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
    expect(document.activeElement).toBe(ok1);

    ok1.focus();
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true }),
    );
    expect(document.activeElement).toBe(ok2);
  });

  it("IME 组合输入（isComposing / keyCode=229）按 Escape 不误关闭弹窗，结束后正常关闭", async () => {
    const hostEl = document.createElement("div");
    document.body.appendChild(hostEl);

    const wrapper = mount(MotionDialog, {
      attachTo: hostEl,
      props: { open: true, ariaLabel: "IME 输入弹窗" },
      attrs: { "data-test": "ime-dialog" },
      slots: {
        default: '<input type="text" data-test="ime-input" />',
      },
    });
    mountedWrappers.push(wrapper);
    await flushPromises();

    const input = document.body.querySelector('[data-test="ime-input"]') as HTMLInputElement;
    expect(input).not.toBeNull();
    input.focus();
    expect(document.activeElement).toBe(input);

    // 1) isComposing: true 时按 Esc 仅取消候选词，不得关闭弹窗
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
    expect(document.body.querySelector('[data-test="ime-dialog"]')).not.toBeNull();

    // 2) keyCode === 229（兼容老式/Safari IME 处理）时按 Esc 同样不得关闭弹窗
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
