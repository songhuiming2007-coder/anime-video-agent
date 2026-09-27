// Spec 10 §2.9 的两条纯规则（便于单测）：变量名白名单与 `security -w` 输出的取值规则。
// 值的处理如实保留已知盲区（RF-15）：`security -w` 对非 ASCII 值输出十六进制串，它本身是可打印 ASCII，
// 会通过校验被当成密钥。不做解码、不做猜测——合法密钥也可能形如十六进制，host 无法区分。
import { KEY_ENV_NAME_RE } from "./constants";

/** PATH / PYTHONPATH / DYLD_INSERT_LIBRARIES / NODE_OPTIONS 这类会改变子进程行为的名字一律拒绝。 */
export function validKeyEnvName(name: string): boolean {
  return KEY_ENV_NAME_RE.test(name);
}

/** 去掉**恰好一个**末尾 `\n`；其余必须是可打印 ASCII、无空白、1~4096 字符，否则返回 null。 */
export function keyFromSecurityStdout(stdout: string): string | null {
  const v = stdout.endsWith("\n") ? stdout.slice(0, -1) : stdout;
  return /^[\x21-\x7e]{1,4096}$/.test(v) ? v : null;
}
