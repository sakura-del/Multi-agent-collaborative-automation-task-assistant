import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import PageHeader from "@/components/common/PageHeader.vue";

describe("PageHeader", () => {
  it("renders editorial title, optional context and the original action slot", () => {
    const wrapper = mount(PageHeader, {
      props: {
        eyebrow: "Evidence library",
        title: "知识库",
        description: "文献综述的证据来源。",
        descriptionTestid: "library-hint",
      },
      slots: { default: '<button type="button">上传</button>' },
    });

    expect(wrapper.find("h1").text()).toBe("知识库");
    expect(wrapper.find("h1").classes()).toContain("font-display");
    expect(wrapper.text()).toContain("Evidence library");
    expect(wrapper.find('[data-test="library-hint"]').text()).toBe("文献综述的证据来源。");
    expect(wrapper.get("header").get("button").text()).toBe("上传");
  });

  it("omits optional eyebrow, description and action wrapper when absent", () => {
    const wrapper = mount(PageHeader, { props: { title: "引用库" } });

    expect(wrapper.find("h1").text()).toBe("引用库");
    expect(wrapper.find("header").findAll("p")).toHaveLength(0);
    expect(wrapper.find("header").element.children).toHaveLength(1);
  });
});
