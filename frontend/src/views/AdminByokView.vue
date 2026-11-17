<script setup lang="ts">
/**
 * 管理员侧 BYOK 渗透率看板（Issue #15 P2）。
 *
 * ## 四个数各有分母，必须一起显示
 *
 * - BYOK 任务占比 = BYOK 任务 / 全部任务
 * - BYOK token 占比 = BYOK token / 全部 token
 * - BYOK 用户渗透率 = 跑过 BYOK 的用户 / 跑过任务的用户
 * - 有凭据用户占比 = 有凭据的用户 / 跑过任务的用户
 *
 * 只显示一个「渗透率」会让运营误判：用户可能配了 key 但一直跑平台模型
 * （有凭据 ≠ 用过），也可能没配 key 却全用平台。两个口径分开，才知道
 * 该优化接入流程还是该引导切换。
 *
 * ⚠️ 测试契约：`byok-refresh` / `byok-error` / `byok-days-<n>` /
 * `byok-task-share` / `byok-token-share` / `byok-user-penetration` /
 * `byok-credential-penetration` / `byok-credentials` / `byok-trend-row-<date>` /
 * `byok-provider-<provider>-<model>` / `byok-top-user-<username>` / `byok-empty`。
 */
import { computed, onMounted, ref } from "vue";
import { RefreshCw, ShieldCheck } from "lucide-vue-next";

import { getByokOverview } from "@/api/admin";
import type { ByokOverview } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, StatCard } from "@/components/ui/card";
import EmptyState from "@/components/common/EmptyState.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import PageHeader from "@/components/common/PageHeader.vue";

const overview = ref<ByokOverview | null>(null);
const days = ref(30);
const loading = ref(true);
const error = ref("");

const DAY_OPTIONS = [7, 30, 90] as const;

const isEmpty = computed(() => (overview.value?.totals.tasks ?? 0) === 0);

/** 趋势只画最近 14 天：90 天全画在行内会变成噪音，完整数据在接口里。 */
const trend = computed(() => (overview.value?.trend ?? []).slice(-14));
const maxTrendTokens = computed(() =>
  Math.max(1, ...trend.value.map((point) => point.tokens))
);

function percent(value: number | undefined): string {
  return `${((value ?? 0) * 100).toFixed(1)}%`;
}

function formatTokens(tokens: number): string {
  return tokens.toLocaleString("zh-CN");
}

/** 金额：小额 6 位、大额 4 位；null 是「未收录单价」，不是 0。 */
function formatUsd(value: string | null | undefined): string {
  if (value === null || value === undefined) {
    return "未收录单价";
  }
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "—";
  }
  return amount < 0.01 ? `$${amount.toFixed(6)}` : `$${amount.toFixed(4)}`;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleDateString("zh-CN");
}

function trendWidth(tokens: number): number {
  return Math.max(2, Math.round((tokens / maxTrendTokens.value) * 100));
}

function byokWidth(point: { tokens: number; byok_tokens: number }): number {
  if (point.tokens <= 0) return 0;
  return Math.round((point.byok_tokens / point.tokens) * 100);
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    overview.value = await getByokOverview(days.value);
  } catch (err) {
    error.value = (err as Error).message || "看板加载失败";
  } finally {
    loading.value = false;
  }
}

function selectDays(value: number): void {
  if (days.value === value) return;
  days.value = value;
  void load();
}

onMounted(load);
</script>

<template>
  <section>
    <PageHeader
      eyebrow="Admin · BYOK"
      title="BYOK 渗透率"
      description="用户自带模型的接入与使用情况。只统计窗口内数据，管理员可见。"
    >
      <div class="flex items-center gap-2">
        <div class="flex rounded-md border border-border p-0.5">
          <button
            v-for="option in DAY_OPTIONS"
            :key="option"
            type="button"
            :data-test="`byok-days-${option}`"
            class="rounded px-2 py-1 text-xs transition-colors"
            :class="
              days === option
                ? 'bg-foreground font-medium text-background'
                : 'text-muted-foreground hover:text-foreground'
            "
            @click="selectDays(option)"
          >
            {{ option }} 天
          </button>
        </div>
        <button
          type="button"
          data-test="byok-refresh"
          class="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground disabled:opacity-60"
          :disabled="loading"
          @click="load"
        >
          <RefreshCw
            :size="13"
            :class="loading ? 'animate-spin motion-reduce:animate-none' : ''"
            aria-hidden="true"
          />
          {{ loading ? "刷新中…" : "刷新" }}
        </button>
      </div>
    </PageHeader>

    <ErrorBanner :message="error" testid="byok-error" />

    <div
      v-if="loading && !overview"
      class="mt-3 flex items-center gap-2 rounded-2xl border border-border bg-card px-4 py-8 text-sm text-muted-foreground"
    >
      <RefreshCw :size="15" class="animate-spin motion-reduce:animate-none" aria-hidden="true" />
      正在统计…
    </div>

    <EmptyState
      v-else-if="overview && isEmpty"
      data-test="byok-empty"
      title="窗口内还没有任务"
      description="等有任务跑过之后，这里会显示 BYOK 任务占比与用户渗透率。"
    />

    <template v-else-if="overview">
      <!-- 四个渗透率：每个数字都带分母，避免「0%」歧义 -->
      <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="BYOK 任务占比"
          :value="percent(overview.totals.byok_task_share)"
          value-testid="byok-task-share"
          :hint="`${overview.totals.byok_tasks} / ${overview.totals.tasks} 个任务`"
        />

        <StatCard
          label="BYOK Token 占比"
          :value="percent(overview.totals.byok_token_share)"
          value-testid="byok-token-share"
          :hint="`${formatTokens(overview.totals.byok_tokens)} / ${formatTokens(overview.totals.tokens)} tokens`"
        />

        <StatCard
          label="BYOK 用户渗透率"
          :value="percent(overview.users.byok_penetration)"
          value-testid="byok-user-penetration"
          :hint="`跑过 BYOK：${overview.users.byok_active} / 活跃 ${overview.users.active} 人`"
        />

        <StatCard
          label="有凭据用户占比"
          :value="percent(overview.users.credential_penetration)"
          value-testid="byok-credential-penetration"
          :hint="`有凭据：${overview.users.with_credentials} / 活跃 ${overview.users.active} 人`"
        />
      </div>

      <!-- 凭据健康：接入不等于可用，invalid 要能看见 -->
      <div
        data-test="byok-credentials"
        class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-2xl border border-border bg-card px-3 py-2 text-xs text-muted-foreground"
      >
        <span class="inline-flex items-center gap-1 font-medium text-foreground">
          <ShieldCheck :size="13" aria-hidden="true" />
          凭据 {{ overview.credentials.total }} 把
        </span>
        <span>可用 {{ overview.credentials.active }}</span>
        <span :class="overview.credentials.invalid > 0 ? 'text-warning' : ''">
          失效 {{ overview.credentials.invalid }}
        </span>
        <span>已停用 {{ overview.credentials.disabled }}</span>
        <span class="ml-auto">
          有可用凭据的用户：{{ overview.users.with_active_credentials }}
        </span>
      </div>

      <!-- 成本估算：用户自费 vs 平台承担（公开标价，不是结算账单） -->
      <div data-test="byok-cost" class="mt-3 grid gap-2 sm:grid-cols-3">
        <StatCard
          label="用户自费成本（估算）"
          size="md"
          :value="formatUsd(overview.cost.byok)"
          value-testid="byok-cost-byok"
          hint="BYOK 任务的等值成本"
        />
        <StatCard
          label="平台承担成本（估算）"
          size="md"
          :value="formatUsd(overview.cost.platform)"
          value-testid="byok-cost-platform"
          hint="平台模型任务"
        />
        <StatCard
          label="合计"
          size="md"
          :value="formatUsd(overview.cost.total)"
          value-testid="byok-cost-total"
        >
          <template #hint>
            <p
              v-if="overview.cost.unpriced_byok_tokens + overview.cost.unpriced_platform_tokens > 0"
              class="mt-0.5 text-[11px] text-warning"
            >
              有
              {{
                formatTokens(
                  overview.cost.unpriced_byok_tokens + overview.cost.unpriced_platform_tokens,
                )
              }}
              tokens 未收录单价
            </p>
            <p v-else class="mt-0.5 text-[11px] text-muted-foreground">USD · 公开标价估算</p>
          </template>
        </StatCard>
      </div>
      <p class="mt-1 text-[11px] text-muted-foreground">
        按厂商公开标价估算，不含折扣、缓存命中与批处理优惠；平台任务按默认模型估算。
      </p>

      <div class="mt-4 grid gap-4 lg:grid-cols-2">
        <!-- 趋势：最近 14 天。外层宽度=总量，内层深色=BYOK 部分 -->
        <Card class="rounded-3xl">
          <CardHeader>
            <CardTitle class="flex items-baseline justify-between gap-2 text-base">
              <span>最近 14 天趋势</span>
              <span class="text-xs font-normal text-muted-foreground">按 token</span>
            </CardTitle>
          </CardHeader>
          <CardContent class="space-y-2">
            <div
              v-for="point in trend"
              :key="point.date"
              :data-test="`byok-trend-row-${point.date}`"
              class="grid grid-cols-[4.2rem_1fr_5.5rem] items-center gap-2 text-xs"
            >
              <span class="tabular-nums text-muted-foreground">{{ point.date.slice(5) }}</span>
              <div class="h-2 w-full rounded-full bg-muted">
                <div
                  class="h-full rounded-full bg-primary/25"
                  :style="{ width: `${trendWidth(point.tokens)}%` }"
                  :title="`总量 ${formatTokens(point.tokens)} tokens`"
                >
                  <div
                    class="h-full rounded-full bg-primary"
                    :style="{ width: `${byokWidth(point)}%` }"
                  />
                </div>
              </div>
              <span class="text-right tabular-nums text-muted-foreground">
                {{ point.byok_tasks }}/{{ point.tasks }} 任务
              </span>
            </div>
          </CardContent>
        </Card>

        <!-- 厂商分布：只统计 BYOK 任务 -->
        <Card class="rounded-3xl">
          <CardHeader>
            <CardTitle class="text-base">BYOK 厂商 / 模型</CardTitle>
          </CardHeader>
          <CardContent>
            <p v-if="overview.providers.length === 0" class="text-sm text-muted-foreground">
              窗口内没有 BYOK 任务。
            </p>
            <div v-else class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="border-b border-border text-left text-xs text-muted-foreground">
                    <th class="py-1.5 pr-3 font-medium">厂商 / 模型</th>
                    <th class="py-1.5 pr-3 text-right font-medium">任务</th>
                    <th class="py-1.5 pr-3 text-right font-medium">Tokens</th>
                    <th class="py-1.5 text-right font-medium">成本</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="row in overview.providers"
                    :key="`${row.provider}-${row.model}`"
                    :data-test="`byok-provider-${row.provider}-${row.model}`"
                    class="border-b border-border/60 last:border-0"
                  >
                    <td class="py-1.5 pr-3">
                      <span class="font-medium">{{ row.provider }}</span>
                      <span class="text-muted-foreground"> / {{ row.model }}</span>
                    </td>
                    <td class="py-1.5 pr-3 text-right tabular-nums">{{ row.tasks }}</td>
                    <td class="py-1.5 pr-3 text-right tabular-nums">
                      {{ formatTokens(row.tokens) }}
                    </td>
                    <td class="py-1.5 text-right tabular-nums">
                      {{ row.cost === null ? "未收录" : formatUsd(row.cost) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>

      <!-- 配了但没用：推动切换的靶子名单（全站历史口径） -->
      <Card class="mt-4 rounded-3xl" data-test="byok-dormant">
        <CardHeader>
          <CardTitle class="flex flex-wrap items-baseline justify-between gap-2 text-base">
            <span class="flex items-center gap-2">
              配了但没用
              <Badge variant="warning">{{ overview.dormant_count }}</Badge>
            </span>
            <span class="text-xs font-normal text-muted-foreground">
              已配置凭据、但从未在任务里选择自己的模型（全站历史）
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p
            v-if="overview.dormant_users.length === 0"
            data-test="byok-dormant-empty"
            class="text-sm text-muted-foreground"
          >
            没有这类用户。
          </p>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-border text-left text-xs text-muted-foreground">
                  <th class="py-1.5 pr-3 font-medium">用户</th>
                  <th class="py-1.5 pr-3 text-right font-medium">可用 / 凭据</th>
                  <th class="py-1.5 pr-3 font-medium">最近配置</th>
                  <th class="py-1.5 pr-3 text-right font-medium">平台任务</th>
                  <th class="py-1.5 text-right font-medium">平台 Tokens</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="user in overview.dormant_users"
                  :key="user.user_id"
                  :data-test="`byok-dormant-${user.username}`"
                  class="border-b border-border/60 last:border-0"
                >
                  <td class="py-1.5 pr-3 font-medium">{{ user.username }}</td>
                  <td class="py-1.5 pr-3 text-right tabular-nums">
                    {{ user.active_credentials }} / {{ user.credentials }}
                  </td>
                  <td class="py-1.5 pr-3 text-muted-foreground">
                    {{ formatDate(user.latest_credential_at) }}
                    <span v-if="user.days_since_credential !== null" class="text-[11px]">
                      （{{ user.days_since_credential }} 天前）
                    </span>
                  </td>
                  <td class="py-1.5 pr-3 text-right tabular-nums">{{ user.platform_tasks }}</td>
                  <td class="py-1.5 text-right tabular-nums">
                    {{ formatTokens(user.platform_tokens) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <!-- Top 用户：按 BYOK token 排序，没有 BYOK 的用户不在表里 -->
      <Card class="mt-4 rounded-3xl">
        <CardHeader>
          <CardTitle class="flex items-baseline justify-between gap-2 text-base">
            <span>BYOK Top 用户</span>
            <Badge variant="outline">按 BYOK token 排序</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p v-if="overview.top_users.length === 0" class="text-sm text-muted-foreground">
            窗口内没有用户使用自带模型。
          </p>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-border text-left text-xs text-muted-foreground">
                  <th class="py-1.5 pr-3 font-medium">用户</th>
                  <th class="py-1.5 pr-3 text-right font-medium">BYOK 任务 / 总任务</th>
                  <th class="py-1.5 pr-3 text-right font-medium">BYOK Tokens</th>
                  <th class="py-1.5 pr-3 text-right font-medium">自费成本</th>
                  <th class="py-1.5 text-right font-medium">自身 token 占比</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="user in overview.top_users"
                  :key="user.user_id"
                  :data-test="`byok-top-user-${user.username}`"
                  class="border-b border-border/60 last:border-0"
                >
                  <td class="py-1.5 pr-3 font-medium">{{ user.username }}</td>
                  <td class="py-1.5 pr-3 text-right tabular-nums">
                    {{ user.byok_tasks }} / {{ user.tasks }}
                  </td>
                  <td class="py-1.5 pr-3 text-right tabular-nums">
                    {{ formatTokens(user.byok_tokens) }}
                  </td>
                  <td class="py-1.5 pr-3 text-right tabular-nums">
                    {{ user.byok_cost === null ? "未收录" : formatUsd(user.byok_cost) }}
                  </td>
                  <td class="py-1.5 text-right tabular-nums">
                    {{ percent(user.byok_token_share) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </template>
  </section>
</template>
