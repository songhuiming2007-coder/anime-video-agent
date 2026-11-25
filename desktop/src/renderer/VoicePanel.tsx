// 03.5 顺听面板（Spec 11 §2.3/§4.4）：段落列表 + 播放走 renderer 原生 <audio>（ava-media://）+ 六指令按钮化。
//
// 映射表（冻结）：听 N → 行内播放钮；听 → 顺序播放；停 → 停；回滚 N → 行内 回滚（仅当 attic 有快照）；
// 撤回 N → 待应用条目行内 撤回；done → 完成并应用补丁。
// 自由文法纠错的解析与落盘都在 core（`/voice-parse` 纯算、`/voice-add` 重新解析原文），UI 只渲染结构化字段。
// 播放器从 afplay/QuickTime 改为原位 <audio>（direction §0.2 第 3 条：全要素应用内原位审计）。
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { encodeMediaUrl } from "../shared/mediaUrl";
import type { VoiceCheckJson, VoiceInfoJson, VoicePatchJson, VoiceSegmentJson } from "../shared/protocol";
import { heardKey, lockState, redoSegments, segStatus, synthSummary, type SegStatusKind } from "../shared/voiceInfo";
import { CopyButton } from "./copy";
import { FloatingTooltip } from "./floating";
import { Icon } from "./icons";
import { Collapsible, Disclosure, prefersReducedMotion, SegmentedBar } from "./motion";
import { errText, type RpcClient } from "./rpc";
import { Badge, StateView } from "./ui";

/** 播放条时钟：段都是秒级，不显小时。NaN / 负数落 0:00。 */
export function formatAudioClock(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const s = Math.floor(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

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

const LIVE_POLL_MS = 5000;

/** D73：每期最近一次读到的配音信息，切回重新挂载时首屏占位，挂载后立即重读。 */
const lastInfo = new Map<string, VoiceInfoJson>();

/** D87：每期在面板里放过的「重配后版本」（键 = 段号@完成时刻）。 */
const heardLog = new Map<string, Set<string>>();
const heardOf = (epKey: string): Set<string> => {
  let s = heardLog.get(epKey);
  if (s === undefined) heardLog.set(epKey, (s = new Set()));
  return s;
};

const STATUS_TONE: Record<SegStatusKind, "wait" | "warn" | "danger" | "ok" | undefined> = {
  running: "wait",
  queued: undefined,
  failed: "danger",
  interrupted: "warn",
  unfinished: "warn",
  redo: "warn",
  redone: "ok",
  heard: undefined,
};

const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

type QueueItem = { wav: string; label: string; mtime: number | null };
type BusyAction = "parse" | "add" | "retract" | "revert" | "reseed" | "apply" | "clearLock" | null;
type VoiceMsg = { kind: "ok" | "err"; text: string; n: number };

/**
 * 独立播放条组件：自己持有 <audio> 与 rAF 60fps 进度条 DOM 更新，
 * 避免播放期间每秒多次重渲染整张段落列表（40+ 段零重排）。
 */
function NowPlayingBar({
  epKey,
  current,
  remainingCount,
  paused,
  onPausedChange,
  onTogglePauseRef,
  onEnded,
  onStop,
}: {
  epKey: string;
  current: QueueItem | null;
  remainingCount: number;
  paused: boolean;
  onPausedChange: (p: boolean) => void;
  onTogglePauseRef: React.MutableRefObject<(() => void) | null>;
  onEnded: () => void;
  onStop: () => void;
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const seekRef = useRef<HTMLInputElement | null>(null);
  const draggingSeekRef = useRef(false);
  const [clock, setClock] = useState<{ sec: number; dur: number }>({ sec: 0, dur: 0 });
  const [playErr, setPlayErr] = useState<string | null>(null);

  const src = current ? encodeMediaUrl("episodes", `${epKey}/03-audio/${current.wav}`) : "";

  const togglePause = useCallback(() => {
    const a = audioRef.current;
    if (!a || !current) return;
    if (a.paused) {
      onPausedChange(false);
      void a.play().catch(() => onPausedChange(true));
    } else {
      a.pause();
      onPausedChange(true);
    }
  }, [current, onPausedChange]);

  useEffect(() => {
    onTogglePauseRef.current = togglePause;
    return () => {
      onTogglePauseRef.current = null;
    };
  }, [togglePause, onTogglePauseRef]);

  // 切段自动播放
  useEffect(() => {
    draggingSeekRef.current = false;
    setPlayErr(null);
    setClock({ sec: 0, dur: 0 });
    if (seekRef.current) {
      seekRef.current.max = "0";
      seekRef.current.value = "0";
      seekRef.current.disabled = true;
    }
    if (!current) {
      onPausedChange(false);
      return;
    }
    const a = audioRef.current;
    if (!a) return;
    onPausedChange(false);
    void a.play().catch(() => onPausedChange(true));
  }, [current, onPausedChange]);

  // 60fps 进度条直接写 DOM ref，数字时钟仅在整数秒变化时更新 React 状态
  useEffect(() => {
    const a = audioRef.current;
    if (!a || !current) return;
    let rafId = 0;
    const syncDom = () => {
      const t = Number.isFinite(a.currentTime) ? a.currentTime : 0;
      const d = Number.isFinite(a.duration) && a.duration > 0 ? a.duration : 0;
      const inp = seekRef.current;
      if (inp && !draggingSeekRef.current) {
        inp.max = String(d);
        inp.disabled = d <= 0;
        inp.value = String(d > 0 ? Math.min(t, d) : 0);
        inp.setAttribute("aria-valuetext", `${formatAudioClock(t)} / ${formatAudioClock(d)}`);
      }
      const sec = Math.floor(t);
      const dur = Math.floor(d);
      setClock((prev) => (prev.sec === sec && prev.dur === dur ? prev : { sec, dur }));
    };
    const loop = () => {
      syncDom();
      if (!a.paused && !a.ended) {
        rafId = requestAnimationFrame(loop);
      } else {
        rafId = 0;
      }
    };
    const startLoop = () => {
      if (rafId === 0) rafId = requestAnimationFrame(loop);
    };
    const stopLoop = () => {
      if (rafId !== 0) {
        cancelAnimationFrame(rafId);
        rafId = 0;
      }
      syncDom();
    };
    a.addEventListener("play", startLoop);
    a.addEventListener("playing", startLoop);
    a.addEventListener("pause", stopLoop);
    a.addEventListener("ended", stopLoop);
    a.addEventListener("loadedmetadata", syncDom);
    a.addEventListener("durationchange", syncDom);
    a.addEventListener("seeked", syncDom);
    syncDom();
    if (!a.paused) startLoop();
    return () => {
      if (rafId !== 0) cancelAnimationFrame(rafId);
      a.removeEventListener("play", startLoop);
      a.removeEventListener("playing", startLoop);
      a.removeEventListener("pause", stopLoop);
      a.removeEventListener("ended", stopLoop);
      a.removeEventListener("loadedmetadata", syncDom);
      a.removeEventListener("durationchange", syncDom);
      a.removeEventListener("seeked", syncDom);
    };
  }, [current]);

  return (
    <>
      <Disclosure open={current !== null} keepLastContent>
        {current !== null && (
          <div className="voice-nowplaying ui-card-enter" data-testid="voice-nowplaying">
            <button
              type="button"
              className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
              data-testid="voice-nowplaying-toggle"
              onClick={togglePause}
              aria-label={paused ? "继续播放" : "暂停播放"}
            >
              <Icon name={paused ? "play" : "pause"} size="sm" />
            </button>
            <span className="voice-nowplaying-name">段 {current.label}</span>
            {remainingCount > 0 && <span className="ui-badge">余 {remainingCount} 段</span>}
            <input
              ref={seekRef}
              type="range"
              className="voice-nowplaying-seek"
              data-testid="voice-seek"
              aria-label="播放进度"
              aria-valuetext={`${formatAudioClock(clock.sec)} / ${formatAudioClock(clock.dur)}`}
              min={0}
              max={0}
              step={0.05}
              defaultValue={0}
              disabled
              onPointerDown={(e) => {
                draggingSeekRef.current = true;
                try {
                  e.currentTarget.setPointerCapture(e.pointerId);
                } catch {
                  /* 合成事件或已失效指针 */
                }
              }}
              onPointerUp={(e) => {
                draggingSeekRef.current = false;
                try {
                  if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
                } catch {
                  /* 忽略释放失败 */
                }
              }}
              onPointerCancel={() => {
                draggingSeekRef.current = false;
              }}
              onLostPointerCapture={() => {
                draggingSeekRef.current = false;
              }}
              onChange={(e) => {
                const a = audioRef.current;
                const next = Number(e.target.value);
                if (!a || !Number.isFinite(next) || !Number.isFinite(a.duration) || a.duration <= 0) return;
                a.currentTime = Math.max(0, Math.min(a.duration, next));
                const sec = Math.floor(a.currentTime);
                const dur = Math.floor(a.duration);
                setClock((prev) => (prev.sec === sec && prev.dur === dur ? prev : { sec, dur }));
              }}
            />
            <span className="voice-nowplaying-clock">
              {formatAudioClock(clock.sec)} / {formatAudioClock(clock.dur)}
            </span>
            <button
              type="button"
              className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
              onClick={() => {
                audioRef.current?.pause();
                onStop();
              }}
              aria-label="停止播放"
            >
              <Icon name="close" size="sm" />
            </button>
          </div>
        )}
      </Disclosure>
      {playErr && (
        <div className="notice notice--err voice-msg-bar ui-error-shake" role="alert">
          <Icon name="alert" size="sm" />
          <span>{playErr}</span>
        </div>
      )}
      {/* key 带 mtime：重配后同名 wav 换了内容，强制换一个元素重新取，不播旧缓冲（D87） */}
      {current !== null && (
        <audio
          key={`${current.wav}@${current.mtime ?? ""}`}
          ref={audioRef}
          src={src}
          data-testid="voice-audio"
          onPlay={() => onPausedChange(false)}
          onPause={() => onPausedChange(true)}
          onEnded={onEnded}
          onError={() => {
            onPausedChange(true);
            setPlayErr(`段 ${current.label} 音频无法播放，可能文件尚未写完或已损坏`);
          }}
        />
      )}
    </>
  );
}

export function VoicePanel({ epKey, rpc, onReady, live = false }: { epKey: string; rpc: RpcClient; onReady?: (ok: boolean) => void; live?: boolean }) {
  const [info, setInfo] = useState<VoiceInfoJson | null>(() => lastInfo.get(epKey) ?? null);
  const [err, setErr] = useState<string | null>(null);
  const [fresh, setFresh] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [raw, setRaw] = useState("");
  const [card, setCard] = useState<VoicePatchJson | null>(null);
  const [msg, setMsg] = useState<VoiceMsg | null>(null);
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const [revertArmed, setRevertArmed] = useState<string | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [paused, setPaused] = useState(false);
  const [, setHeardTick] = useState(0);
  const [fix, setFix] = useState<{ label: string; word: string } | null>(null);
  const togglePauseRef = useRef<(() => void) | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const busy = busyAction !== null;

  const pushMsg = useCallback((kind: "ok" | "err", text: string) => {
    setMsg((prev) => ({ kind, text, n: (prev?.n ?? 0) + 1 }));
  }, []);

  const load = useCallback(async () => {
    setErr(null);
    setRefreshing(true);
    try {
      const v = await rpc.call<VoiceInfoJson>("voice.info", { epKey });
      lastInfo.set(epKey, v);
      setInfo(v);
      setFresh(true);
    } catch (e) {
      setErr(errText(e));
    } finally {
      setRefreshing(false);
    }
  }, [epKey, rpc]);

  useEffect(() => {
    void load();
  }, [load]);

  const synthRunning = info?.synth?.state === "running";
  useEffect(() => {
    if (!live && !synthRunning) return;
    const t = setInterval(() => void load(), LIVE_POLL_MS);
    return () => clearInterval(t);
  }, [live, synthRunning, load]);

  useEffect(() => {
    if (info !== null) onReady?.(true);
    else if (err !== null) onReady?.(false);
  }, [err, info, onReady]);

  // 人时：顺听面板装载成功才计时（Spec 11 §2.4）；错误态不计（红队 ④）
  const surfaceUp = err === null && info !== null && fresh;
  useEffect(() => {
    if (!surfaceUp) return;
    void rpc.call("time.surface", { epKey, stop: "03.5", visible: "true" }).catch(() => undefined);
    return () => {
      void rpc.call("time.surface", { epKey, stop: "03.5", visible: "false" }).catch(() => undefined);
    };
  }, [epKey, rpc, surfaceUp]);

  useEffect(() => {
    if (revertArmed === null) return;
    const t = setTimeout(() => setRevertArmed(null), 5000);
    return () => clearTimeout(t);
  }, [revertArmed]);

  const current = queue[0] ?? null;

  // D87：放到哪段就把那段「这一版重配」记为已复听（开播即算）
  const curLabel = current?.label ?? null;
  const curDoneAt = curLabel === null ? undefined : info?.synth?.done[curLabel];
  useEffect(() => {
    if (curLabel === null || curDoneAt === undefined) return;
    const set = heardOf(epKey);
    const k = heardKey(curLabel, curDoneAt);
    if (set.has(k)) return;
    set.add(k);
    setHeardTick((n) => n + 1);
  }, [epKey, curLabel, curDoneAt]);

  // 播放推进到新段落时自动滚入视野
  useEffect(() => {
    if (curLabel === null) return;
    const row = listRef.current?.querySelector(`[data-testid="seg-row"][data-label="${CSS.escape(curLabel)}"]`);
    if (row instanceof HTMLElement) {
      row.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
    }
  }, [curLabel]);

  // 段落回滚或重配后 mtime 变化时同步队列
  useEffect(() => {
    if (!info) return;
    const byLabel = new Map(info.segments.map((s) => [s.label, s]));
    setQueue((prev) => {
      if (prev.length === 0) return prev;
      const head = prev[0];
      const headSeg = byLabel.get(head.label);
      if (!headSeg || !headSeg.wav_exists || headSeg.mtime !== head.mtime) {
        return [];
      }
      let changed = false;
      const next = [head];
      for (let i = 1; i < prev.length; i++) {
        const seg = byLabel.get(prev[i].label);
        if (!seg || !seg.wav_exists) {
          changed = true;
          continue;
        }
        if (seg.mtime !== prev[i].mtime || seg.wav !== prev[i].wav) {
          changed = true;
          next.push({ wav: seg.wav, label: seg.label, mtime: seg.mtime });
        } else {
          next.push(prev[i]);
        }
      }
      return changed ? next : prev;
    });
  }, [info]);

  const redoSegs = useMemo(() => (info ? redoSegments(info) : []), [info]);
  const redoSet = useMemo(() => new Set(redoSegs), [redoSegs]);
  const marksBySeg = useMemo(() => {
    const map = new Map<string, string[]>();
    if (!info) return map;
    const globals: string[] = [];
    for (const c of info.pending_corrections) {
      if (typeof c.word !== "string" || c.word === "") continue;
      if (c.scope === "global") {
        globals.push(c.word);
      } else {
        const k = String(c.segment);
        const arr = map.get(k);
        if (arr) arr.push(c.word);
        else map.set(k, [c.word]);
      }
    }
    if (globals.length > 0) {
      for (const s of info.segments) {
        const arr = map.get(s.label);
        map.set(s.label, arr ? [...arr, ...globals] : [...globals]);
      }
    }
    return map;
  }, [info]);

  const playOne = (seg: VoiceSegmentJson) => {
    setPaused(false);
    setQueue([{ wav: seg.wav, label: seg.label, mtime: seg.mtime }]);
  };
  const playAll = () => {
    setPaused(false);
    setQueue((info?.segments ?? []).filter((s) => s.wav_exists).map((s) => ({ wav: s.wav, label: s.label, mtime: s.mtime })));
  };
  const stop = useCallback(() => {
    setQueue([]);
    setPaused(false);
  }, []);

  const doParse = useCallback(
    async (call: Promise<unknown>) => {
      setBusyAction("parse");
      setMsg(null);
      setCard(null);
      try {
        setCard((await call) as VoicePatchJson);
      } catch (e) {
        pushMsg("err", errText(e));
      } finally {
        setBusyAction(null);
      }
    },
    [pushMsg],
  );

  const doAdd = useCallback(
    async (call: Promise<unknown>, action: BusyAction = "add") => {
      setBusyAction(action);
      setMsg(null);
      try {
        const entry = (await call) as Record<string, unknown>;
        pushMsg("ok", `已落盘 #${String(entry.id)} 段${String(entry.segment)}：回滚按段号，撤回按条目 id`);
        setCard(null);
        setRaw("");
        await load();
      } catch (e) {
        pushMsg("err", errText(e));
      } finally {
        setBusyAction(null);
      }
    },
    [load, pushMsg],
  );

  const doAction = useCallback(
    async (call: Promise<unknown>, okText: string, action: BusyAction) => {
      setBusyAction(action);
      setMsg(null);
      try {
        await call;
        pushMsg("ok", okText);
        await load();
      } catch (e) {
        pushMsg("err", errText(e));
      } finally {
        setBusyAction(null);
      }
    },
    [load, pushMsg],
  );

  const doDone = useCallback(
    async (call: Promise<unknown>) => {
      setBusyAction("apply");
      setMsg(null);
      try {
        const r = (await call) as { started: boolean };
        pushMsg("ok", r.started ? "已执行增量重配（进度见时间线）" : "已取消，未执行增量重配");
        await load();
      } catch (e) {
        pushMsg("err", errText(e));
      } finally {
        setBusyAction(null);
      }
    },
    [load, pushMsg],
  );

  if (info === null) {
    return err !== null ? (
      <StateView kind="error" title={err} />
    ) : (
      <StateView kind="loading" title="读取配音信息…" />
    );
  }

  const pending = info.pending_corrections;
  const lock = info.apply_patch_lock;
  const lockKind = lockState(lock);
  const lockStale = lockKind === "stale";
  const lockBusy = lockKind === "busy";
  const heard = heardOf(epKey);
  const today = todayIso();
  const summary = synthSummary(info, heard, today);
  const doneDisabled = busy || lockBusy || synthRunning || redoSegs.length === 0 || info.engine_cloud;
  const playableSegs = info.segments.filter((s) => s.wav_exists);
  const unheardSegs = playableSegs.filter((s) => segStatus(info, s.label, heard, today, redoSet)?.kind === "redone");

  const doneDisabledTip =
    info.engine_cloud
      ? "云端引擎请在云端执行 apply-patch"
      : lockBusy || synthRunning
        ? "重配进行中，完成后可再次执行"
        : redoSegs.length === 0
          ? "暂无需要重配的段落"
          : undefined;

  // 选中即弹卡（打开小卡不是写操作，不要求可信事件；写在小卡的「记下」上才要）
  const onSelectText = (label: string) => {
    const word = (window.getSelection()?.toString() ?? "").trim();
    if (word === "" || word.length > 32) return;
    setFix((prev) => (prev && prev.label === label && prev.word === word ? prev : { label, word }));
  };

  const visibleCardFields = card !== null ? PATCH_FIELDS.filter(([k]) => card[k] !== null && card[k] !== undefined && String(card[k]) !== "") : [];

  return (
    <div className="seg-panel voice-panel" data-testid="voice-panel">
      <div className="toolbar" data-testid="voice-toolbar">
        <button
          type="button"
          className="ui-btn ui-btn--ghost ui-btn--sm"
          data-testid="voice-play-all"
          disabled={busy || playableSegs.length === 0}
          onClick={playAll}
        >
          <Icon name="play" size="sm" />
          顺序播放
        </button>
        {unheardSegs.length > 0 && (
          <button
            type="button"
            className="ui-btn ui-btn--ghost ui-btn--sm"
            data-testid="voice-play-unheard"
            disabled={busy}
            onClick={() => {
              setPaused(false);
              setQueue(unheardSegs.map((s) => ({ wav: s.wav, label: s.label, mtime: s.mtime })));
            }}
          >
            只播待复听（{unheardSegs.length}）
          </button>
        )}
        <FloatingTooltip
          label="停止播放"
          renderTrigger={(t) => (
            <button
              type="button"
              ref={t.ref}
              {...t.props}
              className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
              data-testid="voice-stop"
              disabled={busy || current === null}
              onClick={stop}
              aria-label="停"
            >
              <Icon name="stop" size="sm" />
            </button>
          )}
        />
        <FloatingTooltip
          label="刷新配音状态"
          renderTrigger={(t) => (
            <button
              type="button"
              ref={t.ref}
              {...t.props}
              className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
              data-testid="voice-refresh"
              disabled={busy || refreshing}
              onClick={() => void load()}
              aria-label="刷新"
            >
              <Icon name="refresh" size="sm" className={refreshing ? "ui-spin-icon" : undefined} />
            </button>
          )}
        />
        <span className="muted voice-engine" data-testid="voice-engine">
          {info.engine === "" ? "引擎未记录" : `${info.engine}${info.engine_cloud ? " · 云端" : " · 本地"}`}
        </span>
      </div>
      <div className="muted voice-hint">选中段落里读错的字，就地记下；攒够了在底部只重配这些段。</div>
      {err !== null && (
        <div className="notice notice--warn voice-msg-bar" data-testid="voice-stale">
          <Icon name="alert" size="sm" />
          <span>刷新失败：{err}（以下为上次读到的内容）</span>
          <span className="spacer" />
          <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm" onClick={() => void load()}>
            重试
          </button>
        </div>
      )}

      {info.engine_cloud && (
        <div className="notice voice-msg-bar" data-testid="voice-cloud">
          <Icon name="info" size="sm" />
          本期配音引擎为云端引擎（{info.engine}），重配请去云端执行 apply-patch。
        </div>
      )}
      {lockBusy && (
        <div className="notice voice-msg-bar" data-testid="voice-lock-busy">
          <span className="ui-spinner" />
          增量重配进行中（pid {String(lock.pid)}）。
        </div>
      )}
      {lockStale && (
        <div className="notice notice--warn voice-msg-bar" data-testid="voice-lock-stale">
          <Icon name="alert" size="sm" />
          <span>
            检测到上次重配异常中断留下的锁（{lock.pid === null ? "pid 未记录" : `pid ${lock.pid} 已不存在`}），清除后即可重试。
          </span>
          <button
            type="button"
            className="ui-btn ui-btn--sm"
            data-testid="voice-clear-lock"
            disabled={busy}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void doAction(rpc.call("voice.clearStaleLock", { epKey }), "已清除残留锁，现在可以重试重配", "clearLock");
            }}
          >
            {busyAction === "clearLock" && <span className="ui-spinner" />}
            清除残留锁
          </button>
          <CopyButton text={`python -m pipeline.tts data/episodes/${epKey} --clear-stale-lock`} label="复制命令" testId="voice-lock-copy" />
        </div>
      )}

      {summary !== null && (
        <div className={`notice voice-synth voice-synth--${summary.tone}`} data-testid="voice-synth" data-state={info.synth?.state} title={summary.title}>
          {summary.tone === "wait" ? (
            <span className="ui-spinner" />
          ) : (
            <Icon name={summary.tone === "danger" || summary.tone === "warn" ? "alert" : "info"} size="sm" />
          )}
          <span>{summary.text}</span>
        </div>
      )}

      <div className="voice-list" ref={listRef} data-testid="voice-segments">
        {info.segments.length === 0 ? (
          <StateView kind="empty" title="当前还没有可顺听的段落" detail="配音完成后刷新即可逐段播放与纠错。" />
        ) : (
          info.segments.map((s) => {
            const st = segStatus(info, s.label, heard, today, redoSet);
            const isCurrent = current?.label === s.label && current.mtime === s.mtime;
            const isPlayingThis = isCurrent && !paused;
            const armed = revertArmed === s.label;
            return (
              <div className="voice-row" key={s.label} data-testid="seg-row" data-label={s.label} aria-current={isCurrent ? "true" : undefined}>
                <button
                  type="button"
                  className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon"
                  data-testid="seg-play"
                  disabled={!s.wav_exists}
                  onClick={() => (isCurrent ? togglePauseRef.current?.() : playOne(s))}
                  aria-label={isPlayingThis ? `暂停段 ${s.label}` : isCurrent ? `继续播放段 ${s.label}` : `播放段 ${s.label}`}
                >
                  <Icon name={isPlayingThis ? "pause" : "play"} size="sm" />
                </button>
                <div className="voice-row-main">
                  <div className="voice-row-head">
                    <span>段 {s.label}</span>
                    {st !== null && (
                      <span className="voice-status" data-testid="seg-status" data-kind={st.kind} title={st.title}>
                        <Badge tone={STATUS_TONE[st.kind]}>{st.text}</Badge>
                      </span>
                    )}
                    {!s.wav_exists && <span className="muted">（音频缺席）</span>}
                    <span className="seg-actions">
                      <button
                        type="button"
                        className="ui-btn ui-btn--ghost ui-btn--sm"
                        data-testid="seg-reseed"
                        disabled={busy}
                        title="听感不对（发飘、断层、吞字…）：换一个种子重配这一段"
                        onClick={(e) => {
                          if (!e.nativeEvent.isTrusted) return;
                          void doAdd(rpc.call("voice.add", { epKey, text: `段${s.label} 换种子` }), "reseed");
                        }}
                      >
                        {busyAction === "reseed" && <span className="ui-spinner" />}
                        换种子
                      </button>
                      {s.has_attic && (
                        armed ? (
                          <>
                            <button
                              type="button"
                              className="ui-btn ui-btn--danger ui-btn--sm"
                              data-testid="seg-revert"
                              disabled={busy}
                              onClick={(e) => {
                                if (!e.nativeEvent.isTrusted) return;
                                setRevertArmed(null);
                                void doAction(rpc.call("voice.revert", { epKey, label: s.label }), `已回滚段 ${s.label}`, "revert");
                              }}
                            >
                              {busyAction === "revert" && <span className="ui-spinner" />}
                              确认回滚
                            </button>
                            <button
                              type="button"
                              className="ui-btn ui-btn--ghost ui-btn--sm"
                              disabled={busy}
                              onClick={() => setRevertArmed(null)}
                            >
                              取消
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            className="ui-btn ui-btn--ghost ui-btn--danger-hover ui-btn--sm"
                            data-testid="seg-revert"
                            disabled={busy}
                            title="恢复这一段上一次重配前的音频快照"
                            onClick={() => setRevertArmed(s.label)}
                          >
                            回滚
                          </button>
                        )
                      )}
                    </span>
                  </div>
                  <div className="voice-row-text" data-testid="seg-text" onMouseUp={() => onSelectText(s.label)}>
                    <Marked text={s.text} marks={marksBySeg.get(s.label) ?? []} />
                  </div>
                  <Disclosure open={fix !== null && fix.label === s.label}>
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
                          void doAdd(call, "add");
                        }}
                        onGlobalDone={(line) => {
                          setFix(null);
                          pushMsg("ok", line);
                          void load();
                        }}
                      />
                    )}
                  </Disclosure>
                </div>
              </div>
            );
          })
        )}
      </div>

      {pending.length > 0 && (
        <Collapsible className="pending" testId="voice-pending" defaultOpen bar summary={`待应用纠错条目（${pending.length}）`}>
          {pending.map((c) => (
            <div className="seg-row" key={String(c.id)} data-testid="pending-row" data-id={String(c.id)}>
              <span className="ui-row-meta">#{String(c.id)}</span>
              <span className="ui-row-title">段{String(c.segment)}</span>
              <span className="ui-row-meta">
                {c.word ? `「${String(c.word)}」→ ${String(c.target_tone3)}` : String(c.issue ?? c.kind)} · {c.scope === "global" ? "本期所有段" : "本段"}
              </span>
              <span className="seg-actions">
                <button
                  type="button"
                  className="ui-btn ui-btn--ghost ui-btn--sm"
                  data-testid="pending-retract"
                  disabled={busy}
                  onClick={(e) => {
                    if (!e.nativeEvent.isTrusted) return;
                    void doAction(rpc.call("voice.retract", { epKey, id: String(c.id) }), `已撤回 #${String(c.id)}`, "retract");
                  }}
                >
                  {busyAction === "retract" && <span className="ui-spinner" />}
                  撤回
                </button>
              </span>
            </div>
          ))}
        </Collapsible>
      )}

      <Collapsible className="voice-grammar" summaryTestId="voice-grammar" bar summary="按文法录入（终端 /voice 同一文法）">
        <div className="voice-input" data-testid="voice-input">
          <label className="ui-field">
            如「1段 雪乃 改成 xuě nǎi」「2段 语气发飘 换种子」
            <input className="ui-input" data-testid="voice-raw" value={raw} onChange={(e) => setRaw(e.target.value)} />
          </label>
          <button
            type="button"
            className="ui-btn ui-btn--sm"
            data-testid="voice-parse"
            disabled={busy || raw.trim() === ""}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void doParse(rpc.call("voice.parse", { epKey, text: raw }));
            }}
          >
            {busyAction === "parse" && <span className="ui-spinner" />}
            解析
          </button>
        </div>
        {card !== null && (
          <div className="voice-card ui-card-enter" data-testid="voice-card">
            <div className="ui-section">确认卡（与终端同语义）</div>
            <dl className="ui-kv">
              {visibleCardFields.map(([k, label]) => (
                <Fragment key={k}>
                  <dt>{label}</dt>
                  <dd>{String(card[k])}</dd>
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
              type="button"
              className="ui-btn ui-btn--sm"
              data-testid="voice-add"
              disabled={busy}
              onClick={(e) => {
                if (!e.nativeEvent.isTrusted) return;
                void doAdd(rpc.call("voice.add", { epKey, text: raw }), "add");
              }}
            >
              {busyAction === "add" && <span className="ui-spinner" />}
              确认落盘
            </button>
          </div>
        )}
      </Collapsible>

      {info.heteronyms.length > 0 && (
        <Collapsible className="hetero" testId="voice-heteronyms" bar summary={`多音字预检（${info.heteronyms.length} 个字，只读，仅供关注）`}>
          <div className="voice-chips">
            {info.heteronyms.map((h) => (
              <span key={h.char} className="ui-badge">
                {h.char} {h.readings.join("/")}
              </span>
            ))}
          </div>
        </Collapsible>
      )}

      {msg !== null && (
        <div
          key={msg.n}
          className={msg.kind === "err" ? "notice notice--err voice-msg-bar ui-error-shake" : "notice voice-msg-bar ui-confirm-enter"}
          data-testid="voice-msg"
          role={msg.kind === "err" ? "alert" : "status"}
        >
          <Icon name={msg.kind === "err" ? "alert" : "info"} size="sm" />
          <span>{msg.text}</span>
          <span className="spacer" />
          <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm ui-btn--icon" aria-label="关闭提示" onClick={() => setMsg(null)}>
            <Icon name="close" size="sm" />
          </button>
        </div>
      )}

      <NowPlayingBar
        epKey={epKey}
        current={current}
        remainingCount={Math.max(0, queue.length - 1)}
        paused={paused}
        onPausedChange={setPaused}
        onTogglePauseRef={togglePauseRef}
        onEnded={() => setQueue((q) => q.slice(1))}
        onStop={stop}
      />

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
            type="button"
            className="ui-btn"
            data-testid="voice-done"
            data-mode="apply-patch"
            disabled={doneDisabled}
            title={doneDisabledTip}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void doDone(rpc.call("voice.applyPatch", { epKey }));
            }}
          >
            {busyAction === "apply" && <span className="ui-spinner" />}
            {redoSegs.length > 0 ? `只重配这 ${redoSegs.length} 段` : "无需重配"}
          </button>
        ) : (
          <button
            type="button"
            className="ui-btn"
            data-testid="voice-done"
            data-mode="retts"
            disabled={doneDisabled}
            title={doneDisabledTip}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void doDone(rpc.call("voice.retts", { epKey }));
            }}
          >
            {busyAction === "apply" && <span className="ui-spinner" />}
            {redoSegs.length > 0 ? `只重配这 ${redoSegs.length} 段` : "无需重配"}
          </button>
        )}
      </div>
    </div>
  );
}

/** 段落文字里标出已录、待重配的词 */
function Marked({ text, marks }: { text: string; marks: string[] }) {
  if (marks.length === 0) return <>{text}</>;
  const uniq = [...new Set(marks)];
  const re = new RegExp(`(${uniq.map((m) => m.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "g");
  return (
    <>
      {text.split(re).map((part, i) =>
        uniq.includes(part) ? (
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

type Method = "pinyin" | "homophone" | "latin";
type Scope = "seg" | "ep" | "global";

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
  onAdd: (call: Promise<unknown>) => void;
  onGlobalDone: (line: string) => void;
}) {
  const looksLatin = /^[A-Za-z0-9\s\-_.'’]+$/.test(word) && /[A-Za-z]/.test(word);
  const [pinyin, setPinyin] = useState("");
  const [method, setMethod] = useState<Method>(() => (looksLatin ? "latin" : "pinyin"));
  const [homophone, setHomophone] = useState("");
  const [scope, setScope] = useState<Scope>("seg");
  const [check, setCheck] = useState<VoiceCheckJson | null>(null);
  const [writing, setWriting] = useState(false);
  const [result, setResult] = useState<VoiceCheckJson | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);
  const effScope: Scope = method === "pinyin" ? scope : "global";
  const reading =
    method === "pinyin" ? { word, pinyin } : method === "latin" ? { word, latin: pinyin } : { word, homophone, expect: pinyin };
  const ready = pinyin.trim() !== "" && (method !== "homophone" || homophone.trim() !== "");

  useEffect(() => {
    cardRef.current?.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }, []);

  // 输入或改法变动时清空旧的写入结果提示
  useEffect(() => {
    setResult(null);
  }, [pinyin, homophone, method, scope]);

  // 读音核对：停手 400 ms 后跑一次，取消过时请求防止旧结果覆盖新输入
  useEffect(() => {
    setCheck(null);
    if (!ready) return;
    let alive = true;
    const t = setTimeout(() => {
      void rpc.call<VoiceCheckJson>("voice.check", { epKey, ...reading }).then(
        (res) => {
          if (alive) setCheck(res);
        },
        (e) => {
          if (alive) setCheck({ ok: false, lines: [errText(e)] });
        },
      );
    }, 400);
    return () => {
      alive = false;
      clearTimeout(t);
    };
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
    <div
      ref={cardRef}
      className="ui-popover voice-fix ui-card-enter"
      data-testid="voice-fix"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          onCancel();
        }
      }}
    >
      <span>{method === "homophone" ? "期望读音（核对用）" : `「${word}」应读`}</span>
      <span>
        <input
          className="ui-input voice-fix-input"
          data-testid="fix-pinyin"
          autoFocus
          placeholder={method === "latin" ? "如 Eternal Wind" : "如 jie3 di4"}
          value={pinyin}
          onChange={(e) => setPinyin(e.target.value)}
        />
      </span>
      <span>改法</span>
      <SegmentedBar activeKey={method} label="改法">
        <button type="button" aria-pressed={method === "pinyin"} data-testid="fix-method-pinyin" onClick={() => setMethod("pinyin")}>
          拼音直注
        </button>
        <button type="button" aria-pressed={method === "homophone"} data-testid="fix-method-homophone" onClick={() => setMethod("homophone")}>
          同音字
        </button>
        <button type="button" aria-pressed={method === "latin"} data-testid="fix-method-latin" onClick={() => setMethod("latin")}>
          英文改写
        </button>
      </SegmentedBar>
      {method === "homophone" && (
        <>
          <span>同音字</span>
          <span>
            <input
              className="ui-input voice-fix-input"
              data-testid="fix-homophone"
              placeholder="读音唯一的字"
              value={homophone}
              onChange={(e) => setHomophone(e.target.value)}
            />
          </span>
        </>
      )}
      <span>范围</span>
      <SegmentedBar activeKey={effScope} label="生效范围">
        <button type="button" aria-pressed={effScope === "seg"} disabled={method !== "pinyin"} data-testid="fix-scope-seg" onClick={() => setScope("seg")}>
          本段 {label}
        </button>
        <button type="button" aria-pressed={effScope === "ep"} disabled={method !== "pinyin"} data-testid="fix-scope-ep" onClick={() => setScope("ep")}>
          本期
        </button>
        <button type="button" aria-pressed={effScope === "global"} data-testid="fix-scope-global" onClick={() => setScope("global")}>
          全局
        </button>
      </SegmentedBar>
      {method === "homophone" && (
        <>
          <span />
          <span className="muted">同音字只写全局表（所有番、所有期）</span>
        </>
      )}
      {method === "latin" && (
        <>
          <span />
          <span className="muted" data-testid="fix-latin-hint">
            全大写常被逐字母念：改成首字母大写或小写；缩写要逐字母念就用空格隔开（如 M two U）。只写全局表，写入后重配本段复听确认
          </span>
        </>
      )}
      <span>核对</span>
      <span className={check === null ? "muted" : check.ok ? "voice-ok" : "voice-bad"} data-testid="fix-check">
        {!ready ? "填好读音后自动核对" : check === null ? "核对中…" : check.lines.join("；")}
      </span>
      {result !== null && !result.ok && (
        <>
          <span />
          <span className="voice-bad ui-error-shake" data-testid="fix-result">
            {result.lines.join("；")}
          </span>
        </>
      )}
      <span />
      <span className="voice-fix-actions">
        {needSupersede ? (
          <button
            type="button"
            className="ui-btn ui-btn--sm"
            data-testid="fix-supersede"
            disabled={busy || writing}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void finishGlobal(rpc.call<VoiceCheckJson>("voice.global", { epKey, ...reading, supersede: "true" }));
            }}
          >
            {writing && <span className="ui-spinner" />}
            替换另一张表的同名条目并写入
          </button>
        ) : effScope === "global" ? (
          <button
            type="button"
            className="ui-btn ui-btn--sm"
            data-testid="fix-save"
            disabled={busy || writing || check === null || !check.ok}
            onClick={(e) => {
              if (!e.nativeEvent.isTrusted) return;
              void finishGlobal(rpc.call<VoiceCheckJson>("voice.global", { epKey, ...reading, supersede: "false" }));
            }}
          >
            {writing && <span className="ui-spinner" />}
            记下（写全局表）
          </button>
        ) : (
          <button
            type="button"
            className="ui-btn ui-btn--sm"
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
        <button type="button" className="ui-btn ui-btn--ghost ui-btn--sm" data-testid="fix-cancel" onClick={onCancel}>
          取消
        </button>
      </span>
    </div>
  );
}
