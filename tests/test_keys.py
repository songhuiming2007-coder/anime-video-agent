"""D127：SP 集键满 99 进三位。纯函数 + 每个解析点各一条 SP100 用例 + 静态守卫（全仓不许再各写一份两位正则）。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pipeline import calibration, check_script, clips, cloud, render, vindex
from pipeline.keys import SP_MAX, key_sort, sp_key

ROOT = Path(__file__).resolve().parents[1]


class TestPure:
    def test_sp_key_两位补零_三位原样(self):
        assert [sp_key(n) for n in (1, 9, 99, 100, 999)] == ["SP01", "SP09", "SP99", "SP100", "SP999"]

    @pytest.mark.parametrize("bad", [0, SP_MAX + 1, -3])
    def test_sp_key_越界抛错(self, bad):
        with pytest.raises(ValueError):
            sp_key(bad)

    def test_key_sort_按数字排(self):
        keys = ["SP100", "SP11", "S01E02", "SP10", "SP09", "S00E01", "weird", "SP99"]
        assert sorted(keys, key=key_sort) == ["S00E01", "S01E02", "SP09", "SP10", "SP11", "SP99", "SP100", "weird"]


class TestParseSites:
    """施工图第二节表里的每一处解析点。"""

    def test_vindex_parse_key(self):
        assert vindex._parse_key("SP100") == (None, 100)
        assert vindex._parse_key("SP05") == (None, 5)
        for bad in ("SP001", "SP5", "SP1000"):
            with pytest.raises(SystemExit):
                vindex._parse_key(bad)

    def test_cloud_lib_target(self):
        assert cloud.validate_lib_target("supercell", ["SP99,SP100", "SP105"]) == ["SP99", "SP100", "SP105"]
        with pytest.raises(ValueError):
            cloud.validate_lib_target("supercell", ["SP1000"])

    def test_calibration_按集键覆盖(self):
        assert calibration.parse_target("visual.scene_threshold.supercell/SP100").name == "supercell/SP100"
        with pytest.raises(calibration.CalibrationError):
            calibration.parse_target("visual.scene_threshold.supercell/SP1000")

    def test_clips_锚点认SP100(self):
        a = clips._parse_anchor("supercell SP100 00:12-00:18", 3, ["supercell"], ["supercell"])
        assert a["anime"] == "supercell" and a["season"] is None and a["episode"] == 100
        assert clips._ANCHOR.match("SP1000 00:12") is None

    def test_check_script_锚点与集号(self):
        assert check_script.ANCHOR_TC.match("supercell SP100 00:12-00:18").group("sp") == "100"
        assert check_script._norm_ep("SP100") == "SP100"
        assert check_script._norm_ep("SP1") == "SP01"            # 人写的地方仍容忍一位
        assert check_script._norm_ep("SP100-ryo") == "SP100"     # 语义后缀

    def test_check_script_复用声明不截断(self):
        text = "## 段落 6\n\n画面：\n  锚点: supercell SP100 00:01\n  复用: supercell SP100（人定：唯一物证）\n"
        assert check_script.parse_reuse(text) == {"6": ["supercell SP100"]}
        assert check_script._REUSE_ITEM.fullmatch("supercell SP100").group("sp") == "100"
        assert check_script._REUSE_ITEM.fullmatch("supercell SP1000") is None

    def test_render_avsync取片源归一(self, tmp_path):
        (tmp_path / "01-topic.md").write_text("番: supercell\n", encoding="utf-8")
        assert render._resolve_tag(tmp_path, "SP100") == ("supercell", "SP100")
        assert render._resolve_tag(tmp_path, "supercell SP100-ryo") == ("supercell", "SP100")
        assert render._resolve_tag(tmp_path, "SP5") == ("supercell", "SP05")


def test_vindex_caption_keys按集号排(tmp_path):
    for k in ("SP100", "SP11", "SP10", "SP99"):
        (tmp_path / f"supercell_{k}.captions.json").write_text("[]", encoding="utf-8")
    assert vindex.caption_keys("supercell", tmp_path) == ["SP10", "SP11", "SP99", "SP100"]


#: 写死位数的 SP 正则一律走 pipeline/keys.py 的常量（D127）。生成侧 `f"SP{n:02d}"` 不在此列（三位原样输出）。
_FORBIDDEN = re.compile(r"SP\(?\\d\{|SP\(\?P<sp>\\d|SP\[0-9\]|SP\\d\\d")


def test_静态守卫_pipeline里不再各写一份SP位数():
    hits = []
    for p in sorted((ROOT / "pipeline").rglob("*.py")):
        if p.name == "keys.py":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _FORBIDDEN.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not hits, "SP 集键位数要引用 pipeline.keys 的 SP_NUM / SP_NUM_LOOSE：\n" + "\n".join(hits)
