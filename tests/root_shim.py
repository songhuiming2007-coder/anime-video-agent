"""测试侧「根目录替身」（D81 PR-1 起用）。

D81 §4.1 收紧期名解析后，`ava <期>` 只认落在 `paths.ROOT/data/episodes/` 下的期目录；而一批既有用例
在子进程里用真实仓库根跑 CLI、期目录却放在 `tmp_path`。这里造一个临时根：

- `config/`、`docs/`、`skills/`、`AGENTS.md` 从真实仓库**复制**（读到的规程与配置与改动前一样；不能软链——
  装配器的读域按 resolve 后的路径判，软链出根的规程会被拒载）；`pipeline/` 软链（只为 Code Freeze 判据有个落点）；
- `data/library/` 是空目录（不连真实 data/：没有记忆文件 = 不注入，也不告警）；
- `data/episodes` 软链到测试自己的期根目录（期目录的字面路径落进期根，resolve 后仍是测试原来的目录）；
- 子进程经 sitecustomize（与协议测试同一套写法）把 `paths.ROOT` 指到临时根；进程内驱动直接改 `paths.ROOT`。

只换夹具，不改任何断言。临时根以 `.` 开头，即使放在期根里也不会出现在期列表里。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_COPIED = ("config", "docs", "skills", "AGENTS.md")

HOOK = '''\
import os
from pathlib import Path

if os.environ.get("AVA_SHIM_ROOT"):
    from pipeline import paths

    paths.ROOT = Path(os.environ["AVA_SHIM_ROOT"])
    paths.DATA = paths.ROOT / "data"
'''


def shim_root(base: Path, episodes_dir: Path) -> Path:
    """在 `base/.shim-root` 造临时根，其 `data/episodes` 指向 `episodes_dir`；返回临时根。"""
    root = base / ".shim-root"
    if not root.exists():
        (root / "data" / "library").mkdir(parents=True)
        for name in _COPIED:
            src = REPO / name
            if src.is_dir():
                shutil.copytree(src, root / name, ignore=shutil.ignore_patterns("*.local.json", "__pycache__"))
            elif src.is_file():
                shutil.copy2(src, root / name)
        (root / "pipeline").symlink_to(REPO / "pipeline")
        (root / "data" / "episodes").symlink_to(episodes_dir)
    return root


def shim_episode(base: Path, ep: Path) -> Path:
    """期目录 `ep` 在临时根下的字面路径（`<临时根>/data/episodes/<期名>`）。"""
    return shim_root(base, ep.parent) / "data" / "episodes" / ep.name


def shim_env(base: Path, root: Path, env: dict[str, str] | None = None) -> dict[str, str]:
    """子进程环境：PYTHONPATH 加钩子目录，AVA_SHIM_ROOT 指临时根。"""
    hook = base / ".shim-hook"
    hook.mkdir(exist_ok=True)
    (hook / "sitecustomize.py").write_text(HOOK, encoding="utf-8")
    out = dict(os.environ if env is None else env)
    out["PYTHONPATH"] = os.pathsep.join([str(REPO), str(hook)])
    out["AVA_SHIM_ROOT"] = str(root)
    return out
