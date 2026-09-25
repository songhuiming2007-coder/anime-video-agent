// 打包版验证（Spec 8 §7.1 TS 系列，红队 M7）：直接拉起 .app 二进制、读 stdout 的 AVA_BOOT / AVA_REFUSE / AVA_DIAG 行，
// 查退出码、lsof、ps——不经 inspector（fuse 已关，Playwright 驱动不了打包版）。打包版上的 UI 交互由门禁 15 手验。
// 前置：`npm run release-build` 已产出 dist/mac-arm64/ava.app（验证命令的第一步）。
// 打包版的 userData 固定在 ~/Library/Application Support/ava（测试开关在打包版不生效）：用例只写其中的 settings.json，
// 结束时恢复原状（原先不存在的删掉）；repoRoot 一律指向系统临时目录里的夹具仓库，不碰外置盘（避免触发 TCC 授权弹窗）。
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { cleanup, makeFixtureRepo, REPO, tmp } from "../tests/helpers";

const DESKTOP = resolve(__dirname, "..");
const APP = join(DESKTOP, "dist/mac-arm64/ava.app");
const binOf = (app: string) => join(app, "Contents/MacOS/ava");
const UD = join(homedir(), "Library/Application Support/ava");
const SETTINGS = join(UD, "settings.json");

interface Run {
  code: number | null;
  signal: string | null;
  stdout: string;
  stderr: string;
  /** 在 AVA_BOOT 与 AVA_DIAG 都出现之后、退出之前采样的结果 */
  probe: unknown;
  /** 运行期间对该进程树 LISTEN 的采样（假设 7） */
  listenSeen: string[];
}

function tree(pid: number): number[] {
  const out = [pid];
  const r = spawnSync("/usr/bin/pgrep", ["-P", String(pid)], { encoding: "utf-8" });
  for (const c of r.stdout.split("\n").filter(Boolean)) out.push(...tree(Number(c)));
  return out;
}

function listens(pids: number[]): string {
  return spawnSync("/usr/sbin/lsof", ["-a", "-p", pids.join(","), "-iTCP", "-sTCP:LISTEN", "-nP"], { encoding: "utf-8" }).stdout.trim();
}

/** 拉起打包版；出现 AVA_BOOT + AVA_DIAG 后执行 probe 并结束进程；或 ms 内自行退出。 */
function run(app: string, args: string[] = [], opts: { ms?: number; probe?: (pid: number) => unknown; waitFor?: string; sampleListen?: boolean } = {}): Promise<Run> {
  const ms = opts.ms ?? 15_000;
  return new Promise((done) => {
    const child = spawn(binOf(app), args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let probe: unknown = null;
    const listenSeen: string[] = [];
    let probed = false;
    // LISTEN 采样只在需要时开（假设 7、TI-6b）：同步 pgrep/lsof 每 50 ms 一次会长时间阻塞本进程事件循环
    const sampler = setInterval(() => {
      if (!opts.sampleListen || child.pid === undefined || child.exitCode !== null) return;
      const l = listens(tree(child.pid));
      if (l) listenSeen.push(l);
    }, 50);
    const finish = () => {
      clearInterval(sampler);
      clearTimeout(timer);
      if (killTimer) clearTimeout(killTimer);
    };
    // SIGTERM 后 5 s 仍未退出 → 对整棵进程树 SIGKILL（打包版偶发不响应 SIGTERM；不兜底会留下孤儿、占住单实例锁）
    let killTimer: NodeJS.Timeout | null = null;
    const stop = () => {
      child.kill("SIGTERM");
      killTimer = setTimeout(() => {
        if (child.pid === undefined || child.exitCode !== null) return;
        console.log(`打包版进程 ${child.pid} 在 SIGTERM 后 5 s 未退出，SIGKILL 整棵进程树`);
        if (process.env.AVA_PK_FORENSICS) {
          const ps = spawnSync("/bin/ps", ["-o", "pid,ppid,stat,etime,command", "-p", tree(child.pid).join(",")], { encoding: "utf-8" }).stdout;
          const smp = spawnSync("/usr/bin/sample", [String(child.pid), "1"], { encoding: "utf-8" }).stdout;
          writeFileSync(process.env.AVA_PK_FORENSICS, `${ps}\n${smp}`);
        }
        for (const p of tree(child.pid).reverse()) {
          try {
            process.kill(p, "SIGKILL");
          } catch {
            /* 已退出 */
          }
        }
      }, 5000);
    };
    const timer = setTimeout(stop, ms);
    child.stdout.on("data", (b: Buffer) => {
      stdout += b.toString();
      if (!probed && stdout.includes("AVA_BOOT") && stdout.includes("AVA_DIAG") && (!opts.waitFor || stdout.includes(opts.waitFor))) {
        probed = true;
        setTimeout(() => {
          probe = opts.probe ? opts.probe(child.pid!) : null;
          stop();
        }, 500);
      }
    });
    child.stderr.on("data", (b: Buffer) => (stderr += b.toString()));
    child.on("exit", (code, signal) => {
      finish();
      // 等整棵进程树退干净：单实例锁要等主进程完全退出才释放
      setTimeout(() => done({ code, signal, stdout, stderr, probe, listenSeen }), 800);
    });
  });
}

let savedSettings: string | null = null;
beforeAll(() => {
  if (!existsSync(binOf(APP))) throw new Error(`缺打包产物 ${APP}：先跑 npm run release-build`);
  savedSettings = existsSync(SETTINGS) ? readFileSync(SETTINGS, "utf-8") : null;
});
afterAll(() => {
  if (savedSettings === null) rmSync(SETTINGS, { force: true });
  else writeFileSync(SETTINGS, savedSettings);
});

function setRepo(repoRoot: string | null): void {
  mkdirSync(UD, { recursive: true });
  if (repoRoot === null) rmSync(SETTINGS, { force: true });
  else writeFileSync(SETTINGS, `${JSON.stringify({ version: 1, repoRoot })}\n`);
}

const diagOf = (stdout: string) => /AVA_DIAG dataRoot=(\S+) provenance=(\S+)/.exec(stdout);

describe("TS-2 fuses 与 asar 完整性（假设 2、10）", () => {
  it("verify-fuses：9 位与期望表一致，Info.plist 含 ElectronAsarIntegrity 且与复算的 asar 头哈希相等", () => {
    const out = execFileSync(process.execPath, [join(DESKTOP, "scripts/verify-fuses.mjs"), APP], { encoding: "utf-8" });
    const v = JSON.parse(/VERIFY_FUSES (\{.*\})/.exec(out)![1]);
    expect(v.ok).toBe(true);
    expect(v.fuses).toEqual({
      RunAsNode: "DISABLE",
      EnableCookieEncryption: "ENABLE",
      EnableNodeOptionsEnvironmentVariable: "DISABLE",
      EnableNodeCliInspectArguments: "DISABLE",
      EnableEmbeddedAsarIntegrityValidation: "ENABLE",
      OnlyLoadAppFromAsar: "ENABLE",
      LoadBrowserProcessSpecificV8Snapshot: "DISABLE",
      GrantFileProtocolExtraPrivileges: "ENABLE",
      WasmTrapHandlers: "ENABLE", // Electron 44.4.5 出厂默认（2026-09-25 读未打包二进制实测）
    });
    expect(v.integrity).toBe(v.recomputed);
  });
  it("打包版拉起后 host 从 asar 就绪，且无损自检在打包运行时成立（lossless-json=ok）", async () => {
    setRepo(null);
    const r = await run(APP);
    expect(r.stdout).toContain("AVA_BOOT host-ready lossless-json=ok");
  });
  it("打包版主框架确实载入了界面（AVA_RENDERER loaded，而不是 did-fail-load）", async () => {
    setRepo(null);
    const r = await run(APP, [], { waitFor: "AVA_RENDERER" });
    expect(/AVA_RENDERER (.*)/.exec(r.stdout)?.[1]).toBe("loaded");
  });
});

/** 复制一份 .app 并翻转 app.asar 的一个字节：where = 主入口文件内容区 / asar 头 */
function tamperedCopy(where: "main-entry" | "header"): string {
  const dir = tmp("tamper");
  const app = join(dir, "ava.app");
  execFileSync("/usr/bin/ditto", [APP, app]);
  const asar = join(app, "Contents/Resources/app.asar");
  const b = readFileSync(asar);
  const headerSize = b.readUInt32LE(4);
  const h = b.subarray(8, 8 + headerSize);
  const len = h.readInt32LE(4);
  const headerStr = h.subarray(8, 8 + len).toString("utf-8");
  let off: number;
  if (where === "header") {
    off = 8 + 8 + headerStr.indexOf('"build-info.json"') + 2; // 头 JSON 里的一个文件名字符
  } else {
    const hdr = JSON.parse(headerStr);
    const ent = hdr.files.out.files.main.files["index.js"];
    off = 8 + headerSize + Number(ent.offset) + Math.floor(ent.size / 2);
  }
  b[off] ^= 0x01;
  writeFileSync(asar, b);
  return app;
}

describe("TS-1 asar 完整性：篡改主入口或 asar 头 → 应用代码运行前终止", () => {
  it("两份篡改副本均非 0 退出、10 s 内无 AVA_BOOT；原件出现 AVA_BOOT host-ready", async () => {
    setRepo(null);
    for (const where of ["main-entry", "header"] as const) {
      const app = tamperedCopy(where);
      const r = await run(app, [], { ms: 10_000 });
      console.log(`TS-1 ${where}: code=${r.code} signal=${r.signal} stderr=${r.stderr.trim().split("\n").slice(-2).join(" | ")}`);
      expect(r.stdout).not.toContain("AVA_BOOT");
      expect(r.code === 0 && r.signal === null).toBe(false);
      cleanup(resolve(app, ".."));
    }
    expect((await run(APP)).stdout).toContain("AVA_BOOT host-ready");
  });
});

// TS-9（N30）：篡改副本的崩溃会给本 app 留下崩溃历史。修复前，之后的某次启动会被 AppKit「上次意外退出，要重新打开窗口吗？」
// 模态框挡在 finishLaunching 里，永远到不了 ready。紧跟 TS-1 的两次崩溃：每次启动前删掉偏好键，只能靠这次启动自己写入
const PREF = ["local.ava.desktop", "ApplePersistenceIgnoreState"];
describe("TS-9 崩溃之后启动不被「重新打开窗口」模态框挡住", () => {
  it("TS-1 崩溃后连续 3 次正常启动都出现 AVA_BOOT，且每次都由 app 自己写回 ApplePersistenceIgnoreState=1", async () => {
    setRepo(null);
    for (let i = 0; i < 3; i++) {
      spawnSync("/usr/bin/defaults", ["delete", ...PREF]);
      const r = await run(APP);
      expect(r.stdout, `第 ${i + 1} 次启动`).toContain("AVA_BOOT host-ready");
      expect(spawnSync("/usr/bin/defaults", ["read", ...PREF], { encoding: "utf-8" }).stdout.trim()).toBe("1");
    }
  });
});

describe("TS-6 启动参数白名单", () => {
  it("--remote-debugging-port=0 / --remote-debugging-pipe / --foo → AVA_REFUSE argv、非 0 退出、无 AVA_BOOT；不带参数正常", async () => {
    setRepo(null);
    for (const a of ["--remote-debugging-port=0", "--remote-debugging-pipe", "--foo"]) {
      const r = await run(APP, [a], { ms: 10_000 });
      expect(r.stdout).toContain("AVA_REFUSE argv");
      expect(r.stdout).not.toContain("AVA_BOOT");
      expect(r.code).not.toBe(0);
    }
    const ok = await run(APP);
    expect(ok.stdout, `stdout=${ok.stdout.slice(-400)}\nstderr=${ok.stderr.slice(-800)}`).toContain("AVA_BOOT");
  });
});

describe("TS-7 测试专用开关在打包版不生效", () => {
  it("带 --ava-repo-root=… 启动被白名单拒绝（开关永远到不了解析处）；正常启动的 dataRoot 恒为 settings 里 repoRoot 的 data", async () => {
    const repo = makeFixtureRepo();
    try {
      setRepo(repo);
      const other = tmp("other");
      const refused = await run(APP, [`--ava-repo-root=${other}`, `--ava-user-data=${other}`], { ms: 10_000 });
      expect(refused.stdout).toContain("AVA_REFUSE argv");
      expect(refused.stdout).not.toContain("AVA_BOOT");
      const ok = await run(APP);
      expect(diagOf(ok.stdout)![1]).toBe(join(repo, "data"));
      expect(existsSync(join(other, "settings.json"))).toBe(false);
      cleanup(other);
    } finally {
      cleanup(repo);
    }
  });
});

describe("TI-4 打包版 userData 与崩溃转储位置（假设 9 的打包侧）", () => {
  it("userData、crashDumps 均不在 realpath(dataRoot) 子树；userData ≠ data/browser-profile、不在 Chrome 配置子树；crashDumps 在 userData 子树", async () => {
    const repo = makeFixtureRepo();
    try {
      setRepo(repo);
      const r = await run(APP);
      const paths = JSON.parse(/AVA_PATHS (\{.*\})/.exec(r.stdout)![1]) as { userData: string; crashDumps: string };
      console.log(`TI-4 打包版 userData=${paths.userData} crashDumps=${paths.crashDumps}`);
      const dataReal = realpathSync(join(repo, "data"));
      for (const p of [paths.userData, paths.crashDumps]) expect(`${realpathSync(p)}/`.startsWith(`${dataReal}/`)).toBe(false);
      expect(paths.userData).toBe(UD);
      expect(paths.userData).not.toBe(join(repo, "data/browser-profile"));
      expect(`${paths.userData}/`.startsWith(join(homedir(), "Library/Application Support/Google/Chrome/"))).toBe(false);
      expect(`${realpathSync(paths.crashDumps)}/`.startsWith(`${realpathSync(paths.userData)}/`)).toBe(true);
    } finally {
      cleanup(repo);
    }
  });
});

describe("TI-6b 打包版全部进程零 LISTEN（Playwright 的调试端口不在场）", () => {
  it("host 就绪后，主进程及其全部子孙进程 lsof -iTCP -sTCP:LISTEN 为空", async () => {
    const repo = makeFixtureRepo();
    try {
      setRepo(repo);
      const r = await run(APP, [], { probe: (pid) => ({ pids: tree(pid), listen: listens(tree(pid)) }), sampleListen: true });
      const p = r.probe as { pids: number[]; listen: string };
      expect(p.pids.length).toBeGreaterThan(3); // 主进程 + GPU/网络/renderer/host 等 helper
      expect(p.listen).toBe("");
      expect(r.listenSeen).toEqual([]);
    } finally {
      cleanup(repo);
    }
  });
});

// ---------------- TS-8 / TS-8b：构建溯源（在临时 git 克隆里构建与提交，不碰用户仓库的提交历史） ----------------

const sh = (cwd: string, cmd: string, args: string[]) => execFileSync(cmd, args, { cwd, encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] });
const git = (cwd: string, ...args: string[]) => sh(cwd, "/usr/bin/git", ["-c", "user.name=ava-e2e", "-c", "user.email=ava-e2e@localhost", ...args]).trim();

/** 克隆当前仓库，并把工作树的 desktop/（不含依赖与产物）同步进去；data/ 建在克隆内，.venv 软链到真实环境 */
function cloneRepo(): string {
  const dir = join(tmp("clone"), "repo");
  git(REPO, "clone", "--quiet", REPO, dir);
  for (const name of readdirSync(join(DESKTOP))) {
    if (["node_modules", "out", "dist"].includes(name)) continue;
    rmSync(join(dir, "desktop", name), { recursive: true, force: true });
    cpSync(join(DESKTOP, name), join(dir, "desktop", name), { recursive: true });
  }
  symlinkSync(join(REPO, ".venv"), join(dir, ".venv"));
  mkdirSync(join(dir, "data/library/shots"), { recursive: true });
  mkdirSync(join(dir, "data/episodes"), { recursive: true });
  return dir;
}

function releaseBuild(repo: string): void {
  sh(join(repo, "desktop"), "/usr/bin/env", ["npm", "run", "release-build"]);
}

describe("TS-8 / TS-8b 构建溯源横幅", () => {
  let c1: string;
  let c2: string;
  beforeAll(() => {
    c1 = cloneRepo();
    git(c1, "add", "-A", "desktop");
    git(c1, "commit", "--quiet", "-m", "e2e: 同步工作树 desktop/");
    // 依赖先装好：TS-8b 要证伪的是「release-build 自己的 npm ci」，不能让克隆里缺依赖把构建提前弄挂（MUT-43 的机理）
    sh(join(c1, "desktop"), "/usr/bin/env", ["npm", "ci"]);
    c2 = cloneRepo(); // 另一个克隆：没有 c1 的这次提交
  }, 60_000);
  afterAll(() => {
    for (const c of [c1, c2]) if (c) cleanup(resolve(c, ".."));
  });

  it("干净构建 → 一致；构建后 desktop/ 有新提交 → stale；构建提交不在所选仓库 → incomparable；脏树构建 → dirty；node_modules 手改被 npm ci 冲掉（TS-8b）", async () => {
    releaseBuild(c1);
    const app1 = join(c1, "desktop/dist/mac-arm64/ava.app");
    const info1 = JSON.parse(readFileSync(join(c1, "desktop/out/build-info.json"), "utf-8"));
    expect(info1.desktopDirty).toBe(false);
    expect(info1.gitHead).toBe(git(c1, "rev-parse", "HEAD"));

    setRepo(c1);
    expect(diagOf((await run(app1)).stdout)![2]).toBe("consistent");

    writeFileSync(join(c1, "desktop/e2e-provenance.txt"), "构建之后的 desktop/ 提交\n");
    git(c1, "add", "desktop/e2e-provenance.txt");
    git(c1, "commit", "--quiet", "-m", "e2e: desktop/ 在构建之后有新提交");
    expect(diagOf((await run(app1)).stdout)![2]).toBe("stale");

    setRepo(c2);
    expect(diagOf((await run(app1)).stdout)![2]).toBe("incomparable");

    // TS-8b：在会被打进 renderer 的依赖文件里插入标记串；release-build 第一步 npm ci 必须把它冲掉
    const marker = "AVA_TS8B_MARKER_7f3a";
    const dep = join(c1, "desktop/node_modules/react-dom/client.js");
    writeFileSync(dep, `globalThis.__avaMarker = "${marker}";\n${readFileSync(dep, "utf-8")}`);
    writeFileSync(join(c1, "desktop/src/renderer/style.css"), `${readFileSync(join(c1, "desktop/src/renderer/style.css"), "utf-8")}/* e2e 未提交改动 */\n`);
    releaseBuild(c1);
    const hits = spawnSync("/usr/bin/grep", ["-rl", marker, join(c1, "desktop/out"), join(c1, "desktop/dist")], { encoding: "utf-8" });
    expect(hits.stdout.trim()).toBe("");
    const info2 = JSON.parse(readFileSync(join(c1, "desktop/out/build-info.json"), "utf-8"));
    expect(info2.lockfileSha256).toBe(createHash("sha256").update(readFileSync(join(c1, "desktop/package-lock.json"))).digest("hex"));
    expect(info2.desktopDirty).toBe(true);
    setRepo(c1);
    expect(diagOf((await run(app1)).stdout)![2]).toBe("dirty");
    expect(statSync(app1).isDirectory()).toBe(true);
  }, 600_000);
});

// 假设 7：被拒启动期间 50 ms 粒度采样整棵进程树的 LISTEN，如实记录。
describe("假设 7：被拒启动期间 DevTools 端口是否短暂监听", () => {
  it("--remote-debugging-port=0 / --remote-debugging-pipe：50 ms 粒度采样整棵进程树的 LISTEN，如实记录", async () => {
    setRepo(null);
    for (const a of ["--remote-debugging-port=0", "--remote-debugging-pipe"]) {
      const r = await run(APP, [a], { ms: 10_000, sampleListen: true });
      console.log(`假设 7 ${a}: code=${r.code} 运行期间 LISTEN 采样命中 ${r.listenSeen.length} 次${r.listenSeen.length ? `：${r.listenSeen[0].split("\n").slice(1).join(" / ")}` : ""}`);
      expect(r.stdout).toContain("AVA_REFUSE argv");
    }
  });
});
