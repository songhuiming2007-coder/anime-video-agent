import { describe, expect, it } from "vitest";
import { formatAudioClock } from "../../src/renderer/VoicePanel";

describe("formatAudioClock", () => {
  it("把秒格式成 m:ss，非法值落 0:00", () => {
    expect(formatAudioClock(0)).toBe("0:00");
    expect(formatAudioClock(61.9)).toBe("1:01");
    expect(formatAudioClock(NaN)).toBe("0:00");
    expect(formatAudioClock(-3)).toBe("0:00");
  });
});
