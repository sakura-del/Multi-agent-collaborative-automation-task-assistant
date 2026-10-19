<script setup lang="ts">
import { computed } from "vue";

import type {
  BibliographyEntry,
  ReferenceVerificationSummary,
  VerificationStatus,
} from "@/api/types";

// props 契约（Issue #3 遗留项 / 计划 §1.2）：类型统一来自 `@/api/types`，不另建并行类型。
const props = withDefaults(
  defineProps<{
    summary: ReferenceVerificationSummary | null;
    entries?: BibliographyEntry[];
  }>(),
  { entries: () => [] },
);

/** 四态标签必须与后端 `_VERDICT_LABELS` / 导出报告 `## 引用核验` 口径一致 */
const VERDICT_LABELS: Record<VerificationStatus, string> = {
  verified: "已验证",
  metadata_mismatch: "元数据不符",
  not_found: "未找到",
  unverified: "未核验",
};

const VERDICT_ORDER: VerificationStatus[] = [
  "verified",
  "metadata_mismatch",
  "not_found",
  "unverified",
];

/** 判定态 → 类名（`metadata_mismatch` → `verify__badge--metadata-mismatch`） */
function badgeClass(status: VerificationStatus): string {
  return `verify__badge--${status.replace(/_/g, "-")}`;
}

function verdictLabel(status: VerificationStatus | null): string {
  return status ? VERDICT_LABELS[status] : "";
}

const counts = computed(() =>
  VERDICT_ORDER.map((verdict) => ({
    verdict,
    label: VERDICT_LABELS[verdict],
    count: props.summary?.[verdict] ?? 0,
    badgeClass: badgeClass(verdict),
  })),
);

/** 拦截开关状态：开启时显示被剔除条数，便于审计「哪些文献被拿掉了」 */
const interceptedLabel = computed(() => {
  const summary = props.summary;
  if (!summary) {
    return "";
  }
  return summary.intercepted ? `拦截：开启（已剔除 ${summary.dropped} 条）` : "拦截：关闭";
});
</script>

<template>
  <section class="verify" data-test="verification-panel">
    <h3 class="verify__title">引用核验</h3>

    <template v-if="props.summary">
      <ul class="verify__counts" data-test="verification-summary">
        <li
          v-for="item in counts"
          :key="item.verdict"
          class="verify__badge"
          :class="item.badgeClass"
          :data-test="`verification-count-${item.verdict}`"
        >
          <span>{{ item.label }}</span>
          <strong class="verify__badge-count">{{ item.count }}</strong>
        </li>
      </ul>

      <p class="verify__meta" data-test="verification-intercepted">{{ interceptedLabel }}</p>
      <p
        v-if="props.summary.cached_hits > 0"
        class="verify__meta verify__meta--muted"
        data-test="verification-cached"
      >
        缓存命中 {{ props.summary.cached_hits }} 条
      </p>

      <!-- 逐条结果只出「序号 + 核验结论」：标题与链接由结果页的参考文献列表承载，避免重复渲染 -->
      <ol v-if="props.entries.length" class="verify__entries" data-test="verification-entries">
        <li
          v-for="entry in props.entries"
          :key="entry.index"
          class="verify__entry"
          :data-test="`bibliography-entry-${entry.index}`"
          :title="entry.title"
          :aria-label="
            entry.verification_status ? `${entry.title}：${verdictLabel(entry.verification_status)}` : entry.title
          "
        >
          <span class="verify__entry-index">[{{ entry.index }}]</span>
          <span
            v-if="entry.verification_status"
            class="verify__badge"
            :class="badgeClass(entry.verification_status)"
            :data-test="`bibliography-verdict-${entry.index}`"
          >
            {{ verdictLabel(entry.verification_status) }}
          </span>
        </li>
      </ol>
    </template>

    <!-- 空态：绝不能显示「已验证 0 条」这类误导数字（离线演示任务默认不做核验） -->
    <div v-else class="verify__empty" data-test="verification-empty">
      <p class="verify__empty-title">本次任务未做外部引用核验</p>
      <p class="verify__empty-reason">
        未开启「核验引用」开关，或该任务为离线演示任务（离线默认零外呼以保证可复现），
        因此未与 CrossRef/OpenAlex 比对。
      </p>
    </div>
  </section>
</template>

<style scoped>
.verify {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  font-size: 0.85rem;
}

.verify__title {
  font-size: 0.95rem;
}

.verify__counts,
.verify__entries {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.verify__badge {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.1rem 0.45rem;
  border: 1px solid currentColor;
  border-radius: 999px;
  font-size: 0.78rem;
  line-height: 1.5;
  background: var(--color-card);
}

.verify__badge-count {
  font-variant-numeric: tabular-nums;
}

.verify__badge--verified {
  color: var(--color-success);
}

.verify__badge--metadata-mismatch {
  color: var(--color-warning);
}

.verify__badge--not-found {
  color: var(--color-error);
}

/* 未核验 = 降级态：项目未定义灰色变量，沿用既有 slate 中性色值 */
.verify__badge--unverified {
  color: #64748b;
  border-color: #cbd5e1;
}

.verify__meta {
  color: #475569;
  font-variant-numeric: tabular-nums;
}

.verify__meta--muted {
  color: #64748b;
}

.verify__entry {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}

.verify__entry-index {
  color: #475569;
  font-variant-numeric: tabular-nums;
}

.verify__empty {
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  padding: 0.5rem 0.75rem;
  background: var(--color-card);
}

.verify__empty-title {
  font-weight: 600;
}

.verify__empty-reason {
  color: #64748b;
  margin-top: 0.15rem;
}
</style>
