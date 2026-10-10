// D66：回收站行的大小格式化（纯函数）与标记文案取词
import { describe, expect, it } from "vitest";
import { formatTrashSize } from "../../src/renderer/SessionList";

describe("D66 formatTrashSize", () => {
  it("KB / MB 一位小数", () => {
    expect(formatTrashSize(0)).toBe("0.0 KB");
    expect(formatTrashSize(2048)).toBe("2.0 KB");
    expect(formatTrashSize(1024 * 1024 - 1)).toBe("1024.0 KB");
    expect(formatTrashSize(1024 * 1024)).toBe("1.0 MB");
    expect(formatTrashSize(3.25 * 1024 * 1024)).toBe("3.3 MB");
  });
});
