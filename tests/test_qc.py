"""成片质检的纯函数测试（`qc.py`）。

测两个不碰 ffmpeg 的函数：成片黑帧 → 源片段映射，和源片黑段覆盖判断。
ffmpeg 调用本身不测（要真实片源，按项目约定不进测试）。

背景：成片由源片切片拼接，源片自带黑场转场（情绪停顿、转场）会原样带进成片，
被 blackdetect 误报成缺陷（2026-08-09 实证两处）。所以黑帧判据改成：
成片黑帧必须能对照回源片段，源片同样黑 = 内容，通过。
"""

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
    """含累积漂移窗口的覆盖判断。

    成片时间轴相对排片轴有累积帧舍入误差（随机游走），黑帧映射回源的
    位置允许偏出 idx×FRAME_BOUND——推导上界，见 qc.py 里的注释。
    """

    SPAN = ("src.mkv", 1255.6, 1256.3, 168.1, 168.8, 26)   # 第 26 片，窗口 26×0.05

    def test_源黑段与映射区间有偏移_窗口内_通过(self):
        # 模拟累积漂移：源黑段在 1255.13-1255.84，映射区间起点偏后 0.47s
        # （2026-08-09 实测：42 片成片中间位置漂移 0.5s）
        assert qc._mapped_covered(self.SPAN, [(1255.13, 1255.84)])

    def test_漂移超出窗口_不通过(self):
        # 黑段在 4s 外——窗口（26×0.05=1.3s）罩不住，说明这黑不是该片段带的
        assert not qc._mapped_covered(self.SPAN, [(1251.0, 1252.0)])

    def test_窗口随片段序号增长(self):
        # 第 2 片的窗口只有 0.1s，0.47s 的漂移罩不住；第 26 片（上面）能罩住
        span2 = ("src.mkv", 1255.6, 1256.3, 168.1, 168.8, 2)
        assert not qc._mapped_covered(span2, [(1255.13, 1255.84)])


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


class TestEpisodeDurationBand:
    """成片总时长带的按期覆盖（N15）：tts.py 现在也读这个函数判 WARN 阈值，
    不再自己抄一份 120/240 硬编码——两个调用点（qc 渲染后质检、tts 配音后
    WARN）必须走同一份「没写就用默认带，写了就按该期覆盖」的逻辑。
    """

    def test_没有_01_topic_md_返回None(self, tmp_path):
        assert qc.episode_duration_band(tmp_path) is None

    def test_没写时长目标字段也返回None(self, tmp_path):
        (tmp_path / "01-topic.md").write_text("# 标题\n类型: 人物志\n", encoding="utf-8")
        assert qc.episode_duration_band(tmp_path) is None

    def test_写了时长目标按分钟换算成秒(self, tmp_path):
        (tmp_path / "01-topic.md").write_text("时长目标: 7-8分钟\n", encoding="utf-8")
        assert qc.episode_duration_band(tmp_path) == (420.0, 480.0)
