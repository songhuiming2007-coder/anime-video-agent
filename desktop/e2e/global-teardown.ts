import { REAL_EVENTS, realEventsState } from "../tests/realData";

// 真实数据零污染（§7.1）：全部 e2e 结束后，真实 data/_events.jsonl 必须与开跑前一致（不存在的仍不存在）
export default function globalTeardown(): void {
  const before = process.env.AVA_E2E_REAL_EVENTS_BASELINE;
  const after = realEventsState();
  if (before === undefined) throw new Error("global-setup 未记录真实数据基线");
  if (after !== before) throw new Error(`真实数据被污染：${REAL_EVENTS}\n  开跑前：${before}\n  结束后：${after}`);
  console.log(`真实数据零污染：${REAL_EVENTS} 前后一致（${after}）`);
}
