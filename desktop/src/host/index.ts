// host utilityProcess 入口（Spec 8 §2.2）：接 main 的生命周期消息与 renderer 直连端口，转给 HostService。
// 禁止：对 data/ 的任何写、出网、监听端口、import electron（TG-3）。
import type { Envelope } from "../shared/protocol";
import type { HostToMain, MainToHost } from "../shared/lifecycle";
import { stringifyLossless } from "../shared/losslessJson";
import { createConfirmBroker } from "./confirm";
import { HostService } from "./service";
import { __avaTestSetKeychainExec } from "./spawner";
import { setSpawnObserver } from "./spawner";

interface PortLike {
  on(ev: "message", fn: (e: { data: unknown }) => void): void;
  on(ev: "close", fn: () => void): void;
  postMessage(msg: unknown): void;
  start(): void;
  close(): void;
}

interface ParentPortLike {
  on(ev: "message", fn: (e: { data: MainToHost; ports: PortLike[] }) => void): void;
  postMessage(msg: HostToMain): void;
}

// utilityProcess 注入的 process.parentPort（electron.d.ts 对 NodeJS.Process 的扩展）；按最小形状取用，不 import electron
const parentPort = (process as unknown as { parentPort: ParentPortLike }).parentPort;

let service: HostService | null = null;
let packaged = true; // init 之前一律按打包版处理：测试钩子不布置
let port: PortLike | null = null;
const pendingPorts: PortLike[] = [];

// repoRoot 对话框：host → main 请求，main 选择并二次确认后回 repo-root-chosen
let dialogSeq = 0;
const dialogWaiters = new Map<number, (repoRoot: string | null) => void>();
function chooseRepoRoot(): Promise<string | null> {
  const reqId = ++dialogSeq;
  return new Promise((resolve) => {
    dialogWaiters.set(reqId, resolve);
    parentPort.postMessage({ type: "repo-root-dialog", reqId });
  });
}

// Spec 10 §2.4 第 5 层：browser/抓取卡「批准」前的原生确认框。reqId = "<bootId>:<n>"（🟡-5 防串号）。
const bootId = Math.random().toString(36).slice(2, 10);
let svcRef: HostService | null = null;
const confirmBroker = createConfirmBroker({ bootId, post: (m) => parentPort.postMessage(m) });

// 测试钩子（仅未打包构建，TG-6）：main 布置 → 走到钩子处通知 main 并暂停，直到 main 放行
const armed = new Set<string>();
const releases = new Map<string, () => void>();
function pauseAt(name: string): Promise<void> {
  if (!armed.delete(name)) return Promise.resolve();
  parentPort.postMessage({ type: "test-hook-hit", name });
  return new Promise((r) => releases.set(name, r));
}

function bindPort(p: PortLike): void {
  if (!service) {
    pendingPorts.push(p);
    return;
  }
  port?.close();
  port = p;
  const svc = service;
  svc.attach((env: Envelope) => p.postMessage(env));
  p.on("message", (e) => void svc.handle(e.data as Envelope));
  p.start();
}

parentPort.on("message", (e) => {
  const m = e.data;
  switch (m.type) {
    case "init": {
      packaged = m.isPackaged;
      // 仅未打包构建的测试钩子（TG-6）：把 KEYCHAIN_READ 换成夹具脚本
      if (!m.isPackaged && m.keychainExec) __avaTestSetKeychainExec(m.keychainExec);
      if (!m.isPackaged) {
        // e2e 观测口：每次 spawn 一行 AVA_SPAWN（TA-2/TA-11 按 decide 关联号与 trigger 标签统计）
        setSpawnObserver((s) => process.stdout.write(`AVA_SPAWN ${stringifyLossless({ template: s.template, argv: s.argv, trigger: s.trigger ?? null, decide: s.decide ?? null })}\n`));
      }
      const svc = new HostService(
        { userData: m.userData, isPackaged: m.isPackaged, appPath: m.appPath, devRepoRoot: m.devRepoRoot },
        { chooseRepoRoot, confirm: (t, d) => confirmBroker.request(t, d), bootId, testHook: (name) => (!m.isPackaged ? pauseAt(name) : Promise.resolve()) },
        (reach) => parentPort.postMessage({ type: "reach", reach }),
        (dataRoot) => parentPort.postMessage({ type: "data-root", dataRoot }),
      );
      void svc.start().then(() => {
        service = svc;
        svcRef = svc;
        parentPort.postMessage({ type: "host-ready", losslessJson: svc.losslessJson, dataRoot: svc.dataRoot, provenance: svc.provenance.kind });
        for (const p of pendingPorts.splice(0)) bindPort(p);
      });
      break;
    }
    case "port":
      if (e.ports[0]) bindPort(e.ports[0]);
      break;
    case "renderer-reset":
      service?.resetRenderer();
      break;
    case "shutdown":
      service?.stop();
      break;
    case "quit-query":
      // M7 F-2 的测试钩子路径：armed("quit-query") 时吞掉这次应答，考 main 的 QUIT_QUERY_TIMEOUT_MS 兜底
      void pauseAt("quit-query").then(() => parentPort.postMessage({ type: "quit-state", busy: svcRef?.quitState() ?? [] }));
      break;
    case "quit-proceed":
      // M7 F-1/E-5 的测试钩子路径：armed("quit-proceed") 时把 stopping 阶段拉长，考 before-quit 的重入拦截
      void pauseAt("quit-proceed")
        .then(() => svcRef?.stopAllForQuit() ?? Promise.resolve())
        .then(() => parentPort.postMessage({ type: "sessions-down" }));
      break;
    case "confirm-result":
      confirmBroker.resolve(m.reqId, m.ok);
      break;
    case "repo-root-chosen": {
      const w = dialogWaiters.get(m.reqId);
      dialogWaiters.delete(m.reqId);
      w?.(m.repoRoot);
      break;
    }
    case "test-arm":
      if (!packaged) armed.add(m.name);
      break;
    case "test-release": {
      const r = releases.get(m.name);
      releases.delete(m.name);
      r?.();
      break;
    }
  }
});
