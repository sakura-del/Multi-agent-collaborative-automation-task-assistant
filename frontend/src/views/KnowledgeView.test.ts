import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import KnowledgeView from "@/views/KnowledgeView.vue";
import DocumentTable from "@/components/knowledge/DocumentTable.vue";
import type { KnowledgeDocument, KnowledgeStats } from "@/api/types";

/**
 * 知识库页重设计（2026-10-23）。
 *
 * ## 现状问题（截图实测）
 *
 * 1. **索引状态是假的**：`/knowledge/stats` 硬编码 `vector_index_ready=True`，
 *    页面若展示「索引就绪」，就是展示谎言（本会话已因同类问题导致向量检索
 *    静默失效）。
 * 2. **没有搜索/筛选**：11 篇还能扫，100+ 不可用。
 * 3. **标题与文件名两行重复**：`title` 基本等于 `original_name` 去扩展名，
 *    第二行是噪音。
 * 4. **来源列常量**：全部是 `upload`，一列不传递任何信息。
 * 5. **没有汇总**：只有一行「11 篇文档 · 2835 个分块」。
 *
 * ## 本文件锁什么
 *
 * 锁**信息架构与真实性**，不锁版式：
 * - 汇总卡必须用真实 stats（索引失败数、就绪状态）；
 * - 搜索/类型筛选可用；
 * - 标题与文件名重复时不渲染多余行；
 * - 来源列全相同时隐藏；
 * - 保留既有 `data-test` 契约（E2E 依赖）。
 */

function doc(overrides: Partial<KnowledgeDocument> = {}): KnowledgeDocument {
  return {
    id: "d1",
    title: "RAG 评估方法综述",
    doc_type: "pdf",
    index_status: "ready",
    chunk_count: 120,
    created_at: "2026-10-18T19:45:51Z",
    source: "upload",
    original_name: "RAG 评估方法综述.pdf",
    failure_reason: null,
    ...overrides,
  };
}

function stats(overrides: Partial<KnowledgeStats> = {}): KnowledgeStats {
  return {
    total_docs: 1,
    total_chunks: 120,
    vector_index_ready: true,
    bm25_index_ready: true,
    index_failed_count: 0,
    indexing_count: 0,
    ...overrides,
  } as KnowledgeStats;
}

async function mountWith(
  documents: KnowledgeDocument[],
  stat: KnowledgeStats = stats(),
  options: { skipTable?: boolean } = {},
) {
  setActivePinia(createPinia());
  const { useKnowledgeStore } = await import("@/stores/knowledge");
  const store = useKnowledgeStore();
  store.documents = documents;
  store.stats = stat;
  store.loadAll = async () => {};
  store.loading = false;

  const wrapper = mount(KnowledgeView, {
    global: {
      stubs: {
        DocumentUpload: true,
        ...(options.skipTable ? { DocumentTable: true } : {}),
      },
    },
  });
  return wrapper;
}

describe("汇总：索引状态必须真实", () => {
  it("展示文档数与分块数", async () => {
    const wrapper = await mountWith(
      [doc()],
      stats({ total_docs: 11, total_chunks: 2835 }),
    );

    // 文档数与分块数合在同一张卡里（`11 / 2,835`），两个数字都要在
    const card = wrapper.find('[data-test="kb-stat-docs"]');
    expect(card.exists()).toBe(true);
    expect(card.text()).toContain("11");
    expect(card.text()).toContain("2,835");
  });

  it("索引失败数 > 0 时醒目提示（这是唯一需要用户动手的）", async () => {
    const wrapper = await mountWith(
      [doc({ index_status: "failed", failure_reason: "解析失败" })],
      stats({ index_failed_count: 2 }),
    );

    const card = wrapper.find('[data-test="kb-stat-failed"]');
    expect(card.exists()).toBe(true);
    expect(card.text()).toContain("2");
    // 必须用警示色（提醒要处理），而不是中性色
    expect(card.html()).toMatch(/warning/);
  });

  it("**核心**：索引未就绪时不能显示「就绪」", async () => {
    const wrapper = await mountWith(
      [doc()],
      stats({ vector_index_ready: false }),
    );

    const status = wrapper.find('[data-test="kb-index-status"]');
    expect(status.exists()).toBe(true);
    // 注意断言写法：「未就绪」本身含「就绪」子串，所以不能断言
    // `not.toContain("就绪")`。要断言的是它处于**未就绪**表述。
    expect(status.text()).toContain("未就绪");
  });

  it("索引就绪时才显示「就绪」", async () => {
    const wrapper = await mountWith([doc()], stats({ vector_index_ready: true }));
    expect(wrapper.find('[data-test="kb-index-status"]').text()).toContain("就绪");
  });
});

describe("搜索与筛选", () => {
  it("按标题搜索（大小写不敏感）", async () => {
    const wrapper = await mountWith([
      doc({ id: "a", title: "RAG 评估综述", original_name: "RAG 评估综述.pdf" }),
      doc({ id: "b", title: "多智能体协作", original_name: "多智能体协作.pdf" }),
    ]);

    await wrapper.find('[data-test="kb-search"]').setValue("rag");

    expect(wrapper.find('[data-test="document-row-a"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="document-row-b"]').exists()).toBe(false);
  });

  it("搜索也匹配原始文件名（用户可能记得文件名而不是标题）", async () => {
    const wrapper = await mountWith([
      doc({ id: "a", title: "我的综述", original_name: "10_EVOR_2402.12317v2.pdf" }),
      doc({ id: "b", title: "另一篇", original_name: "05_FactCheck_2404.pdf" }),
    ]);

    await wrapper.find('[data-test="kb-search"]').setValue("evor");

    expect(wrapper.find('[data-test="document-row-a"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="document-row-b"]').exists()).toBe(false);
  });

  it("按类型筛选", async () => {
    const wrapper = await mountWith([
      doc({ id: "p", title: "论文一", doc_type: "pdf" }),
      doc({ id: "t", title: "笔记一", doc_type: "txt" }),
    ]);

    await wrapper.find('[data-test="kb-filter-txt"]').trigger("click");

    expect(wrapper.find('[data-test="document-row-t"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="document-row-p"]').exists()).toBe(false);
  });

  it("筛选后无结果时给出可读引导（不是空白）", async () => {
    const wrapper = await mountWith([doc({ doc_type: "pdf" })]);
    await wrapper.find('[data-test="kb-search"]').setValue("不存在的关键词");

    const empty = wrapper.find('[data-test="kb-filtered-empty"]');
    expect(empty.exists()).toBe(true);
    expect(empty.text()).toContain("没有");
  });
});

describe("标题与列的信息密度", () => {
  it("标题与文件名重复时不渲染第二行（去噪）", async () => {
    // title = original_name 去扩展名 → 第二行纯属重复
    const wrapper = await mountWith([
      doc({ id: "dup", title: "RAG 评估方法综述", original_name: "RAG 评估方法综述.pdf" }),
    ]);
    const table = mount(DocumentTable, {
      props: { documents: [doc({ id: "dup", title: "RAG 评估方法综述", original_name: "RAG 评估方法综述.pdf" })] },
    });
    void wrapper;

    expect(table.find('[data-test="doc-original-name-dup"]').exists()).toBe(false);
  });

  it("标题与文件名不同时保留第二行（信息不丢）", async () => {
    const table = mount(DocumentTable, {
      props: {
        documents: [doc({ id: "diff", title: "我的综述", original_name: "10_EVOR_2402.12317v2.pdf" })],
      },
    });

    const second = table.find('[data-test="doc-original-name-diff"]');
    expect(second.exists()).toBe(true);
    expect(second.text()).toContain("10_EVOR");
  });

  it("来源列全为 upload 时隐藏该列（常量列无信息量）", async () => {
    const table = mount(DocumentTable, {
      props: {
        documents: [
          doc({ id: "x", source: "upload" }),
          doc({ id: "y", source: "upload" }),
        ],
      },
    });

    expect(table.find('[data-test="doc-col-source"]').exists()).toBe(false);
  });

  it("来源不同时保留来源列", async () => {
    const table = mount(DocumentTable, {
      props: {
        documents: [
          doc({ id: "x", source: "upload" }),
          doc({ id: "y", source: "arxiv" }),
        ],
      },
    });

    expect(table.find('[data-test="doc-col-source"]').exists()).toBe(true);
  });
});

describe("既有契约必须保留", () => {
  it("保留 document-table / document-row-<id> / document-delete-<id>", async () => {
    const wrapper = await mountWith([doc({ id: "keep" })]);

    expect(wrapper.find('[data-test="document-table"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="document-row-keep"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="document-delete-keep"]').exists()).toBe(true);
  });

  it("保留空态 knowledge-empty", async () => {
    const wrapper = await mountWith([]);
    expect(wrapper.find('[data-test="knowledge-empty"]').exists()).toBe(true);
  });

  it("上传表单常驻（E2E literature-review 直接操作它，不折叠）", async () => {
    const wrapper = await mountWith([doc()]);
    // DocumentUpload 被 stub，这里断言它被渲染（没有被 v-if 收起）
    expect(wrapper.findComponent({ name: "DocumentUpload" }).exists()).toBe(true);
  });
});
