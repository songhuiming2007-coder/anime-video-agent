# Pipeline 运行问题与架构缺陷复盘（2026-08-18）

在执行《春物·折射下的真物》流水线实战过程中，暴露出的代码设计缺陷与数据契约断裂问题记录如下：

---

### 二、代码本身的设计缺陷（隐式状态耦合 & 数据不一致）

导致中间“15秒后字幕严重错配、排片与音频脱节”的恶性 Bug，暴露出代码层面的 3 个设计漏洞：

1. **局部重跑缺少全局级联校验（最核心的 Bug 根因）**：
   - **代码问题**：当某几个段落的 TTS 被单段重跑后，虽然 `.wav` 文件的时长变了，但 `04-clips.approved.json`（画面排片）依然存着旧的 `duration`。
   - **后果**：`render.py` 在拼画面时按照旧排片时长去切视频，而在生成字幕和拼音频时却按新的音频切，导致画面轨和音频轨在时间轴上直接裂开（产生累积时差）。
   - **改进建议**：`render.py` 或 `clips.py` 应该在入口处强制加一道断言：`assert sum(seg.duration) == sum(wav.duration)`，一旦发现音频时长与排片表不一致，当场抛出 Fatal Error 拒绝渲染，而不是静默合成一个错位的视频。

2. **`cover.py` 强依赖未校验的 JSON 字段（脆性崩溃）**：
   - **代码问题**：`04-clips.json` 生成时没有注入 `season` 和 `episode` 字段（只放了 `source` 文件名），而 `cover.py` 却直接写了 `c['season']` 裸取，导致直接 KeyError 崩溃。
   - **改进建议**：`clips.py` 在导出时就应该规范化 Schema，或者 `cover.py` 内部使用正则从 `source` 路径安全解析 `season`/`episode`，消除对字典特定字段的脆弱假设。

3. **BGM 注册与解析的容错率低**：
   - **代码问题**：`01-topic.md` 里写了 `ユキハルアメ [Instrumental]`，但由于 `bgm.json` 里没有注册带括号的别名，代码在渲染拼接时直接抛出缺失错误。
   - **改进建议**：BGM 匹配应支持规范化后的文件名模糊匹配或在解析 topic 时提前做 dry-run 校验。

---

## 修复记录（2026-08-18，已落地）

三条全部修复，`uv run pytest` 565 → 584 全绿，四处变异检验均红（把新校验逐个改坏，对应测试必红）。未提交 git，由人拍板。

### 1. 局部重跑级联校验 → 已修（tts.py）

现状盘点：段级不变量的双闸（`review.approve` / `render.run` 的 `verify_alignment`）与
`clips --refit` 在 P2 施工图（2026-08-14）已落地，本次不缺。真正的缺口是**产生脏数据
的那一刻没有任何提示**——局部重跑 TTS 只重写 manifest，下游 `04-clips*.json` 还是旧时长，
要等到几小时后渲染才被闸拦住，排查还得倒推。

修法：`tts.py` 新增 `_stale_downstream()`，每次写完 manifest 立刻对
`04-clips.json` / `04-clips.approved.json` 跑 `verify_alignment`，有违例当场 WARN
并指明清算命令（先 `--refit` 吸平差额，差额过大被拒绝了再整条重跑 clips 并重走 05）。
TTS 本身的产物是好的，所以是 WARN 不是 FAIL——诚实指出脏数据，不误杀已成功的配音。

### 2. 配置文件前置校验 → 已修（tts.py / render.py / music.py）

- `tts.load_config()`：坏 JSON、缺 `engine`/`model`/`ref_audio` 键在加载处当场
  SystemExit 并点名缺哪个，不再流到 Engine 构造或 `_voice_fingerprint` 裸 KeyError。
  （`ref_audio` 文件存在性本来就在 Engine 构造时、模型加载前拦，已够早。）
- `render.run()`：`_maybe_music_plan` 与 `_bgm_plan` 提前到**切片之前**解析——
  曲名打错（含本复盘③的括号别名没注册）、曲目文件不存在、缺 lufs、音乐段越界，
  这些只读 JSON 就能判的错不再等切片几分钟后才炸。`_bgm_bed` 改为吃解析好的 plan。
- `music._track_for()`（试听型路径，render 与 qc 共用）：曲目记录缺
  path/dur/lufs、文件不存在，解析时间轴时当场报，附补测/分轨命令。

模糊匹配（规范化文件名别名）没做，选的是 dry-run 前置校验一路：匹配放宽是行为变化，
前置报错是失败提前，后者不动现有判据。

### 3. cover.py 字段 schema → 已修（cover.py / review.py）

`clips.candidate()` 其实一直注入整型 `season`/`episode`；真正的脆弱面是 05 人审手工
补进的片段只有 `source`/`start`/`dur`。修法在消费侧兜底：

- `cover._clip_ep()`：缺键时按 source 路径在片源登记表反查集号（与 `_by_character`
  同一张表），还查不到退文件名做标签——标签只用于展示与铺开分档，拿不到集号
  不该让封面步骤整个崩。`_sample_points` 加 `by_path` 参数，`build()` 只在确实有
  片段缺键时才读 sources.json（懒加载，正常路径零行为变化）。
- `review._clip_ep()`：抽检页同一处裸取同步兜底（同一 bug 类，手工片段最先
  经过的就是 05 这页）。
