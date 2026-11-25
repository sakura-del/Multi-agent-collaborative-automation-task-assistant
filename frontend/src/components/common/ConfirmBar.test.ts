import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, ref } from "vue";

import { MotionDialog } from "@/components/ui/dialog";
import { MotionDrawer } from "@/components/ui/drawer";
import ConfirmBar from "./ConfirmBar.vue";

describe("ConfirmBar 破坏性二次确认条原语", () => {
  const mountedWrappers: Array<{ unmount: () => void }> = [];

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    while (mountedWrappers.length > 0) {
      mountedWrappers.pop()?.unmount();
    }
    document.body.style.overflow = "";
    document.body.innerHTML = "";
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("默认 open=true 兼容外层 v-if，渲染标题、后果说明与 Button 锚点", () => {
    const wrapper = mount(ConfirmBar, {
      props: {
        title: "确认删除该抽取表？",
        consequence: "删除后将无法恢复已抽取的数据列与结果。",
        confirmLabel: "确认删除",
        cancelLabel: "取消",
        confirmTestid: "extract-del-confirm",
        cancelTestid: "extract-del-cancel",
      },
    });
    mountedWrappers.push(wrapper);

    expect(wrapper.text()).toContain("确认删除该抽取表？");
    expect(wrapper.text()).toContain("删除后将无法恢复已抽取的数据列与结果。");

    const confirmBtn = wrapper.get('[data-test="extract-del-confirm"]');
    const cancelBtn = wrapper.get('[data-test="extract-del-cancel"]');
    expect(confirmBtn.text()).toContain("确认删除");
    expect(confirmBtn.classes().join(" ")).toContain("bg-destructive");
    expect(cancelBtn.text()).toContain("取消");
  });

  it("点击确认触发 @confirm，点击取消或按 Esc 触发 @cancel", async () => {
    const wrapper = mount(ConfirmBar, {
      props: {
        title: "确认终止当前任务？",
        confirmLabel: "终止任务",
        confirmTestid: "control-cancel",
        cancelTestid: "control-cancel-abort",
      },
    });
    mountedWrappers.push(wrapper);

    await wrapper.get('[data-test="control-cancel-abort"]').trigger("click");
    expect(wrapper.emitted("cancel")).toHaveLength(1);

    const escEvent = new KeyboardEvent("keydown", {
      key: "Escape",
      bubbles: true,
      cancelable: true,
    });
    const stopSpy = vi.spyOn(escEvent, "stopPropagation");
    window.dispatchEvent(escEvent);
    expect(wrapper.emitted("cancel")).toHaveLength(2);
    expect(stopSpy).toHaveBeenCalled();

    await wrapper.get('[data-test="control-cancel"]').trigger("click");
    expect(wrapper.emitted("confirm")).toHaveLength(1);
  });

  it("loading=true 时禁用按钮、展示降级加载图标且屏蔽点击与 Esc", async () => {
    const wrapper = mount(ConfirmBar, {
      props: {
        title: "确认吊销密钥？",
        loading: true,
        confirmTestid: "revoke-confirm",
        cancelTestid: "revoke-cancel",
      },
    });
    mountedWrappers.push(wrapper);

    const confirmBtn = wrapper.get('[data-test="revoke-confirm"]');
    const cancelBtn = wrapper.get('[data-test="revoke-cancel"]');
    expect(confirmBtn.attributes("disabled")).toBeDefined();
    expect(cancelBtn.attributes("disabled")).toBeDefined();
    expect(wrapper.find(".animate-spin.motion-reduce\\:animate-none").exists()).toBe(true);

    await confirmBtn.trigger("click");
    await cancelBtn.trigger("click");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(wrapper.emitted("confirm")).toBeUndefined();
    expect(wrapper.emitted("cancel")).toBeUndefined();
  });

  it("防连点锁: 同一同步窗口内连续点击确认只触发一次，结束后恢复可点击", async () => {
    const wrapper = mount(ConfirmBar, {
      props: {
        title: "确认移除成员？",
        confirmTestid: "member-confirm",
      },
    });
    mountedWrappers.push(wrapper);

    const confirmBtn = wrapper.get('[data-test="member-confirm"]');
    await confirmBtn.trigger("click");
    await confirmBtn.trigger("click");
    expect(wrapper.emitted("confirm")).toHaveLength(1);

    // 未传 loading 时短防抖后解锁；传 loading=true→false 后也可解锁
    await vi.advanceTimersByTimeAsync(200);
    await confirmBtn.trigger("click");
    expect(wrapper.emitted("confirm")).toHaveLength(2);
  });

  it("嵌套在已打开的 MotionDialog 内按 Esc 仅取消 ConfirmBar，不关闭外层 Dialog", async () => {
    const Host = defineComponent({
      components: { MotionDialog, ConfirmBar },
      setup() {
        const dialogOpen = ref(true);
        const confirmOpen = ref(false);
        const dialogCloseCount = ref(0);
        const barCancelCount = ref(0);
        return { dialogOpen, confirmOpen, dialogCloseCount, barCancelCount };
      },
      template: `
        <MotionDialog
          v-model:open="dialogOpen"
          aria-label="测试弹窗"
          data-test="nested-dialog"
          @close="dialogCloseCount += 1"
        >
          <button type="button" data-test="trigger-del" @click="confirmOpen = true">删除</button>
          <ConfirmBar
            v-if="confirmOpen"
            title="确认删除？"
            cancel-testid="nested-bar-cancel"
            @cancel="barCancelCount += 1; confirmOpen = false"
          />
        </MotionDialog>
      `,
    });

    const wrapper = mount(Host);
    mountedWrappers.push(wrapper);
    await flushPromises();

    // 先弹出 Dialog，再由内部按钮挂载 ConfirmBar（晚于 Dialog 注册监听）
    const triggerDel = document.body.querySelector('[data-test="trigger-del"]') as HTMLButtonElement;
    triggerDel.click();
    await flushPromises();

    expect(document.body.querySelector('[data-test="nested-bar-cancel"]')).not.toBeNull();

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    await flushPromises();

    expect(wrapper.vm.barCancelCount).toBe(1);
    expect(wrapper.vm.dialogCloseCount).toBe(0);
    expect(wrapper.vm.dialogOpen).toBe(true);
    expect(document.body.querySelector('[data-test="nested-dialog"]')).not.toBeNull();
  });

  it("嵌套在已打开的 MotionDrawer 内按 Esc 仅取消 ConfirmBar，不关闭外层 Drawer", async () => {
    const Host = defineComponent({
      components: { MotionDrawer, ConfirmBar },
      setup() {
        const drawerOpen = ref(true);
        const confirmOpen = ref(false);
        const drawerCloseCount = ref(0);
        const barCancelCount = ref(0);
        return { drawerOpen, confirmOpen, drawerCloseCount, barCancelCount };
      },
      template: `
        <MotionDrawer
          v-model:open="drawerOpen"
          aria-label="测试抽屉"
          :panel-attrs="{ 'data-test': 'nested-drawer' }"
          @close="drawerCloseCount += 1"
        >
          <button type="button" data-test="drawer-trigger-del" @click="confirmOpen = true">删除批注</button>
          <ConfirmBar
            :open="confirmOpen"
            title="确认删除批注？"
            cancel-testid="drawer-bar-cancel"
            @cancel="barCancelCount += 1; confirmOpen = false"
          />
        </MotionDrawer>
      `,
    });

    const wrapper = mount(Host);
    mountedWrappers.push(wrapper);
    await flushPromises();

    const triggerDel = document.body.querySelector('[data-test="drawer-trigger-del"]') as HTMLButtonElement;
    triggerDel.click();
    await flushPromises();

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    await flushPromises();

    expect(wrapper.vm.barCancelCount).toBe(1);
    expect(wrapper.vm.drawerCloseCount).toBe(0);
    expect(wrapper.vm.drawerOpen).toBe(true);
    expect(document.body.querySelector('[data-test="nested-drawer"]')).not.toBeNull();
  });

  it("同页多个 ConfirmBar 同时 open=true 时按 Esc 仅栈顶 ConfirmBar 响应取消", async () => {
    const barFirst = mount(ConfirmBar, {
      props: { title: "底层确认条", open: true },
    });
    mountedWrappers.push(barFirst);
    const barSecond = mount(ConfirmBar, {
      props: { title: "顶层确认条", open: true },
    });
    mountedWrappers.push(barSecond);
    await flushPromises();

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    await flushPromises();

    expect(barSecond.emitted("cancel")).toHaveLength(1);
    expect(barFirst.emitted("cancel")).toBeUndefined();
  });

  it("嵌套在 MotionDialog 内且 loading=true 时按 Escape 既不取消 ConfirmBar 也不击穿关闭外层 Dialog", async () => {
    const Host = defineComponent({
      components: { MotionDialog, ConfirmBar },
      setup() {
        const dialogOpen = ref(true);
        const dialogCloseCount = ref(0);
        const barCancelCount = ref(0);
        return { dialogOpen, dialogCloseCount, barCancelCount };
      },
      template: `
        <MotionDialog
          v-model:open="dialogOpen"
          aria-label="加载锁测试弹窗"
          data-test="busy-loading-dialog"
          @close="dialogCloseCount += 1"
        >
          <ConfirmBar
            :open="true"
            :loading="true"
            title="正在删除，请稍候"
            cancel-testid="busy-loading-cancel"
            @cancel="barCancelCount += 1"
          />
        </MotionDialog>
      `,
    });

    const wrapper = mount(Host);
    mountedWrappers.push(wrapper);
    await flushPromises();

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    await flushPromises();

    expect(wrapper.vm.barCancelCount).toBe(0);
    expect(wrapper.vm.dialogCloseCount).toBe(0);
    expect(wrapper.vm.dialogOpen).toBe(true);
    expect(document.body.querySelector('[data-test="busy-loading-dialog"]')).not.toBeNull();
  });

  it("嵌套在 MotionDialog 内点击确认进入 150ms clickLocked 锁期内按 Escape 不击穿外层 Dialog", async () => {
    const Host = defineComponent({
      components: { MotionDialog, ConfirmBar },
      setup() {
        const dialogOpen = ref(true);
        const dialogCloseCount = ref(0);
        const barConfirmCount = ref(0);
        const barCancelCount = ref(0);
        return { dialogOpen, dialogCloseCount, barConfirmCount, barCancelCount };
      },
      template: `
        <MotionDialog
          v-model:open="dialogOpen"
          aria-label="点击锁测试弹窗"
          data-test="busy-lock-dialog"
          @close="dialogCloseCount += 1"
        >
          <ConfirmBar
            :open="true"
            title="确认执行高危操作？"
            confirm-testid="busy-lock-confirm"
            cancel-testid="busy-lock-cancel"
            @confirm="barConfirmCount += 1"
            @cancel="barCancelCount += 1"
          />
        </MotionDialog>
      `,
    });

    const wrapper = mount(Host);
    mountedWrappers.push(wrapper);
    await flushPromises();

    const confirmBtn = document.body.querySelector('[data-test="busy-lock-confirm"]') as HTMLButtonElement;
    expect(confirmBtn).not.toBeNull();
    confirmBtn.click();
    await flushPromises();
    expect(wrapper.vm.barConfirmCount).toBe(1);

    // 150ms clickLocked 锁期内按 Esc：ConfirmBar 不取消，也绝不击穿关闭外层 Dialog
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    await flushPromises();

    expect(wrapper.vm.barCancelCount).toBe(0);
    expect(wrapper.vm.dialogCloseCount).toBe(0);
    expect(wrapper.vm.dialogOpen).toBe(true);

    // 锁期结束后按 Esc 恢复正常取消（仍不关外层 Dialog）
    await vi.advanceTimersByTimeAsync(200);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    await flushPromises();
    expect(wrapper.vm.barCancelCount).toBe(1);
    expect(wrapper.vm.dialogCloseCount).toBe(0);
  });

  it("含 <input> 的 ConfirmBar 嵌套在 MotionDialog 内且 IME isComposing=true 按 Escape 两者均不关闭", async () => {
    const Host = defineComponent({
      components: { MotionDialog, ConfirmBar },
      setup() {
        const dialogOpen = ref(true);
        const confirmOpen = ref(true);
        const dialogCloseCount = ref(0);
        const barCancelCount = ref(0);
        return { dialogOpen, confirmOpen, dialogCloseCount, barCancelCount };
      },
      template: `
        <MotionDialog
          v-model:open="dialogOpen"
          aria-label="IME 嵌套弹窗"
          data-test="ime-nested-dialog"
          @close="dialogCloseCount += 1"
        >
          <ConfirmBar
            :open="confirmOpen"
            title="请输入删除原因"
            @cancel="barCancelCount += 1; confirmOpen = false"
          >
            <input type="text" data-test="confirm-ime-input" />
          </ConfirmBar>
        </MotionDialog>
      `,
    });

    const hostEl = document.createElement("div");
    document.body.appendChild(hostEl);
    const wrapper = mount(Host, { attachTo: hostEl });
    mountedWrappers.push(wrapper);
    await flushPromises();

    const input = document.body.querySelector('[data-test="confirm-ime-input"]') as HTMLInputElement;
    expect(input).not.toBeNull();
    input.focus();
    expect(document.activeElement).toBe(input);

    // 1) IME 组合态按 Esc：barCancelCount=0 且 dialogCloseCount=0
    window.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Escape",
        isComposing: true,
        bubbles: true,
        cancelable: true,
      }),
    );
    await flushPromises();

    expect(wrapper.vm.barCancelCount).toBe(0);
    expect(wrapper.vm.dialogCloseCount).toBe(0);
    expect(wrapper.vm.confirmOpen).toBe(true);
    expect(wrapper.vm.dialogOpen).toBe(true);

    // 2) IME 结束后按 Esc：仅取消 ConfirmBar，仍不关外层 Dialog
    window.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Escape",
        isComposing: false,
        bubbles: true,
        cancelable: true,
      }),
    );
    await flushPromises();

    expect(wrapper.vm.barCancelCount).toBe(1);
    expect(wrapper.vm.dialogCloseCount).toBe(0);
    expect(wrapper.vm.confirmOpen).toBe(false);
    expect(wrapper.vm.dialogOpen).toBe(true);
  });
});
