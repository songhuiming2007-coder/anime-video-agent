"""纯函数测试：tools/motion_render.py 与 tools/motion/{runtime,gl}.js 静态与行为不变量。

不依赖 Playwright、ffmpeg 或外置数据盘，三平台 CI 直接运行（JS 行为单测在检测到 node 时自动执行）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

from PIL import Image
import pytest

from tools import motion_render as MR


SAMPLE_SCRIPT = """# 测试期：行星生物

## 段落 0
配音：开场导语（位于首个章节前）。

## 章节 · 宇宙尽头与宿命起航
### 子节 · Track 12《Departures》与终章

## 音乐段 B18 · Departures 吉他版铺底
音乐: `Departures (Acoustic Ver.)` 完整版 00:00
状态: 背景铺底

## 段落 152
情绪: 低沉克制
语速: 中

配音：听完这一张专辑后，回看那张封面插画。

画面:
  锚点: 2026-11-11-EGOIST-patch SP24 0:00

## 段落 153
情绪: 平静叙述
语速: 中

配音：放在纯商业流行碟的标准下，把这么多风格塞进四十八分钟里，曲风跨度确实大到有些任性。

画面:
  锚点: 2026-11-11-EGOIST-patch SP172 0:00
  查询: 十二首歌在极快舞曲和极静民谣之间来回剧烈摆动

## 段落 154
情绪: 平静叙述
语速: 中

配音：但换个角度想，这四十八分五十六秒就是唯一一次机会。

画面:
  锚点: 2026-11-11-EGOIST-patch SP23 00:00
"""


def test_motion_render_import_has_no_heavy_deps():
    """导入 tools.motion_render 不拖入 playwright / torch / numpy。"""
    code = (
        "import sys; from tools import motion_render; "
        "bad = [m for m in ('playwright', 'torch', 'numpy') if m in sys.modules]; "
        "assert not bad, f'违规导入: {bad}'"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_frame_count_ceil_at_23976():
    assert MR.frame_count(8.492, Fraction(24000, 1001)) == 204
    assert MR.frame_count(1.0, Fraction(24000, 1001)) == 24
    assert MR.frame_count(17.0, Fraction(24000, 1001)) == 408


def test_script_has_chapters_and_ignores_end_marker():
    assert MR.script_has_chapters(SAMPLE_SCRIPT) is True
    assert MR.script_has_chapters("## 段落 1\n配音：你好\n") is False
    assert MR.script_has_chapters("## 章节结束\n## 段落 1\n配音：你好\n") is False


def test_parse_script_context_extracts_music_chapter_and_neighbors():
    ctx = MR.parse_script_context(SAMPLE_SCRIPT, 153)
    assert ctx["seg"] == 153
    assert ctx["has_chapters"] is True
    assert ctx["doc_has_chapters"] is True
    assert ctx["has_music_blocks"] is True

    cur = ctx["current"]
    assert cur is not None
    assert cur["chapter"] == "宇宙尽头与宿命起航"
    assert cur["subchapter"] == "Track 12《Departures》与终章"
    assert cur["music"]["heading"] == "B18 · Departures 吉他版铺底"
    assert cur["music"]["音乐"] == "`Departures (Acoustic Ver.)` 完整版 00:00"
    assert cur["music"]["状态"] == "背景铺底"
    assert cur["emotion"] == "平静叙述"
    assert cur["speed"] == "中"
    assert "四十八分钟" in cur["text"]
    assert cur["anchor"] == "2026-11-11-EGOIST-patch SP172 0:00"

    assert ctx["prev"]["index"] == 152
    assert ctx["prev"]["emotion"] == "低沉克制"
    assert ctx["prev"]["anchor"] == "2026-11-11-EGOIST-patch SP24 0:00"
    assert ctx["next"]["index"] == 154
    assert ctx["next"]["anchor"] == "2026-11-11-EGOIST-patch SP23 00:00"

    # 位于首个 `## 章节` 之前和 `## 章节结束` 之后的段落，段级 has_chapters 应为 False
    ctx_pre = MR.parse_script_context(SAMPLE_SCRIPT, 0)
    assert ctx_pre["has_chapters"] is False
    assert ctx_pre["doc_has_chapters"] is True

    after_end = SAMPLE_SCRIPT + "\n## 章节结束\n\n## 段落 155\n配音：下期预告。\n"
    ctx_end = MR.parse_script_context(after_end, 155)
    assert ctx_end["has_chapters"] is False
    assert ctx_end["current"]["chapter"] == ""
    assert ctx_end["current"]["subchapter"] == ""


def test_parse_script_context_music_bed_semantics():
    """对齐 pipeline/music.py：同源试看槽只让路不覆盖原床；前景淡出清空原床；降为 BGM 接管新床。"""
    script = """# 音乐床语义回归测试

## 音乐段 M0 · 片头铺底
音乐: `Heart Pattern -instrumental-` 完整版 00:00
状态: 背景铺底

## 段落 1
配音：段一在 M0 铺底下。

## 音乐段 M1 · 更衣室试看
源片原声: `伪恋 S01E07` 14:43-15:08
状态: 前景试听，旁白停止
音画同源: 是
过渡: 淡出
画面: 同源

## 段落 2
配音：同源试看结束后，M0 铺底继续接管（复现束胸力学 seg 7 场景）。

## 音乐段 M2 · 前景淡出块
音乐: `βίος` 00:30-01:00
过渡: 结尾自然淡出

## 段落 3
配音：纯前景淡出后无当前铺底床（复现罪恶王冠乐评 seg 2 场景）。

## 音乐段 M3 · 前景降为 BGM
音乐: `Euterpe` 00:00-00:40
过渡: 降为 BGM 继续铺底

## 段落 4
配音：M3 降为 BGM 后成为新床。
"""
    c1 = MR.parse_script_context(script, 1)
    assert c1["current"]["music"]["heading"] == "M0 · 片头铺底"

    c2 = MR.parse_script_context(script, 2)
    assert c2["current"]["music"]["heading"] == "M0 · 片头铺底"
    assert "Heart Pattern" in c2["current"]["music"]["音乐"]

    c3 = MR.parse_script_context(script, 3)
    assert c3["current"]["music"] == {}

    c4 = MR.parse_script_context(script, 4)
    assert c4["current"]["music"]["heading"] == "M3 · 前景降为 BGM"


def test_parse_topic_bgm_list_and_legacy_slots():
    list_topic = """番: 伪恋
BGM:
  - Heart Pattern -instrumental-
  - STEP -Instrumental- @120秒
BGM结束: 占位
"""
    got = MR.parse_topic_bgm(list_topic)
    assert got is not None
    assert got["source"] == "01-topic.md"
    assert got["tracks"] == ["Heart Pattern -instrumental-", "STEP -Instrumental- @120秒"]
    assert "Heart Pattern" in got["音乐"]

    legacy_topic = "番: 春物\nBGM正文: ユキトキ\nBGM结尾: Hello Alone\n"
    got2 = MR.parse_topic_bgm(legacy_topic)
    assert got2 is not None
    assert "BGM正文=ユキトキ" in got2["音乐"]

    assert MR.parse_topic_bgm("番: EGOIST\n类型: 纪录片\n") is None


def test_resolve_segment_wav_zero_padded_and_manifest(tmp_path: Path):
    """P0 #1 回归：1–9 段默认落盘为 seg-07.wav，且优先认 manifest.json 的 file 字段。"""
    audio = tmp_path / "03-audio"
    audio.mkdir()
    (audio / "seg-07.wav").write_bytes(b"wav7")
    (audio / "seg-153.wav").write_bytes(b"wav153")
    (audio / "custom-08.wav").write_bytes(b"wav8")

    assert MR.resolve_segment_wav(tmp_path, 7) == audio / "seg-07.wav"
    assert MR.resolve_segment_wav(tmp_path, 153) == audio / "seg-153.wav"
    assert MR.resolve_segment_wav(tmp_path, 9) is None

    mf = {"segments": [{"index": 8, "file": "custom-08.wav"}]}
    assert MR.resolve_segment_wav(tmp_path, 8, mf) == audio / "custom-08.wav"


def test_find_fallback_thumb_zero_padded_and_numeric_sort(tmp_path: Path):
    """P0 #1 回归：04-thumbs 零填充 s07c* 命中且按 clip/帧号数值排序（s07c2 排在 s07c10 前）。"""
    thumbs = tmp_path / "04-thumbs"
    thumbs.mkdir()
    for name in (
        "s07c0-10.00-5.00-0.jpg",
        "s07c2-20.00-5.00-1.jpg",
        "s07c10-30.00-5.00-0.jpg",
        "s07c10-30.00-5.00-2.jpg",
        "s153c0-1.00-3.00-0.jpg",
    ):
        (thumbs / name).write_bytes(b"jpg")

    assert MR.find_fallback_thumb(thumbs, 7, want_last=False).name == "s07c0-10.00-5.00-0.jpg"
    assert MR.find_fallback_thumb(thumbs, 7, want_last=True).name == "s07c10-30.00-5.00-2.jpg"
    assert MR.find_fallback_thumb(thumbs, 153, want_last=False).name == "s153c0-1.00-3.00-0.jpg"
    assert MR.find_fallback_thumb(thumbs, 99, want_last=False) is None


def test_neighbor_clip_targets_uses_prev_tail_and_next_head():
    clips = {
        "segments": [
            {
                "index": 152,
                "clips": [
                    {"source": "a.mp4", "start": 0.0, "dur": 4.0},
                    {"source": "prev_last.mp4", "start": 10.0, "dur": 10.0},
                ],
            },
            {"index": 153, "clips": [{"source": "mid.mp4", "start": 0.0, "dur": 8.5}]},
            {
                "index": 154,
                "clips": [
                    {"source": "next_first.mp4", "start": 5.0, "dur": 2.0},
                    {"source": "b.mp4", "start": 0.0, "dur": 6.0},
                ],
            },
        ]
    }
    tgt = MR.neighbor_clip_targets(clips, 153, pad=3.0)
    assert tgt["prev"]["source"] == "prev_last.mp4"
    assert tgt["prev"]["same_seg"] is False
    assert tgt["prev"]["frame_t"] == pytest.approx(19.5)
    assert tgt["prev"]["splice_start"] == pytest.approx(17.0)
    assert tgt["prev"]["splice_dur"] == pytest.approx(3.0)

    # 后段只有 2.0s < pad 3.0s，应钳制到 2.0s，不越界
    assert tgt["next"]["source"] == "next_first.mp4"
    assert tgt["next"]["same_seg"] is False
    assert tgt["next"]["frame_t"] == pytest.approx(5.5)
    assert tgt["next"]["splice_start"] == pytest.approx(5.0)
    assert tgt["next"]["splice_dur"] == pytest.approx(2.0)


def test_neighbor_clip_targets_multi_anchor_intra_segment():
    """多锚点段（如束胸力学 seg 7）切出点应指向同段下一兄弟镜头并计算准确段内音频偏移。"""
    clips = {
        "segments": [
            {"index": 6, "clips": [{"source": "ep07.mkv", "start": 100.0, "dur": 10.0}]},
            {
                "index": 7,
                "clips": [
                    {"source": "/abs/patch_assets/binding_pressure-motion.mp4", "start": 0.0, "dur": 11.264},
                    {"source": "ep07.mkv", "start": 347.097, "dur": 5.744},
                ],
            },
            {"index": 8, "clips": [{"source": "ep07.mkv", "start": 752.335, "dur": 13.503}]},
        ]
    }
    tgt = MR.neighbor_clip_targets(clips, 7, pad=3.0, motion_name="binding_pressure-motion.mp4")
    assert tgt["prev"]["seg"] == 6
    assert tgt["prev"]["same_seg"] is False
    assert tgt["motion_clip"]["dur"] == pytest.approx(11.264)
    assert tgt["motion_clip"]["audio_offset"] == pytest.approx(0.0)
    assert tgt["next"]["seg"] == 7
    assert tgt["next"]["same_seg"] is True
    assert tgt["next"]["source"] == "ep07.mkv"
    assert tgt["next"]["splice_start"] == pytest.approx(347.097)
    assert tgt["next"]["audio_offset"] == pytest.approx(11.264)


def test_neighbor_clip_targets_handles_missing_neighbors():
    clips = {"segments": [{"index": 1, "clips": [{"source": "only.mp4", "start": 0.0, "dur": 5.0}]}]}
    tgt = MR.neighbor_clip_targets(clips, 1)
    assert tgt["prev"] is None
    assert tgt["next"] is None


def test_discover_fonts_required_and_optional(tmp_path: Path):
    # 空目录：必需字体保留（由加载层报错），可选字体不虚报
    empty = MR.discover_fonts(tmp_path)
    assert [(fam, req) for fam, _, req in empty] == [("MotionSans", True)]

    # 放入必需 + 两个可选字体后均被识别
    (tmp_path / "SourceHanSansSC-Bold.otf").write_bytes(b"fake")
    (tmp_path / "Songti.ttc").write_bytes(b"fake")
    (tmp_path / "Menlo.ttc").write_bytes(b"fake")
    found = MR.discover_fonts(tmp_path)
    fams = [fam for fam, _, _ in found]
    assert fams == ["MotionSans", "MotionSerif", "MotionMono"]
    assert all(p.is_file() for _, p, _ in found)


def test_build_motion_payload_safe_zones():
    with_chap = MR.build_motion_payload(
        duration=8.492,
        sentences=[{"text": "a", "start": 0.0, "duration": 4.0}],
        has_chapters=True,
        params={"theme": "dark"},
        fonts=["MotionSans", "MotionSerif"],
    )
    assert with_chap["hasChapters"] is True
    assert with_chap["safeTop"] == MR.CHAPTER_BAR_H == 64
    assert with_chap["safeBottom"] == MR.H - MR.SUB_BAND
    assert with_chap["fonts"] == ["MotionSans", "MotionSerif"]

    no_chap = MR.build_motion_payload(
        duration=5.0, sentences=[], has_chapters=False, params={}
    )
    assert no_chap["hasChapters"] is False
    assert no_chap["safeTop"] == 0
    assert no_chap["chapterBarHeight"] == 64


def test_contact_grid_shapes():
    assert MR.contact_grid(1) == (1, 1)
    assert MR.contact_grid(2) == (2, 1)
    assert MR.contact_grid(3) == (3, 1)
    assert MR.contact_grid(4) == (2, 2)
    assert MR.contact_grid(6) == (3, 2)
    assert MR.contact_grid(7) == (3, 3)


def test_analyze_frame_palette_dark_and_light():
    # 纯暗夜色 + 一小条猩红
    dark_img = Image.new("RGB", (320, 180), (16, 18, 24))
    for y in range(85, 95):
        for x in range(320):
            dark_img.putpixel((x, y), (210, 30, 45))
    st_dark = MR.analyze_frame_palette(dark_img, n_colors=3)
    assert st_dark["tone"] == "dark"
    assert st_dark["mean_luma"] < 40
    assert st_dark["palette"][0]["hex"] == "#101218"
    assert any(p["hex"].startswith("#d") for p in st_dark["palette"])

    light_img = Image.new("RGB", (320, 180), (244, 239, 228))
    st_light = MR.analyze_frame_palette(light_img, n_colors=2)
    assert st_light["tone"] == "light"
    assert st_light["mean_luma"] > 220


def test_is_inside_patch_assets_case_and_symlink(tmp_path: Path):
    assert MR.is_inside_patch_assets(tmp_path / "patch_assets" / "splice.mp4") is True
    assert MR.is_inside_patch_assets(tmp_path / "patch_assets" / "sub" / "a.mp4") is True
    assert MR.is_inside_patch_assets(tmp_path / "Patch_Assets" / "splice.mp4") is True
    assert MR.is_inside_patch_assets(tmp_path / "motion" / "_scratch" / "splice.mp4") is False

    # 目录内软链指向目录外仍应拦住（ingest_patch 会扫该目录）
    pa = tmp_path / "patch_assets"
    pa.mkdir()
    outside = tmp_path / "outside.mp4"
    link = pa / "link.mp4"
    link.symlink_to(outside)
    assert MR.is_inside_patch_assets(link) is True


def test_pick_ordinary_bgm_at_tracks_offset_and_loop():
    """普通期 BGM 计划应按前序段落累计时刻选轨、扣减曲内偏移并在超曲长时取模循环。"""
    p1 = Path("/bgm/track1.flac")
    p2 = Path("/bgm/track2.flac")
    plan = [
        ("Track 1", p1, 100.0, -12.0, 0.0, 10.0),   # 有效曲长 90s，覆盖 [0, 90)
        ("Track 2", p2, 80.0, -14.5, 90.0, 5.0),    # 有效曲长 75s，自 90s 接棒
    ]
    assert MR.pick_ordinary_bgm_at([], 10.0) is None

    # 落在首轨第 25s -> 曲内 10 + 25 = 35s
    hit1 = MR.pick_ordinary_bgm_at(plan, 25.0)
    assert hit1 == ("Track 1", p1, pytest.approx(35.0), -12.0)

    # 落在次轨 102s -> 经过 12s -> 曲内 5 + 12 = 17s
    hit2 = MR.pick_ordinary_bgm_at(plan, 102.0)
    assert hit2 == ("Track 2", p2, pytest.approx(17.0), -14.5)

    # 超出次轨有效曲长 (90 + 75 = 165s) -> 循环取模：175 - 90 = 85s -> 85 % 75 = 10 -> 曲内 5 + 10 = 15s
    hit3 = MR.pick_ordinary_bgm_at(plan, 175.0)
    assert hit3 == ("Track 2", p2, pytest.approx(15.0), -14.5)


def test_parse_cli_params_and_stills_times():
    assert MR.parse_cli_params(["a=1", "b=x=y"]) == {"a": "1", "b": "x=y"}
    with pytest.raises(ValueError, match="缺少 '='"):
        MR.parse_cli_params(["broken"])
    with pytest.raises(ValueError, match="键名不能为空"):
        MR.parse_cli_params(["=val"])

    assert MR.parse_stills_times("0, 0.5, 2.5") == [0.0, 0.5, 2.5]
    for bad in ("", " , ", "abc", "-1.0", "nan", "inf"):
        with pytest.raises(ValueError):
            MR.parse_stills_times(bad)


def test_render_contact_sheet_and_context_sheet_without_system_font(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """强制缺失 FONT_FILE 模拟三平台无字体 CI 路径，并验证超长文本不崩且正常截断。"""
    monkeypatch.setattr(MR, "FONT_FILE", tmp_path / "missing-font.otf")
    im1 = Image.new("RGB", (640, 360), (20, 24, 34))
    im2 = Image.new("RGB", (640, 360), (220, 190, 150))
    im3 = Image.new("RGB", (640, 360), (40, 80, 120))
    im4 = Image.new("RGB", (640, 360), (180, 50, 50))
    sheet = MR.render_contact_sheet(
        [("t=0.50s", im1), ("t=2.50s", im2), ("t=5.00s", im3), ("t=7.50s", im4)],
        tmp_path / "out-contact.png",
        max_width=960,
        font_path=None,
    )
    assert sheet.is_file()
    with Image.open(sheet) as out_im:
        assert 900 <= out_im.width <= 960
        assert out_im.height > 400

    ctx = MR.parse_script_context(SAMPLE_SCRIPT, 153)
    ctx["duration"] = 8.492
    ctx["sentences"] = [{"text": "a", "start": 0.0, "duration": 4.0}, {"text": "b", "start": 4.0, "duration": 4.492}]
    ctx["current"]["music"]["音乐"] = "`Very Long Track Name " + ("X" * 160) + "` 完整版 00:00"
    ctx_png = MR.render_context_sheet(
        ctx,
        im1,
        None,  # 测试缺后帧时不崩且照常落盘
        MR.analyze_frame_palette(im1),
        None,
        tmp_path / "context-seg153.png",
        font_path=None,
    )
    assert ctx_png.is_file()
    with Image.open(ctx_png) as c_im:
        assert c_im.width == 1920
        assert c_im.height > 700


def test_motion_js_libraries_are_deterministic_and_free_of_banned_apis():
    """runtime.js 与 gl.js 自身绝不调用被 GUARD_JS 拦截的非确定性 API。"""
    banned = re.compile(r"\b(Math\.random|Date\.now|new\s+Date|performance\.now|requestAnimationFrame)\b")
    for js_path in (MR.RUNTIME_JS, MR.GL_JS):
        assert js_path.is_file(), f"缺少运行时库 {js_path}"
        # 去掉行注释再查（注释里可能解释为什么不用 Math.random）
        code_only = "\n".join(
            re.sub(r"//.*", "", line) for line in js_path.read_text(encoding="utf-8").splitlines()
        )
        m = banned.search(code_only)
        assert m is None, f"{js_path.name} 包含禁用非确定性调用: {m.group(0)}"

    rt_src = MR.RUNTIME_JS.read_text(encoding="utf-8")
    for sym in ("clamp", "lerp", "easeInOut", "easeOut", "prog", "rng", "spring", "impulse", "stagger", "camera2d", "makeGrainPattern"):
        assert sym in rt_src, f"runtime.js 缺少导出 {sym}"

    gl_src = MR.GL_JS.read_text(encoding="utf-8")
    for sym in ("createContext", "compileShader", "createProgram", "createFullscreenQuad", "mat4", "preserveDrawingBuffer"):
        assert sym in gl_src, f"gl.js 缺少导出 {sym}"


def test_motion_js_runtime_and_gl_math_and_safety():
    """真实执行 runtime.js 与 gl.js，验证语法、弹簧/矩阵数学、异常参数防呆与 WebGL 防泄漏。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("当前环境未安装 node，跳过 JS 行为单测")

    probe = f"""
const fs = require('fs');
const assert = require('assert');
global.window = {{}};

// Mock HTMLCanvasElement.prototype.getContext 验证原生 WebGL2 拦截与去重
const createdAttrs = [];
const mockGLObj = {{ isContextLost: () => false, finish: () => {{}} }};
global.HTMLCanvasElement = function HTMLCanvasElement() {{}};
global.HTMLCanvasElement.prototype.getContext = function(type, opts) {{
  createdAttrs.push({{ type, opts }});
  return mockGLObj;
}};

// Mock document.createElement('canvas') 验证 makeGrainPattern 的 opacity 与 fiberColor
let lastStrokeStyle = null;
global.document = {{
  createElement() {{
    return {{
      width: 0,
      height: 0,
      getContext() {{
        return {{
          createImageData(w, h) {{ return {{ data: new Uint8ClampedArray(w * h * 4) }}; }},
          putImageData() {{}},
          set strokeStyle(v) {{ lastStrokeStyle = v; }},
          get strokeStyle() {{ return lastStrokeStyle; }},
          lineWidth: 1,
          beginPath() {{}}, moveTo() {{}}, lineTo() {{}}, stroke() {{}},
        }};
      }},
    }};
  }},
}};

eval(fs.readFileSync({json.dumps(str(MR.RUNTIME_JS))}, 'utf8'));
eval(fs.readFileSync({json.dumps(str(MR.GL_JS))}, 'utf8'));

const m = window.motion;
const g = window.motionGL;

// 1. spring 欠阻尼、临界、过阻尼与非法参数抛错
assert.strictEqual(m.spring(0, 0), 0);
assert.strictEqual(m.spring(-1, 0), 0);
assert.ok(m.spring(0.24, 0, {{ stiffness: 180, damping: 12 }}) > 1.0, '欠阻尼应有过冲');
assert.ok(Math.abs(m.spring(5, 0) - 1.0) < 1e-4, '稳态应收敛到 1');
const over1 = m.spring(0.2, 0, {{ stiffness: 100, damping: 40 }});
const over2 = m.spring(0.6, 0, {{ stiffness: 100, damping: 40 }});
assert.ok(over1 > 0 && over1 < over2 && over2 < 1.0, '过阻尼应单调递增趋近 1');
for (const bad of [{{ stiffness: 0 }}, {{ mass: 0 }}, {{ stiffness: -1 }}, {{ damping: -2 }}, {{ stiffness: NaN }}]) {{
  assert.throws(() => m.spring(0.5, 0, bad), RangeError);
}}

// 2. stagger / remap / smoothstep / impulse / rng 确定性
assert.strictEqual(m.stagger(1.0, 2, 0.0, 0.5, 1.0), 0);
assert.strictEqual(m.stagger(2.0, 2, 0.0, 0.5, 1.0), 1);
assert.strictEqual(m.remap(5, 0, 10, 100, 200), 150);
assert.strictEqual(m.smoothstep(2, 2, 3), 1);
assert.strictEqual(m.impulse(0, 0), 0);
const r1 = m.rng(42), r2 = m.rng(42);
assert.strictEqual(r1(), r2());
assert.strictEqual(r1(), r2());

// 3. makeGrainPattern 支持 opacity 与自定义 fiberColor
const fakeCtx = {{ createPattern(tile, rep) {{ return {{ w: tile.width, rep }}; }} }};
const pat = m.makeGrainPattern(fakeCtx, 7, {{ size: 8, opacity: 0.2, fibers: 3, fiberColor: 'rgba(230,225,215,0.05)' }});
assert.strictEqual(pat.w, 16, 'size 应钳制到 >= 16');
assert.strictEqual(lastStrokeStyle, 'rgba(230,225,215,0.05)');

// 4. 原生 getContext('webgl2') 自动注入 preserveDrawingBuffer 且去重登记
const c = new HTMLCanvasElement();
const rawGl = c.getContext('webgl2');
assert.strictEqual(rawGl, mockGLObj);
assert.strictEqual(createdAttrs[0].opts.preserveDrawingBuffer, true);
assert.strictEqual(window.__MOTION_GL_CONTEXTS__.length, 1);
g.createContext(c);
assert.strictEqual(window.__MOTION_GL_CONTEXTS__.length, 1, '同一 gl 不应重复登记');

// 5. mat4 数学、俯视共线 up 自动切换备用轴、退化参数抛错
const topDown = g.mat4.lookAt([0, 5, 0], [0, 0, 0], [0, 1, 0]);
const row0Len = Math.hypot(topDown[0], topDown[4], topDown[8]);
const row1Len = Math.hypot(topDown[1], topDown[5], topDown[9]);
const row2Len = Math.hypot(topDown[2], topDown[6], topDown[10]);
assert.ok(Math.abs(row0Len - 1) < 1e-5 && Math.abs(row1Len - 1) < 1e-5 && Math.abs(row2Len - 1) < 1e-5, '正俯视矩阵必须保持正交单位基');
assert.throws(() => g.mat4.lookAt([1, 2, 3], [1, 2, 3]), RangeError);
assert.throws(() => g.mat4.perspective(0, 16/9, 0.1, 100), RangeError);
assert.throws(() => g.mat4.perspective(1.0, 16/9, 10, 5), RangeError);

const persp = g.mat4.perspective(Math.PI / 3, 16 / 9, 0.1, 100);
const prod = g.mat4.multiply(g.mat4.identity(), persp);
for (let i = 0; i < 16; i++) assert.ok(Math.abs(prod[i] - persp[i]) < 1e-6);

// 6. createProgram 片段编译失败必删已建 vs；createFullscreenQuad 找不到属性前不建 VAO/buffer
const deleted = [];
let vaoCreated = 0;
const fakeGL = {{
  VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4,
  createShader(t) {{ return {{ kind: t }}; }},
  shaderSource() {{}},
  compileShader() {{}},
  getShaderParameter(s) {{ return s.kind === 1; }}, // vs 成功、fs 失败
  getShaderInfoLog() {{ return 'bad frag'; }},
  deleteShader(s) {{ deleted.push(s.kind); }},
  getAttribLocation() {{ return -1; }},
  createVertexArray() {{ vaoCreated++; return {{}}; }},
}};
assert.throws(() => g.createProgram(fakeGL, 'vs', 'fs'), /FRAGMENT/);
assert.deepStrictEqual(deleted, [2, 1], 'fs 失败时必须同时清理 fs 与已建 vs');
assert.throws(() => g.createFullscreenQuad(fakeGL, {{}}, 'a_missing'), /找不到顶点属性/);
assert.strictEqual(vaoCreated, 0, '找不到属性前不应分配 VAO');
"""
    r = subprocess.run([node, "-e", probe], capture_output=True, text=True)
    assert r.returncode == 0, f"JS 行为单测失败:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
