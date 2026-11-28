"""成片质检（流程第 07 步）。

    python -m pipeline.qc data/episodes/<本期>

逐条实现 CLAUDE.md「质检门禁」，结果落 `06-check.log`，不达标非零退出。
**主观项（节奏、观感）不进门禁**——它们的根在稿子和排片上，
回第 02 / 05 步改，不在成片上补救。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import paths

# loudnorm 单遍归一的实测残差约 ±1，容差留点余量
LUFS_TARGET = paths.conf("audio.lufs", -16.0)
LUFS_TOL = paths.conf("audio.lufs_tolerance", 1.5)
TRUE_PEAK_MAX = paths.conf("audio.true_peak_max", -1.0)   # dBTP
AV_DRIFT_MAX = 0.5                     # 音画时长差，秒
AUDIO_PKT_GAP_MAX = 0.5                # AAC-LC 音频包总时长相对容器时长的最大允许缺口，秒。
                                        # 正常封装仅差 1 帧级编码预热（≤0.05s）；若渲染期滤镜重初始化
                                        # 丢弃 loudnorm 缓冲，会在容器内留下 ~3s/次的 PTS 断洞，
                                        # 容器时长仍与视频对齐，必须靠音频包数反推抓出静默丢包。
BLACK_MAX = 0.5                        # 单段纯黑上限，秒
BLACK_MAX_SP = 1.5                     # SP 特典素材（MV/Live，ADR-0010）的纯黑上限，秒。
                                        # 演唱会暗灯转场、艺术 MV 的 Fade to Black 是表达本身，
                                        # 按番剧的 0.5s 判会大面积误报——只对 clip 带 sp 标记
                                        # 的源放宽，普通番剧片段的门限不动
BLACK_MATCH_MIN = 0.35                 # 成片黑帧与源片黑段的重叠 ≥ 此值才算「源片自带」
                                        # 为什么不是 BLACK_MAX：切片 seek 有帧级误差
                                        # （0.04–0.08s），成片黑帧 0.5s 映射回源片最多
                                        # 偏出这么点，0.35 = 0.5 − 0.15 的容差。
                                        # 再低会把「源片有黑但没盖住成片黑」误认成内容。
FRAME_BOUND = 0.05                     # 成片位置相对排片的误差上界，秒。D145 起 render
                                        # 按成片时间轴的累计边界分帧，任一片段起点离计划
                                        # ≤ 半帧；常见帧率（23.976/24/29.97/30）一帧
                                        # ≤ 0.0417s，取 0.05 有余量。此前按「第 n 片累计
                                        # ≤ n×0.05」放宽，那是在容忍 D145 的累积漂移，
                                        # 卷三第 177 片的窗口放到 ±8.9 s，等于不核。
SILENCE_MAX = 2.0                      # 单段静音上限，秒
BLACK_SRC_BUF = 0.3                    # 对照源片时前后各放宽的秒数
                                        # ffmpeg 快速 seek 会落在关键帧上，实际解码起点
                                        # 可能早于请求位置；不放宽会漏检紧贴边界的黑段。
BLACK_CLUSTER_GAP = 10.0               # 同源多处黑帧间距 ≤ 此秒数才合并为一次扫描窗口；
                                        # 此前按 [min, max] 整段扫，两处相隔 5 分钟的 0.5 s 黑帧
                                        # 会白解中间 319 s 1080p MKV（2026-11-27 EP1 实测占 qc 18.4%）。
ASSEMBLY = "04-assembly.json"   # 总装期部件账本（assemble.sh 写，qc 读）


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    skipped: bool = False   # 缺前置文件跑不了。**不算通过**，见下方 main


def _run(cmd: list[str]) -> tuple[str, int]:
    """跑 ffmpeg 并回收 (stderr, 退出码)——检测类滤镜的输出都在 stderr 上。

    退出码必须跟回来：blackdetect/silencedetect 失败时 stderr 里没有结果行，
    只看输出会把「检测器跑不动」当成「没检出」判 PASS——文件坏到门禁最该
    报警的时候反而全绿（2026-08-16 审计 2-4）。
    """
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.stderr, p.returncode


def _tail(err: str) -> str:
    """stderr 的最后一行（截断）——失败详情给一条就够定位，不给半屏日志。"""
    lines = [ln for ln in err.strip().splitlines() if ln.strip()]
    return lines[-1][:120] if lines else ""


def _probe(video: Path, entries: str, stream: str) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", entries, "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _duration(video: Path, stream: str) -> float:
    """时长。先问流，问不到退到容器。

    `stream=duration` 不是所有容器都填——某些 mkv/mp4 只在 format 上有。
    初版直接 `float(空串)` 抛 ValueError，报错信息对用户毫无意义
    （只会看到一行 `could not convert string to float: ''`）。
    """
    raw = _probe(video, "stream=duration", stream).split(",")[0].strip()
    if not raw or raw == "N/A":
        raw = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, check=True).stdout.strip()
    if not raw or raw == "N/A":
        raise SystemExit(f"FAIL 读不到 {video} 的 {stream} 时长，文件可能损坏")
    return float(raw)


def _audio_packet_check(video: Path, adur: float) -> Check | None:
    """校验 AAC-LC 音频流实际编码帧覆盖时长与容器时长一致，捕获 PTS 断洞/滤镜重建静默丢包。

    拿不到可证伪的 `nb_frames`/`sample_rate`（如无帧数头容器或非 AAC-LC 编码）时返回 None，
    遵循判据 4「拿不到能证伪信息时跳过、不定罪」。
    """
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,profile,sample_rate,nb_frames",
         "-of", "json", str(video)],
        capture_output=True, text=True, check=False,
    )
    if p.returncode != 0 or not p.stdout.strip():
        return None
    try:
        info = json.loads(p.stdout)
    except ValueError:
        return None
    if not isinstance(info, dict):
        return None
    streams = info.get("streams") or []
    if not streams or not isinstance(streams[0], dict):
        return None
    st = streams[0]
    if str(st.get("codec_name", "")).lower() != "aac":
        return None
    prof = str(st.get("profile", "")).lower()
    if "he" in prof or "sbr" in prof:
        return None
    try:
        sr = int(st["sample_rate"])
        nb = int(st["nb_frames"])
    except (KeyError, TypeError, ValueError):
        return None
    if sr <= 0 or nb <= 0:
        return None
    pkt_dur = nb * 1024 / sr
    missing = adur - pkt_dur
    ok = missing <= AUDIO_PKT_GAP_MAX
    detail = (
        f"{nb} 包覆 {pkt_dur:.2f}s / 容器 {adur:.2f}s"
        if ok
        else f"缺失 {missing:.2f}s（{nb} 包仅覆 {pkt_dur:.2f}s / 容器 {adur:.2f}s，疑似滤镜重建丢包）"
    )
    return Check("音频轨包连续", ok, detail)


def _map_black_to_sources(blacks: list[tuple[float, float]],
                          plan: dict,
                          seg_starts: dict[int, float] | None = None
                          ) -> list[tuple[str, float, float, float, float, int, int]]:
    """成片黑帧区间 → 它覆盖的源片段引用。

    成片 = 各段 clips 按顺序拼接（与 render.py 同一份 plan、同一套顺序，
    时间轴才会对得上）。返回
    [(源路径, 源区间起点, 源区间终点, 成片区间起点, 成片区间终点, 累计片段序号, 黑帧序号)]；
    黑帧跨片段边界时一个黑帧映射出多条。片段序号从 1 起；黑帧序号用于把同一条黑
    在多个片段上的映射归拢起来判整体（见 `_black_defects`）。

    段落起点取 `seg_starts`（段落号 → 成片起点）：试听型带音乐段偏移，否则段落连续
    假设会把黑帧映射到错误的源位置（2026-08-16 实测：段落 1 的转场黑被映射到 467s
    报「源无黑」）；普通期是配音实测时长累计（`_voice_starts`），与 render 同口径。

    已知边界（2026-08-16 审计 2-36，核实后有意不修）：音乐段（段落间隙）
    的黑帧不映射不判定——OP/ED 画面自带黑场是内容；要判需把 render 的
    画面段源（_still_frames 的 visual/定格信息）接进 qc，影响面大于收益。
    """
    out: list[tuple[str, float, float, float, float, int, int]] = []
    idx = 0
    t = 0.0
    for seg in plan["segments"]:
        # 试听型：段落起点从音乐时间轴来（含音乐段偏移）；
        # 连续模式（seg_starts=None）：t 自然累计，行为与旧版一致。
        if seg_starts is not None:
            t = seg_starts.get(seg["index"], t)
        for clip in seg["clips"]:
            idx += 1
            # ok_extended 段的末片含定格延展（extend）：占成片时间但不读源片，
            # 映射区间按 dur+extend 推，源区间仍只到 dur 为止
            lo, hi = t, t + clip["dur"] + clip.get("extend", 0.0)
            for bi, (b0, b1) in enumerate(blacks):
                if b1 > lo and b0 < hi:                 # 有交集
                    # 源区间两头都夹在真实素材段内：定格延展区（extend）不读源片，
                    # 映射到源片末帧一个点，不造出倒置区间
                    out.append((
                        clip["source"],
                        clip["start"] + min(clip["dur"], max(0.0, b0 - lo)),
                        clip["start"] + min(clip["dur"], b1 - lo),
                        max(b0, lo), min(b1, hi),
                        idx, bi,
                    ))
            t = hi
    return out


def _seg_starts(mplan: dict | None, audio: list[dict] | None) -> dict[int, float] | None:
    """黑帧映射用的段落成片起点：试听型取音乐时间轴，普通期取配音时长累计。"""
    if mplan:
        return {s["index"]: s["start"] for s in mplan["segments"]}
    return _voice_starts(audio)


def _voice_starts(audio: list[dict] | None) -> dict[int, float] | None:
    """普通期段落的成片起点 = 配音实测时长累计（render 拼人声轨与分帧同一口径，D145）。

    没有 manifest 时返回 None，按片段时长连续累计（旧口径）。
    """
    if not audio:
        return None
    out, t = {}, 0.0
    for a in audio:
        out[a["index"]] = t
        t += a["duration"]
    return out


def _source_black_covers(src_blacks: list[tuple[float, float]],
                         src_lo: float, src_hi: float) -> bool:
    """源片黑段与映射区间的重叠 ≥ 门槛，认定成片黑帧是源片自带。

    源片同样的黑场转场是内容——情绪停顿、转场黑场，成片原样带进来，
    不该算渲染缺陷。
    """
    return any(min(s, src_hi) - max(s0, src_lo) >= BLACK_MATCH_MIN
               for s0, s in src_blacks)


def _mapped_covered(span: tuple[str, float, float, float, float, int],
                    src_blacks: list[tuple[float, float]]) -> bool:
    """一条映射是否被源片黑段覆盖，映射位置允许偏出 FRAME_BOUND。

    render 按成片时间轴分帧（D145），片段起点离排片 ≤ 半帧，不随片段序号累积。
    """
    _, slo, shi, _, _, _idx = span
    return _source_black_covers(src_blacks, slo - FRAME_BOUND, shi + FRAME_BOUND)


def _black_defects(mapped: list[tuple[str, float, float, float, float, int, int]],
                   covered: set[tuple],
                   sp_srcs: set[str] | frozenset = frozenset()) -> list[tuple[str, float, float, float, float]]:
    """按「一条黑」聚合，返回未覆盖时长超门限的黑场（的未覆盖映射）。

    判据要的是**一段连续黑的未覆盖部分 ≥ 门槛**，不是逐片段看。黑帧跨切片
    边界时，边界上一两帧的暗场可能落进相邻片段，而源片只在主片段里有转场黑：
    逐片段查会把整段源转场误判成缺陷（2026-08-10 实测：源片淡出黑 1.45s
    接邻片段首帧 0.13s 暗场，被判「源无黑」）。

    `sp_srcs`（2026-09-07）：SP 特典素材（MV/Live）的源路径集合。这些源里的
    暗场按 BLACK_MAX_SP 判——演唱会暗灯/艺术 MV 的黑场转场是表达本身；
    普通番剧片段仍按 BLACK_MAX。同一条黑跨两类源时分类各自汇总各自判。
    """
    by_black: dict[int, list] = {}
    for span in mapped:
        by_black.setdefault(span[6], []).append(span)
    defects: list[tuple[str, float, float, float, float]] = []
    for spans in by_black.values():
        un = [s for s in spans if s not in covered]
        un_norm = [s for s in un if s[0] not in sp_srcs]
        un_sp = [s for s in un if s[0] in sp_srcs]
        if sum(f1 - f0 for _, _, _, f0, f1, _, _ in un_norm) >= BLACK_MAX:
            defects.extend((src, flo, fhi, slo, shi)
                           for src, slo, shi, flo, fhi, _, _ in un_norm)
        if sum(f1 - f0 for _, _, _, f0, f1, _, _ in un_sp) >= BLACK_MAX_SP:
            defects.extend((src, flo, fhi, slo, shi)
                           for src, slo, shi, flo, fhi, _, _ in un_sp)
    return defects


def _silence_exempt(start: float, end: float, song_ends, card_wins) -> bool:
    """这段静音是不是设计出来的。

    1. 歌曲自然收尾（fade 尾音 < -45dB）不是缺陷——静音区间尾部对齐某个
       音乐事件终点就豁免（2026-08-16 实测：Planetes 自然收尾 793.6s、
       Departures 尾段 428.1s）。
    2. 总装期的章节卡是 3 秒**设计出来的**静音桥接，卡前后各一期的片尾淡出
       自然落进 -45dB 阈值内。桥接要能被声明（`04-assembly.json` 里 kind=card
       的部件窗口）才豁免：写死的豁免清单等于把门禁关掉。
    """
    if any(abs(end - x) <= 0.5 for x in song_ends):
        return True
    return any(start < hi and end > lo for lo, hi in card_wins)


def assembly_ledger(episode: Path) -> dict | None:
    """总装期账本（`00-*/assemble.sh` 产物）：部件实测窗口。不是总装期返回 None。

    总装期是「把已交付的成片拼起来」，没有自己的 `02-script.md`、也没有自己的
    `04-clips.approved.json`：它的排片基准与黑帧来源都是**三部曲成片本身**。
    账本里的 offset/duration 由 `assemble.sh` 用 ffprobe 实测写入——施工图上
    的期望值不算依据（二期实测与期望差 44 秒）。
    """
    p = episode / ASSEMBLY
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _cluster_black_windows(
    spans: list[tuple],
    *,
    max_gap: float = BLACK_CLUSTER_GAP,
) -> list[tuple[float, float]]:
    """把同一源片/部件的多个黑帧映射区间按间隙聚成紧凑检测窗口 [(lo, hi), ...]。

    每条映射先各自放宽 FRAME_BOUND + BLACK_SRC_BUF 并夹到 >= 0；间距 ≤ max_gap 的合并为
    同一窗口（省掉重复启进程），间距更大的拆成独立短窗（避免两头黑帧把中间数分钟正片白解一遍）。
    """
    pad = FRAME_BOUND + BLACK_SRC_BUF
    wins = sorted((max(0.0, s[1] - pad), s[2] + pad) for s in spans)
    if not wins:
        return []
    merged: list[list[float]] = [[wins[0][0], wins[0][1]]]
    for lo, hi in wins[1:]:
        if lo <= merged[-1][1] + max_gap:
            if hi > merged[-1][1]:
                merged[-1][1] = hi
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def _probe_source_blacks(
    by_src: dict[str, list[tuple]],
) -> tuple[set[tuple], list[str]]:
    """对分组后的源片/部件跑分段黑帧对照，返回 (已覆盖映射集合, 失败源说明列表)。"""
    tasks: list[tuple[str, float, float]] = [
        (src, lo, hi)
        for src, spans in by_src.items()
        for lo, hi in _cluster_black_windows(spans)
    ]
    if not tasks:
        return set(), []

    def _check_one(item: tuple[str, float, float]) -> tuple[str, list[tuple[float, float]], str | None]:
        src, lo, hi = item
        err, rc = _run([
            "ffmpeg", "-nostdin", "-ss", f"{lo:.3f}",
            "-t", f"{hi - lo:.3f}", "-i", src, "-vf",
            f"blackdetect=d={BLACK_MAX}:pic_th=0.98", "-an",
            "-f", "null", "-",
        ])
        if rc != 0:
            return src, [], f"{Path(src).name}：ffmpeg 退出码 {rc}（{_tail(err)}）"
        blacks = [
            (lo + float(a), lo + float(b))
            for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)
        ]
        return src, blacks, None

    if len(tasks) == 1:
        results = [_check_one(tasks[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(4, len(tasks))) as pool:
            results = list(pool.map(_check_one, tasks))

    blacks_by_src: dict[str, list[tuple[float, float]]] = {}
    src_errs: list[str] = []
    for src, found, err_msg in results:
        if err_msg is not None:
            src_errs.append(err_msg)
            continue
        blacks_by_src.setdefault(src, []).extend(found)

    covered: set[tuple] = set()
    for src, spans in by_src.items():
        src_blacks = blacks_by_src.get(src)
        if not src_blacks:
            continue
        covered |= {span for span in spans if _mapped_covered(span[:6], src_blacks)}
    return covered, src_errs


def _assembly_black_spans(blacks: list[tuple[float, float]], asm: dict
                          ) -> tuple[list, list, list]:
    """总装期的黑帧归因：成片黑帧 → 落在哪个部件 → 该部件同位置的黑帧。

    判据不变（成片黑帧必须能对照回它的来源），变的是「来源」：流拷贝这一刀
    切不出新黑场，切出来的黑只可能是某一期成片自带的。

    返回 `(mapped, inherited, src_errs)`；`inherited` 是被部件覆盖的
    (部件路径, 成片起点, 成片终点)——它不是「这里放行」，进 detail 让读者看见
    「这一处黑是某一期已经记过的那个」。部件查不动就记 src_errs（宁严勿松）。
    """
    mapped: list[tuple] = []
    by_part: dict[str, list[tuple]] = {}
    for bi, (b0, b1) in enumerate(blacks):
        for p in asm["parts"]:
            lo, hi = p["offset"], p["offset"] + p["duration"]
            if b1 <= lo or b0 >= hi:
                continue
            flo, fhi = max(b0, lo), min(b1, hi)
            span = (p["path"], flo - lo, fhi - lo, flo, fhi, 1, bi)
            mapped.append(span)
            by_part.setdefault(p["path"], []).append(span)

    covered, src_errs = _probe_source_blacks(by_part)

    defects = _black_defects(mapped, covered)
    inherited = [(s[0], s[3], s[4]) for s in mapped if s in covered]
    return defects, inherited, src_errs


def _music_plan(episode: Path) -> dict | None:
    """试听型（稿子有 `音乐段` 块）的音乐时间轴；普通期返回 None。

    三审 S2'：qc 的「与排片一致」基准是 04-clips 的段落排片（222.6s），
    试听型成片按音乐时间轴铺（796s），拿旧基准卡必 FAIL——基准要跟着
    时间轴走。黑帧映射的段落起点、静音豁免的歌尾位置也在这里拿。
    解析失败/缺前置直接报错，不静默回退（跳过不是通过）。
    """
    from . import music
    script = episode / "02-script.md"
    if not script.exists() or not music.parse_script_music(script):
        return None
    mf = episode / "03-audio" / "manifest.json"
    if not mf.exists():
        raise SystemExit(f"FAIL 试听型质检需要 {mf}")
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    block = music.load_tracks_multi(episode)
    return music.build_timeline(episode, manifest, block)


def check(video: Path, plan: dict | None = None,
          audio: list[dict] | None = None) -> list[Check]:
    out: list[Check] = []
    asm = assembly_ledger(video.parent)
    # 音乐时间轴一次解析，多处复用（与排片一致 / 黑帧映射 / 静音豁免），
    # 不重复 parse 稿件与 build_timeline
    mplan = _music_plan(video.parent)

    vdur = _duration(video, "v:0")
    adur = _duration(video, "a:0")

    out.append(Check("音画时长对齐",
                     abs(vdur - adur) < AV_DRIFT_MAX,
                     f"画面 {vdur:.3f}s（{vdur / 60:.2f} 分钟）/ 音频 {adur:.3f}s 差 {abs(vdur - adur) * 1000:.0f}ms"))
    pkt_chk = _audio_packet_check(video, adur)
    if pkt_chk is not None:
        out.append(pkt_chk)

    if plan or asm:
        if mplan:
            want, basis = mplan["total_duration"], "排片"
        elif plan:
            want, basis = plan["total_duration"], "排片"
        else:
            # 总装期没有自己的排片，基准 = 各部件实测时长之和：
            # 账本数字由 assemble.sh 用 ffprobe 探出来，不是施工图上的期望值。
            want, basis = asm["total_duration"], f"总装账本 {len(asm['parts'])} 部件之和"
        out.append(Check("与排片一致",
                         abs(vdur - want) < AV_DRIFT_MAX,
                         f"{basis} {want:.3f}s 成片 {vdur:.3f}s 差 {abs(vdur - want) * 1000:.0f}ms"))
    else:
        out.append(Check("与排片一致", False,
                         "跳过：没有 04-clips.approved.json", skipped=True))

    # 音视频两路并行扫描（2026-11-27 实证优化）：
    # 1. 音频侧必须加 `-vn`（此前漏传导致 loudnorm 与 silencedetect 各自全片软解 1080p 视频）；
    #    用 `asplit=2` 并行分支同时跑 loudnorm 与 silencedetect（不可串接，否则 loudnorm 改电平门限）。
    # 2. 视频侧 `blackdetect` 仅解码画面（`-an`），与音频侧并发执行，28.6 分钟长片实测 95s → 35s（2.7×）。
    audio_cmd = [
        "ffmpeg", "-nostdin", "-i", str(video), "-vn",
        "-filter_complex",
        f"[0:a]asplit=2[a1][a2];[a1]loudnorm=print_format=json[o1];"
        f"[a2]silencedetect=n=-45dB:d={SILENCE_MAX}[o2]",
        "-map", "[o1]", "-map", "[o2]", "-f", "null", "-",
    ]
    video_cmd = [
        "ffmpeg", "-nostdin", "-i", str(video), "-vf",
        f"blackdetect=d={BLACK_MAX}:pic_th=0.98", "-an", "-f", "null", "-",
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_aud = pool.submit(_run, audio_cmd)
        f_vid = pool.submit(_run, video_cmd)
        err_a, rc_a = f_aud.result()
        err_v, rc_v = f_vid.result()

    # 响度与削波：用第二遍 loudnorm 的测量结果，不改文件
    m = re.search(r"\{[^{}]*input_i[^{}]*\}", err_a, re.S)
    if rc_a != 0:
        out.append(Check("响度测量", False,
                         f"ffmpeg 退出码 {rc_a}：{_tail(err_a)}"))
    elif m:
        d = json.loads(m.group(0))
        i, tp = float(d["input_i"]), float(d["input_tp"])
        out.append(Check(f"响度 {LUFS_TARGET}±{LUFS_TOL} LUFS",
                         abs(i - LUFS_TARGET) <= LUFS_TOL, f"{i:.2f} LUFS"))
        out.append(Check(f"真峰值 ≤ {TRUE_PEAK_MAX} dBTP",
                         tp <= TRUE_PEAK_MAX, f"{tp:.2f} dBTP"))
    else:
        out.append(Check("响度测量", False, "loudnorm 没吐出 JSON，检查 ffmpeg 构建"))

    # 黑帧。**判据：成片黑帧必须能对照回源片段。**
    # 动画的黑场转场是内容不是故障——源片转场原样带进成片，会被 blackdetect
    # 误报成缺陷（2026-08-09 实证：S01E07 两句台词间的情绪停顿黑场 0.54s、
    # S03E12 台词开口处的转场黑场 0.71s，源片同样黑）。
    # 所以成片黑帧映射回源区间，源片同样黑 = 内容，通过；成片独有黑 = 渲染
    # 缺陷，拦截。没 plan 时退回旧行为：无源可对照，宁严勿松。
    blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err_v) if rc_v == 0 else []
    probe_err = None if rc_v == 0 else f"ffmpeg 退出码 {rc_v}：{_tail(err_v)}"
    defects: list[tuple[str, float, float, float, float]] = []
    src_errs: list[str] = []
    inherited: list[tuple[str, float, float]] = []   # 总装期：继承自分期成片的黑场
    if blacks:
        if asm:
            defects, inherited, src_errs = _assembly_black_spans(
                [(float(a), float(b)) for a, b in blacks], asm)
        elif plan:
            mapped = _map_black_to_sources(
                [(float(a), float(b)) for a, b in blacks], plan, _seg_starts(mplan, audio))
            by_src: dict[str, list[tuple]] = {}
            for span in mapped:
                by_src.setdefault(span[0], []).append(span)
            covered, src_errs = _probe_source_blacks(by_src)
            # SP 特典素材（clip 带 sp 标记）的艺术暗场按 BLACK_MAX_SP 放宽
            sp_srcs = {c["source"] for s in plan["segments"] for c in s["clips"]
                       if c.get("sp")}
            defects = _black_defects(mapped, covered, sp_srcs)
        else:
            defects = [(str(video), float(a), float(b), float(a), float(b))
                       for a, b in blacks]
    detail = "无" if not (defects or src_errs) else "、".join(
        f"{Path(s).name} 成片 {f0:.1f}-{f1:.1f}s（源 {s0:.1f}-{s1:.1f}s 无黑）"
        for s, f0, f1, s0, s1 in defects[:3])
    if probe_err:
        detail = f"{probe_err}（黑帧检测没跑成，不判无黑）"
    if src_errs:
        detail = (detail + "；" if detail != "无" else "") + \
            f"源片对照失败 {len(src_errs)} 处（{'；'.join(src_errs[:2])}）"
    if inherited:
        # 「对照得回来源」不等于「来源本身没毛病」——一期成片里 SP35 倒A档片头
        # 那段黑是它自己那期记过的 FAIL。这里要把继承事实写出来，别让人以为
        # 总装把问题修好了。
        note = (f"继承分期成片黑场 {len(inherited)} 处（"
                + "、".join(f"{Path(s).name} {f0:.1f}-{f1:.1f}s"
                            for s, f0, f1 in inherited[:3])
                + "），见各期 06-check.log")
        detail = note if detail == "无" else f"{detail}；{note}"
    out.append(Check(f"无 >{BLACK_MAX}s 纯黑（源片转场除外）",
                     not defects and not probe_err and not src_errs, detail))

    # 静音。试听型的歌曲自然收尾（fade 尾音 < -45dB）不是缺陷——
    # 静音区间尾部对齐某个音乐事件终点（曲目/前景/BGM 段的结束）就豁免
    # （2026-08-16 实测：Planetes 自然收尾 793.6s、Departures 尾段 428.1s）。
    song_ends: set[float] = set()
    if mplan:
        for tr in mplan["tracks"]:
            for ev in tr["events"]:
                song_ends.add(round(ev["at"] + ev["t1"] - ev["t0"], 1))
    card_wins = ([(p["offset"], p["offset"] + p["duration"])
                  for p in asm["parts"] if p["kind"] == "card"] if asm else [])
    sils = [(float(s), float(e)) for s, e in re.findall(
        r"silence_start: ([\d.]+)\s+silence_end: ([\d.]+)", err_a)] if rc_a == 0 else []
    bad = [s for s, e in sils if not _silence_exempt(s, e, song_ends, card_wins)]
    if rc_a != 0:
        sil_detail = f"ffmpeg 退出码 {rc_a}：{_tail(err_a)}（静音检测没跑成，不判无静音）"
    elif bad:
        sil_detail = "、".join(f"{s:.1f}s" for s in bad[:3])
    else:
        sil_detail = "无" + (f"（章节卡静音桥接豁免 {len(card_wins)} 处）" if card_wins else "")
    out.append(Check(f"无 >{SILENCE_MAX}s 静音", rc_a == 0 and not bad, sil_detail))

    # 字幕。烧录之后没法从成片反查，所以查它的来源。
    # D132：查上屏的那份文本——过了本期字幕替换表之后的
    from .render import apply_subtitle_map, load_subtitle_map
    if plan and audio:
        shown = apply_subtitle_map(audio, load_subtitle_map(video.parent))
        out.extend(_subtitle_checks([("", s, a) for s, a in zip(plan["segments"], shown)]))
    elif asm:
        # 总装期：字幕卡由三期成片原样流拷贝带进来（未重编码，参数一致性自检与
        # 解码零报错在 assemble.sh 里），所以查它的产出方——逐期用它自己的
        # plan + manifest 跑同一套 wrap 检查。**这不是跳过**：字形、卡数、
        # 时长的数据是同一批，测的仍是真正上屏的那张卡。
        items: list[tuple[str, dict, dict]] = []
        missing: list[str] = []
        for part in asm["parts"]:
            if part["kind"] != "part":
                continue
            # 分期目录从账本的 `src` 拿：截尾件本体在 work/，它的 plan/manifest
            # 在**产出它的那一期**目录里（按文件位置推会全报缺前置）
            if not part.get("src"):
                missing.append(f"{part['name']} 的账本缺 src")
                continue
            ep = Path(part["src"])
            pt, mt = ep / "04-clips.approved.json", ep / "03-audio" / "manifest.json"
            if not (pt.exists() and mt.exists()):
                missing.append(f"{ep.name}/{pt.name if not pt.exists() else mt.name}")
                continue
            items += [(ep.name, s, a) for s, a in zip(
                json.loads(pt.read_text(encoding="utf-8"))["segments"],
                apply_subtitle_map(json.loads(mt.read_text(encoding="utf-8"))["segments"],
                                   load_subtitle_map(ep)))]
        if missing:
            for n in ("字幕无空段", "字幕不超宽", "字幕每卡一行", "字幕不超高"):
                out.append(Check(n, False, "跳过：缺 " + "、".join(missing), skipped=True))
        else:
            out.extend(_subtitle_checks(items))
    else:
        # **缺文件时必须把这四项显式列出来，不能悄悄不跑。**
        # 初版是 `if plan and audio:` 一个分支，缺了就少四项，最后打印「6/6 通过」——
        # 看着是满分，实际跳了三分之一，而字幕超框恰恰是最容易出事的那一类。
        # 2026-07-29 审计发现。跳过不是通过，退出码也要非零。
        why = "跳过：缺 " + " 和 ".join(
            x for x, ok in (("04-clips.approved.json", plan),
                            ("03-audio/manifest.json", audio)) if not ok)
        for n in ("字幕无空段", "字幕不超宽", "字幕每卡一行", "字幕不超高"):
            out.append(Check(n, False, why, skipped=True))
    return out


def _subtitle_checks(items: list[tuple[str, dict, dict]]) -> list[Check]:
    """字幕四项：`items` = [(前缀, 段落, manifest 段)]。

    查**渲染器真正会写出去的那份文本**，也就是过了 `render.wrap` 之后的每一行。

    初版查的是「原文长度 ÷ 每行容量 ≤ 最大行数」，等于假设了折行一定会发生。
    2026-07-29 实测这个假设是错的：libass 对无空格的中文不折行，91 字的段落
    渲成一整行横贯画面、两端各切掉十几个字，而这条检查判了 PASS。
    检查项一旦测的不是真实产物，通过就毫无意义。

    前缀只在总装期非空（同一部片里有三期的段落号，`3-2` 会撞号）。

    「不超宽」按 libass 的真实像素宽判（D146，`subtitle_metrics`）：render 的 `line_width`
    是折行约定（汉字 1 格 = 54 px、标点与 ASCII 半格），汉字实际约 46.5 px、标点满格、
    大写与数字比半格宽，拿约定宽度卡边距测的不是真实产物。超出边距的行 libass 会在空格处
    再折一次，所以「每卡一行」也把它算作折行。
    """
    from .render import inspect_subtitles

    rep = inspect_subtitles(items, check_px=True)
    multi = [c.card_id for c in rep.multi]
    return [
        Check("字幕无空段", not rep.empty, f"空段 {rep.empty}" if rep.empty else "无"),
        Check("字幕不超宽", not rep.too_wide,
              f"超宽（px）{rep.too_wide}" if rep.too_wide
              else f"{len(rep.cards)} 张卡，最宽一行 {rep.widest_px:.0f} px ≤ {rep.cap_px} px"),
        Check("字幕每卡一行", not multi,
              f"{len(multi)} 张卡折了行：{multi[:5]}" if multi else f"最多 {rep.tallest} 行"),
        Check("字幕不超高", not rep.too_tall,
              f"超高 {rep.too_tall}" if rep.too_tall else f"最多 {rep.tallest} 行 ≤ {rep.max_lines}"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", type=Path, help="本期目录，或直接给 mp4")
    ap.add_argument("--json", action="store_true",
                    help="结构化输出给 agent 消费。key 稳定（name/ok/detail/skipped），"
                         "别把 detail 里的中文当接口。退出码与人类模式一致。")
    a = ap.parse_args()
    paths.require_data()

    if a.target.is_dir():
        episode, video = a.target, a.target / "05-final.mp4"
    else:
        episode, video = a.target.parent, a.target
    if not video.exists():
        raise SystemExit(f"FAIL 找不到成片 {video}")

    plan_path = episode / "04-clips.approved.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else None

    mf = episode / "03-audio" / "manifest.json"
    audio = json.loads(mf.read_text(encoding="utf-8"))["segments"] if mf.exists() else None
    checks = check(video, plan, audio)
    passed = sum(c.ok for c in checks)
    skipped = sum(c.skipped for c in checks)
    failed = len(checks) - passed - skipped

    width = max(len(c.name) for c in checks) + 2
    lines = [f"{'SKIP' if c.skipped else 'PASS' if c.ok else 'FAIL'}  "
             f"{c.name:<{width}}{c.detail}" for c in checks]
    lines.append("-" * 62)
    tail = f"{passed}/{len(checks)} 通过"
    if skipped:
        tail += f"，{skipped} 项因缺前置文件未跑——**未跑不是通过**"
    if failed:
        tail += f"，{failed} 项不合格"
    if not failed and not skipped:
        tail += "。主观项仍需人看：节奏、画文是否贴合。"
    lines.append(tail)

    # 06-check.log 始终写：它是这一步落盘的耐久产物，--json 只改 stdout 给机器读。
    log = episode / "06-check.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if a.json:
        print(json.dumps({
            "passed": passed, "failed": failed, "skipped": skipped,
            "ok": not (failed or skipped),
            "checks": [c.__dict__ for c in checks],
        }, ensure_ascii=False))
    else:
        print("\n".join(lines))
        print(f"→ {log}")
    # 跳过也算不合格：门禁的意义是「全过才放行」，少跑一项就不知道那一项行不行。
    return 1 if (failed or skipped) else 0


if __name__ == "__main__":
    raise SystemExit(main())
