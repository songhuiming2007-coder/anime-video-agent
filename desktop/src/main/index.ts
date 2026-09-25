// main 进程（Spec 8 §2.2、§4.4）：单实例单窗口、导航封锁、端口撮合、host 看护、只读媒体协议、出网拦截。
// 禁止：spawn 子进程、解析事件/审批、对 data/ 的任何写、crashReporter.start（TG-3、TG-8）。
import { join } from "node:path";
import { app, BrowserWindow, MessageChannelMain, utilityProcess, type UtilityProcess } from "electron";
import { installEgressBlock } from "./egress";
import { MediaServer, registerMediaScheme } from "./mediaProtocol";
import type { HostToMain, MainToHost } from "../shared/lifecycle";

interface DevSwitches {
  repoRoot: string | null;
  userData: string | null;
  handshakeHook: boolean;
}

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

const dev = readDevSwitches();
if (!app.isPackaged && dev.userData) app.setPath("userData", dev.userData);

registerMediaScheme(); // 必须在 app ready 之前（electron.d.ts registerSchemesAsPrivileged）

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  let win: BrowserWindow | null = null;
  let host: UtilityProcess | null = null;
  let hostReady = false;
  let rendererLoaded = false;
  let handshake = 0; // 每次撮合单调 +1，随端口一起投递；renderer 每个握手号至多采纳一次（§4.4）
  const devOrigin = !app.isPackaged && process.env.ELECTRON_RENDERER_URL ? new URL(process.env.ELECTRON_RENDERER_URL).origin : null;
  const media = new MediaServer(new Set(devOrigin ? ["file://", devOrigin] : ["file://"]));

  const focusWindow = () => {
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.focus();
  };

  const sendToHost = (msg: MainToHost, transfer?: Electron.MessagePortMain[]) => {
    host?.postMessage(msg, transfer);
  };

  // renderer ↔ host 直连一条 MessagePort；main 只撮合不转发。每次 renderer 加载完成重撮合一次。
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

  const startHost = () => {
    host = utilityProcess.fork(join(__dirname, "host.js"), [], { serviceName: "ava-host", stdio: "pipe" });
    host.stdout?.on("data", (b: Buffer) => process.stdout.write(`[host] ${b}`));
    host.stderr?.on("data", (b: Buffer) => process.stderr.write(`[host] ${b}`));
    host.on("message", (m: HostToMain) => {
      if (m.type === "host-ready") {
        process.stdout.write(`AVA_BOOT host-ready lossless-json=${m.losslessJson ? "ok" : "fail"}\n`);
        media.setDataRoot(m.dataRoot);
        hostReady = true;
        matchmake();
      } else if (m.type === "reach") {
        media.setReach(m.reach);
        if (m.reach !== "ok") media.destroyAll(); // 脱盘前不持有 data/ 下任何 fd（TP-6）
      } else if (m.type === "data-root") {
        media.setDataRoot(m.dataRoot);
      }
    });
    host.on("exit", (code) => {
      process.stderr.write(`ava-host exited: ${code}\n`);
      host = null;
      hostReady = false;
    });
    sendToHost({
      type: "init",
      userData: app.getPath("userData"),
      isPackaged: app.isPackaged,
      appPath: app.getAppPath(),
      devRepoRoot: !app.isPackaged ? dev.repoRoot : null,
    });
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
    win.webContents.on("did-finish-load", () => {
      rendererLoaded = true;
      sendToHost({ type: "renderer-reset" });
      matchmake();
    });
    win.on("closed", () => {
      win = null;
    });
    const devUrl = process.env.ELECTRON_RENDERER_URL;
    if (!app.isPackaged && devUrl) void win.loadURL(devUrl);
    else void win.loadFile(join(__dirname, "../renderer/index.html"));
  };

  // TI-2 的 main 侧钩子：测试驱动经 electronApp.evaluate 触发一次带延迟投递的重新撮合（仅未打包构建）
  if (!app.isPackaged && dev.handshakeHook) {
    (globalThis as { __avaTestRematch?: () => number | null }).__avaTestRematch = () => matchmake();
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

  app.on("window-all-closed", () => {
    sendToHost({ type: "shutdown" });
    app.quit();
  });
}
