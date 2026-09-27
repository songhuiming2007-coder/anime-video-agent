// 02.5 停机点编辑器（Spec 11 §2.2/§4.4）：CodeMirror 6 源码编辑器 + markdown-it 预览 + 工具条 + 人审要点侧栏。
//
// 写路径只有一条：人的显式点击 → host → spawn `ava <期> /save-script`（stdin 传正文、指纹不符拒存）。
// 「封板」经 `/seal-script`（core 内跑终端同一条 `git diff --no-index`，空 diff 拒封）。
// 「机检」spawn `pipeline.check_script`，PASS/FAIL/INFO 原文照传——UI 不解析判定语义。
//
// 脏状态纪律（红队 🔴-2）：dirty 时「封板」与「从草稿新建」禁用——封板对象必须是磁盘上人已确认的版本。
// 主题只引用 CSS 变量（Spec 14 S11-R1 / VS-3）：本文件是 `@codemirror/*` 的唯一落点（§5.2，MUT-15）。
import { useCallback, useEffect, useRef, useState } from "react";
import { history, defaultKeymap, historyKeymap } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorState } from "@codemirror/state";
import { EditorView, drawSelection, highlightActiveLine, keymap, lineNumbers } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { EDITOR_PREVIEW_DEBOUNCE_MS, SAVE_SCRIPT_MAX_BYTES } from "../shared/constants";
import { canCreateFromDraft, canSeal, emptyEditor, expectOf, reduceEditor, type ScriptEditorState } from "../shared/editorState";
import { encodeMediaUrl } from "../shared/mediaUrl";
import type { SavedFingerprintJson, ScriptStatJson } from "../shared/protocol";
import { Icon } from "./icons";
import { MarkdownBody } from "./PreviewPane";
import { errText, type RpcClient } from "./rpc";
import { StateView } from "./ui";

/** 人审要点（清单五条，真源在 runbook；TC-13 断言两处关键串齐全）。提醒不是判据（D18）。 */
export const REVIEW_CHECKLIST: readonly string[] = [
  "编辑判断立不立得住：反方立场像不像真人在说话，张力是否虚构",
  "事实核验：台词出处、说话人、关键集号与数字是否真实，拦截 AI 幻觉",
  "去模型味：删去油腻拔高、翻案腔与套路排比",
  "`人物:` 该写没写：对照机检 INFO 行逐段扫一眼。该写没写不可证伪，这一条是提醒不是判据",
  "说话人断言抽帧核对：「某某说」类断言抽帧确认说话人（字幕 Name 字段基本不填，机器判不了）",
];

/** CodeMirror 主题：只引用 CSS 变量（VS-3 禁字面量；VS-11 禁 .cm-* 写进 ui.css）。 */
const editorTheme = EditorView.theme({
  "&": { color: "var(--fg)", backgroundColor: "var(--bg-input)", height: "100%" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)", lineHeight: "var(--leading-normal)" },
  ".cm-content": { caretColor: "var(--accent)" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)" },
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

/** 切期 / 切仓库 / 关窗前询问是否丢弃未保存改动（ScriptEditor 挂载期间才有守卫）。 */
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

function takeStash(epKey: string): string | null {
  try {
    const v = localStorage.getItem(draftKey(epKey));
    if (v !== null) localStorage.removeItem(draftKey(epKey));
    return v;
  } catch {
    return null;
  }
}

export function ScriptEditor({ epKey, rpc }: { epKey: string; rpc: RpcClient }) {
  const [stat, setStat] = useState<ScriptStatJson | null>(null);
  const [ed, setEd] = useState<ScriptEditorState>(emptyEditor);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [sealMsg, setSealMsg] = useState<string | null>(null);
  const [checkOut, setCheckOut] = useState<{ code: number | null; stdoutTail: string; stderrTail: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [reloadArm, setReloadArm] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [stashOffer, setStashOffer] = useState<string | null>(null);
  const [preview, setPreview] = useState("");
  const edRef = useRef(ed);
  edRef.current = ed;
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const suppress = useRef(false);

  /** 把磁盘/本地文本灌进 CodeMirror（不触发 dirty：灌入不是人打的字）。 */
  const setDoc = useCallback((text: string) => {
    const v = viewRef.current;
    if (!v) return;
    suppress.current = true;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: text } });
    suppress.current = false;
  }, []);

  const load = useCallback(async () => {
    setLoadErr(null);
    setLoaded(false);
    setSaveMsg(null);
    setSealMsg(null);
    setCheckOut(null);
    setReloadArm(false);
    try {
      const s = await rpc.call<ScriptStatJson>("script.stat", { epKey });
      setStat(s);
      if (s.script) {
        const r = await fetch(encodeMediaUrl("episodes", `${epKey}/02-script.md`));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const text = await r.text();
        setDoc(text);
        setEd(reduceEditor(edRef.current, { type: "loaded", text, baselineFp: { size: String(s.script.size), mtimeNs: s.script.mtimeNs } }));
      } else if (s.draft) {
        const r = await fetch(encodeMediaUrl("episodes", `${epKey}/02-script.draft.md`));
        const text = r.ok ? await r.text() : "";
        setDoc(text);
        setEd(reduceEditor(edRef.current, { type: "loaded", text, baselineFp: null }));
      } else {
        setDoc("");
        setEd(reduceEditor(edRef.current, { type: "loaded", text: "", baselineFp: null }));
        setLoadErr("找不到 02-script.md，也找不到 02-script.draft.md");
      }
      const stashed = takeStash(epKey);
      if (stashed !== null) setStashOffer(stashed);
      setLoaded(true);
    } catch (e) {
      setLoadErr(errText(e));
    }
  }, [epKey, rpc, setDoc]);

  // CodeMirror 生命周期：只在挂载时建一次；换期由 App 以 key 卸载重建。
  //
  // CSP（生产 index.html 是 `style-src 'self'`）：style-mod 在普通 document 根下靠**注入 <style> 标签**上样式，
  // 会被 CSP 静默拦掉——主题、Markdown 高亮、自绘光标全部失效（外观上是一段裸文本）。
  // 传 `root` 为 ShadowRoot 时 style-mod 改走 `CSSStyleSheet` + `root.adoptedStyleSheets`，构造式样式表不受
  // style-src 约束（e2e A1 用计算样式对拍钉死：背景 = var(--bg-input)、高亮色 ≠ 正文色、光标非零宽）。
  // 因此**不得**把这层 shadow 包装去掉，也不得把编辑器挂回 document 根。
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const shadow = host.shadowRoot ?? host.attachShadow({ mode: "open" });
    const view = new EditorView({
      state: EditorState.create({
        doc: "",
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
            if (u.docChanged && !suppress.current) setEd((s) => reduceEditor(s, { type: "edit", text: u.state.doc.toString() }));
          }),
        ],
      }),
      parent: shadow, // 直接挂进 shadow 根：`.cm-editor{height:100%}` 的包含块就是宿主 .editor-src
      root: shadow,
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 人时：审阅面**装载成功**才计时（Spec 11 §2.4）——错误态下人没在审阅，不计（红队 ④）。
  // `loaded` 而不是 `stat`：`script.stat` 只看得到文件存在与指纹，正文还要经 ava-media:// 取一次，
  // 取不到时 stat 已经回来了——只看 stat 会开一个几十毫秒的假区间（e2e TE-6 钉的就是这个）。
  const surfaceUp = loadErr === null && loaded;
  useEffect(() => {
    if (!surfaceUp) return;
    void rpc.call("time.surface", { epKey, stop: "02.5", visible: "true" }).catch(() => undefined);
    return () => {
      void rpc.call("time.surface", { epKey, stop: "02.5", visible: "false" }).catch(() => undefined);
    };
  }, [epKey, rpc, surfaceUp]);

  // RF-7：脏状态在切期 / 关窗时拦截；草稿同时进 localStorage（便利层）
  useEffect(() => {
    dirtyCheck = () => edRef.current.dirty;
    return () => {
      dirtyCheck = null;
    };
  }, []);
  useEffect(() => {
    const beforeUnload = (e: BeforeUnloadEvent) => {
      if (!edRef.current.dirty) return;
      stashDraft(epKey, edRef.current.text);
      e.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [epKey]);

  // 预览节流（300 ms，Spec 11 §3.5）
  useEffect(() => {
    const t = setTimeout(() => setPreview(ed.text), EDITOR_PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [ed.text]);

  const doSave = useCallback(
    async (call: Promise<unknown>) => {
      setBusy(true);
      setSaveMsg(null);
      setSealMsg(null);
      setEd((s) => reduceEditor(s, { type: "saving" }));
      try {
        const fp = (await call) as SavedFingerprintJson;
        setEd((s) => reduceEditor(s, { type: "saved", baselineFp: { size: String(fp.size), mtimeNs: fp.mtimeNs } }));
        setSaveMsg("已保存");
        void rpc.call<ScriptStatJson>("script.stat", { epKey }).then(setStat, () => undefined);
      } catch (e) {
        setEd((s) => reduceEditor(s, { type: "conflict" }));
        setSaveMsg(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [epKey, rpc],
  );

  const doSeal = useCallback(async (call: Promise<unknown>) => {
    setBusy(true);
    setSealMsg(null);
    try {
      const r = (await call) as { bytes: number };
      setSealMsg(`已封板：补丁 ${r.bytes} 字节`);
    } catch (e) {
      setSealMsg(errText(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const doCheck = useCallback(async (call: Promise<unknown>) => {
    setBusy(true);
    setCheckOut(null);
    try {
      setCheckOut((await call) as { code: number | null; stdoutTail: string; stderrTail: string });
    } catch (e) {
      setCheckOut({ code: null, stdoutTail: "", stderrTail: errText(e) });
    } finally {
      setBusy(false);
    }
  }, []);

  const off = busy || loadErr !== null;
  const dirty = ed.dirty;
  const tooBig = new TextEncoder().encode(ed.text).length > SAVE_SCRIPT_MAX_BYTES;
  const saveParams = () => ({ epKey, text: edRef.current.text, ...expectOf(edRef.current) });

  return (
    <div className="editor-shell" data-testid="script-editor" data-dirty={dirty ? "1" : "0"}>
      <div className="toolbar" data-testid="editor-toolbar">
        <button
          className="ui-btn"
          data-testid="save-script"
          disabled={off || tooBig}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            void doSave(rpc.call("script.save", saveParams()));
          }}
        >
          保存
        </button>
        {stat !== null && stat.script === null && stat.draft !== null && (
          <button
            className="ui-btn"
            data-testid="from-draft"
            disabled={off || !canCreateFromDraft(ed, true)}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void doSave(rpc.call("script.save", saveParams()));
            }}
          >
            从草稿新建
          </button>
        )}
        <button
          className="ui-btn"
          data-testid="seal-script"
          disabled={off || !canSeal(ed)}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            void doSeal(rpc.call("script.seal", { epKey }));
          }}
        >
          封板
        </button>
        <button
          className="ui-btn"
          data-testid="check-script"
          disabled={off}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            void doCheck(rpc.call("script.check", { epKey }));
          }}
        >
          机检
        </button>
        {dirty && (
          <span className="muted" data-testid="editor-dirty">
            有未保存改动
          </span>
        )}
        {tooBig && <span className="warn">正文超过 1 MiB 上限</span>}
      </div>
      {loadErr !== null ? (
        <StateView kind="error" title={loadErr} />
      ) : (
        <div className="editor-split">
          <div
            className="editor-src"
            ref={hostRef}
            data-testid="editor-src"
            onKeyDown={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              if (!e.metaKey && !e.ctrlKey) return;
              if (e.key.toLowerCase() !== "s") return;
              e.preventDefault();
              if (off || tooBig) return;
              void doSave(rpc.call("script.save", saveParams()));
            }}
          />
          <div className="editor-preview" data-testid="editor-preview">
            {preview.trim() === "" ? <div className="muted">（正文为空）</div> : <MarkdownBody text={preview} />}
          </div>
          <aside className="checklist" data-testid="review-checklist">
            <div className="ui-section">人审要点（真源在 runbook，此处只读镜像）</div>
            <ol>
              {REVIEW_CHECKLIST.map((t) => (
                <li key={t}>{t}</li>
              ))}
            </ol>
          </aside>
        </div>
      )}
      {saveMsg !== null && (
        <div className="notice" data-testid="save-msg">
          <Icon name={ed.conflict ? "alert" : "info"} size="sm" />
          {saveMsg}
        </div>
      )}
      {ed.conflict && (
        <div className="notice notice--warn" data-testid="save-conflict">
          <Icon name="alert" size="sm" />
          <span>磁盘版本已变（可能已在别处修改），未保存。本地内容仍在编辑器里。</span>
          <button
            className="ui-btn"
            data-testid="reload-script"
            disabled={busy}
            onClick={() => {
              if (!reloadArm) {
                setReloadArm(true);
                return;
              }
              void load();
            }}
          >
            {reloadArm ? "确认丢弃本地改动并重新载入？" : "重新载入磁盘版本"}
          </button>
        </div>
      )}
      {stashOffer !== null && (
        <div className="notice" data-testid="stash-offer">
          <Icon name="info" size="sm" />
          检测到上次未保存的草稿（localStorage 便利层）。
          <button
            className="ui-btn"
            onClick={() => {
              setDoc(stashOffer);
              setStashOffer(null);
            }}
          >
            恢复草稿
          </button>
        </div>
      )}
      {sealMsg !== null && (
        <div className="notice" data-testid="seal-msg">
          <Icon name="info" size="sm" />
          {sealMsg}
        </div>
      )}
      {checkOut !== null && (
        <pre className="tail" data-testid="check-output" data-code={checkOut.code ?? ""}>
          {checkOut.stdoutTail}
          {checkOut.stderrTail}
        </pre>
      )}
    </div>
  );
}
