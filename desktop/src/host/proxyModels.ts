// D153：会话进程不存在时，conv.models 的降级取列表路径——spawn 一次 core 的 PROBE_MODEL_LIST
// （host/ 纪律禁止 fetch/node:http，出网只能经 spawn；TG-3）。core 侧读生效配置的 base_url、
// 无 auth 直连代理 GET /models（D150 实测无 auth 返回标准 {data:[{id}]}），stdout 每行一个模型 ID。
// 进程在时不用这条路——照常把 models 命令帧透传给 core（core 顺带返回 efforts）。
// 失败如实 {ok:false,text}：renderer popover 显示错误行 + 「重试」（D151），不猜一份列表出来。
import type { CommandResult } from "./sessions";
import type { RunCore } from "./secrets";

const MAX_MODELS = 500;

export async function listProxyModels(run: RunCore, repoRoot: string): Promise<CommandResult> {
  const probe = await run("PROBE_MODEL_LIST", {}, { repoRoot });
  if (probe.code !== 0 || probe.timedOut) {
    const why = probe.stderrTail.trim().split("\n").pop() ?? "core 取列表失败";
    return { ok: false, text: `取模型列表失败：${why}${probe.timedOut ? "（超时）" : ""}` };
  }
  const items = probe.stdoutFull
    ? [...new Set(probe.stdoutFull.split("\n").map((l) => l.trim()).filter((l) => l !== ""))].sort().slice(0, MAX_MODELS)
    : [];
  if (probe.stdoutOverflow || probe.stdoutFull === null) {
    return { ok: false, text: "模型列表太长，截断后不可用" };
  }
  return { ok: true, text: `${items.length} 个模型`, items };
}
