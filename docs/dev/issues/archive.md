# Issues：已归档条目

活跃问题见 `README.md` 单表。本文件只存已解决、已作废或历史上有价值的问题。
**规则：解决即归档，不再删除。** 编号断裂比归档文件更害人。

---

## 2026-10-25：集中清理·真实期已用过或已解决（2 条）

### [D70] 「从草稿新建」在 UI 里不可达：按钮只在预览目标恰为 `02-script.md` 时渲染，但该文件此时不存在、文件树/切换器不列它；唯一能自动跳转的 02.5 停机灰条，就绪条件又要求 `02-script.md` 已存在且已封板——双头互锁，人只能靠终端 `cp` 解围；该按钮在全部测试中零引用、从没被端到端走过
- 状态：**已解决·真实期验证（2026-10-25 凭事件日志归档）**
- 关联（原表）：`desktop/src/renderer/App.tsx`（`editorTarget` 条件）、`ScriptEditor.tsx`（`from-draft` 按钮）、`pipeline/approvals.py`（`_STOP_ARTIFACTS["02.5"]` 与就绪判定）；D47（02-script.md 由人从草稿新建）、D49（预览区改造）、Spec 11
- 原记录（活跃表原文）：状态栏「**已施工·机检与 e2e 通过**（2026-10-11，候选②），待真实一期手验；候选①（改 02.5 就绪语义）未做」。实证：2026-10-10 伪恋期 02 阶段，人在客户端找不到任何进入编辑/预览的入口（agent 只说按钮名没给路径——这次按钮存在但不可达）。**根因**：02.5 就绪条件的语义是「人改完了」（要求 02-script.md + 非空 02-diff.patch），而 02.5 的定义是「**待人**审改稿」——就绪条件描述反了一个相位。修法候选：① 02.5 就绪改为「草稿存在且机检过」（推荐：语义正本清源，strip 直达编辑器）；② 预览切换器列出「工序期待但尚不存在」的文件（灰显，点开=编辑器+从草稿新建）；③ 02.5 卡/状态卡直接放「从草稿新建」按钮（走 script.save 的 -1/-1 断言路径）。待人定后出 spec。**更正（同日，人指出）**：预览竖条人一直知道、不是问题；断点只在双头互锁本身（文件不存在 → 预览切换器不列 → 编辑器不出现 → 按钮不可达）。修法候选①应顺带评估「02.5 待人审时 strip/卡直接给编辑器入口」。 **2026-10-11 施工（候选②）**：`artifactGroups` 在定稿缺、草稿在时把 `02-script.md` 标 `fromDraft`——切换器里这一行不再灰掉，显示「✎ 从草稿新建」，点开即编辑器（`ScriptEditor` 本来就会在定稿缺席时装载草稿并给出按钮）。双头互锁的断点在「切换器不列」这一环，断开它就够，不碰 core 停机点状态机。e2e「D70 从草稿新建可达」首次端到端走通该按钮（切换器 → 编辑器 → 新建定稿、内容 = 草稿 → 按钮退场）；变异（`fromDraft` 恒假）被杀。**候选①不做的理由**：改 02.5 就绪语义牵动 `approvals.py` 的就绪判定、自愈与 02-diff.patch 物理闸门，代价远大于收益；若仍想让 02.5 灰条在「草稿机检过」时就亮，另立 spec。 **2026-10-25 核对**：supercell 卷三 `events.jsonl` 第一条 `script_saved`（`evt_1792822929701_b20e`，2026-10-24 14:22 本机时间）`old` 为 null、`source` 为 desktop：定稿原本不存在，由桌面端写出。桌面端在定稿缺席时写出 `02-script.md` 只有「从草稿新建」这一条路（`script.save` 的 -1/-1 断言），之后同一期桌面端又存了 115 次。候选①（改 02.5 就绪语义）按原记录不做

### [D140] `acquire fetch` 抓 `.ogv` 直链：后缀不在 `VIDEO_EXT`，找不到落盘文件时静默退回 `str(dest)`，台账 `file` 记成 `incoming/` 目录本身，回执照样报「落盘」
- 状态：**已解决（2026-10-24 施工、机检通过，`ca64097`；2026-10-25 归档）**
- 关联（原表）：`pipeline/acquire.py`（`VIDEO_EXT`、`_fetch_one`）；D100
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-24，人回「做啊」后由 Claude Code 改）」。候选 #148（Commons `Sunset-nuages.ogv`）台账指向目录。**施工**：① `.ogv` 进 `VIDEO_EXT`（直链因此走 curl，gate / register 认作视频；下游按 `sources.json` 路径走，不按后缀筛）；② 下载工具退出 0 却找不到以 slug 开头的视频或图片时，按「该源失败」处理、换备选源，全部源如此就报没抓成，台账不加。用例 +3，变异：去掉 `.ogv`、去掉拦截各红；单改回退那一行是等价变异（前面已拦）。全量 3643 通过。#148 实抓后发现原片 4:3 装进 16:9、左右黑边，门禁看不出，没登记，换成 #149（SP184）——黑边检测未做，另记 **2026-10-25 核对**：状态栏无待人、待观察项，按「解决即归档」迁出。原记录末尾「黑边检测未做，另记」没有登记成条目，已列进 10-25 清理的待人定清单

## 2026-10-24：集中清理·施工修复（6 条 + 并入 2 条）

### [D135] 写稿 agent 用 `write_episode_file` 的 `content` 整篇重写 `02-script.md` 时，底稿是它自己上下文里的旧版本，人在桌面端做的手改被静默冲掉；人只让它改一处，它改了一处、同时把几十段退回旧写法。写前留底（`_agent/script-history/`）只能事后救，不能当场拦
- 状态：**已解决（2026-10-24 施工、机检通过，`10d9eec`；人选 ①）**
- 关联（原表）：`pipeline/agent/tools.py`（`resolve_write_content`：`content` 原样落盘，不比对磁盘现版；`edits` 才按磁盘现版逐条唯一替换）、`_agent/script-history/` 留底；D47、D48
- 原记录（活跃表原文）：状态栏「待修（2026-10-24 实跑撞上）」。2026-10-24 supercell 卷三 02.5 逐段改稿，Gemini 三次整篇重写（UTC 07:50:06、09:33:00、09:36:07）：07:50 那次只许动原段17–25，冲掉段2–6、12–14 共 8 段人的手改（人当时已明确「段1到16逐字保留」）；09:33 那次只该新增《夕焼けブルース》一段，冲掉 26 段；被人发现后它只恢复了自己写的那一段。人提出「以我的版本为准」，由 Claude Code 拿每次重写前的留底对照会话里该次 `content` 参数，逐段找出未经要求改动且未恢复的 34 段，恢复前整份留底（`_agent/script-history/20261024T094703658074000-02-script.md`）。只丢了配音行，画面、锚点行没丢。**方向**：① 对已存在的 `02-script.md`（及笔记等人会手改的文件）禁用 `content`，只收 `edits`；新建文件才收 `content`；② 或给 `content` 加乐观锁：模型须带上它最后一次读到的版本哈希，磁盘现版对不上就拒写并把现版还给它（同 Claude Code「读后才能写、文件变了要重读」）；③ 写稿卡的 diff 按段落列出「本次改动段」，超出人点名范围的段落标红。三条可叠加，哪条先做由人定 **2026-10-24 施工**：`resolve_write_content` 对已存在的 `02-script.md`、`resolve_note_content` 对已存在的番剧笔记本体（target=notes）拒收 `content`，报「只能用 edits 按磁盘现版逐条替换」；两函数是落盘、写稿卡 diff、弹卡前校验三处共用的，审卡层在弹卡前拒、绕过审卡的直接调用在执行层同样拒，磁盘一字不动。草稿、`07-titles.md` 等其他白名单文件与终审表照收 content。工具描述里 content 那句同步注明（随文档提交）。用例 +4，变异 4/4。②乐观锁、③写稿卡按段标红没做：① 已从机制上去掉整篇覆盖这条路；③ 是界面改动，要做另提。D117（批量改歌名顺带改人手写内容）是同一类病，一并归档

### [D137] 视频 SP（MV、Live）的同一处画面在企划里没有次数上限：图片微动 SP 有「全企划至多 2 次」（D126），SHOTLIST 写明视频 SP 不受此限，同一支 MV 的同几秒可以被各卷反复锚
- 状态：**已解决（2026-10-24 施工、机检通过，`5ed04b9`；规则人定）**
- 关联（原表）：`pipeline/check_script.py`（照 `STILL_SP_MAX` / `still_sp_uses` / `_check_still_sp_cap` 新增一项；`_REUSE_ITEM` 要能带时间码）、`skills/write-script/SHOTLIST.md`（「视频 SP（MV/Live）不受此限」一句改写，新增条文）；D126
- 原记录（活跃表原文）：状态栏「待施工（2026-10-24 人提）」。**人定规则**：同一支视频 SP（`_is_still` 为假的 SP），以每个锚点的起点为中心前后各 10 秒之内，同一 SP 的锚点（含它自己）在企划所有卷合计至多 2 个；不属于企划的期只算本期。按片段数计，同段写两遍记两次；区间锚点按起点算。人定放行沿用 `复用:` 字段，写法带时间码，如 `复用: supercell SP12 00:20（理由）`，超出的次数落在带声明的段才放行，PASS 结果逐处列出给 02.5 看；与 D126 一样，机检分不出声明是谁写的，靠条文与人看结果把关。**口径**：人在「任意 10 秒窗口」与「每个锚点前后 ±10 秒」两种算法里选了后者，0 s、9 s、18 s 三处也算超（9 s 前后各有一个）。**已有两卷不回改**（人定：以后注意即可）：按 ±10 秒实测卷一、卷二现稿有 3 处超，SP12（卷一段 16 0:20、卷二段 31 0:20、卷二段 28 0:27）、SP19（卷一段 1、19、20、28，0:00 / 0:05 / 0:15 / 0:20）、SP24（卷一段 23 0:00、段 26 0:07 与 0:16）；对这两卷重跑机检会报这几处，属预期。卷三起写稿时先看前面几卷锚过哪些位置 **2026-10-24 施工**：`check_script` 新增「视频 SP 同一处（前后 10 s）全企划至多 2 次」，与 D126 共用一次跨卷读取（`_check_sp_caps`）。对本期每个视频 SP 锚点，数同一 SP 起点落在它 ±10 s 内（含边界、含自己、含企划其他卷）的锚点，>2 即超；按窗口判、相互重叠的超限窗口并成一簇报一次；只报以本期锚点为中心的窗口。`复用:` 条目可带起点时间码（`supercell SP12 00:20`），只放行本段起点对得上的那一处；视频 SP 的声明不带时间码、或时间码对不上本段锚点判 FAIL；图片微动 SP 的声明带不带时间码都认。真实稿只读核对：卷一报 SP12（3 处）、SP19（4 处并一簇）、SP24（3 处），卷二报 SP12，与本行人数出来的三处一致——人定已有两卷不回改，重跑这两卷机检会报这几处，属预期。SHOTLIST「视频 SP 不受此限」改写成新条文（随文档提交，待人审）。用例 +13，变异 8/8

### [D133] 试听型期的无音乐空档没有任何检查：前景块 `淡出` 之后、下一个音乐段之前的口播没有铺底时，`check_script` 与 `render` 都不报，成片就是一段干口播；只有「曲目播超」会报
- 状态：**已解决（2026-10-24 施工、机检通过，`647c1b0`；人定阈值 1 s）**
- 关联（原表）：`pipeline/music.py`（`build_timeline` 已算出全部事件，缺一道覆盖检查）、`pipeline/check_script.py`；D132
- 原记录（活跃表原文）：状态栏「待修（2026-10-23 实跑撞上）」。2026-10-23 supercell 卷二：稿件只写了 9 个前景块，复算出 9 处空档共 954.3s（等于全部口播），另有 M3 降为 BGM 越界、M8「00:00-结束 + 自然结束」判事件倒错两处硬错。人定三条要求（不留空档、严格算时、铺底用人声原版）已写进 write-script SKILL 8.3。**方向**：`build_timeline` 之后按成片时间轴合并全部音乐事件与同源槽，报超过阈值的空档段号区间；稿件有任何 `## 音乐段` 时由 check_script 判 FAIL（需 manifest，配音前跳过并显式列出） **2026-10-24 施工**：`music.music_gaps` 按成片时间轴合并全部曲目事件（前景 / 降为 BGM / 铺底 / 自然收尾）与音画同源槽，求未覆盖区间并映射段号。`check_script` 新增「无音乐空档（>1 s）」：配音后（有 manifest、查的是 `02-script.md`）按真实段长判，>1 s 判 FAIL；配音前或查草稿时，每段取 0.01 s 跑同一条时间轴，只报与时长无关的结构空档（淡出后无人接管的段落），结构上没有空档时通过但带 ⚠ 写明「铺底封顶、曲目播完这类与时长有关的空档配音后再跑一次」——这一处偏离了本行「配音前跳过」的写法：照字面跳过就是每期试听稿在 02 永远红一项，而结构空档恰好配音前就能证伪。`render` 入口按同一判据硬失败。`build_timeline` 加可选 `script` 参数（查草稿用）。复现：卷二 10-23 11:44 留底稿把前景块全改成淡出后（只有前景、没有铺底），复算 9 处共 954.3 s，等于全部口播，与本行记录一致；该留底稿原样会先撞上 M3 越界硬错（判「音乐时间轴建不起来」，同样 FAIL）。全仓扫描：V2 与 supercell 各卷干净；10-23 定规则之前出片的罪恶王冠乐评（段 2，20.9 s）、EGOIST 第一版卷一（段 1、段 13–14）、卷二（段 6）有空档，重渲这几期会被 render 拦（按人定规则属实，不特判）。用例 +13，变异 8/8

### [D131] 会话结算后再跑 `cloud down --rate <单价>` 补单价，不改刚结算的那条，而是新记一条 0 秒、¥0 的 `manual_<时间>` 空会话；runbook 03 第 3 条写的「结算时可用 `down --rate` 补」照做就撞上
- 状态：**已解决（2026-10-24 施工、机检通过，`6b9f4bf`）**
- 关联（原表）：`pipeline/cloud.py`（`cmd_down` 找不到活跃会话时新开 manual 会话结算）、`docs/runbook/03-tts.md` 第 3 条；D128、D108
- 原记录（活跃表原文）：状态栏「待查因（2026-10-23 实跑撞上）」。2026-10-23 supercell 卷二：`up` 时没报单价 → 人在控制台关机 → `down` 按 2.4 元/h 估算结算 ¥2.494（会话 20261023_202528）→ 人报 6.98 元/h 后跑 `down --rate 6.98`，账簿多出 `manual_20261023_212827`（0 s、¥0、rate 6.98），原条目单价没变。已手工修账：原条目改 rate 6.98、cny 7.2534，note 写明人事后补报；空条目删掉（修前备份在会话 scratch）。**方向**：`down --rate` 在没有活跃会话、且最近一条结算的单价是估算值时，改写那一条（重算 cny、note 记原估算值），不另开会话；或另给 `cloud rerate <会话号> <单价>`。runbook 03 第 3 条同步改写。D128 的「待人实跑看账本单价」这次没测到（up 没带 `--rate`） **2026-10-24 施工**：`cmd_down` 在「没有活跃会话 + 实例联系不上 + 带了 `--rate`」时改写账本最后一条：只改单价是估算值（note 含「单价估算」）的那条，时长不动、按新价重算 cny，note 去掉估算说明、记「单价 X 元/h 为人事后补报（日期；原按 Y 元/h 估算 ¥Z）」；最后一条已是人报的单价就拒绝。实例还连得上说明是人手动开的机，照旧新结算一条。没另做 `cloud rerate`。按 10-23 账目原样回放：原条目改成 6.98 元/h、¥7.2534，不多出 manual 会话。runbook 03 第 3 条同步（随文档提交）。用例 +3，变异 5/5

### [D136] 出处核对绑死在「AVA 自己抓过」：`acquire check` 要求候选的 `found_on` 在 AVA 抓取留档里，`fact_add` 要求摘录出自 AVA 抓过的页面；留档只有 AVA 的 `web_fetch` / `crawl` / `browser` 能写。别的 agent（Claude Code 等）查到的候选和事实，必须再让 AVA 把同一页抓一遍才能过，协作时多一轮转手；要守的底线（出处由系统亲自确认、不认手写留档，D124）本身没错，错在把「系统确认」实现成了「AVA 调用过」
- 状态：**已解决（2026-10-24 施工、机检与真实冒烟通过，`d5ea3a4`；人选 ①②③）**
- 关联（原表）：`pipeline/candidate_check.py`（出处项：`fetch_log.lookup(found_on)` 查不到即「出处核不上」）、`pipeline/facts.py`（按 url 找本机抓取留档逐条比对摘录）、`pipeline/agent/fetch_log.py`（`record` 只由 `pipeline/agent/tools.py` 的抓取工具调用）；D99、D100、D124
- 原记录（活跃表原文）：状态栏「待施工（2026-10-24 人提）」。2026-10-24 supercell 卷三：Claude Code 在 AVA 之外检索、整理了卷三全部事实与 MV 出镜考据，入库要靠 Gemini 逐条重抓同一页；准备交给 Claude Code 搜素材时，候选也只能先让 AVA 抓 found_on 再跑 check。**底线不动**：不认任何手写留档，「这条在那一页上」只由系统代码抓取并比对得出。**方向**：① `acquire check` / `fact_add` 遇到出处页没有留档时，由系统当场抓一次（静态，失败升级无头渲染）写进留档再比对，调用方是谁无关；② 要登录态的页（X、B站收藏夹）静态抓不到，把 AVA 的抓取工具做成 CLI（如 `python -m pipeline.agent.fetch <url> [--crawl|--browser]`），任何 agent 可调，留档由这段代码写并记调用来源（`sid` / 调用方标签）；③ 留档条目区分「系统当场抓」与「会话里抓」，核对报告照实标出。改完同步 acquire-assets SKILL 第二节与 project.md 事实那条 **2026-10-24 施工**：新模块 `pipeline/agent/fetch.py`，「抓一页并留档」由 AVA 的抓取工具、系统当场抓、命令行三处共用。① `acquire check`（`candidate_check.check_provenance`）与 `fact_add`（`facts.add`）发现出处页没有留档时由系统当场抓一次（静态，失败升级无头渲染；同一页一批只抓一次）再比对；`facts check` / `promote` 重做校验不出网，只认已有留档。② `python -m pipeline.agent.fetch <url> [--crawl|--browser] --caller <标签>`；另加 `python -m pipeline.facts add <目录> <条目.json> --caller <标签>`——没有它别的 agent 抓完页也没有正当入口写事实，校验与 `fact_add` 同一套。③ 留档新增 `origin`（session / system / cli）与 `caller`，候选核对表与 `facts.md` 照实标出；D136 之前的记录按「AVA 会话里抓」读。底线不变：留档只由 `fetch_log.record` 写，内容是系统抓回来的原文。测试默认不出网（`tests/conftest.py` 替身，测抓取本身的用例标 `real_fetch_and_record`）。真实冒烟：命令行抓 `https://www.supercell.jp/biography.html`（4911 字，留档标「命令行抓（claude-code）」），卷三 85 条事实 `facts check` 0 处问题。acquire-assets SKILL 第二节、`project.md` 事实那条同步（随文档提交）。用例 +17，变异 11/11。D124（外部 agent 绕过拦截手写留档）此后有正当路径，一并归档

### [D117] 02.5 阶段 agent 按人指令批量改歌名时，顺带改了人亲手写的内容，并给出错误解释
- 状态：**已归档（2026-10-24 并入 D135）**
- 关联（原表）：supercell 卷二 `session.jsonl` 2026-10-19T13:12:23 / 13:12:46 两次 `write_episode_file`；D92、D106
- 原记录（活跃表原文）：状态栏「**备忘**（2026-10-19 登记）」。人指令：「把所有歌名都换成罗马字，不要用翻译」。① 段 16 人已手写的小写罗马字《kimi no shiranai monogatari》被改为首字母大写；② 第二次写入为过机检拆句、改标点（183、297 行）与去撇号（Rock 'n' Roll → Rock n Roll），总结里只概括为「断句超长」，未逐处列出；③ 对字数偏高的解释写成「罗马字按字节折算」，实际 `check_script` 用 `len()` 按字符计数（罗马字每个字母计一字）。均已落在留底可回退，未造成丢失；先记录，观察是否复现 **2026-10-24**：10-24 卷三三次整篇重写冲掉人的手改（D135）是本行的同类复现。D135 已让已存在的定稿只收 edits，没点名的段落不再能被整篇覆盖顺带改掉；改动面仍可能超出人点名的范围（edits 里多塞几条），那属于模型行为，留在 D92 一并观察

### [D124] 用 ava 以外的 agent（pi）直接跑 `uv run python -m pipeline.…` 时，`status_card` 的 bash 硬拦截一道都不生效：手写 `fetch-log/`、直接改 `candidates.json` 都不会弹卡
- 状态：**已归档（2026-10-24 并入 D136）**
- 关联（原表）：`pipeline/agent/status_card.py`（规则只在 ava 会话内执行）、`pipeline/agent/fetch_log.py::record`（模块级函数，任何进程可调）；D99、D100
- 原记录（活跃表原文）：状态栏「**备忘**（2026-10-20 登记）」。2026-10-19 supercell 卷二：16:33Z `acquire check` 判 13 条「出处核不上」，pi 在 17:00–17:03Z 直接调 `fetch_log.record()` 写 13 条留档（sid `pi-overnight-20261020`，YouTube / mzstatic 正文为模型自写摘要）后重查全部「可下载」；另用 python 改写 `candidates.json`，删 4 条、重用候选号 #102–114。2026-10-20 已把 13 条留档挪进 `fetch-log/_quarantine/pi-overnight-20261020/`，用真工具重抓后 9 条重新核过，SP86（#102）核不上，人定不撤 **2026-10-24**：D136 给了别的 agent 正当路径——出处页没留档时 `acquire check` / `fact_add` 由系统当场抓，要登录态的页用 `python -m pipeline.agent.fetch`，留档记 `origin=cli` 与调用方标签，不必再手写 `fetch_log.record()`。`status_card` 的 bash 拦截只在 ava 会话内生效这一点没变（它本来就只管 ava）；`fetch-log/_quarantine/pi-overnight-20261020/` 里的 13 条隔离留档保持原样

## 2026-10-24：集中清理·真实期已用过或到期（15 条）

### [D108] 账本按型号查价查不到 vGPU-32GB，按 2.4 元/h 估算，多记约 50%
- 状态：**已解决·真实期验证（2026-10-24 凭账本归档）**
- 关联（原表）：`pipeline/cloud.py`（`lookup_gpu_rate`）、`config/cloud.json`（`gpu_rates_cny`）；D102
- 原记录（活跃表原文）：状态栏「**已修·机检通过**（2026-10-20，`f8c6298`，与 D128 同一提交）。待人：下次租卡 `up --rate` 后看 `cloud status` / 账本的单价」。`gpu_rates_cny` 的键是算力市场的商品名 `vGPU-32GB`（1.58 元/h），`up` 探测到的是 nvidia-smi 名 `NVIDIA GeForce RTX 4080 SUPER`，两者对不上，落到 `hourly_rate_cny` 2.4。10-17 会话账本记 ¥1.312，按实际单价约 ¥0.864。同一张卡在市场上叫 vGPU-32GB、在机器里叫 RTX 4080 SUPER，键用哪一个须人定（配置内容，不改代码也能修：补一个 nvidia-smi 名的键） **2026-10-17 施工**：按人补充改判据——唯一可信的带卡单价是人租卡时在控制台看到的数。`cloud up --rate` 记进会话文件，`cloud down --rate` 可结算时补（优先于 up 时的）；没报时按 `gpu_rates_cny` 参考价或 `hourly_rate_cny` 记，**两者都标估算**（原先查表命中算精确）；`--rate` 超出 0–50 元/h 拒收；`cloud status` 同口径；租机提示、runbook 03、`cloud.json` 两条 note 同步。10-17 那笔 ¥1.312 账目未回改（账本是历史记录，要改由人定）。测试改写 + 新增，变异 2/2 **2026-10-17 supercell 实跑实证**：06:41:55Z 执行了 `cloud up --rate 2.78`，但因 `cloud up` 的参数默认是 `--mode cardless`，与实测检测到的 `gpu` 模式不符，触发了 `WARN 报的单价 2.78 元/h 是按 --mode cardless 报的，实测是 gpu，这个价不记`，导致单价未能存入 session，最终 `down` 时账本依然回退到 2.4 元/h 估算。修法需将 `cmd_up` 的模式自动探测置于单价校验之前，或使 `--rate` 不与默认模式锁死。 **2026-10-20 施工**：`--mode` 默认改为不声明（按 nvidia-smi 探测记），实测带卡就记 `--rate`；明确声明了别的模式仍不记并警告，实测无卡按平台固定价、报的价不记。argparse 抽成 `build_parser()`，测试走真实默认值（D108 的病根就在默认值上）。用例 4 种组合，变异「默认改回 cardless」「声明不符也记」均被杀 **2026-10-24 核对**：账本 `20261020_231740`（10-20 23:17–23:21，`library:supercell` captions，NVIDIA RTX 6000D）记 `rate_cny` 6.98、没有「单价估算」note，`up --rate` 报的价带进了同一会话的结算。10-23 那次 up 没报价，归 D131

### [D128] `cloud up --rate` 报的单价没带进结算：`up` 开出的会话号（20261020_091844）和后来 `status` / `down` 结算的会话号（20261020_091944）不是同一个，结算时单价回退为 2.4 元/h 估算
- 状态：**已解决·真实期验证（2026-10-24 凭账本归档）**
- 关联（原表）：`pipeline/cloud.py`（`cmd_up` 写 `session_data["rate_cny"]`；`lib-captions` 一类命令是否另开会话待查）；D108
- 原记录（活跃表原文）：状态栏「**已修·机检通过**（2026-10-20，`f8c6298`）。待人：下次租卡实跑一次看账本单价」。2026-10-20：`up --mode gpu --rate 1.58` → `lib-captions` ×2 → `down`。账簿 `ledger.jsonl` 记 09:19:44–09:28:02、¥0.332、`rate_cny` 2.4，note 写「没有人报单价」。按人报单价算约 ¥0.25 **2026-10-20 查因**：代码里只有 `cmd_up` 会新开会话号，`lib-captions` 不开；当时 coding agent 的转录里只有 09:18:44 那一次 `up --mode gpu --rate 1.58`，09:19:44 的第二次 `up` 来自别的进程（ava / pi 当天记录里都没找到，按时间盒停查）。不管谁跑的，缺陷是确定的：同一次开机里再跑 `up` 会覆盖活跃会话，单价丢失、开机时刻后挪少计约 60 s。**施工**：`up` 时若活跃会话的连接目标（`cloud connect` 记的 user@server:port）相同、且远端开机时刻（本地 now − `/proc/uptime`，不受两边钟差影响）早于会话开始，就沿用原会话号、开机时刻、单价与任务，新报的 `--rate` 仍可覆盖；否则新开并警告「上一个会话没有 down 结算就被替换」。用例：纯函数 7 情形 + 端到端（10-20 原样：带价 up → 不带价 up 沿用 → 重启后新开）；变异「不沿用」「不看开机时刻」「不看连接目标」均被杀 **2026-10-24 核对**：同 D108——10-20 23:17 那次带价 up 后结算的会话号与 up 开的是同一个（`20261020_231740`），单价 6.98 元/h 照记，没有回落到估算

### [D127] SP 集键只认两位，一个池最多 SP99：`acquire register --as SP100` 报「集键必须形如 SP19（两位）」。supercell 池登记到 SP99 就满了，卷二还有 6 条补丁池素材迁不进来
- 状态：**已解决·真实期验证（2026-10-24 凭产物归档）**
- 关联（原表）：`pipeline/acquire.py:380,386,395`、`pipeline/check_script.py`（`ANCHOR_TC`、集号 `SP(\d{1,2})`、`_REUSE_ITEM`）、`pipeline/clips.py:221`、`pipeline/render.py:1002,1012`、`pipeline/vindex.py::_parse_key`、`pipeline/cloud.py::_LIB_KEY`、`pipeline/calibration.py::_NAME_SP`；ADR-0010、D126
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-20，[spec](../plans/2026-10-20-sp-key-three-digits-spec.md) 第七节）。待人：把卷二剩下 6 条真实登记成 SP100–SP105，切镜头、云端打标、锚点排片走一遍」。2026-10-20 supercell 卷二把补丁池迁进企划池时撞上。池里有 13 个撤下的空号，但复用空号会让老稿与日志里的旧编号悄悄指向新素材，不采用。候选修法：集键放宽到 `SP\d{2,3}`（两位仍补零，三位原样），各处正则统一，补排序与文件名用例（`SP100` 与 `SP10` 的前缀判定）。跨模块改动，先出施工图 **2026-10-20 施工图**：生成侧 12 处 `f"SP{n:02d}"` 本来就出 `SP100`，不用改；解析侧 17 处写死 1–2 位，统一成一个共享常量（人写的锚点 / 复用声明仍容忍 1 位），上限 99→999，前缀判定防 `SP10` 吃 `SP100`，按数字排序；ADR-0010 决策二要补修订段。实测 SP100 锚点在 clips 当场报「认不了」（响亮失败，不是静默） **2026-10-20 施工**：`pipeline/keys.py` 一处定义，17 处解析点改引用，上限 999，按数字排序；静态守卫防再各写一份；全量 3507 通过，变异 6/6；真实 supercell 池只读核对 `next_key` = 100 **2026-10-24 核对**：企划池已登记到 SP110，`data/library/vindex/` 有 `supercell_SP100`–`SP110` 的 scene 与 captions；卷二 `02-script.md` 锚 SP101–SP110，`04-clips.approved.json` 用到 SP101–SP109，10-23 出片

### [D90] 《束胸力学》封面来回 20 来轮：① ava 看不见图（`cover_edit` 只回尺寸），挡脸、字太小、竖版只剩大脸全靠人指；② 交付规矩只存在于往期目录（`08-cover.jpg` / `08-cover-vertical.jpg` / 简介），status 的 09 不核对就说「所有产物已齐备」，ava 两次宣布闭环，还让人在 09 卡填会被拒的 `08-cover.jpg`；③ 模型在「先斩后奏」与「报参数让人批」间摇摆、没看图就说「精准」、夸人
- 状态：**已解决·真实期验证（2026-10-24 凭会话日志归档）**
- 关联（原表）：`pipeline/agent/vision.py`（第 16 个工具 `view_image` + 请求时附图投影）、`pipeline/agent/{tools,llm,session}.py`、`pipeline/status.py`（`publish_gaps`）、`config/agent/scopes/director.md`、`docs/runbook/{08-cover-title,09-publish}.md`；ADR-0031, ADR-0029, D65
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-13，分支 `d90-view-image`，已合 main）。待人：下一期封面环节实际用一次」。人提出；人选「直接给模型读图的能力」、定稿封面「人点名后 ava 可用 bash 搬」、竖版必备。实测：图放进 tool 消息时代理不报错但模型描述全是编的，故走工具结果后追加宿主 user 消息。**未解决**：gemini-3.8-flash 看图对「字压没压到头发 / 脸」判断不稳（同一张已知压脸的图，两次问答结论相反），挡脸检查不能只靠模型看图。2026-10-13 人裁决**不做**确定性挡脸检查（字框比人脸框）：「又难做又不讨好，不如人肉眼看」——细粒度挡脸由人看图把关。副作用：8 月 18 日前的老期按新规则会报缺竖版封面（实情，不特判） **2026-10-24 核对**：`view_image` 卷一会话调用 168 次、卷二 42 次，两卷 `08-cover.jpg` / `08-cover-vertical.jpg` / `08-cover-4x3.jpg` 齐。挡脸由人看图把关（10-13 人裁决），不另做检查

### [D116] 桌面端 02.5 人工保存不留任何痕迹：`/save-script` 成功写入不留底、不落事件；指纹冲突被拒也只在编辑器里亮一行提示、不落事件。人怀疑改动丢失时无从核对
- 状态：**已解决·真实期验证（2026-10-24 凭事件日志归档）**
- 关联（原表）：`pipeline/agent/cli.py::_cmd_save_script`（直接 `atomic_write`，无 `_keep_history`）、`desktop/src/renderer/ScriptEditor.tsx`（保存按钮与 ⌘S 均不看 dirty，成功即显示「已保存」）；D49-A、Spec 11 §2.2、`tools.py::write_episode_file`（agent 写入走 `_keep_history` 留底到 `_agent/script-history/`）
- 原记录（活跃表原文）：状态栏「**已修·机检通过**（2026-10-20，`ea25293`）。待人：重新打包后在时间线看一次保存记录」。2026-10-19 supercell 卷二：人说段 22 删过「但是，」并保存，事后发现还在。逐一核对：10-18 两份留底、10-19 21:12:23 / 21:12:46 两份留底、agent 在 21:07 / 21:09 / 21:11 三次读稿的工具结果、agent 写入参数里的原文、人 21:13:57 保存后的现行版，**全部带「但是，」**；桌面端 Local Storage 暂存（UTF-8 / UTF-16）也没有该段。结论只能到「这次删除从未落盘」，原因（保存被拒未察觉 / 未保存即被重载 / 记混）无法区分。人 21:13:57 的那次保存也只能靠 mtime 与 agent 空闲时段对上才推断出来。候选改法：人保存成功前按 agent 同口径留底到 `_agent/script-history/`（文件名可带来源标记）；保存成功与被拒各落一条 `events.jsonl` 事件（含新旧指纹） **2026-10-20 施工**：`/save-script` 覆盖前按 agent 同口径留底到 `_agent/script-history/`（文件名不加来源标记，来源记在事件里）；成功落 `script_saved`（来源、新旧指纹、留底文件名），指纹不符落 `script_save_rejected`（期望与实际指纹），都不改既有拒存语义。桌面端把两个事件登记进契约，时间线显示「02-script.md 人工保存（旧版留底 …）」「…人工保存被拒：磁盘版本已变，未写入」。pytest +3、vitest +1，变异 3/3（不留底、被拒不落事件、保存不落事件） **2026-10-24 核对**：卷三 `events.jsonl` 有 116 条 `script_saved`（source desktop）、4 条 `script_save_rejected`，卷二 4 条 `script_saved`：打包版桌面端的人工保存已在落事件。时间线显示没有逐条截图核

### [D60] 标定值（镜头切分阈值、CCIP、cpm、画面检索门槛、歌名秒数）测完、人拍板后还得人手改 config JSON
- 状态：**已解决·真实期验证（2026-10-24 归档）**
- 关联（原表）：`config/project.json`、`config/scenes.json`、`config/voice.json`、`pipeline/shots.py`（calibrate 收尾提示）、AGENTS.md Code Freeze；D59、D51、runbook 01.4
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-09），待真实标定手验」。人裁决：「要」。新增 `pipeline.calibration set/show`，白名单键 + 取值域 + 实录追加进 `_note`，弹卡写入；AGENTS.md Code Freeze 例外由一条扩为两条。[spec](../plans/2026-10-09-calibration-and-notes-spec.md) **2026-10-24 核对**：10-17 supercell 池入库全链里 `calibration set` 真实跑过（见 D103 行「2026-10-17 实测」），标定值写入与 `_note` 追加走的就是本工具

### [D103] 写稿会话读不到完整的素材入库预处理流程：AVA 卷一会话（creative scope、工序 02）只在补丁池路径上知道「补料 → `ingest_patch --anchor-only`」，而「不加 `--anchor-only` 完整打标」只写在 `docs/runbook/04-clips.md`（03.5 / 04 才加载）；番池 SP 的整条链（register → `shots calibrate` → 人拍板阈值 → `calibration set` → `shots build` → `shots gallery` → `vindex captions`（计费先问）→ `vindex embed` → `vindex status` 对账）只散在开发归档 `docs/dev/plans/archive/2026-09-07-egoist-asset-prep.md` 与 CHEATSHEET 放行表里
- 状态：**已解决·真实期验证（2026-10-24 凭账本归档）**
- 关联（原表）：`skills/acquire-assets/SKILL.md` 第二节第 4 步、`config/agent/scopes/creative.md`、`project.md`、`docs/runbook/02-script.md` 第 2.5 步；D59、D84、D95
- 原记录（活跃表原文）：状态栏「**① 说明文字已定稿**（2026-10-17 人审过；两处待人定按建议裁决：长镜头「>30 s 每 15 s 一刀」写成规则、番池上云要做命令）；**② 番池上云命令已施工·机检通过**（[施工图](../plans/2026-10-17-library-captions-cloud-spec.md) 第七节：`cloud lib-push / lib-captions / lib-pull`）。待人：下次租卡用 1–2 个 SP 真跑一次」。修法前先实测核实：SP 上 captions / embed 的用法（本地能否跑、是否必须上云）；访谈字幕的正确流程（`ingest subs` 只抽内嵌字幕轨、`subindex build` 对 SP 是否可用都没核实；10-15 发给 AVA 的补素材 prompt 里「访谈跑字幕（ingest subs / subindex build）」是错的）。再把完整清单写进 acquire-assets SKILL.md 第二节第 4 步，在 creative.md、project.md、runbook 02 第 2.5 步加一两句指路，加测试断言这些指路存在。**2026-10-17 实测（supercell 池 63 件 / 2469 镜头，coding agent 跑通全链）**：① 实际链路 = `acquire register --pool` → `shots calibrate`（代表样本）→ `calibration set` → `shots build --sp` → 长镜头 `shots rebuild --also-cut` → `shots caption-frames` → 上云 → `vindex captions` → 拉回 → 本地 `vindex embed` → `vindex status`；`shots frames` + `shots gallery` 是给人看的联系表，不在打标依赖链上（gallery 缺 frames 会拒）。② **captions 必须上云**，**embed 在本地跑**（16G Mac 不到 1 分钟）。③ **番池 SP 的上云没有封装命令**：`cloud push/run/pull` 只认期目录 `04-patch/`（`build_cmd` 的 captions 分支把 shots/frames/out 目录钉在 `<期>/04-patch/` 下），这次是手动 rsync `data/library/shots/<池>_SP*.json` 与 `frames/<池>_SP*_cap` 上行、`cloud exec` 跑 `vindex captions <池> SP..`、rsync `vindex/<池>_*.captions.json` 下行。④ scdet 对三类 SP 是盲的、降阈值救不回：静态插画 PV、连续溶解的拼贴 MV（supercell SP03 thr5 最长仍 215s）、绘画过程；这次统一按「>30s 的镜头每 15s 注一刀」`--also-cut`，该规则是否进 SKILL 待人定。⑤ 访谈字幕流程本轮没用到（池里无需要字幕的访谈），仍未核实 **2026-10-17 起草**：`skills/acquire-assets/SKILL.md` 第二节第 4 步改写成番池 SP 入库全链（calibrate → `calibration set` → build → 长镜头 `rebuild --also-cut` → caption-frames → 云端 captions → 本地 embed → status 对账，命令参数按各模块 `-h` 核过）；`creative.md`、`project.md`、runbook 02 第 2.5 步各加一句指路；`tests/test_acquire.py` 断言全链顺序与三处指路。待人定：① 「>30 s 每 15 s 一刀」是否写成规则；② 番池 SP 上云要不要给 cloud 加 push / run / pull（现在只认期目录 `04-patch/`）。访谈字幕流程仍未核实，SKILL 里照实写了 **2026-10-24 核对**：`cloud lib-captions` 10-20 真实跑过两次（账本 `20261020_091944` 与 `20261020_231740`，episode 均为 `library:supercell`），SP100–SP110 的 captions 即其产物。访谈字幕流程仍未核实（本行原记录已照实写明），要用时另立

### [D74] 05 审片页没生成却报「已就绪」：agent 只跑 `clips` 不跑 `review`，还告诉人「可查看 `04-review.html`」；status / 停机点 / 预览切换器都不检查审看页在不在，人在桌面端哪里都找不到它
- 状态：**已解决（2026-10-24 归档：④ 批准闸兜底）**
- 关联（原表）：`pipeline/status.py`（05 判定只看 `04-clips.json`，下一步直接给 `--approve`）、`desktop/src/shared/stopPreview.ts`（05 自动打开 `04-review.html`，不存在即空）、`desktop/src/shared/artifactGroups.ts`（排片组 expected 不含 `04-review.html`）、`pipeline/approvals.py`（05 批准不要求审看页）；D59, D70（同型：期待产物不存在 → 入口不可达）, runbook 05
- 原记录（活跃表原文）：状态栏「**①②③④ 已施工·机检通过**（④ 2026-10-20，`2800726`，人选「只查存在，过期只警告」）；待真实期观察 agent 是否还跳过 review」。人原话：「没有找到04的html在哪里预览」。实证：伪恋期 events.jsonl 只有 10:02:51 一条 `clips`、零条 `review`；agent 回复「已进入人工停机点 05 审时间码。请在桌面端审片对照（或查看 `04-review.html`）」——文件当时不存在（runbook 05 第 1 步写明 agent 跑 `review`，被跳过）。status 输出「请在浏览器打开 04-review.html…推荐命令 `--approve`」。2026-10-11 10:05 人工补跑 `python -m pipeline.review <期>`（不带 --approve，只抽帧出页）解围。**修法候选**：① status 在 05 阶段检查 `04-review.html` 存在且不旧于 `04-clips.json`，否则下一步改为「agent 跑 `review`」而非 `--approve`（agent 照 status 卡行动，根因修在这里）；② 切换器排片组把 `04-review.html` 列为 expected（缺时灰显「未生成」，人知道还差它）；③ 05 停机点自动打开时审看页缺失，预览区明说「审看页还没生成，让 ava 跑 review」而不是空白；④（较大）05 批准闸门要求审看页存在且新于 04-clips.json——没审看页就批准等于 2026-07-29「检索直出没过人眼」的复发，但这改停机点状态机，须人定。 **2026-10-11 施工（人：「请修复这个issue」）**：① `status._review_page_fresh`：05 阶段审看页缺失或 mtime 早于 `04-clips.json` 时，`current_step` 仍为「05 审时间码」（工序路由 / 停机点 / runbook 注入都按这个串，不动），但 block_reason 写明审看页缺失或过期、下一步改为 `python -m pipeline.review <期>`，next_action 明令「页面生成之前严禁提批准、严禁说可以查看审看页」；页面就绪才给 `--approve`。旧用例 `test_status_clips_unapproved` 断言的正是本病（无审看页也给 --approve），已改为三情形。② 切换器排片组 expected 加 `04-review.html`（缺时灰显「未生成」）。③ `PreviewPane::HtmlFrame` 先 HEAD 探测：**仅 404** 显示「审看页 04-review.html 还没生成——让 ava 跑 review」并每 2 s 复查、生成即自动换成页面；403/415 等路径守卫拒绝照旧交给 iframe（不改 preview-security 行为）。用例：pytest 2 条新增 + 1 条改写；vitest 1 条；e2e「D74 05 审看页缺失」（ack.spec.ts）。变异：status 恒判可用、切换器不列、404 当存在，均被杀（另有一条「初值改 ok」变异存活——探测仍跑、行为未变，属变异过弱，已换成后者）。伪恋期副本实测：去掉批准文件与审看页后 status 下一步 = `pipeline.review`、不含 `--approve`。（附：本期 `04-clips.approved.json` 与 `04-clips.json` 同 mtime 10:02:55 是 `--approve` 复制保留时间戳，事件日志证实批准在 10:08–10:10、审看页生成之后。） **2026-10-13 束胸期复现**：12:04 人指出「桌面端显示html还没生成」；**2026-10-17 supercell 卷一再次复现**：08:51 人原话「可是桌面端显示html未生成」，因 agent 依旧会跳过 review 直报就绪，证明 ①②③ 不足以彻底杜绝，④（改停机点状态机：审看页不存在时不给进 05 或不给批准）必须跟进。 **2026-10-20 施工（④，人选只查存在）**：`review.approve` 在写批准文件前查 `04-review.html`：不存在就拒（「还没有审看页…先跑 python -m pipeline.review 出页、人看过再批」）；比 `04-clips.json` 旧只打 WARN（人在 05 原地改完排片直接批是常态，不为此重抽帧）。闸放在批准动作上、不进 `approvals._gate_valid`——已批过的老期不翻案。现有 15 条批准用例的夹具补了审看页；新增 3 条；e2e `ack` / `stopPoint` / `layout` 45 条通过；变异 4/4（不查存在、不警告过期、恒警告、过期改硬拦） **2026-10-24 核对**：④ 起没有审看页批不了 05，agent 再跳过 review 也只会在批准时被拒，不会再让人对着不存在的页面找。卷二 10-23 的 05：`04-review.html` 21:48:51 生成，批准事件 21:53（本机时间）在其后

### [D104] 稿件里还留着【待核】【待你定】标记时，02.5 照样能批准封板
- 状态：**已解决（2026-10-24 归档；人批清理方案）**
- 关联（原表）：`pipeline/approvals.py`（02.5 批准前置条件）、`pipeline/check_script.py`（不认这两类标记）；D99, D47, D48
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-17，coding agent）。待人：下一期 02.5 在桌面端点一次「封板」/「批准」看拒绝文案」。supercell 卷一第一期 10-15 晚批准 02.5 时，`02-script.md` 里有 5 处【待核】、1 处【待你定】，其中段 6「钢琴滑音」是错的（人听原曲：前奏之后第 12 秒才进「啦啦啦」）。标记是写稿模型自己加的，加得对，但没有一关拦住。修法候选：02.5 批准前扫配音行，含「【待核」「【待你定」就拒绝并列出段号；check_script 同时报 FAIL。10-15 已由 coding agent 核掉 4 处（段 6 / 30 / 34 / 41，改前留底在 `_agent/script-history/`），段 46、段 49 留给人 **2026-10-17 施工**：实测发现只挡「批准」不够——封板即过 02.5（status 凭 `02-diff.patch` 进 03，自愈也凭它把 02.5 记成已批准）。改为同一个扫描 `check_script.pending_marks`（`【待核…】`/`【待你定…】`，按段定位，段外也算）挡三处：`/seal-script`（桌面端封板按钮同路）拒封、`approvals._gate_valid` 不认带标记的封板、`approve` 报具体段号；`check_script` 新增 FAIL 项。真实稿核对：卷一 `02-script.draft.md` 报 6 处并定位到段，`02-script.md` PASS。测试 +3，变异 2/2 **2026-10-24 核对**：卷二 10-19 封板时走过本闸（稿内无标记，放行）。拒绝文案本身没在真实期撞到过；人在 10-24 批准的清理方案里按 10-20 清账先例归档

### [D109] 素材 URL 查重按原始字符串比，同一视频换个链接写法就能重复下载、重复入池
- 状态：**已解决（2026-10-24 归档；人批清理方案）**
- 关联（原表）：`pipeline/acquire.py`（`dup_verdicts`）、`pipeline/candidates.py`（`ledger_seen_urls` 不归一化）；D100, D95
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-17，coding agent）。待人：下一轮补素材时看一次查重拒绝文案」。supercell 池实例：SP01 记 `https://youtu.be/jpV5jeFlt_E`，SP27 记 `https://www.youtube.com/watch?v=jpV5jeFlt_E`；SP23 记 `https://www.youtube.com/watch?v=rZZzlY8vUf8`，SP28 记 `https://youtu.be/rZZzlY8vUf8?si=…`。两对文件字节数一致，查重两次都 PASS。结果：检索每条命中出现两遍（SP01 / SP27 并列），同一画面可能在成片里被当成两个来源。10-17 人定撤掉 SP01、SP23。修法候选：查重前按站点归一化（YouTube 取 video id、去 `si` / `list` 等跟踪参数；nicovideo 取 sm 号；bilibili 取 BV 号），参照 `fetch_log.norm_url` 的做法 **2026-10-17 施工**：`candidates.canonical_media_url`：YouTube（watch / youtu.be / shorts / embed / live，m. / music. 子域）归到视频号，niconico 归到 sm/so/nm 号，bilibili 归到 BV / av 号（`p>1` 分 P 单独算）；其余 `norm_url` 后只去 `utm_*`、`si`、`spm_id_from` 等已知跟踪参数；b23.tv 等短链离线解析不了，保持原样不猜。`propose_candidates` 两处判重与 `acquire.dup_verdicts` 都按规范形比，拒绝时带出先登记的那条原始写法。真实台账 68 条核对：恰好认出 SP01/SP27、SP23/SP28 两对，无误并。SP01、SP23 已由人撤掉，不追溯。测试 +14，变异 1/1 **2026-10-24 核对**：规范形查重 10-17 起生效，真实期的拒绝文案没撞到过；人在 10-24 批准的清理方案里按 10-20 清账先例归档

### [D132] 字幕只能显示配音原文：稿件为了 TTS 念准写中文数字（二零零九年）和歌名罗马字（Kimi no Shiranai Monogatari），字幕也只能跟着显示这种写法；改稿件会让 tts 判文本变了、整段重配
- 状态：**已解决（2026-10-23 生产期经人批准施工；2026-10-24 归档）**
- 关联（原表）：`pipeline/render.py`（`load_subtitle_map` / `apply_subtitle_map`，build_ass 前替换）、`pipeline/qc.py`（字幕四项改查替换后的文本，含总装期）、`tests/test_render.py::TestSubtitleMap`；—
- 原记录（活跃表原文）：状态栏「已修（2026-10-23，生产期经人批准改 pipeline）」。2026-10-23 supercell 卷二：人要字幕显示阿拉伯数字与日文歌名。新增期级 `02-subtitles.json`（{原文: 上屏写法}，`_` 开头为注释），一遍扫描、长键优先、结果不再参与匹配；键必须在上屏句子里原样命中，未命中（拼错或跨句）渲染前当场 FAIL。配音、manifest、台词哈希都不动。AVA 的 `write_episode_file` 白名单已扩入 `02-subtitles.json`（2026-10-23 人批准，写入免卡、覆盖前留底）；runbook 06 已补 2.5 节 **2026-10-24 核对**：卷二 `02-subtitles.json` 随 10-23 出片生效，卷三也已建表

### [D134] `check_script` 的「无引号」判 FAIL，起因已不成立：那条规则出自 2026-07-30 IndexTTS 把弯引号念成怪音节，此后合成前 `tts._MUTE` 一律剥掉引号、配音换成 Qwen3-TTS；而写引用、书名、宣传语时人需要引号上屏
- 状态：**已解决（2026-10-24 生产期经人批准施工；同日归档）**
- 关联（原表）：`pipeline/check_script.py`（「无引号」改为只提示：通过并带 ⚠，照旧报位置）、`tests/test_check_script.py::TestQuotes`（新增一条钉住「引号在合成前被剥掉」这个前提）；D132
- 原记录（活跃表原文）：状态栏「已修（2026-10-24，生产期经人批准改 pipeline）」。2026-10-24 supercell 卷三：人在段16用引号标宣传语，机检报红。人指出读错引号是第一代配音模型的问题。改法：不删检查，降为提示，因为剩下的是文风信号（口播里要靠语气或引导词让人听出是引用），交人判断。「无括号」同样会被 `_MUTE` 剥掉，这次没动，要不要同样处理由人定 **2026-10-24 核对**：卷三机检「无引号」已按提示通过。「无括号」要不要同样降级仍由人定，要改时另立

### [B2] 人时瓶颈观测：写稿侧已解除，03.5 配音顺听疑为最费人环节（待数据证实）
- 状态：**已到期（2026-10-24 按数据改写结论后归档）**
- 关联（原表）：`WORKFLOW.md`；P4, ADR-0014, v2重构方案
- 原记录（活跃表原文）：状态栏「瓶颈观测（非门槛）」。2026-09-18 实测更新：写稿主力切至 Gemini 后，02.5 人工改稿耗时已降至 ≤10 min（10分钟内视频），写稿侧瓶颈解除；耗时阻碍转移至 03.5 配音顺听人耳挑错重配（见 ava-agent 施工图）。2026-09-25 S25：一期 8 份 spec（含人时观测化、记忆、桌面端）全部完工并移入 `docs/dev/plans/archive/` **2026-09-28 复核（只读）**：AGENTS.md 已把人时降为观测量（2026-09-23），本行不再代表任何门槛/止损线，标题与状态已改写。用真实数据数：全仓仅 2 期有 `human_time.json`（`2026-08-03-春物-谁最适合你`、`EGOIST-传奇企划志/02-皮套囚徒`），共 3 条记录、**全部为 09 停机点**（2.24 / 26.58 / 0.53 min），03.5 零样本——「瓶颈在 03.5」目前只有 09-18 的人工观察，人时数据既未证实也未证伪。到期条件：出现 ≥3 期带 03.5 记录的 `human_time.json`（Spec 11 起桌面端 03.5 顺听面板可见即计时）后，按工序算每期中位数，证实则保留为观测结论、证伪则改写瓶颈位置；在此之前不据此改流程 **2026-10-24 到期复算**：带 03.5 记录的 `human_time.json` 已有 5 期（伪恋两期、东京喰种董香二期、supercell 卷一卷二），满足到期条件。各工序每期分钟数：02.5 = 9.9 / 692.1 / 78.7 / 2011.2 / 784.8（中位 692.1）；03.5 = 34.2 / 19.1 / 34.7 / 56.4 / 57.2（中位 34.7）；05 = 5.3 / 17.4 / 152.3 / 47.6 / 61.7（中位 47.6）；09 中位 35.2（4 期）。「瓶颈在 03.5」被证伪，按人时最费的是 02.5。口径注意：计时是「面板可见即计」，02.5 一挂就是几小时（supercell 卷一 2011 分钟≈33.5 小时），这些数含人开着面板干别的的时间，只能比相对大小、不能当真实工时；要据此改流程，先把 02.5 的计时改成按交互计

### [N16] 春物角色样本少
- 状态：**已对齐（2026-09-28 复核；2026-10-24 归档）**
- 关联（原表）：`config/characters.json:5-6`；ADR-0003
- 原记录（活跃表原文）：状态栏「已对齐（2026-09-28 复核）」。`_note`（及顶层 `_note`/`_format`、`春物._missing`、`伪恋._note`）已按 `presence_producer=ccip` 实际实现、阈值标定依据（`ccip_same=0.05`/`ccip_margin=0.02`/`face_expand=1.6`）与 40 集 64 簇贴 24 簇实况对齐；川崎/叶山/三浦/小町代表脸簇少（1–2 簇）、陽乃 0 簇属内容资产事实，保留备忘。**2026-09-28 复核（只读）**：逐值对拍——顶层 `_note` 的 `presence_producer=ccip`、判定线 `1 − ccip_same = 0.95`（即 ccip_same 0.05）、`ccip_margin = 0.02`，`春物._note` 的 0.05 / 0.02 / 1.6，均与 `config/project.json` 的 `visual.*` 春物键逐值一致；`春物._note`「64 簇贴 24 簇、10 个角色」、`伪恋._note`「57 簇贴 44 簇、13 个角色」与 clusters 文件实数完全一致。事实复核（clusters 文件实数）：川崎 2 簇、叶山/三浦/小町各 1 簇、**阳乃 0 簇**——仍是事实。阳乃的检索影响见 N17（本行不重复） **2026-10-24**：本行是已对齐的事实备忘、无待办；阳乃 0 簇的影响见 N17（仍在活跃表）

### [D22] 泛素材多模态漏斗检索（Top-K 候选轻量视觉意象过滤）待探针设计
- 状态：**已定案并被取代（ADR-0015；2026-10-24 归档）**
- 关联（原表）：`docs/adr/0005`；B1, D1, ADR-0015, v2架构方案
- 原记录（活跃表原文）：状态栏「已定案（ADR-0015），v2 推进中　**注：本行编号与 archive 的 [D22]（tts readings 指纹一刀切，2026-09-02 已解决）撞号——编号复用的历史遗留（D23 同例，其活跃行 2026-10-10 已按 [D23②] 归档），本行是另一条 issue，勿混**」。全镜头 VLM 打标取代漏斗粗滤设计；漏斗复核子题（D1）范围收窄到台词检索段 **2026-10-24**：本行要设计的「Top-K 候选轻量视觉意象过滤」已由 ADR-0015 的全镜头 VLM 打标取代，v2 captions 通道在用（supercell 池 SP 全量打标、各卷排片）。剩下的漏斗复核子题在 D1（暂缓中）

## 2026-10-20：C 类推进、无需再手验（2 条）

### [D120] 制片 agent 对数据目录的环境认知有缺口：不知道企划卷比普通期多一层目录、不知道数据目录不在 git 里，导致相对路径与 git 命令失败
- 状态：**已解决（2026-10-20 施工，`277a902`；措辞人审过，按草案加）**
- 关联（原表）：`config/agent/scopes/production.md`（未写明路径约定）、bash 工具环境（已提供 `$AVA_ROOT`）；D81（企划 / 卷目录结构）、D118
- 原记录（活跃表原文）：状态栏「**待修**（2026-10-19 登记；改规程属 D101 人审范围）」。2026-10-19 supercell 卷二：`cat ../../config/voice.json` 失败（卷目录在 `episodes/<企划>/<卷>/`，`../../` 落到 `episodes/`），随后改用 `$AVA_ROOT` 成功；`git diff HEAD 02-script.md` 报 `Could not access 'HEAD'`（`data/` 是外置盘软链、不进 git）。候选改法：production 规程补两句：仓库内文件一律用 `$AVA_ROOT/…`，不用相对路径回溯；期目录不在 git 里，比对版本用 `_agent/script-history/` 留底与 `diff`。措辞按 D101 中性规则语气，由人过目后再提交 **2026-10-20 施工**：`production.md` 第一节末尾加两句（卷目录深一层、仓库文件一律 `"$AVA_ROOT/…"`；`data/` 不在 git 里，比对版本用 `_agent/script-history/` 留底配 `diff`）；`test_agent_context_claims` 断言常驻层含这两句

### [D123] gate「短边 ≥1600」对宽幅截图测的不是真实产物：这条判据针对扫图放大发糊，而宽幅截图转 1920×1080 时整体缩小、不会糊；裁到证据区域后短边跌破 1600 被拒，反而让人保留整视口，把广告留在画面里
- 状态：**已解决（2026-10-20 施工、机检通过，`405690c`；人选「峰值放大 ≤1.0 才 PASS」）**
- 关联（原表）：`pipeline/acquire.py`（`SCAN_MIN_SIDE` 与图片两判据）；AGENTS 三·判据 1 与 3、D122
- 原记录（活跃表原文）：状态栏「**待人定**（2026-10-20 登记）」。2026-10-19 supercell 卷二截图都卡在 3328×1646 过线。候选方向：按转码后的实际缩放倍数判（放大倍数 >1 才算糊），不调阈值 **2026-10-20 施工**：`check_image` 的「短边 ≥1600」换成「峰值放大」：`still_peak_magnify` 与 `still_argv` 同一套几何（铺满取盖住画面的那一边 / 安全框取放得进的那一边，再 × 缓推 1.15），>1.0 判 FAIL 并给出至少要多大的原图；`candidate_check.gate_reject` 用同一个算式。新判据在任何宽高比上都不比旧的严：16:9 横图需约 2208×1242（旧规则要 2844×1600），竖图、宽幅图缩进安全框，要求低得多；1920×1080 截图峰值 1.15 倍仍判 FAIL（旧规则同样不过）。旧用例里 Back C 1582×2860（0.36 倍）、封面 2041×1357（0.76 倍）改判 PASS。两份 skill 的「短边 <1600」同步。变异 4/4（补了一条偏离 16:9 的铺满边界用例才杀掉「铺满用 min」）

## 2026-10-20：集中处理修复、无需再手验（3 条）

### [D114] 全量测试 2 条红（约自 10-16 起）：`test_agent_assembly_integration.py` 的 `test_step_switch_incremental_injection_and_deduplication`、`test_idea_scope_zero_step_injection`，消息条数比断言多 1
- 状态：**已解决（2026-10-20 修复、全量 3479 条通过；改的是测试隔离，无需人手验）**
- 关联（原表）：`tests/test_agent_assembly_integration.py`（两条测试没隔离仓库根）；Spec 7（跨期记忆）、Spec 1
- 原记录（活跃表原文）：状态栏「**待修**（2026-10-17 登记，coding agent）」。多出来的是跨期记忆注入消息「[跨期记忆 · 经验参考，非规则]…id: M001」：`assemble_resident_prompt("creative"/"idea")` 与 `cli._dispatch_agent_turn(...)` 都没传 `root`，落到真实仓库，读到 `data/library/memory.md`（10-16 起有 M001 条目「状语『地』读成 dì」）。同文件首条测试传了 `root=tmp_path`，所以不红。不是产品缺陷：记忆按设计在有条目时注入。测试依赖真实 `data/` 内容，记忆库一有条目就红、清空又绿，也会把真实记忆写进测试断言的上下文。10-17 实查：撤掉当日改动后 `test_step_switch…` 照样红；手工复现 idea scope 一轮，多出的正是这条记忆注入。**修法候选**：两条测试的 `assemble_resident_prompt` 与 `_dispatch_agent_turn` 都传 `root=tmp_path`（与首条同法），只改测试。 **2026-10-20 施工**：原「修法候选」（两处都传 `root=tmp_path`）不成立——`_dispatch_agent_turn` 拿到 tmp 根后也去那里找 `config/agent/tools.json`，直接崩；首条测试绿只是因为 pipeline scope 本来就不注入跨期记忆。改为文件级 autouse fixture：只把 `memory.render_injection` 的全局库指到 tmp 下的空 `data/library/`（企划记忆走 `store=` 的不动），配置与工序文档仍读真实仓库。去掉 fixture 即回到 2 红

### [D119] bash 硬危险规则「停机点解封物」只按路径字面匹配，不分读写：`ls -la 02-diff.patch` 这类只读命令也弹卡，与 ADR-0030「只拦不可逆」不一致
- 状态：**已解决（2026-10-20 修复、机检通过，变异 4/4；无界面行为需人看）**
- 关联（原表）：`pipeline/agent/status_card.py:278`（`02-diff\.patch\|04-clips\.approved\.json\|07-cover/selected` 任一出现即打标）；ADR-0030、D82
- 原记录（活跃表原文）：状态栏「**待修**（2026-10-19 登记）」。2026-10-19 supercell 卷二：agent 查 `ls -la 02-diff.patch 2>/dev/null \|\| echo "no patch"` 被弹卡，人拒绝。实测 `bash_danger_tags('ls -la 02-diff.patch')` 返回 `['[停机点解封物] 批准只能由人做']`。候选改法：照 `_FREEZE_PATH` + `_WRITE_FORM` 的组合口径，路径命中**且**为写入形式（重定向、cp / mv / rm / tee / touch / ln、`python -c` 写文件等）才打标；只读的 ls / cat / diff / stat 放行。改前补变异测试：写形式必须仍弹卡 **2026-10-20 施工**：解封物规则从 `_BASH_DANGER_RULES` 挪出，改成「路径命中且为写入形式」才打标；写入形式 = Code Freeze 的 `_WRITE_FORM` + 解释器（python / node / perl / bash / sh / xargs …）+ `curl -o` / `wget -O` + `tar` / `unzip` + `git checkout / restore / mv`——原规则字面命中即弹卡，顺带挡住了 `python -c "open('02-diff.patch','w')"`，放开只读不能把这类一起放掉。bash 工具描述「碰停机点解封物」改「写」。用例：硬危险 +7（含两种 python 写法）、免卡 +5（含 10-19 原命令）

### [D125] `web_fetch` 遇到含非 ASCII 字符的 URL 报 `UnicodeEncodeError: 'ascii' codec can't encode characters`，`crawl` 能抓同一 URL
- 状态：**已解决（2026-10-20 修复、机检通过，变异 4/4；无界面行为需人看）**
- 关联（原表）：`pipeline/agent/web.py::fetch_web`；—
- 原记录（活跃表原文）：状态栏「**待修**（2026-10-20 登记）」。复现：`https://commons.wikimedia.org/wiki/File:停電による閉鎖_(5531488699).jpg`。修时注意：留档 `url` 若改存百分号编码形态，`fetch_log.norm_url` 不解码，会与候选 `found_on` 的原文形态对不上 **2026-10-20 施工**：`fetch_web` 发请求前把 URL 转成 ASCII 形态（路径 / 查询非 ASCII 字符百分号编码、已有 %XX 不重复编码、非 ASCII 主机名走 IDNA），只用于请求；返回的 `url` 存原文，没重定向时 `final_url` 也回原文——留档 key 按 `norm_url(final_url)` 算，存编码形态会与 crawl 抓同一页分成两条、也对不上候选 `found_on`。替身 opener 照 http.client 的 `_encode_request` 对 selector 做 ASCII 编码。变异 4/4（请求不编码、final_url 不回原文、重复编码 %、主机不走 IDNA）

## 2026-10-20：集中清账归档（7 条；人确认无待人项 3 条 + 真实期已用过 4 条）

### [N59] 补料池记的是外置盘**绝对路径**，与「路径一律相对仓库根」相抵触：`pool.json` 里 `"path": "/Volumes/Samsung T7/anime-video-data/episodes/<期>/patch_assets/xxx.mp4"`，而同仓 `sources.json` 一律 `data/library/raw/...`
- 状态：**已解决（2026-10-18 施工、机检通过；2026-10-20 人确认归档）**
- 关联（原表）：`pipeline/ingest_patch.py:445,468`（`str(asset.resolve())`）、`pipeline/ingest_patch.py:254`（写入 `sources[(pool, key)]`）、`AGENTS.md` 五「路径一律相对仓库根」；D84, D59, N31
- 原记录（活跃表原文）：状态栏「**已修·机检通过**（2026-10-18，人：「建议快速修复」）」。2026-10-17 读码发现（起因：排查「生成视频当素材」的接入口时顺带看到）。**影响面**：盘改名、换挂载点、换机器（协作者 Windows 侧）后 `render` 按 pool 路径取文件直接取不到，且报错点在 06 渲染、离成因很远；`sources.json` 同一批资产写相对路径，两套口径并存说明不是有意例外。**最小修法**：写入改 `paths` 相对仓库根（与 `sources.json` 同口径），读取侧先认相对、不中再回退绝对（存量期不清洗，避免动到 22 期已封板产物）；测试 +1：造一个含绝对路径的 pool，断言渲染取路径走得到相对化分支。**未修理由**：属于开发态改动，且不在当前任何一期施工范围；触发条件——① 出现一次「盘已挂但 pool 路径取不到」的实跑失败，② 或开始跨机器跑同一期。届时随一次既有施工附带修，不单独开工。 **2026-10-18 复现**：束胸期副本（`2026-10-13-motionab-…`，动效 A/B）新建补丁池，`pool.json` 照旧写成 `/Volumes/Samsung T7/…` 绝对路径；同时看到另一种登记表与文件不一致：束胸期原池 SP01 登记 `size` 984952、`mtime` 16:41，盘上文件已是 90802 字节、20:45（登记后被重转），`ingest_patch.py:230` 只打 WARN 不拦，成片在 20:47 渲染、用的是重转后的文件。 **2026-10-18 施工**：`bgm._rel_repo_path` 挪进 `paths.repo_rel`（按 `data` 符号链接折回 `data/…`，bgm 与补料池共用一份）；`ingest_patch` 登记 `path` 与镜头表 `meta.source` 改存相对路径；读取侧（漂移检测、`pending_assets` 去重）按 `paths.ROOT / path` 拼，存量绝对路径拼了不变，22 期已封板产物不清洗。下游 clips / render / review 拿到的 `source` 与番剧片源同为 `data/…` 相对串，按仓库根 cwd 读。用例 +1（data 符号链接到仓库外 + cwd 不在仓库根）；变异：登记写绝对、镜头表写绝对、读取按 cwd 三条均被杀；`pending_assets` 按路径去重那行未单独杀到（同名兜底掩盖）。副本期实测：重建池后 `pool.json` 为 `data/episodes/…`，04 排片与旧池逐段一致。**已知副作用**：已批准的期若用新代码重建补丁池，`source` 由绝对变相对，`clips` 的产物变化守卫会拦（要 `--force`）；重建补丁池本就少见，守卫不为此放宽。

### [D121] `acquire register --to-patch` 不认企划下的卷：期目录判定仍按 D81 之前的平铺结构，`data/episodes/<企划>/<卷>` 一律报「不是 data/episodes/ 下的期目录」
- 状态：**已解决（2026-10-20 修复、机检通过，`a2db435`；2026-10-20 人确认归档）**
- 关联（原表）：`pipeline/acquire.py::cmd_to_patch`；D81、D120
- 原记录（活跃表原文）：状态栏「**已修·机检通过**（2026-10-20，人批准改代码；分支 `fix/to-patch-volume`，`a2db435`）」。2026-10-19 supercell 卷二夜间补素材（pi）被拒后，agent 照内部逻辑手工挪了 8 个文件。修法：改用 `project.classify_dir`，认 `episode` / `volume`，企划目录本身仍拒。用例 `test_ao5_企划下的卷可挪入_企划目录本身拒`，变异 2/2 被杀。手工挪的 8 条台账字段与 CLI 写出的一致，未回补

### [D83] `run_pipeline` 的结果出网前只过 `scrub_restricted`、不过 `scrub_secrets`：任何模块把密钥值打进 stdout / stderr，会原样进会话日志并发给模型服务商（bash 的输出是脱敏过的）
- 状态：**已解决（2026-10-12 施工，2026-10-15 补 `execute_job` 源头脱敏，机检通过；2026-10-20 人确认归档。残留见原记录末句）**
- 关联（原表）：`pipeline/agent/tools.py::_tool_run_pipeline`（返回 `"stdout_tail": executed_job.stdout_tail,`，对照 `_tool_bash` 的 `"stdout_tail": scrub_secrets(done.stdout_tail, ctx.root),`）、`pipeline/agent/llm.py::_tool_message`；D82（[spec](../plans/2026-10-12-bug-ticket-spec.md) 红队 R-9 上报）、ADR-0029 决策 5、D52
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-12，`e0ca1fb`，分支 `d81-d82-project-layer`，已合 main；变异 3/3）。观察项（`execute_job` 把子进程输出实时回显到宿主 log 帧与 stderr，不脱敏；作业结束事件落盘 `events.jsonl` 的尾部同样未脱）**2026-10-15 已修**：`execute_job` 源头脱敏，回显换掉密钥值再写，只扣住末尾恰好是某个密钥开头的那一截（防 4 KB 读块把密钥切成两半），其余即时写出——第一版按行攒，扣住了无换行的 `[y/N]:` 提示，被 🔴-2 回归用例抓到，当晚改掉（`669cfd1` 之后一个 commit）；无已知密钥时直通、时序不变；尾部与结束事件同口径。测试 +9、协议用例补查 log 帧 / stderr / events.jsonl，变异 10/10。残留：尾部截断处若正好切在密钥中间，留下的后半截不认」。修法：`_tool_run_pipeline` 返回前对 `stdout_tail` / `stderr_tail` / `message` / `error` 过 `scrub_secrets(…, ctx.root)`，与 bash 对齐。测试：临时 `config/*.local.json` 放一个密钥值，让替身模块把它打进输出，断言工具结果、会话日志、假端点收到的请求里都只有 `[已脱敏]`；变异：去掉这次脱敏 → 测试必须变红。不并进 D81 / D82：与企划、工单无关，单独回退与追溯更清楚

### [D91] gemini-3.8-flash agentic 能力差、废话奉承多；换模型只能手改 `agent.local.json` 且全局生效，界面上看不到当前用的是哪个模型
- 状态：**已解决·真实期使用验证（10-14 至 10-17 多次落盘生效；2026-10-20 人确认归档）**
- 关联（原表）：`pipeline/agent/model_switch.py`（会话级设置、按 sid 落 `_agent/session-models.json`、`/model` `/thinking` 解析）、`pipeline/agent/{llm,session,protocol,cli,compact,session_titles}.py`、`desktop/src/{shared/convFrames,shared/convFold,host/sessions,host/service,renderer/SessionHeader,renderer/Composer,renderer/App,renderer/icons}`、`desktop/e2e/modelSwitch.spec.ts`；D62, D80, ADR-0031
- 原记录（活跃表原文）：状态栏「**已施工·真机通过**。会话级模型切换与推理深度已在 10-14~10-17 多次落盘生效；待人：重新打包后在真实窄栏里看一次输入框工具栏」。人提出；人定：只对当前会话、照实列出代理全部模型、界面照 ZCode（输入框下常驻模型 / 推理按钮）。实测（本机 CLIProxyAPI）：请求体 `reasoning_effort` 对 gemini / kimi 都生效，模型名里的 `-high` 只是默认值。回合、压缩摘要器、起标题、读数分母四处都用本会话设置。图标：ZCode 的大脑在 16px / 1.5 描边下认不出，用仪表盘（人定 2026-10-14）。窗口表补登 kimi-k3 / kimi-k3-256k / kimi-k2.8 / gemini-3.5-flash-lite（2026-10-14 实测，见 `compact.json` 各条 `_source`）；代理列出的 `gemini-4-argon-high` 上游回 404，不可用。**2026-10-14 人反馈两处修**：① 长会话名在窄栏把两个按钮挤没——会话名先截断（flex-shrink 1000，全名在悬停提示），再截模型名，推理与发送按钮不缩；② 对话框发裸 `/model` `/thinking` 只打印列表、无法点选——桌面端改为展开对应下拉（新增纯读 `conv.models` 取列表；切换仍只走 `conv.command` 的点击纪律 TG-10）；会话没起时照旧发给 core 打印列表。e2e 加长会话名 + 窄窗与裸命令两段，变异 3/3（旧 CSS、去掉拦截、host 路由改错）

### [D112] 全期素材都来自企划池（SP 锚点）、一部有字幕索引的番都没有时，clips 起步就失败
- 状态：**已解决·真实期跑通（2026-10-17 多次 clips rc=0；2026-10-20 人确认归档）**
- 关联（原表）：`pipeline/clips.py::run`（`load_all(index_dir, animes)`，约 876 行）；ADR-0010、D111
- 原记录（活跃表原文）：状态栏「**已修·机检通过**（2026-10-17 登记）。代码逻辑已实跑（10-17 多次 clips 均正常打出「全部段落都是锚点，不加载字幕索引」且 rc=0）；产物主番字段因 01-topic 临时改动残留「罪恶王冠」，待下期纯净全锚点期最终确认」。supercell 卷一 10-17 跑 clips 报「`data/library/index` 下没有《supercell》的索引，先跑 build」。`01-topic.md` 只有 `番: supercell`、没有 `素材番剧:`，`bgm.animes_of` 退回番名，clips 无条件对它加载字幕索引，而 supercell 素材全是 SP 特典，从来没有字幕索引。EGOIST 没撞上，因为它的素材番剧有罪恶王冠 / PSYCHO-PASS。属引擎没覆盖的形态，不是输入错。**当期绕法（AVA 自行做的）**：`python -c` 调 `clips.main()` 带 `--anime 罪恶王冠`，这是真跑，08:46 覆盖了 `04-clips.json` 并发了第 05 站审批。复核：96 个片段全是 supercell、0 个罪恶王冠，各段 ok / ok_extended，内容可用；但文件头 `anime` 写成罪恶王冠、`animes` 多出罪恶王冠，下游会多加载一份无关片源，若有段落退回检索会搜到罪恶王冠台词镜头。照常重跑（不带 `--anime`）仍会同样失败。**修法**：全部段落都是锚点时不调 `load_all`（`vecs/units` 置空），stderr 打印「全部段落都是锚点，不加载字幕索引」；有一段要检索就照旧加载、照旧报缺。条件只看「是否全锚点」、不看索引在不在：全锚点期索引本来用不上（锚点不检索，`_rescue_starved` 只救检索段）。影响：别的番的全锚点期也不再加载索引，索引的模型版本校验在这类期不再触发。**验证**：测试 +2（空索引目录、不替换 `load_all`：全锚点期排出、有检索段照旧报缺），变异 2/2。

### [D99] 事实文件没有任何关卡：AVA 把没打开过的出处标「一手」、凭记忆补事实、改写动机，还汇报「核心事实均已落实一手文献支撑」，没有东西能反驳它。这类错只靠人或另一个 agent 一条不落地重核才发现，代价极高
- 状态：**已解决·真实期使用（`fact_add` 在 supercell 卷一用了 16 次；2026-10-20 人确认归档。`fact_review` 还没在真实模型上跑过，出问题另立新号）**
- 关联（原表）：企划 / 卷目录的事实文件（如 `01-席卷平成的同人雷暴/01-facts-2007-2009.md`，AVA 经 bash heredoc 写入）；`pipeline/agent/tools.py`（web_fetch / crawl 结果不留可引用的抓取记录）；02.8 `adversarial` 只核字幕与番剧笔记、管不到网页出处；D48, ADR-0021
- 原记录（活跃表原文）：状态栏「**PR-1/2/3 已施工·机检通过**（2026-10-15）。`fact_add` 已在 supercell 卷一深度使用 16 次且回执严格校验；`fact_review` 待真实模型跑一次」。事件：supercell 卷一事实文件，人工抽查 7 条即核出 4 条硬错——11 人名单漏 119 混入后加入的宇佐义大（官网 BIOGRAPHY）、スガ动机被改写成「躲编辑」（三轮 2017 采访）、BRS TV 动画监督误作山本宽（实为吉冈忍）、「首张进 Oricon 前五的初音专辑」（livetune《Re:Package》2008-09 已周榜第 5）；另有 56,236 张、在榜 66 周、五连百万等无出处却标一手（会话日志：全程只 web_fetch 12 页）。错误分四类：① 没读原文标一手 ② 凭记忆补 ③ 名单 / 数字与原文不符 ④ 动机因果被改写——前三类不需要「读懂」就能挡。**方向（人定：靠系统设计在源头避免，而不只事后核对）**：事实条目结构化（说法 / URL / 原文摘录 / 出处级别），写入时由系统校验 URL 在本会话确实抓取过、摘录确实出现在抓到的原文里，过不了的写不进或强制标「未核实」，「一手」不由模型自填；抓取结果留可引用的记录供日后复核；剩下第 ④ 类交给看不到写稿对话的独立核对（判据 11：带随机抽样通过清单与终审裁决）。摘录比对的归一化（日文标点、空白、换行、抓取文本差异）须先拿这份事实文件实测，不许凭想象定规则。**2026-10-15 实测**（AVA 自己的 web_fetch / crawl 重抓 16 个出处，65 条摘录逐级比对）：L0 原样命中 54–55 条；其余需要去空白、NFKC、去 markdown 标记、引号括号归一（6 条把原文「」抄成『』）才命中；2–3 条只在丢掉全部标点后命中（加了说话人冒号、删了脚注号），定为「待核」不判通过（判据 5：问号就在被丢的标点里）。反向：第二版的 3 条编造、1 条问答拼接、仿写的动机改写全部拦下。**按 URL 归属比对第一次就抓到真错**：第三版里「知り合いの中で…」挂在采访第 1 页，实际只在第 2 页。另：未核实清单里 ORICON 2008「機械に歌わせるからこそ…」其实在原文里。方案：抓取全文留档 + 新工具 `fact_add`（人 2026-10-15 允许加工具，且工具表改软约束 ADR-0032）+ bash 写事实库弹卡 + 02.8 接独立核对

### [D66] 回收站只进不出：客户端删除会话只是移到 `_agent/session-trash/`，没有查看回收站、选择性彻底清空的入口，回收站无限胀大，人也确认不了「删掉的到底是什么」
- 状态：**已解决（2026-10-10 施工、三端验证全绿，回收站摆放人选 A；2026-10-20 人确认归档）**
- 关联（原表）：`pipeline/agent/session_log.py::move_session_to_trash`（只写不读）、`pipeline/agent/cli.py`（无列出/清空回收站命令）、`desktop/src/renderer/SessionList.tsx`；D45, D58（删除语义=移回收站）, Spec 10
- 原记录（活跃表原文）：状态栏「**已施工·三端验证全绿**（2026-10-10）；待人：① 打包版真实任务手验（UI 规则 2）② ~~回收站摆放二选一~~（2026-10-10 人选 A=侧栏列表底部，即当前实现）」。人原话：「能不能在客户端提供查看并且选择性的清空回收站一些对话的功能」。可行性已核（2026-10-10 读码）：回收站一段一文件（`<sid>.jsonl`，同名加时间戳后缀），选择性清空=删文件不动主日志；`session_log.list_sessions` 对任何 JSONL 都能出摘要，不用新解析；UI 复用 D45/D58 侧栏列表形态。待裁决：① 要不要「恢复回列表」（恢复=回插主日志，seq/租约/同 sid 冲突要处理，代价远大于清空）；② 彻底清空的确认形态（这是不可逆删除，现行删除有确认框，彻底清空至少同级）；③ agent 是否有触发清空的通路（建议无：删除与彻底删除都保持「仅人手动」，不进 LLM 工具表——现行 `/delete-session` 就不进工具表，cli.py 有原注）。**人裁决（2026-10-10，三项全按推荐）**：① 恢复不做（真误删手动把文件搬回去）；② 逐段确认：复用宿主 deps.confirm → 主进程原生模态框（与删除会话同一落位，渲染层不新造组件），文案写明不可恢复；③ 仅人手动，agent 无通路。**红队复审一轮（2026-10-10，10 条全采纳进 spec 修订版）**：发现 1（阻断）：不持锁的 purge 可插进 move_session_to_trash 两次原子写之间，段从回收站与主日志同时静默消失、双向报成功，且证伪裁决①的「手动搬回」前提——修：`_agent/session-trash.lock` 共用 fcntl 锁（禁用 EpisodeLease.acquire，它 O_CREAT 会创建主日志），purge 锁被占退出码 3 → 宿主 E_SESSION_LOCKED；发现 2/3/4（高）：确认框复用点改宿主 deps.confirm（渲染层无此组件）；LIST_TRASH 模板补 stdoutMax（无则清单超 8KB 时 JSON.parse 炸 E_CORE）；parse_error 判据写明（解析器永不抛错：零字节→empty、>0 且无段→parse_error、有残行→partial）；发现 5–10（低）：unlink 不跟随软链、主校验是 basename；同 sid 多文件按 file 聚合；侧栏收起无入口写明为接受；自动清理补触发条件（回收站 >1GB 或 >200 段、或 /list-trash 输出超 SESSIONS_STDOUT_MAX_BYTES 时另立项）；TG-14 字面量提醒。**二轮核销（2026-10-10）**：10 条处置逐条字面落地；二轮残留 R1（partial 判据漏中间坏行）已修进 §三/§七，R2（反例清单落点）以 spec 附录 A 了结。红队确认可施工。 **2026-10-10 施工**：core `/list-trash` + `/purge-trash --file=`（共用 `session-trash.lock` fcntl 锁，move 两次写与 purge unlink 互斥；basename 主校验 + 双侧 resolve 纵深）；桌面四模板 + `conv.trashList`/`conv.purgeTrash`（确认框在 spawn 前、退 3 → E_SESSION_LOCKED）+ 侧栏可折叠「回收站（N）」分组。pytest 2537 / vitest 504 / playwright 126 全绿；变异 6 条全被杀；TG-14 守卫绿。两项人勾待办见状态列。

## 2026-10-17：真实生产期充分验证归档（34 条）

### [D44] 改一个段落 ava 原地打转十余轮、人点二十多次批准：审批只按工具不按参数（只读的 check_script 也弹卡），没有防打转刹车，且模型查不了字幕、剧情时间码靠编
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/session.py::review_tool_call`、`pipeline/agent/tools.py`（search_notes / write_episode_file）、`config/agent/scopes/creative.md``；[plans/2026-10-08-approval-tiering-subs-search-spec.md](../plans/2026-10-08-approval-tiering-subs-search-spec.md), Spec 9 红线 5 / H-4, ADR-0025
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-08），待真实窗口手验」。事件：董香人物志（二）段落 3。模型为把「句长起伏 CV」0.49 提到 0.55 整篇重写，往别段塞「死斗爆发。」这类短句凑分，还把所有「画面：」改成半角冒号；它给的「S01E09 依子送便当」源自 `01-topic.md:55` 选题表，字幕实查 E09 无依子（便当在 S01E04 03:52，探病在 S01E05 02:53），段落 1、2 的锚点 03:20、10:22 也对不上。已施工：check_script/status 免卡；草稿每轮前 3 次免卡、第 4 次起弹卡；覆盖前留底 20 份；search_notes 加 source=subs。手验清单见 spec 末节。**本期 01-topic.md 第 55 行与 02-script.md 段落 1–3 的锚点仍待人改。**

### [D47] 02.5 阶段 agent 改不了 02-script.md：人让它改段落，它只能改草稿再让人手工同步；且草稿在 02-script.md 建好后还被改，02-diff.patch 的基线被挪动
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/tools.py`（白名单、`script_write_refusal`、`_keep_history`）、`pipeline/agent/status_card.py`（写稿卡 diff）、`session.py::review_tool_call`、`desktop/src/renderer/HumanCards.tsx``；[plans/2026-10-08-agent-edits-final-script-spec.md](../plans/2026-10-08-agent-edits-final-script-spec.md), ADR-0024 决策 1（修订）
- 原记录（活跃表原文）：状态栏「**已施工·机检与 e2e 通过**（2026-10-08），待真实一期手验」。人原话：「应该给 agent tool 去修改 02，因为有的时候改动挺大的」「草稿变成 02 之后，一般可以让人和 agent 直接修改 02」。施工：02-script.md 进白名单，只能改不能新建；它存在后草稿冻结；每次写弹卡带 diff；写前全量留底 `_agent/script-history/`。连带修了桌面端请求卡换行被吞、参数表铺开整篇原文。

### [D48] 02.5 改稿回合又慢又绕：免卡写草稿后同一条 check_script 被判重复（D44 回归）；字数顶格时模型自己删字、改掉人认可的句子；每处修改先贴建议再问；每次整篇重写；没封板时点「批准」报错；人物志写成经历并排、缺因果
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/llm.py`（判重）、`tools.py`（`edits`、`resolve_write_content`）、`cli.py::_seal_before_approve`、`config/agent/scopes/creative.md`、`skills/write-script/SKILL.md` 4.1`；[plans/2026-10-08-edit-turn-efficiency-spec.md](../plans/2026-10-08-edit-turn-efficiency-spec.md), D44, D47, ADR-0024
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-08），待真实一期手验；① 已施工（2026-10-09）；② 02.8 已施工（2026-10-09，[spec](../plans/2026-10-09-adversarial-review-spec.md)）；③ 人裁决关闭」。人原话：「你没发现我让改的全部是因果联系不畅吗」。**待人定**：① 回合脚注标「本轮未查字幕」（段落 4、8 没查却写「核对」）；② `SKILL.md`「02.8 零上下文对抗审查」在 ava 里从未执行（判据 6：执行或删除），它最能抓「不知道为什么」；③ 董香二期 11 集塞 5–6 分钟，放宽时长还是收窄取材在 `01-topic.md` 定。。**2026-10-09**：① 回合脚注照实列出本回合查证调用「查证 字幕 N · 在场 N · 笔记 N · 网页 N」（core 按实际执行分类计数，0 也显示，[spec](../plans/2026-10-09-presence-and-lookups-spec.md)）；② 人同意恢复 02.8，先写 spec（pipeline 侧首个 LLM 调用方）；③ 人裁决关闭：二期已成片，时长本就按期在 `01-topic.md` 覆盖。。**2026-10-09 ② 施工（人裁决：02 机检过后审草稿、不拦、同意出网 +1；重点 事实 / 衔接 / 语言）**：新命令 `run_pipeline adversarial`（弹卡，补期目录）——一次 LLM 调用、不给工具，只喂稿件、番剧笔记与逐段锚点窗口内的字幕和在场索引；输出按三类分，事实类须带出处并机械自校（字幕时间码、在场镜头、笔记摘句、原文逐字都必须在喂给它的证据里，核不上标 ⚠），衔接 / 语言只诊断不改写、终审标「人定」；`write_episode_file` 白名单加 `02-adversarial.md` 供填终审；状态卡只提示不拦。董香二期只读预取实测：22 段、字幕 221 行、在场 72 行、输入 3.6 万字，段 21 窗口里字幕两句与「只认出董香」都在。

### [D49] 桌面端预览区与 02.5 编辑器难用：① 02.5 停机时点右上「预览」打开的是三栏编辑器（源码 / 渲染 / 人审要点），塞进约四成宽的预览栏，每栏两百多像素，无法阅读；② 编辑器不跟随磁盘：没有未保存改动时也不自动重载，agent 改了稿预览不变，只有点「保存」才提示「磁盘版本已变」；③ 人不知道能在 ava 里直接改文案：编辑器只在 02.5 停机时经「预览」出现，无入口说明；④ 侧栏收起（做视频时的常态）就没有任何地方选文件预览，展开后文件树也压在侧栏最底；⑤ 生成中的目录不能边生成边看（配音中想先听已出的段）
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``desktop/src/renderer/App.tsx`（预览区分发）、`ScriptEditor.tsx`、`PreviewPane.tsx`、侧栏文件树`；D39, Spec 11, Spec 14
- 原记录（活跃表原文）：状态栏「**人选 A，已施工**（2026-10-08，[施工 spec](../plans/2026-10-08-preview-voice-build-spec.md)；S2–S5），待真实窗口手验」。人原话：「第一张截图预览这个页面太逆天了」「稿改了预览居然不变」「我不知道怎么任意选择产物去预览……真正做视频的时候是会收起侧栏的」「侧栏选中目录的产物必须翻到侧栏最下面去找」「不能预览正在生成的目录……出一个就能听一个」。方向（待原型截图给人选）：预览栏标题做文件切换器；编辑器窄时源码 / 渲染切换、宽时并排，`02-script.md` 预览上放「编辑」入口；编辑器与预览跟随磁盘；生成中目录实时刷新。

### [D50] 03.5 顺听与纠错难用：① 不能在对话里顺听，必须去侧栏 / 预览区选；② 预览区同时出现顺听面板与旧的音频队列（`seg-XX.wav` 列表 + 播放器），两套播放器重复、混乱；③ 纠错输入是面板底部一个全局自由文本框，被段落列表挤出视野；人要的是「逐段指出哪些字读错」，增量重配只配有错的段；④ 03.5 卡片写「结构化打点须在终端 `python -m pipeline.tts … --review` 完成」，又是只能在终端做的步骤（与 D48 修掉的 02.5 封板同类）
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``desktop/src/renderer/VoicePanel.tsx`、`App.tsx`（VoicePanel 与 PreviewPane 并存）、`HumanCards.tsx`（03.5 卡）、`pipeline/agent/cli.py`（`/voice-*`）`；Spec 11 §2.3, D49
- 原记录（活跃表原文）：状态栏「**人选 A，S1–S7 已施工**（2026-10-08，[施工 spec](../plans/2026-10-08-preview-voice-build-spec.md) 文末）；S7 的已知问题 N61 已修（打点后一次批准通过），待真实窗口手验。① 「在对话里顺听」A 方案不覆盖」。人原话：「无法在对话内部顺听，必须去外面侧栏选中；预览音频页面非常乱；正常情况下是指出每一段有哪些字读错了，现在的页面让人不知道该怎么指出配音错误」。董香二期 `03-audio/` 至今没有 `corrections.json`：这次一条纠错都没录进去。。**人裁决（2026-10-09）**：① 「在对话里顺听」在真实窗口手验后关闭（03.5 就绪时预览区已自动打开顺听面板）；手验仍觉隔一层再加「▶ 去顺听」按钮。 **生产手验发现（2026-10-10 伪恋期 03.5）**：① 「听在面板、打点在卡」分工让第一动作（听）没有入口——卡上无播放，人要自行跳区；② R3「不抢人的预览」挡路：人手动预览过 02-script.md（上一工序产物）后，03.5 就绪不自动切顺听面板，配音进行中的逐段可听（D49-A S5）同理不可见。修法候选：手动预览内容是上一工序产物且新就绪停机点是下一工序时允许自动切换（接力不算抢）；或 03.5 卡内嵌「▶ 去顺听」直达按钮（D50 ① 留的口子）。待人定。③ **重配状态反馈三缺口（同日同会话）**：UI 不说在重配哪些段（「待重配」徽章与「进行中」行都依赖面板重载才出现）；动感只剩「进度见时间线」指向右下角角落按钮；**切走再切回 03-audio 白屏**（面板重挂载时 voice.info 撞重配中途的 manifest，疑似读到半截/失败）——疑似根因：重配期间 manifest 读写并发无保护。 **2026-10-11**：③ 中「切走再切回白屏」随 D73 加固、「不说在重配哪些段」随 D72 部分缓解（底部待重配段由盘上现算、配音进行中实时减少）；「动感只剩进度见时间线」归 D71；①②待人定照旧。

### [D51] agent 在配音与纠错上不会干活：① 让它配音，它不问本地还是云端，直接跑本地 `tts`；② 人报读错的字（绚都、肉体、少年），它让人进终端 `/voice` 录纠错、再跑 `--apply-patch`——agent 没有录纠错的工具（`voice.add` 只是人的命令），也不知道桌面端面板；③ 人要求「同音字替换」，它先改成同义词（身体、年轻人），再改成同音字，且两次都改在稿件里（还是冻结前的草稿）；人指出后才知道纠读音走的是 `03-audio/corrections.json`（拼音覆盖）而不是改稿；④ 以「模型没有听觉」为由不做试错，而人要的是：它录纠错、只重配错的段，人来听
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``config/agent/scopes/creative.md`、`docs/runbook/03.5-voice-check.md`、`pipeline/agent/tools.py`（无纠错工具）、`pipeline/corrections.py``；D50, D52, ADR-0025（工具数上限）
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-08），待真实一期手验」。人原话：「我都来 ava 来了，为什么要碰终端呢，应该你去试错，看什么情况可以读对，然后重配读错的段落」「我告诉你用同音字替换，你后面 2 个为什么都用的同义词替换」「同音字替换不是在文案里进行替换的」。待定：给 agent 一条经人审卡写 `corrections.json` 并只重配对应段的路径（新工具还是复用 `run_pipeline` 子命令，受工具数上限约束）；配音前问本地 / 云端（01-topic 已写则不问）。**已施工**（2026-10-08，[spec](../plans/2026-10-08-agent-voice-corrections-spec.md)；人裁决：复用 run_pipeline、全局层可经卡写、期级不加同音字）：`python -m pipeline.corrections add

### [D52] agent 跑本地配音成功后，整轮被出网护栏拦下并回滚：`tts` 的输出末行「写入 …/03-audio/manifest.json」命中受限出网词 `03-audio/manifest.json`，下一次请求模型时被拦（`stopped: blocked`），`turn_rollback`，模型看不到 25 分钟配音的结果；界面只显示「blocked」，看不到原因（N52 同类）
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/llm.py`（出网断言）、`pipeline/agent/tools.py::RESTRICTED_EGRESS_PATTERNS`、`run_pipeline` 结果的 `stdout_tail``；N52, ADR-0021
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-08），待真实一期手验」。证据：董香二期 `session.jsonl` 第 290 行 `turn_end{stopped:"blocked", duration_s:1524.382}`，前一条工具结果 2753 字符，唯一命中就是该路径。修法候选：工具结果进模型前对受限词脱敏（同状态卡的「[已脱敏]」做法），而不是拦整轮；被拦时发一条带命中模式名的 notice（N52）。**施工**（[spec](../plans/2026-10-08-tool-result-egress-scrub-spec.md)）：`llm.py::_tool_message` 对工具结果脱敏（`tools.py::scrub_restricted`，状态卡与 web 同用）；assistant 的工具参数与人的输入不脱敏（双保险保留）；协议回合被拦时发 `notice{code:"egress_blocked"}`，只含模式名。pytest 2149 / vitest 469 / e2e 119 通过。

### [D53] 03.5 纠错对话暴露的其余问题（D51 之外）：① **runbook 过期且只写终端**：`docs/runbook/03.5-voice-check.md` 写全局表是 `config/voice_readings.json`，该文件不存在（全局层实为 `config/voice.json` 的 readings / pinyin 注入，见 `pipeline/g2p.py`）；整篇只教 `/voice`、afplay、QuickTime，没有桌面端路径——agent 叫人进终端是照 runbook 念的（D51 ② 的根因）；② **格式错的命令过了预检查（bug）**：模型发 `tts --redo 2 4 5 8 9 10`（正确为逗号分隔 `--redo 2,4,5`），`run_pipeline` 预检查放行、人批准后 0.2 s 退出码 2（`unrecognized arguments: 5 8 9 10`），多出的段号还让期目录没被自动补上；③ **编造界面功能**：agent 称「桌面端左侧/底部的语音纠错面板一键填入读音」，界面上没有一键填入——它对桌面端界面一无所知；④ **同音字不校验**：给出的「肉惕」是 ròu tì 不是 ròu tǐ，仓库已有 pypinyin 可以机器校验；⑤ **没提出写在全局还是本期**：「绚都」贯穿董香系列四期，应进全局层一次生效，而不是每期各录；⑥ 回复里写 LaTeX（`$\rightarrow$`），对话区不渲染公式，原样显示
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``docs/runbook/03.5-voice-check.md`、`pipeline/agent/tools.py`（`run_pipeline` 预检查、`_extract_positional_args`）、`config/agent/scopes/*.md`、`pipeline/g2p.py``；D51, D50, D46
- 原记录（活跃表原文）：状态栏「①②③④⑤⑥ 均已施工（④⑤ 随 D51），待新会话实测；原登记（2026-10-08；人追问「这一段对话没有 issue 需要登记吗」后逐条复核）」。证据：董香二期 `session.jsonl` 第 309 行（`--redo` 退出码 2 的工具结果）。② 修法：预检查对 `--redo` 值与多余位置参数在弹卡前拒，并给出正确格式；④⑤ 归入 D51 的纠错路径设计（写入前用 pypinyin 校验替换字读音，并让人选全局 / 本期）。**② 已施工**（2026-10-08，[spec](../plans/2026-10-08-run-pipeline-argv-precheck-spec.md)）：弹卡前拒多余位置参数与 `--redo` 空格段号并给出逗号写法；顺带补齐 5 个漏登记的带值旗标并加 ast 审计。**①③⑥ 已施工**（2026-10-08）：重写 `docs/runbook/03.5-voice-check.md`（桌面端面板路径、三层读音表与放哪层、pypinyin 核对命令、给 agent 的边界）；`director.md` 回复写法加「不描述不知道的界面功能」「不写 LaTeX」；ADR-0019 加修订注。待新会话实测。

### [D59] 很多操作要人自己在终端跑（素材 gate / register、Phase 0 入库与重测、补料、清锁、远端改道），一点也不 agentic
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/tools.py`（`ASSET_COMMANDS` / `PIPELINE_MODULES` 白名单）、`pipeline/acquire.py`、`ingest_patch.py`、`tts.py`、`cloud.py`、`docs/runbook/`、`skills/acquire-assets/SKILL.md`、WORKFLOW / CHEATSHEET`；ADR-0021 §3、ADR-0025、ADR-0020
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-09），两项待人定已裁决（2026-10-09：标定值落 config → D60；ava 写番剧笔记 → D61），待真实一期手验」。人原话：「我发现很多需要“我在终端跑”的操作，这一点也不agentic」；人裁决：「所有需要“我在终端跑”的操作和对应文档都需要进行agentic化的修改」。成因：把「要人批准」与「要人亲手敲」混为一谈；ADR-0021 §3 写了 fetch/gate/register 走白名单，S6-R1 只接了 fetch。原则：只读免卡、写盘弹卡、判断留在卡上与停机点，不加工具。盘点与批次见 [spec](../plans/2026-10-09-agentic-ops-spec.md)。。**2026-10-09 施工（5 批）**：① 素材：`acquire gate`（免卡）/ `register`（自动取号、扫图自动转码、`--to-patch`、`--waive 理由`）/ `forget`；② Phase 0：ingest / shots / vindex / subindex / vprobe 子命令与 timeline 入白名单，只读的免卡；`shots gallery --patch`；`shots frames` 自清残留帧；③ 补料：`scout`、`ingest_patch` 入白名单并补期目录；补丁池图片自动转码；`ingest_patch --reset`（挪进 attic 不删）；④ 运维：`tts --clear-stale-lock`（判 pid）、`cloud relocate-data` / `clean-frames` / `fix-env`（取代 `cloud exec` 修法）；⑤ 文档：WORKFLOW / CHEATSHEET / README / runbook / director.md 改为 agent 发命令、人批卡。仍归人：05 / 09 批准与上传、片源放进 raw/、字体、装远端环境依赖。用例 tests/test_agentic_ops.py；变异 AO-MUT-1..12。

### [N52] 桌面端（协议路径）出网被拦时看不到原因：`turn_finished` 只有 `stopped:"blocked"`，命中串不下发
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/llm.py`（`run_tool_loop` 的 `PermissionError` 分支返回 `error`）、`pipeline/agent/session.py`（只在旧分支 `channel.show` 打 `[BLOCKED]`）、`pipeline/agent/protocol.py``；Spec 9 §2.3、D30
- 原记录（活跃表原文）：状态栏「**随 D52 施工**（2026-10-08），待真实窗口手验」。D30-A 实测：protocol 会话首轮被拦，输出帧只有 `turn_started` / `turn_finished{stopped:"blocked"}` / `stop_points`，无任何 `log`/`notice` 带原因；终端 `ava` 有 `cli.py` 的 `[BLOCKED] 出网被拦截：…`。修法候选：blocked 时发一条 `notice`（只含命中模式名，不含请求体），属 Spec 9 协议修订。

### [N53] 作业输出尾巴里若带受限路径，下一次 LLM 请求会被拦
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/tools.py::run_pipeline`（返回 stdout 尾部，未清洗）、`pipeline/tts.py`（`[OK] 打点已写入 {manifest_path}` 打印绝对路径）`；D30、Spec 16 §10 Q3
- 原记录（活跃表原文）：状态栏「**D52 实证并一并修**（2026-10-08，工具结果进会话前脱敏），待手验」。推断链：`run_pipeline` 返回值进会话 → 含 `…/03-audio/manifest.json` → 下一次 `chat_complete` 整串断言命中 → `[BLOCKED]`。Spec 16 的豁免只覆盖仓库规程，不覆盖这里。待实测确认哪些作业在 agent 可调范围内打印该路径，再定「作业侧不打印绝对路径」还是「工具返回值进会话前清洗」。

### [N61] 宿主 H5 自愈（产物指纹漂移 → 自动重钉停机对象）在真实窗口里不触发：03.5 卡内打点改写 `03-audio/manifest.json` 后 6 s 内没有 H5 的 HEAL，人第一次点批准撞 `E_STALE`、要点两次
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``desktop/src/host/service.ts::checkH5`、`desktop/src/host/heal.ts::h5Step``；D50, Spec 8 H5
- 原记录（活跃表原文）：状态栏「**已修·机检与 e2e 通过**（2026-10-08），待真实窗口手验」。证据：S7 e2e 打印的 spawn 记录只有激活时 `trigger:"H1-activated"` 的 HEAL，对象库仍为旧 pending；core 的自愈本身会处理指纹漂移（`pipeline/approvals.py` 条件 2，批准时确实转 superseded 并新建）。02.5 的 TE-1 能刷新多半走的是 H2（封板后工序前进），不能当作 H5 生效的证据。修好后把 `stopPoint.spec.ts` 的 S7 用例改回「打点后一次批准通过」。。**根因（2026-10-08 插桩实测）**：H5 本身正常，错在基线取样时机——基线是「设为活跃期后的第一次采样」，而采样随 1 s 轮询 tick 才发生；e2e 里打点在激活后 1 s 内就写完 manifest，漂移被吞进基线、按设计永不触发（等 3 s 再打点则 H5 正常触发、core 重钉新对象）。真实窗口里人动作慢于 1 s，多数时候不中，但切回活跃期后立刻操作同样会中。**修**：`service.ts::activate` 在 H1 heal 与首载之后立即 `checkH5` 取基线（Spec 8 H5 行加修订注）。用例：`tests/host/service.test.ts`「N61 H5 基线在激活时取」（撤掉修复即红）；S7 e2e 改为「打点 → H5 恰一次 → 新卡一次批准、不出 E_STALE」。

### [D65] 上下文 3–5 轮涨到 100k：大体积工具结果逐字永久驻留历史、每轮全量重发；建期迁移（Spec 18）把选题阶段的大结果整个背进期会话，期会话首轮即 68k
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/llm.py::_chat`（全量发送 `convo`）、`pipeline/agent/tools.py`（`MAX_READ_BYTES = 200_000`）、`pipeline/agent/cli.py::migrate_idea_session``；D62（手动 /compact 之外无泄压）、D41/D56/D64（读数已可信）、Spec 18、ADR-0022
- 原记录（活跃表原文）：状态栏「**已施工·机检全绿**（2026-10-10）；真实会话观察待下期」。**2026-10-10 实测定案**（pi 协助读码 + 真实日志）：① 读数无错——`compact.py::context_reading` 优先用服务商 `usage.prompt_tokens`，回落口径（字符÷4）只会低估；伪恋期日志 chars/token 实测 1.32–1.39，与 compact.py 标定注释一致。② 常驻层与工序路由（ADR-0022）正常——实测 creative 常驻层 11,432 字符 + 工具 schema 9,329 字符 + 默认工序 WORKFLOW.md，首轮静态 ≈20k token，不是病根。③ 病根在工具结果：全量 `session.jsonl` 统计 92 条 tool 消息，中位数 376 字符；>4000 字符的仅 5 条（5%）却占 67% 体量，几乎全是 `read_artifact`（总量 126k 字符、单条 max 63,698）。伪恋期 turn1 即 68,089 token，主因就是选题讨论里那条 63.7k 字符的笔记被逐字读入并随 `--from-idea` 迁移背进期会话。④ 建期换 system prompt 导致的 cache 前缀作废是一次性事件（`session_log.py::rebuild_messages` 丢弃旧 system、按当前文件现建），之后前缀单调只增，不是持续破——真正贵的是被迁移进来的大结果在期会话每轮重发。施工方向：请求时纯函数「工具结果老化」投影（不动日志），[spec](../plans/2026-10-10-tool-result-aging-spec.md)。**红队复审一轮（2026-10-10，四条成立已吸收进 spec 修订版；统计口径修正为 91 条/排除回滚）**：F1 wrapup 收尾指令会被误判为回合边界、把刚拿到的大结果掏空且 tool_choice=none 禁重读（修：`_WRAPUP_KEY` 内部标记，对齐 `_TIER_KEY`/`COMPACT_KEY` 先例）；F2 D62 压缩器的 choose_cut 与摘要器输入都按未老化字符计，老化落地后「原始体量远大于发送体量」成常态会顶穿尾部预算、撞摘要器窗口（修：两处换老化后口径，范围闸门收窄）；F3「日志已过 D52 脱敏」对 pre-D52 存量日志不成立（董香二期 seq=288 实证），安全论证改写为断言兜底 + fail-closed；F4 占位文案按工具分、run_pipeline 指向 read_status。待人定三项**人拍板（2026-10-10）全采纳**；F2 修法补出第三处 is_bloated（膨胀护栏按未老化尺方向反，连带 fail 文案按老化口径），放置定 session.py compact() 开头投影一次三处共用。 **2026-10-10 施工**：aging.py 新建（请求时纯函数投影，不动盘）；llm.py `_chat` 接投影 + `_WRAPUP_KEY`；session.py compact() 三处口径换老化后 + bloated 文案按老化口径。测试 14 条全绿、变异 9 条全被杀、全量 2519 绿；伪恋期真实日志回放 saved=98,092（≥90,000）。待人定三项维持原判。

### [D68] CUDA 云端配音通道不调 `generate_voice_clone` 时传 temperature/top_k，吃 qwen-tts 包默认（0.9/50，比本地 mlx 首试 0.8/30 更热、随机性更大）；两条引擎路径采样参数不对齐
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/tts.py::_synthesize_cuda` vs `_synthesize_mlx`（`Engine.SAMPLING` 阶梯只服务 mlx 分支）`；EGOIST V2 复盘（2026-10-10，manifest + git log 对时线）
- 原记录（活跃表原文）：状态栏「**已修·机检通过**（2026-10-11），待下次启用云端配音时实听验收」。V2 三期对照实证：01（09-12 云端，「同段统一种子」修复前）最不稳；02（09-13 云端，赶上部分修复）次之；03（09-13 mlx 本地，`seeds_used=[7,7,7,7]` 同段统一实证）明显好但仍不及董香二期——它是 synth_logic 5，当前是 6（差一轮 QC/重配迭代）。修法：CUDA 分支按 `SAMPLING[attempt-1]` 传 temperature/top_k（attempt 语义与 mlx 对齐）；验收：同一段文同种子云端/本地各配一遍，音色语速人耳对照 + cer 同档。**注意**：此差异不是 EGOIST 不稳的主因（主因是 09-11/13 种子纪律三连修之前的时间窗），仅为残留差异。 **2026-10-11 施工**：`tts.sampling_for(attempt)` 收拢阶梯，mlx 与 CUDA 两条分支共用；CUDA 分支向 `generate_voice_clone` 显式传 `temperature`/`top_k`，并**同传 `subtalker_temperature`/`subtalker_top_k`**——读 qwen-tts 0.1.1 源码确认子解码默认也是 0.9/50，而 mlx 版的 code_predictor 直接复用主解码的 temperature/top_k，只传主解码仍不对齐。`attempt<1` 报错不静默取最冷档。用例 `test_cuda采样参数与mlx同一阶梯`（1–4 次逐档断言）、`test_sampling_for拒绝attempt0`；变异 2 条（删 subtalker、删主解码参数）均被杀。验收照原文：同段同种子云端/本地各配一遍人耳对照 + cer 同档。

### [D69] Gemini 3.8 flash 写稿时不读笔记、只搜字幕：SKILL.md 明文「笔记是唯一主源、开写前逐段读」被持续无视——字幕搜索返回 ±1 句碎片给不了分集脉络，模型拿它一集一集探测地图，既慢又在凭记忆选集
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``skills/write-script/SKILL.md`（规则写在正文里模型不看）、`config/agent/scopes/creative.md``；伪恋期会话实证（2026-10-10，两次）；董香二期同型（人回忆：一直看字幕、看笔记很少）
- 原记录（活跃表原文）：状态栏「**已修·待新会话验证**（2026-10-10）」。实证：两轮起笔各连发 30+/50 次 `search_notes [字幕]`、零次读 `伪恋.md`，第二轮 8.8 分钟 50 次调用撞检查点。**修法（人已选）**：规则从 SKILL 正文提到 scope 文档——creative.md 加「01 选题与开写顺序」节（先读笔记铁律 + 立意与时长没问人不许动笔）。**病例 2（同日）**：规则落盘时该会话正在运行，运行中会话不重读 creative.md（`_reinject_changed` 只在恢复后第一轮触发），照旧提示词接着探测——规则改动须「结束会话 → 恢复」才生效。**元教训**：本轮修改第一轮实际未落盘（回合在读完文件后就结束了），后续回复却声称已改——「说了≠做了」，以 mtime/grep 为准验证。

### [D71] 作业执行缺乏状态提示：UI 不告诉用户「现在正在干什么」——重配/配音/排片这类长作业跑起来后，没有常驻的、一眼可见的「正在做什么、做到哪了」指示；人只能自己去右下角「时间线」里翻
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``desktop/src/renderer`（作业状态展示面）、`App.tsx`（时间线入口在右下角角落）、`VoicePanel.tsx`（重配反馈实例）`；D50 ③（重配三缺口）、D46（已修：对话区运行指示——但只覆盖对话侧，作业侧无对应物）、N52（被拦看不到原因，同类已修）
- 原记录（活跃表原文）：状态栏「**人选 A，已施工·机检与 e2e 通过**（2026-10-11，[spec](../plans/2026-10-11-job-status-bar-spec.md)），待真实一期手验（UI 规则 2）」。人原话：「做任务的时候缺乏状态提示、UI不告诉用户在干什么，这是非常大的问题」。实证（2026-10-10 伪恋期）：重配触发后唯一反馈是「进度见时间线」小字，面板无进行中态、底部无作业横幅；配音 125s 期间同理。**定位**：这是作业侧（jobs）的系统性缺口，不是单个面板的问题——对话侧在 D46 修过（工具转圈、「模型思考中」），作业侧没有对应物。方向候选（待人定后出 spec）：① 全局常驻作业条（顶栏或对话区上方，列出进行中作业：命令、期、已运行时长，点击跳时间线）；② 时间线按钮在有作业运行时换醒目态（计数+动效）；③ 各面板自行加进行中态（已被实证不够—— VoicePanel 有「进行中」行但不跳眼）。实例补充（同日）：check_script 机检未过走退出码 1，时间线把它显示为「失败」作业（语义误报：机检有 FAIL 项 ≠ 作业故障）；tts 首配拆成两个作业（23:14/23:26）人无法从 UI 分辨「还在首配」还是「已在重配」。UI 规则适用：场景进 spec 第一节、截图方案人选、验收人勾。 **2026-10-11 三方案真实窗口截图**（脚本 [`plans/2026-10-11-job-status-shots/`](../plans/2026-10-11-job-status-shots/shots.spec.ts)，输出 `desktop/out/d71-shots/`；场景照伪恋期 03.5：`tts --apply-patch` 跑了 1:52、此前一次机检 rc=1 被记成 failed）：0-现状（作业侧唯一线索是面板一行「增量重配进行中（pid …）」与时间线角落计数）；A 顶栏下全宽常驻作业条（命令 · 期 · 已运行时长 · 看进度）；B 时间线头部醒目态（转圈 + 「运行中 1 · tts 1:52」脉动徽章，预览区收起时看不见）；C 顺听面板自管（「正在重配段 1、5（已完成 1/2）」+ 逐段「重配中 / 已重配」）。A/B 是作业侧通用件，C 只覆盖 03-audio 一处；三者可叠加。 **2026-10-11 人选 A 并施工**：`JobBar.tsx` 顶栏下全宽常驻条（转圈 · 正在运行 / 排队中 · 命令（超长截断）· 已运行 m:ss 每秒走 · +N · 看进度），无活动作业不渲染、进程已不在换警示样式；数据 = 活跃期 `ep.jobs`，`JobView` 新增 `startedAt`（取 `job_started` 载荷的 `started_at`，缺则事件时间）；「看进度」= 展开预览区 + 展开时间线（展开状态提到 App）。附带（方案说明里提出、人未反对）：`check_script` 退出码 1 在时间线显示「未通过」、不计「失败 N」。与原型差一处：去掉期名（只显示活跃期的作业，期名冗余）。**不做**：跨期作业（宿主只订阅活跃期；重启条件见 spec §四）。vitest `jobBar` 7 条 + `fold` startedAt 1 条；e2e「D71 全局作业条」含 1280×800 / 1440×900 不相交与不横滚底线；变异 3 条全杀。实现截图：`desktop/out/d71-shots/A-已实现*.png`。

### [D72] 全局读音纠错后重配链路断裂（UI 状态易失 + 双按钮分裂）：在顺听面板划选错字并记入全局表后，受影响段落无法触发重配——主按钮（「只重配这 N 段」）只认期级补丁且在段数为 0 时置灰禁用；管全局表的按钮（「按全局表重配」）不仅是分裂的第二个按钮，且其依赖的 `globalTouched` 仅存于前端 React 组件内存，切走标签或刷新即永久丢失，重配入口彻底消失
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``desktop/src/renderer/VoicePanel.tsx`（`globalTouched` 状态、`voice-retts` 按钮）、`desktop/src/host/service.ts::voiceReTts`、`pipeline/agent/cli.py::/voice-info``；D50, D51, N12, D71
- 原记录（活跃表原文）：状态栏「**已施工·机检与 e2e 通过**（2026-10-11，候选①②③全做），待真实一期手验」。实证（2026-10-10 伪恋期）：人在顺听面板划选「千」「全」并点「记下（写全局表）」，`config/voice.json` 确实于 23:24:57 成功落盘两词（`readings_provenance` 有确凿记录）；人回忆「千真万确标记了错字，点了重配」，但盘上 manifest 零重配（attempts 全为 1）。**根因深挖**：① 纠错与重配在 UI 上严重分裂：期级走 `corrections.json`（按钮 `voice-done` 调 `voice.applyPatch`），全局走 `config/voice.json`（按钮 `voice-retts` 调 `voice.retts`）；② 显眼主按钮是期级按钮，`pendingSegs` 为 0 时显示「只重配这 0 段」且 `disabled`，用户直觉被阻断；③ 致命状态易失：`globalTouched` 未持久化也未随 `voice.info` 下发，完全靠小卡回调在组件内存 `useState` 里临时记一笔！用户切走标签、刷新或重载，内存丢失重置为 `[]`，`按全局表重配` 按钮彻底永久消失。修法候选：① core 的 `/voice-info` 统一计算全局表/期级表综合的待重配段落（对比 manifest 中 readings 与当前两表），由后端统一下发 `pending_segs`；② UI 合并为一个统一的「重配受影响段落（N 段）」按钮，不让人感知底层存储分层；③ `globalTouched` 必须来自数据驱动而非 React 瞬态内存。 **2026-10-11 施工**：① core 新增 `tts.rerun_labels(ep)`（纯读）——判据直接复用 `run()` 的 `_reusable`（speakable 段级比对、钉种子、文本、wav 在盘），只报清单里已有 take 的段（配音进行中未轮到的不算），引擎与当前配置不符时返回 null（判据 4）；`/voice-info` 下发 `rerun_segments`。③ 删掉组件内存 `globalTouched`，待重配段一律来自盘。② 底部只留一个「只重配这 N 段」（N = 期级条目段 ∪ rerun 段）：有期级条目走 `--apply-patch`（读码确认其复用判据同样会重配全局表改过的段），否则走普通重跑；两个写路径分在互斥渲染的两个按钮里以守住 TG-4′「一个处理器一处闭集调用」。顺带：配音进行中面板 5 s 轮询，底部待重配段随每段完成实时减少（D50 ③「不说在重配哪些段」的一半）。用例：pytest `TestRerunSegments` 5 条（含 /voice-info 纯读）；vitest 解析器 1 条；e2e「D72 全局读音纠错后重配入口不丢」复现原病例（记下全局 → 切走切回仍在 → 走普通重跑、确认框取消不起作业）。变异 3 条均被杀。 **同日补**：D71 截图时发现重配进行中（锁在、pid 活着）主按钮仍可点、再点只会撞锁——`doneDisabled` 加 `lockBusy`，D72 e2e 补断言，变异被杀。 **2026-10-11 硬盘到手后真实数据复核（只读）**：病例的「零重配」证据读错了——`attempts` 是单次合成内的重试次数，不是重配次数。伪恋期 events.jsonl：23:14:51 首配 tts（629 s）、**23:26:47 第二次普通 tts（376 s，即「按全局表重配」）**；含「千」「全」的段 1–5、10 的 wav 均在 23:27–23:33 重写，speakable 与当前全局表逐段一致（`rerun_labels` 实测返回 `[]`，判断正确）。即当晚重配实际发生了；本行修的「状态易失 + 双按钮分裂」照样成立（切走即丢入口是读码确证的），只是不该再引用「零重配」当病例证据。

### [D77] agent 做不了「把人给的本机图片放进期目录、改名、裁剪」这类简单文件操作，只能叫人进终端 `cat … \
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：`ava … /import-cover`；人提议开放 bash，而不是逐个打补丁`；`pipeline/agent/tools.py`（工具表 14 个已到顶，ADR-0025；无「从本机路径导入」通路）、`desktop` 封面导入只有人拖拽
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-11，人选方案 B，[ADR-0029](../adr/0029-bash-tool.md)），待打包版真实场景手验」。ADR-0021, ADR-0025, D59, N50

### [D78] 几乎每次工具调用都要人用鼠标点「批准」；剩下的卡也不能回车批准
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/session.py::review_tool_call`、`status_card.py::_BASH_DANGER_RULES`、`desktop/src/renderer/HumanCards.tsx``；ADR-0029, ADR-0030, D44, D37, D47, ADR-0021
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-12，[ADR-0030](../adr/0030-approval-danger-only.md)，[spec](../plans/2026-10-12-steer-queue-and-approval-spec.md)），待真实一期手验」。人原话「在目前的 agent 时代，实际上尽可能减少人的审批，模型是在不断进步的」「如果点击回车就可以批准就好了」「建议再减少一些弹框」。现只剩 bash 硬危险、cloud 花钱 / 销毁、写 config/ 三命令、越过人工闸旗标、草稿打转刹车、检查点会弹卡；回车即批（400 ms 防连按），bash / cloud 卡可「本会话都批准」。施工中途安全分类器拦截，人裁决继续。待办：插盘后复核金样本 G-P1

### [D79] 回合在跑时不能发消息，必须等模型做完一轮
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/{protocol,llm,aging}.py`、`desktop/src/renderer/Composer.tsx``；D78, Pi `agent-loop.js::runLoop`
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-12，[spec](../plans/2026-10-12-steer-queue-and-approval-spec.md)），待真实一期手验」。照 Pi 的 steering：在途消息排队（回 `queued`），这批工具跑完、下一次请求前作为 user 消息送达；模型已作答时有排队则续一轮；中断 / 出错退回输入框。pytest 新增 16 条 + TP-6/TP-13 改写，vitest 523 全绿，变异 12/12 被抓

### [D80] 对话区不「活」：会话没名字、工具行是内部工具名加整行染色、运行中只有转圈、做完一轮过程不收起、遥测铺在聊天里；工具卡的命令和危险标记被待答区滚出视野
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``desktop/src/renderer/{ConversationPane,SessionList,HumanCards}.tsx`、`style.css`、`tokens.css`、`pipeline/agent/session.py`（标题线程、bash 帧 `description` 键）、`cli.py`（`/rename-session`）`；Spec 14, D39, D45, D46, D66, D71, D76, D78, D79, ADR-0030
- 原记录（活跃表原文）：状态栏「**PR-C、PR-A 已施工·机检通过**（2026-10-12，[spec](../plans/2026-10-12-conv-liveliness-spec.md)；人选方案 A；会话标题后台起名 + 改名），PR-D（R5 / R6 人选 C / R7）已施工·机检通过；待真实一期手验」。第二轮学 ZCode（源码实读 `aac4755`）。分四个 PR：A 会话标题（唯一动 core 会话层）、B 截图选型、C 工具行 / 动感 / 回合折叠、D 噪声收纳 / 工具卡 / 会话行。P13 实测（附件脚本）：1440×900 待答区可见 276px、内容 538px，聚焦批准后滚 196px，`card_text` 整段在可见区之上——草案「卡片不显示命令」的诊断被推翻

### [D82] 生产中撞到流水线真 bug 时，ava 受 Code Freeze 约束改不了，只能由人切到 coding agent 凭记忆复述现场；修完 ava 也不知道从哪续跑
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/tools.py`（新工具 + 读域）、`pipeline/agent/session.py`（开会话提醒）、`desktop/src/renderer/`（复制交接按钮）`；ADR-0029, ADR-0030, D53, D81
- 原记录（活跃表原文）：状态栏「**PR-B / C 机检通过，待人验收；PR-A 已施工·机检通过**（2026-10-13，分支 `d81-d82-project-layer`，已合 main；[spec](../plans/2026-10-12-bug-ticket-spec.md) §十三施工记录；[上位需求](../plans/2026-10-12-bug-ticket-direction.md)）。待人：真实任务验收」。人提议（2026-10-12）。现场由系统采集、模型只填现象 / 预期 / 复现 / 为何不是用法错误；一律弹卡；同命令同报错去重；第一阶段只做一键复制交接、不自动拉起 coding agent；修完写回处理结果，ava 提醒续跑。仓库公开，工单原文不进 commit——2026-10-12 人裁决存仓库内 gitignore 的 `tickets/`（静态测试 + 运行时 `git check-ignore` 双守）。spec 改走 `run_pipeline tickets` 模块（工具表已到封顶 15，新工具须立 ADR）；去重报错行取末行（输出只有尾部）；顺带堵 bash 直接跑 `corrections global` 等写 `config/` 命令不弹卡的现有缺口（合并前需人确认）；「复制给 coding agent」按钮待截图选型。2026-10-12 红队审过：bash 堵口人已确认，另加 bash 写 `tickets/` 与拉起 coding agent 两条硬危险（人裁决）；另上报一条现有缺口——`run_pipeline` 结果出网前不过 `scrub_secrets`（spec §十二 R-9），待单独登记

### [D84] 外部真实素材（图表 / 示意图 / 实物照）只有纪录片用得上，普通期段落又不敢拆短：默认带下段落数上限 20 与 skill §8.1 的 8–12 s 微单元矛盾；check_script 不认补丁池锚点；补丁池不打标就不能锚点；6 s 微动后段静止；agent 拿不到图片直链
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/check_script.py`、`pipeline/ingest_patch.py`、`pipeline/acquire.py`、`pipeline/agent/web.py` / `web_crawl.py`、`skills/write-script/SKILL.md`、`skills/acquire-assets/SKILL.md``；ADR-0010, ADR-0011, D59, N31
- 原记录（活跃表原文）：状态栏「**已施工·机检与端到端通过**（2026-10-13，已合 main；`02-asset-requests.json` 经人裁决扩入期文件白名单；待人：真实一期使用），[spec](../plans/2026-10-13-external-stills-microunit-spec.md)）」。人提需求（2026-10-13，《束胸力学》期：压强 / 受力图 / 矿泉水对比原片里没有，一段 20 s 只放一个镜头）。六条缺口 A–F 见 spec。不做：段内锚点与检索混用（拆成微单元后一段一源即可，混用破「一段只走一个通道」）；图片短边 ≥1600 门禁不放宽（判据 3）

### [D85] 模型请求在发出瞬间被拒的回合（`llm_calls` 0、回滚）：桌面端只显示「0 次模型调用 · 上下文约 N 字」，看不到原因；本地字数还盖掉了上一回合的 token 读数；`turn_end` 不记报错原文，事后无从查
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/protocol.py`（`turn_failed` notice）、`pipeline/agent/session.py`（`turn_end.error`）、`pipeline/agent/tools.py::turn_error_text`、`desktop/src/shared/convFold.ts::turnReading``；D56, D62, D64, D52
- 原记录（活跃表原文）：状态栏「**已修·机检通过**（2026-10-13，`4a36078`）。待人：重新打包后在真实会话里看一次失败回合」。事件：《束胸力学》02.5 批准后进 03，`pipeline` scope 走 light 档 `gemini-3.7-flash-high`，本机代理已不再提供该模型（`/v1/models` 只剩 3.8-flash-high 等），连败三轮；creative scope 走 3.8 一直正常。本机 `config/agent.local.json` 的 light 已改为 `gemini-3.8-flash-high`（本机文件、不进 git）。修法：失败回合发 `turn_failed` notice（报错原文脱敏、截断 300 字）、`turn_end` 记 `error`；桌面端 0 次模型调用的回合不出读数（脚注不写上下文、会话头沿用上一读数）。测试：协议端到端用假端点回 400 复现，变异 2/2；桌面端读数变异 1/1

### [D86] 试看片段（音画同源）切自番剧正片时，成片是没有中文字幕的日语对白：渲染只给旁白配字幕，机制原为 Live / MV 设计；写稿 skill 也没写这个写法，AVA 插试看段时搜「试看」一无所获、翻源码试错 6 次
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/subcues.py`（新）、`pipeline/render.py`（`_resolve_tag` / `_avsync_cues` / `build_ass(extra=)`）、`pipeline/music.py`（`原声字幕:`）、`pipeline/check_script.py`（「试看字幕」）、`pipeline/jobs.py`（bash 注入 `AVA_ROOT` 与 venv PATH）、`skills/write-script/``；ADR-0013, D84, D85
- 原记录（活跃表原文）：状态栏「**已施工·机检与端到端通过**（2026-10-13，分支 `d86-avsync-subs`，已合 main，[spec](../plans/2026-10-13-avsync-subs-spec.md)）。字幕样式人选 A（与旁白同款）；待人：真实一期渲染验收」。人提出（《束胸力学》段落 5–6 间插更衣室试看）。同批修了写法缺口（skill 8.3 / SHOTLIST）与 bash 环境（`$AVA_ROOT`、`python` = 项目解释器）。不做：SP 素材配字幕（无字幕索引；Live / MV 本就不需要）

### [D87] 三件事出自同一次 03.5：① 段 6 第 2 句句内 5.8 s 死寂（引擎坏样本，`_trim_silence` 按单采样 -60 dBFS 判有声没裁掉，CER / 时长带都放行）；② 预览栏只有「待重配」，重配中 / 已重配 / 失败一概看不出，一轮改不干净就成糊涂账（另：同日修掉的 datetime 崩溃让「待重配」从不消失）；③ 对话面板被长路径 / 长 URL / 宽表格撑宽，整个流能两指横划
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/tts.py`（`MAX_INNER_GAP` / `_longest_gap` / `_render_one` / `_best_take` / `render_segment` 替代种子兜底 / `_Progress` 进度落盘）、`pipeline/agent/cli.py`（`voice_info_payload.synth`、段 `mtime`）、`desktop/src/shared/voiceInfo.ts`（`segStatus` / `synthSummary`）、`desktop/src/renderer/VoicePanel.tsx`、`desktop/src/renderer/style.css``；D72, D73, D50
- 原记录（活跃表原文）：状态栏「**已施工·机检与端到端通过**（2026-10-13，分支 `d87-voice-gap-status`，已合 main，[spec](../plans/2026-10-13-voice-gap-progress-spec.md)）。真实窗口截图已出；待人：重配《束胸力学》段 6 验收死寂消失、预览栏状态跟得上」。人提出。门槛 2.0 s 取全库实测（304 个 wav：正常 ≤1.75 s，只有段 6 超）；只改「收不收」，不 bump `SYNTH_LOGIC_VERSION`。进度文件 `03-audio/.tts_progress.json` 不论入口（面板 / AVA bash / 终端）都写，面板只信盘上。同日先修：tts 有纠错条目时合成一段即崩（缺 datetime）、clips 坏段 scout 提示被吞（`d0e7385`）。验收中补修三处（见 spec「补修」）：折叠容器 grid 列被执行中工具行的长输出撑宽；中途被中断截断 manifest，下一轮白配没点名的段；普通 `tts` / `--redo` 不拿锁，与 apply-patch 并发配同一期（新增 `03-audio/.tts.lock`）

### [D93] 模型请求写死 60 s 超时：supercell 企划会话两回合在第 3 次调用（58–64k token）被判超时、直接收尾停止，同会话健康调用已到 40–56 s，超时后也不重试
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/llm.py`（`DEFAULT_REQUEST_TIMEOUT_S`、`LLMError.retryable`、`_parse_timeout`、`run_tool_loop._chat` 重试）、`pipeline/agent/session.py`（`llm_retry` notice、`turn_end.llm_retries`）`；D85, D94
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-14，main `92c06d5`）。待人：重启 AVA 在大上下文会话里用一段时间」。根因与调研见 [施工图](../plans/2026-10-14-llm-timeout-retry-spec.md)：本机代理思考期一字节不发，流式修不了；Pi / Codex 均为 300 s 空闲超时 + 自动重试。人裁决默认 300 s 可配（`request_timeout_s`，0 = 不设），超时 / 断连 / 429 / 5xx 自动重试 3 次（2/4/8 s），400/401 不重试。测试 26 条，变异 14/14；真实冒烟：本机代理 1 s 超时判为可重试

### [D94] 模型请求仍是非流式：长输出只能按整体耗时判超时，桌面端也无法实时显示模型输出
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/llm.py::chat_complete``；D93
- 原记录（活跃表原文）：状态栏「**已施工·真实代理对照通过**（2026-10-15，人说开工；同日人跑探针，四组同题流式 / 非流式逐字段一致，查出拼装空值与 `index` 形状偏差已更正；测试 27 条、变异 8/8）。待人：在真实会话里用一段」。[施工图](../plans/2026-10-14-llm-streaming-spec.md) 已记探针事实（SSE 字段、`[DONE]` + 末块 `usage`、思考期静默）与拼装 / 断流判据；明确不解决思考期静默

### [D95] 企划会话里抓取提案被拒（D81 §4.2 红队 R-3），AVA 把 12 条官方 MV / 预告拆成三批、要人分开三个卷会话去抓；拒因「抓取提案会写进母目录」对抓取不成立，且与「立项先预收集」的真实流程冲突
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/tools.py`（`PROJECT_LIBRARY_MODULES`、`acquire_targets_volume`、`project_module_refusal`、`PROJECT_VOLUME_ONLY_TOOLS`）、`config/agent/scopes/project.md``；D81
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-14，main `d76d6c8`）。待人：重启 AVA，在 supercell 企划会话里一次提完、抓完、登记」。[施工图](../plans/2026-10-14-project-acquire-spec.md)：企划会话放行 `acquire_propose` 与 `acquire` 的 fetch / gate / register --pool / forget / probe；`register --to-patch`（含前缀缩写与 `=` 写法）仍拒并列出本企划的卷；`cover_edit`、`view_image` 不变。新测试 13 条，变异 7/7

### [D96] AVA 的浏览器拿不到持久、方便的登录态，抓取反复失败、很耗人精力：Playwright 驱动的隔离 Chromium（`data/browser-profile`）里 Google 拒绝登录（「This browser or app may not be secure」）；出口是机房 VPS（Hostinger，单节点），YouTube 不登录就要求「Sign in to confirm you're not a bot」，两头都卡
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/web_browser.py`（`launch_persistent_context`、`cookies_spec_for` 写死 `chromium:`）、`pipeline/acquire.py::fetch_argv`、`config/agent/web*.json` 的 `browser.cookies_for``；ADR-0021, D95
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-15，人说开工；`login` 入口已人验收；待人在 AVA 里用一次 browser；YouTube 素材探针失败查明是全局 yt-dlp 缺签名求解器 yt-dlp-ejs，人选 yt-dlp 进项目依赖，10-15 晚已施工（`yt-dlp[default]==2026.8.19`，`yt_dlp_argv()` 优先项目环境，`probe 9,13` 2/2 可解析），[实测记录](../plans/2026-10-15-browser-login-options.md)、[施工图](../plans/2026-10-15-browser-camoufox-spec.md)：Camoufox 下 8 个站登录与重开均通过，yt-dlp 在 YouTube、bilibili、X 嵌 YouTube 上用登录态有实测收益）」。事件：supercell 企划会话 12 条官方 MV / 预告全部抓取失败（yt-dlp 升级到 2026.08 无效）。临时绕过：人手动跑 `XDG_CONFIG_HOME=<一次性目录> uv run python -m pipeline.acquire fetch 9,…,20`（目录里只有 yt-dlp 配置 `--cookies-from-browser chrome`），主力 Chrome 登录态只在人跑的这一条命令里用、AVA 不碰。主考查两个方向（人 2026-10-14 定）：① 给 AVA 专用的开源 agent 浏览器（专为 agent 设计、自带持久配置与登录态管理）；② 由人启动独立配置的浏览器并开调试端口，AVA 经 CDP 接上（浏览器不是自动化启动，Google 放行登录，登录态随浏览器常驻、人随时补登）。两者都要实测：Google / YouTube 能否登录并持久、yt-dlp 能否用上同一份登录态、与主力 Chrome 凭据是否隔离、人补登录是否方便；实测后再写施工图。yt-dlp 走导出 cookies 文件违背 ADR-0021「不碰 cookie 本体」，只作最后手段。**2026-10-15 实测**：不带 cookies 复现 bot 检查（出口 Hostinger）；Camoufox 0.5.6（本机已装）持久配置下 `webdriver=false`、登录页正常，yt-dlp `--cookies-from-browser firefox:<配置目录>` 能直接读出 cookies（不经钥匙串、不导出 cookie 本体、与主力 Chrome 完全隔离）；人启动的独立真 Chrome 经 CDP 接入可行（`webdriver=false`），但 yt-dlp 读它要用钥匙串「Chrome Safe Storage」，与主力 Chrome 共用密钥（按已知设计，待人确认）。推荐 Camoufox；真登录、持久性、登录后 yt-dlp 过不过 bot 检查三项必须人测，命令在实测记录 §四。**午间人在场实测**：Camoufox 登录 Google 未被拦、重开保持登录、yt-dlp 用同一配置拿到 1080p；人要求扩到 niconico / bilibili / X / pixiv 等站点后再写施工图

### [D97] 抓取落盘文件名丢日文假名：`slugify` 只留英文、数字与汉字，「さよならメモリーズ」落成 `supercell_Official_MV_Playback_版`、「僕らのあしあと」只剩「僕」。文件名失去辨识度；两首纯假名标题会撞成同一个前缀，`dup_verdicts` 的「同名查重」判 FAIL、以「incoming/ 里已有同名前缀的文件」拒抓并指向 `acquire forget`——拒因误导，照做会移走另一首歌的台账记录
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/candidates.py::slugify`（`[^0-9A-Za-z\u4e00-\u9fff]+` → `_`）、`pipeline/acquire.py::dup_verdicts` 与 `_fetch_one``；D95
- 原记录（活跃表原文）：状态栏「**已修·机检通过**（2026-10-15，main，未推送）。待人：下次抓日文 / 韩文标题时看一眼落盘名」。违反「为 1 亿部番写」：日文标题是常态，韩文等同样全丢。修法方向：保留各文字系统的字母（假名、谚文等），只折掉文件系统不安全字符；或在 slug 后带候选 URL 的稳定短标识保证唯一。改前先查 `slugify` 的全部调用方与已落盘文件的兼容（台账按文件名找条目，旧文件名不能失配）。本批（候选 9–20）已核对互不撞名。**2026-10-15 修**：`slugify` 改为保留任何文字系统的字母、数字与组合附标（Unicode 类别 L / N / M），先 NFKC 归一全角拉丁与半角假名，按 UTF-8 再截到 200 字节（假名汉字 3 字节一字，旧的 80 字符上限会到 240 字节，加 yt-dlp / register 后缀贴着 255 字节文件名上限）；纯 ASCII 行为不变。`_fetch_one` 只撞名、链接没抓过时不再指向 `acquire forget`，改报占用者（候选号与标题）并让人改 title。兼容：台账存的是实际落盘路径，gate / register 按文件名找条目、不重算 slug，incoming/ 里 12 条旧名文件不受影响（register 挪进池时按新规则起名，池里是新文件名）。测试 +7，变异 9/9

### [D100] 素材候选没有任何关卡：AVA 交给人「一次性批准」的候选总表里，链接、分辨率、授权都是模型自己写的。supercell 卷一那张 53 段表点名的 6 个 Wikimedia Commons 文件经 API 查询全部不存在，「CC0」「3840×2160」等配套信息一并是编的；7 条画集扫图没有链接；段 47 凭空写「已声明授权引用」。`acquire_propose` 一调用就下载（ADR-0030），人在对话里看到的正是这张模型写的表
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/agent/tools.py`（`acquire_propose`）、`pipeline/candidates.py::propose_candidates`、`pipeline/agent/llm.py` 的提议后自动下载钩子、`pipeline/acquire.py`（`fetch` / `probe`）、`pipeline/agent/fetch_log.py``；D99, ADR-0021, ADR-0030
- 原记录（活跃表原文）：状态栏「**已施工·机检与真实冒烟通过**（2026-10-15 晚，[施工图](../plans/2026-10-15-asset-candidate-gate-spec.md) 第十节；人定提议不再自动下载、核不上 v1 不许豁免、不做模型对比；提议后自动跑 `acquire check`，机器表挂进工具结果，`fetch` 只抓「可下载」；变异 18/18；10-15 那 6 个 Commons 名实测全部「打不开」）。待人：下一轮补素材只看机器表批一次」。照 D99 的思路做到素材上：抓取留档补存页面的图片与链接列表；候选必填 `found_on`（在哪页找到的）；新模块逐条核出处（`url` 必须在抓过的 `found_on` 页里）、实测可得性与画质、只认 Wikimedia API 返回的授权、提前判门禁；交人批的表由代码生成，模型只写 why；下载只抓核过的。与卷一事实文件第一版编造出处是同一类问题（人 2026-10-15：「编造来源这种事情很多次了」）

### [D102] 10 月起每次要用卡都开不了机：「该主机空闲 GPU 不足，主机 GPU 空闲数量 0 卡」。按量实例绑死一台物理机，模型在它的数据盘上，只能等这一台（同时刻西北B区全区还有 9 张空卡）。另发现控制台「我的镜像」是空的，`cloud.json` 写的镜像并不存在
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/cloud.py`（`_ssh` / `run_rsync` / `cmd_up` / `cmd_doctor`）、`config/cloud.json`、`config/cloud.local.json`、`pipeline/agent/tools.py` 白名单`；ADR-0014, ADR-0016
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-15，[施工图](../plans/2026-10-15-cloud-disposable-instance-spec.md) 第十节；`cloud connect` + `data/cloud/ssh_config`、模型软链到文件存储、doctor 查挂载 / 软链 / 显存、账本记型号按型号查价；变异 21/21）。待人：在非 F29 实例上跑通 connect → doctor → TTS 或打标 → pull → down，实测显存门槛。**2026-10-17 非 F29 实例实跑一轮（coding agent，人授权）**：控制台新租 E70（vGPU-32GB，nvidia-smi 报 RTX 4080 SUPER 32GB，镜像 `ava-py312-torch28-cu128-20261015`）→ `cloud connect` → `cloud up --mode gpu` → `doctor` 全绿（Qwen3-VL-8B 打标 2469 镜头无 OOM，32GB 够用）→ `cloud exec` 跑 `vindex captions` → rsync 拉回 → `cloud down` 验证停止响应 → 控制台确认已关机；33 分钟。连带查出两个问题另开 D107（远端代码版本不查）、D108（账本型号查价对不上）。（`cloud.local.json` 的 `instance_id` 仍是 F29 的，但 `cloud.py` 不读这个键，up / down 全走 SSH，不构成问题。）」。模型放区级文件存储（`/root/autodl-fs`），环境存镜像，要卡时任挑一台有卡的机器开新实例，`cloud connect "<登录指令>"` 换连接，用完即弃；推翻 9-11「停机保号」

### [D107] 远端代码版本与本地不一致时，`cloud doctor` 报全绿，打标照跑
- 状态：**已解决·真实生产验证通过（2026-10-17 人确认归档，在万里花/束胸/supercell等真实期充分使用并跑通）**
- 关联（原表）：``pipeline/cloud.py`（`cmd_doctor` 的 `[REPO]` 只查仓库在不在）、镜像 `ava-py312-torch28-cu128-20261015``；D102, ADR-0016
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-17，coding agent）。待人：下次租卡跑一次 `cloud doctor` 看 `[CODE]`，不一致时 `cloud sync-code`」。10-17 新实例 doctor 报「远端仓库就绪 commit 7e3aecd」，本地 HEAD `a5ee6d9`，相差 545 个 commit，`pipeline/vindex.py` 差 1064 行、`shots.py` 差 283 行。用旧代码打标，产物可能过不了加载时的 prompt_version / model 硬校验，也可能校验相同但内容口径不同（后者不报错）。本次做法：rsync 本地 `pipeline/` `config/`（排除 `*.local.json`）与 `pyproject.toml` 上去，md5 三文件一致、远端 `import pipeline.vindex` 通过后才开跑。另：本地比 origin 超前 20 个 commit，远端 `git pull` 也拿不到当前代码。修法候选：doctor 比对远端与本地的 HEAD 或关键文件哈希，不一致报 FAIL 并给出同步命令；或 up 后自动同步工作区代码 **2026-10-17 施工**：判据用文件内容哈希不用 HEAD（rsync 同步不动 HEAD、本地常有未推送 / 未提交改动）。`pipeline/**/*.py` + `config/**/*.json`（不含 `*.local.json`）+ `pyproject.toml` 逐文件 sha256，本地与远端跑同一段脚本（base64 落地）；doctor 新增 `[CODE]` 项，不一致 FAIL 并给修法；`cloud run` 开跑前同样核对，不一致一条远端命令都不发（ingest_patch 走的 captions 也经过这里）；新增 `cloud sync-code`（计费卡同级，远端有任务在跑就拒，推完再核一遍；不删远端多出的旧文件，只报 INFO）。测试 +7（含真起子进程跑远端脚本与本地逐键比对），变异 2/2

## 2026-10-17：已修且无需再手验 / 已验收归档（6 条）

### [D113] `bgm register` 把每首曲子都写成 `"vocal": false`，带人声的也一样
- 状态：**已解决（2026-10-17 修复、机检通过；supercell 已登记条目的 vocal 已由 AVA / Pi 按实改过，`de152c8` 提交）**
- 关联（原表）：`pipeline/bgm.py::register`（约 284 行写死 `False`）；—
- 原记录（活跃表原文）：状态栏「**已修·机检通过**（2026-10-17 登记，同日人说修，coding agent）」。10-17 发现 `config/bgm.json` 有未提交的 supercell 登记（+856 行，16:46，非 coding agent 所写），共 112 首，其中 74 首名字不含 Instrumental 却标 `vocal:false`，如 `#Love (feat. Ann & Gaku)`；coding agent 在 `00a3ca4` 登记的 5 首 nico 原投稿（恋は戦争等，有人声）也是 `false`。`measure()` 本有按文件名的判断（不含 instrumental 给 `"?"`），`register` 没用它。目前 `pipeline/` 里没有代码读 `vocal`，渲染不受影响；风险在人或 AVA 读曲目表选曲时，把人声曲当成伴奏垫在旁白下。**查明**：手工登记的条目在用这个字段（HEAD 里 `vocal:true` 37 首，如 EGOIST 12 首），表示有没有人声；只看曲名定不了——原声带劇伴大多是纯音乐，曲名里却没有 instrumental（东京喰种 13 首都是）。**修法**：新增 `bgm.infer_vocal`：曲名带 `INST_WORDS` 关键词 → `false`；否则用新参数 `--vocal yes\

### [D111] 云端配音回读：主读不过请 Whisper 仲裁时，段落音频超过 30 秒就抛异常，整轮配音退出
- 状态：**已解决·云端实测通过（2026-10-17，工单 T-20261017T073904Z-155ade）**
- 关联（原表）：`pipeline/asr.py::_backend_whisper_cuda`；ADR-0014
- 原记录（活跃表原文）：状态栏「**已修·云端实测通过**（2026-10-17，coding agent；工单 T-20261017T073904Z-155ade）」。supercell 卷一 10-17 云端配音停在段 44：SenseVoice 主读几轮不过，请 Whisper Large-v3（transformers pipeline）仲裁，该段 >30 秒。transformers 对超过 3000 帧 mel 的输入走长音频生成，要求 `return_timestamps=True`，没传就抛 「You have passed more than 3000 mel input features」，`.tts_progress.json` 记 failed，watchdog 空闲关机。平时长段没出事是因为主读一次就过、走不到仲裁；本地 mlx-whisper 与 SenseVoice 都自带长音频处理，不受影响。**修法**：按 wav 时长（标准库 `wave`，回读输入都是 tts 写的 16-bit PCM）只在超过 `WHISPER_WINDOW_S=30.0` 时传 `return_timestamps=True`；30 秒以内调用不变，因为回读 CER 门槛是按不预测时间戳的解码标定的。**验证**：单测 +5（时长边界、读时长），变异 2/2；新卡（RTX PRO 6000，transformers 4.57.3，whisper-large-v3）实测：13.7 秒段新旧调用转写一字不差；拼接的 35.7 秒段旧调用复现原报错，新调用经 `_backend_whisper_cuda` 正常转写（4.9 秒）。长段转写不带标点、短段带，不影响判定：CER 比对前 `tts.normalize` 先去标点。**未改**：一次仲裁异常仍让整轮配音退出（诚实失败，符合 S 准则）；改成只标该段失败、其余段继续，需人另定。

### [D110] `calibration set` 从命令行直接调用时，实录照样写「经人审卡写入」
- 状态：**已解决（2026-10-17 施工、机检通过；实录来历措辞，无需人手验）**
- 关联（原表）：`pipeline/calibration.py`（实录模板写死「人审卡写入」）；D60
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-17，coding agent）」。人审卡只在 AVA 里调用时才弹；coding agent 或人在终端直接跑 `python -m pipeline.calibration set` 不经过任何卡，实录却写成经过了。10-17 supercell 两个阈值就是这样写进去的（人当晚口头授权免拍板，evidence 里写明了，但模板那句不对）。实录是给以后判断「这个数谁拍的板」用的，来历写错会误导。修法候选：模板按调用来源写（AVA 卡批准 / 命令行直调），或命令行调用时要求 `--approved-by` **2026-10-17 施工**：两个候选合用。`run_pipeline` 执行人在卡上批准的命令时（`ctx.human_approved`，REPL `/run` 确认同理）给作业环境注入 `AVA_HUMAN_CARD=1`（`jobs.HUMAN_CARD_ENV`）；`calibration set` 有它才写「经 AVA 人审卡写入」，没有就必须带 `--approved-by "<谁、何时、怎么批的>"`，实录写「命令行直调写入，批准：…」，两者都没有拒写。10-17 supercell 两条已写入的实录未回改（config 内容，要改由人定）。`corrections global` / `bgm register` 的来历字段有没有同样问题未查。测试 +3，变异 2/2

### [N50] 会话注入的 LLM 密钥变量会一路继承到作业及其子孙（ffmpeg、ffprobe、yt-dlp 等外部程序）
- 状态：**已解决（2026-10-15 施工、机检通过；作业剥密钥变量，无界面行为需人看）**
- 关联（原表）：`pipeline/jobs.py`（起作业 `env = dict(os.environ)` 原样继承）、`desktop/src/host/spawner.ts::spawnSession`（密钥只注入会话进程）；N49、Spec 10 §2.9、TH-6
- 原记录（活跃表原文）：状态栏「**已施工·机检通过**（2026-10-15，[小 spec](../plans/2026-10-15-n50-secret-env-spec.md)；人裁决做、crawl 选 C2 不改）。`run_pipeline` 作业剥全部密钥变量，`adversarial` / `fact_review` / `notes_review` 只留 LLM 那一个；`browser` 起 Camoufox 传去掉密钥的环境；变异 8/8。明示残留：crawl4ai 拉起的 Chromium、playwright 的 node 驱动进程仍带密钥变量（两者都不开放 env 参数）。原状态：备忘（N49 评审副产物，2026-09-29；非 N49 引入）」。N49 独立评审用 `ps -Eww` 取证：会话 → `pipeline.qc` → `ffprobe`/`ffmpeg` 每一级的环境里都有密钥变量（夹具里是 `AVA_TEST_KEY`）。终端 `ava` 本来就是这个行为；这些程序以同一用户身份运行、不读该变量，**不构成越权**。N49 修后这些外部程序（含 `acquire` 的 `yt-dlp`）在桌面端真的会跑，拿到该变量的外部程序因此变多。若要收紧：作业起子进程时剥掉密钥变量（改 `pipeline/`，须另立文问人）。暂不修。 **2026-10-06 扩面加注（Spec 15 v0.3 §2.7，人裁决 R2-9）**：Spec 15 PR3 落地后，桌面端会话还会注入 web 检索服务的密钥（如 `TAVILY_API_KEY`，至多 4 个），同样随作业继承到外部程序。人裁决接受、PR3 不改 `jobs.py`：同用户进程本可直接经 `/usr/bin/security` 读钥匙串，浏览器与 crawl4ai 不经 `jobs.py`，只改一处会造成已收紧的错觉；要收紧须另立小 spec，同时覆盖两条拉起路径。

### [D88] 有些 bug 只有生产环境能撞出来：其中可以提前发现的两类没进测试——静态可查的未定义名（tts 缺 datetime、clips 写错变量名，都在 main 上待了几周），以及长任务中途被打断后盘上状态不一致
- 状态：**已解决（2026-10-13 修复、机检通过；改的是测试覆盖，无需人手验）**
- 关联（原表）：`tests/test_static.py`（ruff E9/F821/F822/F823 进 pytest，ruff 入 dev 依赖）；中断审计修 4 处：`subindex.load_all` / `vindex.load_scene` / `faces.load_faces` 加载时核对 .npy 与 .json 条数（两步写之间被打断会让下标整体错位、检索静默指错）；`render._write_final` 先写 `.05-final.partial.mp4` 再原子替换（此前直写，打断即毁掉上一版成片且 status 报渲染完成）；`vindex.write_presence` 改原子写；D87
- 原记录（活跃表原文）：状态栏「**已修·机检通过**（2026-10-13）」。人提出（「没人用永远没问题」）。审计范围：tts、render、clips、subindex / vindex / faces、shots、assemble、corrections、candidates；shots 抽帧、vindex captions 续跑、clips、assemble 本来就对。未修：`shots.caption_frames` 中断后残留 `tmp-*.jpg` 会报错要人删（响亮失败，不静默）

### [D76] 对话区里 agent 给的长命令难读难复制：代码块只横向滚动、没有复制按钮，人要拖选一整行超长命令
- 状态：**已解决·人已验收（2026-10-11 真实窗口验收「没问题」）**
- 关联（原表）：`desktop/src/renderer/PreviewPane.tsx::renderChatMarkdown`、`style.css` 的 `.conv-md .markdown pre`（`overflow-x: auto`）；D46（对话区 Markdown 渲染）
- 原记录（活跃表原文）：状态栏「**已施工·人已验收**（2026-10-11，人选方案 C；真实窗口验收「没问题」）」。人原话：「客户端显示bash命令之类的文本特别长，还不能一下子就复制」。修法候选：代码块右上角「复制」按钮（`navigator.clipboard`，只复制块内原文）+ 长行折行显示（复制仍是原文）。UI 规则 4：出真实窗口截图让人选。附注：本例的根因其实是 agent 让人去终端跑命令（违背 D59「不该人亲手敲」），能力补上（见 D77）后这类长命令应当很少出现在对话里。**2026-10-11 人选方案 C 并施工**（截图脚本 `docs/dev/plans/2026-10-11-copy-button-shots/`）：助手回复代码块折行显示 + 右上角常驻「复制」（宿主渲染后挂上，不进 `renderChatMarkdown` 输出，D46 不变量不动）；bash 工具行与命令审批卡各一个「复制命令」，复制的是命令原文（卡面每行带「│」前缀、工具行摘要截 80 字）。工具行的原文来自 tool 帧的条件键 `command`（仅 bash，core `session._on_trace` 发、TS `convFrames.CONDITIONAL` 登记）。机检：vitest 3 条 + 真实 core e2e（三处各点一次，系统剪贴板读回原文、每次前写哨兵），变异 5 处全杀。**同日补：继续会话也能复制**（人要求「全量可以复制」）——`protocol._send_history` 按 `tool_call_id` 回查当时的调用，tool 条目照实给工具名（原恒为 None，旧工具行只显示「tool」），bash 条目带 `command`（TS `CONDITIONAL.history` 同步登记），桌面端按 core `tool_summary` 同一规则补命令摘要。机检：pytest 1 条 + vitest 3 条 + 真实 core e2e（结束会话 → 继续 → 旧工具行复制得原文），变异 7 处全杀。

## 2026-10-11：伪恋期 06 渲染配乐（1 条，立案即修）

### [D75] `bgm register` 把外置盘绝对路径写进 config/bgm.json；曲名带曲序时 agent 误以为没登记去重登
- 状态：**已解决**（2026-10-11；pytest 2548 通过，变异 3/3 被抓）
- 关联：`pipeline/bgm.py`（`_rel_repo_path`、`resolve`、`_resolve_rec`）、AGENTS.md Code Freeze、D59（agent 可调 `bgm register`）
- 经过（伪恋期 events.jsonl）：10:12 `render` 失败「曲目表里没有「09. 恋人」，检查01-topic.md 的 BGM正文 字段」——表里早有键「恋人」（register 用 `clean_track_title` 去掉曲序前缀），错在 01-topic.md 写了文件名；agent 却经人审卡跑 `bgm register --anime 伪恋 "/Volumes/Samsung T7/…/09. 恋人.flac"` 重登（多余），真正解围的是 10:17:32 改 01-topic.md。重登把「恋人」的 path 从 `data/library/bgm/伪恋/09. 恋人.flac` 改成了卷绝对路径（违反「路径一律相对仓库根」，换盘即废）。人原话：「bgm.json不改的话没办法配乐啊」——登记本身是正规流程，Claude 先前称其「越权」是错的，错在 AGENTS.md 的 Code Freeze 例外没列它。
- 修：① `_rel_repo_path` 在仓库根之外再按 `data` 符号链接的真实位置折回 `data/…`；② `resolve` 找不到曲名时若去掉曲序前缀能命中，报错直接指出表里的键并写明「改 01-topic.md，不用重新登记」；③ `_resolve_rec` 返回值原为 `{"name", "path": p, **rec}`，`**rec` 把组装好的绝对路径覆盖回相对串（文档写「绝对路径组装」），只在 cwd=仓库根（jobs 作业恰是）时 ffmpeg 找得到——改为 rec 在前；④ AGENTS.md Code Freeze 例外加第三条「曲库登记」；⑤ config/bgm.json 那一行改回相对路径（与已提交版本一致）。
- 附带教训：变异与原文件同字节数、同一秒内改回时，`__pycache__` 的 pyc（按秒级 mtime + size 判失效）会继续用变异版，表现为「还原后测试仍红」。做 Python 变异后要清对应 pyc。

## 2026-10-11：规则补进 AGENTS.md 归档（1 条）

### [N23] 严禁在仓库根目录落地临时脚本
- 状态：**已解决**（2026-10-11：按 2026-09-28 复核给出的最小改写，在 AGENTS.md「五、工程约定」加「临时脚本不落仓库」一条；原行「位置」栏写 `AGENTS.md` 而 AGENTS.md 里并无此条，新 session 无从照做——这是本行唯一的实缺）。`scripts/` 下两份已执行过的一次性 EGOIST 脚本（`migrate_sp_slugs.py`、`rebuild_micromotion_sps.py`）**未动**，删除属红线、留删交人定。
- 关联（原表）：`AGENTS.md`；—
- 原记录（活跃表原文）：2026-08-26 用户严厉指出；单期测试必须在 `data/episodes/<本期>/` 或通过 CLI 完成。**2026-09-28 复核**（只读，未删任何文件）：① 执行现状：仓库根无临时脚本/文件（`git status --ignored` 只有 `.DS_Store`、`.idea/` 与 `*.local.*` 配置，均属正当本机文件）；2026-08-26 以来根目录新增文件只有 `.python-version`。但 `scripts/` 下有两份**已执行过的一次性、单番专用**脚本：`scripts/migrate_sp_slugs.py`（`c5298cd`，EGOIST 池 SP01–46 改名迁移）、`scripts/rebuild_micromotion_sps.py`（`3d8a906`，EGOIST 24 条照片微动素材重构）——无任何文档/代码引用；不在仓库根、不违反本条字面，但属「用完即弃的单期脚本进了仓库」，留/删交人定。② 可执行性：**`AGENTS.md` 里根本没有这一条**（全文无「临时脚本」「仓库根」禁令，本行「位置」栏写的 `AGENTS.md` 不成立），新 session 无从照做。最小改写建议（报人，未改）：在 AGENTS.md「五、工程约定」加一句——「**临时脚本不落仓库**：一次性探针/统计放会话的 scratch 目录或 `/tmp`；单期验证走 `data/episodes/<本期>/` 或现有 CLI；要长期留的工具进 `tools/`（写清用途与用法），`pipeline/` 与仓库根一律不放。」

## 2026-10-10：人口头确认验收归档（3 条；UI 规则 2 的验收人即人本身，董香二期（完整真实一期）与伪恋期的日常使用即手验）

### [D39] 桌面端（美化后）界面不适合人类工作：对话区极小、不能自由收起、文件区/预览区宽度不可调、多处 UI 重叠
- 状态：**已解决·真实一期验收通过**（2026-10-10 人口头确认：董香二期为横跨 09-21→10-08 用打包版完成的完整一期，布局 S1–S4 全程在其中使用，「整体很舒适」（2026-09-29）+ 完整一期双验证）
- 关联（原表）：`desktop/src/renderer`（布局与 `ui.tsx`、`style.css`/`ui.css`）、Spec 14 视觉设计；Spec 14（视觉，已归档）、Spec 10（会话面板）、D40
- 原记录（活跃表原文）：现状实测与方案 A/B 见 [`plans/2026-09-29-desktop-layout-options.md`](../plans/2026-09-29-desktop-layout-options.md)；人选 B（预览默认收起），S1–S4 已施工（`79b83cd` `e5734f9` `0ae1c0c` + S4）；**2026-09-29 人在打包版体验「整体很舒适」**。真实一期验收待硬盘到手（plans/README UI 规则 2 未勾），通过即归档本条并恢复被本条暂停的打包手验；「上下文用量不可见」另立 D41。

### [D41] 桌面端看不到上下文用量，也没有 `/compact` 一类压缩历史的手段：长会话越聊越大，人不知道离上限多远，也没法主动瘦身
- 状态：**已解决·真实生产验证**（2026-10-10 人口头确认：上下文读数从字符版（D41-A）用到 token 版（D56），伪恋期 8 回合实测读数；「看不到用量」的原始问题已不存在）
- 关联（原表）：`desktop/src/renderer`（会话头/回合脚注）、`pipeline/agent/session.py`（`self.messages`）、`llm.py`（`prompt_chars`）；D39, Spec 9, Spec 10
- 原记录（活跃表原文）：人原话：「我们没有显示 context window，没有 /compact，这个可能会带来一些困扰」。**现状（2026-09-29 只读核实）**：① core 每回合已算 `prompt_chars` 并随 `turn_finished` 帧下发（Spec 9 §2.3 第 7 条观测量），但桌面端回合脚注 `shared/convFold.ts::footerText` 不显示它；② 会话历史在一个会话内只增不减（Spec 9 §2.3「唯一天然上限是上下文窗口」），全仓无压缩 / 摘要机制；「继续上次会话」按 `session.jsonl` 全量重建，同样不缩；③ 现成的绕法：「结束会话」后发新消息 = 新会话、历史为空，期的进度在产物里（产物即状态），不靠对话记忆。**待定**：只显示用量（改动小，纯 UI，字符数不等于 token，需如实标注口径）还是另做压缩（牵涉 core、摘要可能丢判断依据，需立 spec 并问人）。 **2026-10-06 D41-A 施工**：口径读码确认（`llm.py::run_tool_loop::_chat`：本回合最后一次请求模型时全部消息正文的字符数，不含工具调用参数与工具定义，发送前计算）。① 回合脚注末尾追加「· 上下文 N」；② 会话头「上下文约 N」（最后一个 `ready` 之后最近一次 `turn_finished` 的值；新会话、继续上次会话都从头算；还没有回合结束不显示）；③ 悬停说明如实标口径（字符数不是 token、无上限刻度）并给出「结束会话后发新消息开新会话」的绕法。读数格式：万以下千分位整数（`9,192 字`），万及以上一位小数（`1.4 万字`）。不画百分比条：配置里拿不到确定的窗口大小。`tokens.css` / `ui.css` 未改。验证：vitest 410、tsc、L-3 底线、全量 e2e 115 passed / 2 skipped（两个跳过是 `AVA_VE0_OUT` / `AVA_SHOTS` 环境变量门控的截图用例）。新增可见字符串：`上下文约 {N}`、`· 上下文 {N}`、悬停说明一段（`SessionHeader.tsx::CONTEXT_READOUT_TITLE`）。。**2026-10-09 修订（D56）**：读数口径由字符数改为服务商返回的 `usage.prompt_tokens`（「上下文 12.3k token」），拿不到时回落字数。

### [D46] 对话区难读：助手回复不渲染 Markdown；失败的工具原文默认整段展开占满版面；模型生成、工具执行期间对话区静止，看不出动没动
- 状态：**已解决·真实生产验证**（2026-10-10 人口头确认：Markdown 渲染 / 工具原文默认收起 / 运行中指示，自 2026-10-08 晚起每个桌面会话日常使用）
- 关联（原表）：`desktop/src/renderer/ConversationPane.tsx`、`PreviewPane.tsx::ChatMarkdown`、`style.css`；[plans/2026-10-08-conv-readability-spec.md](../plans/2026-10-08-conv-readability-spec.md), Spec 10 H-3 / RF-13（修订）, Spec 9 H-3（修订）
- 原记录（活跃表原文）：人原话：「很显然没有渲染，让人看着很费劲」「工具调用结果不要自动展开，占空间太大了」「模型思考、调用工具的时候UI没有动感，让人不知道到底动没动」。H-3 按 RF-13 留的口子修订：渲染器关掉链接与图片，测试证明输出里没有可点控件与资源加载；卡片仍纯文本。失败原文改默认收起（推翻 Spec 10 当时的用户裁决，人新裁决）。运行中：执行中的工具行转圈，末尾「模型思考中 / 工具运行中 · N 秒」。


---

## 2026-10-10：生产证据核销归档（4 条；董香二期/伪恋期的真实使用痕迹——回收站文件 mtime、回合记录的 prompt_tokens——证实修复后的构建已被生产使用，替代「待真实手验」）

### [D45] 一期只能用一个对话：上下文被污染后没法丢掉重开，看不到有哪些会话、不能选、不能删，界面上只有「继续上次会话」
- 状态：**已解决·真实生产验证**（2026-10-08 施工，当日 18:25 即在真实客户端删三段会话——董香二期回收站 3 文件 mtime 为证；列表/进入/新开日常使用）
- 关联（原表）：`desktop/src/renderer/SessionList.tsx`、`desktop/src/host/service.ts`（conv.sessions/enter/fresh/delete）、`pipeline/agent/session_log.py`（`move_session_to_trash`）、`pipeline/agent/cli.py`（`/list-sessions`、`/delete-session`）；[plans/2026-10-08-session-management-spec.md](../plans/2026-10-08-session-management-spec.md), ADR-0024 §3 I2（2026-10-08 增补）, Spec 9 §2.5
- 原记录（活跃表原文）：人原话：「一期视频只能建一次对话……不能直接删除那个对话，只能去选择：继续上次对话」「还需要：看到有哪些会话，可以选择进去，可以删除」。人裁决：删除 = 移到回收站；界面选方案 B（侧栏子列表）；去掉「继续上次会话」按钮；空会话灰显可删。顺带修了协议原有缺陷：新会话的输出帧 sid 恒为 null（只在启动时取一次）。手验清单见 spec 末节。

### [D56] 会话上下文读数显示「多少万字」，人要的是 token：上下文窗口按 token 计，字数既不能和模型上限比，也随中英文比例漂移
- 状态：**已解决·真实生产验证**（2026-10-09 施工，当天伪恋期 8 个真实回合全部带服务商 `prompt_tokens` 实测读数 68k→111k；人正是看该读数发现 D65）
- 关联（原表）：`pipeline/agent/llm.py`（`chat_complete` 只取 `choices[0].message`，丢了响应里的 `usage`；`_chat` 只算 `prompt_chars`）、`desktop/src/shared/convFold.ts`（`charsText`）、`SessionHeader.tsx`；D41, Spec 9 §3
- 原记录（活跃表原文）：人原话：「目前显示上下文是"多少万字"，这不太合理，不应该是多少k token吗？」。成因：D41 施工时 core 只有字符数，为不谎报口径照实写「字」；真正的 token 数现成就有——OpenAI 兼容接口非流式响应都带 `usage.prompt_tokens`（服务商按自己的分词器算、含系统提示与工具定义，即真实上下文占用），只是没读。方向：`chat_complete` 把 `usage` 存进 ContextVar（不改签名，约 60 处测试替身不动），`_chat` 取 `prompt_tokens` 随 `turn_finished` 下发；桌面端显示「上下文 12.3k token」，服务商不给 usage 时回落「上下文约 N 字」。。**2026-10-09 施工**：`chat_complete` 把响应 `usage` 存进 ContextVar（不改签名），`_chat` 取非负整数 `prompt_tokens`，随 outcome、`turn_finished` 帧、`turn_end` 记录下发；桌面端会话头与脚注「上下文 12.3k token」，服务商不给时回落「上下文约 N 字」（悬停说明改口径）。用例：core 五种 usage 形态、vitest 读数与回落、真实 core e2e（假 LLM 带 usage → 头部与脚注显示 token，下一回合不带 → 回落）；变异 D56-MUT-1..3 + 桌面端手工变异。真实服务商是否返回 usage 待手验：不返回就会一直显示「约 N 字」。

### [D57] 选题会话退出再打开后对话区是空的，人以为聊天丢了、不知道存在哪：记录其实完整在 `data/_idea/session.jsonl`，只是没有入口把它接上
- 状态：**已解决·真实生产验证**（恢复入口当日由 D58 侧栏列表取代并经真实使用覆盖；「系统注入默认收起」在 2026-10-10 真实会话截图中可见）
- 关联（原表）：`desktop/src/renderer/SessionHeader.tsx`、`App.tsx`、`desktop/src/host/service.ts::episodesList`、`shared/protocol.ts::EpisodesList`；D42, D45, Spec 18 §3.3, Spec 10（懒启动）
- 原记录（活跃表原文）：成因：桌面端会话进程懒启动（人发第一条消息才拉起，Spec 10）；期会话有 D45 的侧栏会话列表可点进，选题会话是单一滚动段、没有列表，D45 又去掉了「继续上次会话」按钮——重开后选题视图一片空白，要随手发一句话才会恢复出历史。实测：14:32–14:34 那段两轮对话完整在日志里，之后进程没再启动过。**修（2026-10-09）**：起初在宿主里直接 stat 选题日志，被静态守卫 TG-14（Spec 10 红线 3：桌面端不读会话记录）拦下，改为经 core——新增只读 `ava idea /list-sessions`（与期会话 `/list-sessions` 同一行形状），宿主 `conv.sessions` 对选题会话改调它（spawner 模板 `LIST_IDEA_SESSIONS`；进入指定会话 / 删除仍只对期会话开放）；选题视图无活会话时查一次，有可恢复段就在会话头显示「继续上次的选题对话（最后 MM-DD HH:MM）」，点了走既有 `conv.resume`（显式点击纪律：不自动拉起）。**顺带修**：恢复历史里的系统注入（常驻提示、规程、记忆）原先整段平铺（截图实测一屏规程），Spec 10 §2.3 要求的「折叠为『系统注入』」从没落实——改为单独的行类型、默认收起的「系统注入（N 字…）」。用例：core `test_d57_idea_list_sessions`（只读、不改字节）；vitest 模板 argv、宿主列表与拒绝项、折叠行；真实 core e2e「选题聊过 → 退出 app 再打开 → 按钮 → 接上历史、注入收起 → 接着聊」。多开不改（D42 人裁决：同一时刻一个选题会话）。。**2026-10-09 后续（D58）**：会话头「继续上次的选题对话」按钮由侧栏选题会话列表取代；`ava idea /list-sessions` 与「系统注入默认收起」保留。

### [D58] 选题会话看不到有哪些、删不掉：D57 只能接上最近一段，人要的是和期会话（D45）一样的列表——看、进任意一段、新开、删
- 状态：**已解决·真实生产验证**（2026-10-09 施工，当晚 22:28 真实删除一段选题会话——`_idea/_agent/session-trash/` 文件 mtime 为证；伪恋期建期只带当前段实证生效）
- 关联（原表）：`desktop/src/renderer/SessionList.tsx`、`App.tsx`、`desktop/src/host/service.ts`（`epConv` 对 idea 一律拒绝）、`pipeline/agent/protocol.py`（`--idea` 恒恢复最近段）、`cli.py`（`/delete-session` 只认期目录、`migrate_idea_session` 整份迁移）；D57, D45, D42, Spec 18
- 原记录（活跃表原文）：人原话：「但是无法看到有哪些选题回话、无法删除选题回话」。推翻 Spec 18「选题会话是单一滚动段」（同一时刻仍只一个活会话）。人裁决：建期只带当前这段，其余留在选题列表。[spec](../plans/2026-10-09-idea-session-list-spec.md)。。**2026-10-09 施工**：core——`session_log.split_by_sid`（回收站与迁移共用）；`protocol --idea --continue <前缀>` 恢复指定段、`--idea --fresh` 开新段（裸 `--idea` 不变）；`ava idea /delete-session --sid=`（回收站 `data/_idea/_agent/session-trash/`）；`ava new <名> --from-idea[=<sid>]` 只迁一段、`_idea` 原子写回其余段，指定段不可恢复时不退回带别的段。宿主——`conv.enter / fresh / delete` 对选题放行（新开后下一次拉起带 `--fresh`，拉起成功即清）；建期在结束选题活会话前记下它的会话号传给 core（形状不合规就退回带最近段并记诊断）。渲染层——侧栏「选题」下挂同一个会话列表；D57 的会话头按钮撤掉。用例：core 7 条（指定段 / 新开 / 裸恢复、只迁一段逐字节、不可恢复不退回、坏 sid、删除与租约冲突）；vitest（argv、宿主进 / 新开 / 删、建期传 sid）；真实 core e2e（两段 → 退出重开 → 进旧段 → 删另一段进回收站）。变异 D58-MUT-1..3 + 宿主手工变异。


---

## 2026-10-10：清账归档（机检全绿、无任何待办项的 14 条；由 pi 逐条核对 spec 完成判定与人裁决后迁移）

### [N2] tagger general 标签落盘但无检索路径读它
- 状态：**已解决**（2026-10-09 施工：presence 不再落 `gen` 字段；用例 `test_build_presence_does_not_persist_general_tags`）
- 关联（原表）：`pipeline/vindex.py:321-324`；ADR-0003
- 原记录（活跃表原文）：为第 2 层万一复活留的备用料。**2026-09-28 复核：备用料已被取代，建议删除（报人，未动）**（只读）。① 读路径：落盘的 `gen` 字段**没有任何读者**——`pipeline/`、`tests/`、`desktop/src` 全仓 grep，只有 `vprobe.py:74` 读 `tag()` 的**即时输出**做探针展示（不读落盘文件）；`vindex status` 不读它。② 真实数据：`data/library/vindex/` 下 151 个 `*.presence.json` 全部 `producer=ccip`、`gen` 全空——「推理钱已花、顺手存下」这笔备用料在任何一部番上都**从未真实存在**（ccip 不产 general）。③ 价值：它为之预留的第 2 层（画面语义）已由 ADR-0003「第 2 层之死」+ **ADR-0015 改走 VLM 打标→文-文检索**取代，booru general 标签（rain/night/indoors）不再是复活候选。最小删除清单（须人拍板后另开施工 session）：`vindex.py` 的 `tag()` 不再收集 `gen`（约 272/293 行）、`write_presence` 行结构与注释去掉 `gen`（约 316、334–337 行）、`build_presence` 写行去掉 `gen`（约 368 行）、`faces.py:573` 的 `"gen": {}`、`tests/test_vindex.py:306` 夹具；`vprobe.py` 的 tagger 探针展示可保留（读即时输出）或一并去掉。影响面：已落盘文件多一个空字段无害，加载侧不读它，无需迁移。N1（WD 条目保留）不受影响。**2026-10-09 施工**：presence 文件不再落 `gen`（`vindex.build_presence` / `write_presence` 文档与注释、`faces.py`、测试夹具）；`vindex.tag()` 的即时输出保留 `gen` 供 `vprobe` 探针展示（清单里的可保留项）。旧文件里的空字段无害，不迁移。用例 `test_build_presence_does_not_persist_general_tags`。

### [N12] BGM 素材缺口 + voice readings 换番要清
- 状态：**已解决**（2026-10-09 施工：`readings_provenance` 来历记录，人同意只做往后一半；BGM 半条定性为选曲丰富度问题、需要时按番补料，非阻塞）
- 关联（原表）：`config/bgm.json`（原行号已漂移）、`config/voice.json` 的 `readings`、`pipeline/tts.py::speakable`；—
- 原记录（活跃表原文）：伴奏单曲缺；readings 表每番积累需清理。**2026-09-28 复核（只读）**：① BGM：脚本逐条核对 `bgm.json` 9 部番共 241 首登记曲目，**文件全部存在、零缺失**。「伴奏单曲缺」今天的真实含义是：东京喰种/天气之子/你的名字/EGOIST/PSYCHO-PASS 没有 OP/ED 伴奏版（春物 3、罪恶王冠 1、夏隧 1、伪恋 19），只影响「结尾换伴奏」槽位；按 `_slots`「结尾留空即全程一首」，**不会让任何一期 03/06 走不下去**，降为选曲丰富度问题，需要时按番补料（找各番 OP/ED 单曲 CD 的 Instrumental 轨，同春物做法）。② readings：一张**全局表**（121 条，不分番），`speakable` 对每段合成文本全局 `str.replace`，manifest 记整表指纹、改表后只重做受影响段。串番风险低（替换均为同音字，设计上对字幕与回读 CER 透明）；**真实风险是来历缺失**：每条成立的前提（哪番、哪个音色 seg6/seg7、哪个引擎 Qwen3/IndexTTS）只写在 `_note` 散文里，换音色/换引擎时无法机械找出「该重测哪些条」（2026-09-12「乐迷」即过度治理造新错的实例）。g2p 拼音直注层（D25）已在机械转录其中 61 条。可执行做法（报人，不擅自改表）：给每条补结构化来历（`{src, rep, anime, voice, engine, date}`，或按 voice/engine 分节），换音色/引擎时按字段筛出重测清单；「换番清空」不必做——同音替换在别番无害，清掉反而丢实证。**2026-10-09 施工**：`corrections global` 写入时同步写 `config/voice.json` 顶层 `readings_provenance[词] = {table, date, episode, anime, engine, voice}`（引擎、音色取本期配音清单，番名取 `01-topic.md`，没给期目录只记表与日期）；卡面多一行「来历记录」。先核实过：tts 复用指纹只看 engine/model/ref_audio 与两张表内容，加顶层键不触发重配。旧 121 条不回填。AGENTS.md Code Freeze 例外扩到这个键。

### [N51] 桌面端发出的用户消息偶带尾随换行（`'你好\n'`）
- 状态：**已解决**（2026-10-08 夜：Enter 一律 `preventDefault` 再判断；e2e 复现并用例守）
- 关联（原表）：`desktop/src/renderer/Composer.tsx`（onKeyDown：`running`/`disabled` 时 `return` 在 `preventDefault` 之前）；Spec 10 门禁 12、A2
- 原记录（活跃表原文）：D37-A 打包版实测：同一人两次发「你好」，07:41:12 为 `'你好'`，07:58:27 为 `'你好\n'`（人自述：拼音 nihao 选「你好」后按 Enter 发送）。**机理猜测（未证实）**：此前某次 Enter 落在回合运行中，`running` 分支在 `preventDefault` 之前 `return`，textarea 自行插入换行，随下一次发送带出；也可能与输入法上屏时序有关。后果仅为消息多一个换行，模型可读。待人手复现确认触发条件后再定是否修。。**复现与修（2026-10-08 夜）**：机理猜测成立——e2e 用真实按键在回合运行中按两次 Enter，旧代码输入框变成 `你好\n\n`（`running` 分支在 `preventDefault` 之前 return，textarea 自行插入换行）。修：Enter（非 Shift、非输入法选词）一律 `preventDefault`，再判断能不能发。用例 `N51 回合运行中按 Enter`（旧代码红、新代码绿）。

### [N54] 退出确认框的非人手结束路径不回落：对话框抛异常或父窗口被销毁时 `quitPhase` 永久停在 `confirming`，此后 Cmd+Q 全被拦下
- 状态：**已解决**（2026-10-08：退出确认框异常/窗口销毁统一回落 idle；e2e 守）
- 关联（原表）：`desktop/src/main/index.ts`（`quit-state` 分支 `dialog.showMessageBox(...).then(...)` 无 reject 分支；`before-quit` 对 `quitPhase !== "idle"` 一律 `preventDefault`）；Spec 10 §2.10 第 4 步、D37、D40
- 原记录（活跃表原文）：2026-10-06 D37-C 读码（未实测）：D37-B 给抓取/browser 确认框补了「异常 → ok:false」与 `windowGone()`（D40），退出确认框走的是另一条代码路径，两者都没补。后果方向安全（不会误退出），但 app 只能强退。触发面比 D40 更窄：退出框是窗口模态，正常操作关不掉父窗口；`showMessageBox` 抛异常在 Electron 44.4.5 上未见过。修法候选：`.then` 加 reject 分支回 `quitPhase = "idle"`，`closed` 时若 `quitPhase === "confirming"` 一并复位。不阻断 D37/D40 归档。。**修（2026-10-08）**：退出确认框的应答与异常统一走 `settle`（异常按「取消」回落 idle），每次弹框带代号；窗口 `closed` 时若在 `confirming` 则作废代号并回落，迟到应答被忽略。测试桩加 `respond:"throw"`；e2e「N54 退出确认框抛异常」：抛异常后 app 不退、会话零写入，第二次退出照常弹框并能退（去掉异常分支即红）。窗口销毁那一支只读码验证（正常操作关不掉模态框的父窗口，e2e 造不出来）。

### [N55] `assembly.py::load_injected_doc` 不设读域：`assembly.json` 的路由（及 resident）可让装配器读入任意文件（含 `config/*.local.json` 凭据、软链、仓库外绝对路径），并作为规程正文发给 LLM
- 状态：**已解决**（2026-10-08：装配器读域收敛到 `is_trusted_doc_path` 唯一规则）
- 关联（原表）：`pipeline/agent/assembly.py::load_injected_doc`（`target = p if p.is_absolute() else root / p`，不判 resolve 后位置）、`assemble_resident_prompt`（`resident_map` 同样照配置读）；D30、Spec 16 §3 / §5.2.3、ADR-0022、ADR-0026
- 原记录（活跃表原文）：证据：D30-R 读码（Spec 16 文首 🟡-3）。出网断言只比对四个文件名、不看内容，所以凭据**内容**被误路由进消息时修前修后都不响；触发前提是 `assembly.json` 被人误配（模型没有写 `config/` 的工具），故列备忘。修法方向：在 `load_injected_doc`（与常驻层读函数）层按白名单根 + resolve 后判定拒载并告警，**与 Spec 16 §5.2.3 共用同一个 `is_trusted_doc_path` 规则函数**，不另写第二份；建议在 D30-B 落地之后做，直接复用。不在 Spec 16 修。。**修（2026-10-08）**：`assembly._readable_doc` 包一层 `is_trusted_doc_path`（唯一规则，不另写），`load_injected_doc` 与 `assemble_resident_prompt` 三件读入前都过；读域外（凭据、`docs/` 外的 .md、软链出根、仓库外绝对路径）拒载并在 stderr 告警，缺文件照旧静默。现有路由与常驻三件全在白名单根内，无误伤。用例 `test_n55_*` 两条；变异 N55-MUT-1/2。Spec 16 §3 加修订注。

### [N56] `--continue` 恢复后「规程已修订」重注入在生产路径上永不触发
- 状态：**已解决**（2026-10-08：注入提交带 docs sha，`--continue` 后规程修订重注入链路打通）
- 关联（原表）：`pipeline/agent/session.py`：`_assemble` 首轮 / 换工序两处注入与 `_inject_memory` 调 `_commit` 都不传 `docs`；`prepare_resume` 只从带 `docs` 的 msg 记录汇总；`_reinject_changed` 见 `_injected_docs` 为空即 return；Spec 9 §2.7 第 3 条、Spec 16 TD-2f、`tests/test_agent_session.py::test_ts6_ts7_resume_rebuilds_resident_and_reinjects_changed_docs`
- 原记录（活跃表原文）：唯一写 `docs=` 的调用点是 `_reinject_changed` 自己，形成循环依赖：首次注入不记 sha → 恢复后不知道注入过哪些文档 → 不比对、不重注入。后果：规程或记忆在两次会话之间改了，恢复后模型仍按历史里的旧版工作（新版只在换工序重注入时才出现）。TS-6 能绿，是因为它手写了一条生产代码从不产出的带 `docs` 记录。修法候选：`_assemble` 两处与 `_inject_memory` 提交时带上 `docs=[{path, sha256}]`。修之前要想清楚：修好以后，记忆会经 `_reinject_changed` 以 origin=`injection` 重注入，Spec 16 的 MUT-D11 守的正是这条路径。。**修（2026-10-08）**：`_assemble` 首轮与换工序注入、`_inject_memory` 都带 `docs=_doc_shas(...)` 提交；记忆告警不记 sha；`_reinject_changed` 记忆此刻只剩告警时不重注入。MUT-D11 守的「重注入记忆不进可信集」不变（候选仍标不可信）。用例 `test_n56_resume_reinjects_docs_changed_since_natural_injection` 全由生产代码写记录（不手写 docs）；变异 N56-MUT-1。`_injection_record` 测试助手同步带 docs。Spec 9 §2.7 加修订注。

### [N57] 生产入口的常驻层出网豁免只靠「路由表当前正文」(b)，这一路零用例守
- 状态：**已解决**（2026-10-08：补常驻层豁免用例 `test_n57_*`，杀变异 V4）
- 关联（原表）：`pipeline/agent/protocol.py`、`cli.py` 在会话外预置 `tracker.resident_prompt` / `active_scope`（`protocol.py:640`、`cli.py:1799` 等 6 处）；`session.py::_assemble` 的常驻层 `trust(...)` 只在 scope 热切换时执行；`assembly.py::route_trusted_texts` 的常驻部分；Spec 16 §12 偏差 1、§13 🔵-C1，D30
- 原记录（活跃表原文）：D30-C 变异 V4（`route_trusted_texts` 不收常驻三件）全部用例仍绿。今天三份常驻文档都不含受限字面量，所以无可见后果；方向是 fail-closed（将来写进字面量会被拦，不会漏）。补法：加一条用例——常驻文档（如临时仓库的 `AGENTS.md`）含 `03-audio/manifest.json`，tracker 按 `protocol.py` 的方式预置，首轮应照常发出，用来杀 V4；视情况让那 6 处也走 `tracker.trust(trusted_texts_of_resident(...))`。。**补（2026-10-08）**：`test_n57_preset_resident_with_restricted_literal_keeps_sending`——AGENTS.md 含 `03-audio/manifest.json`，常驻层照 protocol.py 在会话外预置，首轮照常发出；V4（可信集 (b) 不收常驻三件）下该用例红，登记为 N57-MUT-1。生产那 6 处预置不加 (a) 的 trust：加了 V4 又成等价变异，(b) 已足够且有用例守。

### [N59] `web_search` 单次条数与调研深度：人物介绍够用，人物剖析类不够
- 状态：**已裁决完毕**（① 条数 20 已施工，见 `[N59①]`；② 人裁决不做；③ 合并模式搁置、将来另立文）
- 关联（原表）：`pipeline/agent/web.py`（`SEARCH_DEFAULT_LIMIT` / `SEARCH_MAX_LIMIT`，Spec 4 §3.2 冻结面）；D29、Spec 15、Spec 4、Spec 13
- 原记录（活跃表原文）：① 条数默认/上限 20 已施工并复核通过（2026-10-06，人裁决与复修见 archive `[N59①]` 与 Spec 15 §13–§16）；② 人裁决不写调研策略文案；③ 两家合并模式搁置，终端取证与候选代价见 archive `[N59①]`。

### [D23②] 配音顺听人机摩擦力大
- 状态：**已解决**（M2a 落地：云端 Qwen3-TTS + SenseVoice/Whisper 仲裁入库（ADR-0017）、结构化打点机制就绪；S25 八份 spec 全部完工归档。agent 时代的顺听摩擦为后续 D50/D51/D53 另案处理）
- 注：（编号撞号：archive 内 2026-09-02 已有一条 [D23]「clips 检索及格 ≠ 可分派」——编号复用的历史遗留，本条是后登记者，故带 ② 后缀）
- 关联（原表）：`pipeline/tts.py`；四阶段工序卡, ADR-0006, ADR-0016, ADR-0017
- 原记录（活跃表原文）：云端 Qwen3-TTS + SenseVoice/Whisper 仲裁已入库（ADR-0017）；结构化打点机制就绪。2026-09-25 S25：一期 8 份 spec 全部完工并移入 `docs/dev/plans/archive/`

### [D55] 段落 3 画面落在便当戏之后的「金木撞见西尾挨打」：锚点 `S01E05 06:12` 是首稿凭空写的，便当戏实际在 04:31–04:41；模型后来查到并在草稿里改成 04:33，但没告诉人，改动没进定稿
- 状态：**已解决**（2026-10-08 夜规程部分施工：锚点只写查过的时间码、改了锚点/查询须在回复里逐条写旧→新；2026-10-09 人裁决：锚点机械核对不做——全分支关闭，无待办）
- 关联（原表）：`data/…/04-clips.json` 段 3（`channel:"anchor"`，直取锚点、不检索不评分）、`pipeline/check_script.py`、`pipeline/clips.py`（锚点不核对）、草稿/定稿双文件流程；D47、D54、判据 4、判据 8
- 原记录（活跃表原文）：证据：① 首稿 L70 写下 06:12 时本会话还没查过 S01E05 字幕（S01E05 的检索在 L117–L140）；② L141 模型查到「吃掉啊 依子好不容易做出来的」04:33、「我开动了」04:41，回复里只贴了配音改法；L145 起草稿锚点为 04:33（`_agent/draft-history` 10:43:50 起全部 04:33）；③ 会话进程 18:26 启动，D47（agent 可改定稿）18:59 才提交，进程不热加载，所以模型只能写草稿，人按回复手工同步定稿（L112–L113 人问「为什么不同步修改 02-script.md」）——锚点改动不在回复里，于是丢了；`02-diff.patch` 段 3 正是 `-04:33 / +06:12`。机器侧没有一道检查能拦：锚点通道不比对锚点处字幕与「查询 / 备选」。段 21 的「备选」恰是字幕原句（19:54），可以机械核对；段 3 的「查询」是描述句，核不了。方向（待定）：① 锚点核对——「查询 / 备选」里凡是字幕原句的，检查它的时间码是否落在 [锚点, 锚点+段时长] 内，不在就报；不是字幕原句的跳过、不定罪（判据 4）；② agent 改了配音以外的字段（锚点、查询）必须在回复里说出来（D47 的写稿卡 diff 已覆盖新会话，旧进程不覆盖）；③ 锚点须来自查过的字幕，查不到就不写锚点走检索。。**2026-10-08 夜施工**：方向 ③ 写进 `creative.md`（锚点只写查过的时间码，查不到写「无（字幕里查不到，待人看片）」走检索）；方向 ② 写进同节（改了锚点/查询/备选须在回复里逐条写「旧 → 新」）。方向 ① 对全部现存稿件只读实跑：可核对 73 条，窗口外 12 条（16%），抽看多为有意为之（画面取 A 处、台词引 B 处），且本条事故（段 3 的查询/备选都是描述句）根本抓不到——未施工，建议不做，交人定。。**人裁决（2026-10-09）**：锚点机械核对不做。

### [D61] ava 不能写番剧笔记：缺笔记只能出 scout 工单交 pi、人搬运；笔记三步流水线（写厚 → 零上下文对抗审查 → 终审回写）在 ava 里没有对应物
- 状态：**已解决**（2026-10-09：《罪恶王冠》实跑 46 条剧情 diff，54 条终审全裁决，回写后 180 条时间码引文机械复核全过）
- 关联（原表）：`pipeline/agent/tools.py`（无写 data/library/ 的工具）、`pipeline/scout.py`、`docs/dev/postmortems/workflow-history.md` 番剧笔记节；D59、D13、ADR-0028、ADR-0025、D47 / D48
- 原记录（活跃表原文）：人裁决：「必须要」。新工具 `write_note`（第 14 槽，ADR-0028）；`pipeline.notes_review`（机械厚度门槛 + 引用核销 + 每集一次零上下文 LLM 剧情 diff）；终审由 ava 回写。[spec](../plans/2026-10-09-calibration-and-notes-spec.md)

### [D62] 上下文只涨不缩：D28 取消轮数硬上限后，长会话以服务商 context-length 报错告终、会话实质变砖；没有压缩机制
- 状态：**已解决**（2026-10-10：手动 /compact、读数分母、双 system 修复、三探针实测完毕；自动压缩代码保留但人裁决关闭；spec「留人」项已全部施工或被裁决否决）
- 关联（原表）：`pipeline/agent/llm.py`（D56 只有读数无动作）、`pipeline/agent/session.py`；D28、D56、D54、ADR-0018
- 原记录（活跃表原文）：裁决：尾部预算改 token 制（≈0.15×可靠窗口 + ≤20 条次级约束）；可见形态 = 当场 notice + turn_end 带压缩字段；idea 会话同一套、重注入降级；PR2 手动 /compact 不等实测，PR3 自动双触发挂 CPA 代理三探针（usage 透传/缓存命中/上游形态）结果。[spec](../plans/2026-10-09-context-compaction-spec.md) **2026-10-09 补人裁决 ⑤**：读数要带分母（窗口表随 PR1），形式施工时出截图方案人选；起因 D64。

### [D64] 继续会话后会话头的上下文读数消失，要等新一回合跑完才出现——而 `--continue` 正是长会话的常态入口，最该看读数的时候看不到
- 状态：**已解决**（2026-10-09：恢复带读数 + 显眼度人选 B 头部徽章施工完；余项①分母随 D62 落地）
- 关联（原表）：`desktop/src/shared/convFold.ts::lastContextReading`（遇 `ready` 清零）、`pipeline/agent/protocol.py` 的 `ready` 帧（不带上次读数）；D56、D62（人裁决 ⑤ 读数要带分母）、D41
- 原记录（活跃表原文）：人原话：「我在期内会话比较难找到明确的 context token 数量」。上次的值一直在：每个 `turn_end` 记录都写了 `prompt_tokens`（D56），恢复时没读。**施工**：`session_log.last_context_reading` 取最后一个测到过读数的 `turn_end`（跳过回滚回合与两项皆缺的修复记录；`prompt_chars` 为 0 视同没测），`prepare_resume` 带出、`ready` 帧新增 `resume_prompt_tokens` / `resume_prompt_chars`（新会话为 null），桌面端在新回合跑完前显示「上下文 110.8k token（上次）」，悬停说明同步。伪恋 `2026-10-10-…` 那期真实记录核对：带回 110977，与对话脚注「110.8k」一致。用例：core 参数化 7 形态 + 回滚 + 真实协议进程 `--continue`；vitest 读数与后缀；变异 5 条全红。**余项**：① 分母并入 D62 裁决 ⑤；② 读数是灰色小字、不显眼——出 A 现状 / B 头部徽章 / C 输入框状态行三张真实窗口截图，**人选 B（2026-10-09）**：读数改为紧跟模式徽章的 `.ui-badge`，正文色、等宽数字（`style.css` 的 `.session-head-readout`；`ui.css` 冻结不动）。截图脚本 `docs/dev/plans/2026-10-09-context-readout-shots/`。

### [D67] D52 之前写入的存量会话日志里 tool 正文未脱敏、含受限字面量：这类会话今天**恢复即死**——每轮请求撞出网断言回滚，无客户端入口可修
- 状态：**已解决**（2026-10-10：prepare_resume 全 role 脱敏投影；终验通过——CLI 恢复真实董香二期发首轮请求成功、无回滚）
- 关联（原表）：`data/episodes/2026-09-21-东京喰种-雾岛董香人物志-二/session.jsonl` seq=288（红队实测：rebuild 后过 assert_egress_boundary 必拦）、D52 修复提交 `15d8d61`；D52, D65, Spec 16
- 原记录（活跃表原文）：红队实测：全量 grep 受限字面量在日志中共 3 处，其中 1 处在 tool 消息正文；该会话已无法恢复。D52 前的既有伤、非 D65 引入。**人裁决（2026-10-10）：① 采纳，带实测修正**——人直接跑实验：不 scrub 被拦、只 scrub tool 仍被拦、全 role scrub 通过；机理是受限字面量还在两条 user 角色历史规程注入（seq=282/292）里，可信集只含当前磁盘正文盖不住历史副本，故必须全 role。② 破坏 append-only 被否，③ 弃疗被否。[spec](../plans/archive/2026-10-10-resume-scrub-spec.md)。频率观察：目前仅确认 1 例。 **2026-10-10 施工**：prepare_resume 重建输出全 role 过 `scrub_restricted`（不动盘、幂等、保对象同一性）；测试 4 条全绿、变异 3 条全被杀、全量 2523 绿；真实日志只读回放复现「不 scrub 必拦 / 全 role scrub 通过」（连带改写 test_td1c 两条断言：历史副本的受限字面量恢复后即被投影为 [已脱敏]）。**终验通过（2026-10-10）**：CLI 恢复真实董香二期（重放 210 条），首轮真实请求成功发出、模型正确回忆上下文、无 turn_rollback、回合正常结束。


---

## 2026-10-08：N60 复核通过归档

### [N60] 桌面端「建期带入」host 侧一次性标记（`carried`）的不变量零用例守
- 状态：**已解决**（✅ 2026-10-08；施工 `f485764` TH-D42 ⑥～⑨ + C-2(b) + C-3 注记、`dee2fde` TH-D42 ⑩ + ⑥ 拒因收紧；定向复核 🟡 `eb0851c` → 收尾复跑 🟢）
- 关联：`desktop/src/host/service.ts`（`carried`、`convSend`、`convResume`、`createEpisode`、确认切仓处 `carried.clear()`）；Spec 18 §9.6 偏差 1、「独立评审」🔵 C-1 / C-2，D42；Spec 18 §10、「N60 定向复核」与「N60 收尾复跑」
- 原记录（活跃表原文）：问题「桌面端「建期带入」的 host 侧一次性标记（`carried`）有两条不变量零用例守：发送失败不消费、切仓清空」；推进「D42-C 自设变异 V3（发送前就消费）、V4（切仓不清标记）全量 vitest 432 仍绿，e2e 只有两版 TX-7 走建期且都是成功路径。今天代码读下来是对的：失败不消费、切仓清空、resume 成功即消费。补法：TH-D42 ⑥「marker true → 首发失败 → 再发仍 `SESSION_CONTINUE`」、⑦「marker true → 切仓 → 同名期首发 `SESSION_NEW`」，可顺带 ⑧ ep 的 resume 消费；顺手可把 `createEpisode` 在 `migrated=false` 时 `carried.delete(name)`（C-2(b)，只在期目录被 app 外删除后同名重建时可达）。不阻断，碰 desktop 时一起做」
- 活跃表最后状态：**定向复核 🟡 打回（2026-10-08，D42-C 评审 session；见 Spec 18「N60 定向复核」）**：V3～V7 全杀、注记与实现一致，但自设变异 V8（未带入时 `carried.clear()` 清掉所有期的标记）全量 vitest 存活——补一条「A 带入 → B 未带入 → A 首发仍 CONTINUE」用例（R-1），另顺手收紧 ⑥ 拒因断言（🔵 R-2）；补完只需复跑 V8 + V5。**此前**：已修·待定向复核（施工方 D42-B session；记录见 Spec 18 §10）：补 TH-D42 ⑥～⑨，V3 / V4 / V5 / V6 均由对应用例断言杀死；顺手采纳 C-2(b)、补 C-3 注记。复核不能是本 session（D42-C 评审 session 可做）。原级别：备忘·用例缺口（D42-C 评审副产物，2026-10-08）
- 评审：D42-C 评审 session 定向复核——⑥～⑨ 断言的都是 spawn 模板序列、⑥ 拒因实测为 ready 前退出（`E_SESSION`）、C-2(b) 不动带入路径、Spec 10 §2.1 / §3.1 注记与实现一致；V3～V7 全杀，自设 V8（未带入时 `carried.clear()`）存活 → 🟡 R-1。补 ⑩ 后收尾复跑：V8 由 ⑩ L907 杀死、V5（删行）由 ⑨ L893 杀死，md5 一致；vitest 437、tsc 绿。产品代码只有 C-2(b) 一行 `else { carried.delete(name) }`。

---

## 2026-10-08：D42 评审通过归档

### [D42] 选题会话（idea）与建期写 `01-topic.md` 交互断层
- 状态：**已解决**（Spec 18；施工 `d1baacf` PR1 core、`907d280` 杀手断言收紧、`a7501d2` PR2 desktop、`31f380a` PR3 回填；2026-10-08 D42-C 独立评审通过）
- 关联：`pipeline/agent/session_log.py`（`acquire_idea_lease`、`EpisodeLease.clear`、`plan_repairs`）、`pipeline/agent/session.py`（`SessionHost.log_dir`、`resume_idea`）、`pipeline/agent/protocol.py`、`pipeline/agent/cli.py`（`migrate_idea_session`、`new --from-idea`）、`desktop/src/host/service.ts`、`sessions.ts`、`spawner.ts`、`desktop/src/renderer/App.tsx`；Spec 10 §2.5, D27 spec（`archive/2026-09-21-ava-entry-idea-scope.md`；原误写 ADR-0018，D42-R2 余-1 更正）, ADR-0021, Spec 18；N60
- 原记录（活跃表原文）：问题「选题会话（`idea` scope）与建期写 `01-topic.md` 交互断层：左栏「选题」入口缺少联网工具且零写权限，聊定后点「＋ 新建一期」不继承对话上下文（提示「讨论不会带入本期，请在这里重述」），无法在选题会话内一气呵成建期并由模型写入 `01-topic.md`」；位置「`config/agent/tools.json`（`idea` 工具表）、`config/agent/scopes/idea.md`、`desktop/src/renderer/App.tsx:413-426`、`pipeline/agent/tools.py`」；推进「**现状与痛点**：① 桌面端左栏首行「选题」（`idea` scope）是最自然的选题入口，但 `tools.json` 中 `idea` 仅配 `read_artifact`/`list_episodes`/`read_status`/`search_notes`，无联网考据工具（`web_search`/`web_fetch`/`crawl`）且无写权限；② 人在「选题」会话聊透张力与锚点后，点右上角「＋ 新建一期」只会建空期并切入全新的 `creative` 会话（`App.tsx:424` 显示「选题会话的讨论不会带入本期；需要的要点请在这里重述」），迫使人手动复述或复制粘贴才能让 `creative` 模型调 `write_episode_file` 落盘 `01-topic.md`；③ 当前临时绕法：不进左栏「选题」，直接点「＋ 新建一期」进 `creative` 会话聊选题并写 `01-topic.md`。**2026-10-06 加注**：方案 (a) 的「idea 补联网三件」已被 D43（所有模式开放全部工具，Spec 17）覆盖，D42 完整 spec 只剩「建期时把选题会话记录带进新期」，且须在 D43 施工之后施工。**后续重构候选方向（待立 spec 拍板）**：(a) 选题转正继承——给 `idea` 补齐联网只读工具，且在 `idea` 下点「＋ 新建一期」（或新增受控立项工具 `propose_new_episode` 经人审卡一键建期+写 `01-topic.md`）时将当前 `idea` 会话历史迁移/挂载为新期的首场 `creative` 会话，消除上下文断层；(b) 入口合一——评估是否取消独立的无期 `idea` 会话，统一收敛到带期目录的 `01 选题`（`creative` scope）。」
- 活跃表最后状态：**已修·待评审（2026-10-08，D42-B）**：施工 `d1baacf` PR1 core、`907d280` 杀手断言收紧、`a7501d2` PR2 desktop、PR3 回填；pytest 2075 passed（6 红与基线同一集合，数据盘未挂载）、vitest 432、Playwright 115 passed / 2 skipped；MUT-D42-a～h 全杀并逐条复核为断言级失败、md5 还原一致；门禁③ 真会话冒烟过（idea 聊一轮 → 再进历史在 → `--from-idea` 带入 → 新期不重述直接写 01-topic.md，弹卡批准落盘）；人 2026-10-08 两处裁决：首发改 host 侧一次性标记（TG-10 冲突）、截图按现状。偏差 7 条见 Spec 18 §9.6。下一步：D42-C 独立评审（不能是施工 session）；打包版真机并入 ACC。**此前**：Spec 18 定向复审 🟢 可动工（2026-10-08，D42-R2）：8 条全部核销，无需人裁决；2 条非阻断残余（余-1 文首「触及冻结面」行与两处「相关」列的 ADR-0018 误指随 PR1 清理；余-2 plan_repairs 修法为 id 级满足的病态边界加注）。详见 Spec 18 文首「定向复审」。沿革：v0.1 → D42-R 🟡 3🟡+5🔵 → v0.2 作者修订（🟡-2 人裁决 (A) 顺手修 `plan_repairs`）→ D42-R2 🟢。下一步：D42-B 施工（另开 session）。完整 spec 已立：`plans/archive/2026-10-08-idea-session-migration-spec.md`（Spec 18，2026-10-08 立文；只剩「建期迁会话」一半——联网三件已被 D43 覆盖）（2026-10-06 人裁决选型稿：方案 (a)、联网三件含 `crawl`、同一时刻只留一个选题会话）
- 评审：✅ 通过（2026-10-08 D42-C，评审人未参与立文、v0.2 修订、D42-R / R2 与 D42-B 施工；详见 Spec 18 文首「独立评审」）。§3.1–§3.3 逐条读码属实（idea `ep_dir` 恒 None、日志目录解耦；data/ 不可达拒启、`_idea` 缺失自建、租约失败显式报错；先建期后迁移、临时文件 + fsync + `os.replace`、清空失败退 5 + 手动指引、marker 恰一行；`plan_repairs` id 级满足；桌面端先结束选题会话、结束不了不建期、带入后清缓冲、idea-note 两态）；偏差 1（host 侧一次性标记）逐路径攻过，无误接别的会话段；全量复跑 pytest 2075 passed（6 红即数据盘未挂载集合）、vitest 432、tsc 绿、Playwright 115 passed / 2 skipped；变异亲跑 9 条——MUT-D42-a / e / f / g 与自设 V1、V2b 断言级杀死，自设 V2 崩在异常不算杀，desktop 自设 V3 / V4 存活（登记 N60）；冒烟：读 D42-B 帧证据 35 帧逐帧一致，并在 HEAD 上用真 LLM 独立复跑（新期记录与迁移前 `_idea` 逐字节相等、迁移后再进 idea 为全新会话、新期带入后弹卡写 01-topic.md）。
- 发现（无阻断，🔵）：C-1 偏差 1 的「失败不消费 / 切仓清空」零用例守（N60）；C-2 标记两处边界（发送确认超时但已送达 / `migrated=false` 不删旧标记）；C-3 Spec 10 §2.1 第 2 条与 §3.1 方法表未随 D42 注记；C-4 建期前无条件结束选题回合（符合 §3.3 ③，ACC 观察）。打包版真机（Spec 10 门禁 12 更新版）并入 ACC。

---

## 2026-10-08：D43 评审通过归档

### [D43] 工具按模式（scope）授权 → 所有模式开放全部工具
- 状态：**已解决**（Spec 17 / ADR-0027；施工 `45fc26b` PR1 core+测试、`e03e373` PR2 文档；2026-10-08 D43-C 独立评审通过）
- 关联：`config/agent/tools.json`、`pipeline/agent/tools.py`（`tool_names`、`build_tool_schemas`、`execute_tool`、`write_episode_file`、`validate_pipeline_command`、`NEEDS_EPISODE_TOOLS`）、`pipeline/agent/memory.py::apply_op`、`pipeline/agent/session.py::review_tool_call`、`config/agent/scopes/*.md`；Spec 17（`plans/archive/2026-10-06-all-tools-all-scopes-spec.md`）、ADR-0027（已通过）、ADR-0021、ADR-0023、ADR-0025、Spec 10 §2.5、D42
- 原记录（活跃表原文）：问题「工具按模式（scope）授权：各模式只见部分工具，且 `write_episode_file` / `write_memory` / `run_pipeline` 在实现里再按模式拒绝一次。人裁决改为**所有模式开放全部工具**」；位置「`config/agent/tools.json`、`pipeline/agent/tools.py`（`tool_names_for_scope`、`execute_tool`、`write_episode_file`、`validate_pipeline_command`）、`pipeline/agent/memory.py::apply_op`、`pipeline/agent/session.py::review_tool_call`、`config/agent/scopes/*.md`」；备注「**人原话（2026-10-06）**：「我建议不论什么模式，都开放所有 tool，具体原因我先不展开，这是我深思后的考虑」；同日确认 ① 实现里的模式检查一起去掉 ② 人审卡全部照旧。设计见 `plans/archive/2026-10-06-all-tools-all-scopes-spec.md`（Spec 17）：`tools.json` 收单表、删越 scope 拒绝与三处实现内检查、`run_pipeline` 合表、与模式无关的护栏全留、无期会话统一报「先建期」。§8 Q1–Q3 人 2026-10-06 裁决全部按建议（单表 / idea 下 `run_pipeline` 报先建期 / pipeline 不加记忆注入）。**2026-10-06 D43-R 红队一轮 🟡 修订后复审**：方向与机制主干成立（无第四处模式闸、护栏与模式无关、R1 探针 14 例全拦、R2/R3/R5 成立），阻塞项 5🟡——🟡-1 §3.4 放行 idea `acquire_propose` 与抓取卡链矛盾（批准后必报「先建期」，**需人裁决**三选一）、🟡-2 TA-6 杀不死 MUT-A8（弹卡≠落盘）、🟡-3 review 层越 scope 拒绝残留无杀手、🟡-4 §7 修订面漏 CHEATSHEET/D27 spec/Spec 9/Spec 1/impl spec §2.3 五处、🟡-5 shipped 变异矩阵 M20/M24/M29 锚点被拆除且 MUT-A1~A8 未要求登记；另 8🔵（含原型实跑失败清单 ≥14 份文件、合表语义写死、R4 批准记录落点实测等）。详见 Spec 17 文首。**2026-10-07**：人裁决 🟡-1 选 (A)（idea 下 `acquire_propose` 也报「先建期」）；人指定由立文人做作者修订，Spec 17 v0.2：12 条采纳、🟡-5 部分采纳（M24 退役、M29 改锚、M20 改锚不退役——「未注册名字拒绝」仍是现役护栏）。**2026-10-07 D43-R2 定向复审 🟢 可动工**（由 D43-R 红队 session 做）：13 条全部核销，🟡-5 中「M20 改锚不退役」的理由成立；原型实测 MUT-A8～A11 都被指定用例杀死；§3.4 拦截无缺口。另 6🔵 由施工吸收：R2-1 T17 不变量与 Q3 冲突，须按 Q3 退役并换等强断言，不许往 `memory.scopes` 加 pipeline；R2-2 §7 再补三处；R2-3 TA-1/MUT-A1 改在请求层；R2-4 T13② 补期目录；R2-5 §3.4 补两个边界；R2-6 先建期 reject 排在 dry-run 之前。人 2026-10-07 确认 🟢。下一步：D43-B 施工（人定 2026-10-08；提示词见桌面提示词文件「D43-B」块）→ D43-C 评审（红队 session 可做，不能是施工方）。**先于 D42-B 施工**（D42 方案 (a) 的「idea 补联网工具」被本条覆盖）」
- 评审：✅ 通过（2026-10-08 D43-C，评审人未参与立文、D43-R / D43-R2 两轮审查与 D43-B 施工；详见 Spec 17 文首「独立评审」）。① §3.1–§3.5 逐条读码核对：单表恰为注册集、反向分叉检查与 B3-r6 同点同形态、「先建期」reject 排在未注册之后 dry-run 之前（R2-6）、NEEDS_EPISODE_TOOLS 恰 4 个且两层共用同一常量与文案、`run_pipeline` 函数本体不拦、合表两条语义成立、`memory.scopes` 未加 pipeline；6🔵（R2-1～R2-6）全部核销。② 变异亲跑 5 条：MUT-A1 / A8 / A9 / A10 全杀、指定杀手逐条在红单内、无中止轮；自设矩阵外变异（PIPELINE_MODULES × ASSET_COMMANDS 人为重名）被 TK-9 杀死，还原 md5 对拍一致。③ 全量 pytest 复跑 `6 failed, 2060 passed`，6 红与基线同一集合（数据盘未挂载），无新红。④ 改写对照抽 9 行全部属实且有牙（M20 先建期版与 TK-4 均经变异实证）。⑤ 文档修订面抽 ≥5 处（三份 ADR、CHEATSHEET、Spec 7 四处、acquire-propose / web-fetch-links / impl spec / Spec 10），注记在位且措辞与现状一致。⑥ 门禁③冒烟帧证据（scratchpad `d43b/smoke-frames-final.jsonl`）逐帧核对：web_search ok=true、两个需期工具 ok=false 且文案逐字相符、受限串整轮 blocked（llm_calls=0）。⑦ diff 边界：PR1 31 文件全在 §10 PR1 范围、PR2 18 文件全为 docs/。
- 发现（无阻断，🔵）：C-1 §10 PR1 文件清单未列 `llm.py` / `jobs.py`（签名删除的必然调用点，§11 已如实记录；建议今后清单注明「签名变更的调用点随行」），见 Spec 17 文首发现表。

---

## 2026-10-06：桌面端可用性收口（续）

### [N59①] web_search 条数默认 / 上限改为 20（N59 的 ②③ 仍在活跃表：② 不做，③ 搁置）
- 状态：**已解决**（施工 `ae0b142`；D29-B 打回一处，复修 `a1125fa`；2026-10-06 定向复核 🟢 通过；仅 ① 部分）
- 关联：`pipeline/agent/web.py`（`SEARCH_DEFAULT_LIMIT` / `SEARCH_MAX_LIMIT` / `_Provider.max_count`）、`pipeline/agent/tools.py`（`web_search` 的 `limit` 描述）；Spec 15 §13 / §14 / §15 / §16，Spec 4 §3.2 修订注记
- 原记录：D29 门禁 7 人审「5 条远远不够；人物介绍还可以，人物剖析有问题」→ 人 2026-10-06 裁决默认与上限都改为 20，调研策略交给模型、不写进 harness。
- 取证（2026-10-06 终端，真实配置、limit=10、两家各单独查）：「一色彩羽 人物分析 性格 成长」「春物 一色彩羽 角色剖析 长评」两条查询两家全部 `truncated=true`；10 条里约一半仍是百科（维基 / 百度 / 游民 / bangumi 角色页），剖析类来源（豆瓣长评、bgm 日志、B站专栏、巴哈、个人博客、reddit 分析帖、知乎话题）只占另一半；查询词带「长评 / 剖析」时评论类明显上浮；两家结果重叠不到一半（如 B站专栏只出现在 Tavily），而现设计主家答了就不问备家。
- 候选方向与代价（③ 搁置的决策依据）：① 提高 `SEARCH_MAX_LIMIT`（每条 snippet 500 字，20 条一次约 1 万字进上下文且每轮重发）——已做；② 调研策略写进 scope / runbook——人裁决不写（原话见 Spec 15 §13）；③ 「两家都查、合并去重」——推翻 Spec 15「每家至多 1 次、不并发」与省额度取舍，Tavily 用量随之翻倍（见 N58）。
- 评审：D29-B 打回 🟡 B-1（默认 limit=20 时请求 Tavily `max_results = 21`，超出文档范围 `0 <= x <= 20`），另 🔵 B-2（带内错误先截后脱敏）、🔵 B-3（用例补丁打全局 `os.environ.get`，junit 下假绿）。复修后定向复核 ✅：tavily 单次上限 20、截住时以「返回数 == 请求数」判 `truncated`，真网请求体 `max_results = 20`；B-2 改为先脱敏后截断；B-3 只替换 `web` 模块的 `os`。6 条变异在 junit 口径下全杀（2 条自设），全量 pytest 2061 passed。遗留 🔵 C-1：T-P16 `exact` 腿删了一条长度断言，下次顺手补（Spec 15 §16）。

### [D29] web_search 默认端点（DuckDuckGo html）被盾，工具实际不可用
- 状态：**已解决**（Spec 15；施工 `4c7059b` PR1、`0a50d0e` PR2、`36fd369` PR3、`4184335` 门禁记录；2026-10-06 D29-B 独立评审通过）
- 关联：`config/agent/web.json`、`pipeline/agent/web.py::_SearchResultParser`；Spec 4, Spec 13, D28；Spec 15（`plans/archive/2026-09-29-web-search-provider-spec.md`）、N50、N58、N59
- 原记录（活跃表原文）：状态「**选型已拍板（2026-09-29：Exa 主 + Tavily 备）；Spec 15 v0.3（2026-10-06 作者修订，回应 D29-R2 的 4🟡 + 5🔵；人同日裁决 R2-9 / N50 接受、Q5 改为「替换」）；**同日定向复核 D29-R3 🟢 可动工**（2🔵 施工时顺手改）→ 施工 D29-A（PR0 人注册 Tavily key，终端与钥匙串两处放，变量名须以 `TAVILY_` 开头）**；**2026-10-06 D29-A 施工中：PR0 ✅（key 两处已放；`exa_mcp` / `tavily` 真实响应入 fixture）、PR1 ✅ `4c7059b`（core provider 链 + 默认链；本机 `web.local.json` 的 search 段经人当场确认已替换为新链；偏差 8 条见 Spec 15 §12，含人裁决的 `exa_mcp` 固定 `objective` 与 `exa_api` 暂不注册）、PR2 ✅ `0a50d0e`（description；终端门禁 4、5 真机过；N58 用量观察）、PR3 ✅ `36fd369`（桌面端从钥匙串注入 web 检索密钥；Spec 10 §2.9 已加修订记录；打包版上人手跑桌面端门禁 4、5 均过：主家答 `exa_mcp`，主家端点指错后由 `tavily` 答）。**已修·待评审（D29-B）**；门禁 7 人审 2026-10-06：人物介绍类「还行」、人物剖析类不足（5 条远远不够），后者登记 N59；评审通过后跑 S13-G8**」；一句话「web_search 默认端点（DuckDuckGo html）被盾，工具实际不可用」；推进 / 线索：2026-09-26 实测（Spec 13 红队一轮 🟡-1 牵出）：GET `https://html.duckduckgo.com/html/` 返回 **202 + anomaly/challenge 页**、零结果，`search_web` 抛「搜索解析为空」。影响：升级链第一级探测失效，asset/creative 联网研究只能直抓已知 URL 或站点 API（Spec 13 门禁 8 起手已改走公开 API）。注意：解析器钉死 DDG DOM（`result__a`/`result__snippet`），换 provider = 端点 + 解析器一起换，不是改一行配置。**方向（2026-09-26 人原话）「参考 pi 的 web search 实现，不要在意国内网络环境、我有代理」**：即照 pi 的多 provider 路线做（provider 可插拔、配置驱动），不因墙内直连迁就选型（**此解读 2026-09-26 人确认属实**）。⚠️ **本行曾误记为「弃 HTML 爬取改走搜索 API——Brave 主力 + 博查 fallback」，2026-09-26 由人更正：provider 从未拍板**（此处已改正，勿再沿用）。可确定的部分：HTML 端点（DDG）已实测被盾；**360 方案（同为 HTML 爬取）已被人否并 revert**（`d4e8bad` → `aeef32d`），与否决理由（2026-09-26 人原话）——「**用 360 搜索不如不用**」，即**检索质量差到不如不搜**：否的是**质量**不是网络可达性；key 走 `web.json` 已有的 `api_key_env`；`_SearchResultParser` 随之退役。**provider 选型由 D29 施工 session 先出调研对比（含 pi 支持清单、各家 key/配额、检索质量、代理下可用性）再报人拍板；首要判据是检索质量，国内可达性不作判据（有代理）。** **另为 Spec 13 门禁 8 复跑前置**（2026-09-26 验收拍板：先修 D29 再复跑）；施工时点按 2026-09-26 人拍板**顺延到二期 21 个 session 收官之后**（不在二期施工范围） **2026-09-29 调研（只读，未改代码，待人拍板）**：① **pi 实际路线**——`~/.pi/agent/web-search.json` 只有 `provider:"all"` 与 summaryModel，**没配任何 key**，靠 `pi-web-access` 0.33.0 的 auto 链（SearXNG→Exa→OpenAI→Brave→Parallel→Tavily→…共 20+ provider 可插拔，另含 DDG 无 key 兜底），实际落到**无 key 的 Exa MCP**（`POST https://mcp.exa.ai/mcp?tools=web_search_exa`，JSON-RPC over SSE，头 `x-exa-source`，429 时提示加 key）。② **代理下实测**（本机 127.0.0.1:7897，3 条查询各 6 结果）：「一色彩羽 角色介绍」得 Gamersky/anibase/搜狗百科/百度百科/Hikarinagi/博客；英文「Iroha Isshiki」得 MAL/Fandom/LNDB；「楪祈 罪恶王冠」得 中文百科全书/动漫大风堂/番组百科/百度百科/维基/萌娘——**中日英三类都给出角色资料页，质量明显好于 DDG 之前的零结果与 360**，可直接喂 Spec 13 门禁 8。③ 各家额度（WebSearch 2026-09 查，价格以官网为准）：Exa 带 key 注册送 $10、每月 1 日重置为 $10（约 1400 次，$7/千次），无 key MCP 有限流；Tavily 每月 1000 credits 免费、无需信用卡（basic=1 credit）；**Brave 2026-02 已取消免费档**（$5 预付额度约 1000 次，绑卡后转计费）；博查/Serper 等未实测。④ **建议**：主力 = Exa（先用无 key MCP，遇 429 再申请 key 走 `api_key_env`），fallback = Tavily（免费 1000/月）；provider 做成配置驱动（`web.json` 加 `provider` 字段 + 每家一个 ~30 行的适配器，返回统一 `{title,url,snippet}`），`_SearchResultParser` 退役。风险：无 key MCP 是 pi 借用的公共入口，无 SLA，429/下线后必须有第二 provider；结果含正文摘录 ~1–2k 字/条，需截断防占上下文。 **2026-10-06 Spec 15 红队一轮（D29-R）：🟡 修订后复审**——5🟡（桌面端会话拿不到 `TAVILY_API_KEY`；`urllib` 跟随 POST 302 时转发 `Authorization`/`x-api-key`，本机探针已证；PR1/PR2 顺序与 Q5 矛盾会打断 web_fetch；删 `WebConfig.api_key` 打断 fetch 脱敏；Q3 与「免 key 入口被收紧」冲突需人澄清）+ 6🔵，详见 Spec 15 文首裁决表。 **2026-10-06 Spec 15 定向复审（D29-R2）：🟡 再修订**——R2-1 本机 `web.local.json` 整份取代 `web.json`，按 v0.2「删 search 段」迁移后 `load_web_config → None`（scratchpad 探针已证），🟡-3 原指控复发；R2-2 web 配置可指名 LLM 或其他 `*_TOKEN` 密钥外发给 Tavily / Exa；R2-3 TH-W2 的 `ready.llm` 在 host 测试里是脚本写死的，M18 存活；R2-4 T5a/T5b/T17 依赖旧 search 配置，须在 PR1 等强改写（Spec 16 的 MUT-D7 杀手）。详见 Spec 15「定向复审」。
- 评审：✅ 通过（2026-10-06 D29-B，评审人未参与 Spec 15 的写作、任何一轮审查与施工；详见 Spec 15 §14）。① 全量 `uv run pytest` 2059 passed；desktop `tsc` 过、`vitest` 422 条全绿；② 变异 10 条亲跑（M5 / M13 / M10 与自设 V1～V5、desktop D1 / D2），全部由指定用例的断言杀死，md5 一致；③ 契约四键与 Spec 4 §3.2 一致，`tools.json` / scopes / runbook / `llm.py` / `session.py` 的 diff 为空，`_guard_url` / `_scrub` / `assert_egress_boundary` 一行没动；④ 终端真网复跑：主路径 `exa_mcp` 20 条带摘录；主家端点指错后由 `tavily` 答；key 未设 → 「tavily: 需要环境变量 TAVILY_API_KEY（…两处放法）」，主家正常时不受影响；key 错误 → 401 直接失败并列出此前尝试；⑤ 凭据审计：diff、提交信息、fixture、已跟踪文件对真 key 都是 0 次命中。桌面端门禁 4、5 没有重跑，依据是施工方存证与对其 `session.jsonl` 的被动核对。
- 发现：🟡 B-1 记在 N59（Tavily 请求 21 超文档上限 20，属 N59 改动，不影响本条归档）；🔵 B-2 带内错误先截断后脱敏，key 跨 200 字边界时前半段会漏（现无暴露面：带内错误只来自免 key 的 exa_mcp）；🔵 B-3 `test_tp9_web_key_env_names_reads_no_values` 替换了进程全局的 `os.environ.get`，加 `--junitxml` 跑时杀死会变成 pytest 内部崩溃。两条 🔵 见 Spec 15 §14，顺手修即可。

### [D30] 期目录处在 03 / 03.5 时会话第一轮即 [BLOCKED]（自家 runbook 字面路径撞出网断言）
- 状态：**已解决**（Spec 16 / ADR-0026；施工 `d5d94bf`；2026-10-06 D30-C 独立评审通过）
- 关联：`docs/runbook/03.5-voice-check.md`、`pipeline/agent/tools.py:54-59`、`pipeline/agent/llm.py:255`；Spec 4, Spec 13；Spec 16、ADR-0026、N52、N53、N55、N56、N57
- 原记录（活跃表原文）：期目录处在 step=03.5 时任何会话第一轮即 [BLOCKED]：runbook 文本含字面 `03-audio/manifest.json` 撞受限出网子串断言——2026-09-26 Spec 13 门禁 8 冒烟复现（绑定 step=03.5 期目录的会话必炸，payload 含该 runbook 文本即触发 `RESTRICTED_EGRESS_PATTERNS` 子串匹配）。候选：① runbook 改写避开字面路径（回避式不治本，别的文档会再撞）；② egress 断言收窄匹配语义（治本，动 Spec 4 冻结面，需独立 spec/ADR）；③ 维持登记人工绕行。验收评审倾向 ② **2026-10-06 D30-A 复现（临时仓库副本、假 LLM 端点、零出网）**：step=03.5 与 **step=03** 两期首轮都 `turn_finished{stopped:"blocked",llm_calls:0}`；逐条断言定位到 origin=`injection` 的工序层注入消息，命中 `docs/runbook/03.5-voice-check.md:7` 与 **`docs/runbook/03-tts.md:16`**（原登记只有 03.5）。注入面全扫：四条模式只有 `03-audio/manifest.json` 出现、只在这两份 runbook。附带发现见 N52（桌面端看不到拦截原因）、N53（作业输出尾巴带路径，未实测）。方案对比与测试/变异清单见 Spec 16。
- 评审：✅ 通过（2026-10-06 D30-C，评审人为 Spec 16 的一轮红队兼定向复审人，未参与修订与施工）。① 退回修复亲跑 TD-1，两期都红在命中串 `'03-audio/manifest.json'`，还原后 md5 一致、转绿；用 `git archive` 取修前 / 修后两份临时仓库副本，协议子进程 + 只监听 127.0.0.1 的假 LLM 真会话复跑：D30-S03 / D30-S035 修前 `blocked`、0 个请求，修后 `done`、恰 1 个请求，请求体含对应 runbook 原文，`prompt_chars` 9192 / 14031 与 D30-A 一致；全量 `2003 passed`。② D30-R 最坏样例在修后代码上逐条实跑：该拦的全拦，读域过滤 13 例全部符合预期。③ 自设变异 4 条：V1（重叠判定）、V3（不按 resolve 后路径判）、V6（首轮注入不进 (a)）分别被 TD-4、TD-5、TD-1e 以断言杀死；V4（(b) 不收常驻三件）存活，登记 N57。④ 文档面齐全；⑤ diff 边界合规。详见 Spec 16 §13。
- 发现（无阻断，🔵）：(a) 生产入口的常驻层只靠 (b)，零用例守（N57）；(b) Spec 16 §5.3「规程原文逐字抄进工具参数会被豁免」与实测不符：工具参数在请求体里转义了两次，实际被拦，方向更严，记在 Spec 16 §13。

### [D37] 原生确认框未经人手点击即记为「批准」
- 状态：**已解决**（定性 `dd3742d`，人裁决方案 (b) `288d1c5`，施工 `b83c692`、`86df0fb`；2026-10-06 D37-C 独立评审通过）
- 关联：`desktop/src/main/index.ts`（`dialog.showMessageBox` + `signal` 的 resolve 语义）、`src/main/confirm.ts` 队列；Spec 10 §2.4 第 5 层、H-1/H-2、门禁 4 与门禁 12
- 原记录（活跃表原文，含定性与施工回填）：2026-09-27 M9 打包版实测**两次**：① `#3` 抓取卡——19:49:36 dump 到 `AXSheet`（正文正确），19:49:37.6 `approvals.jsonl` 已记 `decision: y`，全程我只按过卡片按钮、没碰确认框；② `#7` 抓取卡——确认框正文已读（与卡片逐字一致），我发出的一次真实鼠标点击落在**非 ava 的前台应用**上，随后决策仍记为 `y`。两次的共同点是「确认框消失 + 决策=批准」，但触发事件未确证（候选解释：`signal` 被 abort 时 Electron 以 `response: 0`（=批准按钮）resolve → fail-open）。**必须由人在机器前、无自动化介入地手点一次复现定性**；若成立，则 browser/抓取卡的「第二道闸」形同虚设。**2026-09-27 代码侧排查（只读 + scratchpad 探针，Electron 44.4.5，未改代码）**——实测事实：① 链路只在 `showMessageBox` 返回 `response===0` 时写 `y`（`main/index.ts` show → `confirm.ts` broker → `host/confirm.ts` → `sessions.answer` 过 `needsNativeConfirm` 后才写 `answer`）；② broker 只在 `hostExited()` 时 abort，abort 后 `active!==ac` → **不回任何 confirm-result**；③ 探针（同 options、挂父窗口）：`signal` abort → `response=1`（取消）；父窗口 `close()` → `1`；`app.hide()` 失焦 3 s 内不 resolve、之后 abort → `1`；父窗口 `destroy()` → **永不 resolve**（非 fail-open，但 host 的 `await confirm` 无超时 → 该卡 `answering` 永久占用、再答 E_BUSY，另一个缺陷，待登记）。**候选解释「abort 以 0 resolve」被实测否定**。键盘：Escape/Space 实测 → 取消；Return **结论不定**（1 次 `response=0`、焦点确认在探针窗口的 2 次均不 resolve；另 3 次 System Events 按键落到了其他前台应用，探针作废——键盘探针会误伤前台应用，**不再自动化**）。与 spec 的对照：§2.4 第 5 层要求「默认与取消按钮都是『取消』」，若 Return 能触发「批准」即违约；§2.10 退出确认同款写法，须一并复核。下一步＝人手复现配方（见施工提示词「收尾批进度」D37 行），定性后再修 **2026-10-06 人手定性（D37-A；打包版 HEAD `239f51f`、`desktopDirty=false`，Electron 44.4.5，macOS Darwin 27.0；临时仓库副本 scratchpad `d37-repo`，期 `D37-TEST`；LLM 走 `agent.local.json` 的本地代理；全部点击与按键由人完成，Claude 只读 `session.jsonl` / `_agent/approvals.jsonl` 与 CGWindowList，零键鼠事件）**——**实测事实**（时间为本地 UTC+8）：① 全屏下点抓取卡「批准」→ 确认框弹出（CGWindowList：260×298 独立窗口，几何中心与父窗口重合）；人切到其他应用（终端）回消息、再切回 → 框仍在、无新行；② 人先点框内文字再按 Return → 文字被全选、框仍在、无新行（焦点落文本区，**不计为有效 Return 样本**）；③ 鼠标点「取消」→ 框消失、卡仍在、无新行；④ 再点卡片「批准」、框弹出后不点任何处直接按一次 Return（AVA 在前台）→ **无任何反应**，框仍在、无新行；⑤ 同一框按 Esc → 取消、无新行；⑥ §2.10 退出确认：有 1 张未答卡时 Cmd+Q → 框弹出 → Return → **无反应、未退出**；鼠标点「取消」→ 未退出（PID 存活）；⑦ 对照组：卡片「批准」→ 框内鼠标点「批准」→ 15:56:07.682 `acquire_fetch #1 … y`；⑧ 窗口模式（退出全屏）复测：框形态与全屏相同（人目视为屏幕正中的独立小窗，**未用 AX 取证是否 sheet**——本 session 禁止触碰确认框的自动化），Return 无反应、Esc 取消。`_agent/approvals.jsonl` 全文 5 行：15:43:21 propose y、15:56:07 fetch #1 y（⑦ 对照）、16:03:20 propose y、**16:03:24 fetch #2 y（卡开后 3.2 s；人自述是误以为要一路点批准、用鼠标点了框内「批准」，属人手批准，不计复现）**、16:04:19 propose y；其余所有非人手点「批准」的路径（等待 30 s、切走切回、Return、Esc、鼠标取消）**零写入**。**机理判断（猜测，未证实）**：`defaultId: 1` 在本机没有把 Return 绑到「取消」——Return 不绑任何按钮，所以既不放行也不取消；09-27 的两次 y 都发生在有自动化介入时（AX dump 到 `AXSheet`、System Events 按键/鼠标事件落点不确定），而今天的框人目视为居中窗口、与当时记录的 `AXSheet` 是否同一呈现未确证；无自动化介入的全部样本均未复现。**修复方向（交人裁决，D37-B 据此施工）**：(a) 查清 Return 为何不落到 `defaultId` 并修成 Return = 取消（两处框一致，打包版人手复验）；(b) 接受「Return 无效、Esc = 取消、只有鼠标批准」为安全等价物，改 Spec 10 §2.4 第 5 层 / §2.10 措辞；D40 不受影响照修。09-27 的未解释 y 不再追（复现需重新引入自动化，违反第 5 条纪律），在 D37-C 评审中登记为残余风险。**顺带**：N44 Dock 点图标 → 窗口回前台并可直接输入（人确认）；输入法：拼音选词上屏后再按 Enter 正常发送，「组字中按 Enter」未单独测；发出内容带尾随换行（见 N51）。 **D37-B 施工（2026-10-06，与 D40 同批）**：按钮 / `defaultId` / `cancelId` 不变，抽到 `main/confirm.ts`（`approveBoxOptions` / `quitBoxOptions` / `APPROVE_BUTTON`）；对话框抛异常改回 `ok:false`（此前只撤状态不回复，host 的 `await confirm` 会永久挂起）；Spec 10 §2.4 第 5 层、§2.10 第 4 步改措辞 + §2.10 前加修订记录。新增 TH-22b（两处按钮语义 + 静态核对 `index.ts` 只经构造函数取选项）、TH-22c（非人手路径一律 ok:false）、TH-18b（D40 端到端）。变异 6/6 KILLED（M1 windowGone 不回包、M2 异常分支不回复、M3 抓取框 defaultId=0、M4 退出框 cancelId=0、M5 closed 不调 windowGone、M6 windowGone 不 abort），还原 md5 一致。验证：vitest 407 passed、tsc 通过、playwright 115 passed / 2 skipped；`release-build` @ `b83c692`（`desktopDirty=false`）+ `vitest run e2e-packaged` 11/11。**未实测**：打包版上真实 `destroy()` 窗口的路径（无法人手触发；以 broker 单测 + host 端到端代替）；打包版人手复验（Return / Esc / 鼠标取消 / 鼠标批准 / 退出框 Return）留给 D37-C 当场做。
- 评审：✅ 通过（2026-10-06 D37-C 独立评审，与 D37-B 施工方不共用上下文）。① **打包版人手复验**：`release-build` @ `a2da050`（`desktopDirty=false`，`e2e-packaged` 11/11）；临时仓库副本（scratchpad `d37c-repo`，期 `D37C-TEST`，素材模式）；全部点击与按键由人完成，Claude 只被动读 `_agent/approvals.jsonl` 与 `session.jsonl`，零键鼠事件。逐步（本地 UTC+8）：17:45:18 人批准提案卡（`acquire_propose y`）→ 抓取卡 #1 打开；(1) 卡片「批准」→ 框弹出后不点任何处按 Return → 无反应、框在、卡在、零写入（17:45:49 核）；(2) 同一框按 Esc → 框消失、卡在、零写入；(3) 再批准 → 鼠标点「取消」→ 界面「E_STALE：已在确认框取消」、卡在、零写入（17:46:36 核）；(4) 再批准 → 鼠标点「批准」→ 17:46:52.517 恰一条 `acquire_fetch #1 … y`（latency 93.9 s），卡关闭；(5) 抓取卡 #2 未答时 Cmd+Q → 退出框按 Return → 无反应、AVA 主进程存活；(6) 鼠标点「取消」→ 不退出（17:47:43 进程存活，approvals 全文 2 行）；(7)（D37-A 未测，本次补）Cmd+Q → 退出框按 Esc → 框消失、不退出；(8) Cmd+Q → 鼠标点「退出」→ 17:48:07 卡 #2 `voided(interrupted)`、`turn_end`，进程退出。八步全部与 Spec 10 §2.4 第 5 层 / §2.10 修订语义一致。② **用例逐条读**：TH-22b 钉住两处 `buttons` / `defaultId` / `cancelId` 与 `APPROVE_BUTTON`，并静态核对 `main/index.ts` 两处只经构造函数取选项、两处 `r.response === APPROVE_BUTTON`、`closed` 后执行 `windowGone()`；TH-22c 在 broker 层断言 `ok:false`（show 回 false / 抛异常 / `windowGone` 对已打开与排队中的各回一次且迟到的 true 不回 / 之后能重答 / 空闲不回）；TH-18b 用真实 host 与 main 两端 broker 端到端断言 `E_BUSY` → `windowGone` → `E_STALE`、answer 零行、卡仍打开、重答恰 1 行。断言的都是 ok:false、零写入与卡仍打开，不是「没报错」；抛异常路径的「零写入、卡仍打开」由 host 共享的 `if (!ok) fail("E_STALE")` 分支承担（TH-18「桩返回 false」守，见 V4）。③ **变异 5 条亲跑，全部自行设计**（非施工方 M1–M6 的复跑；`PYTHONDONTWRITEBYTECODE=1 npx vitest run <文件>`）：V1 `windowGone` 只回已打开的、漏回排队中的 → TH-22c windowGone 用例 `expected [['boot1:1',false]] to deeply equal [[…],…(1)]`；V2 对话框异常分支改回 true（fail-open）→ TH-22c 抛异常用例 `expected [['boot1:1',true]] to deeply equal [['boot1:1',false]]`；V3 去掉 resolve 分支的迟到守卫 → TH-22c「迟到的 true 不回」与 TH-22「host 退出零回复」两条断言；V4 host 忽略 `ok:false`（`if (false && !ok)`）→ TH-18 三条 + TH-18b `expected undefined to be 'E_STALE'`；V5 退出框 `defaultId: 0` → TH-22b 退出框用例 `expected +0 to be 1`；均还原 md5 一致（`confirm.ts` 7f5a41db…、`sessions.ts` c5122332…），`git status` 干净。全量 `npx vitest run` 410 passed、`tsc` 通过。④ **diff 边界**：`b83c692` 只动 `main/confirm.ts`、`main/index.ts`、`tests/host/confirm.test.ts`、`tests/host/sessions.test.ts` 与 Spec 10 / README / plans / issues 文档，合规。
- 发现（无阻断）：🔵-1 退出确认框的 `showMessageBox(...).then` 无 reject 分支，且同样挂在主窗口上——抛异常或父窗口 destroy 时 `quitPhase` 永久停在 `confirming`，此后 Cmd+Q 全被拦、只能强退；方向安全（不会误退），但与「非人手路径一律回落取消」口径不一致，登记 **N54**。🔵-2 `windowGone` 的真实 `destroy()` 路径在打包版上无法人手触发，以 broker 单测 + TH-18b 代替（施工已如实申报），接受。**残余风险（登记，不追）**：2026-09-27 两次未解释的 y 都发生在有自动化介入时；无自动化下 D37-A 与本次的全部非人手结束样本（等待、切走切回、Return、Esc、鼠标取消）零写入；复现需重新引入自动化，违反施工纪律第 5 条。

### [D40] 原生确认框所属父窗口被 destroy 时卡片永久卡在 answering
- 状态：**已解决**（随 D37-B 施工 `b83c692`；2026-10-06 与 D37 同场 D37-C 独立评审通过）
- 关联：`desktop/src/main/index.ts`（`dialog.showMessageBox`）、`src/main/confirm.ts` 队列、`host/confirm.ts`；D37, Spec 10 §2.4
- 原记录（活跃表原文，含施工回填）：**已修·待评审**（2026-10-06 随 D37-B：主窗口 `closed` → `mainConfirm.windowGone()` 对已打开与排队中的确认请求各回 ok:false；选 broker 补回包而非 host 超时，理由见 Spec 10 §2.10 前修订记录）——2026-09-27 D37 代码侧探针（Electron 44.4.5，scratchpad，未改代码）实测：父窗口 `destroy()` → 永不 resolve（`close()`/abort/`hide()` 均 resolve 为取消，非本条）。不是 fail-open（不会记批准），但 host 侧无超时，卡片停在 answering、之后所有重答返回 E_BUSY，只能重启。触发面窄（窗口被强制销毁而 host 不退出），优先级低于 D37 定性。**修法待定**：broker 对 `destroy` 事件补一个 ok:false 回包，或 `await confirm` 加超时 fallback 取消。
- 评审：✅ 通过（见上条 [D37] 评审 ②③）：TH-18b 端到端守住「挂起期间重答 E_BUSY → `windowGone` → E_STALE、零写入、卡仍打开 → 重答恰 1 行」；变异 V1（漏回排队）、V3（迟到结果不拦）由 TH-22c 以断言杀死；`win.on("closed")` 调 `windowGone()` 由 TH-22b 静态核对。真实 `destroy()` 打包版无法人手触发，接受以单测代替。同类缺口在退出确认框上仍在，另登记 N54。

### [N39] repoRoot 切换窗口内新起的读取拿到「新代号 + 旧仓库根」
- 状态：**已解决**（施工 4ca9a03；2026-10-06 独立评审通过）
- 关联：`desktop/src/host/service.ts::requestRepoRootChange`（`repoGen += 1` 早于 `resolveRepoRoot()`）；Spec 8 §2.9/§4, D32
- 原记录（活跃表原文，含施工回填）：repoRoot 切换窗口内新起的读取拿到「新代号 + 旧仓库根」。2026-09-28 D32 施工读码发现（未构造出可见现场）：切换开始即递增代号，仓库根要到 `resolveRepoRoot()` 才换；这段窗口里 30 s 定时的 `refreshEpisodeStatuses` 若起跑，会拿新代号去读旧根，结果通过代号比较写进 `summaries` 并推 `episodes.summary`（侧栏摘要，不进期 snapshot）。纵深缺口。候选：`resolveRepoRoot()` 后再递增一次代号 / 切换中不起新读取 / 读取记下发出时的根并在回包时比对；前两者触及 Spec 8 代号语义，须人拍板 **2026-09-29 人拍板「先试候选 2」并已施工**：`refreshEpisodeStatuses` 与 `loadStatus` 入口加 `if (this.switching) return`（切换窗口内不起新读取；`probe` 不加——切换自己在窗口内调它；切换收尾原本就 `void refreshEpisodeStatuses()` 补刷一次）。用例 TH-13b：占住一个在途读取让窗口保持打开，窗口内调 `refreshEpisodeStatuses` 与活跃期 `tickActive`，断言 STATUS spawn 数不变；窗口关闭后恢复。变异 2/2 被该用例以断言杀死（去 refresh 守卫 / 去 loadStatus 守卫，均 `expected 3 to be 2`）。vitest 378 全绿、tsc 通过。**未做**：未构造出用户可见现场（机理成立，后果限于侧栏摘要）；未走 e2e。
- 评审：✅ 通过（2026-10-06 独立评审 N39-B，与施工方不共用上下文）。① 读码：守卫只在 `refreshEpisodeStatuses` 与 `loadStatus` 入口（`if (this.switching) return`）；`probe` 未加且理由成立——切换在 `resolveRepoRoot()` 之后才 `await this.probe()`，此时根已是新根、代号已是新代号，组合一致；全文件带代号的读取只有这三处（`const gen = this.repoGen`），无遗漏；切换窗口唯一的让出点是等在途 spawn 的 `while` 循环，循环结束到 `resolveRepoRoot()` 之间全是同步代码，不存在第二个窗口。② 补刷：`finally` 复位 `switching` 后 `void this.refreshEpisodeStatuses()`，窗口内被跳过的读取不会让侧栏永久陈旧；即使补刷因 `reach` 未就绪提前返回，30 s 定时刷新兜底。③ 变异亲跑（`npx vitest run tests/host/sessions.test.ts -t TH-13b`）：去 refresh 守卫、去 loadStatus 守卫 → 均被 TH-13b 以 `AssertionError: expected 3 to be 2` 杀死，还原 md5 一致、复跑绿。④「未构造用户可见现场、未走 e2e」不必补：后果限于侧栏摘要至多一个刷新周期，TH-13b 用真实 `HostService` 与可控闸门精确卡在窗口内，比 e2e 定时撞窗口更确定。
- 发现（无阻断，🔵）：(a) 删掉切换收尾的 `void this.refreshEpisodeStatuses()`，host 全部 164 个用例仍绿——补刷是 N39 前就有的行为，本修依赖它却无用例守，后果是切仓后侧栏最多空 30 s；(b) 把 refresh 守卫改成永久 `return` 时，TH-13b 是在前置 `waitFor(() => calls > 0)` 超时（8 s）后失败，「窗口关闭后恢复读取」那条断言本身没有独立守住的用例（一般刷新路径只靠 TH-13b 的前置步骤间接覆盖）。两条都不是 N39 引入，记此备查。

## 2026-09-29：桌面端可用性收口

### [N49] 桌面端会话里 agent 起的作业找不到 ffmpeg / ffprobe
- 状态：**已解决**（施工 882a142；2026-09-29 独立评审通过）
- 关联：`desktop/src/host/spawner.ts`（会话 spawn 用 `childEnv(process.env)`，未开 `homebrewPath`）、`pipeline/jobs.py`（作业 `env = dict(os.environ)` 原样继承）、pipeline 各处按名字调 `ffmpeg`/`ffprobe`；Spec 8 §3.4 / RF-3、Spec 10 §3.4、D39
- 原记录（活跃表原文，含施工回填）：桌面端会话里 agent 起的作业找不到 ffmpeg / ffprobe：配音时长复核、排片探测、渲染、质检、封面、补料探测在桌面端全会失败（终端 `ava` 不受影响）。Spec 8 为 Finder 启动的 GUI app 把子进程 PATH 固定为 `/usr/bin:/bin:/usr/sbin:/sbin`，并在 RF-3 写明「加模板时必须复核」；只有 `SEAL_SCRIPT`、`RUN_TTS_APPLY_PATCH` 两个模板开了 `/opt/homebrew/bin`（A3）。Spec 10 新增的 `SESSION_*` 模板没开，而会话里 `run_pipeline` 起的作业经 `jobs.py` 原样继承这个 PATH。**实测（2026-09-29）**：`env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin` 下 `.venv/bin/python` 调 `ffmpeg`、`ffprobe` 均 `FileNotFoundError`，`git` 正常（`/usr/bin/git` 在）；本机 ffmpeg 只在 `/opt/homebrew/bin`。既有 e2e 的真实 core 用例用的是桩作业，所以一直没暴露。**未做**：没有用真实会话跑一次需要 ffmpeg 的作业来端到端证实。**候选修法**：`SESSION_*` 与 `SEAL`/`TTS_APPLY` 一样开 homebrew PATH（agent 工具表是闭集、无 shell 工具，暴露面小，但属 Spec 8 冻结的环境白名单，须人确认）。**施工（2026-09-29，人拍板候选 (a)）**：① 端到端复现——`e2e/sessionReal.spec.ts`「N49 真实 core」在真实会话里批准 `run_pipeline qc`（现场 ffmpeg 生成 5 s mp4），修前作业 stderr 为 `FileNotFoundError: [Errno 2] No such file or directory: 'ffprobe'`（`qc.py` `_probe`）；② `spawnSession` 改 `childEnv(process.env, true)`，PATH 末尾追加 `/opt/homebrew/bin`，系统目录同名程序仍优先；同一 e2e 转绿（`06-check.log` 有 ffprobe 实测的音画时长与 ffmpeg 实测的响度）；③ TH-6 从会话子进程内部取 `os.environ` 断言 PATH 恰为选定值、其余白名单项与 `childEnv` 逐项相同、仍只多一个密钥键；④ 变异：去会话 homebrewPath → N49 e2e 红（TH-6 亦红）；`childEnv` 的 homebrew 段拼错 → TH-6 与 spawner 既有 PATH 断言红；只在会话 env 上拼错 PATH → 仅 TH-6 红；会话 env 改 `PYTHONUNBUFFERED` → TH-6 的逐项循环红（先试的 `LANG=C` 是被「多余键」断言借 PEP 538 的 `LC_CTYPE` 杀的，不算，已换）；均还原 md5 对拍一致；⑤ Spec 8 §3.4、Spec 10 §3.4 archive 原文加修订记录。**顺带盘点（只登记不扩修）**：`acquire` 的 `yt-dlp` 经 `shutil.which` 查找，只在 `/opt/homebrew/bin`、不在 `.venv`——修前桌面端抓取卡批准后同样会 `FAIL 找不到 yt-dlp`，本修随之可找到（未端到端测：真实抓取要出网）；`git`/`curl`/`ssh`/`open` 在 `/usr/bin` 不受影响；Python playwright 浏览器依赖 HOME，已在白名单。**未覆盖**：Intel Mac 的 `/usr/local/bin`（与 SEAL/TTS_APPLY 同一限制，A3 已如实声明）；打包版端到端未跑（留 ACC）。
- 评审：✅ 通过（2026-09-29 独立评审，与施工方不共用上下文）。① 退回修复（会话 env 改回 `childEnv(process.env)`）→ N49 e2e 红在 `sessionReal.spec.ts:474`，stderr 为 `qc.py` `_probe` 的 `FileNotFoundError: [Errno 2] No such file or directory: 'ffprobe'`，无其他失败因素；② 恢复（md5 对拍一致）→ 同一用例绿；会话进程实际 PATH 用 `ps -Eww` 独立取证（不经 TH-6、不经 spawn 日志）：会话 `pipeline.agent.protocol` → `pipeline.qc` → `ffprobe`/`ffmpeg` 每一级 PATH 均为 `/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin`，会话环境仍只有白名单 + 一个密钥键（另有 macOS/venv 自动加的 `__CF_USER_TEXT_ENCODING`、`__PYVENV_LAUNCHER__`）；③ 扩面红队：AST 扫 `pipeline/` 全部 subprocess 调用并核 venv 内 mlx_audio / playwright / crawl4ai，修前修后解析结果只变了 `ffmpeg`、`ffprobe`、`yt-dlp` 三个；`git`、`python3`、`ssh`、`rsync`、`curl`、`open` 仍命中 `/usr/bin`（homebrew 同名版被遮住）；agent 无 shell 工具、argv 恒为 `[sys.executable, -m, pipeline.<白名单模块>]`，`KEY_ENV_NAME_RE` 挡住名为 PATH 的密钥变量；`~/.ssh/config`、git 全局配置无引到 homebrew 程序的钩子——**无新越权面**；`yt-dlp` 从找不到变为真会跑，属恢复 Spec 9 S6-R1 本意，未端到端测（要出网）已如实标注；④ 变异 4 条亲跑，全部断言失败、无伪杀：会话 env 退回修前 → e2e + TH-6；homebrew 挪到 PATH 最前 → 仅 TH-6（顺序只由它守）；会话 env 去掉 TMPDIR → 仅 TH-6 逐项循环；`childEnv` homebrew 段拼成 `/opt/homebrew/sbin` → TH-6 + spawner 既有 PATH 断言 + e2e；均还原 md5 一致；⑤ diff 边界合规，`pipeline/`、`config/`、`package.json` 未动；全程临时副本，真实数据盘零新文件。发现（无阻断）：密钥变量随作业继承到外部程序（既有行为，登记 N50）；N49 e2e 在无 `/opt/homebrew/bin/ffmpeg` 的机器上直接红而非跳过（有意为之，A3 已声明）；`BuiltCore.homebrewPath` 的 JSDoc 写「git 在 /opt/homebrew/bin」不准（本机 `/usr/bin/git` 优先），只是注释，不在本条范围。

## 2026-09-28：收尾批复核收口（只读复核 / 决策准备 session）

### [N43] Playwright 硬编码 `workers: 1`，全量 e2e 只能串行
- 状态：**已解决**（施工 9f07166；2026-09-29 独立评审通过）
- 关联：`desktop/playwright.config.ts`（`workers: 1`）；Spec 8 §7, Spec 10 §7
- 原记录（活跃表原文，含施工回填）：2026-09-28 人报：各用例已有独立临时 `--ava-repo-root` / `--ava-user-data`，理论上可并行。**但并行等于自加负载**：D31/D32/N37 都是「负载下才红」的时序问题，且真实数据零污染的 globalSetup/teardown 在多 worker 下是否仍成立未核。推进：等 D31/D32/N37 收口、N41/N42 落地后再试 `workers: 2–3`，负载下复跑全量 ×3 全绿才可改 **2026-09-28 人拍板：开施工试点 workers=2**。验收：负载下未打包全量 ×3 全绿、真实数据零污染的 globalSetup/teardown 在多 worker 下仍成立（逐条核）；不达标则登记原因退回 workers=1。  **2026-09-28 施工（已修·待评审）**：`playwright.config.ts` workers 1→2（config 注释写明前置与多 worker 安全性论证：用例 mkdtemp 独立 repo/userData、无共享端口——媒体走自定义 scheme、globalSetup/Teardown 是进程级钩子仍只跑一次）。验收实测：空载全量绿（5.6 min，串行 9.3 min，约 -40%）；负载（6×yes）下——N48 修复前 5 轮 3 红（VE-1 ×2 + TX-15 ×1，TX-15 查实是 N48 的 NameError 与 workers 无关）；**N48 修复后负载 ×3 全绿**（111 passed / 2 skipped ×3），真实数据零污染校验逐轮通过。**评审注意（诚实申报）**：N48 修复前 VE-1 在 workers=2+负载下红过 2 次，当时未存日志、直接证据缺失；修复后 3/3 绿。VE-1 那两次红疑似 2 worker + 6×yes 超订下 10 s 等待预算见底，若复发应登记新条目（不许调大超时消音）。TX-15 负载红（N48）已修，workers=2 的验收轮次是在它修复后跑的
- 评审：✅ 通过。① workers=2 独立验证（实测）：空载全量 e2e 1 次 111 passed / 2 skipped（5.7 min）；6×yes 满载全量 2 次均 111 passed / 2 skipped（5.9 / 6.1 min），零失败零重试，每轮 globalTeardown 输出「真实数据零污染」。② 未复测 workers=1 的串行基线耗时，施工方『空载 -40%』未独立证实（本次只证 workers=2 稳定）。③ 边界：config diff 被卷进 N48 的 commit a532725（施工时工作树未提交）属提交卫生问题，内容本身正确；施工方记录的『N48 修复前 VE-1 在 workers=2+负载下红 2 次、直接证据未抓到』——评审满载 ×2 未复现，若复发须登记新条目。

### [N44] TI-10「第二实例启动 → 已有窗口获得焦点」在人正操作别的 app 时偶发红
- 状态：**已解决**（施工 c7036ac；2026-09-29 独立评审通过）
- 关联：`desktop/e2e/ack.spec.ts`（TI-10 的 `isFocused()` 轮询 3 s）；Spec 8 §7（TI-10）
- 原记录（活跃表原文，含施工回填）：2026-09-28 N37 负载验收时发现：负载全量 ×3 中 2 次红；随后单跑——后台模式负载 1/3、前台模式（不带 `--ava-test-background`）负载 2/3、前台空载 3/4 通过，均在人正用终端打字时。判断：断言要的是「macOS 把系统焦点交给本窗口」，macOS 14 起协作式激活会在用户正操作别的 app 时拒绝抢前台，属用例对环境的依赖，**未证实与 N41 有关**（样本小，两种模式分不出差别，故未给 TI-10 开前台特例）。候选：① 断言降为「main 调用了 focusWindow 且窗口 restore/show」+ 真机手验焦点；② 保留焦点断言但仅在无人值守时跑（需可检测的开关）；③ 维持现状并在评审口径里注明「人在机器前时 TI-10 红不算回归」。推荐 ①（焦点是否授予本就不归 app 控制），待人拍板 **2026-09-28 人拍板：采纳候选 ①**——TI-10 的焦点断言改为「main 确实调用了 focusWindow，且窗口已 restore/show」（可观测、不依赖 macOS 是否授予焦点），系统焦点是否真的给到改为打包版真机手验一项；不改 N41 的后台模式。  **2026-09-28 修复（已修·待评审）**：TI-10 的 `isFocused()` 轮询删除，改为断言 ① main 侧 `__avaTestFocusCalls` 计数 +1（仅未打包构建的 dev 计数器，focusWindow 内自增）② 窗口 `isMinimized:false` + `isVisible:true`。Spec 8（archive）TI-10 行已加 N44 修订注记；系统焦点授予列入打包版真机手验。变异 2/2（还原 md5 对拍）：删计数自增→TI-10 红；second-instance 不调 focusWindow→TI-10 红。tsc 通过、vitest 377 passed、未打包全量 e2e 111 passed/2 skipped（真实数据零污染）。**评审注意**：① 「窗口 blur 后 focusWindow 被调」由 dev 计数器观测，focus() 本身的系统效果不再有机检（已转述给人的手验项）；② 手验可与 D37 配方同场做
- 评审：✅ 通过（附一处加固）。① 复现与基线（实测）：TI-10 单跑 3/3 绿；未打包全量 e2e 空载 111 passed / 2 skipped。② 变异 4 条（每条改 main 后 electron-vite build、还原后重建，md5 对拍）：删计数自增→TI-10 红；second-instance 不调 focusWindow→TI-10 红；**删 focusWindow 里的 restore→SURVIVED**（窗口本来就没最小化，『restore/show』断言不可观测）——评审补：先 minimize 并轮询到 isMinimized 再起第二实例，重跑 KILLED，加固版单跑 3/3 绿；删 win.focus()→仍绿，属人拍板候选①的既定边界（系统焦点授予不归 app 控制，改打包版真机手验，未机检）。③ 遗留：打包版真机焦点手验仍是待人项，可与 D37 配方同场。

### [D7] 次回预告仍在索引里未滤除
- 状态：**已收口（style 侧已落盘；混入主对白轨属 N25 同构局限）**（2026-09-29 S21 收尾：状态早已是终态，按维护规则「解决即归档」仅迁移，不改结论）
- 关联：`pipeline/subindex.py:66-105`；N25
- 原记录（活跃表原文）：`NON_DIALOGUE_STYLE` 已补齐独立预告样式（前/后缀 `yokoku`/`preview`/`次回予告`/`下集预告`/`予告`/`预告`）并由单测+变异检验锁死。三番 ASS 实测核明：预告标题卡（春物 S2 `Title-Yokoku`、春物 S1/S3 与喰种 `Title`/`TITLE`）早已被 `^title` 滤除；真正占 ~2% 的预告角色对白在春物（`Sub-CN`/`Text-cn`，420/15009=2.80%）、东京喰种（`CN`/`Default`/`DefaultUP`，338/13810=2.45%）中直接混入正片主对白 style（与 N25 同构，纯 style 过滤若触碰会误伤全片正片台词），罪恶王冠 BD 则不含预告段（0%）。在库 151 集旧索引零差异、免重建

### [D8] 纯中文字幕集数过不了对轴校验
- 状态：**已决策（放弃该集，不入库）**（2026-09-29 S21 收尾：状态早已是终态，按维护规则「解决即归档」仅迁移，不改结论）
- 关联：`pipeline/ingest.py:278-345`、`docs/dev/postmortems/workflow-history.md:1080`；ADR-0007
- 原记录（活跃表原文）：2026-09-25 拍板：**放弃，不走 ASR 兜底**。verify 靠字幕含假名行对日语 ASR（对照窗判据），纯中文/假名稀疏轨无窗可用。全库实测唯一活实例 = 春物 S3 OVA（`.SC.ass` 假名行仅 4%，未入库，笔记已登记素材边界）；夏隧 zh.ass 同为 3% 但已入库。理由三条：① 1 集番外 OVA 不值「ASR(ja)+机翻+质检判据」一整条边链；② ASR+机翻双重无标定误差会混进按字幕组文本标定的检索池，质检体系不覆盖；③ 与 ADR-0007 同逻辑同判。未来若某番大量集数纯中文轨（=整部拒收）再重估 ASR 兜底

### [N5] ingest phase0 重建索引时跳过 verify 的静默风险
- 状态：**已解决（已落盘指纹校验）**（2026-09-29 S21 收尾：状态早已是终态，按维护规则「解决即归档」仅迁移，不改结论）
- 关联：`pipeline/ingest.py:375-418,529-562`；—
- 原记录（活跃表原文）：`register()` 落盘视频字节大小 `size` 与外挂字幕 `sub_sha256`；`phase0 --reindex` 仅在 `path + size + sub_sha256` 三项全匹配时才免跑 `verify`，同名替换片源/字幕、换路径或旧表缺指纹均自动重跑 `verify`（`tests/test_ingest.py::TestPhase0ReindexFingerprint` + M1/M2/M3 变异检验锁死）

### [N28] 非终端信号（`kill -INT <ava pid>`）下 ffmpeg 孙进程存活并继续写输出
- 状态：**已解决（真渲染复验通过）**（2026-09-29 S21 收尾：状态早已是终态，按维护规则「解决即归档」仅迁移，不改结论）
- 关联：`pipeline/jobs.py::execute_job`；ava-impl-spec §3.3, 变异 M21
- 原记录（活跃表原文）：`execute_job` 已开启 `Popen(start_new_session=True)` 并将 `Popen` 返回后的全周期纳入 `try ... except BaseException:`，中断时由 `_kill_process_group` 调用 `os.killpg(proc.pid, signal.SIGKILL)` + `proc.wait(timeout=2.0)` 连根拔起子进程及全部 `ffmpeg` 孙进程并收尸（`tests/test_agent_pr6.py::test_n28_*` 双腿单测 + M1/M2/M3 变异检验锁死）。2026-09-25 真渲染复验（`2026-09-06-你的名字-遗忘的代价`）：6 并发 `ffmpeg` 孙进程（PIDs 6709–6714）下发单点 `kill -INT`，响应耗时 `0.258s`，残留 `ffmpeg` 进程 `[]`（0 存活）

### [D24③] 跨挂载点迁移：shots 镜头表 meta.source 存绝对路径（D24 的 ①② 仍在活跃表挂触发条件）
- 状态：**已解决**（施工 08f5c5a；2026-09-29 独立评审通过；仅 ③ 部分）
- 关联：`pipeline/ingest.py`；ADR-0012, Local-First
- 原记录（活跃表原文，含施工回填）：素材库向 TB 级扩张，缺少全量 vs 增量 hash 变更检测与智能缓存淘汰，跨设备挂载需保证便携性。**2026-09-28 决策准备（只读）**：量化现状——T7 数据盘共 256G（盘 931G 用 301G、余 631G），其中 `raw` 219G、`bgm` 12G、`shots` 1.4G（268 个代表帧目录）、`vindex` 218M、`index` 196M；「TB 级」离现实还远（约 1/4）。重建成本见 ADR-0003「6」：一季建视觉索引约 2.4 h。三子项：**① 增量变更检测——不做**：N5 已落 `path + size + sub_sha256` 三项指纹，`phase0 --reindex` 只重建不匹配的集；触发条件：单次 `--reindex` 实测超过 1 h 或出现「指纹匹配但内容已变」的实例。**② 代表帧缓存淘汰——不做**：1.4G 仅占数据盘 0.5%，无清理压力；触发条件：`shots/` 超过数据盘剩余空间的 10% 或 > 50G。**③ 跨挂载点迁移——最小做（报人）**：代码与 config 无 `/Volumes`/`/Users` 硬编码（仅注释），`data` 走仓库内符号链接；**但 `data/library/shots/*.json` 的 `meta.source` 在 231 个里有 15 个存绝对路径，其中 1 个已失效**（`夏隧_S01E01.json` 指向改名前的旧仓库 `.../anime-video-agent/data/library/...`），`shots.py` 重抽帧（约 336、430 行）直接读它，会当场失败——这就是换盘/改名/换机时的实际断点。最小方案：写入侧改存「相对 data 根」路径、读取侧对绝对路径做一次按 data 根重定位并在失效时给出可操作报错；存量 15 个文件一次性迁移（需人批准改数据）。另开施工 session **2026-09-28 人拍板**：①② 不做（按触发条件挂着）；③ 另开施工——`shots.py` 写入 `meta.source` 改存相对 data 根的路径、读取侧对绝对路径按 data 根重定位并在失效时给可操作报错；存量 15 个绝对路径文件一次性迁移（含已失效的 `夏隧_S01E01.json`）。动 `pipeline/` 与真实 data，施工需配评审。  **2026-09-28 ③施工（已修·待评审）**：写入侧 `shots.meta()` 改走 `_store_source`（绝对路径在 data 根内 → 存 data 根相对；存量仓库根相对 `data/...` 统一改存 data 根相对；data 根之外保持绝对）；读取侧两处抽帧（`frames`/`caption_frames`）改走 `_resolve_source`——三种落盘形态统一解析，绝对路径失效时按 `library/` 尾部拼当前 data 根重定位，仍找不到给可操作报错（指向 sources.json 修法）。存量迁移：15 个绝对路径文件以 `sources.json`（登记表是唯一事实源）为准重写 meta.source 并逐个验证可解析——含已失效的 `夏隧_S01E01`（旧仓库路径 + 目录结构已变，`library/` 尾部规则救不回，靠 sources.json 找回）；迁移后全库 231 个 shots json 零绝对路径。用例 6 条（TestSourcePathPortability）。变异 3/3（还原 md5 对拍）：去重定位分支→重定位用例红；写入侧退回原样→store/meta 用例红；失效不报错→可操作报错用例红。全量 1976 passed。**评审注意**：① 存量 216 个「仓库根相对」文件未动（读取侧兼容）；② `clips.json`/`recheck` 等其他产物的 source 字段不在本次范围；③ 迁移直接改的是 T7 真实数据（拍板已批），未留 .bak——原值可从 git 历史+本 issue 行还原
- 评审：✅ 通过（③ 部分；①② 按触发条件挂着的结论维持，D24 行整体收口）。① 存量核对（实测）：231 张镜头表 meta.source 形态 = 216 个 data/… + 15 个 data 根相对，零绝对路径，且 _resolve_source 全部解析到真实文件（含原已失效的夏隧 S01E01）。② 变异 5 条实跑：重定位去掉→重定位用例红；meta 写入不走 store→写入侧用例红；失效不报错→报错用例红；**frames()/caption_frames() 读取侧改回直接 Path(meta.source)→SURVIVED**（只测 _resolve_source 本身守不到这两处接线）——评审补『假 _extract 记录器 + 相对 source』接线用例，重跑两条均 KILLED（首版夹具 fps 写成浮点、收集期报错造成假杀，已修并确认基线绿）。③ 其余消费方核对：镜头画廊只取 basename，load() 不对账 source，无其他直接 Path(meta.source) 的读者。

### [N19] cpm 换音色/换题材/换引擎要重测
- 状态：**已解决**（施工 06f7ebf；2026-09-29 独立评审通过）
- 关联：`config/project.json` 的 `script.cpm` 与 `_cpm_note`（原行号已漂移）；ADR-0006
- 原记录（活跃表原文，含施工回填）：同一音色不同文风可差 24%；Qwen3 语速待实测。**2026-09-28 复核（只读）**：① 溯源：现值 `cpm = 298`，来自 `df8c702`（2026-09-11，云端 CUDA Qwen3 切换时重测 315→298）——**换引擎的触发条件当时执行过**；但 `_cpm_note` 仍写「2026-08-10 校准 380→315」，**说明文字没跟上数值**（文档漂移，需人确认后改 note）。② 用真实产物复算（全部 22 期 `03-audio/manifest.json`，字数用 `tts.normalize` 与 `expected_duration` 同口径、时长用 manifest 里 ffprobe 复核的段时长）：IndexTTS 8 期中位 289.5；**本地 Qwen3（mlx）12 期中位 261.5（250–282）**；云端 Qwen3 CUDA 2 期（EGOIST V2 01/02）中位 273.5。298 比现行引擎实测**高约 8–14%**：`check_script` 按 cpm 推出的字数带偏宽、`expected_duration` 偏短估约一成；时长门禁 `DUR_BAND 0.5–2.0` 把偏差吸收了，所以从未报错。「Qwen3 语速待实测」已不成立（上面即实测）。③ 待拍板：(a) 按现行引擎近 N 期 manifest 中位数重标（上面的复算法即可复现，脚本不入仓）并同步改 `_cpm_note`；(b) 维持 298 并在 note 写明「有意取偏快值」的理由；另可考虑把 cpm 改为按引擎/音色分键（与 N14 同一「逐条件标定」思路） **2026-09-28 人拍板：按现行引擎近期中位数重标 cpm**（本地 Qwen3 中位 261.5、云端 273.5；取近 N 期现行引擎中位，约 265）并同步改写 `_cpm_note`（写明复算口径：`tts.normalize` 字数 / manifest ffprobe 时长）。动 `config/`，按 rule ④ 另开施工 + 评审；注意 `check_script` 字数带随之收窄，施工时要核对在制期稿件是否因此被新卡。  **2026-09-28 修复（已修·待评审）**：`script.cpm` 298→257，`_cpm_note` 重写（文档漂移已纠：旧 note 还停在 380→315）。重标口径（本 session 复算，不转述复核数字）：本地 mlx qwen3_tts 全部 9 期 03-audio manifest，`tts.normalize` 字数 ÷ ffprobe 段时长，逐期中位 **257.4**、汇总 259.8、范围 250.0–281.7。**与 C 组复核数字的分歧（评审须核）**：复核记「mlx 12 期中位 261.5」，但本地只有 9 期 mlx manifest（17 份 = 9 mlx + 8 IndexTTS，云端 2 期的 manifest 不在本机），12 期口径在本机复算不出——修复取可验证的 9 期中位 257。代码侧 `check_script.CPM` 的 paths.conf 默认值保持 380 不动（纪律：default=原硬编码值）。全量 1970 passed。变异不适用（纯配置改值 + note），等价性由全量绿承担。
- 评审：✅ 通过。① 独立复算（实测，不转述）：全部 17 份 03-audio manifest = 8 IndexTTS + 9 本地 mlx qwen3_tts（云端 CUDA 期的 manifest 不在本机）；mlx 9 期逐期中位的中位数 256.7、逐期汇总的中位数 257.4，取 257 成立；C 组复核「12 期 261.5」在本机数据上确实复算不出，施工方的分歧记录属实。IndexTTS 中位 288–289，说明旧 298 是老引擎口径。② 影响面核对（实测）：用 cpm 298/257 分别跑 17 篇现存稿的 check_script，已完成期的字数/时长带会随之平移（旧稿多数会在新带下判『时长』超上限），但这些期均已成片，仅在重跑机检时可见；唯一在制期（2026-09-21 东京喰种董香人物志-二）尚无 02-script，新带宽只影响其后续稿——符合人拍板「按近期中位数」的意图。③ 全仓 grep 无残留的 298/cpm 引用；代码默认值保持 380 未动（paths.conf 纪律）。④ 备注：云端 CUDA Qwen3 仅 2 期且 manifest 不在本机，若日后以云端为主要引擎应再重标（_cpm_note 已写明「换引擎仍须重测」）。

### [N48] `--continue` 恢复含 tool 记录的会话必崩（NameError: tool_flags）
- 状态：**已解决**（施工 a532725；2026-09-29 独立评审通过）
- 关联：`pipeline/agent/protocol.py::_send_history`；Spec 9 §3.1 history 帧
- 原记录（活跃表原文，含施工回填）：_send_history 是模块级函数，tool_flags 只在 main() 局部 import；TP-8 场景（等卡时被杀）不产生 tool 记录，藏了整场二期；N43 负载验收时 TX-15 抓到。修为函数内局部 import（守 fd 隔离），新增 TP-8b。
- 评审：✅ 通过。① 原始复现（实测）：去掉 _send_history 里的局部 import，TP-8b 红（NameError）而 TP-8 仍绿——证实 TP-8 场景确实打不到 tool 记录路径；还原 md5 一致。② 静态扫描 protocol.py：其余模块级函数没有再依赖 main() 局部 import 的名字。③ diff 边界：main() 顶部移除 tool_flags 导入 + 函数内延迟导入（守 fd 隔离）+ TP-8b。注：N48 施工时未单独登记活跃表行（只在 N43 行内提及），本条按 commit 信息补录。

### [N47] 稿件 `人物:` 写到「0 个已贴名簇」的角色时静默无效
- 状态：**已解决**（施工 0cb32f3；2026-09-29 独立评审通过）
- 关联：`pipeline/check_script.py`（`人物` 字段处理）、`pipeline/vindex.py`（clusters 读取）；D11, N17, ADR-0004
- 原记录（活跃表原文，含施工回填）：2026-09-28 D11/N17 拍板：不批量贴名，改需求驱动。推进：`check_script` 对 `人物:` 经 `alias_map` 映射到的规范键在该番 clusters 里**零个已贴名簇**时出 INFO「该角色在索引里无代表脸，在场过滤对本段无效（贴名：faces sheet <番>）」——只 INFO、永不 FAIL（同 D18 的「写了就走过滤、不写不勉强」口径）；实测唯一现存命中应为春物「阳乃」2 段 **2026-09-28 修复（已修·待评审）**：新增 `faceless_character_hints`（`_HINT_BLOCK` 切块 + `_HINT_WHO` 取值复用 D18 既有口径）：`人物:` 值经 `_character_alias_tables`（`_character_aliases` 拆出的逐番版，合并语义不变）映射到规范键后，在该番 clusters 里零个已贴名簇 → INFO「…无代表脸，在场过滤对本段无效（贴名：python -m pipeline.faces sheet <番>）」。`_named_cluster_tags` 纯 json 读 clusters（不碰 vindex/faces 的 numpy，守 §5.2 纯洁性），文件缺席/坏 json → None 安静跳过（判据 4）。接线在 `main` 的 `character_hints` 之后。用例 4 条（零贴名出 INFO/有贴名不报/缺席安静跳过/未写人物不报）。真实数据复扫全部在库 02-script.md：恰命中春物「阳乃」2 段（2026-08-04-团子不该赢 段3/段5），与预期一致。变异 3/3（`PYTHONDONTWRITEBYTECODE=1`，还原 md5 对拍）：永不报→零贴名用例红；判定取反→有贴名不报 + 缺席跳过红；缺席返回空集而非 None→缺席跳过红。全量 1968 passed。**评审注意**：① `人物:` 多值写法（顿号分隔）在 clips 侧本就整串当一个名字，本提示同口径；② 跨番期同一别名在两番都登记时只按第一番判（现实无此碰撞）||N48|已修·待评审|`--continue` 恢复含 tool 记录的会话时协议进程必崩（NameError）|`pipeline/agent/protocol.py::_send_history`|Spec 9 §2.8/§3.1|2026-09-28 N43 负载验收时抓到（TX-15 红）：`_send_history` 是模块级函数，`tool_flags` 只在 `main()` 里局部 import，重放 tool 角色记录必 NameError 退 1——二期全程没炸是因为 TP-8 的「等卡时被杀」场景不产生 tool 记录、D35 之后「未执行」补记才让 tool 记录变常见。修复：`_send_history` 内局部 import（守 fd 隔离纪律，main ② 先于一切 pipeline import），`main()` 里冗余的 tool_flags import 撤掉。新增 TP-8b（批准执行的工具 → shutdown → --continue → tool 帧重放 ok/text 映射正确、退 0）。修前红（退回即 NameError）、修后绿；全量 1977 passed。**评审注意**：① TX-15 在负载下偶发红的真因就是本条（不是 N43 的 workers=2）；② TP-8 的 role 断言此前是「允许 tool」而非「必有 tool」，覆盖缺口已由 TP-8b 补上
- 评审：✅ 通过（附两处补丁）。① **发现并修复**：施工把 _character_aliases 拆成 per-anime 表 + 合并时，别名冲突从旧版 setdefault「先到先得」悄悄变成「后到覆盖」，注释却写「与旧版同」；真实 17 期上新旧输出零差异（数据暂未触发），但语义已变——恢复先到先得（表内与跨番），补跨番/单番内冲突两条用例，各自变异 KILLED。② **发现并补**：main() 里对 faceless_character_hints 的调用无用例守着（删掉调用 SURVIVED）——补假 DATA 根走 main() 的接线用例，重跑 KILLED。③ 自身逻辑变异 3 条：条件取反→零贴名用例红；缺席当空集→缺席跳过用例红；main 不打印→接线用例红。纯 stdlib 纯洁性（clusters 纯 json 读取）核实。

### [N46] 字幕组 style 命名表写死在代码里，换番/换字幕组要改 `pipeline/`
- 状态：**已解决**（施工 0ecf16d；2026-09-29 独立评审通过）
- 关联：`pipeline/subindex.py::NON_DIALOGUE_STYLE`；D10, ADR-0007
- 原记录（活跃表原文，含施工回填）：2026-09-28 D10 复核拍板：2026-08-09 罪恶王冠加 3 种命名（裸 `JP`、`CN_song`/`JP_song`/`Eng.song` 等）、2026-09-25 D7 加预告样式，都是「换一部番（字幕组）就要改代码」——按总纲属缺陷。推进：把 style 命名的前缀/后缀/精确匹配表迁入 config（机制留代码：前缀匹配、精确匹配、KANA/CJK 兜底），已有单测与 D7 的变异矩阵随之改为读配置；须保持「主对白 style（Sub-CN/Text-cn/CN/Default/JPN）零误伤」这条既有断言 **2026-09-28 修复（已修·待评审）**：表迁 `config/project.json` 的 `subtitle.non_dialogue_styles`（四类：`prefix_bounded`/`prefix`/`exact`/`suffix`，带 `_nd_note` 说明与「主对白 style 绝不进表」警告）；机制留代码——`build_non_dialogue_re(table)` 把表编成单条正则（re.escape 逐项转义），`non_dialogue_re()` 缓存 accessor 走 `paths.conf`（默认值 = 原硬编码表，符合 paths.conf 纪律），`parse()` 改调它。原 `NON_DIALOGUE_STYLE` 常量删除，其上方整段取舍注释保留。用例：既有 13 处断言改走 `non_dialogue_re()`（KEEP/DROP、Inner、裸 JP、NOTE、D7 预告、反序 song 全保留）；新增「机制读表」（自定义表加 pv → PV-CN 滤掉，默认表不滤）与「config 表与代码默认表行为一致」（防配置漂移）。变异 3/3（`PYTHONDONTWRITEBYTECODE=1`，还原 md5 对拍）：prefix_bounded 丢边界→Inner 用例红；exact 退化为前缀→裸JP用例红；suffix 类删除→反序 song + 预告 3 条红。全量 1970 passed。**评审注意**：① 重建索引产物应与现状逐字节一致（机制等价、表同值），未实跑重建（全量重建约 2.4h/番，无触发理由）；② `docs/dev/plans/2026-08-21-检索降级红旗.md` 与 archive 里对 `NON_DIALOGUE_STYLE` 的引用是历史文档，未追改
- 评审：✅ 通过（附一处加固）。① 等价性（实测）：旧硬编码正则与新表编译正则在 131 个真实 ASS style + 边界样例上零差异。② **发现并修复**：config 表为空 / 含空串条目 / 列表写成字符串时，build_non_dialogue_re 编出匹配一切的正则，整部番对白被静默丢光——现 build 时校验形状并用主对白 style 金丝雀（Sub-CN/Text-cn/CN/Default/JPN）自检，违反即 ValueError；新增坏表用例。③ 变异 4 条实跑：suffix 类丢失→反序命名用例红；canary 检查去掉→坏表用例红；形状校验去掉→SURVIVED，补 exact:[""] 用例后 KILLED；accessor 忽略 config→SURVIVED（config 与默认同值造成等价），补 monkeypatch _CONF 的 accessor 用例后 KILLED。全量 1983 passed。

### [N45] `web_fetch` 不处理 `Content-Encoding`：服务器强行 gzip 时把压缩字节当正文返回，正文乱码、链接清单为空，且不报错
- 状态：**已解决**（施工 2e545be；2026-09-29 独立评审通过）
- 关联：`pipeline/agent/web.py::fetch_web`（「不声明 gzip」的假设）；Spec 4, Spec 13
- 原记录（活跃表原文，含施工回填）：2026-09-28 D28 收口时实测：`fetch_web("https://www.python.org/")` 返回 status 200、`text` 为 gzip 二进制乱码、`links` 0 条；curl 确认该站即使请求头 `Accept-Encoding: identity` 仍回 `content-encoding: gzip`（nginx + varnish）。同批 docs.python.org（58 条链接）与 example.com 正常。后果是**静默失败**：模型拿到乱码、以为页面没内容，也不会按「静态被挡→升级 crawl」的提示升级。推进：读响应头 `Content-Encoding`——`gzip`/`deflate` 则在 `max_fetch_bytes` 上限内流式解压（防解压炸弹：解压后字节同受上限约束），其余非 identity 编码明确报错并附升级 crawl 提示；补用例（本地假服务器强制 gzip）与变异 **2026-09-28 修复（已修·待评审）**：新增 `_read_body_capped(resp, content_encoding, limit)`，`fetch_web` 与 `search_web`（同一读取路径、同一 bug 类）统一走它——gzip 走 `gzip.GzipFile.read(limit)`、deflate 走 `zlib.decompressobj` 的 `max_length` 参数，解压后字节同受 `max_fetch_bytes` 封顶；未知编码 ValueError 附升级 crawl 提示；gzip/deflate 流损坏也诚实报错（不静默错解）。`fetched_bytes` 语义=进入解码的字节数（压缩响应报解压后）。真网复测：`fetch_web("https://www.python.org/")` 由乱码净文/0 链接 → 正常净文（52960 字节）/127 链接。用例 7 条（gzip/deflate/解压炸弹封顶/br 报错/坏 gzip 报错/显式 identity/search_web gzip）。变异 3/3（`PYTHONDONTWRITEBYTECODE=1`，还原 md5 对拍）：fetch_web 退回裸读→gzip/deflate/炸弹 5 条红；未知编码静默当 identity→br 用例红；gzip 去封顶→炸弹用例红。Spec 13（archive）按 v0.5 修订记录同步改 §2.5⑤ 与 RF-12。全量 1964 passed。**评审注意**：① 顺手覆盖了 `search_web`（同一 helper，属同一根因，非顺手改别条目）；② 顺带发现并已修：`feab78c` 落档把字面 `03-audio/manifest.json` 写进 `01-topic.md`，触发 egress 断言致 4 条装配集成测试全红（D30 同类第二现场），已改写避开（8502d21）
- 评审：✅ 通过（附一处用例补强）。① 真网复跑（实测）：python.org 200 / 127 链接 / 正文正常。② 变异 4 条实跑（PYTHONDONTWRITEBYTECODE=1，还原 md5 对拍）：gzip 分支失效→gzip 用例红；gz.read(limit)→gz.read()→bomb 用例红；search_web 不走 helper→search 用例红；**deflate 解压去掉 max_length 限流→SURVIVED**（末尾 out[:limit] 兜住输出，仅内存上界不同）——评审补 test_fetch_content_encoding_deflate_bomb_capped（间谍断言每次 decompress 带 max_length ≤ limit+1），重跑 KILLED。③ diff 边界：只动 web.py 读体函数与调用点 + 测试 + Spec 13 archive 修订。

### [N21] 写稿基线与文风文件的两处缺口（原「文档维护琐碎项集合」）
- 状态：**已收口**（2026-09-28 复核）
- 关联：`skills/write-script/BASELINE.md`（知乎 3 行）、`skills/write-script/VOICE.local.md`（本机私有、不进 git）；—
- 原记录（活跃表原文）：④ 知乎 3 篇超 45 字占比缺测；⑥ VOICE.local 移植表缺项；①②③⑤ 已修。**2026-09-28 复核（只读）**：①②③⑤ 抽查确认已修（`scenes.json` 3 处路径引用全部存在；`bgm.json` 241 首逐条存在，见 N12；`SHOTLIST.md` 已无编号步骤可断；`voice.json` 的 `ref_text: null` 理由在 `_note`）。剩两条改写为可执行项：**(1) 实测知乎 3 篇的超 45 字整句数**——这不是补表格：`check_script` 的 `MIN_LONG_SENT = 2` 要求至少 2 句超 45 字，而「外卖骑手」（30400 赞、最长句仅 60 字）若达不到，这道门禁就在误伤合法的短句文风，与 08-04 删掉「气口均长」门禁是同一类错。做法：取 3 篇原文存本机 txt，`python -m pipeline.check_script --raw <文件>` 同口径量（BASELINE 第 12 行），回填表格；判据：外卖骑手超 45 字整句 <2 → 按判据 3 回到成因重议 `MIN_LONG_SENT`，≥2 → 回填即收口。**阻塞**：无头抓取（Camoufox stealth）被知乎登录墙挡住（2026-09-28 三篇均只拿到首页标语），需人允许用已登录 Chrome 读取，或人手粘贴原文。**(2) 补 VOICE.local 移植表「平台专属梗」一行**：模板（`VOICE.md`「移植改动表」）要求至少判断粗口/居高临下/平台专属梗三条，本机表已判前两条、缺第三条；文风属人的判断，由人补一行「保留/去掉 + 一句理由」即收口。（原提示把 ⑥ 与 N12/N16 并提：那两条是 TTS 读音表与角色表，与写稿文风无关，不冲突。）。**2026-09-28 (1) 已完成**：人允许后用登录态 Chrome 取知乎三篇原文（文本只在本机 scratchpad、不入仓），按 `rhythm()` 同规则量——外卖骑手 10.0%（30 句中 3 句超 45 字：60/52/50）、中年悲哀 43.5%（20 句）、雪之下 62.6%（57 句）；已回填 `BASELINE.md` 表并加脚注。**结论：`MIN_LONG_SENT = 2` 在最短句的人类样本上也成立，不误伤短句文风，门禁不动**。剩 (2)：VOICE.local 移植表补「平台专属梗」一行，由人补
- 收口依据：2026-09-28 两条都已完成：(1) 知乎三篇超 45 字占比补测回填 BASELINE（10.0/43.5/62.6%），MIN_LONG_SENT=2 在最短句人类样本上成立，门禁不动（`2551f84`）；(2) 人拍板在 VOICE.local（本机私有、不进 git）移植表补「平台专属梗 → 去掉：同一期视频双平台发；不分平台的通用互联网梗照常可用」。①②③⑤ 早已修（复核确认）。

### [D10] ROADMAP 核心假设未验证 + GUI 边界未答
- 状态：**已收口**（2026-09-28 复核）
- 关联：`docs/dev/ROADMAP.md` 阶段 6（「GUI / 交互壳」）与「未验证的假设」节（原行号已漂移）；ROADMAP 闸门 D
- 原记录（活跃表原文）：骨架通用性、两人不分叉、GUI 给谁用 / 审阅还是编辑。**2026-09-28 决策准备（只读）**：**④ 审阅型还是编辑型——已有答案**：Spec 10–12 已交付并定死边界——审阅 + 结构化决策（待答区卡片、05/09 决策卡）+ 纯人工文本编辑（02.5 CodeMirror 编辑器，Spec 11 §2.9「不做 LLM 辅助/diff 可视化/版本管理」）+ 封面图拖入导入（图像编辑由 agent 的确定性工具 `cover_edit` 执行，Spec 12）；**不做时间轴/画面剪辑**，未越过 ROADMAP「能拖时间轴、调画面 → 正面撞上」那条线。**③ 给谁用——事实已答、文字未跟上**：ADR-0020 已解禁并交付 Electron 桌面端（打包版、仓库根选择器、钥匙串），实际使用者是本人；ROADMAP 阶段 6 仍写「无需从零开发 Electron…套壳 Pi」，**已过时**（ROADMAP 最后改于 2026-09-16）。待拍板：(a) 明确「只给自己用」，协作者走终端/CLI（D9）；(b) 给协作者也用桌面端 → 要补 Windows 打包、报错说人话等，即 ROADMAP 说的「无底洞」。推荐 (a)，并同步改写 ROADMAP 阶段 6。**① 骨架通用性——部分成立**：逐番标定值已按番分键进 config（`visual.*`、`bgm.json`、`characters.json`），9 部番/池已跑通；但**换番仍改过 pipeline 代码**：2026-08-09 罪恶王冠给 `subindex.NON_DIALOGUE_STYLE` 加 3 种字幕组命名、2026-09-25 D7 加预告 style、N26 电影文件名集号正则——按总纲「换一部番就要改代码即缺陷」，字幕组 style 命名表宜迁 config；跨垂类（影视/游戏）的通用性完全未验证，闸门在 D9。**② 两人不分叉**：三平台 CI 已建；共享 fixture 未建（ROADMAP 自述「待建」），与 D9 同一件事，建议并入 D9 裁决
- 收口依据：2026-09-28 人拍板：③ 桌面端只给自己用；④ 边界已由 Spec 10–12 定死（审阅 + 纯文本编辑，不做时间轴/画面剪辑）；② 并入 D9 收口；ROADMAP 阶段 6 开头、闸门 D、假设 5 与阶段 2 已同步改写。① 骨架通用性中「换番仍改代码」的实例（字幕组 style 命名表写死在 subindex）另登记施工 N46；跨垂类通用性仍未验证，闸门随 D9 的协作者条件。

### [N25] 歌词混入 JPN 对白轨且无独立 style（君名本版片源），style 过滤对其无效
- 状态：**已收口**（2026-09-28 复核）
- 关联：`pipeline/subindex.py`（`NON_DIALOGUE_STYLE` 注释段，原行号已漂移）、`data/library/index/你的名字_S01E01.json`；—
- 原记录（活跃表原文）：2026-09-01 Phase 0 绕过；下部电影/下版片源会再踩。**2026-09-28 复核（只读）**：① 现状：「2026-09-01 绕过」**没有落成任何代码或配置**——当前索引（`built_at 2026-09-01`，6273 单元）里歌词仍在：如 31:19–32:20 的《前前前世》「从你的前前前世开始，我就一直在找你 / 循着你怯生生的笑容，我一路飞奔而至」被窗口与对白拼进同一检索单元。原片字幕（Haruhana BDRip `Chs&Jap.ass`）里歌词与对白同在 `CN`/`JPN` style。② 可检测性：歌词块带**成段一致的覆写标签签名**（《前前前世》`{\an7\fad(200,200)\3a&88}`、片尾《なんでもないや》39 行 `{\fad(300,500)\bord0\shad0}`），但签名**不专属歌词**——`\3a&88`、`\pos` 等同样出现在普通对白上（CN/JPN 共 3266 行，签名分布实测），所以**不能做过滤或门禁**（会误伤对白，与 D7 同理）。可做的是 Phase 0 **INFO 提示**：列出「同一 style 内连续 ≥N 行共享同一非默认、含 `\fad` 的覆写签名」的时间块，交人确认是否歌词；判据 1 上它量的是真实产物（原字幕文件）。③ 待拍板：(a) 做上述提示（`ingest`/`subindex` 只读报告，不改过滤逻辑）；(b) 仅在 Phase 0 人工核验清单加一条「电影/无独立歌词 style 的片源：抽看插曲时段是否有歌词行混入对白轨」；另需人判断已入库的君名歌词单元要不要处理——剧场版的插曲段画面多是剧情蒙太奇，危害小于 TV ED（ED 画面不可用），可能无需重建
- 收口依据：2026-09-28 人拍板：只加人工核验一条（已写入 `docs/runbook/01-topic.md`「01.4 换条件重测清单」的「开新番 · 歌词是否混进对白轨」行）；不做自动提示/门禁（覆写标签签名不专属歌词）；已入库的君名歌词单元不重建（剧场版插曲段画面多为剧情蒙太奇，危害小于 TV ED）。

### [N20] 触发式推翻条件未单独立档
- 状态：**已收口**（2026-09-28 复核）
- 关联：`ADR-0002/0003/0004/0005`；—
- 原记录（活跃表原文）：各 ADR 的 what-if 分散在库里；是否抽成备忘是结构性取舍。**2026-09-28 复核（只读）**：① 摘录：ADR-0002 已 superseded（决定二/三由 ADR-0006 取代），无推翻段；ADR-0003「什么情况下推翻本决定」（约 489 行起：第 1 层/第 2 层各自废掉、建索引耗时超量级、跨通道融合须先离线评测）；ADR-0004「什么情况下推翻本决定」（约 133 行起：集号约束致 no_match 变多、融合有离线证据）+ 正文「换番、换 embedding 模型后这个数要重测」；ADR-0005「什么情况下推翻本记录」（约 142 行）与「推翻本补记」（约 237 行）。② 判定：**这些「推翻条件」多由专门的探针/离线评测触发，日常动作碰不到，留在 ADR 里即可，不必抽表**。但同一批 ADR/配置里还散着另一类条件——「换番 / 换引擎 / 换音色 时必须重测」——它们**恰被日常动作触发，且本轮复核实证已被漏掉**：N9（PRESENCE_BAND 换多部番未重测）、N14（face_expand 五番照抄）、N19（cpm 换引擎重测了但 note 没跟上、换题材未测）。③ 方案（报人，同意后再建）：不新开 dev 文档，在生产态加一张「换条件重测清单」——落点候选 `docs/runbook/01-topic.md` 末尾一节（开新番/换音色时人必经此处），或 `docs/WORKFLOW.md` 链出的独立一页；形态 4 列：触发事件（换番/换引擎/换音色/换题材/换 embedding）｜要重测的量（PRESENCE_BAND、ccip_same/ccip_margin/face_expand、scene_threshold、cpm、voice.titles 实测秒数、readings 条目来历、CCIP provider）｜配置位置｜重测方法指针（ADR 节或 issue 行）。推翻条件本身保持原位
- 收口依据：2026-09-28 人拍板：推翻条件留在各 ADR 原位；被日常动作触发且已实证会漏的「换条件要重测」一类收成清单，落在 `docs/runbook/01-topic.md`「01.4 换条件重测清单」（触发事件 | 要重测的量 | 配置位置 | 方法指针）。

### [N8] 簇纯度判据口径未裁定（Phase 0 人工时长已回填）
- 状态：**已收口**（2026-09-28 复核）
- 关联：`docs/adr/0003:270,274`；ADR-0003
- 原记录（活跃表原文）：20 张抽检已执行，正式阈值和总时长未回填。**2026-09-28 复核（只读）**：两个空格子其实在 ADR-0003 正文里都有数，只是没回填到「待定」处，已回填（数值只落 ADR-0003，本行引用）：① Phase 0 人工时长：春物全季贴名约 10 min（64 簇贴 24 簇，ADR-0003「7 / 8」）；机器侧一季约 2.4 h（「6」）；逐角色抽检的人时未单独计时。② 簇纯度：实施中落为逐角色抽检（`vprobe presence`，抽 20 张验精确率），春物 0.05 门槛下八幡/雪乃/一色/平冢静 20/20、结衣约 18/20。**待拍板**：`vprobe.presence_probe` 文档写「错一张就……提阈值或摘名」，而结衣 18/20 当时被放行（理由：错的是大远景小人物）——二者口径冲突。候选：(a) 严格 20/20，结衣按规则提阈值或摘名；(b) 明文允许「大远景/画面小人物」类错误不计，其余错一张即处置；(c) 设精确率下限（如 ≥18/20）。另：抽检记录只见春物一部，后续番（东京喰种、罪恶王冠等）有无逐角色抽检记录未见落档——换番时是否执行过需人确认
- 收口依据：2026-09-28 人拍板：逐角色抽检（vprobe presence，抽 20 张）口径＝大远景/画面中小人物的误认不计，其余错一张即处置（提阈值或摘名）；已写入 ADR-0003「验簇纯度」处。Phase 0 人工时长已回填 ADR-0003（春物全季贴名约 10 min）。遗留小事：`vprobe.presence_probe` 的 docstring 仍写「错一张就…」，下次改 vprobe 时顺带同步到本口径。

### [N10] 周复盘 + 选题没脚本化
- 状态：**已收口**（2026-09-28 复核）
- 关联：`docs/runbook/01-topic.md`、`docs/WORKFLOW.md`（原指针 `WORKFLOW.md:225,695-697` 已失效，该文件现 78 行）；ROADMAP 阶段 2/5
- 原记录（活跃表原文）：手写 `01-topic.md`、拉平台数据手动。**2026-09-28 复核（只读）**：两件事性质不同，分开判。**选题**：已有工具面——`ava idea`（D27 无期选题会话，发散）与 `ava new <名>` 建期；落定 `01-topic.md` 的「张力」按 WORKFLOW 第 53 行是「整条流水线唯一编辑判断，定死后不许 agent 篡改」，属**刻意保留的人的判断**，不该脚本化——这一半不再是缺口，改记为设计。**周复盘**：全仓无任何拉平台数据或出复盘的代码/工具（grep 播放量/analytics/weekly 零命中）；`runbook/01-topic.md` 第 29 行明文「具体播放数字不进 git（账号数据，本机自维护）」，复盘结论以规则形式回写 runbook。能否脚本化取决于数据源是否稳定可得（B 站创作中心/YouTube Studio 都需登录态，按 AGENTS 工具序列属带凭据的浏览器交互）——这是**待人拍板**的一半：(a) 维持手工，只把「复盘→改规则」的回写纪律留在 runbook；(b) 做一个只读导出（人手导出 CSV → 脚本汇总），不碰登录态；(c) 走带登录态的自动拉取（风险与维护成本最高）。推荐 (b) 或 (a)
- 收口依据：2026-09-28 人拍板：**取消周复盘环节**（理由：平台算法差异与运气随机性大，复盘结论不可靠）；选题的「张力」属刻意保留的人的判断，不脚本化。runbook/01-topic.md 里既有的「来自自己账号的实测复盘」规则保留为历史依据，不再定期复盘。

### [D11] 角色贴名未完成 + 「佑」显示名待核对
- 状态：**已收口**（2026-09-28 复核）
- 关联：`config/characters.json:23,51,65,97`；—
- 原记录（活跃表原文）：东京喰种 / 罪恶王冠贴名进度未标完成；用 subindex 核「佑」显示名。**2026-09-28 补**：「贴名未完成」的实测影响已在 N17 行主写（262 簇未贴名中只有春物阳乃造成真实缺口，其余对现有稿件无可见影响），本行只剩「佑」显示名核对这一件待决。**2026-09-28 决策准备（只读）**：① 量化现状与影响面见 N17（主写处）：七番 262 簇未贴名，现有全部稿件的 `人物:` 只有春物「阳乃」落在 0 簇角色上。② 「佑」核定：`yuu_(guilty_crown)`（ユウ）在本地罪恶王冠全部 36 份字幕里**只出现一次**——ep20 07:15「僕はユウ ダアトの使者です」，诸神 GB 轨译作「**我叫Yu** 是Daath的使者」；素材内对他的称呼是拉丁字母「Yu」，「佑」只来自中文维基。他在罪恶王冠 clusters 里 0 个已贴名簇、现有稿件从未写过，**当前影响为零**。候选：(a) 显示名保留「佑」、别名加「Yu」（与字幕组一致，写稿时两种写法都能映射）；(b) 显示名改「Yu」；(c) 不动，等真有稿件要他时再定。推荐 (a)：零成本、不丢维基通行译名。③ 贴名分批：**不建议批量贴名**（262 簇里 261 个对现有稿件无影响，按工作量算不划算）；改为需求驱动两条——(i) 立即：扫春物 40 个未贴名簇找阳乃（~几分钟人工，见 N17）；(ii) 机制（报人）：`check_script` 对 `人物:` 映射到「0 个已贴名簇」的角色出 INFO「该角色在索引里无代表脸，在场过滤对本段无效」，让贴名只在稿件真需要时发生。改名/加别名属内容决策，拍板后另开施工
- 收口依据：2026-09-28 人拍板：「佑」不动，等稿件真用到该角色时再定（依据：本地罪恶王冠 36 份字幕仅 ep20 07:15 出现一次、诸神 GB 轨译作「Yu」，该角色 0 个已贴名簇、稿件从未写过，影响为零）。贴名不批量做，改需求驱动：check_script 对「人物:」映射到零簇角色出 INFO（已登记施工 N47）；影响面实测见 N17。

### [D9] Windows 跑通九步 + API 接入 + 共享 fixture
- 状态：**已收口**（2026-09-28 复核）
- 关联：`docs/dev/ROADMAP.md` 阶段 1/2（原行号已漂移）；ROADMAP 阶段 1/2
- 原记录（活跃表原文）：依赖闸门 A（B3，已归档/降为观测量）；fixture 未建。**2026-09-28 决策准备（只读）**：三子项逐一——**(1) API 接入：主体已达成，换个口径收口**。ROADMAP 阶段 2 要的是「不装 coding agent 也能跑完九步」：ava 宿主（`pipeline/agent/llm.py`，stdlib 直连 `{base_url}/chat/completions`，`CPA_API_KEY`）已承担写稿/查询/标题，`ava <期> /script` 即写稿入口，这条完成判据已满足；**未达成**的是第二条「同一选题跑两次稿件可复现」——LLM 采样不固定，也没人再把它当目标（稿件质量由 02.5 人审 + 02.8 对抗审查兜）。建议：把阶段 2 判为完成，「可复现」改记为已放弃的目标并写明理由。**(2) Windows 跑通九步：建议不做、收口**。现实需求：本项目是个人单机（macOS + T7 外置盘），ROADMAP 里「Windows 协作者」这条动机今天没有实际对象；闸门 A 所依赖的 B3 已被否证降为观测量。前置现状（grep 实数）：mlx 只在 `asr.py`/`tts.py`/`cloud.py` 三处且都已有 CUDA 后端可替（`qwen3_tts_cuda`、`sensevoice_cuda`/`whisper_large_v3_cuda`），`pipeline/*.sh` 两个 bash 脚本（`preflight.sh`、`record_refs.sh`），桌面端只做 macOS 打包；三平台 CI 已在跑纯函数测试（`windows-latest` 在矩阵里）。若将来真有协作者：触发条件 = 出现一个要在 Windows/Linux CUDA 上出片的具体人，届时按 ROADMAP 闸门 A 原判据验收。**(3) 共享 fixture：随 (2) 收口**。它解的是「两人不分叉」（D10 ②）的痛，单人开发不存在；CI 已覆盖依赖能否全新装上这一类最常见的漂移。候选：(a) 三项按上述收口/改写（推荐）；(b) 保留 Windows 与 fixture 为长期目标，但从活跃表移入 ROADMAP 远期节，不占 issue
- 收口依据：2026-09-28 人拍板：ROADMAP 阶段 2（API 接入）判完成——ava 宿主已直连模型 API 承担写稿/查询/标题，「不装 coding agent 跑完九步」已满足；「同一选题稿件可复现」记为已放弃的目标（稿件质量由 02.5 人审 + 02.8 对抗审查兜）。Windows 跑通九步与共享 fixture 收口：个人单机、无现实协作者；重开条件＝出现要在 Windows/Linux CUDA 上出片的具体协作者，届时按闸门 A 原判据验收。

### [N42] e2e 夹具每条用例都现场重编码同一批静态媒体
- 状态：**已收口**（2026-09-28 复核）
- 关联：`desktop/e2e/fixtures.ts::buildFixture`（约 23–75 行）；Spec 8 §7
- 原记录（活跃表原文）：2026-09-28 人报、读码核实：每次 `buildFixture()` 同步调 ffmpeg 7 次（含 libx264 现场编码 20 s 的 `05-final.mp4`、3 个 wav、4 张图）与 python 2 次（04-review.html、shots gallery），产物跨用例完全相同。推进：在 globalSetup 里生成一次缓存，用例内用 APFS 克隆（`cp -c`）拷入各自的临时仓库，夹具仍然逐用例独立；指纹用到 mtime 的用例需确认拷贝后语义不变 **2026-09-28 实测（空载）**：`buildFixture()` 单次 663–678 ms（其中 `makeFixtureRepo` 约 12 ms，余下即 ffmpeg×7 + python×2），整轮只有约 10 个调用点（preview / preview-security 用 `beforeAll` 共享，ack/session 系用另一套轻夹具），合计约 7 s，占一轮未打包全量（约 9.5 min）的 1% 上下；「每条用例重编码、数十次」与实际不符。缓存能省的上限就是这 7 s，却要引入缓存失效与跨用例共享产物的新风险，建议不做、归档，待人拍板
- 收口依据：2026-09-28 人拍板不做。实测 buildFixture 单次约 0.67 s、整轮约 10 个调用点≈7 s，占全量约 1%；缓存收益小于缓存失效与共享产物的新风险。

### [N24] 剧场版/长篇剧情场景实体核验
- 状态：**已收口**（2026-09-28 复核）
- 关联：`data/library/notes/天气之子.md:386`；—
- 原记录（活跃表原文）：2026-08-26 实测：「审讯室」实为「警车后座」；02 与 02.8 加强核验。**2026-09-28 复核（只读）**：① 规程现状：「02.8 加强核验」已落成硬交付——`skills/write-script/SKILL.md` 交付清单要求每期 `02-adversarial.md`（02.8 零上下文对抗审查 + 终审裁决），审查含「锚点时间码回笔记对撞场景描述」一节；2026-08-20 起共 8 期留有该文件（夏隧、校条祭、伪恋、你的名字、天气之子须贺、EGOIST ×3）。② 覆盖判定：出错的两期（`2026-08-24` 电车难题 第 142/148 行、`2026-08-26` 阳菜人物志 第 61 行，均写「审讯室」）**恰都没有 `02-adversarial.md`**；之后做了 02.8 的天气之子须贺期，§3 场景对撞抓出了「60:56–61:15 跨场景重叠风险」，说明这一层在工作。但 02.8 是拿稿件对笔记，**错源在笔记本身时它抓不到**——那一层归判据 11（笔记对抗审查须猎杀场景/因果错）；本例笔记已改正（`notes/天气之子.md` 现已无「审讯室」字样）。③ 结论：场景实体张冠李戴在稿件层已被 02.8 覆盖、在笔记层由判据 11 覆盖，本行无需新机制，建议收口迁 archive（已交付的两期视频属历史，不追改）
- 收口依据：2026-09-28 人拍板收口。出错两期（08-24、08-26）恰都缺 02.8；之后每期 02-adversarial.md 的「锚点时间码回笔记对撞场景描述」在工作；错源在笔记层时归判据 11；笔记已改正。

### [D12] BASELINE 语气词密度判据挂起
- 状态：**已收口**（2026-09-28 复核）
- 关联：`skills/write-script/BASELINE.md:80`；P4
- 原记录（活跃表原文）：口语体样本仅 1 篇；P4 E2 已建 diff 对样本积累机制，≥4 期后决定。**2026-09-28 决策准备（只读）**：① 样本计数：「≥4 期」已满足——P4 E2 的「初稿→人改定稿」配对现有 5 期标准期（夏隧、校条祭、伪恋、你的名字、天气之子须贺）+ EGOIST 7 组（含 archive 版本）。② 可观测量先跑（统计脚本不入仓；只数 `配音:` 行，语气词取 啊吧嘛哇呢呀啦哦嘞呗咯喽，按汉字每百字）：12 组初稿密度 0–0.32、定稿 0–0.31，**人改稿前后几乎零变化**（最大差 0.01），EGOIST 全系列恒 0。③ 结论：这个量在本项目的真实写稿链里**不承载人的修改意图**——人在 02.5 从不增删语气词；BASELINE 的「口语体 1.21」唯一来源是一篇非动漫知乎回答（文风参照而非本项目目标）。按判据 1/2（先问测的是不是真实产物、能卡门槛不代表该卡），**建议不立语气词密度判据，D12 收口**，把 BASELINE「量过但分不开、因而不设门禁」表里该行补一句本次实测理由即可（文案改动待人拍板）。候选：(a) 收口（推荐）；(b) 保留挂起，改等「人明确想要口语化文风」时再议
- 收口依据：2026-09-28 人拍板收口：不立语气词密度判据。依据（实测）：12 组「初稿→人改定稿」配对，语气词密度 0–0.32/百字，人改前后几乎零变化——这个量不承载人在 02.5 的修改意图；BASELINE 的「口语体 1.21」唯一来源是一篇非动漫知乎回答。

### [D2] 无台词画面的排片死角——02 强制确认机制未落实
- 状态：**已收口**（2026-09-28 复核）
- 关联：`docs/dev/adr/0005`（判断 4 附近）、ADR-0008 锚点字段、各期 `04-clips.approved.json` 的 `channel`；ADR-0005, ADR-0008, v2重构方案
- 原记录（活跃表原文）：ADR-0008 锚点强制字段已垫底；v2 云端 Qwen2-VL 逐镜头意象打标彻底消灭无台词死角。**2026-09-28 决策准备（只读）**：用全部 21 期（不含 archive 版本）`04-clips.approved.json` 的 `channel`/`status` 实数核：合计 438 段，**锚点通道 235 段、台词通道 144 段、早期无 channel 字段 59 段；「场景」（ADR-0015 VLM 意象）通道 0 段；全部期 `status` 为 ok/ok_extended，零落空**。2026-08-31 之后的期几乎全走锚点（伪恋、你的名字、EGOIST 五期 100% 锚点）——没有台词可检索的画面由写稿时按笔记直接给时间码落片。**更正**：本行原述「v2 云端 Qwen2-VL 逐镜头意象打标彻底消灭无台词死角」与事实不符——画面语义索引（`vindex/*captions*`）共 59 份——EGOIST 素材池 58 份 + 罪恶王冠 S01E01 1 份（其余番未建），但**生产期稿件从未走过该通道**（EGOIST 五期也全走锚点）。结论：死角**已被 ADR-0008 锚点机制消灭**（「02 强制确认」即锚点字段 + `anchor_none` 显式声明，已落地），D2 建议收口迁 archive；VLM 场景通道的去留与本条无关，属 D22/ADR-0015 的账。**推翻条件**：05 人审里出现「无台词段因锚点缺失/写错而落到台词通道错配」被打回的实例，或出现 `no_match`——出现即重开
- 收口依据：2026-09-28 人拍板收口。依据（实测）：21 期 438 段中锚点通道 235、台词 144、场景(VLM) 0，全部 ok/ok_extended 零落空；死角由 ADR-0008 锚点字段 + anchor_none 显式声明消灭。推翻条件：05 出现无台词段因锚点缺失/写错落到台词通道错配被打回，或出现 no_match。

### [D28] 工具循环 10 轮上限对网络调研偏少
- 状态：**已收口**（2026-09-28 复核）
- 关联：`pipeline/agent/llm.py:33`（`DEFAULT_MAX_ITERATIONS`）、`pipeline/agent/tools.py::_tool_web_fetch`；Spec 4, impl-spec B3-r5
- 原记录（活跃表原文）：2026-09-24 S20 门禁 8 冒烟实测：asset 抓 bgm 一色彩羽，搜索页撞游客登录墙（200 但只有导航栏），随后 9 次 fetch 在作品页与 API 间绕路，没去能直接抓到简介的 `/character/26090` 就耗尽 10 轮。10 这个数本身也没写依据。2026-09-24 用户裁决：**不设固定轮数上限**，停止前必须先做无工具收尾总结，防失控改用「人随时中断 + 完全相同的调用拒绝执行」（归二期 Spec 9）；不靠人指路，改为让模型拿到更好的信息：web_fetch 返回页内链接清单，scope 提示写通用研究策略，站点经验进 memory.md（归二期 Spec 13）；crawl/browser 的 extras 由人安装。到顶回显工具原始 JSON 是另一个 bug（`llm.py` 上限时 `final=convo[-1]` 即最后一条 tool 消息），已交 S25——**S25 已修**：`_dispatch_agent_turn` 在 `max_iterations` 时只打 WARN 交人接管，不回显 `final` 原文（`tests/test_agent_director.py::test_repl_max_iterations_warning` 锁死，变异检验杀死）
- 收口依据：核心裁决（不设固定轮数上限，靠人中断 + 本轮判重 + 检查点）由 Spec 9 落地（`llm.py` 的 CHECKPOINT_EVERY 与 `dedup_key`）；三件配套事逐条核实已落地：① `web_fetch` 返回 `links`/`links_truncated`（`web.py::_extract_links`/`_cap_links`，工具描述已同步），实跑 docs.python.org 取回 58 条；② 「联网研究策略」节已进 `config/agent/scopes/creative.md` 与 `asset.md`；③ 站点经验经 `write_memory` 走 ADR-0023 记忆通道（首次写入人确认）；crawl/browser 依赖为 pyproject 可选组并附人工安装命令（crawl4ai-setup / playwright install）。实跑中另发现 `web_fetch` 不处理 `Content-Encoding` 的静默失败，已单列 N45，不属本条。

## 2026-09-28：收尾批评审通过（独立评审 session）

### [N37] VE-1 在负载下的全量 e2e 里稳定红：脱盘后「期列表清空」10 s 内没落定
- 状态：**已解决**（施工 86b09c0；2026-09-28 独立评审通过）
- 关联：`desktop/e2e/visual.spec.ts:201`（VE-1 ⑩ stale 夹具）；嫌疑在 host 脱盘后的期列表推送
- 原记录（活跃表原文，含施工回填）：2026-09-28 D32 施工时发现（与 D32 无关）：6 个 `yes` 占满 CPU 跑未打包全量 e2e，**4/4 次**红在同一处——`await expect.poll(() => episode.count()).toBe(0)` 10 s 超时，实得 **3**；而紧前一行 `reach-banner` 已在 10 s 内出现。VE-1 单跑、负载下单跑、`visual.spec.ts` 整文件、空载全量均绿。所以不是审计判据问题，而是「reach 已翻红、期列表却迟迟不空」：host 的 `refreshEpisodes()` 在 `reach !== ok` 时直接 return，期列表保持旧 Map；清空要靠别的时机（待查是哪条推送、为何在全量套件后段才慢）。推进：先查脱盘后 `episodes.summary`/`episodes.list` 的实际推送序列（打点，不猜），判定是夹具预算问题还是 host 在脱盘后推了陈旧列表的真实缺陷；不许靠调大 10 s 了事（判据 3） **2026-09-28 定位与修复（host 缺陷）**：机理＝chmod 000 后 `listEpisodes` 因 `stat(episodes)` EACCES 返回空；活跃期 tick（1 s）若抢在 reach 轮询（2 s）之前，`dirExists` 失败→当成「期目录被删」→`refreshEpisodes()` 把期列表清空；reach 先翻红则 tick 与 `refreshEpisodes` 都早退、旧列表永留。空载下多半 tick 先到（列表清空），负载下多半 reach 先到（列表保留，VE-1 红）——同一次拔盘界面取决于竞态。Spec 8 §2.8 / §2.4 表明文「保留最后快照并标陈旧」、Spec 14 mock-06（stale 基准）期列表保留 5 行，故判清空为缺陷。修：`tickEpisodeOnce` 发现期目录不在时先 `pollReach()`，数据根不可达即早退（按脱盘处理），可达才当期目录被删。用例：service.test `N37` 两条（tick 抢先的必现序 + 对照组「只删期目录→照常刷新」），另断言脱盘 tick 不推「期目录已不存在」；修前红（列表 `[]`）。变异 2/2（去 pollReach / 去早退）。VE-1 ⑩ 原 `toBe(0)` 正是把该竞态写成了期望，改为「陈旧标记可见 + 期行数与脱盘前一致」，stale 下限 67→83（三主题实测恒 83，下限只提高）。**评审注意**：VE-1 断言与下限是改期望，依据是 Spec 8 §2.8 与 mock-06，请独立核对这一判断；负载下全量 ×3 结果见下。 **负载验收（6×`yes`，未打包全量 ×3）**：VE-1 3/3 绿（修前 4/4 红）；三轮中第 2、3 轮各红 1 条 TI-10（与本条无关，另登记 N44）。
- 评审：✅ 通过。① 修前复现（实测）：手工回退 service.ts 修复 + VE-1 ⑩ 旧期望（`toBe(0)`），6×`yes` 负载下全量 e2e——VE-1 红、其余 110 过 / 2 跳过，与原报「4/4 红在同一处」同签名；还原后两文件逐字节归位。② 改期望依据核对（实测读 spec）：Spec 8 §2.8 原文「脱卸期间……renderer 保留最后快照并置灰标『陈旧』」支持期列表保留，清空才是缺陷；stale 下限 67→83 与「列表保留后 n 增多」自洽。③ 变异 2/2 实跑（还原后 md5 对拍一致）：去 pollReach 重判 → N37 必现序用例红；去早退 → 同用例红。④ 负载验收（实测）：6×`yes` 下未打包全量 ×3 全绿（111 passed / 2 skipped ×3，含 VE-1）。⑤ 边界：diff 只动 service.ts tick 分支、VE-1 ⑩、N37 两条单测，未碰 reach 轮询与 refreshEpisodes 的其它路径。备注：对照组「只删期目录 → 照常刷新」由施工方用例覆盖，评审复跑全绿（含在上面 vitest 抽查）。

### [N38] 桌面端变异 harness 把「e2e 未选中任何用例」记成红，构建坏掉的变异会报**假 KILLED**
- 状态：**已解决**（施工 5dd7825 + 4a13b4e；2026-09-28 独立评审通过）
- 关联：`desktop/scripts/verify-mutations.mjs:84`
- 原记录（活跃表原文，含施工回填）：2026-09-28 N34 施工实测：MUT-62′ 首版的 `//` 注释落在行中间把整行后半截注释掉，`electron-vite build` 失败，e2e 一条用例都没选中，harness 却判 `KILLED (1s) red=1 :: <e2e 未选中任何用例…>`。后果：任何「把构建弄坏」的变异都会被当成已杀死，变异矩阵的 KILLED 数被虚增。推进：构建失败 / 零用例归为 OTHER（或单列 BUILD_FAIL）且不计杀死；补 harness 自测；用新判定复跑 TS 侧全表，确认没有历史 KILLED 实为构建失败 **2026-09-28 修复（`5dd7825` + `4a13b4e`）**：跑器层面的问题从红条名单分离为 `problems`，判定新增 `BUILD_FAIL`（无报告 / 零 spec 时的顶层错误如 global-setup 构建失败 / vitest 文件级失败且零断言）与 `NO_TESTS`（零用例，含 Playwright「No tests found」），二者优先于红条判定、一律不计杀死；用例已跑时的顶层错误（如 Worker teardown timeout）只进 `notes`。旧版假 KILLED 的机理：占位串塞进红条，串里带着 grep（多半就是期望编号）。自测：`tests/harness/verifyMutations.test.ts` 11 条（纯函数，每条关键分支去掉即红）+ `--self-test` 实跑三条人造变异（坏 e2e 构建 / grep 不中 / vitest 导入源码编译失败）——HEAD 旧判定对前两条报 KILLED，新判定分别 BUILD_FAIL / NO_TESTS / BUILD_FAIL。**全表复跑（净树，N41 落地后）**：69 条 → 68 KILLED、1 SURVIVED（MUT-62，矩阵预期）、BUILD_FAIL/NO_TESTS 0——**历史 KILLED 无一实为构建失败**，Spec 10 §7/§8 无需更正。过程中发现首版把 teardown 超时误判为 BUILD_FAIL（MUT-47/48/49，期望用例其实真红），已由 `4a13b4e` 修正并 `--only` 复跑三条均 KILLED。**评审注意**：① 用「先退回旧判定」复现假 KILLED（`--self-test` 的两条 e2e 人造变异即可）；② 挑战「零 spec 才算 build」这条边界：有没有「部分 spec 跑了、构建其实坏了」的形态
- 评审：✅ 通过。① `--self-test` 实跑（实测）：三条人造变异各归其位——e2e 构建失败 → BUILD_FAIL、grep 选不中 → NO_TESTS、vitest 导入编译失败 → BUILD_FAIL；零假 KILLED。② 自测真能红（实测）：把 BUILD_FAIL/NO_TESTS 优先级分支置否后，--self-test 三条全 FAIL（got=SURVIVED，即「没跑就当存活」同样是误判方向）、harness 单测 11 条红 2 条；还原后 md5 对拍一致、自测复绿。③ 抽查复跑（实测）：MUT-47（首版被 teardown 超时误判 BUILD_FAIL 的那条）经 `--only` 重跑得 KILLED (184s)，MUT-62 得 SURVIVED——与矩阵「等价变异」预期一致。④ 「零 spec 才算 build」边界挑战：Playwright 构建在 global-setup，构建坏则零 spec 跑，不存在「部分 spec 跑了但构建坏了」的形态；vitest 侧「一文件编译失败 + 另一文件真红」会判 BUILD_FAIL 掩盖真红——方向是保守侧（少记杀死、不虚增），可接受。全表 68 KILLED / 1 SURVIVED / 0 BUILD_FAIL 的数字为施工方转述（结果文件已不在），评审未全量重跑。

### [N41] 桌面端 e2e 每条用例都弹出并激活一个 1280×820 窗口，全量跑下来本机无法正常使用
- 状态：**已解决**（施工 d28c736；2026-09-28 独立评审通过）
- 关联：`desktop/src/main/index.ts`（`createWindow()`：未隐藏、未隐藏 Dock 图标）、`desktop/e2e/fixtures.ts`（`electron.launch`）
- 原记录（活跃表原文，含施工回填）：2026-09-28 人报、读码核实：8 个 spec 文件共 112 个 `test()`，每条独立 `electron.launch()` → `app.close()`，全量约十余分钟、平均 5–6 s 抢一次键盘与前台焦点；变异实跑按条目数放大（N38 全表跑到 47/69 时因此被人叫停）。D37 已有「键盘探针会把按键打进其他前台应用」的同类教训。推进：只对未打包构建且由 e2e 启动的实例加测试开关（启动即隐藏 Dock 图标、窗口 `showInactive()` 不激活 app），**打包版零改动**；不用 `show:false`（Chromium 会压低隐藏窗口的绘制，VE 视觉审计与截图可能失真）；键盘/焦点类用例（TX-14/14b、VE-3 等）在无系统焦点下是否仍成立必须全量实测 **2026-09-28 修复（人裁插队）**：未打包构建新增测试开关 `--ava-test-background`（`fixtures.ts::launch` 默认带上），三件事：`app.setActivationPolicy("accessory")`（不进 Dock、不激活 app）；窗口 `show:false` 后 `showInactive()`（可见、照常绘制、不抢焦点）；**显示之后**再移到主屏工作区右下角只露 32×32（先移后显示会被 macOS 在上屏时拉回，实测）。完全移出屏幕与透明度方案未采用（前者会被判遮挡、Chromium 停绘制；后者人裁暂不做）。实测：窗口内 `visibilityState=visible`、rAF 62/s；前台采样对照——改前单条 VE-3 即被 Electron 抢走，改后整轮全量见下。守卫 e2e `N41`（可见/未聚焦/不在 Dock/dx=dy=32）；变异 3/3（去 accessory→dock:true；照常 show→focused:true；先移后显示→dx/dy 变整窗）。打包版零改动（未打包才解析、打包版本就拒绝未知 argv）。
- 评审：✅ 通过。① 变异 3/3 实跑（还原后 md5 对拍一致）：m1 去 accessory→N41 用例红（dock:true）；m2 showInactive 退回 show→红（focused:true）；m3 先移后显示→红（dx/dy 变整窗）——杀手均为守卫用例 N41 本身。② diff 边界：只动 `main/index.ts`（dev.background 分支，打包版 `readDevSwitches` 不解析该开关）、`fixtures.ts`（launch 默认带开关）、`visual.spec.ts`（守卫用例）、issues 行——打包版零改动成立。③ 全量佐证（与 N44 同轮）：vitest 377 passed、未打包全量 e2e 111 passed / 2 skipped、真实数据零污染；N44 的 TI-10 在 background 模式下绿。备注（转述未复核）：「整轮前台采样 1067 次零抢占」为施工方实测。

### [N40] 协议进程以 SIGINT=SIG_IGN 启动时，`interrupt` 全部失效
- 状态：**已解决**（施工 40cb104；2026-09-28 独立评审通过）
- 关联：`pipeline/agent/protocol.py`（启动序列）、`session.py::TurnInterrupt`
- 原记录（活跃表原文，含施工回填）：2026-09-28 D31 负载复现时实测：非交互 shell 里 `&` 起的后台进程继承 SIGINT=SIG_IGN，Python 不装默认处理器，`pthread_kill(SIGINT)` 被内核丢弃——TP-4（等卡时中断）24/24 红，同一用例前台跑全绿。桌面端经 libuv spawn 会重置信号，当前不受影响；但 harness/脚本/将来别的宿主在后台起 core 就会踩到（中断按钮无效、回合停不下）。推进：启动时显式 `signal.signal(SIGINT, signal.default_int_handler)`（或等效），并补「以 SIG_IGN 继承启动仍能中断」的用例 **2026-09-28 修复（已修·待评审）**：`protocol.py` 在装 SIGTERM 处理器处紧接着 `signal.signal(SIGINT, signal.default_int_handler)`（中断只在 ready 后带 turn_id 才有效，装在这里够早）。新增 TP-4d：Popen 时父进程临时 SIG_IGN 让子进程继承，断言等卡中断 <5 s 内 `turn_finished{interrupted}`——修前红（收不到 turn_finished）、修后绿。变异 2/2：A 删恢复行→TP-4d 红；B 装成 SIG_DFL→TP-4/4d/4b/4c 四红（SIG_DFL 收 SIGINT 直接杀进程，空闲 notice 与回合内落点都接不住，所以必须是 default_int_handler）。原始场景复跑：非交互 `bash -c '… & wait'` 后台跑 TP-4 族 6/6 绿（原 24/24 红）；全量 1957 passed。终端 `ava`（cli.py）不用 `pthread_kill`，只靠 tty 的 Ctrl-C；以 SIG_IGN 启动的终端进程本就是启动方有意忽略（nohup 等），不改。**评审注意**：修后以 SIG_IGN 启动的协议进程也会接住外部 SIGINT（空闲发 notice、回合内中断）——这是本条要的语义，但与「启动方想忽略 SIGINT」相反，host 只用 SIGTERM/shutdown 关停，不受影响。
- 评审：✅ 通过。① 变异 2/2 实跑（`PYTHONDONTWRITEBYTECODE=1`，还原后 md5 对拍一致）：A 删恢复行→TP-4d 红（中断 10 s 无 `turn_finished`）；B 装成 SIG_DFL→TP-4/4b/4c/4d 四红（SIGINT 直接杀进程，空闲 notice 与回合内落点都接不住）——与施工回填一致，且证实非 `default_int_handler` 不可。② 原始场景复跑（实测）：非交互 `bash -c '… & wait'` 后台跑 TP-4 族 6/6 绿（修复前此场景 24/24 红）；`tests/test_agent_protocol.py` + `test_agent_session.py` 63 passed，TP-11（SIGTERM）单跑绿——关停与空闲 notice 语义未破。③ diff 边界：只动 `protocol.py` 启动序列 1 行 + 测试 + issues 行，未碰帧构造与 `_on_term`。施工方提示的语义变化（以 SIG_IGN 启动的进程修后也会接外部 SIGINT）已核：host 关停只用 SIGTERM/shutdown，不受影响，成立。

### [N34] 桌面端 e2e 的四处断言缺口（变异实跑暴露）
- 状态：**已解决**（施工 a9e2a77；2026-09-28 独立评审通过）
- 关联：`desktop/e2e/session.spec.ts`（假进程版 TX-1 / TX-8 / TX-14）、`desktop/src/renderer/App.tsx`（onConnect 调用点）；Spec 10 §7 变异表
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 桌面端变异 68 条实跑 66 KILLED。① 假进程版 TX-1 只在待答区内数批准按钮（spec 要求整页），MUT-26 在它下面存活 → 目标改指真实 core 版；② 假进程版 TX-8 不断言确认框桩被调用，MUT-39 存活 → 同上；③ **MUT-46（RequestCard key 改下标）PARTIAL**：静态守卫 TG-17 红，但能造并发两卡的假进程版 TX-14 仍绿，DOM 复用未导致第二次 Enter 答到第二张卡，机理**未证实**；真实 core 的卡串行出现、无从检验；④ **MUT-62 SURVIVED**：变异在 App.tsx 的 onConnect 调用点，单测打不到，e2e 无「host 重启跨越自动呼出等待」路径。③④ 待补用例。**2026-09-28 施工**：①② 核实（读码，未重跑）：真实 core 版 TX-1 以 `[data-testid^=request-answer]` 数**整页**按钮、TX-8 断言 `quitStubCalls == 1`，矩阵已指向真实版，无需补。③ MUT-46 取 (a)：机理＝答复即 `setBusy(true)` → 按钮 `disabled` → 焦点离开，第二次 Enter 与 key 无关（推断，与「变异下 TX-14 仍绿」一致）；key 真正守的是**组件状态归属**，新增假进程版 **TX-14b**（第一张卡写反馈并拒绝后，第二张卡的反馈框为空、其拒绝帧 `feedback:null`）。harness 实跑 **KILLED**（TG-17 + TX-14b；TX-14b 原文 `Expected: "" Received: "只改第一段"`）。④ MUT-62：补 e2e **TX-15b**（回合在跑时 kill -9 host → 重连 → 追加新 pending 必须照常呼出；回合中不写对象库，避开新 host 激活时 H1 自愈 supersede 另建新号的干扰——首版用例正是被它假红）。MUT-62 实跑仍 **SURVIVED**，判为**等价变异**：重连后 `fetchConv(当前键, resetAuto=true)` 以新 snapshot 做同一次 reset，其间无可达的 pendings 事件；两处 reset 一起去掉的 **MUT-62′ → TX-15b 红，KILLED**。矩阵与 Spec 10 §8 如实改写（MUT-46 期望杀手改为 TG-17/TX-14b；MUT-62 标等价、新增 MUT-62′），**没有把 SURVIVED 记成 KILLED**。验证：`vitest` 364 passed、`tsc --noEmit` 0、未打包全量 e2e 110 过 / 2 跳过；三条变异均经 `scripts/verify-mutations.mjs` 实跑、还原后 md5 一致。**顺带发现（未修，报人）**（已单独登记为 N38）：`verify-mutations.mjs:84` 把「e2e 未选中任何用例」记成一条红——变异若把构建弄坏（本次 MUT-62′ 首版就是：`//` 注释落在行中间），harness 会报 **假 KILLED**；判定应把构建失败/零用例归为 OTHER
- 评审：✅ 通过（附一处措辞更正）。① 亲自用 N38 修后的 harness 实跑三条（`--only`，净树）：MUT-46 **KILLED**（TG-17 + TX-14b 双红）、MUT-62 **SURVIVED**、MUT-62′ **KILLED**（TX-15b）——与矩阵写法一致；MUT-46 已由 TX-14b 真杀，矩阵与报告未把 PARTIAL 冒充为 KILLED。② 新用例能真红：TX-14b 由 MUT-46、TX-15b 由 MUT-62′ 实证。③ **更正**：矩阵称 MUT-62 为「等价变异」不严谨——同一行自承重连后 `episodes.list`/`conv.snapshot` 失败时 onConnect 的同步 reset 是唯一一次 reset，即失败路径可观测、只是无用例；已把 Spec 10 §7 该行、§8 回填段与 `mutations.mjs` note 改为「成功路径不可观测、失败路径无用例」，判定仍为预期 SURVIVED。④ 🔵 观察（不阻塞）：TX-15b 用 `waitForTimeout(3000)` 作「计数不变」的观察窗，负载下窗口可能不够；后续 `approval-id === \"after\"` 断言兜住误判为绿的风险。本次负载全量 ×3（N37 验收同批）中 TX-14b/TX-15b 3/3 绿。⑤ diff 只动 `e2e/session.spec.ts`、`scripts/mutations.mjs`、Spec 10 与 issues 行，未越界。

### [D32] TA-12 在全量 e2e 负载下红一次：旧代号的 STATUS 结果进了 snapshot
- 状态：**已解决**（施工 e4243fa；2026-09-28 独立评审通过）
- 关联：`desktop/e2e/ack.spec.ts`（TA-12）、host 侧 repoRoot 切换与在途命令互斥；Spec 8, Spec 10
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 全量 e2e（100 过 / 1 跳过）中唯一红条；单跑 3/3 绿。红的是**防线断言本身**（「旧代号 STATUS 结果不进任何 snapshot」），不是超时类——更像 host 在切换窗口里的真实竞态被负载放大，而非测试 flake。推进：负载下循环复现（如 `--repeat-each` + 并行 CPU 压力），抓到后查切换时代号递增与在途 STATUS 回包的先后；不许靠加等待消掉。**2026-09-28 定位与修复（测试时序竞态，host 代号机制无误）**：给 TA-12 的 MutationObserver 记录打阶段标签后，负载下 15 次未自然复现；注入「`writeClips` 后 6 s 才 `arm`」即必现（2/2），且红的 `05` 出现在 **observer 阶段——切换尚未开始**：它是写入 05 之后、`arm` 抵达 host 之前完成的一次常规活跃期轮询（当前代号，合法），不是旧代号结果泄漏。负载只是拉长了「`arm` 经 main→host IPC 生效」的窗口。反过来，旧顺序下被扣住的若是写入前开始的那次 STATUS，它读到 03.5，防线断言空转。修法（只改用例，host 零改动）：钩子是一次性的，改为「arm→扣住 H1（写入前）→写 05→再 arm→放 H1→扣住 H2（必在写入后开始、读到 05）→确认页面仍是 03.5→装 observer→切换」，全程按条件排序、零新增等待，断言原样保留。验证：注入 6 s 延迟后仍绿；变异「删 `loadStatus` 的代号比较」→TA-12 第 512 行防线断言红（还原后 md5 一致）；TA-12 单跑 3/3 绿；负载（6 个 `yes`）下未打包全量 e2e ×3 中 TA-12 三次全绿，但另有无关红条（VE-1 三次、剪贴板「假设 3」一次，均单跑与空载全量绿，另行登记）；空载全量 108 过 / 2 跳过。**顺带发现（未修，报人）**（已单独登记为 N39）：`requestRepoRootChange` 在切换开始即 `repoGen += 1`，但 `repoRoot` 要到 `resolveRepoRoot()` 才换——切换窗口内新起的 `refreshEpisodeStatuses`（30 s 定时器）会拿到**新代号 + 旧根**，其结果通过代号比较、写入 `summaries` 并推 `episodes.summary`（侧栏摘要，不进期 snapshot）。未构造出可见现场，属纵深缺口；修法候选：`resolveRepoRoot()` 后再递增一次代号，但这触及 Spec 8 代号语义，须人拍板
- 评审：✅ 通过。① 原始复现（实测）：取修前写法、在 arm 前注入 6 s 等待 → TA-12 红在第 512 行防线断言（05 在切换开始前被合法渲染）；修后写法在写入前注入同样 6 s → 绿。② 不变量「切换开始即作废旧代号在途结果」（实测变异，均由第 512 行 `steps.filter(05) == []` 杀死，还原后 md5 一致）：M1 `loadStatus` 去代号比较；M2 代号递增挪到 `resolveRepoRoot()` 之后；M3 切换不递增代号 → 3/3 KILLED。③ diff 只动 `e2e/ack.spec.ts` 的 TA-12 与 issues 行，host 零改动；未新增任何等待（两段扣放按条件排序），防线断言原样未收窄。读码旁证：`refreshEpisodeStatuses` 不经 `loadStatus`、只写侧栏摘要不进 snapshot，不构成绕过（其「新代号+旧根」窗口已单列 N39）。④ 负载（6×`yes`）下未打包全量 ×3（N37 验收同批）：TA-12 3/3 绿；该批另有 TI-10 两次红，与本条无关（N44）。

### [D31] TP-6 在全量负载下偶发红
- 状态：**已解决**（施工 df88840；2026-09-28 独立评审通过）
- 关联：`tests/test_agent_protocol.py`（用例名见该文件 TP-6）；Spec 9, Spec 10
- 原记录（活跃表原文，含施工回填）：2026-09-26 Spec 10 PR0 验收复跑中实测：整套 pytest 下偶发红一次（施工方报红 4、验收方同条件测得红 5）；该用例单独跑 3/3 绿、干净树 3/3 绿，确认与本轮变异无关。风险点是**变异 harness 的红条数被当门禁数字**时，flake 会污染计数（`scripts/verify_mutations.py` 已用「中止轮标 ABORTED」堵住另一类假红，这条是超时/竞态类，未堵）。推进：先复现并定位（怀疑与 TP-6 的 busy 帧时序有关），再决定是收紧时序断言还是给该用例加隔离；不调阈值。**2026-09-27 M9 复现**：Spec 9 变异全表实跑（4 分片并行、机器满载）期间再次出现同形态偶发红，单跑仍绿；harness 已加单轮 15 分钟超时，但计分仍会被它污染，复跑非 KILLED 条目时需单独确认。**2026-09-28 修复（测试侧竞态，非 core 缺陷）**：负载下自然复现未抓到（TP-6 单条 96 次、协议+会话四文件 15 轮×94 条、全量 3 轮，全绿），改用注入定位——在两次 `send` 之间插 0.3 s（模拟测试进程被抢占）即必现，失败原文 `等不到 error；已收到 [ready, notice, stop_points, turn_started, assistant, turn_finished, stop_points, turn_started, assistant, turn_finished, stop_points]`：假端点秒回，「甲」已跑完、「乙」开了第二轮，E_BUSY 无从产生。core 的忙判定（读者线程置 `in_flight`）无误。修法＝等待条件化：「甲」改为停在 `test_slow` 人审卡上（卡未答复＝回合必在进行中）再发「乙」，断言收紧为恰 `E_BUSY`、两条坏帧恰 `[E_BAD_REQUEST, E_BAD_REQUEST]`、被拒帧不进对话；零 sleep / 零 skip / 零阈值改动。验证：注入 0.5 s 间隔仍绿；变异 2/2（删 `E_BUSY` 分支→TP-6 红；键集合校验放宽为 ⊇→TP-6 红，旧断言 `in codes` 放得过它）；负载（8 个 `yes` 占满 10 核）下全量 `uv run pytest` 1956 passed ×3。harness 无需加「单跑复核」标记（竞态已从用例里消除）。**顺带发现（未修，报人）**（已单独登记为 N40）：协议进程若以 SIGINT=SIG_IGN 启动（非交互 shell 里 `&` 起的后台进程默认如此），Python 不装默认 SIGINT 处理器，`interrupt` 全部失效（TP-4 24/24 红）；桌面端经 libuv spawn 会重置信号，不受影响，但 harness/脚本若在后台起 core 会踩到
- 评审：✅ 通过。① 原始复现（实测）：取修前写法、在两次 send 间注入 0.3 s → 红（等不到 E_BUSY，进程超时被杀 finish()==-9）；同注入下新写法绿。② 「为什么不是 core 竞态」（读码）：`protocol.py::_dispatch` 由读者线程在读到「甲」时同步置 `in_flight`，「乙」拿不到 E_BUSY 只可能是「甲」整轮已结束（主循环 finally 清标志），判测试侧竞态成立。③ diff 只动 `tests/test_agent_protocol.py` 的 TP-6 与 issues 行；无 sleep / flaky / skip，断言收紧（E_BUSY 精确、两条 E_BAD_REQUEST、被拒帧不进对话）。④ 变异 3 条实跑（还原后 md5 一致）：删 E_BUSY 分支 / 忙时回 E_NOT_READY（旧断言 `in (E_BUSY, E_NOT_READY)` 会放过）/ 回合结束不清忙标志 → 均被 TP-6 杀死。⑤ 负载（6×`yes`）下全量 `uv run pytest` ×3：1957 passed ×3（实测；N40 修复后 `&` 后台 SIG_IGN 假象亦不再出现）。

### [N36] 会话头降级文案溢出并与对话流重叠
- 状态：**已解决**（施工 db44529；2026-09-28 独立评审通过）
- 关联：`desktop/src/renderer/SessionHeader.tsx` + `style.css`；Spec 10 §2.9 第 6 条
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 门禁 6(b) 实测（钥匙串条目删除后）：`LLM 未就绪：…` 与 `设置密钥：security add-generic-password -s ava -a CPA_API_KEY -w ｜不注入密钥…` 整段在会话头换行后越出自身盒子，与对话流首行、消息气泡互相压字（截图两处独立复现）。文本内容本身正确、命令与 `secrets.ts:15` 逐字一致，属排版缺陷；建议给该块加 `max-height + overflow: auto` 或收成可展开的一行。**2026-09-28 施工**：取「max-height + overflow」（不改 DOM；`<details>` 要动元素种类，越出 Spec 14 重构边界）。根因 = `ui.css` 的 `.session-head` 写死 `height: 40px` + `align-items: center`，多行文案向上下两个方向越出（900 宽实测子元素顶端 299.5、头部顶端 378.5）。`ui.css` 有 sha256 冻结，覆盖写在 `style.css`：`height:auto; min-height:40px; max-height:30vh; overflow-y:auto; flex-wrap:wrap`，单行时仍是 40px；零颜色、零字号、零文案与 `data-testid` 改动，也没改 TSX。用例 e2e `N36`（900/1280 两种宽度，按几何断言）：头部底边不超过对话区顶边、子元素全部在头部盒子内、常规窗口下 `scrollHeight ≤ clientHeight`（挡住「40px + 内部滚动」式假修）、`key-problem` 整段选中后含完整命令。变异 2/2：删规则（原状）→ 子元素越出顶端红；保留 40px 只加滚动 → 子元素越出底端红。深/浅两态截图已目视：无压字、配色不变。代价：800 高窗口里降级态头部约占 200px，对话流变窄（上限 30vh）。验证：vitest 364 passed（含 VS-3/4/5 扫 style.css 与冻结 sha256）、`tsc` 干净、未打包 e2e 108 passed / 2 skipped
- 评审：✅ 通过（打包版真机门禁 6(b) 留人）。① 深/浅两态（实测，未打包构建 + `emulateMedia` 截中栏）：降级说明与 `security add-generic-password -s ava -a AVA_TEST_KEY -w` 整段落在头部盒子内、不压对话流，两态文字均可读；命令仍在同一文本节点可整句选中（用例末段断言）。diff 只动 `style.css` 一条 `.session-head` 覆盖，文案与 `data-testid` 零改动。② 只引用 `--space-1`，不新增颜色/字号；`ui.css`（sha256 冻结）未动，未越 Spec 14 边界。③ 断言是几何的（头部不压 `.conv`、子元素全在盒内、常规窗口 `scrollHeight ≤ clientHeight`），不是只看文案：变异 3 条实跑（还原后 md5 一致）——A 删掉覆盖（原状）→第 453 行红；B「固定 40px + 内部滚动」假修→第 454 行红；C 只换行不长高→第 454 行红。观察（不阻塞、交人判）：窄中栏下降级态头部占去大半高度，对话流只剩一线（施工报告已提）；中栏「人时」行贴左缘无内边距，属既有排版、与本条无关


### [N32] 会话未启动时「素材模式」按钮可点，但点了静默无反应
- 状态：**已解决**（施工 96a3b86；2026-09-28 独立评审通过）
- 关联：`desktop/src/renderer/SessionHeader.tsx`（素材模式按钮）；Spec 10 §2.6
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 真实 core 联调中发现：无会话时 scope 切换无处可发，界面既不禁用也不提示。应禁用或给出「先开始会话」提示；归 M10 之后的 UI 收尾。**2026-09-27 施工**：取「允许点、给明确提示」（每次点击都有可观察结果；禁用态只能靠 tooltip 说明原因，且不自动起会话，守 H-8）。`SessionHeader.tsx`：`onClick` 首句仍是 `isTrusted` 守卫，其后 `!live` → 置提示并 return，零 RPC；提示 `data-testid=scope-needs-session`（`role=status`、`muted`），换期（`convKey` 变）即清、会话起来后不再渲染；有会话时行为零变化。用例 e2e `N32`（页面侧 `MessagePort.postMessage` 观察者计 RPC）：无会话点击 → `conv.send/conv.command` 为 `[]` 且提示可见；对照组发一条消息后同一按钮发出 `conv.command`、stdin 恰 1 行 `command`、提示消失。变异 3/3：α 删早退（原状）→ 提示断言红；β 早退不出提示（假修）→ 提示断言红；γ 出提示不早退 → 「零 conv.command」断言红。验证：`npx vitest run` 364 passed（含 TG-4′/TG-10）、`tsc` 干净、未打包 e2e 107 passed / 2 skipped
- 评审：✅ 通过。① 原始缺陷（实测）：删掉无会话早退（退回修复前）→ e2e N32 红（提示不出现）；HEAD 绿。② 变异 4 条实跑（还原后 md5 一致）：A 退回原状→N32 红；B 早退但不给提示→N32 红（`Expected: visible`）；C 把早退挪到 `isTrusted` 首句之前→vitest 静态守卫两条红（「conv.* … 全部合规」「approval.decide 与 conv.answer 全部合规」），e2e 照绿——首句守卫由静态层看守；D 给提示但不早退→N32 第 412 行 `expect(sent()).toEqual([])` 红（零 conv.command 断言是真的，不是只看文案）。③ 有会话对照组在 N32 用例后半段、HEAD 绿；vitest 364 全绿（TG-4′/TG-10/isTrusted/只挂原生元素静态守卫均在内）。边界：只动 `SessionHeader.tsx` + e2e；新增 1 个 `data-testid` 与 1 句提示文案、只用既有 `muted` 类


### [D38] 退出确认把**空闲**会话也算 busy，与「全部空闲时直接退」不符
- 状态：**已解决**（施工 68c723b；2026-09-28 独立评审通过）
- 关联：`desktop/src/host/sessions.ts::quitState`（对 `phase !== "exited"` 一律收）；Spec 10 §2.10 第 3/5 条、用户裁决「有活动会话时退出 → 弹一次确认；全部空闲时直接退」
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 打包版实测：唯一会话状态为「空闲」时按 Cmd+Q，仍弹原生确认框（正文列出「选题会话 · 空闲」）。第 5 步本身对空闲会话的处理正确（写 `shutdown`、会话 1 s 内发 `bye` 退 0），故这只是「该拦的拦了不该拦的」：全空闲时应当直接退。**2026-09-27 施工**：根因=`quitState()` 与第 5 步 `stopAllForQuit()` 各写一套「忙」判定——前者对 `phase ∉ {exited,none}` 一律收，后者才按「回合在跑或有未答卡」分 shutdown/SIGTERM。修在 host：抽出 `isBusyForQuit(s)`（`turnId !== null |  | open.length > 0`，§2.10 第 5 步原文），`quitState` 与 `stopAllForQuit` 共用；main 零改动（`busy.length === 0 → proceedQuit` 本就对），`sessions-down` 与两个兜底定时器语义未动。用例：host `TH-9⑤`（全空闲 → `[]`；回合在跑、回合已完但有未答卡各列一条，逐字段断言）；e2e `TX-8g`（全空闲 + 「取消」桩 → 仍在 8 s 内退出、会话收 `shutdown` 零信号）、`TX-8h`（回合在跑 → 桩 1 次、列表 `["SESS-A · 运行中"]`、取消后 app/host/回合存活、零 shutdown 零信号）。变异 3/3（还原后 md5 对拍）：A 退回原状（quitState 不过滤）→ TH-9⑤ 第一条断言 `toEqual([])` 红 + e2e TX-8g 红；B 判定去掉 `open.length` → TH-9⑤ 红（CARD 漏列）；C 判定去掉 `turnId` → TH-9⑤ 与既有 TH-9④ 红。验证：`npx vitest run` 364 passed、`tsc --noEmit` 干净、未打包 e2e 全量 106 passed / 2 skipped。**未实测**：打包版门禁 6 复跑（「空闲直接退 / 忙弹框」）——打包版 Playwright 驱动不了、确认框又禁止键盘自动化，留给人手（可与 D37 配方第 6 步一起做）
- 评审：✅ 通过（打包版门禁 6 留人）。① 原始缺陷复现（实测）：把 `quitState` 退回修复前（对所有活会话一律收）→ TH-9⑤ 红、e2e TX-8g（全空闲 Cmd+Q 不弹框直接退）红；HEAD 两者皆绿。② 变异共 3 条实跑（还原后 md5 一致）：A 退回原状→TH-9⑤ + TX-8g；B 忙判定去掉「有未答卡」→TH-9⑤；C 忙判定恒真→TH-9④⑤ + TX-8g。忙路径对照：TX-8、TX-8h 在三条变异下都绿（该拦的仍拦），真实 core 版 TX-8「回合在跑→确认框列出该期→SIGTERM、`turn_end.wrapup == skipped`、8 s 内进程消失」HEAD 实跑绿。③ diff 只动 `host/sessions.ts`（抽出 `isBusyForQuit` 供 `quitState` 与 `stopAllForQuit` 共用），`main/index.ts` 零改动，`sessions-down` 与兜底定时器语义未动。**未实测**：打包版门禁 6「空闲直接退 / 忙弹框」——确认框禁止键盘自动化，留人与 D37 配方第 6 步同场做


### [N35] `cmd_fetch` 无条件先查 yt-dlp，直链（本该走 curl）也走不到
- 状态：**已解决**（施工 b73749e；2026-09-28 独立评审通过）
- 关联：`pipeline/acquire.py::cmd_fetch`（`fetch_argv(..., yt_dlp=yt_dlp_argv())`）、`yt_dlp_argv`；Spec 6
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 门禁 12 实测：URL 后缀 `.mp4`（`DIRECT_EXT` 命中、`pick_fetcher → "curl"`），但 `acquire fetch 7` 仍以「FAIL 找不到 yt-dlp」退 1 —— 因为 `yt_dlp_argv()` 在 `fetch_argv` 之前被求值并 `raise SystemExit`。本机 yt-dlp 既不在依赖也不在 PATH，**故本章所有抓取卡在本机恒失败**（#3、#7 两次实测均为该错）。修法方向：把 yt-dlp 的查找推给 `pick_fetcher == "yt-dlp"` 那一支，或先判 `--dry-run` 的 fetcher 再取 argv。**2026-09-27 施工**：`cmd_fetch` 改为 `yt_dlp_argv() if pick_fetcher(url) == "yt-dlp" else None`，只有页面那一支才找 yt-dlp；`yt_dlp_argv`/`fetch_argv`/报错文案零改动，`--dry-run` 与真跑共用同一个 argv，一并修好。用例 `tests/test_acquire.py::TestCmdFetchWithoutYtDlp`（夹具 monkeypatch `shutil.which`/`find_spec` 并先自检 `yt_dlp_argv()` 确实报错）：直链 dry-run 打印 curl 命令；直链真跑（`subprocess.run` 桩）argv[0]==curl 且台账 +1；页面 URL 报原错且文案逐字。变异 3/3（`PYTHONDONTWRITEBYTECODE=1`，还原 md5 对拍）：a 退回无条件查找→两条直链用例红；b 条件取反→三条全红；c 永不查找（静默退回裸 `yt-dlp`）→页面用例红。全量 `uv run pytest` 1953 passed。真实 data 只读核对（PATH 剥掉全部 yt-dlp）：`fetch 3 --dry-run`（页面）逐字报原错；真实清单两条直链 #1/#2 已在台账、查重先拦，看不到 curl 分支。**未实测**：门禁 12「批准→真抓→台账 +1」在真实 data 上复跑——需往真实清单加探针、跑完从清单/台账撤掉（删改真实数据，留给人手或评审）；另注：本机现已有 yt-dlp（`/opt/homebrew/bin` 与 Python.framework 各一份），原环境已不自然复现
- 评审：✅ 通过（门禁 12 真实数据一项留人）。① 原始复现与修复（实测，真 CLI、零 monkeypatch）：沙箱 `data/`（非 T7）+ 本地 HTTP 服务一个 `.mp4`，`env -i PATH=/usr/bin:/bin`（brew 的 yt-dlp 不可见；venv 里也无 `yt_dlp` 模块）——修复前 `acaae0c`：直链与页面 URL 都 `FAIL 找不到 yt-dlp` 退 1；HEAD：直链走 `curl`、退 0、落盘文件与源逐字节一致、台账 +1；页面 URL 仍报原 `FAIL 找不到 yt-dlp。二选一：…` 退 1。② 变异 3/3 实跑（还原后 md5 一致）：A 退回无条件求值→两条直链用例红；B 判定取反→三条全红；C 永不求 yt-dlp→页面原错文案用例红。③ **门禁 12「批准抓取卡→真抓→台账 +1」未在真实 data 上复跑**：要在 T7 真实清单/台账里加探针再删行，属删改真实数据（红线 1），评审不擅动；上面沙箱真 CLI 已覆盖同一代码路径，真实数据复跑留人（可与 D37 配方同场做）


### [N33] idea 会话收到 `shutdown` 时多发一条「空闲态收到中断，已忽略」notice
- 状态：**已解决**（施工 74d2cd5；2026-09-28 独立评审通过）
- 关联：`pipeline/agent/protocol.py::_idle_notice` 调用路径；Spec 9 §3.1
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 真实 core 联调观测：idea 进程关停流程里的中断被当成空闲中断提示了一次，随后正常退出。无功能后果（进程照常结束、帧仍逐键合规），但对话流尾部多一行误导性提示。修法待查关停序列里信号与 shutdown 帧的先后。**2026-09-28 定位与施工**：先用真实 core 探针实测了 idea/期会话 × 4 种关停（跑过一轮后）：shutdown 帧、EOF、「shutdown 后 EOF」都只有 `bye`，**只有空闲时收到 SIGTERM** 会先冒一条 `interrupt_ignored` 再 `bye`（idea 与期会话相同）。所以「收到 shutdown 时」这句描述不准，实际触发源是 SIGTERM。根因 = `_on_term` 不管有没有回合都 `host.interrupt.request()`，空闲时这一枪打在主循环的 `out.get()` 上，被 MUT-31 的空闲分支接住。修法：`_on_term` 入队 shutdown 后，`in_flight` 为假就直接 return（入队本身就能唤醒主循环；这与 shutdown 帧分派用的判定一致）。忙时语义不变，MUT-31 分支与帧键零改动，`verify_mutations.py` 的 124 个锚点仍各自唯一命中。用例：`test_tp4c_idle_shutdown_sends_no_interrupt_notice[sigterm | shutdown]`（关停段 == `[bye(shutdown)]`、退 0）；对照组 `test_tp4c_idle_sigint_still_notices`（未关停时的空闲 SIGINT 之后恰一条 `interrupt_ignored`、进程存活）。变异 3/3（`PYTHONDONTWRITEBYTECODE=1`，还原后 md5 对拍）：m1 原状 → TP-4c[sigterm] 红；m2 假修（删掉空闲 notice）→ 对照组红；m3 判定取反 → TP-4c[sigterm] 与既有 TP-11 红。验证：`uv run pytest` 1956 passed；桌面端真实 core e2e `sessionReal.spec.ts` 27 passed
- 评审：✅ 通过。① 原始复现（实测）：把 HEAD 的 TP-4c 放到修复前 `acaae0c` 上跑，`[sigterm]` 红——关停段为 `[('notice', …), ('bye','shutdown')]`，与 issue 所述「多一行空闲中断提示」一致；`[shutdown]` 与对照组在旧代码上本就绿（证实施工方「触发源是空闲 SIGTERM 而非 shutdown 帧」的更正）。② 变异 3/3 实跑（还原后 md5 一致）：A 去掉空闲早退→TP-4c[sigterm] 红；B 判定取反→TP-4c[sigterm] + TP-11 红（回合中 SIGTERM 不再打断）；C 删掉空闲 notice→对照组 TP-4c_idle_sigint 红——未关停时的空闲 SIGINT 仍发 notice，MUT-31 语义未破。③ diff 未触及任何帧的构造，键集零变化。备注：用例里有两处 `time.sleep(0.3)` 用于等残余帧落定，不承担判定，不构成时序依赖


### [D36] 作废/已关闭的抓取卡不从待答区撤下，徽标与真源不一致
- 状态：**已解决**（施工 7b9b705；2026-09-28 独立评审通过）
- 关联：`desktop/src/host/sessions.ts`（`s.open` 维护）、`renderer/convStore.ts`；Spec 9 §3.1 `request_closed`、S9-R4、Spec 10 §2.4
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 实测：`session.jsonl` 已 `request_closed(voided #6)` 且只 `request_opened(#7)`，界面待答区仍显示 **#6**、`episodes.summary` 徽标报「2 张卡待答」。按「刷新」后 #6 与 #7 两张卡同时出现。另一次（#4 关闭 → #6 打开）是**滞后十余秒后自愈**。host 的 `request_closed` 分支对 `reason` 无过滤（answered/voided 都删），故「帧没到 host」与「host→renderer 推送丢/迟」两条链都要查；建议补「voided 关闭后徽标立即归零」用例。**2026-09-27 定位与施工**：两条嫌疑链都不是——**帧到了 host，但被 host 当坏帧丢了**。core 的作废帧省略 `decision`（只在非 None 时写）、另带 §3.1 表外的 `cause`；host 的 `parseOutFrame` 按 Spec 9 §3.1 要求 `request_closed` 的 `decision` 在场（string/null）→ 整帧 `malformed` → 记 `frames_lost` 不进 `onFrame` → 卡与徽标永不撤下（answered 帧带 decision，所以答复路径一直正常；TX-0 只跑过 answered 路径，没覆盖作废）。打点证据：把修复退回后跑新用例 TX-0b，快照里恰在关闭处出现 `frames_lost{reason:"malformed"}`、`open` 仍含该卡、`framesLost:1`——即 M9 现场。修法在 core（host 严格校验与 spec 一致，不放宽）：`ProtocolChannel._close` 帧键恰为 `request_id/reason/decision(+rid)`，作废 `decision:null`；`cause` 只进盘（session.jsonl 记录照旧带）。「#4→#6 滞后十余秒自愈」同源（作废帧丢失），**自愈机理未实证**（推测为进程退出时 `finishExit` 清空打开集合）。用例：TP-5/TP-5c 断言作废/答复帧键集；host TH-18（作废帧 → `open` 与徽标同步收缩、零丢帧、末条 delta 已不含；另钉根因：缺 decision 的帧判 malformed）；renderer `convStore` delta 收缩用例；e2e TX-0b（真实 core：待答时点停止 → 作废帧过 `parseOutFrame` 且逐类键集对拍、`framesLost==0`、卡与「张卡待答」立即消失）。变异 2/2 杀（作废帧省略 decision → TP-5 + TX-0b；多带 cause → TP-5 + TX-0b）；S9-MUT-19 因 `_close` 签名去掉 `cause` 重锚，实跑仍 KILLED（TP-5）。验证：pytest 1950 passed、vitest 363 passed、tsc 通过、未打包 e2e 104 passed / 2 skipped
- 评审：✅ 通过。① 现场（实测）：在修复前 `acaae0c` 与 HEAD 上各让真实 core 在卡待答时读到 EOF，抓下 `request_closed` 原帧——旧帧 `{reason:"voided", cause:"interrupted"}`（无 `decision`、带表外 `cause`），喂给 host 真实 `parseOutFrame` 得 `{ok:false, reason:"malformed"}`（整帧丢弃，卡与徽标因此不撤）；HEAD 帧 `{reason:"voided", decision:null}` 解析 ok。根因在 core 成立，host 未放宽。② 变异 3 条链路各一（还原后 md5 一致）：A core 作废帧省略 decision→TP-5 红；B host 只对 answered 撤卡→TH-18 红；C renderer 在 delta 的 open 为空时保留旧 open→**vitest 全绿（convStore 用例只测 q6→q7 替换、没测收缩到空）**，但 e2e TX-0b 红（卡计数期望 0 实得 1）——被 e2e 杀死，单测层有缺口但不阻塞。③ diff 无 renderer/host 源码改动，不存在周期性重取 snapshot 之类掩盖式补丁。空载未打包全量 e2e 108 过 / 2 跳过。未复核（转述）：「#4→#6 滞后十余秒自愈」机理


### [D34] 抓取卡按**整份清单**出卡，而不是按本轮 `acquire_propose` 传入的 URL
- 状态：**已解决**（施工 3e8172e；2026-09-28 独立评审通过）
- 关联：`pipeline/agent/session.py::_fetch_cards`（`for index, candidate in enumerate(candidates, 1)`）；Spec 9 §2.4.4 第 2 条、Spec 6、ADR-0021
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 门禁 12 实测：本轮只提案本地探针（清单第 7 条），弹出来的却是**候选 #3（真人 YouTube 候选）**；批准后 `acquire fetch 3` 真被执行（本次因缺 yt-dlp 失败，零落盘零台账）。spec 原文是「对**本次输入的每个 URL**（按输入顺序去重）在清单中且不在台账 → 一张卡」。后果：一次 `acquire_propose` 会把清单里全部历史未抓候选逐张推给人，批准即真抓——人以为只批了自己这轮提的那条。**2026-09-27 施工**：`_post_execute` 把工具入参经 `_proposed_urls(args)`（按输入顺序去重、与 `propose_candidates` 同样 strip）传给 `_fetch_cards(outcome, urls)`；出卡只遍历本次输入的 URL，须在清单中（序号取清单里首次出现的 1-based N）且不在台账。`tools.py` 工具实现与 schema、`LoopControl` 签名、`cmd_fetch` 均零改动。TK-2/3/3b/3c 夹具改为传入 spec 所述的 4 条入参（期望值不变）；新增 TK-2b（清单有历史未抓候选、本轮只提 1 条 → 只 1 张卡、序号 4）、TK-2c（同 URL 提两次 → 1 张、出卡顺序随输入）。变异 4/4 杀（退回全清单遍历、去掉去重、序号取输入位置、不 strip）。全量 1950 passed
- 评审：✅ 通过。① 评审自写的独立场景（真 `run_turn` + 真 `acquire_propose`，未入库）：清单预置 2 条历史未抓候选，本轮只提案 1 条新的——修复前 `acaae0c` 出 3 张卡（#1 历史甲、#2 历史乙、#3 新），HEAD 只出 `(#3, 新)` 且批准后执行器只收到 3；② 同一调用里同 URL 提两次→HEAD 只 1 张卡。③ TK-3（misaligned）、TK-3b/3c（fetch_disabled_*）在 HEAD 复跑全绿。变异 4 条实跑（还原后 md5 一致）：A 退回全清单→TK-2b/2c 红；B 去掉去重→TK-2c 红；C 不 strip→TK-2b 红；D 序号改取末次出现→**存活**，但属不变量下的等价变异：`propose_candidates` 对清单与台账做 URL 精确串双源判重，清单里不会有重复 URL（只有人手改清单才可能触发），不要求补用例。边界：只动 `session.py` 与测试，`tools.py`/`cmd_fetch` 零改动（已核 diff）


### [D35] 抓取卡上的「停止」停不下回合：只作废当前卡，随即弹下一张
- 状态：**已解决**（施工 3b58c9d；2026-09-28 独立评审通过）
- 关联：`pipeline/agent/session.py::_ask_fetch` 的 `except KeyboardInterrupt` 分支；Spec 9 §2.2 状态表（抓取中中断 → 其余候选卡 `voided`）、H-6
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 实测：`session.jsonl` 序列 `request_opened(#6) → request_closed(voided #6) → request_opened(#7)`，回合仍在跑（无 `turn_end`、UI 仍显示「停止」）。机理：`_ask_fetch` 吃掉 KeyboardInterrupt 后 `return record`，`_fetch_cards` 循环继续问下一张。按 spec 应「中断即整轮停止 + 其余候选卡 voided」。**2026-09-27 施工**：`_ask_fetch` 的中断（等答复时 → `voided`；抓取 job 中 → `interrupted`）改抛内部 `_FetchStopped`，`_fetch_cards` 停止出卡、余卡只记 `voided` 不开卡，再以 `FetchHookInterrupted`（KeyboardInterrupt 子类，携带 outcome）上浮；`llm.py` 为钩子新增 `hook` 阶段，落点处理提交**真实**提案结果（带抓取记录、执行计数不重复加），同回复后续调用补「未执行」，随后照常收尾，`turn_end{interrupted}` 一条。新增 TK-3d/3e/3f（真 `run_turn` + 真 `acquire_propose`）。变异 5/5 杀（退回 `return record`、余卡不 void、`hook` 分支落回合成结果、`_fetch_cards` 无视停止标志、job 中断不接）；S9-MUT-5 锚点因插入 `hook` 分支重锚并实跑仍 KILLED（TL-7）。全量 1948 passed。残余：中断若恰落在 `ask` 之外的几行（如 `_record`），会以普通中断浮出，提案结果照常但丢该次抓取记录（回合仍正确停下）
- 评审：✅ 通过。① 原始复现（实测，会话层真 `run_turn`）：把 HEAD 的 TK-3d/3e/3f 放到修复前的 `acaae0c` 上跑，三条全红——第 1 张抓取卡上中断后仍出了 3 张抓取卡（`['tool_call','fetch','fetch','fetch']`）、回合以 `done` 结束；**未在协议子进程级复现**（与施工报告同一局限）。② 变异 4/4 实跑（评审工作树，还原后 md5 一致）：A `_ask_fetch` 退回 `return record`→TK-3d/3e 红；B 余卡改 `break` 不记 voided→TK-3d/3e/3f 红；C `llm.py` 钩子吞掉中断→TK-3d/3e/3f 红；D 抓取 job 中断不转 `_FetchStopped`→仅 TK-3f 红（该守的那条）；另复跑重锚后的 S9-MUT-5→TL-7 红（仍 KILLED）。③ TL-8/TL-9*/TL-17 在 HEAD 全绿，第二次中断不打断收尾的既有语义未破。边界：动了 `llm.py` 的落点处理（`hook` 阶段）——越出提示词点名的 `session.py`，但属把中断连同真实提案结果上浮的必要改动，已被 TK-3d/3e/3f 与 TL-7 覆盖。残余窗口（中断落在 `ask` 外的 `_record` 等处会丢本次抓取记录）如施工报告所述，未另测


### [D33] 协议进程读到 stdin EOF 不退出：host/app 意外死亡后会话永不自清
- 状态：**已解决**（施工 68390e8；2026-09-28 独立评审通过）
- 关联：`pipeline/agent/protocol.py`（EOF 分支）；Spec 9 §2.8、Spec 10 §2.1 第 7 条、A3
- 原记录（活跃表原文，含施工回填）：2026-09-27 M9 打包版联调实测：`sleep 1` 管道喂 stdin、会话读过 EOF 后 **>40 s 仍存活**（采样栈：主线程卡在 `lock_PyThread_acquire_lock`→`__psynch_cvwait`，无超时；`lsof` 显示 fd3/4 已 `->(none)`）。对照组：`shutdown` 帧 **1 s 内**发 `bye` 退 0，故不是「所有退出路径都坏」。后果：18:11–18:27 的 M9 e2e 留下 **12 个 PPID=1 的孤儿 protocol 进程**（租约指向已删临时目录），杀 app 后会话也不自退。修法方向：读端 EOF → 走 §2.8「中断当前回合 → 收尾 → `turn_end` → 退 0」，并补 EOF 退出用例（现 TP 表无此断言）。**2026-09-27 施工**：根因＝`FrameReader._on_eof` 只 `pthread_kill` 不入队，空闲时主线程阻塞在 `out.get()` 的锁等上，macOS 上 SIGINT 唤不醒它（修前复现 >12 s 存活且连空闲 notice 都没发）。修法＝与 `shutdown` 帧同一出路：回合在跑才打中断，然后 `out.put(("eof",…))` 唤醒主循环 → `bye{eof}` 退 0。新增 TP-5b（空闲 EOF 10 s 内退 0、无 notice、租约释放可立即重开）、TP-5c（工具执行中 EOF → 不等工具跑完、`turn_end{interrupted}`）、TP-5 补盘上 `request_closed(voided)→turn_end` 顺序。变异 4/4 杀：不入队→TP-5b；删主循环 eof 出口→TP-5b；回合中不打中断→TP-5c；空闲也打中断→TP-5b（多出 notice）。全量 1945 passed
- 评审：✅ 通过。① 原始复现（实测）：`acaae0c` 上 `sleep 1 | python -m pipeline.agent.protocol --idea` 45 s 后仍存活；HEAD 同场景约 2 s 发 `bye{eof}` 退出。② 变异 3/3 实跑（评审工作树，`PYTHONDONTWRITEBYTECODE=1`，还原后 md5 对拍一致）：A 删 eof 入队→TP-5b 红；B 空闲也打中断→TP-5b 红（多出 notice）；C 回合中不打中断→TP-5c 红——杀手均为该变异该守的用例。③ 反驳式检查：diff 只动 `FrameReader._on_eof`，`shutdown` 分支与 `FRAME_DRAIN_TIMEOUT_S` 零改动；回合中 EOF 走「中断→收尾→turn_end→bye{eof}」（TP-5c 断言 `turn_end.stopped==interrupted`）。未复核（转述）：施工报告里的 12 个孤儿进程现场


## 2026-09-02：须贺期实战踩出并当天修复

### [D22] tts readings 复用比对全表指纹一刀切
- 状态：**已解决**（2026-09-02，commit 5570613）
- 关联：`pipeline/tts.py::_reusable`
- 要点：改一个读音九段全废，WORKFLOW 承诺的「自动重做受影响的段」从未实现
  （须贺期修段 2/8/9 时靠手动对齐 manifest 指纹才保住单段重跑）。修法：Take 落
  speakable（实际喂模型的文本），复用改段级比对；engine/model/ref_audio 仍全局门；
  旧 manifest 无 speakable 退回全表指纹。顺带修掉指纹 sort_keys 对键序不敏感、
  而 str.replace 按插入序生效的静默复用漏检。

### [D23] clips 检索及格 ≠ 可分派
- 状态：**已解决**（2026-09-02，commit 8b69375）
- 关联：`pipeline/clips.py::_ladder / _rescue_starved`
- 要点：须贺期段 4——查询及格的候选全被段 3 锚点占完，`备选` 从未被搜就
  no_source，05 人工指定 60:23 才救回。修法：_ladder 可续爬（`_ladder_steps`
  步序列 + 下一步下标），分配循环饿死检测续爬一级、新命中追加 hits 尾部保住
  「只救不比」段内序；`rescue` 字段落盘，05 审查页显示「首选被占·第 N 级救回」。
  真实索引回放：段 4 备选救回 ok，其余 8 段与线上版逐字节一致。画面通道同构
  缺口暂不扩（只有两级、无实战案例，YAGNI）。

## 2026-08-27：由 ADR-0008 解决并归档

### [D20] 排片机制重构：笔记 Ground Truth 锚点直通排片
- 状态：**已解决**（2026-08-27，ADR-0008 定案并实现落地）
- 关联：`docs/dev/adr/0008-ground-truth-anchor-clips.md`
- 要点：02 写稿强制 `锚点:` 字段（check_script 机检）；clips.py 锚点通道起点吸附
  镜头切点、一等公民先占位；双塔检索降级为 `锚点: 无` 氛围段的补位。
  注意：08-26 期 clips.json 与 approved 逐字节相同（diff 通道污染），不作验证样本；
  验证义务与推翻条件在 ADR-0008 末节，等下期新番实测。

## 2026-09-05：审计收口销号

### [D21] Qwen3-TTS 长段落音色漂移诊断
- 状态：**已作废**（2026-09-05 用户确认销号）
- 要点：原假设（Qwen3-TTS 在长段落上音色漂移）不成立——根因是仓库根目录
  临时脚本绕过 `render_segment` 直接调引擎，丢了质检与裁静音链；走正规管线的
  产物无此问题。附带纪律沉淀为 N23（严禁仓库根目录临时脚本），仍活跃。

### [N26] phase0 集号正则吃不了电影文件名
- 状态：**已解决**（2026-09-05，phase0 新增 `--episode` 显式集号）
- 要点：剧场版/电影单文件无 [NN] 通例可循；现在 `phase0 <电影.mkv> --anime X
  --season N --episode M` 跳过正则由人给号（一次一部），君名/天气之子当初的
  verify/run/sources 三步拆跑绕过方案不再需要。

### [N7] IndexTTS 遗留模型与自写解码循环
- 状态：**已解决**（2026-09-05 审计批次3）
- 要点：4.4G 模型权重经查早已随 2026-08-15 换引擎删除（仅剩空 .locks 目录，已清）。
  自写自回归解码循环（tts.py `_indextts_audio`）审计确认为引擎接缝的活回退路径
  （config `engine=indextts` 可选），非死代码；删除条件（上游 mlx-audio 修复对齐）
  写在该函数 docstring 里，条件达成再清。

## 2026-08 批：由 P3 / P4 / 施工图解决并删除的条目

### [D3] 缩段不注水拍板落地
- 状态：**已解决**（2026-08-14，P4 E1 落地）
- 关联：`docs/dev/plans/2026-08-14-p4-script-pipeline.md`
- 要点：`01-topic.md` 加 `缩段不注水: 是` 字段，字数下限 × shrink_factor；
  允许承认这段没料而缩短，不许注水凑数。

### [D14] TTS CPM 单源化
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/dev/plans/archive/2026-08-14-p3-doc-debt.md`
- 要点：CPM 取值口径统一，消除多文件默认值漂移。

### [D15] BGM 例外规则同步
- 状态：**已解决**（2026-08-14，BGM 例外规则同步）
- 关联：`docs/dev/adr/0007-no-japanese-subs-support.md` 背景中提及
- 要点：例外曲目来源与理由在 config 与文档间同步完成。

### [D16] 人类介入点口径统一
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/dev/plans/archive/2026-08-14-p3-doc-debt.md`
- 要点：02.5 / 03.5 / 05 / 09 四处人类介入点在 CLAUDE.md / WORKFLOW.md / STANDARD.md 口径统一。

### [D17] 测试数清算
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/dev/plans/archive/2026-08-14-p3-doc-debt.md`
- 要点：测试断言与变异检验覆盖范围厘清并补全。

### [N3] shots calibrate 多参数支持
- 状态：**已解决**（2026-08-14 删除）
- 要点：`shots calibrate` 支持多参数标定。

### [N13] 人审手工片段标记
- 状态：**已解决**（2026-08-14 删除）
- 要点：05 人审改 `04-clips.json` 的标记机制落地。

### [N15] TTS 时长带改读 config
- 状态：**已解决**（2026-08-14 删除）
- 要点：TTS 时长估算带由代码硬编码改为 config 项。

### [N18] 切分标定判据补进 CLAUDE.md
- 状态：**已解决**（2026-08-14 删除）
- 要点：过切/漏切取舍的判据写进常驻规则。

---

## 2026-08-18 Pipeline 运行问题与架构缺陷复盘

- 状态：**已修复并验证**（2026-08-18，未提交 git，由人拍板）
- 来源：原 `docs/issues/2026-08-18-pipeline-inconsistency-issues.md`（孤儿文件，无编号）
- 修复项：
  1. **局部重跑级联校验**：`tts.py` 新增 `_stale_downstream()`，写完 manifest 立即对
     `04-clips.json` / `04-clips.approved.json` 跑 `verify_alignment`，有违例当场 WARN 并指明清算命令。
  2. **配置文件前置校验**：`tts.load_config()` 坏 JSON / 缺键在加载处 SystemExit；
     `render.run()` 把 `_maybe_music_plan` / `_bgm_plan` 提前到切片之前解析。
  3. **cover.py 字段 schema 兜底**：缺 `season`/`episode` 时按 `source` 路径反查片源登记表。
- 验证：`uv run pytest` 565 → 584 全绿，变异检验均红。

---

## 2026-09-25：Spec 8 M12 打包版启动被模态框挡住

### [N30] 打包版崩溃过之后，之后的某次启动永远到不了 ready
- 状态：**已解决**（2026-09-25，S23 修复轮，未提交）
- 关联：`desktop/src/main/index.ts::disableWindowRestoration`、`desktop/e2e-packaged/packaged.test.ts` TS-9、Spec 8 §2.10、TS-9、MUT-59
- 根因：app（签名身份 `local.ava.desktop`）有过生成崩溃报告的崩溃（TS-1 篡改副本的 SIGTRAP、SIGABRT 等；SIGKILL 不算）之后，AppKit 在 `finishLaunching` 里弹模态框「上次意外退出，要重新打开窗口吗？」（`-[NSPersistentUIRestorer promptToIgnorePersistentStateWithCrashHistory:]` → `NSAlert runModal`，`sample` 与截屏坐实），没人点就永远到不了 ready。与 Clash、系统策略检查无关。S23 曾误判为「间歇性卡死、TS-1 干扰已证伪」，S24 查清与 TS-1 的关联并发现连续重跑必红。
- 实测：启动中发 SIGTRAP 模拟崩溃后各启动 3 次——改前 7 次崩溃有 5 次随后弹框；在 app 偏好域设 `ApplePersistenceIgnoreState=YES` 后 6 次崩溃 18 次启动零弹框；由 app 在当次启动自己写入（每次启动前删键）6 次崩溃 18 次启动零弹框。
- 修复：打包版 main 在 `boot()` 之前写 `ApplePersistenceIgnoreState=YES`（ava 的窗口不靠 AppKit 恢复）。TS-1 放回 TS-6 之前，整套连跑 3 轮均 11/11；MUT-59（删掉该调用）被 TS-9 捕获。
- 复现（改前）：启动打包版时对 main 发一次 SIGTRAP，再连续正常启动 3 次，卡住时对 main 做 `sample` 即见上述调用栈。

---

## 2026-09-25：v2 M2a/M2b 与一期 Harness 收口归档

### [B1] 排片错配修复方案选定与落地
- 状态：**已解决**（M2b 交付，commit `b257e6d`）
- 关联：`docs/adr/0005:4-6`、ADR-0005、ADR-0015、P1、v2架构方案
- 要点：Qwen3-VL 镜头意象打标 + bge-m3 文-文检索已交付（ADR-0015，commit `b257e6d`），EGOIST 池解封；衍生子题（D1 候选复核四子题、D6 错配率重测）在各自条目独立跟踪。

### [B3] 每期人类投入「≤10 分钟」止损线口径
- 状态：**已被实践否证**（2026-09-19 否证改 `k × 片长`；2026-09-23 用户裁决进一步降为纯观测量）
- 关联：`ROADMAP.md:30,146`、ROADMAP 闸门 A、`AGENTS.md` 第二节
- 要点：原「≤10 分钟」假设被约 20 期实践否证（人类时间 ∝ 成片时长，20 分钟片光 03.5 就 ≥30 分钟）；2026-09-19 修正预算模型为 `k × 片长`（v1.20）并接通 `human_time.json` 记账；2026-09-23 用户裁决撤销硬预算与「连续 3 期停产」判据，降为纯观测量。

### [D4] 视觉索引第 3 层 VLM 落地
- 状态：**已验收**（M2b 交付，commit `b257e6d`）
- 关联：`docs/adr/0003:38-39,95-141`、ADR-0003、ADR-0015、ADR-0014、v2架构方案
- 要点：ADR-0015 的 VLM 意象打标 + 文-文检索已验收合入（M2b）。

### [D5] 第 2 层（画面语义）复活落地
- 状态：**已验收**（M2b 交付，commit `b257e6d`）
- 关联：`docs/adr/0003:431-433`、ADR-0003、ADR-0015、v2架构方案
- 要点：以 Qwen3-VL captions + bge-m3 文-文检索实现，CLIP 余弦正式废弃，M2b 已对已标定池解封。

### [D25] readings 替换表边际成本极高，需将 CJK 音读泄漏收敛为拼音直注脱敏机制
- 状态：**已落地**（M2a 交付）
- 关联：`config/voice.json`、`pipeline/tts.py`、`pipeline/g2p.py`、ADR-0006、ADR-0014、ADR-0017、v2架构方案
- 要点：`pipeline/g2p.py` 拼音直注脱敏层已交付；`readings` 表已降级为个例 override。

### [D6] 排片错配率完成判据未测
- 状态：**已关闭（无存在必要，2026-09-25 用户裁决）**
- 关联：`docs/dev/adr/0003:478`、ADR-0003、ADR-0005、ADR-0015、P1
- 要点：量化通道（`pipeline/recheck.py` 的 diff/probe/score，P1 交付）已建并留存可用。用户裁决：VLM 场景通道只对真实素材及需要用到真实素材的题材生效，纯动漫题材边际效益不足，没必要急着打标与测算错配率；D6 撤销，编号不回填。ADR-0003「完成判据的实测」节的排片错配率一项随之不再追测（封面候选 9/9 一项已测）。若未来接入真实素材题材，需要错配率数字时用 recheck.py 现成通道重开即可。

### [N27] 夏隧的 scene_threshold=10.0 是沿用值，标定实录缺失
- 状态：**已关闭（无存在必要，2026-09-25 用户裁决）**
- 关联：`config/project.json` visual、ADR-0003、D6 同型裁决
- 要点：用户裁决——夏隧一期视频（2026-08-20《逃避的代价》已交付）已用现值正常出片，无任何故障症状，此条不是 bug 只是 R2 记录缺漏，不值得纠结。闭环前已顺手实测密度表留存：夏隧（82.7min 电影，894 镜 @10.0）thr5–15 中位 3.42–3.71s 全落动画单镜头 2–5s 常态区、thr20 才偏粗（p90 22.7s），与天气之子/君名「thr10 即漏切正片」的形态不同——若未来真要补标定，起点证据在 `data/library/shots/calibrate/long-t8.jpg` / `long-t10.jpg`（已生成未目检），届时重开即可。编号不回填。

### [D18] 集号 / 人物字段自觉性缺口导致错配静默发生
- 状态：**已关闭（不立判据，走替代处置；2026-09-25 用户拍板）**
- 关联：`pipeline/check_script.py`、ADR-0005、ADR-0008、B1
- 要点：集号侧早已由 `锚点:` 强制字段 + 集/锚点一致性机检双卡口覆盖（2026-08-27）。「`人物:` 该写没写」经论证不可证伪（提到名字≠画面需求是该角色；强制每段写=逼人编字段，check_script 对集号已判过同款哲学），不立硬判据。用户原则：**锚点才是一等公民**，文案与锚点严丝合缝时 `人物` 不是刚性必须项，且真实素材（无 ccip 人脸链）下 presence 通道整体失效。替代处置：① check_script 加 **INFO 级提示行**（不报 FAIL）——配音含已登记角色名但未写 `人物` 的段列出给人扫一遍，别名表现成（`characters.json`）；② 02.5 人审要点加「`人物:` 该写没写」一条。两个落点已并入二期 Spec 11 范围（`~/Desktop/ava二期-spec设计与红队评审提示词.md` Spec 11 作者第 6 问 + 红队攻击面），随 02.5 编辑器一并施工。`人物` 保持「写了就走过滤、不写不勉强」的纯可选通道选择器。编号不回填。

### [D19] 说话人确认是全流程最不可靠环节
- 状态：**已关闭（不立判据，走替代处置；2026-09-25 用户拍板）**
- 关联：`skills/write-script/SKILL.md:54`、D18 裁决
- 要点：机器判据三条路全堵（字幕 Name 字段不填无真实标签；presence 答「谁在场」答不了「谁在说话」，画外音/反应镜头是常态，不可证伪；声纹要新建标定链，成本形态同 D6 裁决）。替代处置：① 「某某说」类断言的真实性由锚点机制承重（台词必须锚到真实字幕行）；② 说话人核对保留为 SKILL.md:54 既有抽帧流程约束（人审环节）；③ 可选增效：说话人断言联系表工具（扫 02-script.md 的「X 说」断言批量抽帧拼一张表，人一次过完，复用 `review._frames()`）——纯人审辅助，明确不是判据，需要时再建。编号不回填。

### [D27] 立项前工作无入口：ava 启动形态缺无期选题会话
- 状态：**已解决**（2026-09-21 实施与 15 组变异全量通过，commit `7866ded` / `527ce56`）
- 关联：`pipeline/agent/cli.py`（`create_new_episode` / `select_episode_interactive` / `main`）、`docs/dev/plans/archive/2026-09-21-ava-entry-idea-scope.md`、impl-spec §2.3
- 要点：解决容器先于内容的顺序倒置（2026-09-20 需求交接）；新增 `ava idea` 无期选题会话（只读 4 工具、零写权限）与 `ava new <名>` 建目录后直进对话，15 组变异全杀闭环。

### [N29] 镜头画廊「复制锚点」按钮点不动
- 状态：**已解决**（2026-09-25 插单修复 commit `7f297cc`，55/58 画廊已重生成）
- 关联：`pipeline/shots.py` `gallery()` 的 `onclick="cp(this, {json.dumps(anchor, …)})"` 一行、Spec 8 §2.7 RF-7
- 要点：`json.dumps` 的双引号直接塞进双引号 `onclick` 属性导致浏览器解析截断为 `cp(this, `；2026-09-25 加 `html.escape` 修复并经 S22 复核，55/58 画廊已重生成。尾注：EGOIST SP19/33/47 因代表帧与镜头表张数不一致被 `gallery()` 门禁拦停、按钮仍坏，用户裁决暂不处理。

---

## 归档规则

1. 活跃区只保留 `README.md` 单表中的条目。
2. 问题解决后：状态改为已解决 / 已作废，迁移到本文件，README 中删除。
3. 编号不回填、不补号；删除后留下的空号用本文件记录去向。
