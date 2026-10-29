import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import ReferenceVerificationPanel from "@/components/tasks/ReferenceVerificationPanel.vue";
import type {
  BibliographyEntry,
  ReferenceVerificationSummary,
  VerificationStatus,
} from "@/api/types";

// 类型统一来自 `@/api/types`（计划 §1.2 冻结契约），组件与测试共用同一份声明。
function summary(
  overrides: Partial<ReferenceVerificationSummary> = {}
): ReferenceVerificationSummary {
  return {
    total: 4,
    verified: 1,
    metadata_mismatch: 1,
    not_found: 1,
    unverified: 1,
    cached_hits: 0,
    dropped: 0,
    intercepted: false,
    ...overrides,
  };
}

function entry(
  index: number,
  status: VerificationStatus | null,
  is_retracted: boolean | null = null
): BibliographyEntry {
  return {
    index,
    title: `文献 ${index}`,
    url: `https://arxiv.org/abs/2401.0000${index}v1`,
    verification_status: status,
    is_retracted,
  };
}

function mountPanel(props: Record<string, unknown> = {}) {
  return mount(ReferenceVerificationPanel, {
    props: {
      summary: summary(),
      entries: [entry(1, "verified")],
      ...props,
    },
  });
}

describe("ReferenceVerificationPanel", () => {
  it("renders the four verdict counts with Chinese labels", () => {
    const wrapper = mountPanel({
      summary: summary({
        total: 7,
        verified: 3,
        metadata_mismatch: 2,
        not_found: 1,
        unverified: 1,
      }),
    });

    const expected: Array<[string, string, number]> = [
      ["verified", "已验证", 3],
      ["metadata_mismatch", "元数据不符", 2],
      ["not_found", "未找到", 1],
      ["unverified", "未核验", 1],
    ];
    for (const [verdict, label, count] of expected) {
      const badge = wrapper.find(`[data-test="verification-count-${verdict}"]`);
      expect(badge.exists()).toBe(true);
      expect(badge.text()).toContain(label);
      expect(badge.text()).toContain(String(count));
    }

    expect(wrapper.find('[data-test="verification-panel"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="verification-summary"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="verification-empty"]').exists()).toBe(false);
  });

  it("shows the intercept switch as off with no dropped entries", () => {
    const wrapper = mountPanel({ summary: summary({ intercepted: false, dropped: 0 }) });

    const intercept = wrapper.find('[data-test="verification-intercepted"]');
    expect(intercept.exists()).toBe(true);
    expect(intercept.text()).toContain("拦截：关闭");
    expect(intercept.text()).not.toContain("剔除");
  });

  it("shows the intercept switch as on with the dropped count", () => {
    const wrapper = mountPanel({ summary: summary({ intercepted: true, dropped: 2 }) });

    const intercept = wrapper.find('[data-test="verification-intercepted"]');
    expect(intercept.text()).toContain("拦截：开启");
    expect(intercept.text()).toContain("剔除");
    expect(intercept.text()).toContain("2");
  });

  it("shows the cache hits when present and hides the line when zero", () => {
    const withHits = mountPanel({ summary: summary({ cached_hits: 3 }) });
    expect(withHits.find('[data-test="verification-cached"]').text()).toContain("3");

    const withoutHits = mountPanel({ summary: summary({ cached_hits: 0 }) });
    expect(withoutHits.find('[data-test="verification-cached"]').exists()).toBe(false);
  });

  it("renders an explicit empty state (never misleading zeros) when summary is null", () => {
    const wrapper = mountPanel({ summary: null, entries: [] });

    const empty = wrapper.find('[data-test="verification-empty"]');
    expect(empty.exists()).toBe(true);
    expect(empty.text()).toContain("本次任务未做外部引用核验");

    // 绝不能显示「已验证 0 条」这类误导数字
    expect(wrapper.find('[data-test="verification-summary"]').exists()).toBe(false);
    expect(wrapper.text()).not.toContain("已验证");
    expect(wrapper.text()).not.toContain("未核验 0");
  });

  it("labels every bibliography verdict and skips null statuses", () => {
    const wrapper = mountPanel({
      entries: [
        entry(1, "verified"),
        entry(2, "metadata_mismatch"),
        entry(3, "not_found"),
        entry(4, "unverified"),
        entry(5, null),
      ],
    });

    expect(wrapper.find('[data-test="bibliography-verdict-1"]').text()).toBe("已验证");
    expect(wrapper.find('[data-test="bibliography-verdict-2"]').text()).toBe("元数据不符");
    expect(wrapper.find('[data-test="bibliography-verdict-3"]').text()).toBe("未找到");
    expect(wrapper.find('[data-test="bibliography-verdict-4"]').text()).toBe("未核验");
    // null 状态不渲染徽标，但条目行仍在（标题只作为 title 提示，避免与结果页参考文献列表重复）
    const unverifiedEntry = wrapper.find('[data-test="bibliography-entry-5"]');
    expect(wrapper.find('[data-test="bibliography-verdict-5"]').exists()).toBe(false);
    expect(unverifiedEntry.exists()).toBe(true);
    expect(unverifiedEntry.attributes("title")).toBe("文献 5");
  });

  it("does not render entry rows or badges when the task was not verified", () => {
    const wrapper = mountPanel({ summary: null, entries: [entry(1, "verified")] });

    expect(wrapper.find('[data-test="bibliography-verdict-1"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="bibliography-entry-1"]').exists()).toBe(false);
  });

  it("renders independent retraction badges in three states", () => {
    const wrapper = mountPanel({
      entries: [
        { ...entry(1, "verified"), is_retracted: true },
        { ...entry(2, "verified"), is_retracted: false },
        { ...entry(3, "verified"), is_retracted: null },
      ],
    });

    expect(wrapper.find('[data-test="bibliography-retraction-1"]').text()).toBe("已撤稿");
    expect(wrapper.find('[data-test="bibliography-retraction-2"]').text()).toBe("未标记撤稿");
    expect(wrapper.find('[data-test="bibliography-retraction-3"]').text()).toBe("撤稿状态未知");

    // 语义变体：只有确认撤稿用 destructive；False/None 都不得伪装成「安全」的绿色
    expect(wrapper.find('[data-test="bibliography-retraction-1"]').classes()).toContain(
      "text-destructive"
    );
    expect(wrapper.find('[data-test="bibliography-retraction-2"]').classes()).toContain(
      "text-muted-foreground"
    );
    expect(wrapper.find('[data-test="bibliography-retraction-3"]').classes()).toContain(
      "text-muted-foreground"
    );

    // 原四态徽标保持不变（撤稿是独立徽标，不替代也不改写核验结论）
    expect(wrapper.find('[data-test="bibliography-verdict-1"]').text()).toBe("已验证");
    expect(wrapper.find('[data-test="bibliography-verdict-2"]').text()).toBe("已验证");
    expect(wrapper.find('[data-test="bibliography-verdict-3"]').text()).toBe("已验证");
  });

  it("treats a missing retraction field as unknown and hides it without a verdict", () => {
    const withoutField: BibliographyEntry = {
      index: 1,
      title: "文献 1",
      url: null,
      verification_status: "verified",
    };
    const wrapper = mountPanel({ entries: [withoutField, entry(2, null)] });

    // 字段缺失（老数据 / 未核验）只能显示未知，不能显示「未标记撤稿」或「安全」
    expect(wrapper.find('[data-test="bibliography-retraction-1"]').text()).toBe("撤稿状态未知");
    // 没有四态结论的条目不显示撤稿徽标（撤稿徽标只跟随已核验条目）
    expect(wrapper.find('[data-test="bibliography-retraction-2"]').exists()).toBe(false);
  });

  it("survives an empty entries array and an omitted entries prop", () => {
    const empty = mountPanel({ entries: [] });
    expect(empty.find('[data-test="verification-panel"]').exists()).toBe(true);

    const omitted = mount(ReferenceVerificationPanel, { props: { summary: summary() } });
    expect(omitted.find('[data-test="verification-panel"]').exists()).toBe(true);
    expect(omitted.find('[data-test="verification-summary"]').exists()).toBe(true);
  });
});
