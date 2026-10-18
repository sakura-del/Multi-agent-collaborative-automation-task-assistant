import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import TaskCreateForm from "@/components/tasks/TaskCreateForm.vue";

// 表单只负责校验与 payload 组装：提交动作由父组件（TaskListView）发起
function mountForm(props: Record<string, unknown> = {}) {
  return mount(TaskCreateForm, {
    props: { pending: false, error: null, documents: [], ...props },
  });
}

async function fillValidForm(wrapper: ReturnType<typeof mountForm>) {
  await wrapper.find('[data-test="topic"]').setValue("检索增强生成与多智能体协作的质量评估");
  await wrapper.find('[data-test="keyword-input"]').setValue("RAG");
  await wrapper.find('[data-test="keyword-input"]').trigger("keydown.enter");
  await wrapper.find('[data-test="keyword-input"]').setValue("multi-agent");
  await wrapper.find('[data-test="keyword-input"]').trigger("keydown.enter");
}

describe("TaskCreateForm", () => {
  it("rejects a topic shorter than 5 characters", async () => {
    const wrapper = mountForm();
    await wrapper.find('[data-test="topic"]').setValue("RAG");
    const keywordInput = wrapper.find('[data-test="keyword-input"]');
    await keywordInput.setValue("RAG");
    await keywordInput.trigger("keydown.enter");
    await wrapper.find("form").trigger("submit");

    expect(wrapper.emitted("submit")).toBeUndefined();
    expect(wrapper.find('[data-test="validation-error"]').text()).toContain("主题");
  });

  it("requires between 1 and 10 keywords", async () => {
    const wrapper = mountForm();
    await wrapper.find('[data-test="topic"]').setValue("检索增强生成系统综述");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("submit")).toBeUndefined();
    expect(wrapper.find('[data-test="validation-error"]').text()).toContain("关键词");

    await fillValidForm(wrapper);
    for (let index = 0; index < 10; index += 1) {
      const input = wrapper.find('[data-test="keyword-input"]');
      await input.setValue(`关键词${index}`);
      await input.trigger("keydown.enter");
    }
    await wrapper.find("form").trigger("submit");

    expect(wrapper.emitted("submit")).toBeUndefined();
    expect(wrapper.find('[data-test="validation-error"]').text()).toContain("最多 10");
  });

  it("rejects max documents outside 1..50", async () => {
    const wrapper = mountForm();
    await fillValidForm(wrapper);
    await wrapper.find('[data-test="max-documents"]').setValue("0");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("submit")).toBeUndefined();

    await wrapper.find('[data-test="max-documents"]').setValue("51");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("submit")).toBeUndefined();
    expect(wrapper.find('[data-test="validation-error"]').text()).toContain("文献数");
  });

  it("rejects a date range whose start is after the end", async () => {
    const wrapper = mountForm();
    await fillValidForm(wrapper);
    await wrapper.find('[data-test="date-from"]').setValue("2026-05-01");
    await wrapper.find('[data-test="date-to"]').setValue("2026-01-01");
    await wrapper.find("form").trigger("submit");

    expect(wrapper.emitted("submit")).toBeUndefined();
    expect(wrapper.find('[data-test="validation-error"]').text()).toContain("日期");
  });

  it("emits the exact TaskCreate payload once", async () => {
    const wrapper = mountForm({
      documents: [
        { id: "doc-1", title: "RAG 综述" },
        { id: "doc-2", title: "智能体评估" },
      ],
    });
    await fillValidForm(wrapper);
    await wrapper.find('[data-test="max-documents"]').setValue("15");
    await wrapper.find('[data-test="date-from"]').setValue("2024-01-01");
    await wrapper.find('[data-test="date-to"]').setValue("2025-12-31");
    await wrapper.find('[data-test="lang-en"]').trigger("click");
    await wrapper.find('[data-test="style-apa"]').trigger("click");
    await wrapper.find('[data-test="offline"]').setValue(true);
    await wrapper.find('[data-test="doc-doc-1"]').setValue(true);
    await wrapper.find("form").trigger("submit");

    const emitted = wrapper.emitted("submit");
    expect(emitted).toHaveLength(1);
    expect(emitted?.[0][0]).toEqual({
      topic: "检索增强生成与多智能体协作的质量评估",
      keywords: ["RAG", "multi-agent"],
      max_documents: 15,
      date_from: "2024-01-01",
      date_to: "2025-12-31",
      output_language: "en",
      citation_style: "apa",
      offline_dataset: true,
      document_ids: ["doc-1"],
    });
  });

  it("disables submit while pending", () => {
    const wrapper = mountForm({ pending: true });
    expect(wrapper.find('[data-test="submit"]').attributes("disabled")).toBeDefined();
  });

  it("shows the server error message near the form", () => {
    const wrapper = mountForm({ error: "后端不可用" });
    expect(wrapper.find('[data-test="form-error"]').text()).toContain("后端不可用");
  });
});
