"""`python -m pipeline.agent.probe_efforts`：对一个 base_url 的 /v1 全量扫 effort 支持集，写回 compact.json。

D155：把「每模型 effort 哪几档真生效」从手工记变成探针自动测。以后接新代理跑一遍：

    python -m pipeline.agent.probe_efforts                      # 用生效配置的 base_url
    python -m pipeline.agent.probe_efforts http://127.0.0.1:8317/v1   # 指定别的代理

判定逻辑（实测口径，见 D155）：
- 发 `reasoning_effort=<档>` 给每模型；非流式短输出。
- 200 且 usage/reasoning 字段、或响应 reasoning_content 出现 → 记该档「可能生效」；
- 4xx 且错误消息自报合法集（"valid levels: a, b, c" / "unknown level"）→ 自报集即实测合法集；
- 200 但 thinking 字段恒不出现、非法值（turbo/bogus）也照收 → 判定「被吞」→ 记 `["none"]`。
- 探针只写实测结论；`effort_map`/`effort_param` 由人或后续 D155 数据面补（CPA 修好后探针会测出真支持集）。

注意：发的是真实请求、走计费/配额；每个模型 11 个请求（baseline + 9 档 + turbo + bogus）。
`--dry-run` 只打印不写盘。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from typing import Any

from pipeline import paths
from pipeline.agent import model_switch

LEVELS = [e for e in model_switch.EFFORT_ORDER if e != "none"]  # none 语义 = 不发档，不算「支持集」
PROBE_LEVELS = ["none"] + LEVELS + ["max", "turbo", "bogus"]    # 探测全集：合法集 + 越界值
PROMPT = [{"role": "user", "content": "What is 17*23? Reply with just the number."}]
VALID_RE = re.compile(r"valid levels?:\s*([a-z,\s]+)", re.IGNORECASE)


def _post(base: str, model: str, extra: dict[str, Any], timeout: int = 60) -> tuple[int, dict[str, Any]]:
    body = {"model": model, "messages": PROMPT, "max_tokens": 4096, "stream": False}
    body.update(extra)
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")[:1500]
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = {"raw": raw}
        return e.code, detail
    except (urllib.error.URLError, TimeoutError, OSError) as e:  # 网络错误：如实返回 -1
        return -1, {"exc": repr(e)}


def _observed(j: dict[str, Any]) -> bool:
    """这份 200 回包里 thinking 真出现了吗（reasoning_tokens>0 或 reasoning_content 非空）。"""
    u = j.get("usage") or {}
    det = u.get("completion_tokens_details") or {}
    if isinstance(det.get("reasoning_tokens"), int) and det["reasoning_tokens"] > 0:
        return True
    msg = (j.get("choices") or [{}])[0].get("message") or {}
    rc = msg.get("reasoning_content")
    return isinstance(rc, str) and bool(rc.strip())


def _valid_levels_from_error(j: dict[str, Any]) -> list[str] | None:
    err = j.get("error") or j
    msg = (err.get("message") or err.get("raw") or "") if isinstance(err, dict) else str(err)
    m = VALID_RE.search(str(msg))
    if m:
        return [x.strip() for x in m.group(1).split(",") if x.strip()]
    if "unknown level" in str(msg).lower() or "not supported" in str(msg).lower():
        return []  # 模型在校验但这条没给合法集——证明吃 effort，支持集留空等人补
    return None


def probe_model(base: str, model: str) -> dict[str, Any]:
    """返回 {efforts|["none"], note, detail}。

    判定（与 D155 手工实测同口径）：
    - thinking 字段在**部分档**出现、在别的档不出现 → 档位开关真生效；
    - thinking **恒出现**（none/bogus/turbo 都 200 且都有）→ effort 根本没翻译，被吞；
    - 非法值报「valid levels: ...」→ 模型在校验，自报集即实测集。
    """
    detail: dict[str, Any] = {}
    seen_at: list[str] = []
    claimed: set[str] | None = None
    all_200 = True
    illegal_accepted = False   # turbo/bogus 也 200 → 参数根本没被校验（被吞的硬证据）
    quota_blocked = False      # 429/冷却 → 本次测不了，别覆盖旧值
    for lv in PROBE_LEVELS:
        status, j = _post(base, model, {"reasoning_effort": lv})
        seen = _observed(j) if status == 200 else False
        detail[lv] = {"status": status, "thinking_seen": seen}
        if status in (429, -1):
            quota_blocked = True
        if seen:
            seen_at.append(lv)
        if status != 200:
            all_200 = False
            got = _valid_levels_from_error(j)
            if got:
                claimed = set(got)
            detail[lv]["err"] = str((j.get("error") or {}).get("message") or j.get("raw") or j)[:200]
        elif lv in ("turbo", "bogus"):
            illegal_accepted = True
    if quota_blocked:
        return {"efforts": None, "note": "配额冷却/网络错误，本次测不了（保留旧值）", "detail": detail}
    # 档位开关是否真改变输出：有的档出现 thinking、有的档不出现 → 真生效
    real_levels = [lv for lv in model_switch.EFFORT_ORDER if lv != "none"]
    gated = any(detail.get(lv, {}).get("thinking_seen") for lv in real_levels) and \
            any(not detail.get(lv, {}).get("thinking_seen") for lv in ["none", "low", "minimal"])

    if claimed:
        efforts = ["none"] + [lv for lv in model_switch.EFFORT_ORDER if lv in claimed]
        return {"efforts": efforts, "note": f"非法值自报合法集（thinking 出现在 {seen_at}）", "detail": detail}
    if not all_200 and not illegal_accepted:
        # 有报错但没给合法集（claude 的 "unknown level"）：模型在校验 = 吃 effort，
        # 支持集取探测的全档（短题下 auto/none 不触发 thinking 是正常，不算不支持）。
        efforts = ["none"] + [lv for lv in real_levels if detail.get(lv, {}).get("status") == 200]
        return {"efforts": efforts, "note": f"非法值报错（校验存在、无合法集自报）；200 的档记为支持集（thinking 出现在 {seen_at}）", "detail": detail}
    if illegal_accepted:
        # turbo/bogus 都 200 → 参数没进上游校验，不管 thinking 出没出现都算被吞
        return {"efforts": ["none"],
                "note": f"非法值 turbo/bogus 也 200 照收（thinking_seen={seen_at}）→ CPA 未翻译/模型不吃，判定被吞",
                "detail": detail}
    if gated or seen_at:
        efforts = ["none"] + [lv for lv in real_levels if detail.get(lv, {}).get("thinking_seen")]
        return {"efforts": efforts, "note": f"thinking 字段随档出现（{seen_at}）", "detail": detail}
    return {"efforts": ["none"], "note": f"全档未见 thinking（all_200={all_200}）", "detail": detail}


def _list_models(base: str) -> list[str]:
    req = urllib.request.Request(base.rstrip("/") + "/models")
    with urllib.request.urlopen(req, timeout=15) as r:
        body = json.loads(r.read().decode())
    return sorted({m["id"] for m in body.get("data", []) if isinstance(m, dict) and m.get("id")})


def run(base_url: str, *, dry_run: bool, models_only: list[str] | None) -> int:
    base = base_url
    if not base:
        from pipeline.agent.llm import configured_base_url
        base = configured_base_url()
    if not base:
        print("probe_efforts: 没有可用 base_url（配置也没读到）", file=sys.stderr)
        return 2
    try:
        models = models_only or _list_models(base)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        print(f"probe_efforts: 取模型列表失败 {e}", file=sys.stderr)
        return 2
    print(f"probe_efforts: {base} 共 {len(models)} 个模型", file=sys.stderr)

    cfg_path = paths.CONFIG / "agent" / "compact.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.setdefault("models", {})
    changed: list[str] = []
    for m in models:
        res = probe_model(base, m)
        efforts = res["efforts"]
        if efforts is None:  # 配额冷却/网络错 → 不动这条
            print(f"  {m:38s} SKIP    {res['note']}", file=sys.stderr)
            continue
        entry = cfg["models"].setdefault(m, {})
        # 探针不管 window：新模型条目只有 efforts/_source，window 缺省 = 「表里没有窗口」（D155 校验允许缺省）
        old = entry.get("efforts")
        entry["efforts"] = efforts
        from datetime import date
        note = f"probe_efforts {date.today().isoformat()}: {res['note']}"
        src = entry.get("_source", "")
        entry["_source"] = (src.rstrip("。") + "。" if src else "") + note
        mark = "CHANGED" if old != efforts else "same"
        if old != efforts:
            changed.append(m)
        print(f"  {m:38s} {mark:7s} efforts={efforts}", file=sys.stderr)
        if res["note"].startswith("全档 200") or "被吞" in res["note"]:
            print(f"      ↳ 疑似被吞：{res['note']}", file=sys.stderr)
    if dry_run:
        print("dry-run：不写盘", file=sys.stderr)
        return 0
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"写回 {cfg_path}（{len(changed)} 个模型 efforts 有变化: {changed}）", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline.agent.probe_efforts")
    p.add_argument("base_url", nargs="?", default=None, help="缺省读生效配置的 base_url")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--model", action="append", dest="models", help="只测某几个模型（可多次）")
    args = p.parse_args(argv)
    return run(args.base_url, dry_run=args.dry_run, models_only=args.models)


if __name__ == "__main__":
    raise SystemExit(main())
