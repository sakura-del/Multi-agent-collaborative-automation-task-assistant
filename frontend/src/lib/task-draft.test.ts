import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  TASK_DRAFT_TTL_MS,
  TASK_DRAFT_VERSION,
  clearTaskDraft,
  draftFingerprint,
  fromCloudDraftPayload,
  hasTaskDraft,
  isDraftEmpty,
  loadTaskDraft,
  saveTaskDraft,
  taskDraftStorageKey,
  toCloudDraftPayload,
  type TaskCreateDraftValues,
} from "@/lib/task-draft";

// 草稿能力是「用户 m00465：导入完文献回来草稿没了」的修复底座：
// 只锁语义（按用户隔离 / 过期与脏数据丢弃 / 白名单收敛），不锁实现细节。
function draftValues(
  overrides: Partial<TaskCreateDraftValues> = {},
): TaskCreateDraftValues {
  return {
    workflowPreset: "literature_review",
    topic: "检索增强生成与多智能体协作的质量评估",
    draftText: "",
    seedDois: [],
    seedDraft: "",
    snowballDirection: "both",
    keywords: ["RAG", "multi-agent"],
    keywordDraft: "",
    maxDocuments: 15,
    dateFrom: "2024-01-01",
    dateTo: "2025-12-31",
    language: "zh",
    citationStyle: "gbt7714",
    offlineDataset: false,
    useReferenceLibrary: true,
    verifyReferences: false,
    modelSource: "platform",
    selectedCredential: "",
    selectedDocuments: ["doc-1"],
    literatureSources: [],
    ...overrides,
  };
}

describe("task-draft 草稿存取", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("保存后按用户命名空间读回全部字段", () => {
    saveTaskDraft("u-1", draftValues());

    expect(localStorage.getItem(taskDraftStorageKey("u-1"))).toBeTruthy();
    expect(taskDraftStorageKey("u-1")).toBe("vp:task-create-draft:u-1");
    expect(taskDraftStorageKey(null)).toBe("vp:task-create-draft:anonymous");

    const draft = loadTaskDraft("u-1");
    expect(draft).not.toBeNull();
    expect(draft!.version).toBe(TASK_DRAFT_VERSION);
    expect(draft!.values).toMatchObject({
      topic: "检索增强生成与多智能体协作的质量评估",
      keywords: ["RAG", "multi-agent"],
      maxDocuments: 15,
      useReferenceLibrary: true,
      selectedDocuments: ["doc-1"],
    });
    expect(Number.isFinite(Date.parse(draft!.savedAt))).toBe(true);
  });

  it("不同用户的草稿互不覆盖，也不会读串", () => {
    saveTaskDraft("u-1", draftValues({ topic: "用户一的主题草稿" }));
    saveTaskDraft("u-2", draftValues({ topic: "用户二的主题草稿" }));

    expect(loadTaskDraft("u-1")!.values.topic).toBe("用户一的主题草稿");
    expect(loadTaskDraft("u-2")!.values.topic).toBe("用户二的主题草稿");
  });

  it("未登录用户落到 anonymous，与登录用户互不影响", () => {
    saveTaskDraft(null, draftValues({ topic: "匿名用户的草稿主题" }));

    expect(loadTaskDraft(undefined)!.values.topic).toBe("匿名用户的草稿主题");
    expect(loadTaskDraft("u-1")).toBeNull();
  });

  it("超过 7 天的草稿不再恢复，并顺手清掉存储", () => {
    const key = taskDraftStorageKey("u-1");
    localStorage.setItem(
      key,
      JSON.stringify({
        version: TASK_DRAFT_VERSION,
        savedAt: new Date(Date.now() - TASK_DRAFT_TTL_MS - 60_000).toISOString(),
        values: draftValues(),
      }),
    );

    expect(loadTaskDraft("u-1")).toBeNull();
    expect(localStorage.getItem(key)).toBeNull();
  });

  it("损坏的 JSON / 版本不符的草稿直接丢弃（不渲染半截数据）", () => {
    const key = taskDraftStorageKey("u-1");

    localStorage.setItem(key, "{not-json");
    expect(loadTaskDraft("u-1")).toBeNull();
    expect(localStorage.getItem(key)).toBeNull();

    localStorage.setItem(
      key,
      JSON.stringify({ version: TASK_DRAFT_VERSION + 1, savedAt: new Date().toISOString(), values: {} }),
    );
    expect(loadTaskDraft("u-1")).toBeNull();
    expect(localStorage.getItem(key)).toBeNull();
  });

  it("白名单收敛：未知字段与错误类型都不会进入草稿", () => {
    const dirty = {
      ...draftValues(),
      topic: 12345,
      keywords: ["RAG", 42, null],
      extraUnknown: "不该被持久化",
    };
    // 模拟 localStorage 被外部写入脏值：类型上故意绕过，运行时靠白名单收敛
    saveTaskDraft("u-1", dirty as unknown as TaskCreateDraftValues);

    const draft = loadTaskDraft("u-1")!;
    expect(draft.values.topic).toBe("");
    expect(draft.values.keywords).toEqual(["RAG"]);
    expect(JSON.parse(localStorage.getItem(taskDraftStorageKey("u-1"))!)).not.toHaveProperty(
      "values.extraUnknown",
    );
  });

  it("hasTaskDraft 反映可恢复性；清除后为 false", () => {
    expect(hasTaskDraft("u-1")).toBe(false);

    saveTaskDraft("u-1", draftValues());
    expect(hasTaskDraft("u-1")).toBe(true);

    clearTaskDraft("u-1");
    expect(hasTaskDraft("u-1")).toBe(false);
    expect(loadTaskDraft("u-1")).toBeNull();
  });

  it("localStorage 抛异常时静默失败，不把异常抛给建任务流程", () => {
    const setItem = vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => saveTaskDraft("u-1", draftValues())).not.toThrow();
    setItem.mockRestore();

    const getItem = vi.spyOn(localStorage, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(() => loadTaskDraft("u-1")).not.toThrow();
    expect(loadTaskDraft("u-1")).toBeNull();
    getItem.mockRestore();
  });

  it("保存/读取云端同步元数据 syncedRevision 与 dirty，并向后兼容旧 localStorage", () => {
    saveTaskDraft("u-meta", draftValues(), { syncedRevision: 4, dirty: false });
    const loaded = loadTaskDraft("u-meta")!;
    expect(loaded.syncedRevision).toBe(4);
    expect(loaded.dirty).toBe(false);

    // 模拟旧格式 localStorage（不含 syncedRevision / dirty）
    localStorage.setItem(
      taskDraftStorageKey("u-legacy"),
      JSON.stringify({
        version: TASK_DRAFT_VERSION,
        savedAt: new Date().toISOString(),
        values: draftValues(),
      }),
    );
    const legacy = loadTaskDraft("u-legacy")!;
    expect(legacy.syncedRevision).toBe(0);
    expect(legacy.dirty).toBe(true);
  });

  it("toCloudDraftPayload / fromCloudDraftPayload 往返转换与内容指纹、空草稿判定", () => {
    const base = draftValues();
    const cloud = toCloudDraftPayload(base);
    expect(cloud.topic).toBe(base.topic);
    expect(cloud.keywords).toEqual(base.keywords);
    expect(cloud.document_ids).toEqual(["doc-1"]);
    expect(cloud.output_language).toBe("zh");

    const restored = fromCloudDraftPayload(cloud as Record<string, unknown>);
    expect(restored.topic).toBe(base.topic);
    expect(restored.keywords).toEqual(base.keywords);
    expect(restored.selectedDocuments).toEqual(["doc-1"]);

    expect(draftFingerprint(base)).toBe(draftFingerprint({ ...base }));
    expect(isDraftEmpty(base)).toBe(false);

    const empty = draftValues({
      topic: "   ",
      keywords: [],
      keywordDraft: "",
      draftText: "",
      seedDois: [],
      seedDraft: "",
      selectedDocuments: [],
    });
    expect(isDraftEmpty(empty)).toBe(true);
  });
});
