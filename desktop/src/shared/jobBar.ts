// D71：全局常驻作业条与时间线语义修正的纯函数（零 DOM）。
// 作业数据只来自活跃期的 events.jsonl 折叠（fold.ts）；这里只做取舍、排序与文案，不读任何别的来源。
import type { JobView } from "./protocol";

const t = (iso: string | null): number => (iso === null ? Number.NaN : Date.parse(iso));
const ascBy = (key: (j: JobView) => number) => (a: JobView, b: JobView) => {
  const x = key(a);
  const y = key(b);
  if (Number.isNaN(x) || Number.isNaN(y)) return Number.isNaN(x) ? (Number.isNaN(y) ? 0 : 1) : -1;
  return x - y;
};

/** 作业条要显示的作业：running 在前（按开始时间升序），其后 pending（按最后事件时间升序）。 */
export function activeJobs(jobs: readonly JobView[]): JobView[] {
  const running = jobs.filter((j) => j.state === "running").sort(ascBy((j) => t(j.startedAt ?? j.lastEventAt)));
  const pending = jobs.filter((j) => j.state === "pending").sort(ascBy((j) => t(j.lastEventAt)));
  return [...running, ...pending];
}

/** 已运行时长 `m:ss` / `h:mm:ss`。没有开始时间、或开始时间晚于此刻（时钟不可信）返回 null，不显示负数。 */
export function elapsedText(startedAt: string | null, now: number): string | null {
  const s0 = t(startedAt);
  if (Number.isNaN(s0) || s0 > now + 1000) return null;
  const s = Math.max(0, Math.floor((now - s0) / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** 命令首词（去掉 `pipeline.` 前缀）：`tts --redo 2` → `tts` */
export function commandHead(command: string | null): string {
  return (command ?? "").trim().split(/\s+/)[0]?.replace(/^pipeline\./, "") ?? "";
}

/**
 * 机检「未通过」：`check_script` 退出码 1 = 稿件有 FAIL 项，是检查结果、不是作业故障（D71 实例：
 * 伪恋期两次机检都被时间线记成「失败」）。只认这一个命令的这一个退出码；退出码 2（用法错）等照旧算失败。
 */
export function isCheckNotPassed(j: JobView): boolean {
  return j.state === "failed" && j.returncode === 1 && commandHead(j.command) === "check_script";
}
