// PreviewPane（Spec 8 §2.7）：按扩展名分发五类预览；文件内容一律经 ava-media://（单一读闸）。
// 切换活跃期时由父组件以 key 卸载本组件，媒体元素随之卸载、请求中止（TP-3、TP-6）。
// Spec 14 PR2：预览区加文件头（D6：图标 + 相对路径 + 类型徽标）、空/错/载三态统一为 StateView、
// 折叠三角与音频队列项改为原生 <button>（VS-8，键盘可达）。
import MarkdownIt from "markdown-it";
import { memo, useEffect, useMemo, useRef, useState, type PointerEvent, type ReactNode } from "react";
import { TEXT_PREVIEW_MAX_BYTES } from "../shared/constants";
import { encodeMediaUrl, type MediaRoot } from "../shared/mediaUrl";
import { formatMediaTime, naturalAudioQueue, previewKind, type PreviewKind } from "../shared/previewKind";
import { copyText, useCodeCopyButtons } from "./copy";
import { Icon, type IconName } from "./icons";
import { prefersReducedMotion, usePresence } from "./motion";
import { Badge, StateView } from "./ui";

export type PreviewTarget =
  | { kind: "file"; root: MediaRoot; rel: string; size: number | null }
  | { kind: "dir"; root: MediaRoot; rel: string; entries: string[] };

const md = new MarkdownIt({ html: false, linkify: false });
/** D46：助手回复用的渲染器，比 md 再收一档——链接、自动链接、引用式链接定义、图片四条规则关掉，
 *  输出里不可能出现 <a> 与 <img>（不可点、不加载外部资源），这是放开 Spec 10 H-3 的前提（RF-13）。 */
const chatMd = new MarkdownIt({ html: false, linkify: false }).disable(["link", "autolink", "reference", "image"]);

export function renderChatMarkdown(text: string): string {
  return chatMd.render(text);
}

/** 类型徽标的文案（Spec 14 §2.8 的 N2；目录另算） */
const KIND_LABEL: Record<PreviewKind | "dir", string> = {
  html: "HTML",
  video: "视频",
  audio: "音频",
  image: "图片",
  markdown: "Markdown",
  json: "JSON",
  text: "文本",
  other: "其他",
  dir: "目录",
};

const KIND_ICON: Record<PreviewKind | "dir", IconName> = {
  html: "file",
  video: "film",
  audio: "wave",
  image: "image",
  markdown: "file",
  json: "file",
  text: "file",
  other: "file",
  dir: "folder",
};

function nameOf(rel: string): string {
  return rel.slice(rel.lastIndexOf("/") + 1);
}

function formatSize(n: number | null): string | null {
  if (n === null || n < 0) return null;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function mediaErrorText(code: number | undefined): string {
  switch (code) {
    case 1:
      return "媒体读取被中止";
    case 2:
      return "读取中断，请检查数据目录连接";
    case 3:
      return "媒体解码失败，文件可能已损坏";
    case 4:
      return "不支持该媒体格式或文件不可读";
    default:
      return `媒体加载失败（错误码 ${code ?? "未知"}）`;
  }
}

function Head({ rel, kind }: { rel: string; kind: PreviewKind | "dir" }) {
  return (
    <div className="preview-head" data-testid="preview-head">
      <Icon name={KIND_ICON[kind]} size="sm" />
      <span className="ui-row-title">{rel}</span>
      <Badge>{KIND_LABEL[kind]}</Badge>
    </div>
  );
}

/** `head=false`：外面已有文件切换器当标题（D49-A S4），不再重复一行文件名 */
export function PreviewPane({
  target,
  head = true,
  hasEpisode = false,
  onPickFile,
}: {
  target: PreviewTarget | null;
  head?: boolean;
  hasEpisode?: boolean;
  onPickFile?: (dirRel: string, fileName: string) => void;
}) {
  if (!target) {
    return (
      <div className="preview-body">
        <StateView kind="empty" title={hasEpisode ? "在顶部选择本期产物文件查看" : "选择一期或文件后可在这里预览"} />
      </div>
    );
  }
  if (target.kind === "dir") {
    const queue = naturalAudioQueue(target.entries);
    const body =
      queue.length === 0 ? (
        <DirMeta rel={target.rel} entries={target.entries} onPickFile={onPickFile} />
      ) : (
        <AudioQueue key={target.rel} root={target.root} dir={target.rel} files={queue} />
      );
    return (
      <>
        {head && <Head rel={target.rel || "（期目录）"} kind={queue.length === 0 ? "dir" : "audio"} />}
        <div key={`dir:${target.root}:${target.rel}`} className="preview-body ui-fade-swap">
          {body}
        </div>
      </>
    );
  }
  const name = nameOf(target.rel);
  const kind = previewKind(name);
  const url = encodeMediaUrl(target.root, target.rel);
  const size = target.size;
  // key = url：换文件即重建组件，不让上一个文件的状态（正文、缩放、播放位置）串到下一个文件
  let body: ReactNode;
  switch (kind) {
    case "html":
      body = <HtmlFrame key={url} url={url} root={target.root} />;
      break;
    case "video":
      body = <VideoPreview key={url} url={url} />;
      break;
    case "audio":
      body = <AudioQueue key={url} root={target.root} dir="" files={[]} single={url} />;
      break;
    case "image":
      body = <ImagePreview key={url} url={url} />;
      break;
    case "markdown":
    case "json":
    case "text":
      body = <TextPreview key={url} url={url} name={name} size={size} />;
      break;
    default:
      body = <DirMeta key={url} rel={name} size={size} entries={[]} note="该类型只显示元数据" />;
  }
  return (
    <>
      {head && <Head rel={target.rel} kind={kind} />}
      <div key={url} className="preview-body ui-fade-swap">
        {body}
      </div>
    </>
  );
}

function DirMeta({
  rel,
  size = null,
  entries,
  note,
  onPickFile,
}: {
  rel: string;
  size?: number | null;
  entries: string[];
  note?: string;
  onPickFile?: (dirRel: string, fileName: string) => void;
}) {
  const sizeStr = formatSize(size);
  return (
    <div className="meta">
      <div className="meta-title">{rel || "（期目录）"}</div>
      {sizeStr && <div className="muted">大小：{sizeStr}</div>}
      {note && <div className="muted">{note}</div>}
      {!note && entries.length === 0 && <div className="muted">（空目录）</div>}
      {entries.length > 0 && <div className="muted">{entries.length} 项</div>}
      {onPickFile && entries.length > 0 && (
        <div className="dir-file-list">
          {entries.map((name) => (
            <button
              type="button"
              key={name}
              className="ui-row dir-file-btn"
              data-testid="dir-file-row"
              data-name={name}
              onClick={() => onPickFile(rel, name)}
            >
              <Icon name={KIND_ICON[previewKind(name)] ?? "file"} size="sm" />
              <span className="ui-row-title">{name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- html：沙箱 iframe，绝不加 allow-same-origin ----
const HTML_MISSING_RECHECK_MS = 2000;

function HtmlFrame({ url, root }: { url: string; root: MediaRoot }) {
  const [state, setState] = useState<"probing" | "ok" | "missing">("probing");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    setLoaded(false);
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const probe = () => {
      fetch(url, { method: "HEAD" }).then(
        (r) => {
          if (!alive) return;
          if (r.status !== 404) setState("ok");
          else {
            setState("missing");
            timer = setTimeout(probe, HTML_MISSING_RECHECK_MS);
          }
        },
        () => {
          if (alive) setState("ok");
        },
      );
    };
    probe();
    return () => {
      alive = false;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [url]);
  if (state === "probing") return <StateView kind="loading" title="打开页面…" />;
  if (state === "missing") {
    const isReview = url.endsWith("/04-review.html");
    return (
      <div data-testid="html-missing">
        <StateView
          kind="empty"
          title={isReview ? "审看页 04-review.html 还没生成" : "这个文件还不存在"}
          detail={isReview ? "让 ava 跑 review（只抽帧出页，不会批准）；生成后这里会自动显示。" : "生成后这里会自动显示。"}
        />
      </div>
    );
  }
  // episodes 根下的 04-review.html 不含 <script>（review.py 生成）→ sandbox=""；shots 根的 gallery 有内联脚本 → allow-scripts
  const sandbox = root === "shots" ? "allow-scripts" : "";
  return (
    <iframe
      className="html-frame"
      title="html 预览"
      src={url}
      sandbox={sandbox}
      data-testid="html-frame"
      data-loaded={loaded ? "1" : "0"}
      onLoad={() => setLoaded(true)}
    />
  );
}

// ---- 视频：原生 <video> + rVFC mediaTime 浮层（点击可复制时间码） ----
type RVFCVideo = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: (now: number, meta: { mediaTime: number }) => void) => number;
  cancelVideoFrameCallback?: (h: number) => void;
};

function VideoPreview({ url }: { url: string }) {
  const ref = useRef<RVFCVideo>(null);
  const [t, setT] = useState<string>(formatMediaTime(0));
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    let h = 0;
    const onFallback = () => {
      if (Number.isFinite(v.currentTime)) setT(formatMediaTime(v.currentTime));
    };
    if (v.requestVideoFrameCallback) {
      const tick = (_: number, meta: { mediaTime: number }) => {
        setT(formatMediaTime(meta.mediaTime));
        h = v.requestVideoFrameCallback!(tick);
      };
      h = v.requestVideoFrameCallback(tick);
    }
    v.addEventListener("seeked", onFallback);
    v.addEventListener("loadedmetadata", onFallback);
    return () => {
      if (h) v.cancelVideoFrameCallback?.(h);
      v.removeEventListener("seeked", onFallback);
      v.removeEventListener("loadedmetadata", onFallback);
    };
  }, [url]);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1200);
    return () => clearTimeout(timer);
  }, [copied]);
  return (
    <div className="video-wrap">
      <video
        ref={ref}
        src={url}
        controls
        preload="metadata"
        data-testid="video"
        onError={() => setErr(mediaErrorText(ref.current?.error?.code))}
      />
      <button
        type="button"
        className="timecode"
        data-testid="timecode"
        title="点击复制当前时间码"
        onClick={() => {
          void copyText(t).then((ok) => {
            if (ok) setCopied(true);
          });
        }}
      >
        {copied ? "已复制" : t}
      </button>
      {err && <StateView kind="error" title={err} />}
    </div>
  );
}

// ---- 音频：原生 <audio> + 连续播放队列 ----
function AudioQueue({ root, dir, files, single }: { root: MediaRoot; dir: string; files: string[]; single?: string }) {
  const urls = useMemo(
    () => (single ? [single] : files.map((f) => encodeMediaUrl(root, dir ? `${dir}/${f}` : f))),
    [root, dir, files, single],
  );
  const [i, setI] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const ref = useRef<HTMLAudioElement>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const autoplay = useRef(false);
  useEffect(() => {
    setErr(null);
    if (autoplay.current) void ref.current?.play().catch(() => {});
  }, [i]);
  useEffect(() => {
    const active = listRef.current?.querySelector("li.current");
    if (active instanceof HTMLElement) {
      active.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
    }
  }, [i]);
  return (
    <div className="audio-wrap">
      <audio
        ref={ref}
        src={urls[i]}
        controls
        data-testid="audio"
        onError={() => setErr(mediaErrorText(ref.current?.error?.code))}
        onEnded={() => {
          if (i + 1 < urls.length) {
            autoplay.current = true;
            setI(i + 1);
          }
        }}
      />
      {err && (
        <div className="notice notice--err ui-error-shake" role="alert">
          <Icon name="alert" size="sm" />
          <span>{err}</span>
        </div>
      )}
      {!single && (
        <ol className="queue" ref={listRef} data-testid="audio-queue">
          {files.map((f, k) => (
            <li key={f} className={k === i ? "current" : ""}>
              <button
                type="button"
                className="ui-row"
                aria-current={k === i ? "true" : undefined}
                onClick={() => {
                  autoplay.current = true;
                  setI(k);
                }}
              >
                {f}
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

// ---- 图片：光标锚定比例缩放 + 指针捕获直接跟手平移 + 仅复位时弹性过渡 ----
function ImagePreview({ url }: { url: string }) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [view, setView] = useState({ z: 1, x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [imgErr, setImgErr] = useState(false);
  const drag = useRef<{ id: number; sx: number; sy: number; ox: number; oy: number } | null>(null);
  const resetTimer = useRef<number | null>(null);

  const reset = () => {
    setResetting(true);
    setView({ z: 1, x: 0, y: 0 });
    if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    resetTimer.current = window.setTimeout(() => {
      resetTimer.current = null;
      setResetting(false);
    }, 240);
  };

  useEffect(() => {
    return () => {
      if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    };
  }, []);

  // 非 passive 滚轮监听：按 deltaY 比例缩放并以鼠标光标位置为缩放锚点
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const onWheel = (e: globalThis.WheelEvent) => {
      e.preventDefault();
      setResetting(false);
      const rect = el.getBoundingClientRect();
      const cx = e.clientX - (rect.left + rect.width / 2);
      const cy = e.clientY - (rect.top + rect.height / 2);
      const sensitivity = e.ctrlKey ? 0.01 : 0.0025;
      const factor = Math.exp(-e.deltaY * sensitivity);
      setView((v) => {
        const nz = Math.min(20, Math.max(0.1, v.z * factor));
        const ratio = nz / v.z;
        return {
          z: nz,
          x: cx - (cx - v.x) * ratio,
          y: cy - (cy - v.y) * ratio,
        };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
    setResetting(false);
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { id: e.pointerId, sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y };
    setDragging(true);
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    setView((v) => ({ ...v, x: d.ox + (e.clientX - d.sx), y: d.oy + (e.clientY - d.sy) }));
  };

  const stopDrag = (e: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.id !== e.pointerId) return;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    drag.current = null;
    setDragging(false);
  };

  const moved = Math.abs(view.z - 1) > 0.005 || Math.abs(view.x) > 0.5 || Math.abs(view.y) > 0.5;
  const pill = usePresence(moved, 140);

  if (imgErr) {
    return <StateView kind="error" title="图片无法加载" detail="请确认文件存在且格式受支持（PNG / JPEG / WebP）。" />;
  }

  return (
    <div
      ref={wrapRef}
      className="image-wrap"
      data-dragging={dragging ? "1" : "0"}
      data-resetting={resetting ? "1" : "0"}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={stopDrag}
      onPointerCancel={stopDrag}
      onLostPointerCapture={stopDrag}
      onDoubleClick={reset}
    >
      <img
        src={url}
        alt=""
        draggable={false}
        data-testid="image"
        onError={() => setImgErr(true)}
        style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.z})` }}
      />
      {pill.mounted && (
        <button
          type="button"
          className="image-zoom-pill"
          data-status={pill.status}
          data-testid="image-zoom-reset"
          title="点击复位缩放与位置（也可双击画面复位）"
          onClick={reset}
        >
          {Math.round(view.z * 100)}% · 复位
        </button>
      )}
    </div>
  );
}

// ---- markdown 渲染（html:false，链接不导航）：PreviewPane 是唯一持有 markdown-it 的文件（TG-13）。
// Spec 11 的编辑器预览也复用本组件（同一渲染参数），避免第二份 markdown 实现。
export const MarkdownBody = memo(function MarkdownBody({ text }: { text: string }) {
  const html = useMemo(() => md.render(text), [text]);
  return (
    <div
      className="markdown"
      data-testid="markdown"
      onClick={(e) => {
        if ((e.target as HTMLElement).closest("a")) e.preventDefault(); // 链接点击不导航
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
});

/** D46：对话区的助手回复（answer / wrapup）。卡片、本地说明、工具原文仍是纯文本。
 *  D76：代码块的「复制」按钮由宿主在渲染后挂上（useCodeCopyButtons），不进 renderChatMarkdown 的输出。 */
export const ChatMarkdown = memo(function ChatMarkdown({ text }: { text: string }) {
  const html = useMemo(() => renderChatMarkdown(text), [text]);
  const ref = useCodeCopyButtons(html);
  return <div ref={ref} className="markdown chat-md" data-testid="chat-markdown" dangerouslySetInnerHTML={{ __html: html }} />;
});

// ---- 文本：md 渲染（html:false）、json 折叠树、其余纯文本；超 5 MiB 截断 ----
function TextPreview({ url, name, size }: { url: string; name: string; size: number | null }) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const truncated = size !== null && size > TEXT_PREVIEW_MAX_BYTES;
  useEffect(() => {
    const ac = new AbortController();
    setText(null);
    setErr(null);
    const headers: Record<string, string> = truncated ? { Range: `bytes=0-${TEXT_PREVIEW_MAX_BYTES - 1}` } : {};
    fetch(url, { signal: ac.signal, headers })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        setText(await r.text());
      })
      .catch((e: unknown) => {
        if (!ac.signal.aborted) setErr(e instanceof Error ? e.message : String(e));
      });
    return () => ac.abort();
  }, [url, truncated, retryCount]);
  if (err) {
    return (
      <div className="pane-crash">
        <StateView kind="error" title={`读取失败：${err}`} />
        <button type="button" className="ui-btn ui-btn--sm" onClick={() => setRetryCount((n) => n + 1)}>
          重试
        </button>
      </div>
    );
  }
  if (text === null) return <StateView kind="loading" title="读取中…" />;
  const kind = previewKind(name);
  return (
    <div className="text-wrap">
      {truncated && (
        <div className="notice" data-testid="truncated">
          <Icon name="info" size="sm" />
          文件超过 5 MiB，只显示前 5 MiB 原文
        </div>
      )}
      {kind === "markdown" && !truncated ? (
        <MarkdownBody text={text} />
      ) : kind === "json" && !truncated ? (
        <JsonView text={text} />
      ) : (
        <pre className="plain" data-testid="plain">{text}</pre>
      )}
    </div>
  );
}

/** 数字按源码原文显示：纳秒 mtime 超过 2^53，经 double 显示就是错的（§3.1 规则 8 同一教训） */
class RawNumber {
  constructor(readonly text: string) {}
}
type SourceReviver = (key: string, value: unknown, ctx?: { source?: string }) => unknown;
const parseKeepingNumbers = (text: string): unknown =>
  (JSON.parse as unknown as (t: string, r: SourceReviver) => unknown)(text, (_k, v, ctx) =>
    typeof v === "number" ? new RawNumber(ctx?.source ?? String(v)) : v,
  );

function JsonView({ text }: { text: string }) {
  const parsed = useMemo(() => {
    try {
      return { ok: true as const, v: parseKeepingNumbers(text) };
    } catch (e) {
      return { ok: false as const, e: e instanceof Error ? e.message : String(e) };
    }
  }, [text]);
  if (!parsed.ok) {
    return (
      <>
        <div className="notice notice--warn">
          <Icon name="alert" size="sm" />
          JSON 解析失败（已回退为原文显示）：{parsed.e}
        </div>
        <pre className="plain">{text}</pre>
      </>
    );
  }
  return (
    <div className="json" data-testid="json">
      <JsonNode k={null} v={parsed.v} depth={0} />
    </div>
  );
}

function JsonNode({ k, v, depth }: { k: string | null; v: unknown; depth: number }): ReactNode {
  const [open, setOpen] = useState(depth < 2);
  const label = k === null ? null : <span className="j-key">{JSON.stringify(k)}: </span>;
  if (v instanceof RawNumber) {
    return (
      <div className="j-leaf">
        {label}
        <span className="j-num">{v.text}</span>
      </div>
    );
  }
  if (v !== null && typeof v === "object") {
    const entries = Array.isArray(v) ? v.map((x, i) => [String(i), x] as const) : Object.entries(v as Record<string, unknown>);
    const [l, r] = Array.isArray(v) ? ["[", "]"] : ["{", "}"];
    return (
      <div className="j-node">
        <button type="button" className="j-toggle" data-testid="json-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          <span className="ui-chev" data-open={open ? "1" : "0"}><Icon name="chevron-right" size="sm" /></span>
          {label}
          <span>{l}</span>
          {!open && <span className="j-collapsed"> …{entries.length} 项 {r}</span>}
        </button>
        {open && (
          <>
            <div className="j-children">
              {entries.map(([ck, cv]) => (
                <JsonNode key={ck} k={Array.isArray(v) ? null : ck} v={cv} depth={depth + 1} />
              ))}
            </div>
            <div>{r}</div>
          </>
        )}
      </div>
    );
  }
  const cls = typeof v === "string" ? "j-str" : "j-lit";
  return (
    <div className="j-leaf">
      {label}
      <span className={cls}>{JSON.stringify(v)}</span>
    </div>
  );
}
