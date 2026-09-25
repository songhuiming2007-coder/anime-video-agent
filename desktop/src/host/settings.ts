// desktop/ 中唯一写文件的模块（TG-2）：目标恒为 <userData>/settings.json（§3.6）。
import * as fs from "node:fs";
import { join } from "node:path";
import { stringifyLossless } from "../shared/losslessJson";

export interface Settings {
  version: 1;
  repoRoot: string;
}

export function settingsPath(userData: string): string {
  return join(userData, "settings.json");
}

export function loadSettings(userData: string): Settings | null {
  try {
    const raw = JSON.parse(fs.readFileSync(settingsPath(userData), "utf-8")) as Record<string, unknown>;
    if (raw.version === 1 && typeof raw.repoRoot === "string" && raw.repoRoot.startsWith("/")) {
      return { version: 1, repoRoot: raw.repoRoot };
    }
  } catch {
    /* 缺失或损坏 → 引导页 */
  }
  return null;
}

/** tmp + rename；只在 main 的原生对话框二次确认后调用（PR4）。 */
export function saveSettings(userData: string, s: Settings): void {
  const target = settingsPath(userData);
  const tmp = `${target}.tmp`;
  fs.writeFileSync(tmp, `${stringifyLossless({ version: 1, repoRoot: s.repoRoot })}\n`, "utf-8");
  fs.renameSync(tmp, target);
}

