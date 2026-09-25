// §2.9 host 崩溃重启看护的退避与熔断（纯函数；端到端见 e2e TI-9）。
import { describe, expect, it } from "vitest";
import { initialRestartState, onHostCrash, onHostStarted, type RestartState } from "../../src/shared/hostRestart";

/** host 每次拉起后存活 aliveMs 就崩：返回每次崩溃后的动作 */
function crashLoop(aliveMs: number, n: number) {
  let s: RestartState = initialRestartState(0);
  let now = 0;
  const out: string[] = [];
  for (let i = 0; i < n; i++) {
    now += aliveMs;
    const r = onHostCrash(s, now);
    s = r.state;
    if (r.action.kind === "fatal") {
      out.push("fatal");
      break;
    }
    out.push(String(r.action.delayMs));
    now += r.action.delayMs;
    s = onHostStarted(s, now);
  }
  return out;
}

describe("host 重启退避与熔断", () => {
  it("立即崩溃循环：1 s → 2 s → 4 s → 8 s，第 5 次（60 s 内）熔断", () => {
    expect(crashLoop(0, 10)).toEqual(["1000", "2000", "4000", "8000", "fatal"]);
  });
  it("每次存活 20 s：退避翻倍并封顶 30 s；60 s 内从不满 5 次，不熔断", () => {
    expect(crashLoop(20_000, 8)).toEqual(["1000", "2000", "4000", "8000", "16000", "30000", "30000", "30000"]);
  });
  it("存活满 60 s 后再崩：连续计数清零，重新从 1 s 起", () => {
    let s = initialRestartState(0);
    s = onHostCrash(s, 100).state;
    s = onHostStarted(s, 1100);
    const r = onHostCrash(s, 1100 + 60_000);
    expect(r.action).toEqual({ kind: "restart", delayMs: 1000 });
  });
});
