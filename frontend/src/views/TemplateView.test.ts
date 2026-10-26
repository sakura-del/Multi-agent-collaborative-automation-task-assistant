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
  });  it("渲染失败时展示后端原因", async () => {
    vi.mocked(renderTemplate).mockRejectedValue(
      new Error("模板要求占位符 title，但未提供内容")
    );

    const wrapper = await mountView();
    await wrapper.find('[data-test="template-render-tpl-9e961b86377bb8b1"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="template-render-error"]').text()).toContain("title");
  });

  it("删除模板后刷新列表", async () => {
    // 设计变更（2026-10-26）：删除改为两步确认。后端会 unlink 磁盘文件，
    // 误点一下就得重新上传。原断言「点一下即删」是旧行为，不是回归。
    vi.mocked(deleteTemplate).mockResolvedValue(undefined);

    const wrapper = await mountView();
    await wrapper.find('[data-test="template-delete-tpl-9e961b86377bb8b1"]').trigger("click");
    await flushPromises();
    await wrapper
      .find('[data-test="template-delete-confirm-tpl-9e961b86377bb8b1"]')
      .trigger("click");
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

/**
 * 模板页重设计（2026-10-26）。
 *
 * ## 取证（服务器真实渲染 + Playwright 结构导出）
 *
 * 前端 `handleRender` 发送的载荷是**假内容**：
 *
 * ```ts
 * sections[key] = "示例内容（key）"
 * bibliography = ["[1] 示例参考文献（references）"]
 * ```
 *
 * 用真实模板 `school_template.docx`（7 个占位符）渲染后，成稿正文逐段是：
 *
 * ```
 * 目录占位内容 / 示例内容（title） / 示例内容（abstract） / 编号条目一 /
 * 示例内容（chapter_1） / 示例内容（chapter_1_1） / 示例内容（body_main） /
 * 撰写人：示例内容（author） / [1] 示例参考文献（references）
 * ```
 *
 * 正文总字符数 **129**。而页面上 `HAS_DEMO_WORD = false` ——
 * **用户完全不知道下载到的是占位文本**。
 *
 * 后端契约本身是严的（`_validate_contract` 缺占位符直接 400 拒绝静默丢内容），
 * 所以缺陷 100% 在前端：它主动填假内容绕过了契约。
 */
describe("TemplateView 示例标注与删除确认（2026-10-26）", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listTemplates).mockResolvedValue([templateRow()]);
  });

  it("按钮文案不能让人以为产出真实成稿", async () => {
    // 取证：按钮叫「渲染」，点了下载一个全是「示例内容」的 docx。
    // 「渲染」这个词承诺的是真实成稿，与实际不符。
    const wrapper = await mountView();

    const button = wrapper.find('[data-test="template-render-tpl-9e961b86377bb8b1"]');
    expect(button.exists()).toBe(true);
    const label = button.text();
    expect(label).not.toBe("渲染");
    // 必须能看出这是「校验/预览」而不是「生成成稿」
    expect(label).toMatch(/校验|预览|试渲染/);
  });

  it("页面上必须有「示例内容」的说明（用户要知道下载的是占位文本）", async () => {
    // 取证：HAS_DEMO_WORD = false，页面一个字都没提
    const wrapper = await mountView();

    const note = wrapper.find('[data-test="template-sample-note"]');
    expect(note.exists()).toBe(true);
    expect(note.text()).toMatch(/示例|占位|示例文本/);
  });

  it("校验成功后提示里要写明「用示例文本填充」", async () => {
    vi.mocked(renderTemplate).mockResolvedValue(new Blob(["docx"]));
    Object.defineProperty(URL, "createObjectURL", { value: () => "blob:mock", configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: () => undefined, configurable: true });
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    const wrapper = await mountView();
    await wrapper.find('[data-test="template-render-tpl-9e961b86377bb8b1"]').trigger("click");
    await flushPromises();

    const success = wrapper.find('[data-test="template-render-success"]');
    expect(success.exists()).toBe(true);
    // 关键：不能让用户以为这是真实成稿
    expect(success.text()).toMatch(/示例/);
    clickSpy.mockRestore();
  });

  it("占位符要显示 label/sample（后端给了就要用，别只显示 key）", async () => {
    // 后端 `PlaceholderOut` 有 label/required/sample 三个字段，
    // 实测服务器返回的全是空串（模板没声明），但前端必须优先用 label，
    // 否则模板作者写了说明也看不到。
    vi.mocked(listTemplates).mockResolvedValue([
      templateRow({
        placeholders: [
          { key: "title", label: "论文标题", required: true, sample: "基于 RAG 的评估方法" },
          { key: "body", label: "正文", required: true, sample: "" },
        ],
      }),
    ]);

    const wrapper = await mountView();

    const row = wrapper.find('[data-test="template-row-tpl-9e961b86377bb8b1"]');
    expect(row.text()).toContain("论文标题");
    expect(row.text()).toContain("正文");
  });

  it("删除模板需要二次确认（后端会 unlink 磁盘文件）", async () => {
    vi.mocked(deleteTemplate).mockResolvedValue(undefined);

    const wrapper = await mountView();
    await wrapper.find('[data-test="template-delete-tpl-9e961b86377bb8b1"]').trigger("click");
    await flushPromises();

    // 第一次点击不能直接删
    expect(deleteTemplate).not.toHaveBeenCalled();
    expect(
      wrapper.find('[data-test="template-delete-confirm-tpl-9e961b86377bb8b1"]').exists()
    ).toBe(true);
  });

  it("确认后才真正删除", async () => {
    vi.mocked(deleteTemplate).mockResolvedValue(undefined);

    const wrapper = await mountView();
    await wrapper.find('[data-test="template-delete-tpl-9e961b86377bb8b1"]').trigger("click");
    await flushPromises();
    await wrapper
      .find('[data-test="template-delete-confirm-tpl-9e961b86377bb8b1"]')
      .trigger("click");
    await flushPromises();

    expect(deleteTemplate).toHaveBeenCalledWith("tpl-9e961b86377bb8b1");
  });

  it("可以取消删除", async () => {
    vi.mocked(deleteTemplate).mockResolvedValue(undefined);

    const wrapper = await mountView();
    await wrapper.find('[data-test="template-delete-tpl-9e961b86377bb8b1"]').trigger("click");
    await flushPromises();
    await wrapper
      .find('[data-test="template-delete-cancel-tpl-9e961b86377bb8b1"]')
      .trigger("click");
    await flushPromises();

    expect(deleteTemplate).not.toHaveBeenCalled();
    expect(
      wrapper.find('[data-test="template-delete-confirm-tpl-9e961b86377bb8b1"]').exists()
    ).toBe(false);
  });
});
