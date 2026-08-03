"""视觉索引的三个探针（ADR-0003 的准入闸门）。

    python -m pipeline.vprobe tagger 春物 S01E01        # 通道 1 能不能认出这部番的角色
    python -m pipeline.vprobe presence 春物 雪乃         # 某个角色认得准不准（相当于验簇纯度）
    python -m pipeline.vprobe scene 春物 S01E01         # 通道 2 的中文查询命中率

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
# 写死在这里就是 anchor_words 那个错误的重演（见 config/project.json 的 _anchor_note）。


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


def scene_probe(anime: str, key: str, queries: list[str], negative: list[str],
                k: int = 5, out_dir: Path = PROBE_DIR) -> tuple[Path, dict]:
    """十条中文查询各出 Top-k，人看命中率。

    **这是第 2 层的准入闸门，不是效果展示。** CLIP 系模型主要在照片上训练，
    动漫是域偏移，效果未经验证；命中率过不去就别建这一层
    （ADR-0003「什么情况下推翻本决定」）。

    只跑一集，因为要判的是「这个模型认不认得动漫画面」，不是「素材够不够多」。

    同时跑一组**反例查询**（`config/scenes.json` 的 `negative`），产出噪声地板——
    门槛必须落在噪声地板之上，否则「没找到」永远不会发生。
    """
    vecs, units = vindex.load_scene(anime, episode=key)
    sh = shots.load(anime, key)["shots"]
    out_dir.mkdir(parents=True, exist_ok=True)
    page = out_dir / f"scene-{anime}_{key}.html"

    blocks, rec = [], []
    for q in queries:
        hits = vindex.search_scene(q, vecs, units, k)
        figs = []
        for score, u in hits:
            i = _shot_index(sh, u.start)
            f = shots.frame_path(anime, key, i)
            figs.append(
                f'<figure><img src="{html.escape(_rel(f, out_dir))}" loading="lazy">'
                f'<figcaption>{score:.3f}　'
                f'{int(u.start) // 60:02d}:{int(u.start) % 60:02d}</figcaption></figure>')
        blocks.append(f"<h2>{html.escape(q)}</h2><div class=row>{''.join(figs)}</div>")
        rec.append({"query": q, "scores": [round(s, 4) for s, _ in hits]})

    # 反例：只记分数，不出图——没什么好看的，要的就是那个数
    neg = sorted(({"query": q, "top1": round(vindex.search_scene(q, vecs, units, 1)[0][0], 4)}
                  for q in negative), key=lambda n: -n["top1"])
    floor = max(n["top1"] for n in neg)

    # **地板是个顺序统计量，不是索引的常数。** 它取 n 条反例的 max，而 max 只随 n
    # 单调上升——多试几条只会更高，绝不会更低。所以它是噪声真实水位的**下界**，
    # 拿它当门槛必然偏松。
    #
    # 这里**不做「抽一半再算一遍」那种检验**：任何子集的 max 都 ≤ 全集的 max，
    # 那个比较恒为真，报出来的「不稳」永远成立，等于没检验
    # （与「一条永远绿的断言等于没有断言」同源，只是方向相反）。
    # 能诚实报的是：地板离次高有多远（说明它被单独一条撑着的程度），
    # 以及正例与反例重叠了多少——后者才是「能不能定出门槛」的直接证据。
    second = neg[1]["top1"] if len(neg) > 1 else floor
    below = [m for m in rec if m["scores"][0] <= floor]

    neg_rows = "".join(
        f"<tr><td>{html.escape(n['query'])}</td><td>{n['top1']:.3f}</td></tr>" for n in neg)
    blocks.append(
        "<h2>反例（这部番不可能有的画面）</h2>"
        f'<div class="meta"><p>这些查询的 Top-1 全是<b>噪声</b>：几百个镜头里纯靠碰运气'
        f"能撞到的最高分。最高 <b>{floor:.3f}</b>（{html.escape(neg[0]['query'])}），"
        f"次高 {second:.3f}。</p>"
        f"<p><b>⚠ 别把这个数抄进 config。</b>它是 {len(neg)} 条反例的 <code>max</code>，"
        f"而 max 只随反例条数单调上升——多写几条它只会更高。"
        f"所以它是噪声水位的<b>下界</b>，当门槛必然偏松。</p>"
        f"<p><b>{len(below)} / {len(rec)} 条正例的 Top-1 没高过这条地板。</b>"
        f"判第 2 层能不能建，看的不是命中率好不好看，而是"
        f"<b>存不存在一个门槛，把肉眼确认的真命中全留下、把这些反例全挡住</b>。"
        f"两边分数重叠就不存在这样的门槛，这一层不许建"
        f"（画文不符且不报错，而 CLAUDE.md 写死了「留白远优于画文不符」）。</p></div>"
        f'<table class="neg">{neg_rows}</table>')

    page.write_text(
        f"<!doctype html><meta charset=utf-8><title>{anime} {key} 画面语义探针</title>"
        f"<style>{CSS}</style><h1>{anime} {key}　画面语义探针（通道 2 准入）</h1>"
        f'<div class="meta">模型 <b>{vindex.SCENE_REPO}</b>，'
        f"{len(units)} 个镜头，{len(queries)} 条查询各出 Top-{k}。<br>"
        f"<b>判据：每条查询的 Top-{k} 里有没有一张真的是它说的那个画面。</b>"
        f"记命中率，过不去就不建这一层——氛围段落继续留给人在第 05 步处理。<br>"
        f"顺便记下命中那几张的分数落在哪，那是画面通道自己的 NO_MATCH 门槛的依据"
        f"（<b>不许沿用台词通道的门槛，两个空间不同</b>）。</div>"
        + "".join(blocks), encoding="utf-8")
    return page, {"模型": vindex.SCENE_REPO, "镜头数": len(units),
                  "查询数": len(queries), "噪声地板": floor, "反例次高": second,
                  "正例未过地板": len(below),
                  "反例": neg, "明细": rec}


def _shot_index(sh: list[dict], start: float) -> int:
    """时间点 → 镜头号。**镜头表由调用方读一次传进来**：
    原先每个命中都重读一遍 shots.json，一次探针要读几十遍同一个文件。"""
    s = shots.at(sh, start + 0.001)
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

    s = sub.add_parser("scene", help="通道 2 准入：中文查询在动漫帧上的命中率")
    s.add_argument("anime")
    s.add_argument("episode")
    s.add_argument("--queries", type=Path, help="一行一条；不给就用内置的十条")
    s.add_argument("-k", type=int, default=5)

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

    # 反例始终从配置读：`--queries` 是临时试问法用的，换正例不该动噪声地板
    queries, negative = vindex.scene_queries(a.anime)
    if a.queries:
        queries = [q.strip() for q in a.queries.read_text(encoding="utf-8").splitlines()
                   if q.strip()]
    page, summary = scene_probe(a.anime, a.episode, queries, negative, a.k)
    print(json.dumps(summary, ensure_ascii=False, indent=1)[:1200])
    print(f"\n看图判：open '{page}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
