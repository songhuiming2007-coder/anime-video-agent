// TF-1 / TF-2 / TF-3：会话类夹具自检（Spec 10 §7.1）。
import { mkdirSync, readFileSync, realpathSync, statSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { assertLocalUrls, fixtureWrite, sessionRepo, sessionRepoChecks } from "../fixtures/session";
import { cleanup, tmp } from "../helpers";
import { fsWriteCalls, type SourceFile } from "../static/scan";

const roots: string[] = [];
afterAll(() => roots.forEach(cleanup));

describe("TF-1 fixtureWrite 的 realpath + lstat 守卫", () => {
  it("经 .venv（指向仓库外的软链）写入被拒，目标 mtime 不变", () => {
    const repo = sessionRepo();
    roots.push(repo.root);
    // 诱饵（M9）：副本的 .venv 原本软链到**真实** venv——守卫一旦被改坏（MUT-42 或真实回归），
    // 这条用例本身就会顺着软链覆盖真实解释器。改指向根外的临时诱饵：检验的性质不变
    //（经指向根外的软链写入必须被拒），坏了也只坏诱饵。
    const decoyDir = tmp("decoy-venv");
    roots.push(decoyDir);
    mkdirSync(join(decoyDir, "bin"));
    writeFileSync(join(decoyDir, "bin/python"), "DECOY");
    unlinkSync(join(repo.root, ".venv"));
    symlinkSync(decoyDir, join(repo.root, ".venv"));
    const target = join(repo.root, ".venv/bin/python");
    expect(realpathSync(target).startsWith(realpathSync(decoyDir))).toBe(true); // 前提：确实走到了诱饵
    const before = statSync(target).mtimeMs;
    expect(() => fixtureWrite(repo.root, ".venv/bin/python", "pwned")).toThrow();
    expect(statSync(target).mtimeMs).toBe(before);
    expect(readFileSync(join(decoyDir, "bin/python"), "utf-8")).toBe("DECOY");
  });

  it("目标本身是指向根外的符号链接 → 拒绝，根外文件内容不变（MUT-66）", () => {
    const repo = sessionRepo();
    roots.push(repo.root);
    const outsideDir = tmp("outside");
    roots.push(outsideDir);
    const outside = join(outsideDir, "evil.txt");
    writeFileSync(outside, "ORIG");
    symlinkSync(outside, join(repo.root, "pipeline/agent/evil.py"));
    expect(() => fixtureWrite(repo.root, "pipeline/agent/evil.py", "NEW")).toThrow();
    expect(readFileSync(outside, "utf-8")).toBe("ORIG");
  });

  it("临时根内的普通路径写入成功", () => {
    const repo = sessionRepo();
    roots.push(repo.root);
    fixtureWrite(repo.root, "pipeline/agent/protocol.py", "# replaced\n");
    expect(readFileSync(join(repo.root, "pipeline/agent/protocol.py"), "utf-8")).toBe("# replaced\n");
  });
});

describe("TF-2 会话夹具不出网（🟡-10）", () => {
  it("副本配置指向本地假端点；钥匙串脚本在临时根内", () => {
    const repo = sessionRepo();
    roots.push(repo.root);
    const c = sessionRepoChecks(repo);
    expect(c.baseUrl.startsWith("http://127.0.0.1:")).toBe(true);
    expect(c.apiKeyEnv).toBe("AVA_TEST_KEY");
    expect(c.webEndpoint.startsWith("http://127.0.0.1:")).toBe(true);
    expect(realpathSync(c.keychain).startsWith(realpathSync(repo.root))).toBe(true);
  });

  it("含外部 URL 的联网工具剧本被拒绝加载", () => {
    expect(() => assertLocalUrls([{ op: "emit", frame: { name: "web_fetch", args: { url: "https://evil.example/x" } } }])).toThrow();
    expect(() => assertLocalUrls([{ op: "emit", frame: { name: "web_fetch", args: { url: "http://127.0.0.1:9/x" } } }])).not.toThrow();
    expect(() => assertLocalUrls([{ op: "emit", frame: { name: "read_episode_file", args: { url: "https://ok.example" } } }])).not.toThrow();
  });
});

describe("TF-3 会话类夹具的 fs 写类 API 只出现在 fixtureWrite 内", () => {
  function stripFixtureWrite(text: string): string {
    const i = text.indexOf("export function fixtureWrite(");
    if (i < 0) return text;
    const start = text.indexOf("{", i);
    let depth = 0;
    for (let j = start; j < text.length; j += 1) {
      if (text[j] === "{") depth += 1;
      else if (text[j] === "}") {
        depth -= 1;
        if (depth === 0) return text.slice(0, i) + text.slice(j + 1);
      }
    }
    return text;
  }

  it("fixtures/session.ts 与 e2e/sessionFixtures.ts：去掉 fixtureWrite 后零命中", () => {
    const paths = [join(__dirname, "../fixtures/session.ts"), join(__dirname, "../../e2e/sessionFixtures.ts")];
    for (const p of paths) {
      const rel = p.includes("e2e") ? "e2e/sessionFixtures.ts" : "fixtures/session.ts";
      const text = readFileSync(p, "utf-8");
      const without = fsWriteCalls({ rel, text: stripFixtureWrite(text) } as SourceFile);
      // e2e/sessionFixtures.ts 不含 fixtureWrite 的实现（它复用 tests/fixtures/session.ts 的那个），必须零命中
      if (rel === "fixtures/session.ts") {
        expect(fsWriteCalls({ rel, text } as SourceFile).length).toBeGreaterThan(0); // fixtureWrite 自己确实在写
        expect(without).toEqual([]);
      } else {
        expect(fsWriteCalls({ rel, text } as SourceFile)).toEqual([]);
      }
    }
  });
});
