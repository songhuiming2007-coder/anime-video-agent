// TG-1~TG-9 静态守卫（Spec 8 §7.1）。每个检查器先用合成片段自测正反例，再扫真实源码。
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { METHODS } from "../../src/shared/protocol";
import {
  ACTION_METHODS,
  DESKTOP,
  RPC_CALL_EXEMPT,
  actionClickViolations,
  cardKeyViolations,
  forbiddenElectronApis,
  fsWriteCalls,
  importViolations,
  jsonStringifyCalls,
  markdownViolations,
  newEpisodeFormViolations,
  rpcCallViolations,
  sessionLogViolations,
  inboundFrameOwners,
  keychainTemplateViolations,
  sourceFiles,
  topDir,
  unguardedHooks,
  type SourceFile,
} from "./scan";

const files = sourceFiles();
const src = (rel: string, text: string): SourceFile => ({ rel, text });

describe("TG-1 npm 依赖精确白名单（§5.1）", () => {
  const pkg = JSON.parse(readFileSync(join(DESKTOP, "package.json"), "utf-8"));
  it("dependencies 恰为 react/react-dom/markdown-it，精确版本", () => {
    expect(pkg.dependencies).toEqual({ "markdown-it": "15.0.2", react: "19.3.0", "react-dom": "19.3.0" });
  });
  it("devDependencies 恰为 §2.1 其余各包 + 三个 @types", () => {
    expect(pkg.devDependencies).toEqual({
      "@electron/fuses": "2.1.3",
      "@playwright/test": "1.63.0",
      "@types/markdown-it": "14.2.0",
      "@types/react": "19.3.0",
      "@types/react-dom": "19.3.0",
      "@vitejs/plugin-react": "5.2.0",
      electron: "44.4.5",
      "electron-builder": "26.15.3",
      "electron-vite": "5.0.0",
      typescript: "5.9.3",
      vite: "7.3.6",
      vitest: "5.0.1",
    });
  });
  it("版本号全部是精确版本", () => {
    for (const v of Object.values({ ...pkg.dependencies, ...pkg.devDependencies })) expect(v).toMatch(/^\d+\.\d+\.\d+$/);
  });
  it("没有 optional/peer/bundled 依赖旁路", () => {
    for (const k of ["optionalDependencies", "peerDependencies", "bundleDependencies", "bundledDependencies", "overrides"]) {
      expect(pkg[k]).toBeUndefined();
    }
  });
});

describe("TG-2 fs 写类 API 只在 host/settings.ts", () => {
  it("检查器自测", () => {
    expect(fsWriteCalls(src("host/tailer.ts", "import * as fs from 'node:fs'; fs.mkdirSync('/x', { recursive: true });"))).toHaveLength(1);
    expect(fsWriteCalls(src("main/a.ts", "import { writeFileSync } from 'node:fs'; writeFileSync('/x', '');"))).toHaveLength(2);
    expect(fsWriteCalls(src("host/a.ts", "fs.openSync(p, 'w');"))).toHaveLength(1);
    expect(fsWriteCalls(src("host/a.ts", "fs.openSync(p, 'r'); fs.readFileSync(p); process.stdout.write('x');"))).toHaveLength(0);
  });
  it("真实源码", () => {
    const hits = files.filter((f) => f.rel !== "host/settings.ts").flatMap(fsWriteCalls);
    expect(hits).toEqual([]);
  });
});

describe("TG-3 §5.2 import 纪律", () => {
  it("检查器自测", () => {
    expect(importViolations(src("shared/a.ts", "import * as fs from 'node:fs';"))).toHaveLength(1);
    expect(importViolations(src("shared/a.ts", "const w = window.location;"))).toHaveLength(1);
    expect(importViolations(src("renderer/a.tsx", "import { ipcRenderer } from 'electron';"))).toHaveLength(1);
    expect(importViolations(src("renderer/a.tsx", "import '../host/service';"))).toHaveLength(1);
    expect(importViolations(src("host/a.ts", "import { spawn } from 'node:child_process';"))).toHaveLength(1);
    expect(importViolations(src("host/spawner.ts", "import { spawn } from 'node:child_process';"))).toHaveLength(0);
    expect(importViolations(src("host/a.ts", "import http from 'node:http';"))).toHaveLength(1);
    expect(importViolations(src("host/a.ts", "await fetch('https://x');"))).toHaveLength(1);
    expect(importViolations(src("host/a.ts", "import { app } from 'electron';"))).toHaveLength(1);
    expect(importViolations(src("main/a.ts", "import { spawn } from 'node:child_process';"))).toHaveLength(1);
    expect(importViolations(src("preload/a.ts", "import { ipcRenderer, shell } from 'electron';"))).toHaveLength(1);
    expect(importViolations(src("renderer/a.tsx", "import { useState } from 'react'; import '../shared/protocol';"))).toHaveLength(0);
  });
  it("真实源码", () => {
    expect(files.flatMap(importViolations)).toEqual([]);
  });
  it("源码只分布在五个约定目录", () => {
    const dirs = new Set(files.map((f) => topDir(f.rel)));
    for (const d of dirs) expect(["shared", "main", "preload", "host", "renderer"]).toContain(d);
  });
});

describe("TG-4′ 答复类方法只在人审卡片的 onClick 里（Spec 10 §2.4 第 1 层）", () => {
  const okHandler = `export const C = ({ rpc }) => <button onClick={(e) => { if (!e.nativeEvent.isTrusted) return; void rpc.call("approval.decide", p); }}>批准</button>;`;
  it("检查器自测：正例", () => {
    expect(actionClickViolations(src("renderer/HumanCards.tsx", okHandler))).toEqual([]);
  });
  it("检查器自测：反例（Spec 10 TG-4′ 逐条点名）", () => {
    const inEffect = `export const C = ({rpc}) => { useEffect(() => { rpc.call("approval.decide", p); }, []); return null; };`;
    expect(actionClickViolations(src("renderer/HumanCards.tsx", inEffect))).toHaveLength(1); // useEffect 里调用
    const inMap = `export const C = ({rpc, xs}) => <ul>{xs.map((x) => rpc.call("approval.decide", x))}</ul>;`;
    expect(actionClickViolations(src("renderer/HumanCards.tsx", inMap))).toHaveLength(1); // .map 回调里调用
    const named = `export const C = ({rpc, ids}) => <button onClick={(e) => { if (!e.nativeEvent.isTrusted) return; const a = (id) => rpc.call("approval.decide", id); ids.forEach(a); }}>批准</button>;`;
    expect(actionClickViolations(src("renderer/HumanCards.tsx", named)).length).toBeGreaterThanOrEqual(1); // 具名函数 + forEach
    const noGuard = `export const C = ({rpc}) => <button onClick={() => void rpc.call("approval.decide", p)}>批准</button>;`;
    expect(actionClickViolations(src("renderer/HumanCards.tsx", noGuard))).toHaveLength(1); // 无 isTrusted 首句
    const wrongVar = `export const C = ({rpc, other}) => <button onClick={(e) => { if (!other.nativeEvent.isTrusted) return; void rpc.call("approval.decide", p); }}>批准</button>;`;
    expect(actionClickViolations(src("renderer/HumanCards.tsx", wrongVar))).toHaveLength(1); // 首句检查的不是第一个形参
    const customTag = `export const C = ({rpc}) => <Button onClick={(e) => { if (!e.nativeEvent.isTrusted) return; void rpc.call("approval.decide", p); }}>批准</Button>;`;
    expect(actionClickViolations(src("renderer/HumanCards.tsx", customTag))).toHaveLength(1); // 挂在自定义组件上
    const twice = `export const C = ({rpc}) => <button onClick={(e) => { if (!e.nativeEvent.isTrusted) return; void rpc.call("approval.decide", a); void rpc.call("approval.decide", b); }}>批准</button>;`;
    expect(actionClickViolations(src("renderer/HumanCards.tsx", twice))).toHaveLength(1); // 同一处理器连写两处
    const loop = `export const C = ({rpc, xs}) => <button onClick={(e) => { if (!e.nativeEvent.isTrusted) return; for (const x of xs) void rpc.call("approval.decide", x); }}>批准</button>;`;
    expect(actionClickViolations(src("renderer/HumanCards.tsx", loop))).toHaveLength(1); // 处理器体内有循环
    expect(actionClickViolations(src("renderer/Other.tsx", okHandler))).toHaveLength(1); // 文件不符
  });
  it("真实源码：approval.decide 与 conv.answer 全部合规", () => {
    expect(files.filter((f) => topDir(f.rel) === "renderer").flatMap(actionClickViolations)).toEqual([]);
  });
  it("表里点名的文件必须存在（否则守卫悄悄失明）", () => {
    for (const spec of Object.values(ACTION_METHODS)) {
      expect(files.some((f) => f.rel === spec.file), spec.file).toBe(true);
    }
  });
});

describe("TG-10 发送 / 建期类方法只在各自组件的点击处理器里（Spec 10 §2.4 第 1 层）", () => {
  it("检查器自测：onKeyDown 与 episode.create", () => {
    const send = `export const C = ({rpc}) => <textarea onKeyDown={(e) => { if (!e.nativeEvent.isTrusted) return; void rpc.call("conv.send", { text }); }} />;`;
    expect(actionClickViolations(src("renderer/Composer.tsx", send))).toEqual([]);
    const create = `export const C = ({rpc}) => <button onClick={(e) => { if (!e.nativeEvent.isTrusted) return; void rpc.call("episode.create", { name }); }}>建期</button>;`;
    expect(actionClickViolations(src("renderer/NewEpisodeForm.tsx", create))).toEqual([]);
    expect(actionClickViolations(src("renderer/Composer.tsx", create))).toHaveLength(1); // episode.create 放错了文件
  });
  it("真实源码：conv.* 与 episode.create 全部合规", () => {
    expect(files.flatMap(actionClickViolations)).toEqual([]);
  });
});

describe("TG-13 只有 PreviewPane.tsx 能渲染 Markdown 与危险 HTML（§7.1）", () => {
  it("检查器自测", () => {
    expect(markdownViolations(src("renderer/App.tsx", "import MarkdownIt from 'markdown-it';"))).toHaveLength(1);
    expect(markdownViolations(src("renderer/App.tsx", "el.innerHTML = 1; dangerouslySetInnerHTML={{ __html: x }};"))).toHaveLength(1);
    expect(markdownViolations(src("renderer/PreviewPane.tsx", "import MarkdownIt from 'markdown-it'; dangerouslySetInnerHTML={{ __html: x }};"))).toEqual([]);
  });
  it("真实源码", () => {
    expect(files.flatMap(markdownViolations)).toEqual([]);
  });
});

describe("TG-14 桌面端不读会话记录（红线 3）", () => {
  it("desktop/src 全文没有 session.jsonl 字面量", () => {
    expect(sessionLogViolations()).toEqual([]);
  });
});

describe("TG-15 建期表单的期名不从对话预填（§2.5）", () => {
  it("检查器自测", () => {
    const ok = `export function NewEpisodeForm({ rpc, onCreated }: { rpc: RpcClient; onCreated: (epKey: string) => void }) { const [name, setName] = useState(""); return null; }`;
    expect(newEpisodeFormViolations(src("renderer/NewEpisodeForm.tsx", ok))).toEqual([]);
    const prefilled = `export function NewEpisodeForm({ rpc, suggested }: { rpc: RpcClient; suggested: string }) { const [name, setName] = useState(""); return null; }`;
    expect(newEpisodeFormViolations(src("renderer/NewEpisodeForm.tsx", prefilled))).toHaveLength(1); // string prop
    const fromProp = `export function NewEpisodeForm({ rpc, initial }: { rpc: RpcClient; initial: string }) { const [name, setName] = useState(initial); return null; }`;
    expect(newEpisodeFormViolations(src("renderer/NewEpisodeForm.tsx", fromProp)).length).toBeGreaterThanOrEqual(1);
  });
  it("真实源码：初值为空串字面量、无 string 型 props", () => {
    expect(files.filter((f) => f.rel === "renderer/NewEpisodeForm.tsx").flatMap(newEpisodeFormViolations)).toEqual([]);
  });
});

describe("TG-16 rpc.call 的首参必须是方法闭集里的字符串字面量（🔵-3）", () => {
  it("检查器自测", () => {
    expect(rpcCallViolations(src("renderer/App.tsx", 'rpc.call("app.health");'), METHODS)).toEqual([]);
    expect(rpcCallViolations(src("renderer/App.tsx", 'const m = "conv.send"; rpc.call(m, {});'), METHODS)).toHaveLength(1);
    expect(rpcCallViolations(src("renderer/App.tsx", "rpc.call.bind(rpc);"), METHODS)).toHaveLength(1);
    expect(rpcCallViolations(src("renderer/App.tsx", "rpc.call.apply(rpc, a);"), METHODS)).toHaveLength(1);
    expect(rpcCallViolations(src("renderer/App.tsx", "const c = rpc.call;"), METHODS)).toHaveLength(1);
    expect(rpcCallViolations(src("renderer/App.tsx", 'rpc.call("no.such");'), METHODS)).toHaveLength(1);
    expect(rpcCallViolations(src(RPC_CALL_EXEMPT, "rpc.call(m, a);"), METHODS)).toEqual([]);
  });
  it("真实源码（renderer/，只豁免 testHooks.ts）", () => {
    expect(files.filter((f) => topDir(f.rel) === "renderer").flatMap((f) => rpcCallViolations(f, METHODS))).toEqual([]);
  });
});

describe("TG-17 卡片的 key（🟡-2 d）", () => {
  it("检查器自测", () => {
    const ok = `export const D = ({ rpc, objs }) => <>{objs.map((o) => <StopPointCard key={o.approval_id} obj={o} />)}</>;`;
    expect(cardKeyViolations(src("renderer/HumanCards.tsx", ok))).toEqual([]);
    const byIndex = `export const D = ({ objs }) => <>{objs.map((o, i) => <StopPointCard key={i} obj={o} />)}</>;`;
    expect(cardKeyViolations(src("renderer/HumanCards.tsx", byIndex))).toHaveLength(1);
    const noKey = `export const D = ({ objs }) => <>{objs.map((o) => <RequestCard obj={o} />)}</>;`;
    expect(cardKeyViolations(src("renderer/HumanCards.tsx", noKey))).toHaveLength(1);
  });
  it("真实源码", () => {
    expect(files.flatMap(cardKeyViolations)).toEqual([]);
  });
});

describe("TG-11 入站帧的构造点只在 host/sessions.ts，且每种恰 1 处（Spec 10 §2.4 第 1 层）", () => {
  it("真实源码", () => {
    const hits = inboundFrameOwners(files);
    expect(hits.map((h) => h.file)).toEqual(hits.map(() => "host/sessions.ts"));
    const counts = new Map<string, number>();
    for (const h of hits) counts.set(h.t, (counts.get(h.t) ?? 0) + 1);
    for (const t of ["user_message", "answer", "interrupt", "command", "shutdown"]) expect(counts.get(t), t).toBe(1);
  });
  it("合成片段：另一文件里的入站帧被点名", () => {
    expect(inboundFrameOwners([src("renderer/App.tsx", 'const f = { v: 1, t: "user_message", text: "x" };')])).toEqual([{ file: "renderer/App.tsx", t: "user_message" }]);
  });
});

describe("TG-12 KEYCHAIN_READ 只在 spawner.ts 与 secrets.ts（Spec 10 §2.9）", () => {
  it("真实源码", () => {
    expect(keychainTemplateViolations(files)).toEqual([]);
    expect(files.some((f) => f.rel === "host/spawner.ts" && f.text.includes("KEYCHAIN_READ"))).toBe(true);
    expect(files.some((f) => f.rel === "host/secrets.ts" && f.text.includes("KEYCHAIN_READ"))).toBe(true);
  });
  it("合成片段", () => {
    expect(keychainTemplateViolations([src("renderer/App.tsx", 'const t = "KEYCHAIN_READ";')])).toHaveLength(1);
  });
});

describe("TG-5 electron-builder.yml 冻结项（§2.10）", () => {
  // 用 electron-builder 自己加载配置时用的 YAML 解析器，测到的就是它看到的
  const req = createRequire(require.resolve("app-builder-lib/package.json"));
  const yaml = req("js-yaml") as { load: (s: string) => unknown };
  const text = readFileSync(join(DESKTOP, "electron-builder.yml"), "utf-8");
  const cfg = yaml.load(text) as Record<string, any>;
  it("asar 完整性不被关闭", () => {
    expect(cfg.asar).toBe(true);
    expect(text).not.toMatch(/disableAsarIntegrity/);
  });
  it("publish: null（红线 6）", () => {
    expect(Object.prototype.hasOwnProperty.call(cfg, "publish")).toBe(true);
    expect(cfg.publish).toBeNull();
  });
  it("mac.identity: null、mac.target 仅 dir", () => {
    expect(Object.prototype.hasOwnProperty.call(cfg.mac, "identity")).toBe(true);
    expect(cfg.mac.identity).toBeNull();
    expect([cfg.mac.target].flat()).toEqual(["dir"]);
  });
  it("electronFuses 与 §2.10 表逐项一致", () => {
    expect(cfg.electronFuses).toEqual({
      runAsNode: false,
      enableCookieEncryption: true,
      enableNodeOptionsEnvironmentVariable: false,
      enableNodeCliInspectArguments: false,
      enableEmbeddedAsarIntegrityValidation: true,
      onlyLoadAppFromAsar: true,
      loadBrowserProcessSpecificV8Snapshot: false,
      grantFileProtocolExtraPrivileges: true,
      resetAdHocDarwinSignature: true,
    });
  });
});

describe("TG-6 测试开关与测试钩子被 isPackaged 守卫", () => {
  it("检查器自测", () => {
    expect(unguardedHooks(src("main/a.ts", `if (!app.isPackaged) { for (const a of argv) if (a === "--ava-x") f(); }`))).toEqual([]);
    expect(unguardedHooks(src("main/a.ts", `for (const a of argv) if (a === "--ava-x") f();`))).toHaveLength(1);
    expect(unguardedHooks(src("main/a.ts", `if (!app.isPackaged && sw.handshakeHook) later();`))).toEqual([]);
    expect(unguardedHooks(src("main/a.ts", `if (sw.handshakeHook) later();`))).toHaveLength(1);
    expect(unguardedHooks(src("host/a.ts", `function run(){ testHook("after-heal"); }`))).toHaveLength(1);
    expect(unguardedHooks(src("host/a.ts", `function testHook(n: string){ if (cfg.isPackaged) return; __avaTestPause(n); }`))).toEqual([]);
    expect(unguardedHooks(src("renderer/a.tsx", `if (!health.isPackaged) installTestHooks();`))).toEqual([]);
  });
  it("真实源码（main/、host/、renderer/）", () => {
    expect(files.filter((f) => ["main", "host", "renderer"].includes(topDir(f.rel))).flatMap(unguardedHooks)).toEqual([]);
  });
});

describe("TG-7 解封物字面量（§6.5）", () => {
  it("desktop/src 不含 02-diff.patch；04-clips.approved.json 只在 host/gateCheck.ts", () => {
    for (const f of files) {
      expect(f.text.includes("02-diff.patch"), f.rel).toBe(false);
      if (f.rel !== "host/gateCheck.ts") expect(f.text.includes("04-clips.approved.json"), f.rel).toBe(false);
    }
  });
});

describe("TG-8 不启用崩溃上报与自动更新", () => {
  it("检查器自测", () => {
    expect(forbiddenElectronApis(src("main/a.ts", "crashReporter.start({ submitURL: '' });"))).toHaveLength(1);
    expect(forbiddenElectronApis(src("main/a.ts", "import { autoUpdater } from 'electron';"))).toHaveLength(1);
  });
  it("真实源码", () => {
    expect(files.flatMap(forbiddenElectronApis)).toEqual([]);
  });
});

describe("TG-9 host/ 与 shared/ 的 JSON.stringify 只在 shared/losslessJson.ts", () => {
  it("检查器自测", () => {
    expect(jsonStringifyCalls(src("host/a.ts", "diag(JSON.stringify(rec));"))).toHaveLength(1);
    expect(jsonStringifyCalls(src("host/a.ts", "diag(stringifyLossless(rec));"))).toHaveLength(0);
  });
  it("真实源码", () => {
    const hits = files
      .filter((f) => ["host", "shared"].includes(topDir(f.rel)) && f.rel !== "shared/losslessJson.ts")
      .flatMap(jsonStringifyCalls);
    expect(hits).toEqual([]);
    expect(jsonStringifyCalls(files.find((f) => f.rel === "shared/losslessJson.ts")!)).toHaveLength(1);
  });
});
