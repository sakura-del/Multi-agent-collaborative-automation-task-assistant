// 任务创建表单草稿：跨路由跳转 / 页面刷新 / 表单收起后保留用户已填写内容。
//
// ## 为什么需要（2026-11-26 用户反馈）
//
// 新用户引用库为空时，表单提示「前往导入文献 →」。跳过去导入完再回来，
// 建任务页已经重新挂载（TaskCreateForm 放在 `AnimatedCollapse` 里且没有
// keepMounted，路由离开或收起都会卸载它），之前填的主题 / 关键词全没了，
// 只能从头再填一次。这是设计缺陷，不是用户操作问题。
//
// ## 设计取舍
//
// - **按用户命名空间**：key 带 userId，同一浏览器换账号不会串草稿；
//   未登录/取不到用户时落到 `anonymous`，不影响功能。
// - **带 version + 7 天 TTL**：结构升级时旧草稿直接丢弃（而不是渲染出
//   半截数据），放了很久没动的草稿不再突然弹出来打断用户。
// - **只存表单值**：不存接口结果、不存服务端 id 推导出的状态。
// - **所有读写 try/catch**：隐私模式 / 存储配额满时不能让「保存草稿」
//   这种附属能力把建任务主流程拖垮。
export const TASK_DRAFT_VERSION = 1;
export const TASK_DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;

/** 落盘的表单值（与 TaskCreateForm 的状态一一对应，全部可选容忍缺失）。 */
export interface TaskCreateDraftValues {
  workflowPreset: string;
  topic: string;
  draftText: string;
  seedDois: string[];
  seedDraft: string;
  snowballDirection: string;
  keywords: string[];
  keywordDraft: string;
  maxDocuments: number | string;
  dateFrom: string;
  dateTo: string;
  language: string;
  citationStyle: string;
  offlineDataset: boolean;
  useReferenceLibrary: boolean;
  verifyReferences: boolean;
  modelSource: string;
  selectedCredential: string;
  selectedDocuments: string[];
  /** 检索文献源白名单（Issue #67 万方 BYOK 芯片），空数组 = 自动 */
  literatureSources: string[];
}

export interface TaskCreateDraft {
  version: number;
  savedAt: string;
  values: TaskCreateDraftValues;
}

/** 草稿 key：按用户隔离；空 userId 归到 anonymous（单测 / 未登录场景）。 */
export function taskDraftStorageKey(userId?: string | null): string {
  const suffix = (userId ?? "").trim() || "anonymous";
  return `vp:task-create-draft:${suffix}`;
}

function readRaw(key: string): string | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeRaw(key: string, value: string): void {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(key, value);
    }
  } catch {
    /* 隐私模式 / 配额满：草稿能力失败不影响建任务 */
  }
}

function removeRaw(key: string): void {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem(key);
    }
  } catch {
    /* 同上 */
  }
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function bool(value: unknown): boolean {
  return value === true;
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

/**
 * 只认白名单字段并做类型收敛：localStorage 是用户可改的外部输入，
 * 脏数据不应该让表单渲染异常，更不该影响提交。
 */
function sanitizeValues(raw: unknown): TaskCreateDraftValues {
  const source = (raw ?? {}) as Record<string, unknown>;
  const maxDocuments =
    typeof source.maxDocuments === "number" || typeof source.maxDocuments === "string"
      ? source.maxDocuments
      : "";
  return {
    workflowPreset: text(source.workflowPreset),
    topic: text(source.topic),
    draftText: text(source.draftText),
    seedDois: stringList(source.seedDois),
    seedDraft: text(source.seedDraft),
    snowballDirection: text(source.snowballDirection),
    keywords: stringList(source.keywords),
    keywordDraft: text(source.keywordDraft),
    maxDocuments,
    dateFrom: text(source.dateFrom),
    dateTo: text(source.dateTo),
    language: text(source.language),
    citationStyle: text(source.citationStyle),
    offlineDataset: bool(source.offlineDataset),
    useReferenceLibrary: bool(source.useReferenceLibrary),
    verifyReferences: bool(source.verifyReferences),
    modelSource: text(source.modelSource),
    selectedCredential: text(source.selectedCredential),
    selectedDocuments: stringList(source.selectedDocuments),
    literatureSources: stringList(source.literatureSources),
  };
}

/** 读取草稿；格式不对、过期或解析失败都返回 null 并顺手清掉脏 key。 */
export function loadTaskDraft(userId?: string | null): TaskCreateDraft | null {
  const key = taskDraftStorageKey(userId);
  const raw = readRaw(key);
  if (!raw) return null;

  let parsed: Partial<TaskCreateDraft> | null = null;
  try {
    parsed = JSON.parse(raw) as Partial<TaskCreateDraft> | null;
  } catch {
    removeRaw(key);
    return null;
  }
  if (
    !parsed ||
    parsed.version !== TASK_DRAFT_VERSION ||
    typeof parsed.values !== "object" ||
    parsed.values === null
  ) {
    removeRaw(key);
    return null;
  }
  const savedAt = typeof parsed.savedAt === "string" ? Date.parse(parsed.savedAt) : Number.NaN;
  if (!Number.isFinite(savedAt) || Date.now() - savedAt > TASK_DRAFT_TTL_MS) {
    removeRaw(key);
    return null;
  }
  return {
    version: TASK_DRAFT_VERSION,
    savedAt: new Date(savedAt).toISOString(),
    values: sanitizeValues(parsed.values),
  };
}

/** 写入草稿（值会被白名单收敛后再落盘）。 */
export function saveTaskDraft(
  userId: string | null | undefined,
  values: TaskCreateDraftValues,
): void {
  const payload: TaskCreateDraft = {
    version: TASK_DRAFT_VERSION,
    savedAt: new Date().toISOString(),
    values: sanitizeValues(values),
  };
  writeRaw(taskDraftStorageKey(userId), JSON.stringify(payload));
}

/** 删除草稿（创建成功、主动放弃时调用）。 */
export function clearTaskDraft(userId?: string | null): void {
  removeRaw(taskDraftStorageKey(userId));
}

/** 是否存在可恢复的草稿（过期草稿会被 loadTaskDraft 顺手清掉）。 */
export function hasTaskDraft(userId?: string | null): boolean {
  return loadTaskDraft(userId) !== null;
}
