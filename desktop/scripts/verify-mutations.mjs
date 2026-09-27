#!/usr/bin/env node
// 桌面端变异验证 harness（Spec 10 §7.2 MUT 矩阵；与 scripts/verify_mutations.py 同一纪律）。
//
//   node scripts/verify-mutations.mjs                 # 全部
//   node scripts/verify-mutations.mjs --only MUT-3 MUT-50
//   node scripts/verify-mutations.mjs --list
//   node scripts/verify-mutations.mjs --out /tmp/x.json --dirty-ok
//
// 纪律（Python 版的三次事故教训照搬）：
// - 恢复 = 从内存写回开跑前读到的原文（不是 git checkout），并逐字节断言复原；
// - 锚点必须逐字命中且在该文件内唯一，否则报错、不静默跳过；
// - 默认拒绝在脏树上开跑（--dirty-ok 放行，结果标注不可复现）；
// - 每条只跑矩阵指定的目标（vitest 文件 / e2e 用例），并核对「红的是不是指定的那几条」：
//   KILLED = 指定杀手全红；PARTIAL = 部分红；OTHER = 指定的没红、别的红了；SURVIVED = 全绿。
//   只有 KILLED 算验过——被别的断言顺带杀死，证明不了指定的那条测试有效。
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { MUTATIONS } from "./mutations.mjs";

const DESKTOP = resolve(import.meta.dirname, "..");
const BIN = join(DESKTOP, "node_modules/.bin");

function parseArgs(argv) {
  const out = { only: null, list: false, out: join(tmpdir(), "desktop_mutation_results.json"), dirtyOk: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--list") out.list = true;
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

/** vitest：JSON 报告里每条失败用例的完整标题。 */
function runVitest(files) {
  const dir = mkdtempSync(join(tmpdir(), "mut-vitest-"));
  const outFile = join(dir, "r.json");
  const r = spawnSync(join(BIN, "vitest"), ["run", ...files, "--reporter=json", `--outputFile=${outFile}`], { cwd: DESKTOP, encoding: "utf-8", timeout: 15 * 60_000 });
  if (!existsSync(outFile)) return { failed: [`<vitest 无报告：exit=${r.status} ${String(r.stderr).slice(-300)}>`], ran: false };
  const rep = JSON.parse(readFileSync(outFile, "utf-8"));
  const failed = [];
  for (const f of rep.testResults ?? []) {
    for (const a of f.assertionResults ?? []) if (a.status === "failed") failed.push(a.title ?? a.fullName);
    if ((f.assertionResults ?? []).length === 0 && f.status === "failed") failed.push(`<文件失败：${f.name}：${String(f.message ?? "").slice(0, 200)}>`);
  }
  return { failed, ran: true };
}

/** Playwright：JSON 报告里 ok=false 的 spec 标题。 */
function runE2e(file, grep) {
  const r = spawnSync(join(BIN, "playwright"), ["test", file, "-g", grep, "--reporter=json"], { cwd: DESKTOP, encoding: "utf-8", timeout: 30 * 60_000, maxBuffer: 256 * 1024 * 1024 });
  let rep;
  try {
    rep = JSON.parse(r.stdout);
  } catch {
    return { failed: [`<playwright 无报告：exit=${r.status} ${String(r.stderr).slice(-300)}>`], ran: false };
  }
  const failed = [];
  let total = 0;
  const walk = (suite) => {
    for (const sp of suite.specs ?? []) {
      total += 1;
      if (!sp.ok) failed.push(sp.title);
    }
    for (const s of suite.suites ?? []) walk(s);
  };
  for (const s of rep.suites ?? []) walk(s);
  if (total === 0) failed.push(`<e2e 未选中任何用例：${file} -g ${grep}>`);
  return { failed, ran: total > 0 };
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
  try {
    for (const t of mut.targets) {
      const r = t.vitest ? runVitest(t.vitest) : runE2e(t.e2e, t.grep);
      failed.push(...r.failed);
    }
  } finally {
    for (const [p, text] of originals) writeFileSync(p, text);
  }
  for (const [p, text] of originals) {
    if (readFileSync(p, "utf-8") !== text) throw new Error(`变异 ${mut.id} 恢复失败：${p} 未逐字节复原`);
  }
  failed = [...new Set(failed)];
  // 编号带边界匹配：TH-1 不许命中 TH-10，TX-8 不许命中 TX-8b
  const idRe = (id) => new RegExp(`(^|[^A-Za-z0-9-])${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![0-9A-Za-z])`);
  const hit = mut.expect.filter((id) => failed.some((f) => idRe(id).test(f)));
  const verdict = failed.length === 0 ? "SURVIVED" : hit.length === mut.expect.length ? "KILLED" : hit.length > 0 ? "PARTIAL" : "OTHER";
  return { ...mut, error: null, failed, hit, verdict };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
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
    else console.log(`[${m.id}] ${row.verdict} (${row.seconds}s) expect=${m.expect.join(",")} red=${row.failed.length} :: ${row.failed.slice(0, 5).join(" | ")}`);
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

process.exit(main());
