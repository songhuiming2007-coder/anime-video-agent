// data/ 可达性诊断（Spec 8 §2.8）：镜像 paths.require_data 的三分，另加 GUI 特有的 TCC 拒绝。
// 绝不 mkdir、绝不写（I1）。
import type { Reach } from "../shared/contracts";
import { errnoOf, type FsProbe } from "./fsio";

export type { Reach };

const DENIED = new Set(["EPERM", "EACCES"]);

export const PERMISSION_HINT =
  "macOS 未授权本 app 访问可移除卷（系统设置 → 隐私与安全性 → 文件与文件夹）；每次重新构建 app 后可能需要重新授权";

export function diagnoseDataRoot(dataRoot: string, fs: FsProbe): { reach: Reach; detail: string } {
  let isLink = false;
  try {
    isLink = fs.lstat(dataRoot).isSymbolicLink();
  } catch (e) {
    const code = errnoOf(e);
    if (code && DENIED.has(code)) return { reach: "permission-denied", detail: PERMISSION_HINT };
    return { reach: "missing", detail: "data/ 不存在，先跑 ./pipeline/preflight.sh --init" };
  }
  try {
    fs.stat(dataRoot);
  } catch (e) {
    const code = errnoOf(e);
    if (code && DENIED.has(code)) return { reach: "permission-denied", detail: PERMISSION_HINT };
    if (isLink) {
      let target = "?";
      try {
        target = fs.readlink(dataRoot);
      } catch {
        /* 读不到链接目标时只显示问号 */
      }
      return { reach: "volume-unmounted", detail: `外置盘未挂载：data 指向 ${target}，插盘后自动恢复` };
    }
    return { reach: "missing", detail: "data/ 不存在，先跑 ./pipeline/preflight.sh --init" };
  }
  try {
    // TCC 拒绝在 stat 上未必可见，readdir 才会报 EPERM
    fs.readdir(dataRoot);
  } catch (e) {
    const code = errnoOf(e);
    if (code && DENIED.has(code)) return { reach: "permission-denied", detail: PERMISSION_HINT };
    return { reach: "missing", detail: `data/ 不可读（${code ?? "unknown"}）` };
  }
  try {
    if (fs.stat(`${dataRoot}/library`).isDirectory()) return { reach: "ok", detail: "" };
  } catch (e) {
    const code = errnoOf(e);
    if (code && DENIED.has(code)) return { reach: "permission-denied", detail: PERMISSION_HINT };
  }
  return {
    reach: "skeleton-incomplete",
    detail: "data/ 在，但 data/library 不在——骨架不全，或者指错了位置；./pipeline/preflight.sh --init 会补齐",
  };
}
