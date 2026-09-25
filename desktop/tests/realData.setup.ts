// 每个测试文件收尾时断言真实 data/_events.jsonl 与全部测试开跑前一致（未被创建、未被修改）。
import { afterAll, expect, inject } from "vitest";
import { REAL_EVENTS, realEventsState } from "./realData";

afterAll(() => {
  expect(realEventsState(), `真实数据被污染：${REAL_EVENTS}`).toBe(inject("realEventsBaseline"));
});
