# D89：《束胸力学》后半程撞出的四个流水线缺陷

## Context

用户在 05 → 06 → 封面阶段连续撞到四个问题，AVA 每次都要靠试错、绕路才能推进：

1. **桌面端批准 05 失败**：这一期有补丁段（段 7、10），`review --approve` 会用 `input()` 要人在终端确认「已核对补丁段」。桌面端调用时没带 `--confirm-patch`，也没有终端可以作答，于是 `EOFError` 崩溃。
2. **加了试看段后渲染报「音乐床时长 91.8s ≠ 296.4s——有轨没接时间轴」**：`render.py:1173` 只要稿件里有任何一个 `## 音乐段`，就改走「试听型」时间轴，01 里的 `BGM:` 列表被**静默忽略**，最后才在渲染末尾报一句看不出原因的错。AVA 试了 29 次工具才查出来，然后用 `背景铺底` 块绕过去了（这是 EGOIST 那几期的正规写法，试看期间 BGM 暂停、结束后接着放，已经支持）。它还往 01 里写了一个不存在的字段 `BGM结束: 占位`。
3. **补丁图片被放大裁切**：`acquire.still_argv` 写死了「放大铺满再居中裁」（`increase` + `crop`）外加 1.0→1.15 缓推。对 16:9 的截图没问题，1:1 的方图、2.5:1 的扁长图表四边的字会被裁掉。AVA 用 bash 手工压了一版「缩小 + 补边」绕过去。普通片段早就是「缩小 + 补边」（`render.py:178`），只有图片这条路还在裁。
4. **出网护栏误拦**：`RESTRICTED_EGRESS_PATTERNS` 里有 `03-audio/manifest.json`。请求里只要出现这个字符串，整轮就被回滚，「配音总长多少」这种正常问题也会中招。当初把它列为受限的理由是「没给 LLM 读它的工具，碰不到就不会发」（Y2-r19，2026-09-18），但后来 AVA 有了 bash：写 `cat 03-audio/*.json` 就能读到内容（护栏只比对字符串），而老老实实写出完整路径反而被拦。manifest 的字段是引擎名、读音表、参考音频路径、各段文本 / 时长 / CER / 种子，没有凭据，文本和稿件本来就会发出去的部分重合。

## 一、05 补丁段确认走桌面端确认框（`pipeline/review.py` + `desktop/`）

- `review.py`：
  - 新增只读查询 `--patch-segments`：打印 JSON `{"patch_segments": [7, 10]}`，复用现成的 `get_patch_pool` / `is_patch_segment`，不写任何文件。
  - stdin 不是终端时不再调 `input()`，直接 `SystemExit`，写清楚要怎么做：「本期有补丁段（段号 7, 10）需人逐段核对后确认：桌面端批准时会弹确认框；终端加 `--confirm-patch`」。终端里交互的行为不变。
- 桌面端 `decide.ts::plan` / `decide()`：05 在 pending 状态下，先跑新模板 `REVIEW_PATCH_SEGS`（只读）。
  - 结果非空 → 宿主弹原生确认框（复用 apply-patch 那套 `deps.confirm`）：「本期有 N 个补丁段（段 7、10，外部素材 / 补丁池画面）。确认已逐段看过再批准？」
  - 取消 → 返回「已取消」，不 spawn、不写任何东西。
  - 确认 → `REVIEW_APPROVE` 的 argv 追加 `--confirm-patch`。
  - 没有补丁段时行为与现在逐字节一致。
  - 指纹核对、`--expect-size/mtime` 闸照旧：查询之后文件如果变了，批准时会被拦下。
- `spawner.ts`：新增 `REVIEW_PATCH_SEGS` 模板，`REVIEW_APPROVE` 增加可选的 `confirmPatch`。

## 二、稿件有音乐段时，01 的 BGM 列表不再被静默忽略（`pipeline/render.py`、`pipeline/check_script.py`）

不做「把 01 的 BGM 列表自动转成铺底块」：BGM 的出处只保留一个（稿件），`背景铺底` 语法本来就有。改成**尽早、说清楚地失败**：

- 新纯函数（放在 `music.py` 或 `render` 旁）：稿件有音乐段、但没有任何块产出 BGM（没有 `背景铺底`，也没有「前景试听后降为 BGM」），而 01 里有 `BGM:` 列表 → 生成一段报错文字，直接给出可以照抄的铺底块（按 01 列表的曲名，第一首挂在段落 1 前；`@N秒` 的起点换算不了，就注明「请放到对应段落前」）。
- `check_script` 新增一项「BGM 出处」：上面这种情况判 FAIL；稿件里有铺底块而 01 里还留着 `BGM:` 列表 → WARN「01 的 BGM 列表在本期不生效，以稿件铺底块为准」，避免两处说法打架。
- `render.run` 在入口（`_maybe_music_plan` 之后、开始切片之前）做同样的检查，报同样的话，不用等到最后一步才报「有轨没接时间轴」。
- 写稿 skill 8.3 节补一句：有音乐段的期，BGM 必须写成稿件里的 `背景铺底` 块。

## 三、图片微动：非 16:9 的图按比例完整放进画面（`pipeline/acquire.py::still_argv`）

- 先按图片宽高比分流：和 16:9 的偏差在 ±5% 以内 → 保持现在的铺满 + 裁（动漫截图不留边）；超出 → **完整放进安全框**：先缩到 `1920/1.15 × 1080/1.15` 以内，再补边到 1920×1080，然后缓推。这样推到最大 1.15 倍时，图的四边仍然完整可见（直接缩到 1920×1080 再推 1.15 倍，会再裁掉 7%）。
- 宽高比要用 ffprobe 读：`still_argv` 现在是纯函数，加一个 `aspect` 参数，由 `still_to_clip` 读出来再传进去，纯函数保持可测。
- **补边的底色属于视觉选择，先出 A/B 让你挑**：用这一期的两张真图（1:1 和扁长图）各渲染三版截图——A 黑边、B 原图虚化放大垫底、C 取图片边缘色补边。你选定后再写死。
- 现在这一期的 `-still.mp4` 是 AVA 手工压的，不动。

## 四、出网护栏：去掉 `03-audio/*` 两条（`pipeline/agent/tools.py`）

- `RESTRICTED_EGRESS_PATTERNS` 只保留凭据：`cloud.local.json`、`agent.local.json`。去掉 `03-audio/manifest.json`、`03-audio/voice.json`。
- 理由写进注释和 ADR-0026 的修订注：这两条防的是内容，而 bash 落地后字符串比对拦不住读取，只会误拦正常提问；内容里也没有凭据。
- 同步 Y2-r19 的「一律不出网」清单（spec 加修订注），以及现有测试里拿 manifest 当受限样例的用例（换成凭据文件名，断言意图不变）。
- `read_artifact` 读域排除 `03-audio/` 这一条不在本次范围：它管的是专用读取工具，不影响 bash。

## 不在本次范围

- AVA「一言不合就渲染」、自行编造 `BGM结束: 占位` 字段：属于模型行为，只记到问题单。01 里那一行由你或 AVA 删掉即可（是期内容，我不碰）。
- `shots.caption_frames` 中断后残留临时帧（D88 已记）。

## 关键文件

- `pipeline/review.py`（`approve`、`main` 加 `--patch-segments`）
- `desktop/src/host/{decide,spawner,service}.ts`（新模板、确认框、`--confirm-patch`）
- `pipeline/music.py` / `pipeline/render.py` / `pipeline/check_script.py`（BGM 出处检查）
- `pipeline/acquire.py`（`still_argv` 加宽高比分流、`still_to_clip` 用 ffprobe 读尺寸）
- `pipeline/agent/tools.py`（受限模式表）及相关测试、ADR-0026、Y2-r19 spec 修订注、写稿 skill 8.3、问题单 D89

## 验证

- **review**：
  - 非终端 + 有补丁段 → 清楚的 `SystemExit`，不再 `EOFError`；
  - `--patch-segments` 输出正确，前后目录树哈希不变（纯读）；
  - 带 `--confirm-patch` 照常批准。
- **桌面端**：
  - `decide` 单测：有补丁段时弹确认框，确认则 argv 带 `--confirm-patch`，取消则零 spawn；没有补丁段时步骤序列与现在一致；
  - e2e：在 TE 夹具里造一个带补丁段的 05 卡，桩掉确认框，分别走确认和取消两条路。
- **BGM 出处**：用 `check_script` / render 入口检查三种稿件（只有试看段 + 01 有 BGM → FAIL 并给出铺底块；有铺底块 + 01 有 BGM → WARN；普通期 → 无变化）；拿《束胸力学》现在的稿件跑一遍，应为 WARN（01 里还留着列表）。
- **图片**：
  - `still_argv` 纯函数测试：16:9 走铺满，1:1 和扁长图走安全框 + 补边；
  - 用真图实压，抽首帧和末帧，确认四边完整可见；
  - A/B/C 截图给你选。
- **护栏**：manifest 路径不再触发拦截；凭据文件名照样拦（含大小写变体）；现有测试按新表更新。
- **变异检验**：每处去掉修复，对应测试必须变红。
- **全量**：pytest、vitest、tsc，以及相关 e2e。桌面端有改动，提交后要重新打包。

## 施工记录（2026-10-13）

- **一、05 补丁段**：`review.py` 新增 `patch_segment_indices`、`--patch-segments`、`_interactive()` 测试缝。桌面端新增 `REVIEW_PATCH_SEGS` 模板，`decide()` 只在 `04-patch/` 存在时查询，人取消返回 `cancelled`（卡片显示「已取消」，停机点的人时区间不关闭）。旧 TA-3 把「补丁段批准失败」本身当成了预期，现拆为 TA-3（只测段级不对齐）和 TA-3p（取消 / 确认两条路）；TA-2b / 2c 原来用管道喂 `y` 模拟终端作答，改为显式 `--confirm-patch`。e2e ack.spec 26/26 通过。
- **二、BGM 出处**：`BGM:` 列表解析从 render 挪进 `bgm.topic_bgm_list`（render 保留 `_bgm_list` 别名）；判据是纯函数 `music.bgm_source_issue`，check_script「BGM 出处」与 render 入口共用。《束胸力学》现稿实跑结果为 WARN（M0、M2 铺底块，01 还留着列表）。
- **三、图片**：人选 C（取四角颜色补边）。安全框在除以推镜倍数之外再留 4% 余量（实测只除以推镜倍数时，推到顶正好贴满画面边，938 × 1.15 ≈ 1079）。真转码测试：给 1:1 图画一圈红边，末帧四边都在画面内。
- **四、护栏**：模式表只剩凭据两条；20 条拿 manifest 当样例的测试改成凭据样例，意图不变。其中两条语义有变：td1c（历史 runbook 原文现在原样发出）、真实董香二期日志（不需要投影脱敏也能过）。
- **变异**：review 非终端分支、decide 确认框与 `04-patch` 闸、BGM 出处（纯函数、render 入口、check_script 接线）、图片宽高比分流、护栏模式表，去掉修复后对应测试都变红。
