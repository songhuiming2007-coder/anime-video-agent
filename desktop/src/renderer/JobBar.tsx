// D71（人选方案 A）：顶栏下的全局常驻作业条。活跃期有作业在跑或排队时出现，结束即消失。
// 只读：数据是活跃期 events.jsonl 的折叠（ep.jobs），本组件不发任何 RPC；「看进度」只展开预览区与时间线。
import { useEffect, useState } from "react";
import { activeJobs, elapsedText } from "../shared/jobBar";
import type { JobView } from "../shared/protocol";

export function JobBar({ jobs, onShowProgress }: { jobs: readonly JobView[]; onShowProgress: () => void }) {
  const list = activeJobs(jobs);
  const ticking = list.some((j) => j.state === "running");
  const [now, setNow] = useState(() => Date.now());
  // 只在有作业在跑时每秒走一次表；没有就不开计时器
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [ticking]);

  if (list.length === 0) return null;
  const head = list[0];
  const gone = head.finishedEventMissing;
  const elapsed = head.state === "running" ? elapsedText(head.startedAt, now) : null;
  return (
    <div className={gone ? "jobbar jobbar--warn" : "jobbar"} data-testid="jobbar" role="status" aria-live="polite">
      {!gone && <span className="ui-spinner" />}
      <b className="jobbar-state">{gone ? "进程已不在，未收到结束事件" : head.state === "running" ? "正在运行" : "排队中"}</b>
      <code className="jobbar-cmd" data-testid="jobbar-cmd" title={head.command ?? head.jobId}>
        {head.command ?? head.jobId}
      </code>
      {elapsed !== null && (
        <span className="muted" data-testid="jobbar-elapsed">
          已运行 {elapsed}
        </span>
      )}
      {list.length > 1 && (
        <span className="muted" data-testid="jobbar-more">
          +{list.length - 1}
        </span>
      )}
      <span className="jobbar-sp" />
      <button className="ui-btn ui-btn--ghost ui-btn--sm" data-testid="jobbar-progress" onClick={onShowProgress}>
        看进度
      </button>
    </div>
  );
}
