"""视觉索引的三个探针（ADR-0003 的准入闸门）。

    python -m pipeline.vprobe tagger 春物 S01E01        # 通道 1 能不能认出这部番的角色
    python -m pipeline.vprobe presence 春物 雪乃         # 某个角色认得准不准（相当于验簇纯度）
    python -m pipeline.vprobe scene 罪恶王冠 S01E01 [--write]   # 通道 2：命中率 + 门槛标定
    python -m pipeline.vprobe captions 罪恶王冠 S01E01 -n 30    # 描述说的是不是那个画面

**探针的存在理由是省一整块死代码。** ADR-0003：「探针的成本是半天，
建完发现不好用的成本是一整块死代码」。第 2 层写得很死——命中率过不去就不建这一层，
氛围段落继续留给人在第 05 步处理。

**这几个探针复用 `vindex` 的同一套函数，不是一次性脚本。** 探针跑通即该通道跑通，
不返工；探针跑不通就是这条通道的判决书，不是探针的 bug。

产出只给数和图，**不给结论**——「这张图里到底是不是她」只有人能判。
"""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path

from . import paths, sheet, shots, vindex

PROBE_DIR = paths.DATA / "library" / "vindex" / "probe"

# 第 2 层探针的查询表在 `config/scenes.json`，**按番分组**，读法见 `vindex.scene_queries`。
#
# **不在这里写死**：编码、检索、算噪声地板这套机制对任何番都一样，属于代码；
# 「空无一人的教室」「太空中的宇宙飞船」是春物的内容，属于配置。
# 写死在这里就是 anchor_words 那个错误的重演（该机制 2026-08-12 已整体删除，
# 记录见 ADR-0005 与 WORKFLOW「锚点看画面集号」节）。


def _rel(target: Path, base: Path) -> str:
    return os.path.relpath(target, base).replace(os.sep, "/")


# ---------------------------------------------------------------- 通道 1


def tagger_probe(anime: str, key: str, n: int = 24,
                 out_dir: Path = PROBE_DIR) -> tuple[Path, dict]:
    """在一集里均匀取 n 个镜头，报每个镜头认出了谁。

    **均匀取，不挑好的。** 挑「一看就是正脸」的帧去证明模型行，等于自己给自己发合格证。
    """
    d = shots.load(anime, key)
    sh = d["shots"]
    step = max(1, len(sh) // n)
    picked = sh[::step][:n]

    files = [shots.frame_path(anime, key, s["i"]) for s in picked]
    recs = vindex.tag(files)
    names = vindex.display_names(anime)
    known = set(names)

    cells, rows = [], []
    for s, f, r in zip(picked, files, recs):
        hit = sorted(((t, sc) for t, sc in r["char"].items()), key=lambda kv: -kv[1])
        # 词表里有的（能用来过滤的）和词表里没有的分开列：
        # 后者说明模型认出了人，但这个番的名表没登记，是补名表的信号，不是模型的错。
        mine = [(names[t], sc) for t, sc in hit if t in known]
        other = [(t, sc) for t, sc in hit if t not in known]
        label = "、".join(f"{n_}{sc:.2f}" for n_, sc in mine) or "—"
        if other:
            label += "  外:" + "、".join(t for t, _ in other[:2])
        cells.append((f, f"{int(s['start']) // 60:02d}:{int(s['start']) % 60:02d} {label}"))
        rows.append({"i": s["i"], "start": s["start"],
                     "chars": {names.get(t, t): sc for t, sc in hit},
                     "gen": sorted(r["gen"], key=lambda t: -r["gen"][t])[:8]})

    out_dir.mkdir(parents=True, exist_ok=True)
    prof = vindex.profile()
    img = sheet.build(cells, out_dir / f"tagger-{anime}_{key}.jpg", cols=6, cell_w=360)

    thr = prof["char_threshold"]
    n_hit = sum(1 for r in rows if any(v >= thr for v in r["chars"].values()))
    summary = {
        "tagger": prof["name"], "model": prof["repo"], "threshold": thr,
        "镜头数": len(rows),
        "认出至少一个已登记角色": n_hit,
        "逐角色计数": _count(rows, thr),
        "sheet": str(img),
    }
    (out_dir / f"tagger-{anime}_{key}.json").write_text(
        json.dumps({"summary": summary, "shots": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return img, summary


def _count(rows: list[dict], thr: float) -> dict[str, int]:
    c: dict[str, int] = {}
    for r in rows:
        for name, sc in r["chars"].items():
            if sc >= thr:
                c[name] = c.get(name, 0) + 1
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


def presence_probe(anime: str, name: str, n: int = 20,
                   out_dir: Path = PROBE_DIR) -> tuple[Path, dict]:
    """抽 n 个「认出了这个角色」的镜头，人一张张看。

    **这一步等价于 ADR-0003 的「验簇纯度」**：每簇抽 20 张人看，错一张就拆簇。
    这里没有簇，判据换成：错一张就说明这个角色在这部番上的精确率不够，
    要么提高它的判定阈值，要么把它从名表里摘掉。

    **验的是精确率，不是召回率。** 「认出的里面有没有认错的」是这张表能答的；
    「有多少张漏认了」它答不了，也不需要答——整个设计就建在
    「检测到 X 可信、没检测到不代表不在场」上。
    """
    pres = vindex.load_presence(anime)
    tag = pres.tag_of(name)
    hits = [(key, s) for key, rows in sorted(pres.by_ep.items()) for s in rows
            if tag in s["tags"]]
    if not hits:
        raise SystemExit(
            f"FAIL 索引里没有任何镜头认出「{name}」（阈值 {pres.threshold}）。\n"
            f"     要么这个角色的判定阈值太高，要么模型在这部番上认不出他/她")
    step = max(1, len(hits) // n)
    picked = hits[::step][:n]

    cells = [(shots.frame_path(anime, key, s["i"]),
              f"{key} {int(s['start']) // 60:02d}:{int(s['start']) % 60:02d}")
             for key, s in picked]
    out_dir.mkdir(parents=True, exist_ok=True)
    img = sheet.build(cells, out_dir / f"presence-{anime}-{name}.jpg", cols=5, cell_w=380)
    return img, {"角色": name, "标签": tag, "命中镜头": len(hits),
                 "抽检": len(picked), "阈值": pres.threshold}


# ---------------------------------------------------------------- 通道 2


CSS = """
:root { color-scheme: dark light; }
body { margin:0; padding:24px; background:#14161a; color:#e6e8eb;
       font:15px/1.6 -apple-system,"PingFang SC",sans-serif; }
h1 { font-size:20px; margin:0 0 4px; }
h2 { font-size:16px; margin:28px 0 8px; }
.meta { color:#8b929c; font-size:13px; margin-bottom:20px; }
.row { display:grid; grid-template-columns:repeat(5,1fr); gap:10px; }
figure { margin:0; }
figure img { width:100%; border-radius:4px; display:block; }
figcaption { font-size:12px; color:#8b929c; margin-top:4px;
             font-variant-numeric:tabular-nums; }
table.neg { border-collapse:collapse; font-size:14px; }
table.neg td { padding:3px 18px 3px 0; font-variant-numeric:tabular-nums; }
@media (prefers-color-scheme: light){
  body{background:#fff;color:#1a1c20} .meta,figcaption{color:#5f6672}
}
"""


def calibrate_threshold(neg_top1: list[float], pos_top1: list[float]) -> dict:
    """零假设组法标定门槛（纯函数）：`(噪声地板 + 命中带下沿) / 2`。

    两组分数：反例（这部番题材上不可能有的画面）的 Top-1 与真实查询的 Top-1。
    门槛取两者中间——**它同时满足「挡住反例」与「留下真命中」**，而这正是
    CLIP 那套做不到的事（两边分数完全重叠，见 ADR-0003 待实测 #4）。

    `floor` 是 n 条反例的 max，是噪声水位的**下界**（max 只随反例条数上升），
    所以这么标出来的门槛偏松——与 subindex 的 `NO_MATCH=0.45` 同源，也同一性质。

    `overlap=True`（floor ≥ 命中带下沿）时**不存在**这样的门槛：不返回可用的数，
    让调用方自己去判 ADR-0015 推翻条件②，不许写 config。
    """
    if not neg_top1 or not pos_top1:
        raise SystemExit("FAIL 标定要两组分数：反例 Top-1 与真实查询 Top-1，缺一不可")
    neg = sorted(neg_top1, reverse=True)
    floor, second = neg[0], (neg[1] if len(neg) > 1 else neg[0])
    hit_low, hit_high = min(pos_top1), max(pos_top1)
    return {"floor": round(floor, 4), "floor_second": round(second, 4),
            "hit_low": round(hit_low, 4), "hit_high": round(hit_high, 4),
            "threshold": round((floor + hit_low) / 2, 4),
            "overlap": floor >= hit_low,
            "n_neg": len(neg_top1), "n_pos": len(pos_top1)}


def write_no_match(anime: str, cal: dict, episode: str | None,
                   path: Path = vindex.SCENES) -> None:
    """把标定出来的门槛写进 `config/scenes.json`——**写即解封**（机制如此）。

    写的是那份 JSON 的全部键，只加/改 `no_match` 与 `_no_match_note`；
    重写会重排缩进，但**不丢任何注记**（注释都是真键）。
    重叠时拒绝写：那时的「门槛」是拿一个不存在的值去卡检索。
    """
    if cal["overlap"]:
        raise SystemExit(
            f"FAIL 《{anime}》反例与正例分数重叠（地板 {cal['floor']:.3f} ≥ 命中带下沿 "
            f"{cal['hit_low']:.3f}），**门槛标不出来**，不写 config。\n"
            f"     ADR-0015 推翻条件②：这条路线在这部番上不成立（画文不符且不报错），\n"
            f"     停工报告，不许换嵌入模型重试第三次")
    db = json.loads(path.read_text(encoding="utf-8"))
    conf = db.setdefault(anime, {})
    conf["no_match"] = cal["threshold"]
    conf["_no_match_note"] = (
        f"零假设组法标定（vprobe scene {anime} {episode or '整池'}，bge-m3 文-文索引）："
        f"{cal['n_neg']} 条反例 Top-1 的地板 {cal['floor']:.3f}（次高 {cal['floor_second']:.3f}），"
        f"{cal['n_pos']} 条真实查询 Top-1 的下沿 {cal['hit_low']:.3f}，取中间值。"
        f"两组不重叠。**这个数只对这次标定成立，不许照抄到别的番 / 别的池**"
        f"（ADR-0003 待实测 #5：门槛是域条件的，不是模型常数）。")
    paths.atomic_write(path, json.dumps(db, ensure_ascii=False, indent=2) + "\n")


def scene_probe(anime: str, key: str | None, queries: list[str], negative: list[str],
                k: int = 5, out_dir: Path = PROBE_DIR,
                write: bool = False) -> tuple[Path, dict]:
    """查询各出 Top-k + 零假设组标定门槛。**第 2 层的准入闸门。**

    v2 换的是索引内核（VLM captions + bge-m3，ADR-0015），探针的活没变：
    看每条查询的 Top-k 里有没有一张真的是它说的那个画面（命中率，目标 ≥70%），
    并用反例组量出噪声地板——**「存不存在一个门槛」才是准入判据**，
    命中率好看但两组分数重叠照样不许建（画文不符且不报错）。

    **判的是「认不认得这些画面」，不是「认不认得动漫画面」**（2026-09-11 裁决）：
    M2b 的主要服务对象是**实拍素材池**（Live/MV/采访），番剧降为参照组——它走
    番剧笔记的锚点直通，根本不需要视觉索引。

    `anime` 是**素材池名**：`key=None` = 整池标定（检索池就是整池，量噪声地板就该在
    整池上量）；给了 `key` 就只跑那一集（参照组/单集复核）。
    `write=True` 把标定值写进 `config/scenes.json`（写即解封）；两组重叠时拒绝写。
    """
    vecs, units = vindex.load_scene(anime, episode=key, require_full=False)
    out_dir.mkdir(parents=True, exist_ok=True)
    page = out_dir / f"scene-{anime}_{key or 'pool'}.html"

    # 镜头表按集缓存：池级标定会跨 8 集（原先每个命中重读一遍 shots.json）
    tabs = {k: shots.load(anime, k)["shots"] for k in {vindex.key_of(u) for u in units}}

    blocks, rec = [], []
    for q in queries:
        hits = vindex.search_scene(q, vecs, units, k)
        figs = []
        for score, u in hits:
            kk = vindex.key_of(u)
            f = shots.frame_path(anime, kk, _shot_index(tabs[kk], u.start))
            figs.append(
                f'<figure><img src="{html.escape(_rel(f, out_dir))}" loading="lazy">'
                f'<figcaption>{score:.3f}　{kk} {_mmss(u.start)}</figcaption></figure>')
        blocks.append(f"<h2>{html.escape(q)}</h2><div class=row>{''.join(figs)}</div>")
        rec.append({"query": q, "scores": [round(s, 4) for s, _ in hits]})

    # 反例：只记分数，不出图——没什么好看的，要的就是那个数
    neg = sorted(({"query": q, "top1": round(vindex.search_scene(q, vecs, units, 1)[0][0], 4)}
                  for q in negative), key=lambda n: -n["top1"])
    cal = calibrate_threshold([n["top1"] for n in neg], [m["scores"][0] for m in rec])
    floor, second = cal["floor"], cal["floor_second"]

    # **地板是个顺序统计量，不是索引的常数。** 它取 n 条反例的 max，而 max 只随 n
    # 单调上升——多试几条只会更高，绝不会更低。所以它是噪声真实水位的**下界**，
    # 拿它当门槛必然偏松。这正是要跟「命中带」取中间的原因。
    #
    # 这里**不做「抽一半再算一遍」那种检验**：任何子集的 max 都 ≤ 全集的 max，
    # 那个比较恒为真，报出来的「不稳」永远成立，等于没检验
    # （与「一条永远绿的断言等于没有断言」同源，只是方向相反）。
    # 能诚实报的是：地板离次高有多远（说明它被单独一条撑着的程度），
    # 以及两组分数重不重叠——后者才是「能不能定出门槛」的直接证据。
    below = [m for m in rec if m["scores"][0] <= floor]
    verdict = (f"<b>两组重叠 → 门槛标不出来</b>（地板 {floor:.3f} ≥ 命中带下沿 "
               f"{cal['hit_low']:.3f}）：ADR-0015 推翻条件②，停工报告，不许再换嵌入模型重试"
               if cal["overlap"] else
               f"两组不重叠 → 标定门槛 = 中间值 <b>{cal['threshold']:.3f}</b>"
               f"（地板 {floor:.3f} / 命中带下沿 {cal['hit_low']:.3f}）")

    neg_rows = "".join(
        f"<tr><td>{html.escape(n['query'])}</td><td>{n['top1']:.3f}</td></tr>" for n in neg)
    pos_rows = "".join(
        f"<tr><td>{html.escape(m['query'])}</td><td>{m['scores'][0]:.3f}</td>"
        f"<td>{m['scores'][-1]:.3f}</td></tr>" for m in rec)
    blocks.append(
        "<h2>门槛标定（零假设组法）</h2>"
        f'<div class="meta"><p>地板：{cal["n_neg"]} 条反例的 Top-1 最高 '
        f"<b>{floor:.3f}</b>（{html.escape(neg[0]['query'])}），次高 {second:.3f}。"
        f"命中带：{cal['n_pos']} 条真实查询的 Top-1 从 <b>{cal['hit_low']:.3f}</b> 到 "
        f"{cal['hit_high']:.3f}。{verdict}</p>"
        f"<p><b>⚠ 别把地板直接抄进 config。</b>它是 {cal['n_neg']} 条反例的 <code>max</code>，"
        f"而 max 只随反例条数单调上升——多写几条它只会更高。所以它是噪声水位的"
        f"<b>下界</b>，当门槛必然偏松；取中间值才是这一步的产出。</p>"
        f"<p>{len(below)} / {len(rec)} 条正例的 Top-1 没高过这条地板（正例表里 Top-1 一列）。</p>"
        f"</div>"
        f'<table class="neg"><tr><th>反例查询</th><th>Top-1</th></tr>{neg_rows}</table>'
        f'<table class="neg"><tr><th>真实查询</th><th>Top-1</th><th>Top-{k}</th></tr>{pos_rows}</table>')

    page.write_text(
        f"<!doctype html><meta charset=utf-8><title>{anime} {key or '整池'} 画面语义探针</title>"
        f"<style>{CSS}</style><h1>{anime} {key or '整池'}　画面语义探针（通道 2 准入）</h1>"
        f'<div class="meta">索引 <b>{vindex.CAPTIONS_REPO}</b> 打标 + '
        f"<b>{vindex.EMBED_REPO}</b> 向量；{len(units)} 个镜头，{len(queries)} 条查询各出 Top-{k}。<br>"
        f"<b>判据一：每条查询的 Top-{k} 里有没有一张真的是它说的那个画面</b>（目标 ≥70%，人判）。<br>"
        f"<b>判据二：反例与正例的分数重不重叠</b>——分数量叠就不存在能用的门槛，"
        f"这比命中率不够更致命（ADR-0003 待实测 #4：CLIP 就是死在这里）。</div>"
        + "".join(blocks), encoding="utf-8")
    summary = {"索引": f"{vindex.CAPTIONS_REPO} + {vindex.EMBED_REPO}",
               "范围": key or "整池", "镜头数": len(units), "查询数": len(queries),
               **cal, "正例未过地板": len(below), "反例": neg, "明细": rec}
    if write:
        write_no_match(anime, cal, key)
        print(f"OK 已写入 config/scenes.json 的《{anime}》no_match = {cal['threshold']}")
    return page, summary


def _mmss(t: float) -> str:
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


def captions_probe(anime: str, key: str | None = None, n: int = 30,
                   out_dir: Path = PROBE_DIR) -> tuple[Path, dict]:
    """抽 n 个镜头人看 caption 准不准（ADR-0015 待实测 #1：目标准确 ≥27/30）。

    **均匀取、不挑好的**（与 tagger 探针同一条纪律）：挑「一看就好描述」的镜头
    去证明模型行，等于自己给自己发合格证。

    `key=None` = **整池抽样**（2026-09-11 裁决：验收对象是素材池，不是番剧；
    池里各集的镜头数天然不等，按行均匀取即为按镜头数加权）。

    产 HTML（帧与描述并排，中文只在浏览器里显示得对）+ markdown 抽查表（人填对/错）。
    **帧出的是这个镜头全部采样帧**（长镜头 3 张），因为人判的是「这条描述说的是不是
    模型看到的那几帧」，不是「说的是不是缩略图那一帧」。
    """
    keys = vindex.caption_keys(anime) if key is None else [key]
    picked = [(k, r) for k in keys for r in vindex.load_captions(anime, k)[1]]
    meta = vindex.load_captions(anime, keys[0])[0]
    step = max(1, len(picked) // n)
    picked = picked[::step][:n]
    out_dir.mkdir(parents=True, exist_ok=True)

    figs = []
    for k, r in picked:
        imgs = "".join(
            f'<img src="{html.escape(_rel(shots.caption_frame_path(anime, k, r["shot"], k_, shots.FRAMES_DIR), out_dir))}"'
            f' loading="lazy">' for k_ in range(r.get("frames", 1)))
        bad = ("" if r["status"] == "ok"
               else f'<span class="bad">[{r["status"]}] {html.escape(r.get("reason", ""))}</span>')
        figs.append(
            f'<figure><div class=frames>{imgs}</div>'
            f'<figcaption><b>{k} #{r["shot"] + 1}</b> {_mmss(r["start"])} {bad}<br>'
            f'{html.escape(r["caption"] or "（无描述）")}</figcaption></figure>')
    tag = key or "整池"
    page = out_dir / f"captions-{anime}_{key or 'pool'}.html"
    page.write_text(
        f"<!doctype html><meta charset=utf-8><title>{anime} {tag} caption 抽验</title>"
        f"<style>{CSS}"
        ".frames img{width:100%;display:block;margin-bottom:2px}"
        ".bad{color:#f28b82}</style>"
        f"<h1>{anime} {tag}　caption 抽验（{len(picked)} 个镜头）</h1>"
        f'<div class="meta">模型 <b>{vindex.CAPTIONS_REPO}</b>，prompt '
        f"<b>{meta['prompt_version']}</b>，"
        f"{'按镜头均匀取（不挑好的）' if key is None else f'只抽 {key} 这一集'}。<br>"
        f"<b>判据：这条描述说的是不是这个画面</b>（准确，不是文笔）。"
        f"目标准确 ≥27/30（ADR-0015），不达标回 prompt 工程，两轮不达触发推翻条件①。</div>"
        f'<div class=row>{"".join(figs)}</div>', encoding="utf-8")

    md_lines = [f"# {anime} {tag} caption 抽验（{len(picked)} 个镜头）", "",
                "判据：这条描述说的是不是这个画面（准确），不是文笔好不好。",
                "「人判」列填 对/错；错了在「错在哪」写一句。目标准确 ≥27/30（ADR-0015）。", "",
                "| 集键 | 镜头 | 时间码 | 采样帧文件 | 描述 | 人判 | 错在哪 |",
                "|---|---|---|---|---|---|---|"]
    for k, r in picked:
        files = " ".join(shots.caption_frame_path(anime, k, r["shot"], k_,
                                                 shots.FRAMES_DIR).name
                        for k_ in range(r.get("frames", 1)))
        md_lines.append(f"| {k} | {r['shot'] + 1} | {_mmss(r['start'])} | {files} | "
                        f"{r['caption'] or '（无描述）'} |  |  |")
    md = out_dir / f"captions-{anime}_{key or 'pool'}.md"
    md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    summary = {"模型": vindex.CAPTIONS_REPO, "prompt 版本": meta["prompt_version"],
               "范围": tag, "池内集数": len(keys),
               "镜头总数": sum(len(vindex.load_captions(anime, k)[1]) for k in keys),
               "抽验": len(picked),
               "html": str(page), "抽查表": str(md),
               "抽验镜头": [{"集键": k, "shot": r["shot"], "start": r["start"],
                             "caption": r["caption"], "status": r["status"]}
                            for k, r in picked]}
    return page, summary


def _shot_index(sh: list[dict], start: float) -> int:
    """时间点 → 镜头号。**镜头表由调用方读一次传进来**：
    原先每个命中都重读一遍 shots.json，一次探针要读几十遍同一个文件。"""
    s = shots.at(sh, start, eps=0.05)
    return s["i"] if s else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tagger", help="通道 1 准入：这个 tagger 认不认得这部番的角色")
    t.add_argument("anime")
    t.add_argument("episode")
    t.add_argument("-n", type=int, default=24)

    p = sub.add_parser("presence", help="逐角色抽检：认出的里面有没有认错的")
    p.add_argument("anime")
    p.add_argument("name")
    p.add_argument("-n", type=int, default=20)

    s = sub.add_parser("scene", help="通道 2 准入：命中率 + 零假设组标定门槛")
    s.add_argument("anime", help="素材池名（一部番或一池特典素材）")
    s.add_argument("episode", nargs="?", default=None,
                   help="不给 = 整池标定（检索池就是整池）；给了就只跑那一集")
    s.add_argument("--queries", type=Path, help="一行一条；不给就用配置里的十条")
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--write", action="store_true",
                   help="把标定值写进 config/scenes.json（写即解封；两组重叠时拒绝写）")

    cp = sub.add_parser("captions", help="caption 抽验：描述说的是不是那个画面（≥27/30）")
    cp.add_argument("anime", help="素材池名（一部番或一池特典素材）")
    cp.add_argument("episode", nargs="?", default=None, help="不给 = 整池抽样")
    cp.add_argument("-n", type=int, default=30)

    a = ap.parse_args()
    paths.require_data()

    if a.cmd == "tagger":
        img, summary = tagger_probe(a.anime, a.episode, a.n)
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        print(f"\n看图判：open '{img}'")
        print("判据：认出的对不对（精确率），以及这部番的主要角色是不是都在词表里（覆盖）。")
        return 0

    if a.cmd == "presence":
        img, summary = presence_probe(a.anime, a.name, a.n)
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        print(f"\n看图判：open '{img}'")
        print("判据：这些镜头里是不是都真的有这个人。错一张就说明该调阈值或摘掉这个角色。")
        return 0

    if a.cmd == "captions":
        page, summary = captions_probe(a.anime, a.episode, a.n)
        print(json.dumps({k: v for k, v in summary.items() if k != "抽验镜头"},
                         ensure_ascii=False, indent=1))
        print(f"\n看图判：open '{page}'")
        print(f"抽查表（填「人判」列）：{summary['抽查表']}")
        print("判据：这条描述说的是不是这个画面（准确，不是文笔）。目标准确 ≥27/30。")
        return 0

    # 反例始终从配置读：`--queries` 是临时试问法用的，换正例不该动噪声地板
    queries, negative = vindex.scene_queries(a.anime)
    if a.queries:
        queries = [q.strip() for q in a.queries.read_text(encoding="utf-8").splitlines()
                   if q.strip()]
    page, summary = scene_probe(a.anime, a.episode, queries, negative, a.k, write=a.write)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("反例", "明细")},
                     ensure_ascii=False, indent=1))
    print(f"\n看图判：open '{page}'")

    # 标定证据落盘（ADR-0003 待实测 #4 的同格式：反例分数表 / 正例分数表 / 是否重叠）
    ev = page.with_suffix(".json")
    ev.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"标定证据：{ev}")
    if summary["overlap"]:
        print("★ 两组分数重叠：门槛标不出来（ADR-0015 推翻条件②）——不写 config，停工报告。")
        return 1
    print(f"门槛 = (地板 {summary['floor']:.3f} + 命中带下沿 {summary['hit_low']:.3f}) / 2 "
          f"= {summary['threshold']:.3f}" + ("（已写入 config）" if a.write else
                                            "（确认后加 --write 写入 config/scenes.json）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
