// vitest globalSetup：全部测试开跑前记下真实 data/_events.jsonl 的状态，供每个测试文件收尾时比对（realData.setup.ts）。
import type { TestProject } from "vitest/node";
import { realEventsState } from "./realData";

declare module "vitest" {
  export interface ProvidedContext {
    realEventsBaseline: string;
  }
}

export default function setup(project: TestProject): void {
  project.provide("realEventsBaseline", realEventsState());
}
