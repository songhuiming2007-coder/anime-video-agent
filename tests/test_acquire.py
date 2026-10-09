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



class TestCmdFetchWithoutYtDlp:
    """N35：本机 yt-dlp 既不在 PATH 也不在虚拟环境时，直链照样走 curl，页面仍报原错。

    2026-09-27 M9 门禁 12 实测：`.mp4` 直链的 `acquire fetch 7` 以「FAIL 找不到 yt-dlp」退 1——
    `yt_dlp_argv()` 在选工具之前就被求值。
    """

    DIRECT = "https://example.com/probe.mp4"
    PAGE = "https://www.youtube.com/watch?v=x"

    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        import importlib.util
        import json
        import shutil

        from pipeline import paths

        data = tmp_path / "data"
        (data / "episodes").mkdir(parents=True)
        inc = data / "library" / "incoming"
        inc.mkdir(parents=True)
        cands = [{"title": "直链探针", "url": self.DIRECT, "type": "live", "source": "测试", "why": "N35"},
                 {"title": "页面候选", "url": self.PAGE, "type": "live", "source": "测试", "why": "N35"}]
        (inc / "candidates.json").write_text(json.dumps(cands, ensure_ascii=False), encoding="utf-8")
        (inc / "fetched.json").write_text("[]", encoding="utf-8")
        monkeypatch.setattr(paths, "DATA", data)
        # yt-dlp 两条找法都落空（命令不在 PATH、模块不可导入）
        real_which, real_find = shutil.which, importlib.util.find_spec
        monkeypatch.setattr(A.shutil, "which", lambda n, *a, **k: None if n == "yt-dlp" else real_which(n, *a, **k))
        monkeypatch.setattr(A.importlib.util, "find_spec",
                            lambda n, *a, **k: None if n == "yt_dlp" else real_find(n, *a, **k))
        with pytest.raises(SystemExit, match="找不到 yt-dlp"):
            A.yt_dlp_argv()  # 夹具自检：环境里确实没有 yt-dlp
        return inc

    def test_直链dry_run打印curl命令(self, repo, capsys):
        assert A.cmd_fetch(1, dry_run=True) == 0
        line = next(ln for ln in capsys.readouterr().out.splitlines() if "执行：" in ln)
        assert line.split("执行：", 1)[1].split()[0] == "curl"
        assert line.endswith(self.DIRECT)

    def test_直链真跑用curl且台账加一(self, repo, monkeypatch):
        import json
        import subprocess

        ran: list[list[str]] = []

        def fake_run(argv, *a, **k):
            ran.append(list(argv))
            (repo / "直链探针.mp4").write_bytes(b"\0")
            return subprocess.CompletedProcess(argv, 0)

        monkeypatch.setattr(A.subprocess, "run", fake_run)
        assert A.cmd_fetch(1) == 0
        assert [a[0] for a in ran] == ["curl"]
        ledger = json.loads((repo / "fetched.json").read_text(encoding="utf-8"))
        assert [e["url"] for e in ledger] == [self.DIRECT]

    def test_页面URL仍报原错且文案逐字(self, repo, capsys):
        # 批量语义（2026-10-10 B3）：单条失败不再抛 SystemExit，打印原文、退 1；文案逐字不变
        assert A.cmd_fetch(2, dry_run=True) == 1
        out = capsys.readouterr().out
        assert ("FAIL 找不到 yt-dlp。二选一：\n"
                "     uv add yt-dlp        # 进项目虚拟环境（推荐，E6）\n"
                "     brew install yt-dlp  # 系统级") in out

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


# ---------- 候选号解析 / 探针 / cookies 桥（2026-10-10，B2+B1） ----------


class TestParseNos:
    def test_单号与逗号列表保序去重(self):
        assert A.parse_nos("3", 5) == [3]
        assert A.parse_nos(" 1, 3,3,5 ", 5) == [1, 3, 5]

    def test_all展开(self):
        assert A.parse_nos("ALL", 3) == [1, 2, 3]

    def test_越界空串垃圾都拒(self):
        for bad in ("6", "0", "x", "", "1,,2"):
            with pytest.raises(SystemExit):
                A.parse_nos(bad, 5)


class TestProbeArgv:
    PAGE = "https://www.youtube.com/watch?v=x"

    def test_直链走HEAD且带超时(self):
        argv = A.probe_argv("https://example.com/a.mp4")
        assert argv[:3] == ["curl", "-sSIL", "--max-time"] and argv[-1].endswith("a.mp4")

    def test_页面只解析不落盘(self):
        argv = A.probe_argv(self.PAGE, yt_dlp=["yt-dlp"])
        assert "-s" in argv and "-J" in argv and "--no-cache-dir" in argv
        assert "--cookies-from-browser" not in argv

    def test_cookies旗只在该挂时挂(self):
        from pathlib import Path
        argv = A.probe_argv(self.PAGE, yt_dlp=["yt-dlp"], cookies="chromium:/p")
        assert argv[1:3] == ["--cookies-from-browser", "chromium:/p"]
        fargv = A.fetch_argv(self.PAGE, Path("/tmp/in"), "s", yt_dlp=["yt-dlp"], cookies="chromium:/p")
        assert fargv[1:3] == ["--cookies-from-browser", "chromium:/p"]
        fargv0 = A.fetch_argv(self.PAGE, Path("/tmp/in"), "s", yt_dlp=["yt-dlp"])
        assert "--cookies-from-browser" not in fargv0


class TestCmdProbe:
    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        import json as J
        from pipeline import paths
        data = tmp_path / "data"
        inc = data / "library" / "incoming"
        inc.mkdir(parents=True)
        cands = [
            {"title": "直链素材", "url": "https://cdn.example.com/a.mp4", "type": "live",
             "source": "测试", "why": "直链支路"},
            {"title": "页面素材", "url": "https://www.youtube.com/watch?v=x", "type": "mv",
             "source": "测试", "why": "页面支路", "expected_dur": 360},
        ]
        (inc / "candidates.json").write_text(J.dumps(cands, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(paths, "DATA", data)
        monkeypatch.setattr(A, "yt_dlp_argv", lambda: ["yt-dlp"])
        return inc

    def _fake_run(self, ytdlp_rc=0, ytdlp_out="{}", ytdlp_err=""):
        import subprocess

        def fake(argv, *a, **k):
            if argv[0] == "curl":
                return subprocess.CompletedProcess(argv, 0,
                    stdout="HTTP/1.1 200 OK\r\nContent-Type: video/mp4\r\nContent-Length: 1048576\r\n",
                    stderr="")
            return subprocess.CompletedProcess(argv, ytdlp_rc, stdout=ytdlp_out, stderr=ytdlp_err)
        return fake

    def test_直链可解析(self, repo, monkeypatch, capsys):
        monkeypatch.setattr(A.subprocess, "run", self._fake_run())
        assert A.cmd_probe("1") == 0
        assert "HTTP 200" in capsys.readouterr().out

    def test_页面可解析且时长对上(self, repo, monkeypatch, capsys):
        import json as J
        doc = {"title": "某MV", "duration": 360,
               "formats": [{"height": 1080}, {"height": 720}, {"vcodec": "none"}]}
        monkeypatch.setattr(A.subprocess, "run", self._fake_run(ytdlp_out=J.dumps(doc)))
        assert A.cmd_probe("2") == 0
        out = capsys.readouterr().out
        assert "某MV" in out and "偏差 0.0%" in out and "最高 1080p" in out
        assert "⚠" not in out

    def test_时长偏差超5给警告但不判死(self, repo, monkeypatch, capsys):
        import json as J
        doc = {"title": "剪辑版", "duration": 300, "formats": []}
        monkeypatch.setattr(A.subprocess, "run", self._fake_run(ytdlp_out=J.dumps(doc)))
        assert A.cmd_probe("2") == 0          # WARN 不判死：探针是报告，判决在 gate
        assert "⚠" in capsys.readouterr().out

    def test_要登录的给提示且整批退1(self, repo, monkeypatch, capsys):
        monkeypatch.setattr(A.subprocess, "run",
                            self._fake_run(ytdlp_rc=1, ytdlp_err="ERROR: Sign in to confirm"))
        assert A.cmd_probe("2") == 1
        assert "登录态" in capsys.readouterr().out

    def test_all一批全探失败不遮成功(self, repo, monkeypatch, capsys):
        monkeypatch.setattr(A.subprocess, "run",
                            self._fake_run(ytdlp_rc=1, ytdlp_err="boom"))
        assert A.cmd_probe("all") == 1        # 1 成 1 败 → 退 1
        out = capsys.readouterr().out
        assert "HTTP 200" in out and "1/2 可解析" in out


# ---------- 批量抓取 + also 备选源（2026-10-10，B3+B4） ----------


class TestFetchBatch:
    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        import json as J
        from pipeline import paths
        data = tmp_path / "data"
        inc = data / "library" / "incoming"
        inc.mkdir(parents=True)
        cands = [
            {"title": "直链甲", "url": "https://cdn.example.com/a.mp4", "type": "live",
             "source": "测试", "why": "批量支路甲"},
            {"title": "直链乙", "url": "https://cdn.example.com/b.mp4", "type": "live",
             "source": "测试", "why": "批量支路乙"},
            {"title": "带备选", "url": "https://dead.example.com/c.mp4",
             "also": ["https://cdn.example.com/c-backup.mp4"], "type": "mv",
             "source": "测试", "why": "备选源支路"},
        ]
        (inc / "candidates.json").write_text(J.dumps(cands, ensure_ascii=False), encoding="utf-8")
        (inc / "fetched.json").write_text("[]", encoding="utf-8")
        monkeypatch.setattr(paths, "DATA", data)
        return inc

    def _fake_run(self, fail_hosts=()):
        import subprocess
        from pathlib import Path

        def fake(argv, *a, **k):
            url = argv[-1]
            if any(h in url for h in fail_hosts):
                return subprocess.CompletedProcess(argv, 1)
            # curl 支路：-o 后就是完整落盘路径（dest/slug.mp4），照写
            Path(argv[argv.index("-o") + 1]).write_bytes(b"\0")
            return subprocess.CompletedProcess(argv, 0)
        return fake

    def test_批量单条失败不阻塞整批(self, repo, monkeypatch, capsys):
        monkeypatch.setattr(A.subprocess, "run", self._fake_run(fail_hosts=("b.mp4",)))
        assert A.cmd_fetch("1,2") == 1
        import json as J
        ledger = J.loads((repo / "fetched.json").read_text(encoding="utf-8"))
        assert [e["url"] for e in ledger] == ["https://cdn.example.com/a.mp4"]
        assert "1/2 成功" in capsys.readouterr().out

    def test_all全成功(self, repo, monkeypatch, capsys):
        monkeypatch.setattr(A.subprocess, "run", self._fake_run())
        assert A.cmd_fetch("all") == 0
        assert "3/3 成功" in capsys.readouterr().out

    def test_dry_run打印全部命令不抓(self, repo, monkeypatch, capsys):
        import subprocess
        monkeypatch.setattr(A.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("不许真跑")))
        assert A.cmd_fetch("1,3", dry_run=True) == 0
        out = capsys.readouterr().out
        assert out.count("  执行：") == 2 and out.count("换备选源执行：") == 1

    def test_主源死了自动换备选且台账记实际源(self, repo, monkeypatch, capsys):
        monkeypatch.setattr(A.subprocess, "run", self._fake_run(fail_hosts=("dead.example.com",)))
        assert A.cmd_fetch("3") == 0
        import json as J
        ledger = J.loads((repo / "fetched.json").read_text(encoding="utf-8"))
        assert ledger[0]["url"] == "https://cdn.example.com/c-backup.mp4"
        assert "换备选源执行" in capsys.readouterr().out

    def test_备选源也抓过照样查重拒(self, repo, capsys):
        import json as J
        (repo / "fetched.json").write_text(J.dumps(
            [{"url": "https://cdn.example.com/c-backup.mp4"}]), encoding="utf-8")
        assert A.cmd_fetch("3") == 1
        assert "查重没过" in capsys.readouterr().out


class TestAlsoSchema:
    def test_合法备选源收进清单(self):
        raw = """[{"title": "t", "url": "https://a.com/x", "type": "mv", "source": "s",
                   "why": "w", "also": ["https://b.com/y"]}]"""
        assert A.load_candidates(raw)[0]["also"] == ["https://b.com/y"]

    def test_坏备选源一次报全(self):
        raw = """[{"title": "t", "url": "https://a.com/x", "type": "mv", "source": "s",
                   "why": "w", "also": ["magnet:?xt=bad"]}]"""
        with pytest.raises(SystemExit, match="also"):
            A.load_candidates(raw)
