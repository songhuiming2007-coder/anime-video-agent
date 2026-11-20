// macOS 本地安装与由内而外重签脚本纯函数单测（不触及真实钥匙串或 /Applications）。
import { chmodSync, mkdirSync, symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  collectSignTargets,
  isMachOBuffer,
  parseIdentities,
  resolveIdentitySha1,
  validateDesignatedRequirement,
  validateInstallPath,
} from "../../scripts/install-mac.mjs";
import { tmp } from "../helpers";

describe("install-mac 纯函数与由内而外签名收集", () => {
  it("parseIdentities 与 resolveIdentitySha1：唯一匹配、重复去重、指纹直连与冲突报错", () => {
    const raw = [
      '  1) AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "AVA Local Dev"',
      '  2) AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "AVA Local Dev"',
      '  3) bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb "SmartEnglish Dev"',
      '     3 valid identities found',
    ].join("\n");
    const ids = parseIdentities(raw);
    expect(ids).toHaveLength(3);
    expect(resolveIdentitySha1(ids, "AVA Local Dev")).toBe("A".repeat(40));
    expect(resolveIdentitySha1(ids, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")).toBe("B".repeat(40));
    expect(() => resolveIdentitySha1(ids, "Missing Cert")).toThrow(/init-cert/);

    const conflict = [
      ...ids,
      { sha1: "C".repeat(40), name: "AVA Local Dev" },
    ];
    expect(() => resolveIdentitySha1(conflict, "AVA Local Dev")).toThrow(/AVA_SIGN_IDENTITY/);
  });

  it("validateDesignatedRequirement：放行基于 bundleId+证书叶的 DR，拒收 cdhash", () => {
    const good = 'Executable=/Applications/AVA.app/Contents/MacOS/AVA\n# designated => identifier "local.ava.desktop" and certificate leaf = H"0123abcd"';
    expect(validateDesignatedRequirement(good)).toContain('identifier "local.ava.desktop"');

    expect(() => validateDesignatedRequirement('# designated => cdhash H"deadbeef"')).toThrow(/identifier/);
    expect(() =>
      validateDesignatedRequirement('# designated => identifier "local.ava.desktop" and certificate leaf = H"11" and cdhash H"22"'),
    ).toThrow(/cdhash/);
    expect(() => validateDesignatedRequirement('# designated => identifier "other.app" and certificate leaf = H"11"')).toThrow(
      /local\.ava\.desktop/,
    );
  });

  it("isMachOBuffer 与 validateInstallPath：魔数及目标路径校验", () => {
    expect(isMachOBuffer(Buffer.from([0xcf, 0xfa, 0xed, 0xfe, 0x0c, 0x00]))).toBe(true);
    expect(isMachOBuffer(Buffer.from([0xca, 0xfe, 0xba, 0xbe]))).toBe(true);
    expect(isMachOBuffer(Buffer.from("plain text"))).toBe(false);
    expect(isMachOBuffer(Buffer.from([0xcf, 0xfa]))).toBe(false);

    expect(validateInstallPath("/Applications/AVA.app")).toBe("/Applications/AVA.app");
    expect(() => validateInstallPath("AVA.app")).toThrow(/绝对路径/);
    expect(() => validateInstallPath("/Applications/AVA")).toThrow(/\.app/);
    expect(() => validateInstallPath("/Applications/.app")).toThrow(/\.app/);
  });

  it("collectSignTargets：按由内而外顺序收集，跳过软链接与包主可执行文件", () => {
    const root = tmp("install-mac-targets");
    const app = join(root, "AVA.app");
    const fwRoot = join(app, "Contents/Frameworks");
    const efVerA = join(fwRoot, "Electron Framework.framework/Versions/A");
    const helperDir = join(fwRoot, "AVA Helper.app/Contents/MacOS");

    mkdirSync(join(efVerA, "Libraries"), { recursive: true });
    mkdirSync(join(efVerA, "Helpers"), { recursive: true });
    mkdirSync(join(efVerA, "Resources/en.lproj"), { recursive: true });
    mkdirSync(helperDir, { recursive: true });
    mkdirSync(join(app, "Contents/MacOS"), { recursive: true });

    const machoHeader = Buffer.from([0xcf, 0xfa, 0xed, 0xfe, 0x0c, 0x00, 0x00, 0x01]);
    // 应被跳过的包主可执行文件（外层 .framework / .app 签名会一并覆盖）
    writeFileSync(join(efVerA, "Electron Framework"), machoHeader);
    writeFileSync(join(helperDir, "AVA Helper"), machoHeader);
    writeFileSync(join(app, "Contents/MacOS/AVA"), machoHeader);
    // 非 Mach-O 数据文件应被忽略
    writeFileSync(join(efVerA, "Resources/en.lproj/locale.pak"), Buffer.from("not-macho"));
    // 嵌套独立 Mach-O 二进制
    const dylib = join(efVerA, "Libraries/libffmpeg.dylib");
    const crashpad = join(efVerA, "Helpers/chrome_crashpad_handler");
    writeFileSync(dylib, machoHeader);
    writeFileSync(crashpad, machoHeader);
    chmodSync(crashpad, 0o755);

    // 软链接必须忽略，不得重复进队列
    symlinkSync("A", join(fwRoot, "Electron Framework.framework/Versions/Current"));
    symlinkSync("Versions/Current/Electron Framework", join(fwRoot, "Electron Framework.framework/Electron Framework"));

    const targets = collectSignTargets(app);
    expect(targets).toEqual([
      crashpad,
      dylib,
      join(fwRoot, "Electron Framework.framework"),
      join(fwRoot, "AVA Helper.app"),
      app,
    ]);
  });
});
