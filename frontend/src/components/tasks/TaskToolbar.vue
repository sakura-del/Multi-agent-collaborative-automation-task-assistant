<script setup lang="ts">
import { computed, ref } from "vue";
import {
  MessageSquarePlus,
  Pause,
  Play,
  RotateCcw,
  Square,
} from "lucide-vue-next";

import ConfirmBar from "@/components/common/ConfirmBar.vue";
import ErrorBanner from "@/components/common/ErrorBanner.vue";
import { Button } from "@/components/ui/button";
import { MotionDialog } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import type { SubtaskStatus, TaskControl, TaskStatus } from "@/api/types";

// 人工干预工具栏：只渲染当前状态允许的命令；取消需二次确认；补充输入用弹窗收集必填文本
const props = withDefaults(
  defineProps<{
    status: TaskStatus;
    pending?: boolean;
    selectedNodeId?: string | null;
    selectedNodeStatus?: SubtaskStatus | null;
  }>(),
  { pending: false, selectedNodeId: null, selectedNodeStatus: null }
);

const emit = defineEmits<{ command: [payload: TaskControl] }>();

const confirmingCancel = ref(false);
const supplyingInput = ref(false);
const supplyText = ref("");
const supplyError = ref<string | null>(null);

const TERMINAL = new Set<TaskStatus>(["completed", "failed", "cancelled"]);

const canPause = computed(() => props.status === "running" || props.status === "retrying");
const canResume = computed(() => props.status === "paused");
const canCancel = computed(() => !TERMINAL.has(props.status));
const canRetryNode = computed(
  () =>
    Boolean(props.selectedNodeId) &&
    (props.selectedNodeStatus === "failed" || props.selectedNodeStatus === "waiting_human")
);
const canSupplyInput = computed(
  () => Boolean(props.selectedNodeId) && props.status === "waiting_human"
);
const busy = computed(() => props.pending);

function send(payload: TaskControl) {
  emit("command", payload);
}

function confirmCancel() {
  confirmingCancel.value = false;
  send({ command: "cancel" });
}

function submitSupplyInput() {
  const text = supplyText.value.trim();
  if (!text) {
    supplyError.value = "请输入要补充的内容";
    return;
  }
  supplyError.value = null;
  send({
    command: "supply_input",
    node_id: props.selectedNodeId,
    input: { text },
  });
  supplyingInput.value = false;
  supplyText.value = "";
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-2" data-test="task-toolbar">
    <Button
      v-if="canPause"
      variant="outline"
      size="sm"
      data-test="control-pause"
      type="button"
      :disabled="busy"
      @click="send({ command: 'pause' })"
    >
      <Pause :size="15" aria-hidden="true" /> 暂停
    </Button>

    <Button
      v-if="canResume"
      variant="outline"
      size="sm"
      data-test="control-resume"
      type="button"
      :disabled="busy"
      @click="send({ command: 'resume' })"
    >
      <Play :size="15" aria-hidden="true" /> 恢复
    </Button>

    <Button
      v-if="canRetryNode"
      variant="outline"
      size="sm"
      data-test="control-retry-node"
      type="button"
      :disabled="busy"
      @click="send({ command: 'retry_node', node_id: selectedNodeId })"
    >
      <RotateCcw :size="15" aria-hidden="true" /> 重试节点
    </Button>

    <Button
      v-if="canSupplyInput"
      variant="outline"
      size="sm"
      data-test="control-supply-input"
      type="button"
      :disabled="busy"
      @click="supplyingInput = true"
    >
      <MessageSquarePlus :size="15" aria-hidden="true" /> 补充输入
    </Button>

    <Button
      v-if="canCancel && !confirmingCancel"
      variant="destructive"
      size="sm"
      data-test="control-cancel"
      type="button"
      :disabled="busy"
      @click="confirmingCancel = true"
    >
      <Square :size="15" aria-hidden="true" /> 终止任务
    </Button>

    <ConfirmBar
      v-if="canCancel && confirmingCancel"
      class="basis-full"
      title="确认终止当前任务？"
      consequence="终止后任务无法恢复，已消耗的检索与生成配额不会退还。"
      confirm-label="终止任务"
      cancel-label="取消"
      confirm-testid="control-cancel"
      cancel-testid="control-cancel-abort"
      :loading="busy"
      @confirm="confirmCancel"
      @cancel="confirmingCancel = false"
    />

    <MotionDialog
      v-model:open="supplyingInput"
      title="补充任务输入"
      aria-label="补充任务输入"
      initial-focus="#supply-input"
      :panel-attrs="{ 'data-test': 'supply-input-modal', class: 'p-4' }"
    >
      <div class="grid gap-1.5">
        <Label for="supply-input">补充内容（必填）</Label>
        <textarea
          id="supply-input"
          v-model="supplyText"
          data-test="supply-input-text"
          rows="3"
          placeholder="输入需要补充的背景、约束或文献线索（Ctrl/Cmd + Enter 提交）"
          class="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
          @keydown.ctrl.enter.prevent="submitSupplyInput"
          @keydown.meta.enter.prevent="submitSupplyInput"
        />
      </div>
      <ErrorBanner :message="supplyError" class="mt-2" @dismiss="supplyError = null" />
      <div class="mt-3 flex justify-end gap-1.5">
        <Button variant="ghost" size="sm" type="button" @click="supplyingInput = false">
          取消
        </Button>
        <Button
          data-test="supply-input-submit"
          size="sm"
          type="button"
          @click="submitSupplyInput"
        >
          提交
        </Button>
      </div>
    </MotionDialog>
  </div>
</template>
