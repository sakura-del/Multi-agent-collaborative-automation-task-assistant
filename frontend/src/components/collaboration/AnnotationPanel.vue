<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Check, MessageSquare, RotateCcw, Trash2 } from "lucide-vue-next";

import EmptyState from "@/components/common/EmptyState.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  createAnnotation,
  deleteAnnotation,
  listAnnotations,
  reopenAnnotation,
  resolveAnnotation,
} from "@/api/collaboration";
import type { Annotation } from "@/api/types";

/**
 * 导师批注面板（Phase 2 · Task 6）。
 *
 * ## 为什么默认只做任务级批注
 *
 * 段落级批注需要任务详情页把 Markdown 拆段并逐段算指纹渲染气泡——那是
 * 另一块 UI（本组件的职责是「看/写批注」）。任务级批注已经覆盖导师最常见
 * 的用法（整体评价、要求补局限性/方法细节），段落级通过 `anchor_text`
 * 从任务详情页的段落气泡入口进来，本组件负责展示与状态管理。
 *
 * ## 已解决不删除
 *
 * `resolved_at` 只标记，不删行：导师要能看到历史意见（「这条我提过吗」）。
 * 所以列表**同时显示已解决与未解决**，用徽标区分，并提供「重新打开」。
 *
 * ## 权限决定按钮显隐（后端仍校验）
 *
 * - 作者本人：可删除自己的批注；
 * - 其他人：不显示删除（admin 由后端放行，但前端不主动暴露——
 *   管理动作走任务详情页的显式操作，避免误删导师意见）。
 *
 * ⚠️ 测试契约：`annotation-list` / `annotation-empty` / `annotation-error` /
 * `annotation-form` / `annotation-input` / `annotation-submit` /
 * `annotation-row-<id>` / `annotation-resolved-<id>` / `annotation-resolve-<id>` /
 * `annotation-reopen-<id>` / `annotation-delete-<id>`。
 */
const props = defineProps<{
  taskId: string;
  /** 当前用户 id（决定「删除」按钮是否显示）。 */
  currentUserId?: string;
  /**
   * 去掉自身外框与标题（2026-10-23 任务详情页改为侧边抽屉）。
   *
   * 抽屉自己已有标题栏与边框，面板再带一层会**重复显示「导师批注」**并多一圈框
   * （截图实测）。默认 `false` 保持既有调用方行为不变。
   */
  bare?: boolean;
}>();

const annotations = ref<Annotation[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const busyId = ref<string | null>(null);

const draft = ref("");
const submitting = ref(false);

const unresolvedCount = computed(
  () => annotations.value.filter((item) => !item.resolved_at).length
);

onMounted(() => {
  void refresh();
});

async function refresh() {
  loading.value = true;
  error.value = null;
  try {
    annotations.value = await listAnnotations(props.taskId);
  } catch (err) {
    error.value = (err as Error).message || "批注加载失败";
  } finally {
    loading.value = false;
  }
}

async function submit() {
  const content = draft.value.trim();
  if (!content) {
    return;
  }
  submitting.value = true;
  error.value = null;
  try {
    const created = await createAnnotation(props.taskId, {
      anchor_type: "task",
      content,
    });
    annotations.value = [...annotations.value, created];
    draft.value = "";
  } catch (err) {
    error.value = (err as Error).message || "批注提交失败";
  } finally {
    submitting.value = false;
  }
}

async function toggleResolved(annotation: Annotation) {
  busyId.value = annotation.annotation_id;
  error.value = null;
  try {
    const updated = annotation.resolved_at
      ? await reopenAnnotation(props.taskId, annotation.annotation_id)
      : await resolveAnnotation(props.taskId, annotation.annotation_id);
    annotations.value = annotations.value.map((item) =>
      item.annotation_id === updated.annotation_id ? updated : item
    );
  } catch (err) {
    error.value = (err as Error).message || "状态更新失败";
  } finally {
    busyId.value = null;
  }
}

async function remove(annotation: Annotation) {
  busyId.value = annotation.annotation_id;
  error.value = null;
  try {
    await deleteAnnotation(props.taskId, annotation.annotation_id);
    annotations.value = annotations.value.filter(
      (item) => item.annotation_id !== annotation.annotation_id
    );
  } catch (err) {
    error.value = (err as Error).message || "删除失败";
  } finally {
    busyId.value = null;
  }
}

function canDelete(annotation: Annotation): boolean {
  return Boolean(props.currentUserId) && annotation.author_id === props.currentUserId;
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}
</script>

<template>
  <section :class="props.bare ? '' : 'rounded-lg border border-border bg-card'">
    <!-- bare 模式由外层（抽屉）提供标题栏，这里不再重复显示 -->
    <header
      v-if="!props.bare"
      class="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2"
    >
      <h2 class="flex items-center gap-1.5 text-sm font-medium">
        <MessageSquare class="size-4" />
        导师批注
      </h2>
      <Badge v-if="unresolvedCount" variant="warning">{{ unresolvedCount }} 条待处理</Badge>
      <Badge v-else-if="annotations.length" variant="success">全部已处理</Badge>
    </header>

    <!-- bare 模式下把「待处理」计数放到内容顶部（信息不丢失） -->
    <div v-else-if="unresolvedCount || annotations.length" class="mb-2 flex justify-end">
      <Badge v-if="unresolvedCount" variant="warning">{{ unresolvedCount }} 条待处理</Badge>
      <Badge v-else variant="success">全部已处理</Badge>
    </div>

    <div :class="props.bare ? '' : 'px-3 py-2'">
      <ErrorBanner :message="error" testid="annotation-error" />

      <form class="mb-2" data-test="annotation-form" @submit.prevent="submit">
        <textarea
          v-model="draft"
          rows="2"
          placeholder="写一条批注（例如：请补充局限性讨论）"
          class="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          data-test="annotation-input"
        />
        <div class="mt-1 flex justify-end">
          <Button
            type="submit"
            size="sm"
            :disabled="submitting || !draft.trim()"
            data-test="annotation-submit"
          >
            {{ submitting ? "提交中…" : "添加批注" }}
          </Button>
        </div>
      </form>

      <p v-if="loading" class="text-sm text-muted-foreground">加载中…</p>

      <EmptyState
        v-else-if="!annotations.length && !error"
        data-test="annotation-empty"
        title="还没有批注"
        description="导师可在任务详情页写批注；学生处理完可标记为已解决。"
      />

      <ul v-else class="divide-y divide-border" data-test="annotation-list">
        <li
          v-for="annotation in annotations"
          :key="annotation.annotation_id"
          class="py-2"
          :data-test="`annotation-row-${annotation.annotation_id}`"
        >
          <div class="flex flex-wrap items-start justify-between gap-2">
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-1.5">
                <span class="text-xs font-medium">
                  {{ annotation.author_name ?? "成员" }}
                </span>
                <Badge v-if="annotation.anchor_type === 'paragraph'" variant="outline">
                  段落
                </Badge>
                <Badge
                  v-if="annotation.resolved_at"
                  variant="success"
                  :data-test="`annotation-resolved-${annotation.annotation_id}`"
                >
                  已解决
                </Badge>
                <span class="text-xs text-muted-foreground">
                  {{ formatTime(annotation.created_at) }}
                </span>
              </div>
              <p class="mt-1 whitespace-pre-wrap text-sm">{{ annotation.content }}</p>
            </div>
            <div class="flex shrink-0 items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                :disabled="busyId === annotation.annotation_id"
                :data-test="`annotation-${annotation.resolved_at ? 'reopen' : 'resolve'}-${annotation.annotation_id}`"
                @click="toggleResolved(annotation)"
              >
                <component :is="annotation.resolved_at ? RotateCcw : Check" />
                {{ annotation.resolved_at ? "重新打开" : "标记已解决" }}
              </Button>
              <Button
                v-if="canDelete(annotation)"
                variant="ghost"
                size="sm"
                :disabled="busyId === annotation.annotation_id"
                :data-test="`annotation-delete-${annotation.annotation_id}`"
                @click="remove(annotation)"
              >
                <Trash2 />
                删除
              </Button>
            </div>
          </div>
        </li>
      </ul>
    </div>
  </section>
</template>
