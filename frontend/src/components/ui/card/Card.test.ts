import { h } from "vue";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import Card from "./Card.vue";
import CardAction from "./CardAction.vue";
import CardContent from "./CardContent.vue";
import CardDescription from "./CardDescription.vue";
import CardFooter from "./CardFooter.vue";
import CardHeader from "./CardHeader.vue";
import CardTitle from "./CardTitle.vue";
import { CARD_TARGET_CLASS } from "./card-context";

function classes(wrapper: ReturnType<typeof mount>): string {
  return wrapper.classes().join(" ");
}

describe("Card 结构", () => {
  it("默认 outlined：白底、16px 圆角、根容器管垂直间距", () => {
    const wrapper = mount(Card);
    const cls = classes(wrapper);

    expect(cls).toContain("bg-card");
    expect(cls).toContain("rounded-2xl");
    expect(cls).toContain("border-border");
    // 根管 py + gap，部件才管 px——省略部件时边缘不会塌
    expect(cls).toContain("py-5");
    expect(cls).toContain("gap-5");
  });

  it("muted + compact：灰底无边框、16px 内边距", () => {
    const wrapper = mount(Card, { props: { variant: "muted", size: "compact" } });
    const cls = classes(wrapper);

    expect(cls).toContain("bg-muted");
    expect(cls).toContain("border-transparent");
    expect(cls).toContain("py-4");
  });

  it("selected：主色描边 + 1px 环，并把状态暴露到 DOM", () => {
    const wrapper = mount(Card, { props: { selected: true } });
    const cls = classes(wrapper);

    expect(cls).toContain("border-primary");
    expect(cls).toContain("shadow-[0_0_0_1px_var(--primary)]");
    expect(wrapper.attributes("data-selected")).toBe("true");
  });

  it("未选中不输出 data-selected（避免暴露无意义属性）", () => {
    const wrapper = mount(Card);
    expect(wrapper.attributes("data-selected")).toBeUndefined();
  });

  it("interactive 有 hover 与按压样式，但不抢焦点", () => {
    const wrapper = mount(Card, { props: { interactive: true } });
    const cls = classes(wrapper);

    expect(cls).toContain("cursor-pointer");
    expect(cls).toContain("hover:border-border-strong");
    // whileTap 会给元素塞 tabIndex=0，在真正的控件前多一个无名 tab 停靠点
    expect(wrapper.attributes("tabindex")).toBeUndefined();
  });

  it("部件继承 Card 的 size：compact 用 px-4", () => {
    const wrapper = mount(Card, {
      props: { size: "compact" },
      slots: { default: () => h(CardHeader, null, () => h(CardTitle, null, () => "标题")) },
    });

    const header = wrapper.get('[data-slot="card-header"]');
    expect(header.classes().join(" ")).toContain("px-4");
  });

  it("部件自管水平内边距：六个部件各带自己的 px", () => {
    const wrapper = mount(Card, {
      slots: {
        default: () => [
          h(CardHeader, null, () => h(CardTitle, null, () => "标题")),
          h(CardDescription, null, () => "描述"),
          h(CardContent, null, () => "正文"),
          h(CardFooter, null, () => "底部"),
        ],
      },
    });

    for (const slot of ["card-header", "card-description", "card-content", "card-footer"]) {
      expect(wrapper.get(`[data-slot="${slot}"]`).classes().join(" ")).toContain("px-5");
    }
  });

  it("CardAction 占右上角两行（有它时 Header 才切两列）", () => {
    const wrapper = mount(Card, {
      slots: {
        default: () => [
          h(CardHeader, null, () => [h(CardTitle, null, () => "标题"), h(CardAction, null, () => "操作")]),
        ],
      },
    });

    const action = wrapper.get('[data-slot="card-action"]');
    expect(action.classes().join(" ")).toContain("col-start-2");
    expect(action.classes().join(" ")).toContain("row-span-2");

    const header = wrapper.get('[data-slot="card-header"]');
    expect(header.classes().join(" ")).toContain("[&:has([data-slot=card-action])]:grid-cols-[1fr_auto]");
  });

  it("CardTitle 的 as 决定语义标签，默认 div", () => {
    const heading = mount(CardTitle, { props: { as: "h2" }, slots: { default: () => "标题" } });
    expect(heading.element.tagName).toBe("H2");

    const plain = mount(CardTitle, { slots: { default: () => "标题" } });
    expect(plain.element.tagName).toBe("DIV");
  });

  it("CARD_TARGET_CLASS 把控件热区铺满整卡", () => {
    expect(CARD_TARGET_CLASS).toContain("after:absolute");
    expect(CARD_TARGET_CLASS).toContain("after:inset-0");
  });

  it("class 透传可与默认样式合并（页面大卡仍可覆盖成 24px）", () => {
    const wrapper = mount(Card, { props: { class: "rounded-3xl" } });
    const cls = classes(wrapper);

    expect(cls).toContain("rounded-3xl");
    expect(cls).not.toContain("rounded-2xl");
  });
  it("as 决定根标签：列表里必须是 li，表单容器是 form", () => {
    const item = mount(Card, { props: { as: "li" }, slots: { default: () => "条目" } });
    expect(item.element.tagName).toBe("LI");

    const form = mount(Card, { props: { as: "form" }, slots: { default: () => "表单" } });
    expect(form.element.tagName).toBe("FORM");
  });
});
