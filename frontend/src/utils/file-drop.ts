/**
 * 批量/文件夹/拖拽文件收集与过滤工具（纯前端，零外部依赖）。
 *
 * 兼容：
 * - 拖拽文件夹：优先通过 `DataTransferItem.webkitGetAsEntry()` 递归遍历；
 * - 单元测试 / 无 webkitGetAsEntry 环境：平滑回退读取 `dataTransfer.files`。
 */

export const MAX_BATCH_FILES = 50;

const IGNORED_NAMES = new Set([".ds_store", "thumbs.db", "desktop.ini"]);

interface FileSystemEntryLike {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
}

interface FileSystemFileEntryLike extends FileSystemEntryLike {
  file(success: (file: File) => void, error?: (err: unknown) => void): void;
}

interface FileSystemDirectoryReaderLike {
  readEntries(
    success: (entries: FileSystemEntryLike[]) => void,
    error?: (err: unknown) => void,
  ): void;
}

interface FileSystemDirectoryEntryLike extends FileSystemEntryLike {
  createReader(): FileSystemDirectoryReaderLike;
}

type WebkitEntryItem = {
  webkitGetAsEntry?: () => FileSystemEntryLike | null;
};

export interface SkippedFile {
  name: string;
  reason: string;
}

export interface FilteredFilesResult {
  valid: File[];
  skipped: SkippedFile[];
}

function extensionOf(name: string): string {
  const index = name.lastIndexOf(".");
  return index >= 0 ? name.slice(index).toLowerCase() : "";
}

function isHiddenOrTemp(name: string): boolean {
  const lower = name.toLowerCase();
  return name.startsWith(".") || name.startsWith("~$") || IGNORED_NAMES.has(lower);
}

/** 从 FileSystemEntry 递归收集全部 File（限制最大深度防止异常符号链接环）。 */
async function readEntryFiles(entry: FileSystemEntryLike, depth = 0): Promise<File[]> {
  if (depth > 8) return [];
  if (entry.isFile) {
    return new Promise<File[]>((resolve) => {
      (entry as FileSystemFileEntryLike).file(
        (file) => resolve([file]),
        () => resolve([]),
      );
    });
  }
  if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntryLike).createReader();
    const allEntries: FileSystemEntryLike[] = [];
    // FileSystemDirectoryReader.readEntries 每次最多返回 100 条，需循环读取直到空数组
    while (true) {
      const batch = await new Promise<FileSystemEntryLike[]>((resolve) => {
        reader.readEntries(
          (entries) => resolve(entries),
          () => resolve([]),
        );
      });
      if (!batch.length) break;
      allEntries.push(...batch);
    }
    const nested = await Promise.all(allEntries.map((child) => readEntryFiles(child, depth + 1)));
    return nested.flat();
  }
  return [];
}

/** 解析拖拽事件的 DataTransfer，同时支持多文件与嵌套文件夹。 */
export async function collectFilesFromDataTransfer(dt: DataTransfer | null): Promise<File[]> {
  if (!dt) return [];
  const items = Array.from(dt.items ?? []) as unknown as WebkitEntryItem[];
  const entries = items
    .map((item) =>
      typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() : null,
    )
    .filter((entry): entry is FileSystemEntryLike => entry !== null);

  if (entries.length > 0) {
    const groups = await Promise.all(entries.map((entry) => readEntryFiles(entry)));
    return groups.flat();
  }
  return Array.from(dt.files ?? []);
}

/** 过滤不支持的扩展名、系统隐藏文件、超限文件，并去重与截断上限。 */
export function filterSupportedFiles(
  files: File[],
  acceptedExtensions: readonly string[],
  maxBytes: number,
  maxCount: number = MAX_BATCH_FILES,
): FilteredFilesResult {
  const valid: File[] = [];
  const skipped: SkippedFile[] = [];
  const seen = new Set<string>();

  for (const file of files) {
    if (isHiddenOrTemp(file.name)) {
      continue;
    }
    const dedupKey = `${file.webkitRelativePath || file.name}::${file.size}::${file.lastModified}`;
    if (seen.has(dedupKey)) {
      continue;
    }
    seen.add(dedupKey);

    if (!acceptedExtensions.includes(extensionOf(file.name))) {
      skipped.push({ name: file.name, reason: "格式不支持" });
      continue;
    }
    if (file.size > maxBytes) {
      skipped.push({ name: file.name, reason: "超过 30 MB 上限" });
      continue;
    }
    if (valid.length >= maxCount) {
      skipped.push({ name: file.name, reason: `单批最多 ${maxCount} 个文件` });
      continue;
    }
    valid.push(file);
  }
  return { valid, skipped };
}

/** 文件名去扩展名作为默认文档标题（最长 256 字符）。 */
export function defaultTitleFromFile(file: File): string {
  return file.name.replace(/\.[^.]+$/, "").trim().slice(0, 256) || file.name.slice(0, 256);
}
