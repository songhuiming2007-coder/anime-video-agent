// Spec 10 §2.9：LLM 密钥来源 = macOS 钥匙串。KEYCHAIN_READ 结果的唯一读取者（TG-12）。
// 密钥只写进这一次 SESSION_* spawn 的 env[名字]，从不进 argv、spawn 日志、诊断、任何发往 renderer 的消息。
// 这里不读 config/（避免 TS 侧出现第二份规则）：名字由 core 的 PROBE_KEY_ENV 回答（C10-R2）。
import { KEYCHAIN_SERVICE } from "../shared/constants";
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
