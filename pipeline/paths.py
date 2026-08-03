"""路径与模型缓存的唯一来源。

**任何会下载 HuggingFace 模型的模块，`from . import paths` 必须排在所有第三方 import 前面。**

HuggingFace 默认把权重下到 `~/.cache/huggingface`，也就是内置盘——这直接违反
CLAUDE.md「模型一律不落内置盘」。2026-07-29 检查时 `~/.cache/huggingface`
已经积了 1.0G，就是漏在这里。本模块在 import 时把 HF_HOME 指到 SSD，
并对「已经晚了」的情况当场报错（见下方注释）。

用 setdefault：外部显式设了 HF_HOME 就尊重外部，方便临时换缓存位置。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MODELS = DATA / "models"
VOICE = DATA / "voice"
EPISODES = DATA / "episodes"
CONFIG = ROOT / "config"

os.environ.setdefault("HF_HOME", str(MODELS))
os.environ.setdefault("HF_HUB_CACHE", str(MODELS / "hub"))

# `huggingface_hub.constants` 一旦被 import，缓存路径就算死成常量，此后改环境变量无效。
# 注意查的是 constants 而不是 huggingface_hub：后者是惰性模块，光 import 它还没定死。
# 真正会提前触发的是 mlx_audio / sentence_transformers 这类库——它们 import 时就用了
# hub 的功能。晚于它们 import 本模块等于什么都没做，而且不会报错，模型安安静静写进内置盘。
# 2026-07-29 就这么漏了 2.6G（whisper 在内置盘重下了一份）。这里把静默泄漏变成当场报错。
_hf = sys.modules.get("huggingface_hub.constants")
if _hf is not None and not str(_hf.HF_HUB_CACHE).startswith(str(MODELS)):
    raise SystemExit(
        f"FAIL huggingface_hub 的缓存已被钉在 {_hf.HF_HUB_CACHE}，再设 HF_HOME 无效，"
        f"模型会写进内置盘。\n"
        f"     把 `from . import paths` 挪到该文件所有第三方 import 的前面。"
    )


_CONF: dict | None = None


def conf(dotted: str, default=None):
    """读 `config/project.json` 的一个值，键用点分：`conf("video.crf", "18")`。

    **每个调用点都必须给出 default，而且 default 要等于原来写死的那个值。**
    这样配置文件缺失、键写错、或者别人 clone 下来还没建配置时，行为与从前完全一致——
    配置是用来「调」的，不是用来「让程序能跑」的。
    把默认值搬进 json 会让缺文件变成一种新的失败模式，而这个项目今天已经有太多静默失败了。
    """
    global _CONF
    if _CONF is None:
        f = CONFIG / "project.json"
        try:
            _CONF = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except json.JSONDecodeError as e:
            raise SystemExit(f"FAIL {f} 不是合法 JSON：{e}")
    cur = _CONF
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def model_revision(repo_id: str) -> str | None:
    """本地缓存里这个模型的 commit sha。取不到返回 None。

    **索引必须自描述模型身份**（ADR-0003）。理由是这类不一致不会自己暴露：
    维度不同会崩，那算运气好；**同维度换 backbone 不会崩**——余弦照样算得出来，
    分数照样落在看起来正常的区间，照样过阈值、照样返回 Top-K、照样渲染出片。
    视觉模型这个风险比文本大得多：CLIP ViT-B/32 与 B/16 都是 512 维。

    从本地缓存目录名读，不走网络：建索引时可能没网，而没网不该导致元信息缺一块。
    """
    root = Path(os.environ.get("HF_HUB_CACHE", str(MODELS / "hub")))
    d = root / f"models--{repo_id.replace('/', '--')}"
    ref = d / "refs" / "main"
    if ref.exists():
        return ref.read_text(encoding="utf-8").strip()
    snaps = sorted((d / "snapshots").glob("*")) if (d / "snapshots").is_dir() else []
    return snaps[-1].name if snaps else None


def require_data() -> Path:
    """`data/` 不可达就立刻退出，**绝不自动创建**（CLAUDE.md「存储约定」）。

    与 preflight.sh 的 require_data 同义，Python 侧的等价物。

    三种失败分开报，因为修法完全不同：还没建 / 盘没挂 / 建了但骨架不全。
    2026-07-30 之前这里只认符号链接，于是没有外置盘、把 data 建成真实目录的人
    拿到的提示是「可能已被误建成本地目录」——**指错了方向，而且是唯一的提示**。
    存放位置是使用者的选择，符号链接只是其中一种放法。
    """
    if not DATA.exists():           # 跟随符号链接，悬空即为 False
        if DATA.is_symlink():
            hint = conf("storage.volume_hint", "外置盘")
            raise SystemExit(
                f"FAIL {DATA} 是悬空的符号链接，指向 {os.readlink(DATA)}\n"
                f"     插上{hint} 再跑。别 mkdir——那会在挂载点里建出实体目录，"
                f"盘插上之后反而看不见"
            )
        raise SystemExit(
            f"FAIL {DATA} 不存在。先建存储骨架：\n"
            f"     ./pipeline/preflight.sh --init [外置盘上的目标目录]"
        )
    if not (DATA / "library").is_dir():
        raise SystemExit(
            f"FAIL {DATA} 在，但 {DATA / 'library'} 不在——骨架不全，或者指错了位置\n"
            f"     ./pipeline/preflight.sh --init [外置盘上的目标目录] 会补齐"
        )
    return DATA
