import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import CreateExtractionDialog from "@/components/extractions/CreateExtractionDialog.vue";

vi.mock("@/api/extractions", () => ({
  createExtractionTable: vi.fn(),
}));

import { createExtractionTable } from "@/api/extractions";

describe("CreateExtractionDialog R4 校验聚焦与折叠控制", () => {
  const mountedWrappers: Array<{ unmount: () => void }> = [];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    while (mountedWrappers.length > 0) {
      mountedWrappers.pop()?.unmount();
    }
    document.body.style.overflow = "";
    document.body.innerHTML = "";
  });

  function mountDialog() {
    const hostEl = document.createElement("div");
    document.body.appendChild(hostEl);
    const wrapper = mount(CreateExtractionDialog, {
      attachTo: hostEl,
      props: {
        open: true,
        sources: [{ source_type: "knowledge_doc", source_id: "doc-1", title: "测试论文" }],
      },
      global: { stubs: { Teleport: true } },
    });
    mountedWrappers.push(wrapper);
    return wrapper;
  }

  it("标题为空提交时渲染 aria-invalid=\"true\" 并聚焦到标题输入框", async () => {
    const wrapper = mountDialog();
    await flushPromises();

    const titleInput = wrapper.get('[data-test="extraction-title-input"]');
    await titleInput.setValue("   ");
    await wrapper.get('[data-test="extraction-create-submit"]').trigger("click");
    await flushPromises();

    expect(createExtractionTable).not.toHaveBeenCalled();
    expect(titleInput.attributes("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(titleInput.element);
  });

  it("主字段 prompt 出错而隐藏列标识也非法时，不误展开技术标识区并聚焦到 prompt", async () => {
    const wrapper = mountDialog();
    await flushPromises();

    const firstKeyInput = wrapper.findAll('input[placeholder="key（如 sample_size）"]')[0];
    const firstPromptInput = wrapper.findAll('input[placeholder="抽取指令（要求模型提取什么）"]')[0];

    await firstKeyInput.setValue("123-bad");
    await firstPromptInput.setValue(" ");
    await wrapper.get('[data-test="extraction-create-submit"]').trigger("click");
    await flushPromises();

    expect(createExtractionTable).not.toHaveBeenCalled();
    expect(firstKeyInput.element.closest('[aria-hidden="true"]')).not.toBeNull();
    expect(firstPromptInput.attributes("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(firstPromptInput.element);
  });

  it("所有主字段合法仅隐藏列标识非法时，自动展开技术标识区并聚焦到该标识输入框", async () => {
    const wrapper = mountDialog();
    await flushPromises();

    const firstKeyInput = wrapper.findAll('input[placeholder="key（如 sample_size）"]')[0];
    await firstKeyInput.setValue("123-bad");
    expect(firstKeyInput.element.closest('[aria-hidden="true"]')).not.toBeNull();

    await wrapper.get('[data-test="extraction-create-submit"]').trigger("click");
    await flushPromises();

    expect(createExtractionTable).not.toHaveBeenCalled();
    expect(firstKeyInput.element.closest('[aria-hidden="true"]')).toBeNull();
    expect(firstKeyInput.attributes("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(firstKeyInput.element);
  });
});
