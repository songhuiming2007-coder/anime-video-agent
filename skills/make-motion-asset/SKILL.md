---
name: make-motion-asset
description: 为某一段配音写一张动效场景页（HTML），用 tools/motion_render.py 逐帧渲成 *-motion.mp4，作为补料走 ingest_patch --anchor-only 进锚点。用于原片拍不出来的原理讲解、流程示意、数据卡，或里程碑期的生成画面。
---

# 动效讲解素材

画面由程序画，不由图像或视频模型生成。你写场景页，`tools/motion_render.py` 负责逐帧截图、合成和合约检查。
这份说明只管生成侧；登记、排片、渲染、质检都是流水线现成的步骤，用法见文末。

## 一、输入

- 这一段的配音原文与逐句起点：`03-audio/manifest.json` 的 `segments[].sentences`。渲染时给 `--episode <期> --seg N`，
  工具会把它们注入 `MOTION.sentences`。
- 这一段要讲清的那一个概念。一段素材只讲一件事。
- 不跟配音走的画面（片头、数据卡）用 `--duration` 直接给时长，`MOTION.sentences` 为空。

## 二、输出与合约

产物是 `<期>/patch_assets/<名>-motion.mp4`。下面几项由 `motion_render.py` 在出片前检查，不过就不落盘（半成品留在同目录的隐藏文件里）：

| 项 | 要求 | 以哪个检查为准 |
|---|---|---|
| 分辨率、帧率 | `config/project.json` 的 `video.width/height/fps` | ffprobe |
| 时长 | 做满整段配音；帧数 = ceil(时长 × fps) | ffprobe 数帧 |
| 声音 | 不出声，BGM 与配音由 06 统一混 | 流只能有一条视频 |
| 黑场 | 不出现 ≥ `qc.BLACK_MAX` 的近黑画面；背景不用近黑色，开头不从黑场淡入 | 与 07 质检同参数的 blackdetect |
| 可复现 | 同一输入逐帧一致 | `--verify-repro` 两遍逐帧比哈希 |
| 文件名 | 以 `-motion.mp4` 结尾。`-still.mp4` 会被 `ingest_patch` 登记成图片微动 | 工具拒绝其他后缀 |

时长做满整段的理由：多锚点段里素材只占一截，占多长到 04 排片才定。做满了怎么切都够；做短了会被末帧定格补，
补超过 `align.EXTEND_MAX` 就退回人工。

## 三、场景页约定

- 定义全局 `renderFrame(t)`，t 是秒。画面只由 t 决定。渲染器在运行时拦下 `Math.random`、`Date`、`performance.now`、
  `requestAnimationFrame`、CSS 动画与过渡、任何联网请求，碰到就报错退出。需要随机点缀时用 `motion.rng(seed)`。
- 运行时 `window.motion`（`tools/motion/runtime.js`）给 `prog(t, t0, t1)`、`lerp`、`clamp`、缓动函数和 `rng`。
- 字体写 `font-family: MotionSans`，由渲染器从 `data/fonts/SourceHanSansSC-Bold.otf` 加载，不依赖系统字体。
- 场景页放 `<期>/motion/`，不放 `patch_assets/`（那个目录只放待登记的视频）。
- 同一场景要出变体（如配色）时用 `--param key=value`，页面从 `MOTION.params` 读。

## 四、分镜规范

- **镜头按句子卡。** 关键动作的起点取 `MOTION.sentences[i].start`，不在页面里写死秒数，重新配音后页面不用改。
  一句一个动作；句子之间的停顿里不开始新动作。
- **多锚点段里，关键动作要落在 04 分给素材的那一截里。** 素材只按自己的时间轴从 0 开始播，04 用 `clips.size()`
  按各锚点的自然时长加权分配这一段的时长，素材拿到的那一截之后的画面不会进成片。
  这个长度只取决于素材全长和同段其他锚点，跟画面内容无关，所以先用整段长度出一版、登记、跑 04，
  从 `04-clips.json` 读出素材实际分到的 `dur`，再把关键动作收进这个长度里重渲（全长不变，重排后分配也不变）。
  束胸期第 7 段：素材 17.0s、后接原片镜头，素材分到 11.264s；而配音念到「红线」在 13s 前后，
  所以压力打到红线提前到 10s，没有跟着配音卡。
- **画面里不写句子。** 字幕由 06 用 libass 统一烧。符号标注可以有（`F`、`n`、`σθ`、单位），中文说明文字不放。
- **底部留出字幕安全区。** 主体元素不进 `MOTION.safeBottom` 以下。出关键帧时加 `--debug` 会叠出这条带子。
- **讲的东西要对，而且画面强调的量要算出来。** 示意图里的箭头长短、读数高低按公式计算，不按感觉摆。
  然后在页面里把这个量打印出来核一遍，确认画面强调的地方确实是它最大的地方。
  第一个样本（束胸期第 7 段）就踩过：截面压扁后椭圆两侧的曲率反而超过胸前凸起，画面变成了「两侧压得最狠」，
  和配音说的相反。查出来的办法是逐圈打印前侧与两侧的曲率半径。
- **没有来源的数值不上画面。** 阈值线可以画，但没查到出处的具体数值不标。
- 配色、动效类型由内容决定；每一帧都要能单独看懂，不靠运动本身传达信息。

## 五、流程

```bash
# 1. 先出关键帧给人看构图（每句一张）
uv run python tools/motion_render.py <期>/motion/<名>.html --episode <期> --seg N \
    --stills 1.5,4.3,7.6,11,15.5 --stills-dir <scratch 目录> --debug

# 2. 人认可构图后出片
uv run python tools/motion_render.py <期>/motion/<名>.html --episode <期> --seg N \
    --out <期>/patch_assets/<名>-motion.mp4 --verify-repro

# 3. 登记进补丁池（只登记不打标，救援检索搜不到它，只能靠锚点引用）
uv run python -m pipeline.ingest_patch <期> --anchor-only

# 4. 稿子里引用：锚点: <池名> SPxx 0:00

# 5. 本段还有别的锚点时：跑 04，读素材分到的 dur；关键动作超出的话改场景页重渲，
#    再 ingest_patch --anchor-only --reset（旧池挪进 04-patch.attic/）后重跑 04
uv run python -m pipeline.clips <期>
```

池名由期目录名净化得到（`ingest_patch.sanitize_pool_name`，非 ASCII 字符换成 `-`），以第 3 步的输出为准。
之后 04 排片、05 人审、06 渲染、07 质检照常走；第一次用新素材时专门看一遍 `06-check.log`。
