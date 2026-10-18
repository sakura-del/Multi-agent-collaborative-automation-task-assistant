<script setup lang="ts">
import { computed, ref } from "vue";
import { Pause, Play, RotateCcw, Square, MessageSquarePlus } from "lucide-vue-next";

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
  <div class="toolbar" data-test="task-toolbar">
    <button
      v-if="canPause"
      class="btn"
      data-test="control-pause"
      type="button"
      :disabled="busy"
      @click="send({ command: 'pause' })"
    >
      <Pause :size="15" /> 暂停
    </button>

    <button
      v-if="canResume"
      class="btn"
      data-test="control-resume"
      type="button"
      :disabled="busy"
      @click="send({ command: 'resume' })"
    >
      <Play :size="15" /> 恢复
    </button>

    <button
      v-if="canRetryNode"
      class="btn"
      data-test="control-retry-node"
      type="button"
      :disabled="busy"
      @click="send({ command: 'retry_node', node_id: selectedNodeId })"
    >
      <RotateCcw :size="15" /> 重试节点
    </button>

    <button
      v-if="canSupplyInput"
      class="btn"
      data-test="control-supply-input"
      type="button"
      :disabled="busy"
      @click="supplyingInput = true"
    >
      <MessageSquarePlus :size="15" /> 补充输入
    </button>

    <button
      v-if="canCancel"
      class="btn btn--danger"
      data-test="control-cancel"
      type="button"
      :disabled="busy"
      @click="requestCancel"
    >
      <Square :size="15" /> {{ confirmingCancel ? "确认取消？" : "取消" }}
    </button>
    <button
      v-if="confirmingCancel"
      class="btn"
      data-test="control-cancel-abort"
      type="button"
      @click="confirmingCancel = false"
    >
      放弃
    </button>

    <div v-if="supplyingInput" class="toolbar__modal" data-test="supply-input-modal">
      <label for="supply-input">补充内容（必填）</label>
      <textarea id="supply-input" v-model="supplyText" data-test="supply-input-text" rows="3" />
      <p v-if="supplyError" class="banner banner--error">{{ supplyError }}</p>
      <div class="toolbar__modal-actions">
        <button class="btn" type="button" @click="supplyingInput = false">取消</button>
        <button class="btn btn--primary" data-test="supply-input-submit" type="button" @click="submitSupplyInput">
          提交
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.btn--danger {
  color: var(--color-error);
  border-color: #fecaca;
}

.toolbar__modal {
  flex-basis: 100%;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.75rem;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-card);
}

.toolbar__modal label {
  font-size: 0.85rem;
}

.toolbar__modal textarea {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 0.5rem;
  font-family: inherit;
  font-size: 0.9rem;
}

.toolbar__modal-actions {
  display: flex;
  gap: 0.5rem;
  justify-content: flex-end;
}
</style>
