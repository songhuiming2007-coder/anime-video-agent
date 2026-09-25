// 打包产物加固核验（Spec 8 §2.10、TS-2）：读已打包二进制的全部 9 位 fuse，并复算 app.asar 头哈希与 Info.plist 比对。
// 用法：node scripts/verify-fuses.mjs [<.app 路径>]（缺省 dist/mac-arm64/ava.app）。任何一项不符 → 退出 1。
// 最后一行输出 `VERIFY_FUSES <json>` 供 e2e-packaged 读取。
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { openSync, readSync, closeSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { FuseState, FuseV1Options, getCurrentFuseWire } from "@electron/fuses";

const desktop = join(dirname(fileURLToPath(import.meta.url)), "..");
const app = process.argv[2] ?? join(desktop, "dist/mac-arm64/ava.app");
const binary = join(app, "Contents/MacOS/ava");

// §2.10 表的 8 位（electron-builder.yml electronFuses 段）；WasmTrapHandlers 不由 electron-builder 设置，
// 期望值 = Electron 44.4.5 的出厂默认，2026-09-25 首次打包实测读出后写入（期望值先跑）。
const EXPECTED = {
  RunAsNode: FuseState.DISABLE,
  EnableCookieEncryption: FuseState.ENABLE,
  EnableNodeOptionsEnvironmentVariable: FuseState.DISABLE,
  EnableNodeCliInspectArguments: FuseState.DISABLE,
  EnableEmbeddedAsarIntegrityValidation: FuseState.ENABLE,
  OnlyLoadAppFromAsar: FuseState.ENABLE,
  LoadBrowserProcessSpecificV8Snapshot: FuseState.DISABLE,
  GrantFileProtocolExtraPrivileges: FuseState.ENABLE,
  WasmTrapHandlers: FuseState.ENABLE,
};

const name = (s) => FuseState[s] ?? `未知(${s})`;

/** asar 头：8 字节 size pickle（UInt32 payload 长度 + UInt32 头长度），随后是头 pickle（UInt32 payload 长度 + Int32 字符串长度 + 字符串） */
function asarHeaderString(file) {
  const fd = openSync(file, "r");
  try {
    const sizeBuf = Buffer.alloc(8);
    readSync(fd, sizeBuf, 0, 8, 0);
    const size = sizeBuf.readUInt32LE(4);
    const headerBuf = Buffer.alloc(size);
    readSync(fd, headerBuf, 0, size, 8);
    const len = headerBuf.readInt32LE(4);
    return headerBuf.subarray(8, 8 + len);
  } finally {
    closeSync(fd);
  }
}

const wire = await getCurrentFuseWire(binary);
const fuses = {};
const problems = [];
for (const [k, want] of Object.entries(EXPECTED)) {
  const got = wire[FuseV1Options[k]];
  fuses[k] = name(got);
  if (got !== want) problems.push(`${k}: 期望 ${name(want)}，实际 ${name(got)}`);
}

const plist = JSON.parse(execFileSync("/usr/bin/plutil", ["-convert", "json", "-o", "-", join(app, "Contents/Info.plist")], { encoding: "utf-8" }));
const integrity = plist.ElectronAsarIntegrity?.["Resources/app.asar"] ?? null;
const recomputed = createHash("sha256").update(asarHeaderString(join(app, "Contents/Resources/app.asar"))).digest("hex");
if (!integrity) problems.push("Info.plist 缺 ElectronAsarIntegrity[Resources/app.asar]");
else if (integrity.algorithm !== "SHA256" || integrity.hash !== recomputed) problems.push(`asar 头哈希不符：Info.plist ${integrity.hash}，复算 ${recomputed}`);

for (const [k, v] of Object.entries(fuses)) console.log(`${k.padEnd(40)} ${v}`);
console.log(`ElectronAsarIntegrity                    ${integrity ? integrity.hash : "缺失"}（复算 ${recomputed}）`);
for (const p of problems) console.error(`FAIL ${p}`);
console.log(`VERIFY_FUSES ${JSON.stringify({ ok: problems.length === 0, fuses, integrity: integrity?.hash ?? null, recomputed, problems })}`);
process.exit(problems.length === 0 ? 0 : 1);
