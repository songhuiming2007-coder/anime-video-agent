"""BGM 曲目：从 CD 镜像里分轨，量指标，供 render.py 取用。

分轨/测量（`scan`/`extract`/`measure`）仍是攒曲库用的工具，不进每期循环。
但**选哪首**从 2026-08-08 起改成每期人耳现选（CLAUDE.md「十、BGM 约定」）——
AI 的音乐审美、对经典歌曲的判断不如人，Phase 0 一次性锁死 3–5 首违背这个事实。
选曲结果记在每期 `01-topic.md` 的 `BGM正文`/`BGM结尾` 字段，`resolve()` 读它，
没填才退回 `config/bgm.json` 的 `use`（多数番这个字段会是空的）。

**为什么需要它：** 原声碟是拆好轨的（一首一个 flac），但 OP/ED 单曲碟是
**整轨镜像 + cue**——一张碟只有一个 `GNCA-0380.flac`，四首歌全在里面。
2026-07-29 定曲目表时我按 flac 文件数扫曲库，六张单曲碟因此各自只显示为「一个文件」，
整档 OP/ED 被漏掉了。而每张单曲碟都带 instrumental 轨，也就是说
「OP/ED 有人声、会跟口播打架」这个不选它们的理由从一开始就不成立。

扫曲库不能只看文件数，**碟里有 cue 就必须解析 cue**。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import unicodedata
from pathlib import Path

from . import paths

# cue 的时间码是 MM:SS:FF，FF 是 CD 帧，一秒 75 帧（红皮书标准，不是视频帧率）。
CD_FRAMES_PER_SEC = 75

# 认 instrumental 的写法。六张碟六种写法，没有统一标准：
#     春擬き -instrumental-        エブリデイワールド （Instrumental）
#     ユキトキ <Instrumental>      芽ぐみの雨 [Instrumental]
# 所以按关键词认，不按标点认。カラオケ / off vocal 是同义的另两种叫法。
INST_WORDS = ("instrumental", "off vocal", "offvocal", "カラオケ", "inst.")

_TIME = re.compile(r"INDEX\s+(\d+)\s+(\d+):(\d+):(\d+)")
_TITLE = re.compile(r'^\s*TITLE\s+"?(.*?)"?\s*$')
_TRACK = re.compile(r"^\s*TRACK\s+(\d+)\s+AUDIO")
_FILE = re.compile(r'^\s*FILE\s+"(.+?)"')


class Track:
    """cue 里的一条轨。`start`/`end` 是秒，`end is None` 表示到碟尾。"""

    def __init__(self, no: int, title: str, start: float):
        self.no, self.title, self.start = no, title, start
        self.end: float | None = None
        self.pregap: float | None = None   # INDEX 00，本轨的前导静音起点

    @property
    def duration(self) -> float | None:
        return None if self.end is None else self.end - self.start

    @property
    def instrumental(self) -> bool:
        t = self.title.lower()
        return any(w in t for w in INST_WORDS)


def _read_cue(cue: Path) -> str:
    """cue 的编码没有标准，实测同一套片源里 UTF-8-BOM 和 Shift-JIS 混着来。

    先试 utf-8-sig（BOM 会被吃掉），失败再试 cp932。两个都不行才报错——
    不要用 errors="replace" 蒙混过去，曲名糊了后面按名字取轨就会失败。
    """
    for enc in ("utf-8-sig", "cp932"):
        try:
            return cue.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"FAIL cue 编码认不出来：{cue}")


def parse_cue(cue: Path) -> tuple[Path, list[Track]]:
    """解析 cue，返回（镜像 flac 路径, 轨列表）。"""
    audio, tracks = None, []
    for line in _read_cue(cue).splitlines():
        if m := _FILE.match(line):
            audio = cue.parent / m.group(1)
        elif m := _TRACK.match(line):
            tracks.append(Track(int(m.group(1)), "", -1.0))
        elif m := _TIME.search(line):
            idx, mm, ss, ff = (int(x) for x in m.groups())
            t = int(mm) * 60 + int(ss) + int(ff) / CD_FRAMES_PER_SEC
            if not tracks:
                continue
            # INDEX 00 是前导（上一轨的余音/静音），INDEX 01 才是本轨真正开始。
            if idx == 0:
                tracks[-1].pregap = t
            else:
                tracks[-1].start = t
        elif m := _TITLE.match(line):
            # 碟标题在 TRACK 之前出现，那条不要。
            if tracks and not tracks[-1].title:
                tracks[-1].title = m.group(1)

    # 每轨的终点 = 下一轨的前导起点（有的话），否则下一轨的起点。
    # 用前导起点是为了把下一首的引子切干净——不然结尾会带进半秒别的曲子。
    for a, b in zip(tracks, tracks[1:]):
        a.end = b.pregap if b.pregap is not None else b.start
    if audio is None or not audio.exists():
        raise SystemExit(f"FAIL cue 指向的音频不存在：{audio}")
    return audio, tracks


def _safe(name: str) -> str:
    """曲名转文件名。日文假名汉字全部保留，只清掉路径分隔符和空白。

    不做罗马字转写——文件名要能跟曲目表和笔记里的名字对上，
    转写之后人对不上号，改配置就得来回猜。
    """
    name = unicodedata.normalize("NFC", name)
    for bad in '/\\:*?"<>|':
        name = name.replace(bad, "")
    return re.sub(r"\s+", " ", name).strip()


def extract(cue: Path, out_dir: Path, only_instrumental: bool = True) -> list[Path]:
    """把 cue 里的轨切出来存成独立 flac。

    切割用 `-c:a flac` 重编码而不是 `-c copy`：flac 的 copy 只能按帧边界切，
    差几十毫秒无所谓，但**边界不准会把下一首的头音带进来**，做 BGM 时很明显。
    flac→flac 重编码是无损的，代价只有时间。
    """
    audio, tracks = parse_cue(cue)
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for t in tracks:
        if only_instrumental and not t.instrumental:
            continue
        dest = out_dir / f"{_safe(t.title)}.flac"
        if dest.exists():
            made.append(dest)
            continue
        cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y",
               "-i", str(audio), "-ss", f"{t.start:.6f}"]
        if t.end is not None:
            cmd += ["-to", f"{t.end:.6f}"]
        cmd += ["-c:a", "flac", "-compression_level", "8", str(dest)]
        subprocess.run(cmd, check=True)
        made.append(dest)
    return made


def measure(audio: Path) -> dict:
    """量笔记要求记的四项：时长、响度、有没有人声、入声点。

    「有没有人声」机器判不了，靠曲名里的 instrumental 字样，判不出来就留给人。
    「前奏长度」也判不了——那是音乐结构，不是信号特征。改成量**入声点**：
    从头开始第一次超过 -30 dBFS 的时刻。它回答的是「这首拿来做结尾，
    观众要等多久才听到东西」，正是选曲真正关心的那个量。
    """
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(audio)],
        capture_output=True, text=True, check=True).stdout.strip())

    # loudnorm 的 print_format=json 把测量结果写在 stderr 里，但**后面还跟着收尾日志**。
    # 从 `{` 切到 stderr 末尾会带上那些行，json.loads 直接抛异常——初版就这么写的，
    # 结果整列响度全是「—」而没有任何报错。所以要切到配对的 `}` 为止。
    p = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "info", "-i", str(audio),
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True)
    lufs, i, j = None, p.stderr.rfind("{"), p.stderr.rfind("}")
    if -1 < i < j:
        try:
            lufs = float(json.loads(p.stderr[i:j + 1])["input_i"])
        except (ValueError, KeyError):
            pass

    # 入声点：astats 逐窗给 RMS，找第一个超 -30 dBFS 的窗。
    # 窗长要按真实采样率算——astats 的 reset=N 是「每 N 个音频帧复位」，
    # 一帧 1024 样本，44.1k 的碟一窗 0.279s，写死 0.25s 会有 11% 误差。
    sr_out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate", "-of", "csv=p=0", str(audio)],
        capture_output=True, text=True, check=True).stdout.strip()
    # 带内嵌封面图（attached_pic）的 mp3，csv=p=0 在唯一字段后仍会拖一个逗号——
    # 不是第二个流混进来了（-select_streams a:0 已经排除了封面那条 video 流），
    # 是 ffprobe 自己在探测到文件里还有别的流时，CSV 逗号分隔符照样打出来。
    # 只取逗号前的第一段，其余按原逻辑退回默认采样率。
    sr = int(sr_out.split(",")[0] or 44100)
    reset = 12
    win = reset * 1024 / sr
    p = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-t", "30", "-i", str(audio),
         "-af", f"astats=metadata=1:reset={reset},"
                "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
         "-f", "null", "-"],
        capture_output=True, text=True)
    onset = None
    for n, line in enumerate(v for v in p.stdout.splitlines() if "=" in v):
        try:
            if float(line.split("=")[1]) > -30.0:
                onset = n * win
                break
        except ValueError:
            continue

    return {"duration": round(dur, 1), "lufs": lufs, "onset": onset,
            "vocal": None if "instrumental" in audio.stem.lower() else "?"}


def clean_track_title(stem: str) -> str:
    """去除文件名开头的音轨序号，如 '01. 标题' -> '标题'，'05.交歓' -> '交歓'。"""
    cleaned = re.sub(r"^\d+\s*[\.\-、_]\s*", "", stem).strip()
    return cleaned or stem


def infer_slot(stem: str) -> str:
    """根据曲名关键词推断默认 slot（结尾 OP/ED 伴奏 vs 正文劇伴）。"""
    lower = stem.lower()
    return "结尾" if any(w in lower for w in INST_WORDS) else "正文"


def _rel_repo_path(path: Path) -> str:
    """转为相对于 paths.ROOT 的统一相对路径字符串。"""
    try:
        rel = path.resolve().relative_to(paths.ROOT.resolve())
        return str(rel).replace("\\", "/")
    except ValueError:
        pass
    try:
        rel = path.relative_to(paths.ROOT)
        return str(rel).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def register(files: list[Path], anime: str, note: str | None = None, overwrite: bool = False) -> int:
    """批量测量音频文件并写入 config/bgm.json，返回成功登记的曲目数。"""
    cfg_file = paths.CONFIG / "bgm.json"
    if not cfg_file.exists():
        raise SystemExit(f"FAIL 配置文件不存在：{cfg_file}")

    db = json.loads(cfg_file.read_text(encoding="utf-8"))
    anime_entry = db.setdefault(anime, {})

    if note:
        anime_entry["_note"] = note
    elif "_note" not in anime_entry:
        anime_entry["_note"] = f"《{anime}》BGM 曲库（原声带与主题曲/伴奏候选池），每期选曲现选"

    existing_tracks = {} if overwrite else anime_entry.get("tracks", {})
    count = 0

    print(f"开始批量测量与登记 [{anime}]（共 {len(files)} 首，overwrite={overwrite}）...")
    for f in sorted(files):
        if not f.exists():
            print(f"  跳过不存在文件：{f}")
            continue

        title = clean_track_title(f.stem)
        m = measure(f)
        rel_path = _rel_repo_path(f)

        existing_tracks[title] = {
            "path": rel_path,
            "slot": infer_slot(f.stem),
            "dur": m["duration"],
            "lufs": round(m["lufs"], 2) if m["lufs"] is not None else None,
            "onset": round(m["onset"], 3) if m["onset"] is not None else None,
            "vocal": False,
        }
        lufs_str = f"{m['lufs']:.1f}" if m["lufs"] is not None else "—"
        onset_str = f"{m['onset']:.1f}s" if m["onset"] is not None else "—"
        print(f"  + [{title}] slot={existing_tracks[title]['slot']} dur={m['duration']}s lufs={lufs_str} onset={onset_str}")
        count += 1

    anime_entry["tracks"] = existing_tracks
    cfg_file.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"完成！成功写入 {count} 首曲目至 {cfg_file.relative_to(paths.ROOT)} [{anime}]")
    return count


def load(anime: str) -> dict:
    """读该番的曲目表。render.py 用这个，不直接碰 json 结构。"""
    cfg = paths.CONFIG / "bgm.json"
    if not cfg.exists():
        return {}
    d = json.loads(cfg.read_text(encoding="utf-8"))
    return d.get(anime, {})


def anime_of(episode: Path) -> str | None:
    """从 `01-topic.md` 的「番:」字段取番名，去掉括号里的全称。

    `番: 春物（我的青春恋爱物语果然有问题）` → `春物`
    曲目表、番剧笔记都按短名做键，全称只是给人看的。
    """
    topic = episode / "01-topic.md"
    if not topic.exists():
        return None
    for line in topic.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"^\s*番\s*[:：]\s*(.+?)\s*$", line):
            return re.split(r"[（(]", m.group(1))[0].strip()
    return None


def resolve(anime: str, slot: str, override: str | None = None) -> dict | None:
    """取某个用途（`正文` / `结尾`）该用哪首，返回带绝对路径和实测响度的条目。

    2026-08-08 起选曲改成每期人耳现选（CLAUDE.md「十、BGM 约定」）——AI 的音乐审美、
    对经典歌曲的判断不如人，Phase 0 时一次性锁死 3–5 首违背这个事实。`override` 是
    `01-topic.md` 里 `BGM正文`/`BGM结尾` 字段填的曲名，人听完当期配音再挑，
    有值就用它，不必是 `use` 锁定的那首——`use` 现在只是没人手动指定时的退路，
    不是权威来源，多数番不会再配这个字段。

    两件事必须由这里给出，不能让调用方自己猜：

    1. **路径**。曲目表里存相对路径（`data/library/bgm/...`），因为 `data` 通常是指向
       外置盘的符号链接；写死 `/Volumes/<卷名>` 换个盘就全废（CLAUDE.md 相对路径约定）。
    2. **实测响度**。2026-07-29 量下来，劇伴 -14.4…-18.1 LUFS，OP/ED 单曲伴奏
       -7.1…-13.2 LUFS，**极差 11 dB**。渲染里那套闪避参数是对着 -16.9 的
       繋ぎとめた世界 调的，换成 -7.1 的 芽ぐみの雨 会凭空大 9.8 dB 压过口播。
       所以静态增益是错的控制量，得按每首的实测值归一到统一目标。
    """
    tbl = load(anime)
    name = override or (tbl.get("use") or {}).get(slot)
    if not name:
        return None
    rec = tbl.get("tracks", {}).get(name)
    if rec is None:
        src = f"01-topic.md 的 BGM{slot} 字段" if override else f"config/bgm.json 的 {anime}.use.{slot}"
        raise SystemExit(f"FAIL 曲目表里没有「{name}」，检查{src}")
    p = paths.ROOT / rec["path"]
    if not p.exists():
        raise SystemExit(
            f"FAIL BGM 文件不存在：{p}\n     跑 `python -m pipeline.bgm extract` 重新分轨")
    if rec.get("lufs") is None:
        raise SystemExit(
            f"FAIL 「{name}」没有实测响度，闪避参数会失准。\n"
            f"     跑 `python -m pipeline.bgm measure {rec['path']}` 补进 config/bgm.json")
    return {"name": name, "path": p, **rec}


def episode_choice(episode: Path, slot: str) -> str | None:
    """从 `01-topic.md` 取 `BGM正文` / `BGM结尾` 字段——每期人耳现选的曲名。

    这两个字段允许后补：`01-topic.md` 在选题阶段（01）就建了，但 BGM 要等
    03 配音出来、人听过才能选，字段填晚于文件创建没关系，渲染时读到就用，
    没填就交给 `resolve()` 退回 `use` 字段（多数情况下那也是空的，直接不铺 BGM）。
    """
    topic = episode / "01-topic.md"
    if not topic.exists():
        return None
    for line in topic.read_text(encoding="utf-8").splitlines():
        if m := re.match(rf"^\s*BGM{re.escape(slot)}\s*[:：]\s*(.+?)\s*$", line):
            return m.group(1).strip()
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="BGM 分轨与测量（Phase 0，一部番跑一次）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="扫目录下所有 cue，列出轨（找 instrumental 用）")
    p.add_argument("root", type=Path)
    p.add_argument("--all", action="store_true", help="列全部轨，不只 instrumental")

    p = sub.add_parser("extract", help="把 instrumental 轨切成独立 flac")
    p.add_argument("root", type=Path)
    p.add_argument("--anime", required=True)
    p.add_argument("--all", action="store_true")

    p = sub.add_parser("measure", help="量时长/响度/入声点")
    p.add_argument("files", type=Path, nargs="+")

    p = sub.add_parser("register", help="批量测量曲目并登记进 config/bgm.json（Phase 0）")
    p.add_argument("files", type=Path, nargs="+", help="音频文件列表")
    p.add_argument("--anime", required=True, help="番剧名称")
    p.add_argument("--note", help="曲库备注说明")
    p.add_argument("--overwrite", action="store_true", help="是否覆盖已有曲库（默认增量更新）")

    a = ap.parse_args()
    paths.require_data()

    if a.cmd in ("scan", "extract"):
        cues = sorted(a.root.rglob("*.cue"))
        if not cues:
            raise SystemExit(f"FAIL {a.root} 下没有 cue")
        out = paths.DATA / "library" / "bgm" / a.anime if a.cmd == "extract" else None
        for cue in cues:
            try:
                _, tracks = parse_cue(cue)
            except SystemExit as e:
                print(f"跳过 {cue.parent.name}：{e}")
                continue
            want = [t for t in tracks if a.all or t.instrumental]
            if not want:
                continue
            print(f"\n### {cue.parent.name}")
            for t in want:
                d = t.duration
                print(f"  {t.no:02d}  {t.title:<44s} {d and f'{int(d)//60}:{int(d)%60:02d}' or '到碟尾'}")
            if a.cmd == "extract":
                for f in extract(cue, out, only_instrumental=not a.all):
                    print(f"      → {f.relative_to(paths.ROOT)}")

    elif a.cmd == "measure":
        print(f"{'曲目':<40s} {'时长':>7s} {'响度':>9s} {'入声':>6s}")
        for f in a.files:
            m = measure(f)
            lufs = f"{m['lufs']:.1f}" if m["lufs"] is not None else "—"
            onset = f"{m['onset']:.1f}s" if m["onset"] is not None else "—"
            print(f"{f.stem:<40s} {m['duration']:>6.1f}s {lufs:>8s}  {onset:>6s}")

    elif a.cmd == "register":
        register(a.files, anime=a.anime, note=a.note, overwrite=a.overwrite)


if __name__ == "__main__":
    main()
