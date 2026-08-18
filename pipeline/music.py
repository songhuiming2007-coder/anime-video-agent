"""试听型乐评的音乐时间轴：`02-script.md` 的 `音乐段` 标记 → 成片时间轴与音量事件。

本期（2026-08-15 罪恶王冠·音乐乐评）是试听型乐评：音乐本身是评论证据，
旁白必须让出空间。与普通 BGM 铺底不同，本期音乐轨要回答三个问题：

1. **哪些时刻是前景**（音乐大音量、旁白停）——`## 音乐段 Mx` 块
2. **前景之后音轨怎么走**——`过渡:` 行（降为 BGM / 淡出 / 自然播放至结束）
3. **每首曲目在成片里的事件序列**——由 1+2 推导

「机制进代码、内容进配置」：本模块只解析与推导，曲目信息（路径/lufs）在
`config/bgm.json`，每期的前景区间写在当期 `02-script.md` 的 `音乐段` 块里。
换一期只改稿子，不动代码。

音量策略（2026-08-16 用户拍板）：
- 前景：曲目实测 LUFS 归一到 `FOREGROUND_LUFS = -14`
- BGM：归一到 -26（render 的 BGM_TARGET_LUFS），再经侧链闪避
- 自然播放（结尾）：同前景音量，让歌曲完整收尾
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import paths

# 前景目标响度。2026-08-16 两版成片实测：-14 时前景音乐窗口电平（-10~-15
# dBFS）与说话中的人声（-13~-16）几乎同响，解说被音乐「顶平」——音乐间隙
# 与旁白段落只有 1-2dB 差，听感上解说明显小于普通期（普通期 BGM 低人声
# 10dB+）。-19 是估算值：前景比 BGM(-26) 仍高 7dB（保留「试听大音量」语义），
# 但整片音乐能量降 ~3.5dB 后 loudnorm 增益回升，人声回到普通期水平、
# 音乐低于人声 3-5dB。待 2026-08-16 版试听后按听感微调（-16 温和/-19 当前）。
FOREGROUND_LUFS = -19.0

_HEAD = re.compile(r"^##\s*(段落|音乐段)\s*(\S+)", re.M)
_TITLE_ONLY = re.compile(r"^音乐[：:]\s*`(.+?)`", re.M)
_TIME_RANGE = re.compile(r"`(.+?)`.+?(\d+):(\d{2})-(?:(\d+):(\d{2})|结束)")
_TRANSITION = re.compile(r"^过渡[：:]\s*(.+)$", re.M)
# 音乐段块的可选 `画面:` 行：指定画面来源（如 `S01E01 0:00` = 从该集该秒切）。
# 不写 = 上一段最后一帧定格。这是内容声明，换期照写即可。
_VISUAL = re.compile(r"^画面[：:]\s*(.+)$", re.M)
# 段落 9 那种「音乐: `Planetes` 继续播放至完整版结束」——自然收尾标记
_NATURAL = re.compile(r"^音乐[：:]\s*`(.+?)`.+?(?:自然|完整版结束|继续播放)", re.M)


@dataclass
class MusicBlock:
    """`## 音乐段 Mx` 块解析结果。"""

    label: str
    title: str
    t0: float
    t1: float | None        # None = 到曲目结束
    after: str              # 'bgm' 降为 BGM / 'fade' 淡出 / 'end' 自然播放至结束
    visual: str | None      # `画面:` 行（如 "S01E01 0:00"）；None = 上一段定格


def _to_sec(m: int, s: str) -> float:
    return m * 60 + int(s)


def parse_script_music(path: Path) -> list[MusicBlock]:
    """解析 02-script.md 里全部 `音乐段` 块。

    `过渡:` 行决定 after（缺行/不认识的值直接报错——审查 B3：静默兜底
    fade 会让「自然播放下一首」被误判成 end）：
    - 「降为 BGM」→ bgm（同一音轨降音量继续覆盖其后段落）
    - 「自然播放/自然结束」→ end（播到曲目结束）
    - 其余 → fade（淡出 / 自然进入下一首）
    """
    text = path.read_text(encoding="utf-8")
    blocks: list[MusicBlock] = []
    for m in re.finditer(r"^##\s*音乐段\s*(\S+)", text, re.M):
        nxt = text.find("\n## ", m.end())
        body = text[m.end():nxt] if nxt != -1 else text[m.end():]
        title_m = _TITLE_ONLY.search(body)
        if not title_m:
            raise SystemExit(f"FAIL 音乐段 {m.group(1)} 缺 `音乐: `行")
        tm = _TIME_RANGE.search(body)
        if not tm:
            # 审查 B2：时间解析失败静默回退 t0=0/t1=None（整曲从 0 播）——
            # 这正是本项目最恨的静默失败，直接报错指明块
            raise SystemExit(
                f"FAIL 音乐段 {m.group(1)} 的 `音乐: `行时间解析失败"
                f"（应为 `音乐: `曲名` 完整版 MM:SS-MM:SS` 或 `MM:SS-结束`）")
        trans = _TRANSITION.search(body)
        if not trans:
            raise SystemExit(f"FAIL 音乐段 {m.group(1)} 缺 `过渡: `行")
        trans_text = trans.group(1)
        t0 = _to_sec(int(tm.group(2)), tm.group(3))
        t1 = None if tm.group(4) is None else (
            _to_sec(int(tm.group(4)), tm.group(5)))
        if "降为 BGM" in trans_text:
            after = "bgm"
        elif "自然播放" in trans_text or "自然结束" in trans_text:
            after = "end"
        elif "淡出" in trans_text or "自然进入下一首" in trans_text:
            after = "fade"
        else:
            raise SystemExit(
                f"FAIL 音乐段 {m.group(1)} 的 `过渡: `行不认识"
                f"（{trans_text}）——只认「降为 BGM / 自然播放(至结束) / 淡出」")
        vis = _VISUAL.search(body)
        blocks.append(MusicBlock(m.group(1), title_m.group(1), t0, t1, after,
                                 vis.group(1).strip() if vis else None))
    return blocks


def _track_for(title: str, bgm: dict) -> dict:
    """稿子曲名 → bgm.json 的曲目记录。

    稿子写的是歌名（My Dearest），曲库键名可能带版本后缀
    （My Dearest (Album Mix)）。按前缀匹配，严格到只有一个命中。
    """
    hits = [rec for key, rec in bgm.get("tracks", {}).items()
            if key.startswith(title) or title.startswith(key)]
    if len(hits) != 1:
        raise SystemExit(
            f"FAIL 曲库匹配「{title}」得到 {len(hits)} 个（必须恰好 1 个）")
    rec = hits[0]
    # 配置前置校验（2026-08-18 复盘②）：记录缺字段、文件不存在，在解析
    # 时间轴时当场报——本函数是所有曲目记录的唯一入口，不拦的话错会流到
    # 渲染中段的 ffmpeg（文件不存在）或音量算术（lufs 为 None 时 TypeError）
    for k in ("path", "dur", "lufs"):
        if rec.get(k) is None:
            raise SystemExit(
                f"FAIL 「{title}」在 config/bgm.json 的记录缺 `{k}`。"
                f"量法：python -m pipeline.bgm measure <文件>，结果补进曲目表")
    if not (paths.ROOT / rec["path"]).exists():
        raise SystemExit(
            f"FAIL BGM 文件不存在：{paths.ROOT / rec['path']}（「{title}」）\n"
            f"     检查 config/bgm.json 的 path，或重跑 `python -m pipeline.bgm extract` 分轨")
    return rec


def build_timeline(episode: Path, manifest: dict, bgm: dict) -> dict:
    """唯一入口：稿子标题顺序 → 成片时间轴 + 每曲事件序列。

    返回：
      {
        "total_duration": float,   # 成片总长（段落 + 音乐段占位 + 自然收尾）
        "segments": [              # 段落成片起点（字幕/人声/画面轨都要用它）
          {index, start, dur},
        ],
        "blocks": [                # 音乐段在成片里的占位（前景段）
          {label, title, start, dur, vol: "foreground", visual},
        ],
        "tracks": [                # 每首曲目的事件序列（曲目内位置）
          {name, path, lufs, events: [{t0, t1, vol, at}]},
        ],
      }

    事件语义：
      - vol=foreground：前景大音量，旁白停
      - vol=bgm：同音轨低音量 + 侧链闪避，覆盖其后段落
      - vol=natural：结尾自然播放（同前景音量），播到曲目结束
      - 事件之间曲目内位置不连续 = 跳点（渲染时事件边界淡入淡出衔接）
    """
    script = episode / "02-script.md"
    text = script.read_text(encoding="utf-8")
    blocks = {b.label: b for b in parse_script_music(script)}
    segs = {int(m.group(2)): m.group(2)
            for m in _HEAD.finditer(text) if m.group(1) == "段落"}

    # 稿子里段落/音乐段的出现顺序 → 成片占位
    timeline: list[dict] = []
    t = 0.0
    for m in _HEAD.finditer(text):
        kind, label = m.group(1), m.group(2)
        if kind == "段落":
            n = int(label)
            seg = next((s for s in manifest["segments"] if s["index"] == n), None)
            if seg is None:
                raise SystemExit(f"FAIL manifest 里没有段落 {n}")
            timeline.append({"kind": "seg", "n": n, "start": t,
                             "dur": seg["duration"]})
            t += seg["duration"]
        else:
            b = blocks[label]
            t1 = b.t1 if b.t1 is not None else _track_for(b.title, bgm)["dur"]
            if not (0.0 <= b.t0 < t1 <= _track_for(b.title, bgm)["dur"] + 1e-6):
                # 审查 B5：t0/t1 越界不拦的话，ffmpeg -ss 出空流，报错离病根很远
                raise SystemExit(
                    f"FAIL 音乐段 {label} 时间越界：{b.t0:.1f}-{t1:.1f}s"
                    f"（曲目《{b.title}》全长"
                    f" {_track_for(b.title, bgm)['dur']:.1f}s）")
            timeline.append({"kind": "music", "label": label,
                             "title": b.title, "t0": b.t0, "t1": t1,
                             "after": b.after, "visual": b.visual,
                             "start": t})
            t += t1 - b.t0

    # 段落块内的「音乐: `X` 继续播放至完整版结束」→ 自然收尾事件
    # （审查 B6：循环只迭代段落块，body 里不可能有 `音乐段` 标题——死条件已删；
    #  段落找不到时给干净 FAIL，不 TypeError 崩溃）
    naturals: list[tuple[str, float]] = []
    for seg_m in re.finditer(r"^##\s*段落\s*(\S+)", text, re.M):
        nxt = text.find("\n## ", seg_m.end())
        body = text[seg_m.end():nxt] if nxt != -1 else text[seg_m.end():]
        nat = _NATURAL.search(body)
        if nat:
            seg_end = next((it for it in timeline if it["kind"] == "seg"
                            and it["n"] == int(seg_m.group(1))), None)
            if seg_end is None:
                raise SystemExit(f"FAIL 段落 {seg_m.group(1)} 不在时间轴里")
            naturals.append((nat.group(1),
                             seg_end["start"] + seg_end["dur"]))

    # 每曲事件序列
    track_evs: dict[str, list[dict]] = {}
    order: list[str] = []
    for i, it in enumerate(timeline):
        if it["kind"] != "music":
            continue
        title = it["title"]
        if title not in order:
            order.append(title)
        evs = track_evs.setdefault(title, [])
        t0, t1 = it["t0"], it["t1"]
        evs.append({"t0": t0, "t1": t1, "vol": "foreground", "at": it["start"]})
        if it["after"] == "bgm":
            # BGM 从前景结束点继续，覆盖到**下一个音乐段开始**
            # （BGM 是给旁白让路的铺底，旁白段落之间由下一个音乐段接管）。
            nxt = next((x for x in timeline[i + 1:] if x["kind"] == "music"), None)
            nxt_start = (nxt["start"] if nxt
                         else timeline[-1]["start"] + timeline[-1]["dur"])
            evs.append({"t0": t1, "t1": t1 + (nxt_start - it["start"] - (t1 - t0)),
                        "vol": "bgm",
                        "at": it["start"] + (t1 - t0)})
        elif it["after"] == "end":
            evs.append({"t0": t1, "t1": None, "vol": "natural",
                        "at": it["start"] + (t1 - t0)})

    for title, at in naturals:
        if title not in order:
            order.append(title)
        evs = track_evs.setdefault(title, [])
        # natural 从当前曲目播放位置接续：查同曲最后一个事件的曲目内终点
        tail_t = evs[-1]["t1"] if evs else 0.0
        evs.append({"t0": tail_t, "t1": None, "vol": "natural", "at": at})

    # 解析曲目信息 + 修正 t1=None（曲目结束）
    tracks = []
    for title in order:
        rec = _track_for(title, bgm)
        dur_all = rec["dur"]
        evs = []
        for ev in track_evs[title]:
            e = dict(ev)
            if e["t1"] is None:
                e["t1"] = dur_all
            # natural 段的曲目内终点 = 曲目结束
            if e["vol"] == "natural":
                e["t1"] = dur_all
            # BGM 延续事件的终点 = 曲目内起点 + 成片 BGM 段时长，**可能超出
            # 曲目全长**（2026-08-16 审计 2-13）：渲染端 `-ss t0 -t dur` 对超界
            # 静默截短，中段音乐空缺，而 render 的时长校验只查音乐床总长
            # （amix longest 不变），测不出中段空洞。前景块自带越界校验
            # （上方 B5），这条延续路径漏了同类检查。
            if e["t1"] > dur_all + 1e-6:
                raise SystemExit(
                    f"FAIL 《{title}》的 BGM 延续段要播到曲目内 {e['t1']:.1f}s，"
                    f"但曲目全长只有 {dur_all:.1f}s。\n"
                    f"     超界部分会被 ffmpeg 静默截短，中段音乐空缺。"
                    f"把这一段的 `过渡: 降为 BGM` 改成 `淡出`，或缩短前景区间")
            evs.append(e)
        tracks.append({"name": title, "path": rec["path"],
                       "lufs": rec["lufs"], "events": evs})

    # 成片总长：稿子时间轴末尾 + 自然收尾段（natural 在最后一个段落之后，不在 timeline 里）
    natural_end = max(
        (e["at"] + e["t1"] - e["t0"]
         for tr in tracks for e in tr["events"] if e["vol"] == "natural"),
        default=0.0)

    return {
        "total_duration": max(
            timeline[-1]["start"] + timeline[-1]["dur"], natural_end),
        "segments": [{"index": it["n"], "start": it["start"],
                      "dur": it["dur"]}
                     for it in timeline if it["kind"] == "seg"],
        "blocks": [{"label": it["label"], "title": it["title"],
                    "start": it["start"],
                    "dur": (min(it["t1"], _track_for(it["title"], bgm)["dur"])
                            if it["t1"] is not None
                            else _track_for(it["title"], bgm)["dur"]) - it["t0"],
                    "vol": "foreground",
                    "visual": it["visual"]}
                   for it in timeline if it["kind"] == "music"],
        "tracks": tracks,
    }
