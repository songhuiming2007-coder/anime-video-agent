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
    # 三份文件各自读入后的 (配置路径, strip 原文)，scope 取拼 extra_prompt **之前**的那份；
    # 缺失文件的占位标题不在其中（Spec 16 §5.2.1：出网断言可信集的常驻层来源）
    doc_texts: tuple[tuple[str, str], ...] = ()


@dataclass
class SessionContextTracker:
    resident_prompt: str = ""
    active_scope: str | None = None  # 记录当前常驻层所属 scope，支持 scope 热切换
    injected_paths: set[str] = field(default_factory=set)
    active_step_key: str | None = None
    _warned_paths: set[str] = field(default_factory=set)  # 红队 M2：警告去重
    # 记忆告警拆成两个标志（Spec 9 §2.3 第 3 条 🔵-1）：
    #   printed  = 终端已经打过（**不随回滚回退**，免得下一轮重复刷屏）
    #   injected = 告警消息还在历史里（**随回滚回退**，下一轮重新注入）
    memory_warn_printed: bool = False
    memory_warn_injected: bool = False
    # 出网断言可信集 (a)（Spec 16 §5.2.1）：本进程装配器实际拼进消息的规程正文，只增不减。
    # **不进** `_rollback` 的恢复清单——可信集是超集无害，豁免只认逐字字节。
    trusted_doc_texts: list[str] = field(default_factory=list)

    def trust(self, texts: list[str]) -> None:
        for text in texts:
            if text and text not in self.trusted_doc_texts:
                self.trusted_doc_texts.append(text)

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
    正文与告警各自每会话一次，判重由调用方按 `tracker.injected_paths` /
    `memory_warn_injected` / `memory_warn_printed` 做。
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
    resident_map = resident_paths(scope, config_path, root)
    doc_texts: list[tuple[str, str]] = []

    # 1. Director
    director_p = base_root / resident_map["director"]
    if director_p.exists():
        director_text = read_resident_file(director_p)
        doc_texts.append((resident_map["director"], director_text.strip()))
    else:
        director_text = "# Director Persona"

    # 2. Scope
    scope_p = base_root / resident_map["scope"]
    if scope_p.exists():
        scope_text = read_resident_file(scope_p)
        doc_texts.append((resident_map["scope"], scope_text.strip()))
    else:
        scope_text = f"# {scope.capitalize()} Scope"

    if extra_prompt:
        scope_text = f"{scope_text}\n\n{extra_prompt}"

    # 3. AGENTS.md
    agents_p = base_root / resident_map["agents"]
    if agents_p.exists():
        agents_text = read_resident_file(agents_p)
        doc_texts.append((resident_map["agents"], agents_text.strip()))
    else:
        agents_text = ""

    parts = [p.strip() for p in (director_text, scope_text, agents_text) if p.strip()]
    content = "\n\n---\n\n".join(parts)
    token_estimate = len(content) // 4
    return AssembledResident(
        scope=scope, content=content, token_estimate=token_estimate, doc_texts=tuple(doc_texts)
    )


def read_resident_file(path: Path) -> str:
    """常驻层三件的唯一读法（做换行翻译）。装配与出网可信集 (b) 共用（Spec 16 §5.2.2）。"""
    return path.read_text(encoding="utf-8", errors="replace")


def resident_paths(
    scope: str,
    config_path: Path | None = None,
    root: Path | None = None,
) -> dict[str, str]:
    """`assembly.json` 的 resident 三件按 scope 展开后的配置路径（缺键用默认值）。"""
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
    return resident_map


# ---- 出网断言可信集（Spec 16 §5.2；ADR-0026） ----

# 白名单根：resolve 后的仓库相对路径必须落在这里（§5.2.3 第 2 条）
TRUSTED_DOC_ROOTS: tuple[str, ...] = ("docs/", "skills/", "config/agent/scopes/")
TRUSTED_DOC_FILES: tuple[str, ...] = ("AGENTS.md",)


def is_trusted_doc_path(root: Path | None, rel_path: str) -> bool:
    """这份文档的正文能否进出网断言的可信集（Spec 16 §5.2.3，五条同时满足）。

    唯一一份规则：可信集收集调用它；N55 的装配器拒载修复也复用它，不许另写第二份。
    """
    from pipeline.agent.memory import MEMORY_REL_PATH  # 函数内 import：装配器热路径不背这个模块
    from pipeline.agent.tools import RESTRICTED_EGRESS_PATTERNS

    patterns = tuple(p.casefold() for p in RESTRICTED_EGRESS_PATTERNS)
    configured = Path(rel_path)
    # 第 5 条（配置路径一侧）
    if any(p in configured.as_posix().casefold() for p in patterns):
        return False
    base = Path(root or paths.ROOT)
    target = configured if configured.is_absolute() else base / configured
    # 第 1 条：resolve(strict=True) 成功且在仓库根之内（软链出根、仓库外绝对路径都过不去）
    try:
        real = target.resolve(strict=True)
        base_real = base.resolve(strict=True)
        rel_real = real.relative_to(base_real).as_posix()
    except (OSError, RuntimeError, ValueError):
        return False
    folded = rel_real.casefold()
    # 第 3 条：不在 data/ 下、不是记忆文档
    if folded == "data" or folded.startswith("data/") or folded == MEMORY_REL_PATH.casefold():
        return False
    # 第 4 条：.md；第 5 条（resolve 后一侧）
    if not rel_real.endswith(".md") or any(p in folded for p in patterns):
        return False
    # 第 2 条：白名单根
    return rel_real in TRUSTED_DOC_FILES or rel_real.startswith(TRUSTED_DOC_ROOTS)


def trusted_texts_of_docs(docs: list[InjectedDoc], root: Path | None) -> list[str]:
    """(a) 的工序层捕获：装配器拼进消息的正文是 `doc.content.strip()`，逐份过读域过滤。"""
    return [
        d.content.strip() for d in docs
        if d.content.strip() and is_trusted_doc_path(root, d.rel_path)
    ]


def trusted_texts_of_resident(resident: AssembledResident, root: Path | None) -> list[str]:
    """(a) 的常驻层捕获：三份文件各自的原文（不含 extra_prompt），逐份过读域过滤。"""
    return [text for rel, text in resident.doc_texts if text and is_trusted_doc_path(root, rel)]


def route_trusted_texts(
    scope: str,
    config_path: Path | None = None,
    root: Path | None = None,
) -> list[str]:
    """可信集 (b)：resident 三件（当前 scope）+ routes 下全部 scope、全部工序键所指文档的
    **当前磁盘正文**，读法与装配器同层一致（Spec 16 §5.2.2）。每回合装配时调用。

    作用：`--continue` 恢复出来的历史里，上一进程注入的规程只要没改版，就能在这里被认出。
    """
    base_root = root or paths.ROOT
    cfg_file = config_path or (base_root / "config" / "agent" / "assembly.json")
    texts: list[str] = []

    for rel in resident_paths(scope, config_path, root).values():
        target = base_root / rel
        if not is_trusted_doc_path(root, rel):
            continue
        try:
            text = read_resident_file(target).strip()
        except OSError:
            continue
        if text:
            texts.append(text)

    try:
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return texts
    routes = data.get("routes") if isinstance(data, dict) else None
    if not isinstance(routes, dict):
        return texts
    seen: set[str] = set()
    for scope_routes in routes.values():
        if not isinstance(scope_routes, dict):
            continue
        for key, doc_list in scope_routes.items():
            if key == "_extends" or not isinstance(doc_list, list):
                continue
            for rel in doc_list:
                rel = str(rel)
                if rel in seen:
                    continue
                seen.add(rel)
                if not is_trusted_doc_path(root, rel):
                    continue
                doc = load_injected_doc(rel, root=root)  # 路由文档的唯一读法：不翻译换行
                if doc is not None and doc.content.strip():
                    texts.append(doc.content.strip())
    return texts


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
