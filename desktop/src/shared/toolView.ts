// D80（R2 / R4）：对话流的工具行与回合折叠——纯函数、零 DOM。只决定「怎么显示」，ConvRow 的数据一条不删。
// 人选方案 A（2026-10-12，plans/2026-10-12-conv-liveliness-shots/options.shots.spec.ts）。
import type { ConvRow, ToolRow } from "./convFold";

/** 工具名 → 中文类别词（R2 ②）。15 个名字与 config/agent/tools.json 逐一核对（2026-10-12）；未登记的原样显示 */
export const TOOL_CATEGORY: Readonly<Record<string, string>> = {
  bash: "终端",
  read_artifact: "读取",
  read_status: "读取",
  list_episodes: "读取",
  write_episode_file: "写入",
  write_note: "写入",
  write_memory: "写入",
  cover_edit: "写入",
  run_pipeline: "流水线",
  web_search: "搜索",
  search_notes: "搜索",
  web_fetch: "网页",
  crawl: "网页",
  browser: "网页",
  acquire_propose: "素材",
};

export function toolCategory(name: string): string {
  return TOOL_CATEGORY[name] ?? name;
}

/** 摘要为空时的主文本兜底：这两个工具的参数本来就可以为空，只剩类别词「读取」说不清读了什么 */
const MAIN_FALLBACK: Readonly<Record<string, string>> = { read_status: "期状态", list_episodes: "期列表" };

/**
 * 状态词（R2 ②）。`run` 只在所属回合还在跑时成立：回合已结束却没收到结束帧的行（中断、进程退出）是「已停止」，
 * 不能假装还在动（D46 同一条）。「已拒绝」不在这里：拒绝是卡片行的状态，host 不解析工具原文去猜（S9-R1）。
 */
export type ToolStatus = "run" | "ok" | "fail" | "stopped";
export const STATUS_WORD: Readonly<Record<ToolStatus, string>> = { run: "执行中", ok: "已执行", fail: "执行失败", stopped: "已停止" };

export function toolStatus(row: ToolRow, turnRunning: boolean): ToolStatus {
  if (row.ok === null) return turnRunning ? "run" : "stopped";
  return row.ok ? "ok" : "fail";
}

/** 主文本：bash 用模型给的用途，没有时用命令摘要；其余用 core 给的摘要 */
export function mainText(row: ToolRow): string {
  if (row.name === "bash") return row.description ?? row.summary;
  return row.summary !== "" ? row.summary : (MAIN_FALLBACK[row.name] ?? "");
}

/** 次文本：bash 有用途时，命令摘要退到次文本（等宽灰）；否则无 */
export function subText(row: ToolRow): string {
  return row.name === "bash" && row.description !== null ? row.summary : "";
}

/** 耗时 < TOOL_SECS_MIN_MS 不显示（R2 ④）：亚秒级耗时对人没有信息量，≥1s 才可能是「这一步慢」的线索 */
export const TOOL_SECS_MIN_MS = 1000;
export function durText(ms: number | null): string {
  return ms === null || ms < TOOL_SECS_MIN_MS ? "" : `${(ms / 1000).toFixed(1)}s`;
}

/** 失败状态词的悬停提示：原文第一处非空行（只作提示；展开区仍逐字不截断，S9-R1） */
export function firstLine(text: string | null): string {
  if (text === null) return "";
  return text.split("\n").find((l) => l.trim() !== "")?.trim() ?? "";
}

/** 回合用时：<60 秒写「N 秒」，否则「M分S秒」（照 ZCode 的「已工作 1分12秒」） */
export function spanText(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s < 60 ? `${String(s)} 秒` : `${String(Math.floor(s / 60))}分${String(s % 60)}秒`;
}

// ---------------- 分组与回合切段 ----------------

/** 过程里的一项：单行，或同一回合内连续 ≥2 条 bash 折成的终端组（R2 ⑦） */
export type ProcItem = { k: "row"; index: number } | { k: "group"; key: string; indices: number[] };

export type TurnOutcome = "running" | "done" | "fail" | "rejected" | "stopped";

export type Segment =
  | { k: "loose"; index: number }
  | {
      k: "turn";
      /** 段首 user 行的下标——在一次挂载内稳定（行序列只追加） */
      key: string;
      user: number;
      process: ProcItem[];
      /** 折叠块之后照常显示的行（终稿答复、页脚、停机点、提示……），按原序 */
      tail: number[];
      running: boolean;
      outcome: TurnOutcome;
      tools: number;
      fails: number;
      /** 段首 user 行时刻；结束时刻 = 页脚时刻（历史重建的段没有页脚，为 null，不显示用时） */
      startAt: number;
      endAt: number | null;
    };

/** 进折叠块的行（R4 ①）：工具、作业输出、卡片记录、系统注入，以及不是终稿的答复与回合中途送达的插话 */
const PROCESS_KINDS = new Set<ConvRow["k"]>(["tool", "log", "card", "injection"]);

/** 连续 ≥2 条 bash 折成一组；其余原样 */
export function groupRuns(rows: readonly ConvRow[], indices: readonly number[]): ProcItem[] {
  const out: ProcItem[] = [];
  let run: number[] = [];
  const flush = () => {
    if (run.length >= 2) out.push({ k: "group", key: `g:${String(run[0])}`, indices: run });
    else for (const i of run) out.push({ k: "row", index: i });
    run = [];
  };
  for (const i of indices) {
    const r = rows[i];
    if (r.k === "tool" && r.name === "bash") run.push(i);
    else {
      flush();
      out.push({ k: "row", index: i });
    }
  }
  flush();
  return out;
}

/**
 * 把行序列切成回合段（R4 ①）。段 = 一条 user 行起，到该回合页脚（含）为止；页脚之后、下一条 user 之前的行（停机点等）
 * 归入该段的 tail。回合中途送达的插话（D79 steer：user 行紧跟在工具行之后、回合未结束）留在过程里，不另起一段。
 * 历史重建没有页脚：一条 user 行若跟在答复之后，即视为新一轮。第一条 user 之前的行是 loose（会话开头的提示等）。
 */
export function turnSegments(rows: readonly ConvRow[], convRunning: boolean): Segment[] {
  type Open = { user: number; body: number[]; closed: boolean; footer: number | null };
  const turns: Open[] = [];
  const out: (Segment | Open)[] = [];
  let cur: Open | null = null;
  rows.forEach((r, i) => {
    if (r.k === "user") {
      const last = cur && cur.body.length > 0 ? rows[cur.body[cur.body.length - 1]] : null;
      const steer = cur !== null && !cur.closed && last !== null && last.k !== "assistant" && cur.body.some((j) => PROCESS_KINDS.has(rows[j].k));
      if (steer && cur) {
        cur.body.push(i);
        return;
      }
      cur = { user: i, body: [], closed: false, footer: null };
      turns.push(cur);
      out.push(cur);
      return;
    }
    if (cur === null) {
      out.push({ k: "loose", index: i });
      return;
    }
    cur.body.push(i);
    if (r.k === "footer") {
      cur.closed = true;
      cur.footer = i;
    }
  });
  const lastTurn = turns.length > 0 ? turns[turns.length - 1] : null;
  return out.map((o) => ("k" in o ? o : finish(rows, o, convRunning && o === lastTurn && !o.closed)));
}

function finish(rows: readonly ConvRow[], t: { user: number; body: number[]; footer: number | null }, running: boolean): Segment {
  // 终稿答复：段内最后一条 assistant 行，且它之后再没有过程类行
  let finalAnswer = -1;
  for (let j = t.body.length - 1; j >= 0; j -= 1) {
    const r = rows[t.body[j]];
    if (r.k === "assistant") {
      finalAnswer = t.body[j];
      break;
    }
    if (PROCESS_KINDS.has(r.k) || r.k === "user") break;
  }
  const process: number[] = [];
  const tail: number[] = [];
  for (const i of t.body) {
    const r = rows[i];
    if (i === finalAnswer) tail.push(i);
    else if (PROCESS_KINDS.has(r.k) || r.k === "user" || r.k === "assistant") process.push(i);
    else tail.push(i);
  }
  const toolRows = process.map((i) => rows[i]).filter((r): r is ToolRow => r.k === "tool");
  const fails = toolRows.filter((r) => r.ok === false).length;
  const footer = t.footer === null ? null : rows[t.footer];
  const rejected = process.some((i) => {
    const r = rows[i];
    return r.k === "card" && r.status === "rejected";
  });
  const stopped =
    (footer !== null && footer.k === "footer" && footer.stopped !== "done") ||
    t.body.some((i) => rows[i].k === "exited") ||
    (!running && toolRows.some((r) => r.ok === null));
  const outcome: TurnOutcome = running ? "running" : fails > 0 ? "fail" : rejected ? "rejected" : stopped ? "stopped" : "done";
  return {
    k: "turn",
    key: `t:${String(t.user)}`,
    user: t.user,
    process: groupRuns(rows, process),
    tail,
    running,
    outcome,
    tools: toolRows.length,
    fails,
    startAt: rows[t.user].at,
    endAt: footer !== null ? footer.at : null,
  };
}

/** 默认开合（R4 ②③）：运行中展开；正常结束收起；有失败 / 被拒 / 被中断的回合不自动收起，免得藏住出问题的现场 */
export function turnDefaultOpen(seg: Extract<Segment, { k: "turn" }>): boolean {
  return seg.outcome !== "done";
}

/** 终端组默认开合（R2 ⑦）：组内有失败或正在跑的命令就展开，否则收起 */
export function groupDefaultOpen(rows: readonly ConvRow[], indices: readonly number[], turnRunning: boolean): boolean {
  return indices.some((i) => {
    const r = rows[i];
    return r.k === "tool" && (r.ok === false || (r.ok === null && turnRunning));
  });
}

/** 折叠行的标签（R4 ①②）：运行中「工作中 N 秒」；被中断「已停止 …」；其余「已工作 …」；历史段不知用时，只写「工作过程」 */
export function foldLabel(seg: Extract<Segment, { k: "turn" }>, now: number): string {
  if (seg.running) return `工作中 ${spanText(now - seg.startAt)}`;
  if (seg.endAt === null) return seg.outcome === "stopped" ? "已停止" : "工作过程";
  const span = spanText(seg.endAt - seg.startAt);
  return seg.outcome === "stopped" ? `已停止 · 用时 ${span}` : `已工作 ${span}`;
}
