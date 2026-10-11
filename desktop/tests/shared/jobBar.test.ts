// D71：作业条取舍、时长文案与机检「未通过」判据。期望值先在实现上手算核过（见各用例注释）。
import { describe, expect, it } from "vitest";
import { activeJobs, commandHead, elapsedText, isCheckNotPassed } from "../../src/shared/jobBar";
import type { JobView } from "../../src/shared/protocol";

const job = (o: Partial<JobView> & { jobId: string }): JobView => ({
  command: null,
  state: "pending",
  lastEventAt: "2026-10-10T15:00:00.000000Z",
  startedAt: null,
  pid: null,
  returncode: null,
  durationS: null,
  stderrTail: null,
  message: null,
  noFollowupEvents: false,
  finishedEventMissing: false,
  ...o,
});

describe("activeJobs", () => {
  it("只取 running 与 pending；running 在前按开始时间升序，pending 按最后事件时间升序", () => {
    const jobs = [
      job({ jobId: "done", state: "succeeded" }),
      job({ jobId: "fail", state: "failed" }),
      job({ jobId: "blk", state: "blocked" }),
      job({ jobId: "p2", state: "pending", lastEventAt: "2026-10-10T15:05:00Z" }),
      job({ jobId: "r2", state: "running", startedAt: "2026-10-10T15:03:00Z" }),
      job({ jobId: "p1", state: "pending", lastEventAt: "2026-10-10T15:04:00Z" }),
      job({ jobId: "r1", state: "running", startedAt: "2026-10-10T15:01:00Z" }),
    ];
    expect(activeJobs(jobs).map((j) => j.jobId)).toEqual(["r1", "r2", "p1", "p2"]);
  });
  it("没有活动作业返回空数组（作业条不渲染）", () => {
    expect(activeJobs([job({ jobId: "a", state: "succeeded" })])).toEqual([]);
  });
});

describe("elapsedText", () => {
  const now = Date.parse("2026-10-10T15:28:39Z");
  it("m:ss 与 h:mm:ss（112 s → 1:52；3725 s → 1:02:05）", () => {
    expect(elapsedText("2026-10-10T15:26:47Z", now)).toBe("1:52");
    expect(elapsedText("2026-10-10T14:26:34Z", now)).toBe("1:02:05");
    expect(elapsedText("2026-10-10T15:28:39Z", now)).toBe("0:00");
  });
  it("没有开始时间、或开始时间在未来（时钟不可信）返回 null", () => {
    expect(elapsedText(null, now)).toBeNull();
    expect(elapsedText("2026-10-10T15:30:00Z", now)).toBeNull();
    expect(elapsedText("garbage", now)).toBeNull();
  });
});

describe("isCheckNotPassed", () => {
  it("只有 check_script 的退出码 1 算「未通过」（带不带 pipeline. 前缀）", () => {
    expect(isCheckNotPassed(job({ jobId: "a", state: "failed", returncode: 1, command: "check_script" }))).toBe(true);
    expect(isCheckNotPassed(job({ jobId: "b", state: "failed", returncode: 1, command: "pipeline.check_script data/x" }))).toBe(true);
  });
  it("退出码 2、别的命令的退出码 1、成功的机检都不是", () => {
    expect(isCheckNotPassed(job({ jobId: "c", state: "failed", returncode: 2, command: "check_script" }))).toBe(false);
    expect(isCheckNotPassed(job({ jobId: "d", state: "failed", returncode: 1, command: "tts" }))).toBe(false);
    expect(isCheckNotPassed(job({ jobId: "e", state: "succeeded", returncode: 0, command: "check_script" }))).toBe(false);
  });
  it("commandHead", () => {
    expect(commandHead("pipeline.tts --redo 2")).toBe("tts");
    expect(commandHead(null)).toBe("");
  });
});
