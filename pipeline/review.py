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
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
    got = sum(c["dur"] for c in seg.get("clips") or [])
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
    return Path(str(c.get("source", ""))).name or "?"


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
        # **通道要标出来。** 画面通道的分数和台词通道的不是一个量，
        # 人扫这一页时若不知道某段走的是哪条，会拿一列数横着比。
        # 角色过滤退回也要标：它说明那一段的过滤没起作用，画面里未必有那个人。
        chan = ""
        if s.get("channel") == "scene":
            chan = '　<span class="flag">画面通道</span>'
        elif s.get("person"):
            chan = f'　人物 <b>{html.escape(s["person"])}</b>'
            if s.get("filter_fell_back"):
                chan += '<span class="flag">过滤为空→退回，画面里未必有他</span>'
        # 集号与降级链要显示出来：集级没命中掉到季内/全空间 = 集号或查询
        # 写得不稳，是回去改稿的信号（ADR-0004）；没写集字段的段不显示。
        align_txt = _align_txt(s, audio_by_index.get(s["index"]), manifest_present)
        body.append(f'<div class="q"><span class="no"></span>查询 <b>'
                    f'{html.escape(s.get("used_query") or "—")}</b>'
                    f'　{s["duration"]:.1f}s{note}{chan}{_ep_label(s)}{align_txt}</div>')
        if not s["clips"]:
            body.append('<div class="clip"><div class="side flag">'
                        '无匹配，渲染会退到降级方案</div></div>')
        for n, c in enumerate(s["clips"]):
            imgs = "".join(
                f'<img src="04-thumbs/{p.name}" loading="lazy">'
                for p in fmap.get((s["index"], n), []))
            # 在场分显示出来（ADR-0004）：0.000 = 未检出，这段画面里未必有那个人，
            # 垫底是排片时故意放的；它只在同段落内跟别的候选比过，别拿它跟别的段横比。
            # score 为 None = 人手工指定/覆盖的片段（05 人审改画面），标「手工」，不做分数横比。
            score_txt = f"{c['score']:.3f}" if c.get("score") is not None else "手工"
            body.append(
                f'<div class="clip"><div class="shots">{imgs}</div>'
                f'<div class="side"><div class="line">'
                f'{html.escape(c.get("line") or "（无台词）")}</div>'
                f'<div class="n">{_clip_ep(c)} '
                f'{_hhmmss(c["start"])} · {c["dur"]:.1f}s · {score_txt}'
                f'{_presence_txt(c.get("presence"))}</div>'
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
    data = json.loads(src.read_text(encoding="utf-8"))
    audio = json.loads(manifest_path.read_text(encoding="utf-8"))["segments"]
    violations = verify_alignment(data["segments"], audio)
    if violations:
        raise SystemExit(
            "FAIL 段级时长不对齐，不许 approve：\n  " + "\n  ".join(violations) +
            f"\n     人审改过画面（start/source）？先跑 "
            f"python -m pipeline.clips {episode} --refit 重排版再 approve")
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
