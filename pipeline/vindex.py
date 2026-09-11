"""视觉索引：两个并列通道，检索单元是镜头（ADR-0003）。

    python -m pipeline.vindex presence 春物 S01E01     # 通道 1：画面里有谁
    python -m pipeline.vindex captions 罪恶王冠 S01E01  # 通道 2：云端 VLM 逐镜头打意象
    python -m pipeline.vindex embed    罪恶王冠        # 通道 2：captions → bge-m3 向量库
    python -m pipeline.vindex search "夜晚空无一人的天台" --anime 春物
    python -m pipeline.vindex status --anime 春物

字幕索引答的是「谁说了什么」，这里补上「画面里有谁」和「画面是什么」。
**两个通道之间是路由，不是分数融合**——每一段按需要走哪一条，绝不加权求和
（接法见 `clips.py`）。

| 通道 | 答什么 | 产出 | 排序 |
|---|---|---|---|
| 1 角色在场 | 这个镜头里有没有 X | 布尔 + 在场分 | **只在台词通道内、同一段落带内做 tie-break**（ADR-0004） |
| 2 画面语义 | 这个镜头像不像「夜晚的天台」 | 文-文余弦分数 | 只在本通道内排 |

**通道 2 在 v2 换了内核**（ADR-0015）：CLIP 图文余弦（已证死）→ Qwen3-VL 意象打标
（`captions`，云端）→ bge-m3 文本向量（`embed`，本地）→ 文-文检索。
门槛 `no_match` 仍逐番标定、仍存在 `config/scenes.json`，标定法换成
`vprobe scene` 的零假设组（与 subindex 的 `NO_MATCH=0.45` 同一套办法）。
**标定之前写了 `场景` 仍当场报错**（封印机制 = config 里缺 `no_match`）。

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
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import paths, shots          # paths 必须最先：把 HF_HOME 钉到 SSD

import numpy as np

VINDEX_DIR = paths.DATA / "library" / "vindex"
CHARACTERS = paths.CONFIG / "characters.json"
SCENES = paths.CONFIG / "scenes.json"


def _atomic_write(dest: Path, data: str) -> None:
    """统一走 paths.atomic_write（理由见 subindex._atomic_write）。"""
    paths.atomic_write(dest, data)


def _atomic_save(dest: Path, vecs: np.ndarray) -> None:
    tmp = dest.with_name(dest.name + ".tmp")
    with open(tmp, "wb") as f:
        np.save(f, vecs)
    os.replace(tmp, dest)

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

    model_id/revision 必须与当前配置一致（ADR-0003「加载时硬校验」）。
    2026-08-16 审计 2-2 之前这里漏了：producer 检查拦不住 camie→wd 这种换
    tagger（producer 字段都还是 "tagger"），两种量纲的概率分静默混用。
    """
    want = presence_producer()
    if want == "ccip":
        from .faces import CCIP_MODEL, CCIP_REPO    # 延迟 import，faces 又依赖本模块
        want_model = CCIP_REPO + "/" + CCIP_MODEL
        # ccip 写侧记的是仓库级 revision（faces.build_presence），model_id 却是
        # 仓库名/模型名的组合——按组合名查本地缓存查不到，得显式给
        want_rev = paths.model_revision(CCIP_REPO)
    else:
        prof = profile()
        want_model = prof["repo"]
        want_rev = paths.model_revision(prof["repo"])
    by_ep: dict[str, list[dict]] = {}
    thr_used: float | None = None
    rec_thr: float | None = None     # 各集文件自带的判定阈值，跨文件必须一致
    for p in sorted(out_dir.glob(f"{anime}_*.presence.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        m = _check_meta(d["meta"], p, kind="presence", model_id=want_model,
                        revision=want_rev)
        if m.get("producer") != want:
            raise SystemExit(
                f"FAIL {p.name} 是 {m.get('producer')} 建的，当前配置要 {want}。\n"
                f"     两者的分数量纲不同，不可混用。改 visual.presence_producer，"
                f"或用当前 producer 重建索引")
        if rec_thr is None:
            rec_thr = m["decision_threshold"]
        elif m["decision_threshold"] != rec_thr:
            # 阈值是量纲的一部分：一半文件按 0.5 折布尔、一半按 0.85 折，
            # 同一个 Presence 里「在场」的含义就随集漂移，且不报错
            raise SystemExit(
                f"FAIL {p.name} 的判定阈值 {m['decision_threshold']} 与其他集的"
                f" {rec_thr} 不一致。\n"
                f"     同一部番的索引必须用同一套参数建，混用的部分重建：\n"
                f"     python -m pipeline.faces presence {anime}")
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


def _check_meta(m: dict, path: Path, kind: str, model_id: str | None = None,
                revision: str | None = None) -> dict:
    """索引元信息硬校验。对不上直接失败，不许继续（ADR-0003）。

    **理由是这类不一致不会自己暴露。** 维度不同会崩，那算运气好；
    同维度换模型不会崩——余弦照样算得出来，分数照样落在看起来正常的区间，
    照样过阈值、照样返回 Top-K、照样渲染出片。

    `revision` 显式给时用它比（写侧记的可能是仓库级 revision，而 model_id 是
    组合名，自动查缓存查不到）；不给时按 model_id 查本地缓存。
    """
    if m.get("kind") != kind:
        raise SystemExit(f"FAIL {path.name} 不是 {kind} 索引（kind={m.get('kind')}）")
    if model_id is not None:
        if m.get("model_id") != model_id:
            raise SystemExit(
                f"FAIL {path.name} 建索引时用的是 {m.get('model_id')}，当前配置是 {model_id}。\n"
                f"     两个模型的输出不可比，重建索引再用")
        rev = revision if revision is not None else paths.model_revision(model_id)
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
    _atomic_save(out_dir / f"{anime}_{key}.scene.npy", vecs)

    # 人看的标签，**只用于显示**：`04-clips.json` 里画面通道的命中总得让人看懂是什么镜头。
    # 它不参与任何判断，也不参与排序。
    labels = _labels(anime, key, len(d["shots"]), out_dir)
    _atomic_write(scene_path(anime, key, out_dir), json.dumps({
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
    }, ensure_ascii=False))
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
               episode: str | None = None,
               require_full: bool = True) -> tuple[np.ndarray, list[Shot]]:
    """加载画面语义索引（v2：captions 的 bge-m3 向量库，ADR-0015）。

    **`anime` 的真实语义是「素材池」**（2026-09-11 裁决）：它既是一部番（`S01E01`），
    也可以是一池特典素材（`SP05`）——参数名不改，改的是理解（在注释里说清）。

    给了 `episode` 就只加载那一集（探针用，允许索引只建了一部分）；
    **不给就要求池里的每一集都在**——检索池缩水是本项目最会骗人的一种失败：
    池子只有应有的七分之一时，检索照常返回 Top-K、分数照常在阈值以上、成片照常渲染。
    `require_full=False` 只给探针开：量噪声地板时「索引盖了多少就量多少」是可接受的，
    拿着半份索引去检索排片不行——这两件事不能共用一个口子（不报错的那件才要卡）。

    只认 `producer=captions` 的产物：旧 CLIP 索引（若盘上还有）在这里按 model_id 硬拒。
    """
    pattern = f"{anime}_{episode}.scene.json" if episode else f"{anime}_*.scene.json"
    vecs, units, keys = [], [], []
    for p in sorted(out_dir.glob(pattern)):
        npy = p.with_suffix(".npy")
        if not npy.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        m = _check_meta(d["meta"], p, kind="scene", model_id=EMBED_REPO)
        if m.get("producer") != "captions":
            raise SystemExit(
                f"FAIL {p.name} 的 producer 是 {m.get('producer')!r}，当前画面通道要 captions。\n"
                f"     旧的 CLIP 图文索引不可用（ADR-0015）：重建走 "
                f"`vindex embed {anime}`")
        v = np.load(npy)
        if v.shape[1] != m["dim"] or m["dim"] != EMBED_DIM:
            raise SystemExit(
                f"FAIL {npy.name} 的维度 {v.shape[1]} / 元信息 {m['dim']} / 当前 "
                f"{EMBED_DIM} 三者不一致")
        season, ep = _parse_key(m["episode"])
        vecs.append(v)
        keys.append(m["episode"])
        # 说明里**总是带时间码**：`04-clips.json` 交给人抽检时，画面通道的命中
        # 若只写「镜头」，人无从判断它是哪一处。caption 本身附在后面，
        # 也是人可读审计红利（ADR-0015 决定 5）。
        units += [Shot(anime, season, ep, s["start"], s["end"],
                       f"镜头 {_fmt(s['start'])} {s.get('label', '')}".strip())
                  for s in d["shots"]]
    if not vecs:
        raise SystemExit(
            f"FAIL {out_dir} 下没有《{anime}》的画面语义索引。v2 的建法：\n"
            f"     python -m pipeline.vindex captions {anime} <集号>   # 云端 VLM 打标\n"
            f"     python -m pipeline.vindex embed {anime}             # bge-m3 建库")
    if episode is None and require_full:
        _require_full_index(anime, set(keys), "画面语义索引")
    return np.vstack(vecs), units


def caption_keys(anime: str, out_dir: Path = VINDEX_DIR) -> list[str]:
    """这个池里已经打过标的集键（`SP01` / `S01E01`…），按字典序。

    **探针要跨集抽样（池级标定与 30 镜头抽验），而抽样单位是「池」不是「集」**
    （2026-09-11 裁决：M2b 主要服务对象是素材池）。
    """
    tail = len(".captions.json")
    return sorted(p.name[len(anime) + 1:-tail]
                  for p in out_dir.glob(f"{anime}_*.captions.json"))


def _require_full_index(anime: str, have: set[str], what: str) -> None:
    """索引覆盖的集必须与镜头表一致，否则拒绝加载（ADR-0003「集数对得上」）。

    **这是检索池缩水的当场失败。** 判据不是「找得到东西吗」（那个永远为真），
    而是「集数对得上吗」。探针要单集时显式给 `episode`，不走这条。
    """
    want = {p.stem[len(anime) + 1:] for p in shots.SHOTS_DIR.glob(f"{anime}_*.json")}
    miss = sorted(want - have)
    extra = sorted(have - want)
    if miss or extra:
        def _list(xs: list[str]) -> str:
            return "、".join(xs[:6]) + ("…" if len(xs) > 6 else "")
        raise SystemExit(
            f"FAIL 《{anime}》的{what}只覆盖 {len(have)}/{len(want)} 集"
            + (f"，缺：{_list(miss)}" if miss else "")
            + (f"，多（镜头表里没有）：{_list(extra)}" if extra else "")
            + f"\n     索引池缩水是静默失败：检索照常返回 Top-K、分数照常过阈值、"
              f"成片照常渲染。\n     Phase 0 一次做完全集（ADR-0003）")


def _parse_key(key: str) -> tuple[int | None, int]:
    """集键 → (季, 集)。**`SPxx` 的季是 None**（ADR-0010 决策二：特典集不属于某一季）。

    **`anime` 与集键的真实语义是「素材池 + 池里的一个单元」**（2026-09-11 裁决）：
    一部番（`S01E03`）与一池 Live/MV 素材（`SP05`）走同一个键系，
    所以这里两种都认——看不懂 SP 的后果是池索引根本加载不了（embed 跑完才发现）。
    """
    m = re.fullmatch(r"S(\d{2})E(\d{2})", key)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"SP(\d{2})", key)
    if m:
        return None, int(m.group(1))
    raise SystemExit(f"FAIL 集键格式不对：{key}（应形如 S01E03 或 SP05）")


def key_of(u: "Shot") -> str:
    """检索命中的集键：番剧 `SxxEyy`、素材池 `SPxx`。

    走 `shots._key` 而不是在这里再写一遍格式——两处各写一份的下场是
    某天其中一处改了（比如 SP 改成三位数），另一处静默按旧格式找文件。
    """
    return shots._key(u.season, u.episode)


def search_scene(query: str, vecs: np.ndarray, units: list[Shot],
                 k: int = 24, season: int | None = None,
                 episode: int | None = None) -> list[tuple[float, Shot]]:
    """与 `subindex.search` 同构的集掩码（ADR-0004）；查询文本走 bge-m3（与索引同空间）。

    排序轴是文-文余弦，卡门槛的是 `clips.scene_no_match(番)`——**它只在本通道内
    排序、只与本通道的门槛比**（S10：台词段的 0.45 与这里的数长得像纯属巧合）。
    """
    if (season is None) != (episode is None):
        raise SystemExit("FAIL search_scene 的 season/episode 必须成对给（或都不给）")
    q = encode_text_query(query)
    if season is not None:
        mask = [u.season == season and u.episode == episode for u in units]
        vecs = vecs[mask]
        units = [u for u, m in zip(units, mask) if m]
        if not units:
            return []
    scores = vecs @ q                      # 已归一化，点积即余弦
    top = np.argsort(-scores)[:k]
    return [(float(scores[i]), units[i]) for i in top]


# ---------------------------------------------------------------- 通道 2（v2，ADR-0015）
#
# **换的是机制，不是模型。** 第 2 层当年死于「CLIP 图文余弦没有绝对含义」
# （ADR-0003 待实测 #4：噪声地板随反例条数无上界爬升、反例 z 分高过 8/10 条正例、
# 三种门槛定法全败）。v2 把「算不算命中」从跨模态余弦手里收回来：
#
#   镜头 → Qwen3-VL 生成 ≤30 字中文意象 → bge-m3 文本向量 → 文-文余弦
#
# VLM 只负责把画面翻译成文本；「算不算命中」交回给本项目验证过的文-文标定法。
# 职责分离是这一层的核心（也是它和 CLIP 路子最根本的区别）。
#
# **上面那截 CLIP 代码（`scene_model`/`encode_images`/`encode_query`/`build_scene`
# /`vindex scene`）本轮不删**——删除是红线动作，按 ADR-0015 由人在 captions 索引
# 验收后拍板。但它已经是死路径：`load_scene` 只认 captions 索引（model_id=bge-m3），
# `vindex scene` 建出来的 CLIP 产物加载时会被硬拒。这一点是故意的，不是遗漏。

CAPTIONS_REPO = "Qwen/Qwen3-VL-8B-Instruct"
EMBED_REPO = "BAAI/bge-m3"           # ADR-0015 指定的 captions 向量模型
EMBED_DIM = 1024                     # bge-m3 密集向量维度；换模型必须连它一起改

CAPTION_PROMPT_VERSION = "v1"        # **改 prompt 等于换模型**：版本不一致加载硬失败
CAPTION_MAX_CHARS = 30               # ADR-0015：≤30 字结构化意象
CAPTION_MAX_NEW_TOKENS = 64          # 30 字 + 余量；限长输出是成本杠杆（ADR-0003）
CAPTION_TEMPERATURES = (0.2, 0.0)    # 首次 / 降温重试一次（ADR-0015）
CAPTION_BATCH = 4                    # 一次前向几个镜头；OOM 减半重试一次

CAPTION_SYSTEM = (
    "你是动画分镜分析师。用不超过 30 个汉字描述这个镜头。\n"
    "必须覆盖：时间/空间（场景）、人物动作、光影色调、情绪氛围。\n"
    "禁止：评价性词汇（“精美”“经典”）、剧情推测、超出画面的信息。\n"
    "格式：直接输出描述句，不加任何前缀。")

# 禁词表只装 ADR-0015 明令的两类：评价词与剧情推测。
# **不装情绪词也不装罕见词**——「情绪氛围」是要求里必须覆盖的一维，
# 「孤独」「压抑」「死寂」是好描述。误伤会把合格 caption 记成 failed，
# 而 failed 是对账第七条上的一个洞，不是免费的重试。
CAPTION_BANNED = (
    "精美", "经典", "震撼", "精彩", "出色", "完美", "杰作", "优秀", "惊艳",
    "唯美", "绝美", "壮丽", "感人", "史诗", "名场面", "神来之笔",
    "似乎", "仿佛", "大概", "也许", "可能", "意味着", "象征", "暗示", "预示",
)

# 单镜头耗时的估值。**唯一的用途是预算闸**（ADR-0015 判断 6）：
# 真实值由 M2b 批次 1 实测回填，回填前不要把任何决策建在它上面。
CAPTION_SECONDS_PER_SHOT = 1.5


def caption_chars(text: str) -> int:
    """caption 的字数：只数汉字/字母/数字（`str.isalnum`），标点与空格不计。

    ADR-0015 写的是「不超过 30 个汉字」，所以量的是**字**不是字符——
    量字符会把「。，、」算进去，把一条 29 字的合格描述判成超长。
    """
    return sum(1 for c in text if c.isalnum())


def clean_caption(text: str) -> str:
    """去掉模型爱加的前缀与包裹引号。**只做这一件确定性的事，不改写内容。**"""
    t = re.sub(r"^\s*(描述|意象|画面|镜头)\s*[:：]\s*", "", text.strip())
    return t.strip().strip('"\'“”「」『』').strip()


def check_caption(text: str) -> str | None:
    """不合格的原因；合格返回 None。**纯函数**：重试与对账都按它判。"""
    t = clean_caption(text)
    n = caption_chars(t)
    # 字数口径是唯一口径：只剩标点的输出就是空输出（不是「短」而是「没有描述」）
    if n == 0:
        return "空输出"
    if n > CAPTION_MAX_CHARS:
        return f"超长 {n} 字（>{CAPTION_MAX_CHARS}）"
    hit = [w for w in CAPTION_BANNED if w in t]
    if hit:
        return f"含禁词「{hit[0]}」"
    return None


def is_oom(exc: BaseException) -> bool:
    """显存不足判定（纯函数）。

    torch 的 OOM 是 RuntimeError 的子类，但换后端/换版本的类名会变，
    所以两条都认：类名是 OutOfMemoryError，或消息里有 out of memory。
    """
    return (type(exc).__name__ == "OutOfMemoryError"
            or "out of memory" in str(exc).lower())


def caption_input_spec() -> dict:
    """caption 的输入规格：帧数与采样位点。**改了它等于换了索引的输入**，
    所以写进 meta 并在加载时硬校验（与 model_id / prompt_version 同一层级）。"""
    return {"long_shot": shots.CAPTION_LONG_SHOT,
            "points": list(shots.CAPTION_POINTS),
            "width": shots.CAPTION_FRAME_W}


def captions_path(anime: str, key: str, out_dir: Path = VINDEX_DIR) -> Path:
    return out_dir / f"{anime}_{key}.captions.json"


def caption_model_ref() -> str:
    """caption 模型的加载目标：**数据盘上的平铺目录优先，否则 HF repo id**。

    云端模型是平铺下载在数据盘上的（`config/cloud.json` 的 `remote_models` +
    `models[repo].dir` → `/root/autodl-tmp/models/Qwen/Qwen3-VL-8B-Instruct/`），
    不是 HF hub 缓存布局，所以 `from_pretrained("Qwen/Qwen3-VL-8B-Instruct")` 在云端
    找不到缓存**会去联网重下 17G**，而且写进 `paths.HF_HOME`——即仓库内 `data/models`，
    在系统盘上。这正是本项目「静默往内置盘写几个 G」那条坑（paths.py 的 HF_HOME 注释）。
    所以：配置里记了本地目录就用它（与 tts.py `_resolve_model` 同一条约定）。
    """
    cf = json.loads((paths.CONFIG / "cloud.json").read_text(encoding="utf-8"))
    entry = (cf.get("models") or {}).get(CAPTIONS_REPO) or {}
    root = cf.get("remote_models")
    if root and entry.get("dir"):
        p = Path(root) / entry["dir"]
        if p.is_dir():
            return str(p)
    return CAPTIONS_REPO


def model_fingerprint(ref: str | Path) -> str | None:
    """模型目录的指纹：`config.json` + 权重索引的 sha256 前 16 位。

    **为什么不能只写 repo id**：云端模型是平铺目录，没有 HF hub 的 `refs/main`，
    `paths.model_revision()` 在那里返回 None——一份没有版本的索引回答不了
    「模型换没换」。指纹覆盖权重清单（换了模型清单必换），不必哈希 17G 权重
    （读一遍要几分钟，而这一步每次加载都要跑）。
    """
    d = Path(ref)
    parts = [d / n for n in ("config.json", "model.safetensors.index.json")]
    parts = [p for p in parts if p.is_file()]
    if not parts:
        return None
    h = hashlib.sha256()
    for p in parts:
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _check_model_identity(m: dict, path: Path) -> None:
    """模型身份硬校验：`model_id` 必对，版本与指纹各自同类才比（ADR-0003）。

    两类标识分开报：commit sha（hub 缓存）与目录指纹（平铺下载），
    拿一类去比另一类的值会把「同一个模型」误判成不一致。
    """
    if m.get("model_id") != CAPTIONS_REPO:
        raise SystemExit(
            f"FAIL {path.name} 打标用的是 {m.get('model_id')}，当前是 {CAPTIONS_REPO}。\n"
            f"     描述口径不同，不可混用（ADR-0015）：重建 captions")
    rev, have_rev = m.get("revision"), paths.model_revision(CAPTIONS_REPO)
    if rev and have_rev and rev != have_rev:
        raise SystemExit(
            f"FAIL {path.name} 打标时的模型版本是 {str(rev)[:12]}，"
            f"本机缓存的是 {str(have_rev)[:12]}。\n     重建 captions")
    fp, have_fp = m.get("model_fingerprint"), model_fingerprint(caption_model_ref())
    if fp and have_fp and fp != have_fp:
        raise SystemExit(
            f"FAIL {path.name} 打标时的模型指纹是 {fp}，当前模型目录是 {have_fp}。\n"
            f"     权重清单变了（换了模型或换了精度），描述不可比：重建 captions")


def caption_meta(anime: str, key: str, m: dict) -> dict:
    """captions 的自描述元信息（ADR-0003 索引自描述标准的延伸）。"""
    return {
        "kind": "captions", "anime": anime, "episode": key,
        "model_id": CAPTIONS_REPO, "revision": paths.model_revision(CAPTIONS_REPO),
        "model_fingerprint": model_fingerprint(caption_model_ref()),
        "prompt_version": CAPTION_PROMPT_VERSION,
        "frames": caption_input_spec(), "max_chars": CAPTION_MAX_CHARS,
        "shots": _shots_fingerprint(m),
        "built_at": date.today().isoformat(),
    }


def check_caption_meta(m: dict, path: Path) -> dict:
    """captions 元信息硬校验：模型 / revision / prompt 版本 / 输入规格 / 镜头切分。

    `prompt_version` 是 captions 新增的自描述维度（ADR-0015）：**改 prompt 等于换模型**。
    版本对不上时不许混用——旧 captions 是另一套指令的产物，混进同一个索引里，
    检索照样返回 Top-K、分数照样落在正常区间，只是描述口径已经悄悄漂了一半。
    """
    kind = m.get("kind")
    if kind != "captions":
        raise SystemExit(f"FAIL {path.name} 不是 captions 索引（kind={kind}）")
    _check_model_identity(m, path)
    if m.get("prompt_version") != CAPTION_PROMPT_VERSION:
        raise SystemExit(
            f"FAIL {path.name} 的 prompt 版本是 {m.get('prompt_version')}，"
            f"当前是 {CAPTION_PROMPT_VERSION}。\n"
            f"     改 prompt 等于换模型：旧描述与新描述不可比。重建 captions")
    if m.get("frames") != caption_input_spec():
        raise SystemExit(
            f"FAIL {path.name} 的取样规格是 {m.get('frames')}，当前是 {caption_input_spec()}。\n"
            f"     帧数与位点变了，这批 caption 不再是同一种输入下的产物。重建 captions")
    _check_meta_shots(m, path)
    return m


def pending_shots(rows: list[dict]) -> int:
    """还没有最终结论的行数（既非 ok 也非 failed）。断点续跑靠它。"""
    return sum(1 for r in rows if r.get("status") not in ("ok", "failed"))


def load_captions(anime: str, key: str,
                  out_dir: Path = VINDEX_DIR) -> tuple[dict, list[dict]]:
    """读一集的 captions，并核对它与镜头表逐行对齐。

    **逐行对齐是这里唯一的硬判据。** 少了/多了/错了镜头号，描述与镜头的对应
    就整体错位；余弦照样算、Top-K 照样返回、成片照样渲染，没有任何人报错。
    """
    p = captions_path(anime, key, out_dir)
    if not p.exists():
        raise SystemExit(
            f"FAIL 没有 {p}。意象打标是 Phase 0 的活（ADR-0015）：\n"
            f"     python -m pipeline.vindex captions {anime} {key}")
    d = json.loads(p.read_text(encoding="utf-8"))
    check_caption_meta(d["meta"], p)
    rows = d["captions"]
    sh = shots.load(anime, key)["shots"]
    if [r.get("shot") for r in rows] != [s["i"] for s in sh]:
        raise SystemExit(
            f"FAIL {p.name} 与镜头表逐行对不上（{len(rows)} 行 vs {len(sh)} 个镜头）。\n"
            f"     描述与镜头错位是最毒的一类静默失败：句子通顺、检索正常，\n"
            f"     只是写的不是那个镜头。重建：vindex captions {anime} {key}")
    return d["meta"], rows


_VLM = None


def vlm():
    """Qwen3-VL 处理器与模型。第一次调用才加载——8B 权重不该在 import 时进显存。

    **串行加载，峰值驻留 ≤ 1 个大模型**（ADR-0016）：这一个模块里同时只有一个
    大模型活着，TTS/ASR 不与之共存于同一次任务。
    """
    global _VLM
    if _VLM is None:
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        proc = AutoProcessor.from_pretrained(caption_model_ref())
        # 自回归生成必须左填充：右填时 batch 里短序列的生成从 padding 之后开始，
        # 截出来的新 token 就不是它自己的续写。这是批量推理最经典的静默错位。
        proc.tokenizer.padding_side = "left"
        model = AutoModelForImageTextToText.from_pretrained(
            caption_model_ref(), dtype=torch.float16).to("cuda").eval()
        _VLM = (proc, model, torch)
    return _VLM


def caption_user_text(n_frames: int) -> str:
    """用户轮的文字。**多帧必须告诉模型它们是同一个镜头**，否则它会当成几张
    不同的画面并列描述，产出「第一张…第二张…」这种不像 caption 的句子。"""
    if n_frames > 1:
        return f"同一个镜头的 {n_frames} 个代表帧，按时间先后给出。"
    return "镜头代表帧。"


def caption_messages(frames: list[Path]) -> list[dict]:
    """一个镜头的对话结构。**纯函数 + 结构是硬要求**：

    transformers 4.57 的 `ProcessorMixin.apply_chat_template` 会把
    `message["content"]` 当成**部件列表**逐个取 `content["type"]`，所以 system
    那一条的 content 不能写成裸字符串（云端实测：`TypeError: string indices must
    be integers, not 'str'`；而 tokenizer 自己的模板路径不挑——本地拿 tokenizer
    试会误以为没问题，这个坑只在 processor 上出现）。
    """
    content = [{"type": "image", "image": str(f)} for f in frames]
    content.append({"type": "text", "text": caption_user_text(len(frames))})
    return [{"role": "system", "content": [{"type": "text", "text": CAPTION_SYSTEM}]},
            {"role": "user", "content": content}]


def _generate(requests: list[list[Path]], temperature: float,
              seed: int) -> list[str]:
    """一批镜头 → 每个镜头一条原文输出。**请求数 ≠ 返回数当场失败**（不许静默错位）。"""
    proc, model, torch = vlm()
    convs = [caption_messages(frames) for frames in requests]
    inputs = proc.apply_chat_template(
        convs, add_generation_prompt=True, tokenize=True, return_dict=True,
        return_tensors="pt", padding=True).to(model.device)
    kwargs = ({"do_sample": True, "temperature": temperature} if temperature > 0
              else {"do_sample": False})
    # 固定种子：同一次批量组成下重跑能得到同一批输出（审计要的是可复查，不是随机性）
    torch.manual_seed(seed)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=CAPTION_MAX_NEW_TOKENS, **kwargs)
    texts = proc.batch_decode(out[:, inputs["input_ids"].shape[1]:],
                              skip_special_tokens=True)
    if len(texts) != len(requests):
        raise SystemExit(
            f"FAIL 一次前向返回 {len(texts)} 条，请求 {len(requests)} 个镜头。\n"
            f"     caption 与镜头错位不会报错，只会让描述写的不是那个镜头：这里硬失败")
    return texts


def _free_cuda() -> None:
    """OOM 之后把缓存块还给 CUDA，否则减半重试还是撞同一堵墙。"""
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:       # 无卡环境（本地单测）走到这里什么都不用做
        pass


def build_captions(anime: str, key: str, out_dir: Path = VINDEX_DIR,
                   batch: int = CAPTION_BATCH, limit: int | None = None,
                   progress=None) -> dict:
    """打标一集：逐镜头生成 ≤30 字意象，落 `<番>_<集>.captions.json`。

    **断点续跑靠「已有 caption 的镜头跳过」**（产物即状态）：文件里每行先按镜头号
    占好位（`status: pending`），跑完一批就把整份写回去。中断重跑只补没有 `ok` 的行
    ——与 `tts` 的单段增量复用同一条纪律。failed 行会被重试（它没有描述，
    不属于「已有 caption」）；S4 的「跳过不定罪」说的是不把它判成画面不合格，
    不是永不重试。

    `batch` 是「一次前向塞几个镜头」，显存不足时自动减半重试一次；减到 1 仍 OOM
    就诚实失败——8B VLM 掉到 CPU 上的耗时本身就是失败（架构方案四.4）。
    `limit` 是冒烟闸：只打前 N 个没打过的镜头，其余留 pending（**建库会拒绝 pending**，
    所以半份索引不会静默流到下游）。
    """
    d = shots.load(anime, key)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = captions_path(anime, key, out_dir)

    if dest.exists():
        # 元信息对不上就当场失败，不许「接着上次写」：那是两套口径混进一个文件
        old = json.loads(dest.read_text(encoding="utf-8"))
        check_caption_meta(old["meta"], dest)
        if len(old["captions"]) != len(d["shots"]):
            raise SystemExit(
                f"FAIL {dest.name} 的镜头数与当前镜头表不同（{len(old['captions'])} vs "
                f"{len(d['shots'])}）。\n     镜头表变过，旧 captions 指向的不是同一批镜头：删掉重建")
        rows = old["captions"]
        meta = old["meta"]
    else:
        meta = caption_meta(anime, key, d["meta"])
        rows = [{"shot": s["i"], "start": s["start"], "end": s["end"],
                 "caption": "", "status": "pending"} for s in d["shots"]]

    by_shot = {r["shot"]: r for r in rows}
    n_frames = {s["i"]: len(shots.caption_points(s["start"], s["end"]))
                for s in d["shots"]}
    todo = [r["shot"] for r in rows if r["status"] != "ok"]
    if limit is not None:
        todo = todo[:limit]

    # **缺帧当场失败。** 缺一张就整体错位一个镜头，而错位之后每条描述都通顺。
    # **帧目录从模块属性现取**（不靠 `caption_frame_path` 的默认参数）：默认参数在 def
    # 时就绑死了，测试换不掉它，而「帧从哪儿取」正是这一层最该被测到的那句
    # （同 clips.scene_no_match 显式传 vindex.SCENES 的理由）。
    def _frame(i: int, k: int) -> Path:
        return shots.caption_frame_path(anime, key, i, k, shots.FRAMES_DIR)

    need = [_frame(i, k) for i in todo for k in range(n_frames[i])]
    missing = [p for p in need if not p.exists()]
    if missing:
        raise SystemExit(
            f"FAIL {key} 缺 {len(missing)} 张 caption 帧（第一张：{missing[0].name}）。\n"
            f"     先跑：python -m pipeline.shots caption-frames {anime} {key}")

    def save() -> None:
        _atomic_write(dest, json.dumps({"meta": meta, "captions": rows},
                                       ensure_ascii=False, indent=1))

    if not todo:
        return {"shots": len(rows),
                "ok": sum(1 for r in rows if r["status"] == "ok"),
                "failed": sum(1 for r in rows if r["status"] == "failed"),
                "skipped": len(rows)}

    save()            # 先把占位行落盘：中途崩了也看得出「这一集还没打完」
    done = 0          # 本轮打完的条数，只用在失败信息里（总数从 rows 现数，续跑才算得准）
    i, n = 0, max(1, batch)
    while i < len(todo):
        chunk = todo[i:i + n]
        req = [[_frame(s, k) for k in range(n_frames[s])] for s in chunk]
        try:
            texts = _generate(req, CAPTION_TEMPERATURES[0], seed=chunk[0])
        except Exception as e:              # 分流要看清是 OOM 还是别的
            if is_oom(e) and n > 1:
                n = max(1, n // 2)
                print(f"WARN {key} 显存不足，batch 减半 → 每次 {n} 个镜头", flush=True)
                _free_cuda()
                continue
            raise SystemExit(
                f"FAIL {key} 从第 {chunk[0]} 号镜头起打标失败（batch={n}）：{e}\n"
                f"     已打完的 {done} 个镜头在 {dest.name} 里，重跑即续跑")
        for j, (s, raw) in enumerate(zip(chunk, texts)):
            r = by_shot[s]
            reason = check_caption(raw)
            tries = 1
            if reason:                      # 降温重试一次，只重跑这一条
                try:
                    text2 = _generate(req[j:j + 1], CAPTION_TEMPERATURES[1], seed=s)[0]
                except Exception as e:      # 重试自己失败：按 failed 记账，不拖垮整集
                    text2, reason, tries = "", f"重试失败：{e}", 2
                else:
                    tries = 2
                    reason2 = check_caption(text2)
                    raw, reason = text2, reason2
            r["frames"] = n_frames[s]
            r["tries"] = tries
            r["caption"] = clean_caption(raw) if reason is None else ""
            r["status"] = "ok" if reason is None else "failed"
            if reason:
                r["reason"] = reason
            else:
                r.pop("reason", None)
                done += 1
        save()
        i += len(chunk)
        if progress:
            progress(i, len(todo))
    return {"shots": len(rows), "ok": sum(1 for r in rows if r["status"] == "ok"),
            "failed": sum(1 for r in rows if r["status"] == "failed"),
            "skipped": len(rows) - len(todo)}


def estimate_caption_cost(n_shots: int, rate_cny_per_hour: float) -> float:
    """预估**单次运行**的云端花费（元）。纯函数。估值只用来卡预算闸（待实测回填）。

    闸门的口径是「本次运行预估额」，不是「单季成本」（2026-09-11 裁决）：它防的是
    **量级错误**——手滑对一部 3 小时 Live 全集跑打标那种，与素材长短、是不是番无关。
    几十秒的 SP 素材镜头数少，预估自然远在闸下，不会多一步确认。
    """
    return n_shots * CAPTION_SECONDS_PER_SHOT / 3600.0 * rate_cny_per_hour


def cloud_budget() -> tuple[float, float]:
    """（元/小时, Phase 0 预算上限），读 config/cloud.json。

    **不 import pipeline.cloud**：那会把一条与检索无关的模块拖进每个 import vindex
    的路径（clips/render/cover 都 import 它）。这两个数只是配置。
    """
    p = paths.CONFIG / "cloud.json"
    if not p.exists():
        raise SystemExit(f"FAIL 没有 {p}，预算闸算不出上限（不许默认放行）")
    cf = json.loads(p.read_text(encoding="utf-8"))
    return float(cf["hourly_rate_cny"]), float(cf["budget_per_phase0_cny"])


_EMBEDDER = None


def embed_model_ref() -> str:
    """bge-m3 的加载目标：**平铺本地目录优先**（`data/models/<repo>`），否则 HF repo id。

    与 `caption_model_ref` 同源的理由，但这一条是本地网络实测逼出来的：
    HF hub 缓存的 blob 名带 etag，而 xet 后端**每次请求换 etag**——本地网速下
    断一次就整份重下，残渣不能续（2026-09-11 实测：2.27G 权重连下两次都没完，
    两个不同后缀的 `.incomplete` 双双作废）。平铺目录加 curl 断点续传对网络抖动是稳的，
    而且与云端数据盘的模型布局一致（同一个约定，两处受益）。
    """
    p = paths.MODELS / EMBED_REPO
    return str(p) if p.is_dir() else EMBED_REPO


def embedder():
    """bge-m3 文本嵌入器。第一次调用才加载（与 tagger 同一个纪律）。"""
    global _EMBEDDER
    if _EMBEDDER is None:
        from sentence_transformers import SentenceTransformer

        _EMBEDDER = SentenceTransformer(embed_model_ref())
    return _EMBEDDER


def encode_text_query(text: str) -> np.ndarray:
    """查询文本 → bge-m3 向量（已归一化）。

    **它取代了 `encode_query`（CLIP 图像空间）在检索路径上的位置**：查询与镜头描述
    现在同在文本空间，「算不算命中」才有一个可标定的门槛——这正是第 2 层复活的全部内容。
    """
    v = np.asarray(embedder().encode([text], normalize_embeddings=True,
                                     show_progress_bar=False), dtype=np.float32)
    if v.shape[1] != EMBED_DIM:
        raise SystemExit(
            f"FAIL {EMBED_REPO} 的查询向量维度是 {v.shape[1]}，期望 {EMBED_DIM}。\n"
            f"     维度对不上时检索结果不是「差」而是「没有意义」，必须硬失败")
    return v[0]


def build_embed(anime: str, out_dir: Path = VINDEX_DIR, batch: int = 64,
                progress=None) -> dict:
    """captions → bge-m3 向量库：每集一个 `<番>_<集>.scene.npy` + `.scene.json`。

    产物沿用 `scene` 的文件名（`load_scene` 读的就是它）：**换的是内核，不是契约**。
    旧的 CLIP 产物（若盘上还有）会在 `_check_meta` 处按 model_id 硬拒。

    **打标失败的镜头保留零向量占位**，不跳过：跳过会让向量行与镜头行错位，
    而错位之后每一条检索结果都还是「看起来正常」的。零向量与任何归一化查询的
    余弦是 0，永远过不了门槛——所以它等价于「这个镜头没有描述」，但**行数仍然对得上**。
    """
    files = sorted(out_dir.glob(f"{anime}_*.captions.json"))
    if not files:
        raise SystemExit(
            f"FAIL {out_dir} 下没有《{anime}》的 captions，先跑 "
            f"`vindex captions {anime} <集号>`（云端）")
    emb = None
    n_ep = n_shot = n_failed = 0
    # **先把每一集都验一遍（逐行对齐 + 无 pending），再加载 2G 模型。**
    # 半份 captions 不该换来一次模型加载，而加载排在前面就得先付这个代价。
    loaded = []
    for p in files:
        key = p.name[len(anime) + 1:-len(".captions.json")]
        meta, rows = load_captions(anime, key, out_dir)
        pend = pending_shots(rows)
        if pend:
            raise SystemExit(
                f"FAIL {p.name} 还有 {pend} 个镜头没打标（pending）。\n"
                f"     半份 captions 建出来的库会让检索池静默缩水，先跑完 captions")
        loaded.append((key, meta, rows))
    for key, meta, rows in loaded:
        ok_idx = [i for i, r in enumerate(rows) if r["status"] == "ok" and r["caption"]]
        vecs = np.zeros((len(rows), EMBED_DIM), dtype=np.float32)
        if ok_idx:
            emb = emb or embedder()
            v = np.asarray(emb.encode([rows[i]["caption"] for i in ok_idx],
                                      batch_size=batch, normalize_embeddings=True,
                                      show_progress_bar=False), dtype=np.float32)
            if v.shape[1] != EMBED_DIM:
                raise SystemExit(
                    f"FAIL {EMBED_REPO} 出来的维度是 {v.shape[1]}，期望 {EMBED_DIM}")
            vecs[ok_idx] = v
        _atomic_save(out_dir / f"{anime}_{key}.scene.npy", vecs)
        _atomic_write(scene_path(anime, key, out_dir), json.dumps({
            "meta": {
                "kind": "scene", "producer": "captions",
                "anime": anime, "episode": key,
                "model_id": EMBED_REPO, "revision": paths.model_revision(EMBED_REPO),
                "dim": EMBED_DIM, "normalize": "l2",
                "captions_model": CAPTIONS_REPO,
                "prompt_version": meta["prompt_version"],
                "failed_rows": sum(1 for r in rows if r["status"] != "ok"),
                "shots": meta["shots"],
                "built_at": date.today().isoformat(),
            },
            # label 就是那条 caption 本身。**人可读是这套索引的隐性审计红利**
            # （ADR-0015 决定 5）：04-clips.json 里能直接看到检索命中的是哪一个镜头、
            # 它被描述成了什么，不必回头翻图。
            "shots": [{"i": r["shot"], "start": r["start"], "end": r["end"],
                       "label": r["caption"] or "（无描述）",
                       "caption_status": r["status"]} for r in rows],
        }, ensure_ascii=False))
        n_ep += 1
        n_shot += len(rows)
        n_failed += sum(1 for r in rows if r["status"] != "ok")
        if progress:
            progress(n_ep, len(files))
    return {"episodes": n_ep, "shots": n_shot, "failed": n_failed}


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

    **判据是门槛标定了没有**，不是索引建了没有。v2 换了索引内核（captions +
    bge-m3，ADR-0015），但这条判据没变：没有标定出来的 `no_match`，
    `clips` 那边写 `场景` 本来就会当场失败，这一层等于不存在。

    这条存在的意义是不让数字**永远红着**。一道永远失败的门禁和没有门禁
    效果一样，但它会让人学会忽略它——本项目在门禁上踩过的坑全是这个形状。
    还没标定的番应当被如实报成「未启用」，而不是报成「缺 40 集」。
    """
    try:
        return scene_conf(anime, path).get("no_match") is not None
    except SystemExit:
        return False


def subtitle_index_count(anime: str, index_dir: Path) -> int:
    """成对计数：.json 与 .npy 都在才算一集。

    计数口径必须与 `subindex.load_all` 的加载口径一致（按 .json glob、
    要求 .npy 在盘）。孤儿 .npy（写一半崩溃的残骸）原先也被 `*.npy` glob
    数进来，六条数字照样相等而检索池实际少一集——这正是「六条数字」要防的
    假绿（2026-08-16 审计 2-1）。
    """
    return len([p for p in index_dir.glob(f"{anime}_*.json")
                if p.with_suffix(".npy").exists()])


def caption_counts(anime: str, out_dir: Path = VINDEX_DIR) -> dict[str, int]:
    """意象打标（v2 第七条）的对账数字，ADR-0015。

    **这一条数的是「完整覆盖的集数」，不是文件数。** 跑到一半崩溃的 captions
    文件同样会被 glob 数到，而它代表的是一集没打完——文件数口径下这种半份状态
    看起来和做完了完全一样，那正是「六条数字」要防的假绿。
    所以：行数与镜头表对齐、且没有 pending 行，才算这一集覆盖了。

    `failed` 单独计：S4 是不定罪不是不算账（ADR-0015：允许 failed 存在
    但必须显式计数）。零描述镜头在向量库里是零向量，永远过不了门槛。
    """
    out = {"episodes": 0, "files": 0, "shots": 0, "failed": 0, "pending": 0}
    for p in sorted(out_dir.glob(f"{anime}_*.captions.json")):
        out["files"] += 1
        key = p.name[len(anime) + 1:-len(".captions.json")]
        rows = json.loads(p.read_text(encoding="utf-8")).get("captions", [])
        st = [r.get("status") for r in rows]
        pend = sum(1 for s in st if s not in ("ok", "failed"))
        out["failed"] += st.count("failed")
        out["pending"] += pend
        sp = shots.SHOTS_DIR / f"{anime}_{key}.json"
        n_shots = (len(json.loads(sp.read_text(encoding="utf-8"))["shots"])
                   if sp.exists() else None)
        if n_shots is not None and len(rows) == n_shots and pend == 0:
            out["episodes"] += 1
            out["shots"] += len(rows)
    return out


def status(anime: str) -> dict[str, int]:
    """七条数字（v2 起：ADR-0003 的六条 + ADR-0015 的意象打标）。

    **判据不能是「检索得到东西吗」，必须是「集数对得上吗」。** 前者永远为真：
    池子只有应有的七分之一时，检索照常返回 Top-K、分数照常在阈值以上、成片照常渲染出来。

    画面语义与意象打标两个通道**未启用且盘上无产物时不计入**（见 `scene_enabled`）；
    计数口径与加载口径一致：`captions_coverage` 只数完整覆盖的集，
    与 `load_captions` 的行对齐校验同一回事。
    打标失败的镜头数不进这个值（它是集数），由 `main` 单独打出来——
    **不进值不等于不报**：ADR-0015 要求 failed 显式计数。
    """
    from .ingest import load_sources
    from .subindex import INDEX_DIR

    notes = paths.DATA / "library" / "notes" / f"{anime}.md"
    n_notes = len(note_episodes(notes))
    out = {
        "片源": len(load_sources(anime)),
        "字幕索引": subtitle_index_count(anime, INDEX_DIR),
        "笔记": n_notes,
        "镜头表": len(list(shots.SHOTS_DIR.glob(f"{anime}_*.json"))),
        "角色在场": len(list(VINDEX_DIR.glob(f"{anime}_*.presence.json"))),
    }
    n_scene = len(list(VINDEX_DIR.glob(f"{anime}_*.scene.npy")))
    # 未启用但盘上有残留 → 照样报出来，让人看见这份半吊子状态
    if scene_enabled(anime) or n_scene:
        out["画面语义"] = n_scene
    cap = caption_counts(anime)
    if cap["files"] or scene_enabled(anime):
        out["意象打标"] = cap["episodes"]
    return out


def hit_line(score: float, u: "Shot") -> str:
    """画面通道的一条命中 → 人读的一行。**集键走 `key_of`**：

    池素材（`SP05`）的 `season` 是 None，按 `S%02dE%02d` 格式化会当场 TypeError——
    而这条路径只在人手动跑 `vindex search` 时才走到，所以**不带单测就会一直活着**
    （CLI 辅助函数容易被当成「反正不重要」而漏测，它偏是排片前最常用的那条）。
    """
    return (f"{score:.3f}  {key_of(u)} "
            f"{_fmt(u.start)}-{_fmt(u.end)}  {u.text}")


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

    s = sub.add_parser("scene", help="通道 2（已弃）：给每个镜头算 CLIP 画面向量")
    s.add_argument("anime")
    s.add_argument("episode", nargs="+")

    c = sub.add_parser("captions", help="通道 2：云端 VLM 逐镜头打意象（ADR-0015）")
    c.add_argument("anime")
    c.add_argument("episode", nargs="+", help="SxxEyy，可多个")
    c.add_argument("--batch", type=int, default=CAPTION_BATCH,
                   help=f"一次前向几个镜头（默认 {CAPTION_BATCH}）；显存不够会自动减半")
    c.add_argument("--confirm-cost", action="store_true",
                   help="预估超 Phase 0 预算时仍继续（大账单必须是显式动作）")
    c.add_argument("--limit", type=int, metavar="N",
                   help="冒烟用：只打前 N 个镜头，其余留 pending"
                        "（建库会拒绝 pending，不会静默出半份索引）")

    e = sub.add_parser("embed", help="通道 2：captions → bge-m3 向量库")
    e.add_argument("anime")
    e.add_argument("--batch", type=int, default=64)

    q = sub.add_parser("search", help="画面语义检索")
    q.add_argument("query")
    q.add_argument("--anime", default=paths.conf("anime.default"))
    q.add_argument("-k", type=int, default=8)

    w = sub.add_parser("who", help="某段时间里有谁（查角色索引）")
    w.add_argument("anime")
    w.add_argument("episode")
    w.add_argument("--start", type=float, default=0.0)
    w.add_argument("--end", type=float, default=1e9)

    st = sub.add_parser("status", help="七条数字对不对得上")
    st.add_argument("--anime", default=paths.conf("anime.default"))

    a = ap.parse_args()
    if a.cmd in ("search", "status") and not a.anime:
        raise SystemExit("FAIL 没指定番名：给 --anime，或在 config/project.json 里设 anime.default")
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
        bad = 0
        for key in a.episode:
            # 多集批命令逐集报 FAIL，不打断整批（E10）：detect/presence 是
            # 几小时级的活，一集缺代表帧停下等于逼人重跑前面已完成的
            try:
                n = build_presence(a.anime, key, batch=a.batch)
            except SystemExit as e:
                bad += 1
                print(f"FAIL {key}  {e}", flush=True)
                continue
            print(f"OK {key} {n} 个镜头 → {presence_path(a.anime, key).name}", flush=True)
        if bad:
            print(f"{bad} 集失败")
        return 1 if bad else 0

    if a.cmd == "scene":
        bad = 0
        for key in a.episode:
            try:
                n = build_scene(a.anime, key)
            except SystemExit as e:
                bad += 1
                print(f"FAIL {key}  {e}", flush=True)
                continue
            print(f"OK {key} {n} 个镜头 → {scene_path(a.anime, key).name}", flush=True)
        if bad:
            print(f"{bad} 集失败")
        return 1 if bad else 0

    if a.cmd == "search":
        vecs, units = load_scene(a.anime)
        for score, u in search_scene(a.query, vecs, units, a.k):
            print(hit_line(score, u))
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

    if a.cmd == "captions":
        rate, budget = cloud_budget()
        shots_total = 0
        for key in a.episode:
            shots_total += len(shots.load(a.anime, key)["shots"])
        est = estimate_caption_cost(shots_total, rate)
        print(f"[*] 单次运行预估 {shots_total} 个镜头 ≈ ¥{est:.2f}"
              f"（按 {CAPTION_SECONDS_PER_SHOT:g}s/镜头、{rate:g} 元/h 估；"
              f"Phase 0 预算上限 ¥{budget:.2f}）", flush=True)
        if est > budget and not a.confirm_cost:
            raise SystemExit(
                f"FAIL 单次运行预估超过 Phase 0 预算闸 ¥{budget:.2f}。\n"
                f"     大账单必须是显式动作（ADR-0015 判断 6）：确认后加 --confirm-cost 重跑")
        bad = 0
        for key in a.episode:
            try:
                r = build_captions(a.anime, key, batch=a.batch, limit=a.limit)
            except SystemExit as e:
                bad += 1
                print(f"FAIL {key}  {e}", flush=True)
                continue
            print(f"OK {key} 打标 {r['shots']} 个镜头：ok {r['ok']}、failed {r['failed']}"
                  f"（本轮续跑跳过 {r['skipped']}）→ {captions_path(a.anime, key).name}",
                  flush=True)
        if bad:
            print(f"{bad} 集失败，重跑同一命令即续跑")
        return 1 if bad else 0

    if a.cmd == "embed":
        r = build_embed(a.anime, batch=a.batch)
        print(f"OK 建库 {r['episodes']} 集 / {r['shots']} 个镜头"
              f"（其中 {r['failed']} 个零向量占位）→ {VINDEX_DIR}/{a.anime}_*.scene.npy")
        return 0

    st_ = status(a.anime)
    for k, v in st_.items():
        print(f"  {k:<8} {v}")
    if "画面语义" not in st_:
        print("  画面语义   未启用（门槛未标定，见 ADR-0015；写 `场景` 会当场报错）")
    cap = caption_counts(a.anime)
    if "意象打标" not in st_:
        print("  意象打标   未打标（v2 第七条，跑 `vindex captions`）")
    vals = list(st_.values())
    red = len(set(vals)) != 1
    if not red and not (cap["failed"] or cap["pending"]):
        print(f"OK {len(vals)} 条数字一致：{vals[0]}")
        return 0
    if red:
        print("-" * 40)
        print("★ 数字对不上。**素材边界缩水是唯一一种「越用越不觉得有问题」的失败**——"
              "检索照常返回 Top-K，分数照常在阈值以上，成片照常渲染出来，全程不报错。")
    # ADR-0015：failed 允许存在但必须显式计数（S4 不定罪≠不报）；pending = 跑到一半
    if cap["failed"] or cap["pending"]:
        print(f"★ 意象打标：{cap['shots']} 个镜头有描述，{cap['failed']} 个 failed"
              f"（S4 不定罪，但计入对账第七条），{cap['pending']} 个没结论。"
              f"重跑 `vindex captions` 即续跑")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
