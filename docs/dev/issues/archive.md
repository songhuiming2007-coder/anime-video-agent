# Issues：已归档条目

活跃问题见 `README.md` 单表。本文件只存已解决、已作废或历史上有价值的问题。
**规则：解决即归档，不再删除。** 编号断裂比归档文件更害人。

---

## 2026-09-28：收尾批评审通过（独立评审 session）

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
- 关联：`docs/adr/0008-ground-truth-anchor-clips.md`
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
- 关联：`docs/plans/2026-08-14-p4-script-pipeline.md`
- 要点：`01-topic.md` 加 `缩段不注水: 是` 字段，字数下限 × shrink_factor；
  允许承认这段没料而缩短，不许注水凑数。

### [D14] TTS CPM 单源化
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/plans/2026-08-14-p3-doc-debt.md`
- 要点：CPM 取值口径统一，消除多文件默认值漂移。

### [D15] BGM 例外规则同步
- 状态：**已解决**（2026-08-14，BGM 例外规则同步）
- 关联：`docs/adr/0007-no-japanese-subs-support.md` 背景中提及
- 要点：例外曲目来源与理由在 config 与文档间同步完成。

### [D16] 人类介入点口径统一
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/plans/2026-08-14-p3-doc-debt.md`
- 要点：02.5 / 03.5 / 05 / 09 四处人类介入点在 CLAUDE.md / WORKFLOW.md / STANDARD.md 口径统一。

### [D17] 测试数清算
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/plans/2026-08-14-p3-doc-debt.md`
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
