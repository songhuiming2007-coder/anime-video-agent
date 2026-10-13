# D86：试看片段（音画同源）配上原片中文字幕 施工 spec

> 对应 issue：**D86**。相关：ADR-0013（双模态片段协议）、D84、D85。
> 含一项视觉选择（`SRC` 字幕样式），按 UI 规则先出真实帧截图、人选后定。

## Context

《束胸力学》要在段落 5、6 之间插一段更衣室试看。系统已经支持「试看」：`## 音乐段` 里写 `源片原声:` 和 `音画同源: 是`（ADR-0013），画面和声音切自同一段原片。但这个机制是为 EGOIST 的 Live / MV 设计的，**渲染只给旁白配字幕**。番剧对白的试看渲染出来是没有中文字幕的日语原声，看点在对白时就不能用。

目标：试看片段如果切自番剧正片（`SxxEyy`），自动把该集字幕索引里落在窗口内的中文台词按原时间烧进成片；SP 素材（Live / MV）不加字幕，EGOIST 各期零漂移。

已核实的现状：
- 字幕索引 `data/library/index/<番>_SxxEyy.json` 是 2 句滑窗（`subindex.py:243-256`：`start=第 i 句起点`，`end=第 i+1 句终点`，`text=两句拼接`）。逐句文本可以用 `pipeline/agent/tools.py::subtitle_lines` 精确还原；逐句终点 = `units[i-1].end`（i≥1，精确）。只有整集第 0 句没有精确终点，取下一句起点。建索引时已滤掉日文行和制作名单（`KANA` / `NOISE`），字幕时间与片源对齐（入库时 `ingest.verify` 校过）。
- 渲染：`render.build_ass`（`render.py:381`）只生成旁白字幕卡。`music.build_timeline` 产出的 `plan["blocks"]` 里，音画同源块带 `start`（成片起点）、`dur`、`avsync`（`伪恋 S01E07`）和 `t0`（源片起点）（`music.py:500-508`）。`render._source_path`（`render.py:1016`）已经有「番名前缀 + 集键」的解析与番表校验。
- 试看槽内旁白静音（`build_timeline` 不在槽内放段落），两种字幕在时间上不会重叠。

## 方案

### 1. 逐句字幕时间：新文件 `pipeline/subcues.py`（纯标准库，渲染不背 ML 栈）
- `cues(units, source) -> list[(start, end, text)]`：文本还原沿用 `subtitle_lines` 的剥后缀算法（结构不对照旧报错，不给半截台词）；终点按上面的规则取。
- `load_cues(anime, key) -> list | None`：读 `index_dir()/<番>_<键>.json`，没有这一集的索引返回 None。
- `window(cues, t0, t1) -> list[(offset_start, offset_end, text)]`：与窗口 [t0, t1] 有重叠的句子按窗口裁剪，换成相对窗口起点的偏移；裁剪后显示不足 `MIN_CUE_S = 0.3` 秒的丢掉（窗口边缘只露一帧半帧的字会闪一下，0.3 s ≈ 7 帧，低于此人眼读不出字）。
- `tools.subtitle_lines` 改为调用 `subcues.cues` 取文本，两处共用一份还原逻辑。

### 2. 稿件语法：默认开启，可以关
- 番剧集（`SxxEyy`）的试看**默认配字幕**；块里写 `原声字幕: 关` 可以关掉（例如截了一段 OP 原曲，不想要歌词字幕）。SP 素材永远不配。
- `music.parse_script_music`：解析可选行 `原声字幕: 开/关`，只认这两个值，写别的当场报错。`MusicBlock` 加 `src_subs: bool`，`build_timeline` 把它带进 `plan["blocks"]`。
- 番剧集试看却**没有该集字幕索引**时，渲染直接报错（判据 9：跳过不是通过），报错里给两条出路：先建这一集的索引，或者写 `原声字幕: 关`。

### 3. 渲染：`render.py`
- 从 `_source_path` 抽出 `_resolve_tag(episode, tag) -> (番, 集键)`，`_source_path` 改为调用它（行为不变）。
- 新增 `_avsync_cues(episode, plan) -> list[(成片起, 成片止, 文本)]`：遍历带 `avsync` 的块，跳过 SP 和 `src_subs=False`，用 `subcues.window` 加上块的 `start` 映射到成片时间。
- `build_ass(..., extra=None)`：`extra` 里的每条按旁白同款换行（`wrap(_ass_escape(..), per_line())`）写成 Dialogue，样式名 `SRC`（样式定义见第 4 步，人选后定）。
- 调用处（`render.py:1276`）在有 `music_plan` 时传入 `extra=_avsync_cues(episode, music_plan)`。

### 4. 字幕样式：先出真实截图让人选（UI 规则：视觉类先截图后定）
在《伪恋》S01E07 更衣室这一段的真实帧上，用同一组台词烧两版，各截 3 张：
- **A**：和旁白完全同款（白字、黑描边、底部居中）；
- **B**：和旁白区分开（字号小一档，或换成淡色，位置不变），让观众分得清「这是原片台词」。
人选定之后再把 `SRC` 样式写死。截图脚本放 scratch，不进仓库。

### 5. 写稿期提示：`check_script`
- 新增一项「试看字幕」：番剧集试看列出窗口内有几句字幕（`M3：6 句`）。0 句且没写 `原声字幕: 关` → FAIL（这段多半是纯画面，或者窗口写错了）；没有索引 → FAIL，并给出和渲染同样的两条出路。SP 试看不计入。这样写稿时就能确认窗口里有没有对白，不必等到 06 渲染才发现。

### 6. 文档
- 写稿 skill 8.3 节去掉「番剧对白暂无中文字幕」，改为说明默认配字幕、`原声字幕: 关` 的用法、字幕来自字幕索引。
- runbook 06-render 的形态 2 段落、ADR-0013 加补记；D86 登记进问题单，plans 新增 spec 和索引行。

## 关键文件
- 新建 `pipeline/subcues.py`
- 修改 `pipeline/render.py`（`build_ass`、`_source_path` → `_resolve_tag`、`_avsync_cues`、调用处）、`pipeline/music.py`（`原声字幕:` 解析、`src_subs`）、`pipeline/check_script.py`（「试看字幕」项）、`pipeline/agent/tools.py`（`subtitle_lines` 改调 `subcues`）
- 文档：`skills/write-script/SKILL.md`、`docs/runbook/06-render.md`、`docs/dev/adr/0013-dual-mode-clip-protocol.md`、`docs/dev/issues/README.md`、`docs/dev/plans/`

## 验证
- 单测：
  - `subcues.cues`：结束时间由滑窗还原；用真实 `伪恋_S01E07.json` 抽 3 句，与 `subgrep` 输出逐字比对。
  - `window`：边缘裁剪、<0.3 s 丢弃、窗口外的句子不进。
  - `music`：`原声字幕:` 的开 / 关 / 非法值。
  - `_avsync_cues`：番剧集映射后的成片时间对得上；SP 跳过、`关` 跳过、缺索引报错。
  - `build_ass`：`extra` 产生 `SRC` 行，旁白行逐字节不变。
  - `check_script`：试看字幕项的几种情况。
- 变异检验：去掉成片偏移、去掉 SP 跳过、去掉 0.3 s 门槛，各自对应的测试必须变红。
- EGOIST 零漂移：对 EGOIST V1 / V2 已有稿件跑 `music.build_timeline` 和 `_avsync_cues`，确认全是 SP、额外字幕为空；生成的 `vo.ass` 与改动前逐字节一致。
- 端到端（T7）：scratch 临时期，复制《束胸力学》的 `01-topic.md`，稿件写一段 `伪恋 S01E07` 的更衣室试看，跑 `check_script` 看「试看字幕」项；再用 ffmpeg 按 `_avsync_cues` 生成的 ASS 把这段原片烧成 30 秒预览，抽帧确认字幕和对白同步。这一步同时产出第 4 步的 A / B 截图。
- 全量 `pytest`。

## 施工记录（2026-10-13）

- 新建 `pipeline/subcues.py`（纯标准库）；`tools.subtitle_lines` 改调它，同一份还原逻辑。
- 真实数据核对：《伪恋》S01E07 还原出 390 句，逐句文本与起点与 `subgrep` 输出逐字一致，无负时长。
- `check_script` 的「试看字幕」先跳过 SP 标签再解析归属（初版对 SP 块报了归属错误，SP 本就不配字幕，不该出声）。
- 测试：`test_subcues.py` 5 条、music 1 条、render 4 条、check_script 5 条；变异 4/4（去掉成片偏移、去掉 SP 跳过、去掉 0.3 s 门槛、终点取错单元）。
- EGOIST 零漂移：V1 / V2 五期 `_maybe_music_plan` + `_avsync_cues`，同源块全是 SP，额外字幕为空，计划字典无 `src_subs` 键；`build_ass(extra=[])` 与不传逐字节一致（单测）。
- 端到端：scratch 期写《伪恋》S01E07 17:11.00–17:41.50 试看，`check_script`「试看字幕 M3：9 句」；按 `_avsync_cues` 生成 ASS，在真实片源上烧 30.5 s 预览 A / B 两版，抽帧确认字幕与对白同步（第 12 s「小野寺小野寺…」、第 27.5 s「我是被捡来的…」），片源无内嵌字幕，不叠字。
- 同批（D86 起因的另两处）：写稿 skill 8.3「试看 / 原声片段」与 SHOTLIST 指引；bash 作业注入 `AVA_ROOT`、`PATH` 前置项目解释器目录（测试 1 条，变异 1/1）。
- `SRC` 样式：人在真实帧截图上比过 A（与旁白同款）/ B（46px 淡黄），**选 A**（2026-10-13）。
