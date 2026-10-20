<script setup lang="ts">
import { computed, ref } from "vue";
import {
  CircleAlert,
  MessageSquarePlus,
  Pause,
  Play,
  RotateCcw,
  Square,
} from "lucide-vue-next";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
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

function requestCancel() {
  if (!confirmingCancel.value) {
    confirmingCancel.value = true;
    return;
  }
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
      v-if="canCancel"
      variant="destructive"
      size="sm"
      data-test="control-cancel"
      type="button"
      :disabled="busy"
      @click="requestCancel"
    >
      <Square :size="15" aria-hidden="true" /> {{ confirmingCancel ? "确认取消？" : "取消" }}
    </Button>
    <Button
      v-if="confirmingCancel"
      variant="ghost"
      size="sm"
      data-test="control-cancel-abort"
      type="button"
      @click="confirmingCancel = false"
    >
      放弃
    </Button>

    <div
      v-if="supplyingInput"
      class="basis-full rounded-lg border border-border bg-card p-3"
      data-test="supply-input-modal"
    >
      <div class="grid gap-1.5">
        <Label for="supply-input">补充内容（必填）</Label>
        <textarea
          id="supply-input"
          v-model="supplyText"
          data-test="supply-input-text"
          rows="3"
          class="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
        />
      </div>
      <Alert v-if="supplyError" variant="destructive" class="mt-2">
        <CircleAlert />
        <span>{{ supplyError }}</span>
      </Alert>
      <div class="mt-2 flex justify-end gap-1.5">
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
    </div>
  </div>
</template>
