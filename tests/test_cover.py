"""封面候选：去重与铺开取样。

这两个函数都改过，而且改法值得记：

- `_dedup` 初版只用感知哈希，九个候选里四个是同一场戏几秒之内的四张。
  dHash 对「同镜头内角色微动」不敏感，放宽阈值又会误合并真镜头——用错工具了。
  镜头是时间上的连续区间，按时间判才对。
- `_spread` 换掉的是两版打分函数。**候选之间没有排名是刻意的**，
  因为没有可辩护的排序依据；测试要守住的正是「不排名、铺开取」这个性质。
"""

import numpy as np

import json
import pytest

from pipeline import cover


def frame(n, src, t, sharp, tag="通篇 S02E02", hash_seed=None):
    """造一个候选。hash 默认按 n 取不同值，保证 dHash 不会误判成同一画面。"""
    rng = np.random.default_rng(hash_seed if hash_seed is not None else n)
    return {"n": n, "src": src, "t": t, "sharp": sharp, "sat": 0.3, "tag": tag,
            "file": None, "bright": 128.0,
            "hash": np.packbits(rng.integers(0, 2, 64).astype(bool))}


class TestDedup:
    def test_同集相隔太近算同一个镜头(self):
        # 按 0.5 秒抽帧，一个 4 秒的镜头出 8 张几乎一样的图。
        # 不去重的话 9 张候选可能全是同一个镜头，等于只给了人一个选择。
        cands = [frame(i, "/a.mkv", 973.0 + i * 0.5, sharp=100 + i) for i in range(8)]
        assert len(cover._dedup(cands)) == 1

    def test_同集隔得远是不同镜头(self):
        cands = [frame(1, "/a.mkv", 100.0, 200), frame(2, "/a.mkv", 200.0, 100)]
        assert len(cover._dedup(cands)) == 2

    def test_不同源文件即使时间相同也不合并(self):
        cands = [frame(1, "/a.mkv", 100.0, 200), frame(2, "/b.mkv", 100.0, 100)]
        assert len(cover._dedup(cands)) == 2

    def test_同一镜头保留最清晰的那张(self):
        cands = [frame(1, "/a.mkv", 100.0, 50), frame(2, "/a.mkv", 101.0, 900),
                 frame(3, "/a.mkv", 102.0, 300)]
        kept = cover._dedup(cands)
        assert len(kept) == 1 and kept[0]["sharp"] == 900

    def test_画面几乎相同时靠_dHash_合并(self):
        # 管的是另一回事：同一个镜头在片中**不同时刻**复现（正反打来回切），
        # 时间判不出来，得靠画面判。
        same = np.packbits(np.zeros(64, dtype=bool))
        a = frame(1, "/a.mkv", 100.0, 200); a["hash"] = same
        b = frame(2, "/a.mkv", 900.0, 100); b["hash"] = same
        assert len(cover._dedup([a, b])) == 1


class TestSpread:
    def test_取够_KEEP_张(self):
        cands = [frame(i, "/a.mkv", i * 30.0, 100 + i, tag=f"段{i}") for i in range(40)]
        assert len(cover._spread(cands)) == cover.KEEP

    def test_候选不足就全给(self):
        cands = [frame(i, "/a.mkv", i * 30.0, 100, tag=f"段{i}") for i in range(4)]
        assert len(cover._spread(cands)) == 4

    def test_名场面留两个坑(self):
        # 笔记里人工标注过有张力的时刻，来源本身就带判断，比盲抽强。
        famous = [frame(i, "/a.mkv", i * 30.0, 500 + i, tag="名场面 S02E02") for i in range(5)]
        rest = [frame(100 + i, "/b.mkv", i * 30.0, 100, tag=f"段{i}") for i in range(30)]
        out = cover._spread(famous + rest)
        assert sum(1 for c in out if c["tag"].startswith("名场面")) >= 2

    def test_不按清晰度排名(self):
        # **这是刻意的。** 拉普拉斯方差测的是「画面里有多少细节」不是「画面有多好」，
        # 杂乱背景细节最多，按它排专挑最杂乱的画面——实测选出了货架和书架，
        # 主角一个都没有。它是准入门槛，不是排序依据。
        cands = [frame(i, "/a.mkv", i * 30.0, sharp=i * 10, tag=f"段{i:02d}")
                 for i in range(30)]
        out = cover._spread(cands)
        sharps = [c["sharp"] for c in out]
        assert sharps != sorted(sharps, reverse=True), "候选不该按清晰度降序"

    def test_覆盖全片而不是挤在一处(self):
        # 铺开取样的全部意义：给人一份有代表性的样本
        cands = [frame(i, "/a.mkv", i * 30.0, 100, tag=f"段{i:02d}") for i in range(30)]
        tags = {c["tag"] for c in cover._spread(cands)}
        assert len(tags) >= cover.KEEP - 1     # 几乎每张来自不同段落

    def test_全是名场面时也不越界(self):
        cands = [frame(i, "/a.mkv", i * 30.0, 100, tag="名场面 S02E02") for i in range(3)]
        assert len(cover._spread(cands)) <= cover.KEEP


class TestTopicEpisodes:
    """`01-topic.md` 里点名的集号。

    两个字段分开读，因为它们本来是两件事：

    - `锚点`：稿子引用了哪几集。**内容判据**，稿件机检也看它
    - `封面集`：封面该从哪几集取样。可选

    2026-07-30 分开的：给「雪乃最适合大老师」配封面时最好的素材在 S3E12
    （她告白那一集），而稿子一句都没引它。往 `锚点` 里塞会污染那个字段的含义。
    """

    def _topic(self, tmp_path, text):
        f = tmp_path / "01-topic.md"
        f.write_text(text, encoding="utf-8")
        return f

    def test_两个字段都读且封面集在前(self, tmp_path):
        f = self._topic(tmp_path, "锚点: S1E01 / S2E02\n封面集: S3E12\n")
        assert cover._topic_episodes(f) == ["S01E01", "S02E02", "S03E12"]

    def test_没有封面集也能跑(self, tmp_path):
        # 向后兼容：老的 topic 文件没这个字段，行为与从前一致
        f = self._topic(tmp_path, "锚点: S1E01 / S2E02\n")
        assert cover._topic_episodes(f) == ["S01E01", "S02E02"]

    def test_OVA_记作该季_E00(self, tmp_path):
        # 与 ingest.phase0 的编号一致。原先正则是 `S(\d)E(\d{1,2})`，
        # 「S1 OVA」匹配不上，那一集**静默地不进封面池**——
        # 2026-07-30 实测漏掉了婚活特辑，而它是本期论点的出处。
        f = self._topic(tmp_path, "锚点: S1 OVA 01:06 / S1E01 08:05\n")
        assert cover._topic_episodes(f) == ["S01E00", "S01E01"]

    def test_笔记里的_S1OVA_写法也认(self, tmp_path):
        f = self._topic(tmp_path, "锚点: S1OVA / S3OVA\n")
        assert cover._topic_episodes(f) == ["S01E00", "S03E00"]

    def test_重复的集只算一次(self, tmp_path):
        f = self._topic(tmp_path, "锚点: S1E01 08:05 / S1E01 16:16\n封面集: S1E01\n")
        assert cover._topic_episodes(f) == ["S01E01"]

    def test_时间码不会被误读成集号(self, tmp_path):
        # 锚点行里全是 `08:05+16:16+17:04–17:09` 这样的时间码
        f = self._topic(tmp_path, "锚点: S1E01 08:05+16:16+17:04–17:09\n")
        assert cover._topic_episodes(f) == ["S01E01"]

    def test_别的字段不参与(self, tmp_path):
        # 「张力」那行提到集号是行文需要，不该被当成取样清单
        f = self._topic(tmp_path,
                        "张力: S3E12 那一集他终于说了\n锚点: S1E01\n")
        assert cover._topic_episodes(f) == ["S01E01"]

    def test_零填充季度号也认(self, tmp_path):
        # 旧正则 `S(\d)` 只认单数字季度，`S01E15` 静默匹配不上——
        # 2026-08-31 校条祭期实测：名场面/通篇两路取样全空，封面 FAIL。
        f = self._topic(tmp_path,
                        "锚点: S01E15 16:39\n封面集: S01E15 (备选 S01E22)\n")
        assert cover._topic_episodes(f) == ["S01E15", "S01E22"]

    def test_名场面通路的笔记表头认零填充集号(self, tmp_path, monkeypatch):
        # 同族 bug：笔记分集表头正则 `S(\d)E` 只认单数字季度，`### S01E15`
        # 匹配不上 → 名场面通路静默失效（该函数注释里早就埋着这条教训）。
        (tmp_path / "library" / "notes").mkdir(parents=True)
        (tmp_path / "library" / "notes" / "测试番.md").write_text(
            "## 分集速查\n\n### S01E15 标题\n\n| 16:39 | 台词 |\n", encoding="utf-8")
        topic = tmp_path / "01-topic.md"
        topic.write_text("封面集: S01E15\n", encoding="utf-8")
        monkeypatch.setattr(cover.paths, "DATA", tmp_path)
        monkeypatch.setattr(cover, "load_sources",
                            lambda anime: {"S01E15": {"path": "/x.mkv", "duration": 1500.0}})
        pts = cover._notes_points("测试番", topic)
        # 999.5 = 16×60+39（台词起点）+ 0.5（防闪烁后移）
        assert pts == [("/x.mkv", 999.5, "名场面 S01E15")]


class TestEmptyPool:
    """候选池筛空 → 失败，不静默出 0 张候选、退出码 0（2026-08-16 审计 2-14）。

    角色硬过滤为空早已显式失败（_by_character）；无过滤的空池是同类
    「跳过不是通过」却放行——整集过暗/糊或取样源全失效都表现成这个形态。
    """

    def _ep(self, tmp_path):
        ep = tmp_path / "ep"
        ep.mkdir(parents=True, exist_ok=True)
        (ep / "04-clips.approved.json").write_text(
            json.dumps({"segments": [], "total_duration": 0.0}), encoding="utf-8")
        (ep / "01-topic.md").write_text("类型: 杂谈\n", encoding="utf-8")
        return ep

    def test_全部抽帧失败_候选池筛空报错(self, tmp_path, monkeypatch):
        ep = self._ep(tmp_path)
        monkeypatch.setattr(cover.paths, "conf",
                            lambda d, default=None: "春物" if d == "anime.default" else default)
        monkeypatch.setattr(cover, "_sample_points",
                            lambda data, by_path=None: [("/x.mkv", 5.0, "S01E01")])
        monkeypatch.setattr(cover, "_notes_points", lambda anime, topic: [])
        monkeypatch.setattr(cover, "_episode_points", lambda anime, topic: [])
        monkeypatch.setattr(cover, "_grab", lambda src, t, dest: None)   # 抽帧全失败
        with pytest.raises(SystemExit, match="筛空"):
            cover.build(ep)

    def test_全部过筛失败_同样报错(self, tmp_path, monkeypatch):
        # 抽帧成功但全被亮度/清晰度筛掉 → cands 空 → 同一守卫
        import PIL.Image

        def fake_grab(src, t, dest):
            PIL.Image.new("RGB", (64, 64)).save(dest, quality=50)
            return dest
        ep = self._ep(tmp_path)
        monkeypatch.setattr(cover.paths, "conf",
                            lambda d, default=None: "春物" if d == "anime.default" else default)
        monkeypatch.setattr(cover, "_sample_points",
                            lambda data, by_path=None: [("/x.mkv", 5.0, "S01E01")])
        monkeypatch.setattr(cover, "_notes_points", lambda anime, topic: [])
        monkeypatch.setattr(cover, "_episode_points", lambda anime, topic: [])
        monkeypatch.setattr(cover, "_grab", fake_grab)
        monkeypatch.setattr(cover, "_metrics",
                            lambda f: {"bright": 1.0, "sharp": 0.0})    # 黑且糊
        with pytest.raises(SystemExit, match="筛空"):
            cover.build(ep)

    def test_没有番名配置时显式报错(self, tmp_path, monkeypatch):
        # 番名检查在所有文件检查之前——config 删掉 anime.default 不许
        # 静默跳回「春物」（2026-08-16 审计 2-16）
        monkeypatch.setattr(cover.paths, "conf", lambda d, default=None: default)
        with pytest.raises(SystemExit, match="番名"):
            cover.build(tmp_path / "ep")


class TestSamplePointsSchema:
    """片段 schema 兜底（2026-08-18 复盘③）：人审手工补进 04-clips 的片段
    可能只有 source/start/dur，旧实现裸取 c['season'] 直接 KeyError，
    整页候选崩掉。"""

    PLAN = {"segments": [{"index": 3, "clips": [
        {"season": 2, "episode": 5, "source": "/a.mkv", "start": 10.0, "dur": 0.3},
        {"source": "data/library/raw/某番/[07].mkv", "start": 0.0, "dur": 0.3},
    ]}]}

    def test_机器产物照常带集号(self):
        pts = cover._sample_points(self.PLAN)
        assert pts[0][2].startswith("段3 S02E05")

    def test_手写片段从片源登记表反查(self):
        by_path = {"data/library/raw/某番/[07].mkv": "S01E07"}
        pts = cover._sample_points(self.PLAN, by_path)
        assert pts[1][2].startswith("段3 S01E07")

    def test_反查不到退文件名不KeyError(self):
        pts = cover._sample_points(self.PLAN)   # 不给 by_path
        assert "[07].mkv" in pts[1][2]
