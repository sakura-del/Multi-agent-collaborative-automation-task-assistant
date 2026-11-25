import { AxiosError, AxiosHeaders, type AxiosResponse } from "axios";
import { describe, expect, it } from "vitest";

import { normalizeError } from "./client";

function makeAxiosError(
  status: number,
  data: unknown,
  code?: string,
  message = `Request failed with status code ${status}`,
): AxiosError {
  const headers = new AxiosHeaders();
  const response: AxiosResponse = {
    data,
    status,
    statusText: "Error",
    headers,
    config: { headers },
  };
  return new AxiosError(message, code, { headers }, undefined, response);
}

describe("api/client normalizeError 错误归一化", () => {
  it("优先提取后端统一 data.message 并保留 error_code 与 details", () => {
    const err = makeAxiosError(403, {
      error_code: "EMAIL_NOT_VERIFIED",
      message: "请先完成邮箱验证",
      details: { email: "u@example.com" },
      detail: "ignored detail",
    });

    const normalized = normalizeError(err);
    expect(normalized.message).toBe("请先完成邮箱验证");
    expect(normalized.code).toBe("EMAIL_NOT_VERIFIED");
    expect(normalized.details).toEqual({ email: "u@example.com" });
  });

  it("data.message 缺失或为空字符串时回退提取字符串 data.detail", () => {
    const err1 = makeAxiosError(404, { detail: "任务不存在" });
    expect(normalizeError(err1).message).toBe("任务不存在");

    const err2 = makeAxiosError(400, { message: "   ", detail: "参数有误" });
    expect(normalizeError(err2).message).toBe("参数有误");
  });

  it("解析 FastAPI 422 data.detail 数组提取首条可读中文提示", () => {
    const err = makeAxiosError(422, {
      detail: [
        {
          loc: ["body", "topic"],
          msg: "Field required",
          type: "missing",
        },
      ],
    });

    const normalized = normalizeError(err);
    expect(normalized.message).not.toContain("Request failed with status code 422");
    expect(normalized.message).toContain("topic");
    expect(normalized.message).toContain("必填");
  });

  it("解析带 Value error 前缀或嵌套对象格式的 data.detail", () => {
    const errValidation = makeAxiosError(422, {
      detail: [
        {
          loc: ["body", "date_to"],
          msg: "Value error, 结束年份不能早于开始年份",
          type: "value_error",
        },
      ],
    });
    const msg = normalizeError(errValidation).message;
    expect(msg).not.toContain("Value error,");
    expect(msg).toContain("date_to");
    expect(msg).toContain("结束年份不能早于开始年份");

    const errObj = makeAxiosError(400, { detail: { message: "模型凭据已失效" } });
    expect(normalizeError(errObj).message).toBe("模型凭据已失效");
  });

  it("请求超时 ECONNABORTED 返回可读中文提示，非 AxiosError 保底返回", () => {
    const timeoutErr = new AxiosError("timeout of 15000ms exceeded", "ECONNABORTED");
    expect(normalizeError(timeoutErr).message).toBe("请求超时，请稍后重试");

    const plainErr = new Error("普通错误");
    expect(normalizeError(plainErr)).toBe(plainErr);
    expect(normalizeError("字符串异常").message).toBe("字符串异常");
  });

  it("502 HTML、空 422 数组、状态码兜底绝不透出 Request failed with status code", () => {
    const html502 = makeAxiosError(
      502,
      "<!DOCTYPE html><html><head><title>502 Bad Gateway</title></head><body>502</body></html>",
    );
    const msg502 = normalizeError(html502).message;
    expect(msg502).not.toMatch(/Request failed with status code/i);
    expect(msg502).toContain("服务暂时不可用");

    const empty422 = makeAxiosError(422, { detail: [] });
    const msg422 = normalizeError(empty422).message;
    expect(msg422).not.toMatch(/Request failed with status code/i);
    expect(msg422).toContain("校验");

    const empty401 = makeAxiosError(401, {});
    expect(normalizeError(empty401).message).toContain("登录状态已失效");

    const empty403 = makeAxiosError(403, null);
    expect(normalizeError(empty403).message).toContain("没有权限");

    const other418 = makeAxiosError(418, {});
    expect(normalizeError(other418).message).toBe("请求失败（HTTP 418）");
  });

  it("支持 3 层嵌套 detail 对象提取，超限则回退可读中文状态映射", () => {
    const deep3 = makeAxiosError(400, {
      detail: {
        detail: {
          message: "deep",
        },
      },
    });
    expect(normalizeError(deep3).message).toBe("deep");

    const tooDeep = makeAxiosError(400, {
      detail: {
        detail: {
          detail: {
            detail: {
              message: "level5",
            },
          },
        },
      },
    });
    const tooDeepMsg = normalizeError(tooDeep).message;
    expect(tooDeepMsg).not.toMatch(/Request failed with status code/i);
    expect(tooDeepMsg).toBe("请求参数有误");
  });
});
