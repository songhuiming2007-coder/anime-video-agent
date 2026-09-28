// N38：变异 harness 的判定。跑器没跑出有效结果（构建失败 / 零用例）不许判 KILLED。
// 端到端的一面（真把构建弄坏、真 grep 不中）由 `node scripts/verify-mutations.mjs --self-test` 实跑。
import { describe, expect, it } from "vitest";
import { classify, parseE2eReport, parseVitestReport } from "../../scripts/verify-mutations.mjs";

const spec = (title: string, ok: boolean) => ({ title, ok });

describe("N38 parseE2eReport", () => {
  it("global-setup 构建失败（顶层 errors、零 spec）→ build 问题，不进红条名单", () => {
    const r = parseE2eReport({ suites: [], errors: [{ message: "Error: Command failed: electron-vite build" }] }, "：e2e/session.spec.ts -g TX-15b");
    expect(r.failed).toEqual([]);
    expect(r.problems.map((p) => p.kind)).toEqual(["build"]);
  });
  it("grep 选不中（实测：Playwright 报顶层「No tests found」）→ empty 问题", () => {
    const r = parseE2eReport({ suites: [], errors: [{ message: "Error: No tests found.\nMake sure that arguments are regular expressions matching test files." }] }, "：e2e/session.spec.ts -g N38-NO-SUCH-CASE");
    expect(r.failed).toEqual([]);
    expect(r.problems.map((p) => p.kind)).toEqual(["empty"]);
  });
  it("零 spec 且无 errors → empty 问题", () => {
    const r = parseE2eReport({ suites: [{ suites: [] }], errors: [] }, "：e2e/session.spec.ts -g TX-15b");
    expect(r.failed).toEqual([]);
    expect(r.problems.map((p) => p.kind)).toEqual(["empty"]);
  });
  it("正常跑：红条照记、无问题", () => {
    const r = parseE2eReport({ suites: [{ specs: [spec("TX-15b 重连", false), spec("TX-15 其他", true)] }] });
    expect(r).toEqual({ failed: ["TX-15b 重连"], problems: [] });
  });
});

describe("N38 parseVitestReport", () => {
  it("文件级失败且零断言（导入的源码编译炸了）→ build 问题", () => {
    const r = parseVitestReport({ testResults: [{ name: "tests/host/settle.test.ts", status: "failed", message: "Transform failed", assertionResults: [] }] });
    expect(r.failed).toEqual([]);
    expect(r.problems.map((p) => p.kind)).toEqual(["build"]);
  });
  it("零用例 → empty 问题", () => {
    expect(parseVitestReport({ testResults: [] }).problems.map((p) => p.kind)).toEqual(["empty"]);
  });
  it("断言失败照记为红条", () => {
    const r = parseVitestReport({ testResults: [{ name: "x", status: "failed", assertionResults: [{ status: "failed", fullName: "TH-1 懒启动" }, { status: "passed", fullName: "TH-2" }] }] });
    expect(r).toEqual({ failed: ["TH-1 懒启动"], problems: [] });
  });
});

describe("N38 classify", () => {
  it("有 build 问题 → BUILD_FAIL，即便红条里命中了期望编号", () => {
    expect(classify(["TX-15b"], ["TX-15b 重连"], [{ kind: "build", detail: "e2e 全局错误" }]).verdict).toBe("BUILD_FAIL");
  });
  it("只有 empty 问题 → NO_TESTS", () => {
    expect(classify(["TX-15b"], [], [{ kind: "empty", detail: "e2e 未选中任何用例：e2e/session.spec.ts -g TX-15b" }]).verdict).toBe("NO_TESTS");
  });
  it("无问题时四类判定不变（编号带边界匹配）", () => {
    expect(classify(["TH-1"], ["TH-1 a"]).verdict).toBe("KILLED");
    expect(classify(["TH-1"], ["TH-10 a"]).verdict).toBe("OTHER");
    expect(classify(["TH-1", "TX-9"], ["TH-1 a"]).verdict).toBe("PARTIAL");
    expect(classify(["TH-1"], []).verdict).toBe("SURVIVED");
  });
});
