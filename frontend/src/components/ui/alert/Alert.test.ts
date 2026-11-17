import { describe, expect, it } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Alert } from "@/components/ui/alert";

describe("Animated Alert", () => {
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
});
