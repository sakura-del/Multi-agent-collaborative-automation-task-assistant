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
    // 权益字段（2026-10-26 后端新增）
    allow_team: true,
    allow_api_access: false,
    max_workspaces: 1,
    max_seats_per_workspace: 3,
    price_cents: 1900,
    period_end: "2026-11-22T13:39:27+08:00",
    seats_purchased: 1,
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
    description: "每月 10 篇，支持团队协作与导师批注（3 席位）",
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
    description: "每月 50 篇，开放 API 与 MCP 接入（10 席位）",
  }),
  plan({
    plan_id: "institution",
    name: "机构版",
    monthly_reviews: -1,
    max_workspaces: 100,
    max_seats_per_workspace: 200,
    allow_api_access: true,
    allow_team: true,
    price_cents: 199900,
    description: "篇数不限，按席位计价，支持统一身份与审计",
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
        allow_team: true,
        allow_api_access: true,
        max_workspaces: 100,
        max_seats_per_workspace: 200,
        price_cents: 199900,
        seats_purchased: 5,
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
    expect(cards.length).toBe(4);
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
    // 机构版 199900 分 = ¥1999（不是 ¥1999.00，也不是 ¥19.99）
    expect(wrapper.find('[data-test="plan-card-institution"]').text()).toContain("1999");
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
        allow_team: false,
        allow_api_access: false,
        max_workspaces: 0,
        max_seats_per_workspace: 0,
        price_cents: 0,
        period_end: null,
        seats_purchased: null,
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

/**
 * 重设计新增断言（2026-10-26）。
 *
 * 下面每条都对应一次**真实取证**（服务器 + Playwright 结构导出），
 * 不是版式偏好：
 *
 * | 取证 | 事实 |
 * | --- | --- |
 * | `DEMO_CURRENT_CARD.mentionsTeam/Api/Seats/Workspace` 全为 false | 当前卡只有「机构版 2026-10 3/不限篇」，三项权益一条都没有 |
 * | 页面上唯一可点元素是导航链接 + 一个空按钮 | 免费版用户被告知「升级套餐可立即继续」，但页面上**没有任何升级入口** |
 * | `FREE_PROGRESS_BAR.backgroundColor = oklch(0.62 0.19 250)`（主色蓝） | 额度用尽（100%）时进度条仍是「健康」的蓝色 |
 * | `LOADING_STATE.textLen = 8`（只有两个标题） | 加载中页面空白，无加载态 |
 * | `ERROR_STATE.planCardCount = 0` | usage 接口失败连带清空套餐列表（plans 其实成功了） |
 * | 当前卡 `text` 含 `2026-10` 但无到期日 | 用量周期被当成「订阅到期」展示 |
 */
describe("PlanView 重设计（2026-10-26）", () => {
  it("当前套餐卡必须显示权益，而不是只有名字和篇数", async () => {
    // 取证：当前卡 text = "机构版 2026-10 3 / 不限 篇"，mentionsTeam/Api/Seats 全 false。
    // 用户要判断「该不该升级」得自己去下面四张卡里对照找。
    vi.mocked(getBillingUsage).mockResolvedValue(
      usage({
        plan_id: "institution",
        plan_name: "机构版",
        monthly_reviews: -1,
        reviews_remaining: null,
        reviews_used: 3,
        allow_team: true,
        allow_api_access: true,
        max_workspaces: 100,
        max_seats_per_workspace: 200,
        price_cents: 199900,
        seats_purchased: 5,
      }) as never,
    );
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    const entitlements = wrapper.find('[data-test="plan-entitlements"]');
    expect(entitlements.exists()).toBe(true);
    const text = entitlements.text();
    // 四项权益都要在
    expect(text).toContain("团队");
    expect(text).toContain("API");
    expect(text).toContain("工作区");
    expect(text).toContain("席位");
    // 数值必须来自后端字段，不是硬编码
    expect(text).toContain("100");
    expect(text).toContain("200");
  });

  it("订阅到期日必须显示，且与用量周期分开", async () => {
    // 取证：页面只有 `2026-10`（用量周期），没有订阅到期日。
    // 两者含义不同：用量按月重置，订阅按购买日+30天到期。
    vi.mocked(getBillingUsage).mockResolvedValue(
      usage({ period_end: "2026-11-22T13:39:27+08:00" }) as never,
    );
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    const periodEnd = wrapper.find('[data-test="plan-period-end"]');
    expect(periodEnd.exists()).toBe(true);
    // 2026-11-22 必须出现（日期格式随 locale，只断言年月日关键片段）
    expect(periodEnd.text()).toMatch(/2026/);
    expect(periodEnd.text()).toContain("11");
    expect(periodEnd.text()).toContain("22");

    // 用量周期与到期日是两个不同元素（不能混成一个）
    expect(wrapper.find('[data-test="plan-period-key"]').exists()).toBe(true);
  });

  it("免费档显示「无到期日」，不编假日期", async () => {
    vi.mocked(getBillingUsage).mockResolvedValue(
      usage({
        plan_id: "free",
        plan_name: "免费版",
        monthly_reviews: 1,
        reviews_used: 1,
        reviews_remaining: 0,
        allow_team: false,
        allow_api_access: false,
        max_workspaces: 0,
        max_seats_per_workspace: 0,
        price_cents: 0,
        period_end: null,
        seats_purchased: null,
      }) as never,
    );
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    const periodEnd = wrapper.find('[data-test="plan-period-end"]');
    expect(periodEnd.exists()).toBe(true);
    expect(periodEnd.text()).toContain("免费");
  });

  it("额度用尽时进度条必须是警示色（不能还是健康的蓝色）", async () => {
    // 取证：FREE_PROGRESS_BAR.backgroundColor = oklch(0.62 0.19 250) = --primary 蓝。
    // 100% 用尽的进度条看起来和「用了一半」一样，用户扫一眼不会警觉。
    vi.mocked(getBillingUsage).mockResolvedValue(
      usage({ plan_id: "free", reviews_used: 1, monthly_reviews: 1, reviews_remaining: 0 }) as never,
    );
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    const bar = wrapper.find('[data-test="plan-progress-bar"]');
    expect(bar.exists()).toBe(true);
    // 用语义 token（bg-warning / bg-destructive），不是一次性颜色
    expect(bar.classes().some((c) => c === "bg-warning" || c === "bg-destructive")).toBe(true);
    expect(bar.classes()).not.toContain("bg-primary");
  });

  it("未用尽时进度条保持主色（不要全部变红，否则警示失去意义）", async () => {
    vi.mocked(getBillingUsage).mockResolvedValue(
      usage({ reviews_used: 5, monthly_reviews: 10, reviews_remaining: 5 }) as never,
    );
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    const bar = wrapper.find('[data-test="plan-progress-bar"]');
    expect(bar.classes()).toContain("bg-primary");
  });

  it("额度用尽时提供升级入口（现在只有一句话，没有任何可点的地方）", async () => {
    // 取证：页面上唯一可点元素是导航链接 + 一个空按钮（退出登录）。
    // 文案写着「升级套餐可立即继续」，但用户**没有任何办法升级**。
    vi.mocked(getBillingUsage).mockResolvedValue(
      usage({ plan_id: "free", reviews_used: 1, monthly_reviews: 1, reviews_remaining: 0 }) as never,
    );
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    const hint = wrapper.find('[data-test="plan-upgrade-hint"]');
    expect(hint.exists()).toBe(true);
    // 引导里必须有一个可聚焦的按钮（点它滚到套餐对比区或给出开通方式）
    const action = hint.find("button, a");
    expect(action.exists()).toBe(true);
  });

  it("点击升级入口会滚动到套餐对比区（不能让用户自己找）", async () => {
    const scrollSpy = vi.fn();
    const originalScrollIntoView = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scrollSpy;

    vi.mocked(getBillingUsage).mockResolvedValue(
      usage({ plan_id: "free", reviews_used: 1, monthly_reviews: 1, reviews_remaining: 0 }) as never,
    );
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper.find('[data-test="plan-upgrade-hint"] button').trigger("click");

    expect(scrollSpy).toHaveBeenCalled();

    Element.prototype.scrollIntoView = originalScrollIntoView;
  });

  it("加载中要显示加载态，不能是空白页", async () => {
    // 取证：LOADING_STATE.textLen = 8（只有「我的套餐」「可选套餐」两个标题），
    // hasLoading=false / hasSkeleton=0 / hasSpinner=0 —— 3 秒空白。
    let resolveUsage: (value: unknown) => void = () => {};
    vi.mocked(getBillingUsage).mockReturnValue(
      new Promise((resolve) => {
        resolveUsage = resolve;
      }) as never,
    );
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    const loading = wrapper.find('[data-test="plan-loading"]');
    expect(loading.exists()).toBe(true);
    expect(loading.text()).toContain("加载");

    resolveUsage(usage());
    await flushPromises();
    expect(wrapper.find('[data-test="plan-loading"]').exists()).toBe(false);
  });

  it("usage 接口失败时仍展示套餐列表（plans 成功不该被连累）", async () => {
    // 取证：ERROR_STATE.planCardCount = 0 —— usage 500 导致 4 张套餐卡全消失，
    // 而 listBillingPlans 其实成功了。用户在最需要看套餐的时刻看不到套餐。
    vi.mocked(getBillingUsage).mockRejectedValue(new Error("usage 500"));
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="error-banner"]').exists()).toBe(true);
    // 关键：套餐列表仍在
    expect(wrapper.findAll('[data-test^="plan-card-"]').length).toBe(4);
    // 当前卡缺失是正常的（没有数据），但不能显示成「暂无套餐信息」骗人
    expect(wrapper.find('[data-test="plan-current"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="plan-empty"]').exists()).toBe(false);
  });

  it("当前套餐卡必须能看出「比免费档多了什么」", async () => {
    // 用户最关心的决策信息：我在哪一档、多花 19 元买到什么。
    vi.mocked(getBillingUsage).mockResolvedValue(
      usage({ plan_id: "student", plan_name: "学生版", price_cents: 1900 }) as never,
    );
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    const current = wrapper.find('[data-test="plan-current"]');
    // 月费必须显示（金额信息不能只在下面的对比卡里）
    expect(current.text()).toContain("19");
  });

  it("当前套餐卡不被标成「可选套餐」（它不在可选列表的语义里）", async () => {
    vi.mocked(getBillingUsage).mockResolvedValue(usage() as never);
    vi.mocked(listBillingPlans).mockResolvedValue(PLANS as never);

    const wrapper = mountView();
    await flushPromises();

    // 当前卡在对比区里应该有明确「当前」标记，且不可点（没有支付网关，点了也没用）
    const currentCard = wrapper.find('[data-test="plan-card-student"]');
    expect(currentCard.text()).toContain("当前");
  });
});
