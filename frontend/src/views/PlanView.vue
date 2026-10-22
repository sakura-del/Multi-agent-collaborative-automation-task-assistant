<script setup lang="ts">
/**
 * 我的套餐（Phase 2 · 计费套餐）。
 *
 * ## 两类 429 必须分开提示
 *
 * | 错误码 | 含义 | 用户该做什么 |
 * | --- | --- | --- |
 * | `PLAN_QUOTA_EXCEEDED` | 本月篇数用尽 | 升级套餐 / 等下月 |
 * | `TOKEN_BUDGET_EXCEEDED` | 今日 token 预算用尽 | **明天再试**（买套餐没用） |
 *
 * 都显示「额度不足」会让用户误以为升级就能解决。
 */
import { computed, onMounted, ref } from "vue";
import { CreditCard, Check, X } from "lucide-vue-next";

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

/** 不限篇数的哨兵值（与后端 `UNLIMITED_REVIEWS` 对齐）。 */
const UNLIMITED = -1;

const isUnlimited = computed(() => usage.value?.monthly_reviews === UNLIMITED);

const usedPercent = computed(() => {
  const current = usage.value;
  if (!current || current.monthly_reviews <= 0) return 0;
  const percent = (current.reviews_used / current.monthly_reviews) * 100;
  return Math.min(100, Math.max(0, Math.round(percent)));
});

/** 额度用尽时给升级引导（不是「联系管理员」这种无用提示）。 */
const showUpgradeHint = computed(
  () => usage.value !== null && usage.value.reviews_remaining === 0,
);

/** 分 → 元。免费档显示「免费」。 */
function formatPrice(cents: number): string {
  if (cents === 0) return "免费";
  const yuan = cents / 100;
  return Number.isInteger(yuan) ? `¥${yuan}` : `¥${yuan.toFixed(2)}`;
}

function formatReviews(reviews: number): string {
  return reviews === UNLIMITED ? "不限" : `${reviews} 篇/月`;
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    const [usageData, planList] = await Promise.all([getBillingUsage(), listBillingPlans()]);
    usage.value = usageData;
    plans.value = planList;
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "加载套餐信息失败";
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-6">
    <header class="flex items-center gap-2">
      <CreditCard class="size-5 text-muted-foreground" />
      <h1 class="text-xl font-semibold">我的套餐</h1>
    </header>

    <ErrorBanner v-if="error" :message="error" />

    <Card v-if="usage" data-test="plan-current">
      <CardHeader>
        <CardTitle class="flex items-center gap-2 text-base">
          {{ usage.plan_name }}
          <Badge variant="secondary">{{ usage.period_key }}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent class="space-y-3">
        <div class="flex items-baseline gap-2 text-sm">
          <span class="text-2xl font-semibold">{{ usage.reviews_used }}</span>
          <span class="text-muted-foreground">
            / {{ isUnlimited ? "不限" : usage.monthly_reviews }} 篇
          </span>
          <span v-if="!isUnlimited" class="text-muted-foreground">
            （本月剩余 {{ usage.reviews_remaining }} 篇）
          </span>
        </div>

        <div
          v-if="!isUnlimited"
          class="h-2 w-full overflow-hidden rounded-full bg-muted"
          role="progressbar"
          :aria-valuenow="usedPercent"
          aria-valuemin="0"
          aria-valuemax="100"
        >
          <div
            data-test="plan-progress-bar"
            class="h-full rounded-full bg-primary transition-all"
            :style="{ width: `${usedPercent}%` }"
          />
        </div>

        <p v-if="showUpgradeHint" data-test="plan-upgrade-hint" class="text-sm text-muted-foreground">
          本月额度已用完。升级套餐可立即继续，或等 {{ usage.period_key }} 周期结束后自动重置。
        </p>
      </CardContent>
    </Card>

    <EmptyState
      v-else-if="!loading && !error"
      data-test="plan-empty"
      title="暂无套餐信息"
      description="请先登录后查看套餐。"
    />

    <section class="space-y-3">
      <h2 class="text-base font-medium">可选套餐</h2>
      <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card
          v-for="item in plans"
          :key="item.plan_id"
          :data-test="`plan-card-${item.plan_id}`"
          :data-current="String(usage?.plan_id === item.plan_id)"
        >
          <CardHeader>
            <CardTitle class="flex items-center justify-between text-base">
              {{ item.name }}
              <Badge v-if="usage?.plan_id === item.plan_id" variant="success">当前</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent class="space-y-2 text-sm">
            <p class="text-lg font-semibold">{{ formatPrice(item.price_cents) }}</p>
            <p class="text-muted-foreground">{{ formatReviews(item.monthly_reviews) }}</p>
            <p class="flex items-center gap-1 text-muted-foreground">
              <component
                :is="item.allow_team ? Check : X"
                class="size-4"
                :class="item.allow_team ? 'text-emerald-600' : 'text-muted-foreground'"
              />
              团队协作：{{ item.allow_team ? "支持" : "不支持" }}
            </p>
            <p class="flex items-center gap-1 text-muted-foreground">
              <component
                :is="item.allow_api_access ? Check : X"
                class="size-4"
                :class="item.allow_api_access ? 'text-emerald-600' : 'text-muted-foreground'"
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
  </div>
</template>
