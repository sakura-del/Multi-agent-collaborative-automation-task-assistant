import { describe, expect, it } from "vitest";

import { decodeReferenceFile } from "@/utils/reference-file";

/** 把字节数组转成 `ArrayBuffer`（`TextDecoder` 的输入要求）。 */
function bufferOf(bytes: number[]): ArrayBuffer {
  return Uint8Array.from(bytes).buffer;
}

describe("decodeReferenceFile", () => {
  it("按 UTF-8 解码（UTF-8 优先）", () => {
    const bytes = new TextEncoder().encode("万方题录：检索增强生成综述");

    expect(decodeReferenceFile(bytes.buffer)).toBe("万方题录：检索增强生成综述");
  });

  it("UTF-8 解码失败时回退 GB18030（知网/维普导出常见 GBK）", () => {
    // "测试" 的 GBK/GB18030 字节；按 UTF-8 严格解码必然失败。
    const bytes = bufferOf([0xb2, 0xe2, 0xca, 0xd4]);

    expect(decodeReferenceFile(bytes)).toBe("测试");
  });

  it("不会把 GBK 字节解出替换字符（`�`）", () => {
    // 若实现只做宽松 UTF-8 解码，这里会得到 "��"。
    const bytes = bufferOf([0xca, 0xd4, 0xb2, 0xe2]);

    const text = decodeReferenceFile(bytes);

    expect(text).not.toContain("\uFFFD");
    expect(text).toBe("试测");
  });
});
