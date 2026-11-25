// desktop/ 中唯一写文件的模块（TG-2）：目标恒为 <userData>/settings.json（§3.6）。
import * as fs from "node:fs";
import { join } from "node:path";
import { stringifyLossless } from "../shared/losslessJson";

export interface Settings {
  version: 1;
  repoRoot: string;
  /** A2：用户主动点击「设为默认」时落盘的默认模型 / 推理强度；缺省或 null 表示沿用仓库配置默认 */
  defaultModel?: string | null;
  defaultEffort?: string | null;
}

export function settingsPath(userData: string): string {
  return join(userData, "settings.json");
}

function optStr(v: unknown): string | null {
  return typeof v === "string" && v.trim() !== "" ? v.trim() : null;
}

export function loadSettings(userData: string): Settings | null {
  try {
    const raw = JSON.parse(fs.readFileSync(settingsPath(userData), "utf-8")) as Record<string, unknown>;
    if (raw.version === 1 && typeof raw.repoRoot === "string" && raw.repoRoot.startsWith("/")) {
      const dm = optStr(raw.defaultModel);
      const de = optStr(raw.defaultEffort);
      return {
        version: 1,
        repoRoot: raw.repoRoot,
        ...(dm !== null ? { defaultModel: dm } : {}),
        ...(de !== null ? { defaultEffort: de } : {}),
      };
    }
  } catch {
    /* 缺失或损坏 → 引导页 */
  }
  return null;
}

/** tmp + rename；只在仓库切换确认或用户主动点击「设为默认」后调用。 */
export function saveSettings(userData: string, s: Settings): void {
  const target = settingsPath(userData);
  const tmp = `${target}.tmp`;
  const payload: Settings = {
    version: 1,
    repoRoot: s.repoRoot,
    ...(s.defaultModel ? { defaultModel: s.defaultModel } : {}),
    ...(s.defaultEffort ? { defaultEffort: s.defaultEffort } : {}),
  };
  fs.writeFileSync(tmp, `${stringifyLossless(payload)}\n`, "utf-8");
  fs.renameSync(tmp, target);
}

