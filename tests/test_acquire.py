"""`acquire.py` 的纯函数层。

**观测值全部真跑出来过**（测试标准 2026-08-16 的观测值注入例外）：下面每个尺寸/时长/音轨
组合都能追到 T7 上一份真文件，不是编造「模型会怎么答」。跑这些测试不碰片源与外置盘，
只喂数字给纯函数——所以 clone 下来就能跑。

真观测值出处（2026-09-11 实测）：
- `SP01.mp4` 1920×1080 / 无音轨 / 359.4s / 184M 解复用通过
- `SP18.mp4` 1280×720 / aac 2ch / 7335.4s / 1707M 解复用通过
- `BK/Booklet 01.png` 5720×2837（短边 2837）
- `BK/Back C.png` 1582×2860（短边 1582，**差 18px 不过**）
- `01-借躯降生/08-cover.jpg` 2041×1357（短边 1357）
"""

from __future__ import annotations

import pytest

from pipeline import acquire as A

# ---------- 判据裁决 ----------


class TestCheckVideo:
    """视频四判据。每条的期望值都先在实现上跑过真文件再写进来。"""

    @staticmethod
    def _v(**kw):
        base = dict(kind="live", intact_ok=True, intact_detail="1707M 全片解复用通过",
                    width=1280, height=720, has_audio=True, audio_detail="有（aac 2ch）",
                    duration=7335.4, expected_dur=7200.0)
        return A.check_video(**{**base, **kw})

    def test_SP18_720p_带音轨_过(self):
        # SP18 是真文件：1280×720 正好压在地板上、aac 2ch、声明 7200s 实际 7335.4s（+1.9%）
        vs = self._v()
        assert A.overall(vs) == "PASS"
        res = next(v for v in vs if v.name == "分辨率")
        assert "needs_enhance" in res.note  # 720p 过地板，但低于 1080p 要标超分
        assert self._v(duration=7335.4, expected_dur=None)[3].level == "PASS"

    def test_池素材无音轨只报WARN不拒(self):
        # SP01（在库素材）实测无音轨。若按「必须有音轨」硬判，本池 17/18 全被拦（S3：
        # 拦了不该拦的）——池素材切片时统一 -an 丢音轨（01-assets 铁律一），不构成拒收理由。
        vs = self._v(kind="mv", width=1920, height=1080, has_audio=False,
                     audio_detail="无音轨", duration=359.4, expected_dur=None)
        aud = next(v for v in vs if v.name == "音轨")
        assert aud.level == "WARN"
        assert A.overall(vs) == "WARN"  # 不拒

    def test_访谈无音轨是硬拒(self):
        # 访谈的价值全在说话：没音轨就是死素材
        vs = self._v(kind="interview", has_audio=False, audio_detail="无音轨")
        assert next(v for v in vs if v.name == "音轨").level == "FAIL"
        assert A.overall(vs) == "FAIL"

    def test_低于720p地板是FAIL(self):
        vs = self._v(width=640, height=480)
        assert next(v for v in vs if v.name == "分辨率").level == "FAIL"
        assert A.overall(vs) == "FAIL"

    def test_时长偏差超阈值是WARN不是拒(self):
        # 真实场景：声明官方全场 7200s，下成 450.87s 的剪辑版（SP04 实测时长）→ -93.7%
        vs = self._v(duration=450.87, expected_dur=7200.0)
        d = next(v for v in vs if v.name == "时长偏差")
        assert d.level == "WARN"
        assert "剪辑版" in d.note
        assert A.overall(vs) == "WARN"

    def test_时长容差卡在5个点上(self):
        """两头夹住容差：+4.79% 过、+5.09% 不过。

        （用 SP18 实测 7335.4s 反推声明值：7000 与 6980。）
        **只测「差很多要 WARN」是捉不住 bug 的**——变异检验实测：把容差从 5% 放大到 50%，
        那一版测试照样全绿。容差本身也得有上下夹。
        """
        def lv(exp):
            return next(v for v in self._v(duration=7335.4, expected_dur=exp)
                        if v.name == "时长偏差").level
        assert lv(7000.0) == "PASS"   # +4.79%
        assert lv(6980.0) == "WARN"   # +5.09%

    def test_没声明预期时长就不判(self):
        # S4：拿不到能证伪的信息不定罪，「我不知道」不是「不合格」
        d = self._v(expected_dur=None)[3]
        assert d.level == "PASS" and "跳过不判" in d.note

    def test_完整性不过直接FAIL(self):
        vs = self._v(intact_ok=False, intact_detail="只处理到 900/7335s：moov atom not found")
        assert next(v for v in vs if v.name == "完整性").level == "FAIL"
        assert A.overall(vs) == "FAIL"

    def test_读不出时长时跳过时长判据(self):
        # 容器烂到 ffprobe 读不出 format duration：别抛异常，按 S4 跳过
        d = self._v(duration=None, expected_dur=7200.0)[3]
        assert d.level == "PASS" and "拿不到能证伪" in d.note


class TestCheckImage:
    """图片两判据。scan 是图片不是视频，别拿音轨判据去量它。"""

    def test_真扫图短边2837过(self):
        # BK/Booklet 01.png 实测 5720×2837
        vs = A.check_image(decoded=True, decode_detail="解出 PNG RGB",
                           width=5720, height=2837)
        assert A.overall(vs) == "PASS"

    def test_短边1582差18px不过(self):
        # BK/Back C.png 实测 1582×2860：差 18px 也是不过，阈值不因为「差得少」松口
        vs = A.check_image(decoded=True, decode_detail="解出 PNG RGB",
                           width=1582, height=2860)
        assert next(v for v in vs if v.name == "短边像素").level == "FAIL"
        assert A.overall(vs) == "FAIL"

    def test_封面2041x1357按扫图判也不过(self):
        # 成片封面 2041×1357（短边 1357）当扫图用会糊——两者判据同一条线
        assert A.overall(A.check_image(decoded=True, decode_detail="解出 JPEG RGB",
                                       width=2041, height=1357)) == "FAIL"

    def test_解不开就FAIL(self):
        vs = A.check_image(decoded=False, decode_detail="OSError: truncated",
                           width=0, height=0)
        assert A.overall(vs) == "FAIL"


class TestOverall:
    def test_只有WARN时整批不能报PASS(self):
        # 自己跑出来的 bug：首版 worst 只在 FAIL 时更新，一批全 WARN 会打出「整批结论：PASS」，
        # 于是没人去看那一行 WARN。
        vs = [A.Verdict("音轨", "无音轨", "可选", "WARN")]
        assert A.overall(vs) == "WARN"

    def test_全过才是PASS(self):
        assert A.overall([A.Verdict("a", "1", "2", "PASS")]) == "PASS"

    def test_FAIL压过WARN(self):
        vs = [A.Verdict("a", "1", "2", "WARN"), A.Verdict("b", "1", "2", "FAIL")]
        assert A.overall(vs) == "FAIL"


# ---------- candidates.json ----------


GOOD = """[
 {"title": "EGOIST LIVE 2023 横滨终场 全场", "url": "https://example.com/full.mp4",
  "type": "live", "source": "yt-dlp/B站", "expected_dur": 7200, "why": "终场全场"},
 {"title": "chelly reche 访谈", "url": "https://example.com/interview",
  "type": "interview", "source": "YouTube", "expected_dur": null, "why": "第一次用真名说话"}
]"""


class TestLoadCandidates:
    def test_合法清单原样返回(self):
        assert len(A.load_candidates(GOOD)) == 2

    def test_坏条目点名行号且一次报全(self):
        # 三处错一起报、每处带行号：这份文件是 agent 写的，只说「第 2 条错了」等于让人自己数
        raw = """[
 {"title": "缺 why", "url": "https://example.com/a.mp4", "type": "mv", "source": "x"},
 {"title": "坏类型", "url": "https://example.com/b.mp4", "type": "podcast", "source": "x", "why": "y"},
 {"title": "坏 url", "url": "magnet:?xt=urn:btih:dead", "type": "live", "source": "x", "why": "y"}
]"""
        with pytest.raises(SystemExit) as e:
            A.load_candidates(raw)
        msg = str(e.value)
        assert "第 2 行" in msg and "缺字段 why" in msg
        assert "第 3 行" in msg and "podcast" in msg
        assert "第 4 行" in msg and "magnet" in msg

    def test_顶层不是数组报错(self):
        with pytest.raises(SystemExit, match="必须是数组"):
            A.load_candidates('{"title": "单个对象"}')

    def test_不是JSON时报出解析行号(self):
        with pytest.raises(SystemExit, match="不是合法 JSON"):
            A.load_candidates("[\n {\"title\": 没引号}\n]")


# ---------- 查重 ----------


class TestDup:
    def test_URL抓过就拒(self):
        vs = A.dup_verdicts(url="https://example.com/full.mp4", slug="full",
                            seen_urls={"https://example.com/full.mp4"}, existing_names=set())
        assert A.overall(vs) == "FAIL"
        assert "别下第二遍" in vs[0].note

    def test_同名文件在就拒(self):
        vs = A.dup_verdicts(url="https://example.com/x", slug="EGOIST_LIVE",
                            seen_urls=set(), existing_names={"EGOIST_LIVE"})
        assert A.overall(vs) == "FAIL"

    def test_都没撞就放行(self):
        vs = A.dup_verdicts(url="https://example.com/x", slug="新素材",
                            seen_urls=set(), existing_names={"旧的"})
        assert A.overall(vs) == "PASS"


# ---------- 抓取命令 ----------


class TestFetcher:
    def test_直链后缀走curl(self):
        assert A.pick_fetcher("https://example.com/a.mp4") == "curl"
        assert A.pick_fetcher("https://cdn.example.com/scan.jpg") == "curl"
        assert A.pick_fetcher("https://example.com/场刊.pdf") == "curl"

    def test_页面走yt_dlp(self):
        # 视频页 URL 不带媒体后缀，交给 yt-dlp 解析页面
        assert A.pick_fetcher("https://www.youtube.com/watch?v=xxxx") == "yt-dlp"
        assert A.pick_fetcher("https://www.bilibili.com/video/BV1xx") == "yt-dlp"

    def test_curl必须带断点续传(self):
        # 吃过亏：HF 权重断在中途，没有 -C - 就得从头再来（2026-09-11）
        from pathlib import Path
        cmd = A.fetch_argv("https://example.com/a.mp4", Path("/tmp/in"), "a")
        assert cmd[0] == "curl" and "-C" in cmd and cmd[cmd.index("-C") + 1] == "-"
        assert cmd[-1] == "https://example.com/a.mp4"
        assert "/tmp/in/a.mp4" in cmd

    def test_yt_dlp命令形态(self):
        from pathlib import Path
        cmd = A.fetch_argv("https://www.youtube.com/watch?v=x", Path("/tmp/in"), "LIVE",
                           yt_dlp=["/opt/yt-dlp"])
        assert cmd[0] == "/opt/yt-dlp"
        assert "--continue" in cmd and "--no-playlist" in cmd
        assert cmd[cmd.index("-o") + 1] == "/tmp/in/LIVE.%(ext)s"

    def test_标题折成安全文件名(self):
        assert A.slugify("EGOIST LIVE 2023 横滨终场") == "EGOIST_LIVE_2023_横滨终场"
        assert A.slugify("///") == "asset"

    def test_认不出类型不猜(self):
        assert A.kind_of("x.zip") == "unknown"
        assert A.kind_of("场刊.jpg") == "scan"


# ---------- 集键 ----------


class TestRegisterKey:
    def test_正常集键(self):
        assert A.register_key("SP19") == 19
        assert A.register_key(" SP01 ") == 1

    @pytest.mark.parametrize("bad", ["SP9", "S01E01", "19", "sp19", "SP190"])
    def test_坏集键报错(self, bad):
        # 集键写死两位 SP（ADR-0010 决策二）：让手输自由发挥，迟早出现 SP9 与 SP09 两份
        with pytest.raises(SystemExit, match="SP19"):
            A.register_key(bad)
