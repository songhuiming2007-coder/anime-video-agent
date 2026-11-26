---
name: make-motion-asset
description: 为某一段配音写一张程序化动效场景页（HTML，支持 Canvas 2D / WebGL2 / SVG），用 tools/motion_render.py 逐帧渲成 *-motion.mp4，作为补料走 ingest_patch --anchor-only 进锚点。用于原片和静态图都讲不清的原理、结构对比、数据跨度或情绪化意象画面。
---

# 动效讲解素材

画面由程序画，不由图像或视频模型生成。你写场景页，`tools/motion_render.py` 负责逐帧截图、合成和合约检查。
这份说明管视觉设计、上下文衔接与生成合约；登记、排片、渲染、质检走流水线既有步骤。

**核心定位：动效素材是整期视频里的一个镜头，不是独立的视觉展示页。** 评判标准是切进前后相邻素材时能否自然承接情绪与画风、把这一句讲清楚，而不是单看这一段有多花哨。

**介入时机**：02 写稿阶段（尚未配音、排片）先在 `02-asset-requests.json` 登记待造动效，不空跑 `--episode --seg`；正式渲染放在 03 配音完成后（推荐 04 初排之后，这样 `--context` 和 `--splice` 能拿到真实前后镜头）。不跟配音走的独立镜头（如片头）可用 `--duration` 单独渲染。

## 一、开工前置：先看上下文，再定视觉方向

写场景页代码之前，先采集并核对三项真实上下文，不凭空套模板：

1. **看相邻镜头与色温**：用 `--context` 抽出本段切入前、切出后的真实画面（支持多锚点段内相邻镜头）、平均亮度/明暗分区、5 枚主色色卡，以及当前生效的背景配乐床（有 `## 音乐段` 按音乐床规则提取，普通期回退 `01-topic.md` 的 BGM 列表）和本段配音句轴（产出 `motion/_scratch/context-segN.png` + `.json`）。先用 `view_image` 看过 `motion/_scratch/context-segN.png`，再记下四项基准：
   - **明度基调**：暗调 / 中调 / 亮调（避免在暗调镜头之间突然插进高亮度白底或高饱和亮色）；
   - **编辑化提纯色板**：实拍抽出的 5 色色卡常偏灰褐或低饱和，取其**色温方向与明暗基调**，主动拉开「底色 / 结构墨色 / 强调色（1–2 个）」三档明度差，不把灰暗像素原样糊成低对比泥底；
   - **材质语汇**：实拍胶片、实体装帧、动画赛璐珞、线稿或舞台灯光，让动效质感与之同源；
   - **可承接的视觉母题**：优先从本期已有素材里生长元素（如专辑内页的波形线条、MV 里的丝线、原片里的标志物件），不生造与全片无关的装饰皮肤。
2. **看当下配乐与情绪**：核对 `context-segN.json` / `02-script.md` 中本段生效的配乐与本段 `情绪`、`语速` 字段，决定运动速度、停顿留白与切镜力度。安静收尾段不做喧闹的机械弹跳，快节奏吐槽段不做拖沓缓漂。
3. **读配音逐句时间轴**：`--context` 在期目录已有配音时，会把本段 `duration` 与 `sentences` 同步写进 `motion/_scratch/context-segN.json`（无需直接读 `03-audio/`），写场景页前据此规划每句节拍与 `--stills` 采样时刻。渲染时传 `--episode . --seg N`，工具自动把它们注入 `MOTION.sentences`。

## 二、视觉构成与节奏语法

克制不等于空洞极简（纯色底加一条孤立细线和两行居中字同样平庸）。在不绑定具体皮肤的前提下，用以下六条搭出电影感镜头构成：

- **单一核心意象，不做控制台堆砌。** 一条动效只立一个贴合口播因果的主视觉隐喻。能用一条波形、一个缩放结构或一组空间关系讲清的，不叠加装饰选框、印刷色带、重复标题、占位数字或无关仪表盘。
- **三层景深构成，避免平涂纸片感。** 画面明确拆出三层：① 背景氛围层（微弱方向光晕、暗角渐变与确定性颗粒 `makeGrainPattern`，不做无呼吸的纯色平涂）；② 中景结构层（低透明标尺、参考网格、辅助投影或景深衬线）；③ 前景主体层（核心意象、主运动轨迹与高对比视觉锚点）。
- **非对称版式、字级反差与防抖读数。** 不把所有元素固定堆在水平正中（`x = 960`）；按视线流动做主次重心布局，拉大主数字/主标签与辅助刻度的字号与透明度反差。画面不放完整口播句子（字幕由 06 烧录），只放 2–8 字视觉锚点、专名、刻度、公式或实测值。动态递增数字若当前字体栈无 `MotionMono`，按固定槽宽逐位绘制以防逐帧横向抖动。
- **句内「预备 → 主显 → 落定余韵」与全程持续呼吸。** 避免前 0.6s 弹完后画面死停 3 秒：每句在 `MOTION.sentences[i].start` 处起势，主体落位后保持低幅持续运动（如 `camera2d` 1.00→1.03 慢推、光晕缓移、波场微呼吸或景深漂移），句间停顿不突兀起新主动作；多拍切镜优先用空间推进或错开退场/入场，避免两套复杂图文直接半透明叠影。
- **讲的东西要对，量要算出来、值要有出处。** 示意图比例、箭头长短、曲线走势与极值位置按公式或实测数据计算，不按感觉摆，并在页面里核对极值点与口播论点一致（束胸期第 7 段曾因截面压扁后两侧曲率大于前胸导致焦点反了）。没查到出处的数值不上画面；缺测项用占位诚实标出，不插值补编。拒绝扁平简笔画直描人物轮廓或默认深蓝圆角柱状图。
- **按画面需要选渲染通道（Canvas 2D / WebGL2 / SVG）。**
  - 细腻颗粒、精准排版与 2D 镜头推进优先用 **Canvas 2D**；
  - 需要 3D 纵深、透视推拉、体积光或连续波场时用 **WebGL2 / GLSL**，推荐**双画布叠层范式**：底层 `<canvas>` 跑 WebGL2 空间光影，上层叠加透明 `<canvas>` 用 Canvas 2D 画清晰中文锚点与标尺（避免在 WebGL 内手写中文字体贴图；着色器保持精简以保证 SwiftShader 秒级渲染与 `--verify-repro` 逐帧一致）；
  - 简洁线稿可用 SVG，但同样需三层景深与持续呼吸。
- **上下安全区留空。** 底部主体元素与文字不进 `MOTION.safeBottom` 以下；若本段处于 `## 章节` 范围内（`MOTION.hasChapters === true`），顶部 `0..MOTION.safeTop`（`64px`）留给章节顶栏，关键元素放在 `y ≥ 80px` 以下。出关键帧加 `--debug` 会同时叠出底部字幕带与顶部章节带。

## 三、输出与合约

产物是 `patch_assets/<名>-motion.mp4`。以下各项由 `tools/motion_render.py` 在出片前检查，不过就不落盘（半成品留在隐藏文件里）：

| 项 | 要求 | 以哪个检查为准 |
|---|---|---|
| 分辨率、帧率 | `config/project.json` 的 `video.width/height/fps` | ffprobe |
| 时长 | 做满整段配音；帧数 = ceil(时长 × fps) | ffprobe 数帧 |
| 声音 | 不出声，BGM 与配音由 06 统一混 | 流只能有一条视频 |
| 黑场 | 不出现 ≥ `qc.BLACK_MAX` 的近黑画面；`blackdetect` 按 `pix_th=0.10 / pic_th=0.98` 判黑，实测暗调底色在 `#1c1e26`（`RGB(26,28,36)`，luma ≈ 28）以下且高亮像素占比不足 2% 时会拦截，暗调场景须把底色稳在 luma ≥ 28 或用氛围微光保证非暗像素 ≥ 2%，开头不从纯黑淡入 | 与 07 质检同参数的 blackdetect |
| 可复现 | 同一输入逐帧一致 | `--verify-repro` 两遍逐帧比哈希 |
| 文件名 | 以 `-motion.mp4` 结尾。`-still.mp4` 会被 `ingest_patch` 登记成图片微动 | 工具拒绝其他后缀 |

时长做满整段的理由：多锚点段里素材只占一截，占多长到 04 排片才定。做满了怎么切都够；做短了会被末帧定格补，补超过 `align.EXTEND_MAX` 就退回人工。

## 四、场景页与分镜约定

- 定义全局 `renderFrame(t)`，`t` 是秒，画面只由 `t` 决定。渲染器在运行时拦下 `Math.random`、`Date`、`performance.now`、`requestAnimationFrame`、CSS 动画与过渡、任何联网请求，碰到就报错退出。需要随机纹理或点缀时用 `motion.rng(seed)`。
- 字体由渲染器从 `data/fonts/` 自动加载：`MotionSans`（必需，`SourceHanSansSC-Bold.otf`）及按磁盘存在情况加载的可选族（常见 `MotionSerif` 对应 `Songti.ttc`，若存在还支持 `MotionSansRegular`、`MotionMono`），不引用联网字体 URL。`MOTION.fonts` 在场景加载前即填入可用族名；但 `FontFace` 异步加载在 `window.initScene()` 与首次 `renderFrame(t)` 之前才彻底就绪，因此依赖自定义字体的 `ctx.measureText` 或离屏文字贴图须写在可选全局 `initScene()` 或 `renderFrame(t)` 内（首帧懒初始化），不写在顶层脚本里。
- 运行时 `window.motion`（`tools/motion/runtime.js`）提供风格中立的确定性原语：`clamp`、`lerp`、`remap`、`easeIn` / `easeOut` / `easeInOut` / `easeOutExpo` / `easeInOutQuint` / `easeOutBack` / `smoothstep`、`prog`、`stagger`、`spring`、`impulse`、`rng`、`camera2d`、`makeGrainPattern`（支持 `alpha` 或 `opacity` 及暗底用 `fiberColor`）。
- WebGL2 运行时 `window.motionGL`（`tools/motion/gl.js`）提供 `createContext`、`compileShader`、`createProgram`、`createFullscreenQuad` 与列主序 `mat4`（同时自动兜底原生 `canvas.getContext('webgl2')`，补齐 `preserveDrawingBuffer: true` 并接入每帧上下文丢失检测与 `gl.finish()` 同步）。
- 场景页放期内 `motion/`，中间图与连切预览固定放期内 `motion/_scratch/`（在 `view_image` 允许读域内，且不会污染 `patch_assets/`）。同一场景需要变体时用 `--param key=value`，页面从 `MOTION.params` 读。
- **镜头按句子卡**：关键动作起点取 `MOTION.sentences[i].start`，不在页面里写死秒数，重新配音后页面不用改。
- **多锚点段收进分配时长**：先用整段长度出一版、登记、跑 04，从 `04-clips.json` 读出素材实际分到的 `dur`，若关键动作超出该长度则收进 `dur` 内重渲（全长不变，重排后分配也不变）。

## 五、流程

AVA 会话的 `bash` 默认工作目录在当期目录（`$AVA_ROOT` 指向仓库根），以下为 AVA 实跑口径；开发/CLI 从仓库根运行时改为 `uv run python tools/motion_render.py --episode data/episodes/<期> ...`。

1. **抽上下文并写 5 行视觉简报**：先跑 `--context`，用 `view_image` 看 `motion/_scratch/context-segN.png` 并读 `motion/_scratch/context-segN.json` 的分句时长，明确前后明度/色温/母题、当前配乐与情绪、单一核心隐喻、三层构图与分句节拍：

```bash
python "$AVA_ROOT/tools/motion_render.py" --episode . --seg N --context motion/_scratch
```

2. **写场景页并出关键帧接触表自查**：至少覆盖每句中段与切镜点，`--stills` 自动附产 `motion/_scratch/<名>-contact.png`，先用 `view_image` 自审（检查上下安全区、三层景深、字距与遮挡、过渡是否叠影、明度与前后镜头是否协调），修完再交人看构图：

```bash
python "$AVA_ROOT/tools/motion_render.py" motion/<名>.html --episode . --seg N \
    --stills 0.5,2.5,5.0,7.5 --stills-dir motion/_scratch --debug
```

3. **人认可构图后出片、验复现并生成连切预览**（`--splice` 必须落在 `motion/_scratch/`，禁止落进 `patch_assets/`；加 `--splice-bgm` 可顺带混入低音量参考 BGM 铺底；工具同时自动生成 `motion/_scratch/splice-segN-seams.png` 4 格接缝抽帧图）：

```bash
python "$AVA_ROOT/tools/motion_render.py" motion/<名>.html --episode . --seg N \
    --out patch_assets/<名>-motion.mp4 --verify-repro \
    --splice motion/_scratch/splice-segN.mp4 --splice-bgm
```

4. **上下文连切复核**：
   - AVA 先用 `view_image` 检查 `motion/_scratch/splice-segN-seams.png`（`PREV 尾 | MOTION 首 | MOTION 尾 | NEXT 首`），核对切入/切出两刀的明度与构图衔接；
   - 由**人**播放 `motion/_scratch/splice-segN.mp4`（含前后镜头、真实分段原声及参考 BGM；已有成片可加 `--motion patch_assets/<名>-motion.mp4 --splice motion/_scratch/splice-segN.mp4 --splice-bgm` 独立复现），确认节奏与气氛不跳戏后再入池。
5. **登记进补丁池并写锚点**：在 AVA 会话里经 `run_pipeline` 执行 `ingest_patch --anchor-only`，从输出读取真实池名与 `SPxx`，写进 `02-script.md` 的 `锚点: <池名> SPxx 0:00`，再经 `run_pipeline` 跑 `clips`（多锚点段核对分到的 `dur`，超出则收进 `dur` 重渲并用 `ingest_patch --anchor-only --reset` 重建）。

之后 04 排片、05 人审、06 渲染、07 质检照常走；首次引入新类型动效时核对一遍 `06-check.log`。
