<script setup lang="ts">
/**
 * 我的套餐（Phase 2 · 计费套餐 · 2026-10-26 重设计）。
 *
 * ## 两类 429 必须分开提示
 *
 * | 错误码 | 含义 | 用户该做什么 |
 * | --- | --- | --- |
 * | `PLAN_QUOTA_EXCEEDED` | 本月篇数用尽 | 升级套餐 / 等下月 |
 * | `TOKEN_BUDGET_EXCEEDED` | 今日 token 预算用尽 | **明天再试**（买套餐没用） |
 *
 * 都显示「额度不足」会让用户误以为升级就能解决。
 *
 * ## 这次重设计修的 6 个真实缺陷（都是取证得到的，不是版式偏好）
 *
 * 取证方式：服务器真实接口 + Playwright 结构导出（几何/文本/颜色/可点元素）。
 *
 * | # | 缺陷 | 取证证据 |
 * | --- | --- | --- |
 * | 1 | **当前套餐卡没有任何权益** | `mentionsTeam/Api/Seats/Workspace` 全 false，只有「机构版 2026-10 3 / 不限 篇」。用户判断「该不该升级」得自己去下面四张卡里对照找 |
 * | 2 | **没有订阅到期日** | 页面只有 `2026-10`（用量周期）。两者含义不同：用量按月重置，订阅按购买日+30天到期。用户会以为 10 月底才续费 |
 * | 3 | **额度用尽进度条还是健康蓝** | `backgroundColor = oklch(0.62 0.19 250)`（= `--primary`）。100% 用尽与用了一半长得一样 |
 * | 4 | **说「升级」但没有升级入口** | 页面上可点元素只有导航链接 + 一个空按钮。文案写「升级套餐可立即继续」，用户**没有任何办法升级** |
 * | 5 | **加载中空白** | 两个接口各延迟 3s 时，页面 `textLen = 8`（只有两个标题），无 loading/骨架/spinner |
 * | 6 | **usage 失败连累套餐列表** | `usage` 返回 500 时 `planCardCount = 0`，而 `plans` 其实成功了。用户在最需要看套餐时看不到套餐 |
 *
 * ## 为什么权益字段必须来自后端

 * 「当前套餐卡」直接渲染 `usage.allow_team` 等字段，**不**拿 `plan_id` 去
 * `plans` 列表里找。后者等于前端维护第二份套餐配置 —— 后端改权益/改价时
 * 前端会静默显示旧值。这些字段后端 `Plan` 表本来就有，只是之前没返回。
 */
import { computed, onMounted, ref } from "vue";
import { ArrowUpRight, Check, Loader2, X } from "lucide-vue-next";
import PageHeader from "@/components/common/PageHeader.vue";

import { getBillingUsage, listBillingPlans } from "@/api/billing";
import type { BillingPlan, BillingUsage } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import EmptyState from "@/components/common/EmptyState.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";

const usage = ref<BillingUsage | null>(null);
const plans = ref<BillingPlan[]>([]);
const loading = ref(true);
const error = ref("");
/** 套餐对比区的锚点：升级引导按钮滚到这里。 */
const plansSection = ref<HTMLElement | null>(null);

/** 不限篇数的哨兵值（与后端 `UNLIMITED_REVIEWS` 对齐）。 */
const UNLIMITED = -1;

const isUnlimited = computed(() => usage.value?.monthly_reviews === UNLIMITED);

/**
 * 额度是否已用尽。
 *
 * 用 `reviews_remaining === 0` 判定，**不自己算** `used >= limit`：
 * 剩余量的口径（含下限 0）在服务端定义，前端重算会漂移。
 */
const isExhausted = computed(() => usage.value?.reviews_remaining === 0);

const usedPercent = computed(() => {
  const current = usage.value;
  // 不限篇数 / 免费档 0 额度都不画进度条，这里返回 0 只是兜底
  if (!current || current.monthly_reviews <= 0) return 0;
  const percent = (current.reviews_used / current.monthly_reviews) * 100;
  return Math.min(100, Math.max(0, Math.round(percent)));
});

/**
 * 进度条配色：**只在用尽时变警示色**。
 *
 * 为什么不是「越高越红」：工作台里 80% 是常态，天天红色会让警示失去意义。
 * 只有「已经不能再跑任务」这个状态才需要立刻吸引注意。
 */
const progressBarClass = computed(() =>
  isExhausted.value ? "bg-warning" : "bg-primary"
);

/** 额度用尽时给升级引导（不是「联系管理员」这种无用提示）。 */
const showUpgradeHint = computed(() => isExhausted.value);

/** 当前套餐的权益列表（全部来自后端 `usage`，不查 `plans`）。 */
const entitlements = computed(() => {
  const current = usage.value;
  if (!current) return [];
  return [
    {
      key: "team",
      label: "团队协作",
      enabled: Boolean(current.allow_team),
      detail: current.allow_team ? "支持" : "不支持",
    },
    {
      key: "api",
      label: "开放 API",
      enabled: Boolean(current.allow_api_access),
      detail: current.allow_api_access ? "支持" : "不支持",
    },
    {
      key: "workspaces",
      label: "工作区",
      enabled: (current.max_workspaces ?? 0) > 0,
      detail:
        (current.max_workspaces ?? 0) > 0 ? `${current.max_workspaces} 个` : "不可用",
    },
    {
      key: "seats",
      label: "席位",
      enabled: (current.max_seats_per_workspace ?? 0) > 0,
      detail:
        (current.max_seats_per_workspace ?? 0) > 0
          ? `${current.max_seats_per_workspace} 席/工作区`
          : "不可用",
    },
  ];
});

/** 订阅到期日文本；免费档没有到期概念（`period_end` 为 null）。 */
const periodEndText = computed(() => {
  const value = usage.value?.period_end;
  if (!value) {
    return "免费档（无到期日）";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "—";
  }
  return `${parsed.toLocaleDateString("zh-CN")} 到期`;
});

/** 分 → 元。免费档显示「免费」。 */
function formatPrice(cents: number | undefined): string {
  if (!cents) return "免费";
  const yuan = cents / 100;
  return Number.isInteger(yuan) ? `¥${yuan}` : `¥${yuan.toFixed(2)}`;
}

function formatReviews(reviews: number): string {
  return reviews === UNLIMITED ? "不限" : `${reviews} 篇/月`;
}

function formatWorkspaces(count: number): string {
  return count > 0 ? `${count} 个工作区` : "不含团队";
}

/** 滚动到套餐对比区（升级引导的落地动作）。 */
function scrollToPlans() {
  plansSection.value?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/**
 * 加载数据。
 *
 * ## 两个接口**分别**处理失败（重设计缺陷 6）
 *
 * 原先用 `Promise.all` —— 任一失败就整体进 catch，`plans` 明明成功了也被丢掉，
 * 于是 4 张套餐卡全部消失。而用户恰恰在「额度出问题」时最需要看套餐。
 *
 * 现在用 `allSettled`：谁的错谁报，能显示的照常显示。
 */
async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const [usageResult, plansResult] = await Promise.allSettled([
      getBillingUsage(),
      listBillingPlans(),
    ]);

    if (plansResult.status === "fulfilled") {
      plans.value = plansResult.value;
    }
    if (usageResult.status === "fulfilled") {
      usage.value = usageResult.value;
    }

    // 错误消息：优先报用量（用户更关心「我这个月还能不能跑」）
    const failures = [
      usageResult.status === "rejected" ? "套餐用量" : null,
      plansResult.status === "rejected" ? "套餐列表" : null,
    ].filter(Boolean);
    if (failures.length) {
      const reason =
        usageResult.status === "rejected"
          ? usageResult.reason
          : plansResult.status === "rejected"
            ? plansResult.reason
            : null;
      const detail = reason instanceof Error ? reason.message : "";
      error.value = `${failures.join(" 与 ")}加载失败${detail ? `：${detail}` : ""}`;
    }
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-6">
    <PageHeader
      eyebrow="Subscription & usage"
      title="我的套餐"
      description="当前套餐、本月用量与可选套餐对比。"
    />

    <ErrorBanner v-if="error" :message="error" />

    <!-- 加载态：明确告知「正在取数据」，不要空白页（缺陷 5） -->
    <div
      v-if="loading"
      data-test="plan-loading"
      class="flex items-center gap-2 rounded-lg border border-border bg-card px-4 py-8 text-sm text-muted-foreground"
    >
      <Loader2 :size="16" class="animate-spin motion-reduce:animate-none" aria-hidden="true" />
      <span>正在加载套餐信息…</span>
    </div>

    <template v-else>
      <!-- 当前套餐：名字 + 月费 + 用量 + **权益** + 到期日 -->
      <Card v-if="usage" data-test="plan-current">
        <CardHeader>
          <CardTitle class="flex flex-wrap items-center gap-2 text-base">
            <span>{{ usage.plan_name }}</span>
            <Badge variant="success">当前套餐</Badge>
            <span class="text-sm font-normal text-muted-foreground">
              {{ formatPrice(usage.price_cents) }}
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent class="space-y-4">
          <!-- 用量 -->
          <div>
            <div class="flex flex-wrap items-baseline gap-2 text-sm">
              <span class="text-2xl font-semibold tabular-nums">{{ usage.reviews_used }}</span>
              <span class="text-muted-foreground">
                / {{ isUnlimited ? "不限" : usage.monthly_reviews }} 篇
              </span>
              <span
                v-if="!isUnlimited"
                :class="['text-muted-foreground', isExhausted ? 'text-warning' : '']"
              >
                （本月剩余 {{ usage.reviews_remaining }} 篇）
              </span>
              <span
                data-test="plan-period-key"
                class="ml-auto text-xs text-muted-foreground tabular-nums"
              >
                用量周期 {{ usage.period_key }}
              </span>
            </div>

            <div
              v-if="!isUnlimited"
              class="mt-2 h-2 w-full overflow-hidden rounded-full bg-muted"
              role="progressbar"
              :aria-valuenow="usedPercent"
              aria-valuemin="0"
              aria-valuemax="100"
            >
              <!-- 用尽才变警示色（缺陷 3）：语义 token，不写一次性颜色 -->
              <div
                data-test="plan-progress-bar"
                :class="['h-full rounded-full transition-all', progressBarClass]"
                :style="{ width: `${usedPercent}%` }"
              />
            </div>
          </div>

          <!-- 权益：四项全部来自后端 usage 字段（缺陷 1） -->
          <div data-test="plan-entitlements" class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <div
              v-for="item in entitlements"
              :key="item.key"
              :data-test="`plan-entitlement-${item.key}`"
              class="rounded-md border border-border px-2.5 py-2"
            >
              <p class="flex items-center gap-1 text-[11px] text-muted-foreground">
                <component
                  :is="item.enabled ? Check : X"
                  :size="11"
                  :class="item.enabled ? 'text-success' : 'text-muted-foreground'"
                  aria-hidden="true"
                />
                {{ item.label }}
              </p>
              <p
                :class="[
                  'mt-0.5 text-sm font-medium tabular-nums',
                  item.enabled ? 'text-foreground' : 'text-muted-foreground',
                ]"
              >
                {{ item.detail }}
              </p>
            </div>
          </div>

          <!-- 订阅到期日（缺陷 2）：与用量周期分开显示 -->
          <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span data-test="plan-period-end">{{ periodEndText }}</span>
            <span v-if="usage.seats_purchased">
              已购 {{ usage.seats_purchased }} 席位
            </span>
          </div>

          <!-- 额度用尽：文案 + **可点的升级入口**（缺陷 4） -->
          <div
            v-if="showUpgradeHint"
            data-test="plan-upgrade-hint"
            class="flex flex-wrap items-center gap-2 rounded-md border border-warning/30 bg-warning/8 px-3 py-2"
          >
            <p class="text-sm text-foreground">
              本月额度已用完。升级套餐可立即继续，或等 {{ usage.period_key }} 周期结束后自动重置。
            </p>
            <button
              type="button"
              data-test="plan-upgrade-action"
              class="inline-flex items-center gap-1 rounded-md bg-foreground px-2.5 py-1 text-xs font-medium text-background transition-colors hover:opacity-90"
              @click="scrollToPlans"
            >
              查看可升级套餐
              <ArrowUpRight :size="12" aria-hidden="true" />
            </button>
          </div>
        </CardContent>
      </Card>

      <!-- 真·空态：只在两个接口都失败且没有任何数据时出现 -->
      <EmptyState
        v-else-if="!error"
        testid="plan-empty"
        title="暂无套餐信息"
        description="请先登录后查看套餐。"
      />

      <!-- 套餐对比区 -->
      <section ref="plansSection" class="space-y-3">
        <div class="flex flex-wrap items-baseline justify-between gap-2">
          <h2 class="text-base font-medium">可选套餐</h2>
          <p class="text-xs text-muted-foreground">
            当前为手工开通（暂无在线支付）；如需升级请联系管理员开通。
          </p>
        </div>

        <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Card
            v-for="item in plans"
            :key="item.plan_id"
            :data-test="`plan-card-${item.plan_id}`"
            :data-current="String(usage?.plan_id === item.plan_id)"
            :class="usage?.plan_id === item.plan_id ? 'border-primary/40 ring-1 ring-primary/20' : ''"
          >
            <CardHeader>
              <CardTitle class="flex items-center justify-between text-base">
                {{ item.name }}
                <Badge v-if="usage?.plan_id === item.plan_id" variant="success">当前</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent class="space-y-2 text-sm">
              <p class="text-lg font-semibold tabular-nums">{{ formatPrice(item.price_cents) }}</p>
              <p class="text-muted-foreground">{{ formatReviews(item.monthly_reviews) }}</p>
              <p class="flex items-center gap-1 text-muted-foreground">
                <component
                  :is="item.allow_team ? Check : X"
                  :size="14"
                  :class="item.allow_team ? 'text-success' : 'text-muted-foreground'"
                  aria-hidden="true"
                />
                {{ formatWorkspaces(item.max_workspaces) }}
              </p>
              <p class="flex items-center gap-1 text-muted-foreground">
                <component
                  :is="item.max_seats_per_workspace > 0 ? Check : X"
                  :size="14"
                  :class="item.max_seats_per_workspace > 0 ? 'text-success' : 'text-muted-foreground'"
                  aria-hidden="true"
                />
                {{
                  item.max_seats_per_workspace > 0
                    ? `${item.max_seats_per_workspace} 席/工作区`
                    : "不含席位"
                }}
              </p>
              <p class="flex items-center gap-1 text-muted-foreground">
                <component
                  :is="item.allow_api_access ? Check : X"
                  :size="14"
                  :class="item.allow_api_access ? 'text-success' : 'text-muted-foreground'"
                  aria-hidden="true"
                />
                开放 API：{{ item.allow_api_access ? "支持" : "不支持" }}
              </p>
              <p v-if="item.description" class="pt-1 text-xs text-muted-foreground">
                {{ item.description }}
              </p>
            </CardContent>
          </Card>
        </div>
      </section>
    </template>
  </div>
</template>
