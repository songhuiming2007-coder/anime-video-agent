"""候选复核探针（P1）：diff 分桶、probe 纯函数辅助、score 判分。

`diff`/`score` 是纯函数，直接测；`probe` 需要真实索引/模型/ffmpeg，这里只测
被拆出来的纯函数部分（分桶、注记扫描、伪 clip 构造、候选匹配、上下文查找），
`probe_episode` 本体的「至少一期跑通」由挂盘环境下的手工验证覆盖（方案完成判定）。

**四个判分指标的期望值先手算，写进断言前不看代码跑出什么。** 见
`TestComputeScore.test_四个指标手算期望值` 顶部的手算表。
"""

import json

import pytest

from pipeline import recheck as r
from pipeline.subindex import Unit


def clip(source="a.mkv", season=1, episode=1, start=10.0, dur=3.0, **extra):
    return {"source": source, "season": season, "episode": episode,
            "start": start, "dur": dur, **extra}


def seg(index=1, status="ok", clips=None, **extra):
    return {"index": index, "status": status, "clips": clips or [], **extra}


class TestBucketSegment:
    """六桶各一条，float 舍入差不误报，带注记的归 annotated_bad。"""

    def test_unchanged(self):
        c1 = clip()
        b, notes = r.bucket_segment(seg(clips=[c1]), seg(clips=[dict(c1)]))
        assert b == "unchanged" and notes == {}

    def test_content_changed_source(self):
        m = seg(clips=[clip(source="a.mkv")])
        a = seg(clips=[clip(source="b.mkv")])
        b, _ = r.bucket_segment(m, a)
        assert b == "content_changed"

    def test_content_changed_episode(self):
        m = seg(clips=[clip(episode=1)])
        a = seg(clips=[clip(episode=2)])
        b, _ = r.bucket_segment(m, a)
        assert b == "content_changed"

    def test_start_shifted(self):
        m = seg(clips=[clip(start=10.0, dur=3.0)])
        a = seg(clips=[clip(start=12.5, dur=3.0)])
        b, _ = r.bucket_segment(m, a)
        assert b == "start_shifted"

    def test_dur_changed(self):
        m = seg(clips=[clip(start=10.0, dur=3.0)])
        a = seg(clips=[clip(start=10.0, dur=4.5)])
        b, _ = r.bucket_segment(m, a)
        assert b == "dur_changed"

    def test_count_changed(self):
        m = seg(clips=[clip()])
        a = seg(clips=[clip(), clip(start=20.0)])
        b, _ = r.bucket_segment(m, a)
        assert b == "count_changed"

    def test_human_filled(self):
        m = seg(status="no_match", clips=[])
        a = seg(clips=[clip()])
        b, _ = r.bucket_segment(m, a)
        assert b == "human_filled"

    def test_annotated_bad_优先于其他桶(self):
        # clip 内容也变了（会被误判成 content_changed），但注记必须优先
        m = seg(clips=[clip(source="a.mkv", _manual_fix="改 start 是因为镜头拍错人")])
        a = seg(clips=[clip(source="b.mkv")])
        b, notes = r.bucket_segment(m, a)
        assert b == "annotated_bad"
        assert notes == {"_manual_fix": "改 start 是因为镜头拍错人"}

    def test_annotated_bad_键名大小写不敏感(self):
        m = seg(clips=[clip(MANUAL_FIX="x")])
        b, notes = r.bucket_segment(m, seg(clips=[clip()]))
        assert b == "annotated_bad" and "MANUAL_FIX" in notes

    def test_annotated_bad_中文键名(self):
        m = seg(clips=[clip(手改说明="镜头切错了")])
        b, notes = r.bucket_segment(m, seg(clips=[clip()]))
        assert b == "annotated_bad" and notes["手改说明"] == "镜头切错了"

    def test_float舍入差不误报(self):
        # 0.00001 的舍入差（浮点运算残留），round 3 位后应视为相同
        m = seg(clips=[clip(start=10.12341, dur=3.00001)])
        a = seg(clips=[clip(start=10.1234, dur=2.99999)])
        b, _ = r.bucket_segment(m, a)
        assert b == "unchanged"

    def test_note字段拼接键值(self):
        m = seg(clips=[clip(_manual_fix="改因A")])
        _, notes = r.bucket_segment(m, seg(clips=[clip()]))
        assert notes == {"_manual_fix": "改因A"}


class TestAutoLabel:
    def test_dur_changed自动good(self):
        assert r.auto_label("dur_changed") == "good"

    def test_unchanged保持null(self):
        # unchanged 是唯一允许 label 为 null 的桶——探针/判分都不需要它
        assert r.auto_label("unchanged") is None

    def test_其余桶保持null待人工标注(self):
        for b in ("content_changed", "start_shifted", "count_changed",
                  "human_filled", "annotated_bad"):
            assert r.auto_label(b) is None


class TestPseudoClip:
    def test_有片源返回伪clip(self):
        u = Unit(anime="春物", season=1, episode=8, start=10.0, end=13.5, text="台词")
        sources = {"S01E08": {"path": "x.mkv", "duration": 1400.0}}
        c = r._pseudo_clip(u, sources)
        assert c == {"start": 10.0, "dur": 3.5, "source": "x.mkv"}

    def test_该集片源未登记返回None(self):
        u = Unit(anime="春物", season=9, episode=9, start=0.0, end=1.0, text="台词")
        assert r._pseudo_clip(u, {}) is None


class TestMatchesHumanClip:
    def test_同集且区间相交(self):
        cand = {"season": 1, "episode": 8, "start": 9.5, "end": 13.2}
        human = {"season": 1, "episode": 8, "start": 10.0, "dur": 3.0}
        assert r._matches_human_clip(cand, human) is True

    def test_不同集不算(self):
        cand = {"season": 1, "episode": 7, "start": 9.5, "end": 13.2}
        human = {"season": 1, "episode": 8, "start": 10.0, "dur": 3.0}
        assert r._matches_human_clip(cand, human) is False

    def test_同集但区间不相交(self):
        cand = {"season": 1, "episode": 8, "start": 100.0, "end": 103.0}
        human = {"season": 1, "episode": 8, "start": 10.0, "dur": 3.0}
        assert r._matches_human_clip(cand, human) is False


class TestContext:
    def _units(self):
        return [
            Unit(anime="春物", season=1, episode=8, start=0.0, end=2.0, text="第0句"),
            Unit(anime="春物", season=1, episode=8, start=2.0, end=4.0, text="第1句"),
            Unit(anime="春物", season=1, episode=8, start=4.0, end=6.0, text="第2句"),
        ]

    def test_中间单元有前后句(self):
        units = self._units()
        prev_t, next_t = r._context(units, units[1])
        assert prev_t == "第0句" and next_t == "第2句"

    def test_首单元前句为无(self):
        units = self._units()
        prev_t, next_t = r._context(units, units[0])
        assert prev_t == "无" and next_t == "第1句"

    def test_末单元后句为无(self):
        units = self._units()
        prev_t, next_t = r._context(units, units[-1])
        assert prev_t == "第1句" and next_t == "无"

    def test_单元不在列表里返回双无(self):
        units = self._units()
        other = Unit(anime="春物", season=1, episode=9, start=0.0, end=1.0, text="别集")
        assert r._context(units, other) == ("无", "无")


class TestComputeScore:
    """四个指标：手算表见下，先算后写。

    段 | bucket           | label | top1 verdict | 参与
    ---|------------------|-------|--------------|----
    1  | content_changed  | bad   | not_match    | bad 检出
    2  | content_changed  | bad   | unsure       | bad 检出（unsure 也算检出）
    7  | content_changed  | bad   | not_match    | bad 检出
    3  | start_shifted    | good  | match        | good 未误拒
    4  | dur_changed      | good  | not_match    | good 误拒
    5  | content_changed  | good  | unsure       | good 未误拒（unsure 不算拒）
    6  | annotated_bad    | skip  | unsure       | **弃权：不进任何分母**（2026-08-14
                                          审计修正——skip=人工说不清，判它对错无意义）

    检出率 = 3/3 = 100%
    误拒率 = 1/3 ≈ 33.3%（good_total=3: 段3,4,5）
    unsure率 = 2/6 ≈ 33.3%（段2,5 top1=unsure；scored_total=6，段6 弃权不计）
    人选复核率：content_changed 段 1,2,5,7（段2无 human/候选数据，不计入分母）
      段1：命中候选 rank1，该 rank 判 not_match → 不算命中
      段5：命中候选 rank1，该 rank 判 unsure → 不算命中
      段7：命中候选 rank2，该 rank 判 match → 算命中
      => 1/3 ≈ 33.3%
    """

    def _rows(self):
        return [
            {"index": 1, "bucket": "content_changed", "label": "bad",
             "human": [clip(season=1, episode=1, start=10.0, dur=3.0)]},
            {"index": 2, "bucket": "content_changed", "label": "bad", "human": []},
            {"index": 7, "bucket": "content_changed", "label": "bad",
             "human": [clip(season=3, episode=1, start=100.0, dur=2.0)]},
            {"index": 3, "bucket": "start_shifted", "label": "good"},
            {"index": 4, "bucket": "dur_changed", "label": "good"},
            {"index": 5, "bucket": "content_changed", "label": "good",
             "human": [clip(season=2, episode=3, start=50.0, dur=4.0)]},
            {"index": 6, "bucket": "annotated_bad", "label": "skip"},
        ]

    def _verdicts(self):
        return [
            {"index": 1, "judgments": [{"rank": 1, "verdict": "not_match", "reason": "x"}]},
            {"index": 2, "judgments": [{"rank": 1, "verdict": "unsure", "reason": "x"}]},
            {"index": 7, "judgments": [{"rank": 1, "verdict": "not_match", "reason": "x"},
                                        {"rank": 2, "verdict": "match", "reason": "x"}]},
            {"index": 3, "judgments": [{"rank": 1, "verdict": "match", "reason": "x"}]},
            {"index": 4, "judgments": [{"rank": 1, "verdict": "not_match", "reason": "x"}]},
            {"index": 5, "judgments": [{"rank": 1, "verdict": "unsure", "reason": "x"}]},
            {"index": 6, "judgments": [{"rank": 1, "verdict": "unsure", "reason": "x"}]},
        ]

    def _candidates(self):
        return {
            1: [{"rank": 1, "season": 1, "episode": 1, "start": 9.5, "end": 13.2}],
            7: [{"rank": 1, "season": 9, "episode": 9, "start": 0.0, "end": 1.0},
                {"rank": 2, "season": 3, "episode": 1, "start": 99.5, "end": 102.5}],
            5: [{"rank": 1, "season": 2, "episode": 3, "start": 49.0, "end": 54.5}],
        }

    def test_四个指标手算期望值(self):
        m = r.compute_score(self._rows(), self._verdicts(), self._candidates())
        assert m["检出率"] == pytest.approx(3 / 3)
        assert m["误拒率"] == pytest.approx(1 / 3)
        assert m["unsure率"] == pytest.approx(2 / 6)
        assert m["人选复核率"] == pytest.approx(1 / 3)
        assert m["counts"] == {"bad": 3, "good": 3, "scored": 6, "content_changed_matched": 3}

    def test_skip段不进任何分母(self):
        # 2026-08-14 审计修正：skip = 人工弃权，probe 不为它生成候选（eligible
        # 只收 bad/good），score 里 verdicts 万一有它也整段忽略——它既不是
        # ground truth 也不是待测样本，判它对错无意义
        rows = [
            {"index": 1, "bucket": "annotated_bad", "label": "skip", "human": []},
            {"index": 2, "bucket": "content_changed", "label": "bad", "human": []},
        ]
        verdicts = [
            {"index": 1, "judgments": [{"rank": 1, "verdict": "unsure", "reason": "x"}]},
            {"index": 2, "judgments": [{"rank": 1, "verdict": "not_match", "reason": "x"}]},
        ]
        m = r.compute_score(rows, verdicts, {})
        assert m["counts"] == {"bad": 1, "good": 0, "scored": 1,
                               "content_changed_matched": 0}
        assert m["unsure率"] == 0.0

    def test_label为null的非unchanged段被拒收(self):
        rows = [{"index": 1, "bucket": "content_changed", "label": None, "human": []}]
        verdicts = [{"index": 1, "judgments": [{"rank": 1, "verdict": "match", "reason": "x"}]}]
        with pytest.raises(SystemExit):
            r.compute_score(rows, verdicts, {})

    def test_label为null但bucket是unchanged不报错(self):
        # 结构上允许出现（虽然 probe 正常不会为 unchanged 段生成工作单），
        # 出现时应正常记 scored/unsure，不因 label=None 崩溃
        rows = [{"index": 1, "bucket": "unchanged", "label": None, "human": []}]
        verdicts = [{"index": 1, "judgments": [{"rank": 1, "verdict": "match", "reason": "x"}]}]
        m = r.compute_score(rows, verdicts, {})
        assert m["counts"]["scored"] == 1
        assert m["counts"]["bad"] == 0 and m["counts"]["good"] == 0

    def test_verdicts里的段不在report里报错(self):
        rows = [{"index": 1, "bucket": "content_changed", "label": "bad", "human": []}]
        verdicts = [{"index": 99, "judgments": [{"rank": 1, "verdict": "match", "reason": "x"}]}]
        with pytest.raises(SystemExit):
            r.compute_score(rows, verdicts, {})

    def test_judgments缺rank1报错(self):
        rows = [{"index": 1, "bucket": "content_changed", "label": "bad", "human": []}]
        verdicts = [{"index": 1, "judgments": [{"rank": 2, "verdict": "match", "reason": "x"}]}]
        with pytest.raises(SystemExit):
            r.compute_score(rows, verdicts, {})

    def test_样本为零时指标返回None(self):
        m = r.compute_score([], [], {})
        assert m["检出率"] is None and m["误拒率"] is None
        assert m["unsure率"] is None and m["人选复核率"] is None


class TestVerdictLine:
    def test_方向成立(self):
        assert "方向成立" in r.verdict_line({"检出率": 0.60, "误拒率": 0.15})

    def test_检出率不足_方向作废(self):
        assert "方向作废" in r.verdict_line({"检出率": 0.59, "误拒率": 0.0})

    def test_误拒率超线_上下文不够(self):
        assert "上下文不够" in r.verdict_line({"检出率": 0.60, "误拒率": 0.16})

    def test_样本不足(self):
        assert "样本不足" in r.verdict_line({"检出率": None, "误拒率": None})


class TestDiffEpisodeRerun:
    """diff_episode 重跑不冲掉已标注的 label，除非 bucket 变了。"""

    def _write_pair(self, ep_dir, m_clips, a_clips):
        (ep_dir / "04-clips.json").write_text(
            json.dumps({"anime": "春物", "segments": [seg(index=1, clips=m_clips)]}),
            encoding="utf-8")
        (ep_dir / "04-clips.approved.json").write_text(
            json.dumps({"anime": "春物", "segments": [seg(index=1, clips=a_clips)]}),
            encoding="utf-8")

    def test_bucket不变时保留人工标注(self, tmp_path):
        m_clips = [clip(source="a.mkv")]
        a_clips = [clip(source="b.mkv")]
        self._write_pair(tmp_path, m_clips, a_clips)
        r.diff_episode(tmp_path)
        rows = r.load_report(tmp_path)
        assert rows[0]["bucket"] == "content_changed" and rows[0]["label"] is None

        # 人工把 label 填成 bad
        rows[0]["label"] = "bad"
        (tmp_path / "04-mismatch-report.json").write_text(
            json.dumps(rows[0], ensure_ascii=False) + "\n", encoding="utf-8")

        r.diff_episode(tmp_path)  # 重跑：clips 没变，bucket 不变
        rows2 = r.load_report(tmp_path)
        assert rows2[0]["label"] == "bad"

    def test_bucket变了重置label(self, tmp_path):
        self._write_pair(tmp_path, [clip(source="a.mkv")], [clip(source="b.mkv")])
        r.diff_episode(tmp_path)
        rows = r.load_report(tmp_path)
        rows[0]["label"] = "bad"
        (tmp_path / "04-mismatch-report.json").write_text(
            json.dumps(rows[0], ensure_ascii=False) + "\n", encoding="utf-8")

        # 重跑 clips.py 后内容变了（现在两边一致 → unchanged）
        self._write_pair(tmp_path, [clip(source="a.mkv")], [clip(source="a.mkv")])
        r.diff_episode(tmp_path)
        rows2 = r.load_report(tmp_path)
        assert rows2[0]["bucket"] == "unchanged"
        assert rows2[0]["label"] is None  # 旧的 "bad" 不能沿用到不同 bucket


class TestDiffEpisodeMissingFiles:
    def test_两份都缺返回None(self, tmp_path):
        assert r.diff_episode(tmp_path) is None

    def test_缺机器基线(self, tmp_path):
        (tmp_path / "04-clips.approved.json").write_text(
            json.dumps({"anime": "春物", "segments": []}), encoding="utf-8")
        result = r.diff_episode(tmp_path)
        assert result == {"episode": tmp_path.name, "status": "missing_machine"}

    def test_缺approved(self, tmp_path):
        (tmp_path / "04-clips.json").write_text(
            json.dumps({"anime": "春物", "segments": []}), encoding="utf-8")
        result = r.diff_episode(tmp_path)
        assert result == {"episode": tmp_path.name, "status": "missing_approved"}
