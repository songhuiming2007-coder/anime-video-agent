// TP-5（host 层）：可达性诊断的五分，均无新建目录。UI 文案的 e2e 部分见 e2e/preview.spec.ts。
import { chmodSync, mkdirSync, symlinkSync } from "node:fs";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { realFs } from "../../src/host/fsio";
import { diagnoseDataRoot } from "../../src/host/reach";
import { cleanup, tmp, treeManifest } from "../helpers";

const root = tmp("reach");
afterAll(() => cleanup(root));

describe("diagnoseDataRoot", () => {
  it("悬空符号链接 → volume-unmounted，文案含 readlink 目标", () => {
    const repo = join(root, "r1");
    mkdirSync(repo);
    symlinkSync("/Volumes/NoSuchDisk/anime-video-data", join(repo, "data"));
    const before = treeManifest(root);
    const r = diagnoseDataRoot(join(repo, "data"), realFs);
    expect(r.reach).toBe("volume-unmounted");
    expect(r.detail).toContain("/Volumes/NoSuchDisk/anime-video-data");
    expect(treeManifest(root)).toEqual(before);
  });
  it("chmod 000 → permission-denied（不报成脱盘）", () => {
    const repo = join(root, "r2");
    mkdirSync(join(repo, "data/library"), { recursive: true });
    chmodSync(join(repo, "data"), 0o000);
    try {
      const r = diagnoseDataRoot(join(repo, "data"), realFs);
      expect(r.reach).toBe("permission-denied");
      expect(r.detail).toContain("隐私与安全性");
    } finally {
      chmodSync(join(repo, "data"), 0o755);
    }
  });
  it("缺失 / 骨架不全 / 正常", () => {
    const repo = join(root, "r3");
    mkdirSync(repo);
    expect(diagnoseDataRoot(join(repo, "data"), realFs).reach).toBe("missing");
    mkdirSync(join(repo, "data"));
    expect(diagnoseDataRoot(join(repo, "data"), realFs).reach).toBe("skeleton-incomplete");
    mkdirSync(join(repo, "data/library"));
    expect(diagnoseDataRoot(join(repo, "data"), realFs)).toEqual({ reach: "ok", detail: "" });
  });
});
