// 本地假 LLM 端点（Spec 10 PR4：真实 core + 本地假端点）。OpenAI 兼容的 POST /v1/chat/completions。
//
// 与 Spec 9 的 Python 假端点（tests/test_agent_protocol.py::FakeEndpoint）同一纪律：
// 请求里 tool_calls 与随后 tool 消息的配对不齐 → 回 400（服务商对缺配对历史的已知行为，A2）。
// 这样「配对是否齐全」由真实 core 发出的请求体本身证明，而不是由测试另写一份期望。
//
// 只监听 127.0.0.1（TF-2：夹具断言 base_url 以 http://127.0.0.1: 开头）。零 fs 写入。
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

export type WireMessage = { role: string; content?: unknown; tool_calls?: { id: string; function: { name: string; arguments: string } }[]; tool_call_id?: string };
export type ChatRequest = { model: string; messages: WireMessage[]; tools?: unknown[]; tool_choice?: string };
/** 一次回复：助手消息本体；`delayMs` 让这一次响应延迟（TX-4/TX-8 需要「回合在跑」的窗口）。 */
export type Reply = { message: WireMessage; delayMs?: number };

export interface FakeLlm {
  url: string;
  requests: ChatRequest[];
  badPairings: number;
  /** 追加剧本：第 n 次请求取第 n 条；用完后重复最后一条。 */
  push(...replies: Reply[]): void;
  close(): Promise<void>;
}

export function assistant(content: string): Reply {
  return { message: { role: "assistant", content } };
}

/**
 * 不出网自检（TF-2 的延伸）：剧本里任何形如 URL 的字符串都必须指向 127.0.0.1。
 * 真实 core 会**真的执行**被批准的工具（web_fetch、acquire fetch 调 yt-dlp……），
 * 这里比会话类夹具的 assertLocalUrls 更宽：不限工具名，扫全部参数。
 */
function assertLocalOnly(value: unknown, where: string): void {
  if (typeof value === "string") {
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(value) && !value.startsWith("http://127.0.0.1:")) throw new Error(`假 LLM 剧本里有非本机 URL（${where}）：${value}`);
    return;
  }
  if (Array.isArray(value)) return value.forEach((v) => assertLocalOnly(v, where));
  if (value && typeof value === "object") for (const v of Object.values(value)) assertLocalOnly(v, where);
}

let callSeq = 0;
/** 构造一条带 tool_calls 的回复。id 全局递增，免得同一会话里两次调用撞号（判重看的是参数，不是 id）。 */
export function toolCalls(...calls: { name: string; args: Record<string, unknown> }[]): Reply {
  for (const c of calls) assertLocalOnly(c.args, c.name);
  return {
    message: {
      role: "assistant",
      content: null,
      tool_calls: calls.map((c) => ({ id: `call_${++callSeq}`, type: "function", function: { name: c.name, arguments: JSON.stringify(c.args) } })) as WireMessage["tool_calls"],
    },
  };
}

export function pairingOk(messages: WireMessage[]): boolean {
  let i = 0;
  while (i < messages.length) {
    const calls = messages[i].tool_calls ?? [];
    if (calls.length) {
      const ids = calls.map((c) => String(c.id));
      const following: string[] = [];
      let j = i + 1;
      while (j < messages.length && messages[j].role === "tool") following.push(String(messages[j].tool_call_id)), j++;
      if (following.join("\u0000") !== ids.join("\u0000")) return false;
      i = j;
      continue;
    }
    i++;
  }
  return true;
}

export async function startFakeLlm(): Promise<FakeLlm> {
  const replies: Reply[] = [];
  const state = { requests: [] as ChatRequest[], badPairings: 0 };
  const sockets = new Set<import("node:net").Socket>();
  const server: Server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      const body = JSON.parse(Buffer.concat(chunks).toString("utf-8")) as ChatRequest;
      state.requests.push(body);
      const index = state.requests.length - 1;
      const answer = (status: number, payload: unknown): void => {
        const data = Buffer.from(JSON.stringify(payload), "utf-8");
        res.writeHead(status, { "Content-Type": "application/json", "Content-Length": data.length });
        res.end(data);
      };
      if (!pairingOk(body.messages ?? [])) {
        state.badPairings++;
        answer(400, { error: { message: "tool messages not paired" } });
        return;
      }
      const reply = replies.length ? replies[Math.min(index, replies.length - 1)] : assistant("好");
      const send = (): void => answer(200, { choices: [{ message: reply.message }] });
      if (reply.delayMs) setTimeout(send, reply.delayMs);
      else send();
    });
  });
  server.on("connection", (s) => {
    sockets.add(s);
    s.on("close", () => sockets.delete(s));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as AddressInfo).port;
  return {
    url: `http://127.0.0.1:${port}/v1`,
    get requests() {
      return state.requests;
    },
    get badPairings() {
      return state.badPairings;
    },
    push: (...r: Reply[]) => void replies.push(...r),
    close: () =>
      new Promise<void>((resolve) => {
        for (const s of sockets) s.destroy();
        server.close(() => resolve());
      }),
  };
}
