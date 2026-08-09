"""字幕语义索引：把台词连同时间码变成可检索的向量。

检索在文本上做，不需要视频在手——只有确定用哪些片段之后才需要对应集数的视频文件。

用法：
    python -m pipeline.subindex build <字幕文件> --anime 春物 --season 1 --episode 3
    python -m pipeline.subindex search "角色置身人群却格格不入" -k 5
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

from . import paths  # 必须在 sentence_transformers 之前：把模型缓存钉到 SSD

import numpy as np
import pysubs2
from sentence_transformers import SentenceTransformer

# 2026-07-28 矩阵评测结论：small→base 是命中率的主要变量，切分方式次要。
# bge-small 窗口2 = 75% / bge-base 窗口2 = 100%（Top-1，8 条查询）。不要为省 300MB 退回 small。
MODEL_NAME = "BAAI/bge-base-zh-v1.5"
# bge 中文模型的检索约定：查询侧加指令前缀，被检索文本不加
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

INDEX_DIR = paths.DATA / "library" / "index"

# 字幕里的非台词行：字幕组信息、OP/ED 制作名单、纯符号
NOISE = re.compile(
    r"(字幕组|翻译|校对|时轴|压制|后期|片源|特效|招募|微博|QQ群|"
    r"作词|作曲|编曲|歌:|唄:|歌唱|OP\d|ED\d|www\.|https?://)"
)

# 窗口=1 语义信号不足（88%），窗口=3 混入无关台词、干扰污染翻倍。2 是实测最优。
WINDOW = 2

# 双语字幕（Chs&Jap / JPSC）把中日两条轨放在同一个文件里。日文行必须滤掉：
# 检索查询是中文，索引里混进日文既翻倍了单元数，又会让滑窗把中日两句并成一个单元。
# 判据用假名——汉字中日都有，假名只有日文有。ASR 产出的字幕是纯中文，不受影响。
KANA = re.compile(r"[ぁ-んァ-ヴー]")

# **绘图事件按标签认，不按内容认。** ASS 的绘图模式由 `\p1`（及以上）开启、`\p0` 关闭，
# 标签之后的「文本」是矢量路径坐标，长这样：
#     m 13 44 b 17 49 23 57 25 64 42 74 54 42 14 43 m 91 68 b 95 71 ...
# 这是 OP 卡拉OK 标题图形，不是台词。
#
# **这条必须在 `_clean` 之前判。** 2026-07-30 踩的就是这个：原先只靠下方的 CJK 判据
# 兜底，而 `_clean` 会先把 `{...}` 整段剥掉——**用来识别绘图的那个信号，在过滤之前
# 就被自己扔了**。S1 的 OP 特效每片花瓣带一个标题汉字，剥完标签正好是「坐标串 + 一个汉字」，
# CJK 判据于是放行：S01E01 建出的 682 个单元里 234 个（34%）是这种垃圾。
#
# 后果不只是浪费空间——它们会被检索到。查「歌词」类语义时返回的是 OP 画面，
# 而且分数看着完全正常，没有任何地方报错。
DRAW = re.compile(r"\\p[1-9]")

# 兜底判据：**必须含汉字才算台词。** 2026-07-29 实测 S3E02 的 1010 个单元里有 619 个
# （61%）是纯坐标串——只滤假名和噪声词拦不住，因为它们两样都不沾。
# 上面的 DRAW 是正解，这条留着兜「没有标签但同样不是台词」的情况（罗马音、纯符号）。
CJK = re.compile(r"[一-鿿]")

# **OP/ED/插曲的歌词、标题、画面字，一律按 style 滤掉。**
#
# 2026-07-30 踩的：段落「过日子最怕的是有话不说、攒着」检索到 S01E10 与 S01E11 的
# 22:43.63——**两集同一个时间码，因为那是片尾 ED**。片尾曲每集都在同一位置。
#
# 这是最坏的一种陷阱：**语义最贴、画面完全不能用。** ED 歌词就是全剧主题的浓缩，
# 所以对「说不出口」「无法坦率」这类主题式查询命中率极高，分数还很正常，
# 而切出来的是滚字幕的片尾。全程不报错。
#
# 判据用 style 而不是时间窗或文风：**style 是字幕组自己声明的分类**，
# 最接近「这一行是不是台词」这个事实。时间窗每集不同（有的集没 OP），文风判不了。
#
# 命名逐组不同（OP-CN / EDCN / EDCN-yui / In-CN…），所以按前缀匹配。
# 「留」的一侧是 Sub-CN / Text-cn 这类，前缀不沾，不会误杀。
# 遇到新发布时看 `build` 打印的 style 分布，有没漏的一眼能看出来。
NON_DIALOGUE_STYLE = re.compile(
    r"^(?:op|ed|in)(?:[-_ ]|cn|jp|\d|$)"
    r"|^(?:title|staff|bgm|gamen|logo|sign|song|lyric|kara)", re.I)


@dataclass
class Unit:
    """一个可检索单元：连续几句台词 + 它们在源片中的时间区间。"""

    anime: str
    season: int
    episode: int
    start: float  # 秒
    end: float
    text: str

    @property
    def duration(self) -> float:
        return self.end - self.start


def _clean(raw: str) -> str:
    """去掉 ASS 覆盖标签、换行符与首尾空白。"""
    s = re.sub(r"\{[^}]*\}", "", raw)  # {\an8} 之类
    s = s.replace(r"\N", " ").replace(r"\n", " ").replace("\\h", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def styles(path: Path) -> dict[str, int]:
    """各 style 有多少行进得了索引，以及被 style 判据滤掉多少。

    给 `build` 打印用。**这个判据是按字幕组的命名前缀匹配的，逐组不同**，
    所以要让它可审计——换个发布时看一眼分布，有没漏掉的歌词 style 一目了然。
    """
    kept: dict[str, int] = {}
    for ev in pysubs2.load(str(path)):
        if ev.is_comment or DRAW.search(ev.text):
            continue
        t = _clean(ev.text)
        if not t or NOISE.search(t) or not CJK.search(t) or KANA.search(t) or len(t) < 2:
            continue
        key = ev.style + ("  ←滤" if NON_DIALOGUE_STYLE.match(ev.style) else "")
        kept[key] = kept.get(key, 0) + 1
    return kept


def parse(path: Path, anime: str, season: int, episode: int) -> list[Unit]:
    """字幕文件 → 检索单元列表（滑窗合并，步长 1，窗口间重叠）。"""
    subs = pysubs2.load(str(path))

    lines: list[tuple[float, float, str]] = []
    for ev in subs:
        if ev.is_comment:
            continue
        if NON_DIALOGUE_STYLE.match(ev.style):  # OP/ED 歌词、标题、画面字
            continue
        if DRAW.search(ev.text):  # 绘图事件，必须在 _clean 剥掉标签之前判
            continue
        text = _clean(ev.text)
        if not text or NOISE.search(text):
            continue
        if not CJK.search(text):  # 绘图坐标 / 罗马音 / 纯符号，见上方注释
            continue
        if KANA.search(text):  # 双语字幕的日文轨，见上方注释
            continue
        if len(text) < 2:  # 纯语气词对检索无价值
            continue
        lines.append((ev.start / 1000.0, ev.end / 1000.0, text))

    lines.sort(key=lambda x: x[0])

    units: list[Unit] = []
    for i in range(len(lines)):
        chunk = lines[i : i + WINDOW]
        if not chunk:
            continue
        units.append(
            Unit(
                anime=anime,
                season=season,
                episode=episode,
                start=chunk[0][0],
                end=chunk[-1][1],
                text=" ".join(c[2] for c in chunk),
            )
        )
    return units


_model: SentenceTransformer | None = None


def model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed(texts: list[str], is_query: bool = False) -> np.ndarray:
    payload = [QUERY_PREFIX + t for t in texts] if is_query else texts
    return model().encode(
        payload, normalize_embeddings=True, show_progress_bar=False
    ).astype(np.float32)


def meta(dim: int) -> dict:
    """索引的自描述元信息。**换模型必须能被发现。**

    2026-08-03 补（ADR-0003 要求「两个索引一起补」）。在此之前这个文件里只有台词和
    时间码，没有模型身份，靠「只有一个人、只有一台机器、没改过模型」侥幸没出事。

    **理由是这类不一致不会自己暴露。** 维度不同会崩（`np.vstack` 或 `vecs @ q` 抛异常），
    那算运气好；**同维度换模型不会崩**——余弦照样算得出来，分数照样落在看起来正常的
    区间，照样过阈值、照样返回 Top-K、照样渲染出片。
    """
    return {
        "kind": "subtitle",
        "model_id": MODEL_NAME,
        "revision": paths.model_revision(MODEL_NAME),
        "dim": dim,
        "window": WINDOW,
        "query_prefix": QUERY_PREFIX,
        "built_at": date.today().isoformat(),
    }


def build(sub_path: Path, anime: str, season: int, episode: int, out_dir: Path) -> int:
    units = parse(sub_path, anime, season, episode)
    if not units:
        raise SystemExit(f"FAIL 未从 {sub_path} 解析出任何台词，检查文件格式")

    vecs = embed([u.text for u in units])
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{anime}_S{season:02d}E{episode:02d}"
    np.save(out_dir / f"{stem}.npy", vecs)
    (out_dir / f"{stem}.json").write_text(
        json.dumps({"meta": meta(int(vecs.shape[1])),
                    "units": [asdict(u) for u in units]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(units)


def load_all(index_dir: Path, anime: str | None = None) -> tuple[np.ndarray, list[Unit]]:
    """加载分集索引。这个量级（万级）暴力余弦即可，无需向量库。

    **给了 `anime` 就只加载那一部。** 索引目录是全局的，一台机器上做几部番时
    所有 `.npy` 都堆在一起；不过滤就会跨番命中——而下游 `clips.candidate` 拿
    `S02E02` 这样的键去当前番的片源表里查文件，**命中别的番会解析成本番的同季同集，
    切出完全不相干的画面且不报错**。2026-07-29 审计时发现，当时只有一部番所以没暴露。

    过滤放在加载这一层，不放在检索之后：同一个 `.npy` 里的单元同属一部番，
    整个文件跳过比逐条筛便宜，也不会让 `vecs` 和 `units` 的下标错位。
    """
    vecs, units = [], []
    for meta_path in sorted(index_dir.glob("*.json")):
        vec_path = meta_path.with_suffix(".npy")
        if not vec_path.exists():
            continue
        d = json.loads(meta_path.read_text(encoding="utf-8"))
        rows = _check(d, meta_path)
        if anime is not None and (not rows or rows[0]["anime"] != anime):
            continue
        vecs.append(np.load(vec_path))
        units.extend(Unit(**r) for r in rows)
    if not vecs:
        where = f"{index_dir} 下" + (f"没有《{anime}》的" if anime else "没有")
        raise SystemExit(f"FAIL {where}索引，先跑 build")
    return np.vstack(vecs), units


def _check(d, path: Path) -> list[dict]:
    """校验元信息并取出台词单元。**对不上直接失败，不许继续。**

    旧格式（裸列表，没有模型身份）也在这里拦下：它建的时候用的什么模型无从得知，
    而拿 bge-base 的查询去打一份 bge-small 建的索引不会报错，只会静默地变差。
    """
    if isinstance(d, list):
        raise SystemExit(
            f"FAIL {path.name} 是旧格式索引（没有模型身份）。\n"
            f"     2026-08-03 起索引必须自描述模型身份，否则同维度换模型不会崩、只会静默出错。\n"
            f"     重建（已登记的集免跑 verify，只重算向量）：\n"
            f"     python -m pipeline.ingest phase0 <该季所有 mkv> --anime <番> --season <N> --reindex")
    m = d.get("meta", {})
    if m.get("model_id") != MODEL_NAME:
        raise SystemExit(
            f"FAIL {path.name} 建索引时用的是 {m.get('model_id')}，当前是 {MODEL_NAME}。\n"
            f"     两个模型的向量空间不可比，重建索引再用")
    rev = paths.model_revision(MODEL_NAME)
    if rev and m.get("revision") and m["revision"] != rev:
        raise SystemExit(
            f"FAIL {path.name} 的模型版本是 {m['revision'][:12]}，本机缓存的是 {rev[:12]}。\n"
            f"     换 backbone 常常不改维度，分数照样算得出来，所以这里必须硬失败。重建索引")
    return d["units"]


def search(
    query: str, vecs: np.ndarray, units: list[Unit], k: int = 5,
    season: int | None = None, episode: int | None = None,
) -> list[tuple[float, Unit]]:
    """检索台词单元。`season`/`episode` 给任一就**只在该集内打分**（ADR-0004）。

    集号是剧情知识（「先根据剧情锁定季和集」），比语义分更强——跨季/跨集的高分
    会顶掉正确画面（2026-08-09 实证：段 19 正确的 S01E08 0.653 输给跨季 S04E05
    0.506）。所以集约束必须进打分这一步，不是检索之后过滤。

    season/episode 必须成对给（S2E07 和 S1E07 同时存在，只给一个没有意义）。
    返回的是 (分, unit) 对，掩码下标不泄露，天然安全。
    """
    if (season is None) != (episode is None):
        raise SystemExit("FAIL search 的 season/episode 必须成对给（或都不给）")
    q = embed([query], is_query=True)[0]
    if season is not None:
        mask = [u.season == season and u.episode == episode for u in units]
        vecs = vecs[mask]
        units = [u for u, m in zip(units, mask) if m]
        if not units:
            return []                     # 该集一个单元都没有，空结果走降级链
    scores = vecs @ q  # 已归一化，点积即余弦
    top = np.argsort(-scores)[:k]
    return [(float(scores[i]), units[i]) for i in top]


def _fmt(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="索引一个字幕文件")
    b.add_argument("subtitle", type=Path)
    b.add_argument("--anime", required=True)
    b.add_argument("--season", type=int, default=1)
    b.add_argument("--episode", type=int, required=True)
    b.add_argument("--index-dir", type=Path, default=INDEX_DIR)

    s = sub.add_parser("search", help="语义检索")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--anime", help="只搜这一部；不给则搜索引目录里的全部")
    s.add_argument("--index-dir", type=Path, default=INDEX_DIR)

    a = ap.parse_args()
    if a.cmd == "build":
        n = build(a.subtitle, a.anime, a.season, a.episode, a.index_dir)
        print(f"OK 索引 {n} 个单元 → {a.index_dir}")
    else:
        vecs, units = load_all(a.index_dir, a.anime)
        for score, u in search(a.query, vecs, units, a.k):
            print(
                f"{score:.3f}  {u.anime} S{u.season:02d}E{u.episode:02d} "
                f"{_fmt(u.start)}-{_fmt(u.end)}  {u.text}"
            )


if __name__ == "__main__":
    main()
