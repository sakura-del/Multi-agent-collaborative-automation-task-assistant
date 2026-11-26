import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";

const mocks = vi.hoisted(() => ({
  submitFeedback: vi.fn(),
  toastSuccess: vi.fn(),
}));

vi.mock("@/api/growth", () => ({
  submitFeedback: mocks.submitFeedback,
  trackEvent: vi.fn(),
  initUmami: vi.fn(),
}));

vi.mock("@/lib/toast", () => ({
  toastSuccess: mocks.toastSuccess,
  toastError: vi.fn(),
  toastInfo: vi.fn(),
  toastWarning: vi.fn(),
}));

import FeedbackDialog from "./FeedbackDialog.vue";

describe("FeedbackDialog（站内反馈弹窗 · Issue #61 P2-11）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.submitFeedback.mockResolvedValue({
      id: "fb-1",
      category: "bug",
      content: "测试反馈",
      page_path: "/tasks",
      status: "open",
      created_at: null,
    });
  });

  it("选择类型并提交反馈后调用 POST /feedback 并关闭弹窗", async () => {
    const stub = { template: "<div />" };
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/tasks", component: stub }],
    });
    await router.push("/tasks");
    await router.isReady();

    const wrapper = mount(FeedbackDialog, {
      props: { open: true },
      global: { plugins: [router] },
      attachTo: document.body,
    });
    await flushPromises();

    const bugBtn = document.body.querySelector('[data-test="feedback-cat-bug"]') as HTMLButtonElement;
    expect(bugBtn).not.toBeNull();
    bugBtn.click();

    const textarea = document.body.querySelector('[data-test="feedback-content"]') as HTMLTextAreaElement;
    textarea.value = "移动端任务标题有一点折行";
    textarea.dispatchEvent(new Event("input"));

    const form = document.body.querySelector('[data-test="feedback-dialog"] form') as HTMLFormElement;
    form.dispatchEvent(new Event("submit", { cancelable: true }));
    await flushPromises();

    expect(mocks.submitFeedback).toHaveBeenCalledWith({
      category: "bug",
      content: "移动端任务标题有一点折行",
      page_path: "/tasks",
    });
    expect(mocks.toastSuccess).toHaveBeenCalled();
    expect(wrapper.emitted("update:open")?.[0]).toEqual([false]);

    wrapper.unmount();
  });
});
