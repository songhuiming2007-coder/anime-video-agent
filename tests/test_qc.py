"""成片质检的纯函数测试（`qc.py`）。

测两个不碰 ffmpeg 的函数：成片黑帧 → 源片段映射，和源片黑段覆盖判断。
ffmpeg 调用本身不测（要真实片源，按项目约定不进测试）。

背景：成片由源片切片拼接，源片自带黑场转场（情绪停顿、转场）会原样带进成片，
被 blackdetect 误报成缺陷（2026-08-09 实证两处）。所以黑帧判据改成：
成片黑帧必须能对照回源片段，源片同样黑 = 内容，通过。
"""

import pytest

from pipeline import qc


def _plan(clips_per_seg):
    """构造与 render.py 同构的 plan：每段 clips 按顺序拼接。"""
    t = 0.0
    segs = []
    for i, n in enumerate(clips_per_seg, 1):
        clips = []
        for j in range(n):
            clips.append({"source": f"src{i}-{j}.mkv", "start": 100.0 * j,
                          "dur": 10.0})
            t += 10.0
        segs.append({"index": i, "clips": clips})
    return {"total_duration": t, "segments": segs}


class TestMapBlackToSources:
    """成片时间轴 → 源片段引用。成片 = clips 按段内顺序、段按顺序拼接。"""

    def test_黑帧落在第二段内(self):
        plan = _plan([2, 2])          # 段1 0-20s，段2 20-40s
        out = qc._map_black_to_sources([(25.0, 26.0)], plan)
        assert len(out) == 1
        src, slo, shi, flo, fhi, idx, bi = out[0]
        assert src == "src2-0.mkv"    # 段2 第一个 clip
        assert (flo, fhi) == (25.0, 26.0)
        assert abs(slo - 5.0) < 1e-6  # 成片 25s = 源 clip start 0 + (25-20)
        assert abs(shi - 6.0) < 1e-6
        assert idx == 3               # 第 3 个片段（段1 两个 + 段2 第一个）
        assert bi == 0                # 第一条黑

    def test_黑帧跨片段边界_映射两条(self):
        plan = _plan([2])             # 0-20s，每 clip 10s
        out = qc._map_black_to_sources([(9.5, 10.5)], plan)
        assert len(out) == 2
        # 第一条：clip0 的尾巴（源 9.5-10.0），片段序号 1
        assert out[0][0] == "src1-0.mkv" and abs(out[0][1] - 9.5) < 1e-6
        assert abs(out[0][3] - 9.5) < 1e-6 and abs(out[0][4] - 10.0) < 1e-6
        assert out[0][5] == 1
        # 第二条：clip1 的开头（源 start=100，成片 10.0-10.5 → 源 100-100.5），序号 2
        assert out[1][0] == "src1-1.mkv" and abs(out[1][1] - 100.0) < 1e-6
        assert abs(out[1][2] - 100.5) < 1e-6
        assert out[1][5] == 2

    def test_成片黑帧短于片段时源区间不越界(self):
        plan = _plan([2])
        out = qc._map_black_to_sources([(0.0, 1.0)], plan)
        # 第一个 clip 起点就是成片 0，源映射不应为负
        assert abs(out[0][1] - 0.0) < 1e-6

    def test_黑帧与所有片段不相交_空(self):
        plan = _plan([2])             # 0-20s
        assert qc._map_black_to_sources([(99.0, 100.0)], plan) == []


class TestSourceBlackCovers:
    """源片黑段覆盖判断。重叠 ≥ BLACK_MATCH_MIN 才算覆盖。"""

    def test_源片黑段完全盖住_通过(self):
        # 映射区间 1255.1-1255.8，源片黑段 1255.13-1255.84
        assert qc._source_black_covers([(1255.13, 1255.84)], 1255.1, 1255.8)

    def test_源片黑段被seek截断_仍通过(self):
        # seek 落在关键帧、解码起点偏晚：黑段只露出 0.5s，也够
        assert qc._source_black_covers([(1255.35, 1256.0)], 1255.1, 1255.8)

    def test_源片没有黑_不通过(self):
        assert not qc._source_black_covers([], 1255.1, 1255.8)
        # 源片黑段在别处
        assert not qc._source_black_covers([(1300.0, 1301.0)], 1255.1, 1255.8)

    def test_重叠不足门槛_不通过(self):
        # 只露出来 0.2s，比 BLACK_MATCH_MIN 小——黑帧不是这块源片带的
        assert not qc._source_black_covers([(1255.60, 1256.5)], 1255.1, 1255.8)


class TestMappedCovered:
    """映射位置允许偏出 FRAME_BOUND（D145 起 render 按成片时间轴分帧，起点离排片 ≤ 半帧）。

    此前窗口按「第 n 片 × 0.05 s」随序号放宽，是在容忍累积漂移；漂移修掉后，
    放宽的窗口只会把邻近的源黑段错认成「源片自带」。
    """

    SPAN = ("src.mkv", 1255.6, 1256.3, 168.1, 168.8, 26)   # 第 26 片

    def test_偏出一帧以内_通过(self):
        # 源黑段到 1255.92：不放宽只重叠 0.32 s（< BLACK_MATCH_MIN），放宽 FRAME_BOUND 后 0.37 s
        assert not qc._source_black_covers([(1255.0, 1255.92)], 1255.6, 1256.3)
        assert qc._mapped_covered(self.SPAN, [(1255.0, 1255.92)])

    def test_偏出半秒_不再按片段序号罩住(self):
        # 映射起点偏后 0.47 s（旧口径下第 26 片窗口 1.3 s 能罩住，那正是 D145 的漂移）
        assert not qc._mapped_covered(self.SPAN, [(1255.13, 1255.84)])

    def test_窗口不随片段序号增长(self):
        far = ("src.mkv", 1255.6, 1256.3, 168.1, 168.8, 177)
        near = ("src.mkv", 1255.6, 1256.3, 168.1, 168.8, 1)
        # 放宽 FRAME_BOUND 后重叠 1255.55–1255.85 = 0.30 s < BLACK_MATCH_MIN；旧口径第 177 片窗口 8.85 s 会整段罩住
        blacks = [(1255.0, 1255.85)]
        assert qc._mapped_covered(far, blacks) == qc._mapped_covered(near, blacks) is False


class TestVoiceStarts:
    def test_普通期段落起点按配音时长累计(self):
        audio = [{"index": 1, "duration": 2.5}, {"index": 2, "duration": 1.25}, {"index": 4, "duration": 3.0}]
        assert qc._voice_starts(audio) == {1: 0.0, 2: 2.5, 4: 3.75}

    def test_没有manifest退回连续累计(self):
        assert qc._voice_starts(None) is None

    def test_试听型取音乐时间轴_普通期取配音(self):
        mplan = {"segments": [{"index": 1, "start": 30.0}, {"index": 2, "start": 41.5}]}
        audio = [{"index": 1, "duration": 2.5}, {"index": 2, "duration": 1.0}]
        assert qc._seg_starts(mplan, audio) == {1: 30.0, 2: 41.5}
        assert qc._seg_starts(None, audio) == {1: 0.0, 2: 2.5}


def _span(src, slo, shi, flo, fhi, idx, bi):
    return (src, slo, shi, flo, fhi, idx, bi)


class TestBlackDefects:
    """一条黑跨多片段：按「未覆盖部分 ≥ BLACK_MAX」整体判，不逐片段。

    黑帧跨切片边界时边界上一两帧暗场可能落进相邻片段，而源片只在主片段有
    转场黑——逐片段判会把整段源转场误报成缺陷（2026-08-10 实测踩坑）。
    """

    def test_全覆盖_无缺陷(self):
        m = [_span("a.mkv", 631.0, 632.5, 82.0, 83.5, 1, 0)]
        assert qc._black_defects(m, set(m)) == []

    def test_未覆盖只有边角料_小于门槛_放行(self):
        # 主片段源片淡出黑 1.45s + 邻片段首帧 0.13s 暗场（2026-08-10 楪祈实况）
        m1 = _span("a.mkv", 631.0, 632.5, 82.0, 83.5, 1, 0)    # 被源黑盖住
        m2 = _span("b.mkv", 314.0, 314.13, 83.5, 83.63, 2, 0)  # 没盖住，0.13s
        assert qc._black_defects([m1, m2], {m1}) == []

    def test_未覆盖超过门槛_判缺陷(self):
        m1 = _span("a.mkv", 631.0, 632.5, 82.0, 83.5, 1, 0)
        m2 = _span("b.mkv", 314.0, 314.6, 83.5, 84.1, 2, 0)    # 没盖住，0.6s
        assert qc._black_defects([m1, m2], {m1}) == [
            ("b.mkv", 83.5, 84.1, 314.0, 314.6)]

    def test_两条黑互不干扰(self):
        m1 = _span("a.mkv", 631.0, 632.5, 82.0, 83.5, 1, 0)     # bi=0 全覆盖
        m2 = _span("c.mkv", 500.0, 500.6, 300.0, 300.6, 2, 1)   # bi=1 未覆盖 0.6s
        assert qc._black_defects([m1, m2], {m1}) == [
            ("c.mkv", 300.0, 300.6, 500.0, 500.6)]


class TestNoDurationGate:
    """成片总时长不进质检门禁（2026-11-20 裁决）：随内容自然决定，不设默认带，
    也不解析或校验 `01-topic.md` 的 `时长目标` 字段。"""

    def _fake_ok(self, monkeypatch, dur_s: float):
        import subprocess as sp

        def run(cmd, **kw):
            if cmd[0] == "ffprobe":
                return sp.CompletedProcess(cmd, 0, stdout=f"{dur_s}\n", stderr="")
            if any("loudnorm" in a for a in cmd):
                return sp.CompletedProcess(
                    cmd, 0, stdout="", stderr='{"input_i": "-16.0", "input_tp": "-1.5"}'
                )
            return sp.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(qc.subprocess, "run", run)

    @pytest.mark.parametrize("dur_s", [15.0, 180.0, 3600.0])
    def test_任意总时长均不出现时长门禁且不失败(self, monkeypatch, tmp_path, dur_s):
        (tmp_path / "01-topic.md").write_text("时长目标: 随内容不限\n", encoding="utf-8")
        video = tmp_path / "05-final.mp4"
        video.write_text("stub", encoding="utf-8")
        self._fake_ok(monkeypatch, dur_s)
        checks = qc.check(video)
        assert not any("成片时长" in c.name for c in checks)
        align = next(c for c in checks if c.name == "音画时长对齐")
        assert align.ok is True
        assert f"{dur_s / 60:.2f} 分钟" in align.detail


class TestDetectorFailure:
    """检测器自己失败时，黑帧/静音两项不许判 PASS（2026-08-16 审计 2-4）。

    blackdetect/silencedetect 的结果行都在 stderr 上；ffmpeg 非零退出时
    stderr 里没有结果行，旧实现把「检测器跑不动」当成「没检出」——文件坏到
    门禁最该报警的时候反而全绿。ffmpeg 本身按约定不进测试：这里在
    subprocess 边界注入「ffprobe 正常、ffmpeg 失败」的观测值，测的是门禁
    对退出码的裁决（边界注入的先例见 test_tts 的回读豁免）。
    """

    def _fake(self, monkeypatch):
        import subprocess as sp

        def run(cmd, **kw):
            if cmd[0] == "ffmpeg":
                return sp.CompletedProcess(cmd, 1, stdout="",
                                           stderr="  [Parsed_blackdetect] 检测失败细节")
            return sp.CompletedProcess(cmd, 0, stdout="60.0\n", stderr="")

        monkeypatch.setattr(qc.subprocess, "run", run)

    def test_ffmpeg失败_黑帧静音判FAIL不假绿(self, monkeypatch, tmp_path):
        video = tmp_path / "05-final.mp4"
        video.write_text("not a real video", encoding="utf-8")
        self._fake(monkeypatch)
        checks = qc.check(video)             # plan=None、audio=None → 字幕四项走 SKIP 分支
        black = next(c for c in checks if "纯黑" in c.name)
        silence = next(c for c in checks if "静音" in c.name)
        loud = next(c for c in checks if "响度" in c.name)
        # 旧实现在这里三样里黑帧/静音是 ok=True（stderr 空列表 → 无缺陷）
        assert black.ok is False and "退出码 1" in black.detail
        assert silence.ok is False and "退出码 1" in silence.detail
        assert loud.ok is False              # 响度测量也失败，不许漏报
        assert black.skipped is False and silence.skipped is False


class TestAssemblyBlackSpans:
    """总装期的黑帧归因：来源是**分期成片**，不是素材源片。

    流拷贝这一刀切不出新黑场，切出来的黑只能来自某一期成片。判据因此
    改问「部件同位置是不是也黑」，而不是「素材源片同位置是不是也黑」。
    """

    @staticmethod
    def _asm():
        return {"total_duration": 30.0, "parts": [
            {"name": "card_part1", "path": "/x/card_part1.mp4", "kind": "card",
             "offset": 0.0, "duration": 3.0},
            {"name": "part1_trimmed", "path": "/x/part1_trimmed.mp4", "kind": "part",
             "offset": 3.0, "duration": 27.0},
        ]}

    def _fake(self, monkeypatch, black: bool):
        """模拟部件上的 blackdetect：覆盖/不覆盖整个请求窗口。"""
        calls = []

        def fake(cmd):
            calls.append(cmd)
            if not black:
                return "", 0
            t = float(cmd[cmd.index("-t") + 1])
            return f"black_start:0.0 black_end:{t}", 0

        monkeypatch.setattr(qc, "_run", fake)
        return calls

    def test_部件同位置也黑_继承不判缺陷(self, monkeypatch):
        calls = self._fake(monkeypatch, black=True)
        defects, inherited, errs = qc._assembly_black_spans([(9.0, 10.0)], self._asm())
        assert (defects, errs) == ([], [])
        assert inherited == [("/x/part1_trimmed.mp4", 9.0, 10.0)]
        # 只在**落进黑帧的那个部件**上开检测器，卡片窗口不跑
        assert calls[0][calls[0].index("-i") + 1] == "/x/part1_trimmed.mp4"
        assert len(calls) == 1

    def test_部件同位置不黑_判缺陷(self, monkeypatch):
        self._fake(monkeypatch, black=False)
        defects, inherited, errs = qc._assembly_black_spans([(9.0, 10.0)], self._asm())
        assert inherited == [] and errs == []
        assert defects == [("/x/part1_trimmed.mp4", 9.0, 10.0, 6.0, 7.0)]

    def test_部件查不动_既记对照失败也不判过(self, monkeypatch):
        # 检测器跑不动不能当成「没检出」判过（2026-08-16 审计 2-4）。
        # 与原来「源片对照」分支同款语义：窗口没被证明覆盖 → 照样算缺陷，
        # 同时把「检测器跑不动」单独记一笔——两笔账都留着。
        monkeypatch.setattr(qc, "_run", lambda cmd: ("boom", 1))
        defects, inherited, errs = qc._assembly_black_spans([(9.0, 10.0)], self._asm())
        assert inherited == []
        assert defects == [("/x/part1_trimmed.mp4", 9.0, 10.0, 6.0, 7.0)]
        assert len(errs) == 1 and "part1_trimmed.mp4" in errs[0]

    def test_黑帧跨部件边界_两边分别归因(self, monkeypatch):
        self._fake(monkeypatch, black=True)
        defects, inherited, errs = qc._assembly_black_spans([(2.0, 4.0)], self._asm())
        assert (defects, errs) == ([], [])
        assert [i[0] for i in inherited] == ["/x/card_part1.mp4", "/x/part1_trimmed.mp4"]


class TestSilenceExempt:
    """静音豁免只有两种：歌曲自然收尾，与**被声明的**章节卡静音桥接。"""

    def test_尾部对齐音乐事件终点_豁免(self):
        assert qc._silence_exempt(50.0, 51.2, song_ends=[51.5], card_wins=[])

    def test_落在章节卡窗口内_豁免(self):
        # 一期末尾 1s 淡出 + 3s 卡：静音区间起点在卡之前，只要与卡窗口相交就算桥接
        assert qc._silence_exempt(888.9, 892.5, song_ends=[], card_wins=[(889.5, 892.5)])

    def test_无声明窗口的静音_不豁免(self):
        assert not qc._silence_exempt(100.0, 103.0, song_ends=[], card_wins=[(889.5, 892.5)])


class TestAssemblyLedger:
    """总装期账本：有 04-assembly.json 才走总装口径，没有就是普通期。"""

    def test_有账本_读出来(self, tmp_path):
        (tmp_path / "04-assembly.json").write_text(
            '{"total_duration": 2915.6, "parts": []}', encoding="utf-8")
        assert qc.assembly_ledger(tmp_path)["total_duration"] == 2915.6

    def test_无账本_返回None(self, tmp_path):
        assert qc.assembly_ledger(tmp_path) is None


class TestQcPerfMerging:
    """2026-11 质检提速护栏：音频单遍 `-vn` + `asplit=2`，源片黑帧按间隙聚类避免白解整段。"""

    def test_音频扫描带vn且仅跑两次主ffmpeg(self, monkeypatch, tmp_path):
        import subprocess as sp

        video = tmp_path / "05-final.mp4"
        video.write_text("stub", encoding="utf-8")
        ff_cmds: list[list[str]] = []

        def fake_run(cmd, **kw):
            if cmd[0] == "ffprobe":
                return sp.CompletedProcess(cmd, 0, stdout="120.0\n", stderr="")
            ff_cmds.append(cmd)
            if any("loudnorm" in a for a in cmd):
                return sp.CompletedProcess(
                    cmd, 0, stdout="",
                    stderr='{"input_i": "-16.0", "input_tp": "-1.5"}\n',
                )
            return sp.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(qc.subprocess, "run", fake_run)
        checks = qc.check(video)
        assert len(ff_cmds) == 2
        aud_cmd = next(c for c in ff_cmds if any("loudnorm" in a for a in c))
        vid_cmd = next(c for c in ff_cmds if any("blackdetect" in a for a in c))
        assert "-vn" in aud_cmd
        fc = aud_cmd[aud_cmd.index("-filter_complex") + 1]
        assert "asplit=2" in fc and "loudnorm" in fc and "silencedetect" in fc
        assert "-an" in vid_cmd
        assert next(c for c in checks if "响度" in c.name).ok
        assert next(c for c in checks if "静音" in c.name).ok

    def test_黑帧窗口聚类_近邻合并_远距拆开_下界夹零(self):
        # 间距 4s (≤ BLACK_CLUSTER_GAP) 合并；间距 317s 拆成两段；起点近 0 夹到 0.0
        spans = [
            _span("a.mkv", 0.1, 0.8, 0.1, 0.8, 1, 0),
            _span("a.mkv", 314.0, 315.0, 10.0, 11.0, 2, 1),
            _span("a.mkv", 318.0, 319.0, 14.0, 15.0, 3, 2),
            _span("a.mkv", 631.0, 632.0, 50.0, 51.0, 4, 3),
        ]
        wins = qc._cluster_black_windows(spans)
        assert len(wins) == 3
        assert wins[0][0] == 0.0
        assert wins[1][0] < 314.0 and wins[1][1] > 319.0
        assert wins[2][0] > 629.0 and wins[2][1] > 632.0

    def test_probe_source_blacks部分失败时保留成功覆盖并记录错误(self, monkeypatch):
        def fake_run(cmd):
            src = cmd[cmd.index("-i") + 1]
            if src == "bad.mkv":
                return "decode error", 1
            return "black_start:0.0 black_end:3.0", 0

        monkeypatch.setattr(qc, "_run", fake_run)
        s_ok = _span("ok.mkv", 10.0, 11.0, 1.0, 2.0, 1, 0)
        s_bad = _span("bad.mkv", 20.0, 21.0, 5.0, 6.0, 2, 1)
        covered, errs = qc._probe_source_blacks({"ok.mkv": [s_ok], "bad.mkv": [s_bad]})
        assert covered == {s_ok}
        assert len(errs) == 1 and "bad.mkv" in errs[0]

    def test_真实视频_asplit单遍响度等价且音频包连续门禁抓出PTS断洞(self, tmp_path):
        import json as _json
        import re as _re
        import shutil
        import subprocess as sp

        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            pytest.skip("需要 ffmpeg/ffprobe")

        # 1. 正常短片：asplit=2 单遍 loudnorm 数值与独立 loudnorm 一致，且 _audio_packet_check 判 PASS
        good = tmp_path / "good.mp4"
        sp.run([
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=24:d=4",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", str(good),
        ], check=True, capture_output=True)

        solo_err, solo_rc = qc._run([
            "ffmpeg", "-nostdin", "-i", str(good), "-vn",
            "-af", "loudnorm=print_format=json", "-f", "null", "-",
        ])
        split_err, split_rc = qc._run([
            "ffmpeg", "-nostdin", "-i", str(good), "-vn",
            "-filter_complex",
            f"[0:a]asplit=2[a1][a2];[a1]loudnorm=print_format=json[o1];"
            f"[a2]silencedetect=n=-45dB:d={qc.SILENCE_MAX}[o2]",
            "-map", "[o1]", "-map", "[o2]", "-f", "null", "-",
        ])
        assert solo_rc == 0 and split_rc == 0
        d_solo = _json.loads(_re.search(r"\{[^{}]*input_i[^{}]*\}", solo_err, _re.S).group(0))
        d_split = _json.loads(_re.search(r"\{[^{}]*input_i[^{}]*\}", split_err, _re.S).group(0))
        assert float(d_split["input_i"]) == pytest.approx(float(d_solo["input_i"]), abs=0.05)
        assert float(d_split["input_tp"]) == pytest.approx(float(d_solo["input_tp"]), abs=0.05)

        good_chk = qc._audio_packet_check(good, qc._duration(good, "a:0"))
        assert good_chk is not None and good_chk.ok is True

        # 2. 复现 B1 断洞成片：异源 concat + loudnorm 误入视频 -filter_complex 静默丢包，门禁必须判 FAIL
        s1, s2, s3 = tmp_path / "s1.mp4", tmp_path / "s2.mp4", tmp_path / "s3.mp4"
        sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=64x64:r=24:d=4",
                "-vf", "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709,format=yuv420p",
                "-c:v", "libx264", "-preset", "ultrafast", str(s1)], check=True, capture_output=True)
        sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=64x64:r=24:d=4",
                "-vf", "setparams=range=pc:color_primaries=smpte170m:color_trc=smpte170m:colorspace=smpte170m,format=yuvj420p",
                "-c:v", "libx264", "-preset", "ultrafast", str(s2)], check=True, capture_output=True)
        sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=64x64:r=24:d=4",
                "-vf", "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709,format=yuv420p",
                "-c:v", "libx264", "-preset", "ultrafast", str(s3)], check=True, capture_output=True)
        (tmp_path / "cat.txt").write_text("file s1.mp4\nfile s2.mp4\nfile s3.mp4\n", encoding="utf-8")
        cat_vid = tmp_path / "cat.mp4"
        sp.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "cat.txt",
                "-c", "copy", str(cat_vid)], check=True, capture_output=True, cwd=tmp_path)
        wav = tmp_path / "v.wav"
        sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:r=48000:d=12",
                "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True, capture_output=True)
        m_err, _ = qc._run(["ffmpeg", "-nostdin", "-i", str(wav), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
        ld = _json.loads(_re.search(r"\{[^{}]*input_i[^{}]*\}", m_err, _re.S).group(0))
        ln = (f"loudnorm=I=-16:TP=-1.5:LRA=11:linear=true:measured_I={ld['input_i']}"
              f":measured_TP={ld['input_tp']}:measured_LRA={ld['input_lra']}"
              f":measured_thresh={ld['input_thresh']}:offset={ld['target_offset']}")
        broken = tmp_path / "broken.mp4"
        sp.run([
            "ffmpeg", "-y", "-v", "error", "-i", str(cat_vid), "-i", str(wav),
            "-filter_complex", f"[0:v:0]split=2[vm][vh];[vm]null[vo];[vh]crop=64:16:0:0[ho];[1:a:0]{ln}[ao]",
            "-map", "[vo]", "-map", "[ao]", "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-shortest", str(broken),
            "-map", "[ho]", "-an", "-c:v", "libx264", "-preset", "ultrafast", str(tmp_path / "hud.mp4"),
        ], check=True, capture_output=True)
        bad_chk = qc._audio_packet_check(broken, qc._duration(broken, "a:0"))
        assert bad_chk is not None and bad_chk.ok is False and "缺失" in bad_chk.detail


class TestSilenceDetectionAndProfileWhitelist:
    """ffmpeg 8 silencedetect 换行前缀解析 + avsync 块尾豁免 + AAC-LC profile 白名单。"""

    def test_parse_silences兼容ffmpeg8前缀交错日志与片尾未闭合静音(self):
        stderr = (
            "[silencedetect @ 0x7bcac58420] silence_start: 10.5\n"
            "[Parsed_loudnorm_1 @ 0x123] {\"input_i\": \"-16.0\"}\n"
            "[silencedetect @ 0x7bcac58420] silence_end: 13.8 | silence_duration: 3.3\n"
            "[silencedetect @ 0x7bcac58420] silence_start: 55.0\n"
        )
        assert qc._parse_silences(stderr, end_dur=60.0) == [(10.5, 13.8), (55.0, 60.0)]
        assert qc._parse_silences(stderr) == [(10.5, 13.8)]

    def test_真实视频_ffmpeg8静音门禁能抓出中段静音且不误杀正常音频(self, tmp_path):
        import shutil
        import subprocess as sp

        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            pytest.skip("需要 ffmpeg/ffprobe")

        # 构造 4.2s 真实 MP4：0.8s 音调 + 2.6s 真静音（> SILENCE_MAX=2.0s）+ 0.8s 音调
        vid = tmp_path / "with_silence.mp4"
        sp.run([
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=24:d=4.2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.8",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=2.6",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=0.8",
            "-filter_complex", "[1:a][2:a][3:a]concat=n=3:v=0:a=1[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", str(vid),
        ], check=True, capture_output=True)

        checks = qc.check(vid)
        sil = next(c for c in checks if "静音" in c.name)
        assert sil.ok is False, f"真实 >2s 静音必须被门禁检出，实际 detail={sil.detail!r}"
        assert "0.8s" in sil.detail

    def test_avsync音乐块尾端淡出静音被豁免(self, tmp_path, monkeypatch):
        import subprocess as sp

        vid = tmp_path / "05-final.mp4"
        vid.write_bytes(b"stub")
        monkeypatch.setattr(
            qc, "_music_plan",
            lambda _ep: {
                "total_duration": 100.0,
                "tracks": [],
                "blocks": [{"label": "M1", "start": 10.0, "dur": 40.0, "avsync": "S01E01"}],
                "segments": [],
            },
        )

        def fake_run(cmd, **kw):
            if cmd[0] == "ffprobe":
                return sp.CompletedProcess(cmd, 0, stdout="100.0\n", stderr="")
            if any("silencedetect" in a for a in cmd):
                err = (
                    '{"input_i": "-16.0", "input_tp": "-1.5"}\n'
                    "[silencedetect @ 0xabc] silence_start: 47.0\n"
                    "[silencedetect @ 0xabc] silence_end: 50.2 | silence_duration: 3.2\n"
                )
                return sp.CompletedProcess(cmd, 0, stdout="", stderr=err)
            return sp.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(qc.subprocess, "run", fake_run)
        checks = qc.check(vid)
        sil = next(c for c in checks if "静音" in c.name)
        assert sil.ok is True

    @pytest.mark.parametrize("profile,want_checked", [
        ("LC", True),
        ("", True),
        ("HE-AAC", False),
        ("LD", False),
        ("ELD", False),
    ])
    def test_audio_packet_check仅校验AAC_LC白名单(self, tmp_path, monkeypatch, profile, want_checked):
        import json as _json
        import subprocess as sp

        payload = _json.dumps({
            "streams": [{
                "codec_name": "aac",
                "profile": profile,
                "sample_rate": "48000",
                "nb_frames": "100",
            }]
        })
        monkeypatch.setattr(
            qc.subprocess, "run",
            lambda cmd, **kw: sp.CompletedProcess(cmd, 0, stdout=payload, stderr=""),
        )
        got = qc._audio_packet_check(tmp_path / "x.mp4", 10.0)
        if want_checked:
            assert got is not None and got.ok is False
        else:
            assert got is None
