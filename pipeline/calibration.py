"""标定值写入（D60）：人看测量结果拍板，agent 经人审卡把值和标定实录写进 config。

    python -m pipeline.calibration show <键路径>                         # 只读
    python -m pipeline.calibration set <键路径> <值> --evidence "<标定实录>"   # 弹卡写入

AGENTS.md Code Freeze 的第二个窄口子（第一个是读音表，D51）：只放下表这些**按番 / 按条件
标定的数**，其余 config 照旧只读。写法同 `corrections.write_global_entry`：往返格式校验
（写回不能动到别的字节）、读后 digest 防并发、原子替换；实录追加进对应的 `_note`，
标定证据和值落在同一个文件里（ADR-0003「这里的数全部实测得来」）。

| 键路径 | 文件 | 取值域 |
|---|---|---|
| `visual.scene_threshold.<番>`、`visual.scene_threshold.<番>/SPxx` | project.json | 1–50 |
| `visual.ccip_same.<番>`、`visual.ccip_margin.<番>` | project.json | 0–1 |
| `visual.face_expand.<番>` | project.json | 1–3 |
| `script.cpm` | project.json | 150–400 |
| `scenes.<番>.no_match` | scenes.json | 0–1 |
| `titles.<歌名>` | voice.json | 0.1–30 秒 |
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import paths

EVIDENCE_MIN_CHARS = 20


class CalibrationError(Exception):
    """写不进去的原因（不合白名单、越界、格式或并发问题），原样给人看。"""


@dataclass(frozen=True)
class Rule:
    file: str                      # config/ 下的文件名
    prefix: str                    # 键路径前缀
    suffix: str                    # 键路径后缀（scenes 的 `.no_match`）
    container: tuple[str, ...]     # 值所在的字典路径；名字为空时（script.cpm）最后一段即键
    note: tuple[str, ...] | None   # 实录追加到的 `_note` 路径；None = 追加到 `<容器>/<名字>` 同级的 note_key
    note_key: str | None           # scenes 的 `<番>._no_match_note`
    lo: float
    hi: float
    integer: bool = False
    named: bool = True             # False：键路径就是完整键（script.cpm）
    allow_sp: bool = False         # 名字可带 `/SPxx`（scene_threshold 的按集键覆盖）


RULES: tuple[Rule, ...] = (
    Rule("project.json", "visual.scene_threshold.", "", ("visual", "scene_threshold"),
         ("visual", "_scene_threshold_note"), None, 1, 50, allow_sp=True),
    Rule("project.json", "visual.ccip_same.", "", ("visual", "ccip_same"), ("visual", "_ccip_note"), None, 0, 1),
    Rule("project.json", "visual.ccip_margin.", "", ("visual", "ccip_margin"), ("visual", "_ccip_note"), None, 0, 1),
    Rule("project.json", "visual.face_expand.", "", ("visual", "face_expand"), ("visual", "_ccip_note"), None, 1, 3),
    Rule("project.json", "script.cpm", "", ("script",), ("script", "_cpm_note"), None, 150, 400,
         integer=True, named=False),
    Rule("scenes.json", "scenes.", ".no_match", (), None, "_no_match_note", 0, 1),
    Rule("voice.json", "titles.", "", ("titles",), ("titles", "_note"), None, 0.1, 30),
)

#: 名字不许以 `_` 开头（下划线键是注释，写进去会覆盖 `_note`）、不许带换行与路径分隔符。
_NAME = re.compile(r"^[^_./\s][^/\n\r]*$")
_NAME_SP = re.compile(r"^([^_./\s][^/\n\r]*?)(/SP\d{2})?$")


@dataclass(frozen=True)
class Target:
    rule: Rule
    name: str        # 番 / 番/SPxx / 歌名；script.cpm 为 ""

    @property
    def path(self) -> Path:
        return paths.CONFIG / self.rule.file

    def key_path(self) -> tuple[str, ...]:
        if not self.rule.named:
            return self.rule.container + ("cpm",)
        if self.rule.file == "scenes.json":
            return (self.name, "no_match")
        return self.rule.container + (self.name,)

    def note_path(self) -> tuple[str, ...]:
        if self.rule.note is not None:
            return self.rule.note
        return (self.name, self.rule.note_key or "_note")


def parse_target(key: str) -> Target:
    key = key.strip()
    for rule in RULES:
        if not rule.named:
            if key == rule.prefix:
                return Target(rule, "")
            continue
        if key.startswith(rule.prefix) and key.endswith(rule.suffix):
            name = key[len(rule.prefix): len(key) - len(rule.suffix) if rule.suffix else None]
            ok = (_NAME_SP if rule.allow_sp else _NAME).match(name)
            if not name or not ok or (rule.allow_sp and "/" in name and not ok.group(2)):
                raise CalibrationError(f"名字「{name}」不合用：不能空、不能以 _ 开头（那是注释键）、不能含换行或 /"
                                       + ("（按集键覆盖写成 <番>/SPxx）" if rule.allow_sp else ""))
            return Target(rule, name)
    allowed = "、".join(r.prefix + ("<名>" if r.named else "") + r.suffix for r in RULES)
    raise CalibrationError(f"键路径「{key}」不在标定白名单内（AGENTS.md Code Freeze 例外只放：{allowed}）")


def parse_value(target: Target, raw: str) -> float | int:
    try:
        v = float(raw)
    except ValueError:
        raise CalibrationError(f"值「{raw}」不是数") from None
    r = target.rule
    if not r.lo <= v <= r.hi:
        raise CalibrationError(f"值 {raw} 超出取值域 {r.lo:g}–{r.hi:g}")
    if r.integer:
        if v != int(v):
            raise CalibrationError(f"{r.prefix} 只收整数，收到 {raw}")
        return int(v)
    return v


def _load(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    cfg = json.loads(raw.decode("utf-8"))
    if _dump(cfg, raw).encode("utf-8") != raw:
        raise CalibrationError(f"config/{path.name} 的排版与标准 JSON 输出不一致，自动写入会改动其他行；请人手改。")
    return cfg, raw


def _dump(cfg: dict, original: bytes) -> str:
    text = json.dumps(cfg, ensure_ascii=False, indent=2)
    return text + "\n" if original.endswith(b"\n") else text


def _get(cfg: dict, keys: tuple[str, ...]):
    cur = cfg
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _set(cfg: dict, keys: tuple[str, ...], value) -> None:
    cur = cfg
    for k in keys[:-1]:
        nxt = cur.setdefault(k, {})
        if not isinstance(nxt, dict):
            raise CalibrationError(f"{'.'.join(keys)} 的上一层不是对象，结构不认识，请人手改")
        cur = nxt
    cur[keys[-1]] = value


def _evidence_line(target: Target, value, old, evidence: str) -> str:
    label = target.name or ".".join(target.key_path())
    return (f"\n\n**{label} = {value:g}**（{date.today().isoformat()}，经 `calibration set` 人审卡写入；"
            f"旧值 {old if old is not None else '无'}）：{evidence.strip()}")


def plan(key: str, raw_value: str, evidence: str) -> dict:
    """不落盘的写入计划：旧值、新值、追加的实录、影响面。"""
    target = parse_target(key)
    value = parse_value(target, raw_value)
    if len(evidence.strip()) < EVIDENCE_MIN_CHARS:
        raise CalibrationError(f"标定实录太短（{len(evidence.strip())} 字，至少 {EVIDENCE_MIN_CHARS} 字）："
                               "写清测了什么、在哪集、看到什么、为什么取这个数")
    cfg, _raw = _load(target.path)
    old = _get(cfg, target.key_path())
    if isinstance(old, dict):
        raise CalibrationError(f"{key} 现在是一个对象，不是一个数——结构不认识，请人手改")
    return {"target": target, "value": value, "old": old,
            "note": _evidence_line(target, value, old, evidence), "impact": impact(target, value)}


def impact(target: Target, value) -> list[str]:
    """改这个数会让哪些已有产物过期（卡面与命令输出都给人看）。"""
    p = target.rule.prefix
    if p == "visual.scene_threshold.":
        from . import shots

        anime, _, key = target.name.partition("/")
        pattern = f"{anime}_{key}.json" if key else f"{anime}_*.json"
        stale = []
        for f in sorted(shots.SHOTS_DIR.glob(pattern)) if shots.SHOTS_DIR.is_dir() else ():
            try:
                thr = json.loads(f.read_text(encoding="utf-8"))["meta"]["scene_threshold"]
            except (OSError, ValueError, KeyError):
                continue
            if abs(thr - value) > 1e-9:
                stale.append(f.stem.removeprefix(f"{anime}_"))
        if not stale:
            return ["已有镜头表与新值一致（或还没建），无需重建"]
        return [f"已建镜头表里 {len(stale)} 集用的是旧阈值，load() 会判过期，要 shots rebuild："
                + "、".join(stale[:12]) + ("…" if len(stale) > 12 else "")]
    if p in ("visual.ccip_same.", "visual.ccip_margin.", "visual.face_expand."):
        return ["改了要从 faces detect 起重跑这部番的整条人脸链（_ccip_note）"]
    if p == "script.cpm":
        return ["影响所有期的字数 / 时长估算与 check_script、qc 的时长判据"]
    if p == "scenes.":
        return ["影响这部番的画面检索门槛（no_match）与补丁池默认 floor"]
    return ["影响含这个歌名的段的时长估算与回读豁免"]


def write(key: str, raw_value: str, evidence: str) -> dict:
    p = plan(key, raw_value, evidence)
    target: Target = p["target"]
    cfg, raw = _load(target.path)
    _set(cfg, target.key_path(), p["value"])
    note_keys = target.note_path()
    _set(cfg, note_keys, str(_get(cfg, note_keys) or "") + p["note"])
    text = _dump(cfg, raw)
    if hashlib.sha256(target.path.read_bytes()).digest() != hashlib.sha256(raw).digest():
        raise CalibrationError(f"config/{target.path.name} 在读取之后被改过（有人在手改？），本次未写入；重新提议即可。")
    tmp = target.path.with_name(target.path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target.path)
    return p


def preview(argv: list[str]) -> tuple[bool, list[str]]:
    """弹卡前预检（不落盘）：(能否写入, 卡面行)。`argv` 为模块名之后的参数。"""
    try:
        ns = _parser().parse_args(argv)
    except SystemExit:
        return False, [f"参数不合用法：{' '.join(argv)}（见 python -m pipeline.calibration -h）"]
    if ns.cmd != "set":
        return True, []
    try:
        p = plan(ns.key, ns.value, ns.evidence)
    except (CalibrationError, OSError, ValueError) as exc:
        return False, [str(exc)]
    t: Target = p["target"]
    return True, [f"写入：config/{t.rule.file} 的 {'.'.join(t.key_path())}（全局配置，所有期生效）",
                  f"旧值 {p['old'] if p['old'] is not None else '无'} → 新值 {p['value']:g}",
                  f"实录追加进 {'.'.join(t.note_path())}：{p['note'].strip()[:200]}",
                  *(f"影响：{line}" for line in p["impact"])]


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m pipeline.calibration", description="标定值写入（D60）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="写一个标定值（弹人审卡）")
    s.add_argument("key")
    s.add_argument("value")
    s.add_argument("--evidence", required=True, help="标定实录：测了什么、在哪集、看到什么、为什么取这个数")
    sh = sub.add_parser("show", help="看一个标定值与它的实录（只读）")
    sh.add_argument("key")
    return ap


def main(argv: list[str] | None = None) -> int:
    ns = _parser().parse_args(argv)
    try:
        if ns.cmd == "show":
            target = parse_target(ns.key)
            cfg, _ = _load(target.path)
            print(f"{ns.key} = {_get(cfg, target.key_path())}")
            note = str(_get(cfg, target.note_path()) or "")
            print(f"实录（{'.'.join(target.note_path())}，末 400 字）：{note[-400:] if note else '无'}")
            return 0
        p = write(ns.key, ns.value, ns.evidence)
    except CalibrationError as exc:
        raise SystemExit(f"FAIL {exc}") from None
    t: Target = p["target"]
    print(f"OK config/{t.rule.file} {'.'.join(t.key_path())}：{p['old']} → {p['value']:g}（实录已追加）")
    for line in p["impact"]:
        print(f"   {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
