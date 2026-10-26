<script setup lang="ts">
/**
 * 引用样式管理页（Issue #4 M2 §1.5 · 2026-10-26 重设计）。
 *
 * ## 这次重设计修的两个真实缺陷（取证得到的，不是版式偏好）
 *
 * 取证方式：服务器真实接口 + Playwright 结构导出（文本/可点元素/元信息去重）。
 *
 * | # | 缺陷 | 取证证据 |
 * | --- | --- | --- |
 * | 1 | **完全没有预览** | `HAS_PREVIEW_WORD = false`；`CLICKABLE` 只有 9 个导航链接 + 上传按钮 + 退出登录，每个样式行不可点不可展开 |
 * | 2 | **删除没有确认** | 删除按钮直接 `@click="handleDelete"`。后端会 unlink 磁盘文件，误点一下就得重新上传 |
 *
 * ## 缺陷 1 为什么严重
 *
 * 6 个样式里 `gbt7714-2025-numeric` 与 `gbt7714-2015-numeric` 的
 * numbering / ordering / locale **完全相同**（实测 `META_DISTINCT = 5/6`）。
 * 用户光看 `citation-number · citation-order · zh-CN` **分不出区别**，
 * 而这两个样式的实际输出差异明显（2015 版作者全大写、无 `[J]` 类型标识）。
 *
 * ## 预览为什么由后端渲染
 *
 * 导出走 `CitationRenderer`（citeproc + 真实 `.csl`）。前端另写格式化器 =
 * 第二份 CSL 实现，必然漂移 —— 用户会看到「预览长这样、下载出来不是」，
 * 比没有预览更糟。所以预览调 `GET /citation-styles/previews`。
 *
 * ## `warnings` 非空 = 该预览不可信
 *
 * `CitationRenderer` 在 citeproc 失败时**静默回退**极简格式化器，`render()`
 * 依然返回非空条目 —— 看起来完全成功。实测踩过：探针 sys.path 注入让 6 个样式
 * 全部走兜底，输出只有 2 种。所以这里把 warning 显式展示出来。
 *
 * ⚠️ 测试契约（不要改）：`style-upload-form` / `style-file` / `style-upload-submit` /
 * `style-upload-error` / `style-upload-success` / `style-load-error` / `style-empty` /
 * `style-row-<id>` / `style-delete-<id>` / `style-delete-confirm-<id>` /
 * `style-delete-cancel-<id>` / `style-preview-<id>` / `style-preview-warning-<id>` /
 * `style-preview-error` / `style-preview-note`。
 */
import { computed, onMounted, ref } from "vue";
import { CircleCheck, Loader2, Trash2, Upload } from "lucide-vue-next";

import ErrorBanner from "@/components/common/ErrorBanner.vue";
import FilePicker from "@/components/common/FilePicker.vue";
import EmptyState from "@/components/common/EmptyState.vue";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  deleteCitationStyle,
  listCitationStyles,
  previewCitationStyles,
  uploadCitationStyle,
} from "@/api/citation-styles";
import type { CitationStyleOut, CitationStylePreview } from "@/api/types";

const ACCEPTED = [".csl"];

const styles = ref<CitationStyleOut[]>([]);
const previews = ref<CitationStylePreview[]>([]);
const previewError = ref<string | null>(null);
const defaultStyleId = ref("");
const loading = ref(false);
const loadError = ref<string | null>(null);
const uploadError = ref<string | null>(null);
const successMessage = ref<string | null>(null);
const uploading = ref(false);
const deletingId = ref<string | null>(null);
const selectedFile = ref<File | null>(null);
const filePicker = ref<{ reset: () => void } | null>(null);
/** 待确认删除的样式 id（非空时该行显示确认条）。 */
const confirmingDeleteId = ref<string | null>(null);

const defaultStyleTitle = computed(
  () => styles.value.find((style) => style.style_id === defaultStyleId.value)?.title ?? ""
);
const customCount = computed(
  () => styles.value.filter((style) => style.source === "custom").length
);
const hint = computed(() => {
  if (!styles.value.length) {
    return "上传 .csl 文件即可在创建任务时选用；系统预置样式不可删除";
  }
  const base = `${styles.value.length} 种样式（自定义 ${customCount.value} 种）`;
  return defaultStyleTitle.value ? `${base} · 默认：${defaultStyleTitle.value}` : base;
});

/** style_id → 预览（按 id 对齐，不依赖数组下标顺序）。 */
const previewById = computed(() => {
  const map = new Map<string, CitationStylePreview>();
  for (const item of previews.value) {
    map.set(item.style_id, item);
  }
  return map;
});

/** 样例条数（用于说明预览口径）。 */
const sampleCount = computed(() => previews.value[0]?.entries.length ?? 0);

onMounted(() => {
  void refresh();
});

/**
 * 加载列表 + 预览。
 *
 * ## 两个请求**分别**处理失败
 *
 * 预览是新功能，失败不该让用户失去「看列表 + 上传 + 删除」的能力；
 * 反过来列表失败时预览也没意义。所以用 `allSettled`，各自报错。
 */
async function refresh() {
  loading.value = true;
  loadError.value = null;
  previewError.value = null;
  try {
    const [listResult, previewResult] = await Promise.allSettled([
      listCitationStyles(),
      previewCitationStyles(),
    ]);

    if (listResult.status === "fulfilled") {
      styles.value = listResult.value.styles;
      defaultStyleId.value = listResult.value.default_style_id;
    } else {
      loadError.value = (listResult.reason as Error).message || "样式列表加载失败";
    }

    if (previewResult.status === "fulfilled") {
      previews.value = previewResult.value.previews;
    } else {
      // 只让预览区降级：列表与上传仍然可用
      previewError.value =
        (previewResult.reason as Error).message || "样式预览加载失败";
      previews.value = [];
    }
  } finally {
    loading.value = false;
  }
}

function extensionOf(name: string): string {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

function fileError(file: File | null): string | null {
  if (!file) {
    return "请先选择文件";
  }
  if (!ACCEPTED.includes(extensionOf(file.name))) {
    return `仅支持 ${ACCEPTED.join(" / ")} 样式文件`;
  }
  return null;
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0] ?? null;
  selectedFile.value = file;
  successMessage.value = null;
  uploadError.value = fileError(file);
}

async function handleUpload() {
  successMessage.value = null;
  const invalid = fileError(selectedFile.value);
  if (invalid) {
    uploadError.value = invalid;
    return;
  }
  uploadError.value = null;
  uploading.value = true;
  try {
    const created = await uploadCitationStyle(selectedFile.value as File);
    successMessage.value = `已上传样式「${created.title}」（${created.style_id}）`;
    selectedFile.value = null;
    filePicker.value?.reset();
    await refresh();
  } catch (error) {
    // 后端 400 的 detail（含逐条 reasons）在这里展示
    uploadError.value = (error as Error).message || "样式上传失败";
  } finally {
    uploading.value = false;
  }
}

/** 第一次点击：进入确认态（不删）。 */
function requestDelete(styleId: string) {
  uploadError.value = null;
  successMessage.value = null;
  confirmingDeleteId.value = styleId;
}

function cancelDelete() {
  confirmingDeleteId.value = null;
}

/** 确认后才真正删除。 */
async function confirmDelete(styleId: string) {
  confirmingDeleteId.value = null;
  uploadError.value = null;
  successMessage.value = null;
  deletingId.value = styleId;
  try {
    await deleteCitationStyle(styleId);
    successMessage.value = `已删除自定义样式 ${styleId}`;
    await refresh();
  } catch (error) {
    uploadError.value = (error as Error).message || "样式删除失败";
  } finally {
    deletingId.value = null;
  }
}
</script>

<template>
  <section>
    <header class="mb-4">
      <h1 class="text-lg font-semibold tracking-tight">引用样式</h1>
      <p class="mt-0.5 text-sm text-muted-foreground">{{ hint }}</p>
    </header>

    <ErrorBanner :message="loadError" testid="style-load-error" />

    <form
      class="mb-3 flex flex-wrap items-end gap-3 rounded-lg border border-border bg-card p-4"
      data-test="style-upload-form"
      @submit.prevent="handleUpload"
    >
      <div class="grid gap-1.5">
        <Label for="style-file">样式文件</Label>
        <FilePicker
          id="style-file"
          ref="filePicker"
          data-test="style-file"
          :accept="ACCEPTED.join(',')"
          placeholder="选择 .csl 文件"
          @change="onFileChange"
        />
      </div>
      <Button data-test="style-upload-submit" type="submit" :disabled="uploading">
        <Loader2 v-if="uploading" class="animate-spin" aria-hidden="true" />
        <Upload v-else :size="15" aria-hidden="true" />
        <span>{{ uploading ? "上传中…" : "上传样式" }}</span>
      </Button>
    </form>

    <ErrorBanner :message="uploadError" testid="style-upload-error" />
    <Alert
      v-if="successMessage"
      variant="success"
      class="mb-3"
      data-test="style-upload-success"
    >
      <CircleCheck />
      <span>{{ successMessage }}</span>
    </Alert>

    <p v-if="loading && !styles.length" class="text-sm text-muted-foreground">加载中…</p>
    <EmptyState
      v-else-if="!styles.length"
      testid="style-empty"
      title="还没有可用样式"
      description="上传一个 .csl 文件即可作为自定义引用样式使用。"
    />

    <template v-else>
      <!-- 预览口径说明：用户需要知道预览用的不是自己的文献 -->
      <div
        v-if="!previewError"
        data-test="style-preview-note"
        class="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"
      >
        <span>
          下方每条预览由服务端按该样式渲染 <strong>{{ sampleCount }}</strong> 条样例文献
          （中文期刊 + 英文会议），与最终导出使用同一个渲染引擎。
        </span>
      </div>
      <ErrorBanner :message="previewError" testid="style-preview-error" />

      <ul class="space-y-2">
        <li
          v-for="style in styles"
          :key="style.style_id"
          class="rounded-lg border border-border bg-card px-3 py-2.5"
          :data-test="`style-row-${style.style_id}`"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-1.5">
                <span class="text-sm font-medium">{{ style.title }}</span>
                <Badge :variant="style.source === 'custom' ? 'outline' : 'muted'">
                  {{ style.source === "custom" ? "自定义" : "预置" }}
                </Badge>
                <Badge v-if="style.style_id === defaultStyleId" variant="default">默认</Badge>
              </div>
              <div class="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <code class="rounded bg-muted px-1 py-0.5">{{ style.style_id }}</code>
                <span>{{ style.numbering }} · {{ style.ordering }} · {{ style.default_locale }}</span>
              </div>
            </div>
            <Button
              v-if="style.source === 'custom'"
              variant="ghost"
              size="icon-sm"
              :data-test="`style-delete-${style.style_id}`"
              :disabled="deletingId === style.style_id"
              :title="`删除 ${style.title}`"
              :aria-label="`删除 ${style.title}`"
              @click="requestDelete(style.style_id)"
            >
              <Trash2 :size="15" />
            </Button>
          </div>

          <!-- 删除确认：后端会 unlink 磁盘文件，误点就得重新上传 -->
          <div
            v-if="confirmingDeleteId === style.style_id"
            class="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-destructive/30 bg-destructive/8 px-2.5 py-1.5"
          >
            <span class="text-xs text-foreground">
              删除后文件将从磁盘移除，需要重新上传。确认删除「{{ style.title }}」？
            </span>
            <button
              type="button"
              :data-test="`style-delete-confirm-${style.style_id}`"
              class="rounded bg-destructive px-2 py-0.5 text-xs font-medium text-destructive-foreground"
              @click="confirmDelete(style.style_id)"
            >
              确认删除
            </button>
            <button
              type="button"
              :data-test="`style-delete-cancel-${style.style_id}`"
              class="rounded border border-border px-2 py-0.5 text-xs font-medium"
              @click="cancelDelete"
            >
              取消
            </button>
          </div>

          <!-- 预览：真实渲染结果（后端 citeproc 渲染，与导出同一引擎） -->
          <div
            v-if="previewById.get(style.style_id)"
            :data-test="`style-preview-${style.style_id}`"
            class="mt-2 rounded-md border border-border bg-background px-2.5 py-2"
          >
            <p
              v-for="(entry, index) in previewById.get(style.style_id)?.entries ?? []"
              :key="index"
              class="text-xs leading-relaxed text-foreground"
            >
              {{ entry }}
            </p>
            <!-- 行内标记：用户要能看出「正文里引用长什么样」 -->
            <p
              v-if="Object.keys(previewById.get(style.style_id)?.markers ?? {}).length"
              class="mt-1 text-[11px] text-muted-foreground"
            >
              正文标记：
              <code
                v-for="(marker, refId) in previewById.get(style.style_id)?.markers ?? {}"
                :key="refId"
                class="mr-1 rounded bg-muted px-1 py-0.5"
              >
                {{ marker }}
              </code>
            </p>
            <!-- 兜底告警：非空说明该输出不代表此样式，必须显式提示 -->
            <p
              v-if="previewById.get(style.style_id)?.warnings?.length"
              :data-test="`style-preview-warning-${style.style_id}`"
              class="mt-1 rounded bg-warning/12 px-1.5 py-1 text-[11px] text-warning"
            >
              ⚠️ 该预览可能未按样式渲染（已回退内置格式化器）：
              {{ previewById.get(style.style_id)?.warnings?.[0] }}
            </p>
          </div>
        </li>
      </ul>
    </template>
  </section>
</template>
