import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import PlanView from "@/views/PlanView.vue";

vi.mock("@/api/billing", () => ({
  getBillingUsage: vi.fn(),
  listBillingPlans: vi.fn(),
}));

import { getBillingUsage, listBillingPlans } from "@/api/billing";

function usage(overrides: Record<string, unknown> = {}) {
  return {
    period_key: "2026-10",
    reviews_used: 3,
    monthly_reviews: 10,
    reviews_remaining: 7,
    plan_id: "student",
    plan_name: "学生版",
    ...overrides,
  };
}

function plan(overrides: Record<string, unknown> = {}) {
  return {
    plan_id: "free",
    name: "免费版",
    monthly_reviews: 1,
    max_workspaces: 0,
    max_seats_per_workspace: 0,
    allow_api_access: false,
    allow_team: false,
    price_cents: 0,
    description: "每月 1 篇综述",
    ...overrides,
  };
}

const PLANS = [
  plan(),
  plan({
    plan_id: "student",
    name: "学生版",
    monthly_reviews: 10,
    max_workspaces: 1,
    max_seats_per_workspace: 3,
    allow_team: true,
    price_cents: 1900,
  }),
  plan({
    plan_id: "pro",
    name: "专业版",
    monthly_reviews: 50,
    max_workspaces: 5,
    max_seats_per_workspace: 10,
    allow_api_access: true,
    allow_team: true,
    price_cents: 6900,
  }),
];

function mountView() {
  return mount(PlanView);
}

beforeEach(() => {
  vi.mocked(getBillingUsage).mockReset();
  vi.mocked(listBillingPlans).mockReset();
});

describe("PlanView", () => {
  it("展示当前套餐与本月用量", async () => {
    vi.mocked(getBillingUsage).mockResolvedValue(usage() as never);
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    const current = wrapper.find('[data-test="plan-current"]');
    expect(current.exists()).toBe(true);
    expect(current.text()).toContain("学生版");
    expect(current.text()).toContain("3");
    expect(current.text()).toContain("10");
  });

  it("进度条宽度按已用/额度计算", async () => {
    vi.mocked(getBillingUsage).mockResolvedValue(usage({ reviews_used: 5 }) as never);
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    const bar = wrapper.find('[data-test="plan-progress-bar"]');
    expect(bar.exists()).toBe(true);
    expect(bar.attributes("style")).toContain("50%");
  });

  it("不限篇数时不显示进度条，显示「不限」", async () => {
    vi.mocked(getBillingUsage).mockResolvedValue(
      usage({
        plan_id: "institution",
        plan_name: "机构版",
        monthly_reviews: -1,
        reviews_remaining: null,
        reviews_used: 42,
      }) as never,
    );
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="plan-progress-bar"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="plan-current"]').text()).toContain("不限");
  });

  it("列出套餐并标出当前套餐", async () => {
    vi.mocked(getBillingUsage).mockResolvedValue(usage() as never);
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    const cards = wrapper.findAll('[data-test^="plan-card-"]');
    expect(cards.length).toBe(3);
    expect(wrapper.find('[data-test="plan-card-student"]').attributes("data-current")).toBe(
      "true",
    );
    expect(wrapper.find('[data-test="plan-card-free"]').attributes("data-current")).toBe(
      "false",
    );
  });

  it("价格以元展示（分转元）", async () => {
    vi.mocked(getBillingUsage).mockResolvedValue(usage() as never);
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="plan-card-student"]').text()).toContain("19");
    expect(wrapper.find('[data-test="plan-card-pro"]').text()).toContain("69");
    expect(wrapper.find('[data-test="plan-card-free"]').text()).toContain("免费");
  });

  it("功能对照：团队与 API 的可用性", async () => {
    vi.mocked(getBillingUsage).mockResolvedValue(usage() as never);
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    const freeCard = wrapper.find('[data-test="plan-card-free"]');
    expect(freeCard.text()).toContain("团队");
    expect(freeCard.text()).toContain("不支持");

    const proCard = wrapper.find('[data-test="plan-card-pro"]');
    expect(proCard.text()).toContain("支持");
  });

  it("加载失败显示错误横幅", async () => {
    vi.mocked(getBillingUsage).mockRejectedValue(new Error("boom"));
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="error-banner"]').exists()).toBe(true);
  });

  it("免费档提示可升级（不显示「联系管理员」这类无用引导）", async () => {
    vi.mocked(getBillingUsage).mockResolvedValue(
      usage({
        plan_id: "free",
        plan_name: "免费版",
        monthly_reviews: 1,
        reviews_used: 1,
        reviews_remaining: 0,
      }) as never,
    );
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    const hint = wrapper.find('[data-test="plan-upgrade-hint"]');
    expect(hint.exists()).toBe(true);
    expect(hint.text()).toContain("升级");
  });
});
