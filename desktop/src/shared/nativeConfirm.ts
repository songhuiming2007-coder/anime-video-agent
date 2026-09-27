// Spec 10 §2.4 第 5 层：browser 卡与抓取卡「批准」前经 main 弹原生确认框的触发判定与正文生成（纯函数）。
//
// 判定输入取自 host 自己保存的 request 帧，从不使用 renderer 传来的任何字段。
// 正文按字段排版（三轮 🔵-4）：确定性字段在前、全文不截断；模型填写的自由文本在后、单独标注并按
// CONFIRM_FREE_TEXT_MAX_CHARS 截断——键序、参数填充与自由文本里伪造的「URL: …」都挤不掉、冒充不了前面的字段。
// 一切 Unicode 格式字符（\p{Cf}：双向覆盖、零宽等）替换为可见的 ⟨U+XXXX⟩ 并在首行注明数量（二轮 🔵-4）。
import { CONFIRM_FREE_TEXT_MAX_CHARS } from "./constants";
import type { OutFrame } from "./convFrames";

const FREE_TEXT_LABEL = "以下为模型填写的理由（未经核实）：";
const CF = /\p{Cf}/gu;

function escapeInvisible(text: string): { text: string; count: number } {
  let count = 0;
  const out = text.replace(CF, (ch) => {
    count += 1;
    return `⟨U+${(ch.codePointAt(0) ?? 0).toString(16).toUpperCase().padStart(4, "0")}⟩`;
  });
  return { text: out, count };
}

export function needsNativeConfirm(req: OutFrame, decision: string): boolean {
  if (decision !== "approve" || req.t !== "request") return false;
  if (req.kind === "fetch") return true;
  return req.kind === "tool_call" && requestFields(req).tool === "browser";
}

function str(v: unknown): string {
  return typeof v === "string" ? v : v === null || v === undefined ? "" : String(v);
}

function clipText(text: string): string {
  const chars = Array.from(text);
  if (chars.length <= CONFIRM_FREE_TEXT_MAX_CHARS) return text;
  return `${chars.slice(0, CONFIRM_FREE_TEXT_MAX_CHARS).join("")}…（已截断，共 ${chars.length} 字符）`;
}

function requestFields(req: OutFrame): Record<string, unknown> {
  return (req.fields ?? {}) as Record<string, unknown>;
}

function browserDetail(req: OutFrame): string[] {
  const fields = requestFields(req);
  const args = (fields.args ?? {}) as Record<string, unknown>;
  const target = str(fields.target) || str(args.url);
  return [`操作：${str(args.action)}`, `目标：${target}`, FREE_TEXT_LABEL, clipText(str(args.reason))];
}

function fetchDetail(req: OutFrame): string[] {
  const fields = requestFields(req);
  return [
    `序号：${str(fields.no)}`,
    `标题：${str(fields.title)}`,
    `URL：${str(fields.url)}`,
    `类型：${str(fields.type)}`,
    `来源：${str(fields.source)}`,
    `预计时长：${str(fields.expected_dur)}`,
    FREE_TEXT_LABEL,
    clipText(str(fields.why)),
  ];
}

/** 标题 + 正文；正文首行在含有不可见字符时注明数量。 */
export function renderConfirmDetail(req: OutFrame): { title: string; detail: string } {
  const browser = req.kind === "tool_call";
  const title = browser ? "批准 browser 调用？" : "批准抓取素材？";
  const body = browser ? browserDetail(req) : fetchDetail(req);
  const escaped = body.map((line) => escapeInvisible(line));
  const invisible = escaped.reduce((n, e) => n + e.count, 0);
  const head = invisible > 0 ? [`含 ${invisible} 个不可见字符，已显式标出`] : [];
  return { title, detail: [...head, ...escaped.map((e) => e.text)].join("\n") };
}
