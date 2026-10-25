"""会话内切换模型与推理强度（D91）：`/model`、`/thinking`。

**只对当前会话**（人裁决 2026-10-13）：默认值仍是 `config/agent.local.json`，本模块不写它。
会话的设置按 sid 落在会话记录目录的 `_agent/session-models.json`（同 session_titles：不往
session.jsonl 塞元数据，读改写持 fcntl 锁、原子写），所以重开同一个会话、桌面端重启进程之后还在。

推理强度走请求体 `reasoning_effort`（OpenAI Chat Completions 字段）。本机 CLIProxyAPI 把它译成各家
的思考参数（`internal/thinking/apply.go`）；2026-10-13 实测 gemini-3.8-flash-high 与 kimi-k3 上
none / low / high 的推理 token 数都随之变化，模型名里的 `-high` 只是默认值。
"""

from __future__ import annotations

import contextlib
import difflib
import fcntl
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from pipeline.agent.llm import LLMConfig

# CLIProxyAPI 认的档位（apply.go：none → 关，auto → 动态，其余为档位名）
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "auto")
EFFORT_LABELS = {
    "none": "关", "minimal": "极低", "low": "低", "medium": "中",
    "high": "高", "xhigh": "超高", "auto": "自动",
}
RESET_WORD = "default"
MODELS_TIMEOUT_S = 10

FILE_NAME = "session-models.json"
LOCK_NAME = "session-models.lock"


class ModelSwitchError(ValueError):
    """命令参数不合法 / 列表取不到：原文回给人，不改任何设置。"""


@dataclass(frozen=True)
class Override:
    model: str | None = None
    reasoning_effort: str | None = None

    @property
    def empty(self) -> bool:
        return self.model is None and self.reasoning_effort is None


def effective_config(cfg: "LLMConfig | None", override: Override) -> "LLMConfig | None":
    """默认配置 + 本会话设置 → 实际使用的配置。

    换模型时把分档表清空：推理档与轻量档都用这个模型（会话里说「用 kimi-k3」就是全用它，
    不让起标题、压缩之类的轻量请求悄悄留在旧模型上）。
    """
    if cfg is None or override.empty:
        return cfg
    out = cfg
    if override.model is not None:
        out = replace(out, model=override.model, models={})
    if override.reasoning_effort is not None:
        out = replace(out, reasoning_effort=override.reasoning_effort)
    return out


def describe(cfg: "LLMConfig | None", override: Override, purpose: str | None) -> dict[str, Any]:
    """给界面与命令回显用：实际模型、推理档、各自来源（session / default）。"""
    eff = effective_config(cfg, override)
    if eff is None:
        return {"model": None, "reasoning_effort": None, "model_source": "default", "effort_source": "default"}
    return {
        "model": eff.model_for(purpose),
        "reasoning_effort": eff.reasoning_effort,
        "model_source": "session" if override.model is not None else "default",
        "effort_source": "session" if override.reasoning_effort is not None else "default",
    }


def list_models(cfg: "LLMConfig") -> list[str]:
    """GET `{base_url}/models`。失败如实抛 ModelSwitchError（不猜一份列表出来）。"""
    url = cfg.base_url.rstrip("/") + "/models"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {cfg.api_key}"})
    try:
        with urllib.request.urlopen(request, timeout=MODELS_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ModelSwitchError(f"取模型列表失败：HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ModelSwitchError(f"取模型列表失败：{exc}") from None
    except json.JSONDecodeError as exc:
        raise ModelSwitchError(f"模型列表不是 JSON：{exc}") from None
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise ModelSwitchError("模型列表格式不对（缺 data 数组）")
    ids = [str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")]
    return sorted(dict.fromkeys(ids))


#: ``check_model`` 放行未知模型时留下的提醒；每次调用先清回 None（同进程一次只跑一条命令，
#: 协议层执行 ``run_command`` 的线程与读这个值的线程是同一个）
last_warn: str | None = None


def check_model(name: str, available: list[str]) -> str:
    """D148：格式非法（空 / 含空白或控制字符）照旧拒；不在代理列表不再拒——返回名字照常切，
    提醒留在 ``last_warn`` 由调用方放进回执（代理列表落后于新模型，人打字出错靠 warn 就够，不拦死）。"""
    global last_warn
    last_warn = None
    name = name.strip()
    if not name or any(c.isspace() or ord(c) < 0x20 for c in name):
        raise ModelSwitchError(f"模型名 {name!r} 不合法（空或含空白/控制字符）")
    if name in available:
        return name
    close = difflib.get_close_matches(name, available, n=3, cutoff=0.4)
    hint = f"；相近：{'、'.join(close)}" if close else ""
    last_warn = f"模型 {name} 不在代理列表{hint}——已照常切换，名字打错的话用 /model 换"
    return name


def check_effort(value: str) -> str:
    v = value.strip().lower()
    if v not in EFFORTS:
        raise ModelSwitchError(f"推理强度只能是 {' / '.join(EFFORTS)}（或 {RESET_WORD} 恢复默认），收到 {value!r}")
    return v


# ---------------------------------------------------------------------------
# 按 sid 持久化
# ---------------------------------------------------------------------------


def store_path(log_dir: Path) -> Path:
    return Path(log_dir) / "_agent" / FILE_NAME


def _parse(raw: str) -> dict[str, dict[str, Any]]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("顶层不是对象")
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _from_entry(entry: dict[str, Any] | None) -> Override:
    if not entry:
        return Override()
    model = entry.get("model")
    effort = entry.get("reasoning_effort")
    return Override(
        model=model if isinstance(model, str) and model.strip() else None,
        reasoning_effort=effort if effort in EFFORTS else None,
    )


def load(log_dir: Path | None, sid: str | None) -> tuple[Override, str | None]:
    """读回某个会话的设置：返回 (设置, 警告)。文件坏了 → 默认 + 警告（不静默当成没设过）。"""
    if log_dir is None or not sid:
        return Override(), None
    path = store_path(log_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Override(), None
    except OSError as exc:
        return Override(), f"读不了 {path.name}（{exc}），本会话按默认模型"
    try:
        return _from_entry(_parse(raw).get(sid)), None
    except ValueError as exc:
        return Override(), f"{path.name} 无法解析（{exc}），本会话按默认模型"


@contextlib.contextmanager
def _lock(log_dir: Path) -> Iterator[None]:
    agent_dir = Path(log_dir) / "_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(agent_dir / LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def save(log_dir: Path, sid: str, override: Override) -> None:
    """持锁读改写本 sid 的条目；设置清空则删掉条目。文件坏了 → 拒写（不拿空表覆盖别的会话）。"""
    from pipeline.paths import atomic_write

    path = store_path(log_dir)
    with _lock(log_dir):
        try:
            data = _parse(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except ValueError as exc:
            raise ModelSwitchError(f"{path.name} 无法解析，拒绝覆盖：{exc}") from None
        if override.empty:
            if sid not in data:
                return
            data.pop(sid)
        else:
            data[sid] = {
                "model": override.model,
                "reasoning_effort": override.reasoning_effort,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")


# ---------------------------------------------------------------------------
# 命令（终端 REPL 与对话框共用）
# ---------------------------------------------------------------------------


def _effort_text(effort: str | None, source: str) -> str:
    if effort is None:
        return "模型默认（不发推理参数）"
    tag = "本会话设置" if source == "session" else "默认配置"
    return f"{effort}（{EFFORT_LABELS.get(effort, effort)}，{tag}）"


def run_command(
    line: str,
    cfg: "LLMConfig | None",
    override: Override,
    purpose: str | None,
    *,
    models_fetcher=None,
) -> tuple[Override, str]:
    """解析 `/model …` / `/thinking …`：返回 (新设置, 回显文字)。参数不合法抛 ModelSwitchError。

    `cfg` 是**默认**配置（未叠本会话设置）。没有可用配置时只能看、不能换。
    """
    global last_warn
    last_warn = None   # D148：每条命令先把上一条的提醒清掉（模块级瞬时态）
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    if cfg is None:
        raise ModelSwitchError("LLM 不可用（缺配置或密钥），无法切换模型")
    if models_fetcher is None:
        models_fetcher = list_models   # 调用时取（测试可打桩模块属性）
    info = describe(cfg, override, purpose)
    if cmd == "/model":
        if not arg:
            available = models_fetcher(cfg)
            tag = "本会话设置" if info["model_source"] == "session" else "默认配置"
            lines = [f"当前模型：{info['model']}（{tag}）", "代理提供的模型："]
            lines += [f"  {'●' if m == info['model'] else '○'} {m}" for m in available]
            lines.append("切换：/model <名>；恢复默认：/model default（只影响本会话）")
            return override, "\n".join(lines)
        if arg == RESET_WORD:
            new = replace(override, model=None)
            return new, f"已恢复默认模型：{describe(cfg, new, purpose)['model']}（本会话）"
        name = check_model(arg, models_fetcher(cfg))
        new = replace(override, model=name)
        text = f"本会话已切换到 {name}（下一轮起生效；新会话仍用默认模型）"
        if last_warn:
            text += f"\n{last_warn}"
        return new, text
    if cmd == "/thinking":
        if not arg:
            levels = " / ".join(f"{e}（{EFFORT_LABELS[e]}）" for e in EFFORTS)
            return override, (
                f"当前推理强度：{_effort_text(info['reasoning_effort'], info['effort_source'])}\n"
                f"可选：{levels}\n切换：/thinking <档>；恢复默认：/thinking default（只影响本会话）"
            )
        if arg == RESET_WORD:
            new = replace(override, reasoning_effort=None)
            after = describe(cfg, new, purpose)
            return new, f"已恢复默认推理强度：{_effort_text(after['reasoning_effort'], after['effort_source'])}"
        effort = check_effort(arg)
        new = replace(override, reasoning_effort=effort)
        return new, f"本会话推理强度设为 {effort}（{EFFORT_LABELS[effort]}，下一轮起生效）"
    raise ModelSwitchError(f"未知命令 {cmd}")


def is_command(line: str) -> bool:
    head = line.strip().split(maxsplit=1)[0] if line.strip() else ""
    return head in ("/model", "/thinking")
