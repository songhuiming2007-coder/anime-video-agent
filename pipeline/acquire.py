"""素材自动化搜集：抓取 / 门禁 / 登记（M2.5）。

**分层（[plan 2026-09-10 §6.1](../docs/plans/2026-09-10-v2-architecture-design.md)）**：
检索与「这本画册是不是那本」这类判断是探索密集型劳动，没有机器判据——归 skill + agent；
这里只做**确定性**的三件事：

    python -m pipeline.acquire gate <文件或目录>              # 质量门禁 → 逐项判据
    python -m pipeline.acquire fetch <候选号> [--dry-run]     # 从 candidates.json 抓 → incoming/
    python -m pipeline.acquire register <文件> --pool EGOIST --as SP19
        → 复用 `ingest.register`（登记前强制过 intact）+ 落到 sources.json

自动化的是**抓取与初筛**，入库的最后一公里仍是显式动作（对齐「05 显式 approve」的
人机边界哲学）。`fetch` 只认人/agent 判断过并写进 `candidates.json` 的 URL，
不自己去搜任何东西。

**纯函数层与执行层分开**：判据裁决、candidates 校验、抓取器选择都是纯函数（tests 覆盖），
ffprobe / yt-dlp / curl 的调用集中在文件下半部分。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from . import paths
from .ingest import register as ingest_register
from .ingest import intact

# ---------- 常量（每个数写为什么，E3） ----------

#: 成片是 1080p 输出，720p 是能接受的地板：再低上不了 1080p 时间线而不被看出来。
#: 老 Live 只有 720p 是常态，所以地板不设 1080p——不够 1080p 的走 enhance 通道（plan §6.2）。
RES_FLOOR = (1280, 720)
RES_TARGET = (1920, 1080)

#: 扫图低于这个短边，排版放大到 1080p 时间线上会明显糊（杂志扫图常见 1600–4000px）。
SCAN_MIN_SIDE = 1600

#: `expected_dur` 是 agent 从考据页读来的声明时长（如官方 Live 全场 120 分钟），
#: 只当 WARN：撞车多半是「下到了剪辑版/精华版」，那是内容判断，不是文件坏了。
DUR_TOLERANCE = 0.05

#: 同媒质下的细分类型里，只有访谈是「没音轨就废」的：它的价值全在说话。
#: 而 live/mv **不能要求音轨**——实测本池 SP01–SP17 一共 17 条**全部无音轨**
#: （SP18 是唯一带音轨的），且 01-assets-video.md 铁律一要求池素材切片时强制 `-an` 丢弃音频：
#: 「必须有音轨」当硬判据会把本池 17/18 全拦掉，是典型的「拦了不该拦的」（S3）。
AUDIO_REQUIRED = ("interview",)

TYPES = ("live", "mv", "scan", "interview")
VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".flv", ".ts", ".avi")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")

#: 直链判定用的后缀：命中就 curl（不经 yt-dlp），否则交给 yt-dlp 解析页面。
DIRECT_EXT = VIDEO_EXT + IMAGE_EXT + (".pdf", ".zip", ".m4v")


def incoming() -> Path:
    """人机交接目录 `data/library/incoming/`。

    走 `require_data()` 再建目录：T7 没挂时 `mkdir` 会在 /Volumes 下建出实体目录，
    几十 G 静默写进系统盘（数据与存储标准）。
    """
    root = paths.require_data()
    d = root / "library" / "incoming"
    d.mkdir(parents=True, exist_ok=True)
    return d


def candidates_path() -> Path:
    return incoming() / "candidates.json"


def ledger_path() -> Path:
    return incoming() / "fetched.json"


# ---------- 判据裁决（纯函数） ----------


@dataclass(frozen=True)
class Verdict:
    """一条判据的结果：**判据名 + 实测值 + 阈值 + 过/不过**（S1 可证伪）。

    不给光秃秃一个布尔：布尔说不清「差多少」「量的是什么」，也就没法判该不该放行。
    """

    name: str
    value: str
    threshold: str
    level: str  # PASS / WARN / FAIL
    note: str = ""

    @property
    def passed(self) -> bool:
        return self.level != "FAIL"

    def line(self, width: int = 10) -> str:
        mark = {"PASS": "✓", "WARN": "!", "FAIL": "✗"}[self.level]
        tail = f"  ← {self.note}" if self.note else ""
        return (f"{mark} {self.name:<{width}} {self.value:<26} "
                f"{self.threshold:<14}{self.level}{tail}")


TABLE_HEAD = f"  {'判据':<10} {'实测值':<26} {'阈值':<14}结论"


def _verdict(name: str, value: str, threshold: str, level: str, note: str = "") -> Verdict:
    return Verdict(name, value, threshold, level, note)


def check_video(*, kind: str, intact_ok: bool, intact_detail: str, width: int, height: int,
                has_audio: bool, audio_detail: str = "",
                duration: float | None = None,
                expected_dur: float | None = None) -> list[Verdict]:
    """视频四判据：完整性 / 分辨率 / 音轨 / 时长偏差。

    `kind` 影响两件事：音轨那一条的判决强度（见 `AUDIO_REQUIRED`），以及展示。
    参数全是**观测值**（ffprobe 与 ffmpeg 的真实输出），函数本身不碰文件——
    与 `tests/` 的「只测纯函数 + 观测值注入」对上（测试标准 2026-08-16 明示例外）。
    """
    out: list[Verdict] = []

    out.append(_verdict(
        "完整性", intact_detail, "全片解复用无截断",
        "PASS" if intact_ok else "FAIL",
        "" if intact_ok else "没下完或容器损坏，重下（.part 可续传）",
    ))

    floor_ok = width >= RES_FLOOR[0] and height >= RES_FLOOR[1]
    need_enhance = width < RES_TARGET[0] or height < RES_TARGET[1]
    out.append(_verdict(
        "分辨率", f"{width}×{height}",
        f"≥{RES_FLOOR[0]}×{RES_FLOOR[1]}",
        "PASS" if floor_ok else "FAIL",
        "低于 1080p → needs_enhance（plan §6.2 超分通道）" if floor_ok and need_enhance
        else ("" if floor_ok else "低于地板，上不了 1080p 时间线"),
    ))

    out.append(_verdict(
        "音轨", audio_detail or ("有" if has_audio else "无"),
        "必须有" if kind in AUDIO_REQUIRED else "可选",
        "PASS" if has_audio else ("FAIL" if kind in AUDIO_REQUIRED else "WARN"),
        "" if has_audio else
        ("访谈的价值全在说话，没音轨就是死素材" if kind in AUDIO_REQUIRED else
         "池素材切片时统一 -an 丢音轨（01-assets 铁律一），无音轨不构成拒收；"
         "要拿 MC/清唱原声的段落才必须有"),
    ))

    if expected_dur is None:
        # S4：拿不到能证伪的信息就不定罪。「没声明」不是「不合格」。
        out.append(_verdict("时长偏差", f"{duration:.1f}s（未声明预期）" if duration else "—",
                            "±5%", "PASS", "candidates 没写 expected_dur，跳过不判"))
    elif duration is None:
        # S4：读不出时长就不判，别把「我不知道」记成「不合格」
        out.append(_verdict("时长偏差", "ffprobe 读不出时长", "±5%", "PASS",
                            "拿不到能证伪的信息，跳过不判"))
    else:
        dev = (duration - expected_dur) / expected_dur if expected_dur else 0.0
        ok = abs(dev) <= DUR_TOLERANCE
        out.append(_verdict(
            "时长偏差", f"{duration:.1f}s vs 声明 {expected_dur:.1f}s（{dev:+.1%}）",
            f"±{DUR_TOLERANCE:.0%}", "PASS" if ok else "WARN",
            "" if ok else "多半下成了剪辑版/精华版——内容判断，交人看一眼",
        ))
    return out


def check_image(*, decoded: bool, decode_detail: str,
                width: int, height: int) -> list[Verdict]:
    """图片两判据：可解码 / 短边 ≥1600px。**别拿音轨判据去量扫图。**"""
    short = min(width, height)
    return [
        _verdict("可解码", decode_detail, "能完整解码", "PASS" if decoded else "FAIL",
                 "" if decoded else "截断或非图片，重下"),
        _verdict("短边像素", f"{short}px（{width}×{height}）", f"≥{SCAN_MIN_SIDE}",
                 "PASS" if short >= SCAN_MIN_SIDE else "FAIL",
                 "" if short >= SCAN_MIN_SIDE else "放大到 1080p 时间线会糊"),
    ]


_RANK = {"PASS": 0, "WARN": 1, "FAIL": 2}


def overall(vs: list[Verdict]) -> str:
    """整份素材的结论：按最重的那条判（FAIL > WARN > PASS）。

    WARN 也要能浮到整批结论上：把「有 WARN 的批」打成 PASS，就没人会去看那行 WARN 了
    （首版就是这个 bug，自己跑出来的）。
    """
    return max((v.level for v in vs), key=_RANK.__getitem__, default="PASS")


def kind_of(name: str) -> str:
    """按后缀分派素材类型（gate 的判据分派用它；具体是 live 还是 mv 只影响展示）。"""
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXT:
        return "scan"
    if ext in VIDEO_EXT:
        return "video"
    return "unknown"


# ---------- candidates.json（纯函数） ----------


def _line_of(raw: str, needle: str | None) -> int | None:
    """在原文里定位某个字符串所在行（1 起）。找不到返回 None。

    报错点行号是为了**能直接跳过去改**：这份文件是 agent 写的，说「第 3 条错了」
    等于让人自己数一遍。
    """
    if not needle:
        return None
    for i, ln in enumerate(raw.splitlines(), 1):
        if needle in ln:
            return i
    return None


def load_candidates(raw: str) -> list[dict]:
    """校验 candidates.json，坏条目**一次报全**并点名位置（E10：批量里一个坏不打断整批）。"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"FAIL candidates.json 不是合法 JSON：第 {e.lineno} 行 {e.msg}")
    if not isinstance(data, list):
        raise SystemExit("FAIL candidates.json 顶层必须是数组（plan §6.1 的 schema）")

    bad: list[str] = []
    for i, c in enumerate(data, 1):
        line = _line_of(raw, (c.get("url") or c.get("title"))
                        if isinstance(c, dict) else None)
        where = f"第 {line} 行" if line else f"第 {i} 条"
        if not isinstance(c, dict):
            bad.append(f"  {where}：不是对象")
            continue
        missing = [k for k in ("title", "url", "type", "source", "why") if not c.get(k)]
        if missing:
            bad.append(f"  {where}（{c.get('title') or '无标题'}）：缺字段 {'/'.join(missing)}")
        if c.get("type") and c["type"] not in TYPES:
            bad.append(f"  {where}（{c.get('title')}）：type={c['type']!r} 不在 {TYPES}")
        if c.get("url") and urlparse(str(c["url"])).scheme not in ("http", "https"):
            bad.append(f"  {where}（{c.get('title')}）：url 不可解析 {c['url']!r}")
        if c.get("expected_dur") is not None and not isinstance(c["expected_dur"], (int, float)):
            bad.append(f"  {where}（{c.get('title')}）：expected_dur 须为秒数或 null")
    if bad:
        raise SystemExit("FAIL candidates.json 有问题的条目（修好再跑 fetch）：\n" + "\n".join(bad))
    return data


def dup_verdicts(*, url: str, slug: str, seen_urls: set[str],
                 existing_names: set[str]) -> list[Verdict]:
    """抓取前查重：URL 抓过没有 / 同名文件在不在。

    **跨源内容级去重不建**（「换个 rip 的同一场 Live」）：那要真撞上才知道需要什么判据，
    YAGNI；本次先挡「同一条链接」「同一个文件名」这两种真会重复下几百 M 的情况。
    """
    return [
        _verdict("URL 查重", url, "未抓过",
                 "FAIL" if url in seen_urls else "PASS",
                 "台账里已有这一条，别下第二遍" if url in seen_urls else ""),
        _verdict("同名查重", slug, "不撞名",
                 "FAIL" if slug in existing_names else "PASS",
                 "incoming/ 里已有同名前缀的文件" if slug in existing_names else ""),
    ]


# ---------- 抓取命令（纯函数：选工具 + 造命令） ----------


def pick_fetcher(url: str) -> str:
    """直链（后缀认得出来）走 curl，页面走 yt-dlp。

    curl 那条**必须带 `-C -`**：HF 权重那次教训——大文件断在中途，
    不续传就得从头再来（2026-09-11）。
    """
    ext = Path(urlparse(url).path).suffix.lower()
    return "curl" if ext in DIRECT_EXT else "yt-dlp"


def slugify(title: str) -> str:
    """标题 → 安全的文件名前缀（英文/数字保留，其余折成 `_`）。"""
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", title).strip("_")
    return (s or "asset")[:80]


def fetch_argv(url: str, dest_dir: Path, slug: str, *, yt_dlp: list[str] | None = None) -> list[str]:
    """要执行的抓取命令（`--dry-run` 打印的就是它）。"""
    if pick_fetcher(url) == "curl":
        return ["curl", "-L", "-C", "-", "--fail", "-o", str(dest_dir / f"{slug}{Path(urlparse(url).path).suffix.lower()}"), url]
    cmd = (yt_dlp or ["yt-dlp"])
    return cmd + [
        "--no-playlist", "--newline", "--continue",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", str(dest_dir / f"{slug}.%(ext)s"),
        url,
    ]


def register_key(text: str) -> int:
    """`--as SP19` → 19。集键写死成两位 SP（ADR-0010 决策二），别让手输自由发挥。"""
    m = re.fullmatch(r"SP(\d{2})", text.strip())
    if not m:
        raise SystemExit(f"FAIL 集键必须形如 SP19（两位），收到 {text!r}")
    return int(m.group(1))


# ---------- 执行层：ffprobe ----------


def probe_media(path: Path) -> dict:
    """ffprobe 一次拿全：视频流尺寸、有没有音轨、时长、编码名。

    `-count_frames` 之类不要：大文件上太慢，判据不需要逐帧数。
    打不开的返回 `{"error": 工具原文}`，由调用方判 FAIL——**不抛异常**：
    批量 gate 里一个坏文件不该打断整批（E10）。
    """
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return {"error": (proc.stderr.strip().splitlines() or [f"退出码 {proc.returncode}"])[0][:60]}
    out = proc.stdout
    d = json.loads(out)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    if v is None:
        raise ValueError("没有视频流")
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "has_audio": a is not None,
        "audio_detail": (f"有（{a.get('codec_name')} {a.get('channels') or '?'}ch）" if a else "无音轨"),
        "duration": float(d.get("format", {}).get("duration") or 0) or None,
        "vcodec": v.get("codec_name") or "?",
    }


def probe_image(path: Path) -> dict:
    """图片解码 + 尺寸（Pillow 已在依赖里，不引 ffmpeg 那条路）。"""
    from PIL import Image

    try:
        with Image.open(path) as im:
            im.load()
            return {"decoded": True, "detail": f"解出 {im.format} {im.mode}",
                    "width": im.width, "height": im.height}
    except Exception as e:  # Pillow 的异常类型按格式各不相同，一律记「解不开」
        return {"decoded": False, "detail": f"{type(e).__name__}: {e}"[:40],
                "width": 0, "height": 0}


# ---------- 执行层：台账 ----------


def ledger_load(path: Path | None = None) -> list[dict]:
    """抓取台账。**为什么不用 sources.json 记 URL**：那张表是 ingest 的，按 SP 键存
    视音频规格、没有 url 字段，而且 register 是整条覆盖写入——往里塞自定义字段
    下次重登记就没了。所以 URL 历史放交接目录里，与 candidates.json 同源。"""
    p = path or ledger_path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise SystemExit(f"FAIL 台账不是合法 JSON：{p}（修好或移走再跑）")


def ledger_save(entries: list[dict], path: Path | None = None) -> None:
    p = path or ledger_path()
    paths.atomic_write(p, json.dumps(entries, ensure_ascii=False, indent=2))


def ledger_seen_urls(entries: list[dict]) -> set[str]:
    return {e["url"] for e in entries if e.get("url")}


# ---------- 三个子命令 ----------


def cmd_gate(target: Path, *, expected_dur: float | None = None) -> int:
    """逐文件打印判据表。有 FAIL 退出码 1（S9：退出码也算不合格）。"""
    files: list[Path] = []
    if target.is_dir():
        files = sorted(f for f in target.iterdir()
                       if f.suffix.lower() in VIDEO_EXT + IMAGE_EXT)
        if not files:
            raise SystemExit(f"FAIL {target} 里没有可判的视音频或图片文件")
    else:
        files = [target]

    entries = ledger_load()
    worst = "PASS"
    for f in files:
        led = next((e for e in entries if Path(e.get("file", "")).name == f.name), None)
        t = (led or {}).get("type") or kind_of(f.name)
        exp = expected_dur if expected_dur is not None else (led or {}).get("expected_dur")
        print(f"\n素材 {f}（类型 {t}）")
        if kind_of(f.name) == "scan":
            p = probe_image(f)
            vs = check_image(decoded=p["decoded"], decode_detail=p["detail"],
                             width=p["width"], height=p["height"])
        elif kind_of(f.name) == "video":
            p = probe_media(f)
            if "error" in p:
                vs = [_verdict("可打开", p["error"], "ffprobe 能读", "FAIL",
                               "不是视频流或容器坏了")]
            else:
                ok, detail = intact(f)
                vs = check_video(kind=t if t in TYPES else "video",
                                 intact_ok=ok, intact_detail=detail, width=p["width"],
                                 height=p["height"], has_audio=p["has_audio"],
                                 audio_detail=p["audio_detail"], duration=p["duration"],
                                 expected_dur=exp)
        else:
            raise SystemExit(f"FAIL 认不出素材类型（后缀 {f.suffix}），gate 不猜")
        if led:
            vs.append(_verdict("URL 查重", led.get("url", "—"), "未抓过", "PASS",
                               f"台账已有（候选 #{led.get('candidate')}，"
                               f"{led.get('as') or '未登记'}）"))
        print(TABLE_HEAD)
        for v in vs:
            print("  " + v.line())
        verdict = overall(vs)
        print(f"  → {verdict}")
        worst = max(worst, verdict, key=_RANK.__getitem__)
    sys.stdout.write(f"\n整批结论：{worst}\n")
    return 1 if worst == "FAIL" else 0


def cmd_fetch(no: int, *, dry_run: bool = False) -> int:
    cp = candidates_path()
    if not cp.exists():
        raise SystemExit(f"FAIL 没有候选清单 {cp}——先按 skills/acquire-assets/SKILL.md 检索并写出它")
    cands = load_candidates(cp.read_text(encoding="utf-8"))
    if not 1 <= no <= len(cands):
        raise SystemExit(f"FAIL 候选号 {no} 超出范围（1–{len(cands)}）")
    c = cands[no - 1]
    dest = incoming()
    slug = slugify(c["title"])
    entries = ledger_load()
    dvs = dup_verdicts(url=c["url"], slug=slug,
                       seen_urls=ledger_seen_urls(entries),
                       existing_names={f.stem for f in dest.iterdir()})
    print(f"候选 #{no} {c['title']}\n  {c['url']}\n  why: {c['why']}")
    for v in dvs:
        print("  " + v.line(width=0))
    if overall(dvs) == "FAIL":
        raise SystemExit("FAIL 查重没过，没抓。确认要重抓就先清台账/改文件名（人显式动作）")

    argv = fetch_argv(c["url"], dest, slug, yt_dlp=yt_dlp_argv())
    print("  执行：" + " ".join(argv))
    if dry_run:
        return 0
    proc = subprocess.run(argv)
    if proc.returncode != 0:
        # S9：工具原文照抄，不自己压成一句「下载失败」——错误信息是给人看的证据
        raise SystemExit(f"FAIL 抓取失败（{pick_fetcher(c['url'])} 退出码 {proc.returncode}），"
                         f"续传重跑同一条命令即可；原始输出见上方")
    made = [f for f in dest.iterdir() if f.stem.startswith(slug) and f.suffix.lower() in VIDEO_EXT + IMAGE_EXT]
    rel = str(made[0]) if made else str(dest)
    entries.append({"url": c["url"], "title": c["title"], "type": c["type"],
                    "source": c["source"], "candidate": no, "file": rel,
                    "as": None, "why": c["why"],
                    "expected_dur": c.get("expected_dur")})
    ledger_save(entries)
    print(f"  → 落盘 {rel}；台账 +1（下一步：acquire gate {rel}）")
    return 0


def cmd_register(file: Path, *, pool: str, key: int, force: bool = False) -> int:
    """登记进 sources.json。**复用 `ingest.register`**（登记前强制过 intact），
    另做三件它不管的事：集键冲突、门禁复核、落到池的 raw 目录。

    为什么要挪文件：池里 SP01–SP18 全在 `data/library/raw/<池>/`，**集键与文件名同号**
    是这套目录能看懂的约定（E1 产物即状态）。放进来的东西留在 incoming/ 会让池分叉两处。
    """
    sources = paths.DATA / "library" / "sources.json"
    db = json.loads(sources.read_text(encoding="utf-8")) if sources.exists() else {}
    have = db.get(pool, {})
    k = f"SP{key:02d}"
    if k in have:
        raise SystemExit(f"FAIL {pool}/{k} 已登记（{have[k]['path']}）。"
                         f"换一个集键，或先想清楚要不要覆盖——覆盖是人的显式决定")

    if kind_of(file.name) != "video":
        raise SystemExit(
            f"FAIL {file.name} 不是视频，register 只收视频（渲染要 mp4）。\n"
            f"     扫图先按 01-assets-video.md 铁律三转 6s 微动再登记：\n"
            f"     ffmpeg -loop 1 -i {file} -c:v libx264 -t 6.0 -vf "
            f"\"scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
            f"zoompan=z='min(zoom+0.0015,1.15)':d=144:x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':s=1920x1080:fps=23.976\" "
            f"-pix_fmt yuv420p -r 23.976 -an data/library/raw/{pool}/{k}.mp4")

    led = next((e for e in ledger_load() if Path(e.get("file", "")).name == file.name), None)
    if not force:
        p = probe_media(file)
        if "error" in p:
            raise SystemExit(f"FAIL ffprobe 打不开：{p['error']}")
        ok, detail = intact(file)
        vs = check_video(kind=(led or {}).get("type") or "video",
                         intact_ok=ok, intact_detail=detail, width=p["width"],
                         height=p["height"], has_audio=p["has_audio"],
                         audio_detail=p["audio_detail"], duration=p["duration"],
                         expected_dur=(led or {}).get("expected_dur"))
        print(TABLE_HEAD)
        for v in vs:
            print("  " + v.line())
        if overall(vs) == "FAIL":
            raise SystemExit("FAIL 门禁没过，没登记。先修素材；确实要收就 --force 并写理由给人看")

    dest = paths.DATA / "library" / "raw" / pool / f"{k}{file.suffix.lower()}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if file.resolve() != dest.resolve():
        if dest.exists():
            raise SystemExit(f"FAIL 目标已存在 {dest}——同名不同文件，先人看一眼再决定")
        shutil.move(str(file), str(dest))
        print(f"  挪入池：{file} → {dest}")
    # 登记表里存**相对仓库根**的路径（E4）；ingest 存的就是传进去的那个字符串
    if Path.cwd() != paths.ROOT:
        os.chdir(paths.ROOT)
    entry = ingest_register(dest.relative_to(paths.ROOT), anime=pool, season=1, episode=0, sp=key)
    print(f"  已登记 {pool}/{k}：{entry['width']}×{entry['height']} "
          f"{entry['duration']:.1f}s fps={entry['fps']}")
    entries = ledger_load()
    for e in entries:
        if Path(e.get("file", "")).name == file.name:
            e["file"], e["as"] = str(dest), k
    ledger_save(entries)
    print(f"  下一步：shots calibrate {dest} → 定阈值 → shots build / gallery")
    return 0


def yt_dlp_argv() -> list[str]:
    """yt-dlp 的调用形态。

    **它不在这份仓库的依赖里**（2026-09-11 实测 `uv run python -c "import yt_dlp"` →
    ModuleNotFoundError），系统里另有一个全局可执行文件。所以先找命令、再退回
    `python -m yt_dlp`，都没有就报出修复命令——不许静默换一个下载器（S9）。
    """
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    if importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    raise SystemExit("FAIL 找不到 yt-dlp。二选一：\n"
                     "     uv add yt-dlp        # 进项目虚拟环境（推荐，E6）\n"
                     "     brew install yt-dlp  # 系统级")


def main() -> None:
    ap = argparse.ArgumentParser(prog="acquire", description="素材抓取 / 门禁 / 登记（M2.5）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gate", help="质量门禁：逐项打印判据与结论")
    g.add_argument("target", type=Path, help="文件或目录")
    g.add_argument("--expected-dur", type=float, help="声明时长（秒），用来算时长偏差")

    f = sub.add_parser("fetch", help="从 candidates.json 抓一条到 incoming/")
    f.add_argument("no", type=int, help="候选号（1 起，candidates.json 里的顺序）")
    f.add_argument("--dry-run", action="store_true", help="只打印将执行的命令")

    r = sub.add_parser("register", help="登记进 sources.json（走 ingest 既有登记）")
    r.add_argument("file", type=Path)
    r.add_argument("--pool", default="EGOIST", help="素材池名（挂企划名下）")
    r.add_argument("--as", dest="key", required=True, metavar="SPxx")
    r.add_argument("--force", action="store_true", help="门禁不过也登记（会写进交回材料）")

    a = ap.parse_args()
    if a.cmd == "gate":
        raise SystemExit(cmd_gate(a.target, expected_dur=a.expected_dur))
    if a.cmd == "fetch":
        raise SystemExit(cmd_fetch(a.no, dry_run=a.dry_run))
    if a.cmd == "register":
        raise SystemExit(cmd_register(a.file, pool=a.pool, key=register_key(a.key),
                                      force=a.force))


if __name__ == "__main__":
    main()
