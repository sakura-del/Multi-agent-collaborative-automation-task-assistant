<script setup lang="ts">
import { computed } from "vue";
import { ShieldCheck } from "lucide-vue-next";

import { Badge } from "@/components/ui/badge";
import type { BadgeVariants } from "@/components/ui/badge";
import type {
  BibliographyEntry,
  ReferenceVerificationSummary,
  VerificationStatus,
} from "@/api/types";

/** 四态标签必须与后端 `_VERDICT_LABELS` / 导出报告 `## 引用核验` 口径一致 */
const VERDICT_LABELS: Record<VerificationStatus, string> = {
  verified: "已验证",
  metadata_mismatch: "元数据不符",
  not_found: "未找到",
  unverified: "未核验",
};

/**
 * 判定态 → Badge 语义变体。
 *
 * ⚠️ 必须用**字面量**类名/变体，不能拼字符串：Tailwind 靠静态扫描生成样式，
 * `verify__badge--${status}` 这类动态拼接在原子化后会失效。
 */
const VERDICT_TONE: Record<VerificationStatus, NonNullable<BadgeVariants["variant"]>> = {
  verified: "success",
  metadata_mismatch: "warning",
  not_found: "destructive",
  unverified: "muted",
};

const VERDICT_ORDER: VerificationStatus[] = [
  "verified",
  "metadata_mismatch",
  "not_found",
  "unverified",
];

// props 契约（Issue #3 遗留项 / 计划 §1.2）：类型统一来自 `@/api/types`，不另建并行类型。
const props = withDefaults(
  defineProps<{
    summary: ReferenceVerificationSummary | null;
    entries?: BibliographyEntry[];
  }>(),
  { entries: () => [] },
);

function verdictLabel(status: VerificationStatus | null): string {
  return status ? VERDICT_LABELS[status] : "";
}

/**
 * 撤稿三态标签（与后端 `_retraction_label` 口径一致）。
 *
 * ⚠️ `false` 只表示来源未标记撤稿，**不等于安全**；`null` / 缺失一律显示「未知」，
 * 不得把任一情况粉饰成「未撤稿」。
 */
function retractionLabel(value: boolean | null | undefined): string {
  if (value === true) {
    return "已撤稿";
  }
  if (value === false) {
    return "未标记撤稿";
  }
  return "撤稿状态未知";
}

const counts = computed(() =>
  VERDICT_ORDER.map((verdict) => ({
    verdict,
    label: VERDICT_LABELS[verdict],
    count: props.summary?.[verdict] ?? 0,
    tone: VERDICT_TONE[verdict],
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
  <section class="flex flex-col gap-2" data-test="verification-panel">
    <h3 class="text-sm font-medium">引用核验</h3>

    <template v-if="props.summary">
      <ul class="flex flex-wrap gap-1.5" data-test="verification-summary">
        <li
          v-for="item in counts"
          :key="item.verdict"
          :data-test="`verification-count-${item.verdict}`"
        >
          <Badge :variant="item.tone">
            {{ item.label }}
            <strong class="ml-1 tabular-nums">{{ item.count }}</strong>
          </Badge>
        </li>
      </ul>

      <p class="text-xs text-muted-foreground" data-test="verification-intercepted">
        {{ interceptedLabel }}
      </p>
      <p
        v-if="props.summary.cached_hits > 0"
        class="text-xs text-muted-foreground"
        data-test="verification-cached"
      >
        缓存命中 {{ props.summary.cached_hits }} 条
      </p>

      <!-- 逐条结果只出「序号 + 核验结论」：标题与链接由结果页的参考文献列表承载，避免重复渲染 -->
      <ol
        v-if="props.entries.length"
        class="flex flex-wrap gap-1.5"
        data-test="verification-entries"
      >
        <li
          v-for="entry in props.entries"
          :key="entry.index"
          class="inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5"
          :data-test="`bibliography-entry-${entry.index}`"
          :title="entry.title"
          :aria-label="
            entry.verification_status
              ? `${entry.title}：${verdictLabel(entry.verification_status)}`
              : entry.title
          "
        >
          <span class="text-xs tabular-nums text-muted-foreground">[{{ entry.index }}]</span>
          <Badge
            v-if="entry.verification_status"
            :variant="VERDICT_TONE[entry.verification_status]"
            :data-test="`bibliography-verdict-${entry.index}`"
          >
            {{ verdictLabel(entry.verification_status) }}
          </Badge>
          <!-- 撤稿状态是独立徽标：只对已核验条目显示，且不覆盖四态结论 -->
          <Badge
            v-if="entry.verification_status"
            :variant="entry.is_retracted === true ? 'destructive' : 'muted'"
            :data-test="`bibliography-retraction-${entry.index}`"
          >
            {{ retractionLabel(entry.is_retracted) }}
          </Badge>
        </li>
      </ol>
    </template>

    <!-- 空态：绝不能显示「已验证 0 条」这类误导数字（离线演示任务默认不做核验） -->
    <div
      v-else
      class="flex gap-2 rounded-lg border border-dashed border-border px-3 py-2.5"
      data-test="verification-empty"
    >
      <ShieldCheck class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <div>
        <p class="text-sm font-medium">本次任务未做外部引用核验</p>
        <p class="mt-0.5 text-xs text-muted-foreground">
          未开启「核验引用」开关，或该任务为离线演示任务（离线默认零外呼以保证可复现），
          因此未与 CrossRef/OpenAlex 比对。
        </p>
      </div>
    </div>
  </section>
</template>
