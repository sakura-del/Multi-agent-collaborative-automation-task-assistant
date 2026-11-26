import { describe, expect, it } from "vitest";

import {
  collectFilesFromDataTransfer,
  defaultTitleFromFile,
  filterSupportedFiles,
  MAX_BATCH_FILES,
} from "./file-drop";

function makeFile(
  name: string,
  size = 1024,
  options: { lastModified?: number; relativePath?: string } = {},
): File {
  const file = new File(["x"], name, {
    type: "application/octet-stream",
    lastModified: options.lastModified ?? 1700000000000,
  });
  Object.defineProperty(file, "size", { value: size });
  if (options.relativePath) {
    Object.defineProperty(file, "webkitRelativePath", { value: options.relativePath });
  }
  return file;
}

describe("file-drop 工具", () => {
  it("filterSupportedFiles 静默忽略隐藏/临时文件，并记录不支持格式、超限、去重与数量截断", () => {
    const maxBytes = 30 * 1024 * 1024;
    const files = [
      makeFile(".DS_Store"),
      makeFile("Thumbs.db"),
      makeFile("desktop.ini"),
      makeFile(".hidden.pdf"),
      makeFile("~$draft.docx"),
      makeFile("image.png"),
      makeFile("huge.pdf", maxBytes + 1),
      makeFile("a.pdf", 2048, { relativePath: "sub/a.pdf" }),
      makeFile("a.pdf", 2048, { relativePath: "sub/a.pdf" }),
      makeFile("b.docx", 2048),
      makeFile("c.md", 512),
      makeFile("d.txt", 512),
    ];

    const result = filterSupportedFiles(
      files,
      [".pdf", ".docx", ".md", ".markdown", ".txt"],
      maxBytes,
      3,
    );

    expect(result.valid.map((f) => f.name)).toEqual(["a.pdf", "b.docx", "c.md"]);
    expect(result.skipped).toEqual([
      { name: "image.png", reason: "格式不支持" },
      { name: "huge.pdf", reason: "超过 30 MB 上限" },
      { name: "d.txt", reason: "单批最多 3 个文件" },
    ]);
    expect(MAX_BATCH_FILES).toBe(50);
  });

  it("collectFilesFromDataTransfer 支持递归解析嵌套文件夹条目", async () => {
    const file1 = makeFile("root.pdf");
    const file2 = makeFile("nested.docx");
    const file3 = makeFile("deep.md");

    const fakeFileEntry = (file: File) => ({
      isFile: true,
      isDirectory: false,
      name: file.name,
      file: (cb: (f: File) => void) => cb(file),
    });

    const deepDirEntry = {
      isFile: false,
      isDirectory: true,
      name: "deep",
      createReader: () => {
        let called = false;
        return {
          readEntries: (cb: (entries: unknown[]) => void) => {
            if (!called) {
              called = true;
              cb([fakeFileEntry(file3)]);
            } else {
              cb([]);
            }
          },
        };
      },
    };

    const rootDirEntry = {
      isFile: false,
      isDirectory: true,
      name: "papers",
      createReader: () => {
        let called = false;
        return {
          readEntries: (cb: (entries: unknown[]) => void) => {
            if (!called) {
              called = true;
              cb([fakeFileEntry(file2), deepDirEntry]);
            } else {
              cb([]);
            }
          },
        };
      },
    };

    const dt = {
      items: [
        { webkitGetAsEntry: () => fakeFileEntry(file1) },
        { webkitGetAsEntry: () => rootDirEntry },
      ],
      files: [],
    } as unknown as DataTransfer;

    const collected = await collectFilesFromDataTransfer(dt);
    expect(collected.map((f) => f.name)).toEqual(["root.pdf", "nested.docx", "deep.md"]);
  });

  it("collectFilesFromDataTransfer 在无 webkitGetAsEntry 时回退读取 dt.files", async () => {
    const f1 = makeFile("fallback.pdf");
    const dt = {
      items: [{ kind: "file" }],
      files: [f1],
    } as unknown as DataTransfer;

    const collected = await collectFilesFromDataTransfer(dt);
    expect(collected.map((f) => f.name)).toEqual(["fallback.pdf"]);
    expect(await collectFilesFromDataTransfer(null)).toEqual([]);
  });

  it("defaultTitleFromFile 去除扩展名并截断超长标题", () => {
    expect(defaultTitleFromFile(makeFile("  My Paper.pdf  "))).toBe("My Paper");
    const longBase = "a".repeat(300);
    expect(defaultTitleFromFile(makeFile(`${longBase}.pdf`)).length).toBe(256);
  });
});
