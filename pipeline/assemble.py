"""企划总装：各卷成片 → 合集成片（D81 D，`docs/dev/plans/2026-10-12-project-layer-spec.md` §10）。

把 EGOIST 典藏版的 `assemble.sh` 泛化成工序：部件清单从卷表读，流参数跟随第一卷，不再写死。
步骤与 `assemble.sh` 一一对应：前置检查 → 删减清单/截断点 → 过场卡 → 截尾/中段切除 → 参数自检 → concat → 自证 → 账本 → qc。

卷表「片尾」之外的人定刀口（中段切除 [t0,t1)、定点截尾）写在合集期目录的 `cuts.json`，形状与语义见
`_load_cuts` docstring；定点截尾与 `余韵N秒` 取严（小者），中段切除在截尾后剩余区间内生效、
缝口淡出淡入可配（默认 0.5s，常用 0.5–1s）。多段保留窗口重编码拼接（流拷贝跨不过切口）；
仍是单段前缀的走原 trim_argv 路径，`完整保留` 且无删减的卷照原样 `-c copy`。

带着 EGOIST 的三条教训（`assemble.sh` 文件头原则 1–4）：
1. 绝不重配 TTS、绝不重渲分期主体：`余韵N秒` 的卷只截尾 + 淡出（必须重编码），`完整保留` 的卷原样 `-c copy`；
2. 截断点 = `music.build_timeline` 的旁白终点 + 卷表余韵秒数，不写预测值（二期预测值差 44 秒）；
3. 每个部件的流签名必须与基准逐项相等，否则 `concat -c copy` 会静默损坏音频；
4. 总装后自证：总长 = 部件之和、全片解码零报错（流拷贝拼错不报错，只在播放时炸）。

用法：python -m pipeline.assemble <企划目录> [--out <目录>] [--replace]
  默认输出到卷表 `合集:` 指向的成员；已有 05-final.mp4 必须带 --replace 才覆盖（不留底）。
退出码：0 = 完成且 qc 通过（qc 的退出码透传）；1 = 签名不一致 / 自证失败 / 输入在总装期间变了 / ffmpeg 失败；
2 = 前置检查不过（在任何 ffmpeg 与任何文件创建之前）；3 = 另一个总装正持有输出目录的锁。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import paths
from . import project as project_mod

# 取值全部来自 EGOIST 典藏版已交付的 assemble.sh（验收要求与典藏版逐项一致，改了就对不上）
CARD_SECONDS = 3                    # `CARD_DUR=3`
CARD_FADE_S = 0.5                   # 过场卡 `fade=t=in:st=0:d=0.5` / `fade=t=out:…:d=0.5`
TRIM_FADE_S = 1.0                   # `FADE=1.0`：截断点前音画淡出
CUTS_FILE = "cuts.json"             # 合集期目录的人定删减清单（_load_cuts 的形状与语义）
SEAM_FADE_S = 0.5                   # 切口淡出/淡入默认秒数（删减清单可用 seam_fade_s 覆盖，常用 0.5–1）
SEAM_FADE_MAX_S = 10.0              # 形状检查上限：更长的「淡入淡出」多半不是缝口参数
SHORT_REMAIN_WARN_S = 30.0          # 切后剩余短于此值给警告（多半是人定刀口写错位数）
TRIM_VIDEO_ARGS = ("-c:v", "libx264", "-preset", "medium", "-crf", "18")
TRIM_AUDIO_BITRATE = "192k"
COLOR_RANGE = "tv"                  # `-color_range tv`（不在签名字段里，照脚本原值）
DURATION_TOLERANCE_S = 0.1          # 自证「总长 = 部件之和」的容差（`$GOT-$SUM<0.1`），也是验收的时长差上限
ASSEMBLE_FREE_FACTOR = 2.0          # 输出 ≈ 部件之和，截尾件 ≤ 部件之和：两者相加 ≤ 2 倍（EGOIST 实测 1.66 / 1.82 GB）

# 流签名字段：与 assemble.sh 的 stream_sig() 同一组
SIG_FIELDS = ("codec_type", "codec_name", "profile", "level", "pix_fmt", "width", "height", "r_frame_rate",
              "channels", "channel_layout", "sample_rate")

FINAL = "05-final.mp4"
COVER = "08-cover.jpg"
SCRIPT = "02-script.md"
MANIFEST = ("03-audio", "manifest.json")
LEDGER = "04-assembly.json"
LOCK = ("_agent", "assemble.lock")
TMP_FINAL = ".05-final.assembling.mp4"

Runner = Callable[..., subprocess.CompletedProcess]


def default_runner(argv: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    """一律 argv、不经 shell（spec §10.3 ⑦）。"""
    return subprocess.run(argv, capture_output=capture, text=True, check=False)


class AssembleError(Exception):
    def __init__(self, code: int, msg: str) -> None:
        super().__init__(msg)
        self.code = code


@dataclass
class Part:
    seq: int
    name: str                 # 卷目录名
    member: Path              # 成员目录（字面路径）
    final: Path               # resolve 后的 05-final.mp4
    cover: Path               # resolve 后的 08-cover.jpg
    tail_s: int | None        # None = 完整保留
    vo_end: float | None = None
    cut: str | None = None    # 截断点（两位小数的字符串，与 assemble.sh 的 printf "%.2f" 同形）
    kept: list[tuple[float, float]] | None = None   # 多段保留窗口（成片时间轴）：None=非中段切除路径
    tail_cut: bool = False    # 片尾被截（余韵 / 定点）：kept 末段收尾要补 TRIM_FADE_S 淡出
    seam: float = SEAM_FADE_S
    note: str | None = None   # 人定刀口描述（卡面与日志展示；纯余韵截尾为 None，走既有文案）


@dataclass
class Plan:
    project_dir: Path
    project_name: str
    out_dir: Path             # resolve 后
    target: Path              # out_dir / 05-final.mp4
    replace: bool
    target_exists: bool
    parts: list[Part] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 前置检查（不跑 ffmpeg、不建任何文件）


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _member_file(member: Path, *rel: str) -> Path:
    """成员目录里的部件文件：必须存在，且 resolve 后落在成员目录的 resolve() 之内（成员可以是软链卷）。"""
    p = member.joinpath(*rel)
    if not p.is_file():
        raise AssembleError(2, f"缺 {member.name}/{'/'.join(rel)}")
    real, root = p.resolve(), member.resolve()
    if not _inside(real, root):
        raise AssembleError(2, f"{member.name}/{'/'.join(rel)} 指向成员目录之外（{real}），拒绝")
    return real


def _load_cuts(path: Path) -> dict[str, dict]:
    """删减清单（合集期目录 `cuts.json`）：按卷目录名给人定刀口，形状错一律退 2。

    ```
    {"seam_fade_s": 0.5,                          # 可选：缝口淡出/淡入秒数，默认 SEAM_FADE_S
     "volumes": {"02-…": {"tail_at": 1902.0},     #   定点截尾（成片时间轴绝对秒数）
                 "03-…": {"remove": [[t0, t1],  #   中段切除 [t0,t1)，可多段
                                       …], "seam_fade_s": 0.5}}}
    ```

    返回以 name_key(卷目录名) 为键的 spec 表；`_` 前缀键当注释忽略，其余未知键报错（防笔误）。
    窗口重叠 / 倒序 / 非数字在这里拦；越界、与余韵截尾的关系依赖成片时间轴，在 _apply_cuts 里查。
    """
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssembleError(2, f"{CUTS_FILE} 读不了或不是合法 JSON：{exc}") from None
    if not isinstance(doc, dict):
        raise AssembleError(2, f"{CUTS_FILE} 顶层须为对象")

    def num(v: object, what: str) -> float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise AssembleError(2, f"{CUTS_FILE}：{what} 须为数字，实为 {v!r}")
        return float(v)

    def seam_of(v: object, what: str) -> float:
        s = num(v, what)
        if not 0 < s <= SEAM_FADE_MAX_S:
            raise AssembleError(2, f"{CUTS_FILE}：{what} 须在 (0, {SEAM_FADE_MAX_S:g}] 秒，实为 {s:g}")
        return s

    bad_top = set(doc) - {"seam_fade_s", "volumes"} - {k for k in doc if k.startswith("_")}
    if bad_top:
        raise AssembleError(2, f"{CUTS_FILE}：未知顶层键 {sorted(bad_top)}（注释请用 `_` 前缀键）")
    default_seam = seam_of(doc["seam_fade_s"], "seam_fade_s") if "seam_fade_s" in doc else SEAM_FADE_S
    vols = doc.get("volumes", {})
    if not isinstance(vols, dict):
        raise AssembleError(2, f"{CUTS_FILE}：`volumes` 须为对象（键 = 卷目录名）")
    specs: dict[str, dict] = {}
    for name, spec in vols.items():
        key = project_mod.name_key(name)
        if key in specs:
            raise AssembleError(2, f"{CUTS_FILE}：卷 {name!r} 出现两次（大小写 / Unicode 规范化后相同）")
        if not isinstance(spec, dict):
            raise AssembleError(2, f"{CUTS_FILE}：{name!r} 的条目须为对象")
        bad = set(spec) - {"remove", "tail_at", "seam_fade_s"} - {k for k in spec if k.startswith("_")}
        if bad:
            raise AssembleError(2, f"{CUTS_FILE}：{name!r} 的条目有未知键 {sorted(bad)}")
        out = {"dir": name, "remove": [], "tail_at": None,
               "seam": seam_of(spec["seam_fade_s"], f"{name!r}.seam_fade_s") if "seam_fade_s" in spec else default_seam}
        removes = spec.get("remove", [])
        if not isinstance(removes, list):
            raise AssembleError(2, f"{CUTS_FILE}：{name!r} 的 remove 须为窗口列表 [[t0, t1], …]")
        for w in removes:
            if not isinstance(w, list) or len(w) != 2:
                raise AssembleError(2, f"{CUTS_FILE}：{name!r} 的 remove 窗口须为 [t0, t1] 两元素，实为 {w!r}")
            t0, t1 = num(w[0], f"{name!r}.remove t0"), num(w[1], f"{name!r}.remove t1")
            if t0 < 0:
                raise AssembleError(2, f"{CUTS_FILE}：{name!r} 的 remove 起点 {t0:g}s 为负")
            if t0 >= t1:
                raise AssembleError(2, f"{CUTS_FILE}：{name!r} 的 remove 窗口倒序或零长：[{t0:g}, {t1:g})")
            out["remove"].append((t0, t1))
        if "tail_at" in spec:
            t = num(spec["tail_at"], f"{name!r}.tail_at")
            if t <= 0:
                raise AssembleError(2, f"{CUTS_FILE}：{name!r} 的 tail_at 须为正数秒，实为 {t:g}")
            out["tail_at"] = t
        if not out["remove"] and out["tail_at"] is None:
            raise AssembleError(2, f"{CUTS_FILE}：{name!r} 的条目没有 remove / tail_at，无删减内容")
        specs[key] = out
    return specs


def _apply_cuts(part: Part, spec: dict | None, warns: list[str]) -> None:
    """在卷成片时间轴上算保留窗口：定点截尾与 `余韵N秒` 截尾取严（小者），中段切除窗口一律生效。

    结果三态：净保留 = 全片 → cut/kept 都不设（-c copy）；净保留 = 前缀 [0, cut) → part.cut
    （走既有 trim_argv 单段截尾路径）；多段 → part.kept（filter_complex 重编码拼接）。
    截断点 = build_timeline 的旁白终点 + 余韵秒数（assemble.sh 第 1/5 步原样），不写预测值。
    """
    from . import music

    manifest = json.loads(part.member.joinpath(*MANIFEST).read_text(encoding="utf-8"))
    try:
        tl = music.build_timeline(part.member, manifest, music.load_tracks_multi(part.member))
    except SystemExit as exc:
        raise AssembleError(2, f"{part.name} 的成片时间轴算不出来：{exc}") from None
    total = tl["total_duration"]
    part.vo_end = max(s["start"] + s["dur"] for s in tl["segments"])

    rel = part.vo_end + float(part.tail_s) if part.tail_s is not None else None
    if rel is not None and rel >= total:
        raise AssembleError(2, f"{part.name}：截断点 {rel:.2f}s 超过成片总长 {total:.2f}s")
    tail_at = spec["tail_at"] if spec else None
    if tail_at is not None and tail_at >= total:
        raise AssembleError(2, f"{part.name}：定点截尾 {tail_at:g}s 不在成片（总长 {total:.2f}s）内")
    if rel is not None and tail_at is not None and tail_at >= rel:
        warns.append(f"{part.name}：定点截尾 {tail_at:g}s 晚于余韵截尾 {rel:.2f}s，不生效（取小者）")
    cands = [x for x in (rel, tail_at) if x is not None]
    end = min(cands) if cands else total

    removes = sorted(spec["remove"]) if spec else []
    for i in range(1, len(removes)):
        if removes[i][0] < removes[i - 1][1]:
            raise AssembleError(2, f"{part.name}：remove 窗口 [{removes[i - 1][0]:g},{removes[i - 1][1]:g})"
                                   f" 与 [{removes[i][0]:g},{removes[i][1]:g}) 重叠")
    active: list[tuple[float, float]] = []
    for t0, t1 in removes:
        if t1 > total:
            raise AssembleError(2, f"{part.name}：remove 窗口 [{t0:g},{t1:g}) 越过成片总长 {total:.2f}s")
        if t0 >= end:
            warns.append(f"{part.name}：remove 窗口 [{t0:g},{t1:g}) 完全在截尾点 {end:.2f}s 之后，不生效")
            continue
        if t1 > end:
            warns.append(f"{part.name}：remove 窗口 [{t0:g},{t1:g}) 越过截尾点，按 [{t0:g},{end:.2f}) 生效")
            t1 = end
        active.append((t0, t1))

    kept, cur = [], 0.0
    for t0, t1 in active:
        if t0 > cur:
            kept.append((cur, t0))
        cur = t1
    if cur < end:
        kept.append((cur, end))
    if not kept:
        raise AssembleError(2, f"{part.name}：中段切除窗口把全片切空，没有剩余内容")

    # 末保留段终点才是实际截尾点：remove 窗口可以吃掉截尾点前的尾部，使成片比 end 更短
    eff_end = kept[-1][1]
    part.tail_cut = eff_end < total - 1e-9
    remain = sum(c - a for a, c in kept)
    if remain < SHORT_REMAIN_WARN_S:
        warns.append(f"{part.name}：切后剩余 {remain:.2f}s 过短（<{SHORT_REMAIN_WARN_S:g}s），确认刀口没写错")
    for a, c in kept[1:-1]:
        if c - a < 2 * part.seam:
            warns.append(f"{part.name}：保留段 [{a:g},{c:g}) 长 {c - a:.2f}s，短于缝口淡入淡出之和 {2 * part.seam:g}s")

    desc: list[str] = []
    if active:
        desc.append("中段切除 " + "、".join(f"{a:g}–{c:g}s" for a, c in active)
                    + f"（缝口 {part.seam:g}s 淡出淡入）")
    if part.tail_cut:
        if tail_at is not None and end == tail_at:
            t = f"定点截到 {eff_end:.2f}s"
            if rel is not None:
                t += f"（余韵截尾 {rel:.2f}s 更宽，取定点）"
        elif rel is not None:
            t = f"截到 {eff_end:.2f}s（旁白终点 {part.vo_end:.2f}s + 余韵 {part.tail_s} 秒）"
        else:
            t = f"末段随切除止于 {eff_end:.2f}s"   # 完整保留卷被 remove 窗口吃到片尾
        if eff_end < end - 1e-9:
            t += f"（末段切除至 {eff_end:.2f}s，先于截尾点 {end:.2f}s）"
        desc.append(t + f"；末 {TRIM_FADE_S:g}s 淡出")

    if len(kept) == 1 and kept[0][0] == 0.0:
        if part.tail_cut:
            part.cut = f"{eff_end:.2f}"
        # 净保留 = 全片：cut/kept 都不设，照原样 -c copy
    else:
        part.kept = kept
    if spec is not None:
        part.note = "；".join(desc) if desc else "删减清单各项均不生效，原样保留"


def plan(project_dir: Path, out: Path | None, replace: bool) -> Plan:
    pdir = Path(project_dir)
    if project_mod.classify_dir(pdir) != "project":
        raise AssembleError(2, f"不是企划目录：{pdir}")
    doc = project_mod.load(pdir)
    if doc is None:
        raise AssembleError(2, f"企划 {pdir.name} 没有 {project_mod.PROJECT_FILE}（部件清单从卷表读）")
    if isinstance(doc, project_mod.ParseError):
        raise AssembleError(2, f"企划 {pdir.name} 的 {project_mod.PROJECT_FILE} 解析失败：{doc}")
    if not doc.volumes:
        raise AssembleError(2, f"企划 {pdir.name} 的卷表为空")

    parts: list[Part] = []
    for v in sorted(doc.volumes, key=lambda v: v.seq):
        member = pdir / v.dir
        if project_mod.classify_dir(member) != "volume":
            raise AssembleError(2, f"卷表第 {v.seq} 卷 `{v.dir}` 不是本企划的成员（目录不存在或没有 01-topic.md）")
        part = Part(v.seq, v.dir, member, _member_file(member, FINAL), _member_file(member, COVER), v.tail_s)
        parts.append(part)

    members_real = [p.member.resolve() for p in parts]
    if out is None:
        if doc.collection is None:
            raise AssembleError(2, "卷表没写 `合集:`，又没给 --out：不知道输出到哪")
        coll = pdir / doc.collection
        if project_mod.classify_dir(coll) != "volume":
            raise AssembleError(2, f"合集 `{doc.collection}` 不存在或没有 01-topic.md（qc 读它的 `时长目标`）")
        out_dir = coll.resolve()
    else:
        if not Path(out).is_dir():
            raise AssembleError(2, f"--out 目录不存在（不自动建）：{out}")
        out_dir = Path(out).resolve()
        if out_dir == pdir.resolve():
            raise AssembleError(2, "--out 不能是企划目录本身")
        if not (out_dir / project_mod.TOPIC_FILE).is_file():
            raise AssembleError(2, "--out 目录没有 01-topic.md（qc 读它的 `时长目标`）")
    if any(_inside(out_dir, m) for m in members_real):
        raise AssembleError(2, f"输出目录落在卷目录里（{out_dir}）：总装不往任何一卷里写")
    target = out_dir / FINAL
    if target.is_symlink() or (target.exists() and not _inside(target.resolve(), out_dir)):
        raise AssembleError(2, f"{FINAL} 是软链，拒绝覆盖（输出必须落在输出目录内）")
    exists = target.exists()
    if exists and not replace:
        raise AssembleError(2, f"输出目录已有 {FINAL}：要覆盖须带 --replace（不留底）")

    # 人定删减清单：住在卷表 `合集:` 指向的期目录；--out 别处时仍以合集期目录为准（规格属于合集）。
    cuts_dir = (pdir / doc.collection) if doc.collection and (pdir / doc.collection).is_dir() else out_dir
    specs = _load_cuts(cuts_dir / CUTS_FILE)
    listed = {project_mod.name_key(v.dir) for v in doc.volumes}
    for spec in specs.values():
        if project_mod.name_key(spec["dir"]) not in listed:
            raise AssembleError(2, f"{CUTS_FILE}：{spec['dir']!r} 不在卷表里（删减清单只写成员卷目录名）")

    warns: list[str] = []
    for part in parts:
        spec = specs.get(project_mod.name_key(part.name))
        if spec is not None:
            part.seam = spec["seam"]
        if part.tail_s is None and spec is None:
            continue
        _member_file(part.member, SCRIPT)      # 时间轴依赖稿件 + manifest：余韵截尾与删减清单同需
        _member_file(part.member, *MANIFEST)
        _apply_cuts(part, spec, warns)
    return Plan(pdir, doc.name, out_dir, target, replace, exists, parts, warns)


def _check_space(pl: Plan) -> None:
    need = ASSEMBLE_FREE_FACTOR * sum(p.final.stat().st_size for p in pl.parts)
    free = shutil.disk_usage(pl.out_dir).free
    if free < need:
        raise AssembleError(2, f"输出目录所在卷空闲 {free / 1e9:.2f} GB < 需要 {need / 1e9:.2f} GB"
                               f"（{ASSEMBLE_FREE_FACTOR:g} × 部件源文件之和）")


def _rel(p: Path) -> str:
    for base in (paths.ROOT, paths.ROOT.resolve()):
        try:
            return str(p.relative_to(base))
        except ValueError:
            continue
    return str(p)


def preview_lines(pl: Plan) -> list[str]:
    lines = []
    if pl.replace and pl.target_exists:
        lines.append(f"⚠ 将覆盖已有 {FINAL}（不留底）")
    lines.append(f"总装 企划「{pl.project_name}」（{pl.project_dir.name}）→ {_rel(pl.target)}")
    for i, p in enumerate(pl.parts, 1):
        lines.append(f"  card_part{i} ← {p.name}/{COVER}（{CARD_SECONDS} 秒过场卡）")
        if p.kept is not None:
            lines.append(f"  part{i}_cut ← {p.name}/{FINAL}：{p.note}")
        elif p.cut is not None:
            desc = p.note or (f"截到 {p.cut}s（旁白终点 {p.vo_end:.2f}s + 余韵 {p.tail_s} 秒，"
                              f"末 {TRIM_FADE_S:g}s 淡出）")
            lines.append(f"  part{i}_trimmed ← {p.name}/{FINAL} {desc}")
        else:
            lines.append(f"  {p.name} ← {p.name}/{FINAL}（完整保留，-c copy）")
    lines.extend(f"  ⚠ {w}" for w in pl.warnings)
    return lines


class _ParseFail(Exception):
    pass


class _QuietParser(argparse.ArgumentParser):
    """卡面预检用：参数错不打印、不退出，交给审查层当拒因。"""

    def error(self, message: str):  # type: ignore[override]
        raise _ParseFail(message)


def _parser(cls: type[argparse.ArgumentParser] = argparse.ArgumentParser) -> argparse.ArgumentParser:
    ap = cls(prog="python -m pipeline.assemble", description=__doc__.splitlines()[0],
             allow_abbrev=False)   # `--rep` 不被认作 --replace
    ap.add_argument("project", type=Path, help="企划目录")
    ap.add_argument("--out", type=Path, default=None, help="输出目录（默认卷表 `合集:` 指向的成员；必须已存在）")
    ap.add_argument("--replace", action="store_true", help=f"覆盖已有 {FINAL}（不留底）")
    return ap


def preview(rest: list[str]) -> tuple[bool, list[str]]:
    """审批卡卡面（spec §10.3 ⑭）：部件、截断点、输出位置、是否覆盖。不成立 → (False, [拒因])，弹卡前拒。"""
    try:
        a = _parser(_QuietParser).parse_args(rest)
    except _ParseFail as exc:
        return False, [f"assemble 参数不成立：{exc}"]
    try:
        return True, preview_lines(plan(a.project, a.out, a.replace))
    except AssembleError as exc:
        return False, [f"assemble 前置检查不过：{exc}"]


# ---------------------------------------------------------------- ffprobe / ffmpeg（一律 argv）


def probe_streams(path: Path, runner: Runner) -> list[dict]:
    p = runner(["ffprobe", "-v", "error", "-show_entries", "stream=" + ",".join(SIG_FIELDS), "-of", "json", str(path)])
    if p.returncode != 0:
        raise AssembleError(1, f"ffprobe 读不了 {path}：{p.stderr.strip()}")
    return json.loads(p.stdout).get("streams", [])


def signature(streams: list[dict]) -> list[tuple]:
    return [tuple(s.get(k) for k in SIG_FIELDS) for s in streams]


def probe_duration(path: Path, runner: Runner) -> float:
    p = runner(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    if p.returncode != 0:
        raise AssembleError(1, f"ffprobe 读不了 {path}：{p.stderr.strip()}")
    return float(p.stdout.strip())


@dataclass(frozen=True)
class Base:
    """基准流参数（第一卷 05-final.mp4 探出来的），过场卡与截尾件按它生成。"""
    width: int
    height: int
    fps: str
    pix_fmt: str
    channels: int
    layout: str
    sample_rate: str


def base_from(streams: list[dict]) -> Base:
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None or a is None:
        raise AssembleError(1, "第一卷成片缺视频流或音频流，定不了基准")
    ch = int(a["channels"])
    layout = a.get("channel_layout") or {1: "mono", 2: "stereo"}.get(ch)
    if not layout:
        raise AssembleError(1, f"第一卷音频 {ch} 声道且没有声道布局，过场卡的静音轨定不了")
    return Base(int(v["width"]), int(v["height"]), v["r_frame_rate"], v["pix_fmt"], ch, layout,
                str(a["sample_rate"]))


def card_argv(cover: Path, out: Path, b: Base) -> list[str]:
    fade_out = f"{CARD_SECONDS - CARD_FADE_S:.2f}"
    return [
        "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", str(CARD_SECONDS), "-i", str(cover),
        "-f", "lavfi", "-t", str(CARD_SECONDS), "-i", f"anullsrc=r={b.sample_rate}:cl={b.layout}",
        "-map", "0:v", "-map", "1:a",
        "-vf", (f"scale={b.width}:{b.height}:force_original_aspect_ratio=increase,crop={b.width}:{b.height},"
                f"fps={b.fps},format={b.pix_fmt},fade=t=in:st=0:d={CARD_FADE_S},"
                f"fade=t=out:st={fade_out}:d={CARD_FADE_S}"),
        *TRIM_VIDEO_ARGS, "-color_range", COLOR_RANGE,
        "-c:a", "aac", "-b:a", TRIM_AUDIO_BITRATE, "-ac", str(b.channels), "-ar", b.sample_rate, "-shortest", str(out),
    ]


def trim_argv(src: Path, out: Path, cut: str, b: Base) -> list[str]:
    st = f"{float(cut) - TRIM_FADE_S:.2f}"
    return [
        "ffmpeg", "-y", "-loglevel", "error", "-ss", "0", "-to", cut, "-i", str(src),
        "-vf", f"fade=t=out:st={st}:d={TRIM_FADE_S}", "-af", f"afade=t=out:st={st}:d={TRIM_FADE_S}",
        *TRIM_VIDEO_ARGS, "-pix_fmt", b.pix_fmt, "-color_range", COLOR_RANGE, "-r", b.fps,
        "-c:a", "aac", "-b:a", TRIM_AUDIO_BITRATE, "-ac", str(b.channels), "-ar", b.sample_rate, str(out),
    ]


def cut_argv(src: Path, out: Path, kept: list[tuple[float, float]], seam: float,
             tail_cut: bool, b: Base) -> list[str]:
    """中段切除：各保留段 trim 后缝口淡出/淡入，concat 滤波一次重编码（流拷贝跨不过切口）。

    缝口参数 seam：切口左侧 fade out + 右侧 fade in，各 seam 秒（删减清单 `seam_fade_s`，文档常用 0.5–1s）。
    末保留段被截尾时尾部补 TRIM_FADE_S 淡出（与 trim_argv 的 1s 截尾淡出同一语义）；完整保留的末段自然收尾。
    """
    v_parts, a_parts, n = [], [], len(kept)
    for i, (a, c) in enumerate(kept):
        dur = c - a
        v = f"[0:v]trim=start={a:.2f}:end={c:.2f},setpts=PTS-STARTPTS"
        au = f"[0:a]atrim=start={a:.2f}:end={c:.2f},asetpts=PTS-STARTPTS"
        if a > 0:                                        # 切口右侧淡入
            v += f",fade=t=in:st=0:d={seam:g}"
            au += f",afade=t=in:st=0:d={seam:g}"
        if i + 1 < n:                                    # 切口左侧淡出
            st = max(dur - seam, 0.0)
            v += f",fade=t=out:st={st:.2f}:d={seam:g}"
            au += f",afade=t=out:st={st:.2f}:d={seam:g}"
        elif tail_cut:                                   # 截尾末端淡出（同 trim_argv 的 1s）
            st = max(dur - TRIM_FADE_S, 0.0)
            v += f",fade=t=out:st={st:.2f}:d={TRIM_FADE_S:g}"
            au += f",afade=t=out:st={st:.2f}:d={TRIM_FADE_S:g}"
        v_parts.append(v + f"[v{i}]")
        a_parts.append(au + f"[a{i}]")
    fc = (";".join(v_parts + a_parts)
          + ";" + "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
          + ";" + "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]")
    return ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
            "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
            *TRIM_VIDEO_ARGS, "-pix_fmt", b.pix_fmt, "-color_range", COLOR_RANGE, "-r", b.fps,
            "-c:a", "aac", "-b:a", TRIM_AUDIO_BITRATE, "-ac", str(b.channels), "-ar", b.sample_rate, str(out)]


def concat_argv(listing: Path, out: Path) -> list[str]:
    return ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
            str(out)]


def concat_line(path: Path) -> str:
    """concat demuxer 的一行：单引号包裹，内部 `'` 写成 `'\\''`；换行 / 控制字符直接拒（会多出一条指令）。"""
    s = str(path)
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in s):
        raise AssembleError(2, f"部件路径含换行或控制字符，拼接清单写不安全：{s!r}")
    return "file '" + s.replace("'", "'\\''") + "'\n"


def _ffmpeg(argv: list[str], runner: Runner, what: str) -> None:
    p = runner(argv)
    if p.returncode != 0:
        raise AssembleError(1, f"{what} 失败（ffmpeg 退出码 {p.returncode}）：{(p.stderr or '').strip()[-800:]}")


# ---------------------------------------------------------------- 主流程


def _fingerprint(files: list[Path]) -> dict[Path, tuple[int, int]]:
    return {f: (f.stat().st_size, f.stat().st_mtime_ns) for f in files}


def _lock(out_dir: Path) -> int:
    d = out_dir.joinpath(*LOCK[:-1])
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(d / LOCK[-1]), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise AssembleError(3, f"另一个总装正在用输出目录 {out_dir}（{'/'.join(LOCK)} 被占用）") from None
    return fd


def run(pl: Plan, runner: Runner = default_runner, log: Callable[[str], None] = print) -> int:
    _check_space(pl)
    for p in pl.parts:   # 拼接清单的每个路径先验（任何文件创建之前）
        concat_line(p.final)
    fd = _lock(pl.out_dir)
    tmp = pl.out_dir / TMP_FINAL
    try:
        sources = [f for p in pl.parts for f in (p.final, p.cover)]
        before = _fingerprint(sources)
        base_streams = probe_streams(pl.parts[0].final, runner)
        base = base_from(base_streams)
        cards, work = pl.out_dir / "cards", pl.out_dir / "work"
        cards.mkdir(exist_ok=True)
        work.mkdir(exist_ok=True)

        log("=== 保留窗口（删减清单 + 卷表片尾）===")
        for p in pl.parts:
            if p.kept is not None:
                desc = "保留 " + " ∪ ".join(f"[{a:.2f},{c:.2f})" for a, c in p.kept)
            elif p.cut is not None:
                desc = f"截到 {p.cut}s"
            else:
                desc = "完整保留"
            log(f"  {p.name} → {desc}")
        for w in pl.warnings:
            log(f"  ⚠ {w}")

        assembly: list[tuple[str, str, Path, Path | None]] = []   # (name, kind, 文件, src)
        for i, p in enumerate(pl.parts, 1):
            card = cards / f"card_part{i}.mp4"
            _ffmpeg(card_argv(p.cover, card, base), runner, f"过场卡 card_part{i}")
            assembly.append((f"card_part{i}", "card", card, None))
            if p.kept is not None:
                cut = work / f"part{i}_cut.mp4"
                _ffmpeg(cut_argv(p.final, cut, p.kept, p.seam, p.tail_cut, base), runner, f"切除 part{i}_cut")
                assembly.append((f"part{i}_cut", "part", cut, p.member.resolve()))
            elif p.cut is not None:
                trimmed = work / f"part{i}_trimmed.mp4"
                _ffmpeg(trim_argv(p.final, trimmed, p.cut, base), runner, f"截尾 part{i}_trimmed")
                assembly.append((f"part{i}_trimmed", "part", trimmed, p.member.resolve()))
            else:
                assembly.append((p.name, "part", p.final, p.member.resolve()))

        log("=== 参数一致性自检 ===")
        want = signature(base_streams)
        for name, _k, f, _s in assembly:
            got = signature(probe_streams(f, runner))
            if got != want:
                diff = [f"  - {','.join(map(str, w))}" for w in want] + [f"  + {','.join(map(str, g))}" for g in got]
                raise AssembleError(1, f"编码参数不一致，禁止流拷贝拼接：{name}\n" + "\n".join(diff))
            log(f"  ✓ {name}")

        listing = work / "concat.txt"
        listing.write_text("".join(concat_line(f) for _n, _k, f, _s in assembly), encoding="utf-8")
        _ffmpeg(concat_argv(listing, tmp), runner, "concat 拼接")

        log("=== 自证：总长 = 部件之和、全片解码零报错 ===")
        durs = [probe_duration(f, runner) for _n, _k, f, _s in assembly]
        got_total = probe_duration(tmp, runner)
        if abs(got_total - sum(durs)) >= DURATION_TOLERANCE_S:
            raise AssembleError(1, f"总装时长 {got_total:.3f}s ≠ 部件之和 {sum(durs):.3f}s（容差 {DURATION_TOLERANCE_S}s）")
        dec = runner(["ffmpeg", "-v", "error", "-i", str(tmp), "-f", "null", "-"])
        detail = ((dec.stdout or "") + (dec.stderr or "")).strip()
        if detail or dec.returncode != 0:
            raise AssembleError(1, "全片解码有报错：\n" + "\n".join(detail.splitlines()[:5]))

        if _fingerprint(sources) != before:
            changed = [str(f) for f, fp in before.items() if not f.exists() or _fingerprint([f])[f] != fp]
            raise AssembleError(1, "总装期间部件源文件变了（被重渲？），不替换、不写账本：" + "、".join(changed))
        os.replace(tmp, pl.target)

        parts, t = [], 0.0
        for (name, kind, f, src), d in zip(assembly, durs):
            rec = {"name": name, "path": str(f.resolve()), "kind": kind, "offset": round(t, 3), "duration": round(d, 3)}
            if src is not None:
                rec["src"] = str(src)
            parts.append(rec)
            t += d
        paths.atomic_write(pl.out_dir / LEDGER, json.dumps(
            {"total_duration": got_total, "parts": parts,
             "_note": "总装账本：部件实测窗口，由 pipeline.assemble 生成。别手改——"
                      "qc 的黑帧归因与静音豁免按 offset/duration 定位。"},
            ensure_ascii=False, indent=2))
        log(f"  总长 {got_total:.2f}s，{len(parts)} 个部件，解码零报错；账本 {LEDGER}")
    finally:
        if tmp.exists():
            tmp.unlink()   # 自证没过的临时成片（本工序自己写的），旧成片原样保留
        os.close(fd)

    log("=== 收尾：pipeline.qc ===")
    return runner([sys.executable, "-m", "pipeline.qc", str(pl.out_dir)], capture=False).returncode


def main(argv: list[str] | None = None, runner: Runner = default_runner) -> int:
    a = _parser().parse_args(argv)
    try:
        pl = plan(a.project, a.out, a.replace)
        for line in preview_lines(pl):
            print(line)
        return run(pl, runner)
    except AssembleError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
