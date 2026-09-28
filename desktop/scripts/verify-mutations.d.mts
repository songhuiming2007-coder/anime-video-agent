// 给 tests/harness/verifyMutations.test.ts 的类型声明（harness 本体是纯 .mjs，直接用 node 跑）
export type Problem = { kind: "build" | "empty"; detail: string };
export type Parsed = { failed: string[]; problems: Problem[] };
export type Verdict = "KILLED" | "PARTIAL" | "OTHER" | "SURVIVED" | "BUILD_FAIL" | "NO_TESTS";
export function parseVitestReport(rep: unknown): Parsed;
export function parseE2eReport(rep: unknown, label?: string): Parsed;
export function classify(expect: string[], failed: string[], problems?: Problem[]): { hit: string[]; verdict: Verdict };
