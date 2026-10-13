# D87：配音句内长静音判不合格 + 03.5 预览栏显示逐段重配状态 + 对话面板不再横向滚动

> 现场备注：面板发起的 apply-patch 已按用户要求停掉（SIGINT，锁已释放；段 7 已重配，段 6 合成到第 3 句时中断，旧 wav 未被覆盖，段 15 的纠错未应用）。段 6 等门禁上线后再重配。

## Context

两个问题，都出自《束胸力学》的 03.5 顺听：

1. **段 6 句内 5.8 s 死寂**：第 2 句在 8.15 s 就念完了，引擎却多生成了 5.8 s 近静音尾巴（-55~-72 dB，中间夹一个 -34 dB 的杂音）。`_trim_silence` 的判据是「单个采样超过 -60 dBFS 就算有声」，所以一点没裁掉；回读 CER 和时长门禁也都没拦住。全库 296 段实测：正常的句内停顿最长 1.75 s（多数 ≤1.25 s），段 6 是孤立异常。用户选方案 2：在质检层判不合格并重试。
2. **预览栏看不出重配进度**：每段只有「待重配 / 播放中 / 音频缺席」三种标记。重配期间哪段在排队、哪段正在合成、哪段刚重配完要复听、哪段失败了，一概看不出来，只能看顶栏的「正在运行」。另外刚修掉的 datetime 崩溃让「合成完一段就把纠错标为已应用」从没成功过，所以「待重配」从不消失。重配常常一轮改不干净，没有状态就成了糊涂账。
   - 重配可能来自三个入口：面板按钮、AVA 在 bash 里跑 `tts --redo`、终端。所以状态必须从盘上读，不能靠面板内存。

## 一、句内长静音门禁（`pipeline/tts.py`）

- 新常数 `MAX_INNER_GAP = 2.0`（秒）和 `GAP_DB = -45`，并附注全库实测分布（正常最长 1.75 s，段 6 为 4.35 s + 1.75 s）。
- 新纯函数 `_longest_gap(path) -> float`：按 50 ms 窗算 RMS，低于 -45 dB 的窗算静音，返回最长连续静音的秒数。在 `_trim_silence` 之后对单个合成单元测量，所以首尾不算在内。
- `_render_one`：`ok_gap = gap < MAX_INNER_GAP`，与 `ok_dur`、`ok_cer` 并列。不过时打印「句内静音 X.Xs（第 k 次）」，走现有的重试链。
- **长静音不许豁免**：`_best_take`（ASR 盲区豁免挑时长最好的那次）排除句内长静音的那几次。死寂不是 ASR 盲区，挑不出合格的就照常 FAIL。
- **段内种子一致的兜底**（`render_segment`）：
  - 现状：某句靠换种子才过 → 整段回基准种子 7 重排 → 重排失败就报错要人钉种子。而句内静音这种坏样本，换回种子 7 必然原样复现。
  - 改为：基准种子重排失败时，如果第一遍里撞出来的替代种子只有一个，就**整段统一改用这个种子**重排一次（段内仍然一致）；这一次也失败才报错，报错指路不变。stderr 打 WARN 说明「段 6 整段改用种子 X（基准 7：第 2 句句内静音 5.8 s）」，`seeds_used` 照常记账。
- **不 bump `SYNTH_LOGIC_VERSION`**：这次只改「收不收」，不改任何一次合成产出的音频。bump 会把全库每一段都标成「旧逻辑产物」，制造噪音。

## 二、重配进度落盘（`pipeline/tts.py`）

- 新文件 `03-audio/.tts_progress.json`（用 `paths.atomic_write` 原子写）：
  `{v:1, pid, started_at, state: running|ok|failed, queue:[待合成段号], done:{段号: 完成时刻}, current, note, finished_at, error}`
- `run()` 在 `for seg in segs` 之前算出 queue（不在 `done` 里的段）。queue 为空就不写，避免把上一轮的「已重配」冲掉。写入时机：
  1. 开始时；
  2. 每段开始合成时：设置 `current`；
  3. 每段合成完：记入 `done`，与 `corrections.mark_segment_done` 同一处；
  4. 正常结束：`state=ok`；
  5. `try/except BaseException` 捕获 SystemExit 和 Ctrl-C：`state=failed`，`error` 截断到 300 字后照常抛出。
- `note`：`_render_one` 每次「第 k 次不过」时更新一行原因，比如「第 2 次：句内静音 5.8s」。用一个模块级小钩子实现，非 run 调用时不起作用。
- SIGKILL 或断电会让 state 停在 running、pid 已死，由读取方判成「中断」。
- apply-patch 递归调用 `run(redo=…)`，由内层统一写，不另开通道。

## 三、voice-info 带上进度（`pipeline/agent/cli.py::voice_info_payload`）

- 新增 `synth` 字段：读 `.tts_progress.json`，复用 `_pid_alive` 判定，`running` 但 pid 已死就改成 `interrupted`。文件缺失或损坏时为 `null`，面板照常装载。
- 每段新增 `mtime`（wav 的修改时间），用于播放器换源。

## 四、桌面端预览栏（`desktop/`）

- `src/shared/protocol.ts`：`VoiceInfoJson.synth`、`VoiceSegmentJson.mtime`。
- `src/shared/voiceInfo.ts`：容错解析（字段缺失时为 null，与 D72 `rerun_segments` 同一纪律）。新纯函数 `segStatus(info, label, heardAt)` 给出每段状态，**优先级**从高到低：
  1. **合成中**（显示 note，如「第 2 次：句内静音 5.8s」）
  2. **排队中**（显示第几个 / 共几个）
  3. **失败**（显示 error 前几十字，悬停看全文）/ **中断**
  4. **待重配**（沿用现有判据）
  5. **已重配 HH:MM · 待复听** → 在面板里播放过这一段之后变为 **已重配 · 已复听**。复听记录存在模块级 Map 里，挂载期间不丢，与 D73 的 `lastInfo` 同法。
- `src/renderer/VoicePanel.tsx`：
  - 段落列表上方加一行进度摘要：「重配进行中 2/3 · 段 6 合成中」/「上次重配 14:32 完成：段 6、11（待复听 1 段）」/「重配失败：段 6 — 原因」。
  - 行内徽标改由 `segStatus` 决定。
  - 轮询条件改为 `live || synth.state === "running"`，AVA 在 bash 里跑的重配也会刷新。
  - `<audio key={wav+mtime}>`：重配后同名 wav 强制重新加载，不会播到旧音频。
- 视觉规则：实现后在真实窗口截图（重配中 / 已重配待复听 / 失败三种），确认后再提交。desktop 有改动，需要重新打包。

## 五、对话面板横向滚动（`desktop/src/renderer/style.css`）

**成因**：滚动容器 `.conv-stream` 是 `overflow: auto`（`ui.css:105`），只要有一个子元素比面板宽，整个对话流就能两指左右划。会撑宽的来源：
- 用户气泡 `.conv-user` 是 `pre-wrap`，但没设 `overflow-wrap`；
- markdown 正文的段落、列表、行内 `code`、表格单元格都没设 `overflow-wrap`，不带空格的长路径、长 URL、长哈希折不了行；
- 表格没有宽度上限。

代码块、工具输出、命令行早就设了 `overflow-wrap: anywhere`，所以不是它们。

**修法**（全部写在 `style.css`。`ui.css` 被 `visual.test.ts` 的 sha256 基线锁住，改它要回到人面前重看 mock，这次不碰）：
- `.conv-shell .conv-stream { overflow-wrap: anywhere; }`。这个属性会继承，一条规则覆盖所有行类型，包括以后新增的。用 `anywhere` 而不用 `break-word`，是因为只有前者会缩小元素的最小内容宽度，flex 子项和表格列才收得进来。中文本来就逐字可断，不受影响；英文只在一行放不下时才断词。
- `.conv-md .markdown table { display: block; max-width: 100%; overflow-x: auto; }`：表格先靠折行收窄，实在收不进去时只在表格内部横向滚动，不会带动整个对话流。
- `.conv-md .markdown img { max-width: 100%; }`
- 不给 `.conv-stream` 加 `overflow-x: hidden` 兜底：那会把内容悄悄裁掉，把问题藏起来而不是解决。

**验证（视觉类，靠真实窗口）**：
- 从真实 AVA 会话里找出撑宽的消息（扫会话记录里超长的无空格串和宽表格），再加几个构造的极端例子：用户气泡里一条长路径、一张 8 列表格、一个长 URL、一段行内长哈希。
- 用 Electron 加载真实样式表，在 900 px 和 600 px 两种宽度下检查 `conv-stream.scrollWidth <= clientWidth`。脚本放 scratch。
- 在修复前后各截一张图给人看。

## 六、文档

- 问题单登记 D87。
- runbook 03.5 节补一句状态含义。
- `tts.py` 中门禁常数的注释写明实测依据。

## 关键文件

- `pipeline/tts.py`（`_longest_gap`、`_render_one`、`_best_take`、`render_segment`、`run`）
- `pipeline/agent/cli.py`（`voice_info_payload`）
- `desktop/src/shared/{protocol,voiceInfo}.ts`、`desktop/src/renderer/VoicePanel.tsx`
- 测试：`tests/test_tts.py`、`tests/test_corrections.py` 或 voice-info 相关测试、`desktop/tests/shared/voiceInfo.test.ts`

## 验证

- **Python 单测**：
  - `_longest_gap`：合成 wav（语音 + 2.5 s 静音 + 语音 → 2.5；纯连续语音 → 小于门槛）。
  - 用假 Engine 驱动 `_render_one`：第一次产出带长静音的音频 → 判不过并重试；三次都带长静音 → 不豁免、FAIL。
  - `render_segment`：基准种子在某句出长静音，替代种子干净 → 整段统一用替代种子，`seeds_used` 全部相同。
  - `run()`：进度文件依次经过 running → done 累积 → ok；中途 SystemExit → failed 并带 error；queue 为空时不写。
  - `voice_info_payload`：pid 已死 → interrupted；文件损坏 → null。
- **变异检验**：去掉 `ok_gap`、去掉 `_best_take` 的排除、去掉统一替代种子的兜底、去掉 failed 分支，对应测试各自必须变红。
- **真实数据**：拿《束胸力学》现有的 `seg-06.wav` 跑 `_longest_gap`，必须 ≥ 2.0；全库其余 295 段必须全部 < 2.0（门槛无误伤）。
- **桌面端**：`segStatus` 优先级表测试与解析容错测试；`npx vitest run`、`npx tsc --noEmit -p tsconfig.json`；打包后用真实窗口截图三种状态。
- 全量 `pytest`。
- **端到端**：在 AVA 里对《束胸力学》重配段 6，看预览栏依次显示排队 → 合成中（含重试原因）→ 已重配待复听，复听后变为已复听；听新的段 6 确认死寂消失。

## 施工记录（2026-10-13）

- **门槛实测**：`_longest_gap` 扫全库 304 个 wav（整段文件，含 0.18 s 句间停顿，比单句测得更严）：只有《束胸力学》`seg-06.wav` 超门槛（4.35 s）；次大值 1.75 / 1.7 / 1.6 s，没有误伤。
- **变异**：Python 5 处（`ok_gap` 不参与收货、豁免不排除死寂、去掉替代种子兜底、失败不记 failed、pid 已死不判中断）和 TS 4 处（待重配压过已重配、复听按版本记、失败段判失败、`mtime` 缺省），对应测试全部变红。
- **对话面板**：用 Electron 加载真实样式表测量（scratch 脚本）。600 px 时修复前 `conv-stream` 内容宽 877 px、可视宽 598 px（来源是长 URL 和表格里的长哈希）；修复后 598 = 598。第一版对表格也用了 `anywhere`，把「CER」压成竖排，改成单元格用 `break-word`、表格内部横滚。
- **e2e**：新增「D87 03.5 预览栏逐段重配状态」（临时 repo，盘上写进度文件，靠面板自己的轮询跟上）。stopPoint / convTools / layout / visual / session 共 57 条全部通过，VE-1 对比度审计也通过。
