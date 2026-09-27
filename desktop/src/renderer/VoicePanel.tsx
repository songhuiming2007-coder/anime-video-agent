// 03.5 顺听面板（Spec 11 §2.3/§4.4）：段落列表 + 播放走 renderer 原生 <audio>（ava-media://）+ 六指令按钮化。
//
// 映射表（冻结）：听 N → 行内 ▶；听 → 顺序播放；停 → 停；回滚 N → 行内 回滚（仅当 attic 有快照）；
// 撤回 N → 待应用条目行内 撤回；done → 完成并应用补丁。
// 自由文法纠错的解析与落盘都在 core（`/voice-parse` 纯算、`/voice-add` 重新解析原文），UI 只渲染结构化字段。
// 播放器从 afplay/QuickTime 改为原位 <audio>（direction §0.2 第 3 条：全要素应用内原位审计）。
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { encodeMediaUrl } from "../shared/mediaUrl";
import type { VoiceInfoJson, VoicePatchJson, VoiceSegmentJson } from "../shared/protocol";
import { lockState } from "../shared/voiceInfo";
import { Icon } from "./icons";
import { errText, type RpcClient } from "./rpc";
import { StateView } from "./ui";

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

export function VoicePanel({ epKey, rpc }: { epKey: string; rpc: RpcClient }) {
  const [info, setInfo] = useState<VoiceInfoJson | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [raw, setRaw] = useState("");
  const [card, setCard] = useState<VoicePatchJson | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [queue, setQueue] = useState<{ wav: string; label: string }[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      setInfo(await rpc.call<VoiceInfoJson>("voice.info", { epKey }));
    } catch (e) {
      setErr(errText(e));
    }
  }, [epKey, rpc]);

  useEffect(() => {
    void load();
  }, [load]);

  // 人时：顺听面板**装载成功**才计时（Spec 11 §2.4）；错误态不计（红队 ④）
  const surfaceUp = err === null && info !== null;
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

  if (err !== null) return <StateView kind="error" title={err} />;
  if (info === null) return <StateView kind="loading" title="读取配音信息…" />;
  const pending = info.pending_corrections;
  const lock = info.apply_patch_lock;
  const lockKind = lockState(lock);
  const lockStale = lockKind === "stale";
  const lockBusy = lockKind === "busy";
  const doneDisabled = busy || pending.length === 0 || info.engine_cloud;

  return (
    <div className="seg-panel" data-testid="voice-panel">
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
        <span className="muted" data-testid="voice-engine">{info.engine === "" ? "引擎未记录" : info.engine}</span>
        <button
          className="ui-btn"
          data-testid="voice-done"
          disabled={doneDisabled}
          onClick={(e) => {
            if (!e.nativeEvent.isTrusted) return;
            void doDone(rpc.call("voice.applyPatch", { epKey }));
          }}
        >
          完成并应用补丁
        </button>
      </div>

      {pending.length === 0 && <div className="muted" data-testid="voice-no-pending">当前没有待应用的纠错条目</div>}
      {info.engine_cloud && (
        <div className="notice" data-testid="voice-cloud">
          <Icon name="info" size="sm" />
          本期配音引擎为云端引擎（{info.engine}），请去云端执行 apply-patch。
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

      <div className="seg-list" data-testid="voice-segments">
        {info.segments.map((s) => (
          <div className="seg-row" key={s.label} data-testid="seg-row" data-label={s.label}>
            <button className="ui-btn ui-btn--sm" data-testid="seg-play" disabled={!s.wav_exists} onClick={() => playOne(s)} aria-label={`播放段 ${s.label}`}>
              ▶
            </button>
            <span className="ui-row-title">段 {s.label}</span>
            <span className="ui-row-meta">
              {s.text}
              {!s.wav_exists && <span className="muted">（音频缺席）</span>}
            </span>
            <span className="seg-actions">
              {s.has_attic && (
                <button
                  className="ui-btn ui-btn--sm"
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
        ))}
      </div>

      {pending.length > 0 && (
        <div className="pending" data-testid="voice-pending">
          <div className="ui-section">待应用纠错条目（{pending.length}）</div>
          {pending.map((c) => (
            <div className="seg-row" key={String(c.id)} data-testid="pending-row" data-id={String(c.id)}>
              <span className="ui-row-meta">#{String(c.id)}</span>
              <span className="ui-row-title">段{String(c.segment)}</span>
              <span className="ui-row-meta">
                {String(c.kind)} · {String(c.scope)}
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
        </div>
      )}

      <div className="voice-input" data-testid="voice-input">
        <label className="ui-field">
          纠错（终端同一文法：如「听成 雪之下，改成 xue3」/「段 5 语速太快」）
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
              【全局生效】该拼音注入将影响全期所有包含该词的段落！
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

      {msg !== null && (
        <div className="notice" data-testid="voice-msg">
          <Icon name="info" size="sm" />
          {msg}
        </div>
      )}

      {info.heteronyms.length > 0 && (
        <div className="hetero" data-testid="voice-heteronyms">
          <div className="ui-section">多音字预检（只读；仅供关注，非错误）</div>
          <ul className="ui-row-meta">
            {info.heteronyms.map((h) => (
              <li key={h.char}>
                · 字「{h.char}」候选：{h.readings.join(", ")}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="muted" data-testid="voice-review-note">
        结构化打点（manifest human_review 五项）须在终端
        <code>{` python -m pipeline.tts ${epKey} --review `}</code>
        完成。
      </div>

      {current !== null && <audio ref={audioRef} src={urlOf(current.wav)} data-testid="voice-audio" onEnded={() => setQueue((q) => q.slice(1))} />}
    </div>
  );
}
