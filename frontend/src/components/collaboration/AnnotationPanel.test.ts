import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import AnnotationPanel from "@/components/collaboration/AnnotationPanel.vue";

vi.mock("@/api/collaboration", () => ({
  listAnnotations: vi.fn(),
  createAnnotation: vi.fn(),
  resolveAnnotation: vi.fn(),
  reopenAnnotation: vi.fn(),
  deleteAnnotation: vi.fn(),
}));

import {
  createAnnotation,
  deleteAnnotation,
  listAnnotations,
  reopenAnnotation,
  resolveAnnotation,
} from "@/api/collaboration";

function annotation(overrides: Record<string, unknown> = {}) {
  return {
    annotation_id: "a1",
    task_id: "t1",
    author_id: "u1",
    author_name: "导师",
    anchor_type: "task",
    anchor_key: null,
    content: "请补充局限性讨论",
    resolved_at: null,
    created_at: "2026-10-21T10:00:00Z",
    ...overrides,
  };
}

function mountPanel(props: Record<string, unknown> = {}) {
  return mount(AnnotationPanel, {
    props: { taskId: "t1", currentUserId: "u1", ...props },
  });
}

beforeEach(() => {
  vi.mocked(listAnnotations).mockReset();
  vi.mocked(createAnnotation).mockReset();
  vi.mocked(resolveAnnotation).mockReset();
  vi.mocked(reopenAnnotation).mockReset();
  vi.mocked(deleteAnnotation).mockReset();
});

describe("AnnotationPanel", () => {
  it("展示批注列表与待处理计数", async () => {
    vi.mocked(listAnnotations).mockResolvedValue([
      annotation(),
      annotation({ annotation_id: "a2", content: "第二条", author_id: "u2" }),
    ] as never);

    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.find('[data-test="annotation-list"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("请补充局限性讨论");
    expect(wrapper.text()).toContain("2 条待处理");
  });

  it("空态给出「导师可写批注」的引导", async () => {
    vi.mocked(listAnnotations).mockResolvedValue([] as never);

    const wrapper = mountPanel();
    await flushPromises();

    const empty = wrapper.find('[data-test="annotation-empty"]');
    expect(empty.exists()).toBe(true);
    expect(empty.text()).toContain("批注");
  });

  it("加载失败显示错误而非空态", async () => {
    vi.mocked(listAnnotations).mockRejectedValue(new Error("后端 500"));

    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.find('[data-test="annotation-error"]').text()).toContain("后端 500");
    expect(wrapper.find('[data-test="annotation-empty"]').exists()).toBe(false);
  });

  it("提交批注后追加到列表并清空输入", async () => {
    vi.mocked(listAnnotations).mockResolvedValue([] as never);
    vi.mocked(createAnnotation).mockResolvedValue(
      annotation({ annotation_id: "new1", content: "新批注" }) as never
    );

    const wrapper = mountPanel();
    await flushPromises();

    await wrapper.get('[data-test="annotation-input"]').setValue("新批注");
    await wrapper.get('[data-test="annotation-form"]').trigger("submit");
    await flushPromises();

    // 任务级批注：anchor_type=task（段落批注走任务详情页的段落入口）
    expect(createAnnotation).toHaveBeenCalledWith("t1", {
      anchor_type: "task",
      content: "新批注",
    });
    expect(wrapper.find('[data-test="annotation-row-new1"]').exists()).toBe(true);
    expect((wrapper.get('[data-test="annotation-input"]').element as HTMLTextAreaElement).value).toBe(
      ""
    );
  });

  it("空白内容不提交", async () => {
    vi.mocked(listAnnotations).mockResolvedValue([] as never);

    const wrapper = mountPanel();
    await flushPromises();

    await wrapper.get('[data-test="annotation-input"]').setValue("   ");
    await wrapper.get('[data-test="annotation-form"]').trigger("submit");
    await flushPromises();

    expect(createAnnotation).not.toHaveBeenCalled();
  });

  it("标记已解决后显示「已解决」徽标且仍在列表里", async () => {
    vi.mocked(listAnnotations).mockResolvedValue([annotation()] as never);
    vi.mocked(resolveAnnotation).mockResolvedValue(
      annotation({ resolved_at: "2026-10-21T12:00:00Z" }) as never
    );

    const wrapper = mountPanel();
    await flushPromises();

    await wrapper.get('[data-test="annotation-resolve-a1"]').trigger("click");
    await flushPromises();

    // 已解决不删除（导师要能看到历史意见）
    expect(wrapper.find('[data-test="annotation-row-a1"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="annotation-resolved-a1"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("全部已处理");
  });

  it("已解决的批注可重新打开", async () => {
    vi.mocked(listAnnotations).mockResolvedValue([
      annotation({ resolved_at: "2026-10-21T12:00:00Z" }),
    ] as never);
    vi.mocked(reopenAnnotation).mockResolvedValue(annotation() as never);

    const wrapper = mountPanel();
    await flushPromises();

    await wrapper.get('[data-test="annotation-reopen-a1"]').trigger("click");
    await flushPromises();

    expect(reopenAnnotation).toHaveBeenCalledWith("t1", "a1");
    expect(wrapper.find('[data-test="annotation-resolved-a1"]').exists()).toBe(false);
  });

  it("只有作者本人能看到删除按钮", async () => {
    vi.mocked(listAnnotations).mockResolvedValue([
      annotation({ annotation_id: "mine", author_id: "u1" }),
      annotation({ annotation_id: "others", author_id: "u2" }),
    ] as never);

    const wrapper = mountPanel({ currentUserId: "u1" });
    await flushPromises();

    expect(wrapper.find('[data-test="annotation-delete-mine"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="annotation-delete-others"]').exists()).toBe(false);
  });

  it("删除后从列表移除", async () => {
    vi.mocked(listAnnotations).mockResolvedValue([annotation()] as never);
    vi.mocked(deleteAnnotation).mockResolvedValue(undefined as never);

    const wrapper = mountPanel();
    await flushPromises();

    await wrapper.get('[data-test="annotation-delete-a1"]').trigger("click");
    await flushPromises();

    expect(deleteAnnotation).toHaveBeenCalledWith("t1", "a1");
    expect(wrapper.find('[data-test="annotation-row-a1"]').exists()).toBe(false);
  });

  it("段落批注显示「段落」徽标", async () => {
    vi.mocked(listAnnotations).mockResolvedValue([
      annotation({ anchor_type: "paragraph", anchor_key: "abcdef1234567890" }),
    ] as never);

    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.find('[data-test="annotation-row-a1"]').text()).toContain("段落");
  });
});
