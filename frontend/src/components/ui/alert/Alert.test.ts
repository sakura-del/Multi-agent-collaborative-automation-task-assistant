import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, nextTick, ref, type VNode } from "vue";
import { Motion } from "motion-v";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Alert } from "@/components/ui/alert";
import { DURATION } from "@/lib/ease";

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

describe("Animated Alert", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("按变体渲染底色与 role", () => {
    const warning = mount(Alert, {
      props: { variant: "warning", title: "注意" },
    });
    const warningSurface = warning.get("[data-variant='warning']");
    expect(warningSurface.attributes("role")).toBe("alert");
    expect(warningSurface.classes().join(" ")).toContain("bg-warning/8");

    const success = mount(Alert, {
      props: { variant: "success", title: "完成" },
    });
    const successSurface = success.get("[data-variant='success']");
    expect(successSurface.attributes("role")).toBe("status");
    expect(successSurface.classes().join(" ")).toContain("bg-success/5");
  });

  it("结构化内容渲染标题、描述与自动图标", () => {
    const wrapper = mount(Alert, {
      props: {
        variant: "destructive",
        title: "保存失败",
        description: "网络中断，请重试。",
      },
    });

    expect(wrapper.text()).toContain("保存失败");
    expect(wrapper.text()).toContain("网络中断");
    expect(wrapper.find("svg").exists()).toBe(true);
  });

  it("旧版插槽写法保持行内布局", () => {
    const wrapper = mount(Alert, {
      props: { variant: "muted" },
      slots: { default: "<span>旧版提示</span>" },
    });

    expect(wrapper.text()).toContain("旧版提示");
    expect(wrapper.find("button[aria-label='关闭提示']").exists()).toBe(true);
  });

  it("关闭按钮上报 update:open=false", async () => {
    const wrapper = mount(Alert, {
      props: { variant: "info", title: "提示" },
    });

    await wrapper.get("button[aria-label='关闭提示']").trigger("click");
    await flushPromises();

    expect(wrapper.emitted("update:open")?.[0]).toEqual([false]);
  });

  it("ErrorBanner 保留 role/data-test 契约，且新错误会重新弹出", async () => {
    const wrapper = mount(ErrorBanner, { props: { message: "第一次错误" } });
    expect(wrapper.find("[data-test='error-banner']").exists()).toBe(true);
    expect(wrapper.get("[data-test='error-banner']").attributes("role")).toBe("alert");

    await wrapper.get("button[aria-label='关闭提示']").trigger("click");
    await flushPromises();

    await wrapper.setProps({ message: "第二次错误" });
    await flushPromises();

    expect(wrapper.text()).toContain("第二次错误");
  });

  it("ErrorBanner 关闭触发 @dismiss，且父重置后同文案错误可正常复现", async () => {
    const wrapper = mount(ErrorBanner, { props: { message: "网络超时" } });
    expect(wrapper.find("[data-test='error-banner']").exists()).toBe(true);

    await wrapper.get("button[aria-label='关闭提示']").trigger("click");
    await flushPromises();
    expect(wrapper.emitted("dismiss")).toEqual([[]]);

    // 模拟父级接收 dismiss 清空 error 后，再次触发完全相同的错误文案
    await wrapper.setProps({ message: null });
    await flushPromises();
    expect(wrapper.find("[data-test='error-banner']").exists()).toBe(false);

    await wrapper.setProps({ message: "网络超时" });
    await flushPromises();
    expect(wrapper.find("[data-test='error-banner']").exists()).toBe(true);
    expect(wrapper.text()).toContain("网络超时");
  });

  it("ErrorBanner 未清 null 直接关闭后保持隐藏，不卡在开启态", async () => {
    const wrapper = mount(ErrorBanner, { props: { message: "未清空错误" } });
    await wrapper.get("button[aria-label='关闭提示']").trigger("click");
    await flushPromises();

    expect(wrapper.find("[data-test='error-banner']").exists()).toBe(false);
  });

  it("Alert 在 prefers-reduced-motion: reduce 下可正常渲染并关闭，图标过渡降级为 micro", async () => {
    mockReducedMotion(true);
    const wrapper = mount(Alert, {
      props: { variant: "destructive", title: "降级提示" },
    });

    expect(wrapper.text()).toContain("降级提示");
    const motions = wrapper.findAllComponents(Motion);
    // 第一个 Motion 为外层 Alert，第二个为内部变体图标 Motion
    expect(motions.length).toBeGreaterThanOrEqual(2);
    const iconWrapper = motions[1] as unknown as {
      props: (key: string) => { duration?: number } | undefined;
    };
    const iconTransition = iconWrapper.props("transition");
    expect(iconTransition?.duration).toBe(DURATION.micro);

    await wrapper.get("button[aria-label='关闭提示']").trigger("click");
    await flushPromises();
    expect(wrapper.emitted("update:open")?.[0]).toEqual([false]);
  });

  it("ErrorBanner 关闭触发 @dismiss 清空 message 时保持 Alert 挂载以完整播放退场动效", async () => {
    const Host = defineComponent({
      components: { ErrorBanner },
      setup() {
        const message = ref<string | null>("请求处理失败");
        return { message };
      },
      template: `<ErrorBanner :message="message" @dismiss="message = null" />`,
    });

    const wrapper = mount(Host);
    await flushPromises();

    const alertComp = wrapper.findComponent(Alert);
    expect(alertComp.exists()).toBe(true);
    expect(alertComp.props("open")).toBe(true);
    expect(wrapper.text()).toContain("请求处理失败");

    await wrapper.get("button[aria-label='关闭提示']").trigger("click");
    await nextTick();

    expect(wrapper.vm.message).toBeNull();
    // 关键：不可靠外层 v-if 销毁 Alert，必须保留 Alert 实例并将 open 置为 false 以触发 AnimatePresence 退场
    expect(wrapper.findComponent(Alert).exists()).toBe(true);
    expect(wrapper.findComponent(Alert).props("open")).toBe(false);
    // 退场内容缓存：Alert 默认插槽文本在 message 置空后仍保留上一条文案直至退场完成
    const slotNodes = (wrapper.findComponent(Alert).vm.$slots.default?.({}) ?? []) as VNode[];
    const slotText = slotNodes
      .map((node: VNode) => (typeof node.children === "string" ? node.children : ""))
      .join("");
    expect(slotText).toContain("请求处理失败");

    // 退场完成后重新设置消息仍可正常显示
    await flushPromises();
    wrapper.vm.message = "请求处理失败";
    await flushPromises();
    expect(wrapper.findComponent(Alert).props("open")).toBe(true);
    expect(wrapper.find("[data-test='error-banner']").exists()).toBe(true);
  });
});
