// 构建溯源（Spec 8 §2.10）：把 { gitHead, desktopDirty, lockfileSha256, builtAt } 写入 out/build-info.json，
// 随 asar 一起受完整性保护。健康面板据此给出红/黄/灰三色横幅。git 不可用时构建失败，不写半截信息。
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const desktop = join(dirname(fileURLToPath(import.meta.url)), "..");
const git = (...args) => execFileSync("/usr/bin/git", ["-C", desktop, ...args], { encoding: "utf-8" });

const gitHead = git("rev-parse", "HEAD").trim();
// desktop/ 下任何未提交改动（含未跟踪文件；node_modules/out/dist 已被 .gitignore 排除）
const desktopDirty = git("status", "--porcelain", "--", ".").trim() !== "";
const lockfileSha256 = createHash("sha256").update(readFileSync(join(desktop, "package-lock.json"))).digest("hex");
const info = { gitHead, desktopDirty, lockfileSha256, builtAt: new Date().toISOString() };

mkdirSync(join(desktop, "out"), { recursive: true });
writeFileSync(join(desktop, "out/build-info.json"), `${JSON.stringify(info, null, 2)}\n`);
console.log(`build-info: ${gitHead.slice(0, 12)} dirty=${desktopDirty}`);
