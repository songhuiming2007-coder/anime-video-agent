# 动效素材体系升级（文档 / Context 层 + 底层渲染引擎）施工图

日期：2026-11-26
状态：**Phase 1（文档与 Context 层）与 Phase 2（动效底层升级）均已施工·机检与真实冒烟通过，待人审阅与实片验收**
关联：ADR-0010、ADR-0022、D84、D92、D101

---

## 一、背景与根因

对照仓库内已有两件动效产物（束胸期 `binding_pressure.html`、EGOIST 期 `bpm-span.html`）与高完成度交互影像作品（*The Last Boring Slide* 全片 8 分钟 64 帧拆解），当前动效链路的底层确定性渲染底座已经可用，但产出视觉质量偏低、且极易与全片脱节，根因集中在五点：

1. **Context 层缺入口**：`config/agent/scopes/{asset,creative,pipeline}.md`、`docs/runbook/{02-script,04-clips}.md` 与 `skills/write-script/SKILL.md` §8.5 此前全未提及 `skills/make-motion-asset/SKILL.md`，AVA 默认只走外部静图抓取。
2. **脱离全片上下文**：动笔前不看前后相邻镜头（`seg-1`、`seg+1`）与当下 `## 音乐段` 情绪；验收只单看一段 `.mp4`，不看前后连切接缝。结果容易做出明度、色温、气氛与全片割裂的“独立炫技页”。
3. **规则侧重工程校验、缺审美原则**：旧版 `SKILL.md` 仅有一句视觉说明，且一刀切禁止中文视觉标签；缺少「单一核心意象、克制留白、拒绝直白照描与套版柱状图、接触表自审」要求。
4. **字体与安全区底座单一**：`tools/motion_render.py` 仅注入单一粗黑体 `SourceHanSansSC-Bold.otf`（仓库 `data/fonts/` 已有 `Songti.ttc` 未注入），且仅注入底部字幕安全区 `safeBottom`，未注入长篇章节顶栏安全区 `safeTop`（`chapters.BAR_HEIGHT = 64`）。
5. **运行时原语过薄，缺空间光影与物理动效工具**：`tools/motion/runtime.js` 仅 29 行（只有 `easeInOut` / `easeOut`），缺少确定性弹簧、微震、2D 相机、离屏颗粒与 WebGL2 辅助；虽然无头 Chromium 实测已具备 WebGL2 确定性渲染能力，但工具链未显式封装与护航。

### 前置实测证据（S7：先验后写）

2026-11-26 在本机 `.venv` Playwright Chromium 上实测：
- **WebGL2 确定性**：`ANGLE (Google, Vulkan 1.3.0 SwiftShader)`，WebGL 2.0、`MAX_TEXTURE_SIZE = 8192`；1080p GLSL fragment shader 经 `tools/motion_render.py --duration 1.0 --verify-repro`（24 帧 × 2 遍 + `ffmpeg` + `blackdetect`）耗时 **5.61 s**，两遍逐帧 SHA-256 完全一致（`0737927b469550e0`）。
- **多字体 `FontFace` 加载**：在 `file://` 场景页源下，`data/fonts/SourceHanSansSC-Bold.otf` 与 `data/fonts/Songti.ttc` 均返回 `loaded`。

---

## 二、设计三条核心原则

1. **Context-First（动效是整期视频的一个镜头）**：做前必看相邻真实画面与配乐情绪，做后必看 `seg-1 → motion → seg+1` 连切预览。
2. **单一意象 + 克制 + 机制与内容分离（R1）**：底层库只提供与风格无关的确定性原语（物理缓动、相机、颗粒、多字体、安全区、WebGL 辅助），**不把任何单一网红美术皮肤（如包豪斯色带、固定色板）写死进工具库**。
3. **打开 WebGL2 / 3D 空间光影上限**：Canvas 2D 与 WebGL2 均为一等渲染通道，由同一确定性 `renderFrame(t)` 与 `--verify-repro` 守住。

---

## 三、分阶段施工清单

### Phase 1：文档与 Context 层（本批已落盘，待人按 D101 审阅）

| 文件 | 改动 |
|---|---|
| `skills/make-motion-asset/SKILL.md` | 中性规则语气重写：补充「先看前后镜头与配乐」「单一意象与克制留白」「上下安全区」「Canvas 2D / WebGL2 / SVG 选型」「接触表自查与前后连切复核」；CLI 命令保持与当前代码一致（S6 零虚报） |
| `config/agent/scopes/{asset,creative,pipeline,director}.md` | 最小字符增量接入 `make-motion-asset` 指针与「先看上下文 / 出图先看」规则，严格守住 `resident_budget` 与单文件 ≤ 2000 字上限 |
| `skills/write-script/SKILL.md` | §8.2 E 类与 §8.5 补料流程补入程序化动效分支 |
| `docs/runbook/{02-script,04-clips}.md`、`docs/WORKFLOW.md` | 补料通道补入程序化动效指针（`WORKFLOW.md` 保持 78 行 ≤ 100 行） |

### Phase 2：动效底层升级（按三条核心规则，待人批 Phase 1 后施工）

全部改动限定在 `tools/motion_render.py`、`tools/motion/`、`tests/test_motion_render.py` 与 `skills/make-motion-asset/SKILL.md`（同步新旗标），**不动 `pipeline/` 既有渲染/排片/质检链路**。

#### PR-2A：规则 1 落地 — 上下文抽帧与连切验收工具化（`tools/motion_render.py`）
- **`--context <目录>` 模式**（配 `--episode <期> --seg N`）：
  - 自动定位切入前与切出后的真实画面（优先读 `04-clips.approved.json` / `04-clips.json` 对应源片抽帧，支持多锚点段内相邻兄弟镜头；尚未排片时回退读 `04-thumbs/` 兼容零填充 `s07c0` 与 `s153c0` 或 `02-script.md` 相邻锚点信息，缺哪个如实提示，不猜测）；
  - 按 `pipeline/music.py` 语义提取本段当前生效的背景配乐床（音画同源槽不覆盖原床，纯前景淡出清空床；普通期无 `## 音乐段` 时回退读 `01-topic.md` 的 BGM 声明），同步写入本段配音实测时长与分句起点（若 `03-audio/manifest.json` 存在），连同相邻帧与像素统计（BT.709 平均亮度、dark/mid/light 明暗分区、5 枚代表主色）拼成 `context-segN.png` + `context-segN.json`，供 Agent 调用 `view_image` 一眼完成上下文锚定。
- **`--splice <路径>` 模式**（配 `--episode <期> --seg N` 与已出片的 `--out` 或 `--motion <mp4>`，可选 `--splice-bgm`）：
  - 自动截取切入前末 3 秒 + 本段动效（配 `manifest.json` 对应真实分段 WAV，兼容零填充）+ 切出后首 3 秒，合成单条连切预览 `.mp4`（禁止落入 `patch_assets/`），加 `--splice-bgm` 时复用 `pipeline.music.build_timeline` / `pipeline.bgm` 混入低音量（-28 LUFS）参考 BGM 铺底；
  - 同步自动生成 `<stem>-seams.png` 4 格接缝抽帧图（`PREV 尾 | MOTION 首 | MOTION 尾 | NEXT 首`），供 AVA 用 `view_image` 自审切入/切出两刀。
- **`--stills` 自动拼接触表**：`--stills` 输出单帧 PNG 的同时自动生成 `<stem>-contact.png`（2–3 列拼图，带每帧时间戳水印），供 `view_image` 一眼检查全片节奏与转场。

#### PR-2B：规则 2 落地 — 多字体、双安全区与确定性物理/质感原语
- **`tools/motion_render.py`**：
  - 字体栈升级：固定加载 `MotionSans`（`data/fonts/SourceHanSansSC-Bold.otf`）；若 `data/fonts/Songti.ttc` 存在则加载为 `MotionSerif`；若 `data/fonts/` 存在常规无衬线或等宽字体文件（如 `MotionSansRegular` / `MotionMono`）则按声明表加载；在 `add_init_script` 前即把磁盘可用族名预填入 `MOTION.fonts`，并在 `FontFace` 加载就绪后同步精确列表与调用可选全局 `initScene()` 钩子（S4/S7）；
  - 按段粒度注入 `MOTION.safeTop`（从 `pipeline.chapters.BAR_HEIGHT` 取 `64`，并注入 `hasChapters` 布尔值，仅当本段位于活跃 `## 章节` 范围内时为 `True`）；`--debug` 同时叠加顶部章节栏安全带与底部字幕安全带。
- **`tools/motion/runtime.js`**（纯确定性、风格中立，不写死任何具体视觉皮肤）：
  - 物理与节奏：`spring(t, t0, {stiffness, damping, mass})`（解析解，逐帧精确无状态，参数越界抛 `RangeError`）、`impulse(t, t0, freq, decay)`、`easeIn`、`easeOut`、`easeInOut`、`easeOutBack`、`easeOutExpo`、`easeInOutQuint`、`smoothstep`、`remap`、`stagger(t, i, start, step, dur, ease)`；
  - 2D 相机辅助：`camera2d(ctx, {x, y, zoom, rot, cx, cy})`；
  - 离屏确定性材质：`makeGrainPattern(ctx, seed, {size, alpha, opacity, fibers, fiberAlpha, fiberColor})`（基于 `rng(seed)` 单次生成离屏 canvas，支持浅/暗底纤维色，零每帧重算开销、100% 过 `--verify-repro`）。

#### PR-2C：规则 3 落地 — WebGL2 / 3D 空间光影一等支持
- **`tools/motion/gl.js`**（独立于 `runtime.js`，零 2D 场景负担）：
  - 封装 WebGL2 上下文初始化（自动设 `{preserveDrawingBuffer: true, antialias: true}` 并兜底拦截原生 `HTMLCanvasElement.prototype.getContext('webgl2' / 'webgl')`）、Shader 编译与报错清理、全屏 Quad 辅助（前置校验属性位置防泄漏）、轻量确定性 `mat4`（`perspective` 参数校验、`lookAt` 重合抛错与共线 `up` 自动切换备用轴、`multiply`、`rotate*`、`translate`、`scale`）；
  - `tools/motion_render.py` 在 `shoot()` 中检查所有已登记 WebGL 上下文的 `isContextLost()` 并调用 `gl.finish()` 同步保底，杜绝帧缓冲未冲刷导致的随机黑帧或复现漂移。
- 同步更新 `skills/make-motion-asset/SKILL.md`，补入正向视觉构成/节奏语法、暗调 `blackdetect` 量化底线（`#1c1e26` / luma ≥ 28）、AVA 会话实跑口径与真实落地旗标清单。

---

## 四、验证与完成判定

### Phase 1（已验）
- `.venv/bin/python -m pytest -q tests/test_docs_invariants.py tests/test_agent_assembly.py tests/test_agent_assembly_integration.py tests/test_agent_context_claims.py tests/test_static.py` 全绿；
- `git diff` 人工按 D101 过目确认语气中性、无虚报旗标。

### Phase 2（施工完成后验收）
1. 新增 `tests/test_motion_render.py` 覆盖纯函数（上下文解析、安全区计算、字体清单过滤、帧数计算、接触表网格布局、`runtime.js`/`gl.js` 语法与确定性数学单测）+ 变异检验；
2. 真实端到端冒烟（scratch 目录，不改真实期产物）：
   - 对 `2026-11-11-EGOIST-行星生物` seg 153 跑 `--context`，确认产出 `context-seg153.png`（含 `seg152` 实体封面、`seg154` 实体册、明度与色卡、木吉他音乐段信息）；
   - 跑一个 Canvas 2D 场景与一个 WebGL2 3D 光影场景，均通过 `--stills --debug`（含自动接触表）、`--out ...-motion.mp4 --verify-repro` 与 `--splice` 连切预览。
3. 全量 `pytest` 全绿。

---

## 五、施工与验收记录（2026-11-26）

- **单测与静态门禁**：`tests/test_motion_render.py` 新增 21 条纯函数、缺失字体 CI 回退与真实 Node 执行 `runtime.js`/`gl.js` 的行为测试（`-W error` 零警告通过）；全量 `pytest` 通过，`pipeline/` 零改动。
- **两轮变异检验（首轮 6/6 + 复审修复 6/6 全部 KILLED）**：覆盖 `_RE_CHAP_END`、`neighbor_clip_targets`、`discover_fonts`、`safeTop`、`is_inside_patch_assets`（含大小写与软链）、`avsync` 铺底床保持、`seg-07.wav` 零填充、缩略图数值排序、`gl.js` `lookAt` 共线 `up` 换轴、`runtime.js` `spring` 参数校验。
- **两期真实端到端冒烟（scratch 目录零污染）**：
  - `2026-10-13-motionab-伪恋-鸫诚士郎的束胸力学` seg 7：`--context` 正确保持 `M0 · 片头铺底 (Heart Pattern)`、导出 `17.01s (4 句)` 与同段后镜；`--splice --splice-bgm` 命中 `seg-06.wav`/`seg-07.wav` 零静音告警，自动生成 `splice-seg7-seams.png` 4 格接缝图并混入 `Heart Pattern @89.3s`；
  - `2026-11-11-EGOIST-行星生物` seg 153：`--context` 竖拍 `IMG_1481.MOV` 保比例黑边居中，导出 `8.49s (2 句)` 与 B18 吉他版铺底；原生 `canvas.getContext('webgl2')` + 顶层 `MOTION.fonts` + `initScene()` 测宽 + Canvas 2D 混合场景通过 `--stills --debug`、`--out ... --verify-repro`（24 帧逐帧哈希一致 `87eb47f61982daec`）与 `--splice --splice-bgm`（自动生成 `splice-seg153-seams.png` 并混入 `Departures (Acoustic Ver.) @8.6s`）。
