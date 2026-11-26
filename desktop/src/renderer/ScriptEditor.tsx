// 02.5 停机点编辑器（Spec 11 §2.2/§4.4）：CodeMirror 6 源码编辑器 + markdown-it 预览 + 工具条 + 人审要点侧栏。
//
// 写路径只有一条：人的显式点击 → host → spawn `ava <期> /save-script`（stdin 传正文、指纹不符拒存）。
// 「封板」经 `/seal-script`（core 内跑终端同一条 `git diff --no-index`，空 diff 拒封）。
// 「机检」spawn `pipeline.check_script`，PASS/FAIL/INFO 原文照传——UI 不解析判定语义。
//
// 脏状态纪律（红队 🔴-2）：dirty 时「封板」与「从草稿新建」禁用——封板对象必须是磁盘上人已确认的版本。
// 主题只引用 CSS 变量（Spec 14 S11-R1 / VS-3）：本文件是 `@codemirror/*` 的唯一落点（§5.2，MUT-15）。
import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { history, defaultKeymap, historyKeymap } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorState } from "@codemirror/state";
import { EditorView, drawSelection, highlightActiveLine, keymap, lineNumbers } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { EDITOR_PREVIEW_DEBOUNCE_MS, SAVE_SCRIPT_MAX_BYTES } from "../shared/constants";
import { canCreateFromDraft, canSeal, diskVerdict, emptyEditor, expectOf, reduceEditor, type ScriptEditorState } from "../shared/editorState";
import { encodeMediaUrl } from "../shared/mediaUrl";
import type { SavedFingerprintJson, ScriptStatJson } from "../shared/protocol";
import { FloatingModal, FloatingTooltip } from "./floating";
import { Icon } from "./icons";
import { Disclosure, SegmentedBar } from "./motion";
import { MarkdownBody } from "./PreviewPane";
import { errText, RpcFailure, type RpcClient } from "./rpc";
import { Badge, StateView, StatusDot } from "./ui";

/** 人审要点（清单六条，真源在 runbook；TC-13 断言核心串齐全）。提醒不是判据（D18）。 */
export const REVIEW_CHECKLIST: readonly string[] = [
  "编辑判断立不立得住：反方立场像不像真人在说话，张力是否虚构",
  "事实核验：台词出处、说话人、关键集号与数字是否真实，拦截 AI 幻觉",
  "去模型味：删去油腻拔高、翻案腔与套路排比",
  "`人物:` 该写没写：对照机检 INFO 行逐段扫一眼。该写没写不可证伪，这一条是提醒不是判据",
  "说话人断言抽帧核对：「某某说」类断言抽帧确认说话人（字幕 Name 字段基本不填，机器判不了）",
  "访谈原话出处顺口带出：改稿涉及转述本人采访原话时，尽量顺口带出媒体名或年份（如「在二零一七年 CINRA 专访里」），避免泛写「她在采访中回忆」",
];

function renderChecklistItem(line: string) {
  const parts = line.split(/(`[^`]+`)/g);
  return parts.map((p, i) =>
    p.startsWith("`") && p.endsWith("`") ? <code key={i}>{p.slice(1, -1)}</code> : <Fragment key={i}>{p}</Fragment>,
  );
}

/** CodeMirror 主题：只引用 CSS 变量（VS-3 禁字面量；VS-11 禁 .cm-* 写进 ui.css）。 */
const editorTheme = EditorView.theme({
  "&": { color: "var(--fg)", backgroundColor: "var(--bg-input)", height: "100%" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)", lineHeight: "var(--leading-normal)" },
  // 光标跟正文色：浅色 --fg #1d1d1f，深色 --fg #ececf0。不许用 --accent 浅粉（白底/深底都看不清）。
  ".cm-content": { caretColor: "var(--fg)" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--fg)" },
  ".cm-gutters": { backgroundColor: "var(--bg-subtle)", color: "var(--fg-muted)", border: "none" },
  ".cm-activeLine": { backgroundColor: "var(--bg-hover)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--bg-hover)" },
  ".cm-selectionBackground, .cm-content ::selection": { backgroundColor: "var(--bg-selected)" },
  "&.cm-focused .cm-selectionBackground": { backgroundColor: "var(--bg-selected)" },
});

const mdHighlight = HighlightStyle.define([
  { tag: tags.heading, color: "var(--syntax-heading)", fontWeight: "var(--weight-semibold)" },
  { tag: tags.link, color: "var(--syntax-link)" },
  { tag: tags.url, color: "var(--syntax-link)" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: tags.strong, fontWeight: "var(--weight-semibold)" },
  { tag: tags.monospace, color: "var(--syntax-str)", fontFamily: "var(--font-mono)" },
  { tag: [tags.meta, tags.processingInstruction, tags.contentSeparator], color: "var(--syntax-meta)" },
  { tag: tags.keyword, color: "var(--syntax-key)" },
  { tag: tags.string, color: "var(--syntax-str)" },
  { tag: tags.number, color: "var(--syntax-num)" },
  { tag: [tags.bool, tags.null], color: "var(--syntax-lit)" },
]);

// ---------------- RF-7 的便利层（不是真相源）：脏状态切期/关窗拦截 + localStorage 草稿暂存 ----------------

let dirtyCheck: (() => boolean) | null = null;

/** 查询 02-script 编辑器当前是否有未保存改动（App 用来唤起应用内三键保存/丢弃弹窗）。 */
export function isScriptDirty(): boolean {
  return Boolean(dirtyCheck?.());
}

/** 切期 / 切仓库 / 关窗前询问是否丢弃未保存改动（保留旧签名供回退）。 */
export function confirmDiscardDirty(): boolean {
  if (!dirtyCheck?.()) return true;
  return window.confirm("02-script 编辑器有未保存的改动，确定丢弃？");
}

const draftKey = (epKey: string) => `ava.scriptDraft.${epKey}`;

function stashDraft(epKey: string, text: string): void {
  try {
    localStorage.setItem(draftKey(epKey), text);
  } catch {
    /* 配额满 / 隐私模式：便利层失败不影响真相源 */
  }
}

function peekStash(epKey: string): string | null {
  try {
    return localStorage.getItem(draftKey(epKey));
  } catch {
    return null;
  }
}

function clearStash(epKey: string): void {
  try {
    localStorage.removeItem(draftKey(epKey));
  } catch {
    /* 便利层失败不影响真相源 */
  }
}

const EDITOR_WIDE_MIN_PX = 760;
const DISK_POLL_MS = 2000;
type EditorPane = "src" | "render" | "tips";
type BusyAction = "save" | "seal" | "check" | null;
type FlashMsg = { kind: "ok" | "err"; text: string; n: number };

const PANES: readonly [EditorPane, string][] = [
  ["src", "源码"],
  ["render", "渲染"],
  ["tips", "要点"],
];

function isConflictError(e: unknown, text: string): boolean {
  if (e instanceof RpcFailure && e.error.code === "E_STALE") return true;
  return /冲突|已变|指纹不符|stale|mtime/i.test(text);
}

export function ScriptEditor({
  epKey,
  rpc,
  pendingSwitch = false,
  onResolveSwitch,
}: {
  epKey: string;
  rpc: RpcClient;
  pendingSwitch?: boolean;
  onResolveSwitch?: (action: "proceed" | "cancel" | "save-failed") => void;
}) {
  const [stat, setStat] = useState<ScriptStatJson | null>(null);
  const [ed, setEd] = useState<ScriptEditorState>(emptyEditor);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [saveMsg, setSaveMsg] = useState<FlashMsg | null>(null);
  const [sealMsg, setSealMsg] = useState<FlashMsg | null>(null);
  const [checkOut, setCheckOut] = useState<{ code: number | null; stdoutTail: string; stderrTail: string } | null>(null);
  const [checkExpanded, setCheckExpanded] = useState(false);
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const [reloadArm, setReloadArm] = useState(false);
  const [diskChanged, setDiskChanged] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [stashOffer, setStashOffer] = useState<string | null>(null);
  const [preview, setPreview] = useState("");
  const edRef = useRef(ed);
  edRef.current = ed;
  const hostRef = useRef<HTMLDivElement | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const [wide, setWide] = useState(false);
  const [pane, setPane] = useState<EditorPane>("src");
  const [tipsOpen, setTipsOpen] = useState(false);
  const viewRef = useRef<EditorView | null>(null);
  const suppress = useRef(false);
  const busy = busyAction !== null;

  // 首帧同步测量宽度，避免从窄态闪跳到宽态
  useLayoutEffect(() => {
    const el = shellRef.current;
    if (!el) return;
    setWide(el.getBoundingClientRect().width >= EDITOR_WIDE_MIN_PX);
    const ro = new ResizeObserver(([entry]) => setWide(entry.contentRect.width >= EDITOR_WIDE_MIN_PX));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  /** 把磁盘/本地文本灌进 CodeMirror（不触发 dirty：灌入不是人打的字）。可选保留光标与滚动位置。 */
  const setDoc = useCallback((text: string, preserveCursor = false) => {
    const v = viewRef.current;
    if (!v) return;
    const prevTop = preserveCursor ? v.scrollDOM.scrollTop : 0;
    const prevAnchor = preserveCursor ? Math.min(v.state.selection.main.anchor, text.length) : 0;
    suppress.current = true;
    v.dispatch({
      changes: { from: 0, to: v.state.doc.length, insert: text },
      ...(preserveCursor ? { selection: { anchor: prevAnchor } } : {}),
    });
    suppress.current = false;
    if (preserveCursor) {
      requestAnimationFrame(() => {
        if (viewRef.current === v) v.scrollDOM.scrollTop = prevTop;
      });
    }
  }, []);

  const loadGenRef = useRef(0);

  const load = useCallback(async () => {
    const gen = (loadGenRef.current += 1);
    setLoadErr(null);
    setLoaded(false);
    setSaveMsg(null);
    setSealMsg(null);
    setCheckOut(null);
    setReloadArm(false);
    setDiskChanged(false);
    try {
      const s = await rpc.call<ScriptStatJson>("script.stat", { epKey });
      if (loadGenRef.current !== gen) return;
      setStat(s);
      if (s.script) {
        const r = await fetch(encodeMediaUrl("episodes", `${epKey}/02-script.md`));
        if (loadGenRef.current !== gen) return;
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const text = await r.text();
        if (loadGenRef.current !== gen) return;
        setDoc(text);
        setPreview(text);
        setEd(reduceEditor(edRef.current, { type: "loaded", text, baselineFp: { size: String(s.script.size), mtimeNs: s.script.mtimeNs } }));
      } else if (s.draft) {
        const r = await fetch(encodeMediaUrl("episodes", `${epKey}/02-script.draft.md`));
        if (loadGenRef.current !== gen) return;
        const text = r.ok ? await r.text() : "";
        if (loadGenRef.current !== gen) return;
        setDoc(text);
        setPreview(text);
        setEd(reduceEditor(edRef.current, { type: "loaded", text, baselineFp: null }));
      } else {
        setDoc("");
        setPreview("");
        setEd(reduceEditor(edRef.current, { type: "loaded", text: "", baselineFp: null }));
        setLoadErr("找不到 02-script.md，也找不到 02-script.draft.md");
      }
      const stashed = peekStash(epKey);
      if (stashed !== null) setStashOffer(stashed);
      setLoaded(true);
    } catch (e) {
      if (loadGenRef.current !== gen) return;
      setLoadErr(errText(e));
    }
  }, [epKey, rpc, setDoc]);

  /** 静默原位同步磁盘更新：保留滚动位置、光标与机检结果，不卸载编辑器 */
  const syncFromDisk = useCallback(
    async (st: ScriptStatJson) => {
      if (!st.script) return;
      const gen = loadGenRef.current;
      try {
        const r = await fetch(encodeMediaUrl("episodes", `${epKey}/02-script.md`));
        if (!r.ok || loadGenRef.current !== gen) return;
        const text = await r.text();
        if (loadGenRef.current !== gen) return;
        if (edRef.current.dirty) {
          setDiskChanged(true);
          return;
        }
        setStat(st);
        setDiskChanged(false);
        setDoc(text, true);
        setEd(reduceEditor(edRef.current, { type: "reload", text, baselineFp: { size: String(st.script.size), mtimeNs: st.script.mtimeNs } }));
        setSaveMsg((prev) => ({ kind: "ok", text: "已同步磁盘上的新版本（agent 或别处改过）", n: (prev?.n ?? 0) + 1 }));
      } catch {
        /* 单次轮询读取失败静默跳过，下个周期重试 */
      }
    },
    [epKey, setDoc],
  );

  // CodeMirror 生命周期：挂载在 ShadowRoot 下绕开 CSP style-src 'self'（e2e A1 钉死）；
  // 载入中或错误态不挂载编辑宿主（避免空白编辑框闪烁、防止载入完成覆盖首帧输入，并满足 TE-6 editor-src 计数 0）。
  const hasHost = loaded && loadErr === null;
  useEffect(() => {
    if (!hasHost) return;
    const host = hostRef.current;
    if (!host) return;
    const shadow = host.shadowRoot ?? host.attachShadow({ mode: "open" });
    const view = new EditorView({
      state: EditorState.create({
        doc: edRef.current.text,
        extensions: [
          lineNumbers(),
          highlightActiveLine(),
          drawSelection(),
          history(),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          markdown(),
          syntaxHighlighting(mdHighlight),
          editorTheme,
          EditorView.lineWrapping,
          EditorView.updateListener.of((u) => {
            if (u.docChanged && !suppress.current) {
              const next = reduceEditor(edRef.current, { type: "edit", text: u.state.doc.toString() });
              if (next.dirty && !edRef.current.dirty) window.postMessage({ type: "ava-dirty", dirty: true }, "*");
              setEd(next);
            }
          }),
        ],
      }),
      parent: shadow,
      root: shadow,
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, [hasHost]);

  useEffect(() => {
    void load();
    return () => {
      loadGenRef.current += 1;
    };
  }, [load]);

  // D49-A S3：跟随磁盘。没有未保存改动 → 静默原位同步；有 → 只亮提示条，不覆盖人的改动
  useEffect(() => {
    if (!loaded || loadErr !== null) return;
    let stopped = false;
    const t = setInterval(() => {
      void rpc.call<ScriptStatJson>("script.stat", { epKey }).then(
        (st) => {
          if (stopped || !st.script) return;
          const v = diskVerdict(edRef.current, { size: String(st.script.size), mtimeNs: st.script.mtimeNs });
          if (v === "reload") {
            void syncFromDisk(st);
          } else if (v === "warn") {
            setDiskChanged(true);
          }
        },
        () => undefined,
      );
    }, DISK_POLL_MS);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [epKey, rpc, loaded, loadErr, syncFromDisk]);

  // 人时：审阅面装载成功才计时（Spec 11 §2.4）——错误态下不计（红队 ④ / TE-6）
  const surfaceUp = loadErr === null && loaded;
  useEffect(() => {
    if (!surfaceUp) return;
    void rpc.call("time.surface", { epKey, stop: "02.5", visible: "true" }).catch(() => undefined);
    return () => {
      void rpc.call("time.surface", { epKey, stop: "02.5", visible: "false" }).catch(() => undefined);
    };
  }, [epKey, rpc, surfaceUp]);

  // RF-7：脏状态在切期 / 关窗 / ⌘Q 退出时拦截；编辑期防抖暂存草稿到 localStorage（兜底防强退/崩溃）
  useEffect(() => {
    dirtyCheck = () => edRef.current.dirty;
    return () => {
      dirtyCheck = null;
      window.postMessage({ type: "ava-dirty", dirty: false }, "*");
    };
  }, []);
  const autoStashedRef = useRef(false);
  useLayoutEffect(() => {
    window.postMessage({ type: "ava-dirty", dirty: ed.dirty }, "*");
  }, [ed.dirty]);
  useEffect(() => {
    if (!ed.dirty) {
      // 编辑后按 ⌘Z 撤回至与磁盘基线完全一致时，清掉本次编辑期自动写入的暂存草稿（不误伤打开时已存在的旧 stashOffer）
      if (autoStashedRef.current) {
        autoStashedRef.current = false;
        clearStash(epKey);
      }
      return;
    }
    const t = window.setTimeout(() => {
      if (edRef.current.dirty) {
        stashDraft(epKey, edRef.current.text);
        autoStashedRef.current = true;
      }
    }, 400);
    return () => window.clearTimeout(t);
  }, [epKey, ed.dirty, ed.text]);
  useEffect(() => {
    const beforeUnload = (e: BeforeUnloadEvent) => {
      if (!edRef.current.dirty) return;
      stashDraft(epKey, edRef.current.text);
      e.preventDefault();
    };
    const onMenu = (e: MessageEvent) => {
      if (e.source === window && e.data?.type === "ava-menu" && e.data.action === "stash-draft" && edRef.current.dirty) {
        stashDraft(epKey, edRef.current.text);
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    window.addEventListener("message", onMenu);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      window.removeEventListener("message", onMenu);
    };
  }, [epKey]);

  // 预览节流（300 ms，Spec 11 §3.5）
  useEffect(() => {
    const t = setTimeout(() => setPreview(ed.text), EDITOR_PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [ed.text]);

  useEffect(() => {
    if (!reloadArm) return;
    const t = window.setTimeout(() => setReloadArm(false), 5000);
    return () => window.clearTimeout(t);
  }, [reloadArm]);

  useEffect(() => {
    if (saveMsg?.kind !== "ok") return;
    const t = window.setTimeout(() => setSaveMsg((cur) => (cur === saveMsg ? null : cur)), 4000);
    return () => window.clearTimeout(t);
  }, [saveMsg]);

  useEffect(() => {
    if (sealMsg?.kind !== "ok") return;
    const t = window.setTimeout(() => setSealMsg((cur) => (cur === sealMsg ? null : cur)), 5000);
    return () => window.clearTimeout(t);
  }, [sealMsg]);

  const doSave = useCallback(
    async (call: Promise<unknown>): Promise<boolean> => {
      setBusyAction("save");
      setSaveMsg(null);
      setSealMsg(null);
      setEd((s) => reduceEditor(s, { type: "saving" }));
      try {
        const fp = (await call) as SavedFingerprintJson;
        clearStash(epKey);
        setStashOffer(null);
        setDiskChanged(false);
        setEd((s) => reduceEditor(s, { type: "saved", baselineFp: { size: String(fp.size), mtimeNs: fp.mtimeNs } }));
        setSaveMsg((prev) => ({ kind: "ok", text: "已保存", n: (prev?.n ?? 0) + 1 }));
        void rpc.call<ScriptStatJson>("script.stat", { epKey }).then(setStat, () => undefined);
        return true;
      } catch (e) {
        const text = errText(e);
        setEd((s) => reduceEditor(s, { type: isConflictError(e, text) ? "conflict" : "save-failed" }));
        setSaveMsg((prev) => ({ kind: "err", text, n: (prev?.n ?? 0) + 1 }));
        return false;
      } finally {
        setBusyAction(null);
      }
    },
    [epKey, rpc],
  );

  const doSeal = useCallback(async (call: Promise<unknown>) => {
    setBusyAction("seal");
    setSealMsg(null);
    try {
      const r = (await call) as { bytes: number };
      setSealMsg((prev) => ({ kind: "ok", text: `已封板：补丁 ${r.bytes} 字节`, n: (prev?.n ?? 0) + 1 }));
    } catch (e) {
      setSealMsg((prev) => ({ kind: "err", text: errText(e), n: (prev?.n ?? 0) + 1 }));
    } finally {
      setBusyAction(null);
    }
  }, []);

  const doCheck = useCallback(async (call: Promise<unknown>) => {
    setBusyAction("check");
    setCheckOut(null);
    setCheckExpanded(false);
    try {
      setCheckOut((await call) as { code: number | null; stdoutTail: string; stderrTail: string });
    } catch (e) {
      setCheckOut({ code: null, stdoutTail: "", stderrTail: errText(e) });
    } finally {
      setBusyAction(null);
    }
  }, []);

  const off = busy || !loaded || loadErr !== null;
  const dirty = ed.dirty;
  const draftOnly = stat !== null && stat.script === null && stat.draft !== null;
  const showFromDraft = canCreateFromDraft(ed, draftOnly);
  const nothingToSave = stat !== null && stat.script !== null && !dirty;
  const tooBig = new TextEncoder().encode(ed.text).length > SAVE_SCRIPT_MAX_BYTES;
  const saveParams = () => ({ epKey, text: edRef.current.text, ...expectOf(edRef.current) });
  const sealDisabledReason = dirty ? "有未保存的改动，请先保存后再封板" : !canSeal(ed) ? "先保存为 02-script.md 后再封板" : null;

  return (
    <div
      className="editor-shell"
      ref={shellRef}
      data-testid="script-editor"
      data-dirty={dirty ? "1" : "0"}
      data-layout={wide ? "wide" : "narrow"}
      onKeyDown={(e) => {
        if (!e.nativeEvent.isTrusted) return;
        if (!e.metaKey && !e.ctrlKey) return;
        if (e.key.toLowerCase() !== "s") return;
        e.preventDefault();
        if (off || tooBig) return;
        void doSave(rpc.call("script.save", saveParams()));
      }}
    >
      <div className="toolbar" data-testid="editor-toolbar">
        {dirty && (
          <span className="editor-dirty-wrap ui-confirm-enter">
            <StatusDot tone="wait" />
            <span className="muted" data-testid="editor-dirty">
              未保存
            </span>
          </span>
        )}
        {tooBig && <span className="warn">正文超过 1 MiB 上限</span>}
        <span className="spacer" />
        {wide ? (
          <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm" data-testid="editor-tips-toggle" aria-pressed={tipsOpen} onClick={() => setTipsOpen((o) => !o)}>
            要点
          </button>
        ) : (
          <SegmentedBar activeKey={pane} testId="editor-pane-switch" label="编辑视图">
            {PANES.map(([k, label]) => (
              <button key={k} type="button" aria-pressed={pane === k} data-pane={k} onClick={() => setPane(k)}>
                {label}
              </button>
            ))}
          </SegmentedBar>
        )}
        {showFromDraft ? (
          <FloatingTooltip
            label={<>由草稿新建 02-script.md 定稿 <kbd className="ui-kbd">⌘S</kbd></>}
            renderTrigger={(t) => (
              <span ref={t.ref} {...t.props}>
                <button
                  type="button"
                  className="ui-btn ui-btn--primary ui-btn--sm"
                  data-testid="from-draft"
                  disabled={off || tooBig}
                  onClick={(e) => {
                    if (!e.nativeEvent.isTrusted) return;
                    void doSave(rpc.call("script.save", saveParams()));
                  }}
                >
                  {busyAction === "save" && <span className="ui-spinner" />}
                  从草稿新建
                </button>
              </span>
            )}
          />
        ) : (
          <FloatingTooltip
            label={
              nothingToSave ? (
                "当前没有未保存的改动"
              ) : draftOnly ? (
                <>保存修改为 02-script.md 定稿 <kbd className="ui-kbd">⌘S</kbd></>
              ) : (
                <>保存文稿 <kbd className="ui-kbd">⌘S</kbd></>
              )
            }
            renderTrigger={(t) => (
              <span ref={t.ref} {...t.props}>
                <button
                  type="button"
                  className={dirty && !tooBig && !off ? "ui-btn ui-btn--primary ui-btn--sm" : "ui-btn ui-btn--ghost ui-btn--sm"}
                  data-testid="save-script"
                  disabled={off || tooBig || nothingToSave}
                  onClick={(e) => {
                    if (!e.nativeEvent.isTrusted) return;
                    void doSave(rpc.call("script.save", saveParams()));
                  }}
                >
                  {busyAction === "save" && <span className="ui-spinner" />}
                  {draftOnly ? "保存为定稿" : "保存"}
                </button>
              </span>
            )}
          />
        )}
        <FloatingTooltip
          label={sealDisabledReason ?? "生成人审补丁并封板"}
          renderTrigger={(t) => (
            <span ref={t.ref} {...t.props}>
              <button
                type="button"
                className="ui-btn ui-btn--ghost ui-btn--sm"
                data-testid="seal-script"
                disabled={off || !canSeal(ed)}
                onClick={(e) => {
                  if (!e.nativeEvent.isTrusted) return;
                  void doSeal(rpc.call("script.seal", { epKey }));
                }}
              >
                {busyAction === "seal" && <span className="ui-spinner" />}
                封板
              </button>
            </span>
          )}
        />
        <button
          type="button"
          className="ui-btn ui-btn--ghost ui-btn--sm"
          data-testid="check-script"
          disabled={off}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            void doCheck(rpc.call("script.check", { epKey }));
          }}
        >
          {busyAction === "check" && <span className="ui-spinner" />}
          机检
        </button>
      </div>
      {loadErr !== null ? (
        <div className="pane-crash">
          <StateView kind="error" title={loadErr} />
          <button type="button" className="ui-btn ui-btn--sm" data-testid="editor-retry" onClick={() => void load()}>
            重新载入
          </button>
        </div>
      ) : !loaded ? (
        <StateView kind="loading" title="载入文稿…" />
      ) : (
        <div className="editor-split ui-fade-swap" data-layout={wide ? "wide" : "narrow"} data-pane={wide ? (tipsOpen ? "all" : "two") : pane}>
          <div className="editor-src" ref={hostRef} data-testid="editor-src" />
          <div key={wide ? "preview-wide" : `preview-${pane}`} className={`editor-preview${!wide ? " ui-fade-swap" : ""}`} data-testid="editor-preview">
            {preview.trim() === "" ? <div className="muted">（正文为空）</div> : <MarkdownBody text={preview} />}
          </div>
          <aside key={wide ? "tips-wide" : `tips-${pane}`} className={`checklist${!wide ? " ui-fade-swap" : ""}`} data-testid="review-checklist">
            <div className="ui-section">人审要点</div>
            <ol>
              {REVIEW_CHECKLIST.map((t) => (
                <li key={t}>{renderChecklistItem(t)}</li>
              ))}
            </ol>
          </aside>
        </div>
      )}
      <Disclosure open={saveMsg !== null} keepLastContent>
        {saveMsg !== null && (
          <div
            key={saveMsg.n}
            className={saveMsg.kind === "err" ? "notice notice--err voice-msg-bar ui-error-shake" : "notice voice-msg-bar ui-confirm-enter"}
            data-testid="save-msg"
            role={saveMsg.kind === "err" ? "alert" : "status"}
          >
            <Icon name={saveMsg.kind === "err" ? "alert" : "info"} size="sm" />
            <span>{saveMsg.text}</span>
          </div>
        )}
      </Disclosure>
      <Disclosure open={ed.conflict || diskChanged} keepLastContent>
        {(ed.conflict || diskChanged) && (
          <div className="notice notice--warn voice-msg-bar ui-error-shake" data-testid="save-conflict">
            <Icon name="alert" size="sm" />
            <span>{ed.conflict ? "磁盘版本已变（可能已在别处修改），未保存。本地内容仍在编辑器里。" : "磁盘上的版本已变（agent 或别处改过）。你有未保存改动，没有自动载入。"}</span>
            <button
              type="button"
              className={reloadArm ? "ui-btn ui-btn--danger ui-btn--sm" : "ui-btn ui-btn--sm"}
              data-testid="reload-script"
              disabled={busy || !loaded}
              onClick={() => {
                if (!reloadArm) {
                  setReloadArm(true);
                  return;
                }
                clearStash(epKey);
                setStashOffer(null);
                void load();
              }}
            >
              {reloadArm ? "确认丢弃本地改动并重新载入？" : "重新载入磁盘版本"}
            </button>
            {reloadArm && (
              <button
                type="button"
                className="ui-btn ui-btn--ghost ui-btn--sm"
                data-testid="reload-script-cancel"
                disabled={busy || !loaded}
                onClick={() => setReloadArm(false)}
              >
                取消
              </button>
            )}
          </div>
        )}
      </Disclosure>
      <Disclosure open={stashOffer !== null} keepLastContent>
        {stashOffer !== null && (
          <div className="notice voice-msg-bar ui-card-enter" data-testid="stash-offer">
            <Icon name="info" size="sm" />
            <span>检测到上次未保存的草稿。</span>
            <button
              type="button"
              className="ui-btn ui-btn--sm"
              onClick={() => {
                const draft = stashOffer;
                setDoc(draft);
                setEd((s) => reduceEditor(s, { type: "edit", text: draft }));
                clearStash(epKey);
                setStashOffer(null);
              }}
            >
              恢复草稿
            </button>
            <button
              type="button"
              className="ui-btn ui-btn--ghost ui-btn--sm"
              data-testid="stash-dismiss"
              onClick={() => {
                clearStash(epKey);
                setStashOffer(null);
              }}
            >
              忽略
            </button>
          </div>
        )}
      </Disclosure>
      <Disclosure open={sealMsg !== null} keepLastContent>
        {sealMsg !== null && (
          <div
            key={sealMsg.n}
            className={sealMsg.kind === "err" ? "notice notice--err voice-msg-bar ui-error-shake" : "notice voice-msg-bar ui-confirm-enter"}
            data-testid="seal-msg"
            role={sealMsg.kind === "err" ? "alert" : "status"}
          >
            <Icon name={sealMsg.kind === "err" ? "alert" : "info"} size="sm" />
            <span>{sealMsg.text}</span>
          </div>
        )}
      </Disclosure>
      <Disclosure open={checkOut !== null} keepLastContent>
        {checkOut !== null && (
          <div>
            <div className="editor-check-head">
              <Badge tone={checkOut.code === 0 ? "ok" : "danger"}>{checkOut.code === 0 ? "机检通过" : "机检未通过"}</Badge>
              <span className="muted">退出码 {checkOut.code ?? "—"}</span>
              <span className="spacer" />
              <button
                type="button"
                className="ui-btn ui-btn--ghost ui-btn--sm"
                onClick={() => setCheckExpanded((v) => !v)}
              >
                {checkExpanded ? "收起" : "展开全部"}
              </button>
              <button
                type="button"
                className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
                aria-label="关闭机检输出"
                onClick={() => setCheckOut(null)}
              >
                <Icon name="close" size="sm" />
              </button>
            </div>
            <pre className="tail" data-testid="check-output" data-code={checkOut.code ?? ""} data-expanded={checkExpanded ? "1" : "0"}>
              {checkOut.stdoutTail}
              {checkOut.stderrTail}
            </pre>
          </div>
        )}
      </Disclosure>
      <FloatingModal
        open={pendingSwitch}
        onClose={() => {
          if (!busy) onResolveSwitch?.("cancel");
        }}
        testId="dirty-script-modal"
        className="confirm-modal"
        ariaLabel="文稿有未保存的改动"
      >
        <h3 className="confirm-modal-title">文稿有未保存的改动</h3>
        <p className="confirm-modal-body">离开前要如何处理当前编辑中的 02-script.md？</p>
        <div className="confirm-modal-actions">
          <button
            type="button"
            className="ui-btn ui-btn--ghost ui-btn--sm"
            data-testid="dirty-cancel"
            disabled={busy}
            onClick={() => onResolveSwitch?.("cancel")}
          >
            取消
          </button>
          <button
            type="button"
            className="ui-btn ui-btn--danger ui-btn--sm"
            data-testid="dirty-discard"
            disabled={busy}
            onClick={() => {
              clearStash(epKey);
              setStashOffer(null);
              onResolveSwitch?.("proceed");
            }}
          >
            丢弃改动
          </button>
          <button
            type="button"
            className="ui-btn ui-btn--primary ui-btn--sm"
            data-testid="dirty-save-and-switch"
            disabled={off || tooBig}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void doSave(rpc.call("script.save", saveParams())).then((ok) => {
                onResolveSwitch?.(ok ? "proceed" : "save-failed");
              });
            }}
          >
            {busyAction === "save" && <span className="ui-spinner" />}
            {busyAction === "save" ? "正在保存…" : "保存并切换"}
          </button>
        </div>
      </FloatingModal>
    </div>
  );
}
