// main 进程（Spec 8 §2.2、§4.4）：启动参数白名单、单实例单窗口、导航封锁、端口撮合、host 看护、只读媒体协议、出网拦截、
// repoRoot 原生对话框。
// 禁止：spawn 子进程、解析事件/审批、对 data/ 的任何写、crashReporter.start（TG-3、TG-8）。
import * as fs from "node:fs";
import { join } from "node:path";
import { app, BrowserWindow, dialog, MessageChannelMain, systemPreferences, utilityProcess, type UtilityProcess } from "electron";
import { installEgressBlock } from "./egress";
import { MediaServer, registerMediaScheme } from "./mediaProtocol";
import { HOST_STDERR_TAIL_BYTES } from "../shared/constants";
import { initialRestartState, onHostCrash, onHostStarted } from "../shared/hostRestart";
import type { HostToMain, MainToHost } from "../shared/lifecycle";
import { repoRootProblem } from "../shared/repoRoot";

// 启动参数白名单（§2.10，红队 B4 / m3）：打包版除 macOS 可能附带的 -psn_* 外，出现任何参数一律拒绝启动。
// 9 位 fuses 里没有禁用 --remote-debugging-* 的一位，黑名单不可能列全。这是 main 入口的第一件事。
function argvRefused(): boolean {
  if (!app.isPackaged) return false;
  const extra = process.argv.slice(1).filter((a) => !a.startsWith("-psn_"));
  if (extra.length === 0) return false;
  process.stdout.write("AVA_REFUSE argv\n");
  return true;
}

// 关掉 AppKit 窗口状态恢复（N30）：app 崩溃过之后，AppKit 会在 finishLaunching 里弹模态框「上次意外退出，要重新打开窗口吗？」，
// 没人点就永远到不了 ready。ava 的窗口不靠 AppKit 恢复，这个框对它只有阻塞。必须在 finishLaunching 之前写入
// （main 脚本顶层即可）；只写 app 自己的偏好域，未打包构建的偏好域是 Electron 共用的，不碰。
function disableWindowRestoration(): void {
  if (app.isPackaged) systemPreferences.setUserDefault("ApplePersistenceIgnoreState", "boolean", true);
}

interface DevSwitches {
  repoRoot: string | null;
  userData: string | null;
  handshakeHook: boolean;
}

/** 测试驱动经 electronApp.evaluate 摆放的对话框桩（仅未打包构建，TA-12） */
interface DialogStub {
  calls: number;
  respond: { repoRoot: string | null; confirm: boolean };
}

type TestGlobals = typeof globalThis & {
  __avaTestDialog?: DialogStub;
  __avaTestRematch?: () => number | null;
  __avaTestArm?: (name: string) => void;
  __avaTestRelease?: (name: string) => void;
  __avaTestHookHits?: string[];
  __avaTestHostPid?: () => number | null;
};

// 测试专用启动开关：只在未打包构建中解析（TG-6、TS-7）
function readDevSwitches(): DevSwitches {
  const sw: DevSwitches = { repoRoot: null, userData: null, handshakeHook: false };
  if (!app.isPackaged) {
    for (const a of process.argv) {
      if (a.startsWith("--ava-repo-root=")) sw.repoRoot = a.slice("--ava-repo-root=".length);
      else if (a.startsWith("--ava-user-data=")) sw.userData = a.slice("--ava-user-data=".length);
      else if (a === "--ava-test-handshake-hook") sw.handshakeHook = true;
    }
  }
  return sw;
}

function boot(): void {
  const dev = readDevSwitches();
  if (!app.isPackaged && dev.userData) app.setPath("userData", dev.userData);

  registerMediaScheme(); // 必须在 app ready 之前（electron.d.ts registerSchemesAsPrivileged）

  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return;
  }

  let win: BrowserWindow | null = null;
  let host: UtilityProcess | null = null;
  let hostReady = false;
  let rendererLoaded = false;
  let mainLoadFailed = false; // 本次主框架载入是否失败（did-fail-load 之后的错误页 did-finish-load 不算载入成功）
  let quitting = false;
  let fatal: string | null = null;
  let restartState = initialRestartState(Date.now());
  let hostStderr = Buffer.alloc(0);
  let handshake = 0; // 每次撮合单调 +1，随端口一起投递；renderer 每个握手号至多采纳一次（§4.4）
  const devOrigin = !app.isPackaged && process.env.ELECTRON_RENDERER_URL ? new URL(process.env.ELECTRON_RENDERER_URL).origin : null;
  const media = new MediaServer(new Set(devOrigin ? ["file://", devOrigin] : ["file://"]));
  const testGlobals = globalThis as TestGlobals;

  const focusWindow = () => {
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.focus();
  };

  const sendToHost = (msg: MainToHost, transfer?: Electron.MessagePortMain[]) => {
    host?.postMessage(msg, transfer);
  };

  // renderer ↔ host 直连一条 MessagePort；main 只撮合不转发。每次 renderer 加载完成、每次 host 重启就绪都重撮合一次。
  // 返回本次握手号；守卫未满足（未撮合）返回 null。
  const matchmake = (): number | null => {
    if (!win || !host || !hostReady || !rendererLoaded) return null;
    const n = ++handshake;
    const { port1, port2 } = new MessageChannelMain();
    sendToHost({ type: "port" }, [port1]);
    const deliver = () => win?.webContents.postMessage("ava-port", n, [port2]);
    // TI-2 的 main 侧重新握手钩子：延迟投递真实端口，给伪造端口先到的机会（仅未打包构建）
    if (!app.isPackaged && dev.handshakeHook) setTimeout(deliver, 1500);
    else deliver();
    return n;
  };

  /** repoRoot 只在 main 里选（§2.10，红队 R2-M5）：原生目录对话框 + 二次确认（显示所选路径与其形状校验结果）。 */
  const chooseRepoRoot = async (): Promise<string | null> => {
    const stub = !app.isPackaged ? testGlobals.__avaTestDialog : undefined;
    if (stub) {
      stub.calls += 1;
      return stub.respond.confirm ? stub.respond.repoRoot : null;
    }
    const opts: Electron.OpenDialogOptions = { title: "选择 anime-video-agent 仓库根目录", properties: ["openDirectory"] };
    const picked = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
    const root = picked.canceled ? null : picked.filePaths[0] ?? null;
    if (!root) return null;
    const problem = repoRootProblem(root, {
      readText: (p) => {
        try {
          return fs.readFileSync(p, "utf-8");
        } catch {
          return null;
        }
      },
      isExecutable: (p) => {
        try {
          fs.accessSync(p, fs.constants.X_OK);
          return true;
        } catch {
          return false;
        }
      },
      exists: (p) => fs.existsSync(p),
    });
    const box: Electron.MessageBoxOptions = problem
      ? { type: "error", message: `不能切换到：${root}`, detail: `校验不通过：${problem}`, buttons: ["取消"] }
      : { type: "question", message: `把桌面端切换到这个仓库？\n${root}`, detail: "校验通过：pyproject.toml、.venv/bin/python、pipeline/agent/cli.py 均在。切换后全部状态从该仓库的磁盘重新读取。", buttons: ["切换", "取消"], defaultId: 1, cancelId: 1 };
    const r = win ? await dialog.showMessageBox(win, box) : await dialog.showMessageBox(box);
    return !problem && r.response === 0 ? root : null;
  };

  /** 熔断：停止重启，renderer 换成致命面板（含 host stderr 尾部）；UI 不再持有任何 host 端口 */
  const showFatal = (why: string) => {
    fatal = `${why}\n\n--- host stderr 尾部 ---\n${hostStderr.toString("utf-8")}`;
    process.stderr.write(`ava-host 熔断：${why}\n`);
    loadRenderer();
  };

  const startHost = () => {
    hostReady = false;
    hostStderr = Buffer.alloc(0);
    restartState = onHostStarted(restartState, Date.now());
    const h = utilityProcess.fork(join(__dirname, "host.js"), [], { serviceName: "ava-host", stdio: "pipe" });
    host = h;
    // 按行加前缀转发：按数据块加前缀会在行中间插入「[host] 」，把诊断行（如 AVA_SPAWN）切坏
    let outPartial = "";
    h.stdout?.on("data", (b: Buffer) => {
      const lines = (outPartial + b.toString("utf-8")).split("\n");
      outPartial = lines.pop() ?? "";
      for (const l of lines) process.stdout.write(`[host] ${l}\n`);
    });
    h.stderr?.on("data", (b: Buffer) => {
      process.stderr.write(`[host] ${b}`);
      const all = Buffer.concat([hostStderr, b]);
      hostStderr = all.subarray(Math.max(0, all.length - HOST_STDERR_TAIL_BYTES));
    });
    h.on("message", (m: HostToMain) => {
      if (host !== h) return;
      if (m.type === "host-ready") {
        process.stdout.write(`AVA_BOOT host-ready lossless-json=${m.losslessJson ? "ok" : "fail"}\n`);
        // 打包版验证的诊断行（§7.1 TS-7、TS-8）：dataRoot 与构建溯源判定
        process.stdout.write(`AVA_DIAG dataRoot=${m.dataRoot ?? "-"} provenance=${m.provenance}\n`);
        // TI-4：app 自身状态（userData、崩溃转储）的实际位置，打包版只能经 stdout 核对
        process.stdout.write(`AVA_PATHS ${JSON.stringify({ userData: app.getPath("userData"), crashDumps: app.getPath("crashDumps") })}\n`);
        media.setDataRoot(m.dataRoot);
        hostReady = true;
        matchmake();
      } else if (m.type === "reach") {
        media.setReach(m.reach);
        if (m.reach !== "ok") media.destroyAll(); // 脱盘前不持有 data/ 下任何 fd（TP-6）
      } else if (m.type === "data-root") {
        media.setDataRoot(m.dataRoot);
        media.destroyAll();
      } else if (m.type === "repo-root-dialog") {
        void chooseRepoRoot().then(
          (repoRoot) => sendToHost({ type: "repo-root-chosen", reqId: m.reqId, repoRoot }),
          () => sendToHost({ type: "repo-root-chosen", reqId: m.reqId, repoRoot: null }),
        );
      } else if (m.type === "test-hook-hit") {
        if (!app.isPackaged) testGlobals.__avaTestHookHits?.push(m.name);
      }
    });
    h.on("exit", (code) => {
      if (host !== h) return;
      process.stderr.write(`ava-host exited: ${code}\n`);
      host = null;
      hostReady = false;
      media.destroyAll();
      if (quitting) return;
      // 看护（§2.9）：退避 1 s → 2 s → 4 s …封顶 30 s；60 s 内崩 5 次熔断
      const r = onHostCrash(restartState, Date.now());
      restartState = r.state;
      if (r.action.kind === "fatal") showFatal(`host 在 60 s 内崩溃 ${r.action.crashesInWindow} 次，已停止重启（最后退出码 ${code}）`);
      else setTimeout(() => !quitting && !fatal && startHost(), r.action.delayMs);
    });
    sendToHost({
      type: "init",
      userData: app.getPath("userData"),
      isPackaged: app.isPackaged,
      appPath: app.getAppPath(),
      devRepoRoot: !app.isPackaged ? dev.repoRoot : null,
    });
  };

  const loadRenderer = () => {
    if (!win) return;
    mainLoadFailed = false;
    const query: Record<string, string> = fatal ? { fatal } : {};
    const devUrl = process.env.ELECTRON_RENDERER_URL;
    if (!app.isPackaged && devUrl) {
      const u = new URL(devUrl);
      for (const [k, v] of Object.entries(query)) u.searchParams.set(k, v);
      void win.loadURL(u.toString());
    } else {
      void win.loadFile(join(__dirname, "../renderer/index.html"), { query });
    }
  };

  const createWindow = () => {
    win = new BrowserWindow({
      width: 1280,
      height: 820,
      title: "ava",
      webPreferences: {
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        webSecurity: true,
        preload: join(__dirname, "../preload/index.js"),
      },
    });
    win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    win.webContents.on("will-navigate", (e) => e.preventDefault()); // 同时封住拖放文件导航
    // 只跟踪主框架：iframe 导航也会发 did-start-loading 却没有对应的 did-finish-load，
    // 会把 rendererLoaded 永久卡在 false、让之后的撮合被静默跳过（S22 🟡-1 ②）。
    // 用提交后的 did-navigate 而不是 did-start-navigation：后者在 will-navigate 被阻止的导航上也会触发。
    win.webContents.on("did-navigate", () => {
      rendererLoaded = false;
    });
    // 打包版验证的观测口（TS-2）：主框架是否真的载入了界面——只看 host 就绪会漏掉「窗口空白」
    win.webContents.on("did-fail-load", (_e, code, desc, url, isMainFrame) => {
      if (!isMainFrame) return;
      mainLoadFailed = true;
      process.stdout.write(`AVA_RENDERER fail ${code} ${desc} ${url}\n`);
    });
    win.webContents.on("did-finish-load", () => {
      // 主框架载入失败后 Chromium 会载入错误页并再触发一次 did-finish-load：错误页不算载入成功
      if (!mainLoadFailed) process.stdout.write("AVA_RENDERER loaded\n");
      rendererLoaded = true;
      sendToHost({ type: "renderer-reset" });
      matchmake();
    });
    win.on("closed", () => {
      win = null;
    });
    loadRenderer();
  };

  if (!app.isPackaged) {
    // TI-2 的 main 侧钩子：测试驱动经 electronApp.evaluate 触发一次带延迟投递的重新撮合
    if (dev.handshakeHook) testGlobals.__avaTestRematch = () => matchmake();
    // §4.3 host 侧测试钩子的中继：布置、命中记录、放行（TA-9/TA-9c/TA-12）
    testGlobals.__avaTestHookHits = [];
    testGlobals.__avaTestArm = (name: string) => sendToHost({ type: "test-arm", name });
    testGlobals.__avaTestRelease = (name: string) => sendToHost({ type: "test-release", name });
    testGlobals.__avaTestHostPid = () => host?.pid ?? null;
  }

  app.on("second-instance", focusWindow);

  void app.whenReady().then(() => {
    media.install();
    installEgressBlock(app.getAppPath(), !app.isPackaged ? process.env.ELECTRON_RENDERER_URL ?? null : null);
    startHost();
    createWindow();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on("before-quit", () => {
    quitting = true;
  });

  app.on("window-all-closed", () => {
    quitting = true;
    sendToHost({ type: "shutdown" });
    app.quit();
  });
}

if (argvRefused()) app.exit(1);
else {
  disableWindowRestoration();
  boot();
}
