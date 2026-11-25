import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import { Button, buttonVariants } from "./index";

describe("Button 与 buttonVariants 微按压反馈", () => {
  it("常规尺寸 (sm/default/lg) 包含 active:scale-[0.985] 且不含 active:scale-95", () => {
    for (const size of ["sm", "default", "lg"] as const) {
      const classes = buttonVariants({ variant: "default", size });
      expect(classes).toContain("active:scale-[0.985]");
      expect(classes).toContain("motion-reduce:active:scale-100");
      expect(classes).toContain("motion-reduce:transition-none");
      expect(classes).toContain("duration-100");
      expect(classes).toContain("transition-[color,background-color,border-color,transform]");
      expect(classes).not.toContain("active:scale-95");
    }
  });

  it("图标尺寸 (icon/icon-sm) 裸调 buttonVariants 含 active:scale-95 且无冲突按压类", () => {
    for (const size of ["icon", "icon-sm"] as const) {
      // 直接校验裸调（守卫 WorkspaceView.vue:481 未经过 cn() 的用法）
      const classes = buttonVariants({ variant: "ghost", size });
      expect(classes).toContain("active:scale-95");
      expect(classes).toContain("motion-reduce:active:scale-100");
      expect(classes).not.toContain("active:scale-[0.985]");
    }
  });

  it("保持 Button 组件原有 props/slots 渲染契约", () => {
    const wrapper = mount(Button, {
      props: { variant: "destructive", size: "sm", type: "submit", disabled: true },
      slots: { default: "确认删除" },
    });

    const btn = wrapper.get("button");
    expect(btn.text()).toBe("确认删除");
    expect(btn.attributes("type")).toBe("submit");
    expect(btn.attributes("disabled")).toBeDefined();
    expect(btn.classes().join(" ")).toContain("bg-destructive");
    expect(btn.classes().join(" ")).toContain("active:scale-[0.985]");
  });
});
