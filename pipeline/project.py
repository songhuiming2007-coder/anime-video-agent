"""企划层（D81）：期目录树规则 + `00-project.md` 解析器。

一处规则，处处调用（D81 spec §4.1「判定只有一处」）：「这个目录是企划 / 卷 / 普通期」一律走
`classify_dir`，期列表一律走 `get_episode_tree`；`pipeline.agent.cli` 以原名再导出（`cli.classify_dir`、
`cli.get_episode_tree`）。放在这个叶子模块而不是 cli.py：`adversarial` / `crosscheck` / `assemble`
也要认企划，import cli.py 会拖进整个 agent 栈（cli.py 文件头已写明共享函数要下沉到零依赖叶子模块）。

`00-project.md` 的解析器是**唯一一处**（spec §4.7）：宿主经 core 管理命令 `/project-meta` 取结果，
TS 侧不另写解析器。机读字段 = (企划, 合集, 卷表全部行)；散文章节（总纲、标题模板……）只列标题、不解析。

顶层只依赖 stdlib + pipeline.paths（叶子性）。
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from pipeline import paths

PROJECT_FILE = "00-project.md"
TOPIC_FILE = "01-topic.md"

# 取值理由见 D81 spec §6.4
PROJECT_NAME_MAX_CHARS = 40   # 企划名进状态卡首行与侧栏；超了多半是把一句话当成了名字
YEAR_SPAN_MAX_CHARS = 20      # `2011–2014`、`2020–2023（终场）` 都在 15 字内；超了说明写进了别的内容
TAIL_MAX_S = 60               # 只做形状检查：余韵超过一分钟就不是「片尾余韵」而是一段音乐

VOLUME_TABLE_HEADING = "卷表"
VOLUME_TABLE_COLUMNS = ("卷序", "目录", "年份", "时长预算", "片尾")
TAIL_KEEP = "完整保留"

# ---------------------------------------------------------------------------
# 期名合规（原 cli._episode_name_problem，搬到叶子模块；cli 以原名再导出）
# ---------------------------------------------------------------------------

_RE_EP_NAME_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")

#: 期名末尾不许出现的句读。期名常从 agent 回复里整句复制，句末标点跟着进了目录名
#: （2026-10-09 伪恋那期建成 `…进攻哲学。`）；期目录又没有改名入口（D63），只能拒在建期前。
#: 只收标题不会拿来收尾的句读。不收括号引号（已有期名 `…函数的终途（void）`），
#: 也不收 `？！…`（「她为什么要逃？」是正当的问句标题）。
EP_NAME_TRAILING_PUNCT = "。，、；：．.,;:"


def episode_name_problem(ep_name: str) -> str | None:
    r"""期名不合规的具体原因，合规返回 None（Spec 10 C10-R1 §3.7 第 2 步）。

    每条都有来历：`.`/`_` 前缀会被 `get_episodes_list` 藏起来（建了看不见）；
    `-` 开头会与 `ava --continue` 这类开关歧义；`/`、`\`、NUL 与控制字符
    会让 `mkdir` 建到期根之外或不存在的路径；255 字节是 APFS 单段名上限；
    末尾句读多半是整句复制带进来的（见 `EP_NAME_TRAILING_PUNCT`）。
    """
    if not ep_name or ep_name != ep_name.strip():
        return "不许为空或首尾带空白"
    if ep_name in (".", ".."):
        return f"不许是 {ep_name!r}"
    if ep_name[0] in "._":
        return f"不许以 {ep_name[0]!r} 开头（期列表会把它藏起来）"
    if ep_name.startswith("-"):
        return "不许以 '-' 开头（会与命令行开关混淆）"
    if ep_name[-1] in EP_NAME_TRAILING_PUNCT:
        return f"不许以 {ep_name[-1]!r} 结尾（多半是从句子里连句末标点一起复制的）"
    if any(ch in ep_name for ch in ("/", "\\", "\0")):
        return "不许含 '/'、'\\' 或 NUL"
    ctrl = _RE_EP_NAME_CTRL.search(ep_name)
    if ctrl:
        return f"不许含控制字符（U+{ord(ctrl.group()):04X}）"
    if len(ep_name.encode("utf-8")) > 255:
        return "过长（UTF-8 超过 255 字节，APFS 单段名上限）"
    return None


def name_key(name: str) -> str:
    """目录名比较口径：NFC 后 casefold（APFS 大小写不敏感，`01-A` 与 `01-a` 是同一个目录，红队 R-19）。"""
    return unicodedata.normalize("NFC", name).casefold()


# ---------------------------------------------------------------------------
# 树规则（spec §4.1）：顶层条目 d 有 00-project.md，或有至少一个成员 → 企划；否则普通期
# ---------------------------------------------------------------------------


def episodes_root(root: Path | None = None) -> Path:
    return Path(root or paths.ROOT) / "data" / "episodes"


def _is_member(sub: Path) -> bool:
    """企划的成员：不以 `.` / `_` 开头的子目录（跟随软链判目录），且有 01-topic.md。"""
    return (
        not sub.name.startswith((".", "_"))
        and sub.is_dir()
        and (sub / TOPIC_FILE).exists()
    )


def project_members(project_dir: Path) -> list[Path]:
    """成员按目录名**码点序**升序（Python `sorted()` 即码点序；TS 侧必须显式按码点比较）。"""
    try:
        subs = list(Path(project_dir).iterdir())
    except OSError:
        return []
    return sorted((s for s in subs if _is_member(s)), key=lambda p: p.name)


def _is_project_top(d: Path) -> bool:
    if (d / PROJECT_FILE).is_file():
        return True
    try:
        return any(_is_member(s) for s in d.iterdir())
    except OSError:
        return False


def _literal(path: Path | str) -> Path:
    """规范化后的字面路径：绝对化 + 折叠 `.` / `..`，**不跟随软链**（卷的身份按字面路径，红队 R-5）。"""
    return Path(os.path.normpath(os.path.abspath(str(path))))


def episode_key(path: Path | str, root: Path | None = None) -> tuple[str, ...] | None:
    """路径相对 `data/episodes/` 的段（字面路径优先，再试期根的 resolve 形态）；不在期根下返回 None。

    期根常是外置盘软链（`data -> /Volumes/…`），人也可能直接给 resolve 后的绝对路径——两种形态都认，
    但都只在**字面**上取段，不 resolve 被判的那个路径本身。
    """
    lit = _literal(path)
    base = episodes_root(root)
    forms = [f for f in (_literal(base), base.resolve() if base.exists() else None) if f is not None]
    for form in forms:
        try:
            rel = lit.relative_to(form)
        except ValueError:
            continue
        parts = rel.parts
        return parts if parts else None
    # 期根以上的那一段经软链（macOS `/var -> /private/var`、外置盘软链）：只 resolve「期根」那一段，
    # 末尾的期 / 企划 / 卷各段仍按字面取（不 resolve 被判的路径本身，软链卷身份不变）
    for k in (1, 2):
        if len(lit.parts) <= k:
            continue
        head = Path(*lit.parts[:-k])
        try:
            resolved_head = head.resolve()
        except OSError:
            continue
        if any(resolved_head == f for f in forms):
            return lit.parts[-k:]
    return None


def classify_dir(path: Path | str, root: Path | None = None) -> str | None:
    """「企划 / 卷 / 普通期」的唯一判定（spec §4.1、红队 R-13）。

    - 期根下一段：不是目录或以 `.` 开头 → None；企划条件成立 → "project"；否则 "episode"。
      `_` 前缀的顶层目录不进列表，但显式指定时照常判定（§4.9 镜像企划靠这一条）。
    - 期根下两段：父目录按上一条是企划、本目录是它的成员 → "volume"；否则 None。
    - 其余（期根本身、第三层、期根之外）→ None。
    """
    parts = episode_key(path, root)
    if parts is None or len(parts) > 2:
        return None
    base = episodes_root(root)
    top = base / parts[0]
    if parts[0].startswith(".") or not top.is_dir():
        return None
    if len(parts) == 1:
        return "project" if _is_project_top(top) else "episode"
    sub = top / parts[1]
    if _is_project_top(top) and _is_member(sub):
        return "volume"
    return None


def project_of(path: Path | str, root: Path | None = None) -> Path | None:
    """卷所属企划的字面路径（父目录按字面取，不 resolve）；不是卷返回 None。"""
    if classify_dir(path, root) != "volume":
        return None
    parts = episode_key(path, root)
    assert parts is not None
    return episodes_root(root) / parts[0]


def literal_episode_path(path: Path | str, root: Path | None = None) -> Path | None:
    """把期根下的路径（字面或 resolve 形态）统一成 `paths.ROOT/data/episodes/<段>` 的字面形态。"""
    parts = episode_key(path, root)
    if parts is None:
        return None
    return episodes_root(root).joinpath(*parts)


@dataclass(frozen=True)
class ProjectEntry:
    dir: Path
    members: tuple[Path, ...]
    has_project_file: bool
    mtime: float


@dataclass(frozen=True)
class EpisodeTree:
    plain: tuple[Path, ...]
    projects: tuple[ProjectEntry, ...]
    hidden_underscore: int
    # 普通期与企划按 mtime 降序混排（企划的 mtime = 企划目录与全部成员 mtime 的最大值）
    order: tuple[Path | ProjectEntry, ...] = field(default=())


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def get_episode_tree(root: Path | None = None) -> EpisodeTree:
    """期列表的树形（spec §4.1）。`.` 前缀跳过；`_` 前缀计入隐藏数（企划内的 `_` 子目录同样计入）。"""
    ep_root = episodes_root(root)
    if not ep_root.exists():
        return EpisodeTree((), (), 0, ())
    plain: list[Path] = []
    projects: list[ProjectEntry] = []
    hidden = 0
    for d in ep_root.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        if d.name.startswith("_"):
            hidden += 1
            continue
        if _is_project_top(d):
            members = tuple(project_members(d))
            hidden += sum(1 for s in d.iterdir() if s.is_dir() and s.name.startswith("_"))
            mtime = max([_mtime(d), *(_mtime(m) for m in members)])
            projects.append(ProjectEntry(d, members, (d / PROJECT_FILE).is_file(), mtime))
        else:
            plain.append(d)
    order: list[Path | ProjectEntry] = [*plain, *projects]
    order.sort(key=lambda e: e.mtime if isinstance(e, ProjectEntry) else _mtime(e), reverse=True)
    return EpisodeTree(tuple(plain), tuple(projects), hidden, tuple(order))


# ---------------------------------------------------------------------------
# `00-project.md` 解析（spec §4.7）
# ---------------------------------------------------------------------------

_HEADER_LINE = re.compile(r"^([^\s:：#|][^:：]*?)\s*[:：]\s*(.*?)\s*$")
_DURATION = re.compile(r"^([\d.]+)\s*[-–~]\s*([\d.]+)\s*分钟$")   # 与 check_script.DURATION_FIELD 同一数字段语法
_TAIL = re.compile(r"^余韵(\d+)秒$")
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class ParseError(ValueError):
    """`00-project.md` 不可解析（机读部分）。消息写明哪一行、哪一条规则。"""


@dataclass(frozen=True)
class Volume:
    seq: int
    dir: str
    years: str
    budget: str
    budget_min: tuple[float, float]
    tail: str
    tail_s: int | None   # None = 完整保留

    def cells(self) -> tuple[str, str, str, str, str]:
        return (str(self.seq), self.dir, self.years, self.budget, self.tail)


@dataclass(frozen=True)
class ProjectDoc:
    name: str
    collection: str | None
    volumes: tuple[Volume, ...]
    sections: tuple[str, ...]   # 全部 `## ` 标题（含卷表），按出现顺序


def _split_row(line: str) -> list[str] | None:
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|") and len(s) >= 2):
        return None
    return [c.strip() for c in s[1:-1].split("|")]


def parse(text: str) -> ProjectDoc:
    """解析 `00-project.md`；不成立抛 ParseError（唯一一处，宿主经 `/project-meta` 取结果）。"""
    lines = text.split("\n")
    first_h2 = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), len(lines))
    name: str | None = None
    collection: str | None = None
    seen: set[str] = set()
    for no, ln in enumerate(lines[:first_h2], 1):
        m = _HEADER_LINE.match(ln)
        if not m:
            continue
        key, value = m.group(1).strip(), m.group(2)
        if key not in ("企划", "合集"):
            continue   # 其他行（含 `番:`）是说明文字：没有消费方的字段不进解析（判据 6）
        if key in seen:
            raise ParseError(f"第 {no} 行：机读头 `{key}` 出现两次")
        seen.add(key)
        if key == "企划":
            name = value
        else:
            collection = value
    if name is None:
        raise ParseError("机读头缺 `企划:` 行（第一个 `## ` 标题之前）")
    if not name or len(name) > PROJECT_NAME_MAX_CHARS or _CTRL.search(name):
        raise ParseError(f"`企划` 须为 1–{PROJECT_NAME_MAX_CHARS} 字、不含控制字符：{name!r}")
    if collection is not None:
        problem = episode_name_problem(collection)
        if problem:
            raise ParseError(f"`合集` 不是合规目录名：{problem}")

    sections = tuple(ln[3:].strip() for ln in lines if ln.startswith("## "))
    try:
        table_at = next(i for i, ln in enumerate(lines) if ln.startswith("## ") and ln[3:].strip() == VOLUME_TABLE_HEADING)
    except StopIteration:
        raise ParseError(f"缺 `## {VOLUME_TABLE_HEADING}` 一节") from None
    end = next((i for i in range(table_at + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = lines[table_at + 1:end]
    start = next((i for i, ln in enumerate(body) if ln.strip().startswith("|")), None)
    if start is None:
        raise ParseError(f"`## {VOLUME_TABLE_HEADING}` 下没有表格（至少要有表头）")
    header = _split_row(body[start])
    if header is None or tuple(header) != VOLUME_TABLE_COLUMNS:
        raise ParseError(f"卷表表头须恰为 {' / '.join(VOLUME_TABLE_COLUMNS)}（按此顺序），实为 {header}")
    sep = _split_row(body[start + 1]) if start + 1 < len(body) else None
    if sep is None or len(sep) != len(VOLUME_TABLE_COLUMNS) or not all(re.fullmatch(r":?-{3,}:?", c) for c in sep):
        raise ParseError("卷表表头下一行须为分隔行 `|---|---|---|---|---|`")
    volumes: list[Volume] = []
    keys: dict[str, int] = {}
    for offset, ln in enumerate(body[start + 2:]):
        if not ln.strip().startswith("|"):
            break
        no = table_at + 1 + start + 2 + offset + 1
        cells = _split_row(ln)
        if cells is None or len(cells) != len(VOLUME_TABLE_COLUMNS):
            raise ParseError(f"第 {no} 行：卷表行须恰有 {len(VOLUME_TABLE_COLUMNS)} 列")
        seq_raw, dirname, years, budget, tail = cells
        if not re.fullmatch(r"\d+", seq_raw) or int(seq_raw) != len(volumes) + 1:
            raise ParseError(f"第 {no} 行：卷序须为从 1 起的连续整数，此处应为 {len(volumes) + 1}，实为 {seq_raw!r}")
        problem = episode_name_problem(dirname)
        if problem:
            raise ParseError(f"第 {no} 行：目录 {dirname!r} 不合规：{problem}")
        key = name_key(dirname)
        if key in keys:
            raise ParseError(f"第 {no} 行：目录 {dirname!r} 与第 {keys[key]} 卷重复（大小写 / Unicode 规范化后相同）")
        keys[key] = len(volumes) + 1
        if len(years) > YEAR_SPAN_MAX_CHARS or _CTRL.search(years):
            raise ParseError(f"第 {no} 行：年份须为单行、≤ {YEAR_SPAN_MAX_CHARS} 字")
        m = _DURATION.match(budget)
        if not m:
            raise ParseError(f"第 {no} 行：时长预算须形如 `14-16分钟`，实为 {budget!r}")
        a, b = float(m.group(1)), float(m.group(2))
        if not 0 < a <= b:
            raise ParseError(f"第 {no} 行：时长预算须 0 < A ≤ B，实为 {budget!r}")
        if tail == TAIL_KEEP:
            tail_s = None
        else:
            mt = _TAIL.match(tail)
            if not mt or not 1 <= int(mt.group(1)) <= TAIL_MAX_S:
                raise ParseError(f"第 {no} 行：片尾须为 `{TAIL_KEEP}` 或 `余韵N秒`（1 ≤ N ≤ {TAIL_MAX_S}），实为 {tail!r}")
            tail_s = int(mt.group(1))
        volumes.append(Volume(len(volumes) + 1, dirname, years, budget, (a, b), tail, tail_s))
    if volumes and volumes[-1].tail_s is not None:
        raise ParseError(f"卷表末行（第 {len(volumes)} 卷）片尾必须是 `{TAIL_KEEP}`（末卷片尾完整保留、不裁）")
    if collection is not None and name_key(collection) in keys:
        raise ParseError(f"`合集` {collection!r} 与第 {keys[name_key(collection)]} 卷目录相同")
    return ProjectDoc(name, collection, tuple(volumes), sections)


def machine_fields(doc: ProjectDoc) -> tuple:
    """机读字段 = (企划, 合集, 卷表全部行)；解析后比较，空白与表格对齐不算变化。"""
    return (doc.name, doc.collection, tuple(v.cells() for v in doc.volumes))


def diff_machine(before: ProjectDoc | None, after: ProjectDoc) -> list[str]:
    """机读字段逐行 diff（卡面用）；相同返回 []。before=None 表示首次立项或原文件不可解析。"""
    if before is not None and machine_fields(before) == machine_fields(after):
        return []
    lines: list[str] = []
    if before is None:
        lines.append(f"+ 企划: {after.name}")
        if after.collection is not None:
            lines.append(f"+ 合集: {after.collection}")
        lines.extend("+ | " + " | ".join(v.cells()) + " |" for v in after.volumes)
        if not after.volumes:
            lines.append("  （卷表为空）")
        return lines
    for label, old, new in (("企划", before.name, after.name), ("合集", before.collection, after.collection)):
        if old != new:
            lines.append(f"- {label}: {old if old is not None else '（无）'}")
            lines.append(f"+ {label}: {new if new is not None else '（无）'}")
    old_rows = [v.cells() for v in before.volumes]
    new_rows = [v.cells() for v in after.volumes]
    for i in range(max(len(old_rows), len(new_rows))):
        o = old_rows[i] if i < len(old_rows) else None
        n = new_rows[i] if i < len(new_rows) else None
        if o == n:
            continue
        if o is not None:
            lines.append("- | " + " | ".join(o) + " |")
        if n is not None:
            lines.append("+ | " + " | ".join(n) + " |")
    return lines


def members_vs_table(project_dir: Path, doc: ProjectDoc) -> list[str]:
    """卷表与成员目录对不上的提示（状态卡用；只提示、不拦——先写卷表再建卷是正常顺序）。"""
    members = {name_key(m.name): m.name for m in project_members(project_dir)}
    listed = {name_key(v.dir): v.dir for v in doc.volumes}
    if doc.collection is not None:
        listed.setdefault(name_key(doc.collection), doc.collection)
    hints = [f"卷表列了 `{listed[k]}` 但目录不存在" for k in listed if k not in members]
    hints += [f"目录 `{members[k]}` 不在卷表" for k in members if k not in listed]
    return hints


def load(project_dir: Path) -> ProjectDoc | ParseError | None:
    """读并解析企划目录的 `00-project.md`：不存在 None，不可解析返回 ParseError 实例（不抛）。"""
    path = Path(project_dir) / PROJECT_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        return ParseError(f"读不了 {PROJECT_FILE}：{exc}")
    try:
        return parse(text)
    except ParseError as exc:
        return exc


def volume_seq(project_dir: Path, volume_name: str) -> int | None:
    """卷在卷表里的卷序；企划文件缺失 / 不可解析 / 不在卷表返回 None。"""
    doc = load(project_dir)
    if not isinstance(doc, ProjectDoc):
        return None
    key = name_key(volume_name)
    return next((v.seq for v in doc.volumes if name_key(v.dir) == key), None)
