// 会话类夹具（Spec 10 §7）：假 `pipeline/agent/protocol.py`（Python 剧本驱动）+ 写入守卫 + 不出网配置改写。
//
// 为何这样写（Spec 10 一轮 🔴-1）：`.venv` 是软链到仓库外的真解释器（本机 realpath 落在 Homebrew），
// 绝不能在 `.venv/` 下写任何东西——夹具改为在 `makeFixtureRepo` **已复制的** `pipeline/agent/` 里写假 protocol.py，
// 真实的 `.venv/bin/python` 照常以 `-m pipeline.agent.protocol` 执行它。夹具从不写 `.venv` 下的路径。
//
// 每个会话进程按自己的 argv（期目录绝对路径，或 `--idea`）取自己的剧本与记录文件：`<root>/.ava-session/<name>.script.jsonl`。
import * as fsw from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { keychainExecPath, __avaTestSetKeychainExec } from "../../src/host/spawner";
import { makeFixtureRepo } from "../helpers";

/**
 * 夹具写入的唯一出口（TF-1）：写前逐级 lstat + realpath，任一级是符号链接或指向临时根之外即抛错。
 * 目标本身若是符号链接也拒绝（否则指向外部的链接会让写入穿出去，二轮 🔵-9 / MUT-66）。
 * TF-3：本文件里所有 fs 写类 API 只出现在这个函数体内。
 */
export function fixtureWrite(root: string, rel: string, data: string, mode?: number): void {
  const rootReal = fsw.realpathSync(root);
  const target = resolve(rootReal, rel);
  const relPath = relative(rootReal, target);
  if (relPath.startsWith("..") || isAbsolute(relPath)) throw new Error(`fixtureWrite 目标越出临时根：${rel}`);
  let cur = rootReal;
  for (const seg of relPath.split(sep)) {
    cur = join(cur, seg);
    let st;
    try {
      st = fsw.lstatSync(cur);
    } catch {
      break; // 余下组件不存在：由 mkdir/写入创建普通目录/文件
    }
    if (st.isSymbolicLink()) throw new Error(`fixtureWrite 路径组件是符号链接，拒绝写入：${cur}`);
    const real = fsw.realpathSync(cur);
    if (real !== rootReal && !real.startsWith(rootReal + sep)) throw new Error(`fixtureWrite 路径经符号链接越出临时根：${cur}`);
  }
  fsw.mkdirSync(dirname(target), { recursive: true });
  fsw.writeFileSync(target, data);
  if (mode !== undefined) fsw.chmodSync(target, mode);
}

const NET_TOOLS = new Set(["web_fetch", "crawl", "browser"]);

/** 「不出网」自检（TF-2）：联网工具的 URL 只许 http://127.0.0.1:，否则剧本拒绝加载。 */
export function assertLocalUrls(value: unknown): void {
  if (Array.isArray(value)) return value.forEach(assertLocalUrls);
  if (value === null || typeof value !== "object") return;
  const o = value as Record<string, unknown>;
  if (typeof o.name === "string" && NET_TOOLS.has(o.name)) {
    const args = (o.args ?? {}) as Record<string, unknown>;
    const url = args.url ?? o.url;
    if (typeof url === "string" && !url.startsWith("http://127.0.0.1:")) {
      throw new Error(`剧本里 ${o.name} 的 URL 不是本地假端点，拒绝加载：${url}`);
    }
  }
  for (const x of Object.values(o)) assertLocalUrls(x);
}

function protocolPy(baseDir: string): string {
  return `# 假协议进程（Spec 10 §7 夹具）：按剧本 JSON 行驱动，记录收到的 stdin 与信号。
import json, os, signal, subprocess, sys, time

BASE = ${JSON.stringify(baseDir)}
_argv1 = sys.argv[1] if len(sys.argv) > 1 else "idea"
NAME = "idea" if _argv1.startswith("--") else os.path.basename(_argv1)
EP = _argv1 if not _argv1.startswith("--") else ""
SCRIPT = os.path.join(BASE, NAME + ".script.jsonl")
RECORD = os.path.join(BASE, NAME + ".record.jsonl")
CHILDLOCK = RECORD + ".child"
ignore_term = [False]
seq = [0]
sid = "s1"


def rec(obj):
    with open(RECORD, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\\n")


def emit(frame):
    seq[0] += 1
    out = {"v": 1, "seq": seq[0], "sid": sid}
    out.update(frame)
    sys.stdout.write(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\\n")
    sys.stdout.flush()


def on_term(signum, _frame):
    rec({"kind": "signal", "signal": "SIGTERM"})
    if not ignore_term[0]:
        os._exit(143)


signal.signal(signal.SIGTERM, on_term)


def subst(tmpl, fr, turn):
    s = json.dumps(tmpl, ensure_ascii=False)
    s = s.replace('"$rid"', json.dumps(fr.get("rid")))
    s = s.replace('"$turn"', json.dumps("t%d" % turn))
    s = s.replace('"$request_id"', json.dumps(fr.get("request_id")))
    s = s.replace('"$decision"', json.dumps(fr.get("decision")))
    return json.loads(s)


def serve(op):
    turn = [0]
    while True:
        line = sys.stdin.readline()
        if line == "":
            rec({"kind": "eof"}); return
        rec({"kind": "stdin", "line": line.rstrip("\\n")})
        try:
            fr = json.loads(line)
        except Exception:
            continue
        t = fr.get("t")
        if t == "user_message":
            turn[0] += 1
            for tmpl in op.get("on_turn", []):
                emit(subst(tmpl, fr, turn[0]))
            if op.get("after_turn"):
                time.sleep(op.get("after_turn_delay", 0.4))
                for tmpl in op["after_turn"]:
                    emit(subst(tmpl, fr, turn[0]))
        elif t in ("answer", "command", "interrupt"):
            for tmpl in op.get("on_" + t, []):
                emit(subst(tmpl, fr, turn[0]))
            if t == "interrupt" and op.get("on_interrupt_end"):
                return
        elif t == "shutdown":
            if op.get("on_shutdown") == "exit":
                return


def main():
    rec({"kind": "pid", "pid": os.getpid()})
    rec({"kind": "env_keys", "keys": sorted(os.environ.keys())})
    ops = [json.loads(l) for l in open(SCRIPT, encoding="utf-8") if l.strip()]
    last = [None]
    for op in ops:
        k = op.get("op")
        if k == "emit":
            emit(op["frame"])
        elif k == "raw":
            sys.stdout.write(op["text"]); sys.stdout.flush()
        elif k == "stderr":
            sys.stderr.write(op["text"]); sys.stderr.flush()
        elif k == "sleep":
            time.sleep(op["seconds"])
        elif k == "wait":
            for _ in range(op.get("lines", 1)):
                line = sys.stdin.readline()
                if line == "":
                    rec({"kind": "eof"}); return
                rec({"kind": "stdin", "line": line.rstrip("\\n")})
                last[0] = json.loads(line)
        elif k == "reply":
            for tmpl in op["frames"]:
                emit(subst(tmpl, last[0] or {}, 1))
        elif k == "bigline":
            seq[0] += 1
            sys.stdout.write(json.dumps({"v": 1, "t": "assistant", "seq": seq[0], "sid": sid, "turn_id": "t1", "kind": "answer", "text": "x" * op["bytes"]}, separators=(",", ":")) + "\\n")
            if op.get("then"):
                emit(op["then"])
            sys.stdout.flush()
        elif k == "partial_big":
            sys.stdout.write("y" * op["bytes"]); sys.stdout.flush()
            time.sleep(op.get("sleep", 0.1))
            sys.stdout.write("\\n"); emit(op["then"]); sys.stdout.flush()
        elif k == "partial":
            text = op["text"]
            cut = op["cut"]
            sys.stdout.write(text[:cut]); sys.stdout.flush()
            time.sleep(op.get("sleep", 0.05))
            sys.stdout.write(text[cut:]); sys.stdout.flush()
        elif k == "grandchild":
            code = (
                "import json,signal,sys\\n"
                "def h(s,f):\\n"
                "    open(sys.argv[1],'a').write(json.dumps({'kind':'child_signal','signal':'SIGTERM'})+'\\\\n')\\n"
                "signal.signal(signal.SIGTERM,h)\\n"
                # python -c code PATH 下 sys.argv == ['-c', PATH]：路径在 argv[1]。M9 实测此前写的是
                # argv[2]——处理器抛 IndexError、永远写不出 child_signal，TH-9 ②「孙进程未收到 SIGTERM」
                # 恒真（MUT-11 因此存活）。另：处理器装好后才报就绪，测试等到它再发信号，否则
                # 「没收到」可能只是处理器还没装、按默认动作悄悄死了。
                "open(sys.argv[1],'a').write(json.dumps({'kind':'child_ready'})+'\\\\n')\\n"
                "import time\\n"
                "while True: time.sleep(0.2)\\n"
            )
            p = subprocess.Popen([sys.executable, "-c", code, CHILDLOCK])
            rec({"kind": "grandchild", "pid": p.pid})
        elif k == "ignore_sigterm":
            ignore_term[0] = True
        elif k == "dump_env":
            rec({"kind": "env", "env": dict(os.environ)})
        elif k == "event":
            ev = {
                "event_id": op.get("event_id", "evt_test_0001"),
                "timestamp": op.get("timestamp", "2026-09-25T10:00:00.000000Z"),
                "episode": NAME,
                "type": op["type"],
                "payload": op.get("payload", {}),
            }
            with open(os.path.join(EP, "events.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\\n")
        elif k == "serve":
            serve(op)
        elif k == "loop":
            while True:
                line = sys.stdin.readline()
                if line == "":
                    rec({"kind": "eof"}); return
                rec({"kind": "stdin", "line": line.rstrip("\\n")})
        elif k == "exit":
            sys.exit(op.get("code", 0))
    sys.exit(0)


main()
`;
}

export interface SessionRepo {
  root: string;
  baseDir: string;
  keychainPath: string;
  eps: string;
}

/** 会话键 → 剧本/记录文件名（与假 protocol.py 的 argv 解析规则一致）。 */
export function nameOf(convKey: string): string {
  return convKey === "idea" ? "idea" : convKey.slice(3);
}

/**
 * 建好一个会话专用夹具仓库：配置改本地、假钥匙串在临时根内、假 protocol.py 就位（TF-2）。
 *
 * `llmUrl` 给出时（Spec 10 PR4，真实 core）：**不写**假 protocol.py——副本里就是 `makeFixtureRepo`
 * 复制来的真实 `pipeline/agent/protocol.py`；`base_url` 指向调用方起的本地假 LLM 端点。
 * 其余守卫（配置改本地、钥匙串在临时根内、写入经 fixtureWrite）与假进程版完全相同。
 */
export function sessionRepo(opts: { withApprovals?: boolean; llmUrl?: string } = {}): SessionRepo {
  const root = makeFixtureRepo(opts);
  const baseDir = join(root, ".ava-session");
  const baseUrl = opts.llmUrl ?? "http://127.0.0.1:9/v1";
  if (!baseUrl.startsWith("http://127.0.0.1:")) throw new Error(`假 LLM 端点必须在本机：${baseUrl}`);
  fixtureWrite(root, "config/agent.local.json", JSON.stringify({ base_url: baseUrl, model: "fake", api_key_env: "AVA_TEST_KEY" }, null, 2));
  fixtureWrite(root, "config/agent/web.local.json", JSON.stringify({ search: { endpoint: "http://127.0.0.1:9/search" } }, null, 2));
  const keychainPath = join(baseDir, "security");
  fixtureWrite(root, ".ava-session/security", `#!/bin/sh\nf=${JSON.stringify(join(baseDir, "key"))}\nif [ -f "$f" ]; then cat "$f"; else exit 44; fi\n`, 0o755);
  if (opts.llmUrl === undefined) fixtureWrite(root, "pipeline/agent/protocol.py", protocolPy(baseDir));
  __avaTestSetKeychainExec(keychainPath);
  return { root, baseDir, keychainPath, eps: join(root, "data/episodes") };
}

export function scriptPathOf(repo: SessionRepo, name: string): string {
  return join(repo.baseDir, `${name}.script.jsonl`);
}

export function recordPathOf(repo: SessionRepo, name: string): string {
  return join(repo.baseDir, `${name}.record.jsonl`);
}

/** 写剧本（并做不出网自检）；同时清空该会话的记录文件。 */
export function sessionScript(repo: SessionRepo, name: string, ops: unknown[]): void {
  assertLocalUrls(ops);
  fixtureWrite(repo.root, `.ava-session/${name}.script.jsonl`, ops.map((o) => JSON.stringify(o)).join("\n") + "\n");
  fixtureWrite(repo.root, `.ava-session/${name}.record.jsonl`, "");
}

/** 写假钥匙串的值（不调用 = 脚本退 44「找不到条目」）。 */
export function sessionKey(repo: SessionRepo, value: string): void {
  fixtureWrite(repo.root, ".ava-session/key", value);
}

export interface SessionRecord {
  kind: "stdin" | "signal" | "grandchild" | "child_signal" | "child_ready" | "env" | "env_keys" | "eof" | "pid";
  line?: string;
  signal?: string;
  pid?: number;
  env?: Record<string, string>;
  keys?: string[];
}

export function sessionRecords(repo: SessionRepo, name: string): SessionRecord[] {
  try {
    return fsw
      .readFileSync(recordPathOf(repo, name), "utf-8")
      .trim()
      .split("\n")
      .filter(Boolean)
      .map((l) => JSON.parse(l) as SessionRecord);
  } catch {
    return [];
  }
}

export function stdinLines(repo: SessionRepo, name: string): string[] {
  return sessionRecords(repo, name)
    .filter((r) => r.kind === "stdin")
    .map((r) => r.line as string);
}

export function childSignals(repo: SessionRepo, name: string): SessionRecord[] {
  try {
    return fsw
      .readFileSync(`${recordPathOf(repo, name)}.child`, "utf-8")
      .trim()
      .split("\n")
      .filter(Boolean)
      .map((l) => JSON.parse(l) as SessionRecord);
  } catch {
    return [];
  }
}

export function keychainPathInUse(): string {
  return keychainExecPath();
}

/** TF-2 的配置自检读数：本地假端点 + 临时根内的钥匙串脚本。 */
export function sessionRepoChecks(repo: SessionRepo): { baseUrl: string; apiKeyEnv: string; webEndpoint: string; keychain: string } {
  const local = JSON.parse(fsw.readFileSync(join(repo.root, "config/agent.local.json"), "utf-8")) as { base_url: string; api_key_env: string };
  const web = JSON.parse(fsw.readFileSync(join(repo.root, "config/agent/web.local.json"), "utf-8")) as { search: { endpoint: string } };
  return { baseUrl: local.base_url, apiKeyEnv: local.api_key_env, webEndpoint: web.search.endpoint, keychain: keychainPathInUse() };
}
