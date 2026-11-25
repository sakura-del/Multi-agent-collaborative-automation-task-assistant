import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("@/components/chat/ChatShell.vue", () => ({
  default: {
    name: "ChatShell",
    props: ["variant"],
    template: `<div data-test="stub-chat-shell" :data-variant="variant" />`,
  },
}));

import ChatView from "@/views/ChatView.vue";

describe("ChatView 会话全屏页", () => {
  it("外层容器使用 h-[calc(100dvh-2.75rem)] 且 overflow-hidden 消除双滚动条，并传入 full 形态", () => {
    const wrapper = mount(ChatView);
    const root = wrapper.element as HTMLElement;
    expect(root.className).toContain("h-[calc(100dvh-2.75rem)]");
    expect(root.className).toContain("overflow-hidden");
    expect(root.className).not.toContain("h-[calc(100svh-4rem)]");

    const shell = wrapper.get('[data-test="stub-chat-shell"]');
    expect(shell.attributes("data-variant")).toBe("full");
  });
});
