/**
 * 本地题录文件读取（Issue #67 Step 1）。
 *
 * ## 为什么单独抽成 util
 *
 * 知网 / 维普 / 万方导出的 `.net` 文件在 Windows 上常是 **GBK/GB18030** 编码。
 * `new TextDecoder("utf-8")` 默认**不报错**，只把非法字节替换成 U+FFFD ——
 * 用户会以为文件损坏，而解析器只会报「无法识别格式」。
 *
 * 正确顺序：UTF-8 严格解码（`fatal: true`）失败后再回退 GB18030。
 * 抽成纯函数便于单测（Node / happy-dom 都支持 `TextDecoder("gb18030")`）。
 */

/** 单个导入文件上限：题录文件通常几十 KB，超过 5 MB 基本都是误选。 */
export const MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024;

/**
 * 解码题录文件字节：UTF-8 严格优先，失败回退 GB18030。
 *
 * @throws 当两种编码都无法解码时抛出（调用方给可读提示）。
 */
export function decodeReferenceFile(buffer: ArrayBuffer): string {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    return new TextDecoder("gb18030").decode(buffer);
  }
}
