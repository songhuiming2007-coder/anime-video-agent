"""D60：标定值写入（spec `docs/dev/plans/2026-10-09-calibration-and-notes-spec.md` 一节）。

`paths.CONFIG` 指向 tmp 下复制的三份真实 config（只读复制，不改仓库里的）；
`shots.SHOTS_DIR` 指向 tmp，影响面用临时镜头表算。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pipeline import calibration as C
from pipeline import paths, shots
from pipeline.agent.session import review_tool_call
from pipeline.agent.tools import validate_pipeline_command

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"
EVID = "S01E01 整集密度表 thr8–12 中位 3.0s，切点抽检 24 个里 20 个真切点，取 10"


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "config"
    d.mkdir()
    for name in ("project.json", "scenes.json", "voice.json"):
        shutil.copy2(REPO_CONFIG / name, d / name)
    monkeypatch.setattr(paths, "CONFIG", d)
    monkeypatch.setattr(shots, "SHOTS_DIR", tmp_path / "shots")
    return d


def _load(cfg: Path, name: str) -> dict:
    return json.loads((cfg / name).read_text(encoding="utf-8"))


# ---------- 白名单与取值域 ----------


@pytest.mark.parametrize("key", [
    "visual.min_shot", "visual.tagger", "video.crf", "readings.肉体", "visual.scene_threshold._note",
    "visual.scene_threshold.a/b", "scenes._note.no_match", "script.max_chars",
])
def test_白名单外的键拒(cfg: Path, key: str):
    with pytest.raises(C.CalibrationError):
        C.plan(key, "10", EVID)


@pytest.mark.parametrize("key,value", [
    ("visual.scene_threshold.新番", "0.5"), ("visual.ccip_same.新番", "1.5"),
    ("visual.face_expand.新番", "5"), ("script.cpm", "100"), ("script.cpm", "250.5"),
    ("scenes.罪恶王冠.no_match", "-0.1"), ("titles.My Dearest", "40"),
])
def test_越界或类型不对拒(cfg: Path, key: str, value: str):
    with pytest.raises(C.CalibrationError):
        C.plan(key, value, EVID)


def test_实录太短拒(cfg: Path):
    with pytest.raises(C.CalibrationError, match="实录太短"):
        C.plan("visual.scene_threshold.新番", "10", "看着差不多")


# ---------- 写入 ----------


def test_新番阈值写入_实录追加_其余字节不动(cfg: Path):
    before = _load(cfg, "project.json")
    C.write("visual.scene_threshold.新番", "9.5", EVID)
    after = _load(cfg, "project.json")
    assert after["visual"]["scene_threshold"]["新番"] == 9.5
    note = after["visual"]["_scene_threshold_note"]
    assert note.startswith(before["visual"]["_scene_threshold_note"])
    assert "**新番 = 9.5**" in note and EVID in note and "旧值 无" in note
    # 除了这两处，其余全部不变
    after["visual"]["scene_threshold"].pop("新番")
    after["visual"]["_scene_threshold_note"] = before["visual"]["_scene_threshold_note"]
    assert after == before


def test_按集键覆盖与旧值记录(cfg: Path):
    C.write("visual.scene_threshold.EGOIST/SP04", "14", EVID)
    after = _load(cfg, "project.json")
    assert after["visual"]["scene_threshold"]["EGOIST/SP04"] == 14
    assert "旧值 15.0" in after["visual"]["_scene_threshold_note"]


@pytest.mark.parametrize("key,value,where", [
    ("script.cpm", "240", ("project.json", ("script", "cpm"), ("script", "_cpm_note"))),
    ("scenes.罪恶王冠.no_match", "0.59", ("scenes.json", ("罪恶王冠", "no_match"), ("罪恶王冠", "_no_match_note"))),
    ("titles.My Dearest", "1.5", ("voice.json", ("titles", "My Dearest"), ("titles", "_note"))),
    ("visual.ccip_margin.春物", "0.03", ("project.json", ("visual", "ccip_margin", "春物"), ("visual", "_ccip_note"))),
])
def test_各类目标写对位置(cfg: Path, key: str, value: str, where):
    fname, vkeys, nkeys = where
    C.write(key, value, EVID)
    d = _load(cfg, fname)
    cur = d
    for k in vkeys:
        cur = cur[k]
    assert cur == (int(value) if key == "script.cpm" else float(value))
    note = d
    for k in nkeys:
        note = note[k]
    assert EVID in note


def test_没有note键的番新建note(cfg: Path):
    C.write("scenes.春物.no_match", "0.55", EVID)
    assert EVID in _load(cfg, "scenes.json")["春物"]["_no_match_note"]


def test_读后被人手改_拒写(cfg: Path, monkeypatch):
    real_load = C._load
    calls = []

    def racing(path):
        got = real_load(path)
        calls.append(path)
        if len(calls) == 2:  # write 自己那次读之后、落盘之前，有人手改了文件
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        return got

    monkeypatch.setattr(C, "_load", racing)
    with pytest.raises(C.CalibrationError, match="被改过"):
        C.write("visual.scene_threshold.新番", "10", EVID)


def test_排版不标准拒写(cfg: Path):
    p = cfg / "scenes.json"
    p.write_text(json.dumps(json.loads(p.read_text(encoding="utf-8")), ensure_ascii=False, indent=4), encoding="utf-8")
    with pytest.raises(C.CalibrationError, match="排版"):
        C.plan("scenes.春物.no_match", "0.5", EVID)


def test_影响面列出要重建的集(cfg: Path, tmp_path: Path):
    sd = tmp_path / "shots"
    sd.mkdir()
    for key, thr in (("S01E01", 10.0), ("S01E02", 8.0)):
        (sd / f"新番_{key}.json").write_text(json.dumps({"meta": {"scene_threshold": thr}, "shots": []}),
                                             encoding="utf-8")
    p = C.plan("visual.scene_threshold.新番", "8", EVID)
    assert "S01E01" in p["impact"][0] and "S01E02" not in p["impact"][0]


# ---------- 接入：白名单、免卡、弹卡前预检 ----------


def _review(command: str, tmp_path: Path):
    ep = tmp_path / "data" / "episodes" / "01"
    ep.mkdir(parents=True, exist_ok=True)
    return review_tool_call("run_pipeline", {"command": command}, ep_dir=ep, scope="asset", root=None)


def test_show免卡_set弹卡且卡上有旧值新值(cfg: Path, tmp_path: Path):
    assert validate_pipeline_command("calibration show script.cpm")[0]
    assert _review("calibration show script.cpm", tmp_path).action == "allow"
    v = _review(f'calibration set visual.scene_threshold.罪恶王冠 9 --evidence "{EVID}"', tmp_path)
    assert v.action == "ask"
    card = v.request.card_text
    assert "[全局配置]" in card and "旧值 10.0 → 新值 9" in card and "config/project.json" in card


def test_set不成立在弹卡前拒(cfg: Path, tmp_path: Path):
    v = _review(f'calibration set visual.min_shot 1 --evidence "{EVID}"', tmp_path)
    assert v.action == "reject" and "白名单" in v.reason
    v = _review('calibration set script.cpm 240 --evidence "短"', tmp_path)
    assert v.action == "reject" and "实录太短" in v.reason
