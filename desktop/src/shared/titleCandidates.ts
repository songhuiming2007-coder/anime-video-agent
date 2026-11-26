// 从 07-titles.md 提取候选标题（09 定稿卡片一键填入；纯函数，零外部依赖）。
// 仅读「定稿 / 候选 / 备选」小节及无二级标题文件的顶层列表/表格；严格屏蔽代码块、封面/简介/标签/原料/规范/复盘区与分隔线之后内容。

export interface TitleCandidate {
  title: string;
  label?: string;
}

const STOP_HEADING_RE = /(简介|标签|原料|说明|备注|封面|素材|配字|规范|原则|复盘|记录|注意|BGM|互动)/i;
const FINAL_HEADING_RE = /^(定稿|最终|选定)/;
const CAND_HEADING_RE = /(候选|备选|标题)/;
const PLACEHOLDER_RE = /^[（(]?(待写|待定|待填|待选|空|略|无|暂无|TBD|TODO)[)）]?$/i;
const DASH_ONLY_RE = /^[-—–…·._\s]+$/;
const META_PREFIX_RE = /^(?:封面|封面字|封面配字|简介|标签|时长|发布时间|时间|日期|BV号|备注|说明|BGM|互动话题)[：:]/i;
const TITLE_PREFIX_RE = /^(?:最终选定|最终|选定|定稿)?标题[：:]\s*/;
const FINAL_LABEL_RE = /^(?:定稿|已定|最终|选定)/;
const KNOWN_LIST_LABEL_RE =
  /^(?:定稿|已定|最终|选定|备选|候选|金句|反问|下判断|复述再打|剧情钩子|钩子|对比|反差|立论|悬念|痛点|人物志|吐槽|考据|直球|风格|路子|角度|方向|叙事|情绪|反转|幽默|短评|总结|融合|诗意|哲思|现实)/;
const HEADER_TITLE_CELL_RE = /^(?:候选|备选|定稿)?标题$|^title$/i;
const HEADER_LABEL_CELL_RE = /^(?:路子|风格|方向|类型|角度|标签|label)$/i;
const BOILERPLATE_RE = /^(?:本文件由|判据见|选的时候看|这是知情的偏离|它不满足)/;
const MAX_FINAL_PROSE_LEN = 90;

const QUOTE_PAIRS: readonly [string, string][] = [
  ["「", "」"],
  ["『", "』"],
  ["“", "”"],
  ['"', '"'],
  ["'", "'"],
];

function isWholeWrapped(s: string, open: string, close: string): boolean {
  if (s.length < open.length + close.length || !s.startsWith(open) || !s.endsWith(close)) return false;
  if (open === close) {
    return s.indexOf(close, open.length) === s.length - close.length;
  }
  let depth = 0;
  for (let i = 0; i < s.length; i++) {
    if (s[i] === open) depth += 1;
    else if (s[i] === close) {
      depth -= 1;
      if (depth === 0 && i < s.length - 1) return false;
    }
  }
  return depth === 0;
}

function isWrappedInCodeOrQuotes(raw: string): boolean {
  const s = raw.trim();
  if (isWholeWrapped(s, "`", "`")) return true;
  for (const [open, close] of QUOTE_PAIRS) {
    if (isWholeWrapped(s, open, close)) return true;
  }
  return false;
}

function stripOuterQuotes(raw: string): string {
  let s = raw.trim();
  let changed = true;
  while (changed && s.length >= 2) {
    changed = false;
    if (isWholeWrapped(s, "**", "**") || isWholeWrapped(s, "__", "__")) {
      s = s.slice(2, -2).trim();
      changed = true;
      continue;
    }
    if (isWholeWrapped(s, "`", "`")) {
      s = s.slice(1, -1).trim();
      changed = true;
      continue;
    }
    for (const [open, close] of QUOTE_PAIRS) {
      if (isWholeWrapped(s, open, close)) {
        s = s.slice(1, -1).trim();
        changed = true;
        break;
      }
    }
  }
  return s;
}

/** 提取 `` `标题`——说明 `` 或 `**标题**——说明` 里的前置标题；仅在明确带 `已定/定稿` 前缀时才切裸破折号。 */
function extractBeforeNote(raw: string, allowPlainDash = false): string {
  const s = raw.trim();
  const codeHead = /^`([^`]+)`(?:\s*(?:——|--|[（(]).*)?$/.exec(s);
  if (codeHead) return codeHead[1].trim();
  const boldHead = /^\*\*([^*]+)\*\*(?:\s*(?:——|--|[（(]).*)?$/.exec(s);
  if (boldHead) return boldHead[1].trim();
  if (allowPlainDash) {
    const dashIdx = s.indexOf("——");
    if (dashIdx > 0) return s.slice(0, dashIdx).trim();
  }
  return s;
}

function cleanTitle(raw: string): string | null {
  const s = stripOuterQuotes(raw.replace(/\s+/g, " ").trim());
  if (!s || DASH_ONLY_RE.test(s) || PLACEHOLDER_RE.test(s)) return null;
  if (/[：:]$/.test(s)) return null;
  return s;
}

function cleanLabel(raw: string): string | undefined {
  const s = stripOuterQuotes(raw.replace(/\s+/g, " ").trim());
  if (!s || DASH_ONLY_RE.test(s) || PLACEHOLDER_RE.test(s) || /^\d+$/.test(s)) return undefined;
  return s;
}

/** 按非转义管道符切分 Markdown 表格行，还原 `\|` 为 `|`。 */
function splitTableRow(line: string): string[] {
  const s = line.trim();
  const cells: string[] = [];
  let cur = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\" && i + 1 < s.length && s[i + 1] === "|") {
      cur += "|";
      i += 1;
      continue;
    }
    if (s[i] === "|") {
      cells.push(cur.trim());
      cur = "";
      continue;
    }
    cur += s[i];
  }
  cells.push(cur.trim());
  if (s.startsWith("|") && cells.length > 0 && cells[0] === "") cells.shift();
  if (s.endsWith("|") && !s.endsWith("\\|") && cells.length > 0 && cells[cells.length - 1] === "") cells.pop();
  return cells;
}

function isSeparatorRow(cells: string[]): boolean {
  return cells.length > 0 && cells.every((c) => /^:?-{1,}:?$/.test(c));
}

function isParentheticalNote(s: string): boolean {
  return (s.startsWith("（") && s.endsWith("）")) || (s.startsWith("(") && s.endsWith(")"));
}

/** 解析 `## 定稿…` 节里的单条定稿（纯文本或单行引用 `>`）。 */
function parseFinalLine(raw: string): string | null {
  let s = raw.trim();
  if (!s) return null;
  // 去掉可选前导 Markdown 标题号（如 `> # 标题`）
  s = s.replace(/^#{1,6}\s+/, "").trim();
  if (!s || isParentheticalNote(s)) return null;
  const unwrapped = stripOuterQuotes(s);
  if (META_PREFIX_RE.test(unwrapped) || BOILERPLATE_RE.test(unwrapped)) return null;
  // 处理 `**标题：xxx**` / `**标题**：xxx` / `标题：xxx`
  let body = s;
  const boldKey = /^\*\*(?:最终选定|最终|选定|定稿)?标题\*\*[：:]\s*(.+)$/.exec(body);
  if (boldKey) {
    body = extractBeforeNote(boldKey[1]);
  } else if (TITLE_PREFIX_RE.test(unwrapped)) {
    body = extractBeforeNote(unwrapped.replace(TITLE_PREFIX_RE, ""));
  } else {
    body = extractBeforeNote(body);
  }
  const title = cleanTitle(body);
  if (!title) return null;
  if (META_PREFIX_RE.test(title) || BOILERPLATE_RE.test(title)) return null;
  if (title.length > MAX_FINAL_PROSE_LEN && /[。！？]/.test(title)) return null;
  return title;
}

/** 解析候选节中的行内定稿标注，例如 `**已定（2026-08-09）**：\`标题\`——说明` 或 `**定稿：标题**（说明）`。 */
function parseInlineFinal(line: string): TitleCandidate | null {
  const boldWrapped = /^\*\*(?:已定|定稿|最终定稿|选定)(?:[（(][^）)]*[）)])?[：:]\s*([^*]+?)\*\*/.exec(line);
  if (boldWrapped) {
    const title = cleanTitle(boldWrapped[1]);
    return title ? { title, label: "定稿" } : null;
  }
  const keyVal = /^(?:\*\*|__)?(?:已定|定稿|最终定稿|选定)(?:[（(][^）)]*[）)])?(?:\*\*|__)?[：:]\s*(.+)$/.exec(line);
  if (keyVal) {
    const title = cleanTitle(extractBeforeNote(keyVal[1], true));
    return title ? { title, label: "定稿" } : null;
  }
  return null;
}

interface ParsedListItem extends TitleCandidate {
  isFinal?: boolean;
}

function parseListItem(text: string): ParsedListItem | null {
  const body = text.trim();
  if (!body) return null;
  // 空分组父项（如 `**备选标题**：`、`备选：`）直接跳过
  if (/[：:]$/.test(stripOuterQuotes(body)) || /[：:]$/.test(body)) return null;
  // 完整被反引号或引号包裹的标题不按内部冒号拆 label
  if (isWrappedInCodeOrQuotes(body)) {
    const title = cleanTitle(body);
    return title ? { title } : null;
  }
  // 形如 `【路子】标题` 或 `[路子] 标题`
  const bracket = /^(?:【([^】]{1,16})】|\[([^\]]{1,16})\])\s*(.+)$/.exec(body);
  if (bracket) {
    const title = cleanTitle(bracket[3]);
    if (!title) return null;
    const label = cleanLabel(bracket[1] ?? bracket[2] ?? "");
    return label ? { title, label } : { title };
  }
  // 明确加粗的标签：`**路子**：标题` 或 `**最终选定标题**：\`标题\``
  const boldColon = /^(?:\*\*([^*]{1,18})\*\*|__([^_]{1,18})__)[：:]\s*(.*)$/.exec(body);
  if (boldColon) {
    const rawKey = (boldColon[1] ?? boldColon[2] ?? "").trim();
    const rawVal = boldColon[3].trim();
    if (!rawVal) return null;
    if (META_PREFIX_RE.test(`${rawKey}：`)) return null;
    const title = cleanTitle(extractBeforeNote(rawVal));
    if (!title) return null;
    if (FINAL_LABEL_RE.test(rawKey)) return { title, label: "定稿", isFinal: true };
    const label = cleanLabel(rawKey);
    return label ? { title, label } : { title };
  }
  // 非加粗冒号标签：仅当左侧为明确路子短词且不含书名号/标点/数字前缀时拆 label，避免截断 `《作品》：副标题`
  const colon = /^([^：:《》]{1,10})[：:]\s*(.+)$/.exec(body);
  if (colon) {
    const rawKey = colon[1].trim();
    if (META_PREFIX_RE.test(`${rawKey}：`)) return null;
    if (!/^\d/.test(rawKey) && !/[，。？！?!]/.test(rawKey) && KNOWN_LIST_LABEL_RE.test(rawKey)) {
      const rest = cleanTitle(extractBeforeNote(colon[2]));
      if (rest) {
        if (FINAL_LABEL_RE.test(rawKey)) return { title: rest, label: "定稿", isFinal: true };
        const label = cleanLabel(rawKey);
        return label ? { title: rest, label } : { title: rest };
      }
    }
  }
  const title = cleanTitle(body);
  return title ? { title } : null;
}

export function parseTitleCandidates(md: string, max = 12): TitleCandidate[] {
  const lines = md.split(/\r?\n/);
  const finals: TitleCandidate[] = [];
  const cands: TitleCandidate[] = [];

  type Mode = "none" | "final" | "cand" | "stop";
  let mode: Mode = "none";
  let hasSubHeading = false;
  let finalPicked = false;
  let tableTitleCol = -1;
  let tableLabelCol = -1;
  let inTable = false;
  let fenceChar: string | null = null;
  let fenceLen = 0;

  const pushUnique = (target: TitleCandidate[], seen: Set<string>, item: TitleCandidate) => {
    if (seen.has(item.title)) return;
    seen.add(item.title);
    target.push(item);
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();

    // 围栏代码块（``` / ~~~）完全屏蔽
    if (fenceChar !== null) {
      if (line.startsWith(fenceChar.repeat(fenceLen)) && new RegExp(`^\\${fenceChar}{${fenceLen},}\\s*$`).test(line)) {
        fenceChar = null;
        fenceLen = 0;
      }
      continue;
    }
    const fenceOpen = /^(`{3,})(?!.*`)|^(~{3,})/.exec(line);
    if (fenceOpen) {
      const marker = fenceOpen[1] ?? fenceOpen[2] ?? "";
      fenceChar = marker[0];
      fenceLen = marker.length;
      inTable = false;
      continue;
    }

    // 水平分隔线：若已经过候选/定稿区或已有顶层候选，后续通常是简介/原料区，直接停
    if (/^[-*_]{3,}$/.test(line)) {
      inTable = false;
      if (mode === "final" || mode === "cand" || (mode === "none" && (finals.length > 0 || cands.length > 0))) {
        mode = "stop";
      }
      continue;
    }

    // 标题行：一级标题在已进入正文或非首节时作为分界止位；二至四级标题按语义切换模式
    const heading = /^(#{1,4})\s+(.+?)\s*#*$/.exec(line);
    if (heading) {
      inTable = false;
      tableTitleCol = -1;
      tableLabelCol = -1;
      const level = heading[1].length;
      const h = heading[2].trim();
      if (level === 1) {
        if (hasSubHeading || mode === "final" || mode === "cand" || finals.length > 0 || cands.length > 0) {
          mode = "stop";
        }
        continue;
      }
      hasSubHeading = true;
      finalPicked = false;
      if (STOP_HEADING_RE.test(h)) {
        mode = "stop";
      } else if (FINAL_HEADING_RE.test(h)) {
        mode = "final";
      } else if (CAND_HEADING_RE.test(h)) {
        mode = "cand";
      } else {
        mode = "stop";
      }
      continue;
    }

    if (!line) {
      inTable = false;
      continue;
    }

    if (mode === "stop") continue;
    // 没有二级标题的极简文件允许直接解析表格或列表；一旦出现过二级标题则只读命中区
    if (mode === "none" && hasSubHeading) continue;

    // 引用块：仅在 `## 定稿…` 且尚未取到定稿时允许单行引用定稿（如 `> # 标题` / `> **标题**`），其余一律跳过
    if (line.startsWith(">")) {
      inTable = false;
      if (mode === "final" && !finalPicked) {
        const qBody = line.replace(/^>+\s*/, "");
        const title = parseFinalLine(qBody);
        if (title) {
          finals.push({ title, label: "定稿" });
          finalPicked = true;
        }
      }
      continue;
    }

    // Markdown 表格
    if (line.startsWith("|") && line.includes("|")) {
      const cells = splitTableRow(line);
      if (cells.length < 2) continue;
      if (isSeparatorRow(cells)) {
        inTable = true;
        continue;
      }
      if (!inTable) {
        const nextLine = (lines[i + 1] ?? "").trim();
        const nextIsSep = nextLine.startsWith("|") && nextLine.includes("|") && isSeparatorRow(splitTableRow(nextLine));
        if (nextIsSep) {
          const tIdx = cells.findIndex((c) => /标题|title/i.test(c));
          const lIdx = cells.findIndex((c) => /路子|风格|方向|类型|角度|label/i.test(c));
          tableTitleCol = tIdx >= 0 ? tIdx : cells.length - 1;
          tableLabelCol = lIdx >= 0 ? lIdx : cells.length >= 3 && tableTitleCol === cells.length - 1 ? cells.length - 2 : -1;
          inTable = true;
          continue;
        }
        const explicitTitleIdx = !/^\d+$/.test(cells[0] ?? "") ? cells.findIndex((c) => HEADER_TITLE_CELL_RE.test(stripOuterQuotes(c))) : -1;
        if (explicitTitleIdx >= 0) {
          tableTitleCol = explicitTitleIdx;
          tableLabelCol = cells.findIndex((c) => HEADER_LABEL_CELL_RE.test(stripOuterQuotes(c)));
          inTable = true;
          continue;
        }
        // 无表头行：3+ 列默认最后一列标题、倒数第二列路子；2 列默认第 2 列标题
        tableTitleCol = cells.length - 1;
        tableLabelCol = cells.length >= 3 ? cells.length - 2 : -1;
        inTable = true;
      }
      const rawTitle = cells[tableTitleCol >= 0 ? tableTitleCol : cells.length - 1] ?? "";
      const title = cleanTitle(rawTitle);
      if (!title) continue;
      const label = tableLabelCol >= 0 ? cleanLabel(cells[tableLabelCol] ?? "") : undefined;
      const isFinalRow =
        (label !== undefined && /定稿/.test(label)) ||
        (cells[0] !== undefined && stripOuterQuotes(cells[0]) === "★");
      if (isFinalRow) {
        finals.push({ title, label: label ?? "定稿" });
        if (mode === "final") finalPicked = true;
      } else {
        cands.push(label ? { title, label } : { title });
      }
      continue;
    }

    inTable = false;

    // 候选节里的行内已定/定稿标注（如 `**已定**：\`标题\`——说明` / `**定稿：标题**（说明）`）
    if (mode === "cand") {
      const inlineFinal = parseInlineFinal(line);
      if (inlineFinal) {
        finals.push(inlineFinal);
        continue;
      }
    }

    // 有序 / 无序列表项
    const listMatch = /^(?:[-*+]|\d+[.、)])\s+(.+)$/.exec(line);
    if (listMatch) {
      if (mode === "final") {
        // `## 定稿` 取到首条定稿后，下方列表通常是复盘说明（如 `- **点名观众。** …`），不再收录
        if (!finalPicked) {
          const item = parseListItem(listMatch[1]);
          if (item) {
            finals.push({ title: item.title, label: "定稿" });
            finalPicked = true;
          }
        }
        continue;
      }
      const item = parseListItem(listMatch[1]);
      if (item) {
        if (item.isFinal) finals.push({ title: item.title, label: "定稿" });
        else cands.push(item.label ? { title: item.title, label: item.label } : { title: item.title });
      }
      continue;
    }

    // 「## 定稿…」下的首条纯文本定稿行（取到第一条即止，避免吞入后续复盘长文）
    if (mode === "final" && !finalPicked) {
      const title = parseFinalLine(line);
      if (title) {
        finals.push({ title, label: "定稿" });
        finalPicked = true;
      }
    }
  }

  const out: TitleCandidate[] = [];
  const seen = new Set<string>();
  for (const item of finals) {
    pushUnique(out, seen, { title: item.title, label: item.label ?? "定稿" });
    if (out.length >= max) return out;
  }
  for (const item of cands) {
    pushUnique(out, seen, item);
    if (out.length >= max) return out;
  }
  return out;
}
