"""通道 1 的 producer：人脸检测 → 角色嵌入 → 聚类 → 人工贴名（ADR-0003 第 1 层）。

    python -m pipeline.faces detect 春物 S01E01 ...   # ① 检测 + 嵌入（最贵的一步）
    python -m pipeline.faces cluster 春物              # ② 聚类，出联系表
    python -m pipeline.faces name 春物 3 雪乃           # ③ 人给每簇贴名
    python -m pipeline.faces presence 春物             # ④ 落角色在场索引

**为什么不是更便宜的 booru tagger。** ADR-0003 把 tagger 排在这条路之前当探针，
理由是它可能连聚类和贴名一起顶掉。2026-08-03 探针跑完，答案是不能：

| tagger | 春物 12 角色词表覆盖 | 实测置信度 |
|---|---|---|
| SmilingWolf/wd-swinv2-tagger-v3 | 3/12（**连主角八幡都没有**） | 雪乃 0.99、结衣 0.997 |
| deepghs/camie_tagger_onnx | 11/12 | **0.03–0.14，等于认不出** |
| pixai-tagger-v0.9 | 4/12 | 620 GFLOPs，一季 8.4 小时 |

**词表大的认不准，认得准的词表小。** 而这条路的词表不受限——它不从预训练标签集里
认人，是从这部番自己的素材里聚出簇再由人贴名，所以能覆盖到小町、静老师、川崎、户冢
这些冷门到任何 booru 标签集都不会收的角色。

第 3 步的人工介入**是设计的一部分，不是妥协**：一部番只做一次，与音色、BGM 曲目表、
番剧笔记同属 Phase 0 资产；而姐妹脸这类只有人能一眼分开。
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from . import paths, sheet, shots, vindex

import numpy as np

# YOLOv8 动漫人脸检测。imgutils 的默认档（level='s', version='v1.4'）。
FACE_REPO = "deepghs/anime_face_detection"
FACE_MODEL = "face_detect_v1.4_s"
FACE_CONF = 0.25          # imgutils 默认
FACE_IOU = 0.7            # imgutils 默认

# CCIP：**专门为「两张图是不是同一个动漫角色」训练的度量**，不是通用人脸嵌入。
# 这一点对本项目要紧：雪乃/陽乃是姐妹、人设高度相似，ADR-0003 把她们列为
# 「第 1 层最大的单点风险」，而通用人脸嵌入在动漫上本来就弱。
CCIP_REPO = "deepghs/ccip_onnx"
CCIP_MODEL = "ccip-caformer-24-randaug-pruned"
CCIP_SIZE = 384
# CLIP 的均值方差，不是 ImageNet 的。抄错不会报错，只会让所有距离一起漂。
CCIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
CCIP_STD = (0.26862954, 0.26130258, 0.27577711)

# 作者验证集上的同人阈值（`metrics.json`），f1 0.917 / 精确率 0.933 / 召回率 0.901。
# **留在这里只作参照，判定不用它**——理由见下面那条。
CCIP_AUTHOR_THRESHOLD = 0.17847511429108218

# 判定用的同人上界。**必须在本部番上重新标定，作者那个数在这里太松。**
#
# 作者的 0.1785 是跨多部番标的：它要回答的是「这两张图是不是同一个角色」，
# 跨番时不同角色差得远，松一点无所谓。而这里要回答的是
# **「这张脸是这部番里已登记的哪一个角色」**——同一部番的角色是同一个人设计的，
# 距离整体压缩，而且画面里还有大量没登记的路人。
#
# 2026-08-03 两次标定，第二次推翻第一次：
#
# 1. 在 S01E01 的 67 张人工确认过的代表脸上留一评测：
#    到**同人**其他脸的最近距离 中位 0.016 / 90 分位 0.028 / **最大 0.045**；
#    到**别人**的最近距离 中位 0.208 / **最小 0.051**。两个分布不重叠，边界在 0.05。
# 2. 但当时只有 4 个角色，用作者的 0.1785 当门槛也是 20/20 全对，就没换。
#    **全季 10 个角色时它崩了**：阳乃抽检 20 张只有 3–4 张真是她，
#    被判给她的其实是八幡、海老名、平冢静和各种背景路人，距离全在 0.07–0.17。
#    全季 2936 张脸的 best 距离中位数是 0.083——**一半的脸压根不属于任何已登记角色**，
#    而 0.05–0.1785 这一段里「最近的那个」基本是随机的。
#
# 所以门槛取 0.05。代价是召回率从 77% 掉到 31%，**这是要的方向**：
# 整条角色过滤链建在「检测到 X 可信」上，宁可少认，不可认错（ADR-0003「检测的不对称」）。
CCIP_SAME = 0.05

# 归队时要求最近的角色比第二近的角色近这么多。
#
# **只用上面那个阈值不够，这是实测出来的。** 作者的 0.1785 是跨多部番标定的，
# 而同一部番里角色画风是同一个人设计的，距离整体压缩得多。
# 2026-08-03 在 S01E01 的 7 个簇、67 张人工确认过的代表脸上留一评测：
#
# | | 中位 | 90 分位 | 极值 |
# |---|---|---|---|
# | 到**同一个人**其他脸的最近距离 | 0.016 | 0.028 | 最大 **0.045** |
# | 到**别人**的最近距离 | 0.208 | — | 最小 **0.051** |
#
# 两个分布不重叠，「离谁最近就是谁」67/67 全对。**但最难的一对是平冢静与雪乃：
# 0.051**——两人都是黑长直，人设本来就像，绝对阈值 0.1785 会把她们判成同一个人。
# 初版就是这么写的（「只落进一个人的半径才算数」），结果 391 张脸有 249 张
# 因为同时落进静和雪乃的半径被丢掉，雪乃全集只认出 2 个镜头。
#
# 0.02 的依据：最难那一对的领先量是 0.022，取 0.02 恰好 67 张全保留且全判对。
# **这是在代表脸上标定的，而代表脸是各簇里最典型的那些**，所以真实素材上的领先量
# 只会更小——那些会被判成「没认出来」，这正是安全的方向（ADR-0003「检测的不对称」）。
# 逐角色抽检见 `python -m pipeline.vprobe presence`。
CCIP_MARGIN = 0.02

# 聚类用 OPTICS，参数取 imgutils 的库默认（`ccip_clustering` 的 method='optics'）。
#
# **DBSCAN 那组参数在这份素材上不能用。** 2026-08-03 实测 S01E01 的 391 张脸：
#
# | 方法 | 簇数 | 噪声 | 最大簇 |
# |---|---|---|---|
# | DBSCAN eps=0.1292 min=2（作者 cluster.json） | 3 | 10 | **377** |
# | DBSCAN eps=0.1785 min=2 | 1 | 3 | **388** |
# | OPTICS max_eps=0.5 min=5（库默认） | 7 | 324 | 13 |
#
# 成因是 DBSCAN 的密度连通：侧脸和背影在两个角色之间充当桥，A~B、B~C 就把 A 和 C
# 并进一簇，整季的脸最后串成一坨。**而并错了不会报错**——它只是让贴名时看到一个
# 「什么人都有」的簇，人贴不下去，或者更糟，随手贴了一个名字。
#
# OPTICS 噪声率高（83%）不构成问题：**聚类这一步只负责找出可供人确认的代表脸**，
# 剩下的脸在第 ④ 步按到代表脸的距离归队。
CCIP_MAX_EPS = 0.5
CCIP_MIN_SAMPLES = 5

# 人脸框往外扩多少倍再送 CCIP。**动漫角色的身份信息大半在头发上**，
# 而检测框只框脸，直接裁会把最有辨识度的部分切掉。1.6 是起点，
# 抽检（`vprobe presence`）发现认混了就回来调，调完要重跑嵌入。
FACE_EXPAND = 1.6

# 聚类用的采样上限。全季一万八千张脸，两两距离矩阵是 O(n²)——
# 18000² 的 float32 是 1.3G。这一步的目的只是**让人看见有哪些簇**，采样足够；
# 剩下的脸在第 ④ 步按到各簇代表脸的距离归队，那一步是 O(n × 代表脸数)。
#
# 度量本身很便宜（2026-08-03 实测：4000×4000 只要 0.04 秒、峰值 727MB），
# 真正的约束是**人要看的簇数**——采样越大簇越碎，贴名的人工就越多。
CLUSTER_SAMPLE = 4000
# 每簇留几张代表脸，用于第 ④ 步给全部脸归队，以及出联系表给人看
PROTOTYPES = 24


def faces_path(anime: str, key: str) -> Path:
    return vindex.VINDEX_DIR / f"{anime}_{key}.faces.json"


def clusters_path(anime: str) -> Path:
    return vindex.VINDEX_DIR / f"{anime}.clusters.json"


# ---------------------------------------------------------------- 模型


_M: dict = {}


def _session(repo: str, filename: str):
    key = f"{repo}/{filename}"
    if key not in _M:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download

        _M[key] = ort.InferenceSession(
            hf_hub_download(repo, filename), providers=["CPUExecutionProvider"])
    return _M[key]


def _face_session():
    s = _session(FACE_REPO, f"{FACE_MODEL}/model.onnx")
    meta = s.get_modelmeta().custom_metadata_map
    imgsz = json.loads(meta["imgsz"]) if "imgsz" in meta else [640, 640]
    names = eval(meta["names"], {"__builtins__": {}})   # 形如 {0: 'face'}
    return s, tuple(imgsz), [names[i] for i in range(len(names))]


# ---------------------------------------------------------------- 检测


def _xywh2xyxy(x: np.ndarray) -> np.ndarray:
    y = np.copy(x)
    y[..., 0] = x[..., 0] - x[..., 2] / 2
    y[..., 1] = x[..., 1] - x[..., 3] / 2
    y[..., 2] = x[..., 0] + x[..., 2] / 2
    y[..., 3] = x[..., 1] + x[..., 3] / 2
    return y


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(iou <= iou_threshold)[0] + 1]
    return keep


def detect(img) -> list[tuple[tuple[int, int, int, int], float]]:
    """一张图 → [(x0,y0,x1,y1), 置信度]。

    预处理照 imgutils 的 `yolo_predict`：**直接缩到模型的 imgsz，不做 letterbox**，
    坐标按缩放比还原回原图。不是我偷懒——letterbox 与直缩的坐标还原方式不同，
    混用会让框整体偏移，而偏移了的框裁出来仍然是一张「像脸」的图，不报错。
    """
    sess, imgsz, labels = _face_session()
    ow, oh = img.size
    im = img.resize(imgsz)
    x = (np.asarray(im, dtype=np.float32) / 255.0).transpose(2, 0, 1)[None]
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0][0]   # [4+nc, boxes]

    max_scores = out[4:, :].max(axis=0)
    out = out[:, max_scores > FACE_CONF].transpose(1, 0)
    if not out.size:
        return []
    boxes, scores = _xywh2xyxy(out[:, :4]), out[:, 4:].max(axis=1)
    keep = _nms(boxes, scores, FACE_IOU)
    sx, sy = ow / imgsz[0], oh / imgsz[1]
    return [((int(np.clip(boxes[i][0] * sx, 0, ow)), int(np.clip(boxes[i][1] * sy, 0, oh)),
              int(np.clip(boxes[i][2] * sx, 0, ow)), int(np.clip(boxes[i][3] * sy, 0, oh))),
             float(scores[i])) for i in keep]


def crop(img, box: tuple[int, int, int, int], expand: float = FACE_EXPAND):
    """按 `expand` 往外扩再裁。扩的是**正方形**，避免拉伸——CCIP 会把图直缩到 384×384。"""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half = max(x1 - x0, y1 - y0) * expand / 2
    return img.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half)))


# ---------------------------------------------------------------- 嵌入


def embed(crops: list) -> np.ndarray:
    """人脸裁片 → CCIP 768 维特征。"""
    sess = _session(CCIP_REPO, f"{CCIP_MODEL}/model_feat.onnx")
    mean = np.asarray(CCIP_MEAN, dtype=np.float32)[:, None, None]
    std = np.asarray(CCIP_STD, dtype=np.float32)[:, None, None]
    out = []
    for im in crops:
        a = np.asarray(im.convert("RGB").resize((CCIP_SIZE, CCIP_SIZE)),
                       dtype=np.float32).transpose(2, 0, 1) / 255.0
        out.append((a - mean) / std)
    if not out:
        return np.zeros((0, 768), dtype=np.float32)
    return sess.run(["output"], {"input": np.stack(out)})[0].astype(np.float32)


def differences(feats: np.ndarray) -> np.ndarray:
    """两两「是不是同一个角色」的距离矩阵。**这是学出来的度量，不是余弦。**

    所以阈值也必须用作者给的那个（0.1785），不能套用别处的经验值。
    """
    sess = _session(CCIP_REPO, f"{CCIP_MODEL}/model_metrics.onnx")
    return sess.run(["output"], {"input": feats.astype(np.float32)})[0]


def cross_differences(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """A 中每张脸到 B 中每张脸的距离。度量模型只吃一个批次并吐全矩阵，所以拼起来取块。"""
    n = len(a)
    return differences(np.vstack([a, b]))[:n, n:]


# ---------------------------------------------------------------- ① 检测 + 嵌入


def build_faces(anime: str, key: str, progress=None) -> int:
    d = shots.load(anime, key)
    from PIL import Image

    recs, feats = [], []
    for s in d["shots"]:
        f = shots.frame_path(anime, key, s["i"])
        if not f.exists():
            raise SystemExit(
                f"FAIL 缺代表帧 {f}，先跑 `python -m pipeline.shots frames {anime} {key}`")
        img = Image.open(f).convert("RGB")
        hits = detect(img)
        if hits:
            feats.append(embed([crop(img, b) for b, _ in hits]))
            recs += [{"shot": s["i"], "box": list(b), "score": round(sc, 4)}
                     for b, sc in hits]
        if progress:
            progress(s["i"] + 1, len(d["shots"]))

    vec = np.vstack(feats) if feats else np.zeros((0, 768), dtype=np.float32)
    vindex.VINDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(vindex.VINDEX_DIR / f"{anime}_{key}.faces.npy", vec)
    faces_path(anime, key).write_text(json.dumps({
        "meta": {
            "kind": "faces", "anime": anime, "episode": key,
            "detector": FACE_REPO + "/" + FACE_MODEL,
            "detector_revision": paths.model_revision(FACE_REPO),
            "conf": FACE_CONF, "iou": FACE_IOU, "expand": FACE_EXPAND,
            "embedder": CCIP_REPO + "/" + CCIP_MODEL,
            "embedder_revision": paths.model_revision(CCIP_REPO),
            "dim": int(vec.shape[1]) if vec.size else 768,
            "shots": vindex._shots_fingerprint(d["meta"]),
            "built_at": date.today().isoformat(),
        },
        "faces": recs,
    }, ensure_ascii=False), encoding="utf-8")
    return len(recs)


def load_faces(anime: str, key: str) -> tuple[list[dict], np.ndarray]:
    p = faces_path(anime, key)
    if not p.exists():
        raise SystemExit(f"FAIL 没有 {p.name}，先跑 `python -m pipeline.faces detect {anime} {key}`")
    d = json.loads(p.read_text(encoding="utf-8"))
    vindex._check_meta_shots(d["meta"], p)
    return d["faces"], np.load(p.with_suffix(".npy"))


def episodes(anime: str) -> list[str]:
    return sorted(p.name[len(anime) + 1:-len(".faces.json")]
                  for p in vindex.VINDEX_DIR.glob(f"{anime}_*.faces.json"))


# ---------------------------------------------------------------- ② 聚类


def cluster(anime: str, sample: int = CLUSTER_SAMPLE, seed: int = 0) -> dict:
    """在采样的脸上聚类，每簇留若干代表脸。

    **聚类只是为了让人看见有哪些角色**，不是最终判定。最终判定在第 ④ 步：
    每张脸按到各簇代表脸的距离归队。这样做的原因是 O(n²)——
    全季两万张脸的距离矩阵是 1.6G，而采样四千张只要 64M。
    """
    from sklearn.cluster import OPTICS

    keys = episodes(anime)
    if not keys:
        raise SystemExit(f"FAIL 没有《{anime}》的人脸数据，先跑 `faces detect`")

    allv, src = [], []
    for k in keys:
        recs, vec = load_faces(anime, k)
        allv.append(vec)
        src += [(k, i) for i in range(len(recs))]
    allv = np.vstack(allv)

    rng = np.random.default_rng(seed)
    idx = (rng.choice(len(allv), sample, replace=False)
           if len(allv) > sample else np.arange(len(allv)))
    idx.sort()
    sub = allv[idx]

    diff = differences(sub)
    labels = OPTICS(max_eps=CCIP_MAX_EPS, min_samples=CCIP_MIN_SAMPLES,
                    metric="precomputed").fit_predict(diff)

    clusters = {}
    for lab in sorted(set(labels)):
        if lab < 0:                       # OPTICS 判为噪声，不成簇
            continue
        members = np.nonzero(labels == lab)[0]
        # 代表脸取「到同簇其他脸平均距离最小」的那几张——最典型的，不是最边缘的
        order = members[np.argsort(diff[np.ix_(members, members)].mean(axis=1))]
        protos = order[:PROTOTYPES]
        clusters[str(int(lab))] = {
            "name": None, "n": int(len(members)),
            "protos": [{"episode": src[idx[j]][0], "face": src[idx[j]][1]} for j in protos],
        }

    out = {
        "meta": {
            "kind": "clusters", "anime": anime,
            "embedder": CCIP_REPO + "/" + CCIP_MODEL,
            "embedder_revision": paths.model_revision(CCIP_REPO),
            "cluster": "optics", "max_eps": CCIP_MAX_EPS, "min_samples": CCIP_MIN_SAMPLES,
            "threshold": CCIP_SAME,
            "sample": int(len(idx)), "total_faces": int(len(allv)),
            "episodes": keys, "built_at": date.today().isoformat(),
        },
        "clusters": dict(sorted(clusters.items(),
                                key=lambda kv: -kv[1]["n"])),
    }
    clusters_path(anime).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    return out


def load_clusters(anime: str) -> dict:
    p = clusters_path(anime)
    if not p.exists():
        raise SystemExit(f"FAIL 没有 {p.name}，先跑 `python -m pipeline.faces cluster {anime}`")
    return json.loads(p.read_text(encoding="utf-8"))


def proto_vectors(anime: str, cid: str, db: dict | None = None) -> np.ndarray:
    db = db or load_clusters(anime)
    cache: dict[str, np.ndarray] = {}
    out = []
    for p in db["clusters"][cid]["protos"]:
        if p["episode"] not in cache:
            cache[p["episode"]] = load_faces(anime, p["episode"])[1]
        out.append(cache[p["episode"]][p["face"]])
    return np.stack(out) if out else np.zeros((0, 768), dtype=np.float32)


def _crops(anime: str, cid: str, db: dict, limit: int,
           tmp: Path) -> list[tuple[Path, str]]:
    from PIL import Image

    tmp.mkdir(parents=True, exist_ok=True)
    cells = []
    for n, p in enumerate(db["clusters"][cid]["protos"][:limit]):
        recs, _ = load_faces(anime, p["episode"])
        r = recs[p["face"]]
        img = Image.open(shots.frame_path(anime, p["episode"], r["shot"])).convert("RGB")
        dest = tmp / f"{anime}-c{cid}-{n:02d}.jpg"
        crop(img, tuple(r["box"])).save(dest, quality=90)
        cells.append((dest, f'{p["episode"]} 镜{r["shot"]}'))
    return cells


def cluster_sheet(anime: str, cid: str, out_dir: Path | None = None) -> Path:
    """一簇的代表脸拼成一张图，给人细看用。"""
    out_dir = out_dir or vindex.VINDEX_DIR / "probe"
    db = load_clusters(anime)
    cells = _crops(anime, cid, db, PROTOTYPES, out_dir / "crops")
    return sheet.build(cells, out_dir / f"cluster-{anime}-{cid}.jpg",
                       cols=8, cell_w=200, ratio=1.0)


def overview_sheet(anime: str, per: int = 3, out_dir: Path | None = None) -> Path:
    """所有簇拼成**一张**总览图，每簇 `per` 张代表脸。

    **贴名这一步的人工预算是分钟级的，逐簇开图撑不住。** 全季四万来张脸能聚出
    几十个簇（实测 19499 张脸采样 4000 出 64 簇），一簇一张图就是几十次点击；
    而认人只需要扫一眼——总览图上认出哪几簇是要的角色，再对那几簇单独开图细看。

    没贴名的簇不影响任何事：归队只认已贴名的角色，其余的脸就是「没认出来」。
    """
    out_dir = out_dir or vindex.VINDEX_DIR / "probe"
    db = load_clusters(anime)
    cells = []
    for cid, c in db["clusters"].items():
        got = _crops(anime, cid, db, per, out_dir / "crops")
        tag = f'簇{cid} {c["n"]}张' + (f' ={c["name"]}' if c.get("name") else "")
        cells += [(f, tag if i == 0 else "") for i, (f, _) in enumerate(got)]
    return sheet.build(cells, out_dir / f"clusters-{anime}.jpg",
                       cols=per * 6, cell_w=150, ratio=1.0)


def name_cluster(anime: str, cid: str, name: str) -> dict:
    """给一簇贴名。名字必须在角色名表里，写错当场报错。"""
    db = load_clusters(anime)
    if cid not in db["clusters"]:
        raise SystemExit(f"FAIL 没有簇 {cid}，现有：{'、'.join(db['clusters'])}")
    alias = vindex.alias_map(anime)
    if name not in alias:
        raise SystemExit(
            f"FAIL 角色名表里没有「{name}」，先补进 {vindex.CHARACTERS} 的《{anime}》一节")
    db["clusters"][cid]["name"] = alias[name]
    db["clusters"][cid]["named_at"] = date.today().isoformat()
    clusters_path(anime).write_text(json.dumps(db, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    return db


# ---------------------------------------------------------------- ④ 落索引


def build_presence(anime: str, progress=None) -> dict[str, int]:
    """把「每张脸属于哪个已贴名的簇」变成「每个镜头里有谁」。

    **判据两条，缺一不可**（依据见 `CCIP_MARGIN` 上方的实测表）：

    1. 到最近角色的距离 < `CCIP_SAME`——像不像某个已登记角色
    2. 比第二近的角色近至少 `CCIP_MARGIN`——**像谁**

    到某个角色的距离取到他所有**代表脸**的最小值（不是平均值）：同一个角色在不同集、
    不同角度下差异很大，平均会把「和其中一张几乎一样」这个强证据稀释掉。
    一个角色可能有好几个簇（正脸一簇、侧脸一簇），先按名字合并再算。

    两条都不满足的脸判成**没认出来**，不判成「可能是谁」：整条角色过滤链建在
    「检测到 X 可信」上，把说不准的记成「是 X」是在拆地基；记成「没认出来」
    只是少一个候选，而排片那一侧本来就设计成过滤为空则退回
    （ADR-0003「检测的不对称」）。
    """
    db = load_clusters(anime)
    named = {cid: c["name"] for cid, c in db["clusters"].items() if c.get("name")}
    if not named:
        raise SystemExit(
            f"FAIL 一个簇都还没贴名。先看图再贴：\n"
            f"     python -m pipeline.faces sheet {anime}\n"
            f"     python -m pipeline.faces name {anime} <簇号> <角色名>")

    # 同一个角色可能有好几个簇（正脸一簇、侧脸一簇），按名字合并代表脸
    protos: dict[str, np.ndarray] = {}
    for cid, tag in named.items():
        v = proto_vectors(anime, cid, db)
        protos[tag] = np.vstack([protos[tag], v]) if tag in protos else v
    tags = sorted(protos)
    out = {}
    for n, key in enumerate(db["meta"]["episodes"], 1):
        recs, vec = load_faces(anime, key)
        per_shot: dict[int, dict[str, float]] = {}
        ambiguous = 0
        if len(vec):
            # [脸数, 角色数]：每张脸到每个角色全部代表脸的最小距离
            dist = np.stack([cross_differences(vec, protos[t]).min(axis=1)
                             for t in tags], axis=1)
            order = np.argsort(dist, axis=1)
            for r, row, od in zip(recs, dist, order):
                best = row[od[0]]
                second = row[od[1]] if len(tags) > 1 else np.inf
                if best >= CCIP_SAME:             # 谁都不像
                    continue
                if second - best < CCIP_MARGIN:   # 像不止一个人，说不准
                    ambiguous += 1
                    continue
                cur = per_shot.setdefault(r["shot"], {})
                # 存「相似度」= 1 − 距离，只为了让落盘的数与 tagger 通道同向；
                # **对外仍然只有布尔**，见 vindex.Presence
                cur[tags[od[0]]] = max(cur.get(tags[od[0]], 0.0),
                                       round(1.0 - float(best), 4))
        sh = shots.load(anime, key)["shots"]
        vindex.write_presence(anime, key, [
            {"i": s["i"], "char": per_shot.get(s["i"], {}), "gen": {}} for s in sh],
            producer="ccip", extra={
                "detector": FACE_REPO + "/" + FACE_MODEL,
                "model_id": CCIP_REPO + "/" + CCIP_MODEL,
                "revision": paths.model_revision(CCIP_REPO),
                "expand": FACE_EXPAND, "distance_threshold": CCIP_SAME,
                "author_threshold": CCIP_AUTHOR_THRESHOLD,
                "margin": CCIP_MARGIN,
                # 落盘的分数是 1 − 距离，所以判定阈值也要换算成同一个量纲。
                # 全部落盘的条目都已经过了这道线，这里写下来是为了让加载端不必知道
                # 「ccip 的分数是怎么来的」——量纲跟着文件走。
                "decision_threshold": round(1.0 - CCIP_SAME, 6),
                "keep_threshold": round(1.0 - CCIP_SAME, 6),
                "clusters": {cid: named[cid] for cid in named},
                "characters": tags,
            })
        out[key] = {"镜头": len(sh), "认出角色的镜头": sum(1 for s in sh if per_shot.get(s["i"])),
                    "脸": len(recs), "说不准而放弃": ambiguous}
        if progress:
            progress(n, len(db["meta"]["episodes"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="① 检测人脸 + 算角色嵌入")
    d.add_argument("anime")
    d.add_argument("episode", nargs="+")

    c = sub.add_parser("cluster", help="② 聚类，出簇")
    c.add_argument("anime")
    c.add_argument("--sample", type=int, default=CLUSTER_SAMPLE)

    s = sub.add_parser("sheet", help="出联系表给人贴名")
    s.add_argument("anime")
    s.add_argument("cluster", nargs="?", help="不给就出前 12 大簇")

    n = sub.add_parser("name", help="③ 给一簇贴名")
    n.add_argument("anime")
    n.add_argument("cluster")
    n.add_argument("name")

    p = sub.add_parser("presence", help="④ 落角色在场索引（全季一次）")
    p.add_argument("anime")

    a = ap.parse_args()
    paths.require_data()

    if a.cmd == "detect":
        for key in a.episode:
            n_ = build_faces(a.anime, key)
            print(f"OK {key} {n_} 张脸 → {faces_path(a.anime, key).name}", flush=True)
        return 0

    if a.cmd == "cluster":
        out = cluster(a.anime, a.sample)
        cs = out["clusters"]
        print(f"OK {out['meta']['total_faces']} 张脸，采样 {out['meta']['sample']} 聚出 "
              f"{len(cs)} 簇")
        for cid, c in list(cs.items())[:20]:
            print(f"   簇 {cid:>3}  {c['n']:5d} 张")
        print(f"\n下一步：python -m pipeline.faces sheet {a.anime}   然后逐簇贴名")
        return 0

    if a.cmd == "sheet":
        if a.cluster:
            db = load_clusters(a.anime)
            print(f"  簇 {a.cluster}（{db['clusters'][a.cluster]['n']} 张）→ "
                  f"{cluster_sheet(a.anime, a.cluster)}")
            return 0
        p = overview_sheet(a.anime)
        print(f"OK 全部簇的总览 → {p}\n"
              f"   认出哪几簇是要的角色就贴名，其余不用管：\n"
              f"   python -m pipeline.faces name {a.anime} <簇号> <角色名>\n"
              f"   某一簇要细看：python -m pipeline.faces sheet {a.anime} <簇号>")
        return 0

    if a.cmd == "name":
        db = name_cluster(a.anime, a.cluster, a.name)
        done = [f"{cid}={c['name']}" for cid, c in db["clusters"].items() if c.get("name")]
        print(f"OK 已贴名 {len(done)} 簇：{'、'.join(done)}")
        return 0

    got = build_presence(a.anime)
    n_shot = sum(v["镜头"] for v in got.values())
    n_hit = sum(v["认出角色的镜头"] for v in got.values())
    n_face = sum(v["脸"] for v in got.values())
    n_amb = sum(v["说不准而放弃"] for v in got.values())
    print(f"OK {len(got)} 集 / {n_shot} 个镜头，其中 {n_hit} 个认出了角色（{n_hit / n_shot:.0%}）")
    print(f"   {n_face} 张脸，{n_amb} 张同时落进多个角色的半径而放弃"
          f"（侧脸/背影/糊帧，判成没认出来而不是认错）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
