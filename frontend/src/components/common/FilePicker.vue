<script setup lang="ts">
import type { HTMLAttributes } from "vue";
import { computed, ref } from "vue";
import { Paperclip } from "lucide-vue-next";

import { cn } from "@/lib/utils";

/**
 * 文件选择器（项目通用件）。
 *
 * ## 为什么需要它
 * 原生 `<input type="file">` 的按钮文案（"Choose File" / "No file chosen"）由
 * **浏览器决定，无法用 CSS 或 i18n 修改**，中文界面里会混进英文单词。
 *
 * ## 做法（为什么不是"包装组件接管 input"）
 * 保留**原生 input 元素本身**，只把它铺满容器、设为透明（仍可点击/聚焦），
 * 下面叠一层自定义外观。这样：
 * - 父组件的 `data-test`、`@change` 等 attrs 仍落在**同一个 input** 上
 *   （`inheritAttrs: false` + `v-bind="$attrs"`），测试与 E2E 的
 *   `setInputFiles` / 直接设置 `files` + `trigger("change")` 全部照常工作；
 * - 无障碍不变：它仍是真正的 file input，键盘可聚焦、屏幕阅读器可识别。
 *
 * 若改成"组件内部持有 hidden input + 自定义按钮"，父组件的 attrs 会落到组件根节点
 * 而非 input 上，`@change` 只能靠事件冒泡侥幸生效、`data-test` 也定位错元素——
 * 那是更脆弱的方案，故不采用。
 */
const props = withDefaults(
  defineProps<{
    /** 未选择时的占位文案 */
    placeholder?: string;
    class?: HTMLAttributes["class"];
  }>(),
  { placeholder: "未选择文件", class: undefined }
);

// attrs（含 data-test / accept / @change / disabled）必须透传到 input，故禁用自动继承
defineOptions({ inheritAttrs: false });

const fileName = ref("");
const input = ref<HTMLInputElement | null>(null);

/** 仅用于展示选中文件名；父组件的 @change 会照常触发（同一元素上可挂多个监听器） */
function trackName(event: Event) {
  const target = event.target as HTMLInputElement;
  fileName.value = target.files?.[0]?.name ?? "";
}

const displayName = computed(() => fileName.value || props.placeholder);

// `ref` 不会透传给 input（组件 ref 拿到的是实例），故显式暴露
defineExpose({
  /** 清空选择与展示（上传成功后调用，避免再选同一文件不触发 change） */
  reset() {
    fileName.value = "";
    if (input.value) {
      input.value.value = "";
    }
  },
});
</script>

<template>
  <div :class="cn('relative inline-flex min-w-56 items-center gap-2', props.class)">
    <!-- 原生 input：铺满容器并透明，点击行为完全原生；attrs 全部落在这里 -->
    <input
      ref="input"
      v-bind="$attrs"
      type="file"
      class="absolute inset-0 z-10 cursor-pointer opacity-0 disabled:cursor-not-allowed"
      @change="trackName"
    />

    <!-- 外观层（不接收指针事件，避免挡住 input 的点击） -->
    <span
      class="pointer-events-none inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-background px-3 text-xs font-medium"
    >
      <Paperclip :size="14" aria-hidden="true" />
      <span>选择文件</span>
    </span>
    <span
      class="pointer-events-none min-w-0 max-w-52 truncate text-xs"
      :class="fileName ? 'text-foreground' : 'text-muted-foreground'"
      :title="fileName || undefined"
    >
      {{ displayName }}
    </span>
  </div>
</template>
