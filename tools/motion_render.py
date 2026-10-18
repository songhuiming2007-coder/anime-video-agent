#!/usr/bin/env python3
"""动效讲解素材生成器：把一张场景页（HTML）逐帧截图，合成补料用的 `*-motion.mp4`。

    uv run python tools/motion_render.py <场景.html> --episode data/episodes/<期> --seg 7 \\
        --out data/episodes/<期>/patch_assets/<名>-motion.mp4 [--verify-repro]
    uv run python tools/motion_render.py <场景.html> --episode ... --seg 7 \\
        --stills 1,5,10,15 --stills-dir <目录> [--debug]      # 只出关键帧 PNG，先给人看构图

为什么存在：原片拍不出来的原理讲解（受力、流程、数据卡）以前只能补静止图（D59 转微动）。
这里让模型写程序画动画，产物走既有补料流程（`ingest_patch --anchor-only` → 锚点），
流水线代码一行不改。需求与验收见 `skills/make-motion-asset/SKILL.md`。

场景页的约定：
- 定义全局 `renderFrame(t)`，t 是秒。本工具按 `t = i / fps` 逐帧调用后截图，
  所以画面必须只是 t 的函数。页面里不用 CSS animation / transition / requestAnimationFrame，
  不读当前时间、不用无种子随机、不联网——本工具在运行时拦这几样，违反就报错退出，
  同一输入重跑才能逐帧一致。
- `window.MOTION` 由本工具注入：画幅、帧率、时长、配音逐句起点（`--episode/--seg` 时）、
  字幕安全区上沿 `safeBottom`、`--param` 给的变体参数 `params`。分镜按句子起点卡，不在页面里写死秒数，重新配音后不用改页面。
- `window.motion` 是 `tools/motion/runtime.js` 的缓动 / 插值 / 带种子随机数。
- 字体用 `MotionSans`（仓库内 `data/fonts/SourceHanSansSC-Bold.otf`），不依赖系统字体。
- 字幕由 06 渲染统一烧，画面里只放符号标注（`F`、`n`、单位），不放句子。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import paths  # noqa: E402
from pipeline.qc import BLACK_MAX  # noqa: E402

W = paths.conf("video.width", 1920)
H = paths.conf("video.height", 1080)
FPS = Fraction(paths.conf("video.fps", "24000/1001"))

# 字幕安全区高度：render.py 一张卡只放一行（卡片不折行），字压在 MarginV 上方。
# 1.5 倍字号 = 行高加描边与阴影的余量。画面主体不进这条带子，字幕才不压在关键元素上。
SUB_BAND = paths.conf("subtitle.margin_v", 90) + round(paths.conf("subtitle.size", 54) * 1.5)

FONT_FAMILY = "MotionSans"
FONT_FILE = paths.DATA / "fonts" / "SourceHanSansSC-Bold.otf"
RUNTIME_JS = Path(__file__).resolve().parent / "motion" / "runtime.js"

# 素材在 06 还要再编一次码；平涂色块与细线在 crf 18 下就能看出色带与振铃，
# 这里先留足质量，避免两代有损叠加。
CRF = "12"
PRESET = "slow"

# 渲染期间禁用的非确定性来源：调用即抛错，不静默替换成固定值——
# 替换会让「页面写错了」变成「画面不动」，后者肉眼才看得出。
GUARD_JS = """
(() => {
  const ban = (name) => () => {
    throw new Error(`motion: 渲染期间不能调用 ${name}，画面只能是 renderFrame(t) 的 t 的函数`);
  };
  Math.random = ban('Math.random');
  const RealDate = Date;
  globalThis.Date = new Proxy(RealDate, {
    construct(target, args) {
      if (!args.length) ban('new Date()')();
      return Reflect.construct(target, args);
    },
    apply() { ban('Date()')(); },
  });
  RealDate.now = ban('Date.now');
  performance.now = ban('performance.now');
  window.requestAnimationFrame = ban('requestAnimationFrame');
  window.MOTION = __MOTION__;
})();
"""


def fail(msg: str) -> None:
    raise SystemExit(f"FAIL {msg}")


def frame_count(duration: float) -> int:
    """覆盖 duration 所需的帧数，向上取整：素材短了会被 04 排片末帧定格补，长了只会被截掉。"""
    return math.ceil(Fraction(str(duration)) * FPS)


def segment_timing(episode: Path, seg: int) -> tuple[float, list[dict]]:
    """第 seg 段配音的总时长与逐句起点（03 按句合成后 ffprobe 复核过的值）。"""
    mf = episode / "03-audio" / "manifest.json"
    if not mf.is_file():
        fail(f"找不到 {mf}（--episode 要指向已配音的期目录）")
    segs = [s for s in json.loads(mf.read_text(encoding="utf-8"))["segments"] if s["index"] == seg]
    if not segs:
        fail(f"{mf} 里没有段落 {seg}")
    s = segs[0]
    sentences = [{"text": x["text"], "start": x["start"], "duration": x["duration"]}
                 for x in s.get("sentences") or []]
    return s["duration"], sentences


def open_scene(p, scene: Path, motion: dict, debug: bool):
    """起无头浏览器、装好禁令与运行时、载入场景页和字体，返回 (browser, page, errors)。"""
    errors: list[str] = []
    browser = p.chromium.launch(args=["--allow-file-access-from-files"])
    page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)

    def on_route(route):
        url = route.request.url
        if url.startswith(("file:", "data:", "blob:")):
            route.continue_()
        else:
            errors.append(f"联网请求 {url}")
            route.abort()

    page.route("**/*", on_route)
    page.on("pageerror", lambda e: errors.append(f"页面异常 {e}"))
    page.add_init_script(GUARD_JS.replace("__MOTION__", json.dumps(motion, ensure_ascii=False)))
    page.add_init_script(RUNTIME_JS.read_text(encoding="utf-8"))
    page.goto(scene.resolve().as_uri(), wait_until="load")
    page.add_style_tag(content="html,body{margin:0;overflow:hidden}")
    status = page.evaluate(
        """async ([family, url]) => {
            const f = new FontFace(family, `url(${url})`);
            try { await f.load(); } catch (e) { return String(e); }
            document.fonts.add(f);
            return f.status;
        }""",
        [FONT_FAMILY, FONT_FILE.resolve().as_uri()])
    if status != "loaded":
        fail(f"字体 {FONT_FILE} 没加载上：{status}")
    if errors:                         # 加载期就违规（如顶层调了 requestAnimationFrame），先报它
        fail("；".join(errors))
    if not page.evaluate("typeof renderFrame === 'function'"):
        fail(f"{scene} 没有定义全局 renderFrame(t)")
    if debug:
        page.evaluate(
            """band => {
                const d = document.createElement('div');
                d.style.cssText = `position:fixed;left:0;right:0;bottom:0;height:${band}px;` +
                    'background:rgba(255,0,80,.25);border-top:2px dashed #ff0050;z-index:2147483647';
                document.body.appendChild(d);
            }""", SUB_BAND)
    if errors:
        fail("；".join(errors))
    return browser, page, errors


def shoot(page, errors: list[str], t: float) -> bytes:
    from playwright.sync_api import Error as PlaywrightError

    try:
        page.evaluate("t => renderFrame(t)", t)
    except PlaywrightError as e:       # renderFrame 里抛的错（含禁令）：报页面的原话，不出 Python 堆栈
        fail(f"t={t:.3f}s：renderFrame 出错：{str(e).splitlines()[0]}")
    if errors:
        fail(f"t={t:.3f}s：" + "；".join(errors))
    n = page.evaluate("document.getAnimations().length")
    if n:
        fail(f"t={t:.3f}s：页面里有 {n} 个 CSS 动画 / 过渡；画面只能由 renderFrame(t) 设定")
    return page.screenshot(type="png")


def render_video(p, scene: Path, motion: dict, n: int, out: Path) -> list[str]:
    """逐帧截图直接送 ffmpeg 合成，返回每帧 PNG 的 sha256（复现检查用）。"""
    browser, page, errors = open_scene(p, scene, motion, debug=False)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-c:v", "png",
           "-framerate", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", PRESET, "-crf", CRF, "-pix_fmt", "yuv420p",
           "-an", "-movflags", "+faststart", "-f", "mp4", str(out)]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    hashes = []
    try:
        for i in range(n):
            png = shoot(page, errors, float(i / FPS))
            hashes.append(hashlib.sha256(png).hexdigest())
            enc.stdin.write(png)
            if i % 48 == 0:
                print(f"    帧 {i}/{n}", flush=True)
    finally:
        enc.stdin.close()
        rc = enc.wait()
        browser.close()
    if rc != 0:
        fail(f"ffmpeg 合成失败，退出码 {rc}")
    return hashes


def frame_hashes(p, scene: Path, motion: dict, n: int) -> list[str]:
    browser, page, errors = open_scene(p, scene, motion, debug=False)
    try:
        return [hashlib.sha256(shoot(page, errors, float(i / FPS))).hexdigest() for i in range(n)]
    finally:
        browser.close()


def check_output(out: Path, n: int) -> None:
    """产物合约：分辨率、帧率、帧数、无音轨、无黑场。任何一项不过就失败退出。"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-show_entries",
         "stream=codec_type,width,height,r_frame_rate,nb_read_packets", "-of", "json", str(out)],
        capture_output=True, text=True, check=True)
    streams = json.loads(r.stdout)["streams"]
    video = [s for s in streams if s["codec_type"] == "video"]
    problems = []
    if len(video) != 1 or len(streams) != 1:
        problems.append(f"流应只有一条视频，实际 {[s['codec_type'] for s in streams]}")
    if video:
        v = video[0]
        if (v["width"], v["height"]) != (W, H):
            problems.append(f"分辨率 {v['width']}x{v['height']}，应为 {W}x{H}")
        if Fraction(v["r_frame_rate"]) != FPS:
            problems.append(f"帧率 {v['r_frame_rate']}，应为 {FPS}")
        if int(v["nb_read_packets"]) != n:
            problems.append(f"帧数 {v['nb_read_packets']}，应为 {n}")
    # 与 07 质检同一组参数（qc.py：blackdetect d=BLACK_MAX pic_th=0.98）。
    # 07 对 SP 素材放宽到 BLACK_MAX_SP，这里按更严的 BLACK_MAX 卡：生成画面没有理由出黑场。
    r = subprocess.run(["ffmpeg", "-nostdin", "-i", str(out), "-vf",
                        f"blackdetect=d={BLACK_MAX}:pic_th=0.98", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        problems.append(f"blackdetect 跑不动，退出码 {r.returncode}")
    blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)
    if blacks:
        problems.append("黑场 " + ", ".join(f"{a}–{b}s" for a, b in blacks))
    if problems:
        fail(f"{out.name} 不合约：" + "；".join(problems))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("scene", type=Path, help="场景页 HTML")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--episode", type=Path, help="期目录；配合 --seg 读该段配音时长与逐句起点")
    src.add_argument("--duration", type=float, help="不跟配音走时直接给时长（秒），如片头")
    ap.add_argument("--seg", type=int, help="段落号（--episode 时必填）")
    ap.add_argument("--out", type=Path, help="产物路径，文件名须以 -motion.mp4 结尾")
    ap.add_argument("--stills", help="只出这些时刻（秒，逗号分隔）的关键帧 PNG，不合成视频")
    ap.add_argument("--stills-dir", type=Path, help="关键帧 PNG 输出目录")
    ap.add_argument("--debug", action="store_true", help="关键帧上叠字幕安全区（只配合 --stills）")
    ap.add_argument("--verify-repro", action="store_true", help="再渲一遍，逐帧比对截图哈希")
    ap.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                    help="传给页面 MOTION.params，同一场景出变体用（如配色 A/B），可重复")
    a = ap.parse_args()

    if not a.scene.is_file():
        fail(f"找不到场景页 {a.scene}")
    if a.episode is not None:
        if a.seg is None:
            fail("--episode 要配 --seg")
        duration, sentences = segment_timing(a.episode, a.seg)
    else:
        duration, sentences = a.duration, []
    motion = {"width": W, "height": H, "fps": str(FPS), "duration": duration,
              "sentences": sentences, "safeBottom": H - SUB_BAND,
              "params": dict(kv.split("=", 1) for kv in a.param)}
    n = frame_count(duration)

    from playwright.sync_api import sync_playwright

    if a.stills:
        if not a.stills_dir:
            fail("--stills 要配 --stills-dir")
        a.stills_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser, page, errors = open_scene(p, a.scene, motion, debug=a.debug)
            try:
                for t in (float(x) for x in a.stills.split(",")):
                    dest = a.stills_dir / f"{a.scene.stem}-t{t:06.3f}.png"
                    dest.write_bytes(shoot(page, errors, t))
                    print(f"[OK] {dest}")
            finally:
                browser.close()
        return 0

    if a.debug:
        fail("--debug 只用于 --stills，安全区参考线不能进成片")
    if a.out is None or not a.out.name.endswith("-motion.mp4"):
        fail("--out 必填，且文件名以 -motion.mp4 结尾（-still.mp4 会被 ingest_patch 登记成图片微动）")
    if not a.out.parent.is_dir():
        fail(f"输出目录不存在 {a.out.parent}")

    # 先写隐藏的半成品，合约检查全过才改名：ingest_patch 跳过点开头的文件，
    # 不合格的产物不会被误登记进补丁池。
    partial = a.out.with_name(f".{a.out.name}.partial")
    print(f"[*] {a.scene.name}: {duration:.3f}s → {n} 帧 @ {FPS}")
    with sync_playwright() as p:
        hashes = render_video(p, a.scene, motion, n, partial)
        check_output(partial, n)
        if a.verify_repro:
            print("[*] 复现检查：再渲一遍逐帧比对")
            again = frame_hashes(p, a.scene, motion, n)
            diff = [i for i, (x, y) in enumerate(zip(hashes, again)) if x != y]
            if diff:
                fail(f"两次渲染不一致：{len(diff)} 帧不同，第一帧 #{diff[0]}"
                     f"（t={float(diff[0] / FPS):.3f}s）；半成品留在 {partial}")
    partial.replace(a.out)
    digest = hashlib.sha256("".join(hashes).encode()).hexdigest()[:16]
    print(f"[OK] {a.out}  {n} 帧  逐帧摘要 {digest}" + ("  复现一致" if a.verify_repro else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
