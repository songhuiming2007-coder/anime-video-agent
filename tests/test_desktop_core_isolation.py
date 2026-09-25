"""Spec 8 PR0：core 隔离守卫（TC-1、TC-2）。

桌面端（desktop/）只经 spawn 调既有 CLI，core 不为 UI 开端口、不引入 server / 数据库栈。
TC-4（本 spec 各 PR 对 pipeline/、config/ 的 diff 为空）由各 PR 的验证命令承担，不在此处。
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent

# server / web 框架 / 数据库驱动的发行包名（PEP 503 规范化后比较）
_FORBIDDEN_DISTS = frozenset({
    "fastapi", "uvicorn", "flask", "starlette", "aiohttp", "websockets", "django",
    "tornado", "sanic", "gunicorn", "hypercorn", "quart", "bottle", "falcon",
    "sqlalchemy", "sqlmodel", "peewee", "psycopg", "psycopg2", "psycopg2-binary",
    "pymysql", "mysqlclient", "pymongo", "redis", "aiosqlite", "duckdb",
})


def _dist_name(requirement: str) -> str:
    """'numpy>=2.0,<2.5' / 'foo[bar]; python_version>"3"' → 'numpy' / 'foo'（PEP 503 规范化）。"""
    name = re.split(r"[\s\[<>=!~;@]", requirement.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def test_core_imports_no_server_stack():
    """TC-1：core 热路径不因桌面端载入任何 server / 数据库 / ML 栈（独立子进程，防共享进程假阳性）。"""
    probe = (
        "import sys, pipeline.agent.cli, pipeline.status, pipeline.review; "
        "bad = ('http.server','socketserver','wsgiref','asyncio','websockets','aiohttp',"
        "'fastapi','uvicorn','flask','starlette','sqlite3','numpy','torch'); "
        "leaked = [m for m in bad if m in sys.modules]; "
        "assert not leaked, leaked"
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=REPO_ROOT)
    assert r.returncode == 0, r.stderr


def test_pyproject_declares_no_server_or_db_packages():
    """TC-2：pyproject.toml 的 dependencies 与全部 optional-dependencies 中无 server / DB 包。"""
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    reqs = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        reqs.extend(group)
    assert reqs, "pyproject.toml 未解析出任何依赖，守卫失效"
    names = {_dist_name(r) for r in reqs}
    assert "numpy" in names  # 解析器自检：已知依赖必须被识别出来
    leaked = sorted(names & _FORBIDDEN_DISTS)
    assert not leaked, f"pyproject.toml 声明了 server/DB 依赖：{leaked}"
