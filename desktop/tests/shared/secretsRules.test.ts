// TV-8 / TV-9：密钥规则（Spec 10 §2.9）。
import { describe, expect, it } from "vitest";
import { keyFromSecurityStdout, validKeyEnvName } from "../../src/shared/secretsRules";

describe("TV-8 keyFromSecurityStdout", () => {
  it("去掉恰好一个末尾换行；空行、空白、非 ASCII、空串一律 null", () => {
    expect(keyFromSecurityStdout("sk-abc\n")).toBe("sk-abc");
    expect(keyFromSecurityStdout("sk-abc")).toBe("sk-abc");
    expect(keyFromSecurityStdout("sk-abc\n\n")).toBeNull();
    expect(keyFromSecurityStdout("a b\n")).toBeNull();
    expect(keyFromSecurityStdout("秘密\n")).toBeNull();
    expect(keyFromSecurityStdout("")).toBeNull();
  });

  it("E5 的十六进制输出按「已知行为」返回该串本身（§2.9 第 4 条的盲区，防将来有人加猜测式解码）", () => {
    expect(keyFromSecurityStdout("736b2d74657374\n")).toBe("736b2d74657374");
  });
});

describe("TV-9 validKeyEnvName", () => {
  it("两个现有配置的名字与一般形状通过；会改变子进程行为的名字拒绝", () => {
    for (const ok of ["CPA_API_KEY", "OPENAI_API_KEY", "X_TOKEN", "FOO_KEY"]) expect(validKeyEnvName(ok), ok).toBe(true);
    for (const bad of ["PATH", "PYTHONPATH", "DYLD_INSERT_LIBRARIES", "NODE_OPTIONS", "api_key", "A-KEY", "_KEY", "KEY", "A_API_KEY "]) {
      expect(validKeyEnvName(bad), bad).toBe(false);
    }
  });
});
