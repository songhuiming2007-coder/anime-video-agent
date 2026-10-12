// D80 R1：会话标题的形状规则（宿主 RPC、spawner、侧栏行内编辑共用一份；core session_titles.validate 再按同规则校验一遍）。

/** 与 core `session_titles.TITLE_MAX_CHARS` 同值：提示词要 ≤12 字，校验放宽到 20（理由见 core 注释） */
export const TITLE_MAX_CHARS = 20;

/** 去首尾空白后的标题有问题 → 返回人能看懂的原因；合格 → null。按码点计字（不按 UTF-16 码元） */
export function titleProblem(title: string): string | null {
  const t = title.trim();
  // eslint-disable-next-line no-control-regex
  if (t === "" || /[\u0000-\u001f\u007f-\u009f]/.test(t) || [...t].length > TITLE_MAX_CHARS) return `标题需为 1–${TITLE_MAX_CHARS} 字的单行文本`;
  return null;
}
