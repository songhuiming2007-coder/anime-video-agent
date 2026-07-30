"""审时间码（流程第 05 步）：把排片结果变成一页能扫的图。

    python -m pipeline.review data/episodes/<本期>            # 出 04-review.html
    python -m pipeline.review data/episodes/<本期> --approve  # 存成 approved 版

**这是整条流水线唯一的人工关卡**（CLAUDE.md「人类只在 05 和 09 出现」），
而它此前从没建过——2026-07-29 检查发现本期的 `04-clips.approved.json` 与
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
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import paths

THUMB_W = 400          # 够看清是谁在画面上，又不至于让一页几百张图卡住
THUMB_Q = "4"          # ffmpeg -q:v，2 最好 31 最差
EDGE = 0.4             # 首末帧往里缩一点，避开转场黑帧


def _frames(clip: dict, dest_dir: Path, tag: str) -> list[Path]:
    """抽三帧：进点、中间、出点。

    三帧而不是一帧，是因为一个片段里镜头可能切换——只看中间帧会漏掉
    「前半段是对的人、后半段切走了」这种情况，而那正是要抓的错。
    """
    start, dur = clip["start"], clip["dur"]
    ts = [start + EDGE, start + dur / 2, start + max(EDGE, dur - EDGE)]
    out = []
    for n, t in enumerate(ts):
        p = dest_dir / f"{tag}-{n}.jpg"
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


CSS = """
:root { color-scheme: dark light; }
body { margin:0; padding:24px; background:#14161a; color:#e6e8eb;
       font:15px/1.6 -apple-system,"PingFang SC",sans-serif; }
h1 { font-size:20px; margin:0 0 4px; }
.meta { color:#8b929c; font-size:13px; margin-bottom:24px; }
.seg { border-top:1px solid #262a31; padding:18px 0; }
.seg.bad { background:#2a1c1c; border-radius:8px; padding:18px 14px; margin:8px 0; }
.no { display:inline-block; min-width:56px; color:#8b929c; font-variant-numeric:tabular-nums; }
.say { font-size:16px; margin:0 0 4px; }
.q { color:#8b929c; font-size:13px; margin-bottom:12px; }
.q b { color:#c8ccd2; font-weight:500; }
.clip { display:flex; gap:14px; align-items:flex-start; margin:12px 0 0 56px; }
.shots { display:flex; gap:4px; flex:0 0 auto; }
.shots img { width:200px; border-radius:4px; display:block; }
.side { font-size:13px; color:#a8aeb6; min-width:180px; }
.side .line { color:#e6e8eb; margin-bottom:4px; }
.side .n { color:#6f7681; font-variant-numeric:tabular-nums; }
.flag { color:#ff8a80; font-weight:600; }
@media (max-width:1100px){ .clip{flex-direction:column;margin-left:0} .shots img{width:31vw} }
@media (prefers-color-scheme: light){
  body{background:#fff;color:#1a1c20} .meta,.q,.side{color:#5f6672}
  .seg{border-color:#e3e6ea} .seg.bad{background:#fff1f0} .side .line{color:#1a1c20}
}
"""


def build(episode: Path) -> Path:
    src = episode / "04-clips.json"
    if not src.exists():
        raise SystemExit(f"FAIL 缺 {src}，先跑 `python -m pipeline.clips {episode}`")
    data = json.loads(src.read_text(encoding="utf-8"))
    segs = data["segments"]

    shots_dir = episode / "04-thumbs"
    shots_dir.mkdir(exist_ok=True)

    jobs = [(s, c, n) for s in segs for n, c in enumerate(s["clips"])]
    with ThreadPoolExecutor(max_workers=6) as pool:
        frames = list(pool.map(
            lambda j: _frames(j[1], shots_dir, f"s{j[0]['index']:02d}c{j[2]}"), jobs))
    fmap = {(j[0]["index"], j[2]): f for j, f in zip(jobs, frames)}

    bad = [s for s in segs if s["status"] != "ok"]
    body = [
        f"<h1>{html.escape(episode.name)}　排片抽检</h1>",
        f'<div class="meta">{len(segs)} 段 · '
        f'{sum(len(s["clips"]) for s in segs)} 片段 · '
        f'{data["total_duration"]:.1f}s'
        + (f' · <span class="flag">{len(bad)} 段需注意</span>' if bad else "")
        + "<br>看画面对不对得上左边这句口播。不对就报段号，别的不用管。</div>",
    ]

    for s in segs:
        cls = "seg bad" if s["status"] != "ok" else "seg"
        body.append(f'<div class="{cls}">')
        body.append(f'<div class="say"><span class="no">段 {s["index"]}</span>'
                    f'{html.escape(s["text"])}</div>')
        note = "" if s["status"] == "ok" else f'　<span class="flag">{s["status"]}</span>'
        body.append(f'<div class="q"><span class="no"></span>查询 <b>'
                    f'{html.escape(s.get("used_query") or "—")}</b>'
                    f'　{s["duration"]:.1f}s{note}</div>')
        if not s["clips"]:
            body.append('<div class="clip"><div class="side flag">'
                        '无匹配，渲染会退到降级方案</div></div>')
        for n, c in enumerate(s["clips"]):
            imgs = "".join(
                f'<img src="04-thumbs/{p.name}" loading="lazy">'
                for p in fmap.get((s["index"], n), []))
            body.append(
                f'<div class="clip"><div class="shots">{imgs}</div>'
                f'<div class="side"><div class="line">'
                f'{html.escape(c.get("line") or "（无台词）")}</div>'
                f'<div class="n">S{c["season"]:02d}E{c["episode"]:02d} '
                f'{_hhmmss(c["start"])} · {c["dur"]:.1f}s · {c["score"]:.3f}</div>'
                f'</div></div>')
        body.append("</div>")

    dest = episode / "04-review.html"
    dest.write_text(
        f"<!doctype html><meta charset=utf-8>"
        f"<title>{html.escape(episode.name)} 抽检</title>"
        f"<style>{CSS}</style>" + "".join(body),
        encoding="utf-8")
    return dest


def approve(episode: Path) -> Path:
    """人看过了，存成 approved 版。渲染只吃这个文件。

    **必须是显式动作。** 让 clips.py 自动写 approved 是最省事的做法，
    也正是这一关形同虚设的原因——本期就是这么跳过去的。
    """
    src, dest = episode / "04-clips.json", episode / "04-clips.approved.json"
    if not src.exists():
        raise SystemExit(f"FAIL 缺 {src}")
    shutil.copy2(src, dest)
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="第 05 步：出抽检页 / 批准排片")
    ap.add_argument("episode", type=Path)
    ap.add_argument("--approve", action="store_true", help="批准当前排片，渲染才能开始")
    a = ap.parse_args()
    paths.require_data()

    if a.approve:
        print(f"已批准 → {approve(a.episode)}")
        return 0

    dest = build(a.episode)
    n = len(list((a.episode / "04-thumbs").glob("*.jpg")))
    print(f"{n} 张缩略图 → {dest}\n"
          f"  open '{dest}'\n"
          f"  看完没问题：python -m pipeline.review {a.episode} --approve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
