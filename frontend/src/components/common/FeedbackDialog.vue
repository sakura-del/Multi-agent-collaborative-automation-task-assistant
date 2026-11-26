<script setup lang="ts">
import { ref } from "vue";
import { useRoute } from "vue-router";
import { Loader2, MessageSquarePlus, Send } from "lucide-vue-next";

import { submitFeedback, type FeedbackCategory } from "@/api/growth";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Button } from "@/components/ui/button";
import { MotionDialog } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { toastSuccess } from "@/lib/toast";

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  (event: "update:open", value: boolean): void;
}>();

const route = useRoute();
const category = ref<FeedbackCategory>("suggestion");
const content = ref("");
const submitting = ref(false);
const errorMessage = ref("");

const CATEGORIES: { value: FeedbackCategory; label: string }[] = [
  { value: "suggestion", label: "功能建议" },
  { value: "bug", label: "问题反馈" },
  { value: "question", label: "使用咨询" },
  { value: "other", label: "其他" },
];

async function handleSubmit() {
  const trimmed = content.value.trim();
  if (trimmed.length < 2) {
    errorMessage.value = "请至少输入 2 个字描述您的反馈";
    return;
  }
  submitting.value = true;
  errorMessage.value = "";
  try {
    await submitFeedback({
      category: category.value,
      content: trimmed,
      page_path: route.fullPath,
    });
    content.value = "";
    category.value = "suggestion";
    emit("update:open", false);
    toastSuccess("感谢反馈！我们已收到并会尽快查看处理");
  } catch (err) {
    errorMessage.value = (err as Error).message || "反馈提交失败，请稍后重试";
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <MotionDialog
    :open="props.open"
    aria-label="提交意见反馈"
    class="max-w-md rounded-xl border border-border bg-card p-5 text-card-foreground shadow-raised"
    :panel-attrs="{ 'data-test': 'feedback-dialog' }"
    @update:open="emit('update:open', $event)"
  >
    <div class="flex items-start justify-between gap-3">
      <div>
        <h2 class="flex items-center gap-2 text-base font-semibold">
          <MessageSquarePlus :size="16" class="text-primary" aria-hidden="true" />
          <span>意见反馈</span>
        </h2>
        <p class="mt-1 text-xs text-muted-foreground">
          遇到 Bug、有产品建议或希望优先支持某项功能，欢迎直接告诉开发者。
        </p>
      </div>
    </div>

    <form class="mt-4 space-y-3.5" @submit.prevent="handleSubmit">
      <div class="grid gap-1.5">
        <span class="text-xs font-medium">反馈类型</span>
        <div class="flex flex-wrap gap-1.5" role="group" aria-label="反馈类型">
          <button
            v-for="opt in CATEGORIES"
            :key="opt.value"
            type="button"
            :data-test="`feedback-cat-${opt.value}`"
            :aria-pressed="category === opt.value"
            class="rounded-md border px-2.5 py-1 text-xs font-medium transition-colors"
            :class="
              category === opt.value
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-border bg-background text-muted-foreground hover:text-foreground'
            "
            @click="category = opt.value"
          >
            {{ opt.label }}
          </button>
        </div>
      </div>

      <div class="grid gap-1.5">
        <Label for="feedback-content" class="text-xs">详细描述</Label>
        <textarea
          id="feedback-content"
          v-model="content"
          data-test="feedback-content"
          rows="4"
          maxlength="2000"
          required
          placeholder="例如：希望支持知网题录自动识别、某页面在手机上显示异常等…"
          class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      <ErrorBanner :message="errorMessage" @dismiss="errorMessage = ''" />

      <div class="flex items-center justify-end gap-2 pt-1">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          :disabled="submitting"
          @click="emit('update:open', false)"
        >
          取消
        </Button>
        <Button
          type="submit"
          size="sm"
          data-test="feedback-submit"
          :disabled="submitting"
        >
          <Loader2 v-if="submitting" class="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <Send v-else :size="13" aria-hidden="true" />
          <span>{{ submitting ? "提交中…" : "提交反馈" }}</span>
        </Button>
      </div>
    </form>
  </MotionDialog>
</template>
