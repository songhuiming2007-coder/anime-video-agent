// main 进程（Spec 8 §2.2、§4.4）：启动参数白名单、单实例单窗口、导航封锁、端口撮合、host 看护、只读媒体协议、出网拦截、
// repoRoot 原生对话框。
// 禁止：spawn 子进程、解析事件/审批、对 data/ 的任何写、crashReporter.start（TG-3、TG-8）。
import * as fs from "node:fs";
import { dirname, join } from "node:path";
import { app, BrowserWindow, dialog, ipcMain, Menu, MessageChannelMain, nativeTheme, screen, shell, systemPreferences, utilityProcess, type UtilityProcess } from "electron";
import { installEgressBlock } from "./egress";
import { MediaServer, registerMediaScheme } from "./mediaProtocol";
import { approveBoxOptions, APPROVE_BUTTON, createMainConfirmBroker, quitBoxOptions } from "./confirm";
import { HOST_STDERR_TAIL_BYTES, QUIT_QUERY_TIMEOUT_MS, QUIT_STOP_TIMEOUT_MS } from "../shared/constants";
import { initialRestartState, onHostCrash, onHostStarted } from "../shared/hostRestart";
import { DOCK_IDLE, onAttention, onFocus, type DockAction, type DockState } from "../shared/attention";
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

/** N41：e2e 窗口露在屏幕内的边长（px）；够让窗口仍算「可见」，又小到不挡人 */
const BACKGROUND_SLIVER_PX = 32;

interface DevSwitches {
  repoRoot: string | null;
  userData: string | null;
  handshakeHook: boolean;
  /** 仅未打包构建的测试钩子（TG-6）：把 KEYCHAIN_READ 的可执行路径换成夹具脚本（Spec 10 §3.4） */
  keychainExec: string | null;
  /**
   * 仅未打包构建（N41）：e2e 起的实例不进 Dock、不激活 app、窗口显示但不抢焦点。
   * 全量 e2e 每条用例起一次 app，不加这个开关本机在整轮里无法正常打字。
   */
  background: boolean;
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
  /** Spec 10 §2.10 退出确认桩（仅未打包构建） */
  __avaTestQuit?: { calls: number; lists: string[][]; respond: "quit" | "cancel" | "throw"; hold?: boolean };
  __avaTestQuitRelease?: (respond: "quit" | "cancel") => void;
  /** 未保存文稿退出/关窗确认桩（仅未打包构建） */
  __avaTestDirtyQuit?: { calls: number; respond: "quit" | "cancel" };
  /** Spec 10 §3.3 原生确认框桩（仅未打包构建） */
  __avaTestConfirm?: { calls: number; respond: boolean; last: { title: string; detail: string } | null };
  /** N44：focusWindow 被调次数（仅未打包构建，TI-10 断言用；系统焦点是否授予不归 app 控制） */
  __avaTestFocusCalls?: number;
  /** D98：Dock 动作记录（仅未打包构建；e2e 断言用，Dock 本身截不到） */
  __avaTestDock?: DockAction[];
};

// 测试专用启动开关：只在未打包构建中解析（TG-6、TS-7）
function readDevSwitches(): DevSwitches {
  const sw: DevSwitches = { repoRoot: null, userData: null, handshakeHook: false, keychainExec: null, background: false };
  if (!app.isPackaged) {
    for (const a of process.argv) {
      if (a.startsWith("--ava-repo-root=")) sw.repoRoot = a.slice("--ava-repo-root=".length);
      else if (a.startsWith("--ava-user-data=")) sw.userData = a.slice("--ava-user-data=".length);
      else if (a.startsWith("--ava-keychain=")) sw.keychainExec = a.slice("--ava-keychain=".length);
      else if (a === "--ava-test-handshake-hook") sw.handshakeHook = true;
      else if (a === "--ava-test-background") sw.background = true;
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
  let scriptDirty = false;
  let mainLoadFailed = false; // 本次主框架载入是否失败（did-fail-load 之后的错误页 did-finish-load 不算载入成功）
  let currentDataRoot: string | null = null;
  let quitting = false;
  let fatal: string | null = null;
  /** Spec 10 §2.10 退出状态机：防连按 Cmd+Q 重入 */
  type QuitPhase = "idle" | "confirming-dirty" | "querying" | "confirming" | "stopping";
  let quitPhase: QuitPhase = "idle";
  let quitQueryTimer: NodeJS.Timeout | null = null;
  let quitStopTimer: NodeJS.Timeout | null = null;
  /** 桩把确认框「挂住」时，放行它的入口（TX-8c 重入） */
  let pendingQuitConfirm: ((ok: boolean) => void) | null = null;
  /** N54：每次弹退出确认框换一个代号；窗口销毁时作废，迟到的应答不再生效 */
  let quitConfirmSeq = 0;
  let restartState = initialRestartState(Date.now());
  let hostStderr = Buffer.alloc(0);
  let handshake = 0; // 每次撮合单调 +1，随端口一起投递；renderer 每个握手号至多采纳一次（§4.4）
  const devOrigin = !app.isPackaged && process.env.ELECTRON_RENDERER_URL ? new URL(process.env.ELECTRON_RENDERER_URL).origin : null;
  const media = new MediaServer(new Set(devOrigin ? ["file://", devOrigin] : ["file://"]));
  const testGlobals = globalThis as TestGlobals;

  const focusWindow = () => {
    if (!win) return;
    if (!app.isPackaged) testGlobals.__avaTestFocusCalls = (testGlobals.__avaTestFocusCalls ?? 0) + 1;
    if (win.isMinimized()) win.restore();
    win.focus();
  };

  // D98（人选方案 C）：窗口不在前台时，角标记未看事件数；等人的事件让 Dock 一直弹到人切回来
  let dock: DockState = DOCK_IDLE;
  let bounceId: number | null = null;
  const applyDock = (actions: DockAction[]) => {
    for (const a of actions) {
      if (!app.isPackaged) (testGlobals.__avaTestDock ??= []).push(a);
      if (a.do === "badge") app.dock?.setBadge(a.text);
      else if (a.do === "bounce") bounceId = app.dock?.bounce("critical") ?? null;
      else if (bounceId !== null) {
        app.dock?.cancelBounce(bounceId);
        bounceId = null;
      }
    }
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

  /** repoRoot 只在 main 里选（§2.10，红队 R2-M5）：原生目录对话框 + 二次确认（失败支持一键重试，显示所选路径与校验结果）。 */
  const chooseRepoRoot = async (): Promise<string | null> => {
    const stub = !app.isPackaged ? testGlobals.__avaTestDialog : undefined;
    if (stub) {
      stub.calls += 1;
      return stub.respond.confirm ? stub.respond.repoRoot : null;
    }
    const defaultPath = currentDataRoot ? dirname(currentDataRoot) : undefined;
    for (;;) {
      const opts: Electron.OpenDialogOptions = {
        title: "选择 anime-video-agent 仓库根目录",
        properties: ["openDirectory"],
        ...(defaultPath ? { defaultPath } : {}),
      };
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
      if (problem) {
        const errBox: Electron.MessageBoxOptions = {
          type: "error",
          message: `不能切换到该目录`,
          detail: `${root}\n\n原因：${problem}`,
          buttons: ["重新选择…", "取消"],
          defaultId: 0,
          cancelId: 1,
        };
        const retry = win ? await dialog.showMessageBox(win, errBox) : await dialog.showMessageBox(errBox);
        if (retry.response === 0) continue;
        return null;
      }
      const box: Electron.MessageBoxOptions = {
        type: "question",
        message: `把桌面端切换到这个仓库？`,
        detail: `${root}\n\n校验通过，切换后将从该仓库重新读取全部状态。`,
        buttons: ["切换", "取消"],
        defaultId: 1,
        cancelId: 1,
      };
      const r = win ? await dialog.showMessageBox(win, box) : await dialog.showMessageBox(box);
      return r.response === 0 ? root : null;
    }
  };

  /** 熔断：停止重启，renderer 换成致命面板（含 host stderr 尾部）；UI 不再持有任何 host 端口 */
  const mainConfirm = createMainConfirmBroker({
    show: async (title, detail, signal, opts) => {
      const box: Electron.MessageBoxOptions = approveBoxOptions(title, detail, signal, opts);
      const r = win ? await dialog.showMessageBox(win, box) : await dialog.showMessageBox(box);
      return r.response === APPROVE_BUTTON;
    },
    result: (reqId, ok) => sendToHost({ type: "confirm-result", reqId, ok }),
    stub: () => (!app.isPackaged ? testGlobals.__avaTestConfirm : undefined),
  });

  /** 仅允许在系统浏览器打开严格校验后的 https:// 授权链接（防止 file:///javascript:/命令注入） */
  const openExternalHttps = async (rawUrl: string): Promise<{ ok: boolean; error?: string }> => {
    const trimmed = rawUrl.trim();
    if (!trimmed || trimmed.length > 4096 || /[\x00-\x1f\x7f]/.test(trimmed)) {
      return { ok: false, error: "链接格式不合法" };
    }
    let parsed: URL;
    try {
      parsed = new URL(trimmed);
    } catch {
      return { ok: false, error: "无法解析链接" };
    }
    if (parsed.protocol !== "https:" || !parsed.hostname || parsed.username || parsed.password) {
      return { ok: false, error: "只允许打开 https:// 授权页链接" };
    }
    try {
      await shell.openExternal(parsed.toString());
      return { ok: true };
    } catch (e) {
      return { ok: false, error: e instanceof Error ? e.message : String(e) };
    }
  };

  /** Spec 10 §2.10 第 5 步：main 发送 quit-proceed → host 收尾 → sessions-down → 停定时器 → 退出 */
  const proceedQuit = () => {
    quitPhase = "stopping";
    if (quitQueryTimer) clearTimeout(quitQueryTimer);
    sendToHost({ type: "quit-proceed" });
    quitStopTimer = setTimeout(() => {
      quitting = true;
      app.exit(0);
    }, QUIT_STOP_TIMEOUT_MS);
  };

  const finishQuit = () => {
    if (quitStopTimer) clearTimeout(quitStopTimer);
    sendToHost({ type: "shutdown" });
    quitting = true;
    app.exit(0);
  };

  const completeQuitConfirm = (ok: boolean) => {
    if (ok) proceedQuit();
    else quitPhase = "idle"; // 取消：不向 host 发任何消息，host 照常轮询
  };

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
        currentDataRoot = m.dataRoot;
        media.setDataRoot(m.dataRoot);
        hostReady = true;
        matchmake();
      } else if (m.type === "reach") {
        media.setReach(m.reach);
        if (m.reach !== "ok") media.destroyAll(); // 脱盘前不持有 data/ 下任何 fd（TP-6）
      } else if (m.type === "data-root") {
        currentDataRoot = m.dataRoot;
        media.setDataRoot(m.dataRoot);
        media.destroyAll();
      } else if (m.type === "repo-root-dialog") {
        void chooseRepoRoot().then(
          (repoRoot) => sendToHost({ type: "repo-root-chosen", reqId: m.reqId, repoRoot }),
          () => sendToHost({ type: "repo-root-chosen", reqId: m.reqId, repoRoot: null }),
        );
      } else if (m.type === "test-hook-hit") {
        if (!app.isPackaged) testGlobals.__avaTestHookHits?.push(m.name);
      } else if (m.type === "quit-state") {
        if (quitPhase !== "querying") return; // 重入/迟到：忽略
        if (quitQueryTimer) clearTimeout(quitQueryTimer);
        if (m.busy.length === 0) {
          proceedQuit();
          return;
        }
        quitPhase = "confirming";
        const seq = ++quitConfirmSeq;
        // N54：应答、抛异常一律回落（异常按「取消」）；代号已作废（窗口已销毁）或已不在 confirming 的迟到应答忽略
        const settle = (ok: boolean) => {
          if (seq !== quitConfirmSeq || quitPhase !== "confirming") return;
          completeQuitConfirm(ok);
        };
        const lists = m.busy.map((b) => `${b.label} · ${b.running ? "运行中" : "空闲"}${b.openRequests > 0 ? ` · ${b.openRequests} 张卡待答` : ""}`);
        const stub = !app.isPackaged ? testGlobals.__avaTestQuit : undefined;
        if (stub) {
          stub.calls += 1;
          stub.lists.push(lists);
          if (stub.hold) {
            pendingQuitConfirm = settle; // 挂住：模拟确认框已打开（TX-8c）
            return;
          }
          const answer = stub.respond === "throw" ? Promise.reject(new Error("stub")) : Promise.resolve(stub.respond === "quit");
          void answer.then(settle, () => settle(false));
          return;
        }
        const box: Electron.MessageBoxOptions = quitBoxOptions(lists);
        void (win ? dialog.showMessageBox(win, box) : dialog.showMessageBox(box)).then(
          (r) => settle(r.response === APPROVE_BUTTON),
          () => settle(false),
        );
      } else if (m.type === "sessions-down") {
        if (quitPhase === "stopping") finishQuit();
      } else if (m.type === "confirm-query") {
        mainConfirm.request(m.reqId, m.title, m.detail, m.kind || m.confirmLabel ? { kind: m.kind, confirmLabel: m.confirmLabel } : undefined);
      } else if (m.type === "open-external-url") {
        void openExternalHttps(m.url).then((res) => sendToHost({ type: "open-external-result", reqId: m.reqId, ...res }));
      } else if (m.type === "attention") {
        const r = onAttention(dock, { kind: m.kind, key: m.key }, win !== null && win.isFocused());
        dock = r.state;
        applyDock(r.actions);
      } else if (m.type === "theme-source") {
        nativeTheme.themeSource = m.theme;
      }
    });
    h.on("exit", (code) => {
      if (host !== h) return;
      process.stderr.write(`ava-host exited: ${code}\n`);
      host = null;
      hostReady = false;
      media.destroyAll();
      mainConfirm.hostExited(); // 撤下已打开的确认框、丢弃排队中的全部（二轮 🔵-5）
      // 退出流程中的 host 消失：视同 host 不可用，直接退出（§2.10 第 1、2 步）。
      // stopping 也短路（M7 F-4）：否则会落进看护重启分支，白拉一个新 host 再被兜底定时器带走。
      if (quitPhase !== "idle") {
        quitting = true;
        app.exit(0);
        return;
      }
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
      keychainExec: !app.isPackaged ? dev.keychainExec : null,
    });
  };

  const loadRenderer = () => {
    if (!win) return;
    mainLoadFailed = false;
    const boundedFatal = fatal ? (fatal.length > 4000 ? `${fatal.slice(0, 800)}\n…（日志已截断）…\n${fatal.slice(-3000)}` : fatal) : null;
    const query: Record<string, string> = boundedFatal ? { fatal: boundedFatal } : {};
    const devUrl = process.env.ELECTRON_RENDERER_URL;
    if (!app.isPackaged && devUrl) {
      const u = new URL(devUrl);
      for (const [k, v] of Object.entries(query)) u.searchParams.set(k, v);
      void win.loadURL(u.toString());
    } else {
      void win.loadFile(join(__dirname, "../renderer/index.html"), { query });
    }
  };

  const sendMenu = (action: string) => {
    if (!win) return;
    win.webContents.send("ava-menu", action);
  };

  const installAppMenu = () => {
    const isMac = process.platform === "darwin";
    const template: Electron.MenuItemConstructorOptions[] = [
      ...(isMac
        ? [
            {
              label: "AVA",
              submenu: [
                { role: "about" as const, label: "关于 AVA" },
                { type: "separator" as const },
                { role: "services" as const, label: "服务" },
                { type: "separator" as const },
                { role: "hide" as const, label: "隐藏 AVA" },
                { role: "hideOthers" as const, label: "隐藏其他" },
                { role: "unhide" as const, label: "全部显示" },
                { type: "separator" as const },
                { role: "quit" as const, label: "退出 AVA" },
              ],
            },
          ]
        : []),
      {
        label: "编辑",
        submenu: [
          { role: "undo", label: "撤销" },
          { role: "redo", label: "重做" },
          { type: "separator" },
          { role: "cut", label: "剪切" },
          { role: "copy", label: "拷贝" },
          { role: "paste", label: "粘贴" },
          { role: "selectAll", label: "全选" },
        ],
      },
      {
        label: "显示",
        submenu: [
          { label: "切换侧栏", accelerator: "CmdOrCtrl+B", click: () => sendMenu("toggle-left") },
          { label: "切换预览", accelerator: "CmdOrCtrl+J", click: () => sendMenu("toggle-preview") },
          { label: "搜索期或企划", accelerator: "CmdOrCtrl+K", click: () => sendMenu("focus-search") },
          { type: "separator" },
          { label: "刷新当前期", accelerator: "CmdOrCtrl+R", click: () => sendMenu("refresh-episode") },
          { type: "separator" },
          { role: "resetZoom", label: "实际大小" },
          { role: "zoomIn", label: "放大" },
          { role: "zoomOut", label: "缩小" },
          { type: "separator" },
          { role: "togglefullscreen", label: "进入全屏幕" },
          ...(!app.isPackaged ? [{ type: "separator" as const }, { role: "toggleDevTools" as const, label: "开发者工具" }] : []),
        ],
      },
      {
        label: "窗口",
        submenu: [
          { role: "minimize", label: "最小化" },
          { role: "zoom", label: "缩放" },
          { role: "close", label: "关闭窗口" },
          ...(isMac ? [{ type: "separator" as const }, { role: "front" as const, label: "前置全部窗口" }] : []),
        ],
      },
    ];
    Menu.setApplicationMenu(Menu.buildFromTemplate(template));
  };

  const createWindow = () => {
    win = new BrowserWindow({
      width: 1280,
      height: 820,
      minWidth: 760,
      minHeight: 540,
      backgroundColor: nativeTheme.shouldUseDarkColors ? "#161618" : "#f7f7f8",
      title: "AVA",
      ...(process.platform === "darwin" ? { titleBarStyle: "hiddenInset" as const, trafficLightPosition: { x: 14, y: 14 } } : {}),
      // N41：不用 show:false 了事——隐藏窗口的绘制会被 Chromium 压低，视觉审计与截图会失真；改为下面 showInactive
      show: !dev.background,
      webPreferences: {
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        webSecurity: true,
        preload: join(__dirname, "../preload/index.js"),
      },
    });
    // M2：Electron 默认静默吞掉 renderer 的 beforeunload；处理 will-prevent-unload 弹出明确二选一
    win.webContents.on("will-prevent-unload", (e) => {
      if (!win) return;
      const stub = !app.isPackaged ? testGlobals.__avaTestDirtyQuit : undefined;
      if (stub) {
        stub.calls += 1;
        if (stub.respond === "quit") e.preventDefault();
        return;
      }
      const choice = dialog.showMessageBoxSync(win, {
        type: "warning",
        message: "文稿有未保存的改动，仍要关闭窗口？",
        detail: "关闭窗口不会丢失浏览器暂存草稿，但改动尚未写入磁盘。",
        buttons: ["关闭窗口", "留下编辑"],
        defaultId: 1,
        cancelId: 1,
      });
      if (choice === 0) e.preventDefault();
    });
    if (dev.background) {
      // 固定停在主屏右下角、只露 BACKGROUND_SLIVER_PX 一条边：不压在人正在用的窗口上。
      // 不整窗移出屏幕：完全不可见时 macOS 判为被遮挡，Chromium 会停绘制、压定时器，视觉审计与计时用例失真
      // 先显示再移：macOS 在窗口上屏那一刻会把越界的位置拉回屏幕内，之后的移动不再纠正（实测）
      win.showInactive();
      const wa = screen.getPrimaryDisplay().workArea;
      win.setPosition(wa.x + wa.width - BACKGROUND_SLIVER_PX, wa.y + wa.height - BACKGROUND_SLIVER_PX);
    }
    win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    win.webContents.on("will-navigate", (e) => e.preventDefault()); // 同时封住拖放文件导航
    // 只跟踪主框架：iframe 导航也会发 did-start-loading 却没有对应的 did-finish-load，
    // 会把 rendererLoaded 永久卡在 false、让之后的撮合被静默跳过（S22 🟡-1 ②）。
    // 用提交后的 did-navigate 而不是 did-start-navigation：后者在 will-navigate 被阻止的导航上也会触发。
    win.webContents.on("did-navigate", () => {
      rendererLoaded = false;
      scriptDirty = false;
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
    win.on("focus", () => {
      const r = onFocus(dock);
      dock = r.state;
      applyDock(r.actions);
    });
    win.on("closed", () => {
      win = null;
      mainConfirm.windowGone(); // D40：挂在已销毁窗口上的确认框永不 resolve，主动回 ok:false
      rendererLoaded = false;
      scriptDirty = false;
      sendToHost({ type: "renderer-reset" }); // A3：无窗保活期间释放订阅并结算人时计时器，避免空转推送与虚计人时
      if (quitPhase === "confirming" || quitPhase === "confirming-dirty") {
        // N54：退出确认框挂在已销毁的窗口上同样永不 resolve；作废它并回落，否则此后 Cmd+Q 全被拦下
        quitConfirmSeq += 1;
        pendingQuitConfirm = null;
        quitPhase = "idle";
      }
    });
    loadRenderer();
  };

  ipcMain.on("ava-dirty", (e, dirty: unknown) => {
    if (win && e.sender === win.webContents) scriptDirty = Boolean(dirty);
  });

  if (!app.isPackaged) {
    // TI-2 的 main 侧钩子：测试驱动经 electronApp.evaluate 触发一次带延迟投递的重新撮合
    if (dev.handshakeHook) testGlobals.__avaTestRematch = () => matchmake();
    // §4.3 host 侧测试钩子的中继：布置、命中记录、放行（TA-9/TA-9c/TA-12）
    testGlobals.__avaTestHookHits = [];
    testGlobals.__avaTestArm = (name: string) => sendToHost({ type: "test-arm", name });
    testGlobals.__avaTestRelease = (name: string) => sendToHost({ type: "test-release", name });
    testGlobals.__avaTestHostPid = () => host?.pid ?? null;
    testGlobals.__avaTestQuitRelease = (respond: "quit" | "cancel") => {
      const f = pendingQuitConfirm;
      pendingQuitConfirm = null;
      if (f) f(respond === "quit");
    };
  }

  app.on("second-instance", () => {
    if (!win) createWindow();
    else focusWindow();
  });

  // N41：accessory = 不进 Dock、启动不激活 app（macOS）；要在窗口创建前设
  if (dev.background && process.platform === "darwin") app.setActivationPolicy("accessory");

  void app.whenReady().then(() => {
    installAppMenu();
    media.install();
    installEgressBlock(app.getAppPath(), !app.isPackaged ? process.env.ELECTRON_RENDERER_URL ?? null : null);
    startHost();
    createWindow();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  const beginQuitQuery = () => {
    quitPhase = "querying";
    sendToHost({ type: "quit-query" });
    quitQueryTimer = setTimeout(() => {
      quitting = true;
      app.exit(0);
    }, QUIT_QUERY_TIMEOUT_MS);
  };

  app.on("before-quit", (e) => {
    if (quitPhase !== "idle") {
      // 重入（连按 Cmd+Q）：一律拦下。stopping 期间也拦（M7 F-1）：收尾只能由 sessions-down 或
      // QUIT_STOP_TIMEOUT_MS 结束，不能被第二次 Cmd+Q 抢在前面 app.exit，否则 host 的 shutdown 与
      // 会话的确定收尾（S9-R3 wrapup:"skipped"）都会被跳过。
      e.preventDefault();
      return;
    }
    // 『结束会话』与其实时性无关的退出路径：host 不在、未就绪或已熔断 → 交由 Electron 默认关窗（会走 beforeunload / will-prevent-unload）
    if (!host || !hostReady || fatal !== null) {
      quitting = true;
      return;
    }
    e.preventDefault();
    // 02-script 编辑器有未保存改动时，先弹与 ⌘W 同口径的确认框（并立即通知 renderer 落盘暂存草稿），
    // 避免后续 app.exit(0) 绕过 beforeunload 静默丢稿
    if (scriptDirty && win) {
      quitPhase = "confirming-dirty";
      sendMenu("stash-draft");
      const seq = ++quitConfirmSeq;
      const settleDirty = (ok: boolean) => {
        if (seq !== quitConfirmSeq || quitPhase !== "confirming-dirty") return;
        if (ok) beginQuitQuery();
        else quitPhase = "idle";
      };
      const stub = !app.isPackaged ? testGlobals.__avaTestDirtyQuit : undefined;
      if (stub) {
        stub.calls += 1;
        settleDirty(stub.respond === "quit");
        return;
      }
      void dialog
        .showMessageBox(win, {
          type: "warning",
          message: "文稿有未保存的改动，仍要退出 AVA？",
          detail: "退出不会丢失浏览器暂存草稿，但改动尚未写入磁盘。",
          buttons: ["退出 AVA", "留下编辑"],
          defaultId: 1,
          cancelId: 1,
        })
        .then(
          (r) => settleDirty(r.response === 0),
          () => settleDirty(false),
        );
      return;
    }
    beginQuitQuery();
  });

  app.on("window-all-closed", () => {
    // A3：macOS 惯例——关闭窗口（⌘W / 红色关闭按钮）保留后台会话继续运行，点 Dock 图标重新打开；仅 ⌘Q 触发退出
    if (process.platform !== "darwin") app.quit();
  });
}

if (argvRefused()) app.exit(1);
else {
  disableWindowRestoration();
  boot();
}
