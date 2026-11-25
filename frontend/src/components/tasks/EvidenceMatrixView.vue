<script setup lang="ts">
import { computed } from "vue";
import { Loader2, Search } from "lucide-vue-next";

import type {
  EvidenceMatrix,
  EvidenceMatrixCell,
  EvidenceStrengthBucket,
} from "@/api/types";

/**
 * 研究空白矩阵视图（Issue #32 Phase 3）：
 * 子问题（行）× 证据强度（列：强支持 / 弱支持或存疑 / 冲突 / 空白）。
 */
const props = withDefaults(
  defineProps<{
    matrix: EvidenceMatrix;
    fillingIndex?: number | null;
  }>(),
  { fillingIndex: null },
);

const emit = defineEmits<{
  selectCell: [cell: EvidenceMatrixCell, question: string];
  fillGap: [questionIndex: number, query: string | null];
}>();

const COLUMNS: ReadonlyArray<{
  bucket: EvidenceStrengthBucket;
  label: string;
  tone: string;
}> = [
  {
    bucket: "strong",
    label: "强支持",
    tone: "border-emerald-500/30 bg-emerald-500/8 text-emerald-700 dark:text-emerald-300",
  },
  {
    bucket: "weak_or_caveat",
    label: "弱支持 / 存疑",
    tone: "border-amber-500/30 bg-amber-500/8 text-amber-700 dark:text-amber-300",
  },
  {
    bucket: "conflicting",
    label: "冲突",
    tone: "border-destructive/30 bg-destructive/8 text-destructive",
  },
  {
    bucket: "gap",
    label: "空白 / 无法核验",
    tone: "border-border bg-muted/40 text-muted-foreground",
  },
];

const cellIndex = computed(() => {
  const map = new Map<string, EvidenceMatrixCell>();
  for (const cell of props.matrix.cells) {
    map.set(`${cell.question_index}:${cell.bucket}`, cell);
  }
  return map;
});

function getCell(
  questionIndex: number,
  bucket: EvidenceStrengthBucket,
): EvidenceMatrixCell {
  return (
    cellIndex.value.get(`${questionIndex}:${bucket}`) ?? {
      question_index: questionIndex,
      bucket,
      claim_ids: [],
      ref_indices: [],
      evidence_kind: "none",
      summary: "",
      suggested_query: null,
    }
  );
}

function hasContent(cell: EvidenceMatrixCell): boolean {
  return cell.claim_ids.length > 0 || cell.ref_indices.length > 0;
}

function canFillGap(cell: EvidenceMatrixCell): boolean {
  return (
    cell.bucket === "gap" ||
    (cell.bucket === "weak_or_caveat" && Boolean(cell.suggested_query))
  );
}
</script>

<template>
  <div
    class="overflow-x-auto rounded-lg border border-border bg-card"
    data-test="evidence-matrix"
  >
    <table class="w-full min-w-[44rem] border-collapse text-left text-xs">
      <thead>
        <tr class="border-b border-border bg-muted/30">
          <th class="w-[28%] px-3.5 py-2.5 font-medium text-foreground">
            研究子问题
          </th>
          <th
            v-for="col in COLUMNS"
            :key="col.bucket"
            class="w-[18%] px-3 py-2.5 font-medium text-foreground"
          >
            {{ col.label }}
          </th>
        </tr>
      </thead>
      <tbody class="divide-y divide-border">
        <tr
          v-for="(question, qIdx) in props.matrix.questions"
          :key="qIdx"
          :data-test="`matrix-row-${qIdx}`"
          class="align-top"
        >
          <td class="px-3.5 py-3 text-xs leading-5 font-medium text-foreground">
            <span class="mr-1.5 font-mono text-[10px] text-primary">
              Q{{ qIdx + 1 }}
            </span>
            <span>{{ question }}</span>
          </td>
          <td
            v-for="col in COLUMNS"
            :key="col.bucket"
            class="px-2.5 py-2.5"
          >
            <div
              :data-test="`matrix-cell-${qIdx}-${col.bucket}`"
              class="flex min-h-[5.5rem] flex-col justify-between gap-2 rounded-md border p-2.5 transition-colors"
              :class="
                hasContent(getCell(qIdx, col.bucket))
                  ? col.tone
                  : 'border-dashed border-border/80 bg-background text-muted-foreground'
              "
            >
              <button
                v-if="hasContent(getCell(qIdx, col.bucket))"
                type="button"
                class="flex flex-col items-start gap-1 text-left focus-visible:outline-none"
                :data-test="`matrix-cell-open-${qIdx}-${col.bucket}`"
                @click="emit('selectCell', getCell(qIdx, col.bucket), question)"
              >
                <div class="flex flex-wrap items-center gap-1.5">
                  <span class="font-semibold tabular-nums">
                    {{ getCell(qIdx, col.bucket).claim_ids.length }} 条论断
                  </span>
                  <span class="text-[11px] opacity-80 tabular-nums">
                    · {{ getCell(qIdx, col.bucket).ref_indices.length }} 篇文献
                  </span>
                </div>
                <span
                  v-if="getCell(qIdx, col.bucket).evidence_kind === 'metadata'"
                  :data-test="`matrix-metadata-badge-${qIdx}-${col.bucket}`"
                  class="inline-flex items-center rounded-full border border-current/25 bg-background/60 px-1.5 py-0.5 text-[10px] font-medium"
                >
                  仅题录
                </span>
                <p
                  v-if="getCell(qIdx, col.bucket).summary"
                  class="line-clamp-2 text-[11px] leading-4 opacity-90"
                >
                  {{ getCell(qIdx, col.bucket).summary }}
                </p>
              </button>
              <p v-else class="text-[11px] leading-4 text-muted-foreground/80">
                {{ getCell(qIdx, col.bucket).summary || "—" }}
              </p>

              <button
                v-if="canFillGap(getCell(qIdx, col.bucket))"
                type="button"
                :data-test="`matrix-fill-gap-${qIdx}-${col.bucket}`"
                :disabled="props.fillingIndex !== null"
                class="inline-flex w-fit items-center gap-1 rounded border border-border bg-background px-2 py-1 text-[11px] font-medium text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                @click.stop="
                  emit(
                    'fillGap',
                    qIdx,
                    getCell(qIdx, col.bucket).suggested_query ?? null,
                  )
                "
              >
                <Loader2
                  v-if="props.fillingIndex === qIdx"
                  class="size-3 animate-spin motion-reduce:animate-none"
                  aria-hidden="true"
                />
                <Search v-else class="size-3" aria-hidden="true" />
                <span>{{
                  props.fillingIndex === qIdx ? "补检索中…" : "一键补检索"
                }}</span>
              </button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
