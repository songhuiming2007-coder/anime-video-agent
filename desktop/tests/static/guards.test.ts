// TG-1~TG-9 静态守卫（Spec 8 §7.1）。每个检查器先用合成片段自测正反例，再扫真实源码。
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  DECISION_BAR_FILE,
  DESKTOP,
  decideCallViolations,
  forbiddenElectronApis,
  fsWriteCalls,
  importViolations,
  jsonStringifyCalls,
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

describe("TG-4 approval.decide 只在决策条组件的 onClick 里", () => {
  it("检查器自测", () => {
    const ok = `export function B(){ return <button onClick={() => rpc.call("approval.decide", p)}>批准</button>; }`;
    const inEffect = `useEffect(() => { rpc.call("approval.decide", p); }, []);`;
    expect(decideCallViolations(src(DECISION_BAR_FILE, ok))).toEqual([]);
    expect(decideCallViolations(src(DECISION_BAR_FILE, inEffect))).toHaveLength(1);
    expect(decideCallViolations(src("renderer/Preview.tsx", ok))).toHaveLength(1);
  });
  it("真实源码", () => {
    expect(files.filter((f) => topDir(f.rel) === "renderer").flatMap(decideCallViolations)).toEqual([]);
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
      grantFileProtocolExtraPrivileges: false,
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
