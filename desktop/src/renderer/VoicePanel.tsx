// 03.5 顺听面板（Spec 11 §2.3/§4.4）：段落列表 + 播放走 renderer 原生 <audio>（ava-media://）+ 六指令按钮化。
//
// 映射表（冻结）：听 N → 行内 ▶；听 → 顺序播放；停 → 停；回滚 N → 行内 回滚（仅当 attic 有快照）；
// 撤回 N → 待应用条目行内 撤回；done → 完成并应用补丁。
// 自由文法纠错的解析与落盘都在 core（`/voice-parse` 纯算、`/voice-add` 重新解析原文），UI 只渲染结构化字段。
// 播放器从 afplay/QuickTime 改为原位 <audio>（direction §0.2 第 3 条：全要素应用内原位审计）。
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { encodeMediaUrl } from "../shared/mediaUrl";
import type { VoiceCheckJson, VoiceInfoJson, VoicePatchJson, VoiceSegmentJson } from "../shared/protocol";
import { lockState } from "../shared/voiceInfo";
import { Icon } from "./icons";
import { errText, type RpcClient } from "./rpc";
import { Badge, StateView } from "./ui";

const PATCH_FIELDS: readonly [keyof VoicePatchJson, string][] = [
  ["segment", "段落"],
  ["kind", "补丁类型"],
  ["word", "目标词"],
  ["heard", "听成"],
  ["target_tone3", "目标读音"],
  ["issue", "听感现象"],
  ["action", "调整动作"],
  ["scope", "范围"],
];

/**
 * `onReady(ok)`：面板装载结果（D49-A / D50-A S1）。App 据此决定要不要再叠一份纯播放队列——
 * 面板正常时只留面板一套播放器；面板取不到数据（core 出错）时退回原来的队列，人不至于什么都听不了。
 */
const LIVE_POLL_MS = 5000;

/** D73：每期最近一次读到的配音信息。面板切走即卸载、切回重新挂载——没有它，切回那一刻只有空白等待，
 *  重配进行中一次读取失败还会把整块换成错误态。只作首屏占位，挂载后照样立刻重读。 */
const lastInfo = new Map<string, VoiceInfoJson>();

export function VoicePanel({ epKey, rpc, onReady, live = false }: { epKey: string; rpc: RpcClient; onReady?: (ok: boolean) => void; live?: boolean }) {
  const [info, setInfo] = useState<VoiceInfoJson | null>(() => lastInfo.get(epKey) ?? null);
  const [err, setErr] = useState<string | null>(null);
  /** 本次挂载至少成功读到一次（缓存占位不算）：人时只在读到真数据后计（红队 ④ 的同一纪律） */
  const [fresh, setFresh] = useState(false);
  const [raw, setRaw] = useState("");
  const [card, setCard] = useState<VoicePatchJson | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [queue, setQueue] = useState<{ wav: string; label: string }[]>([]);
  // D50-A S6：在段落文字上选中的词 → 就地纠错小卡。
  // 「全局表改过、待重配的段」不在这里记：D72 起由 core 的 `rerun_segments` 从盘上现算（切走、刷新都不丢）
  const [fix, setFix] = useState<{ label: string; word: string } | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const v = await rpc.call<VoiceInfoJson>("voice.info", { epKey });
      lastInfo.set(epKey, v);
      setInfo(v);
      setFresh(true);
    } catch (e) {
      setErr(errText(e));
    }
  }, [epKey, rpc]);

  useEffect(() => {
    void load();
  }, [load]);

  // D49-A S5：配音作业在跑时每 5 s 重读一次——新出的 wav 立刻可播，出一段听一段。
  // 5 s：voice.info 每次起一个 core 进程（约 0.3 s），一段配音通常远长于 5 s，再密没有意义
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => void load(), LIVE_POLL_MS);
    return () => clearInterval(t);
  }, [live, load]);

  // 手上有数据（哪怕是上次的）就算面板可用：一次读取失败不该把面板换成纯播放队列（D73）
  useEffect(() => {
    if (info !== null) onReady?.(true);
    else if (err !== null) onReady?.(false);
  }, [err, info, onReady]);

  // 人时：顺听面板**装载成功**才计时（Spec 11 §2.4）；错误态不计（红队 ④）
  const surfaceUp = err === null && info !== null && fresh;
  useEffect(() => {
    if (!surfaceUp) return;
    void rpc.call("time.surface", { epKey, stop: "03.5", visible: "true" }).catch(() => undefined);
    return () => {
      void rpc.call("time.surface", { epKey, stop: "03.5", visible: "false" }).catch(() => undefined);
    };
  }, [epKey, rpc, surfaceUp]);

  const urlOf = (wav: string) => encodeMediaUrl("episodes", `${epKey}/03-audio/${wav}`);
  const current = queue[0] ?? null;

  // 队列推进：src 变化后自动开播；`ended` 由元素事件接下一条（Spec 8 §2.7 音频队列同款）
  useEffect(() => {
    const a = audioRef.current;
    if (!a || !current) return;
    void a.play().catch(() => undefined);
  }, [current]);

  const playOne = (seg: VoiceSegmentJson) => setQueue([{ wav: seg.wav, label: seg.label }]);
  const playAll = () => setQueue((info?.segments ?? []).filter((s) => s.wav_exists).map((s) => ({ wav: s.wav, label: s.label })));
  const stop = () => {
    setQueue([]);
    audioRef.current?.pause();
  };

  const doParse = useCallback(
    async (call: Promise<unknown>) => {
      setBusy(true);
      setMsg(null);
      setCard(null);
      try {
        setCard((await call) as VoicePatchJson);
      } catch (e) {
        setMsg(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const doAdd = useCallback(
    async (call: Promise<unknown>) => {
      setBusy(true);
      setMsg(null);
      try {
        const entry = (await call) as Record<string, unknown>;
        setMsg(`已落盘 #${String(entry.id)} 段${String(entry.segment)}：回滚按段号，撤回按条目 id`);
        setCard(null);
        setRaw("");
        await load();
      } catch (e) {
        setMsg(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const doAction = useCallback(
    async (call: Promise<unknown>, okText: string) => {
      setBusy(true);
      setMsg(null);
      try {
        await call;
        setMsg(okText);
        await load();
      } catch (e) {
        setMsg(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const doDone = useCallback(
    async (call: Promise<unknown>) => {
      setBusy(true);
      setMsg(null);
      try {
        const r = (await call) as { started: boolean };
        setMsg(r.started ? "已执行增量重配（进度见时间线）" : "已取消，未执行增量重配");
        await load();
      } catch (e) {
        setMsg(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  if (info === null) return err !== null ? <StateView kind="error" title={err} /> : <StateView kind="loading" title="读取配音信息…" />;
  const pending = info.pending_corrections;
  const lock = info.apply_patch_lock;
  const lockKind = lockState(lock);
  const lockStale = lockKind === "stale";
  const lockBusy = lockKind === "busy";
  const pendingSegs = [...new Set(pending.map((c) => String(c.segment)))];
  // D72：一个按钮管两层。有期级待应用条目 → --apply-patch（它的复用判据同样会重配全局表改过的段）；
  // 只有全局表 / 文本 / 钉种子变了 → 普通重跑 tts。人不必知道读音存在哪张表
  const rerunSegs = info.rerun_segments ?? [];
  const redoSegs = info.segments.map((s) => s.label).filter((l) => pendingSegs.includes(l) || rerunSegs.includes(l));
  // 重配正在跑（锁在、pid 活着）时不许再起一次：第二个进程只会撞锁失败（D71 截图时发现）
  const doneDisabled = busy || lockBusy || redoSegs.length === 0 || info.engine_cloud;
  const marksOf = (label: string): string[] =>
    pending
      .filter((c) => typeof c.word === "string" && c.word !== "" && (String(c.segment) === label || c.scope === "global"))
      .map((c) => String(c.word));

  // 选中即弹卡（打开小卡不是写操作，不要求可信事件；写在小卡的「记下」上才要）
  const onSelectText = (label: string) => {
    const word = (window.getSelection()?.toString() ?? "").trim();
    if (word === "" || word.length > 12) return;
    setFix({ label, word });
  };

  return (
    <div className="seg-panel voice-panel" data-testid="voice-panel">
      <div className="toolbar" data-testid="voice-toolbar">
        <button className="ui-btn" data-testid="voice-play-all" disabled={busy} onClick={playAll}>
          顺序播放
        </button>
        <button className="ui-btn" data-testid="voice-stop" disabled={busy || current === null} onClick={stop}>
          停
        </button>
        <button className="ui-btn" data-testid="voice-refresh" disabled={busy} onClick={() => void load()}>
          刷新
        </button>
        <span className="muted voice-engine" data-testid="voice-engine">
          {info.engine === "" ? "引擎未记录" : `${info.engine}${info.engine_cloud ? " · 云端" : " · 本地"}`}
        </span>
      </div>
      <div className="muted voice-hint">选中段落里读错的字，就地记下；攒够了在底部只重配这些段。</div>
      {err !== null && (
        <div className="notice notice--warn" data-testid="voice-stale">
          <Icon name="alert" size="sm" />
          刷新失败：{err}（下面是上次读到的内容；点「刷新」重试）
        </div>
      )}

      {info.engine_cloud && (
        <div className="notice" data-testid="voice-cloud">
          <Icon name="info" size="sm" />
          本期配音引擎为云端引擎（{info.engine}），重配请去云端执行 apply-patch。
        </div>
      )}
      {lockBusy && (
        <div className="notice" data-testid="voice-lock-busy">
          <Icon name="info" size="sm" />
          增量重配进行中（pid {String(lock.pid)}）。
        </div>
      )}
      {lockStale && (
        <div className="notice notice--warn" data-testid="voice-lock-stale">
          <Icon name="alert" size="sm" />
          <span>
            锁残留（上次进程被杀、断电，或锁文件写到一半；{lock.pid === null ? "pid 未记录" : `pid ${lock.pid} 已不存在`}）。可在终端执行
            <code>{` rm data/episodes/${epKey}/03-audio/.apply_patch.lock `}</code>
            清除后重试；app 不提供「清锁」按钮（那是绕过单写者纪律的新写通道）。
          </span>
        </div>
      )}

      <div className="voice-list" data-testid="voice-segments">
        {info.segments.map((s) => (
          <div className="voice-row" key={s.label} data-testid="seg-row" data-label={s.label} aria-current={current?.label === s.label ? "true" : undefined}>
            <button className="ui-btn ui-btn--sm ui-btn--icon" data-testid="seg-play" disabled={!s.wav_exists} onClick={() => playOne(s)} aria-label={`播放段 ${s.label}`}>
              ▶
            </button>
            <div className="voice-row-main">
              <div className="voice-row-head">
                <span>段 {s.label}</span>
                {current?.label === s.label && <Badge tone="accent">播放中</Badge>}
                {redoSegs.includes(s.label) && <Badge tone="warn">待重配</Badge>}
                {!s.wav_exists && <span className="muted">（音频缺席）</span>}
                <span className="seg-actions">
                  <button
                    className="ui-btn ui-btn--ghost ui-btn--sm"
                    data-testid="seg-reseed"
                    disabled={busy}
                    title="听感不对（发飘、断层、吞字…）：换一个种子重配这一段"
                    onClick={(e) => {
                      if (!e.nativeEvent.isTrusted) return;
                      void doAdd(rpc.call("voice.add", { epKey, text: `段${s.label} 换种子` }));
                    }}
                  >
                    换种子
                  </button>
                  {s.has_attic && (
                    <button
                      className="ui-btn ui-btn--ghost ui-btn--sm"
                      data-testid="seg-revert"
                      disabled={busy}
                      onClick={(e) => {
                        if (!e.nativeEvent.isTrusted) return;
                        void doAction(rpc.call("voice.revert", { epKey, label: s.label }), `已回滚段 ${s.label}`);
                      }}
                    >
                      回滚
                    </button>
                  )}
                </span>
              </div>
              <div className="voice-row-text" data-testid="seg-text" onMouseUp={() => onSelectText(s.label)}>
                <Marked text={s.text} marks={marksOf(s.label)} />
              </div>
              {fix !== null && fix.label === s.label && (
                <FixCard
                  key={`${fix.label}:${fix.word}`}
                  epKey={epKey}
                  rpc={rpc}
                  label={fix.label}
                  word={fix.word}
                  busy={busy}
                  onCancel={() => setFix(null)}
                  onAdd={(call) => {
                    setFix(null);
                    void doAdd(call);
                  }}
                  onGlobalDone={(line) => {
                    setFix(null);
                    setMsg(line);
                    void load(); // 待重配段由 core 从盘上重算（D72）
                  }}
                />
              )}
            </div>
          </div>
        ))}
      </div>

      {pending.length > 0 && (
        <details className="pending" data-testid="voice-pending" open>
          <summary className="ui-section">待应用纠错条目（{pending.length}）</summary>
          {pending.map((c) => (
            <div className="seg-row" key={String(c.id)} data-testid="pending-row" data-id={String(c.id)}>
              <span className="ui-row-meta">#{String(c.id)}</span>
              <span className="ui-row-title">段{String(c.segment)}</span>
              <span className="ui-row-meta">
                {c.word ? `「${String(c.word)}」→ ${String(c.target_tone3)}` : String(c.issue ?? c.kind)} · {c.scope === "global" ? "本期所有段" : "本段"}
              </span>
              <span className="seg-actions">
                <button
                  className="ui-btn ui-btn--sm"
                  data-testid="pending-retract"
                  disabled={busy}
                  onClick={(e) => {
                    if (!e.nativeEvent.isTrusted) return;
                    void doAction(rpc.call("voice.retract", { epKey, id: String(c.id) }), `已撤回 #${String(c.id)}`);
                  }}
                >
                  撤回
                </button>
              </span>
            </div>
          ))}
        </details>
      )}

      <details className="voice-grammar">
        <summary className="ui-section" data-testid="voice-grammar">按文法录入（终端 /voice 同一文法）</summary>
        <div className="voice-input" data-testid="voice-input">
          <label className="ui-field">
            如「1段 雪乃 改成 xuě nǎi」「2段 语气发飘 换种子」
            <input className="ui-input" data-testid="voice-raw" value={raw} onChange={(e) => setRaw(e.target.value)} />
          </label>
          <button
            className="ui-btn"
            data-testid="voice-parse"
            disabled={busy || raw.trim() === ""}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void doParse(rpc.call("voice.parse", { epKey, text: raw }));
            }}
          >
            解析
          </button>
        </div>
        {card !== null && (
          <div className="voice-card" data-testid="voice-card">
            <div className="ui-section">确认卡（与终端同语义）</div>
            <dl className="ui-kv">
              {PATCH_FIELDS.map(([k, label]) => (
                <Fragment key={k}>
                  <dt>{label}</dt>
                  <dd>{card[k] === null || card[k] === undefined ? "（未指定）" : String(card[k])}</dd>
                </Fragment>
              ))}
            </dl>
            {card.scope === "global" && (
              <div className="notice notice--warn" data-testid="voice-global">
                <Icon name="alert" size="sm" />
                该拼音注入影响本期所有包含该词的段落。
              </div>
            )}
            <button
              className="ui-btn"
              data-testid="voice-add"
              disabled={busy}
              onClick={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                // 传原文而非解析结果：core 重新解析并落盘（不信任跨进程往返的解析结果）
                void doAdd(rpc.call("voice.add", { epKey, text: raw }));
              }}
            >
              确认落盘
            </button>
          </div>
        )}
      </details>

      {info.heteronyms.length > 0 && (
        <details className="hetero" data-testid="voice-heteronyms">
          <summary className="ui-section">多音字预检（{info.heteronyms.length} 个字，只读，仅供关注）</summary>
          <div className="voice-chips">
            {info.heteronyms.map((h) => (
              <span key={h.char} className="ui-badge">
                {h.char} {h.readings.join("/")}
              </span>
            ))}
          </div>
        </details>
      )}

      {msg !== null && (
        <div className="notice" data-testid="voice-msg">
          <Icon name="info" size="sm" />
          {msg}
        </div>
      )}

      <div className="voice-foot" data-testid="voice-foot">
        {redoSegs.length === 0 ? (
          <span className="muted" data-testid="voice-no-pending">
            当前没有待重配的段
          </span>
        ) : (
          <span data-testid="voice-redo-segs">
            <Badge tone="warn">待重配 {redoSegs.length} 段</Badge> 段 {redoSegs.join("、")}
          </span>
        )}
        <span className="voice-foot-sp" />
        {/* 同一个位置、同一个按钮名，只按有无期级条目换写路径（每个处理器只许一处闭集调用，TG-4′） */}
        {pending.length > 0 ? (
          <button
            className="ui-btn ui-btn--primary"
            data-testid="voice-done"
            data-mode="apply-patch"
            disabled={doneDisabled}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void doDone(rpc.call("voice.applyPatch", { epKey }));
            }}
          >
            只重配这 {redoSegs.length} 段
          </button>
        ) : (
          <button
            className="ui-btn ui-btn--primary"
            data-testid="voice-done"
            data-mode="retts"
            disabled={doneDisabled}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void doDone(rpc.call("voice.retts", { epKey }));
            }}
          >
            只重配这 {redoSegs.length} 段
          </button>
        )}
      </div>
      {current !== null && <audio ref={audioRef} src={urlOf(current.wav)} data-testid="voice-audio" onEnded={() => setQueue((q) => q.slice(1))} />}
    </div>
  );
}

/** 段落文字里标出已录、待重配的词 */
function Marked({ text, marks }: { text: string; marks: string[] }) {
  if (marks.length === 0) return <>{text}</>;
  const re = new RegExp(`(${marks.map((m) => m.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "g");
  return (
    <>
      {text.split(re).map((part, i) =>
        marks.includes(part) ? (
          <mark key={i} className="voice-mark">
            {part}
          </mark>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}

type Method = "pinyin" | "homophone";
type Scope = "seg" | "ep" | "global";

/**
 * D50-A S6：「这里读错了」小卡。读音先经 core 的 `corrections check` 核对（只读），通过了才能记下。
 * 本段 / 本期 = 期级拼音直注（`voice.add` 的文法串）；全局 = `voice.global`（写 config/voice.json，宿主再弹一次确认框）。
 * 同音字只能写全局表（2026-10-08 人裁决：期级不加同音字）。
 */
function FixCard({
  epKey,
  rpc,
  label,
  word,
  busy,
  onCancel,
  onAdd,
  onGlobalDone,
}: {
  epKey: string;
  rpc: RpcClient;
  label: string;
  word: string;
  busy: boolean;
  onCancel: () => void;
  /** 期级写入的 Promise 交给面板（与文法录入同一个 doAdd：落盘提示、刷新） */
  onAdd: (call: Promise<unknown>) => void;
  onGlobalDone: (line: string) => void;
}) {
  const [pinyin, setPinyin] = useState("");
  const [method, setMethod] = useState<Method>("pinyin");
  const [homophone, setHomophone] = useState("");
  const [scope, setScope] = useState<Scope>("seg");
  const [check, setCheck] = useState<VoiceCheckJson | null>(null);
  const [writing, setWriting] = useState(false);
  const [result, setResult] = useState<VoiceCheckJson | null>(null);
  const effScope: Scope = method === "homophone" ? "global" : scope;
  const reading = method === "pinyin" ? { word, pinyin } : { word, homophone, expect: pinyin };
  const ready = pinyin.trim() !== "" && (method === "pinyin" || homophone.trim() !== "");

  // 读音核对：停手 400 ms 后跑一次（只读，core 打印的原文行照传）
  useEffect(() => {
    setCheck(null);
    if (!ready) return;
    const t = setTimeout(() => {
      void rpc.call<VoiceCheckJson>("voice.check", { epKey, ...reading }).then(setCheck, (e) => setCheck({ ok: false, lines: [errText(e)] }));
    }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [epKey, rpc, word, pinyin, homophone, method, ready]);

  const finishGlobal = async (call: Promise<VoiceCheckJson>) => {
    setWriting(true);
    try {
      const r = await call;
      setResult(r);
      if (r.ok) onGlobalDone(r.lines.find((l) => l.startsWith("[OK]")) ?? "已写入全局读音表");
    } catch (e) {
      setResult({ ok: false, lines: [errText(e)] });
    } finally {
      setWriting(false);
    }
  };
  const needSupersede = result !== null && !result.ok && result.lines.some((l) => l.includes("--supersede"));

  return (
    <div className="ui-popover voice-fix" data-testid="voice-fix">
      <span>「{word}」应读</span>
      <span>
        <input className="ui-input voice-fix-input" data-testid="fix-pinyin" placeholder="如 jie3 di4" value={pinyin} onChange={(e) => setPinyin(e.target.value)} />
      </span>
      <span>改法</span>
      <span className="ui-seg">
        <button aria-pressed={method === "pinyin"} data-testid="fix-method-pinyin" onClick={() => setMethod("pinyin")}>
          拼音直注
        </button>
        <button aria-pressed={method === "homophone"} data-testid="fix-method-homophone" onClick={() => setMethod("homophone")}>
          同音字
        </button>
      </span>
      {method === "homophone" && (
        <>
          <span>同音字</span>
          <span>
            <input className="ui-input voice-fix-input" data-testid="fix-homophone" placeholder="读音唯一的字" value={homophone} onChange={(e) => setHomophone(e.target.value)} />
          </span>
        </>
      )}
      <span>范围</span>
      <span className="ui-seg">
        <button aria-pressed={effScope === "seg"} disabled={method === "homophone"} data-testid="fix-scope-seg" onClick={() => setScope("seg")}>
          本段 {label}
        </button>
        <button aria-pressed={effScope === "ep"} disabled={method === "homophone"} data-testid="fix-scope-ep" onClick={() => setScope("ep")}>
          本期
        </button>
        <button aria-pressed={effScope === "global"} data-testid="fix-scope-global" onClick={() => setScope("global")}>
          全局
        </button>
      </span>
      {method === "homophone" && (
        <>
          <span />
          <span className="muted">同音字只写全局表（所有番、所有期）</span>
        </>
      )}
      <span>核对</span>
      <span className={check === null ? "muted" : check.ok ? "voice-ok" : "voice-bad"} data-testid="fix-check">
        {!ready ? "填好读音后自动核对" : check === null ? "核对中…" : check.lines.join("；")}
      </span>
      {result !== null && !result.ok && (
        <>
          <span />
          <span className="voice-bad" data-testid="fix-result">
            {result.lines.join("；")}
          </span>
        </>
      )}
      <span />
      <span className="voice-fix-actions">
        {needSupersede ? (
          <button
            className="ui-btn ui-btn--sm"
            data-testid="fix-supersede"
            disabled={busy || writing}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void finishGlobal(rpc.call<VoiceCheckJson>("voice.global", { epKey, ...reading, supersede: "true" }));
            }}
          >
            替换另一张表的同名条目并写入
          </button>
        ) : effScope === "global" ? (
          <button
            className="ui-btn ui-btn--sm ui-btn--primary"
            data-testid="fix-save"
            disabled={busy || writing || check === null || !check.ok}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void finishGlobal(rpc.call<VoiceCheckJson>("voice.global", { epKey, ...reading, supersede: "false" }));
            }}
          >
            记下（写全局表）
          </button>
        ) : (
          <button
            className="ui-btn ui-btn--sm ui-btn--primary"
            data-testid="fix-save"
            disabled={busy || writing || check === null || !check.ok}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              onAdd(rpc.call("voice.add", { epKey, text: `段${label} ${word} 改成 ${pinyin.trim()}${effScope === "ep" ? " 所有段" : ""}` }));
            }}
          >
            记下
          </button>
        )}
        <button className="ui-btn ui-btn--ghost ui-btn--sm" data-testid="fix-cancel" onClick={onCancel}>
          取消
        </button>
      </span>
    </div>
  );
}
