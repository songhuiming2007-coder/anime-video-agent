// 测试辅助：Python 真实写端、临时仓库副本夹具、树清单。全部夹具在系统临时目录，真实 data/ 零污染。
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readlinkSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

export const DESKTOP = resolve(__dirname, "..");
export const REPO = resolve(DESKTOP, "..");
export const PY = join(REPO, ".venv/bin/python");

/** 在真实仓库的解释器里跑一段 Python（只读 import pipeline；不写字节码） */
export function py(code: string, args: string[] = []): string {
  return execFileSync(PY, ["-c", code, ...args], {
    cwd: REPO,
    encoding: "utf-8",
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONUTF8: "1" },
  });
}

export function tmp(prefix: string): string {
  return mkdtempSync(join(tmpdir(), `ava-desktop-${prefix}-`));
}

export function cleanup(dir: string): void {
  // 夹具可能被 chmod a-w，先恢复写权限
  try {
    execFileSync("/bin/chmod", ["-R", "u+w", dir]);
  } catch {
    /* 已不存在 */
  }
  rmSync(dir, { recursive: true, force: true });
}

/**
 * 临时 repo 副本（§7.1 夹具，假设 5）：复制 pipeline/、config/、pyproject.toml，软链 .venv，
 * 建 data/library/ 与 data/episodes/。withApprovals=false 时删掉副本里的 pipeline/approvals.py（能力缺席）。
 */
export function makeFixtureRepo(opts: { withApprovals?: boolean } = {}): string {
  const root = tmp("repo");
  cpSync(join(REPO, "pipeline"), join(root, "pipeline"), { recursive: true, filter: (s) => !s.includes("__pycache__") });
  cpSync(join(REPO, "config"), join(root, "config"), { recursive: true });
  cpSync(join(REPO, "pyproject.toml"), join(root, "pyproject.toml"));
  symlinkSync(join(REPO, ".venv"), join(root, ".venv"));
  if (opts.withApprovals === false) rmSync(join(root, "pipeline/approvals.py"));
  mkdirSync(join(root, "data/library/shots"), { recursive: true });
  mkdirSync(join(root, "data/episodes"), { recursive: true });
  return root;
}

export function mkEpisode(repo: string, epKey: string, files: Record<string, string> = {}): string {
  const abs = join(repo, "data/episodes", epKey);
  mkdirSync(abs, { recursive: true });
  for (const [rel, text] of Object.entries(files)) {
    mkdirSync(join(abs, rel, ".."), { recursive: true });
    writeFileSync(join(abs, rel), text);
  }
  return abs;
}

/** (相对路径, 大小, mtime_ns) 清单，排序后比较树是否被改动 */
export function treeManifest(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string, rel: string) => {
    for (const name of readdirSync(dir).sort()) {
      const p = join(dir, name);
      const r = rel ? `${rel}/${name}` : name;
      const st = lstatSync(p, { bigint: true });
      if (st.isSymbolicLink()) {
        out.push(`${r}\t-> ${readlinkSync(p)}\t${st.mtimeNs}`); // 不跟随链接
        continue;
      }
      out.push(`${r}\t${st.isDirectory() ? "d" : st.size}\t${st.mtimeNs}`);
      if (st.isDirectory()) walk(p, r);
    }
  };
  if (existsSync(root)) walk(root, "");
  return out;
}

export function shellScript(path: string, body: string): void {
  writeFileSync(path, `#!/bin/sh\n${body}\n`, { mode: 0o755 });
}
