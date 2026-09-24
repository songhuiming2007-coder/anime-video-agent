"""review.py 渲染逻辑（ADR-0004 新字段展示）。

不碰 build()（会真的抽帧）——只测被提取出来的纯函数：
集号 + 降级链标记、在场分标签、缩略图文件名。前两个是人审界面上唯一能
看到的「集号有没有锁对」「在场分是检出还是漏检」的地方，展示错了等于
人审没看到关键信息（又是静默失败）。第三个测的是缓存键对不对——
2026-08-10 踩过：文件名只按 (段号, clip 序号) 编号，换排片只改 start
不改序号时，`_frames()` 的 `if not p.exists()` 会把旧图当成最新的直接
复用，人审看到的是上一版画面且没有任何报错。
"""

import json
import os
import time
from pathlib import Path

import pytest

from pipeline import align, review
from pipeline.review import _clip_ep, _ep_label, _presence_txt, _thumb_path, approve


class TestEpLabel:
    def test_no_episode_blank(self):
        assert _ep_label({}) == ""
        assert _ep_label({"episode": None}) == ""

    def test_episode_only(self):
        out = _ep_label({"episode": "S01E08"})
        assert "S01E08" in out and "集" in out
        assert "→" not in out

    def test_fell_back_season(self):
        out = _ep_label({"episode": "S01E08", "ep_fell_back": True, "ep_scope": 1})
        assert "→季内检索" in out and "→全空间检索" not in out

    def test_fell_back_global(self):
        out = _ep_label({"episode": "S01E08", "ep_fell_back": True, "ep_scope": 2})
        assert "→全空间检索" in out and "→季内检索" not in out

    def test_escapes_episode(self):
        # 集号来自稿件解析，含 < > 只会来自写错的稿件；不转义会注入 HTML
        out = _ep_label({"episode": "S01E<1>"})
        assert "<b>S01E<1></b>" not in out


class TestPresenceTxt:
    def test_none_blank(self):
        # 场景段没有在场分，显示空白而不是「在场 0」（0 = 有语义的未检出）
        assert _presence_txt(None) == ""

    def test_zero_flag(self):
        # 未检出：垫底是排片时故意放的，但必须让人看到「这段未必有那个人」。
        # 精确匹配不是 in——漏检显示成「在场 0.00」也含「在场 0」子串，
        # in 会把变异后的错误展示放过去（变异检验抓到的）
        assert _presence_txt(0.0) == '<span class="flag">在场 0</span>'

    def test_positive_two_decimals(self):
        # 0.976 → 0.98 是向上舍入；0.995 不测——它在二进制浮点里是
        # 0.99499…，格式化只会给 0.99，是浮点表示问题不是展示问题
        assert _presence_txt(0.973) == " · 在场 0.97"
        assert _presence_txt(0.976) == " · 在场 0.98"


class TestApprove:
    """approve() 是段级不变量的第一道闸（B4）：违例不许拷成 approved 版。"""

    def _write(self, episode: Path, *, seg_dur: float, clip_durs: list[float]):
        clips = {"segments": [{
            "index": 1, "status": "ok",
            "clips": [{"dur": d} for d in clip_durs],
        }]}
        (episode / "04-clips.json").write_text(
            json.dumps(clips, ensure_ascii=False), encoding="utf-8")
        audio_dir = episode / "03-audio"
        audio_dir.mkdir()
        manifest = {"segments": [{"index": 1, "duration": seg_dur}]}
        (audio_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def test_对齐则拷贝成功(self, tmp_path):
        self._write(tmp_path, seg_dur=6.0, clip_durs=[3.0, 3.0])
        dest = approve(tmp_path)
        assert dest == tmp_path / "04-clips.approved.json"
        assert json.loads(dest.read_text(encoding="utf-8")) == \
            json.loads((tmp_path / "04-clips.json").read_text(encoding="utf-8"))

    def test_违例则拒绝且不拷贝(self, tmp_path):
        # 6.0 画面 vs 6.4 配音，差 0.4s > SEG_TOL——不许先拷再报
        self._write(tmp_path, seg_dur=6.4, clip_durs=[3.0, 3.0])
        with pytest.raises(SystemExit, match="段级时长不对齐"):
            approve(tmp_path)
        assert not (tmp_path / "04-clips.approved.json").exists()


class TestThumbPath:
    def test_same_content_same_name(self):
        # 内容不变，重跑要能复用旧图——这就是缓存存在的意义，不能测没了
        a = _thumb_path(Path("/x"), "s04c1", 525.61, 7.479, 0)
        b = _thumb_path(Path("/x"), "s04c1", 525.61, 7.479, 0)
        assert a == b

    def test_start_change_changes_name(self):
        # 换排片最常改的就是 start——文件名必须跟着变，
        # 否则旧缩略图会被 `if not p.exists()` 当成最新的直接复用
        a = _thumb_path(Path("/x"), "s04c1", 625.0, 7.479, 0)
        b = _thumb_path(Path("/x"), "s04c1", 525.61, 7.479, 0)
        assert a != b

    def test_dur_change_changes_name(self):
        # dur 决定中点/出点帧落在哪一秒，同样得反映进文件名，
        # 不然只改 dur 也会撞上同一个陈旧缓存问题
        a = _thumb_path(Path("/x"), "s04c1", 525.61, 7.479, 1)
        b = _thumb_path(Path("/x"), "s04c1", 525.61, 5.0, 1)
        assert a != b


class TestClipEp:
    """抽检页片段的集号标签兜底（2026-08-18 复盘③）：手工补的片段只有
    source/start/dur，裸取 season/episode 会让整页 KeyError。"""

    def test_机器产物照常(self):
        assert _clip_ep({"season": 1, "episode": 8}) == "S01E08"

    def test_缺键退文件名(self):
        assert _clip_ep({"source": "data/library/raw/某番/[08].mkv"}) == "[08].mkv"

    def test_连source都没有不崩(self):
        assert _clip_ep({}) == "?"


class TestPatchApproveGate:
    """Spec §4.4 闸 3 & §5：补丁段标记与 approve 二次确认。"""

    def _write_patch_episode(self, episode: Path, is_anchor: bool = False):
        if is_anchor:
            # 锚点补丁段：无 via="patch-rescue"，但 clip 的 anime 指向补丁池
            clip = {"dur": 6.0, "anime": "my-patch"}
            seg = {"index": 1, "status": "ok", "channel": "anchor", "clips": [clip]}
        else:
            clip = {"dur": 6.0, "anime": "test"}
            seg = {"index": 1, "status": "ok", "via": "patch-rescue", "clips": [clip]}

        (episode / "04-clips.json").write_text(
            json.dumps({"segments": [seg]}, ensure_ascii=False), encoding="utf-8"
        )
        audio_dir = episode / "03-audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        (audio_dir / "manifest.json").write_text(
            json.dumps({"segments": [{"index": 1, "duration": 6.0}]}, ensure_ascii=False),
            encoding="utf-8"
        )
        patch_dir = episode / "04-patch"
        patch_dir.mkdir(parents=True, exist_ok=True)
        (patch_dir / "pool.json").write_text(
            json.dumps({"pool": "my-patch", "assets": {}}), encoding="utf-8"
        )

    def test_存在补丁段且未确认时提示并可拒绝(self, tmp_path, monkeypatch):
        self._write_patch_episode(tmp_path, is_anchor=False)
        monkeypatch.setattr("builtins.input", lambda prompt: "n")
        with pytest.raises(SystemExit, match="存在未人工核对的补丁段"):
            approve(tmp_path)

    def test_存在补丁段且确认后批准(self, tmp_path, monkeypatch):
        self._write_patch_episode(tmp_path, is_anchor=False)
        monkeypatch.setattr("builtins.input", lambda prompt: "y")
        dest = approve(tmp_path)
        assert dest.exists()

    def test_锚点补丁段无via也触发二次确认(self, tmp_path, monkeypatch):
        # 闸 3 核心用例：锚点补丁段无 via，必须由 anime == 补丁池名 捕获
        self._write_patch_episode(tmp_path, is_anchor=True)
        monkeypatch.setattr("builtins.input", lambda prompt: "n")
        with pytest.raises(SystemExit, match="存在未人工核对的补丁段"):
            approve(tmp_path)

    def test_confirm_patch参数可免确认(self, tmp_path):
        self._write_patch_episode(tmp_path, is_anchor=True)
        dest = approve(tmp_path, confirm_patch=True)
        assert dest.exists()


class TestApproveExpectFingerprint:
    """T20（Spec 3 §4.5 S3-R9 / S3-R9a / C.2）：解封物原子核验。

    变体覆盖 MUT-20 / MUT-21 / MUT-24 / MUT-28。
    """

    def _make_clips_episode(self, ep: Path, *, start: float = 10.0) -> Path:
        ep.mkdir(parents=True, exist_ok=True)
        clips = {
            "anime": "TestAnime",
            "segments": [
                {
                    "index": 1,
                    "status": "ok",
                    "clips": [{"season": 1, "episode": 1, "start": start, "dur": 6.0}],
                }
            ],
        }
        (ep / "04-clips.json").write_text(
            json.dumps(clips, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        audio_dir = ep / "03-audio"
        audio_dir.mkdir(exist_ok=True)
        (audio_dir / "manifest.json").write_text(
            json.dumps({"segments": [{"index": 1, "duration": 6.0}]}), encoding="utf-8"
        )
        return ep / "04-clips.json"

    def _fingerprint(self, src: Path) -> tuple[int, int, int]:
        st = src.stat()
        return (st.st_size, st.st_mtime_ns, st.st_atime_ns)

    def test_expect_不符则不写批准文件(self, tmp_path):
        """①（MUT-20）：expect 与当前 04-clips.json 不符 → 退出 1，approved 不存在。"""
        src = self._make_clips_episode(tmp_path)
        size, mtime_ns, atime_ns = self._fingerprint(src)
        dest = tmp_path / "04-clips.approved.json"

        with pytest.raises(SystemExit) as exc_size:
            approve(tmp_path, expect=(size + 1, mtime_ns))
        assert "已不是审阅时的版本" in str(exc_size.value)
        assert not dest.exists()

        with pytest.raises(SystemExit) as exc_mtime:
            approve(tmp_path, expect=(size, mtime_ns + 1))
        assert "已不是审阅时的版本" in str(exc_mtime.value)
        assert not dest.exists()

    def test_expect_相符则字节与_mtime_一致(self, tmp_path):
        """②：相符 → 解封物与源字节相等、st_mtime_ns 相等。"""
        src = self._make_clips_episode(tmp_path)
        size, mtime_ns, _ = self._fingerprint(src)
        dest = approve(tmp_path, expect=(size, mtime_ns))
        assert dest == tmp_path / "04-clips.approved.json"
        assert dest.read_bytes() == src.read_bytes()
        assert dest.stat().st_mtime_ns == mtime_ns

    def test_读取期间改写成_F2_仍写_F1(self, tmp_path, monkeypatch):
        """③（MUT-21 / C.7）：段级校验时源文件已被改成段级对齐的 F2 → 解封物仍是 F1。"""
        src = self._make_clips_episode(tmp_path, start=10.0)
        f1_bytes = src.read_bytes()
        size, mtime_ns, _ = self._fingerprint(src)
        orig_verify = review.verify_alignment

        def _verify_after_rewrite(segments, audio):
            # F2：段级内容与 F1 不同（start 变了），但仍段级对齐；字节数不变
            data = json.loads(src.read_text(encoding="utf-8"))
            data["segments"][0]["clips"][0]["start"] = 20.0
            src.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            assert len(src.read_bytes()) == size
            return orig_verify(segments, audio)

        monkeypatch.setattr(review, "verify_alignment", _verify_after_rewrite)
        dest = approve(tmp_path, expect=(size, mtime_ns))
        assert dest.read_bytes() == f1_bytes
        assert align.has_clips_approved_diff(tmp_path) is True

    def test_不给_expect_时行为与现状一致(self, tmp_path):
        """④：不给 expect → 与 copy2 逐字节、逐 mtime 一致。"""
        src = self._make_clips_episode(tmp_path)
        before = self._fingerprint(src)
        dest = approve(tmp_path)
        assert dest.read_bytes() == src.read_bytes()
        assert dest.stat().st_mtime_ns == before[1]

    def test_expect_经等号形式可注入期目录(self, tmp_path):
        """⑤：`--expect-*` 必须用等号形式，否则 validate_pipeline_command 不再注入期目录。"""
        from pipeline.agent.tools import sys_python, validate_pipeline_command

        self._make_clips_episode(tmp_path)
        ok, msg, argv = validate_pipeline_command(
            ["review", "--approve", "--expect-size=100", "--expect-mtime-ns=200"],
            scope="pipeline",
            ep_dir=tmp_path,
        )
        assert ok, msg
        assert argv == [
            sys_python(), "-m", "pipeline.review", str(tmp_path.resolve()),
            "--approve", "--expect-size=100", "--expect-mtime-ns=200",
        ]

        # 空格形式的对照：值被当成位置参数，期目录不再被注入（等号形式的理由）
        ok2, _msg2, argv2 = validate_pipeline_command(
            ["review", "--approve", "--expect-size", "100"],
            scope="pipeline",
            ep_dir=tmp_path,
        )
        assert ok2
        assert str(tmp_path.resolve()) not in argv2

    def test_同长度原地覆写被二次_fstat_拦下(self, tmp_path, monkeypatch):
        """⑥（MUT-24）：同长度原地覆写为段级对齐的 F2 → 退出 1、不写文件。"""
        src = self._make_clips_episode(tmp_path, start=10.0)
        dest = tmp_path / "04-clips.approved.json"
        size, mtime_ns, _ = self._fingerprint(src)
        orig_read_all = review._read_all

        def _read_after_inplace_rewrite(f):
            time.sleep(0.01)
            data = json.loads(src.read_text(encoding="utf-8"))
            data["segments"][0]["clips"][0]["start"] = 20.0
            src.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            assert src.stat().st_size == size
            return orig_read_all(f)

        monkeypatch.setattr(review, "_read_all", _read_after_inplace_rewrite)
        with pytest.raises(SystemExit) as exc:
            approve(tmp_path, expect=(size, mtime_ns))
        assert "在读取期间被改写" in str(exc.value)
        assert not dest.exists()

    def test_同长度原地覆写_字节数变化也拦下(self, tmp_path, monkeypatch):
        """⑥ 变体：F2 字节数不同 → 同样退出 1、不写文件。"""
        src = self._make_clips_episode(tmp_path, start=10.0)
        dest = tmp_path / "04-clips.approved.json"
        size, mtime_ns, _ = self._fingerprint(src)
        orig_read_all = review._read_all

        def _read_after_longer_rewrite(f):
            data = json.loads(src.read_text(encoding="utf-8"))
            data["extra_note"] = "a much longer note to change the byte count"
            src.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            assert src.stat().st_size != size
            return orig_read_all(f)

        monkeypatch.setattr(review, "_read_all", _read_after_longer_rewrite)
        with pytest.raises(SystemExit) as exc:
            approve(tmp_path, expect=(size, mtime_ns))
        assert "在读取期间被改写" in str(exc.value)
        assert not dest.exists()

    def test_mtime_回拨也被_ctime_拦下(self, tmp_path, monkeypatch):
        """⑦（MUT-28 / C.2）：同长度原地覆写后 os.utime 回拨 mtime → 退出 1、不写文件。"""
        src = self._make_clips_episode(tmp_path, start=10.0)
        dest = tmp_path / "04-clips.approved.json"
        st_before = src.stat()
        orig_read_all = review._read_all

        def _read_after_utime_rollback(f):
            time.sleep(0.01)
            data = json.loads(src.read_text(encoding="utf-8"))
            data["segments"][0]["clips"][0]["start"] = 20.0
            src.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.utime(src, ns=(st_before.st_atime_ns, st_before.st_mtime_ns))
            st_after = src.stat()
            assert st_after.st_size == st_before.st_size
            assert st_after.st_mtime_ns == st_before.st_mtime_ns
            assert st_after.st_ctime_ns != st_before.st_ctime_ns
            return orig_read_all(f)

        monkeypatch.setattr(review, "_read_all", _read_after_utime_rollback)
        with pytest.raises(SystemExit) as exc:
            approve(tmp_path, expect=(st_before.st_size, st_before.st_mtime_ns))
        assert "在读取期间被改写" in str(exc.value)
        assert not dest.exists()
