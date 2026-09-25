// 05 解封物的事后核验（Spec 8 §2.6 闸 3、§4.3）。全 desktop/ 唯一允许出现 04-clips.approved.json 字面量的文件（TG-7 豁免）。
// 它核验「第 ① 步产出物是否对应人审版本」，是事实比对，不参与闸门判定（§6.5）。
// 主防线在 core：review --approve --expect-* 在同一个 fd 上核对并只用核对过的字节写解封物（Spec 3 S3-R9）；
// 在 S3-R9 正确施工的前提下，第 ① 步之后的不等分支不可达，只用于发现 core 回归；
// 「已对齐待确认」分支则可达：解封物在对齐后被改了 mtime、内容不变时 core 双闸仍放行，这里是唯一拦截（TA-2c）。
import type { ArtifactFingerprint } from "../shared/contracts";
import type { FsProbe } from "./fsio";

const GATE_FILE = "04-clips.approved.json";
const PINNED_SOURCE = "04-clips.json";

/** stat(04-clips.approved.json) 的 (size, mtime_ns) 等于 pinned 中 04-clips.json 的钉住值（bigint 纳秒比较）。 */
export function gateMatches(epAbs: string, pinned: ArtifactFingerprint[], fs: FsProbe): boolean {
  const src = pinned.find((a) => a.path === PINNED_SOURCE);
  if (!src) return false;
  try {
    const st = fs.statBig(`${epAbs}/${GATE_FILE}`);
    return st.size === BigInt(src.size) && st.mtimeNs === src.mtime_ns;
  } catch {
    return false; // 解封物不存在：不对应任何人审版本
  }
}
