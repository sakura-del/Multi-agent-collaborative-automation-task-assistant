import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { toast as sonnerToast } from "vue-sonner";

import App from "@/App.vue";
import { useTheme } from "@/composables/useTheme";
import { toastError, toastInfo, toastSuccess, toastWarning } from "./toast";

describe("lib/toast 与 App Toaster 挂载", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.documentElement.classList.remove("dark");
    localStorage.clear();
  });

  it("toastSuccess / toastError / toastInfo / toastWarning 使用规范默认时长", () => {
    const successSpy = vi.spyOn(sonnerToast, "success").mockReturnValue("s1");
    const errorSpy = vi.spyOn(sonnerToast, "error").mockReturnValue("e1");
    const infoSpy = vi.spyOn(sonnerToast, "info").mockReturnValue("i1");
    const warningSpy = vi.spyOn(sonnerToast, "warning").mockReturnValue("w1");

    toastSuccess("已保存");
    expect(successSpy).toHaveBeenCalledWith(
      "已保存",
      expect.objectContaining({ duration: 2800 }),
    );

    toastError("保存失败");
    expect(errorSpy).toHaveBeenCalledWith(
      "保存失败",
      expect.objectContaining({ duration: 4500 }),
    );

    toastInfo("已刷新");
    expect(infoSpy).toHaveBeenCalledWith(
      "已刷新",
      expect.objectContaining({ duration: 3000 }),
    );

    toastWarning("额度接近上限");
    expect(warningSpy).toHaveBeenCalledWith(
      "额度接近上限",
      expect.objectContaining({ duration: 3800 }),
    );
  });

  it("带撤销 action 时自动延长至少 5000ms，显式传入 duration 时优先尊重", () => {
    const successSpy = vi.spyOn(sonnerToast, "success").mockReturnValue("s2");

    toastSuccess("已移除成员", {
      action: { label: "撤销", onClick: () => undefined },
    });
    expect(successSpy).toHaveBeenLastCalledWith(
      "已移除成员",
      expect.objectContaining({ duration: 5000 }),
    );

    toastSuccess("自定义时长", { duration: 1500 });
    expect(successSpy).toHaveBeenLastCalledWith(
      "自定义时长",
      expect.objectContaining({ duration: 1500 }),
    );
  });

  it("App.vue 挂载 Toaster 并随 useTheme 切换明暗主题", async () => {
    const { setTheme } = useTheme();
    setTheme("light");

    const wrapper = mount(App, {
      global: {
        stubs: { RouterView: true },
      },
    });
    await flushPromises();

    const toaster = wrapper.find("[data-sonner-toaster]");
    expect(toaster.exists()).toBe(true);
    expect(toaster.attributes("data-sonner-theme")).toBe("light");

    setTheme("dark");
    await flushPromises();
    expect(toaster.attributes("data-sonner-theme")).toBe("dark");

    wrapper.unmount();
  });
});
