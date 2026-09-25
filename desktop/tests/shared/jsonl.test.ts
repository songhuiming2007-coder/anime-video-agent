import { describe, expect, it } from "vitest";
import { LineSplitter } from "../../src/shared/jsonl";

const enc = (s: string) => new TextEncoder().encode(s);

describe("LineSplitter（半行永不下发）", () => {
  it("TE-2 单元层：半行零下发，补齐后恰 1 行", () => {
    const sp = new LineSplitter(1024);
    expect(sp.push(enc('{"a":1'))).toEqual({ lines: [], overflow: false });
    expect(sp.pendingBytes()).toBe(6);
    expect(sp.push(enc("}\n"))).toEqual({ lines: ['{"a":1}'], overflow: false });
    expect(sp.pendingBytes()).toBe(0);
  });
  it("多字节 UTF-8 字符被切在两块之间仍正确拼回", () => {
    const sp = new LineSplitter(1024);
    const bytes = enc("配音\n");
    expect(sp.push(bytes.subarray(0, 2)).lines).toEqual([]);
    expect(sp.push(bytes.subarray(2)).lines).toEqual(["配音"]);
  });
  it("一块多行 + 尾部半行", () => {
    const sp = new LineSplitter(1024);
    expect(sp.push(enc("a\nb\nc")).lines).toEqual(["a", "b"]);
    expect(sp.push(enc("\n")).lines).toEqual(["c"]);
  });
  it("partial 超上限：丢弃并跳到下一个换行之后", () => {
    const sp = new LineSplitter(8);
    const r1 = sp.push(enc("123456789"));
    expect(r1).toEqual({ lines: [], overflow: true });
    expect(sp.push(enc("0garbage\nok\n")).lines).toEqual(["ok"]);
  });
});
