"""企划总装：各卷成片 → 合集成片（D81 D，`docs/dev/plans/2026-10-12-project-layer-spec.md` §10）。

把 EGOIST 典藏版的 `assemble.sh` 泛化成工序：部件清单从卷表读，流参数跟随第一卷，不再写死。
步骤与 `assemble.sh` 一一对应：前置检查 → 截断点 → 过场卡 → 截尾 → 参数自检 → concat → 自证 → 账本 → qc。

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


@dataclass
class Plan:
    project_dir: Path
    project_name: str
    out_dir: Path             # resolve 后
    target: Path              # out_dir / 05-final.mp4
    replace: bool
    target_exists: bool
    parts: list[Part] = field(default_factory=list)


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


def _cut_point(part: Part) -> tuple[float, str]:
    """截断点 = build_timeline 的旁白终点 + 余韵秒数（assemble.sh 第 1/5 步原样）。"""
    from . import music

    manifest = json.loads(part.member.joinpath(*MANIFEST).read_text(encoding="utf-8"))
    try:
        tl = music.build_timeline(part.member, manifest, music.load_tracks_multi(part.member))
    except SystemExit as exc:
        raise AssembleError(2, f"{part.name} 的成片时间轴算不出来：{exc}") from None
    vo_end = max(s["start"] + s["dur"] for s in tl["segments"])
    cut = vo_end + float(part.tail_s or 0)
    if cut >= tl["total_duration"]:
        raise AssembleError(2, f"{part.name}：截断点 {cut:.2f}s 超过成片总长 {tl['total_duration']:.2f}s")
    return vo_end, f"{cut:.2f}"


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
        if v.tail_s is not None:
            _member_file(member, SCRIPT)
            _member_file(member, *MANIFEST)
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

    for part in parts:
        if part.tail_s is not None:
            part.vo_end, part.cut = _cut_point(part)
    return Plan(pdir, doc.name, out_dir, target, replace, exists, parts)


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
        if p.cut is None:
            lines.append(f"  {p.name} ← {p.name}/{FINAL}（完整保留，-c copy）")
        else:
            lines.append(f"  part{i}_trimmed ← {p.name}/{FINAL} 截到 {p.cut}s"
                         f"（旁白终点 {p.vo_end:.2f}s + 余韵 {p.tail_s} 秒，末 {TRIM_FADE_S:g}s 淡出）")
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

        log("=== 截断点（旁白终点 + 余韵）===")
        for p in pl.parts:
            log(f"  {p.name} → {'完整保留' if p.cut is None else p.cut + 's'}")

        assembly: list[tuple[str, str, Path, Path | None]] = []   # (name, kind, 文件, src)
        for i, p in enumerate(pl.parts, 1):
            card = cards / f"card_part{i}.mp4"
            _ffmpeg(card_argv(p.cover, card, base), runner, f"过场卡 card_part{i}")
            assembly.append((f"card_part{i}", "card", card, None))
            if p.cut is None:
                assembly.append((p.name, "part", p.final, p.member.resolve()))
            else:
                trimmed = work / f"part{i}_trimmed.mp4"
                _ffmpeg(trim_argv(p.final, trimmed, p.cut, base), runner, f"截尾 part{i}_trimmed")
                assembly.append((f"part{i}_trimmed", "part", trimmed, p.member.resolve()))

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
