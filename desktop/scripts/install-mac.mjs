// 本机安装与稳定重签脚本：保持 electron-builder.yml（Spec 8 §2.10 / TG-5）零改动，
// 负责在 login keychain 初始化专用自签名代码签名证书「AVA Local Dev」，并把打包产物
// dist/mac-arm64/AVA.app 由内而外重签后安装到 /Applications/AVA.app，消除重建后 CDHash 漂移。
import { execFileSync, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import {
  closeSync,
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readdirSync,
  readSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { basename, dirname, isAbsolute, join, relative, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const DEFAULT_IDENTITY = "AVA Local Dev";
export const DEFAULT_INSTALL_PATH = "/Applications/AVA.app";
export const BUNDLE_ID = "local.ava.desktop";

const DESKTOP = join(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE_APP = join(DESKTOP, "dist/mac-arm64/AVA.app");
const VERIFY_FUSES = join(DESKTOP, "scripts/verify-fuses.mjs");
const LOGIN_KEYCHAIN = join(homedir(), "Library/Keychains/login.keychain-db");

const MACHO_MAGICS = new Set([
  0xcffaedfe, // MH_CIGAM_64
  0xfeedfacf, // MH_MAGIC_64
  0xcefaedfe, // MH_CIGAM
  0xfeedface, // MH_MAGIC
  0xbebafeca, // FAT_CIGAM
  0xcafebabe, // FAT_MAGIC
]);

export function parseIdentities(stdout) {
  const out = [];
  for (const line of String(stdout).split(/\r?\n/)) {
    const m = /^\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"([^"]+)"/.exec(line);
    if (m) out.push({ sha1: m[1].toUpperCase(), name: m[2] });
  }
  return out;
}

export function resolveIdentitySha1(identities, query) {
  const q = String(query ?? "").trim();
  if (!q) throw new Error("签名身份不能为空（例如「AVA Local Dev」或 40 位 SHA-1 指纹）");

  const isSha1 = /^[0-9A-Fa-f]{40}$/.test(q);
  const matched = isSha1
    ? identities.filter((id) => id.sha1.toUpperCase() === q.toUpperCase())
    : identities.filter((id) => id.name === q);

  const uniqueSha1s = [...new Set(matched.map((id) => id.sha1.toUpperCase()))];
  if (uniqueSha1s.length === 0) {
    throw new Error(
      `钥匙串里找不到可用的代码签名证书「${q}」：请先运行 \`cd desktop && npm run init-cert\` 创建本地签名证书，或通过 AVA_SIGN_IDENTITY 指定已有证书。`,
    );
  }
  if (uniqueSha1s.length > 1) {
    throw new Error(
      `钥匙串里存在多个同名代码签名证书「${q}」（${uniqueSha1s.join(" / ")}）：请通过 AVA_SIGN_IDENTITY=<40位SHA1> 明确指定要使用的指纹。`,
    );
  }
  return uniqueSha1s[0];
}

export function validateDesignatedRequirement(drOutput, bundleId = BUNDLE_ID) {
  const lines = String(drOutput)
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);
  const drLine = lines.find((l) => l.includes("designated =>")) ?? lines.join("\n");
  if (!drLine.includes(`identifier "${bundleId}"`)) {
    throw new Error(`Designated Requirement 缺少 identifier "${bundleId}"：${drLine || "(空)"}`);
  }
  if (!drLine.includes("certificate leaf")) {
    throw new Error(`Designated Requirement 未绑定证书叶子节点（certificate leaf）：${drLine || "(空)"}`);
  }
  if (/cdhash/i.test(drLine)) {
    throw new Error(`Designated Requirement 仍退回 cdhash，重签后 TCC 还会漂移：${drLine}`);
  }
  return drLine;
}

export function isMachOBuffer(buf) {
  if (!buf || buf.length < 4) return false;
  return MACHO_MAGICS.has(buf.readUInt32BE(0));
}

export function validateInstallPath(targetPath) {
  const p = String(targetPath ?? "").trim();
  if (!isAbsolute(p)) {
    throw new Error(`安装路径必须是绝对路径：${p || "(空)"}`);
  }
  if (!p.endsWith(".app") || basename(p) === ".app" || p === "/") {
    throw new Error(`安装路径必须指向明确的 .app 包（例如 /Applications/AVA.app）：${p}`);
  }
  return p;
}

function readHead4(filePath) {
  const fd = openSync(filePath, "r");
  try {
    const buf = Buffer.alloc(4);
    const n = readSync(fd, buf, 0, 4, 0);
    return buf.subarray(0, n);
  } finally {
    closeSync(fd);
  }
}

function isBundleMainExecutable(filePath, frameworksDir) {
  const rel = relative(frameworksDir, filePath);
  const parts = rel.split(sep);
  const name = parts[parts.length - 1];
  // `<Helper>.app/Contents/MacOS/<exec>`：由外层 `<Helper>.app` 签名一并密封
  if (
    parts.length >= 4 &&
    parts[parts.length - 2] === "MacOS" &&
    parts[parts.length - 3] === "Contents" &&
    parts[parts.length - 4].endsWith(".app")
  ) {
    return true;
  }
  // `<Name>.framework/Versions/<ver>/<Name>`：由 `<Name>.framework` 签名一并密封
  if (
    parts.length >= 4 &&
    parts[parts.length - 3] === "Versions" &&
    parts[parts.length - 4] === `${name}.framework`
  ) {
    return true;
  }
  return false;
}

function depthDesc(a, b) {
  const da = a.split(sep).length;
  const db = b.split(sep).length;
  return db - da || a.localeCompare(b);
}

/**
 * 由内而外收集要签名的路径清单（不跟随软链接）：
 * 1) Frameworks 内嵌套的独立 Mach-O（dylib、crashpad_handler、ShipIt 等）；
 * 2) *.framework 包；
 * 3) *.app 辅助包；
 * 4) 外层主 .app 包。
 */
export function collectSignTargets(appPath) {
  const frameworksDir = join(appPath, "Contents/Frameworks");
  const machoFiles = [];
  const frameworks = [];
  const helperApps = [];

  if (existsSync(frameworksDir) && lstatSync(frameworksDir).isDirectory()) {
    const walk = (dir) => {
      for (const entry of readdirSync(dir)) {
        const full = join(dir, entry);
        const st = lstatSync(full);
        if (st.isSymbolicLink()) continue; // 严禁跟随 Frameworks/Versions/Current 等软链接
        if (st.isDirectory()) {
          if (entry === "_CodeSignature" || entry.endsWith(".lproj")) continue;
          if (entry.endsWith(".framework")) frameworks.push(full);
          else if (entry.endsWith(".app")) helperApps.push(full);
          walk(full);
        } else if (st.isFile() && st.size >= 4) {
          if (!isBundleMainExecutable(full, frameworksDir) && isMachOBuffer(readHead4(full))) {
            machoFiles.push(full);
          }
        }
      }
    };
    walk(frameworksDir);
  }

  machoFiles.sort(depthDesc);
  frameworks.sort(depthDesc);
  helperApps.sort(depthDesc);
  return [...machoFiles, ...frameworks, ...helperApps, appPath];
}

function runExec(cmd, args) {
  return execFileSync(cmd, args, { encoding: "utf-8" });
}

function findValidIdentities(keychainPath) {
  const args = ["find-identity", "-v", "-p", "codesigning"];
  if (keychainPath) args.push(keychainPath);
  return parseIdentities(runExec("/usr/bin/security", args));
}

function resolveIdentity(identityQuery) {
  const loginIds = existsSync(LOGIN_KEYCHAIN) ? findValidIdentities(LOGIN_KEYCHAIN) : [];
  const allIds = findValidIdentities();
  const candidates = loginIds.length > 0 ? [...loginIds, ...allIds] : allIds;
  return resolveIdentitySha1(candidates, identityQuery);
}

function initCertificate(identityName) {
  if (/^[0-9A-Fa-f]{40}$/.test(identityName)) {
    throw new Error("--init-cert 需要传入证书名称（例如「AVA Local Dev」），不能传入 SHA-1 指纹");
  }
  const existing = existsSync(LOGIN_KEYCHAIN) ? findValidIdentities(LOGIN_KEYCHAIN) : [];
  if (existing.some((id) => id.name === identityName)) {
    const sha1 = resolveIdentitySha1(existing, identityName);
    console.log(`已存在有效本地签名证书「${identityName}」(${sha1})，无需重复创建。`);
    return;
  }

  const tmpDir = mkdtempSync(join(tmpdir(), "ava-local-cert-"));
  try {
    const confPath = join(tmpDir, "openssl.cnf");
    const keyPath = join(tmpDir, "key.pem");
    const certPath = join(tmpDir, "cert.pem");
    const p12Path = join(tmpDir, "identity.p12");
    const pass = randomBytes(16).toString("hex");

    writeFileSync(
      confPath,
      [
        "[req]",
        "distinguished_name = dn",
        "x509_extensions = v3_codesign",
        "prompt = no",
        "",
        "[dn]",
        `CN = ${identityName}`,
        "",
        "[v3_codesign]",
        "basicConstraints = critical,CA:FALSE",
        "keyUsage = critical,digitalSignature",
        "extendedKeyUsage = critical,codeSigning",
        "",
      ].join("\n"),
      { mode: 0o600 },
    );

    runExec("/usr/bin/openssl", [
      "req",
      "-x509",
      "-newkey",
      "rsa:2048",
      "-nodes",
      "-sha256",
      "-days",
      "3650",
      "-config",
      confPath,
      "-keyout",
      keyPath,
      "-out",
      certPath,
    ]);

    runExec("/usr/bin/openssl", [
      "pkcs12",
      "-export",
      "-inkey",
      keyPath,
      "-in",
      certPath,
      "-out",
      p12Path,
      "-passout",
      `pass:${pass}`,
    ]);

    runExec("/usr/bin/security", [
      "import",
      p12Path,
      "-k",
      LOGIN_KEYCHAIN,
      "-P",
      pass,
      "-T",
      "/usr/bin/codesign",
      "-T",
      "/usr/bin/security",
    ]);

    console.log(`即将在钥匙串写入「${identityName}」的代码签名信任设置，如有系统授权弹窗请点击允许 / Touch ID。`);
    runExec("/usr/bin/security", [
      "add-trusted-cert",
      "-r",
      "trustRoot",
      "-p",
      "codeSign",
      "-k",
      LOGIN_KEYCHAIN,
      certPath,
    ]);

    const sha1 = resolveIdentitySha1(findValidIdentities(LOGIN_KEYCHAIN), identityName);
    console.log(`本地代码签名证书就绪：「${identityName}」SHA-1 = ${sha1}`);
  } finally {
    rmSync(tmpDir, { recursive: true, force: true });
  }
}

function assertNotRunning(targetApp) {
  const execPrefix = join(targetApp, "Contents/MacOS/");
  const res = spawnSync("/usr/bin/pgrep", ["-f", execPrefix], { encoding: "utf-8" });
  if (res.status === 0) {
    throw new Error(`检测到 ${targetApp} 正在运行：请先按 Cmd+Q 完全退出后再执行安装，避免覆盖运行中的二进制。`);
  }
}

function installSignedApp(identityQuery, rawTargetPath, { keepDist = false } = {}) {
  const targetApp = validateInstallPath(rawTargetPath);
  if (!existsSync(join(SOURCE_APP, "Contents/MacOS"))) {
    throw new Error(`未找到打包产物 ${SOURCE_APP}：请先运行 \`npm run release-build\`。`);
  }

  // 复制前先确认源包 fuses 与 asar 头完好
  runExec(process.execPath, [VERIFY_FUSES, SOURCE_APP]);
  assertNotRunning(targetApp);
  assertNotRunning(SOURCE_APP);

  const sha1 = resolveIdentity(identityQuery);
  const parentDir = dirname(targetApp);
  mkdirSync(parentDir, { recursive: true });
  const appName = basename(targetApp);
  // 目录名保留外层隐藏暂存目录，内层包名必须保持 `<App>.app`，否则 @electron/fuses 的 `/\.app[\\/]Contents[\\/]MacOS[\\/]/` 不匹配
  const stagingDir = join(parentDir, `.ava-staging-${process.pid}`);
  const backupDir = join(parentDir, `.ava-backup-${process.pid}`);
  const stagedApp = join(stagingDir, appName);
  const backupApp = join(backupDir, appName);

  try {
    rmSync(stagingDir, { recursive: true, force: true });
    mkdirSync(stagingDir, { recursive: true });
    runExec("/usr/bin/ditto", [SOURCE_APP, stagedApp]);

    const targets = collectSignTargets(stagedApp);
    for (const item of targets) {
      runExec("/usr/bin/codesign", ["--force", "--sign", sha1, "--timestamp=none", item]);
    }

    runExec("/usr/bin/codesign", ["--verify", "--deep", "--strict", stagedApp]);
    const drProc = spawnSync("/usr/bin/codesign", ["-d", "-r", "-", stagedApp], { encoding: "utf-8" });
    if (drProc.status !== 0) {
      throw new Error(`读取签名要求失败：${drProc.stderr || drProc.stdout}`);
    }
    const drLine = validateDesignatedRequirement(`${drProc.stdout}\n${drProc.stderr}`);

    // 重签后再次核验 9 位 Electron fuses 与 ElectronAsarIntegrity
    runExec(process.execPath, [VERIFY_FUSES, stagedApp]);

    rmSync(backupDir, { recursive: true, force: true });
    const hadPrev = existsSync(targetApp);
    if (hadPrev) {
      mkdirSync(backupDir, { recursive: true });
      renameSync(targetApp, backupApp);
    }
    try {
      renameSync(stagedApp, targetApp);
    } catch (err) {
      if (hadPrev && existsSync(backupApp) && !existsSync(targetApp)) {
        renameSync(backupApp, targetApp);
      }
      throw err;
    }
    if (hadPrev) rmSync(backupDir, { recursive: true, force: true });

    if (!keepDist) {
      rmSync(SOURCE_APP, { recursive: true, force: true });
      console.log(`已清理 ad-hoc 构建副本：${SOURCE_APP}（避免 Spotlight / Dock 误启动旧包）`);
    }

    console.log(`已安装稳定签名版 AVA 到：${targetApp}`);
    console.log(`签名身份：${identityQuery} (${sha1})`);
    console.log(`签名要求：${drLine}`);
    console.log("提示：首次安装后只需在「系统设置 → 隐私与安全性」对 /Applications/AVA.app 放行一次，后续重新打包运行 `npm run release-install` 不再失效。");
  } finally {
    rmSync(stagingDir, { recursive: true, force: true });
  }
}

function main() {
  if (process.platform !== "darwin") {
    throw new Error("install-mac.mjs 仅支持 macOS");
  }
  const args = new Set(process.argv.slice(2));
  const identity = process.env.AVA_SIGN_IDENTITY || DEFAULT_IDENTITY;
  const targetPath = process.env.AVA_INSTALL_PATH || DEFAULT_INSTALL_PATH;

  if (args.has("--init-cert")) {
    initCertificate(identity);
    return;
  }
  installSignedApp(identity, targetPath, { keepDist: args.has("--keep-dist") });
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    main();
  } catch (err) {
    console.error(`FAIL ${err instanceof Error ? err.message : String(err)}`);
    process.exit(1);
  }
}
