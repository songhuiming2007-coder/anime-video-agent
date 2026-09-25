// host utilityProcess 入口（Spec 8 §2.2）：接 main 的生命周期消息与 renderer 直连端口，转给 HostService。
// 禁止：对 data/ 的任何写、出网、监听端口、import electron（TG-3）。
import type { Envelope } from "../shared/protocol";
import type { HostToMain, MainToHost } from "../shared/lifecycle";
import { HostService } from "./service";

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
let port: PortLike | null = null;
const pendingPorts: PortLike[] = [];

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
      const svc = new HostService(
        { userData: m.userData, isPackaged: m.isPackaged, appPath: m.appPath, devRepoRoot: m.devRepoRoot },
        {},
        (reach) => parentPort.postMessage({ type: "reach", reach }),
      );
      void svc.start().then(() => {
        service = svc;
        parentPort.postMessage({ type: "host-ready", losslessJson: svc.losslessJson, dataRoot: svc.dataRoot });
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
  }
});
