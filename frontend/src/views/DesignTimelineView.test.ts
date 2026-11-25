import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";

import DesignTimelineView from "@/views/DesignTimelineView.vue";

describe("DesignTimelineView", () => {
  it("移除局部 scoped 白底强覆盖、采用 scaleX 进度条与语义 token 胶囊", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/design/timeline", name: "design-timeline", component: DesignTimelineView }],
    });
    await router.push("/design/timeline");
    await router.isReady();

    const wrapper = mount(DesignTimelineView, {
      global: { plugins: [router] },
    });

    const html = wrapper.html();
    expect(html).not.toContain("design-demo");
    expect(html).not.toContain("[--primary:#2F5BFF]");
    expect(html).toContain("scaleX(");
    expect(html).not.toContain("Design preview · beUI");
    expect(html).toContain("设计预览 · 时间线");
  });
});
