<script setup lang="ts">
/**
 * 全局 AI 入口（Issue #8 P0）：侧栏一句话创建综述任务。
 *
 * - 展开态：BePromptInput 紧凑版，Enter 直提交（submitOnEnter）；
 * - 折叠态：Sparkles 圆按钮，点击展开侧栏并聚焦输入；
 * - 提交 → POST /tasks（topic = 一句话，平台模型 + 套餐配额由后端门禁）
 *   → 跳转任务详情页（规划中状态即时可见）；
 * - 错误（配额超限/凭据问题）就地展示一行 destructive 文案。
 */
import { ref } from "vue";
import { useRouter } from "vue-router";
import { Sparkles } from "lucide-vue-next";

import BePromptInput from "@/components/motion/BePromptInput.vue";
import { taskApi } from "@/api/tasks";

const props = defineProps<{ collapsed: boolean }>();
const emit = defineEmits<{ expand: [] }>();

const router = useRouter();
const topic = ref("");
const submitting = ref(false);
const error = ref<string | null>(null);

async function submit(value: string) {
  const text = value.trim();
  if (!text || submitting.value) return;
  submitting.value = true;
  error.value = null;
  try {
    // 后端契约 keywords 至少 1 个（min_length=1）：一句话入口自动取句首 30 字
    // 作为检索关键词；检索式的主生成在规划器（topic 才是主输入）。
    const created = await taskApi.create({
      topic: text,
      keywords: [text.slice(0, 30)],
    });
    topic.value = "";
    void router.push(`/tasks/${created.task_id}`);
  } catch (err) {
    error.value = (err as Error).message || "任务创建失败";
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <!-- 折叠态：Sparkles 圆按钮 -->
  <button
    v-if="props.collapsed"
    type="button"
    data-test="global-prompt-collapsed"
    class="mx-auto mb-1 mt-1 flex size-9 items-center justify-center rounded-xl border border-border text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    title="一句话开始一篇综述"
    aria-label="一句话开始一篇综述"
    @click="emit('expand')"
  >
    <Sparkles class="size-4" />
  </button>

  <!-- 展开态：紧凑输入条 -->
  <div v-else class="px-2 pb-1 pt-1">
    <BePromptInput
      v-model="topic"
      data-test="global-prompt"
      placeholder="一句话开始一篇综述…"
      hint="Enter 创建任务"
      min-height="3.5rem"
      :submit-on-enter="true"
      :submitting="submitting"
      class="text-xs"
      @submit="submit"
    />
    <p
      v-if="error"
      data-test="global-prompt-error"
      class="mt-1 truncate px-1 text-[11px] leading-4 text-destructive"
      :title="error"
    >
      {{ error }}
    </p>
  </div>
</template>
