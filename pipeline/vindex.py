"""视觉索引：两个并列通道，检索单元是镜头（ADR-0003）。

    python -m pipeline.vindex presence 春物 S01E01     # 通道 1：画面里有谁
    python -m pipeline.vindex scene    春物 S01E01     # 通道 2：画面是什么
    python -m pipeline.vindex search "夜晚空无一人的天台" --anime 春物
    python -m pipeline.vindex status --anime 春物

字幕索引答的是「谁说了什么」，这里补上「画面里有谁」和「画面是什么」。
**两个通道之间是路由，不是分数融合**——每一段按需要走哪一条，绝不加权求和
（接法见 `clips.py`）。

| 通道 | 答什么 | 产出 | 排序 |
|---|---|---|---|
| 1 角色在场 | 这个镜头里有没有 X | 布尔 + 在场分 | **只在台词通道内、同一段落带内做 tie-break**（ADR-0004） |
| 2 画面语义 | 这个镜头像不像「夜晚的天台」 | 余弦分数 | 只在本通道内排（当前不可用） |

**通道 1 的分数只在一个地方被使用：台词通道内、同一段落、台词分差 ≤
`PRESENCE_BAND` 的候选之间决胜负**——那是次级排序，台词分差超过带子就
绝对优先。它绝不跨通道、绝不跨段落比较，也就从结构上不可能顶掉清晰的
台词命中（CLAUDE.md 判据 10 的唯一例外，理由与边界见 ADR-0004）。

> 索引文件里存了每个标签的分数，对外是 `Presence.presence_score()`。
> 存分数有两个用途：改判定阈值时不必重跑几小时推理（与 `shots.py` 存候选
> 切点同源），以及给排片做带内次级排序。用它的地方只有 `clips._by_character`
> 和探针 `vprobe.py`——任何新的使用点都先回 ADR-0004 对一遍边界。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import paths, shots          # paths 必须最先：把 HF_HOME 钉到 SSD

import numpy as np

VINDEX_DIR = paths.DATA / "library" / "vindex"
CHARACTERS = paths.CONFIG / "characters.json"
SCENES = paths.CONFIG / "scenes.json"

# ---------------------------------------------------------------- 通道 1

# **词表大小就是这一层的成败。** 2026-08-03 实测三个 tagger 在春物 12 个角色上的覆盖：
#
# | 模型 | 角色标签数 | 春物覆盖 | 体量 |
# |---|---|---|---|
# | SmilingWolf/wd-swinv2-tagger-v3 | 2751 | **3/12** | 380M |
# | pixai-tagger-v0.9 | 3720 | 4/12 | — |
# | **deepghs/camie_tagger_onnx（refined）** | **27021** | **11/12** | 1.6G |
#
# WD 那 3 个只有雪乃、结衣、一色——**连主角八幡都不在词表里**，拿它做角色过滤等于没做。
# camie 覆盖到八幡、小町、静老师、川崎、户冢，**以及陽乃**——雪乃/陽乃这对姐妹
# 正是 ADR-0003 列的「第 1 层最大的单点风险」，词表里有她们意味着这个风险直接消失，
# 连聚类和人工贴名都省了。
#
# **做成可插拔不是为了好看**，是 ADR-0003 的硬要求：booru tagger 是动漫专用的，
# 换真人影视直接作废，所以「不许让它成为架构里拔不掉的一环」。
# 换一个 tagger 只改这张表加一行 + 配置里改一个名字，其余代码不动。
#
# 预处理逐个模型不同（输入边长、通道序、补边颜色、取哪个输出），
# **这些不是可调参数，是训练时的约定，抄错等于喂模型没见过的分布**，所以写在表里。
#
# **`output` 必须写模型输出的名字，不许按下标取。** camie 一个模型吐 6 个输出
# （`initial/embedding`、`initial/logits`、`initial/output`、`embedding`、`logits`、`output`），
# 其中 `output` 就是 `sigmoid(logits)`——2026-08-03 实测两者最大差 1e-7。
# 初版按 `get_outputs()[-1]` 取再补一次 sigmoid，那样每个标签的分数都会被压到 0.5 以上，
# **等于每个标签都「检出」，而分数看着完全正常，没有任何地方报错**。
# 按名字取 + 下面 `tag()` 里的范围断言，是把这个假设错误变成当场失败。
TAGGERS = {
    "camie": {
        "repo": "deepghs/camie_tagger_onnx",
        "onnx": "refined/model.onnx", "tags": "refined/selected_tags.csv",
        "size": 512, "layout": "NCHW", "channels": "rgb",
        "scale": 1 / 255, "pad": (0, 0, 0), "output": "output",
        # 模型作者验证集上的 high_precision 工作点：精确率 0.971 / 召回率 0.508
        # （`refined/threshold.json` 的 character 段）。**不是自己拍的数。**
        "char_threshold": 0.5,
    },
    "wd": {
        "repo": "SmilingWolf/wd-swinv2-tagger-v3",
        "onnx": "model.onnx", "tags": "selected_tags.csv",
        "size": 448, "layout": "NHWC", "channels": "bgr",
        "scale": 1.0, "pad": (255, 255, 255), "output": "output",
        # 作者官方 Space 的 character 默认值。**这个模型只覆盖春物 3/12 个角色，
        # 拿它做角色过滤等于没做**，留在这里只是为了 camie 拿不到时能把代码路径跑通。
        "char_threshold": 0.85,
    },
}

TAGGER_CATEGORY_CHARACTER = "4"
TAGGER_CATEGORY_GENERAL = "0"

# 落盘下限。低于判定阈值也存，是为了**改判定阈值时不必重跑推理**——
# 一集推理要几分钟，41 集是几小时，而阈值本来就是要回头调的。
# 与 `shots.py` 把候选切点全存下来同源。
KEEP_THRESHOLD = 0.15


def profile() -> dict:
    """当前启用的 tagger。"""
    name = paths.conf("visual.tagger", "camie")
    if name not in TAGGERS:
        raise SystemExit(
            f"FAIL config/project.json 的 visual.tagger = {name!r} 不认识，"
            f"可选：{'、'.join(TAGGERS)}")
    return {**TAGGERS[name], "name": name}

# ---------------------------------------------------------------- 通道 2

# 中文查询直接进图像空间，不经翻译。**CLIP 系模型主要在照片上训练，动漫是域偏移**，
# 所以这一层动手前先跑探针（`vprobe scene`），命中率过不去就不建——
# ADR-0003：「探针的成本是半天，建完发现不好用的成本是一整块死代码」。
SCENE_REPO = "OFA-Sys/chinese-clip-vit-base-patch16"


# ---------------------------------------------------------------- 角色名表


def alias_map(anime: str, path: Path = CHARACTERS) -> dict[str, str]:
    """中文别名 → booru 标签。

    **索引里存的是模型原样吐出来的 booru 标签，中文映射在查询时才做。**
    这样加一个别名、改一个译名都不用重跑推理；只有换模型或改判定阈值才需要重建。
    """
    if not path.exists():
        raise SystemExit(
            f"FAIL 没有 {path}。角色名表是 Phase 0 资产（与 config/bgm.json 同类），\n"
            f"     格式：{{\"<番>\": {{\"<booru 标签>\": [\"中文名\", \"别名\"...]}}}}")
    db = json.loads(path.read_text(encoding="utf-8"))
    table = db.get(anime)
    if not table:
        raise SystemExit(f"FAIL {path} 里没有《{anime}》的角色名表")
    out: dict[str, str] = {}
    for tag, names in table.items():
        if tag.startswith("_"):
            continue
        out[tag] = tag                     # 标签自己也当别名，方便直接写 booru 名
        for n in names:
            out[str(n).strip()] = tag
    return out


def scene_conf(anime: str, path: Path = SCENES) -> dict:
    """这部番的画面语义配置：探针查询（正例 / 反例）与门槛。

    **按番存，不给跨番默认值。** 查询是内容不是机制：校园番问「空无一人的教室」，
    科幻番问的是别的；反例更是——「太空中的宇宙飞船」在春物里是噪声，在科幻番里是正片，
    照搬会把噪声地板测成一个真命中的高分。缺配置就失败，不许拿上一部番的表凑合。
    """
    if not path.exists():
        raise SystemExit(
            f"FAIL 没有 {path}。画面语义配置是 Phase 0 资产（与 config/characters.json 同类），\n"
            f"     格式：{{\"<番>\": {{\"queries\": [...], \"negative\": [...]}}}}")
    db = json.loads(path.read_text(encoding="utf-8"))
    # 下划线开头的顶层键是注释（`_note` / `_why_note`），不是番。
    # 不排掉的话 `db.get("_note")` 会返回一个字符串，它是真值、过得了下面这道检查，
    # 然后在调用方那里炸成 AttributeError——而报错信息里看不出是配置写错了。
    conf = db.get(anime) if not anime.startswith("_") else None
    if not isinstance(conf, dict):
        raise SystemExit(
            f"FAIL {path} 里没有《{anime}》的画面语义配置。\n"
            f"     **不要照抄别的番的查询表**：正例要写这部番真会用到的问法，\n"
            f"     反例要写这部番题材上不可能出现的画面（照抄会把噪声地板测错）")
    return conf


def scene_queries(anime: str, path: Path = SCENES) -> tuple[list[str], list[str]]:
    """(正例, 反例)。两张表都必须非空——反例空了就没有噪声地板，门槛无从定起。"""
    c = scene_conf(anime, path)
    pos = [q for q in c.get("queries", []) if str(q).strip()]
    neg = [q for q in c.get("negative", []) if str(q).strip()]
    for name, table in (("queries", pos), ("negative", neg)):
        if not table:
            raise SystemExit(f"FAIL {path} 的《{anime}》没有 `{name}`，探针跑不了")
    if set(pos) & set(neg):
        # 交集会静默毁掉门槛：本该命中的查询混进反例，地板被它自己顶上去
        raise SystemExit(
            f"FAIL {path} 的《{anime}》正例与反例有交集：{'、'.join(sorted(set(pos) & set(neg)))}")
    return pos, neg


def display_names(anime: str, path: Path = CHARACTERS) -> dict[str, str]:
    """booru 标签 → 显示名（名单里的第一个中文名）。"""
    db = json.loads(path.read_text(encoding="utf-8"))
    return {tag: (names[0] if names else tag)
            for tag, names in db.get(anime, {}).items() if not tag.startswith("_")}


# ---------------------------------------------------------------- 通道 1 推理


_TAGGER: dict = {}


def tagger(prof: dict | None = None):
    """(onnx session, 标签表)。第一次调用才加载——1.6G 模型不该在 import 时进内存。"""
    prof = prof or profile()
    if prof["name"] not in _TAGGER:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download

        onnx = hf_hub_download(prof["repo"], prof["onnx"])
        tags_csv = hf_hub_download(prof["repo"], prof["tags"])
        rows = list(csv.DictReader(open(tags_csv, encoding="utf-8")))
        # **只用 CPU。** CoreML 那条路 2026-08-03 实测跑不通：onnxruntime 把 2580 个节点
        # 切成 340 个 CoreML 分区，其中一个在运行时报
        # 「Unable to compute the prediction using a neural network model」直接失败。
        # CPU 0.24 s/张已经够用（一集 372 个镜头约 90 秒），不值得为它折腾。
        sess = ort.InferenceSession(onnx, providers=["CPUExecutionProvider"])
        _TAGGER[prof["name"]] = (sess, rows)
    return _TAGGER[prof["name"]]


def preprocess(img_path: Path, prof: dict) -> np.ndarray:
    """等比缩到框内 → 补边成正方形 → 按该模型的约定转张量。

    **补边颜色、通道序、取值范围都不能随手改。** 它们是训练时的预处理，
    改一个等于给模型喂没见过的分布——而分布偏了不会报错，只会分数普遍偏低，
    看起来像「这个模型对动漫不灵」。
    """
    from PIL import Image

    size = prof["size"]
    im = Image.open(img_path).convert("RGB")
    w, h = im.size
    s = min(size / w, size / h)
    im = im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), tuple(prof["pad"]))
    canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2))

    a = np.asarray(canvas, dtype=np.float32) * prof["scale"]
    if prof["channels"] == "bgr":
        a = a[:, :, ::-1]
    return a.transpose(2, 0, 1) if prof["layout"] == "NCHW" else a


def _assert_probabilities(probs: np.ndarray, prof: dict, out_name: str) -> None:
    """输出必须落在 [0, 1]，否则说明取到的是 logit，判定阈值全部失去意义。

    **这道断言换掉的是一个静默失败。** 拿 logit 当概率去比 0.5，
    结果是几乎所有标签都不达标（logit 多为负）——索引会安安静静地全空，
    而「一个角色都没认出来」看起来像「模型不行」，不像「取错了输出」。
    """
    lo, hi = float(probs.min()), float(probs.max())
    if lo < -1e-6 or hi > 1.0 + 1e-6:
        raise SystemExit(
            f"FAIL {prof['repo']} 的输出 `{out_name}` 落在 [{lo:.3f}, {hi:.3f}]，不是概率。\n"
            f"     判定阈值是按概率定的，拿 logit 去比会静默出错。\n"
            f"     检查 TAGGERS[{prof['name']!r}]['output'] 是不是该指向已过 sigmoid 的那个输出")


def tag(files: list[Path], batch: int = 1, prof: dict | None = None,
        progress=None) -> list[dict[str, dict[str, float]]]:
    """一批图 → 每张的 {"char": {标签: 分数}, "gen": {标签: 分数}}，只留 >= KEEP_THRESHOLD。

    取哪个输出由 `TAGGERS` 表按名字指定，**并且当场断言它确实是概率**——
    见表上方那段注释记的实测：取错输出不会崩，只会让每个标签都「检出」而分数看着正常。
    """
    # **默认不批。** 2026-08-03 实测 20 张：batch=1 0.245 s/张、batch=4 0.272、batch=8 0.275。
    # CPU 上单张已经把核占满了，攒批只是多占内存。
    prof = prof or profile()
    sess, rows = tagger(prof)
    in_name = sess.get_inputs()[0].name
    out_name = prof["output"]
    names = [r["name"] for r in rows]
    cats = [r["category"] for r in rows]

    out: list[dict[str, dict[str, float]]] = []
    for i in range(0, len(files), batch):
        x = np.stack([preprocess(p, prof) for p in files[i:i + batch]])
        probs = sess.run([out_name], {in_name: x})[0]
        if i == 0:
            _assert_probabilities(probs, prof, out_name)
        for row in probs:
            rec: dict[str, dict[str, float]] = {"char": {}, "gen": {}}
            for j in np.nonzero(row >= KEEP_THRESHOLD)[0]:
                bucket = ("char" if cats[j] == TAGGER_CATEGORY_CHARACTER
                          else "gen" if cats[j] == TAGGER_CATEGORY_GENERAL else None)
                if bucket:
                    rec[bucket][names[j]] = round(float(row[j]), 4)
            out.append(rec)
        if progress:
            progress(min(i + batch, len(files)), len(files))
    return out


# ---------------------------------------------------------------- 通道 1 索引


def presence_path(anime: str, key: str, out_dir: Path = VINDEX_DIR) -> Path:
    return out_dir / f"{anime}_{key}.presence.json"


def write_presence(anime: str, key: str, rows: list[dict], producer: str,
                   extra: dict, out_dir: Path = VINDEX_DIR) -> Path:
    """落一集的角色在场索引。**两个 producer 共用这一个写入口，格式只有一份。**

    `rows` 每行 `{"i": 镜头号, "char": {角色: 分数}, "gen": {通用标签: 分数}}`，
    下标即镜头号。

    `extra` 里必须带 `decision_threshold`——**判定阈值随 producer 走**，
    加载时按它把分数折成布尔。写进文件而不是读配置，是因为文件里的分数是当时那个
    producer 的量纲，换 producer 之后拿新阈值去卡旧分数没有意义。
    """
    d = shots.load(anime, key)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = presence_path(anime, key, out_dir)
    dest.write_text(json.dumps({
        "meta": {
            "kind": "presence", "producer": producer,
            "anime": anime, "episode": key,
            "shots": _shots_fingerprint(d["meta"]),
            "built_at": date.today().isoformat(),
            **extra,
        },
        # **general 标签一并落盘但当前没有任何检索路径读它**——它是第 2 层探针
        # 万一不过时的备用料（booru 标签里有 rain / night / indoors），
        # 顺手存下来是因为推理这一步的钱已经花了，将来要用不必再跑几小时。
        # 它不是一条通道：没有路由指向它，也不许有。
        "shots": rows,
    }, ensure_ascii=False), encoding="utf-8")
    return dest


def build_presence(anime: str, key: str, out_dir: Path = VINDEX_DIR,
                   batch: int = 1, progress=None) -> int:
    """**tagger producer**：给一集的每个镜头打 booru 角色标签。

    这条路 2026-08-03 实测在春物上不够用（词表覆盖 3/12 或认不准，见 `TAGGERS` 上方
    那张表），默认 producer 是 `faces.py` 的人脸聚类。留着它有两个理由：
    换一部主要角色都在 booru 词表里的番时它便宜得多；以及它顺带产出 general 标签。
    """
    prof = profile()
    d = shots.load(anime, key)
    frames_dir = shots.FRAMES_DIR / f"{anime}_{key}"
    if not frames_dir.is_dir():
        raise SystemExit(
            f"FAIL 没有 {anime} {key} 的代表帧，先跑：\n"
            f"     python -m pipeline.shots frames {anime} {key}")

    files = [shots.frame_path(anime, key, s["i"]) for s in d["shots"]]
    missing = [f for f in files if not f.exists()]
    if missing:
        raise SystemExit(
            f"FAIL {key} 缺 {len(missing)} 张代表帧（第一张：{missing[0].name}）。"
            f"缺帧会让镜头与标签整体错位且不报错，不许继续")

    recs = tag(files, batch, prof, progress)
    write_presence(anime, key,
                   [{"i": s["i"], "char": r["char"], "gen": r["gen"]}
                    for s, r in zip(d["shots"], recs)],
                   producer="tagger", extra={
                       "tagger": prof["name"],
                       "model_id": prof["repo"], "file": prof["onnx"],
                       "revision": paths.model_revision(prof["repo"]),
                       "input_size": prof["size"],
                       "keep_threshold": KEEP_THRESHOLD,
                       "decision_threshold": prof["char_threshold"],
                   }, out_dir=out_dir)
    return len(recs)


def _shots_fingerprint(m: dict) -> dict:
    """镜头切分参数的指纹。切分变了，索引里的镜头号就不再指向同一段时间。"""
    return {"detector": m["detector"], "scene_threshold": m["scene_threshold"],
            "min_shot": m["min_shot"], "duration": m["duration"]}


@dataclass
class Presence:
    """一部番的角色在场索引。

    **对外是布尔（`present`）**，外加一个次级排序用的 `presence_score`——
    后者只在台词通道内、同一段落内做候选 tie-break 用，绝不跨通道、绝不跨段落
    比较（ADR-0004）。分数本身按 producer 分两种量纲（ccip 的 1−距离 0.95–0.99、
    tagger 的概率 0.5–1.0），**禁跨 producer 比绝对值**——producer 是谁由
    `load_presence` 时从文件元信息定死，整个 Presence 实例里只有一个 producer。
    """

    anime: str
    by_ep: dict[str, list[dict]]          # SxxEyy -> [{i, start, end, tags:set, scores:dict}]
    alias: dict[str, str]
    threshold: float

    def tag_of(self, name: str) -> str:
        t = self.alias.get(str(name).strip())
        if t is None:
            raise SystemExit(
                f"FAIL 角色名表里没有「{name}」。\n"
                f"     已登记：{'、'.join(sorted(set(self.alias) - set(self.alias.values())))}\n"
                f"     补进 {CHARACTERS} 的《{self.anime}》一节")
        return t

    def episodes(self) -> set[str]:
        return set(self.by_ep)

    def indexed(self) -> set[str]:
        """索引里**真的出现过**的角色标签。

        与「角色名表里有」是两回事：名表是这部番的花名册，而索引只装得下
        贴过名的簇。一个在名表里但没有任何簇贴给他的角色，过滤永远返回空——
        **那是配置没做完，不是这一段漏检**，两者必须区分开报，否则前者会
        伪装成后者，每一段都「过滤为空→退回」，看起来像检测不给力。
        """
        return {t for rows in self.by_ep.values() for s in rows for t in s["tags"]}

    def presence_score(self, season: int, episode: int, start: float, end: float,
                       name: str) -> float:
        """[start, end) 相交镜头里该角色的最大在场分；没检出 0.0。

        `present` 的实现全在这里（`>= threshold`），交集逻辑只留一份，
        两条路不可能再出现不一致。
        """
        eps = self.by_ep.get(f"S{season:02d}E{episode:02d}")
        if not eps:
            return 0.0
        t = self.tag_of(name)
        best = 0.0
        for s in eps:
            if s["start"] < end and start < s["end"]:
                best = max(best, s["scores"].get(t, 0.0))
        return best

    def present(self, season: int, episode: int, start: float, end: float,
                name: str) -> bool:
        """[start, end) 与之相交的镜头里，有没有哪个含这个角色。

        跨镜头是常态（一句台词能横跨两三个镜头），所以任一镜头命中即算命中。
        """
        return self.presence_score(season, episode, start, end, name) >= self.threshold


def presence_producer() -> str:
    p = paths.conf("visual.presence_producer", "ccip")
    if p not in ("ccip", "tagger"):
        raise SystemExit(f"FAIL visual.presence_producer = {p!r} 不认识，可选：ccip、tagger")
    return p


def load_presence(anime: str, out_dir: Path = VINDEX_DIR,
                  threshold: float | None = None) -> Presence:
    """加载一部番的角色在场索引，**元信息对不上就失败**。

    判定阈值默认取文件里记的那个（`decision_threshold`），因为**分数的量纲随
    producer 而不同**：tagger 存的是标签概率，ccip 存的是 1 − 角色距离。
    拿一种量纲的阈值去卡另一种，得到的仍然是一个能用的布尔值——只是它不对应任何东西。
    """
    want = presence_producer()
    by_ep: dict[str, list[dict]] = {}
    thr_used: float | None = None
    for p in sorted(out_dir.glob(f"{anime}_*.presence.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        m = _check_meta(d["meta"], p, kind="presence")
        if m.get("producer") != want:
            raise SystemExit(
                f"FAIL {p.name} 是 {m.get('producer')} 建的，当前配置要 {want}。\n"
                f"     两者的分数量纲不同，不可混用。改 visual.presence_producer，"
                f"或用当前 producer 重建索引")
        thr = m["decision_threshold"] if threshold is None else threshold
        if m.get("keep_threshold", thr) > thr:
            raise SystemExit(
                f"FAIL {p.name} 只存了分数 >= {m['keep_threshold']} 的标签，"
                f"答不了阈值 {thr} 的问题——低于落盘下限的那些标签根本不在文件里。\n"
                f"     要用更低的判定阈值必须重建索引")
        thr_used = thr
        key = m["episode"]
        sh = shots.load(anime, key)["shots"]
        # scores 与 tags 走同一条阈值线（>= thr 才进），所以「能排序」和
        # 「算在场」是同一批标签，语义零变化（ADR-0004）。scores 里的分是
        # 1−距离 / 概率，只用于同一段落内的 tie-break，不跨段落比较。
        by_ep[key] = [
            {"i": r["i"], "start": sh[r["i"]]["start"], "end": sh[r["i"]]["end"],
             "tags": {t for t, sc in r["char"].items() if sc >= thr},
             "scores": {t: sc for t, sc in r["char"].items() if sc >= thr}}
            for r in d["shots"]]
    if not by_ep:
        raise SystemExit(
            f"FAIL {out_dir} 下没有《{anime}》的角色在场索引。\n"
            f"     producer={want} 的建法见 `python -m pipeline.faces --help`"
            if want == "ccip" else
            f"FAIL 先跑 `python -m pipeline.vindex presence {anime} <集号>`")
    return Presence(anime, by_ep, alias_map(anime), thr_used or 0.0)


def _check_meta(m: dict, path: Path, kind: str, model_id: str | None = None) -> dict:
    """索引元信息硬校验。对不上直接失败，不许继续（ADR-0003）。

    **理由是这类不一致不会自己暴露。** 维度不同会崩，那算运气好；
    同维度换模型不会崩——余弦照样算得出来，分数照样落在看起来正常的区间，
    照样过阈值、照样返回 Top-K、照样渲染出片。
    """
    if m.get("kind") != kind:
        raise SystemExit(f"FAIL {path.name} 不是 {kind} 索引（kind={m.get('kind')}）")
    if model_id is not None:
        if m.get("model_id") != model_id:
            raise SystemExit(
                f"FAIL {path.name} 建索引时用的是 {m.get('model_id')}，当前配置是 {model_id}。\n"
                f"     两个模型的输出不可比，重建索引再用")
        rev = paths.model_revision(model_id)
        if rev and m.get("revision") and m["revision"] != rev:
            raise SystemExit(
                f"FAIL {path.name} 建索引时的模型版本是 {m['revision'][:12]}，"
                f"本机缓存的是 {rev[:12]}。\n"
                f"     换 backbone 常常不改维度，分数照样算得出来，所以这里必须硬失败。重建索引")
    _check_meta_shots(m, path)
    return m


def _check_meta_shots(m: dict, path: Path) -> None:
    """镜头切分参数必须与当前镜头表一致，否则索引里的镜头号已经指向别的时间段。"""
    key = m.get("episode")
    if not key:
        return
    cur = shots.load(m["anime"], key)["meta"]
    if m.get("shots") != _shots_fingerprint(cur):
        raise SystemExit(
            f"FAIL {path.name} 的镜头切分参数与当前镜头表不一致，镜头号已经错位。\n"
            f"     文件 {m.get('shots')}\n     当前 {_shots_fingerprint(cur)}\n"
            f"     先 `shots rebuild` 再重建本索引")


# ---------------------------------------------------------------- 通道 2


_SCENE = None


def scene_model():
    global _SCENE
    if _SCENE is None:
        import torch
        from transformers import ChineseCLIPModel, ChineseCLIPProcessor

        model = ChineseCLIPModel.from_pretrained(SCENE_REPO).eval()
        proc = ChineseCLIPProcessor.from_pretrained(SCENE_REPO)
        _SCENE = (model, proc, torch)
    return _SCENE


def _features(out, model, torch):
    """取投影后的嵌入，并校验维度。

    **transformers 5.12 的 `get_image_features` / `get_text_features` 返回的不是张量**，
    而是整个 `BaseModelOutputWithPooling`，投影后的嵌入被塞在 `pooler_output` 里
    （两个方法的 docstring 仍写着「返回张量」，与实现不符——所以两种都接住）。

    **维度必须当场校验。** 拿错张量的后果是静默的：`last_hidden_state` 取错一维
    照样是一堆浮点数，照样归一化得了、照样算得出余弦、照样能排 Top-K，
    只是它不对应任何东西。这与本模块开头那条「同维度换 backbone 不会崩」同源。
    """
    v = out if isinstance(out, torch.Tensor) else out.pooler_output
    want = model.config.projection_dim
    if v.ndim != 2 or v.shape[1] != want:
        raise SystemExit(
            f"FAIL 取到的嵌入形状是 {tuple(v.shape)}，期望 (N, {want})。\n"
            f"     多半是 transformers 换了 get_*_features 的返回结构，"
            f"     去 pipeline/vindex.py 的 `_features` 改取法")
    return v


def encode_images(files: list[Path], batch: int = 16) -> np.ndarray:
    from PIL import Image

    model, proc, torch = scene_model()
    out = []
    for i in range(0, len(files), batch):
        ims = [Image.open(f).convert("RGB") for f in files[i:i + batch]]
        x = proc(images=ims, return_tensors="pt")
        with torch.no_grad():
            v = _features(model.get_image_features(**x), model, torch)
        out.append((v / v.norm(dim=-1, keepdim=True)).cpu().numpy().astype(np.float32))
    return np.vstack(out)


def encode_query(text: str) -> np.ndarray:
    model, proc, torch = scene_model()
    x = proc(text=[text], padding=True, return_tensors="pt")
    with torch.no_grad():
        v = _features(model.get_text_features(**x), model, torch)
    return (v / v.norm(dim=-1, keepdim=True)).cpu().numpy().astype(np.float32)[0]


def scene_path(anime: str, key: str, out_dir: Path = VINDEX_DIR) -> Path:
    return out_dir / f"{anime}_{key}.scene.json"


def build_scene(anime: str, key: str, out_dir: Path = VINDEX_DIR) -> int:
    d = shots.load(anime, key)
    files = [shots.frame_path(anime, key, s["i"]) for s in d["shots"]]
    if not all(f.exists() for f in files):
        raise SystemExit(
            f"FAIL {anime} {key} 的代表帧不全，先跑 `python -m pipeline.shots frames {anime} {key}`")

    vecs = encode_images(files)
    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(out_dir / f"{anime}_{key}.scene.npy", vecs)

    # 人看的标签，**只用于显示**：`04-clips.json` 里画面通道的命中总得让人看懂是什么镜头。
    # 它不参与任何判断，也不参与排序。
    labels = _labels(anime, key, len(d["shots"]), out_dir)
    scene_path(anime, key, out_dir).write_text(json.dumps({
        "meta": {
            "kind": "scene",
            "anime": anime, "episode": key,
            "model_id": SCENE_REPO, "revision": paths.model_revision(SCENE_REPO),
            "dim": int(vecs.shape[1]), "normalize": "l2",
            "shots": _shots_fingerprint(d["meta"]),
            "built_at": date.today().isoformat(),
        },
        "shots": [{"i": s["i"], "start": s["start"], "end": s["end"],
                   "label": labels.get(s["i"], "")}
                  for s in d["shots"]],
    }, ensure_ascii=False), encoding="utf-8")
    return len(vecs)


def _labels(anime: str, key: str, n: int, out_dir: Path) -> dict[int, str]:
    """给人看的镜头说明：有 booru 通用标签就用它，没有就留空（由调用方补时间码）。

    **只有 tagger producer 会产出 general 标签**；默认的 ccip producer 只认角色，
    这里就返回空。这不是缺陷——说明是给人看的，时间码已经够定位了，
    为了让它更好看去多跑一遍 tagger 不值当（一集 90 秒 × 41 集）。
    """
    p = presence_path(anime, key, out_dir)
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for r in d["shots"]:
        top = sorted(r.get("gen", {}).items(), key=lambda kv: -kv[1])[:6]
        if top:
            out[r["i"]] = " ".join(t for t, _ in top)
    return out


@dataclass
class Shot:
    """画面通道的检索命中。**字段与 `subindex.Unit` 同形**，好让 `clips.candidate`、
    `size`、`_overlaps` 和整条渲染链路一行都不用改。"""

    anime: str
    season: int
    episode: int
    start: float
    end: float
    text: str


def load_scene(anime: str, out_dir: Path = VINDEX_DIR,
               episode: str | None = None) -> tuple[np.ndarray, list[Shot]]:
    """加载画面语义索引。给了 `episode` 就只加载那一集（探针用）。"""
    pattern = f"{anime}_{episode}.scene.json" if episode else f"{anime}_*.scene.json"
    vecs, units = [], []
    for p in sorted(out_dir.glob(pattern)):
        npy = p.with_suffix(".npy")
        if not npy.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        m = _check_meta(d["meta"], p, kind="scene", model_id=SCENE_REPO)
        v = np.load(npy)
        if v.shape[1] != m["dim"]:
            raise SystemExit(f"FAIL {npy.name} 的维度 {v.shape[1]} 与元信息 {m['dim']} 不符")
        season, episode = _parse_key(m["episode"])
        vecs.append(v)
        # 说明里**总是带时间码**：`04-clips.json` 交给人抽检时，画面通道的命中
        # 若只写「镜头」，人无从判断它是哪一处。通用标签有就附上，没有也不影响定位。
        units += [Shot(anime, season, episode, s["start"], s["end"],
                       f"镜头 {_fmt(s['start'])} {s.get('label', '')}".strip())
                  for s in d["shots"]]
    if not vecs:
        raise SystemExit(
            f"FAIL {out_dir} 下没有《{anime}》的画面语义索引，先跑 "
            f"`python -m pipeline.vindex scene {anime} <集号>`")
    return np.vstack(vecs), units


def _parse_key(key: str) -> tuple[int, int]:
    m = re.fullmatch(r"S(\d{2})E(\d{2})", key)
    if not m:
        raise SystemExit(f"FAIL 集号格式不对：{key}（应形如 S01E03）")
    return int(m.group(1)), int(m.group(2))


def search_scene(query: str, vecs: np.ndarray, units: list[Shot],
                 k: int = 24, season: int | None = None,
                 episode: int | None = None) -> list[tuple[float, Shot]]:
    """与 `subindex.search` 同构的集掩码（ADR-0004）；画面通道当前不可用，
    参数先摆好，启用时与台词通道一套降级链（`clips._ladder_scene`）。"""
    if (season is None) != (episode is None):
        raise SystemExit("FAIL search_scene 的 season/episode 必须成对给（或都不给）")
    q = encode_query(query)
    if season is not None:
        mask = [u.season == season and u.episode == episode for u in units]
        vecs = vecs[mask]
        units = [u for u, m in zip(units, mask) if m]
        if not units:
            return []
    scores = vecs @ q                      # 已归一化，点积即余弦
    top = np.argsort(-scores)[:k]
    return [(float(scores[i]), units[i]) for i in top]


# ---------------------------------------------------------------- 状态


def note_episodes(notes: Path) -> set[str]:
    """番剧笔记里**分集速查表**登记了哪几集，键统一成 SxxEyy（OVA 记作 E00）。

    判据取「表格第一列是集号的行」，与 CLAUDE.md「笔记集数 = 分集速查表行数」一致。
    **不要按全文出现的集号数**：正文里到处都在引用集号（「见 S2E02」），
    那样数出来永远偏大，而这条判据的全部意义就是发现**偏小**。
    """
    if not notes.exists():
        return set()
    out = set()
    for m in re.finditer(r"^\|\s*S(\d)\s*(?:E(\d{1,2})|(OVA))\s*\|",
                         notes.read_text(encoding="utf-8"), re.M | re.I):
        out.add(f"S{int(m.group(1)):02d}E{0 if m.group(3) else int(m.group(2)):02d}")
    return out


def scene_enabled(anime: str, path: Path = SCENES) -> bool:
    """画面语义通道对这部番算不算已验收。

    **判据是门槛标定了没有**，不是索引建了没有。理由是 ADR-0003 把这一层写成
    「探针过不去就不建」，而门槛 `no_match` 正是探针的产出——没有它，
    `clips` 那边写 `场景` 本来就会当场失败，这一层等于不存在。

    这条存在的意义是不让六条数字**永远红着**。一道永远失败的门禁和没有门禁
    效果一样，但它会让人学会忽略它——本项目在门禁上踩过的坑全是这个形状。
    第 2 层是有意不建的（春物实测：门槛立不住，见 ADR-0003），
    那它就该被如实报成「未启用」，而不是报成「缺 40 集」。
    """
    try:
        return scene_conf(anime, path).get("no_match") is not None
    except SystemExit:
        return False


def status(anime: str) -> dict[str, int]:
    """六条数字（ADR-0003 把 CLAUDE.md 的「三条数字相等」扩成六条）。

    **判据不能是「检索得到东西吗」，必须是「集数对得上吗」。** 前者永远为真：
    池子只有应有的七分之一时，检索照常返回 Top-K、分数照常在阈值以上、成片照常渲染出来。

    画面语义通道**未启用时不计入**（见 `scene_enabled`）。
    """
    from .ingest import load_sources
    from .subindex import INDEX_DIR

    notes = paths.DATA / "library" / "notes" / f"{anime}.md"
    n_notes = len(note_episodes(notes))
    out = {
        "片源": len(load_sources(anime)),
        "字幕索引": len(list(INDEX_DIR.glob(f"{anime}_*.npy"))),
        "笔记": n_notes,
        "镜头表": len(list(shots.SHOTS_DIR.glob(f"{anime}_*.json"))),
        "角色在场": len(list(VINDEX_DIR.glob(f"{anime}_*.presence.json"))),
    }
    n_scene = len(list(VINDEX_DIR.glob(f"{anime}_*.scene.npy")))
    # 未启用但盘上有残留 → 照样报出来，让人看见这份半吊子状态
    if scene_enabled(anime) or n_scene:
        out["画面语义"] = n_scene
    return out


def _fmt(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("presence", help="通道 1：给每个镜头打角色标签")
    p.add_argument("anime")
    p.add_argument("episode", nargs="+", help="SxxEyy，可多个")
    p.add_argument("--batch", type=int, default=1)

    s = sub.add_parser("scene", help="通道 2：给每个镜头算画面向量")
    s.add_argument("anime")
    s.add_argument("episode", nargs="+")

    q = sub.add_parser("search", help="画面语义检索")
    q.add_argument("query")
    q.add_argument("--anime", default=paths.conf("anime.default", "春物"))
    q.add_argument("-k", type=int, default=8)

    w = sub.add_parser("who", help="某段时间里有谁（查角色索引）")
    w.add_argument("anime")
    w.add_argument("episode")
    w.add_argument("--start", type=float, default=0.0)
    w.add_argument("--end", type=float, default=1e9)

    st = sub.add_parser("status", help="四条数字对不对得上")
    st.add_argument("--anime", default=paths.conf("anime.default", "春物"))

    a = ap.parse_args()
    paths.require_data()

    if a.cmd == "presence":
        # **这条命令只建 tagger producer 的索引。** ccip 那条路的流程不一样
        # （要先全季检测、聚类、人贴名，才谈得上落索引），入口在 pipeline.faces。
        # 配置指着 ccip 却跑这条命令，多半是记混了，直接拦住而不是建出一份
        # 加载时才报「producer 不符」的索引。
        if presence_producer() != "tagger":
            raise SystemExit(
                f"FAIL 当前 visual.presence_producer = {presence_producer()}，"
                f"这条命令建的是 tagger 的索引。\n"
                f"     ccip 那条路走：python -m pipeline.faces --help")
        for key in a.episode:
            n = build_presence(a.anime, key, batch=a.batch)
            print(f"OK {key} {n} 个镜头 → {presence_path(a.anime, key).name}", flush=True)
        return 0

    if a.cmd == "scene":
        for key in a.episode:
            n = build_scene(a.anime, key)
            print(f"OK {key} {n} 个镜头 → {scene_path(a.anime, key).name}", flush=True)
        return 0

    if a.cmd == "search":
        vecs, units = load_scene(a.anime)
        for score, u in search_scene(a.query, vecs, units, a.k):
            print(f"{score:.3f}  S{u.season:02d}E{u.episode:02d} "
                  f"{_fmt(u.start)}-{_fmt(u.end)}  {u.text}")
        return 0

    if a.cmd == "who":
        pres = load_presence(a.anime)
        names = display_names(a.anime)
        season, episode = _parse_key(a.episode)
        rows = pres.by_ep.get(a.episode, [])
        for s in rows:
            if s["start"] < a.end and a.start < s["end"]:
                who = "、".join(names.get(t, t) for t in sorted(s["tags"]))
                if who:
                    print(f"  {_fmt(s['start'])}-{_fmt(s['end'])}  {who}")
        return 0

    st_ = status(a.anime)
    for k, v in st_.items():
        print(f"  {k:<8} {v}")
    if "画面语义" not in st_:
        print("  画面语义   未启用（第 2 层未验收，见 ADR-0003；写 `场景` 会当场报错）")
    vals = list(st_.values())
    if len(set(vals)) == 1:
        print(f"OK {len(vals)} 条数字一致：{vals[0]}")
        return 0
    print("-" * 40)
    print("★ 数字对不上。**素材边界缩水是唯一一种「越用越不觉得有问题」的失败**——"
          "检索照常返回 Top-K，分数照常在阈值以上，成片照常渲染出来，全程不报错。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
