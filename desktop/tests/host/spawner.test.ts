// TI-7：子进程环境变量白名单；spawn 形态（stdin ignore、argv 不经 shell）；spawn 日志有界；status stdout 完整读入（S22）。
import { mkdirSync, symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { SPAWN_LOG_MAX, STATUS_STDOUT_MAX_BYTES } from "../../src/shared/constants";
import { childEnv, recordSpawn, runCore, spawnLog, spawnTotal } from "../../src/host/spawner";
import { fetchStatus } from "../../src/host/status";
import { cleanup, PY, shellScript, tmp } from "../helpers";

const root = tmp("spawn");
afterAll(() => cleanup(root));

function fakeRepo(body: string): string {
  const repo = join(root, `r${Math.random().toString(16).slice(2, 8)}`);
  mkdirSync(join(repo, ".venv/bin"), { recursive: true });
  shellScript(join(repo, ".venv/bin/python"), body);
  return repo;
}

const WHITELIST = ["PATH", "HOME", "USER", "TMPDIR", "LANG", "PYTHONUTF8", "PYTHONUNBUFFERED"];

describe("TI-7 环境变量白名单", () => {
  it("子进程 os.environ 键集合 ⊆ 白名单；AVA_EVENTS_ROOT 与 FOO_API_KEY 看不到", async () => {
    process.env.AVA_EVENTS_ROOT = "/tmp/should-not-leak";
    process.env.FOO_API_KEY = "secret-should-not-leak";
    process.env.NODE_OPTIONS = "--inspect";
    try {
      // 真实解释器 + 假 pipeline/status.py：测的是 Python 子进程自己的 os.environ（cwd = repoRoot，-m 解析到假模块）
      const repo = join(root, "pyenv");
      mkdirSync(join(repo, ".venv/bin"), { recursive: true });
      symlinkSync(PY, join(repo, ".venv/bin/python"));
      mkdirSync(join(repo, "pipeline"));
      writeFileSync(join(repo, "pipeline/__init__.py"), "");
      writeFileSync(join(repo, "pipeline/status.py"), "import os\nfor k in sorted(os.environ): print(f'{k}={os.environ[k]}')\n");
      const r = await runCore("STATUS", { ep: "/x" }, { repoRoot: repo });
      expect(r.code).toBe(0);
      const keys = r.stdoutTail.split("\n").filter(Boolean).map((l) => l.slice(0, l.indexOf("=")));
      // macOS 的 CoreFoundation 会给每个进程补 __CF_USER_TEXT_ENCODING，不是继承来的
      const unexpected = keys.filter((k) => !WHITELIST.includes(k) && k !== "__CF_USER_TEXT_ENCODING");
      expect(unexpected).toEqual([]);
      expect(r.stdoutTail).not.toContain("should-not-leak");
      expect(keys).toContain("PATH");
      expect(r.stdoutTail).toContain("PATH=/usr/bin:/bin:/usr/sbin:/sbin\n");
    } finally {
      delete process.env.AVA_EVENTS_ROOT;
      delete process.env.FOO_API_KEY;
      delete process.env.NODE_OPTIONS;
    }
  });
  it("childEnv 纯函数：LANG 缺省补 en_US.UTF-8", () => {
    expect(childEnv({ HOME: "/h", FOO_TOKEN: "x" })).toEqual({
      PATH: "/usr/bin:/bin:/usr/sbin:/sbin", LANG: "en_US.UTF-8", PYTHONUTF8: "1", PYTHONUNBUFFERED: "1", HOME: "/h",
    });
  });
});

describe("spawn 形态", () => {
  it("stdin 恒为 ignore：读 stdin 的子进程立即 EOF", async () => {
    const repo = fakeRepo('read line; echo "got=[$line] rc=$?"');
    const t0 = Date.now();
    const r = await runCore("STATUS", { ep: "/x" }, { repoRoot: repo });
    const ms = Date.now() - t0;
    // 要区分的是「立即 EOF」与「挂到 10 s 超时」（MUT-10）；阈值取超时的一半，不卡机器负载
    expect(r.timedOut, `耗时 ${ms} ms`).toBe(false);
    expect(ms, `耗时 ${ms} ms`).toBeLessThan(5000);
    expect(r.stdoutTail).toBe("got=[] rc=1\n");
  });
  it("argv 不经 shell：期路径里的 shell 元字符原样到达", async () => {
    const repo = fakeRepo('printf "%s|" "$@"');
    const r = await runCore("STATUS", { ep: "/a b/$(touch pwned);x" }, { repoRoot: repo });
    expect(r.stdoutTail).toBe("-m|pipeline.status|/a b/$(touch pwned);x|--json|");
  });
});

describe("S22 🔵5 spawn 日志有界", () => {
  it("超过 SPAWN_LOG_MAX 后只保留最近的条目；累计次数仍单调", () => {
    const total0 = spawnTotal();
    for (let i = 0; i < SPAWN_LOG_MAX + 5; i++) recordSpawn({ template: "GIT_HEAD", argv: [`#${i}`], at: i });
    expect(spawnLog).toHaveLength(SPAWN_LOG_MAX);
    expect(spawnLog[0].argv).toEqual(["#5"]);
    expect(spawnLog[SPAWN_LOG_MAX - 1].argv).toEqual([`#${SPAWN_LOG_MAX + 4}`]);
    expect(spawnTotal()).toBe(total0 + SPAWN_LOG_MAX + 5);
  });
});

describe("S22 🔵6 status --json 的 stdout 完整读入（上限 STATUS_STDOUT_MAX_BYTES，超出按错误）", () => {
  /** 恰为 bytes 字节的合法 status JSON；advisories 里用中文填充 */
  function statusJson(bytes: number): string {
    const base = { episode_dir: "/x", episode_name: "x", current_step: "05 审时间码", is_blocked: false, block_reason: null, completed_steps: [], next_action: "", next_command: null, docs_ref: "", advisories: [""] };
    const empty = Buffer.byteLength(JSON.stringify(base));
    const pad = bytes - empty;
    base.advisories = ["审".repeat(Math.floor(pad / 3)) + "a".repeat(pad % 3)];
    const text = JSON.stringify(base);
    expect(Buffer.byteLength(text)).toBe(bytes);
    return text;
  }
  /** 假解释器：分两块写出文件，切分点落在一个中文字符（3 字节）中间 */
  function repoPrinting(text: string): string {
    const f = join(root, `out${Math.random().toString(16).slice(2, 8)}.json`);
    writeFileSync(f, text);
    const cut = Buffer.from(text).indexOf(Buffer.from("审")) + 1000 * 3 + 1;
    return fakeRepo(`head -c ${cut} '${f}'; sleep 0.1; tail -c +${cut + 1} '${f}'`);
  }

  it("20 KiB（远超旧的 8 KiB 尾部）分两块到达、切点在多字节字符中间 → 完整解析", async () => {
    const text = statusJson(20 * 1024);
    const r = await fetchStatus(repoPrinting(text), "/x");
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value.advisories[0]).toBe(JSON.parse(text).advisories[0]);
  });
  it("恰为上限 → 正常；上限 + 1 字节 → E_CORE（超过上限）", async () => {
    const atCap = await fetchStatus(repoPrinting(statusJson(STATUS_STDOUT_MAX_BYTES)), "/x");
    expect(atCap.ok).toBe(true);
    const over = await fetchStatus(repoPrinting(statusJson(STATUS_STDOUT_MAX_BYTES + 1)), "/x");
    expect(over).toEqual({ ok: false, code: "E_CORE", message: `status --json 输出超过 ${STATUS_STDOUT_MAX_BYTES} 字节上限` });
  });
});
