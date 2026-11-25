import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";

import BePromptInput from "@/components/motion/BePromptInput.vue";

describe("BePromptInput 输入组件", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("正确读取 CSS minHeight（不被 parseInt('10rem') 误压成 10px），并带滚动上限与 rAF 合批", async () => {
    const originalGetComputedStyle = window.getComputedStyle;
    vi.spyOn(window, "getComputedStyle").mockImplementation((el: Element) => {
      const base = originalGetComputedStyle(el);
      if (el.tagName === "TEXTAREA") {
        return new Proxy(base, {
          get(target, prop) {
            if (prop === "minHeight") return "160px";
            const value = Reflect.get(target, prop);
            return typeof value === "function" ? value.bind(target) : value;
          },
        });
      }
      return base;
    });

    const rafCallbacks: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb: FrameRequestCallback) => {
      rafCallbacks.push(cb);
      return rafCallbacks.length;
    });

    const wrapper = mount(BePromptInput, {
      props: {
        modelValue: "",
        minHeight: "10rem",
      },
    });

    const textarea = wrapper.get("textarea");
    expect(textarea.classes()).toContain("max-h-72");
    expect(textarea.classes()).toContain("overflow-y-auto");

    await wrapper.setProps({ modelValue: "第一行内容" });
    await nextTick();
    for (const cb of rafCallbacks) cb(16);

    const styleHeight = (textarea.element as HTMLTextAreaElement).style.height;
    expect(styleHeight).not.toBe("10px");
    expect(Number.parseFloat(styleHeight)).toBeGreaterThanOrEqual(160);
  });

  it("IME 组合输入（isComposing / keyCode=229）按 Enter 不误提交，结束后正常提交", async () => {
    const wrapper = mount(BePromptInput, {
      props: {
        modelValue: "检索增强生成",
        submitOnEnter: true,
      },
    });

    const textarea = wrapper.get("textarea");
    await textarea.trigger("keydown", { key: "Enter", isComposing: true });
    expect(wrapper.emitted("submit")).toBeUndefined();

    const imeEvent = new KeyboardEvent("keydown", {
      key: "Enter",
      isComposing: false,
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(imeEvent, "keyCode", { value: 229 });
    textarea.element.dispatchEvent(imeEvent);
    await flushPromises();
    expect(wrapper.emitted("submit")).toBeUndefined();

    await textarea.trigger("keydown", { key: "Enter", isComposing: false });
    expect(wrapper.emitted("submit")).toEqual([["检索增强生成"]]);
  });

  it("非 compact 与 compact 模式均统一显示「N 字」，不出现英文 chars", () => {
    const normal = mount(BePromptInput, { props: { modelValue: "abc", compact: false } });
    expect(normal.get('[data-test="prompt-input-footer"]').text()).toContain("3 字");
    expect(normal.get('[data-test="prompt-input-footer"]').text()).not.toContain("chars");

    const compact = mount(BePromptInput, { props: { modelValue: "abc", compact: true } });
    expect(compact.get('[data-test="prompt-input-footer"]').text()).toContain("3 字");
    expect(compact.get('[data-test="prompt-input-footer"]').text()).not.toContain("chars");
  });

  it("prompt-input-footer 提供显式发送按钮，支持自定义 submitLabel 与 submitting 禁用态", async () => {
    const wrapper = mount(BePromptInput, {
      props: {
        modelValue: "   ",
      },
    });

    const submitBtn = wrapper.find('[data-test="prompt-submit"]');
    expect(submitBtn.exists()).toBe(true);
    expect(submitBtn.attributes("type")).toBe("button");
    expect(submitBtn.text()).toContain("发送");
    expect((submitBtn.element as HTMLButtonElement).disabled).toBe(true);

    await wrapper.setProps({ modelValue: "有效输入", submitLabel: "创建" });
    expect(submitBtn.text()).toContain("创建");
    expect((submitBtn.element as HTMLButtonElement).disabled).toBe(false);

    await submitBtn.trigger("click");
    expect(wrapper.emitted("submit")).toEqual([["有效输入"]]);

    await wrapper.setProps({ submitting: true });
    expect((submitBtn.element as HTMLButtonElement).disabled).toBe(true);
    expect(submitBtn.find("svg.motion-reduce\\:animate-none").exists()).toBe(true);
  });

  it("attrs 透传落在 textarea 上且暴露 focus() 方法", () => {
    const hostEl = document.createElement("div");
    document.body.appendChild(hostEl);

    const wrapper = mount(BePromptInput, {
      attachTo: hostEl,
      attrs: {
        id: "import-content",
        "data-test": "import-content",
      },
    });

    const root = wrapper.get('[data-test="prompt-input"]');
    const textarea = wrapper.get("textarea");
    expect(root.attributes("id")).toBeUndefined();
    expect(textarea.attributes("id")).toBe("import-content");
    expect(textarea.attributes("data-test")).toBe("import-content");

    (wrapper.vm as unknown as { focus: () => void }).focus();
    expect(document.activeElement).toBe(textarea.element);

    wrapper.unmount();
    hostEl.remove();
  });
});
