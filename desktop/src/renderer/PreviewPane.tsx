// PreviewPane（Spec 8 §2.7）：按扩展名分发五类预览；文件内容一律经 ava-media://（单一读闸）。
// 切换活跃期时由父组件以 key 卸载本组件，媒体元素随之卸载、请求中止（TP-3、TP-6）。
// Spec 14 PR2：预览区加文件头（D6：图标 + 相对路径 + 类型徽标）、空/错/载三态统一为 StateView、
// 折叠三角与音频队列项改为原生 <button>（VS-8，键盘可达）。
import MarkdownIt from "markdown-it";
import { useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode, type WheelEvent } from "react";
import { TEXT_PREVIEW_MAX_BYTES } from "../shared/constants";
import { encodeMediaUrl, type MediaRoot } from "../shared/mediaUrl";
import { formatMediaTime, naturalAudioQueue, previewKind, type PreviewKind } from "../shared/previewKind";
import { Icon, type IconName } from "./icons";
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

function Head({ rel, kind }: { rel: string; kind: PreviewKind | "dir" }) {
  return (
    <div className="preview-head" data-testid="preview-head">
      <Icon name={KIND_ICON[kind]} size="sm" />
      <span className="ui-row-title">{rel}</span>
      <Badge>{KIND_LABEL[kind]}</Badge>
    </div>
  );
}

export function PreviewPane({ target }: { target: PreviewTarget | null }) {
  if (!target) {
    return (
      <div className="preview-body">
        <StateView kind="empty" title="选择左侧产物以预览" />
      </div>
    );
  }
  if (target.kind === "dir") {
    const queue = naturalAudioQueue(target.entries);
    const body = queue.length === 0 ? <DirMeta rel={target.rel} entries={target.entries} /> : <AudioQueue key={target.rel} root={target.root} dir={target.rel} files={queue} />;
    return (
      <>
        <Head rel={target.rel || "（期目录）"} kind={queue.length === 0 ? "dir" : "audio"} />
        <div className="preview-body">{body}</div>
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
      body = <DirMeta key={url} rel={name} entries={[]} note="该类型只显示元数据" />;
  }
  return (
    <>
      <Head rel={target.rel} kind={kind} />
      <div className="preview-body">{body}</div>
    </>
  );
}

function DirMeta({ rel, entries, note }: { rel: string; entries: string[]; note?: string }) {
  return (
    <div className="meta">
      <div className="meta-title">{rel || "（期目录）"}</div>
      {note && <div className="muted">{note}</div>}
      {entries.length > 0 && <div className="muted">{entries.length} 项</div>}
    </div>
  );
}

// ---- html：沙箱 iframe，绝不加 allow-same-origin ----
function HtmlFrame({ url, root }: { url: string; root: MediaRoot }) {
  // episodes 根下的 04-review.html 不含 <script>（review.py 生成）→ sandbox=""；shots 根的 gallery 有内联脚本 → allow-scripts
  const sandbox = root === "shots" ? "allow-scripts" : "";
  return <iframe className="html-frame" title="html 预览" src={url} sandbox={sandbox} data-testid="html-frame" />;
}

// ---- 视频：原生 <video> + rVFC mediaTime 浮层 ----
type RVFCVideo = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: (now: number, meta: { mediaTime: number }) => void) => number;
  cancelVideoFrameCallback?: (h: number) => void;
};

function VideoPreview({ url }: { url: string }) {
  const ref = useRef<RVFCVideo>(null);
  const [t, setT] = useState<string>(formatMediaTime(0));
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const v = ref.current;
    if (!v || !v.requestVideoFrameCallback) return;
    let h = 0;
    const tick = (_: number, meta: { mediaTime: number }) => {
      setT(formatMediaTime(meta.mediaTime));
      h = v.requestVideoFrameCallback!(tick);
    };
    h = v.requestVideoFrameCallback(tick);
    return () => v.cancelVideoFrameCallback?.(h);
  }, [url]);
  return (
    <div className="video-wrap">
      <video ref={ref} src={url} controls preload="metadata" data-testid="video" onError={() => setErr(`媒体错误码 ${ref.current?.error?.code ?? "?"}`)} />
      <div className="timecode" data-testid="timecode">{t}</div>
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
  const ref = useRef<HTMLAudioElement>(null);
  const autoplay = useRef(false);
  useEffect(() => {
    if (autoplay.current) void ref.current?.play().catch(() => {});
  }, [i]);
  return (
    <div className="audio-wrap">
      <audio
        ref={ref}
        src={urls[i]}
        controls
        data-testid="audio"
        onEnded={() => {
          if (i + 1 < urls.length) {
            autoplay.current = true;
            setI(i + 1);
          }
        }}
      />
      {!single && (
        <ol className="queue" data-testid="audio-queue">
          {files.map((f, k) => (
            <li key={f} className={k === i ? "current" : ""}>
              <button
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

// ---- 图片：CSS transform 缩放平移 ----
function ImagePreview({ url }: { url: string }) {
  const [z, setZ] = useState(1);
  const [off, setOff] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number } | null>(null);
  const onWheel = (e: WheelEvent) => setZ((v) => Math.min(20, Math.max(0.1, v * (e.deltaY < 0 ? 1.1 : 1 / 1.1))));
  const onDown = (e: MouseEvent) => (drag.current = { x: e.clientX - off.x, y: e.clientY - off.y });
  const onMove = (e: MouseEvent) => drag.current && setOff({ x: e.clientX - drag.current.x, y: e.clientY - drag.current.y });
  return (
    <div className="image-wrap" onWheel={onWheel} onMouseDown={onDown} onMouseMove={onMove} onMouseUp={() => (drag.current = null)} onMouseLeave={() => (drag.current = null)} onDoubleClick={() => { setZ(1); setOff({ x: 0, y: 0 }); }}>
      <img src={url} alt="" draggable={false} data-testid="image" style={{ transform: `translate(${off.x}px, ${off.y}px) scale(${z})` }} />
    </div>
  );
}

// ---- markdown 渲染（html:false，链接不导航）：PreviewPane 是唯一持有 markdown-it 的文件（TG-13）。
// Spec 11 的编辑器预览也复用本组件（同一渲染参数），避免第二份 markdown 实现。
export function MarkdownBody({ text }: { text: string }) {
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
}

/** D46：对话区的助手回复（answer / wrapup）。卡片、本地说明、工具原文仍是纯文本。 */
export function ChatMarkdown({ text }: { text: string }) {
  const html = useMemo(() => renderChatMarkdown(text), [text]);
  return <div className="markdown chat-md" data-testid="chat-markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}

// ---- 文本：md 渲染（html:false）、json 折叠树、其余纯文本；超 5 MiB 截断 ----
function TextPreview({ url, name, size }: { url: string; name: string; size: number | null }) {
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
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
  }, [url, truncated]);
  if (err) return <StateView kind="error" title={`读取失败：${err}`} />;
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
  if (!parsed.ok) return <pre className="plain">{text}</pre>;
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
        <button className="j-toggle" data-testid="json-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "▾" : "▸"}
        </button>
        {label}
        {l}
        {open ? (
          <div className="j-children">
            {entries.map(([ck, cv]) => (
              <JsonNode key={ck} k={Array.isArray(v) ? null : ck} v={cv} depth={depth + 1} />
            ))}
          </div>
        ) : (
          <span className="j-collapsed"> …{entries.length} 项 </span>
        )}
        {r}
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
