<script setup lang="ts">
import type { HTMLAttributes } from "vue";
import { computed } from "vue";
import {
  SwitchRoot,
  SwitchThumb,
  useForwardPropsEmits,
  type SwitchRootEmits,
  type SwitchRootProps,
} from "reka-ui";

import { cn } from "@/lib/utils";

/**
 * 开关（shadcn-vue 风格，底层 reka-ui `SwitchRoot`）。
 *
 * 用于「自动获取全文」这类**即时生效**的布尔项：与复选框的区别是它表达
 * 「开/关一种行为」，而不是「选中一个选项」。表单里其余布尔项仍是复选框，
 * 不要为了统一而批量替换。
 */
const props = defineProps<
  SwitchRootProps & {
    class?: HTMLAttributes["class"];
  }
>();

const emits = defineEmits<SwitchRootEmits>();

const delegatedProps = computed(() => {
  const { class: _class, ...delegated } = props;
  return delegated;
});

const forwarded = useForwardPropsEmits(delegatedProps, emits);
</script>

<template>
  <SwitchRoot
    v-bind="forwarded"
    :class="
      cn(
        'peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=unchecked]:bg-input',
        props.class
      )
    "
  >
    <SwitchThumb
      class="pointer-events-none block size-4 rounded-full bg-background shadow-lg ring-0 transition-transform data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0"
    />
  </SwitchRoot>
</template>
