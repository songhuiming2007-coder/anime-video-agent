"""审时间码（流程第 05 步）：把排片结果变成一页能扫的图。

    python -m pipeline.review data/episodes/<本期>            # 出 04-review.html
    python -m pipeline.review data/episodes/<本期> --approve  # 存成 approved 版

**这是画面轨唯一的人工关卡**（CLAUDE.md：人类在 02.5 / 05 / 09 出现，
05 是画面轨唯一人工关卡），而它此前从没建过——2026-07-29 检查发现本期的 `04-clips.approved.json` 与
`04-clips.json` 字节完全相同，也就是说画面是检索直出、没过人眼就渲了。

**要人看的是什么：** 语义检索最典型的错误是「台词对了但画面不对」——
索引建在字幕上，命中的是说了什么；那句话可能是画外音，镜头在拍别的东西。
这种错机器判不了，只有眼睛能抓。所以这一页要并排给出三样：
这段口播在说什么、画面长什么样、那一刻字幕原文是什么。三者一对就知道贴不贴。

**为什么不是「3 个候选点 1 个」。** WORKFLOW.md 原先这么写，但那个模型在事实上不成立：
片段 2.5–6 秒，段落 6–17 秒，一段配音要 2–3 个片段接起来才填得满，
所以 `clips` 里的片段**全部都会被用上**，不是候选。要审的是每一个将被用上的片段。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from . import paths
from .align import SEG_TOL, verify_alignment   # 叶子模块：approve 纯 JSON 校验，不背 clips 的 ML 依赖

THUMB_W = 400          # 够看清是谁在画面上，又不至于让一页几百张图卡住
THUMB_Q = "4"          # ffmpeg -q:v，2 最好 31 最差
EDGE = 0.4             # 首末帧往里缩一点，避开转场黑帧


def _ep_label(s: dict) -> str:
    """段头一行里集号 + 降级标记（ADR-0004）。

    降级链（集→季→全空间）要显示出来：集级没命中掉到季内/全空间，
    说明集号或查询写得不稳，是回去改稿的信号；没写集字段的段不显示。
    """
    if not s.get("episode"):
        return ""
    ep = f'　集 <b>{html.escape(s["episode"])}</b>'
    if s.get("ep_fell_back"):
        ep += ('<span class="flag">→季内检索</span>' if s.get("ep_scope") == 1
               else '<span class="flag">→全空间检索</span>')
    return ep


def _presence_txt(pr: float | None) -> str:
    """片段一行里的在场分（ADR-0004）。

    None = 该段没走角色通道（场景段），不显示；0.0 = 未检出，这段画面里
    未必有那个人，垫底是排片时故意放的；>0 = 显示两格小数，只跟同段内
    别的候选比，别拿它跟别的段横比（判据 10）。
    """
    if pr is None:
        return ""
    return ('<span class="flag">在场 0</span>' if pr == 0.0
            else f" · 在场 {pr:.2f}")


def _align_txt(seg: dict, audio_seg: dict | None, manifest_present: bool) -> str:
    """段头一行的「画面合计 / 配音」比对（B4 段级不变量的审图页落点）。

    manifest 缺失或该段配音记录对不上，都必须显式说明「未比对」——
    静默跳过等于让人以为这里天然比对过（判据 9：跳过不是通过）。
    """
    if not manifest_present:
        return '　<span class="flag">（缺 manifest，未比对）</span>'
    if audio_seg is None:
        return '　<span class="flag">（配音段缺失，未比对）</span>'
    got = sum(c["dur"] + c.get("extend", 0.0) for c in seg.get("clips") or [])
    need = audio_seg["duration"]
    txt = f"画面合计 {got:.1f}s / 配音 {need:.1f}s"
    if abs(got - need) > SEG_TOL:
        return f'　<span class="flag">{txt} 差 {got - need:+.1f}s</span>'
    return f"　{txt}"


def _thumb_path(dest_dir: Path, tag: str, start: float, dur: float, n: int) -> Path:
    """三帧其中一帧该落盘的路径。

    **文件名里编进 start/dur，不是纯序号。** 换排片时 (段号, clip 序号) 不变，
    只有 start/dur 变——不管是人工改 `04-clips.json`，还是重跑 `clips.py`
    换了检索结果。纯序号命名（`s04c1-0.jpg`）会让 `_frames()` 的
    `if not p.exists()` 把旧图当成「已经是最新的」直接复用，审图页看到的
    还是上一版画面（2026-08-10 踩过：手改 start 换镜头，重跑 review 后
    三帧原地不动，是上一条台词的画面，而且不报错）。带上 start/dur 后
    内容一变文件名跟着变，旧文件成为孤儿——不需要额外的失效逻辑，
    孤儿本来就是可重生成的中间缓存（`04-thumbs/`），清理约定见 CLAUDE.md，
    这里不自动删。
    """
    return dest_dir / f"{tag}-{start:.2f}-{dur:.2f}-{n}.jpg"


def _frames(clip: dict, dest_dir: Path, tag: str) -> list[Path]:
    """抽三帧：进点、中间、出点。

    三帧而不是一帧，是因为一个片段里镜头可能切换——只看中间帧会漏掉
    「前半段是对的人、后半段切走了」这种情况，而那正是要抓的错。
    """
    start, dur = clip["start"], clip["dur"]
    ts = [start + EDGE, start + dur / 2, start + max(EDGE, dur - EDGE)]
    out = []
    for n, t in enumerate(ts):
        p = _thumb_path(dest_dir, tag, start, dur, n)
        if not p.exists():
            subprocess.run(
                # -ss 放在 -i 前面走关键帧快速定位；审图不需要帧级精确
                ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{t:.3f}",
                 "-i", clip["source"], "-frames:v", "1",
                 "-vf", f"scale={THUMB_W}:-2", "-q:v", THUMB_Q, str(p)],
                check=False, capture_output=True,
            )
        if p.exists():
            out.append(p)
    return out


def _hhmmss(t: float) -> str:
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


def _clip_ep(c: dict) -> str:
    """片段的集号标签。人审手工补的片段可能只有 source/start/dur
    （2026-08-18 复盘③：cover.py 裸取 c['season'] 崩过一次），缺键时用
    文件名兑底——这一页是给人看的，标签降级总比整页 KeyError 强。
    """
    if isinstance(c.get("season"), int) and isinstance(c.get("episode"), int):
        return f"S{c['season']:02d}E{c['episode']:02d}"
    if c.get("sp") and isinstance(c.get("episode"), int):
        return f"SP{c['episode']:02d}"                # SP 特典：season=None（ADR-0010）
    return Path(str(c.get("source", ""))).name or "?"


def get_patch_pool(episode: Path) -> str | None:
    """获取补丁池名称（若存在）。"""
    pool_path = episode / "04-patch" / "pool.json"
    if pool_path.exists():
        try:
            pdata = json.loads(pool_path.read_text(encoding="utf-8"))
            return pdata.get("pool")
        except Exception:
            pass
    return None


def is_patch_segment(seg: dict, patch_pool: str | None = None) -> bool:
    """判定是否为补丁段（Spec §4.4 闸 3）。

    段有 via="patch-rescue" ∪ 任一 clip 的 anime == 补丁池名（或以 -patch 结尾）。
    """
    if seg.get("via") == "patch-rescue":
        return True
    for c in seg.get("clips", []):
        a = c.get("anime")
        if a:
            if patch_pool and a == patch_pool:
                return True
            if a.endswith("-patch"):
                return True
    return False


_STATUS_LABELS: dict[str, str] = {
    "no_match": "无匹配",
    "short": "时长不足",
    "error": "异常",
}

CSS = """
:root {
  color-scheme: dark light;
  --bg: #14161a; --surface: #1b1e24; --surface-2: #22262f; --surface-bad: #2a1c1c;
  --border: #262a31; --text: #e6e8eb; --text-2: #a8aeb6; --text-3: #8b929c;
  --danger: #ff8a80; --danger-bg: rgba(255,138,128,.14);
  --warn: #fbbf24; --warn-bg: rgba(251,191,36,.14);
  --info: #7dd3fc; --info-bg: rgba(125,211,252,.13);
  --accent: #60a5fa; --accent-bg: rgba(96,165,250,.18);
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #ffffff; --surface: #f5f6f8; --surface-2: #ebedf0; --surface-bad: #fff1f0;
    --border: #e3e6ea; --text: #1a1c20; --text-2: #4a515d; --text-3: #5f6672;
    --danger: #c62828; --danger-bg: rgba(198,40,40,.10);
    --warn: #9a5b00; --warn-bg: rgba(180,105,0,.11);
    --info: #0369a1; --info-bg: rgba(3,105,161,.10);
    --accent: #1d4ed8; --accent-bg: rgba(29,78,216,.11);
  }
}
html { scroll-behavior: smooth; }
body { margin:0; padding:24px; background:var(--bg); color:var(--text);
       font:15px/1.6 -apple-system,"PingFang SC",sans-serif; }
h1 { font-size:20px; margin:0 0 4px; letter-spacing:-.01em; }
.meta { color:var(--text-3); font-size:13px; margin-bottom:16px; }
.flt-r { position:absolute; opacity:0; pointer-events:none; }
.topbar { position:sticky; top:0; z-index:10; display:flex; flex-wrap:wrap; align-items:center;
          gap:10px 14px; padding:10px 14px; margin:0 -14px 18px;
          background:color-mix(in srgb, var(--bg) 88%, transparent);
          backdrop-filter:blur(12px); -webkit-backdrop-filter:blur(12px);
          border-bottom:1px solid var(--border); }
.seg-ctl { display:inline-flex; gap:2px; padding:2px; border-radius:8px;
           background:var(--surface); border:1px solid var(--border); }
.seg-ctl label { cursor:pointer; font-size:12.5px; padding:4px 10px; border-radius:6px;
                 color:var(--text-2); user-select:none;
                 transition:background 160ms ease, color 160ms ease; }
.seg-ctl label:hover { color:var(--text); }
#flt-all:checked ~ .topbar label[for="flt-all"],
#flt-attn:checked ~ .topbar label[for="flt-attn"] {
  background:var(--accent-bg); color:var(--accent); font-weight:600;
}
#flt-attn:checked ~ .seg:not(.attn) { display:none; }
.jump-bar { display:flex; flex-wrap:wrap; align-items:center; gap:6px; font-size:12px; color:var(--text-3); }
.jump-chip { display:inline-block; padding:2px 8px; border-radius:999px; text-decoration:none;
             font-variant-numeric:tabular-nums; font-weight:500;
             background:var(--warn-bg); color:var(--warn);
             transition:transform 140ms cubic-bezier(.22,1,.36,1), opacity 140ms ease; }
.jump-chip.is-bad { background:var(--danger-bg); color:var(--danger); }
.jump-chip:hover { opacity:.85; transform:translateY(-1px); }
.all-ok { font-size:12.5px; color:var(--text-3); }
.seg { border-top:1px solid var(--border); padding:18px 0; scroll-margin-top:68px;
       content-visibility:auto; contain-intrinsic-size:auto 240px; }
.seg.bad { background:var(--surface-bad); border-radius:10px; padding:18px 14px; margin:8px 0; border-top-color:transparent; }
.seg:target { animation:seg-hit 1.1s cubic-bezier(.22,1,.36,1); }
@keyframes seg-hit {
  0% { box-shadow:0 0 0 2px var(--accent); background:var(--accent-bg); border-radius:10px; }
  100% { box-shadow:0 0 0 0 transparent; }
}
.seg-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.say { font-size:16px; margin:0 0 4px; flex:1 1 360px; }
.no { display:inline-block; min-width:56px; color:var(--text-3); font-variant-numeric:tabular-nums; font-weight:500; }
.aud { height:30px; max-width:260px; flex:0 0 auto; border-radius:6px; }
.q { color:var(--text-3); font-size:13px; margin-bottom:12px; display:flex; flex-wrap:wrap; align-items:center; gap:4px 8px; }
.q b { color:var(--text); font-weight:500; }
.clip { display:flex; gap:14px; align-items:flex-start; margin:12px 0 0 56px; }
.shots { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; flex:1 1 0; max-width:618px; min-width:0; }
.shots img { width:100%; height:auto; aspect-ratio:16/9; object-fit:cover; border-radius:6px;
             background:var(--surface); display:block; }
.shot-empty { grid-column:1 / -1; padding:18px 14px; border-radius:6px; font-size:12.5px;
              background:var(--surface); border:1px dashed var(--border); color:var(--text-3); text-align:center; }
.side { font-size:13px; color:var(--text-2); flex:0 1 280px; min-width:180px; }
.side .line { color:var(--text); margin-bottom:4px; }
.side .n { color:var(--text-3); font-variant-numeric:tabular-nums; }
.flag { color:var(--danger); font-weight:600; }
.badge { display:inline-block; padding:1px 7px; border-radius:999px; font-size:12px; font-weight:600; line-height:1.5; }
.badge--danger { background:var(--danger-bg); color:var(--danger); }
.badge--warn { background:var(--warn-bg); color:var(--warn); }
.badge--info { background:var(--info-bg); color:var(--info); }
@media (max-width:1100px) {
  .clip { flex-direction:column; margin-left:0; }
  .shots { max-width:none; width:100%; }
  .side { flex-basis:auto; width:100%; }
}
@media (max-width:600px) {
  body { padding:14px; }
  .shots { gap:4px; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior:auto; }
  .seg:target { animation:none; outline:2px solid var(--accent); outline-offset:2px; border-radius:10px; }
  .seg-ctl label, .jump-chip { transition:none; }
}
"""


def _needs_attention(
    s: dict,
    audio_seg: dict | None,
    manifest_present: bool,
    patch_pool: str | None,
) -> tuple[bool, bool]:
    """返回 (是否需注意, 是否阻断级)。供顶部筛选与跳转条使用。"""
    is_bad = s["status"] not in ("ok", "ok_extended")
    if is_bad:
        return True, True
    align_bad = False
    if not manifest_present or audio_seg is None:
        align_bad = True
    else:
        got = sum(c["dur"] + c.get("extend", 0.0) for c in s.get("clips") or [])
        if abs(got - audio_seg["duration"]) > SEG_TOL:
            align_bad = True
    if align_bad:
        return True, True
    if (
        s["status"] == "ok_extended"
        or is_patch_segment(s, patch_pool)
        or s.get("ep_fell_back")
        or s.get("filter_fell_back")
        or any(c.get("presence") == 0.0 for c in s.get("clips") or [])
    ):
        return True, False
    return False, False


def build(episode: Path) -> Path:
    src = episode / "04-clips.json"
    if not src.exists():
        raise SystemExit(f"FAIL 缺 {src}，先跑 `python -m pipeline.clips {episode}`")
    data = json.loads(src.read_text(encoding="utf-8"))
    segs = data["segments"]

    manifest_path = episode / "03-audio" / "manifest.json"
    manifest_present = manifest_path.exists()
    audio_by_index = {}
    if manifest_present:
        audio = json.loads(manifest_path.read_text(encoding="utf-8"))["segments"]
        audio_by_index = {a["index"]: a for a in audio}

    shots_dir = episode / "04-thumbs"
    shots_dir.mkdir(exist_ok=True)

    jobs = [(s, c, n) for s in segs for n, c in enumerate(s["clips"])]
    with ThreadPoolExecutor(max_workers=6) as pool:
        frames = list(pool.map(
            lambda j: _frames(j[1], shots_dir, f"s{j[0]['index']:02d}c{j[2]}"), jobs))
    fmap = {(j[0]["index"], j[2]): f for j, f in zip(jobs, frames)}

    patch_pool = get_patch_pool(episode)
    attn_map = {
        s["index"]: _needs_attention(s, audio_by_index.get(s["index"]), manifest_present, patch_pool)
        for s in segs
    }
    attn_segs = [s for s in segs if attn_map[s["index"]][0]]
    bad = [s for s in segs if s["status"] not in ("ok", "ok_extended")]
    gen_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    body = [
        f"<h1>{html.escape(episode.name)}　审片页</h1>",
        f'<div class="meta">{len(segs)} 段 · '
        f'{sum(len(s["clips"]) for s in segs)} 片段 · '
        f'{data["total_duration"]:.1f}s · 生成于 {gen_at}'
        + (f' · <span class="badge badge--danger">{len(bad)} 段阻断</span>' if bad else "")
        + (f' · <span class="badge badge--warn">{len(attn_segs)} 段需注意</span>' if attn_segs and len(attn_segs) != len(bad) else "")
        + "<br>看画面对不对得上上方这句口播。不对就报段号，别的不用管。</div>",
    ]

    if attn_segs:
        chips = "".join(
            f'<a class="jump-chip{" is-bad" if attn_map[s["index"]][1] else ""}" '
            f'href="#seg-{s["index"]}">段 {s["index"]}</a>'
            for s in attn_segs
        )
        body.append(
            '<input type="radio" name="flt" id="flt-all" class="flt-r" checked>'
            '<input type="radio" name="flt" id="flt-attn" class="flt-r">'
            '<div class="topbar">'
            f'<div class="seg-ctl" role="group" aria-label="段落筛选">'
            f'<label for="flt-all">全部 ({len(segs)})</label>'
            f'<label for="flt-attn">仅需注意 ({len(attn_segs)})</label>'
            f'</div>'
            f'<div class="jump-bar"><span>快速跳转</span>{chips}</div>'
            '</div>'
        )
    else:
        body.append(
            f'<div class="topbar"><span class="all-ok">全部 {len(segs)} 段无机器告警，请按顺序抽检画面与口播。</span></div>'
        )

    for s in segs:
        # ok_extended（锚点段尾帧定格补足）不是阻断项，但要显眼地标出来：
        # 定格的几秒人必须亲眼看一眼
        is_attn, _is_blocking = attn_map[s["index"]]
        cls_parts = ["seg"]
        if s["status"] not in ("ok", "ok_extended"):
            cls_parts.append("bad")
        if is_attn:
            cls_parts.append("attn")
        body.append(f'<div id="seg-{s["index"]}" class="{" ".join(cls_parts)}">')
        audio_seg = audio_by_index.get(s["index"])
        audio_html = ""
        if audio_seg and audio_seg.get("file"):
            audio_html = (
                f'<audio class="aud" controls preload="none" '
                f'src="03-audio/{html.escape(str(audio_seg["file"]))}"></audio>'
            )
        body.append(
            f'<div class="seg-head">'
            f'<div class="say"><span class="no">段 {s["index"]}</span>'
            f'{html.escape(s["text"])}</div>{audio_html}</div>'
        )
        if s["status"] == "ok_extended":
            ext = sum(c.get("extend", 0.0) for c in s["clips"])
            note = f'<span class="badge badge--warn">尾帧定格 +{ext:.1f}s</span>'
        elif s["status"] == "ok":
            note = ""
        else:
            st_raw = str(s["status"])
            st_lbl = _STATUS_LABELS.get(st_raw, st_raw)
            note = f'<span class="badge badge--danger">{html.escape(st_lbl)}</span>'

        patch_tag = ""
        if is_patch_segment(s, patch_pool):
            patch_tag = '<span class="badge badge--warn">补丁·必审</span>'

        # **通道要标出来。** 画面通道的分数和台词通道的不是一个量，
        # 人扫这一页时若不知道某段走的是哪条，会拿一列数横着比。
        # 角色过滤退回也要标：它说明那一段的过滤没起作用，画面里未必有那个人。
        chan = ""
        if s.get("channel") == "anchor":
            # 多锚点蒙太奇（2026-09-07）把整列锚点都亮出来
            raws = ([a["raw"] for a in s["anchors"] if a] if s.get("anchors")
                    else [(s.get("anchor") or {}).get("raw", "")])
            chan = f'<span class="badge badge--info">锚点 {html.escape(" / ".join(raws))}</span>'
        elif s.get("channel") == "scene":
            chan = '<span class="badge badge--info">画面通道</span>'
        elif s.get("person"):
            chan = f'<span>人物 <b>{html.escape(s["person"])}</b></span>'
            if s.get("filter_fell_back"):
                chan += '<span class="badge badge--warn">过滤为空→退回，画面里未必有他</span>'
        # 集号与降级链要显示出来：集级没命中掉到季内/全空间 = 集号或查询
        # 写得不稳，是回去改稿的信号（ADR-0004）；没写集字段的段不显示。
        align_txt = _align_txt(s, audio_seg, manifest_present)
        body.append(
            f'<div class="q"><span class="no"></span>'
            f'<span>查询 <b>{html.escape(s.get("used_query") or "—")}</b></span>'
            f'<span>{s["duration"]:.1f}s</span>{note}{patch_tag}{chan}{_ep_label(s)}{align_txt}</div>'
        )
        if not s["clips"]:
            # render 对非 ok 段直接 SystemExit 拒渲（没有降级方案，S6：文案必须
            # 反映实际行为）——看到这条就该去补锚点或改稿，不是放过
            body.append('<div class="clip"><div class="side flag">'
                        '无匹配，渲染会拒绝启动（先补锚点或改稿）</div></div>')
        for n, c in enumerate(s["clips"]):
            frame_list = fmap.get((s["index"], n), [])
            if frame_list:
                imgs = "".join(
                    f'<img src="04-thumbs/{html.escape(p.name)}" width="{THUMB_W}" height="225" '
                    f'decoding="async" loading="lazy" alt="段 {s["index"]} 片段 {n + 1} 帧 {fi + 1}">'
                    for fi, p in enumerate(frame_list)
                )
            else:
                imgs = '<div class="shot-empty">未能抽出缩略图（请核对片源路径与时间码）</div>'
            # 在场分显示出来（ADR-0004）：0.000 = 未检出，这段画面里未必有那个人，
            # 垫底是排片时故意放的；它只在同段落内跟别的候选比过，别拿它跟别的段横比。
            # score 为 None = 人手工指定/覆盖的片段（05 人审改画面），标「手工」，不做分数横比。
            score_txt = f"{c['score']:.3f}" if c.get("score") is not None else "手工"
            body.append(
                f'<div class="clip"><div class="shots">{imgs}</div>'
                f'<div class="side"><div class="line">'
                f'{html.escape(c.get("line") or "（无台词）")}</div>'
                f'<div class="n">{html.escape(_clip_ep(c))} '
                f'{_hhmmss(c["start"])} · {c["dur"]:.1f}s · {score_txt}'
                f'{_presence_txt(c.get("presence"))}</div>'
                f'</div></div>')
        body.append("</div>")

    dest = episode / "04-review.html"
    dest.write_text(
        f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(episode.name)} 审片页</title>"
        f"<style>{CSS}</style></head><body>" + "".join(body) + "</body></html>",
        encoding="utf-8")
    return dest


def _read_all(f: BinaryIO) -> bytes:
    """从已打开的二进制文件描述符读取全部字节（Spec 3 §4.5 S3-R9a 测试缝）。"""
    return f.read()


def approve(
    episode: Path,
    confirm_patch: bool = False,
    expect: tuple[int, int] | None = None,
) -> Path:
    """人看过了，存成 approved 版。渲染只吃这个文件。

    **必须是显式动作。** 让 clips.py 自动写 approved 是最省事的做法，
    也正是这一关形同虚设的原因——本期就是这么跳过去的。

    **也是段级不变量的第一道闸（B4）。** 人审在这一步之前可能直接改过
    `04-clips.json` 的 start/dur，机器生成时保证的「Σclip.dur == 配音时长」
    不会自动重新成立——这里补验，不通过就不许拷成 approved 版。
    """
    src, dest = episode / "04-clips.json", episode / "04-clips.approved.json"
    if not src.exists():
        raise SystemExit(f"FAIL 缺 {src}")
    manifest_path = episode / "03-audio" / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"FAIL 缺 {manifest_path}，approve 前必须能校验段级时长不变量")
    # D74④：没有审看页就批准 = 排片没过人眼（2026-07-29 检索直出那次的复发）。三次复现都是 agent
    # 只跑 clips、跳过 review。只拦「不存在」；页面比排片旧只警告——人在 05 原地改完排片直接批是常态，
    # 为此重抽帧不值得。闸放在批准动作上、不进 approvals._gate_valid：已批过的老期不翻案
    page = episode / "04-review.html"
    if not page.exists():
        raise SystemExit(f"FAIL 还没有审看页 {page.name}，不许 approve：先跑 python -m pipeline.review {episode} "
                         "出页、人看过再批")
    if page.stat().st_mtime_ns < src.stat().st_mtime_ns:
        print(f"WARN {page.name} 比 04-clips.json 旧：排片在出页之后改过，页上看到的不是最新画面", file=sys.stderr)

    raw_bytes: bytes | None = None
    st1: os.stat_result | None = None
    if expect is None:
        data = json.loads(src.read_text(encoding="utf-8"))
    else:
        if len(expect) != 2 or expect[0] is None or expect[1] is None:
            raise SystemExit(2)
        exp_size, exp_mtime_ns = int(expect[0]), int(expect[1])
        with open(src, "rb") as f:
            st1 = os.fstat(f.fileno())
            if (st1.st_size, st1.st_mtime_ns) != (exp_size, exp_mtime_ns):
                raise SystemExit(
                    f"FAIL 04-clips.json 已不是审阅时的版本"
                    f"（期望 size={exp_size}, mtime_ns={exp_mtime_ns}；"
                    f"实际 size={st1.st_size}, mtime_ns={st1.st_mtime_ns}），未写批准文件"
                )
            raw_bytes = _read_all(f)
            st2 = os.fstat(f.fileno())
            if (
                (st2.st_size, st2.st_mtime_ns, st2.st_ctime_ns)
                != (st1.st_size, st1.st_mtime_ns, st1.st_ctime_ns)
                or len(raw_bytes) != st1.st_size
            ):
                raise SystemExit("FAIL 04-clips.json 在读取期间被改写，未写批准文件")
        data = json.loads(raw_bytes.decode("utf-8"))

    audio = json.loads(manifest_path.read_text(encoding="utf-8"))["segments"]
    violations = verify_alignment(data["segments"], audio)
    if violations:
        raise SystemExit(
            "FAIL 段级时长不对齐，不许 approve：\n  " + "\n  ".join(violations) +
            f"\n     人审改过画面（start/source）？先跑 "
            f"python -m pipeline.clips {episode} --refit 重排版再 approve")

    # 补丁段二次确认闸（Spec §4.4 闸 3 & §5）：
    # 仅当存在补丁段（via="patch-rescue" ∪ 任一 clip 的 anime == 补丁池名）才触发
    patch_segs = patch_segment_indices(episode, data)
    if patch_segs and not confirm_patch:
        seg_nums = ", ".join(str(i) for i in patch_segs)
        if not _interactive():
            # 没有终端可作答（桌面端按钮、agent 作业）：此前照样调 input()，读到 EOF 裸抛 EOFError
            #（2026-10-13《束胸力学》桌面端批准 05 时崩）。说清楚这一关怎么过，不替人确认。
            raise SystemExit(
                f"FAIL 本期有 {len(patch_segs)} 个补丁段（段号：{seg_nums}），批准前需人逐段核对后确认。\n"
                f"     桌面端点「批准」时会弹确认框；终端里核对完加 --confirm-patch 再批准")
        prompt = (
            f"[*] 本期包含 {len(patch_segs)} 个补丁段（段号：{seg_nums}），"
            f"确认已人工核对完毕并批准？[y/N]: "
        )
        print(prompt, end="", flush=True)
        try:
            ans = input("")
        except (EOFError, KeyboardInterrupt):
            raise SystemExit("\nFAIL 已取消 approve：存在未人工核对的补丁段")
        if ans.strip().lower() not in ("y", "yes"):
            raise SystemExit("FAIL 已取消 approve：存在未人工核对的补丁段")

    if expect is None:
        shutil.copy2(src, dest)
    else:
        assert raw_bytes is not None and st1 is not None
        tmp = dest.with_name(f".{dest.name}.tmp.{os.getpid()}")
        try:
            tmp.write_bytes(raw_bytes)
            os.utime(tmp, ns=(st1.st_atime_ns, st1.st_mtime_ns))
            os.replace(tmp, dest)
        except Exception:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise
    return dest


def _interactive() -> bool:
    """有没有人在终端前可以回答 y/N（测试缝）。"""
    return sys.stdin.isatty()


def patch_segment_indices(episode: Path, data: dict | None = None) -> list[int]:
    """本期排片里的补丁段段号（Spec §4.4 闸 3 的判据）。`data` 不给就读 04-clips.json（纯读）。"""
    if data is None:
        src = episode / "04-clips.json"
        if not src.exists():
            raise SystemExit(f"FAIL 缺 {src}")
        data = json.loads(src.read_text(encoding="utf-8"))
    pool = get_patch_pool(episode)
    return [int(s["index"]) for s in data["segments"] if is_patch_segment(s, pool)]


def main() -> int:
    ap = argparse.ArgumentParser(description="第 05 步：出抽检页 / 批准排片")
    ap.add_argument("episode", type=Path)
    ap.add_argument("--approve", action="store_true", help="批准当前排片，渲染才能开始")
    ap.add_argument("--confirm-patch", action="store_true", help="显式确认并跳过补丁段人工二次确认提示")
    ap.add_argument("--expect-size", type=int, default=None, help="期望的 04-clips.json 字节数（须与 --expect-mtime-ns 同时给）")
    ap.add_argument("--expect-mtime-ns", type=int, default=None, help="期望的 04-clips.json mtime_ns（须与 --expect-size 同时给）")
    ap.add_argument("--patch-segments", action="store_true",
                    help="只读：打印本期补丁段段号 JSON（桌面端批准前据此弹确认框），不写任何文件")
    a = ap.parse_args()
    if (a.expect_size is None) != (a.expect_mtime_ns is None):
        ap.error("--expect-size 与 --expect-mtime-ns 必须同时提供或同时省略")
    paths.require_data()

    if a.patch_segments:
        if a.approve:
            ap.error("--patch-segments 是只读查询，不能与 --approve 同用")
        print(json.dumps({"patch_segments": patch_segment_indices(a.episode)}))
        return 0

    if a.approve:
        expect_pair = (
            (a.expect_size, a.expect_mtime_ns)
            if a.expect_size is not None and a.expect_mtime_ns is not None
            else None
        )
        print(f"已批准 → {approve(a.episode, confirm_patch=a.confirm_patch, expect=expect_pair)}")
        return 0

    dest = build(a.episode)
    n = dest.read_text(encoding="utf-8").count('src="04-thumbs/')
    print(f"{n} 张缩略图 → {dest}\n"
          f"  open '{dest}'\n"
          f"  看完没问题：python -m pipeline.review {a.episode} --approve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
