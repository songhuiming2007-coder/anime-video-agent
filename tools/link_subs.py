#!/usr/bin/env python3
"""前置对齐：把跨目录、异前缀的字幕按集号软链到视频同目录的规范名。

    python tools/link_subs.py <视频目录> <字幕目录> [--apply]
    python tools/link_subs.py --selftest

为什么存在：phase0 的 `_find_sub` 只吃「同目录 + 同主文件名 + `*.Chs&Jap.ass`」
的字幕，这是「绝不拿隔壁集字幕配这一集」的护栏，不放宽（字幕组命名千差万别，
把各家的集号正则死磕进核心引擎只会徒增维护负担）。所以在前置一步里把数据
清洗成标准格式：软链 0 字节、不碰原始下载目录、核心引擎零改动。

    <视频目录>/[VCB-Studio] PSYCHO-PASS [01][...].mkv
    <字幕目录>/[AI-Raws] PSYCHO-PASS #01 (...).ass
        ↓ --apply
    <视频目录>/[VCB-Studio] PSYCHO-PASS [01][...].Chs&Jap.ass → 软链到字幕真身

配对判据是**集号相等**（双侧各一个正则提取，可 CLI 覆盖）。任一侧集号缺失或
重复，该集拒做并进报告——软链配错集 phase0 不会替你报错，错的代价在这里拦。
默认 dry-run 只打印配对表，人眼核对后 `--apply` 才落地。幂等：已存在且指向
同一目标的链接跳过；指向别的目标报错，不静默覆盖。
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

# 双侧默认集号正则：视频侧吃压制组通例 [NN]，字幕侧吃诸神通例 #NN。
# 别家发布命名变了用 --v-pattern / --s-pattern 覆盖，不要去改这两个默认值。
V_PATTERN = r"\[(?P<episode>\d{2}|OVA)\]"
S_PATTERN = r"#(?P<episode>\d{2})"


def _ep_of(name: str, pattern: re.Pattern) -> int | None:
    m = pattern.search(name)
    if not m:
        return None
    raw = m.group("episode")
    return int(raw) if raw.isdigit() else 0      # OVA 归 E00，与 phase0 同规


def pair(videos: list[Path], subs: list[Path],
         v_pat: re.Pattern, s_pat: re.Pattern
         ) -> tuple[list[tuple[Path, Path]], list[str]]:
    """（视频, 字幕）配对表 + 问题报告。集号缺失/重复/无对象都进报告，不静默。"""
    def _by_ep(files, pat):
        out: dict[int, Path] = {}
        bad: list[str] = []
        for f in files:
            ep = _ep_of(f.name, pat)
            if ep is None:
                bad.append(f"集号认不出：{f.name}")
            elif ep in out:
                bad.append(f"E{ep:02d} 重复：{out[ep].name} / {f.name}")
            else:
                out[ep] = f
        return out, bad

    vmap, problems = _by_ep(videos, v_pat)
    smap, sbad = _by_ep(subs, s_pat)
    problems += sbad
    pairs = []
    for ep in sorted(vmap):
        if ep in smap:
            pairs.append((vmap[ep], smap[ep]))
        else:
            problems.append(f"E{ep:02d} 有视频没字幕")
    for ep in sorted(smap):
        if ep not in vmap:
            problems.append(f"E{ep:02d} 有字幕没视频")
    return pairs, problems


def apply_links(pairs: list[tuple[Path, Path]]) -> tuple[int, int]:
    """落地软链。返回 (新建数, 跳过数)。幂等；链接已存在且指向别处时报错。"""
    made = skipped = 0
    for video, sub in pairs:
        link = video.parent / f"{video.stem}.Chs&Jap.ass"
        if link.is_symlink():
            if link.resolve() == sub.resolve():
                skipped += 1
                continue
            raise SystemExit(f"FAIL {link.name} 已存在且指向 {link.resolve()}，"
                             f"与本次目标 {sub.name} 不符——人工处理，不覆盖")
        if link.exists():
            raise SystemExit(f"FAIL {link.name} 已存在且不是软链——人工处理，不覆盖")
        link.symlink_to(sub)
        made += 1
    return made, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video_dir", type=Path, nargs="?")
    ap.add_argument("sub_dir", type=Path, nargs="?")
    ap.add_argument("--apply", action="store_true", help="落地软链（默认 dry-run 只打印）")
    ap.add_argument("--v-pattern", default=V_PATTERN)
    ap.add_argument("--s-pattern", default=S_PATTERN)
    ap.add_argument("--selftest", action="store_true", help="内联自检后退出")
    a = ap.parse_args()

    if a.selftest:
        return _selftest()
    if not a.video_dir or not a.sub_dir:
        ap.error("给 <视频目录> <字幕目录>，或 --selftest")

    v_pat, s_pat = re.compile(a.v_pattern), re.compile(a.s_pattern)
    videos = sorted(a.video_dir.glob("*.mkv"))
    subs = sorted(a.sub_dir.glob("*.ass"))
    if not videos or not subs:
        raise SystemExit(f"FAIL 目录里没东西：{a.video_dir} 有 {len(videos)} 个 mkv，"
                         f"{a.sub_dir} 有 {len(subs)} 个 ass")
    pairs, problems = pair(videos, subs, v_pat, s_pat)
    for video, sub in pairs:
        print(f"  E{_ep_of(video.name, v_pat):02d}  {video.name}\n"
              f"       ← {sub.name}")
    if problems:
        print("问题：")
        for p in problems:
            print(f"  ⚠ {p}")
    print(f"---\n{len(pairs)} 对配对，{len(problems)} 个问题"
          + ("" if a.apply else "（dry-run，加 --apply 落地）"))
    if not a.apply:
        return 1 if problems else 0
    made, skipped = apply_links(pairs)
    print(f"软链落地：新建 {made}，跳过（已存在）{skipped}")
    return 1 if problems else 0


def _selftest() -> int:
    """内联自检：临时目录造名配对，覆盖 正常/缺号/重号/幂等 四个分支。"""
    v_pat, s_pat = re.compile(V_PATTERN), re.compile(S_PATTERN)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        vids = [root / f"[VCB] X [{e:02d}][1080p].mkv" for e in (1, 2, 3)]
        subs = [root / f"[AI] X #{e:02d} (BD).ass" for e in (1, 2, 3)]
        pairs, problems = pair(vids, subs, v_pat, s_pat)
        assert len(pairs) == 3 and not problems, (pairs, problems)
        assert pairs[0][1].name.startswith("[AI] X #01"), pairs[0]

        # 字幕缺 E02、E04 有字幕没视频
        pairs2, problems2 = pair(vids, [subs[0], root / "[AI] X #04 (BD).ass"],
                                 v_pat, s_pat)
        assert len(pairs2) == 1 and len(problems2) == 3, (pairs2, problems2)

        # 重号拒做
        _, problems3 = pair(vids, subs + [root / "[AI] X #01 (v2).ass"], v_pat, s_pat)
        assert any("重复" in p for p in problems3), problems3

        # 落地 + 幂等 + 内容可读
        for f in subs:
            f.write_text("字幕", encoding="utf-8")
        made, skipped = apply_links(pairs)
        assert (made, skipped) == (3, 0)
        made2, skipped2 = apply_links(pairs)
        assert (made2, skipped2) == (0, 3)
        link = root / "[VCB] X [01][1080p].Chs&Jap.ass"
        assert link.is_symlink() and link.read_text(encoding="utf-8") == "字幕"
    print("OK selftest 全过（配对/缺号/重号/幂等）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
