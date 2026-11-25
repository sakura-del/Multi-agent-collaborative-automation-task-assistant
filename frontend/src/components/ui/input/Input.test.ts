import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import { Input } from "./index";

describe("Input 原语透传与 aria-invalid 行为", () => {
  it("透传 :aria-invalid=\"true\"、:aria-invalid=\"'true'\" 与静态 aria-invalid=\"true\" 到原生 input", () => {
    const cases = [
      mount(Input, { attrs: { "aria-invalid": true } }),
      mount(Input, { attrs: { "aria-invalid": "true" } }),
      mount({
        components: { Input },
        template: '<Input aria-invalid="true" />',
      }),
    ];

    for (const wrapper of cases) {
      const input = wrapper.get("input");
      expect(input.attributes("aria-invalid")).toBe("true");
      expect(input.classes()).toContain("aria-[invalid=true]:border-destructive");
    }
  });

  it("传入 false、undefined 或不传时不会误渲染 aria-invalid=\"true\"", () => {
    const falseWrapper = mount(Input, { attrs: { "aria-invalid": false } });
    expect(falseWrapper.get("input").attributes("aria-invalid")).not.toBe("true");

    const undefinedWrapper = mount(Input, { attrs: { "aria-invalid": undefined } });
    expect(undefinedWrapper.get("input").attributes("aria-invalid")).toBeUndefined();

    const omittedWrapper = mount(Input);
    expect(omittedWrapper.get("input").attributes("aria-invalid")).toBeUndefined();
  });

  it("保持 v-model 双向绑定与 class 合并", async () => {
    const wrapper = mount(Input, {
      props: { modelValue: "初始值", class: "h-8 font-mono" },
    });
    const input = wrapper.get("input");
    expect((input.element as HTMLInputElement).value).toBe("初始值");
    expect(input.classes()).toContain("h-8");
    expect(input.classes()).toContain("font-mono");

    await input.setValue("新内容");
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["新内容"]);
  });
});
