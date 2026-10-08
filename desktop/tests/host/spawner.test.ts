// TI-7：子进程环境变量白名单；spawn 形态（stdin ignore、argv 不经 shell）；spawn 日志有界；status stdout 完整读入（S22）。
import { mkdirSync, symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { KEYCHAIN_SERVICE, SPAWN_LOG_MAX, SPAWN_TIMEOUT_SHORT_MS, STATUS_STDOUT_MAX_BYTES } from "../../src/shared/constants";
import { __avaTestSetKeychainExec, buildArgv, childEnv, isLongRunning, keychainExecPath, recordSpawn, runArgv, runCore, sessionArgv, spawnLog, spawnTotal } from "../../src/host/spawner";
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

describe("Spec 10 S8-R2 / Spec 12 S8-R19：NEW_EPISODE 与 09 定稿的 argv 形状", () => {
  it("NEW_EPISODE：期名单个 argv 元素，不经 shell", () => {
    const pyPath = join("/repo", ".venv/bin/python");
    expect(buildArgv("NEW_EPISODE", { name: "2026-09-26-建期测试" }, "/repo").argv).toEqual([
      pyPath, "-m", "pipeline.agent.cli", "new", "2026-09-26-建期测试", "--from-idea",
    ]);
    // 名字里的空格 / 怪字符都是一个 argv 元素（无 shell，逐字节到达 core）；Spec 18 §3.3：恒带 --from-idea
    expect(buildArgv("NEW_EPISODE", { name: "a b;$(x)" }, "/repo").argv.slice(-2)).toEqual(["a b;$(x)", "--from-idea"]);
  });

  it("APPROVE 09 变体：四个额外元素 --cover <路径> --title <标题>；其余停机点只有五元素形态", () => {
    const pyPath = join("/repo", ".venv/bin/python");
    expect(buildArgv("APPROVE", { ep: "/ep", stop: "09", approvalId: "appr_1", cover: "07-cover/a.png", title: "标题 带空格" }, "/repo").argv).toEqual([
      pyPath, "-m", "pipeline.agent.cli", "/ep", "/approve", "09", "--id", "appr_1", "--cover", "07-cover/a.png", "--title", "标题 带空格",
    ]);
    expect(buildArgv("APPROVE", { ep: "/ep", stop: "05", approvalId: "appr_2" }, "/repo").argv).toEqual([
      pyPath, "-m", "pipeline.agent.cli", "/ep", "/approve", "05", "--id", "appr_2",
    ]);
  });
});

describe("Spec 15 §2.7 PROBE_WEB_KEY_ENVS", () => {
  it("argv 逐位钉住：只调 core 的 web_key_env_names、每行一个名字；短超时；不开 PATH 口子", () => {
    const { argv, timeoutMs, homebrewPath } = buildArgv("PROBE_WEB_KEY_ENVS", {}, "/repo");
    expect(argv).toEqual([
      join("/repo", ".venv/bin/python"),
      "-c",
      "import sys; from pipeline.agent.web import web_key_env_names; sys.stdout.write(''.join(n + '\\n' for n in web_key_env_names()))",
    ]);
    expect(timeoutMs).toBe(SPAWN_TIMEOUT_SHORT_MS);
    expect(homebrewPath).toBeUndefined();
  });
});

describe("M7 F-3 钥匙串读取的 argv 形状与真实退出码", () => {
  it("KEYCHAIN_READ 的 argv 逐位钉住（-s <服务> -a <变量名> -w）", () => {
    const { argv, timeoutMs } = buildArgv("KEYCHAIN_READ", { envName: "MY_API_KEY" }, "/repo");
    expect(argv).toEqual([keychainExecPath(), "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", "MY_API_KEY", "-w"]);
    expect(timeoutMs).toBeGreaterThan(0);
  });

  it.skipIf(process.platform !== "darwin")(
    "真实 /usr/bin/security 按 buildArgv 的 argv 查不存在的账户 → 退 44（不碰真实条目、无交互）",
    async () => {
      __avaTestSetKeychainExec("/usr/bin/security"); // 默认值即此，显式写死以免夹具残留
      const { argv, timeoutMs } = buildArgv("KEYCHAIN_READ", { envName: "M7_NO_SUCH_ACCOUNT" }, "/repo");
      const r = await runArgv(argv, timeoutMs, root, childEnv(process.env));
      expect(r.code).toBe(44); // security 的「item could not be found」
      expect(keychainExecPath()).toBe("/usr/bin/security");
    },
  );
});

describe("Spec 11 §3.4 / Spec 12 §3.5：停机点模板的 argv 形状、超时与 PATH 开口", () => {
  const pyPath = join("/repo", ".venv/bin/python");
  const NS = "1790171112636927676";

  it("SAVE_SCRIPT：等号形式携带期望指纹 + stdin pipe", () => {
    const b = buildArgv("SAVE_SCRIPT", { ep: "/ep", size: "12", mtimeNs: NS }, "/repo");
    expect(b.argv).toEqual([pyPath, "-m", "pipeline.agent.cli", "/ep", "/save-script", "--expect-size=12", `--expect-mtime-ns=${NS}`]);
    expect(b.stdinPipe).toBe(true);
    expect(b.timeoutMs).toBe(30000);
    expect(b.homebrewPath).toBeUndefined();
  });

  it("SEAL_SCRIPT / RUN_TTS_APPLY_PATCH 显式开口 /opt/homebrew/bin；后者无超时（S8-R17）", () => {
    const seal = buildArgv("SEAL_SCRIPT", { ep: "/ep" }, "/repo");
    expect(seal.argv).toEqual([pyPath, "-m", "pipeline.agent.cli", "/ep", "/seal-script"]);
    expect(seal.homebrewPath).toBe(true);
    const done = buildArgv("RUN_TTS_APPLY_PATCH", { ep: "/ep" }, "/repo");
    expect(done.argv).toEqual([pyPath, "-m", "pipeline.agent.cli", "/ep", "/run", "tts", "--apply-patch"]);
    expect(done.timeoutMs).toBeNull();
    expect(done.homebrewPath).toBe(true);
    expect(childEnv(process.env, true).PATH).toBe("/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin");
    expect(childEnv(process.env).PATH).toBe("/usr/bin:/bin:/usr/sbin:/sbin");
  });

  it("VOICE_* 与 RECORD_TIME、CHECK_SCRIPT 的 argv 形状", () => {
    expect(buildArgv("VOICE_INFO", { ep: "/ep" }, "/repo").argv).toEqual([pyPath, "-m", "pipeline.agent.cli", "/ep", "/voice-info"]);
    expect(buildArgv("VOICE_PARSE", { ep: "/ep" }, "/repo").stdinPipe).toBe(true);
    expect(buildArgv("VOICE_ADD", { ep: "/ep" }, "/repo").stdinPipe).toBe(true);
    expect(buildArgv("VOICE_REVERT", { ep: "/ep", label: "2" }, "/repo").argv.at(-1)).toBe("2");
    expect(buildArgv("VOICE_RETRACT", { ep: "/ep", id: "7" }, "/repo").argv.at(-1)).toBe("7");
    expect(buildArgv("RECORD_TIME", { ep: "/ep", stop: "02.5", entered: "1000.000", left: "1060.000" }, "/repo").argv).toEqual([
      pyPath, "-m", "pipeline.agent.cli", "/ep", "/record-time", "02.5", "--entered=1000.000", "--left=1060.000",
    ]);
    expect(buildArgv("CHECK_SCRIPT", { scriptAbs: "/ep/02-script.md" }, "/repo").argv).toEqual([pyPath, "-m", "pipeline.check_script", "/ep/02-script.md"]);
  });

  it("D50-A S6：VOICE_CHECK / VOICE_GLOBAL / RUN_TTS 的 argv 形状；用户输入一律 --flag=value 单元素", () => {
    expect(buildArgv("VOICE_CHECK", { word: "-x", pinyin: "jie3 di4" }, "/repo").argv).toEqual([
      pyPath, "-m", "pipeline.corrections", "check", "--word=-x", "--pinyin=jie3 di4",
    ]);
    expect(buildArgv("VOICE_GLOBAL", { ep: "/ep", word: "绚都", homophone: "炫嘟", expect: "xuan4du1", supersede: true }, "/repo").argv).toEqual([
      pyPath, "-m", "pipeline.corrections", "global", "/ep", "--word=绚都", "--homophone=炫嘟", "--expect=xuan4du1", "--supersede",
    ]);
    expect(buildArgv("VOICE_GLOBAL", { ep: "/ep", word: "绚都", pinyin: "xuan4du1", supersede: false }, "/repo").argv.at(-1)).toBe("--pinyin=xuan4du1");
    const tts = buildArgv("RUN_TTS", { ep: "/ep" }, "/repo");
    expect(tts.argv).toEqual([pyPath, "-m", "pipeline.agent.cli", "/ep", "/run", "tts"]);
    expect(tts.timeoutMs).toBeNull();
    expect(isLongRunning("RUN_TTS")).toBe(true);
  });

  it("D50-A S7：TTS_REVIEW 只收三维 1–5，单个 --review= 元素；格式不对不 spawn", () => {
    expect(buildArgv("TTS_REVIEW", { ep: "/ep", review: "voice=4,prosody=3,misread=5" }, "/repo").argv).toEqual([
      pyPath, "-m", "pipeline.tts", "/ep", "--review=voice=4,prosody=3,misread=5",
    ]);
    for (const bad of ["voice=6,prosody=3,misread=5", "voice=4,prosody=3", "voice=4,prosody=3,misread=5,rhythm=2", "voice=4,prosody=3,misread=5 --force-all"]) {
      expect(() => buildArgv("TTS_REVIEW", { ep: "/ep", review: bad }, "/repo")).toThrow(/打点格式不对/);
    }
  });

  it("IMPORT_COVER：--name 为单个 argv 元素、stdin pipe（S8-R18）", () => {
    const b = buildArgv("IMPORT_COVER", { ep: "/ep", name: "我的图 名.png" }, "/repo");
    expect(b.argv).toEqual([pyPath, "-m", "pipeline.agent.cli", "/ep", "/import-cover", "--name=我的图 名.png"]);
    expect(b.stdinPipe).toBe(true);
  });

  it("runArgv 的 stdin pipe：逐字节到达、写完即 end()（stdoutFull 完整读入大载荷）", async () => {
    const r = await runArgv(["/bin/cat"], 30_000, root, childEnv(process.env), undefined, "hello 雪乃\n");
    expect(r.stdoutTail).toBe("hello 雪乃\n");
    const big = "x".repeat(300_000);
    const full = await runArgv(["/bin/cat"], 30_000, root, childEnv(process.env), 1 << 20, big);
    expect(full.stdoutFull).toBe(big);
  });

  it("无 stdin 数据时仍为 ignore（既有闭集不变）", async () => {
    const r = await runArgv(["/bin/sh", "-c", "read -r x; echo got=[$x]"], 30_000, root, childEnv(process.env));
    expect(r.stdoutTail).toBe("got=[]\n");
  });
});

describe("D45 会话管理模板的 argv 形状", () => {
  const pyPath = "/repo/.venv/bin/python";
  it("LIST_SESSIONS 完整读入 stdout；DELETE_SESSION 等号形式带会话号", () => {
    const list = buildArgv("LIST_SESSIONS", { ep: "/ep" }, "/repo");
    expect(list.argv).toEqual([pyPath, "-m", "pipeline.agent.cli", "/ep", "/list-sessions"]);
    expect(list.stdoutMax).toBeGreaterThan(0);
    expect(buildArgv("DELETE_SESSION", { ep: "/ep", sid: "aaaaaaaaaaaaaaa1" }, "/repo").argv).toEqual([
      pyPath, "-m", "pipeline.agent.cli", "/ep", "/delete-session", "--sid=aaaaaaaaaaaaaaa1",
    ]);
  });
  it("SESSION_CONTINUE 带 sid → --continue <sid>；不带 → 恢复最近会话（原形态不变）", () => {
    expect(sessionArgv("SESSION_CONTINUE", "/ep", "/repo", "bbbbbbbbbbbbbbb2")).toEqual([pyPath, "-m", "pipeline.agent.protocol", "/ep", "--continue", "bbbbbbbbbbbbbbb2"]);
    expect(sessionArgv("SESSION_CONTINUE", "/ep", "/repo")).toEqual([pyPath, "-m", "pipeline.agent.protocol", "/ep", "--continue"]);
  });
  it.each(["", "AAAAAAAAAAAAAAA1", "aaaa", "aaaaaaaaaaaaaaa1 ", "--force", "../aaaaaaaaaaaa"])("非法会话号 %j 抛错、不生成 argv", (sid) => {
    expect(() => buildArgv("DELETE_SESSION", { ep: "/ep", sid }, "/repo")).toThrow();
    expect(() => sessionArgv("SESSION_CONTINUE", "/ep", "/repo", sid)).toThrow();
  });
});
