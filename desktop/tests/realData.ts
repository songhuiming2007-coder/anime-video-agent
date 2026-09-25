// 真实数据零污染（spec §7.1，继承 Spec 2 T8）：真实 <repo>/data/_events.jsonl 在测试前后状态一致，
// 原先不存在的必须仍然不存在。只读 lstat，不跟随、不创建；data 脱卸时同样记为不存在。
import { lstatSync } from "node:fs";
import { join } from "node:path";
import { REPO } from "./helpers";

export const REAL_EVENTS = join(REPO, "data/_events.jsonl");

export function realEventsState(): string {
  try {
    const st = lstatSync(REAL_EVENTS, { bigint: true });
    return `present ino=${st.ino} size=${st.size} mtimeNs=${st.mtimeNs}`;
  } catch (e) {
    return `absent ${(e as { code?: string }).code ?? String(e)}`;
  }
}
