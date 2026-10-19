import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import {
  deleteCitationStyle,
  listCitationStyles,
  uploadCitationStyle,
} from "@/api/citation-styles";
import type { CitationStyleOut } from "@/api/types";
import StyleView from "@/views/StyleView.vue";

vi.mock("@/api/citation-styles", () => ({
  listCitationStyles: vi.fn(),
  uploadCitationStyle: vi.fn(),
  deleteCitationStyle: vi.fn(),
}));

function styleRow(overrides: Partial<CitationStyleOut> = {}): CitationStyleOut {
  return {
    style_id: "gbt7714-2025-numeric",
    title: "GB/T 7714—2025（顺序编码制）",
    numbering: "citation-number",
    ordering: "citation-order",
    default_locale: "zh-CN",
    source: "preset",
    is_default: true,
    ...overrides,
  };
}

function styleList(): { styles: CitationStyleOut[]; default_style_id: string } {
  return {
    styles: [
      styleRow(),
      styleRow({
        style_id: "apa",
        title: "APA 7th",
        default_locale: "en-US",
        is_default: false,
      }),
      styleRow({
        style_id: "my-style",
        title: "我的国标样式",
        source: "custom",
        is_default: false,
      }),
    ],
    default_style_id: "gbt7714-2025-numeric",
  };
}

/** happy-dom 下无法通过 trigger 设置 target.files，直接挂到 input 元素上 */
async function chooseFile(wrapper: ReturnType<typeof mount>, file: File) {
  const inputWrapper = wrapper.find('[data-test="style-file"]');
  Object.defineProperty(inputWrapper.element, "files", {
    value: [file],
    writable: false,
    configurable: true,
  });
  await inputWrapper.trigger("change");
}

function cslFile(name = "my-style.csl"): File {
  return new File(["<style/>"], name, { type: "application/xml" });
}

async function mountView() {
  const wrapper = mount(StyleView);
  await flushPromises();
  return wrapper;
}

describe("StyleView", () => {
  beforeEach(() => {
    vi.mocked(listCitationStyles).mockResolvedValue(styleList());
  });

  it("renders presets and custom styles, marking the default one", async () => {
    const wrapper = await mountView();

    const preset = wrapper.find('[data-test="style-row-gbt7714-2025-numeric"]');
    const custom = wrapper.find('[data-test="style-row-my-style"]');

    expect(preset.exists()).toBe(true);
    expect(custom.exists()).toBe(true);
    expect(preset.text()).toContain("预置");
    expect(preset.text()).toContain("默认");
    expect(custom.text()).toContain("自定义");
    // 预置样式不可删除
    expect(wrapper.find('[data-test="style-delete-gbt7714-2025-numeric"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="style-delete-my-style"]').exists()).toBe(true);
  });

  it("uploads a .csl, reports success and refreshes the list", async () => {
    vi.mocked(uploadCitationStyle).mockResolvedValue(
      styleRow({ style_id: "my-style", title: "我的国标样式", source: "custom" })
    );
    const wrapper = await mountView();

    await chooseFile(wrapper, cslFile());
    await wrapper.find('[data-test="style-upload-form"]').trigger("submit");
    await flushPromises();

    expect(uploadCitationStyle).toHaveBeenCalledTimes(1);
    expect(vi.mocked(uploadCitationStyle).mock.calls[0][0].name).toBe("my-style.csl");
    expect(wrapper.find('[data-test="style-upload-success"]').text()).toContain("我的国标样式");
    // 上传成功后重新拉取列表
    expect(vi.mocked(listCitationStyles).mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("shows the backend rejection reason instead of failing silently", async () => {
    vi.mocked(uploadCitationStyle).mockRejectedValue(
      new Error("样式准入失败：缺少 <info><id>；检测到 CSL-M 方言，暂不支持")
    );
    const wrapper = await mountView();

    await chooseFile(wrapper, cslFile());
    await wrapper.find('[data-test="style-upload-form"]').trigger("submit");
    await flushPromises();

    const banner = wrapper.find('[data-test="style-upload-error"]');
    expect(banner.text()).toContain("缺少 <info><id>");
    expect(banner.text()).toContain("CSL-M");
  });

  it("rejects non-csl files before calling the API", async () => {
    const wrapper = await mountView();

    await chooseFile(wrapper, new File(["x"], "style.txt", { type: "text/plain" }));
    await wrapper.find('[data-test="style-upload-form"]').trigger("submit");
    await flushPromises();

    expect(uploadCitationStyle).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="style-upload-error"]').text()).toContain(".csl");
  });

  it("deletes a custom style and refreshes", async () => {
    vi.mocked(deleteCitationStyle).mockResolvedValue(undefined);
    const wrapper = await mountView();
    const callsBefore = vi.mocked(listCitationStyles).mock.calls.length;

    await wrapper.find('[data-test="style-delete-my-style"]').trigger("click");
    await flushPromises();

    expect(deleteCitationStyle).toHaveBeenCalledWith("my-style");
    expect(vi.mocked(listCitationStyles).mock.calls.length).toBe(callsBefore + 1);
  });

  it("stays usable when the list request fails", async () => {
    vi.mocked(listCitationStyles).mockRejectedValue(new Error("样式列表不可用"));
    const wrapper = await mountView();

    expect(wrapper.find('[data-test="style-load-error"]').text()).toContain("样式列表不可用");
    // 表单仍然可用：加载失败不能让页面失去上传能力
    expect(wrapper.find('[data-test="style-upload-submit"]').exists()).toBe(true);
  });
});
