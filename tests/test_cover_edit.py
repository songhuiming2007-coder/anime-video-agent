"""Spec 12 PR1：图片导入（TC-1/TC-2）、确定性渲染（TC-3~TC-5）、字体纪律（TC-6）、纯洁性（TC-7）。

夹具纪律（Spec 12 §7.1）：源图用测试内 Pillow 现场生成的最小合法 PNG；
字体走 `config/project.json` 的 `cover.font_file`，**缺席时单个用例显式 skip 并打印原因**
（判据 9：跳过必须显式列出，不算通过）；渲染基线用例在 Pillow 或字体文件变更后必须重跑回填
（RF-2：基线的红是信号，不是脆弱）。
"""

from __future__ import annotations

import io
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from pipeline import cover_edit, paths
from pipeline.agent import cli

REPO_ROOT = Path(__file__).resolve().parent.parent

# TC-3 渲染基线（2026-09-26 首日回填：思源黑体 SC Bold + Pillow 12.3.0，跨两个独立进程实测一致）。
# 改字体、改 font_index、改 MARGIN_PX、改锚点映射、升 Pillow —— 这个值必须重跑回填。
BASELINE_SHA256 = "c0f96a584668913d01a9363ad4a567eba954bdaaf30169a53d85d2d4d3ec51c7"

BASELINE_PARAMS: dict = {
    "source": "07-cover/import/bg.png",
    "output": "07-cover/edit-1.png",
    "lines": [
        {
            "text": "标题压在左上",
            "size": 96,
            "anchor": "tl",
            "color": "#FFFFFF",
            "stroke_width": 6,
            "stroke_color": "#000000",
        },
        {
            "text": "副标题在左下",
            "size": 40,
            "anchor": "bl",
            "dx": 10,
            "dy": -20,
            "color": "#FFEE00",
            "stroke_width": 3,
        },
    ],
}

# 子进程探针：自己造夹具 → 渲染 → 打印结果。参数经 argv 传入，与 BASELINE_PARAMS 同源。
_PROBE = """
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw
from pipeline import cover_edit

ep = Path(sys.argv[1])
(ep / "07-cover" / "import").mkdir(parents=True)
img = Image.new("RGB", (1280, 720), (18, 18, 24))
d = ImageDraw.Draw(img)
d.rectangle([0, 480, 1280, 720], fill=(40, 24, 60))
d.rectangle([80, 80, 400, 320], fill=(200, 90, 40))
img.save(ep / "07-cover" / "import" / "bg.png", format="PNG")
print(json.dumps(cover_edit.render_cover(ep, json.loads(sys.argv[2])), ensure_ascii=False))
"""


class _FakeStdin:
    """裸形态 stdin 替身：图片字节走 `.buffer`（host spawn 的 pipe 形状）。"""

    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)

    def isatty(self) -> bool:
        return False


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """假仓库根：data/episodes/ep-cover + 指向真实字体目录的符号链接（不复制 17MB）。"""
    ep = tmp_path / "data" / "episodes" / "ep-cover"
    ep.mkdir(parents=True)
    fonts = Path(paths.ROOT) / "data" / "fonts"
    if fonts.is_dir():
        (tmp_path / "data" / "fonts").symlink_to(fonts)
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    return ep


def _font_missing_reason() -> str | None:
    rel = str(paths.conf("cover.font_file", cover_edit.DEFAULT_FONT_FILE))
    path = REPO_ROOT / rel
    if not path.is_file():
        return f"正式字体缺席（config cover.font_file={rel}，期望 {path}）——渲染基线/锚点用例显式跳过"
    return None


def _png_bytes(size: tuple[int, int] = (64, 36), color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _big_pixel_png_bytes() -> bytes:
    """40M+ 像素但字节很小：1-bit 全零图（PNG 压缩后仅几 KB）。"""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("1", (7000, 6000)).save(buf, format="PNG")
    return buf.getvalue()


def _png_with_bad_idat() -> bytes:
    """合法容器 + 非 zlib 乱字节 IDAT：open/verify OK，load FAIL（MUT-1 的夹具，🟡-5 实测）。"""

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)   # 8×8 RGB
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", b"\x00\x01\x02garbage")
        + chunk(b"IEND", b"")
    )


def _import_via_stdin(ep: Path, blob: bytes, name: str | None,
                      monkeypatch: pytest.MonkeyPatch) -> int:
    argv = [str(ep), "/import-cover"] + ([f"--name={name}"] if name is not None else [])
    monkeypatch.setattr(sys, "stdin", _FakeStdin(blob))
    return cli.main(argv)


def _cover_snapshot(ep: Path) -> set[str]:
    return {p.relative_to(ep).as_posix() for p in ep.rglob("*") if p.is_file()}


def _last_json(out: str) -> dict:
    return json.loads(out.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# TC-1：导入正常路径（保真、命名、不覆盖）
# ---------------------------------------------------------------------------


def test_tc1_import_preserves_bytes_and_never_overwrites(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blob = _png_bytes()

    assert _import_via_stdin(repo, blob, "封面A.png", monkeypatch) == 0
    first = _last_json(capsys.readouterr().out)
    assert first == {"path": "07-cover/import/封面A.png", "width": 64, "height": 36, "format": "PNG"}
    # 保真：落盘字节与输入逐字节相同（不重编码）
    assert (repo / first["path"]).read_bytes() == blob

    # 同名二次导入 → 追加 -2，两份都在（导入历史不可变）
    assert _import_via_stdin(repo, blob, "封面A.png", monkeypatch) == 0
    second = _last_json(capsys.readouterr().out)
    assert second["path"] == "07-cover/import/封面A-2.png"
    assert (repo / second["path"]).read_bytes() == blob
    assert (repo / first["path"]).read_bytes() == blob

    # .jpeg 规范化为 .jpg；stem 净化的纯函数面
    assert cover_edit.sanitize_stem("我的图 (1).PNG") == "我的图-1-"
    assert cover_edit.sanitize_stem("") == "img"
    assert cover_edit.sanitize_stem("../../etc/passwd") == "passwd"

    # REPL 形态：源路径由 core 读文件（REPL 内没有 stdin 管道）
    src = repo / "外部图.jpeg"
    from PIL import Image

    Image.new("RGB", (36, 64), (1, 2, 3)).save(src, format="JPEG")
    assert cli._dispatch_import_cover(repo, ["/import-cover", str(src)], from_stdin=False) == 0
    repl = _last_json(capsys.readouterr().out)
    assert repl["path"] == "07-cover/import/外部图.jpg" and repl["format"] == "JPEG"
    assert repl["width"] == 36 and repl["height"] == 64


# ---------------------------------------------------------------------------
# TC-2：导入拒绝面（退出码逐条定死，全部零落盘）
# ---------------------------------------------------------------------------


def test_tc2_import_rejections_exit_codes_and_zero_writes(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    before = _cover_snapshot(repo)

    cases: list[tuple[str, bytes, str | None, int]] = [
        ("非图字节", b"not an image", None, 1),
        ("改名换姓的伪 PNG（文件级截断）", _png_bytes()[:60], "x.png", 1),
        ("合法容器 + 乱字节 IDAT（verify OK / load FAIL）", _png_with_bad_idat(), "x.png", 1),
        ("像素超限", _big_pixel_png_bytes(), "big.png", 1),
        ("stdin 超 32 MiB", b"\x89PNG" + b"\x00" * cover_edit.IMPORT_MAX_BYTES, None, 2),
    ]
    for label, blob, name, expected in cases:
        rc = _import_via_stdin(repo, blob, name, monkeypatch)
        capsys.readouterr()
        assert rc == expected, f"{label} 应退 {expected}，实得 {rc}"
        assert _cover_snapshot(repo) == before, f"{label} 不得有任何落盘"

    # 期目录不存在 → 退 1，且绝不 mkdir 期目录（裸形态下 main 会因路径不存在而拒收，
    # 这里直调分派函数族里的 _cmd_import_cover，验的是这道闸本身）
    missing = tmp_path / "data" / "episodes" / "ep-missing"
    assert cli._cmd_import_cover(missing, "x.png", _png_bytes()) == 1
    capsys.readouterr()
    assert not missing.exists(), "导入绝不自动创建期目录"

    # 期目录越界（不在 data/episodes 之下）→ 退 1
    outside = tmp_path / "outside"
    outside.mkdir()
    assert cli._cmd_import_cover(outside, "x.png", _png_bytes()) == 1
    capsys.readouterr()
    assert not (outside / "07-cover").exists()


# ---------------------------------------------------------------------------
# TC-3：渲染基线（跨进程字节级一致）+ 字真的画上去了
# ---------------------------------------------------------------------------


def _render_in_subprocess() -> str:
    import tempfile

    ep = Path(tempfile.mkdtemp()) / "ep"
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, str(ep), json.dumps(BASELINE_PARAMS, ensure_ascii=False)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return _last_json(proc.stdout)["sha256"]


def test_tc3_render_is_byte_reproducible_across_processes(repo: Path) -> None:
    reason = _font_missing_reason()
    if reason:
        pytest.skip(reason)

    first, second = _render_in_subprocess(), _render_in_subprocess()
    assert first == second, "同参数跨两个独立进程渲染必须字节级一致（A2）"
    assert first == BASELINE_SHA256, (
        "渲染基线漂移：依赖（Pillow/字体）变了或渲染管线被改动。"
        "确认是有意改动后重跑回填 BASELINE_SHA256（RF-2）"
    )

    # 字真的画上去了：与源图逐像素比对，差异必须落在文字区域（tl 主标）
    from PIL import Image, ImageChops

    (repo / "07-cover" / "import").mkdir(parents=True)
    (repo / "07-cover" / "import" / "bg.png").write_bytes(_png_bytes((1280, 720), (18, 18, 24)))
    params = dict(BASELINE_PARAMS, output="07-cover/edit-9.png")
    cover_edit.render_cover(repo, params)
    with Image.open(repo / "07-cover" / "edit-9.png") as got, Image.open(
        repo / "07-cover" / "import" / "bg.png"
    ) as src:
        # Pillow 的坑：两张全不透明 RGBA 的 difference 的 alpha 通道恒 0，直接 getbbox() 恒 None。
        # 先 convert("L") 再取 bbox，否则这条断言会永远绿（假绿）。
        diff = ImageChops.difference(got.convert("RGBA"), src.convert("RGBA")).convert("L")
        bbox = diff.getbbox()
    assert bbox is not None, "渲染结果与源图零像素差异 = 字没画上去"
    assert bbox[0] < 700 and bbox[1] < 400, f"文字差异不在左上文字区域: {bbox}"


# ---------------------------------------------------------------------------
# TC-4：schema 校验（各被拒且零落盘；edit-* 64 上限）
# ---------------------------------------------------------------------------


def _valid_params(**over: object) -> dict:
    base = {
        "source": "07-cover/import/bg.png",
        "output": "07-cover/edit-1.png",
        "lines": [{"text": "标题", "size": 40, "anchor": "cc"}],
    }
    base.update(over)
    return base


def test_tc4_render_schema_rejections(repo: Path) -> None:
    (repo / "07-cover" / "import").mkdir(parents=True)
    (repo / "07-cover" / "import" / "bg.png").write_bytes(_png_bytes((320, 180)))

    bad_cases: list[tuple[str, dict]] = [
        ("source 父级越界", _valid_params(source="../x.png")),
        ("source 非 07-cover/", _valid_params(source="02-script.md")),
        ("source 不存在", _valid_params(source="07-cover/import/nope.png")),
        ("output 大写后缀", _valid_params(output="07-cover/edit-1.PNG")),
        ("output 不在 07-cover/", _valid_params(output="edit-1.png")),
        ("output 非法前缀", _valid_params(output="07-cover/1.png")),
        ("lines 0 条", _valid_params(lines=[])),
        ("lines 7 条", _valid_params(lines=[{"text": "t", "size": 40, "anchor": "cc"}] * 7)),
        ("size 越界", _valid_params(lines=[{"text": "t", "size": 401, "anchor": "cc"}])),
        ("颜色非法", _valid_params(lines=[{"text": "t", "size": 40, "anchor": "cc", "color": "red"}])),
        ("锚点非法", _valid_params(lines=[{"text": "t", "size": 40, "anchor": "zz"}])),
        ("顶层多余键", _valid_params(extra=1)),
        ("行内多余键", _valid_params(lines=[{"text": "t", "size": 40, "anchor": "cc", "font": "x"}])),
    ]
    for label, params in bad_cases:
        before = _cover_snapshot(repo)
        with pytest.raises(cover_edit.CoverEditError):
            cover_edit.render_cover(repo, params)
        assert _cover_snapshot(repo) == before, f"{label} 必须零落盘"

    # output 已存在 → 拒（不覆盖任何已存在文件）
    (repo / "07-cover" / "edit-1.png").write_bytes(b"already")
    with pytest.raises(cover_edit.CoverEditError, match="已存在"):
        cover_edit.render_cover(repo, _valid_params())
    assert (repo / "07-cover" / "edit-1.png").read_bytes() == b"already"

    # edit-* 达 64 上限 → 拒
    for i in range(64):
        (repo / "07-cover" / f"edit-b{i}.png").write_bytes(b"x")
    with pytest.raises(cover_edit.CoverEditError, match="上限"):
        cover_edit.render_cover(repo, _valid_params(output="07-cover/edit-c1.png"))


# ---------------------------------------------------------------------------
# TC-5：锚点语义（区域采样，不是全图 hash）
# ---------------------------------------------------------------------------


def _ink_centroid(src: Path, out: Path) -> tuple[float, float]:
    import numpy as np
    from PIL import Image, ImageChops

    with Image.open(src) as a, Image.open(out) as b:
        diff = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA")).convert("L")
    arr = np.asarray(diff, dtype=np.float64)
    ys, xs = np.nonzero(arr)
    assert xs.size > 0, "无墨迹"
    weights = arr[ys, xs]
    return float((xs * weights).sum() / weights.sum()), float((ys * weights).sum() / weights.sum())


def test_tc5_anchor_semantics(repo: Path) -> None:
    reason = _font_missing_reason()
    if reason:
        pytest.skip(reason)

    (repo / "07-cover" / "import").mkdir(parents=True)
    src = repo / "07-cover" / "import" / "bg.png"
    src.write_bytes(_png_bytes((1280, 720), (18, 18, 24)))

    centroids: dict[str, tuple[float, float]] = {}
    for i, anchor in enumerate(("tl", "cc", "br"), start=1):
        params = _valid_params(
            output=f"07-cover/edit-{i}.png",
            lines=[{"text": "锚点测试文字", "size": 64, "anchor": anchor, "stroke_width": 4}],
        )
        cover_edit.render_cover(repo, params)
        centroids[anchor] = _ink_centroid(src, repo / "07-cover" / f"edit-{i}.png")

    width, height = 1280, 720
    tl_x, tl_y = centroids["tl"]
    assert tl_x < width / 3 and tl_y < height / 3, f"tl 墨迹重心不在左上: {centroids['tl']}"
    cc_x, cc_y = centroids["cc"]
    assert abs(cc_x - width / 2) < width / 6 and abs(cc_y - height / 2) < height / 6, (
        f"cc 墨迹重心不在中心: {centroids['cc']}"
    )
    br_x, br_y = centroids["br"]
    assert br_x > width * 2 / 3 and br_y > height * 2 / 3, f"br 墨迹重心不在右下: {centroids['br']}"


# ---------------------------------------------------------------------------
# TC-6：字体纪律（缺席如实报错、不 fallback；家族名钉死）
# ---------------------------------------------------------------------------


def test_tc6_missing_font_is_honest_and_never_falls_back(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        paths,
        "_CONF",
        {"cover": {"font_file": "data/fonts/nope.otf", "font_index": 0}},
    )
    with pytest.raises(cover_edit.CoverEditError) as exc:
        cover_edit.load_cover_font(96)
    message = str(exc.value)
    assert "cover.font_file" in message, message
    assert "data/fonts/nope.otf" in message, message


def test_tc6_font_family_name_is_pinned() -> None:
    reason = _font_missing_reason()
    if reason:
        pytest.skip(reason)

    # 防 ttc index 漂移（RF-6）：index 换一个就是另一个家族，必须钉死在 config
    assert cover_edit.load_cover_font(96).getname() == ("Source Han Sans SC", "Bold")


# ---------------------------------------------------------------------------
# TC-7：依赖纯洁（独立子进程探针）
# ---------------------------------------------------------------------------


def test_tc7_no_heavy_imports_in_cover_edit_tools_cli() -> None:
    probe = (
        "import sys, pipeline.cover_edit, pipeline.agent.tools, pipeline.agent.cli; "
        "heavy = {'PIL', 'numpy', 'torch', 'mlx', 'mlx_whisper'}; "
        "leaked = sorted(m.split('.')[0] for m in sys.modules if m.split('.')[0] in heavy); "
        "assert not leaked, f'检测到违禁顶层重依赖: {leaked}'"
    )
    res = subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT, capture_output=True, text=True)
    assert res.returncode == 0, f"纯洁性探针失败:\n{res.stdout}{res.stderr}"
