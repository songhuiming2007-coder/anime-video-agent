"""看图（D90，ADR-0030）：`view_image` 工具实现 + 请求时附图投影。

为什么附图不放进 tool 消息：本机代理（CLIProxyAPI → Gemini）把 `role:"tool"` 里的 `image_url`
当成文本转发，模型不报错、照样「描述」——描述是编的（2026-10-13 合成图实测：红圆 / 绿方块 /
ZEBRA 73 被说成蓝圆 / 绿三角 / Shape）。同一张图放进 user 消息才认得对。所以工具结果只回尺寸，
图由 `attach_images` 在发请求前以一条宿主 user 消息附在这批工具结果之后。

`attach_images` 与 aging 同一纪律：请求时纯投影，convo 与 session.jsonl 一字不动（base64 不进日志）。
只附**本回合**的图：跨回合背着旧图每轮多几千 token，而且图可能已经变了——要看现状就重新看。
"""

from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path
from typing import Any

# 工具结果里的私有键：`llm._tool_message` 把它移到消息内部键 `IMAGE_KEY`，不进模型看到的 JSON
OUTCOME_IMAGE_KEY = "_image"
# 消息内部键（llm._INTERNAL_KEYS 收录，进请求体前删除）
IMAGE_KEY = "_ava_image"

ALLOWED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_PIXELS = 60_000_000          # 约 8K×7K；再大当解压炸弹拒
SHOWN_LONG_SIDE = 1568           # 发给模型的长边：1080p 封面上手机端可读的字号仍看得清
JPEG_QUALITY = 85
MAX_IMAGES_PER_TURN = 4          # 同一回合最多附最近 4 张（对比两三版够用，token 有上界）

_CACHE: dict[tuple[str, str], tuple[str, int, int]] = {}   # (abs, sha) → (data URL, 宽, 高)
_CACHE_MAX = 16


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _shown_size(width: int, height: int) -> tuple[int, int]:
    long_side = max(width, height)
    if long_side <= SHOWN_LONG_SIDE:
        return width, height
    scale = SHOWN_LONG_SIDE / long_side
    return max(1, round(width * scale)), max(1, round(height * scale))


def view_image(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """`view_image` 工具实现：只认期目录内的图，返回尺寸；图本身由 `attach_images` 附上。

    读域：期目录内（resolve 后再判，软链逃不出去），沿用 read_artifact 的硬排除目录。
    """
    from pipeline.agent.tools import (
        NO_EPISODE_MESSAGE,
        PROJECT_VOLUME_ONLY_MESSAGE,
        READ_DENY_PARTS,
        deny_dir_hit,
    )

    # 两条与 review 层共用文案（NEEDS_EPISODE_TOOLS / PROJECT_VOLUME_ONLY_TOOLS 双保险）
    if ctx.episode_dir is None:
        raise PermissionError(NO_EPISODE_MESSAGE)
    if ctx.session_kind == "project":
        raise PermissionError(PROJECT_VOLUME_ONLY_MESSAGE.format(tool="view_image"))
    raw = str(args.get("path", "")).strip()
    if not raw:
        raise ValueError("path 不能为空")
    rel = Path(raw)
    if rel.is_absolute() or ".." in rel.parts:
        raise PermissionError(f"只看期目录内的图（相对路径，不含 ..）: {raw}")
    root = Path(ctx.episode_dir).resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise PermissionError(f"越界被拒（resolve 后不在期目录内）: {raw}")
    for p in (rel, target):
        if p.suffix.casefold() not in ALLOWED_SUFFIXES:
            raise PermissionError(f"只看图片 {sorted(ALLOWED_SUFFIXES)}，拒看: {raw}")
        hit = deny_dir_hit(p)
        if hit:
            raise PermissionError(
                f"读域硬排除 {hit}/（{sorted(READ_DENY_PARTS)} 与 read_artifact 同一清单），拒看: {raw}"
            )
    if not target.is_file():
        raise FileNotFoundError(f"图片不存在: {raw}")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"图片 {size} 字节，超过上限 {MAX_FILE_BYTES}: {raw}")

    from PIL import Image

    try:
        with Image.open(target) as img:
            width, height = img.size
    except Exception as exc:
        raise ValueError(f"图片打不开: {raw}（{exc}）") from exc
    if width * height > MAX_PIXELS:
        raise ValueError(f"图片 {width}×{height} 像素过大（上限 {MAX_PIXELS}）: {raw}")
    sw, sh = _shown_size(width, height)
    return {
        "path": raw,
        "width": width,
        "height": height,
        "shown": f"{sw}×{sh}",
        "note": "图片附在本批工具结果之后的宿主消息里（只在本回合附；下一回合要看现状请重新调用）",
        OUTCOME_IMAGE_KEY: {"abs_path": str(target), "sha256": _sha256(target)},
    }


def split_outcome(outcome: Any) -> tuple[Any, dict[str, Any] | None]:
    """把工具结果里的私有图片键拆出来：返回 (给模型的结果, 图片引用)。不 mutate 入参。"""
    if not isinstance(outcome, dict):
        return outcome, None
    result = outcome.get("result")
    if not isinstance(result, dict) or OUTCOME_IMAGE_KEY not in result:
        return outcome, None
    image = result.get(OUTCOME_IMAGE_KEY)
    clean = {k: v for k, v in result.items() if k != OUTCOME_IMAGE_KEY}
    return {**outcome, "result": clean}, image if isinstance(image, dict) else None


def _encode(abs_path: str, sha: str) -> tuple[str, int, int] | None:
    """读盘 → 缩放 → JPEG data URL。文件没了或 sha 对不上返回 None（不发旧图冒充现状）。"""
    key = (abs_path, sha)
    if key in _CACHE:
        return _CACHE[key]
    path = Path(abs_path)
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(data).hexdigest() != sha:
        return None
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            rgb = img.convert("RGB")
    except Exception:
        return None
    sw, sh = _shown_size(*rgb.size)
    if (sw, sh) != rgb.size:
        rgb = rgb.resize((sw, sh), Image.LANCZOS)
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=JPEG_QUALITY)
    url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (url, sw, sh)
    return _CACHE[key]


def _turn_boundary(messages: list[Any]) -> int:
    """本回合起点：最后一条真正的人类 user 消息（跳过收尾 / 排队标记，与 aging 同一判据）。"""
    from pipeline.agent import llm

    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if (
            isinstance(m, dict)
            and m.get("role") == "user"
            and llm._WRAPUP_KEY not in m
            and llm.STEER_KEY not in m
        ):
            return i
    return 0


def attach_images(messages: list[Any]) -> list[Any]:
    """请求时附图投影。没有本回合的图 → 原样返回同一个列表对象。

    每一批连续的 tool 消息之后插一条宿主 user 消息（绝不插在 tool 消息中间：那会拆散
    assistant.tool_calls 与结果的配对）。本回合超过 `MAX_IMAGES_PER_TURN` 张时只附最近的，
    更早的那几张只留一行文字。
    """
    start = _turn_boundary(messages)
    refs = [
        i for i in range(start, len(messages))
        if isinstance(messages[i], dict)
        and messages[i].get("role") == "tool"
        and isinstance(messages[i].get(IMAGE_KEY), dict)
    ]
    if not refs:
        return messages
    keep = set(refs[-MAX_IMAGES_PER_TURN:])

    out: list[Any] = []
    pending: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        out.append(m)
        if i in refs:
            ref = m[IMAGE_KEY]
            label = str(ref.get("path") or ref.get("abs_path") or "")
            if i not in keep:
                pending.append({"type": "text", "text": f"{label}：本回合较早看过的图，已省略；需要再看请重新调用 view_image"})
            else:
                enc = _encode(str(ref.get("abs_path", "")), str(ref.get("sha256", "")))
                if enc is None:
                    pending.append({"type": "text", "text": f"{label}：看图之后文件变了或不在了，不附旧图；需要现状请重新调用 view_image"})
                else:
                    url, sw, sh = enc
                    pending.append({"type": "text", "text": f"{label}（发给你的是 {sw}×{sh}）："})
                    pending.append({"type": "image_url", "image_url": {"url": url}})
        nxt = messages[i + 1] if i + 1 < len(messages) else None
        batch_ends = not (isinstance(nxt, dict) and nxt.get("role") == "tool")
        if pending and isinstance(m, dict) and m.get("role") == "tool" and batch_ends:
            out.append({
                "role": "user",
                "content": [{"type": "text", "text": "[宿主附图，不是人发的消息] view_image 的结果："}, *pending],
            })
            pending = []
    return out
