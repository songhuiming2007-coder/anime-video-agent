"""工序层上下文装配器与路由引擎（ADR-0022）。

常驻层字节级恒定，工序层精准增量注入。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pipeline import paths

STEP_KEY_MAP: dict[str, str] = {
    "01 选题": "01",
    "02 脚本写作": "02",
    "02 脚本写作（草稿待定稿）": "02",
    "02.5 人审改稿": "02.5",
    "03 语音合成": "03",
    "03.5 配音顺听 / 04 排片": "03.5",
    "05 审时间码": "05",
    "06 本地渲染": "06",
    "07 自动质检": "07",
    "07 自动质检（未通过）": "07",
    "08 封面与标题候选": "08",
    "09 人工发布": "09",
}


@dataclass(frozen=True)
class InjectedDoc:
    rel_path: str
    abs_path: Path
    content: str
    token_estimate: int  # 估算口径：len(content) // 4（经验值，仅作预算粗估，不作精确计费）


@dataclass(frozen=True)
class AssembledResident:
    scope: str
    content: str  # director.md + scope.md + AGENTS.md 瘦身版
    token_estimate: int


@dataclass
class SessionContextTracker:
    resident_prompt: str = ""
    active_scope: str | None = None  # 记录当前常驻层所属 scope，支持 scope 热切换
    injected_paths: set[str] = field(default_factory=set)
    active_step_key: str | None = None
    _warned_paths: set[str] = field(default_factory=set)  # 红队 M2：警告去重
    memory_warned: bool = False  # 记忆告警每会话一次，不占正文注入名额（Spec 7 三轮 🟡-D）

    def get_initial_system_prompt(
        self,
        status_card: str,
    ) -> str:
        """首轮 messages[0]：仅常驻层 + 动态层，工序层不拼入。"""
        return f"{self.resident_prompt}\n\n---\n\n{status_card}"

    def warn_once(self, path: str, message: str) -> None:
        """同一缺失文件只警告一次，防止 REPL 刷屏（红队 M2）。"""
        norm_path = str(Path(path).as_posix())  # 统一路径格式
        if norm_path not in self._warned_paths:
            print(f"[WARN] {message}", file=sys.stderr)
            self._warned_paths.add(norm_path)


def step_key_of(current_step: str | None) -> str:
    """current_step 字符串 → 路由键。查表优先，未命中回退 'default'。"""
    if current_step is None:
        return "default"
    for prefix, key in sorted(STEP_KEY_MAP.items(), key=lambda x: -len(x[0])):
        if current_step.startswith(prefix):
            return key
    return "default"


def resolve_step_docs(
    scope: str,
    step_key: str,
    config_path: Path | None = None,
    root: Path | None = None,
) -> list[Path]:
    """解析 scope + step_key 到文档路径清单。

    支持 `_extends` 继承：先取 _base，再叠加 scope 特有键。
    配置损坏（JSON 解析失败 / routes 键缺失）时打印警告并返回空清单。
    """
    base_root = root or paths.ROOT
    cfg_file = config_path or (base_root / "config" / "agent" / "assembly.json")

    if not cfg_file.exists():
        return []

    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] 无法读取装配路由配置 {cfg_file}: {e}", file=sys.stderr)
        return []

    if not isinstance(data, dict) or "routes" not in data or not isinstance(data["routes"], dict):
        print(f"[WARN] 装配路由配置格式错误: 缺少 routes 字典", file=sys.stderr)
        return []

    routes = data["routes"]
    base_routes = routes.get("_base", {})
    scope_routes = routes.get(scope)

    if scope_routes is None:
        merged = dict(base_routes)
    elif isinstance(scope_routes, dict):
        parent_name = scope_routes.get("_extends")
        if parent_name and parent_name in routes:
            merged = dict(routes[parent_name])
        else:
            merged = {}
        merged.update({k: v for k, v in scope_routes.items() if k != "_extends"})
    else:
        return []

    doc_list = merged.get(step_key)
    if doc_list is None:
        doc_list = merged.get("default", [])

    if not isinstance(doc_list, list):
        return []

    return [Path(p) for p in doc_list]


def load_injected_doc(
    rel_path: str,
    root: Path | None = None,
) -> InjectedDoc | None:
    """读取文档。三层防御：
    1. 循环软链 → OSError 族捕获（实测 macOS/Python 3.12 自指软链 read_text 抛 OSError 子类，非 RuntimeError），返回 None；
    2. 文件不存在 → FileNotFoundError 捕获，返回 None；
    3. 编码异常 → errors='replace' 降级，打印警告。
    """
    base_root = root or paths.ROOT
    p = Path(rel_path)
    target = p if p.is_absolute() else (base_root / p)

    try:
        raw_bytes = target.read_bytes()
    except (FileNotFoundError, OSError):
        return None

    try:
        content = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content = raw_bytes.decode("utf-8", errors="replace")
        print(f"[WARN] 文件编码非纯 UTF-8，已降级替换: {rel_path}", file=sys.stderr)

    try:
        abs_path = target.resolve()
    except OSError:
        abs_path = target

    norm_rel = Path(rel_path).as_posix()
    token_estimate = len(content) // 4
    return InjectedDoc(
        rel_path=norm_rel,
        abs_path=abs_path,
        content=content,
        token_estimate=token_estimate,
    )


def memory_scopes(
    config_path: Path | None = None,
    root: Path | None = None,
) -> frozenset[str]:
    """assembly.json 的 `memory.scopes`；缺键或配置损坏 → 空集（任何 scope 都不注入）。"""
    base_root = root or paths.ROOT
    cfg_file = config_path or (base_root / "config" / "agent" / "assembly.json")
    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return frozenset()
    if not isinstance(data, dict):
        return frozenset()
    block = data.get("memory")
    if not isinstance(block, dict):
        return frozenset()
    scopes = block.get("scopes")
    if not isinstance(scopes, list):
        return frozenset()
    return frozenset(str(s) for s in scopes)


def resolve_memory_injection(
    scope: str,
    *,
    root: Path | None = None,
    config_path: Path | None = None,
) -> tuple[InjectedDoc, bool] | None:
    """跨期记忆注入文档（Spec 7 §4.6）。返回 (doc, 是否告警)；不注入返回 None。

    不走 `load_injected_doc`：原样读取会绕过 memory 的校验层与来源确认。
    正文与告警各自每会话一次，判重由调用方按 `tracker.injected_paths` / `memory_warned` 做。
    """
    if scope not in memory_scopes(config_path, root):
        return None
    from pipeline.agent import memory  # 函数内 import：装配器热路径不背这个模块

    content = memory.render_injection(root)
    if content is None:
        return None
    base_root = Path(root or paths.ROOT)
    return (
        InjectedDoc(
            rel_path=memory.MEMORY_REL_PATH,
            abs_path=base_root / memory.MEMORY_REL_PATH,
            content=content,
            token_estimate=len(content) // 4,
        ),
        content.startswith(memory.WARNING_PREFIX),
    )


def assemble_resident_prompt(
    scope: str,
    config_path: Path | None = None,
    root: Path | None = None,
    extra_prompt: str = "",
) -> AssembledResident:
    """组装常驻层：director.md + scope.md + AGENTS.md 瘦身版。

    会话内字节级恒定，任何修改都会破坏 Prompt Cache。
    """
    base_root = root or paths.ROOT
    cfg_file = config_path or (base_root / "config" / "agent" / "assembly.json")

    resident_map = {
        "director": "config/agent/scopes/director.md",
        "scope": f"config/agent/scopes/{scope}.md",
        "agents": "AGENTS.md",
    }

    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("resident"), dict):
                res_cfg = data["resident"]
                if "director" in res_cfg:
                    resident_map["director"] = res_cfg["director"]
                if "scope" in res_cfg:
                    resident_map["scope"] = str(res_cfg["scope"]).format(scope=scope)
                if "agents" in res_cfg:
                    resident_map["agents"] = res_cfg["agents"]
        except Exception:
            pass

    # 1. Director
    director_p = base_root / resident_map["director"]
    if director_p.exists():
        director_text = director_p.read_text(encoding="utf-8", errors="replace")
    else:
        director_text = "# Director Persona"

    # 2. Scope
    scope_p = base_root / resident_map["scope"]
    if scope_p.exists():
        scope_text = scope_p.read_text(encoding="utf-8", errors="replace")
    else:
        scope_text = f"# {scope.capitalize()} Scope"

    if extra_prompt:
        scope_text = f"{scope_text}\n\n{extra_prompt}"

    # 3. AGENTS.md
    agents_p = base_root / resident_map["agents"]
    if agents_p.exists():
        agents_text = agents_p.read_text(encoding="utf-8", errors="replace")
    else:
        agents_text = ""

    parts = [p.strip() for p in (director_text, scope_text, agents_text) if p.strip()]
    content = "\n\n---\n\n".join(parts)
    token_estimate = len(content) // 4
    return AssembledResident(scope=scope, content=content, token_estimate=token_estimate)


def render_step_injection(docs: list[InjectedDoc], step_name: str | None = None) -> str:
    """将工序文档清单渲染为 user 消息文本。"""
    if not docs:
        return ""
    doc_bodies = "\n\n---\n\n".join(d.content.strip() for d in docs)
    header = (
        f"[系统提示更新] 当前工序已进入 {step_name}。"
        if step_name
        else "[系统提示更新] 当前工序规程已更新。"
    )
    return (
        f"{header}\n\n"
        "请遵循以下规程：\n\n"
        "---\n\n"
        f"{doc_bodies}\n\n"
        "---\n\n"
        "**注意**：以上规程仅适用于当前工序。若后续工序切换，将追加新的上下文更新。"
    )
