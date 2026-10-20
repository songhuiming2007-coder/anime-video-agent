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


def still_peak_magnify(size: tuple[int, int]) -> float:
    """`still_argv` 出的视频里原图像素被放大的峰值倍数（纯函数，与 still_argv 同一套几何）。

    铺满：先放大到盖住 1920×1080；安全框：缩进 STILL_SAFE_W×STILL_SAFE_H（小图会被放大）。
    再缓推到 STILL_ZOOM_END——zoompan 末帧裁出 1/z 再放回 1920×1080，所以峰值 = 首帧缩放 × z。
    """
    w, h = size
    fit = max(1920 / w, 1080 / h) if still_fills(size) else min(STILL_SAFE_W / w, STILL_SAFE_H / h)
    return fit * STILL_ZOOM_END


#: 图片转缓推视频时的峰值放大倍数上限（D123）。图片唯一的去处是 `still_to_clip`：横屏铺满或缩进
#: 安全框，再缓推到 STILL_ZOOM_END。峰值 >1 就是在插值放大、会糊；≤1 只是缩小，原图多大都清楚。
#: 此前判「短边 ≥1600」：它量的不是产物——宽幅截图裁到证据区域后短边跌破 1600 被拒（实际缩小 0.5 倍），
#: 竖版 1582×2860 的 booklet 被拒（实际缩小 0.36 倍），反倒逼人留整视口、把广告带进画面。
STILL_MAX_MAGNIFY = 1.0
