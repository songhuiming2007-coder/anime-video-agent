"""章节进度条（方案 C 纪实窄顶栏）解析、门禁、libass 真实进度动画与 `07-chapters.txt` 导出。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pipeline import chapters as ch
from pipeline import check_script as cs
from pipeline import paths, render

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="需要 ffmpeg / ffprobe",
)


def _write_script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "02-script.md"
    p.write_text(body, encoding="utf-8")
    return p


class TestParseAndGuards:
    def test_无章节标记返回None_且check_script不多出检查项(self, tmp_path: Path):
        p = _write_script(
            tmp_path,
            "## 段落 1\n\n配音：第一段话很短，第二季第二集里他站起来说了那句话。\n\n画面：\n  查询: 某台词\n",
        )
        assert ch.parse_script_chapters(p) is None
        names = [c.name for c in cs.run(p)]
        assert "章节顶栏" not in names

    def test_单层章节与穿透背景铺底音乐段(self, tmp_path: Path):
        p = _write_script(
            tmp_path,
            """## 章节 · 01 开篇
## 音乐段 B1 · 铺底
音乐: `曲甲` 完整版 00:00
状态: 背景铺底

## 段落 1
配音：第一段。

## 章节 · 02 试听
## 音乐段 M1 · 高潮
音乐: `曲甲` 完整版 00:10-00:30
状态: 前景试听，旁白停止
过渡: 淡出

## 章节结束
## 段落 2
配音：下期再见。
""",
        )
        spec = ch.parse_script_chapters(p)
        assert spec is not None
        assert len(spec.chapters) == 2
        assert spec.chapters[0].title == "01 开篇"
        assert spec.chapters[0].anchor == ch.AnchorRef("seg", 1)
        assert spec.chapters[1].title == "02 试听"
        assert spec.chapters[1].anchor == ch.AnchorRef("music", "M1")
        assert spec.end_anchor == ch.AnchorRef("seg", 2)

    def test_双层聚合与文末章节结束排除尾曲(self, tmp_path: Path):
        p = _write_script(
            tmp_path,
            """## 章节 · 序章
## 段落 1
配音：序幕文本。

## 章节 · 十二曲全解析
### 子节 · [ TRACK 01 / 12 ] 原罪の灯
## 段落 2
配音：第一首。

### 子节 · [ TRACK 02 / 12 ] 战歌
## 段落 3
配音：第二首。

## 章节结束
""",
        )
        spec = ch.parse_script_chapters(p)
        assert spec is not None
        assert [c.title for c in spec.chapters] == ["序章", "十二曲全解析"]
        assert [s.title for s in spec.chapters[1].subs] == [
            "[ TRACK 01 / 12 ] 原罪の灯",
            "[ TRACK 02 / 12 ] 战歌",
        ]
        assert spec.end_anchor == ch.AnchorRef("vo_fg_end", "")

    @pytest.mark.parametrize(
        "bad_body,msg",
        [
            ("## 章节 没有分隔符\n## 段落 1\n配音：甲。\n", "格式错误"),
            ("## 章节 ·   \n## 段落 1\n配音：甲。\n", "格式错误"),
            ("### 子节 · 孤儿\n## 段落 1\n配音：甲。\n", "必须写在某个"),
            ("## 章节 · 只有一章\n## 段落 1\n配音：甲。\n", "至少需要 2 个"),
            ("## 章节结束\n## 段落 1\n配音：甲。\n", "没有任何 `## 章节`"),
            (
                "## 章节 · 甲\n## 章节 · 乙\n## 段落 1\n配音：甲。\n",
                "起点重合",
            ),
            (
                "## 章节 · 甲\n## 段落 1\n配音：甲。\n"
                "## 章节 · 乙\n## 段落 2\n配音：乙。\n## 章节结束\n## 章节 · 丙\n## 段落 3\n配音：丙。\n",
                "之后不能再出现",
            ),
            (
                "## 子节 · 错层级\n## 段落 1\n配音：甲。\n",
                "子节标题层级错误",
            ),
            (
                "### 章节 · 错层级\n## 段落 1\n配音：甲。\n",
                "章节标题层级错误",
            ),
            (
                "## 章节 · 甲\n## 段落 1\n配音：甲。\n### 章节结束\n",
                "章节结束层级错误",
            ),
            (
                "### 章节粘连无分隔符\n## 段落 1\n配音：甲。\n",
                "章节标题层级错误",
            ),
        ],
    )
    def test_语法与结构错误报错且check_script标FAIL(self, tmp_path: Path, bad_body: str, msg: str):
        p = _write_script(tmp_path, bad_body)
        with pytest.raises(SystemExit, match=msg):
            ch.parse_script_chapters(p)
        res = ch.check_script_chapters(p)
        assert res is not None and res[0] is False and msg.replace("`", "") in res[1].replace("`", "")

    def test_超过8个一级章节被拦(self, tmp_path: Path):
        blocks = [f"## 章节 · 第{i}章\n## 段落 {i}\n配音：内容。\n" for i in range(1, 10)]
        p = _write_script(tmp_path, "\n".join(blocks))
        with pytest.raises(SystemExit, match="最多 8 个"):
            ch.parse_script_chapters(p)

    def test_窄格长标题被机检拦下(self, tmp_path: Path):
        blocks = [
            "## 章节 · 极短开场\n## 段落 1\n配音：一句话。\n",
            "## 章节 · 这是一个在极窄格子里绝对装不下的超长章节标题名称示例\n## 段落 2\n配音：一。\n",
        ]
        blocks.extend(f"## 章节 · 章{i}\n## 段落 {i}\n配音：{'字' * 200}。\n" for i in range(3, 8))
        p = _write_script(tmp_path, "\n".join(blocks))
        res = ch.check_script_chapters(p)
        assert res is not None and res[0] is False and "槽宽" in res[1]

    def test_segment_block不吞入章节标题后面的内容(self):
        text = "## 段落 1\n\n配音：测试。\n\n### 备注\n\n音乐: `曲` 继续播放至完整版结束\n\n## 章节 · 第二章\n"
        matches = cs._SEGMENT_BLOCK.findall(text)
        assert len(matches) == 1 and "## 章节" not in matches[0][1]
        assert "继续播放至完整版结束" in matches[0][1]

        from pipeline import music

        bodies = list(music._segment_bodies(text))
        assert len(bodies) == 1 and bodies[0][1] == matches[0][1].strip() or "继续播放至完整版结束" in bodies[0][1]

        sub_text = "## 段落 1\n\n配音：测试。\n\n### 子节 · 新小节\n\n## 段落 2\n\n配音：续。\n"
        sub_matches = cs._SEGMENT_BLOCK.findall(sub_text)
        sub_bodies = list(music._segment_bodies(sub_text))
        assert "### 子节" not in sub_matches[0][1]
        assert "### 子节" not in sub_bodies[0][1]

    def test_超长子节标题与非数字段号报错而非裸崩溃(self, tmp_path: Path):
        long_sub = "长" * 60
        p = _write_script(
            tmp_path,
            f"## 章节 · 甲\n### 子节 · {long_sub}\n## 段落 1\n配音：一段。\n"
            "## 章节 · 乙\n## 段落 2\n配音：二段。\n",
        )
        res = ch.check_script_chapters(p)
        assert res is not None and res[0] is False and "过宽" in res[1] or "超出画面" in res[1]

        bad_spec = ch.ChapterSpec(chapters=(
            ch.ChapterSpecItem("甲", ch.AnchorRef("seg", "三")),
            ch.ChapterSpecItem("乙", ch.AnchorRef("seg", 2)),
        ))
        with pytest.raises(SystemExit, match="不是整数"):
            ch.resolve_chapters(tmp_path, bad_spec, {1: (0.0, 5.0), 2: (5.0, 10.0)}, None, 10.0)


class TestBadgeResolution:
    def test_显式铭牌与关闭(self, tmp_path: Path):
        ep = tmp_path / "ep"
        ep.mkdir()
        (ep / "01-topic.md").write_text("进度条铭牌: VOL.02 # 备注\n", encoding="utf-8")
        assert ch.resolve_badge(ep) == "[ VOL.02 ]"
        (ep / "01-topic.md").write_text("进度条铭牌: [ EBE · 12 ]\n", encoding="utf-8")
        assert ch.resolve_badge(ep) == "[ EBE · 12 ]"
        (ep / "01-topic.md").write_text("进度条铭牌: 无\n", encoding="utf-8")
        assert ch.resolve_badge(ep) is None
        (ep / "01-topic.md").write_text("进度条铭牌: [ SUPERCELL CHRONICLE EXTRA ]\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="过宽"):
            ch.resolve_badge(ep)

    def test_企划卷自动推断卷序铭牌_独立期返回None(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(paths, "ROOT", tmp_path)
        monkeypatch.setattr(paths, "DATA", tmp_path / "data")
        proj = tmp_path / "data" / "episodes" / "某企划"
        v2 = proj / "02-第二卷"
        v2.mkdir(parents=True)
        (v2 / "01-topic.md").write_text("番: 某番\n", encoding="utf-8")
        (proj / "00-project.md").write_text(
            "企划: 测试企划\n\n## 卷表\n\n| 卷序 | 目录 | 年份 | 时长预算 | 片尾 |\n|---|---|---|---|---|\n"
            "| 1 | 01-第一卷 | 2011 | 10-12分钟 | 完整保留 |\n"
            "| 2 | 02-第二卷 | 2012 | 10-12分钟 | 完整保留 |\n",
            encoding="utf-8",
        )
        assert ch.resolve_badge(v2) == "[ VOL.02 ]"

        plain = tmp_path / "data" / "episodes" / "2026-11-11-独立期"
        plain.mkdir(parents=True)
        (plain / "01-topic.md").write_text("番: 某番\n", encoding="utf-8")
        assert ch.resolve_badge(plain) is None


class TestChaptersTxtAndMapping:
    def test_格式与自动补零及同秒合并(self):
        txt = ch.format_chapters_txt([(0.2, "序章"), (0.8, "第一幕"), (65.9, "尾声")], 120.0)
        assert txt == "00:00 序章 · 第一幕\n01:05 尾声\n"

        txt_hour = ch.format_chapters_txt([(12.0, "正片开始"), (3665.0, "终章")], 4000.0)
        assert txt_hour == "00:00:00 序幕\n00:00:12 正片开始\n01:01:05 终章\n"

    def test_中段切除与截尾映射(self):
        kept = [(0.0, 100.0), (120.0, 200.0)]
        assert ch.map_source_time_to_kept(10.0, kept) == pytest.approx(10.0)
        assert ch.map_source_time_to_kept(110.0, kept) is None
        assert ch.map_source_time_to_kept(150.0, kept) == pytest.approx(130.0)
        assert ch.map_source_time_to_kept(210.0, kept) is None

    def test_章节跨越切除口保留章尾并剔除仅落入淡出带的残章(self):
        # 章2 [10, 20) 开头 [10, 14) 被切走，后半截 [14, 20) 保留，应从缝口 10s 处标起
        kept = [(0.0, 10.0), (14.0, 24.0)]
        assert ch.map_chapter_to_kept(10.0, 20.0, kept, seam=0.5) == pytest.approx((10.0, 16.0))
        # seam=1.0 时 [9.2, 14.0) 的可读区全部落入左窗 1s 淡出带内，应整体剔除
        assert ch.map_chapter_to_kept(9.2, 14.0, kept, seam=1.0) is None

    def test_含前景音乐与尾曲默认停在vo_fg_end_且支持kept投影(self, tmp_path: Path):
        (tmp_path / "01-topic.md").write_text("番: 测试番\n", encoding="utf-8")
        sp = _write_script(
            tmp_path,
            "## 章节 · 01 叙事\n## 段落 1\n配音：甲。\n\n"
            "## 章节 · 02 试听\n### 子节 · 小节一\n## 音乐段 M1 · 高潮\n"
            "音乐: `曲` 完整版 00:00-00:20\n状态: 前景试听，旁白停止\n过渡: 淡出\n\n"
            "### 子节 · 小节二\n## 段落 2\n配音：乙。\n",
        )
        spec = ch.parse_script_chapters(sp)
        assert spec is not None and spec.end_anchor is None
        seg_span = {1: (0.0, 30.0), 2: (50.0, 80.0)}
        music_plan = {"blocks": [{"label": "M1", "start": 30.0, "dur": 20.0}]}
        # total_duration 120s 含 40s 纯享尾曲，未写章节结束也应停在 vo_fg_end=80s 并淡出
        res = ch.resolve_chapters(tmp_path, spec, seg_span, music_plan, 120.0)
        assert res.bar_end == pytest.approx(80.0)
        assert res.explicit_end is True

        # 裁掉 02 前半 [30, 52) 后，第 2 章与子节二合并保留并同步前移
        proj = ch.project_chapters_to_kept(res, [(0.0, 30.0), (52.0, 75.0)], seam=0.5, tail_fade=1.0)
        assert proj is not None
        assert [c.title for c in proj.chapters] == ["01 叙事", "02 试听"]
        assert proj.chapters[1].start == pytest.approx(30.0)
        assert proj.chapters[1].end == pytest.approx(53.0)
        assert [s.title for s in proj.chapters[1].subs] == ["小节二"]
        assert proj.chapters[1].subs[0].start == pytest.approx(30.0)


class TestRealFfmpegHudRender:
    def test_缺字体目录时render在切片前报错(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(paths, "ROOT", tmp_path)
        monkeypatch.setattr(paths, "DATA", tmp_path / "data")
        monkeypatch.setattr(paths, "CONFIG", tmp_path / "config")
        monkeypatch.setattr(paths, "_CONF", None)
        ep = tmp_path / "ep"
        (ep / "03-audio").mkdir(parents=True)
        (ep / "01-topic.md").write_text("类型: 杂谈\n", encoding="utf-8")
        script = (
            "## 章节 · 第一章\n## 段落 1\n配音：一段话。\n\n"
            "## 章节 · 第二章\n## 段落 2\n配音：二段话。\n"
        )
        (ep / "02-script.md").write_text(script, encoding="utf-8")
        from pipeline.align import compute_script_vo_hash
        (ep / "03-audio" / "manifest.json").write_text(
            json.dumps({
                "script_vo_hash": compute_script_vo_hash(ep / "02-script.md"),
                "segments": [{"index": 1, "duration": 2.0}, {"index": 2, "duration": 2.0}],
            }),
            encoding="utf-8",
        )
        (ep / "04-clips.approved.json").write_text(
            json.dumps({
                "total_duration": 4.0,
                "segments": [
                    {"index": 1, "status": "ok", "clips": [{"source": "x.mp4", "start": 0.0, "dur": 2.0}]},
                    {"index": 2, "status": "ok", "clips": [{"source": "x.mp4", "start": 0.0, "dur": 2.0}]},
                ],
            }),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match="字体目录"):
            render.run(ep)

        # 只有假字体文件（缺少 Songti.ttc）同样拦截
        fake_fonts = tmp_path / "data" / "fonts"
        fake_fonts.mkdir(parents=True)
        (fake_fonts / "dummy.ttf").write_bytes(b"fake")
        with pytest.raises(SystemExit, match="Songti.ttc"):
            render.run(ep)

    def test_无章节时vo_ass逐字节不变_无HUD样式(self, tmp_path: Path):
        segs = [{"index": 1, "duration": 2.5}]
        audio = [{"text": "一句普通字幕。", "sentences": None}]
        a = render.build_ass(segs, audio, tmp_path / "a.ass").read_text(encoding="utf-8")
        b = render.build_ass(segs, audio, tmp_path / "b.ass", hud=None).read_text(encoding="utf-8")
        assert a == b
        assert "HUD_" not in a

    @needs_ffmpeg
    def test_libass进度条像素逐帧推进与章节结束淡出(self, tmp_path: Path):
        resolved = ch.ResolvedChapters(
            chapters=(
                ch.ResolvedChapter("第一章", 0.0, 2.0),
                ch.ResolvedChapter("第二章", 2.0, 2.2),
            ),
            badge="[ VOL.01 ]",
            bar_end=2.2,
            total_duration=3.2,
            explicit_end=True,
        )
        hud = ch.build_hud_ass(resolved, width=1920)
        ass_path = render.build_ass(
            [{"index": 1, "duration": 3.2}],
            [{"text": "测试。", "sentences": None}],
            tmp_path / "vo.ass",
            hud=hud,
        )
        fonts_dir = paths.ROOT / "data" / "fonts"
        vf = f"ass={ass_path.name}"
        if fonts_dir.is_dir():
            (tmp_path / "_fonts").symlink_to(fonts_dir.resolve())
            vf = f"ass={ass_path.name}:fontsdir=_fonts"

        out_mp4 = tmp_path / "out.mp4"
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=24:d=3.2",
                "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out_mp4),
            ],
            check=True, cwd=tmp_path,
        )

        def gold_right_x(ts: float) -> tuple[int, int]:
            png = tmp_path / f"f_{ts:.2f}.png"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
                 "-ss", f"{ts:.2f}", "-i", str(out_mp4), "-frames:v", "1", str(png)],
                check=True,
            )
            arr = np.asarray(Image.open(png).convert("RGB"))
            band = arr[40:44, :, :]
            # 暖金色进度像素：R 很高且显著大于 B
            gold_mask = (band[:, :, 0].astype(int) > 180) & (
                band[:, :, 0].astype(int) - band[:, :, 2].astype(int) > 80
            )
            cols = np.where(gold_mask.any(axis=0))[0]
            top_max = int(arr[0:45, :, :].max())
            return (int(cols[-1]) if len(cols) else -1), top_max

        x_early, max_early = gold_right_x(0.30)
        x_late, _ = gold_right_x(1.70)
        x_after, max_after = gold_right_x(3.05)

        assert max_early > 150
        assert x_early > 190
        assert x_late - x_early > 350
        assert x_after == -1 and max_after < 15

    @needs_ffmpeg
    def test_子节两级聚合真实渲染不报错(self, tmp_path: Path):
        resolved = ch.ResolvedChapters(
            chapters=(
                ch.ResolvedChapter("序章", 0.0, 1.0),
                ch.ResolvedChapter(
                    "专辑全解析",
                    1.0,
                    3.0,
                    subs=(
                        ch.ResolvedSubChapter("[ 01 / 02 ] 原罪", 1.0, 2.0),
                        ch.ResolvedSubChapter("[ 02 / 02 ] 告别", 2.0, 3.0),
                    ),
                ),
            ),
            badge=None,
            bar_end=3.0,
            total_duration=3.0,
            explicit_end=False,
        )
        hud = ch.build_hud_ass(resolved, width=1920)
        ass_path = render.build_ass(
            [{"index": 1, "duration": 3.0}],
            [{"text": "测试。", "sentences": None}],
            tmp_path / "vo.ass",
            hud=hud,
        )
        out_png = tmp_path / "frame.png"
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=1920x1080:d=2.0",
                "-ss", "1.6", "-vf", f"ass={ass_path.name}", "-frames:v", "1", str(out_png),
            ],
            check=True, cwd=tmp_path,
        )
        arr = np.asarray(Image.open(out_png).convert("RGB"))
        band = arr[40:44, :, :]
        gold_mask = (band[:, :, 0].astype(int) > 180) & (band[:, :, 0].astype(int) - band[:, :, 2].astype(int) > 80)
        assert gold_mask.any()
