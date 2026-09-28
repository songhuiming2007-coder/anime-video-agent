#!/usr/bin/env node
// 桌面端变异验证 harness（Spec 10 §7.2 MUT 矩阵；与 scripts/verify_mutations.py 同一纪律）。
//
//   node scripts/verify-mutations.mjs                 # 全部
//   node scripts/verify-mutations.mjs --only MUT-3 MUT-50
//   node scripts/verify-mutations.mjs --list
//   node scripts/verify-mutations.mjs --out /tmp/x.json --dirty-ok
//   node scripts/verify-mutations.mjs --self-test       # harness 自测：人造的坏构建 / 零用例变异都不许判 KILLED
//
// 纪律（Python 版的三次事故教训照搬）：
// - 恢复 = 从内存写回开跑前读到的原文（不是 git checkout），并逐字节断言复原；
// - 锚点必须逐字命中且在该文件内唯一，否则报错、不静默跳过；
// - 默认拒绝在脏树上开跑（--dirty-ok 放行，结果标注不可复现）；
// - 每条只跑矩阵指定的目标（vitest 文件 / e2e 用例），并核对「红的是不是指定的那几条」：
//   KILLED = 指定杀手全红；PARTIAL = 部分红；OTHER = 指定的没红、别的红了；SURVIVED = 全绿。
//   只有 KILLED 算验过——被别的断言顺带杀死，证明不了指定的那条测试有效。
// - 跑器没跑出有效结果（N38）：构建失败 / 无报告 / 文件级失败 → BUILD_FAIL；选中零条用例 → NO_TESTS。
//   这些是「问题」不是「红条」，优先于上面四类，一律不计杀死。旧版把它们塞进红条名单，
//   而占位串里带着 grep（多半就是期望编号），于是构建坏掉的变异报了假 KILLED（N34 的 MUT-62′ 首版）。
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { MUTATIONS } from "./mutations.mjs";

const DESKTOP = resolve(import.meta.dirname, "..");
const BIN = join(DESKTOP, "node_modules/.bin");

function parseArgs(argv) {
  const out = { only: null, list: false, out: join(tmpdir(), "desktop_mutation_results.json"), dirtyOk: false, selfTest: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--list") out.list = true;
    else if (a === "--self-test") out.selfTest = true;
    else if (a === "--dirty-ok") out.dirtyOk = true;
    else if (a === "--out") out.out = argv[++i];
    else if (a === "--only") {
      out.only = [];
      while (i + 1 < argv.length && !argv[i + 1].startsWith("--")) out.only.push(argv[++i]);
    } else throw new Error(`未知参数 ${a}`);
  }
  return out;
}

function dirtyFiles() {
  const s = execFileSync("git", ["status", "--porcelain", "--", "."], { cwd: DESKTOP, encoding: "utf-8" });
  return s.split("\n").filter(Boolean);
}

/**
 * vitest JSON 报告 → 失败用例的完整标题（failed）+ 跑器层面的问题（problems）。
 * problems 的 kind：build = 文件级失败且一条断言都没跑（导入/编译炸了）；empty = 一条用例都没有。
 */
export function parseVitestReport(rep) {
  const failed = [];
  const problems = [];
  let total = 0;
  for (const f of rep.testResults ?? []) {
    const asserts = f.assertionResults ?? [];
    total += asserts.length;
    for (const a of asserts) if (a.status === "failed") failed.push(a.fullName ?? a.title); // fullName 含 describe 前缀：编号常写在 describe 上
    if (asserts.length === 0 && f.status === "failed") problems.push({ kind: "build", detail: `文件失败：${f.name}：${String(f.message ?? "").slice(0, 200)}` });
  }
  if (total === 0 && problems.length === 0) problems.push({ kind: "empty", detail: "vitest 未跑任何用例" });
  return { failed, problems };
}

function runVitest(files) {
  const dir = mkdtempSync(join(tmpdir(), "mut-vitest-"));
  const outFile = join(dir, "r.json");
  const r = spawnSync(join(BIN, "vitest"), ["run", ...files, "--reporter=json", `--outputFile=${outFile}`], { cwd: DESKTOP, encoding: "utf-8", timeout: 15 * 60_000 });
  if (!existsSync(outFile)) return { failed: [], problems: [{ kind: "build", detail: `vitest 无报告：exit=${r.status} ${String(r.stderr).slice(-300)}` }] };
  return parseVitestReport(JSON.parse(readFileSync(outFile, "utf-8")));
}

/**
 * Playwright JSON 报告 → ok=false 的 spec 标题（failed）+ 跑器层面的问题（problems）。
 * 顶层 errors = global-setup（构建）或加载失败 → build，其中「No tests found」→ empty；一条 spec 都没有 → empty。
 */
export function parseE2eReport(rep, label = "") {
  const failed = [];
  const problems = [];
  let total = 0;
  const walk = (suite) => {
    for (const sp of suite.specs ?? []) {
      total += 1;
      if (!sp.ok) failed.push(sp.title);
    }
    for (const s of suite.suites ?? []) walk(s);
  };
  for (const s of rep.suites ?? []) walk(s);
  for (const e of rep.errors ?? []) {
    const msg = String(e.message ?? e.value ?? JSON.stringify(e));
    // grep 选不中时 Playwright 也报顶层错误（「No tests found」），归 empty 而不是 build
    const kind = /^Error: No tests found/.test(msg) ? "empty" : "build";
    problems.push({ kind, detail: `e2e ${kind === "empty" ? "未选中任何用例" : "全局错误"}${label}：${msg.slice(0, 300)}` });
  }
  if (total === 0 && problems.length === 0) problems.push({ kind: "empty", detail: `e2e 未选中任何用例${label}` });
  return { failed, problems };
}

function runE2e(file, grep) {
  // JSON 报告写文件：global-setup 的构建日志走 stdout，会把 --reporter=json 的输出搅乱
  const outFile = join(mkdtempSync(join(tmpdir(), "mut-e2e-")), "r.json");
  const r = spawnSync(join(BIN, "playwright"), ["test", file, "-g", grep, "--reporter=json"], {
    cwd: DESKTOP, encoding: "utf-8", timeout: 30 * 60_000, maxBuffer: 256 * 1024 * 1024,
    env: { ...process.env, PLAYWRIGHT_JSON_OUTPUT_NAME: outFile },
  });
  let rep;
  try {
    rep = JSON.parse(readFileSync(outFile, "utf-8"));
  } catch {
    return { failed: [], problems: [{ kind: "build", detail: `playwright 无报告：exit=${r.status} ${String(r.stderr).slice(-300)}` }] };
  }
  return parseE2eReport(rep, `：${file} -g ${grep}`);
}

/**
 * 判定。problems 优先：跑器没跑出有效结果时，红条名单不可信（可能只是没跑），不计杀死。
 * 编号带边界匹配：TH-1 不许命中 TH-10，TX-8 不许命中 TX-8b。
 */
export function classify(expect, failed, problems = []) {
  const idRe = (id) => new RegExp(`(^|[^A-Za-z0-9-])${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![0-9A-Za-z])`);
  const hit = expect.filter((id) => failed.some((f) => idRe(id).test(f)));
  let verdict;
  if (problems.some((p) => p.kind === "build")) verdict = "BUILD_FAIL";
  else if (problems.length > 0) verdict = "NO_TESTS";
  else verdict = failed.length === 0 ? "SURVIVED" : hit.length === expect.length ? "KILLED" : hit.length > 0 ? "PARTIAL" : "OTHER";
  return { hit, verdict };
}

function edits(mut) {
  return mut.edits ?? [{ file: mut.file, old: mut.old, new: mut.new }];
}

function checkOne(mut) {
  const es = edits(mut);
  const originals = new Map();
  for (const e of es) {
    const p = join(DESKTOP, e.file);
    if (!originals.has(p)) originals.set(p, readFileSync(p, "utf-8"));
  }
  // 锚点逐字唯一（多处编辑按顺序施加在同一份文本上）
  const next = new Map(originals);
  for (const e of es) {
    const p = join(DESKTOP, e.file);
    const text = next.get(p);
    const n = text.split(e.old).length - 1;
    if (n !== 1) return { ...mut, error: `锚点在 ${e.file} 命中 ${n} 次（需逐字命中且唯一）` };
    next.set(p, text.replace(e.old, e.new));
  }
  for (const [p, text] of next) writeFileSync(p, text);
  let failed = [];
  const problems = [];
  try {
    for (const t of mut.targets) {
      const r = t.vitest ? runVitest(t.vitest) : runE2e(t.e2e, t.grep);
      failed.push(...r.failed);
      problems.push(...r.problems);
    }
  } finally {
    for (const [p, text] of originals) writeFileSync(p, text);
  }
  for (const [p, text] of originals) {
    if (readFileSync(p, "utf-8") !== text) throw new Error(`变异 ${mut.id} 恢复失败：${p} 未逐字节复原`);
  }
  failed = [...new Set(failed)];
  const { hit, verdict } = classify(mut.expect, failed, problems);
  return { ...mut, error: null, failed, problems, hit, verdict };
}

// harness 自测（N38）：三条人造变异，判定都必须不是 KILLED。期望编号故意写成 grep 串本身——
// 旧判定正是因为占位串里带着 grep 才把它们判成 KILLED。
const SELF_TEST = [
  { id: "SELF-e2e-build", guard: "e2e 构建失败 → BUILD_FAIL", want: "BUILD_FAIL", expect: ["TX-15b"],
    targets: [{ e2e: "e2e/session.spec.ts", grep: "TX-15b" }], file: "src/renderer/App.tsx",
    old: "export function App() {", new: "export function App((( // SELF-e2e-build\n) {" },
  { id: "SELF-e2e-empty", guard: "e2e grep 选不中 → NO_TESTS", want: "NO_TESTS", expect: ["N38-NO-SUCH-CASE"],
    targets: [{ e2e: "e2e/session.spec.ts", grep: "N38-NO-SUCH-CASE" }], file: "src/renderer/App.tsx",
    old: "export function App() {", new: "// SELF-e2e-empty（无害）\nexport function App() {" },
  { id: "SELF-vitest-build", guard: "vitest 导入的源码编译失败 → BUILD_FAIL", want: "BUILD_FAIL", expect: ["TH-1"],
    targets: [{ vitest: ["tests/host/settle.test.ts"] }], file: "src/host/sessions.ts",
    old: "export class SessionManager {", new: "export class SessionManager ((( { // SELF-vitest-build" },
];

function selfTest() {
  let bad = 0;
  for (const m of SELF_TEST) {
    const row = checkOne(m);
    const ok = !row.error && row.verdict === m.want;
    if (!ok) bad += 1;
    console.log(`[${m.id}] ${ok ? "ok" : "FAIL"} want=${m.want} got=${row.error ? `ERROR ${row.error}` : row.verdict} :: ${(row.problems ?? []).map((p) => p.detail).join(" | ").slice(0, 300)}`);
  }
  console.log(bad ? `\n⚠️ harness 自测 ${bad} 条不符` : "\nharness 自测全部符合");
  return bad ? 1 : 0;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.selfTest) return selfTest();
  if (args.list) {
    for (const m of MUTATIONS) console.log(`${m.id.padEnd(8)} ${m.expect.join(",").padEnd(22)} ${m.guard}`);
    return 0;
  }
  const dirty = dirtyFiles();
  if (dirty.length && !args.dirtyOk) {
    console.error("ABORT: desktop/ 工作树不干净（恢复动作按内存原文写回，但脏树上的结果不对应任何 commit）。先提交，或 --dirty-ok：");
    for (const d of dirty) console.error(`  ${d}`);
    return 2;
  }
  if (dirty.length) console.log(`[WARN] --dirty-ok：${dirty.length} 个文件未提交，本轮结果不对应任何 commit`);
  const rows = [];
  for (const m of MUTATIONS) {
    if (args.only && !args.only.includes(m.id)) continue;
    const t0 = Date.now();
    const row = checkOne(m);
    row.seconds = Math.round((Date.now() - t0) / 1000);
    rows.push(row);
    if (row.error) console.log(`[${m.id}] ⚠️ ${row.error}`);
    else console.log(`[${m.id}] ${row.verdict} (${row.seconds}s) expect=${m.expect.join(",")} red=${row.failed.length} :: ${[...row.problems.map((p) => `<${p.detail}>`), ...row.failed].slice(0, 5).join(" | ")}`);
  }
  let merged = {};
  if (existsSync(args.out)) for (const r of JSON.parse(readFileSync(args.out, "utf-8"))) merged[r.id] = r;
  for (const r of rows) merged[r.id] = r;
  writeFileSync(args.out, JSON.stringify(Object.values(merged), null, 2));
  const bad = rows.filter((r) => r.error || r.verdict !== "KILLED").map((r) => `${r.id}:${r.error ? "ERROR" : r.verdict}`);
  console.log(`\n结果已写入 ${args.out}`);
  if (bad.length) console.error(`⚠️ 非 KILLED：${bad.join(" ")}`);
  return 0;
}

// 单测（tests/verifyMutations.test.ts）只 import 判定函数，不跑 main
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) process.exit(main());
