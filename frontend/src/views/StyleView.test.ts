import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import {
  deleteCitationStyle,
  listCitationStyles,
  previewCitationStyles,
  uploadCitationStyle,
} from "@/api/citation-styles";
import type { CitationStyleOut, CitationStylePreview } from "@/api/types";
import StyleView from "@/views/StyleView.vue";

vi.mock("@/api/citation-styles", () => ({
  listCitationStyles: vi.fn(),
  previewCitationStyles: vi.fn(),
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

/** 预览数据：每个样式一条真实渲染结果（各样式文本必须不同）。 */
function previews(): { previews: CitationStylePreview[]; sample_count: number } {
  return {
    previews: [
      {
        style_id: "gbt7714-2025-numeric",
        title: "GB/T 7714—2025（顺序编码制）",
        source: "preset",
        is_default: true,
        entries: ["[1] 牛耕田, 李四. 检索增强生成评估方法综述[J]. 计算机学报, 2024, 47(3): 1-20."],
        markers: { "sample-zh-journal": "[1]" },
        warnings: [],
      },
      {
        style_id: "apa",
        title: "APA 7th",
        source: "preset",
        is_default: false,
        entries: [
          "Niu, G., & Li, S. (2024). 检索增强生成评估方法综述. 计算机学报, 47(3), 1–20.",
        ],
        markers: { "sample-zh-journal": "(Niu & Li, 2024)" },
        warnings: [],
      },
      {
        style_id: "my-style",
        title: "我的国标样式",
        source: "custom",
        is_default: false,
        entries: ["牛耕田, 李四. 检索增强生成评估方法综述. 计算机学报, 2024."],
        markers: {},
        warnings: [],
      },
    ],
    sample_count: 2,
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
    vi.mocked(previewCitationStyles).mockResolvedValue(previews());
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
    // 设计变更（2026-10-26）：删除改为两步确认。后端会 unlink 磁盘文件，
    // 误点一下就得重新上传，所以第一次点击只进确认态。
    // 原断言「点一下即删」是旧行为，不是回归。
    vi.mocked(deleteCitationStyle).mockResolvedValue(undefined);
    const wrapper = await mountView();
    const callsBefore = vi.mocked(listCitationStyles).mock.calls.length;

    await wrapper.find('[data-test="style-delete-my-style"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="style-delete-confirm-my-style"]').trigger("click");
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

/**
 * 样式预览（2026-10-26 重设计）。
 *
 * 每条断言都对应一次**真实取证**（服务器 + Playwright 结构导出）：
 *
 * | 取证 | 事实 |
 * | --- | --- |
 * | `HAS_PREVIEW_WORD = false` | 页面上完全没有「预览」二字，没有任何预览入口 |
 * | `CLICKABLE` 只有 9 个导航链接 + 上传按钮 + 退出登录 | 每个样式行不可点、不可展开 |
 * | `META_DISTINCT = 5 / 6` | `gbt7714-2025-numeric` 与 `gbt7714-2015-numeric` 的元信息完全相同，光看 `citation-number · citation-order · zh-CN` 分不出区别 |
 */
describe("StyleView 预览（2026-10-26）", () => {
  beforeEach(() => {
    vi.mocked(listCitationStyles).mockResolvedValue(styleList());
    vi.mocked(previewCitationStyles).mockResolvedValue(previews());
  });

  it("每个样式都展示真实渲染的预览条目", async () => {
    // 取证：页面只有元信息，没有任何预览内容
    const wrapper = await mountView();

    const preview = wrapper.find('[data-test="style-preview-gbt7714-2025-numeric"]');
    expect(preview.exists()).toBe(true);
    // 必须是渲染出来的条目文本，不是元信息回显
    expect(preview.text()).toContain("检索增强生成评估方法综述");
    expect(preview.text()).toContain("计算机学报");
  });

  it("预览必须能看出样式之间的差异（这是预览的唯一价值）", async () => {
    // 取证：gbt7714-2025-numeric 与 gbt7714-2015-numeric 元信息完全相同。
    // 若预览也相同，用户就没有任何依据在两者间选择。
    const wrapper = await mountView();

    const gb = wrapper.find('[data-test="style-preview-gbt7714-2025-numeric"]').text();
    const apa = wrapper.find('[data-test="style-preview-apa"]').text();

    expect(gb).not.toBe(apa);
    // GB/T 有 [J] 类型标识；APA 没有
    expect(gb).toContain("[J]");
    expect(apa).not.toContain("[J]");
  });

  it("行内标记（marker）也要展示，否则看不出正文里长什么样", async () => {
    const wrapper = await mountView();

    const gb = wrapper.find('[data-test="style-preview-gbt7714-2025-numeric"]');
    // 数字制：[1]；著者-出版年制：(Niu & Li, 2024)
    expect(gb.text()).toContain("[1]");

    const apa = wrapper.find('[data-test="style-preview-apa"]');
    expect(apa.text()).toContain("(Niu & Li, 2024)");
  });

  it("渲染走兜底时必须显式警告，不能当正常预览展示", async () => {
    // 实测踩过：探针 sys.path 注入让 6 个样式全部走兜底格式化器，
    // render() 依然返回非空条目 —— 看起来完全成功，实际 6 个样式只有 2 种输出。
    vi.mocked(previewCitationStyles).mockResolvedValue({
      previews: [
        {
          style_id: "gbt7714-2025-numeric",
          title: "GB/T 7714—2025（顺序编码制）",
          source: "preset",
          is_default: true,
          entries: ["[1] 牛耕田, 李四. 检索增强生成评估方法综述[J]. 计算机学报, 2024."],
          markers: {},
          warnings: ["样式渲染条目失败（ValueError: not found in bundled styles），已回退内置极简格式化器"],
        },
      ],
      sample_count: 2,
    });

    const wrapper = await mountView();

    const warning = wrapper.find('[data-test="style-preview-warning-gbt7714-2025-numeric"]');
    expect(warning.exists()).toBe(true);
    expect(warning.text()).toContain("回退");
  });

  it("预览加载失败不影响样式列表与上传（局部失败不连坐）", async () => {
    // 与套餐页同一类缺陷：一个接口失败不该让整页失去功能
    vi.mocked(previewCitationStyles).mockRejectedValue(new Error("preview 500"));

    const wrapper = await mountView();

    // 列表仍在
    expect(wrapper.find('[data-test="style-row-gbt7714-2025-numeric"]').exists()).toBe(true);
    // 上传仍可用
    expect(wrapper.find('[data-test="style-upload-submit"]').exists()).toBe(true);
    // 预览区给出说明，而不是静默空白
    expect(wrapper.find('[data-test="style-preview-error"]').exists()).toBe(true);
  });

  it("预览区说明样例口径（几条样例、什么语言）", async () => {
    // 用户需要知道「预览用的不是我的文献」，否则会以为预览里应该有自己上传的论文
    const wrapper = await mountView();

    const note = wrapper.find('[data-test="style-preview-note"]');
    expect(note.exists()).toBe(true);
    expect(note.text()).toContain("样例");
  });

  it("加载中不显示空态（避免把加载误报成没有样式）", async () => {
    let resolveList: (value: unknown) => void = () => {};
    vi.mocked(listCitationStyles).mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve;
      }) as never
    );

    const wrapper = mount(StyleView);
    await flushPromises();

    expect(wrapper.find('[data-test="style-empty"]').exists()).toBe(false);

    resolveList(styleList());
    await flushPromises();
    expect(wrapper.find('[data-test="style-row-gbt7714-2025-numeric"]').exists()).toBe(true);
  });
});

/**
 * 删除确认（2026-10-26 重设计）。
 *
 * 取证：`CLICKABLE` 里删除按钮直接 `@click="handleDelete"`，**没有任何确认**。
 * 自定义样式删掉后文件也从磁盘删除（后端 `service.delete` 会 unlink），
 * 误点一下就得重新上传。
 */
describe("StyleView 删除确认（2026-10-26）", () => {
  beforeEach(() => {
    vi.mocked(listCitationStyles).mockResolvedValue(styleList());
    vi.mocked(previewCitationStyles).mockResolvedValue(previews());
  });

  it("点删除先要确认，不能直接删", async () => {
    vi.mocked(deleteCitationStyle).mockResolvedValue(undefined);
    const wrapper = await mountView();

    await wrapper.find('[data-test="style-delete-my-style"]').trigger("click");
    await flushPromises();

    // 第一次点击**不能**直接调删除接口
    expect(deleteCitationStyle).not.toHaveBeenCalled();
    // 必须出现确认
    expect(wrapper.find('[data-test="style-delete-confirm-my-style"]').exists()).toBe(true);
  });

  it("确认后才真正删除", async () => {
    vi.mocked(deleteCitationStyle).mockResolvedValue(undefined);
    const wrapper = await mountView();

    await wrapper.find('[data-test="style-delete-my-style"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="style-delete-confirm-my-style"]').trigger("click");
    await flushPromises();

    expect(deleteCitationStyle).toHaveBeenCalledWith("my-style");
  });

  it("可以取消删除", async () => {
    vi.mocked(deleteCitationStyle).mockResolvedValue(undefined);
    const wrapper = await mountView();

    await wrapper.find('[data-test="style-delete-my-style"]').trigger("click");
    await flushPromises();
    await wrapper.find('[data-test="style-delete-cancel-my-style"]').trigger("click");
    await flushPromises();

    expect(deleteCitationStyle).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="style-delete-confirm-my-style"]').exists()).toBe(false);
  });
});
