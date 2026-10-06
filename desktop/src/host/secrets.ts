// Spec 10 §2.9：LLM 密钥来源 = macOS 钥匙串。KEYCHAIN_READ 结果的唯一读取者（TG-12）。
// Spec 15 §2.7（2026-10-06 修订 §2.9）：另按 web 检索链上声明的名字读取并注入，失败只影响该家、不影响 LLM。
// 密钥只写进这一次 SESSION_* spawn 的 env[名字]，从不进 argv、spawn 日志、诊断、任何发往 renderer 的消息。
// 这里不读 config/（避免 TS 侧出现第二份规则）：名字由 core 的 PROBE_KEY_ENV 回答（C10-R2）。
import { KEYCHAIN_SERVICE, WEB_KEY_MAX } from "../shared/constants";
import { keyFromSecurityStdout, validKeyEnvName } from "../shared/secretsRules";
import type { CoreResult, Template, TemplateArgs } from "./spawner";

export type RunCore = <T extends Template>(t: T, args: TemplateArgs[T], ctx: { repoRoot: string }) => Promise<CoreResult>;

export type KeyResolution = { name: string; value: string } | { problem: string };

const OPT_OUT = "不注入密钥，会话照常启动并按 Spec 9 如实降级";

function setupHint(name: string): string {
  return `设置密钥：security add-generic-password -s ${KEYCHAIN_SERVICE} -a ${name} -w`;
}

export async function resolveLlmKey(run: RunCore, repoRoot: string): Promise<KeyResolution> {
  const probe = await run("PROBE_KEY_ENV", {}, { repoRoot });
  const name = probe.stdoutTail.trim();
  if (probe.code !== 0 || name === "") {
    return { problem: `未能读取 config/agent*.json 的 api_key_env（${OPT_OUT}）` };
  }
  if (!validKeyEnvName(name)) {
    return { problem: `配置指名的变量名不合规：${name}｜${setupHint(name)}｜${OPT_OUT}` };
  }
  const read = await run("KEYCHAIN_READ", { envName: name }, { repoRoot });
  if (read.code === 44) return { problem: `钥匙串里没有该密钥｜${setupHint(name)}｜${OPT_OUT}` };
  if (read.code !== 0) return { problem: `钥匙串读取失败（退出码 ${read.code ?? "signal"}）｜${setupHint(name)}｜${OPT_OUT}` };
  const value = keyFromSecurityStdout(read.stdoutTail);
  if (value === null) return { problem: `钥匙串里的值不是可打印 ASCII（只存密钥原文）｜${setupHint(name)}｜${OPT_OUT}` };
  return { name, value };
}

/**
 * Spec 15 §2.7：web 检索链上声明的密钥（名字由 core 的 PROBE_WEB_KEY_ENVS 回答）→ { 名字: 值 }。
 * 整体一个 try（第 6 条）：任何异常都等同「无 web 密钥」，绝不冒出去拖垮会话启动；
 * 探针退出码非 0 / 超时 / 出现不合 KEY_ENV_NAME_RE 的行 → 整体不注入；重复只取一次；与 LLM 同名跳过
 * （core 的 §2.4 校验已保证正常配置不会同名，这里只是纵深）；超过 WEB_KEY_MAX 只取前几个。
 * 单个名字钥匙串缺条目 / 读失败 / 值不合法 → 只是不注入该名字，core 侧该家「未就绪」并在错误消息里写明放法。
 * 诊断只写名字与退出码，从不写值。
 */
export async function resolveWebKeys(
  run: RunCore,
  repoRoot: string,
  llmName: string | null,
  diag: (msg: string) => void,
): Promise<Record<string, string>> {
  try {
    const probe = await run("PROBE_WEB_KEY_ENVS", {}, { repoRoot });
    if (probe.code !== 0 || probe.timedOut) {
      diag(`web 检索密钥探测失败（退出码 ${probe.code ?? "signal"}${probe.timedOut ? "，超时" : ""}），本会话不注入 web 密钥`);
      return {};
    }
    const lines = probe.stdoutTail.split("\n").filter((l) => l !== "");
    const bad = lines.filter((l) => !validKeyEnvName(l));
    if (bad.length > 0) {
      diag(`web 检索密钥探测输出含 ${bad.length} 行不合规的变量名，本会话不注入 web 密钥`);
      return {};
    }
    let names = [...new Set(lines)].filter((n) => n !== llmName);
    if (names.length > WEB_KEY_MAX) {
      diag(`web 检索链声明了 ${names.length} 个密钥变量，只注入前 ${WEB_KEY_MAX} 个`);
      names = names.slice(0, WEB_KEY_MAX);
    }
    const out: Record<string, string> = {};
    for (const name of names) {
      const read = await run("KEYCHAIN_READ", { envName: name }, { repoRoot });
      if (read.code !== 0) {
        const why = read.code === 44 ? "钥匙串里没有该密钥" : `钥匙串读取失败（退出码 ${read.code ?? "signal"}）`;
        diag(`web 检索密钥 ${name}：${why}，该检索服务本会话未就绪｜${setupHint(name)}`);
        continue;
      }
      const value = keyFromSecurityStdout(read.stdoutTail);
      if (value === null) {
        diag(`web 检索密钥 ${name}：钥匙串里的值不是可打印 ASCII，未注入｜${setupHint(name)}`);
        continue;
      }
      out[name] = value;
    }
    return out;
  } catch (e) {
    diag(`web 检索密钥解析异常（${e instanceof Error ? e.name : "unknown"}），本会话不注入 web 密钥`);
    return {};
  }
}
