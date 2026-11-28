"""render.py 的纯函数测试。ffmpeg 命令本身的正确性由运行期 ffprobe 复核，
这里只测不依赖音视频的解析逻辑。"""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from pipeline import render

from pipeline.render import (_bgm_layout, _bgm_list, _body_entry_delay, _seg_entry, _music_bed, _runs, _vol_expr, _extract_loudnorm_json)
from pipeline.render import BGM_MID_ENTRY, BGM_OUTRO_ENTRY, FOREGROUND_LUFS


def _topic(tmp_path: Path, content: str) -> Path:
    ep = tmp_path / "ep"
    ep.mkdir(parents=True, exist_ok=True)
    (ep / "01-topic.md").write_text(content, encoding="utf-8")
    return ep


def test_entry_absent_returns_zero(tmp_path: Path):
    ep = _topic(tmp_path, "番: 罪恶王冠\nBGM正文: x\n")
    assert _body_entry_delay(ep) == 0.0


def test_entry_present_parses_seconds(tmp_path: Path):
    ep = _topic(tmp_path, "BGM正文切入点: 36秒\n")
    assert _body_entry_delay(ep) == 36.0


def test_entry_decimal(tmp_path: Path):
    ep = _topic(tmp_path, "BGM正文切入点: 35.2秒\n")
    assert _body_entry_delay(ep) == 35.2


def test_entry_malformed_raises(tmp_path: Path):
    ep = _topic(tmp_path, "BGM正文切入点: 三十六秒\n")
    with pytest.raises(SystemExit):
        _body_entry_delay(ep)


def test_no_topic_file_returns_zero(tmp_path: Path):
    assert _body_entry_delay(tmp_path) == 0.0


def test_bgm_list_parses_entries_and_auto(tmp_path: Path):
    ep = _topic(tmp_path, "BGM:\n  - A @36秒\n  - B\n  - C @470秒\n")
    assert _bgm_list(ep) == [("A", 36.0, 0.0), ("B", None, 0.0), ("C", 470.0, 0.0)]


def test_bgm_list_absent_returns_none(tmp_path: Path):
    ep = _topic(tmp_path, "BGM正文: x\n")
    assert _bgm_list(ep) is None


def test_bgm_list_malformed_at_raises(tmp_path: Path):
    ep = _topic(tmp_path, "BGM:\n  - A @ 啊秒\n")
    with pytest.raises(SystemExit):
        _bgm_list(ep)


def test_bgm_list_stops_at_non_item_line(tmp_path: Path):
    ep = _topic(tmp_path, "BGM:\n  - A @36秒\n\n## 下一节\n")
    assert _bgm_list(ep) == [("A", 36.0, 0.0)]


def test_bgm_list_offset_parses(tmp_path: Path):
    """+M秒 = 曲内偏移（跳片头引子），可单独用也可跟 @起点并用（@ 在前）。"""
    ep = _topic(tmp_path, "BGM:\n  - A +9秒\n  - B @204秒 +9.5秒\n")
    assert _bgm_list(ep) == [("A", None, 9.0), ("B", 204.0, 9.5)]


def test_bgm_list_malformed_offset_raises(tmp_path: Path):
    ep = _topic(tmp_path, "BGM:\n  - A +九秒\n")
    with pytest.raises(SystemExit):
        _bgm_list(ep)


def test_seg_entry_reads_and_rejects(tmp_path: Path):
    ep = _topic(tmp_path, "BGM中段切入点: 291秒\n")
    assert _seg_entry(ep, "中段", BGM_MID_ENTRY) == 291.0
    ep2 = _topic(tmp_path / "ep2", "BGM中段切入点: 三秒\n")
    with pytest.raises(SystemExit):
        _seg_entry(ep2, "中段", BGM_MID_ENTRY)
    assert _seg_entry(tmp_path, "结尾", BGM_OUTRO_ENTRY) is None


def _rec(dur: float, start: float, offset: float = 0.0) -> tuple[dict, float, float]:
    return {"dur": dur}, start, offset


def test_layout_crossfade_gap_last():
    # 本期真实结构：Departures(255s)36 起 → 100(175s)291 起 → Planetes 470 起
    plan = [_rec(255, 36), _rec(175, 291), _rec(367, 470)]
    lens, runs = _bgm_layout(plan, 490)
    assert lens == [258, 175, 20]
    assert runs == [0, 2]


def test_layout_three_contiguous_crossfades():
    plan = [_rec(100, 0), _rec(100, 90), _rec(100, 180)]
    lens, runs = _bgm_layout(plan, 300)
    assert lens == [93, 93, 120]
    assert runs == [0]


def test_layout_gap_mid_run():
    plan = [_rec(100, 0), _rec(100, 90), _rec(200, 200)]
    lens, runs = _bgm_layout(plan, 300)
    assert lens == [93, 100, 100]
    assert runs == [0, 2]


def test_layout_seek_offset_single_last():
    # 本期真实结构：愛にできる 414.5s 从曲内 9s 放起，单首铺到片尾
    plan = [_rec(414.5, 0, 9.0)]
    lens, runs = _bgm_layout(plan, 223.7)
    assert lens == [223.7]
    assert runs == [0]


def test_layout_seek_offset_shrinks_effective_dur():
    # 偏移 9s → 有效曲长 91s < 到下一首的 95s 间隔 → 停顿而非交叉，并拆成两个 run
    plan = [_rec(100, 0, 9.0), _rec(100, 95)]
    lens, runs = _bgm_layout(plan, 300)
    assert lens == [91, 205]
    assert runs == [0, 1]


class TestMusicBedRuns:
    """F2：连续事件合并成 run（曲目内位置连续 = 同一音轨，只变音量）。

    合并后音量用表达式做斜坡，不在交界处剪出淡变断口（03-music-cues.md
    过渡原则「不重新切一份音频」）。
    """

    def test_连续事件合并成一个run(self):
        evs = [
            {"t0": 0.0, "t1": 15.0, "vol": "foreground"},
            {"t0": 15.0, "t1": 33.6, "vol": "bgm"},
            {"t0": 263.0, "t1": 286.0, "vol": "foreground"},
        ]
        runs = _runs(evs)
        assert len(runs) == 2
        assert [(e["t0"], e["t1"]) for e in runs[0]] == [(0.0, 15.0), (15.0, 33.6)]
        assert runs[1] == [evs[2]]

    def test_跳点不合并(self):
        evs = [
            {"t0": 0.0, "t1": 15.0, "vol": "foreground"},
            {"t0": 263.0, "t1": 286.0, "vol": "foreground"},
        ]
        assert len(_runs(evs)) == 2

    def test_单事件run(self):
        assert len(_runs([{"t0": 0.0, "t1": 22.0, "vol": "foreground"}])) == 1

    def test_音量表达式_单段恒定_线性域(self):
        evs = [{"t0": 0.0, "t1": 15.0, "vol": "foreground"}]
        expr = _vol_expr(evs, -10.0)
        # 前景增益 = FOREGROUND_LUFS - lufs（当前 -19-(-10) = -9dB）→ 线性值。
        # 引用常量算期望，不写死数值（常量标定变了测试自动跟随）。
        # 断言计算值而不是字符串（二轮审查 B2'：字符串断言在音量语义
        # 被改坏时照样全绿——volume 滤镜里裸数字是线性倍率不是 dB）。
        fg = FOREGROUND_LUFS - (-10.0)
        assert float(expr) == pytest.approx(10 ** (fg / 20), abs=1e-3)

    def test_音量表达式_单段bgm_线性域(self):
        # B2''：单段路径的 bgm 分支（-26 - lufs）之前没被任何测试覆盖
        evs = [{"t0": 0.0, "t1": 15.0, "vol": "bgm"}]
        expr = _vol_expr(evs, -10.0)
        assert float(expr) == pytest.approx(10 ** (-16 / 20), abs=1e-3)

    def test_音量表达式_斜坡分段_线性域(self):
        evs = [
            {"t0": 0.0, "t1": 15.0, "vol": "foreground"},
            {"t0": 15.0, "t1": 33.6, "vol": "bgm"},
        ]
        expr = _vol_expr(evs, -10.0)
        # 平台值必须出现在表达式里且是线性域：
        # 前景（FOREGROUND_LUFS-(-10) dB）与 BGM -26-(-10)=-16dB
        fg = FOREGROUND_LUFS - (-10.0)
        assert f"{10 ** (fg / 20):.4f}" in expr
        assert f"{10 ** (-16 / 20):.4f}" in expr
        assert "lt(t,15.000)," in expr
        assert "(t-15.000)/0.50" in expr
        # 斜坡两端的线性值夹在中间（斜坡在线性域插值）
        assert f"{10 ** (fg / 20):.4f}+({10 ** (-16 / 20):.4f}-{10 ** (fg / 20):.4f})" in expr


class TestLoudnormTwoPass:
    """两遍归一的 JSON 提取（纯函数部分）。

    2026-08-16：单次 loudnorm 动态模式会把音乐段后的旁白压掉（段落 8 人声
    -13.3 vs 段落 1 -8.2，TTS 原始电平相同）；改两遍测量 + 线性增益。
    """

    STDERR = """[Parsed_loudnorm_0 @ 0x123] {
        "input_i" : "-22.16",
        "input_tp" : "-8.91",
        "input_lra" : "11.70",
        "input_thresh" : "-32.60",
        "target_offset" : "0.16",
        "target_i" : "-16.00"
    }
    [out#0 @ 0x456] frame=  N/A ..."""

    def test_提取测量JSON(self):
        m = _extract_loudnorm_json(self.STDERR)
        assert m["input_i"] == "-22.16"
        assert m["target_offset"] == "0.16"
        assert m["input_lra"] == "11.70"

    def test_无JSON报错(self):
        with pytest.raises(SystemExit):
            _extract_loudnorm_json("ffmpeg 日志，没有 JSON")


class TestBgmPlanGuards:
    """配了 BGM 却读不到 `番:` → 报错，不静默渲无 BGM 版（2026-08-16 审计 2-11）。

    「没配曲库不拦渲染」指允许不配；配了（`BGM:` 列表或 `BGM正文` 槽位）
    却被静默丢弃是「写错了却不报」，与试听型路径同一口径。
    """

    def test_BGM列表无番字段_报错(self, tmp_path: Path):
        from pipeline.render import _bgm_plan
        ep = _topic(tmp_path, "BGM:\n  - 某曲\n")
        with pytest.raises(SystemExit, match="番"):
            _bgm_plan(ep, 100.0, [0.0])

    def test_命名槽位无番字段_同样报错(self, tmp_path: Path):
        from pipeline.render import _bgm_plan
        ep = _topic(tmp_path, "BGM正文: 某曲\n")
        with pytest.raises(SystemExit, match="番"):
            _bgm_plan(ep, 100.0, [0.0])

    def test_什么都没配_无番也不拦(self, tmp_path: Path):
        from pipeline.render import _bgm_plan
        ep = _topic(tmp_path, "类型: 杂谈\n")
        assert _bgm_plan(ep, 100.0, [0.0]) is None


class TestCutGuards:
    """负起点守卫（2026-08-16 审计 2-12）：ffmpeg 的 -ss 对负值按片尾倒数
    解释，切出错误画面且守卫 2（时长复核）照样通过。"""

    def test_负起点在碰ffmpeg之前就被拦(self, tmp_path: Path):
        from pipeline.render import cut
        with pytest.raises(SystemExit, match="负"):
            cut({"source": "/不存在的片源.mkv", "start": -5.0, "dur": 3.0},
                tmp_path / "x.mp4", frames=72)

    def test_hhmmss负数报错(self):
        from pipeline.render import _hhmmss_to_sec
        with pytest.raises(SystemExit, match="负数"):
            _hhmmss_to_sec("-5")
        with pytest.raises(SystemExit):
            _hhmmss_to_sec("-1:30")

    def test_hhmmss正常写法不受影响(self):
        from pipeline.render import _hhmmss_to_sec
        assert _hhmmss_to_sec("1:33.75") == pytest.approx(93.75)
        assert _hhmmss_to_sec("0:00") == 0.0


class TestAssEscape:
    """ASS 正文转义（2026-08-16 审计 2-24）：花括号是 override 标签块语法，
    稿件文本带着它会被 libass 当标签整段吃掉——字幕内容凭空消失且不报错。"""

    def test_花括号换成全角(self):
        from pipeline.render import _ass_escape
        # 串里的字面反斜杠随反斜杠规则一并转全角（二次审计 §6-3 起）
        assert _ass_escape("他说{\\an8}了什么") == "他说｛＼an8｝了什么"

    def test_换行压成空格(self):
        from pipeline.render import _ass_escape
        assert "\n" not in _ass_escape("第一行\n第二行\r\n第三行")

    def test_普通文本原样(self):
        from pipeline.render import _ass_escape
        s = "注意，她没说啥客套话。就在这儿——你永远不用猜。"
        assert _ass_escape(s) == s

    def test_空字幕卡报错不静默渲空卡(self, tmp_path: Path):
        from pipeline.render import build_ass
        segments = [{"index": 1, "duration": 5.0}]
        audio = [{"text": " ", "sentences": None}]     # 整段空白 → 卡内容为空
        with pytest.raises(SystemExit, match="空字幕卡"):
            build_ass(segments, audio, tmp_path / "vo.ass")

    def test_成片ASS里花括号真的被转义(self, tmp_path: Path):
        # 端到端：不转义的话 {an8} 是 override 标签块，libass 把它吃掉，
        # 字幕内容凭空消失且不报错——这正是要防的静默失败
        from pipeline.render import build_ass
        segments = [{"index": 1, "duration": 5.0}]
        audio = [{"text": "他说{an8}了什么", "sentences": None}]
        dest = build_ass(segments, audio, tmp_path / "vo.ass")
        text = dest.read_text(encoding="utf-8")
        assert "｛an8｝" in text and "{an8}" not in text


class TestAssEscapeBackslash:
    """ASS 转义补反斜杠（二次审计 §6-3）：`\\N`/`\\h` 在花括号外同样是
    标签语法（libass/VSFilter 均识别），字面反斜杠进稿件会折行/丢字。"""

    def test_反斜杠转全角(self):
        from pipeline.render import _ass_escape
        assert _ass_escape("C:\\Games") == "C:＼Games"
        assert _ass_escape("a\\Nb") == "a＼Nb"

    def test_成片ASS不含原始反斜杠(self, tmp_path: Path):
        # 单行卡：文件里唯一可能的反斜杠来源就是稿件文本（折行标记 \N
        # 只在多行卡出现），端到端断言转义真的发生了
        from pipeline.render import build_ass
        segments = [{"index": 1, "duration": 5.0}]
        audio = [{"text": "目录C:\\Games里的\\N不是换行", "sentences": None}]
        dest = build_ass(segments, audio, tmp_path / "vo.ass")
        text = dest.read_text(encoding="utf-8")
        assert "\\" not in text and "＼" in text


class TestBgmBedEntry:
    """`_bgm_bed` 的 plan 改由 run() 入口解析（配置前置校验，2026-08-18 复盘②）。
    没配 BGM（plan 为 None）直接返回 None，不碰 ffmpeg、不读 episode 目录。"""

    def test_无plan返回None(self, tmp_path: Path):
        from pipeline.render import _bgm_bed
        assert _bgm_bed(None, 100.0, tmp_path) is None


class TestRenderStaleArtifactGuard:
    """渲染终极因果防线（Stale Artifact Guard）：
    若 02-script.md 台词被修改但 03-audio 未更新，render 拒绝渲染，防盲目压制。
    """

    def test_台词不匹配拒绝渲染(self, tmp_path: Path):
        import json
        from pipeline.align import compute_script_vo_hash
        from pipeline import render

        ep = tmp_path / "ep"
        (ep / "03-audio").mkdir(parents=True)
        script_file = ep / "02-script.md"
        script_file.write_text("## 段落 1\n\n配音：原始台词。\n", encoding="utf-8")
        h = compute_script_vo_hash(script_file)
        (ep / "03-audio" / "manifest.json").write_text(
            json.dumps({"script_vo_hash": h, "segments": [{"index": 1, "duration": 5.0}]}),
            encoding="utf-8"
        )
        (ep / "04-clips.approved.json").write_text(
            json.dumps({"segments": [{"index": 1, "status": "ok", "clips": []}]}),
            encoding="utf-8"
        )

        # 篡改台词
        script_file.write_text("## 段落 1\n\n配音：改了台词未更配音。\n", encoding="utf-8")

        with pytest.raises(SystemExit, match="配音台词已被修改.*Hash 校验不匹配"):
            render.run(ep)

    def test_传入force可跳过渲染哈希校验(self, tmp_path: Path, monkeypatch):
        import json
        from pipeline.align import compute_script_vo_hash
        from pipeline import render

        ep = tmp_path / "ep"
        (ep / "03-audio").mkdir(parents=True)
        script_file = ep / "02-script.md"
        script_file.write_text("## 段落 1\n\n配音：原始台词。\n", encoding="utf-8")
        h = compute_script_vo_hash(script_file)
        (ep / "03-audio" / "manifest.json").write_text(
            json.dumps({"script_vo_hash": h, "segments": [{"index": 1, "duration": 5.0}]}),
            encoding="utf-8"
        )
        (ep / "04-clips.approved.json").write_text(
            json.dumps({"segments": [{"index": 1, "status": "ok", "clips": []}]}),
            encoding="utf-8"
        )

        # 篡改台词
        script_file.write_text("## 段落 1\n\n配音：改了台词未更配音。\n", encoding="utf-8")

        # 传入 force=True 会跳过哈希检查，继续走到后续段落状态或时长校验
        # 这里没有 clips 会报 status 不是 ok 或 clips 为空，证明哈希校验已被放行
        with pytest.raises(SystemExit) as exc_info:
            render.run(ep, force=True)
        assert "Hash 校验不匹配" not in str(exc_info.value)



def test_runs_成片不连续不合并():
    """自然收尾：曲目内连续（94→324.5s）但成片位置隔几百秒时必须拆成两个 run——
    合并会把 230s 收尾错摆到前景块后面，音乐床比成片短一半（皮套囚徒期实测）。"""
    from pipeline.render import _runs
    fg = {"t0": 61.0, "t1": 94.0, "vol": "foreground", "at": 95.9}
    nat = {"t0": 94.0, "t1": 324.52, "vol": "natural", "at": 503.13}
    runs = _runs([fg, nat])
    assert len(runs) == 2
    # 两处都连续时仍合并（第一期 M6 前景→收尾形态，行为不变）
    fg2 = {"t0": 0.0, "t1": 10.0, "vol": "foreground", "at": 494.0}
    nat2 = {"t0": 10.0, "t1": 272.16, "vol": "natural", "at": 504.0}
    assert len(_runs([fg2, nat2])) == 1


class TestRenderApprovedDiffHardGate:
    """Spec §4.2 R6 & R3：render approved 过期硬闸。"""

    def _setup(self, ep: Path, clips_data: dict | None, approved_data: dict):
        if clips_data is not None:
            (ep / "04-clips.json").write_text(
                json.dumps(clips_data, ensure_ascii=False), encoding="utf-8"
            )
        (ep / "04-clips.approved.json").write_text(
            json.dumps(approved_data, ensure_ascii=False), encoding="utf-8"
        )
        audio_dir = ep / "03-audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        (audio_dir / "manifest.json").write_text(
            json.dumps({"segments": [{"index": 1, "duration": 5.0}]}, ensure_ascii=False),
            encoding="utf-8"
        )

    def test_diff非空时抛出SystemExit指向重走05(self, tmp_path):
        ep = tmp_path / "ep"
        ep.mkdir()
        clips_data = {"segments": [{"index": 1, "status": "ok", "clips": [{"dur": 5.0, "start": 10.0}]}]}
        appr_data = {"segments": [{"index": 1, "status": "ok", "clips": [{"dur": 5.0, "start": 0.0}]}]}
        self._setup(ep, clips_data, appr_data)

        with pytest.raises(SystemExit) as exc:
            render.run(ep)
        assert "04-clips.approved.json 已过期" in str(exc.value)
        assert "python -m pipeline.review" in str(exc.value)

    def test_diff非空但传force时放行硬闸(self, tmp_path):
        ep = tmp_path / "ep"
        ep.mkdir()
        clips_data = {"segments": [{"index": 1, "status": "bad", "clips": []}]}
        appr_data = {"segments": [{"index": 1, "status": "bad", "clips": []}]}
        # 让 clips.json 存在 diff
        clips_data_diff = {"segments": [{"index": 1, "status": "bad", "clips": [], "diff": True}]}
        self._setup(ep, clips_data_diff, appr_data)

        with pytest.raises(SystemExit) as exc:
            render.run(ep, force=True)
        assert "04-clips.approved.json 已过期" not in str(exc.value)
        assert "状态不是 ok" in str(exc.value)

    def test_clips_json缺失时不比且放行硬闸(self, tmp_path):
        ep = tmp_path / "ep"
        ep.mkdir()
        appr_data = {"segments": [{"index": 1, "status": "bad", "clips": []}]}
        self._setup(ep, None, appr_data)  # 无 04-clips.json

        with pytest.raises(SystemExit) as exc:
            render.run(ep)
        assert "04-clips.approved.json 已过期" not in str(exc.value)
        assert "状态不是 ok" in str(exc.value)

    def test_内容相同放行硬闸(self, tmp_path):
        ep = tmp_path / "ep"
        ep.mkdir()
        appr_data = {"segments": [{"index": 1, "status": "bad", "clips": []}]}
        self._setup(ep, appr_data, appr_data)  # 逐字节一致

        with pytest.raises(SystemExit) as exc:
            render.run(ep)
        assert "04-clips.approved.json 已过期" not in str(exc.value)
        assert "状态不是 ok" in str(exc.value)



class TestAvsyncSubs:
    """D86：试看块（音画同源）切自番剧集时，窗口内的原片中文台词按成片时间上屏。"""

    CUES = [(100.0, 102.0, "窗口前"), (110.0, 112.5, "第一句"), (113.0, 115.0, "第二句"), (139.9, 141.0, "压线")]

    def _plan(self, *blocks):
        return {"blocks": list(blocks)}

    def _setup(self, monkeypatch, cues):
        from pipeline import subcues
        monkeypatch.setattr(render.bgm, "anime_of", lambda ep: "伪恋")
        monkeypatch.setattr(render.bgm, "animes_of", lambda ep: ["伪恋"])
        monkeypatch.setattr(subcues, "load_cues", lambda anime, key: cues.get((anime, key)))

    def test_番剧集窗口内台词映射到成片时间(self, tmp_path, monkeypatch):
        self._setup(monkeypatch, {("伪恋", "S01E07"): self.CUES})
        blk = {"label": "M3", "avsync": "伪恋 S01E07", "t0": 109.0, "dur": 31.0, "start": 60.0}
        # 第一句：60 + (110-109) = 61 起；压线那句只剩 0.1 s（< 0.3）丢掉；窗口前的不进
        assert render._avsync_cues(tmp_path, self._plan(blk)) == [
            (61.0, 63.5, "第一句"), (64.0, 66.0, "第二句")]

    def test_SP素材与关掉的块不配字幕(self, tmp_path, monkeypatch):
        self._setup(monkeypatch, {})
        sp = {"label": "M5", "avsync": "伪恋 SP04", "t0": 0.0, "dur": 20.0, "start": 0.0}
        off = {"label": "M6", "avsync": "S01E07", "t0": 0.0, "dur": 20.0, "start": 30.0, "src_subs": False}
        assert render._avsync_cues(tmp_path, self._plan(sp, off, {"label": "M1", "start": 0, "dur": 9})) == []

    def test_番剧集缺字幕索引当场报错(self, tmp_path, monkeypatch):
        self._setup(monkeypatch, {})
        blk = {"label": "M3", "avsync": "S01E07", "t0": 0.0, "dur": 20.0, "start": 0.0}
        with pytest.raises(SystemExit, match="原声字幕: 关"):
            render._avsync_cues(tmp_path, self._plan(blk))

    def test_build_ass_extra出SRC行_不给extra逐字节不变(self, tmp_path):
        from pipeline.render import build_ass
        segments = [{"index": 1, "duration": 5.0}]
        audio = [{"text": "旁白一句。", "sentences": None}]
        plain = build_ass(segments, audio, tmp_path / "a.ass").read_text(encoding="utf-8")
        again = build_ass(segments, audio, tmp_path / "b.ass", extra=[]).read_text(encoding="utf-8")
        assert again == plain and "SRC" not in plain
        text = build_ass(segments, audio, tmp_path / "c.ass",
                         extra=[(61.0, 63.5, "第一句{x}")]).read_text(encoding="utf-8")
        assert "\nStyle: SRC," in text
        assert "Dialogue: 0,0:01:01.00,0:01:03.50,SRC,,0,0,0,,第一句｛x｝" in text
        assert text.replace(render.SRC_STYLE_LINE, "").startswith(plain.split("[Events]")[0])


class TestWriteFinalAtomic:
    """最终合成先写临时文件再原子替换（2026-10-13 中断审计）。此前 ffmpeg 直写 05-final.mp4：
    中途被打断，上一版成片已被覆盖成半截，而 status 只看文件在不在，报「渲染完成，待质检」。
    变异：_write_final 改回直写 05-final.mp4 → 第一条红（旧成片被半截覆盖后又被删）。"""

    def _fake_ffmpeg(self, monkeypatch, fail):
        def run(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"half-written")       # ffmpeg 已经写了一半
            if fail is not None:
                raise fail
        monkeypatch.setattr(render.subprocess, "run", run)

    def test_被打断_旧成片原样_临时文件清掉(self, tmp_path, monkeypatch):
        (tmp_path / render.FINAL).write_bytes(b"previous good render")
        self._fake_ffmpeg(monkeypatch, KeyboardInterrupt())
        with pytest.raises(KeyboardInterrupt):
            render._write_final(tmp_path, tmp_path, ["ffmpeg"])
        assert (tmp_path / render.FINAL).read_bytes() == b"previous good render"
        assert not (tmp_path / render.FINAL_PART).exists()

    def test_ffmpeg失败_首次渲染不留下冒充成片的文件(self, tmp_path, monkeypatch):
        import subprocess
        self._fake_ffmpeg(monkeypatch, subprocess.CalledProcessError(1, "ffmpeg"))
        with pytest.raises(subprocess.CalledProcessError):
            render._write_final(tmp_path, tmp_path, ["ffmpeg"])
        assert not (tmp_path / render.FINAL).exists(), "status 靠它判渲染完成"
        assert not (tmp_path / render.FINAL_PART).exists()

    def test_成功_替换成新成片(self, tmp_path, monkeypatch):
        (tmp_path / render.FINAL).write_bytes(b"previous good render")
        self._fake_ffmpeg(monkeypatch, None)
        out = render._write_final(tmp_path, tmp_path, ["ffmpeg"])
        assert out == tmp_path / render.FINAL and out.read_bytes() == b"half-written"
        assert not (tmp_path / render.FINAL_PART).exists()


# ---------------------------------------------------------------------------
# N31：duration() 拒收静态图片（ffprobe 对 jpg 报单帧 0.04 s，不拦会静默切出 0.04 s 的片段）
# ---------------------------------------------------------------------------


needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="需要 ffmpeg / ffprobe")


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@needs_ffmpeg
@pytest.mark.parametrize("ext", ["jpg", "png", "webp"])
def test_duration_静态图片报错(tmp_path: Path, ext: str) -> None:
    img = tmp_path / f"still.{ext}"
    _ff("-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", str(img))
    with pytest.raises(SystemExit, match="静态图片"):
        render.duration(img)


@needs_ffmpeg
def test_duration_大图jpg走image2也拦(tmp_path: Path) -> None:
    """大尺寸 jpg 被 ffprobe 认成 image2、报 0.040000——就是 N31 说的静默 0.04 s；小图认成 jpeg_pipe。"""
    from PIL import Image

    img = tmp_path / "scan.jpg"
    Image.new("RGB", (4000, 3000), "red").save(img)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=format_name",
                            "-of", "csv=p=0", str(img)], capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "image2", "夹具真走到了 image2 这一支"
    with pytest.raises(SystemExit, match="静态图片"):
        render.duration(img)


@needs_ffmpeg
def test_duration_视频与音频照常(tmp_path: Path) -> None:
    mp4 = tmp_path / "v.mp4"
    _ff("-f", "lavfi", "-i", "color=c=blue:s=64x64:r=25:d=1.2", "-pix_fmt", "yuv420p", str(mp4))
    wav = tmp_path / "a.wav"
    _ff("-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "0.5", str(wav))
    assert render.duration(mp4) == pytest.approx(1.2, abs=0.05)
    assert render.duration(wav) == pytest.approx(0.5, abs=0.01)


@needs_ffmpeg
def test_duration_图片改成视频扩展名也拦(tmp_path: Path) -> None:
    """按 ffprobe 认出的格式判，不看扩展名。"""
    fake = tmp_path / "still.mp4"
    _ff("-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", "-f", "image2", str(fake))
    with pytest.raises(SystemExit, match="静态图片"):
        render.duration(fake)


class TestSubtitleMap:
    """D132 字幕显示替换表：只换上屏文本，配音与 manifest 不动；没命中的键当场报。"""

    def _audio(self):
        return [{"text": "二零零九年八月十二日，单曲《Kimi no Shiranai Monogatari》发售。",
                 "sentences": [{"text": "二零零九年八月十二日，单曲《Kimi no Shiranai Monogatari》发售。",
                                "start": 0.0, "duration": 4.0}]},
                {"text": "第十一首。", "sentences": None}]

    def test_没有文件是空表_原样返回(self, tmp_path: Path):
        from pipeline.render import apply_subtitle_map, load_subtitle_map
        audio = self._audio()
        assert load_subtitle_map(tmp_path) == {}
        assert apply_subtitle_map(audio, {}) is audio

    def test_替换句子与整段_原manifest不动(self, tmp_path: Path):
        from pipeline.render import apply_subtitle_map, load_subtitle_map
        (tmp_path / "02-subtitles.json").write_text(json.dumps({
            "_note": "注释键忽略",
            "二零零九年八月十二日": "2009年8月12日",
            "Kimi no Shiranai Monogatari": "君の知らない物語",
            "十一": "11"}, ensure_ascii=False), encoding="utf-8")
        audio = self._audio()
        shown = apply_subtitle_map(audio, load_subtitle_map(tmp_path))
        assert shown[0]["sentences"][0]["text"] == "2009年8月12日，单曲《君の知らない物語》发售。"
        assert shown[0]["text"] == shown[0]["sentences"][0]["text"]
        assert shown[1]["text"] == "第11首。"
        assert audio[0]["sentences"][0]["text"].startswith("二零零九年")   # 副本，不改原件

    def test_长键优先且结果不再参与匹配(self):
        from pipeline.render import apply_subtitle_map
        audio = [{"text": "三月十一日，第十一首，11人", "sentences": None}]
        shown = apply_subtitle_map(audio, {"三月十一日": "3月11日", "十一": "11", "11": "壹壹"})
        # 连锁替换会把刚换出的 11 再换成 壹壹
        assert shown[0]["text"] == "3月11日，第11首，壹壹人"

    def test_未命中的键报错(self):
        from pipeline.render import apply_subtitle_map
        with pytest.raises(SystemExit, match="找不到"):
            apply_subtitle_map(self._audio(), {"二零一零年": "2010年"})

    def test_跨句的键算未命中(self):
        from pipeline.render import apply_subtitle_map
        audio = [{"text": "甲。乙", "sentences": [{"text": "甲。", "start": 0, "duration": 1},
                                                 {"text": "乙", "start": 1, "duration": 1}]}]
        with pytest.raises(SystemExit, match="跨了句"):
            apply_subtitle_map(audio, {"甲。乙": "丙"})

    def test_成片ASS用的是替换后文本(self, tmp_path: Path):
        from pipeline.render import apply_subtitle_map, build_ass
        segments = [{"index": 1, "duration": 4.0}, {"index": 2, "duration": 1.0}]
        shown = apply_subtitle_map(self._audio(), {"十一": "11"})
        text = build_ass(segments, shown, tmp_path / "vo.ass").read_text(encoding="utf-8")
        assert "第11首" in text and "第十一首" not in text


class TestRenderSubtitleFailFast:
    """D171：render.run() 入口在切片与 ffmpeg 编码前 fail-fast 拦截字幕版式违规。"""

    def _setup_ep(self, tmp_path: Path, segs: list[tuple[int, list[str]]], sub_map: dict | None = None) -> Path:
        ep = tmp_path / "ep"
        (ep / "03-audio").mkdir(parents=True)
        man_segs, plan_segs = [], []
        for idx, sent_texts in segs:
            full = "".join(sent_texts)
            dur = float(len(sent_texts) * 2)
            sents = [{"text": t, "start": float(k * 2), "duration": 2.0} for k, t in enumerate(sent_texts)]
            man_segs.append({"index": idx, "file": f"{idx:02d}.wav", "duration": dur, "text": full, "sentences": sents})
            plan_segs.append({
                "index": idx, "status": "ok", "duration": dur, "text": full,
                "clips": [{"source": "/nonexistent.mkv", "start": 0.0, "dur": dur}],
            })
        (ep / "03-audio" / "manifest.json").write_text(
            json.dumps({"segments": man_segs}, ensure_ascii=False), encoding="utf-8",
        )
        (ep / "04-clips.approved.json").write_text(
            json.dumps({"segments": plan_segs}, ensure_ascii=False), encoding="utf-8",
        )
        if sub_map is not None:
            (ep / "02-subtitles.json").write_text(json.dumps(sub_map, ensure_ascii=False), encoding="utf-8")
        return ep

    def _forbid_work(self, monkeypatch):
        import tempfile as _tf

        orig_mkdtemp = _tf.mkdtemp

        def guarded_mkdtemp(*a, **kw):
            if kw.get("prefix") == "render-":
                pytest.fail("字幕违规必须在建 render 临时目录前拦截")
            return orig_mkdtemp(*a, **kw)

        monkeypatch.setattr(render, "cut", lambda *a, **kw: pytest.fail("字幕违规必须在 cut 之前拦截"))
        monkeypatch.setattr(render, "ThreadPoolExecutor", lambda *a, **kw: pytest.fail("字幕违规必须在起切片线程池前拦截"))
        monkeypatch.setattr(render.tempfile, "mkdtemp", guarded_mkdtemp)

    def test_D171_书名号长句_23_82_83_经二次断卡通过verify_subtitles(self, tmp_path: Path):
        ep = self._setup_ep(
            tmp_path,
            [
                (23, ["8月15日。", "Aimer 第4张双主打单曲 CD《Anata ni Deawanakereba ~Kasetsutouka~ / Hoshikuzu Venus》正式发售。"]),
                (82, ["紧接着4月7日。", "Aimer 在 Tower Records 涩谷店地下一层办起第一场《Tower of Lovers》——"]),
                (83, ["最特别的一站。", "是四月十一日与十八日分别在东京品川基督教会和大阪举办的教会专场《Premier Live in church》。"]),
            ],
            sub_map={
                "第一": "第1",
                "四月十一日与十八日": "4月11日与18日",
                "Anata ni Deawanakereba ~Kasetsutouka~ / Hoshikuzu Venus": "あなたに出会わなければ 〜夏雪冬花〜 / 星屑ビーナス",
            },
        )
        man = json.loads((ep / "03-audio" / "manifest.json").read_text(encoding="utf-8"))
        plan = json.loads((ep / "04-clips.approved.json").read_text(encoding="utf-8"))
        mapped = render.apply_subtitle_map(man["segments"], render.load_subtitle_map(ep))
        rep = render.verify_subtitles(plan["segments"], mapped)
        assert not rep.empty and not rep.too_wide and not rep.multi and not rep.too_tall

    def test_D171_真实超格卡在切片前fail_fast并列卡号(self, tmp_path: Path, monkeypatch):
        from pipeline import subtitle_metrics as sm

        self._forbid_work(monkeypatch)
        monkeypatch.setattr(sm, "text_px", lambda s: render.line_width(s) * 46.5)

        ep = self._setup_ep(
            tmp_path,
            [
                (23, ["二零一二年。", "东京台场的 Joypolis 馆里架起了初音未来演唱会同款的 Dilad 透射屏。"]),
                (82, ["同年五月。", "之前提到过的乐评频道 Music Synopsis 对这首歌的赞美几乎没有留余地。"]),
                (83, ["紧接着。", "在 supercell 做了第三年主唱之后单飞的 Koeda 迎来了个人第一张迷你专辑。"]),
            ],
            sub_map={
                "第三": "第3",
                "第一": "第1",
            },
        )
        with pytest.raises(SystemExit) as exc_info:
            render.run(ep)
        msg = str(exc_info.value)
        assert "字幕每卡一行" in msg
        for cid in ("23-2", "82-2", "83-2"):
            assert cid in msg
        assert "Dilad" in msg
        assert "Music Synopsis" in msg
        assert "第3年主唱" in msg

    def test_空字幕段在切片前fail_fast(self, tmp_path: Path, monkeypatch):
        from pipeline import subtitle_metrics as sm

        self._forbid_work(monkeypatch)
        monkeypatch.setattr(sm, "text_px", lambda s: render.line_width(s) * 46.5)
        ep = self._setup_ep(tmp_path, [(1, ["   "])])
        with pytest.raises(SystemExit) as exc_info:
            render.run(ep)
        msg = str(exc_info.value)
        assert "字幕无空段" in msg and "1" in msg

    def test_单行像素超宽在切片前fail_fast(self, tmp_path: Path, monkeypatch):
        from pipeline import subtitle_metrics as sm

        self._forbid_work(monkeypatch)
        # 中文标点在 libass 里实际占满格（约 46.5 px）：30 格约定宽度下真实像素达 1860 > 1680
        monkeypatch.setattr(sm, "text_px", lambda s: len(s) * 46.5)
        ep = self._setup_ep(tmp_path, [(4, ["一、" * 20])])
        with pytest.raises(SystemExit) as exc_info:
            render.run(ep)
        msg = str(exc_info.value)
        assert "字幕不超宽" in msg and "字幕每卡一行" in msg and "4-1" in msg and "px" in msg

    @needs_ffmpeg
    @pytest.mark.skipif(not shutil.which("fc-match"), reason="需要 fontconfig (fc-match)")
    def test_D171_真实像素度量在切片前fail_fast(self, tmp_path: Path, monkeypatch):
        self._forbid_work(monkeypatch)
        ep = self._setup_ep(
            tmp_path,
            [
                (1, ["一、" * 20]),
                (82, ["同年五月。", "之前提到过的乐评频道 Music Synopsis 对这首歌的赞美几乎没有留余地。"]),
            ],
        )
        with pytest.raises(SystemExit) as exc_info:
            render.run(ep)
        msg = str(exc_info.value)
        assert "字幕不超宽" in msg and "1-1" in msg
        assert "字幕每卡一行" in msg and "82-2" in msg and "Music Synopsis" in msg


class TestEncoderAndProbes:
    """2026-11 渲染性能优化护栏：硬件编码安全回退、单次 JSON ffprobe 与单遍 HUD 净顶条。"""

    def test_auto模式随硬件探测结果切换且保留限幅色彩(self, monkeypatch):
        monkeypatch.setattr(render, "VT_QUALITY", "75")
        monkeypatch.setattr(render, "PRESET", "faster")
        monkeypatch.setattr(render, "CRF", "18")

        monkeypatch.setattr(render, "_videotoolbox_available", lambda: True)
        vt = render.final_video_encoder_args("auto")
        assert vt[:2] == ["-c:v", "h264_videotoolbox"]
        assert "-color_range" in vt and vt[vt.index("-color_range") + 1] == "tv"
        assert "-pix_fmt" in vt and vt[vt.index("-pix_fmt") + 1] == "yuv420p"
        assert "-spatial_aq" in vt and vt[vt.index("-spatial_aq") + 1] == "1"

        monkeypatch.setattr(render, "_videotoolbox_available", lambda: False)
        sw = render.final_video_encoder_args("auto")
        assert sw == ["-c:v", "libx264", "-preset", "faster", "-crf", "18", "-pix_fmt", "yuv420p"]

    def test_显式libx264不触发vt探测(self, monkeypatch):
        monkeypatch.setattr(render, "_videotoolbox_available",
                            lambda: pytest.fail("libx264 模式不应探测 VideoToolbox"))
        sw = render.final_video_encoder_args("libx264")
        assert sw[:2] == ["-c:v", "libx264"]

    def test_显式videotoolbox不可用报错且非法值报错(self, monkeypatch):
        monkeypatch.setattr(render, "_videotoolbox_available", lambda: False)
        with pytest.raises(SystemExit, match="h264_videotoolbox"):
            render.final_video_encoder_args("videotoolbox")
        with pytest.raises(SystemExit, match="不支持"):
            render.final_video_encoder_args("nvenc")

    def test_vt_quality非法值直接报配置错且不干扰硬件探测(self, monkeypatch):
        render._videotoolbox_available.cache_clear()
        seen_cmds: list[list[str]] = []

        def fake_run(cmd, **kw):
            seen_cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(render.sys, "platform", "darwin")
        monkeypatch.setattr(render.subprocess, "run", fake_run)
        monkeypatch.setattr(render, "VT_QUALITY", "999")
        try:
            assert render._videotoolbox_available() is True
            assert seen_cmds and seen_cmds[0][seen_cmds[0].index("-q:v") + 1] == render._VT_PROBE_QUALITY
            with pytest.raises(SystemExit, match="vt_quality"):
                render.final_video_encoder_args("videotoolbox")
            with pytest.raises(SystemExit, match="vt_quality"):
                render.final_video_encoder_args("auto")
            monkeypatch.setattr(render, "VT_QUALITY", "bad")
            with pytest.raises(SystemExit, match="vt_quality"):
                render.final_video_encoder_args("videotoolbox")
            # 显式 libx264 不受 vt_quality 影响
            assert render.final_video_encoder_args("libx264")[:2] == ["-c:v", "libx264"]
        finally:
            render._videotoolbox_available.cache_clear()

    def test_脏流帧率与时长防护(self, tmp_path, monkeypatch):
        for bad_fps in ("0/0", "0", "-24", "24/0", "N/A", "1/2/3"):
            with pytest.raises(ValueError):
                render._fps_to_frame_time(bad_fps)

        monkeypatch.setattr(
            render.subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 0,
                stdout=json.dumps({"format": {"format_name": "mp4", "duration": "N/A"}, "streams": []}),
                stderr="",
            ),
        )
        with pytest.raises(SystemExit, match="时长非法"):
            render._probe_source(tmp_path / "bad.mp4")

        monkeypatch.setattr(
            render.subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 0,
                stdout=json.dumps({
                    "format": {"format_name": "mp4", "duration": "5.0"},
                    "streams": [{"r_frame_rate": "0/0"}],
                }),
                stderr="",
            ),
        )
        with pytest.raises(SystemExit, match="帧率非法"):
            render._probe_source(tmp_path / "badfps.mp4")

    def test_still_frames相对路径src_cache规范化命中(self, tmp_path: Path, monkeypatch):
        sub = tmp_path / "sub"
        sub.mkdir()
        fake_src = sub / "clip.mp4"
        fake_src.write_bytes(b"stub")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(render, "_source_path", lambda ep, tag: fake_src.resolve())
        monkeypatch.setattr(
            render, "_probe_source",
            lambda p: pytest.fail("规范化后应命中 src_cache，不应重复 ffprobe"),
        )
        cut_infos: list[tuple[float, float, float] | None] = []
        monkeypatch.setattr(
            render, "cut",
            lambda clip, dest, slow=1.0, frames=None, src_info=None: cut_infos.append(src_info),
        )
        plan = {
            "segments": [{"index": 1, "start": 0.0, "dur": 5.0}],
            "blocks": [{"kind": "fg", "start": 5.0, "dur": 4.0, "visual": "S01E01 0:00"}],
        }
        monkeypatch.setattr(render, "music_visual_slots", lambda p: [(5.0, 4.0, "S01E01 0:00")])
        cache = {"sub/clip.mp4": (20.0, 1.0 / 24.0, 0.0)}
        out = render._still_frames(tmp_path, plan, {}, tmp_path, workers=2, src_cache=cache)
        assert len(out) == 1
        assert cut_infos == [(20.0, 1.0 / 24.0, 0.0)]

    def test_音乐期视频前置失败立即触发cancel且不阻塞异常传播(self, tmp_path: Path, monkeypatch):
        import time as _time
        from pipeline import chapters as chapters_mod, music as music_mod

        ep = tmp_path / "ep"
        (ep / "03-audio").mkdir(parents=True)
        (ep / "01-topic.md").write_text("类型: 试听\n", encoding="utf-8")
        (ep / "02-script.md").write_text("# test\n", encoding="utf-8")
        wav = ep / "03-audio" / "01.wav"
        wav.write_bytes(b"stub")
        src = tmp_path / "src.mp4"
        src.write_bytes(b"stub")
        man = {"segments": [{"index": 1, "file": "01.wav", "duration": 2.0, "text": "一句", "sentences": None}]}
        plan = {"segments": [{"index": 1, "status": "ok", "duration": 2.0, "text": "一句",
                              "clips": [{"source": str(src), "start": 0.0, "dur": 2.0}]}]}
        (ep / "03-audio" / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
        (ep / "04-clips.approved.json").write_text(json.dumps(plan), encoding="utf-8")
        mplan = {"segments": [{"index": 1, "start": 0.0, "dur": 2.0}], "blocks": [], "total_duration": 2.0}

        monkeypatch.setattr(render, "verify_script_vo_hash", lambda *a, **kw: None)
        monkeypatch.setattr(render, "verify_alignment", lambda *a, **kw: [])
        monkeypatch.setattr(render, "verify_subtitles", lambda *a, **kw: None)
        monkeypatch.setattr(render, "_maybe_music_plan", lambda *a, **kw: mplan)
        monkeypatch.setattr(render, "music_visual_slots", lambda *a, **kw: [])
        monkeypatch.setattr(render, "check_tiling", lambda *a, **kw: None)
        monkeypatch.setattr(render, "_probe_source", lambda *a, **kw: (10.0, 1.0 / 24.0, 0.0))
        monkeypatch.setattr(render, "cut", lambda clip, dest, **kw: dest.write_bytes(b"c"))
        monkeypatch.setattr(music_mod, "parse_script_music", lambda *a, **kw: [])
        monkeypatch.setattr(music_mod, "bgm_source_issue", lambda *a, **kw: None)
        monkeypatch.setattr(music_mod, "music_gaps", lambda *a, **kw: [])
        monkeypatch.setattr(chapters_mod, "parse_script_chapters", lambda *a, **kw: None)
        monkeypatch.setattr(chapters_mod, "collect_music_hud_spans", lambda *a, **kw: ())

        cancel_seen = []

        def slow_audio(*a, cancel=None, **kw):
            cancel_seen.append(cancel)
            if cancel is not None:
                cancel.wait(timeout=5.0)
            return tmp_path / "voice.wav", {}

        monkeypatch.setattr(render, "_prepare_audio", slow_audio)
        monkeypatch.setattr(render, "_still_frames", lambda *a, **kw: (_ for _ in ()).throw(SystemExit("FAIL still boom")))

        t0 = _time.monotonic()
        with pytest.raises(SystemExit, match="still boom"):
            render.run(ep)
        elapsed = _time.monotonic() - t0
        assert elapsed < 1.0, f"视频失败后不应等待后台音频线程：{elapsed:.2f}s"
        assert cancel_seen and cancel_seen[0] is not None and cancel_seen[0].is_set()

    def test_非darwin平台vt探测直接返回False(self, monkeypatch):
        render._videotoolbox_available.cache_clear()
        monkeypatch.setattr(render.sys, "platform", "linux")
        monkeypatch.setattr(render.subprocess, "run",
                            lambda *a, **kw: pytest.fail("非 macOS 不应拉起 ffmpeg 探测 VT"))
        try:
            assert render._videotoolbox_available() is False
        finally:
            render._videotoolbox_available.cache_clear()

    def test_write_final带extra_args失败清partial保留旧成片(self, tmp_path, monkeypatch):
        (tmp_path / render.FINAL).write_bytes(b"old")
        seen = []

        def fake_run(cmd, **kw):
            seen.append(cmd)
            (tmp_path / render.FINAL_PART).write_bytes(b"half")
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(render.subprocess, "run", fake_run)
        with pytest.raises(subprocess.CalledProcessError):
            render._write_final(tmp_path, tmp_path, ["ffmpeg"], extra_args=["-map", "[hout]", "top.mp4"])
        assert seen and seen[0][-3:] == ["-map", "[hout]", "top.mp4"]
        assert (tmp_path / render.FINAL).read_bytes() == b"old"
        assert not (tmp_path / render.FINAL_PART).exists()

    @needs_ffmpeg
    def test_probe_source与probe_cut_result等价旧函数且拒收静态图(self, tmp_path: Path):
        mp4 = tmp_path / "v.mp4"
        _ff("-f", "lavfi", "-i", "testsrc2=size=64x64:rate=30000/1001:duration=1.5",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1.8",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(mp4))
        dur, ft, gap = render._probe_source(mp4)
        ref_dur = render.duration(mp4)
        assert dur == pytest.approx(ref_dur, abs=1e-6)
        assert ft == pytest.approx(render.frame_time(mp4), abs=1e-6)
        assert gap == pytest.approx(render._video_tail_gap(mp4, ref_dur), abs=1e-6)
        assert gap > 0.1

        n_pkt, cut_dur = render._probe_cut_result(mp4)
        assert n_pkt == render.frame_count(mp4)
        assert cut_dur == pytest.approx(ref_dur, abs=1e-6)

        img = tmp_path / "still.png"
        _ff("-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1", str(img))
        with pytest.raises(SystemExit, match="静态图片"):
            render._probe_source(img)
        with pytest.raises(SystemExit, match="静态图片"):
            render._probe_cut_result(img)

    @needs_ffmpeg
    def test_单遍合成同时落盘05_final与hud_clean_top_异源拼接零丢包(self, tmp_path: Path, monkeypatch):
        """端到端真实产物护栏（B1）：异源 concat（tv/yuv420p 与 pc/yuvj420p 切换）+ HUD 单遍 split=2
        必须把 loudnorm 放在独立 -af 链；若错塞进视频 -filter_complex，libavfilter 触发 reinit_filters
        会静默丢弃 ~3s/次音频包，本测试直接核查真实产物 AAC 包数与包时长连续性。"""
        from pipeline import chapters as chapters_mod, qc

        monkeypatch.setattr(render, "W", 160)
        monkeypatch.setattr(render, "H", 90)
        monkeypatch.setattr(render, "MARGIN_LR", 10)
        monkeypatch.setattr(render, "MARGIN_V", 10)
        monkeypatch.setattr(render, "FONT_SIZE", 12)
        monkeypatch.setattr(render, "ENCODER", "libx264")
        monkeypatch.setattr(render, "PRESET", "ultrafast")
        monkeypatch.setattr(
            chapters_mod, "collect_music_hud_spans",
            lambda mplan, bplan, total: (chapters_mod.MusicHudSpan(title="Test Track", start=0.0, end=total),),
        )

        # 构造两种不同 color_range/pix_fmt 的真实源片（tv/yuv420p 与 pc/yuvj420p），
        # 复现多源番剧/实拍/定格拼接时触发 libavfilter reinit_filters 的真实边界。
        src_tv = tmp_path / "src_tv.mp4"
        src_pc = tmp_path / "src_pc.mp4"
        _ff("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24000/1001:duration=6.0",
            "-vf", "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709,format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", str(src_tv))
        _ff("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24000/1001:duration=6.0",
            "-vf", "setparams=range=pc:color_primaries=smpte170m:color_trc=smpte170m:colorspace=smpte170m,format=yuvj420p",
            "-c:v", "libx264", "-preset", "ultrafast", str(src_pc))
        ep = tmp_path / "ep"
        (ep / "03-audio").mkdir(parents=True)
        (ep / "01-topic.md").write_text("类型: 杂谈\n", encoding="utf-8")
        man, segs = [], []
        # 3 段 × 4.5s = 13.5s（每段 > 3s 以便 loudnorm 缓冲写满并暴露 reinit 丢包）
        for i, (src, d) in enumerate(((src_tv, 4.5), (src_pc, 4.5), (src_tv, 4.5)), 1):
            wav = ep / "03-audio" / f"{i:02d}.wav"
            _ff("-f", "lavfi", "-i", f"sine=frequency={260 + 40 * i}:sample_rate=48000", "-t", str(d),
                "-ac", "1", str(wav))
            real_d = render.duration(wav)
            man.append({"index": i, "file": wav.name, "duration": real_d, "text": f"第{i}段。",
                        "sentences": None})
            segs.append({"index": i, "status": "ok", "duration": real_d, "text": f"第{i}段。",
                         "clips": [{"source": str(src), "start": 0.0, "dur": real_d}]})
        (ep / "03-audio" / "manifest.json").write_text(json.dumps({"segments": man}), encoding="utf-8")
        (ep / "04-clips.approved.json").write_text(json.dumps({"segments": segs}), encoding="utf-8")

        out = render.run(ep)
        clean_top = ep / "_agent" / "hud_clean_top.mp4"
        assert out.is_file() and clean_top.is_file()
        total = sum(m["duration"] for m in man)
        want_frames = render.span_frames(0.0, total)
        assert render.frame_count(clean_top) == want_frames
        dims = render._ffprobe(clean_top, "stream=width,height", "v:0")
        assert dims == f"160,{chapters_mod.hud_strip_height(90)}"

        # 真实产物音频包完整性断言：0 处 PTS 断洞，AAC 包覆盖时长与容器时长一致
        a_info = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate,nb_frames,duration", "-of", "json", str(out)],
            capture_output=True, text=True, check=True,
        ).stdout)["streams"][0]
        adur = float(a_info["duration"])
        pkt_dur = int(a_info["nb_frames"]) * 1024 / int(a_info["sample_rate"])
        assert adur - pkt_dur < 0.2, f"疑似 loudnorm 重建丢包：容器 {adur:.3f}s，包覆盖仅 {pkt_dur:.3f}s"
        pkt_chk = qc._audio_packet_check(out, adur)
        assert pkt_chk is not None and pkt_chk.ok is True
