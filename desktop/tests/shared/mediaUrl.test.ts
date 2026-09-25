// §3.5 路径守卫纯函数层（TS-3 的单元部分；协议层实测见 e2e）
import { describe, expect, it } from "vitest";
import { corsAllowOrigin, decodeAndGuard, encodeMediaUrl, parseRange, relPathProblem } from "../../src/shared/mediaUrl";

const roots = { episodes: "/d/episodes", shots: "/d/library/shots" };
const links: Record<string, string> = {
  "/d/episodes/EP/link-out.json": "/d/library/memory.json",
  "/d/episodes/EP/link-shots.png": "/d/library/shots/x.png",
  "/d/episodes/EP/link-in.md": "/d/episodes/EP/02-script.md",
};
const realpath = (p: string) => {
  if (p.includes("missing")) throw Object.assign(new Error("ENOENT"), { code: "ENOENT" });
  return links[p] ?? p;
};
const guard = (u: string) => decodeAndGuard(u, roots, realpath);

describe("decodeAndGuard", () => {
  it("正常路径与逐段编码", () => {
    const u = encodeMediaUrl("episodes", "EGOIST/01 Live/03-audio/seg-01.wav");
    expect(u).toBe("ava-media://episodes/EGOIST/01%20Live/03-audio/seg-01.wav");
    expect(guard(u)).toEqual({ ok: true, abs: "/d/episodes/EGOIST/01 Live/03-audio/seg-01.wav", mime: "audio/wav", root: "episodes" });
  });
  it("规则 1：root 内部的 %2F 段注入 → 400（只有规则 1 能拦住）", () => {
    expect(guard("ava-media://episodes/EP%2F02-script.md")).toEqual({ ok: false, status: 400 });
    expect(guard("ava-media://episodes/EP%5C02-script.md")).toEqual({ ok: false, status: 400 });
    expect(guard("ava-media://shots/..%2F..%2Fbrowser-profile/x.json")).toEqual({ ok: false, status: 400 });
    expect(guard("ava-media://episodes/EP/%00.md")).toEqual({ ok: false, status: 400 });
    expect(guard("ava-media://episodes/EP//a.md")).toEqual({ ok: false, status: 400 });
    expect(guard("ava-media://episodes/EP/%2e%2e/a.md")).toEqual({ ok: false, status: 400 });
    expect(guard("ava-media://episodes/EP/%E0%A4%A.md")).toEqual({ ok: false, status: 400 });
  });
  it("规则 2：realpath 必须是所选 root 的严格后代", () => {
    expect(guard("ava-media://episodes/EP/link-out.json")).toEqual({ ok: false, status: 403 });
    expect(guard("ava-media://episodes/EP/link-shots.png")).toEqual({ ok: false, status: 403 }); // 同一 dataRoot 下的另一 root 也不行
    expect(guard("ava-media://episodes/EP/link-in.md")).toMatchObject({ ok: true, abs: "/d/episodes/EP/02-script.md" });
    expect(guard("ava-media://episodes/EP/missing.md")).toEqual({ ok: false, status: 404 });
  });
  it("规则 3：扩展名白名单；未知 root", () => {
    expect(guard("ava-media://episodes/EP/run.sh")).toEqual({ ok: false, status: 415 });
    expect(guard("ava-media://library/memory.md")).toEqual({ ok: false, status: 404 });
    expect(guard("http://episodes/EP/a.md")).toEqual({ ok: false, status: 400 });
  });
});

describe("relPathProblem（H5 段检查共享）", () => {
  it.each([["../x"], ["/etc/hosts"], ["a//b"], [""], ["a/./b"], ["a\\b"], ["a\0b"]])("%s 被拒", (p) => {
    expect(relPathProblem(p)).not.toBeNull();
  });
  it("正常相对路径通过", () => {
    expect(relPathProblem("03-audio/manifest.json")).toBeNull();
  });
});

describe("parseRange", () => {
  it("单段 / 开放区间 / 后缀区间", () => {
    expect(parseRange("bytes=100-199", 1000)).toEqual({ start: 100, end: 199 });
    expect(parseRange("bytes=900-", 1000)).toEqual({ start: 900, end: 999 });
    expect(parseRange("bytes=-100", 1000)).toEqual({ start: 900, end: 999 });
    expect(parseRange("bytes=0-5000", 1000)).toEqual({ start: 0, end: 999 });
    expect(parseRange(null, 1000)).toBeNull();
  });
  it("多段与越界 → unsatisfiable", () => {
    expect(parseRange("bytes=0-1,5-6", 1000)).toBe("unsatisfiable");
    expect(parseRange("bytes=1000-", 1000)).toBe("unsatisfiable");
    expect(parseRange("bytes=5-1", 1000)).toBe("unsatisfiable");
    expect(parseRange("items=0-1", 1000)).toBe("unsatisfiable");
  });
});

describe("corsAllowOrigin（S21 修订 §2.7）", () => {
  const allowed = new Set(["file://"]);
  it("只放行 renderer 自身源", () => {
    expect(corsAllowOrigin("file://", allowed)).toBe("file://");
  });
  it("沙箱 iframe（Origin null）、其他源、无 Origin → 不放行", () => {
    expect(corsAllowOrigin("null", new Set(["null", "file://"]))).toBeNull();
    expect(corsAllowOrigin("http://localhost:5173", allowed)).toBeNull();
    expect(corsAllowOrigin(null, allowed)).toBeNull();
  });
});
