import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import {
  deleteTemplate,
  listTemplates,
  renderTemplate,
  uploadTemplate,
} from "@/api/templates";
import type { TemplateOut } from "@/api/types";
import TemplateView from "@/views/TemplateView.vue";

vi.mock("@/api/templates", () => ({
  listTemplates: vi.fn(),
  uploadTemplate: vi.fn(),
  deleteTemplate: vi.fn(),
  renderTemplate: vi.fn(),
}));

function templateRow(overrides: Partial<TemplateOut> = {}): TemplateOut {
  return {
    template_id: "tpl-9e961b86377bb8b1",
    name: "学院模板",
    checksum: "a".repeat(64),
    size: 4096,
    placeholders: [
      { key: "title", label: "", required: true, sample: "" },
      { key: "body", label: "", required: true, sample: "" },
      { key: "references", label: "", required: true, sample: "" },
    ],
    style_map: { Heading1: "h1", Normal: "body" },
    ...overrides,
  };
}

/** happy-dom 下无法通过 trigger 设置 target.files，直接挂到 input 元素上 */
async function chooseFile(wrapper: ReturnType<typeof mount>, file: File) {
  const inputWrapper = wrapper.find('[data-test="template-file"]');
  Object.defineProperty(inputWrapper.element, "files", {
    value: [file],
    writable: false,
    configurable: true,
  });
  await inputWrapper.trigger("change");
}

function docxFile(name = "school.docx"): File {
  return new File(["docx-bytes"], name, {
    type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  });
}

async function mountView() {
  const wrapper = mount(TemplateView);
  await flushPromises();
  return wrapper;
}

describe("TemplateView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listTemplates).mockResolvedValue([templateRow()]);
  });

  it("列出模板并展示占位符与 style_map", async () => {
    const wrapper = await mountView();

    const row = wrapper.find('[data-test="template-row-tpl-9e961b86377bb8b1"]');
    expect(row.exists()).toBe(true);
    expect(row.text()).toContain("学院模板");
    // 占位符清单要可见（用户据此做章节映射）
    expect(row.text()).toContain("title");
    expect(row.text()).toContain("body");
    expect(row.text()).toContain("references");
  });

  it("空态给出明确引导", async () => {
    vi.mocked(listTemplates).mockResolvedValue([]);

    const wrapper = await mountView();

    expect(wrapper.find('[data-test="template-empty"]').exists()).toBe(true);
  });

  it("上传 .docx 成功后展示成功信息并刷新列表", async () => {
    vi.mocked(uploadTemplate).mockResolvedValue(templateRow({ name: "新模板" }));

    const wrapper = await mountView();
    await chooseFile(wrapper, docxFile());
    await wrapper.find('[data-test="template-upload-submit"]').trigger("submit");
    await flushPromises();

    expect(uploadTemplate).toHaveBeenCalledTimes(1);
    expect(wrapper.find('[data-test="template-upload-success"]').text()).toContain("新模板");
    // 上传后重新拉列表
    expect(vi.mocked(listTemplates).mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("上传非 .docx 时前端先拦住，不发请求", async () => {
    const wrapper = await mountView();
    await chooseFile(wrapper, new File(["x"], "evil.docm"));
    await wrapper.find('[data-test="template-upload-submit"]').trigger("submit");
    await flushPromises();

    expect(uploadTemplate).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="template-upload-error"]').text()).toContain(".docx");
  });

  it("上传被后端拒绝时展示可读原因（含宏提示）", async () => {
    vi.mocked(uploadTemplate).mockRejectedValue(new Error("模板包含宏（vbaProject.bin），已拒绝"));

    const wrapper = await mountView();
    await chooseFile(wrapper, docxFile());
    await wrapper.find('[data-test="template-upload-submit"]').trigger("submit");
    await flushPromises();

    expect(wrapper.find('[data-test="template-upload-error"]').text()).toContain("宏");
  });

  it("渲染成功后触发 .docx 下载", async () => {
    vi.mocked(renderTemplate).mockResolvedValue(new Blob(["docx"]));
    // 记录 createObjectURL / click 调用（happy-dom 没有真实下载）
    const createObjectURL = vi.fn(() => "blob:mock");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true });
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    const wrapper = await mountView();
    await wrapper.find('[data-test="template-render-tpl-9e961b86377bb8b1"]').trigger("click");
    await flushPromises();

    expect(renderTemplate).toHaveBeenCalledTimes(1);
    expect(createObjectURL).toHaveBeenCalled();
    expect(clickSpy).toHaveBeenCalled();
    clickSpy.mockRestore();
  });

  it("渲染失败时展示后端原因", async () => {
    vi.mocked(renderTemplate).mockRejectedValue(
      new Error("模板要求占位符 title，但未提供内容")
    );

    const wrapper = await mountView();
    await wrapper.find('[data-test="template-render-tpl-9e961b86377bb8b1"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="template-render-error"]').text()).toContain("title");
  });

  it("删除模板后刷新列表", async () => {
    vi.mocked(deleteTemplate).mockResolvedValue(undefined);

    const wrapper = await mountView();
    await wrapper.find('[data-test="template-delete-tpl-9e961b86377bb8b1"]').trigger("click");
    await flushPromises();

    expect(deleteTemplate).toHaveBeenCalledWith("tpl-9e961b86377bb8b1");
    expect(vi.mocked(listTemplates).mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("列表加载失败时展示错误横幅", async () => {
    vi.mocked(listTemplates).mockRejectedValue(new Error("网络错误"));

    const wrapper = await mountView();

    expect(wrapper.find('[data-test="template-load-error"]').text()).toContain("网络错误");
  });
});
