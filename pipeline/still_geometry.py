"""图片转缓推视频的几何（纯常量与纯函数，只依赖标准库）。

从 `acquire` 拆出来（D122）：`browser` 截图的回执要报「入池时要放大几倍」，而 `acquire` 一 import 就经
ingest → subindex 拖进 torch / sentence_transformers（实测 2.3 s），agent 工具侧不能背。
`acquire` 原样重新导出这些名字，`still_argv` 与门禁照旧从那里用。
"""

from __future__ import annotations

#: 缓推终点：1.0 → 1.15 匀速推满全片。步长按帧数算（`0.15 / (帧数-1)`），不写死——
#: 旧写法每帧 +0.0015 在第 100 帧（≈4.2 s）就到顶，6 s 里最后 1.8 s 已经不动了。
STILL_ZOOM_END = 1.15


#: 宽高比与 16:9 相差在这个比例以内算「横屏截图」，照旧铺满裁边（动漫截图不留边）；超出算图表 / 方图 /
#: 扁长图，完整放进安全框（D89：《束胸力学》1:1 的环向应力图、2.5:1 的肺容量图被裁掉四边的字）。
STILL_FILL_TOL = 0.05
#: 安全框：缩到这里以内，缓推到 STILL_ZOOM_END 时四边仍完整可见（直接缩到 1920×1080 再推 1.15 倍会再裁 7%）。
#: 再留 STILL_MARGIN 的余量：只除以推镜倍数时，推到顶正好贴满画面边缘（实测 938×1.15≈1079），
#: 标注顶着画框不好看，舍入还会吃掉一像素。取偶数，yuv420p 要求宽高为偶数。
STILL_MARGIN = 1.04
STILL_SAFE_W = int(1920 / (STILL_ZOOM_END * STILL_MARGIN)) // 2 * 2
STILL_SAFE_H = int(1080 / (STILL_ZOOM_END * STILL_MARGIN)) // 2 * 2


def still_fills(size: tuple[int, int] | None) -> bool:
    """这张图走「铺满裁边」（True）还是「完整放进安全框」（False）。尺寸未知按旧行为铺满。"""
    if size is None or not size[0] or not size[1]:
        return True
    return abs((size[0] / size[1]) / (16 / 9) - 1) <= STILL_FILL_TOL


def still_frame_fit(size: tuple[int, int]) -> float:
    """`still_argv` 首帧缩放比（铺满 1920×1080 或缩进 STILL_SAFE_W×STILL_SAFE_H 安全框）。"""
    w, h = size
    return max(1920 / w, 1080 / h) if still_fills(size) else min(STILL_SAFE_W / w, STILL_SAFE_H / h)


def still_peak_magnify(size: tuple[int, int]) -> float:
    """`still_argv` 出的视频里原图像素被放大的峰值倍数（纯函数，与 still_argv 同一套几何）。

    铺满：先放大到盖住 1920×1080；安全框：缩进 STILL_SAFE_W×STILL_SAFE_H（小图会被放大）。
    再缓推到 STILL_ZOOM_END——zoompan 末帧裁出 1/z 再放回 1920×1080，所以峰值 = 首帧缩放 × z。
    """
    return still_frame_fit(size) * STILL_ZOOM_END


#: 网页物证截图的高宽比与成片缩小下限（仅管 `source == "screenshot"`，不影响画册/Booklet 等实体竖版扫图）。
#: 1.35：即便 550 CSS 宽的窄栏按 1.5 倍视口高上限（660 CSS）截一段访谈/表格，高宽比仅 1.20；超出即整页或长竖条。
#: 0.30：即便最高 scale=4 下 660 CSS 高（2640 px），放进 902 px 安全高也只缩到 0.34×；低于 0.30 说明原图
#: 超过 ~3000 px 高或 ~5300 px 宽，正文塞进 1080p 成片会缩成几像素（与 still_peak_magnify 对称守两端）。
SHOT_MAX_H_OVER_W = 1.35
SHOT_MIN_FRAME_FIT = 0.30


def screenshot_frame_issue(size: tuple[int, int]) -> str | None:
    """网页截图进 1080p 成片框的局部构图校验；合规返回 None，不合规返回原因（仅用于网页截图）。"""
    w, h = size
    if w <= 0 or h <= 0:
        return "尺寸非法"
    hw = h / w
    if hw > SHOT_MAX_H_OVER_W:
        return (
            f"高宽比 {hw:.2f} 超上限 {SHOT_MAX_H_OVER_W:g}（整页或长竖条截图进 1080p 安全框字会缩糊；"
            "请只截本段对应的那一段或那几行表）"
        )
    fit = still_frame_fit(size)
    if fit < SHOT_MIN_FRAME_FIT:
        return (
            f"进 1080p 成片要缩到 {fit:.2f}×（下限 {SHOT_MIN_FRAME_FIT:g}×，原图 {w}×{h} 范围过大；"
            "请收紧到目标段落或表格，去掉多余留白与无关区域）"
        )
    return None


#: 图片转缓推视频时的峰值放大倍数上限（D123）。图片唯一的去处是 `still_to_clip`：横屏铺满或缩进
#: 安全框，再缓推到 STILL_ZOOM_END。峰值 >1 就是在插值放大、会糊；≤1 只是缩小，原图多大都清楚。
#: 此前判「短边 ≥1600」：它量的不是产物——宽幅截图裁到证据区域后短边跌破 1600 被拒（实际缩小 0.5 倍），
#: 竖版 1582×2860 的 booklet 被拒（实际缩小 0.36 倍），反倒逼人留整视口、把广告带进画面。
STILL_MAX_MAGNIFY = 1.0
