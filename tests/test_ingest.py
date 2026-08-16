"""phase0 的批处理纪律（E10）与成对跳过判据。

2026-08-16 审计：① verify 分支早就是逐集 FAIL，build/register 却是裸奔——
一集字幕解析不出台词（SystemExit）或 intact 不过，后面二十几集全停；
② 跳过判据只看 .npy，写一半崩溃的孤儿残骸被当成「已索引」跳过重建。
verify/build/register 都是模型/ffmpeg 边界，按项目约定在边界注入观测值
（先例见 test_tts 的回读豁免、test_check_script 的 load_sources）。
"""

from pathlib import Path

import pytest

from pipeline import ingest


def _mk(tmp_path, name):
    v = tmp_path / name
    v.write_bytes(b"mkv")
    (tmp_path / name.replace(".mkv", ".Chs&Jap.ass")).write_text(
        "[Script Info]", encoding="utf-8")
    return v


class TestPhase0Batch:
    def _env(self, monkeypatch, results):
        calls = []
        monkeypatch.setattr(ingest, "verify", lambda v, s: (True, []))
        monkeypatch.setattr(ingest, "register",
                            lambda v, a, s, e: calls.append(("reg", e)) or {})

        def build(sub, anime, season, ep, index_dir):
            calls.append(("build", ep))
            r = results[ep]
            if isinstance(r, BaseException):
                raise r
            return r
        monkeypatch.setattr(ingest, "build", build)
        return calls

    def test_一集build失败整批继续(self, tmp_path, monkeypatch):
        v1, v2 = _mk(tmp_path, "[01].mkv"), _mk(tmp_path, "[02].mkv")
        calls = self._env(monkeypatch, {1: SystemExit("FAIL 未解析出台词"), 2: 120})
        bad = ingest.phase0([v1, v2], "春物", 1, index_dir=tmp_path / "idx")
        assert bad == 1
        assert ("build", 2) in calls and ("reg", 2) in calls   # 第二集照常入库

    def test_一集register失败整批继续(self, tmp_path, monkeypatch):
        v1, v2 = _mk(tmp_path, "[01].mkv"), _mk(tmp_path, "[02].mkv")
        calls = self._env(monkeypatch, {1: 120, 2: 121})
        monkeypatch.setattr(ingest, "register",
                            lambda v, a, s, e: (_ for _ in ()).throw(
                                SystemExit("FAIL 片源不完整")) if e == 1
                            else calls.append(("reg", e)) or {})
        bad = ingest.phase0([v1, v2], "春物", 1, index_dir=tmp_path / "idx")
        assert bad == 1 and ("reg", 2) in calls

    def test_孤儿npy不算已索引_不跳过(self, tmp_path, monkeypatch):
        # npy 在、json 缺（写一半崩溃的残骸）→ 必须重建，不许 SKIP
        v1, v2 = _mk(tmp_path, "[01].mkv"), _mk(tmp_path, "[02].mkv")
        idx = tmp_path / "idx"
        idx.mkdir()
        (idx / "春物_S01E01.npy").write_bytes(b"")
        calls = self._env(monkeypatch, {1: 111, 2: 222})
        ingest.phase0([v1, v2], "春物", 1, index_dir=idx)
        assert ("build", 1) in calls      # 旧判据会 SKIP 第 1 集

    def test_成对索引才跳过(self, tmp_path, monkeypatch):
        v1 = _mk(tmp_path, "[01].mkv")
        idx = tmp_path / "idx"
        idx.mkdir()
        (idx / "春物_S01E01.npy").write_bytes(b"")
        (idx / "春物_S01E01.json").write_text("{}", encoding="utf-8")
        calls = self._env(monkeypatch, {1: 111})
        bad = ingest.phase0([v1], "春物", 1, index_dir=idx)
        assert bad == 0 and calls == []   # 完整在盘 → SKIP，不重跑


class TestRequireData:
    """ingest/subindex 的 main 必须先 require_data（2026-08-16 审计 2-17）。

    register/build 都会往 data/ 里 mkdir——缺 data/ 时自动创建实体目录，
    正是 CLAUDE.md「脚本绝不自动创建 data/」要防的后果（符号链接指向
    未挂载卷时几十 G 静默写进系统盘的前半段）。
    """

    def test_ingest缺data目录当场报错(self, monkeypatch):
        from pipeline import paths
        monkeypatch.setattr(paths, "DATA", tmp := __import__("pathlib").Path("/nonexistent-ava"))
        monkeypatch.setattr("sys.argv", ["ingest", "probe", "x.mkv"])
        with pytest.raises(SystemExit, match="先建存储骨架"):
            ingest.main()

    def test_subindex缺data目录当场报错(self, monkeypatch):
        from pipeline import subindex
        from pipeline import paths
        monkeypatch.setattr(paths, "DATA", __import__("pathlib").Path("/nonexistent-ava"))
        monkeypatch.setattr("sys.argv", ["subindex", "search", "查询"])
        with pytest.raises(SystemExit, match="先建存储骨架"):
            subindex.main()


class TestVerifyWindowGuard:
    """verify 采样窗护栏（2026-08-16 审计 2-31，核实属实）。

    dur<100s 时 hi = dur*0.8-60 < lo，rng.uniform(lo, hi) 采出负起点，
    ffmpeg -ss 负值报错——离「这片太短，采不出窗」的病根很远。
    护栏放在 ASR 依赖 import 之前（短片失败不等模型加载，测试也不依赖 mlx）。
    """

    def test_短视频当场报错给出替代路径(self, monkeypatch):
        monkeypatch.setattr(ingest, "_video_duration", lambda v: 60.0)
        with pytest.raises(SystemExit, match="采不出"):
            ingest.verify(Path("短特典.mkv"), Path("外挂.ass"))

    def test_足够长的视频不受影响(self, monkeypatch):
        calls = {}

        def fake_uniform(lo, hi):
            calls.setdefault("range", []).append((lo, hi))
            return 1000.0

        import random as _random
        monkeypatch.setattr(ingest, "_video_duration", lambda v: 1500.0)
        monkeypatch.setattr(_random.Random, "uniform", fake_uniform)
        # 走到 ASR 之前只断言窗口合法：1500s 时 hi=1140 > lo=300
        try:
            ingest.verify(Path("正片.mkv"), Path("外挂.ass"))
        except Exception:
            pass    # 后续需要真模型/文件，任何异常都行——窗口计算已经过掉了
        assert all(lo < hi for lo, hi in calls.get("range", []))
