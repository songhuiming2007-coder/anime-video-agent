# Implementation Spec：桌面端对话面板与人审卡片（Spec 10 / desktop 侧，消费 Spec 9）

> **归档状态（2026-09-29，S21 收尾）**：已施工并验收（PR0–PR4）；§9 门禁 1–5、7、8、10、11、13 已勾；**未验**：门禁 6（密钥打包版手验）、9（05 返工真机手验）、12（打包版完整回合手验）——均待人；**遗留**：D37 / D40（2026-10-06 D37-C 独立评审通过，已迁 issues archive；打包版人手复验八步见 archive `[D37]`）、N39（切仓窗口新代号+旧根；2026-10-06 独立评审通过，已迁 issues archive）。

日期：2026-09-26（**v0.5**，红队四轮定向复审 **🟢**，所提 4🔵 已按用户指示并入；三轮 1🟡 + 4🔵 已于 v0.4、二轮 3🟡 + 11🔵 已于 v0.3、一轮 1🔴 + 12🟡 + 13🔵 已于 v0.2 收口；状态：**已施工并验收（PR0–PR4，M6–M9；2026-09-29 S21 收尾核对：门禁 6/9/12 含打包版/真机手验，未验，见 §9；D37 定性中、N39 待拍板）**；原动工条件——S9-R1~R4 经定向复核并入 Spec 9 v0.8、Spec 9 PR3 施工完成——均已满足，见 §1.4）  
上位文档：`docs/dev/plans/2026-09-22-harness-evolution-direction.md`（§0.1 旅程第 2–5 步、§0.2 第 1/2 条、§4 施工红线、§5 明确排除、§6 Spec 10）  
相关 ADR：ADR-0020（§3、§4「host utilityProcess 承载 agent 会话」、§5）、ADR-0018（保留条款）、ADR-0021（素材 fetch 人批、browser 逐调用卡）、ADR-0023（记忆首次写入人确认）。**不新立 ADR**（理由见 §6.7）  
契约依赖：**Spec 8**（已施工，`desktop/` 代码即现状，文档 `archive/2026-09-23-electron-desktop-spec.md` v0.5）；**Spec 9**（v0.7 红队 🟢、**尚未施工**，`2026-09-25-agent-session-protocol-spec.md`，以其 §3.1 冻结协议与 §6.6 H-1~H-10 为准）；Spec 2/3/5/6/7（已施工）  
**修订关系**：承接 Spec 9 提给本 spec 的 **S8-R1**；向 Spec 8 新提 **S8-R2~R12**、向 Spec 9 提 **S9-R1~R4**、向 core 提 **C10-R1~R4**、向 direction 提 **DIR-R2**。全部列于 §6.1。S8-R1 已随 Spec 9 §6.1 于 2026-09-25 获授权；**其余全部修订请求于 2026-09-26 获用户授权**（授权不等于可动工：动工仍须本 spec 定向复审 🟢，且 S9-R1~R4 须经红队对 Spec 9 的定向复核）  
对应 issues：—（direction §6 Spec 10 新需求，issues 表无对应条目）  
格式范本：Spec 2、Spec 3、Spec 8（`archive/`），Spec 9  
撰写基线：HEAD `c31201d`；`uv run pytest` 1715 passed；`cd desktop && npx vitest run` 121 passed（均 2026-09-25 实跑）。工作树另有他人未提交改动（`README.md`、`docs/dev/plans/README.md`、Spec 9 文稿），不在 `pipeline/`、`desktop/`、`tests/`

**用户裁决记录**（2026-09-25）

| 时机 | 事项 | 裁决 |
|---|---|---|
| 撰写期间 | 自然语言发起新期的流程 | **先 idea 聊，再建期**：未选期时中央输入进 idea 会话；人觉得差不多了点「建期」、自己填期名；core 建目录；idea 讨论不自动带入新期 |
| 撰写期间 | 回复呈现 | **帧级实时，不做逐字流式**（不改 Spec 9 的非流式协议） |
| 撰写期间 | 停机点自动呼出与焦点 | **人在看别的就不抢**：预览区空着或正显示上一次自动呼出的内容才自动切换；人手动点开过别的文件或媒体正在播放 → 不替换，只出「已就绪 · 查看」条；任何情况下不动键盘焦点 |
| 撰写期间 | 呼出时机 | **回合跑完再呼出**：该期会话有回合在跑时决策卡照常出现、预览不自动切；回合结束后只呼出每种停机点最新的那一个 |
| 撰写期间 | LLM 密钥来源 | **macOS 钥匙串** |
| 撰写期间 | 切到别的期时原会话 | **继续在后台跑**：期列表显示「运行中」「N 张卡待答」徽标；只在点「结束会话」或退出 app 时结束 |
| 撰写期间 | 打开一期时对话区 | **空白，可点「继续上次会话」**：打开期不起进程、不占租约；发第一条消息才起新会话 |
| 撰写期间 | 有活动会话时退出 | **弹一次确认**，列出哪几期在跑、几张卡未答；全部空闲时直接退 |
| 撰写期间 | 卡片位置 | **流内留痕 + 输入框上方常驻待答区**；停机点决策卡同样进待答区 |
| 撰写期间 | 工具轨迹 | **成功一行，失败展开原文**：失败、被拒、判重、中断的调用默认展开并逐字显示模型看到的那段原文；成功调用不显示结果正文 |
| 撰写期间 | scope 切换与记忆确认 | **会话头部两个按钮**：「素材模式」开关；收到记忆待确认通知时才出现「确认记忆…」 |
| 撰写期间 | 发送键 | **Enter 发送，Shift+Enter 换行**；输入法选词中的 Enter 不发送 |
| 撰写期间 | 建期后的 idea 会话 | **保留，可随时切回**；手动「结束会话」或退出 app 才结束 |
| 红队一轮后（🟡-1） | renderer 被攻破后可自行驱动 agent 并批卡 | **browser 卡与素材抓取卡的「批准」改走 main 原生确认框**；其余卡维持一次点击，残余影响范围如实写入 RF-3 |
| 红队一轮后（🟡-4） | 退出 app 时回合中的会话 | **确认退出即跳过收尾**：空闲会话发 `shutdown`；回合中或有未答卡的会话直接 SIGTERM，确定地记 `wrapup:"skipped"`；提 S9-R3 修订 H-6 措辞 |
| 2026-09-26 | §6.1 全部修订请求（S8-R2~R12、S9-R1~R4、C10-R1~R4、DIR-R2、测试改动） | **授权**（「授权你」）；落地时机见 §8：C10-* 随 PR0，S8-* 随 PR1–PR3，S9-* 须先并入 Spec 9 并经其定向复核，DIR-R2 随 PR0 落盘 |
| 红队四轮后 | S9-R3（`aborted` 例外）、S9-R4（每回合恰好一帧、覆盖全部结局、失败照发、带 `turn_id`）在 2026-09-26 授权后补充的措辞 | **确认**（v0.5 最终措辞）；并入 Spec 9 v0.8 后对 Spec 9 做只限 S9-R1~R4 的定向复核 |
| 红队一轮后（🟡-6） | 空模板 `01-topic.md` 被判为 01 已完成 | **改 core：字段没填算 01 未完成**（C10-R3；判定规则由 core 定，本 spec 按「0 期真实回归」的最小规则提出，见 §3.8） |

**作者自报的未实测假设**（红队优先攻击面；正文相应处已标「约」）

| 编号 | 假设 | 状态 / 退路 |
|---|---|---|
| A1 | Playwright `_electron` 的 `locator.click()` 产生 `isTrusted === true` 的点击 | **已由红队实测成立**（Electron 44.4.5 + Playwright 1.63：`locator.click()` → true，`el.click()` → false）；PR1 以 `ack.spec.ts` 零改动为尺子可行 |
| A2 | 聚焦窗口里的键盘 Enter 产生可信 keydown；输入法选词期间 `isComposing` 为真（或 `keyCode === 229`） | **前一半红队实测成立**（`keyboard.press("Enter")` → 可信 keydown，`isComposing:false`、`keyCode:13`）；输入法一半无法自动化，改为门禁 12 手验 |
| A3 | host（utilityProcess）被 SIGKILL 后，它 spawn 的 detached 会话进程的 stdin 立即读到 EOF | **已由红队实测成立**（utilityProcess 内 spawn，host SIGKILL 后约 8 ms EOF）；作者先前在纯 Node 下测得 0.9 s 内（E4） |
| A4 | `security add-generic-password` 建的条目，被 app spawn 的 `/usr/bin/security` 读取时不弹授权框 | 未测（需写登录钥匙串，不在只读实验范围）。退路：首次弹框点「始终允许」；门禁 6 打包版手验 |
| A5 | Spec 9 协议进程从 spawn 到 `ready` 在 1 s 内 | **成立**（PR4 实测，2026-09-27）：真实 core、空会话记录，`SESSION_NEW` spawn → `ready` **129 ms**（`e2e/sessionReal.spec.ts`「A5 实测」）。此前：`import …` 0.06 s、RSS 约 27 MiB（E6） |
| A6 | `SESSION_FRAME_MAX_BYTES = 8 MiB` 盖得住全部合法帧 | **成立，样本小**（PR4 实测）：真实 `data/episodes/*/session.jsonl` 全部 56 条消息中最大一条 **57.5 KB**（一条 `tool` 结果）；协议帧的最大载体（`assistant.text`/`history.text`/`tool.observation`/`request.fields.args`）不超过对应消息的 JSON，距 8 MiB 上限约 140 倍。常量不改，样本积累后再看。v0.2 起会话读端对完整行也检查长度（🟡-8），超限可检测、不会被静默照收 |
| A7 | Spec 9 实现后的帧逐键符合其 §3.1 + 本 spec S9-R1/R2/R4 | **首次联调不成立，core 侧修复后成立**（PR4）：真实 core 暴露 5 处不符（入站 `v`、idea 回合、`tool` 帧键集合、`request.turn_id`、`fields.danger`），按 RF-8 停下报告、经人批准在 core 侧修（Spec 9 `f5bc649`），TS 侧未打补丁；修后 TX-0 逐类键集合与 `REQUIRED`/`CONDITIONAL` 双向一致、`framesLost == 0` |
| A8 | render 等 job 刷 `log` 帧时 renderer 不卡顿 | **基本成立、有轻微卡顿**（PR4 实测）：桩 job 一口气打 2 万行经真实 core → **20 371 个 `log` 帧、0 丢失**；洪峰期间 renderer 最大 rAF 间隔 **333 ms**，超过 250 ms 的共 **3 次**，其余均在一帧量级。不影响正确性；若真实 render 下人感到卡，再调 `CONV_PUSH_COALESCE_MS` 或折叠 log 渲染（不预设） |

---

## 0. 一句话设计

**对话是 host 托管的一条 stdio 管道，按钮只有两种来源：会话发来的 `request` 帧和 core 写下的停机点对象；两者都只能被人的一次可信点击答复，其中 browser 与素材抓取的批准还要过一道 main 原生确认框。**

host 新增 `sessions.ts`：每个会话键（`ep:<epKey>` 或 `idea`）至多一个 Spec 9 协议进程，**懒启动**（人发第一条消息或点「继续上次会话」才 spawn），切期后在后台继续跑，只从 stdout 解析帧、stderr 只作诊断。renderer 中栏换成「工序条 + 对话流 + 待答区 + 输入框」，产物树移到左栏；对话流以帧为粒度实时追加，成功的工具调用一行、失败的逐字展开原文；待答区里的工具卡、抓取卡、检查点卡、记忆卡与停机点决策卡共用一个卡片组件，`conv.answer` 与 `approval.decide` 只允许直接写在这个组件里原生元素的 `onClick` 里、首句检查 `isTrusted`、处理器内不许有循环。新期由人在 idea 会话旁点「建期」、填期名，host spawn 既有的 `ava new <名>`（core 负责校验与建目录，UI 不 mkdir；空模板的新期停在 01，由 agent 经写入卡补全）。LLM 密钥由 host 从钥匙串读出，只进会话进程的环境。停机点自动呼出改为「host 判定该回合已结算（回合结束、`stop_points` 到达、其后开始的一次对象库读取已完成）、预览区不归人占用、没有媒体在播放」时才发生，且从不移动键盘焦点。

---

## 1. 红队裁决与修订纪要

### 1.4 第四轮红队定向复审（v0.4 → v0.5；结论 🟢）

红队结论：三轮 🟡-1 与 4 条 🔵 均已收口，无新阻塞项，PR0–PR3 可动工；PR4 的前置条件不变（§8）。本轮另提 4 条 🔵，用户指示当轮并入（🔵-1 的 `turn_id` 由用户明确指定写法），由红队直接修订本文件。

| 编号 | 红队指控 | 修订动作 |
|---|---|---|
| 🔵-1 | `stop_points` 帧不带 `turn_id`，host 无法区分帧属于哪个回合：旧回合的迟到帧若落在新回合 `turn_finished` 之后，会被当成新回合的帧，新回合在自己的 `ensure_pending` 写盘前结算（v0.1 缺陷重现）。真实 Spec 9 主循环下不可达，属夹具与将来重构风险 | S9-R4 增键 `turn_id`（`ready` 那帧为 `null`）；§2.7 第 2 条改为按 `turn_id` 精确匹配，`null` 与不符的帧不推进结算；TH-20 ⑦ 改写；MUT-71 |
| 🔵-2 | approvals 能力缺席（Spec 8 TA-6）或 `reach !== "ok"` 时 host 不读对象库，每个回合都会走 10 s 超时并写诊断 | §2.7 第 4 条：这两种状态下进入 `await-read` 即结算；TH-20 ⑧；MUT-72 |
| 🔵-3 | R6 扫描 `entries` 重建 `awaiting`，长回合日志让缓冲超过 `CONV_BUFFER_MAX_BYTES` 截头后 `turn_started` 丢失，中间版本重新闪进预览 | `ConvSnapshot` 增 `settlePending`（单独保存，不受截头影响）；R6 改为只看 `phase`/`turnId`/`settlePending` 三个顶层字段；TV-6 ⑪(d)；MUT-73 |
| 🔵-4 | TX-5 ⑤ 缺同步点：两次 `clips` 间隔过短时第一版 pending 从未建出，MUT-50 下「第二版、计数恰增 1」照样成立，用例抓不到它 | TX-5 ⑤ 增同步点：批准第一张卡后先等第一版 pending 出现在 `approvals_store.json` 并断言呼出计数未变，再点第二张卡 |

**授权确认**：S9-R4 的 `turn_id` 键为用户 2026-09-26 指示加入；S9-R3（含 `aborted` 例外）与 S9-R4（含 v0.3/v0.4 补充的义务与 `turn_id`）的 v0.5 最终措辞已于 2026-09-26 获用户确认（「确认」）。下一步：并入 Spec 9 v0.8，并对 Spec 9 做只限 S9-R1~R4 的定向复核。

### 1.3 第三轮红队定向复审裁决与修订纪要（v0.3 → v0.4，1🟡 + 4🔵 全收；原裁决「🟡 修订后定向复审，只剩 1 条 🟡」）

红队报告为终端输出、未落盘，本表即唯一存档。每条均已独立复核。无驳回。

| 编号 | 红队指控 | 复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🟡-1 | S9-R4 要求 `stop_points`「紧跟 `turn_finished`、二者之间不插入其他帧」，又要求它在 `ensure_pending` 写盘之后发出——`ensure_pending` 恰夹在两帧之间，而它与 REPL 同源，新建停机点时会打印 `[approvals] … 待审批已更新`，协议下成为 `log` 帧；失败时的 `notice` 也落在中间。host 若严格要求相邻，凡新建停机点的回合都走 10 s 超时；TH-20 ②、TX-5 ⑤ 未断言 `timedOut: false`，缺陷会被兜底掩盖 | **属实**：`cli.py:1307` 调 `_sync_repl_displayed_approval`，后者在 `cli.py:131` 打印该行；Spec 9 §2.4.1 写明回合末 `ensure_pending`「与 REPL 每轮 `cli.py:1307` 同源」；Spec 9 §2.1 第 4 条把 `sys.stdout` 逐行转为 `log` 帧 | **采纳** | S9-R4 删去相邻约束，改为「每回合恰好一帧，在 `turn_finished` 之后、`ensure_pending` 写盘之后」；§2.7 第 2 条改为「`turn_finished` 之后的第一帧 `stop_points`」，并写明两帧之间可夹任意帧；第 6 条写明兜底只为 core 缺帧而设；TH-20 除 ⑤ 外全部断言 `timedOut === false` 且时钟推进不超过超时的一半；新增 TH-20 ⑥（两帧间夹 `log` 与 `notice`）与 MUT-67；TX-5 ⑤ 同样断言无超时 |
| 🔵-1 | renderer 中途重载时 R6 清空 `awaiting`，而 host 未变、回合可能仍在跑，中间版本会再闪进预览 | 属实 | **采纳** | R6 改为从 snapshot 重建 `awaiting`（最后一个 `turn_started` 无对应 `settled` 即恢复）；TV-6 ⑪ 拆为 (a)(b)(c) 三个变体；MUT-68 |
| 🔵-2 | 结算登记边界未写：超时后才到的 `stop_points`；未结算时下一回合开始 | 属实 | **采纳** | §2.7 第 2 条：每会话键一个槽位、新覆盖旧、超时后到的 `stop_points` 忽略；TH-20 ⑦、MUT-70 |
| 🔵-3 | TX-5 ⑤ 看不到要断言的东西：05 的 v1、v2 呼出的都是 `04-review.html`，从预览内容分辨不出；桩是否产出审片页未写 | 属实（`shared/stopPreview.ts` 的 05 映射为 `04-review.html`） | **采纳** | 预览区增只读属性 `data-auto-open-approval-id`、`data-auto-open-count` 作观测口；桩同时写出带版本标记的 `04-review.html` |
| 🔵-4 | browser 卡的确认框会被参数填充挤掉 URL：参数只有 `action`、`url`、`reason`，URL 在 `args.url` 与 `fields.target`（`cli.py:841-852`），`args` 整段超 4 000 字符截断，`reason` 是模型自由文本，可借键序挤掉 URL 或伪造「URL: …」行 | 属实（`tools.py:538-541` 的 browser schema） | **采纳** | 第 5 层改为按字段排版：确定性字段（操作、目标 URL；抓取卡的序号、标题、URL、类型、来源、时长）在前、全文；模型自由文本（`reason`、`why`）在后、以「以下为模型填写的理由（未经核实）：」引出、单独截断；常量改为 `CONFIRM_FREE_TEXT_MAX_CHARS`；TH-18 增伪造与填充用例；MUT-69 |

红队同时确认：E11 独立复跑一致（`4 failed, 1711 passed`，失败的正是 §6.1 的 4 个用例）；TX-8d 可观测、MUT-48 能被抓住；MUT-58、60~62、64 纸面推演成立；macOS 上 `MessageBoxOptions.signal` 只对挂了父窗口的确认框生效，与第 5 层「窗口模态」一致，两条须一起保留（已在 §2.4 第 5 层并列写明）。

**授权确认**：红队判断 S9-R3 补的 `aborted` 例外只是复述 Spec 9 §2.2 已有语义，可视为在原授权范围内；S9-R4 在授权后新增了义务、本轮又改了措辞，**须人对 v0.4 的最终措辞明确确认一句，再并入 Spec 9**。

**下一轮定向复审应限定的范围**：S9-R4 最终措辞（§3.2）；§2.7 第 2、6 条与 R6；TH-20（含 ⑥⑦ 与 `timedOut` 断言）；TX-5 ⑤ 的观测口；三轮 🔵-1~🔵-4 的处理。红队已声明这些改到位即可判 🟢；之后按约定把 S9-R1~R4 并入 Spec 9 v0.8，并对 Spec 9 做只限这四处的定向复核。

### 1.2 第二轮红队定向复审裁决与修订纪要（v0.2 → v0.3，3🟡 + 11🔵 全收；原裁决「🟡 修订后定向复审，v0.2 不可动工，离 🟢 差 3 条 🟡」）

红队报告为终端输出、未落盘，本表即唯一存档。红队更正了一轮标题的计数（按正文 12 条 🟡）。本轮报告的 🔵 列表中第 6、7 两条被重复粘贴一次，按内容计 11 条。每条在采纳前均已独立复核。无驳回。

| 编号 | 红队指控 | 复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🟡-1 | C10-R1 改为同源后会打红第 4 个用例 `test_create_new_episode_and_reject_duplicates`（`tests/test_agent_cli.py:95-98` 只建 `data/episodes`、不建 `data/library`）；上一轮红队插件用的是旧 `require_data()`、查的是挂着的真实盘，被掩盖；作者照抄了 3 条清单 | **属实**：作者以同源语义原型跑**全量** pytest（E11）：`4 failed, 1711 passed`，第 4 个正是该用例。作者 v0.2 转抄清单、没有自己跑全量，是作者的过失 | **采纳** | §3.7、§6.1、TY-6、PR0 改为 4 个用例并附 E11；§3.7 写明「现有测试影响清单一律以实现后的全量实跑为准」 |
| 🟡-2 | R2 三步结算会卡死、真实通路证据跑不了：① `stop_points` 不保证每回合都发；② `readAt` 下发方式未定义，参与 `approvals` 变化比较则每秒推 delta、不参与则常态收不到；③ host 重启时钟不连续、`autoOpen` 未重置；④ `readAt` 语义未写；⑤ TX-5 ⑤ 中模型无法两次改写 `04-clips.json`（写白名单只有 `01-topic.md`、`02-script.draft.md`，`clips` 需要真实索引） | **属实**：① Spec 9 TP-12 只测「有挂起停机点」；② `service.ts:812-813` 仅在 `approvalsKey` 变化时下发；⑤ `tools.py:26-29` 白名单与红队所述一致 | **采纳** | S9-R4 补「每回合恰好一帧、`items` 可空、覆盖全部结局、紧跟 `turn_finished`」；§2.7 重写为 **host 侧结算**：host 以读取**开始**时刻比较，满足后先推 `episode.delta` 再追加 `settled{turnId}` 条目，renderer 只认 `settled`；`SETTLE_TIMEOUT_MS` 兜底并记诊断；撤销 `readAt`，`approvals` 结构不变；`onConnect` 重置 `autoOpen`（R6）；新增 TH-20、TV-6 ⑧~⑪；TX-5 ⑤ 写明以桩替换夹具副本的 `pipeline/clips.py`、会话与 heal 保持真实；MUT-50 改挂、新增 MUT-59~62 |
| 🟡-3 | MUT-48 挂在 TX-8 上走不到：它植入在 `window-all-closed`，TX-8 走 `before-quit`；关窗后取消时窗口已没，「1 s 内时间线出现事件」无从观察 | **属实**：`main/index.ts:264-273` 只在 `did-finish-load` 发 `renderer-reset`，关窗（`closed`）只置 `win = null`，host 订阅不清，可经 `AVA_SPAWN` 观测 | **采纳** | 新增 TX-8d（`BrowserWindow.close()` 触发、桩返回取消、断言活跃期 `STATUS` spawn 仍按 5 s 周期出现、`activate` 重开后恢复）；MUT-48 改挂 TX-8d |
| 🔵-1 | 「原样搬入」与 TG-4′ 第 1 条冲突：`DecisionBar.tsx:126` 的调用点最近外层函数是传给 `run` 的函数、处理器无事件形参 | 属实 | **采纳** | §2.4 改为「语义不变、结构按第 1 层改写」，给出改写后的形状（`run` 改收 Promise）；TG-4′ 正例纳入它 |
| 🔵-2 | 同一处理器内展开连写两处 `rpc.call("conv.answer", …)` 满足全部五条规则 | 属实 | **采纳** | 第 1 层增「每个处理器内这些方法的调用合计至多一处」；MUT-58 |
| 🔵-3 | TG-16「含 `.call` 的任何别名调用」无法照写实现 | 属实 | **采纳** | 改为 AST 规则：每个名为 `call` 的属性访问必须直接是被调用者，首参为方法闭集中的字面量 |
| 🔵-4 | 第 5 层确认框须显式标出不可见字符（core 只剥 C0/C1，双向覆盖与零宽字符可伪装 URL）；写明窗口模态、截断规则、判定输入取自 host | 属实；E10 实测 `\p{Cf}` 覆盖红队点名的全部码点 | **采纳** | §2.4 第 5 层四条边界；`renderConfirmDetail`；`CONFIRM_ARGS_MAX_CHARS`（v0.4 按三轮 🔵-4 改为 `CONFIRM_FREE_TEXT_MAX_CHARS`）；TH-18 扩展、MUT-63 |
| 🔵-5 | 确认框 `reqId` 在 host 重启后撞号，旧确认框的结果可能批了新 host 的同号请求 | 属实 | **采纳** | `reqId` 带 `hostBootId`；main 以 `AbortSignal`（`electron.d.ts:22586`，E10）在 host 退出时撤下并作废排队；TH-22、MUT-64 |
| 🔵-6 | 退出边界：`quit-proceed` 后应拒新请求；兜底路径下会话走完整收尾，与 RF-17 不一致；e2e `app.close()` 须预装「退出」桩 | 属实（Spec 9 §2.8 EOF 路径做收尾） | **采纳** | §2.10、§3.1 增「退出期间 `E_BUSY`」；RF-17 如实写例外；§2.10 增 e2e 约定；TH-21、MUT-65 |
| 🔵-7 | S9-R3 只写 `skipped`，漏了 §2.10 的 `aborted` 例外 | 属实 | **采纳** | S9-R3 措辞补齐 |
| 🔵-8 | RF-3 事实错误：asset scope 无 `run_pipeline`，模型碰不到 `cloud up` | 属实（`config/agent/tools.json` 的 asset 工具表） | **采纳** | RF-3 改正 |
| 🔵-9 | TF-1 只 realpath 父目录，目标本身是外指符号链接时仍会写穿；没有静态守卫保证只经 `fixtureWrite`；范围是否含 Spec 8 既有夹具未写 | 属实 | **采纳** | TF-1 增 `lstat` 拒符号链接；新增 TF-3 静态守卫；写明范围只含本 spec 新增的会话类夹具；MUT-66 |
| 🔵-10 | TF-2 没管联网工具：副本 `config/agent/web*.json` 指向真实 duckduckgo，`web_fetch` 可取任意 URL | 属实（`config/agent/web.json` 的 `search.endpoint` 为 duckduckgo；`web.py:73-74` 优先读 `web.local.json`） | **采纳（两手都做）** | 改写副本 `web.local.json` 的搜索端点到本地；剧本加载时校验 `web_fetch`/`crawl`/`browser` 的 URL 只许 `http://127.0.0.1:`；TF-2 扩展 |
| 🔵-11 | MUT-18 机理不准：删掉 `require_data_at` 后先起作用的是「episodes 须已是目录」，退出码仍为 2 | 属实 | **采纳** | 机理改为靠 stderr 原文与「`library` 缺席、`episodes` 存在」场景 |

**授权范围说明**：S9-R3（补 `aborted` 例外）与 S9-R4（补「每回合恰好一帧」等）的措辞在 2026-09-26 授权之后有补充，**请人确认补充部分仍在授权范围内**。

**下一轮定向复审应限定的范围**：① §2.7 host 侧结算（第 1~7 条）与 S9-R4 新措辞、TH-20、TX-5 ⑤ 的桩方案；② TX-8d 与 MUT-48；③ §3.7 的 4 个用例与 E11。二轮 🔵 的修订（§2.4 第 1、5 层，TF-1~TF-3，RF-3，RF-17）请按表抽查。其余章节二轮已判收口，本轮未改。

### 1.1 第一轮红队裁决与修订纪要（v0.1 → v0.2，1🔴 + 12🟡 + 13🔵 全收；原裁决「🟡 修订后定向复审，v0.1 不可动工」）

红队报告为终端输出、未落盘，本表即唯一存档。报告标题写「11 条 🟡」，正文实列 🟡-1~🟡-12 共 12 条，按正文计。每条在采纳前均已对照工作树独立复核（「复核」列）。无驳回。

| 编号 | 红队指控 | 复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🔴-1 | 「以 `.venv/bin/python` 的替身出现」的假会话夹具会顺着 `desktop/tests/helpers.ts:43` 的 `.venv` 软链写穿，覆盖 Homebrew 的 python3.12；固定 PATH 下 `#!/usr/bin/env node` 也找不到 node | 属实：`helpers.ts:43` 为 `symlinkSync(join(REPO, ".venv"), join(root, ".venv"))`；`realpath .venv/bin/python` → `/opt/homebrew/Cellar/python@3.12/3.12.13_4/…/python3.12` | **采纳** | §7 夹具改为仿 `fakeCore`（`e2e/ackFixtures.ts:154`）在**已复制的** `pipeline/agent/` 里写假 `protocol.py`（Python 剧本驱动），不碰 `.venv`；新增夹具断言：任何写入目标 `realpath` 后必须落在临时根内（TF-1、MUT-42） |
| 🟡-1 | RF-3 把影响范围写错：host 绑定只查「打开中、选项合法」，`request_id` 本来就推给 renderer；T1 防模型不防 renderer。攻破的 renderer 可 `conv.send` 任意指令再自批卡片，包括 browser、抓取、写产物 | 属实：§4.2 的 host 绑定只核对打开集合与 `options`；§3.1 `ConvSnapshot.open` 把全部打开请求推给 renderer | **采纳 + 用户裁决** | 用户裁决：browser 卡（`kind:"tool_call"` 且 `fields.tool === "browser"`）与抓取卡（`kind:"fetch"`）的**批准**，经 host 请 main 弹原生确认框，人点「批准」后才写 `answer`；拒绝不弹。§2.4 第 5 层、§3.3 新增 `confirm-query`/`confirm-result`；RF-3 重写影响范围差（ack：同 Spec 8；人审答复：新增，browser/抓取已收回；任意 spawn：不能）；TH-18、TX-13、MUT-43 |
| 🟡-2 | TG-4′ 只查字面量祖先链，漏检：onClick 里定义具名函数再 `forEach`；`rpc.call(m)` 动态方法名（`renderer/testHooks.ts:17` 有先例）；自定义组件上的 `onClick` 可被 `useEffect` 传伪造 `{nativeEvent:{isTrusted:true}}` 调用；答完卡 A 后按钮节点复用，人再按 Enter 以可信事件答复没看过的卡 B | 属实（纸面推演逐条成立；`testHooks.ts:17` 为 `w.__avaTestCall = (method, params) => rpc.call(method, params)`） | **采纳** | TG-4′ 收紧为五条（§2.4 第 1 层）：调用点最近的外层函数必须就是该 `onClick` 函数；`onClick` 函数体内禁任何循环语句与迭代方法；`onClick` 只许挂在小写原生元素上；首句检查的 `e` 必须是处理器第一个形参；每张卡 `key={request_id}`（停机点卡 `key={approval_id}`）。新增 TG-16（renderer 全部 `rpc.call` 首参为字符串字面量，只豁免 `testHooks.ts`）、TX-14（键盘答完一张卡后再按 Enter，下一张零 `answer`）；MUT-23b、MUT-44~46 |
| 🟡-3 | 退出流程让 Spec 8 已过门禁回归：host 熔断或崩溃时无人回 `quit-state`，app 永远退不出、e2e 挂在 `app.close()`；`window-all-closed` 先发 `shutdown`（停定时器）再 `app.quit()`，人在确认框点「取消」后 host 不再轮询 | 属实：`main/index.ts:201-212` host 退出后 `host = null`、熔断走 `showFatal`；`main/index.ts:303-307` 先 `sendToHost({type:"shutdown"})` 再 `app.quit()`；`host/index.ts:91-93` `shutdown` → `service.stop()` 清定时器 | **采纳** | §2.10 重写：host 不在、未就绪、已熔断，或 `QUIT_QUERY_TIMEOUT_MS` 内未应答 → 直接退出；生命周期 `shutdown` 挪到 `quit-proceed` 之后；`window-all-closed` 只调 `app.quit()`；退出流程状态机防连按 Cmd+Q 重入。TX-8b/8c、MUT-47~49 |
| 🟡-4 | 退出只留 2 s 违背 H-6（收尾是一次最长 60 s 的模型请求）；若 SIGTERM 到达时已在收尾中，Spec 9 §2.2 记 `aborted` 而非 §6.2 写的 `skipped`，还白花一次请求；「红队可裁」的交接不成立 | 属实：Spec 9 §2.2 状态表「收尾中」到达的中断 → 放弃收尾（`wrapup:"aborted"`） | **采纳 + 用户裁决** | 提 S9-R3 修订 H-6（§6.1）；用户裁决按红队建议：人确认退出后，空闲会话发 `shutdown`，回合中或有未答卡的会话直接 SIGTERM（Spec 9 §2.2 第 5 条，确定地记 `skipped`），不再「先 shutdown 再等 2 s」。删 `QUIT_SHUTDOWN_WAIT_MS`；§6.2 H-6 行改写 |
| 🟡-5 | C10-R1 按 §3.7 字面实现会打红 3 个现有用例（它们只替换 `paths.ROOT`、不建 `data/episodes`）；`require_data()` 查的是 import 时算好的 `paths.DATA`，TY-1 的「缺席 / 悬空」在临时根下测到的是真实 `data/`；MUT-18 被第 1 步掩盖 | 属实：`paths.py:20-21` `DATA = ROOT / "data"` 为模块常量；`tests/test_agent_cli.py:400`、`425`、`tests/test_agent_tools.py:708` 均只 `monkeypatch.setattr(paths, "ROOT", tmp_path)` 并依赖 `create_new_episode` 自建 `data/episodes`（即依赖本缺陷） | **采纳** | §3.7 重写：检查与写入同源，`paths` 增 `require_data_at(data)`（`require_data()` = `require_data_at(DATA)`，行为字节不变），`create_new_episode` 以调用时的 `paths.ROOT / "data"` 为准；3 个用例列入 §6.1 测试改动（改为在 `tmp_path` 建好 `data/library`、`data/episodes`）；MUT-18 改为「删 `require_data_at` 检查」；TY-1 断言精确退出码 2 与 stderr 原文 |
| 🟡-6 | 「agent 在 01 工序推断」无机制：空模板 `01-topic.md` 一存在 status 即判 01 完成、落在 02 | 属实（作者实测，E7）：模板期 `status --json` → `current_step: "02 脚本写作"`、`completed_steps: ["01 选题"]`；`status.py:197` 只看文件是否存在 | **采纳 + 用户裁决** | 用户裁决改 core：新增 C10-R3（§3.8）。规则以真实数据先量后定：严格规则（类型 ∈ 题材表、杂谈须带模式与张力）会让 26 个真实期中 5 个倒退回 01；最小规则「存在『类型』行且其值全为空 → 01 未完成」0 期倒退、模板命中（E7），挂插件跑全量 1715 passed（E8）。§2.5 第 4 步改写；TY-8、MUT-53/54 |
| 🟡-7 | 自动呼出在真实通路上不确定：`turn_finished` 经 stdout 即时到达，对象库要等 1 s 轮询加 heal；R2 可能先呼出中间版本 v1、下个 tick 再呼出 v2；TV-6 纯函数测不出，TX-5 ③ 时红时绿 | 属实：`service.ts:783-786` 活跃期每个 tick 才 `loadApprovals`；且 Spec 9 §4.7 回合末尾的顺序为「写 `turn_end`；`turn_finished`、`stop_points`；`ensure_pending`」，`ensure_pending` 未必先于 `turn_finished` | **采纳（比建议再收紧一步）** | R2 改为三步门槛：收到 `turn_finished` → 收到其后的 `stop_points` → host 对该期对象库的一次读取，其读取时刻晚于 `stop_points` 到达时刻；两个时刻都用 host 时钟打。另提 **S9-R4**：`stop_points` 帧必须在本回合 `ensure_pending` 完成之后发出（否则「晚于 `stop_points`」仍可能读到旧库）。TV-6 ⑧⑨、TX-5 ⑤（真实 heal 时序）、MUT-50 |
| 🟡-8 | TH-12 按现写会红：`LineSplitter` 只查无换行残段，同一块里遇到换行的整行不查长度；超限合法帧会被照收 | 属实：`shared/jsonl.ts:13-41` 只在 `start < chunk.length` 分支比较 `maxPartialBytes` | **采纳** | 会话读端在 `LineSplitter` 之外对每个完整行再查 UTF-8 字节数（`host/sessions.ts` 的 `boundedLines`），超限计 `oversize`；不改 Spec 8 tail 共用的分行器。TH-12 改为「块内完整行超限」「残段超限」两个变体；MUT-15 机理改写 |
| 🟡-9 | host 崩溃重启后对话状态未定义：renderer `onConnect` 只重新 activate 期；新 host 的 `generation` 从头计，与旧值撞上时新会话条目会拼到旧对话上，旧的打开卡继续显示可答 | 属实：`App.tsx:86-93` `onConnect` 只 `episodes.list` 与 `episode.activate` | **采纳** | `generation` 改为 `"<hostBootId>:<n>"`（`hostBootId` 为 host 启动时的随机串）；renderer `onConnect` 清空全部会话桶，按 `episodes.summary` 与 `idea` 摘要重新取 snapshot，原先活着、但新 host 不认识的会话键显示「会话已结束（host 重启）」。TV-10、TH-19、MUT-51 |
| 🟡-10 | PR4 e2e 有真实出网与花钱风险：`makeFixtureRepo` 用 `cpSync` 复制真实 `config/`（本机有 `agent.local.json`、`api_key_env: CPA_API_KEY`），未打包构建的 `KEYCHAIN_READ` 调真 `/usr/bin/security` | 属实：`helpers.ts:41` `cpSync(join(REPO, "config"), …)`；`config/agent.local.json` 存在 | **采纳** | §7 前言：会话类夹具强制改写副本的 `config/agent.local.json` 为本地假端点与 `api_key_env: "AVA_TEST_KEY"`，并强制启用假钥匙串钩子；夹具断言两条都生效（TF-2），否则用例直接失败；MUT-56 |
| 🟡-11 | 切换 repoRoot 有竞态：S8-R10 只在第一段守卫查活会话，`choosing`/`switching` 期间不拦新请求，会话会被绑到旧仓库 | 属实：`service.ts:931-963` 只在 `requestRepoRootChange` 入口检查 | **采纳** | S8-R10 扩展：`choosing` 或 `switching` 期间 `conv.send`、`conv.resume`、`episode.create` 一律 `E_BUSY`；`NEW_EPISODE` 计入在途，切换等它结束。TH-13 扩展、MUT-52 |
| 🟡-12 | 冻结条款互相矛盾：§4.2 `conv.answer` 先查活进程（`E_SESSION`）再查打开集合，而 §2.10 与 TH-4 要求退出后答复得 `E_STALE`，按冻结顺序 TH-4 必红 | 属实 | **采纳** | 口径定为 `E_STALE`：先查打开集合（进程退出时已清空），再查进程；§4.2 与 §2.10 同步；MUT-4 机理改写 |
| 🔵-1 | `feedback` 与「参数全为字符串」「空文本发 null」矛盾；`checkParamKeys` 跳过任何名为 `feedback` 的键的类型检查（`protocol.ts:80`）；超 1 MiB 时 core 回不带 `rid` 的 `E_TOO_LARGE`，host 只能等超时 | 属实 | **采纳** | §3.1：`feedback` 为可选键，出现时必须是字符串且 UTF-8 ≤ `FEEDBACK_MAX_BYTES`，由 host 自行校验；缺席即帧内 `null`；整帧长度在写入前预检，永不触发 core 的 `E_TOO_LARGE` |
| 🔵-2 | 留痕文案「已作废（原因）」「附反馈」在 `request_closed` 帧里无对应字段 | 属实（Spec 9 §3.1 只有 `reason`/`decision`） | **采纳** | 新增本地条目 `answered_local{requestId, decision, feedback}`；作废原因只在 `voided_local` 时可知，其余显示「已作废」不带原因 |
| 🔵-3 | `api_key_env_name` 在 `base_url`/`model` 缺失时应返回 `None`，与 `load_llm_config` 一致 | 属实（`llm.py:136-139` 三字段缺一即 `None`） | **采纳** | §6.1 C10-R2 与 TY-5 写明 |
| 🔵-4 | 建期名应禁 `-` 开头（`-x` 能建成；期名 `--continue` 会与 `ava --continue` 歧义） | 属实（纸面：§3.7 v0.1 规则无此条） | **采纳** | §3.7 增禁 `-` 前缀；TY-2 加 `-x`、`--continue` |
| 🔵-5 | E1 记录不准：`../../escape` 建在临时仓库根内、episodes 根外，不是「临时根外一级」；建到根外的是 `../../../escape` | 属实（作者 E1 输出中 `escape` 的相对路径即在根内） | **采纳** | 附录 E1 改正；§2.5 表同步 |
| 🔵-6 | `lifecycle.ts:7-21` 实为 `8-22` | 属实 | **采纳** | 改正 |
| 🔵-7 | TG-13 范围太窄 | 属实 | **采纳** | 改为：全 `renderer/` 中只有 `PreviewPane.tsx` 可 import `markdown-it`、使用 `dangerouslySetInnerHTML` |
| 🔵-8 | TX-5 ④ 与 ② 冲突（② 点了产物树，焦点必然离开输入框） | 属实 | **采纳** | ④ 改为「每次自动呼出与出现『已就绪』条的前后，`activeElement` 不变」 |
| 🔵-9 | TH-9 ② 的假进程必须同时忽略 SIGTERM，否则 MUT-12 被「pid 退出后清理整组」掩盖 | 属实 | **采纳** | TH-9 ② 夹具忽略 `shutdown` 与 SIGTERM |
| 🔵-10 | TH-11 可能掩盖 MUT-14：`error` 帧已给出 `E_SESSION_LOCKED` | 属实 | **采纳** | 新增 TH-11b「只退 3、不发帧」；MUT-14 改挂 TH-11b |
| 🔵-11 | §2.4 与 §7.1 的 TG-10 清单不一致（漏 `interrupt`、`end`、`resume`）；`episode.create` 属 S8-R2 却排在 PR1 | 属实 | **采纳** | §2.4 与 TG-10 统一为同一张表（§2.4 第 1 层）；§8 写明 PR1 提前落地 S8-R2 中的 `episode.create` 一项，其余方法随 PR2 |
| 🔵-12 | `created_at` 比较口径：Python `isoformat()` 微秒为 0 时省略小数，按字符串比较会排错序 | **部分属实，且扩大**：`approvals.py:91-93` `_utc_now_iso()` 即 `isoformat()`；按字符串比较 `"…:00Z"` 与 `"…:00.000001Z"`，`.`（0x2E）< `Z`（0x5A），前者被判为更新。**Spec 8 已落地的 `host/heal.ts:109` `latestPerStop` 同样按字符串 `>=` 比较**，同一潜在缺陷已在生产代码里 | **采纳** | 新增纯函数 `shared/isoTime.ts` `compareIso`（小数秒补足 6 位后按数值比较）；`autoOpen` 使用它；另提 **S8-R12**（非阻塞）把 `heal.ts:109` 改用同一函数。TV-11、MUT-55 |
| 🔵-13 | A1、A3 结论回填假设表 | — | **采纳** | 假设表已更新 |
| 自查 1 | 撰写 C10-R3 时发现 `check_script.py:100` 的 `TOPIC_FIELDS = r"^\s*(类型\|模式)\s*[:：]\s*(.+)"` 中 `\s*` 可跨行：对 `ava new` 模板解析出 `类型 = "模式："`（E9） | 作者复核中发现 | **提出** | C10-R4（非阻塞）：改为行内空白 `[ \t]*`；C10-R3 的规则不复用这个正则 |
| 自查 2 | S9-R4 的必要性（见 🟡-7 复核列）：Spec 9 未规定 `stop_points` 与 `ensure_pending` 的先后 | 作者复核中发现 | **提出** | S9-R4 |

**下一轮定向复审应限定的范围**：① §2.10 退出流程（host 缺席、熔断、超时、重入、取消后 host 仍在轮询）与 S9-R3 的措辞；② §2.7 R2 三步门槛与 S9-R4 是否足以让 TX-5 ⑤ 稳定；③ §2.4 第 1 层 TG-4′ 五条规则与 TG-16 能否再被绕过、第 5 层原生确认框的触发判定；④ §3.7/§3.8 的 C10-R1/R3 与 3 个改写用例、E7/E8 实测；⑤ §7 夹具（TF-1、TF-2）能否防住 🔴-1 与 🟡-10 的两类事故。其余章节 v0.1 已抽查通过（红队 32 处行号、34 条变异纸面推演），本轮只改了措辞与编号。

---
## 2. 关键设计决策（含代码现状证据）

### 2.1 决策 1：会话进程由 host 托管——懒启动、后台常驻、只读 stdout（设计问题 5）

**现状证据**：spawn 闭集全部是短命令，`stdio: ["ignore", "pipe", "pipe"]`、有超时（`desktop/src/host/spawner.ts:205-211`、`63-109`）；Spec 8 §2.9 写明「v1 不拉起长任务；app 退出时 host 不向子进程发信号」。host 的 `shutdown` 只停定时器（`host/index.ts:91-93` → `service.ts:261-264`）；main 在 `window-all-closed` 里发完 `shutdown` 立即 `app.quit()`（`main/index.ts:303-307`）。ADR-0020 §4 已把「agent 会话」列为 host utilityProcess 的职责。

**裁决**：

1. **会话键**：`ConvKey = "idea" | "ep:<epKey>"`。每个键至多一个活进程；idea 全局至多一个。进程在 spawn 时绑定该期当时的绝对路径（Spec 9：进程绑定期目录，无切换命令）。
2. **懒启动**（用户裁决）：`episode.activate`、`episode.subscribe`、`episode.refresh`、切期、窗口聚焦**一律不 spawn 会话**。只有两种人的操作会起进程：`conv.send`（该键无活进程时以 `new` 模式起）与 `conv.resume`（`--continue`）。（**2026-10-08 修订注记（D42 / Spec 18；N60，D42-C 🔵 C-3 补列）**：仍只有这两种人的操作起进程，但模板有两处变化——① `episode.create` 带入了选题记录（`migrated:true`）的期，首次 `conv.send` 以 `continue` 模式（`SESSION_CONTINUE`）起，接着迁入段聊；host 侧一次性标记，发送 / resume 成功才消费，切仓清空，同名重建未带入作废（人 2026-10-08 裁决，Spec 18 §9.6 偏差 1）；② `conv.resume` 对 idea 以 `SESSION_IDEA` 起，core 启动即恒恢复。）理由：Spec 9 规定协议进程**启动即取期租约**（Spec 9 §2.5），打开 app 看一眼就占租约会让同期的终端 REPL 发不了 agent 消息（Spec 9 I-10）。
3. **后台常驻**（用户裁决）：切到别的期不影响原会话；回合继续跑，卡片挂着等人切回来。进程只在四种情况下结束：人点「结束会话」；退出 app（§2.10）；进程自己退出（崩溃、协议错误）；host 死亡导致 stdin EOF（A3）。
4. **只读 stdout（H-9）**：stdout 按 `\n` 切行（复用 `shared/jsonl.ts` 的 `LineSplitter`，上限 `SESSION_FRAME_MAX_BYTES`），每行经 `shared/convFrames.ts` 校验（§3.2）后才进入会话状态；stderr 只进一个 8 KiB 尾部环形缓冲，显示在诊断面板与「会话已退出」条目里，**从不解析**。
5. **写 stdin 的入口闭集**：`conv.send`（`user_message`）、`conv.interrupt`、`conv.answer`、`conv.command`、结束序列（`shutdown`）。这五处是 host 里仅有的写会话 stdin 的调用点（TG-11）。host **从不**自行生成 `user_message`（H-8）。
6. **renderer 重载不影响会话**：`resetRenderer`（`service.ts:271-274`）只清期订阅，不碰会话；renderer 重连后对已知会话键发 `conv.snapshot` 取回缓冲。
7. **host 崩溃**：会话进程是 detached 的进程组组长，不随 host 死亡；其 stdin 写端随 host 关闭，Spec 9 按 EOF 处理（中断当前回合 → 有回复则收尾 → 写 `turn_end` → 退出 0）。新 host 不认领旧进程：界面上该期显示「会话已结束（host 重启）」与「继续上次会话」。旧进程收尾期间仍持租约，此时 `conv.resume` 得 `E_SESSION_LOCKED`，文案「上一会话进程仍在收尾（pid …），稍后再试」。
8. **可达性**：`reach !== "ok"` 时 `conv.send`、`conv.resume` 返回 `E_UNREACHABLE`；已在跑的会话不杀（它自己会遇到写失败并按 Spec 9 §2.5「写失败即停」处理）。
9. **期目录消失**：期列表刷新时若某活会话绑定的路径已不存在，该会话标「期目录已不存在」、输入框禁用，不自动结束（进程内状态由 Spec 9 处理，人可点「结束会话」）。

### 2.2 决策 2：对 Spec 8 冻结闭集的逐条修订（设计问题 1）

Spec 8 三个闭集与两条不变量的修订全部列在这里，编号对应 §6.1。

| 闭集 / 不变量 | Spec 8 现状（代码） | 修订 | 编号 |
|---|---|---|---|
| renderer↔host 方法闭集 | 11 个方法（`shared/protocol.ts:7-18`、`47-59`） | 新增 8 个：`conv.send`、`conv.resume`、`conv.interrupt`、`conv.answer`、`conv.command`、`conv.end`、`conv.snapshot`、`episode.create`（签名见 §3.1）；参数仍 exact-keys、除 `feedback` 外全为字符串、**无文件系统路径字段** | S8-R2 |
| push 主题 | 5 个（`protocol.ts:31`） | 新增 `conv.snapshot`、`conv.delta` | S8-R2 |
| 错误码 | 9 个（`protocol.ts:20-29`） | 新增 `E_SESSION`（会话进程未就绪、已退出或回了协议错误，附原错误码与原文）、`E_SESSION_LOCKED`（期租约被占） | S8-R2 |
| spawn 闭集 | 9 个模板，全部短命令、stdin `ignore`（`spawner.ts:15-24`、`205-211`） | 新增 `SESSION_NEW`、`SESSION_CONTINUE`、`SESSION_IDEA`（长驻、stdin `pipe`、无超时）与 `NEW_EPISODE`、`PROBE_KEY_ENV`、`KEYCHAIN_READ`（短命令，规则同现状）；见 §3.4 | S8-R3 |
| 子进程环境白名单 | 不继承 `process.env`，明确排除 `*_API_KEY`（`spawner.ts:115-127`） | **只有**三个 `SESSION_*` 模板额外带一个变量：名字取自 `PROBE_KEY_ENV`、值取自 `KEYCHAIN_READ`，名字须匹配 `KEY_ENV_NAME_RE`；其余模板不变（TI-7 继续成立） | S8-R1②、S8-R3 |
| I1（host 零写入） | TG-2、TI-3a、TE-11 | **不变**。会话、建期的全部写入都发生在 core 子进程里 | — |
| I2（桌面端引发的 core 写入闭集） | 自愈簿记 + 显式点击两类（Spec 8 §2.3） | 增两类：**会话写入**——桌面端 spawn 的会话进程引发的 core 写入 = 终端 REPL 同情形下的写入（`session.jsonl`、经人审卡批准的工具副作用、`ensure_pending` 的自愈簿记、`approvals.jsonl` 审计行等，逐条以 Spec 9 为准）；**建期**——人点「建期」后 `ava new` 写出的期目录与 `01-topic.md` | S8-R1①、S8-R4 |
| 长任务与退出语义 | 「v1 不拉起长任务；app 退出不向子进程发信号」（Spec 8 §2.9） | 会话进程是例外：结束序列与退出确认见 §2.10 | S8-R5 |
| host↔main 生命周期消息 | 闭集（`shared/lifecycle.ts:8-22`） | 新增 `quit-query`/`quit-state`/`quit-proceed`/`sessions-down`/`confirm-query`/`confirm-result`（§3.3）；`window-all-closed` 不再先发 `shutdown` | S8-R5 |
| 停机点自动呼出 | 活跃期出现新 pending 即替换预览（Spec 8 §2.5；`renderer/App.tsx:149-166`） | 改为 §2.7 的确定性规则（host 侧回合结算 + renderer 呼出规则），收口 RF-24 | S8-R6 |
| 显式点击守卫 TG-4 | `approval.decide` 只在 `renderer/DecisionBar.tsx` 的 `onClick` 里（`tests/static/scan.ts:165-188`） | 泛化为 §2.4 第 1 层五条规则（指定文件、调用点的最近外层函数即处理器、处理器内禁循环与迭代方法、首句 `isTrusted`、只挂原生元素）与 TG-16/TG-17；`DecisionBar.tsx` 并入 `HumanCards.tsx` | S8-R7 |
| 版面与期列表摘要 | 中栏 = 工序卡 + 决策条 + 产物树（`App.tsx:184-194`）；`EpisodeSummary` 无会话信息 | 产物树移左栏；中栏 = 工序条 + 对话流 + 待答区 + 输入框；`EpisodeSummary` 增 `conv`、`EpisodesList` 增 `idea`；Spec 8 §2.11「v1 不做对话面板」一条删去 | S8-R8 |
| §3.1 规则 6（`approval_resolved` 两种形状） | 无 `approval_id` 一律标「命令卡拒执」（`App.tsx:395`） | 按载荷 `decision` 与 `source` 显示「命令卡批准 / 拒绝」（H-10；`status_card.py:366-378` 对批准与拒绝都发该事件） | S8-R9 |
| repoRoot 切换互斥 | decide/heal 在途 → `E_BUSY`（`service.ts:933`），只在入口检查（`service.ts:931-963`） | 另加：存在任何活会话 → `E_BUSY`「先结束全部会话再切换仓库」；`choosing`/`switching` 期间 `conv.send`、`conv.resume`、`episode.create` 一律 `E_BUSY`；`NEW_EPISODE` 计入在途 | S8-R10 |
| 停机点「最新对象」的比较 | `host/heal.ts:109` 以字符串 `>=` 比较 `created_at` | 改用 `shared/isoTime.ts` 的 `compareIso`（🔵-12 扩大） | S8-R12 |
| egress 边界 | Spec 8 §6.3：「未来对话面板出网时须重新纳入 egress 审计」 | 复审结论：main/host/renderer 仍零出网（TS-5、TG-3 不变）；LLM 流量只在会话进程（core）内，经既有 `assert_egress_boundary`（`tools.py:280-291`）；桌面端到 core 的新数据只有人打的字（经 stdin） | S8-R11（文字修订） |

**不改的**：媒体协议与路径守卫、CSP、打包加固、tail 与 heal 触发闭集 H1–H5、决策条的四道闸与 05 两步链、`--id` 与指纹核验、npm 依赖白名单（零新增，TG-1 不变）。

### 2.3 决策 3：对话面板的呈现（设计问题 2）

**帧级实时**（用户裁决）：Spec 9 v1 的模型请求是非流式的（Spec 9 §2.8），一次模型回复完整到达才出一帧 `assistant`。面板在以下时刻立即更新：`turn_started`、每个 `tool` 帧（start / end）、`log`、`request`、`request_closed`、`assistant`、`notice`、`error`、`turn_finished`。回合进行中显示「运行中 · 已 N 秒」（按 `turn_started` 到达时刻计时，只作展示）。逐字流式不在本 spec（§2.11）。

**条目映射**（纯函数 `shared/convFold.ts`，host 与 renderer 共用同一份折叠逻辑）：

| 来源 | 呈现 |
|---|---|
| 人的消息 | host 在 `user_message` 被 `turn_started` 确认后记一条 `user` 条目（人打的原文）；被 `error` 拒收则不记，输入框保留原文 |
| `assistant{kind:"answer"}` | 助手气泡，**纯文本**（`white-space: pre-wrap`，不渲染 Markdown、不识别链接，H-3）。**2026-10-08 修订（D46）**：改渲染 Markdown（关链接与图片），见 RF-13 |
| `assistant{kind:"wrapup"}` | 同上，标「收尾总结」 |
| `assistant{kind:"local_note"}` | 灰色，标「本地说明（未进入会话历史）」 |
| `tool{phase:"start"}` → `tool{phase:"end"}` | 一行：`⚙ 工具名 摘要 ✓/✗ 耗时`。耗时 = 两帧到达 host 的时间差，只作展示。`duplicate: true` 标「重复调用」。**非成功**（S9-R1 的判定：失败、被拒、判重、中断、未执行）默认展开 `observation`，等宽、逐字、不截断（用户裁决；判据 4、ADR-0020 §5「错误 observation 完整保留」）。**2026-10-08 修订（D46，人新裁决）**：改为默认收起，点开仍逐字、不截断 |
| `log` | 挂在当前回合里最近一个尚未结束的工具行下，折叠为「作业输出（末 N 行）」；没有未结束的工具行时单独成行 |
| `request` | 流内一行留痕「[工具卡] write_episode_file · 待答」，卡片本体进待答区（§2.4）；`request_closed` 后留痕行改为「已批准 / 已拒绝（附反馈）/ 已作废（原因）」 |
| `stop_points` | 流内一行不可点击的提示「停机点 03.5 待审（在待答区处理）」（H-2：按钮只来自对象库，不来自帧） |
| `notice` / `error` | 按 `level` 着色的一行纯文本，原文显示 `code` |
| `turn_finished` | 回合脚注：模型调用 X · 工具 Y（执行 Z、重复拒绝 W）· 检查点 C · 用时 S s · 停止原因 · 收尾状态 |
| `history`（恢复时） | 首条前插分隔「以下为恢复的历史，重建自 session.jsonl：只含消息，不含当时的工具耗时与作业输出」；`role:"system_note"` 折叠为「系统注入」；`role:"tool"` 按 S9-R1 同一判定显示 |
| `ready` | 不进流：更新会话头部（scope、LLM 状态、`continue_status`）。`other_sessions` 非空时流首一行「该期还有 N 个更早的会话；最近一个 <sid 前 8 位> · M 条消息 · <时间>」；`llm: "degraded"` 时一行降级说明（§2.9） |
| 进程退出 | 一行「会话已结束（退出码 / 信号）」，附 stderr 尾部（折叠） |

**输入框**：Enter 发送、Shift+Enter 换行、输入法选词中（`isComposing` 或 `keyCode === 229`，A2）的 Enter 不发送（用户裁决）。回合运行中输入框只读、「发送」换成「停止」（Spec 9 回合中 `user_message` 必得 `E_BUSY`，排队代发违反 H-8）。「停止」发 `interrupt{turn_id}`（H-6）。

**「有损」声明分两层**（回答设计问题 2 的后半）：

- **事件时间线**（`events.jsonl`）：缺口在磁盘上无痕迹（Spec 8 §2.4 列举的 queue.Full、期目录缺席、父进程被杀），**有损声明原样延续**，时间线顶部横幅不变。
- **对话面板**（协议帧）：缺口**全部可检测**，因此不挂常驻有损横幅，而是在缺口处显式标出。一个会话进程存活期间，帧由 Spec 9 的无界队列与独立写线程整帧写出，只在三种情况下丢：单帧超过 `SESSION_FRAME_MAX_BYTES`、行不能解析、host 缓冲超过 `CONV_BUFFER_MAX_BYTES` 截头。每种都计数并在流内标「此处丢失 N 帧（原因）」或「更早的对话已从界面缓冲移出，可结束会话后『继续上次会话』重建」。跨进程（恢复）只保证**消息**完整（`session.jsonl`），轨迹细节不保证，由 `history` 分隔条说明。

### 2.4 决策 4：统一人审卡片组件与「显式点击」纪律（设计问题 3）

**组件**：新文件 `renderer/HumanCards.tsx`，导出 `RequestCard`（Spec 9 的四种请求）与 `StopPointCard`（Spec 8 决策条的全部内容从 `DecisionBar.tsx` 搬入：**语义不变**——四道闸、Reject 表单、门禁 14 横幅、RF-20 提示、全部 `data-testid` 照旧；**结构按第 1 层改写**——现状 `DecisionBar.tsx:126` 为 `onClick={() => void run(obj, () => rpc.call("approval.decide", …))}`，调用点最近的外层函数是传给 `run` 的箭头函数、处理器也没有事件形参，改为 `onClick={(e) => { if (!e.nativeEvent.isTrusted) return; void run(obj, rpc.call("approval.decide", …)); }}`，`run` 的第二个参数由「返回 Promise 的函数」改为 Promise 本身；二轮 🔵-1），外框 `CardFrame` 共用。`DecisionBar.tsx` 删除（它的逻辑整体搬家，不是删功能；§8 PR1 以 e2e `ack.spec.ts` 零改动为尺子，A1 已证其点击方式可行）。

**待答区**（用户裁决）：输入框正上方，按到达顺序列出活跃会话键的**未关闭** `request`，以及活跃期对象库中可 ack 的停机点对象（沿用 `DecisionBar.tsx:15-17` 的 `actionable` 判定）。每张请求卡 `key={request_id}`、停机点卡 `key={approval_id}`：答完一张，它的按钮节点随卡片卸载，不会被下一张卡复用（🟡-2 d）。滚动对话流不会把卡片滚走；答完或作废即收起，流内留痕行更新状态。后台期的卡片不出现在当前待答区，只在期列表显示「N 张卡待答」徽标（徽标不可点击答复，点它 = 切到该期）。

**各类卡片的按钮**（Spec 9 §3.2 定义选项，UI 不增不减）：

| kind | 按钮 | 附加 |
|---|---|---|
| `tool_call` | 批准 / 拒绝 | 「拒绝」旁可选文本框「告诉它怎么改」（仅 `feedback_allowed`）；空文本不带 `feedback` 键；`fields.tool === "browser"` 时批准须过第 5 层原生确认框 |
| `fetch` | 批准 / 拒绝 | 每张卡一个候选；**没有「全部批准」**（H-4）；批准须过第 5 层原生确认框 |
| `checkpoint` | 继续 / 停止 | **没有「以后不再询问」**（H-4） |
| `memory_ack` | 批准 / 拒绝 | 只由人点「确认记忆…」发起（§2.8） |
| 停机点对象 | 批准 / 打回… | Spec 8 §2.6 全部语义不变 |

卡片内容一律纯文本（H-3）：`title`、`card_text`（`pre-wrap`）、`fields` 键值表（值为对象或数组时 `JSON.stringify(v, null, 2)` 后按文本显示）。卡片上**不出现任何推荐、预判或默认高亮**（direction §5）。

**「显式点击」从 TG-4 扩展到全部人发起的动作**——五层，各有测试：

1. **静态（TG-4′、TG-10、TG-16、TG-17）**：下表每个方法名的字符串字面量只允许出现在指定文件；每处调用**最近的外层函数**必须就是一个 JSX 事件属性（`onClick`，`Composer.tsx` 另允许 `onKeyDown`）的值函数本身（不许经由在处理器里定义的具名函数、回调或 `useCallback` 间接调用）；该处理器函数体内**不得**出现任何循环语句（`for`/`for…in`/`for…of`/`while`/`do`）或迭代方法调用（`.map`/`.forEach`/`.reduce`/`.filter`/`.some`/`.every`/`.flatMap`/`.find`）；处理器的**第一个形参**命名的事件对象上的 `nativeEvent.isTrusted` 检查必须是函数体**第一条语句**（`if (!e.nativeEvent.isTrusted) return;` 形状）；该事件属性必须挂在**小写原生元素**（`button`、`textarea` 等）上，不许挂在自定义组件上（自定义组件的 `onClick` 可被任意代码以伪造事件对象调用，🟡-2 c）；**每个处理器里下表方法的调用合计至多一处**（防止把两次答复展开连写，二轮 🔵-2）。另（TG-16，🟡-2 b、二轮 🔵-3）：`renderer/` 中每一个名为 `call` 的属性访问表达式，都必须**直接**作为一次调用的被调用者（因此 `rpc.call.bind(…)`、`rpc.call.apply(…)`、`const c = rpc.call` 都不合规），且该调用的第一个实参必须是 renderer↔host 方法闭集中的字符串字面量；只豁免 `renderer/testHooks.ts`。

   | 方法 | 唯一允许的文件 | 事件属性 |
   |---|---|---|
   | `approval.decide`、`conv.answer` | `renderer/HumanCards.tsx` | `onClick` |
   | `conv.send`、`conv.interrupt` | `renderer/Composer.tsx` | `onClick`、`onKeyDown` |
   | `conv.command`、`conv.end`、`conv.resume` | `renderer/SessionHeader.tsx` | `onClick` |
   | `episode.create` | `renderer/NewEpisodeForm.tsx` | `onClick` |

2. **运行时（可信事件）**：上述处理器首句检查 `isTrusted`。E3 与红队实测（A1、A2）：Electron 44.4.5 下 `el.click()`、`dispatchEvent` 为 `false`，`webContents.sendInputEvent`、Playwright `locator.click()`、`keyboard.press` 为 `true`。它挡住页面内脚本合成的点击；**挡不住**被攻破的 renderer 直接发 RPC（见第 5 层与 RF-3）。
3. **host 绑定**：`conv.answer` 只接受该会话键当前**打开中**的 `request_id`（否则 `E_STALE`、零写入）；`decision ∈ request.options`；`feedback` 仅 `kind === "tool_call" && decision === "reject" && feedback_allowed` 时允许；同一 `request_id` 同时只允许一个在途答复（第二次 → `E_BUSY`）。它保证答复对得上一张真实的卡，**不**证明答复来自人。
4. **没有无点击路径**：打开期、切期、窗口聚焦、重连、收到帧、定时器，一律不产生 `conv.answer`/`approval.decide`/`conv.send`（TX-2 以 stdin 与 spawn 日志计数断言）；键盘答完一张卡后再按 Enter，不会答到下一张（TX-14）。
5. **原生确认框（用户裁决，🟡-1）**：对 browser 卡（`kind === "tool_call"` 且 `fields.tool === "browser"`）与抓取卡（`kind === "fetch"`）的 `decision === "approve"`，host 在第 3 层校验通过后、写 `answer` 之前，经 `confirm-query` 请 main 弹原生 `dialog.showMessageBox`：标题「批准 browser 调用？」/「批准抓取素材？」，正文取自**host 保存的 request 帧**（`fields` 的 `url`/`title`/`args` 等，纯文本，renderer 不参与拼装），按钮「批准」「取消」，默认与取消按钮都是「取消」（**2026-10-06 修订（D37），经用户裁决**：语义以实测为准——Return 不触发任何按钮、Esc = 取消、**只有鼠标点「批准」才放行**；选项由 `main/confirm.ts::approveBoxOptions` 生成，TH-22b 钉住）。main 回 `confirm-result{ok}`；`ok` 为假 → `conv.answer` 返回 `E_STALE`「已在确认框取消」、零写入，卡片仍打开。拒绝不弹框（拒绝不会造成副作用）。边界（二轮 🔵-4、🔵-5）：
   - **判定输入取自 host**：`needsNativeConfirm` 的输入是 host 按 `requestId` 从**自己保存的打开请求**里取出的帧，从不使用 renderer 传来的任何字段；
   - **不可见字符显式化**：正文由纯函数 `renderConfirmDetail`（§4.1）生成，把一切 Unicode 格式字符（`\p{Cf}`：双向覆盖 U+202A–202E、U+2066–2069，零宽 U+200B–200F、U+2060、U+FEFF，软连字符 U+00AD 等；E10 实测 `\p{Cf}` 覆盖以上全部）替换为可见的 `⟨U+XXXX⟩`，并在正文首行注明「含 N 个不可见字符，已显式标出」；
   - **按字段排版，不整段转储参数**（三轮 🔵-4）：正文先列**确定性字段、全文不截断**，再列**模型填写的自由文本、单独标注、超长截断**。browser 卡（参数只有 `action`、`url`、`reason`，`tools.py:538-541`）：先「操作：<`args.action`>」「目标：<`fields.target`>」（`target` 即 `args.url`，`cli.py:841-852`），再以一行「以下为模型填写的理由（未经核实）：」引出 `args.reason`；抓取卡：先「序号、标题、URL、类型、来源、预计时长」，再同样引出 `why`。自由文本超过 `CONFIRM_FREE_TEXT_MAX_CHARS` 时截断并标「…（已截断，共 N 字符）」。键序、填充和自由文本里伪造的「URL: …」都挤不掉、冒充不了前面的确定性字段；
   - **窗口模态**：`dialog.showMessageBox(win, …)` 挂在主窗口上（macOS 上 `signal` 只对挂了父窗口的确认框生效，本条与下一条须一起保留，三轮红队确认）；
   - **不串号**：`reqId` 为 `"<hostBootId>:<n>"`；main 为每个确认框传 `signal`（Electron 44 `MessageBoxOptions.signal`，`electron.d.ts:22586`），host 退出时 main 以该信号撤下已打开的确认框，并把排队中的全部作废——旧 host 的确认结果永远到不了新 host（新 host 也不认识旧 `bootId` 的 `reqId`）。原生对话框在 renderer 之外，被攻破的 renderer 伪造不了这一次点击。其余请求（写入卡、`run_pipeline` 卡、记忆卡、检查点卡）维持一次点击，残余影响范围见 RF-3。

**测试钩子只在未打包构建生效**：本 spec 新增的钩子（假钥匙串、假会话剧本、确认框桩、`sendInputEvent` 点击中继）全部挂在既有的 `!app.isPackaged` 守卫之下，TG-6 的钩子标识正则同步扩展（S8-R7）。

### 2.5 决策 5：自然语言发起新期——先 idea 聊，人点「建期」，core 建目录（设计问题 4）

**现状证据**：core 已有建期入口 `ava new <名>`（`cli.py:1559-1566`，非 TTY 下建完即返回 0）→ `create_new_episode`（`cli.py:430-451`）。实测（E1，scratchpad，未改仓库）与红队复核：

| 输入 | 现状结果 |
|---|---|
| `data/` 不存在 | 返回 0，**建出 `data/episodes/<名>/`**——违反 AGENTS.md 五「脚本绝不自动创建 `data/`」（`cli.py:438` 的 `mkdir(parents=True)`）；三个现有用例正依赖这一行为（🟡-5） |
| `data` 是悬空符号链接（外置盘未挂载） | 抛 `FileExistsError` 回溯，无可读提示 |
| `../../escape` | 返回 0，建在仓库根下（episodes 根之外）；`../../../escape` 与绝对路径建到仓库根之外（红队 🔵-5 复核） |
| `番/01`、`_x`、两个空格、`-x` | 返回 0，分别建出嵌套期、被期列表隐藏的期（`cli.py:332` 排除 `_` 前缀）、名为空白的期、以 `-` 开头的期 |
| 空模板 `01-topic.md` 建成后 | `status --json` 判 `current_step: "02 脚本写作"`、`completed_steps: ["01 选题"]`（E7；`status.py:197` 只看文件是否存在） |

**流程**（用户裁决）：

1. 左栏顶部「选题」入口 = idea 会话键。未选任何期时中栏即为 idea 视图；人在输入框打字，第一条消息懒启动 `SESSION_IDEA`（Spec 9 `--idea`：无期目录、不落盘。（**2026-10-08 修订（D43 / Spec 17 / ADR-0027），经用户裁决**：「零写权限」改为「无期目录，写期文件前须先建期」——所有模式开放全部工具，`write_episode_file` / `cover_edit` / `run_pipeline` / `acquire_propose` 四个需期工具在无期会话统一报「先建期」，不藏工具、不弹卡。））。（**2026-10-08 修订注记（D42 / Spec 18，人 2026-10-06 裁决「方案 (a) 转正继承、同一时刻只保留一个选题会话」）**：「不落盘」已废——`SESSION_IDEA` 仍无期目录，但落盘到库级 `data/_idea/`、启动即恒恢复最近段，app 重启后点「选题」接着上次聊；`conv.resume` 对 idea 也以 `SESSION_IDEA` 起。）
2. idea 视图的会话头部常驻「建期…」按钮；左栏「＋ 新建一期」= 切到 idea 视图并展开同一个表单。表单只有一个输入「期名」，**初始为空、不从对话内容预填**（模型在对话里提议的名字只是文字，人自己决定抄不抄；direction §5）。
3. 人点「建期」→ `episode.create{name}` → host spawn `NEW_EPISODE`：`[py, "-m", "pipeline.agent.cli", "new", name]`（**2026-10-08 修订注记（D42 / Spec 18，人 2026-10-06 裁决「方案 (a) 转正继承、同一时刻只保留一个选题会话」）**：argv 恒加 `--from-idea`；选题会话进程活着则 host 先按 §2.10 结束序列结束它，结束不了 → 不建期、`E_BUSY`）。**校验全部在 core**（C10-R1，§3.7）；host 只做 exact-keys 与「是字符串」检查，名字作为单个 argv 元素传入（`shell: false`）。失败 → `E_CORE`，UI 原样显示 core 的 stderr 尾部。
4. 成功 → host 刷新期列表，返回 `{ epKey: name }`；renderer `episode.activate` 该期（触发 H1）、切到该期视图、焦点落在输入框（人刚点过按钮，移焦是对人操作的直接响应，不属于 §2.7 的「抢焦点」）。对话区空白，首行一条本地提示「选题会话的讨论不会带入本期；需要的要点请在这里重述」（纯文本，不是消息）：带入需要 host 代发一条人没打过的 `user_message`，违反 H-8。（**2026-10-08 修订注记（D42 / Spec 18，人 2026-10-06 裁决「方案 (a) 转正继承、同一时刻只保留一个选题会话」）**：带入改由 core 完成——`--from-idea` 把选题记录整段复制进新期，不是代发消息，H-8 不碰。`episode.create` 返回 `{ epKey, migrated, sid, messages }`（解析 core 的单行 marker，缺失/不合式按 `migrated:false` + host diag）。`idea-note` 两态：`migrated` 时为「已带入选题会话记录（N 条消息）」，否则旧文案不变。该期首次 `conv.send` 由 host 一次性标记改走 `SESSION_CONTINUE`（接着迁入段聊；Spec 18 §3.3 ② 原写「renderer 先 resume 后 send」，与 TG-10 冲突，人 2026-10-08 裁决改为 host 侧标记，renderer 仍只发一次 `conv.send`）。）**新期停在 01**（C10-R3，§3.8：模板里「类型」为空即 01 未完成）；scope 为 creative（`resolver.py:23`），`01-topic.md` 在写白名单（`tools.py:26-29`），01 工序的规程被注入，agent 据人的描述推断类型与模式，经写入卡写 `01-topic.md`，人批准后 status 才前进到 02。
5. idea 会话保留在后台（用户裁决），可切回继续聊或再建一期。（**2026-10-08 修订注记（D42 / Spec 18，人 2026-10-06 裁决「方案 (a) 转正继承、同一时刻只保留一个选题会话」）**：本条已被取代——「建期即转正并结束；同一时刻只保留一个选题会话」。带入成功后 host 清掉选题会话的条目缓冲，选题视图显示「已带入 <期名>」本地提示；再点「选题」、发消息即懒启动全新选题会话（`_idea` 已清空）。未带入（选题没聊过）时缓冲保留，与改造前一致。）

**UI 绝不 mkdir**：host 仍受 TG-2（fs 写类 API 只在 `host/settings.ts`）约束；建期只有 `NEW_EPISODE` 一条路；TH-14 断言 core 拒绝时磁盘上什么都没多出来。

**与 direction 的出入**：§0.1 第 2 步写「Agent 自主推断题材模式、建立期目录」。按用户裁决，建目录由人点按钮、core 执行，agent 负责推断与填写 `01-topic.md`。若要 agent 自己建期，须新增工具（工具表第 13 个，碰红线 6，需立 ADR 并与 Spec 12 的 ADR-0025 协调）。提 DIR-R2 修订 direction 措辞。

### 2.6 决策 6：多期、多会话的切换与隔离（设计问题 5）

沿用 Spec 8 §2.5 的 epKey 分桶，扩到会话键：

- **身份**：renderer 只持有 `ConvKey`；`ep:<epKey>` 的 epKey 必须在 host 的期映射里（否则 `E_BAD_REQUEST`），路径只存在于 host。
- **消息级隔离**：每条 `conv.*` push 带 `convKey`、`generation`（`"<hostBootId>:<n>"`：`hostBootId` 为 host 进程启动时生成的随机串，`n` 为该 host 内每起一个会话进程 +1）、`seq`；renderer 状态 `Map<ConvKey, ConvState>`，组件只读 `convs[activeConvKey]`；不属于已知会话键的 push 丢弃；`generation` 不同的 delta 丢弃；`seq` 跳号 → 请求 `conv.snapshot`（与 Spec 8 TE-10 同一规则）。
- **host 重启（🟡-9）**：renderer 在 `onConnect`（`App.tsx:86-93` 所在处）**清空全部会话桶**，再按 `episodes.summary` 的 `conv` 与 `idea` 摘要为新 host 认识的活会话取 snapshot；此前桶里有、但新 host 不认识的会话键显示一行「会话已结束（host 重启）」与「继续上次会话」。旧 host 下未答的卡随桶清空消失，不会以可点的样子留在界面上；`autoOpen` 状态同时按 §2.7 R6 重置（新 host 的时间与回合登记与旧的无关，二轮 🟡-2 第 3 点）。
- **待答区隔离**：待答区只取活跃会话键的未关闭请求与活跃期的对象库；后台期的请求永不进入当前待答区（TV-4、TX-6）。
- **媒体隔离**：不变（切期卸载全部媒体元素，Spec 8 TP-3）。
- **终端与桌面端同期**：期租约是 Spec 9 的进程级 flock。桌面端懒启动，因此「看」不占锁；桌面端有活会话时终端对同期的 agent 回合被 Spec 9 I-10 拒绝，反之桌面端 `conv.send` 得 `E_SESSION_LOCKED`，文案「该期已有活跃会话（可能是终端里的 ava），先在那边退出」。
- **后台徽标**：`EpisodeSummary.conv = { live, running, openRequests }` 由 host 从帧折叠得出（`shared/convFold.ts` 同一份逻辑），随 `episodes.summary` 下发；停机标记仍只来自 `status --json`（Spec 8 §2.12）。

### 2.7 决策 7：停机点自动呼出与对话流的焦点规则（设计问题 6，收口 Spec 8 RF-24）

**现状证据**：`App.tsx:149-166` 在活跃期出现任何新的 pending `approval_id` 时无条件 `setPreview`，人正在看的文件、正在播放的音频被替换。RF-24：agent 在一个回合里连改几版 `04-clips.json`，H5 每版各建一个 pending，预览区跟着各闪一次半成品。v0.1 的「回合结束即呼出最新」在真实通路上不确定（一轮 🟡-7）：`turn_finished` 经 stdout 即时到达，对象库却要等活跃期 1 s 一次的读取（`service.ts:783-786`）才能看到回合末尾 `ensure_pending` 的结果。v0.2 把判定放在 renderer、靠下发 `readAt` 比较，二轮 🟡-2 指出三处断点：`stop_points` 不保证每回合都发（会卡死）；`readAt` 若参与 `approvals` 的变化比较（`service.ts:812-813`）会每秒推一次 delta、若不参与则常态下永远收不到；host 重启后时钟不连续。v0.3 改为**结算由 host 判定**。

**host 侧回合结算**（`host/sessions.ts` + `service.ts`，TH-20 驱动）：

1. host 同时持有两类时间戳，都用**本 host 进程**的单调时钟：会话帧的到达时刻；对活跃期对象库每一次读取的**开始**时刻（`readStart`，在打开文件之前取；若取读完时刻，一次在 `stop_points` 之前就开始的读取会被误判为「之后读的」）。时间戳不出 host。
2. 某会话键收到 `turn_finished{turn_id}` → 登记待结算 `{turnId, stage: "await-stop-points"}`；收到 **`turn_id` 与槽位相同**的 `stop_points`（S9-R4 保证每回合恰好一帧、带本回合 `turn_id`，`items` 可空，回滚、出错、降级的回合也发）→ `stage = "await-read", after = 该帧到达时刻`。两帧之间可以夹着任意其他帧（例如 `ensure_pending` 新建停机点时打印的 `[approvals] … 待审批已更新`，`cli.py:131`，协议下成为 `log` 帧；`ensure_pending` 失败时的 `notice`），host 不要求相邻（三轮 🟡-1）。
   **登记边界**（三轮 🔵-2；四轮 🔵-1 改为按 `turn_id` 精确匹配）：每个会话键只有一个待结算槽位；新的 `turn_finished` 覆盖尚未结算的旧登记（旧回合此后不会再产生 `settled`，renderer 侧按 R2b 的 `turnId` 比对忽略任何迟到的旧条目）；`turn_id` 与槽位不符的 `stop_points`（迟到的旧回合帧、槽位已因超时结算后才到的帧）与 `turn_id` 为 `null` 的 `stop_points`（`ready` 时发的那帧）一律不推进结算。不按「`turn_finished` 之后的第一帧」认领：旧回合的迟到帧若落在新回合 `turn_finished` 之后，会被当成新回合的帧，让新回合在自己的 `ensure_pending` 写盘之前结算（v0.1 的缺陷）。
3. 该期是活跃期时，第一次 `readStart > after` 的对象库读取完成后：host **先**推送这次读取带来的 `episode.delta`（若对象库有变化），**再**向该会话追加条目 `settled{turnId, timedOut: false}`。MessagePort 保序，renderer 处理 `settled` 时对象库已是结算后的版本。
4. 该期不是活跃期时，立即追加 `settled{turnId, timedOut: false}`（后台期不自动呼出，§2.5）。该期是活跃期、但 host 此时不读对象库——approvals 能力缺席（`!capApprovals`，Spec 8 TA-6）或 `reach !== "ok"`——同样在进入 `await-read` 时立即结算（四轮 🔵-2）：这两种状态下没有自动呼出可做，等读取只会让每个回合都走第 6 条的超时。
5. 会话进程退出（没有 `stop_points` 可等）→ `after = 退出时刻`，按第 3 条结算。
6. 兜底：登记后 `SETTLE_TIMEOUT_MS` 内未结算 → 追加 `settled{turnId, timedOut: true}` 并写诊断「回合结算超时（stage=…）」。无论哪一步缺帧，renderer 都不会无限等待。**兜底只为 core 缺帧而设**：正常通路（含回合里新建了停机点、两帧之间夹着 `log`/`notice`）必须以 `timedOut: false` 结算，测试逐项断言这一点（TH-20、TX-5 ⑤），否则超时路径会把时序缺陷静默掩盖成「只是慢了 10 s」。
7. `approvals` 的快照与 delta 结构**不变**（撤销 v0.2 的 `readAt`），`approvalsKey` 的变化比较照旧。

**renderer 侧规则**（纯函数 `renderer/autoOpen.ts`，零 DOM，TV-6 逐条驱动）：

状态：`owner ∈ {none, auto, human}`（当前预览内容的来源）、`seen: Set<approvalId>`、`deferred: Map<停机点类型, 对象>`、`awaiting: turnId | null`（已见 `turn_started`、尚未见对应 `settled`）、`strip: 对象 | null`。「最新」一律用 `compareIso`（§4.1）比较 `created_at`，相等取数组中靠后者。

| # | 输入事件 | 动作 |
|---|---|---|
| R1 | 活跃期对象库出现未见过的 pending 对象 | 记入 `seen`。若 `awaiting` 非空：按停机点类型记入 `deferred`（同类型保留最新者）；否则取本批最新者为候选，走 R3 |
| R2a | 活跃期会话的 `turn_started{turnId}` | `awaiting = turnId` |
| R2b | 同一会话的 `settled{turnId}`（`turnId === awaiting`） | 以**当前**对象库中仍为 pending、且在 `deferred` 里的对象取最新者为候选，走 R3；清空 `deferred`、`awaiting = null`。`deferred` 为空（本回合零停机点，常态）则只清状态，此后新出现的 pending 照 R1 立即处理 |
| R3 | 应用候选 | `owner === "human"` **或** 有媒体元素在播放（`!paused && !ended`）→ 不替换，`strip = 候选`（预览区顶部一条「停机点 03.5 已就绪 · 查看」）；否则替换预览，`owner = "auto"`，`strip = null` |
| R4 | 人在产物树或画廊点开文件 | `owner = "human"` |
| R5 | 人点「已就绪 · 查看」条 | 打开该停机点的预览，`owner = "auto"`，`strip = null` |
| R6 | 切换活跃期，或 `onConnect`（host 重连或 renderer 重载，二轮 🟡-2 第 3 点） | 预览清空（现状 `App.tsx:104`），`owner = "none"`、`deferred`/`strip` 清空；`seen` 保留。`awaiting` **不是简单清空，而是从新取回的会话 snapshot 的顶层字段重建**（三轮 🔵-1；四轮 🔵-3 改为不依赖条目缓冲）：`phase === "running"` → `awaiting = snapshot.turnId`（回合仍在跑）；否则 `settlePending` 非空 → `awaiting = settlePending`（已结束但未结算）；否则 `awaiting = null`。不扫描 `entries`：长回合的日志可能让缓冲超过 `CONV_BUFFER_MAX_BYTES` 被截头，`turn_started` 条目随之丢失。host 重启时新 host 的 snapshot 两个字段都为空，`awaiting` 自然为 null；renderer 重载时 host 未变，回合中的中间版本仍被延迟 |

- **键盘焦点永不移动**：自动呼出与「已就绪」条都不调用 `focus()`，预览组件不设 `autoFocus`；TX-5 ④ 断言每次呼出与出现「已就绪」条的前后 `document.activeElement` 不变。
- **决策卡不受延迟影响**：R1 只延迟**预览**；卡片按对象库照常进入待答区。
- **RF-24 的残余**：H5 仍会为中间版本各建一个 pending（Spec 3 自愈的正常行为，本 spec 不改），但回合进行中和结算前它们不再闪进预览；每个回合至多呼出一次。回合外（例如人在终端改文件）的多次写入仍可能多次呼出，但只会替换 `owner === "auto"` 的内容。结算超时（第 6 条）时按当时的对象库呼出，可能是中间版本；诊断里可见，属已知上限。

### 2.8 决策 8：09 标题的多轮讨论在面板里怎么承载（设计问题 7）

- **承载**：09 的标题讨论就是该期主会话里的普通对话。当期处于 08/09 时 scope 为 pipeline（`resolver.py:23`），该 scope 的工具表含 `read_artifact`（`config/agent/tools.json`），模型能读 `07-titles.md`、在对话里提候选、听人的意见改；Spec 10 **不新增**任何标题组件、候选列表或选中状态。
- **定稿不在对话里**：09 的决策卡仍是 Spec 8 的纯记录型 `APPROVE 09 --id`（ADR-0018「封面标题只出候选」不变）。「09 的 ack 记录人最终选定的封面与标题」属于 Spec 12，须由 Spec 12 问人（direction §6 Spec 12）。
- **给 Spec 12 留的接口**：`StopPointCard` 按停机点类型渲染一个 `body` 插槽，09 的插槽在本 spec 里为空；Spec 12 可在其中加选择控件，但须遵守 §2.4 第 1 层（点击才答、`isTrusted`、无循环、原生元素）。图片拖入、导入期目录、确定性图片编辑工具都在 Spec 11/12，本 spec 不做。
- **scope 与记忆两个命令**（用户裁决）：会话头部显示当前 scope（来自 `ready.scope` 与最近一次成功的 `command_result`）。「素材模式」开关发 `command{name:"scope", arg:"asset" | "auto"}`，回合运行中禁用，idea 会话不显示。收到 `notice{code:"memory_unconfirmed"}` 后头部出现「确认记忆…」，点击发 `command{name:"memory_ack"}`，core 随后发 `kind:"memory_ack"` 的请求卡进待答区，由人批或拒（Spec 7 首次写入人确认、Spec 9 S7-R1）。

### 2.9 决策 9：LLM 密钥来源——钥匙串（S8-R1②、H-7；用户裁决）

**现状证据**：`load_llm_config` 只从 `config/agent.local.json`（优先）或 `config/agent.json` 的 `api_key_env` 指名的环境变量读密钥（`llm.py:118-160`，`138`、`141`），缺失即降级。Finder 启动的 app 拿不到 shell 环境，Spec 8 的环境白名单也明确排除 `*_API_KEY`。

**流程**（每次 spawn `SESSION_*` 前执行一次）：

1. `PROBE_KEY_ENV` 问 core：当前配置指名的环境变量叫什么（C10-R2 新增纯读函数 `api_key_env_name`，与 `load_llm_config` 共用同一段「local 优先」选择逻辑，`base_url`/`model`/`api_key_env` 缺一即返回 `None`；host 不自己读 `config/`，避免 TS 侧出现第二份规则）。
2. 名字须匹配 `KEY_ENV_NAME_RE = /^[A-Z][A-Z0-9_]*_(API_KEY|KEY|TOKEN)$/`（排除 `PATH`、`PYTHON*`、`DYLD_*`、`NODE_OPTIONS` 等会改变子进程行为的名字）；不匹配 → 不注入，诊断记「配置指名的变量名不合规」。
3. `KEYCHAIN_READ`：`/usr/bin/security find-generic-password -s ava -a <名字> -w`。退出 44 = 未找到（E2 实测）。
4. 值的处理（E5 实测）：去掉**恰好一个**末尾 `\n`；须匹配 `/^[\x21-\x7e]{1,4096}$/`（可打印 ASCII、无空白），否则不注入。**已知盲区**：E5 实测条目含非 ASCII 时 `-w` 输出的是该值的十六进制串，它本身就是可打印 ASCII，会通过校验、被当成密钥注入，直到服务商拒绝认证才暴露。合法密钥也可能形如十六进制，host 无法区分，因此不做解码、不做猜测；降级说明与 `desktop/` 文档写明「钥匙串里存的必须是密钥原文（纯 ASCII）」，认证失败时 core 以 `LLMError` 如实报出（RF-15）。
5. 通过 → 只写进这一次 `SESSION_*` spawn 的 `env[名字]`。**密钥从不**进 argv、spawn 日志、`AVA_SPAWN` 行、诊断、任何发往 renderer 的消息、host 的 stdout/stderr（TH-6 以标记串全链路断言）。`KEYCHAIN_READ` 的结果只在 `host/secrets.ts` 内被读取（TG-12）。
6. 任一步失败 → 照常 spawn（不带密钥），Spec 9 以 `ready{llm:"degraded", degrade_reason}` 如实降级；会话头部显示「LLM 未就绪：<原因>」与一行可复制的命令 `security add-generic-password -s ava -a <名字> -w`（名字取自第 1 步；第 1 步失败时显示「未能读取 config/agent*.json 的 api_key_env」）。不弹任何输入密钥的界面（renderer 永不接触密钥）。
7. 钥匙串授权框（A4）属于系统行为，不在 UI 控制之内；每次重建 app 后是否需要重新授权，同 Spec 8 假设 4 的实测口径记录。

> **修订记录（2026-10-06，D29；Spec 15 `2026-09-29-web-search-provider-spec.md` §2.7，人 2026-10-06 裁决「桌面端也要能用备用检索服务」，不接受「备家只在终端可用」）**：「每次 spawn 只注入**一个**密钥」改为「**LLM 密钥 + web 检索链上声明的密钥**（至多 1 + 4 个）」。新增短命令模板 `PROBE_WEB_KEY_ENVS`（core 纯读函数 `web_key_env_names`：与 `load_web_config` 共用「local 优先」选择，只回答通过 Spec 15 §2.4 名字校验的名字——族前缀、`KEY_ENV_NAME_RE` 同形、不与 LLM 同名；stdout 每行一个名字）。host 在 LLM 密钥之后对每个名字照第 2～4 步读钥匙串，通过的写进同一次 `SESSION_*` spawn 的 `env[名字]`。**web 密钥任何失败都不影响 LLM 密钥的注入与会话头的降级文案**：解析整体一个 try，探针退出码非 0 / 超时 / 输出含不合规行 / 调用抛异常 → 整体不注入 web 密钥；单个名字钥匙串缺条目或值不合法 → 只是不注入该名字（core 侧该检索服务「未就绪」并在错误消息里写明两处放法）；重复只读一次、与 LLM 同名跳过、超过 4 个只取前 4 个。原因只进诊断（名字与退出码，从不含值）。第 5 条的密钥纪律对 web 密钥逐字适用（TH-W1 以两个标记串全链路断言）；`KEYCHAIN_READ` 仍只在 `host/secrets.ts` 内读取（TG-12）。TH-6「恰多一个键」在没有 web 链的夹具上照旧成立，有 web 链时的口径由 TH-W1～W4 守。放过的面：会话进程多出至多 4 个密钥变量，随 `jobs.py` 继承给作业子孙（N50 扩面，人裁决接受）。

> **2026-10-06 修订记录（D37 / D40，经用户裁决方案 (b)）**——第 5 层与 §2.10 的确认框。
> - **原因**：D37-A 人手定性（打包版 HEAD `239f51f`，Electron 44.4.5，macOS Darwin 27.0，全部点击与按键由人完成）：两处确认框里按 Return **都不触发任何按钮**（`defaultId: 1` 未把 Return 绑到「取消」），Esc = 取消，鼠标点「取消」= 取消，只有鼠标点「批准」/「退出」才放行；等待 30 s、切到其他应用再切回均零写入。原文「默认按钮是取消」与实测不符，但方向安全，用户裁决不改按键绑定、改措辞。证据见 issues D37 行。
> - **改动**：① 按钮、`defaultId`、`cancelId` 不变，抽到 `main/confirm.ts`（`approveBoxOptions` / `quitBoxOptions` / `APPROVE_BUTTON`），TH-22b 钉住「放行按钮永不在默认位或取消位」并静态核对 `main/index.ts` 两处只经这两个函数取选项；② **非人手路径一律 `ok:false`**：对话框抛异常此前只撤状态不回复（host 的 `await confirm` 会永久挂起），改为回 `ok:false`；③ **D40**：主窗口 `closed`（含 `destroy()`，挂在其上的对话框永不 resolve）时 broker 新增 `windowGone()`：abort 已打开的一个，并对它与排队中的全部各回一次 `ok:false`，卡片回到打开、可在新窗口重答。选 broker 补回包而不是给 host 的 `await confirm` 加超时：人看确认框可以很久（D37-A 实测一次 766 s），超时会误伤正常审阅。用例 TH-18b（host 端到端：挂起期间重答 `E_BUSY` → `windowGone` → `E_STALE`、零写入、卡仍打开 → 重答恰 1 行 `answer`）、TH-22c（show 回 false / 抛异常 / windowGone / windowGone 后重答 / 空闲 windowGone）。
> - **残余风险**：2026-09-27 两次「未经人手点击被记批准」发生在有自动化介入时（AX dump、System Events 按键），无自动化下未复现；复现需重新引入自动化，违反施工纪律第 5 条，不再追。

### 2.10 决策 10：结束会话与退出 app（H-5、H-6；用户裁决）

- **会话进程退出时（任何原因）**：host 立即把该会话全部未关闭请求移出打开集合、记一条 `voided_local`（原因 `session_exited`），推送 delta；在途的 `conv.answer`/`conv.send` 以 `E_SESSION` 结束。此后对这些 `request_id` 的答复在打开集合检查处即得 `E_STALE`、零写入（H-5；与 §4.2 冻结顺序一致，🟡-12）。
- **进程组清理**：会话 pid 以任何方式退出后，若其进程组仍有成员（`process.kill(-pid, 0)` 成功），host 对整组发一次 SIGKILL，清掉残留的 browser driver 与浏览器；job 子进程在各自的新会话里（Spec 2 `start_new_session`），不受影响。
- **「结束会话」（`conv.end`）**：写 `shutdown` → 最长等 `SESSION_END_WAIT_MS`（90 s：Spec 9 收尾最长一次模型请求 60 s + job 杀组 + 余量），界面显示「收尾中…」→ 仍未退出则 `SIGTERM` **只发给会话 pid**（让 Spec 9 的处理器跑）→ 再等 `SESSION_KILL_GRACE_MS`（5 s）→ `SIGKILL` 整个进程组。SIGTERM 不打整组：同组里的 browser driver 会在会话处理器之前被杀。
- **退出 app**（用户裁决两次；🟡-3、🟡-4）。main 维护一个退出状态机 `quitPhase ∈ {idle, querying, confirming, stopping}`，`before-quit` 只在 `idle` 时处理，其余阶段 `preventDefault` 后忽略（防连按 Cmd+Q 重入；`confirming` 时把焦点还给已开的确认框）：
  1. `before-quit`：若 host 不存在、未就绪（`hostReady === false`）或已熔断（`fatal !== null`）→ **直接退出**（此时若有会话进程，它们的 stdin 随 host 消失而 EOF，按 Spec 9 自行收尾退出，A3 已实测）。
  2. 否则 `preventDefault`，`quitPhase = querying`，向 host 发 `quit-query`；`QUIT_QUERY_TIMEOUT_MS`（2 s）内没有 `quit-state` → 直接退出（同上）。
  3. `quit-state.busy` 为空 → 进入第 5 步。
  4. 否则 `quitPhase = confirming`，弹原生 `dialog.showMessageBox`（列出「期名 · 运行中 / N 张卡未答」，并写明「正在运行的渲染等作业会被中断；回合不会再做收尾总结」；按钮「退出」「取消」，默认与取消按钮都是「取消」；**2026-10-06 修订（D37）**：实测 Return 不触发任何按钮、Esc = 取消、只有鼠标点「退出」才退出；选项由 `main/confirm.ts::quitBoxOptions` 生成）。取消 → `quitPhase = idle`，**不向 host 发任何消息**，host 照常轮询。
  5. 退出：`quitPhase = stopping`，main 发 `quit-proceed`。host 自收到起对一切 `conv.*` 与 `episode.create` 返回 `E_BUSY`「正在退出」（否则空闲会话收到 `shutdown` 的同时可能刚进来一条 `user_message`，二轮 🔵-6）。随后对每个活会话：**空闲**（无回合在跑、无未答卡）→ 写 `shutdown` 帧；**忙**（回合在跑或有未答卡）→ 直接 `SIGTERM` 会话 pid（Spec 9 §2.2 第 5 条：中断 + 跳过收尾 + `turn_end{wrapup:"skipped"}`；S9-R3）。唯一例外：人先点了「停止」、会话**已在收尾中**时退出，SIGTERM 按 Spec 9 §2.2 状态表放弃收尾、记 `aborted`——这是人两次明确的中断，如实记录，不再为它等待。随后每个会话最长等 `SESSION_KILL_GRACE_MS`（5 s），未退出 → `SIGKILL` 整组；全部结束后进程组清理，回 `sessions-down`；main 此时才发生命周期 `shutdown`（停定时器），然后 `app.exit(0)`。main 侧对第 5 步另设 `QUIT_STOP_TIMEOUT_MS`（10 s）兜底，超时直接退出。
- **`window-all-closed`**：只调 `app.quit()`，不再先发 `shutdown`（现状 `main/index.ts:303-307` 先发 `shutdown`，取消退出后 host 已停止轮询，🟡-3）；退出统一走上面的状态机。关窗后取消退出时，窗口已不存在而 app 与 host 继续运行（host 的订阅在关窗时不清，`renderer-reset` 只在页面载入完成时发，`main/index.ts:264-273`），`activate` 重开窗口后照常恢复（TX-8d）。
- **兜底路径的收尾语义如实说明**：第 1、2 步的「直接退出」不经过第 5 步，会话读到 stdin EOF，按 Spec 9 §2.8 走**完整收尾**（最长一次模型请求 60 s）后自行退出——与第 5 步「不收尾」不同，见 RF-17。
- **e2e 约定**：凡起过会话的用例，在收尾调 `app.close()` 之前必须装好「确认框默认返回『退出』」的桩（未打包构建的测试钩子），否则真实确认框会让用例挂住。

### 2.11 决策 11：范围闸门

**做**：§2.1–§2.10；Spec 9 H-1~H-10 全部承接（§6.2）。

**不做**：
- 逐字流式（用户裁决；需要时另提 Spec 9 修订，并保证未完成片段不经 `commit()`，Spec 9 §2.8）；
- 子会话（`/chat`、`/script`、`/memory digest`）的桌面端入口：Spec 9 协议没有这些命令，v1 只有主会话与 idea；
- 在界面上浏览、选择任意历史会话：v1 只有「继续上次会话」（Spec 9 `--continue` 的默认选择规则）；
- 助手回复的 Markdown 渲染（H-3）；
- `/voice` 按钮化、02.5 编辑器、人时采集（Spec 11）；封面导入、图片编辑、09 定稿载荷（Spec 12）；
- 会话记录的人时记账：对话与卡片停留**不计人时**，门禁 14 的横幅照旧（Spec 8）；
- 对「类型」取值是否属于题材表、杂谈是否带齐模式与张力的机器把关：会让 5 个真实期倒退（E7），不在本 spec（RF-16）；
- 任何 LLM 推荐、预判、卡片默认高亮（direction §5）。

---

## 3. 数据契约

### 3.1 renderer ↔ host（S8-R2，并入 `shared/protocol.ts`）

```ts
export type ConvKey = "idea" | `ep:${string}`;

// 新增方法（exact-keys；除 feedback 外全部参数为字符串；无任何路径字段）
| "conv.send"        // { convKey, text } → { turnId }：无活进程则先起 new 会话、等 ready；写 user_message，
                     //   以同 rid 的 turn_started 解析、以同 rid 的 error 拒绝（S9-R2）；写入前预检整帧 ≤ 1 048 576 字节
                     //   （2026-10-08 D42 注：带入了选题记录的期，首次无活进程时以 continue 模式起，见 §2.1 第 2 条注记）
| "conv.resume"      // { convKey } → ConvSnapshot：仅 ep:*；以 --continue 起会话；已有活进程 → E_BUSY
                     //   （2026-10-08 D42 注：idea 也可，以 --idea 起、core 恒恢复；ep:* 成功后消费该期的带入标记）
| "conv.interrupt"   // { convKey, turnId }：turnId 须等于 host 记录的当前回合，否则 E_STALE 且零写入
| "conv.answer"      // { convKey, requestId, decision, feedback? } → { decision }：§2.4 第 3、5 层；§4.2 冻结顺序
| "conv.command"     // { convKey, name, arg? }：name ∈ {memory_ack, scope}；scope 时 arg ∈ {asset, auto}，否则不许带 arg
| "conv.end"         // { convKey } → { code, signal }：§2.10 结束序列
| "conv.snapshot"    // { convKey } → ConvSnapshot
| "episode.create"   // { name } → { epKey }：spawn NEW_EPISODE；校验在 core
                     //   （2026-10-08 D42 注：返回 { epKey, migrated, sid, messages }；argv 恒带 --from-idea；
                     //    选题会话活着先按 §2.10 结束、结束不了 E_BUSY 不建期；marker 缺失按未带入 + diag）

export type PushTopic = /* 现有 5 个 */ | "conv.snapshot" | "conv.delta";
export type ErrCode = /* 现有 9 个 */ | "E_SESSION" | "E_SESSION_LOCKED";

export type ConvEntry =                                     // host 缓冲的单位，只追加
  | { k: "frame"; at: number; frame: OutFrame }              // 已校验的 Spec 9 出站帧（§3.2）；at = host 到达时刻
  | { k: "user"; at: number; text: string }                  // 被 turn_started 确认的人的原文
  | { k: "answered_local"; at: number; requestId: string; decision: string; feedback: string | null }   // 🔵-2：留痕文案的来源
  | { k: "settled"; at: number; turnId: string; timedOut: boolean }   // §2.7 host 侧回合结算（二轮 🟡-2）
  | { k: "spawned"; at: number; mode: "new" | "continue" | "idea"; pid: number }
  | { k: "exited"; at: number; code: number | null; signal: string | null; stderrTail: string }
  | { k: "voided_local"; at: number; requestIds: string[]; cause: "session_exited" }
  | { k: "frames_lost"; at: number; count: number; reason: "oversize" | "malformed" };

export interface ConvSnapshot {
  convKey: ConvKey;
  generation: string;                 // "<hostBootId>:<n>"（🟡-9）
  seq: number;                        // 本 generation 已下发的最后一个 delta 序号
  phase: "none" | "starting" | "idle" | "running" | "ending" | "exited";
  turnId: string | null;
  entries: ConvEntry[];
  truncatedHead: boolean;             // 缓冲超 CONV_BUFFER_MAX_BYTES 已截头
  open: OutFrame[];                   // 未关闭的 request 帧（单独保存，截头不影响待答区）
  framesLost: number;
  settlePending: string | null;       // host 待结算槽位里的 turnId（§2.7 第 2 条），无则 null；单独保存，截头不影响（四轮 🔵-3）
}
export interface ConvDelta { convKey: ConvKey; generation: string; seq: number; entries: ConvEntry[]; phase: ConvSnapshot["phase"]; turnId: string | null; open: OutFrame[] }

// 现有结构的增量（S8-R8）
interface EpisodeSummary { /* 现有字段 */ conv: { live: boolean; running: boolean; openRequests: number } | null }
interface EpisodesList  { /* 现有字段 */ idea: { live: boolean; running: boolean; openRequests: number } | null }
```

- **`feedback`（🔵-1）**：可选键；出现时 host 自行校验「是字符串、trim 后非空、UTF-8 ≤ `FEEDBACK_MAX_BYTES`」，否则 `E_BAD_REQUEST`（`checkParamKeys` 对名为 `feedback` 的键不做类型检查，`protocol.ts:80`，不能依赖它）；缺席时帧内写 `null`。
- **整帧预检**：`conv.send`、`conv.answer`、`conv.command` 在写 stdin 前以 `stringifyLossless` 序列化并检查 UTF-8 长度 ≤ 1 048 576（Spec 9 `MAX_INBOUND_LINE_BYTES`），超限 `E_BAD_REQUEST`、零写入——永不触发 core 那条不带 `rid` 的 `E_TOO_LARGE`。
- Spec 9 错误码到 host 错误码的映射（冻结）：`E_BUSY`→`E_BUSY`；`E_STALE`、`E_UNKNOWN_REQUEST`、`E_REQUEST_CLOSED`→`E_STALE`；`E_BAD_REQUEST`→`E_BAD_REQUEST`；`E_SESSION_LOCKED`→`E_SESSION_LOCKED`；其余（`E_NOT_READY`、`E_SESSION_BROKEN`、`E_NO_EPISODE`、`E_TOO_LARGE` 及未知）→`E_SESSION`，`message` 以「<原码>：<原文>」开头。不带 `rid` 的 `error` 帧不归给任何在途请求，只进对话流一行（MUT-37）。
- 会话进程在 `ready` 之前退出时，按退出码映射（Spec 9 §3.6），**不依赖**是否先收到 `error` 帧：3 → `E_SESSION_LOCKED`；4 → `E_UNREACHABLE`；其余 → `E_SESSION`（TH-11b，🔵-10）。
- `conv.delta` 合并：同一会话键在 `CONV_PUSH_COALESCE_MS` 内到达的条目合并为一条 delta（A8）。
- **切换仓库期间**（🟡-11）：`choosing` 或 `switching` 为真时，`conv.send`、`conv.resume`、`episode.create` 返回 `E_BUSY`「正在切换仓库」、零 spawn、零写入。
- **退出期间**（二轮 🔵-6）：收到 `quit-proceed` 起，全部 `conv.*` 与 `episode.create` 返回 `E_BUSY`「正在退出」。

### 3.2 host ↔ 会话进程（消费 Spec 9 §3.1 v1，加 S9-R1/R2/R4）

**帧校验（`shared/convFrames.ts` + `host/sessions.ts` 的 `boundedLines`，冻结）**：
1. stdout 经 `LineSplitter(SESSION_FRAME_MAX_BYTES)` 切行；残段超限时分行器已丢弃到下一个换行，记 `oversize`；**每个完整行**再以 UTF-8 字节数检查，超过 `SESSION_FRAME_MAX_BYTES` 同样丢弃并记 `oversize`（🟡-8：`shared/jsonl.ts:13-41` 只在残段分支比较上限，同一块内遇到换行的整行不查长度；这里不改 Spec 8 tail 共用的分行器）；
2. 一行一个 JSON 对象；`JSON.parse` 失败 → `frames_lost{reason:"malformed"}`；
3. `v === 1`，`t` ∈ 出站闭集 `{ready, history, turn_started, assistant, tool, request, request_closed, command_result, stop_points, turn_finished, log, notice, error, bye}`；否则 malformed；
4. 按 `t` 检查必需键存在且为预期的原始类型（字符串 / 数字 / 布尔 / 数组 / 对象）；多出的键忽略（向前兼容）；
5. 任何数字若为整数且 `!Number.isSafeInteger` → malformed（Spec 9 承诺不含超过 2^53 的整数，Spec 8 RF-23 同一教训）；
6. `seq` 在一个进程内应单调递增；跳号只记诊断，不判帧无效。

**S9-R1（`tool` 帧与 `history` 的内容定义）**：Spec 9 §3.1 只列了 `tool` 帧的键，没有定义 `summary`，也没有携带模型看到的原文；「错误 observation 原样保留」在界面上无法落实。请求：

| 帧 | 键 | 定义 |
|---|---|---|
| `tool{phase:"start"}` | `summary` | 与终端 `[tool]` 回显同一规则（`cli.py:803-819`，Spec 9 已搬入内核的 `ToolVerdict.echo`）扩展到全部工具：已列举的工具取指定参数，其余取 `json.dumps(args, ensure_ascii=False)[:60]`；剥控制字符 |
| `tool{phase:"end"}` | `ok` | 该调用**明确成功**：提交进 `messages` 的 tool 消息 `content` 能解析为 JSON 对象、顶层 `ok is True`，且 `result` 不是 `ok is False` 的对象（`run_pipeline` 失败时顶层仍为 `ok: True`、失败在 `result` 里，`tools.py:937-941`、`755-769`；红队已复核） |
| 同上 | `observation`（新增） | `ok` 为假时 = 该 tool 消息 `content` **逐字**（含判重、人拒、预校验拒、中断、未执行等合成结果）；`ok` 为真时为 `null` |
| `history{role:"tool"}` | `ok`（新增）、`text` | `ok` 同上判定；`ok` 为真时 `text` 为空串，否则为 `content` 逐字 |

判定只在 core 做一次（`protocol.py`），TS 侧不解析 tool 消息内容。

**S9-R2（请求关联号 `rid`）**：Spec 9 的 `error` 帧不带关联信息，host 无法可靠判断一条 `error` 是回应哪次 `user_message`、`answer` 还是 `command`（答复由读者线程处理、用户消息由主循环处理，二者的错误帧没有顺序保证，Spec 9 §4.4）。请求：
- 全部入站帧允许可选键 `rid`（字符串，`^[A-Za-z0-9_-]{1,64}$`，host 生成）；Spec 9 入站 exact-keys 相应放宽为「必需键 + 可选 `rid`」；
- 由某条入站帧直接引起的出站帧回显其 `rid`：`turn_started`（对 `user_message`）、`request_closed{reason:"answered"}`（对 `answer`）、`command_result`（对 `command`）、`error`（对任何入站帧）；其余出站帧不带 `rid`。

**S9-R4（回合末尾的帧，一轮 🟡-7 复核中提出，二轮 🟡-2 补全）**：**每个回合恰好发一帧 `stop_points`**（`items` 可为空），在该回合的 `turn_finished` 之后、本回合 `ensure_pending` **完成写盘之后**发出，内容取自其后的对象库；**该帧新增键 `turn_id`**，值为本回合的 `turn_id`；回合之外发出的 `stop_points`（`ready` 时那一帧）`turn_id` 为 `null`（四轮 🔵-1：host 按 `turn_id` 精确匹配待结算回合，§2.7 第 2 条；Spec 9 TP-12 的键集合断言相应加入 `turn_id`）；覆盖全部结局（`done`、`interrupted`、`error`、`checkpoint_stop`、`blocked`、`degraded`、回滚）；`ensure_pending` 失败时照发（`items` 取失败前可读到的对象，或为空），并另发 `notice`。**不要求两帧相邻**（v0.4 按三轮 🟡-1 删去 v0.3 的「二者之间不插入其他帧」）：`ensure_pending` 夹在两帧之间执行，它与 REPL 同源（Spec 9 §2.4.1 引 `cli.py:1307`），新建停机点时会打印 `[approvals] … 待审批已更新`（`cli.py:131`），协议下成为 `log` 帧；失败时的 `notice` 同样落在两帧之间。Spec 9 §4.7 现写「写 `turn_end`；（协议）`turn_finished`、`stop_points`；`ensure_pending`」，未规定先后，Spec 9 TP-12 只测了「有挂起停机点」一种情况；§2.7 的 host 结算依赖本条。

### 3.3 host ↔ main 生命周期消息（S8-R5，并入 `shared/lifecycle.ts:8-22`）

```ts
// MainToHost 新增
| { type: "quit-query" }
| { type: "quit-proceed" }
| { type: "confirm-result"; reqId: string; ok: boolean }                    // reqId = "<hostBootId>:<n>"
// HostToMain 新增
| { type: "quit-state"; busy: { convKey: string; label: string; running: boolean; openRequests: number }[] }
| { type: "sessions-down" }
| { type: "confirm-query"; reqId: string; title: string; detail: string }   // §2.4 第 5 层；detail 由 renderConfirmDetail 从 host 保存的 request 帧生成
```

- main 对 `confirm-query` 串行处理（同一时刻至多一个原生确认框，窗口模态）；确认框打开期间 app 退出走 §2.10 状态机，未决的 `confirm-query` 视为 `ok: false`；**host 退出时**，main 以 `AbortSignal` 撤下已打开的确认框、丢弃排队中的全部，不向任何 host 回 `confirm-result`（二轮 🔵-5）。
- 未打包构建里，确认框与退出确认框都可由测试桩替代（沿用 Spec 8 TA-12 的对话框桩写法，TG-6 守卫）。

### 3.4 spawn 模板（S8-R3，并入 `host/spawner.ts`；`py = <repoRoot>/.venv/bin/python`，`ep` 为 host 映射且刚 stat 过的绝对路径）

| 模板 | argv | stdio / 超时 | 环境 |
|---|---|---|---|
| `SESSION_NEW` | `[py, "-m", "pipeline.agent.protocol", ep]` | `["pipe","pipe","pipe"]`、`detached: true`、**无超时**（长驻） | 白名单 + 至多一个密钥变量（§2.9）（**2026-10-06 D29 修订**：+ web 检索链上至多 4 个，见 §2.9 修订记录） |
| `SESSION_CONTINUE` | `[py, "-m", "pipeline.agent.protocol", ep, "--continue"]` | 同上 | 同上 |
| `SESSION_IDEA` | `[py, "-m", "pipeline.agent.protocol", "--idea"]` | 同上 | 同上 |
| `NEW_EPISODE` | `[py, "-m", "pipeline.agent.cli", "new", name]` | 现状规则（stdin `ignore`、`detached`）、30 s；**计入在途**（切换仓库须等它结束，🟡-11） | 白名单 |
| `PROBE_KEY_ENV` | `[py, "-c", "import sys; from pipeline.agent.llm import api_key_env_name; sys.stdout.write(api_key_env_name() or '')"]` | 现状规则、10 s | 白名单 |
| `KEYCHAIN_READ` | `["/usr/bin/security", "find-generic-password", "-s", "ava", "-a", envName, "-w"]` | 现状规则、10 s；**stdout 不进尾部缓冲以外的任何地方**，结果只返回给 `secrets.ts` | 白名单 |

- `spawner.ts` 仍是全 `desktop/` 唯一 import `node:child_process` 的文件（TG-3 不变）；新增 `spawnSession()` 返回一个窄接口 `{ pid, write(line), onStdout(chunk), onStderr(chunk), onExit(code, signal), signal(sig, group) }`，不把 `ChildProcess` 类型漏出该文件。
- `SESSION_*` 的 spawn 日志只记模板名、argv 与会话键，不记环境。
- **2026-09-29 修订（N49），经用户同意**：`SESSION_*` 的「白名单」里 PATH 为 `/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin`（Spec 8 §3.4 同日修订记录）。原因：会话内 `run_pipeline` 起的作业原样继承会话 env，固定 PATH 下 render/qc/cover/tts 等按名字调 `ffmpeg`/`ffprobe` 全部 `FileNotFoundError`，桌面端跑不完任何一期。密钥变量仍至多一个，且 §2.9 规则 2 拒绝名为 `PATH` 的密钥变量，注入覆盖不到 PATH。
- 未打包构建的测试钩子可把 `KEYCHAIN_READ` 的可执行路径换成夹具脚本（`!isPackaged` 守卫，TG-6）；打包版无此开关。

### 3.5 常量（全部为初值，PR2/PR4 实测回填）

| 常量 | 初值 | 为什么是这个数 |
|---|---|---|
| `SESSION_FRAME_MAX_BYTES` | 8 MiB | 最大合法帧估计为 `request.fields.args` 里的整份稿件（数十 KB）与长回复；Spec 9 TP-14 按 ≥ 400 KB 帧测试；8 MiB 约是 `read_artifact` 上限 200 000 字节（`tools.py:313`）的 40 倍（A6）。v0.2 起对完整行也生效 |
| `CONV_BUFFER_MAX_BYTES` | 16 MiB / 会话 | 超过即截头并标出；未关闭请求单独保存不受影响；RSS 上界可估 |
| `SESSION_READY_TIMEOUT_MS` | 30 000 | spawn → `ready`；import 实测 0.06 s（E6），恢复大 `session.jsonl` 与 `ensure_pending` 未测，留两个数量级余量（A5） |
| `SEND_ACK_TIMEOUT_MS` / `ANSWER_ACK_TIMEOUT_MS` | 10 000 | Spec 9 在装配前写 `turn_start`（§4.7），`turn_started` 帧的发出时刻协议未规定，估计为毫秒级（约，PR4 实测）；超时 → `E_TIMEOUT`「结果未知」。原生确认框等人的时间不计入 |
| `FEEDBACK_MAX_BYTES` | 65 536 | 人手写的改法说明；远小于 1 MiB 帧上限，留出其余字段 |
| `SESSION_END_WAIT_MS` | 90 000 | 收尾最长一次模型请求（`REQUEST_TIMEOUT = 60`，`llm.py:34`）+ job 杀组 + 余量 |
| `SESSION_KILL_GRACE_MS` | 5 000 | 与 Spec 8 SIGTERM→SIGKILL 一致，高于 Spec 9 `FRAME_DRAIN_TIMEOUT_S` 2 s |
| `QUIT_QUERY_TIMEOUT_MS` | 2 000 | host 在线时 `quit-state` 是内存查询，毫秒级；2 s 内不答视同 host 不可用，直接退出（🟡-3） |
| `QUIT_STOP_TIMEOUT_MS` | 10 000 | 退出第 5 步兜底：`SESSION_KILL_GRACE_MS` + 进程组清理 + 余量 |
| `SETTLE_TIMEOUT_MS` | 10 000 | 结算要等的只有两帧（毫秒级）与一次活跃期读取（`ACTIVE_POLL_MS` 1 s）；10 倍余量，超时即按当时对象库结算并记诊断（§2.7 第 6 条） |
| `CONFIRM_FREE_TEXT_MAX_CHARS` | 2 000 | 原生确认框里模型自由文本（browser 的 `reason`、抓取的 `why`）的显示上限；确定性字段（操作、目标 URL、标题、来源等）不受此限、全文显示（三轮 🔵-4 由 v0.3 的 `CONFIRM_ARGS_MAX_CHARS` 改名改义） |
| `CONV_PUSH_COALESCE_MS` | 50 | 合并 `log` 洪峰（A8）；人的感知阈值约 100 ms |
| `LOG_TAIL_LINES` | 200 | 每个工具行下展示的作业输出行数上限（只影响显示） |
| `KEYCHAIN_SERVICE` | `"ava"` | 固定服务名；账户名 = 变量名 |
| `KEY_ENV_NAME_RE` | `^[A-Z][A-Z0-9_]*_(API_KEY\|KEY\|TOKEN)$` | 现有两个配置（`CPA_API_KEY`、`OPENAI_API_KEY`）都匹配；排除一切改变子进程行为的名字 |

（v0.1 的 `QUIT_SHUTDOWN_WAIT_MS` 按 🟡-4 删除。）

### 3.6 渲染层会话状态（`renderer/convStore.ts`，纯 reducer）

`Store.convs: Record<ConvKey, ConvState>`，`ConvState = fold(entries) + { generation, seq, phase, turnId, open, framesLost, truncatedHead }`。reducer 规则与 Spec 8 `store.ts:51-80` 同构：snapshot 整体替换；delta 只在 `generation`（字符串，含 host 启动随机串）相同且 `seq == last + 1` 时追加；否则丢弃或请求 snapshot。新增动作 `reset-convs`：`onConnect` 时清空全部会话桶（§2.6）。

### 3.7 建期名规则（C10-R1，core 唯一真源；v0.2 按 🟡-5 重写）

**检查与写入同源**：`pipeline/paths.py` 增 `require_data_at(data: Path) -> Path`，内容即现 `require_data` 的函数体（`paths.py:131-158`），把模块常量 `DATA` 换成参数；`require_data()` 改为 `return require_data_at(DATA)`，行为与输出字节不变。`create_new_episode(name)` 在**调用时**取 `data = paths.ROOT / "data"`（与现状 `cli.py:432` 的写入口径一致），检查与写入都以它为准——测试替换 `paths.ROOT` 时，检查的就是临时根下的 `data/`，不会碰到真实 `data/`。

`create_new_episode(name)`：
1. `require_data_at(data)`；它以 `SystemExit(msg)` 报错时，捕获并把 `msg` 原样写 stderr、返回 2（缺席、悬空、骨架不全三分提示原文不变，**绝不创建**）；再要求 `data/episodes/` 已是目录，否则 stderr「FAIL <路径> 不存在：先跑 ./pipeline/preflight.sh --init」并返回 2，不创建；
2. 名字须同时满足：与 `name.strip()` 相等且非空；不含 `/`、`\`、`\0` 与任何控制字符（`\x00-\x1f\x7f-\x9f`）；不是 `.`、`..`；不以 `.`、`_` 开头（`cli.py:330`、`332` 会把它们藏起来）；不以 `-` 开头（`-x`、`--continue` 会与命令行开关混淆，🔵-4）；UTF-8 编码 ≤ 255 字节（APFS 单段名上限，约）。不满足 → stderr 一行说明具体违反哪条，返回 2；
3. `(data / "episodes" / name).mkdir(parents=False, exist_ok=False)`；创建后 `resolve().parent` 必须等于 `(data / "episodes").resolve()`，否则删除该空目录并返回 2（纵深防御，正常不可达）；
4. 退出码：0 成功；1 已存在（现状语义不变）；2 名字不合规或 `data/` 不可达。`main` 对 `ava new` 要求恰好一个名字参数（`cli.py:1559` 现为 `len(args) >= 2`，多余参数被静默忽略），否则 stderr 用法并返回 2。

**受影响的现有用例**（v0.3 以**同源语义原型**实跑**全量** pytest 为准：`4 failed, 1711 passed`，E11；v0.2 照抄上一轮报告的 3 条清单漏了第 4 条，二轮 🟡-1）：`tests/test_agent_cli.py:95` `test_create_new_episode_and_reject_duplicates`（只建了 `data/episodes`、没建 `data/library`，同源检查判「骨架不全」）、`:400` `test_main_new_enters_repl_in_tty`、`:425` `test_non_tty_dual_gates_for_new_and_idea`、`tests/test_agent_tools.py:708` `test_full_chain_smoke_in_temp_repo`（后三个依赖「自建 `data/episodes`」这一缺陷）。改写方式：在 `tmp_path` 下先建 `data/library/` 与 `data/episodes/`，断言不变。列入 §6.1。**此后凡「现有测试影响」的清单，一律以实现后的全量 pytest 实跑为准。**

### 3.8 01 工序完成判定（C10-R3，用户裁决；🟡-6）

- **规则**：`01-topic.md` 存在，且文中存在至少一行「类型」字段（行内匹配 `^[ \t]*类型[ \t]*[:：][ \t]*(.*?)[ \t]*$`，逐行、**不跨行**），且所有这样的行的值都为空 → **01 未完成**：`current_step = "01 选题"`、`completed_steps = []`、`is_blocked = false`（不是停机点，agent 可以自己推进）、`next_action = "01-topic.md 的「类型」尚未填写：在对话里说明本期想做什么，agent 推断后经写入卡填写；也可以手动填写"`、`docs_ref = "docs/runbook/01-topic.md"`。其余情况与现状完全相同（`status.py:197` 只看存在）。
- **为什么是这条规则**（E7，26 个真实期实测）：「类型须属于题材表（`check_script.py:99`）、杂谈须带模式与张力」的严格规则会让 5 个真实期倒退回 01（类型缺行、写成「算法诗」「杂谈（乐评）」「娱乐向（肥宅快乐导向）」、杂谈缺张力）；本规则 0 期倒退，`ava new` 模板命中。挂插件对全量测试原型验证：1715 passed（E8）。代价：没有「类型」行的旧式文件、类型值不在题材表的文件不被把关（RF-16）。
- **只在 `status.py` 内实现**，不 import `check_script`（后者 import `bgm`，`check_script.py:20`；status 是 Spec 8 每 5 s spawn 的热路径）。
- **C10-R4（非阻塞，作者自查 1）**：`check_script.py:100` 的 `TOPIC_FIELDS` 用 `\s*` 可跨行，对模板解析出 `类型 = "模式："`、`模式 = "张力："`（E9）。改为行内空白 `[ \t]*`；与本 spec 的功能无依赖，可随 PR0 一并修或另行处理。

---

## 4. 模块接口与签名

### 4.1 `shared/`（纯 TS）

```ts
// convFrames.ts —— §3.2 校验，host 唯一的帧入口
export type OutFrame = { v: 1; t: OutType; seq: number; sid: string | null } & Record<string, unknown>;
export function parseOutFrame(line: string): { ok: true; frame: OutFrame } | { ok: false; reason: "malformed" };

// convFold.ts —— §2.3 映射；host（摘要、徽标）与 renderer（视图）共用
export type ConvRow = /* user | assistant | toolLine | logBlock | cardTrace | stopNote | notice | turnFooter | historySeparator | exited | framesLost */;
export function foldConv(entries: readonly ConvEntry[]): { rows: ConvRow[]; running: boolean; turnId: string | null };
export function convSummary(s: { phase: ConvSnapshot["phase"]; open: readonly OutFrame[] }): { live: boolean; running: boolean; openRequests: number };

// isoTime.ts —— 🔵-12：小数秒补足 6 位后按数值比较（Python isoformat 在微秒为 0 时省略小数部分）
export function compareIso(a: string, b: string): number;          // 无法解析 → 抛错（不静默按字符串比较）

// secretsRules.ts —— §2.9 的两条纯规则，便于单测
export function validKeyEnvName(name: string): boolean;
export function keyFromSecurityStdout(stdout: string): string | null;   // 去一个末尾 \n；可打印 ASCII 才返回

// nativeConfirm.ts —— §2.4 第 5 层的触发判定（纯函数）
export function needsNativeConfirm(req: OutFrame, decision: string): boolean;   // (fetch ∨ tool_call∧fields.tool==="browser") ∧ decision==="approve"；req 只来自 host 保存的打开请求
export function renderConfirmDetail(req: OutFrame): { title: string; detail: string };   // \p{Cf} → ⟨U+XXXX⟩；确定性字段在前、全文；模型自由文本在后、标注并按 CONFIRM_FREE_TEXT_MAX_CHARS 截断
```

### 4.2 `host/`

```ts
// sessions.ts —— 会话管理（§2.1、§2.10）；host 中唯一构造入站帧、写会话 stdin 的模块（TG-11）
export class SessionManager {
  constructor(deps: { spawnSession: SpawnSession; resolveKey: () => Promise<{ name: string; value: string } | { problem: string }>;
                      confirm: (title: string, detail: string) => Promise<boolean>;      // → main 的 confirm-query
                      now: () => number; bootId: string; push: (env: Envelope) => void; diag: (s: string) => void;
                      blocked: () => boolean });                                        // choosing || switching（🟡-11）
  send(key: ConvKey, target: SessionTarget, text: string): Promise<{ turnId: string }>;
  resume(key: ConvKey, target: SessionTarget): Promise<ConvSnapshot>;
  interrupt(key: ConvKey, turnId: string): void;                     // 本地校验失败抛 E_STALE
  answer(key: ConvKey, requestId: string, decision: string, feedback: string | null): Promise<{ decision: string }>;
  command(key: ConvKey, name: "memory_ack" | "scope", arg: "asset" | "auto" | null): Promise<{ ok: boolean; text: string }>;
  end(key: ConvKey): Promise<{ code: number | null; signal: string | null }>;
  snapshot(key: ConvKey): ConvSnapshot;
  summaries(): Map<ConvKey, { live: boolean; running: boolean; openRequests: number }>;
  anyLive(): boolean;                                                 // S8-R10
  quitState(): QuitBusy[];
  stopAllForQuit(): Promise<void>;                                    // §2.10 第 5 步：空闲 shutdown、忙 SIGTERM、5 s 后 SIGKILL 组；此后拒绝新请求
  onApprovalsRead(epKey: string, readStart: number): void;           // §2.7 host 结算：由 service 在活跃期每次读对象库前取 readStart、读完并推完 episode.delta 后调用
}

// secrets.ts —— KEYCHAIN_READ 结果的唯一读取者（TG-12）
export function resolveLlmKey(run: typeof runCore, repoRoot: string): Promise<{ name: string; value: string } | { problem: string }>;

// spawner.ts（增）
export type SessionTemplate = "SESSION_NEW" | "SESSION_CONTINUE" | "SESSION_IDEA";
export function spawnSession(t: SessionTemplate, args: { ep?: string }, ctx: { repoRoot: string }, extraEnv: Record<string, string>): SessionProc;
```

**`conv.answer` 处理顺序（冻结；v0.2 按 🟡-12 改）**：会话键映射 → `requestId` 在打开集合（否则 `E_STALE`，零写入；进程退出时打开集合已清空，因此退出后的答复都落在这里）→ 该请求无在途答复（否则 `E_BUSY`）→ `decision ∈ options`、`feedback` 合规、整帧 ≤ 1 MiB（否则 `E_BAD_REQUEST`）→ **若 `needsNativeConfirm`：`await confirm(…)`，为假 → `E_STALE`「已在确认框取消」、零写入；等待期间进程退出 → `E_STALE`**（打开集合已清空，复查一次）→ 活进程存在（否则 `E_SESSION`）→ 生成 `rid`、写 `answer` 帧（写失败 → `E_SESSION`）→ 等同 `rid` 的 `request_closed`（→ 记 `answered_local`、成功）或 `error`（→ 映射）或进程退出（→ `E_SESSION`）或超时（→ `E_TIMEOUT`）。

**会话退出处理（冻结）**：`onExit` → `phase = "exited"` → 把 `open` 中全部 `request_id` 写进一条 `voided_local` 条目并清空 `open` → 拒绝全部在途等待（`E_SESSION`）→ 进程组清理（§2.10）→ 推 delta → 刷新期列表摘要。

### 4.3 `renderer/`

| 文件 | 内容 |
|---|---|
| `HumanCards.tsx` | `CardFrame`、`RequestCard`、`StopPointCard`（原 `DecisionBar.tsx` 内容）、`AnswerDock`；**全仓唯一**出现 `"approval.decide"` 与 `"conv.answer"` 的文件 |
| `Composer.tsx` | 输入框、发送 / 停止；唯一出现 `"conv.send"`、`"conv.interrupt"` 的文件 |
| `SessionHeader.tsx` | scope、LLM 状态、素材模式、确认记忆、结束会话、继续上次会话、建期…；唯一出现 `"conv.command"`、`"conv.end"`、`"conv.resume"` 的文件 |
| `NewEpisodeForm.tsx` | 期名输入（初值恒为空串）与「建期」；唯一出现 `"episode.create"` 的文件 |
| `ConversationPane.tsx` | 对话流（`foldConv` 的行）；纯展示，不含任何上述方法名 |
| `convStore.ts`、`autoOpen.ts` | 纯函数（§3.6、§2.7） |
| `App.tsx` | 版面：左栏期列表（含徽标）+「选题」+ 产物树 + 画廊；中栏工序条 + 对话 + 待答区 + 输入；右栏 PreviewPane；底部时间线（不变）；`onConnect` 清空会话桶（§2.6） |

---

## 5. 依赖白名单与纯洁性保障

- **npm 零新增**：`dependencies`/`devDependencies` 与 Spec 8 §5.1 完全相同，TG-1 不改。对话、卡片、输入框全部手写。
- **import 纪律不变**（Spec 8 §5.2）：host 仍只允许 `node:fs`、`node:fs/promises`、`node:path`，`node:child_process` 仍只在 `spawner.ts`；renderer 不新增任何 import 来源。
- **Python 侧**：C10-R1~R4 只改 `pipeline/paths.py`、`pipeline/agent/cli.py`、`pipeline/agent/llm.py`、`pipeline/status.py`、`pipeline/check_script.py` 五个既有模块，只用 stdlib 与仓内模块；不新增模块、不动 `pyproject.toml`；`status.py` 不新增任何 import（§3.8）。
- **子进程纯洁性断言（红线 7）**：Spec 8 的 `tests/test_desktop_core_isolation.py` TC-1 探针（`import pipeline.agent.cli, pipeline.status, pipeline.review` 后 `sys.modules` 无 server/DB/ML 栈）追加 `pipeline.agent.protocol`（桌面端现在直接 spawn 它；Spec 9 PR3 落地后启用）与 `pipeline.agent.llm`（`PROBE_KEY_ENV` import 它）。期望值 `[]` 已对现有三个模块实跑（Spec 8），新增两项在 PR0/PR4 先跑后写。
- **不开端口**：TI-6 扩展为同时检查会话进程：`lsof -a -p <会话 pid> -iTCP -sTCP:LISTEN` 为空（Spec 9 TG-2 从源码层保证，本条从运行时再看一次）。

---

## 6. 跨 spec 接口与系统边界

### 6.1 修订请求（**2026-09-26 全部获用户授权**；S8-R1 早于此随 Spec 9 获授权）

| 编号 | 对象 | 请求 | 级别 |
|---|---|---|---|
| **S8-R1** | Spec 8 §2.3、§3.4（Spec 9 提出，交本 spec 并入） | ① I2 增「会话写入」类；② 会话模板提供 `api_key_env` 指名的变量 | 阻塞；**已获授权**（Spec 9 §6.1，2026-09-25），本 spec 按 §2.2、§2.9 落地 |
| **S8-R2** | Spec 8 §3.2 | 方法闭集 +8、push 主题 +2、错误码 +2（§3.1）；`approvals` 快照结构不变 | 阻塞 |
| **S8-R3** | Spec 8 §3.4 | spawn 闭集 +6；`SESSION_*` 为 stdin `pipe`、无超时的长驻例外；环境白名单对 `SESSION_*` 的一个密钥变量例外 | 阻塞 |
| **S8-R4** | Spec 8 §2.3 I2 | 增「建期」类（`ava new` 写出的期目录与 `01-topic.md`）；TI-3b 的清单相应扩展 | 阻塞 |
| **S8-R5** | Spec 8 §2.2、§2.9；`main/index.ts:299-307` | 生命周期消息 +6（§3.3）；「v1 不拉起长任务、退出不发信号」对会话进程改为 §2.10；`window-all-closed` 不再先发 `shutdown`；退出状态机 | 阻塞 |
| **S8-R6** | Spec 8 §2.5「停机点自动呼出」、RF-24 | 替换为 §2.7 规则 | 阻塞（用户裁决） |
| **S8-R7** | Spec 8 TG-4、TG-6；`renderer/DecisionBar.tsx` | TG-4 泛化为 §2.4 第 1 层五条规则（TG-4′、TG-10、TG-16、TG-17）；`DecisionBar.tsx` 并入 `HumanCards.tsx`；`approval.decide` 的点击处理器加 `isTrusted` 首句；TG-6 钩子正则扩展 | 阻塞 |
| **S8-R8** | Spec 8 §2.11、§3.3、版面 | 删去「v1 不做对话面板」；`EpisodeSummary.conv`、`EpisodesList.idea`；产物树移左栏 | 阻塞 |
| **S8-R9** | Spec 8 §3.1 规则 6；`App.tsx:395` | 无 `approval_id` 的 `approval_resolved` 按 `decision`/`source` 显示批准或拒绝（H-10） | 非阻塞（现存显示缺陷，可先修） |
| **S8-R10** | Spec 8 §2.10；`service.ts:931-963` | repoRoot 切换在有活会话时 `E_BUSY`；`choosing`/`switching` 期间 `conv.send`、`conv.resume`、`episode.create` 一律 `E_BUSY`；`NEW_EPISODE` 计入在途（🟡-11） | 阻塞 |
| **S8-R11** | Spec 8 §6.3 | egress 复审结论入文（§2.2 末行） | 非阻塞（文字） |
| **S8-R12** | `desktop/src/host/heal.ts:109` `latestPerStop` | 按字符串 `>=` 比较 `created_at`，在微秒恰为 0 的时间戳上排错序（🔵-12 扩大）；改用 `shared/isoTime.ts` 的 `compareIso` | 非阻塞（Spec 8 已落地代码的潜在缺陷，概率约百万分之一，可随 PR2 修） |
| **S9-R1** | Spec 9 §3.1 | `tool` 帧 `summary`/`ok` 的定义与新增 `observation`；`history` 的 tool 条目增 `ok`、`text` 规则（§3.2） | 阻塞（Spec 9 未施工，越早改越省；须红队对 Spec 9 做定向复核） |
| **S9-R2** | Spec 9 §3.1 | 入站可选 `rid`、四类出站帧回显（§3.2） | 阻塞（同上） |
| **S9-R3** | Spec 9 §6.6 H-6 | 原文「关窗先发 shutdown，留出收尾时间后再 SIGTERM」→「**结束会话**先发 `shutdown`、留出收尾时间后再 SIGTERM；**退出 app**（人已在确认框同意中断）时，空闲会话发 `shutdown`，回合中或有未答卡的会话直接 SIGTERM，记 `turn_end{wrapup:"skipped"}`；若会话已在收尾中（人先点过停止），SIGTERM 按 §2.2 状态表放弃收尾、记 `aborted`」（二轮 🔵-7：与 §2.10 一致） | 阻塞；已授权（2026-09-26）；`aborted` 例外为授权后补充，2026-09-26 已获人确认 |
| **S9-R4** | Spec 9 §4.7、§3.1 | 每个回合恰好一帧 `stop_points`（`items` 可空、覆盖全部结局），在 `turn_finished` 之后、本回合 `ensure_pending` 完成写盘之后发出，**不要求相邻**；该帧新增键 `turn_id`（回合外发出的 `ready` 那帧为 `null`）（§3.2） | 阻塞（§2.7 host 结算依赖）；v0.3/v0.4 在 2026-09-26 授权之后补充了措辞（新增义务：每回合恰好一帧、覆盖全部结局、失败照发）；`turn_id` 键为用户 2026-09-26 指示加入（四轮 🔵-1）；v0.5 最终措辞 2026-09-26 已获人确认，**已并入 Spec 9 v0.8**（2026-09-26），待红队定向复核 |
| **C10-R1** | `pipeline/paths.py`、`pipeline/agent/cli.py` `create_new_episode`、`main` | §3.7 全部：`require_data_at`；检查与写入同源；名字规则（含禁 `-` 前缀）；退出码 | 阻塞（其中「建出 `data/`」违反 AGENTS.md 五，与桌面端无关也该修） |
| **C10-R2** | `pipeline/agent/llm.py` | 新增 `api_key_env_name(root=None) -> str \| None`（只读配置、不读环境变量；`base_url`/`model`/`api_key_env` 缺一即 `None`，同 `llm.py:136-140`）；`load_llm_config` 改为调用它取名字，行为字节不变 | 阻塞 |
| **C10-R3** | `pipeline/status.py` | §3.8：存在「类型」行且值全为空 → 01 未完成 | 阻塞；方向已由用户裁决（🟡-6） |
| **C10-R4** | `pipeline/check_script.py:100` `TOPIC_FIELDS` | `\s*` 改 `[ \t]*`，不再跨行（E9） | 非阻塞（作者自查） |
| **DIR-R2** | direction §0.1 第 2 步、§6 Spec 10「范围」 | 「Agent 自主推断题材模式、建立期目录」→「Agent 在选题会话里推断题材模式；人点『建期』、填期名，由 core 建立期目录；新期停在 01，agent 经写入卡补全 `01-topic.md`」 | 非阻塞（用户已裁决，落文字） |
| **测试改动** | ① `desktop/tests/static/guards.test.ts`、`scan.ts`（`DECISION_BAR_FILE` 与 TG-4 检查器）；`e2e/preview.spec.ts` 中依赖中栏产物树位置的选择器（若有）。② `tests/test_agent_cli.py:95`、`:400`、`:425`、`tests/test_agent_tools.py:708`：在 `tmp_path` 预建 `data/library/`、`data/episodes/`（C10-R1；v0.3 按二轮 🟡-1 由 3 个改为 4 个，E11） | 随 S8-R7、S8-R8、C10-R1 | 阻塞；**`e2e/ack.spec.ts` 必须零改动**（`data-testid` 全部保留），作为卡片搬家不改语义的尺子 |

### 6.2 Spec 9 §6.6 H-1~H-10 的承接

| 编号 | 要求 | 本 spec 的机制 | 测试 |
|---|---|---|---|
| H-1 | `answer` 只由人的点击产生 | §2.4 五层：静态规则、`isTrusted`、host 绑定、无无点击路径、browser/抓取卡的原生确认框 | TG-4′、TG-16、TG-17、TX-2、TX-13、TX-14、TH-3、TH-18 |
| H-2 | 只有 `request` 帧能生成可点击的批准控件 | 可点击控件的来源只有 `request` 帧与对象库（Spec 3 对象）；`assistant`/`log`/`stop_points` 只渲染为纯文本 | TG-13、TX-1 |
| H-3 | 卡片字段与模型文本按纯文本渲染 | 全 `renderer/` 只有 `PreviewPane.tsx` 可用 `markdown-it` 与 `dangerouslySetInnerHTML` | TG-13 |
| H-4 | 抓取卡逐条、检查点卡无「以后不再询问」 | 按钮只取 `request.options`；处理器内禁循环与迭代方法 | TG-4′、TX-12 |
| H-5 | 进程退出时未关闭请求卡立即作废 | §4.2 会话退出处理；退出后答复 `E_STALE` | TH-4、TX-9 |
| H-6 | 停止发 `interrupt{turn_id}`；关窗先 `shutdown` 再 SIGTERM | 「停止」按钮；「结束会话」严格按 H-6；**退出 app 按 S9-R3 修订后的 H-6**（用户裁决：回合中直接 SIGTERM、记 `skipped`） | TH-8、TH-9、TX-4、TX-8 |
| H-7 | spawn 时提供密钥；缺失如实降级 | §2.9 | TH-6、TH-7 |
| H-8 | 没有人操作时不生成 `user_message` | 构造 `user_message` 只在 `SessionManager.send` 一处，且只由 `conv.send` 调用；`conv.send` 只在 `Composer.tsx` 可信事件处理器里 | TG-10、TG-11、TH-5 |
| H-9 | 只从 stdout 解析帧 | §2.1 第 4 条 | TH-2 |
| H-10 | 无 `approval_id` 的 `approval_resolved` 不得一律标拒执 | S8-R9 | TV-7、TX-11 |

### 6.3 仍然成立的 Spec 8 约束

I1（host 零写入）、媒体只读协议与路径守卫、renderer 零 Node、CSP、出网拦截、单实例、打包加固与构建溯源、heal 触发闭集 H1–H5、ack 四道闸与 05 两步链——全部不变，对应测试不改。退出流程改动后，Spec 8 的 host 熔断（TI-9b）与 e2e 的 `app.close()` 必须照旧能退出（TX-8b）。

### 6.4 与 Spec 11、Spec 12 的边界

- **Spec 11**：02.5 编辑器、03.5 按钮、人时采集；本 spec 的卡片组件与待答区可被复用，但不预置任何 Spec 11 控件。Spec 9 RF-17（`cli.py:644-646` 在 EOF 时取「是」）仍归 Spec 11。
- **Spec 12**：09 的选择控件放进 `StopPointCard` 的 09 插槽（§2.8）；图片导入与编辑工具、ADR-0025 都归 Spec 12。

### 6.5 与 Spec 5/6/7（经 Spec 9）

browser 逐调用卡、素材抓取卡、`write_memory` 卡与记忆确认卡都以 Spec 9 的 `request` 帧到达。本 spec 负责呈现、转交人的答复，并对 browser 与抓取卡的批准加一道原生确认框（§2.4 第 5 层）；不改 core 的任何判定。

### 6.6 八条施工红线对照

| 红线 | 落点 |
|---|---|
| 1 不引入数据库 | 会话状态在 host 内存缓冲 + core 的 `session.jsonl`；无任何存储引擎 |
| 2 无框架、无 server | npm 零新增（TG-1）；stdio 管道；会话进程不监听（TI-6 扩展） |
| 3 产物即状态 | 工序只来自 `status --json`（C10-R3 也在 status 内）；停机点卡只来自对象库；`desktop/src` 不出现 `session.jsonl` 字面量（TG-14）；帧只驱动对话显示与徽标 |
| 4 停机点不减、ack 显式 | 停机点卡语义不变；09 仍纯记录；全部答复经可信点击 |
| 5 人审闸门不自动化 | 抓取逐条、记忆确认、browser 卡全部照 Spec 9 出卡；browser 与抓取批准另需原生确认；无批量、无自动答复（H-1、H-4、H-8） |
| 6 工具表封顶 | 新增工具 0 个（建期走人按钮 + 既有 `ava new`，未走「agent 建期工具」） |
| 7 零重依赖 | §5 |
| 8 期望值先跑、变异检验 | E1–E9 已先跑；§7 用例的期望值在各 PR 首日先跑再写；§7.2 |

### 6.7 ADR-0018 保留条款与「不立 ADR」

- 不引入 agent 框架：npm 白名单不变；会话内核是 Spec 9 自写的。
- Code Freeze：会话的 `notice{code:"code_freeze"}` 原样显示为黄色一行；健康面板照旧。
- 配音双层闸：桌面端不新增任何 `/run` 模板；模型经工具卡发起的 `tts` 仍受 Spec 9 C-R5 约束。
- 封面标题只出候选：本 spec 不新增任何定稿动作（§2.8）。
- **不立 ADR**：会话托管在 host 由 ADR-0020 §4 授权；钥匙串是密钥**来源**的变化，`llm.py`「密钥只从环境变量读」的边界不变（host 把钥匙串的值放进子进程环境）；建期不新增工具；C10-R3 是对 01 完成判定的收紧，不改工序顺序。

---

## 7. 测试规格与变异检验矩阵

> **同步点**：host 集成测试以「驱动假会话进程写帧 → 断言」为节拍，不 sleep 等轮询。  
> **假会话进程（v0.2 按 🔴-1 重写）**：仿 `fakeCore`（`e2e/ackFixtures.ts:154`），在 `makeFixtureRepo` **已复制的** `<tmp>/pipeline/agent/` 下写一个假 `protocol.py`，按剧本文件（JSON 行）逐行向 stdout 写帧、把收到的每一行 stdin 与收到的信号追加到临时根内的记录文件。真实的 `.venv/bin/python` 照常以 `-m pipeline.agent.protocol` 执行它，**夹具从不写 `.venv` 下的任何路径**。它让 PR2/PR3 不依赖 Spec 9 施工；真实 core 的契约由 PR4 的 TX-0 负责。  
> **夹具写入守卫（TF-1、TF-3）**：**本 spec 新增的会话类夹具**（`desktop/tests/fixtures/session*.ts`、`desktop/e2e/sessionFixtures.ts`）的每一次写文件、建目录都经 `fixtureWrite(root, rel, data)`：写前对目标父目录做 `realpath`，必须是 `realpath(root)` 的后代；目标本身若已存在，先 `lstat`，是符号链接即拒绝（否则指向外部的链接会让写入穿出去，二轮 🔵-9）；任一不满足即抛错。TF-3 以 Spec 8 TG-2 的 `fsWriteCalls` 扫描器静态保证这些文件里除 `fixtureWrite` 的实现外没有直接的 fs 写 API。Spec 8 的既有夹具（`tests/helpers.ts`、`e2e/ackFixtures.ts`）不在此范围：它们只写复制出来的 `pipeline/` 与 `data/`，不经 `.venv`，本 spec 不改它们。`.venv` 软链指向仓库外（本机 `realpath .venv/bin/python` → `/opt/homebrew/Cellar/python@3.12/…`），任何经由它的写都会被拦下。  
> **不出网、不花钱（TF-2，🟡-10）**：凡会起会话进程的夹具，强制改写副本的 `config/agent.local.json` 为 `{base_url: <本地假端点>, model: "fake", api_key_env: "AVA_TEST_KEY"}`，并强制把 `KEYCHAIN_READ` 换成夹具脚本（未打包构建的测试钩子）。联网工具（二轮 🔵-10）：副本的 `config/agent/web.local.json`（`web.py:73-74` 优先读它）改写为 `search.endpoint` 指向本地假端点；`web_fetch`/`crawl`/`browser` 能取任意 URL、无法靠配置收拢，因此假 LLM 的剧本文件由夹具加载时校验：出现这三个工具的调用，其 URL 参数必须以 `http://127.0.0.1:` 开头，否则拒绝加载。夹具在用例开始前断言：副本 `config/agent.local.json` 的 `base_url` 与 `config/agent/web.local.json` 的 `search.endpoint` 都以 `http://127.0.0.1:` 开头；host 解析到的 `KEYCHAIN_READ` 可执行路径位于临时根内。任一不成立即失败，不跑用例。  
> **真实数据零污染**：全部夹具在临时目录；测试前后断言真实 `<repo>/data` 未被修改（沿用 Spec 8）。  
> **期望值先跑**：TH/TX 的具体计数与文案在各 PR 首日先对实现跑出、解释清楚再写入。

### 7.1 测试规格

**TY：core（pytest，PR0）**

| 编号 | 场景 | 断言 |
|---|---|---|
| TY-1 | `create_new_episode`（替换 `paths.ROOT` 为 `tmp_path`，C10-R1 同源后测到的是临时根）：`data/` 缺席；`data` 为悬空软链；`data/library` 缺席；`data/episodes/` 缺席 | 各自退出码**恰为 2**；stderr 分别包含 `require_data_at` 的三种原文（「不存在。先建存储骨架」「是悬空的符号链接」「骨架不全」）与「先跑 ./pipeline/preflight.sh --init」；**临时根下文件清单前后完全相同**；真实 `<repo>/data` 的 mtime 不变 |
| TY-2 | 名字 `../x`、绝对路径、`a/b`、`a\b`、空串、`"  "`、`" x"`、`_x`、`.x`、`.`、`..`、`-x`、`--continue`、含 `\x07`、含 `\0`、256 字节 | 全部退 2；episodes 根内外均未新建任何东西 |
| TY-3 | 合法名 `2026-09-26-罪恶王冠-测试`；同名再建一次 | 第一次退 0，目录与 `01-topic.md` 存在且模板内容与现状逐字节相同；第二次退 1 |
| TY-4 | `main(["new"])`、`main(["new", "a", "b"])`（非 TTY） | 均退 2，未建目录 |
| TY-5 | `api_key_env_name`：只有 `agent.json`；有 `agent.local.json`；local 损坏；`base_url`/`model`/`api_key_env` 各缺一 | 返回值与 `load_llm_config` 实际选用的文件与字段一致，缺一即 `None`；不读取 `os.environ`（以替换 `os.environ` 为访问即抛的映射断言） |
| TY-6 | 回归 | 全量 `uv run pytest` 除 §6.1 列出的 **4 个**改写用例外零改动全绿（v0.3 按二轮 🟡-1；E11 为同源原型的实跑依据）；`require_data()` 在真实仓库上的输出与改动前逐字节相同 |
| TY-7 | TC-1 探针追加 `pipeline.agent.llm`（PR0）与 `pipeline.agent.protocol`（PR4） | 泄漏清单为 `[]` |
| TY-8 | C10-R3：① `ava new` 生成的模板；② 同一模板把「类型：」填为「人物志」；③ E7 中 5 种非标准形状各一个夹具（无类型行、类型「算法诗」、「杂谈（乐评）」、「娱乐向（肥宅快乐导向）」、杂谈缺张力）；④ 类型行值为空、但另一行 `类型：杂谈`；⑤ `类型：` 后紧跟换行、下一行是 `模式：立论` | ① `current_step == "01 选题"`、`completed_steps == []`、`is_blocked is False`；② 为 `02 脚本写作`；③ 各夹具的 `status --json` 与改动前逐字节相同；④ 不算未完成（存在非空值）；⑤ 算未完成（不跨行）。另：`python -c "import pipeline.status"` 后 `sys.modules` 不含 `pipeline.check_script`、`pipeline.bgm` |
| TY-9 | C10-R4：模板经 `episode_genre` 解析 | 返回 `("", "")`（改前为 `("", "张力：")`，E9） |

**S9-R1/R2/R4 的用例**写入 Spec 9 的 `tests/test_agent_protocol.py`（随 Spec 9 PR3 施工，编号由 Spec 9 定向复核时分配）：失败调用的 `observation` 逐字节等于 `session.jsonl` 中对应 tool 消息的 `content`；成功调用为 `null`；`run_pipeline` 失败（顶层 `ok: True`、`result.ok: False`）判为非成功；每类入站帧带 `rid` 时对应出站帧回显、不带时不回显；`stop_points` 帧到达时，对象库文件中已含本回合 `ensure_pending` 新建的对象。

**TF：夹具自检（vitest，PR2 起）**

| 编号 | 断言 |
|---|---|
| TF-1 | `fixtureWrite(root, ".venv/bin/python", …)` 抛错且目标文件 mtime 不变；在临时根内建一个指向根外临时文件的符号链接 `pipeline/agent/evil.py`，`fixtureWrite(root, "pipeline/agent/evil.py", …)` 抛错且根外文件内容不变；`fixtureWrite(root, "pipeline/agent/protocol.py", …)` 成功 |
| TF-3 | 静态：会话类夹具文件中，fs 写类 API 只出现在 `fixtureWrite` 的实现内（复用 `tests/static/scan.ts` 的 `fsWriteCalls`） |
| TF-2 | 会话类夹具构造后：副本 `config/agent.local.json` 的 `base_url` 以 `http://127.0.0.1:` 开头、`api_key_env == "AVA_TEST_KEY"`；`config/agent/web.local.json` 的 `search.endpoint` 以 `http://127.0.0.1:` 开头；含 `web_fetch` 外部 URL 的剧本被拒绝加载；host 的 `KEYCHAIN_READ` 路径在临时根内 |

**TV：纯函数（vitest，PR1–PR3）**

| 编号 | 断言 |
|---|---|
| TV-1 | `parseOutFrame`：出站闭集 14 种各一个合法样例通过；缺必需键、`v: 2`、未知 `t`、`seq: 2**53 + 1`（以文本构造）、非对象各判 malformed；多出的键被忽略 |
| TV-2 | `foldConv`：一回合含 2 个工具（一成功一失败）、1 段 `log`、1 张卡（开 → `answered_local` → 关）、1 条回复、`turn_finished` → 行序列与期望逐项相等；失败行的 `observation` 原文逐字节保留；`log` 挂在第一个工具行下；卡片留痕显示「已拒绝（附反馈：…）」 |
| TV-3 | `foldConv` 的缺口：`frames_lost`、截头后首条是孤立的 `tool{end}`、`voided_local` → 各自生成显式行，孤立行不崩；无 `voided_local` 的 `request_closed{reason:"voided"}` 显示「已作废」不带原因 |
| TV-4 | `convStore` 多会话：交错投喂 `ep:A`、`ep:B`、`idea` 的 snapshot 与 delta → 各桶的行与 `open` 集合互不包含对方的 `request_id`；`generation` 不同的 delta 被丢弃；跳号请求 snapshot |
| TV-5 | `convSummary`：`phase`/`open` 各组合 → 徽标值 |
| TV-6 | `autoOpen` 逐条驱动：① 空预览 + 新 pending → open；② `owner: human` → strip，不 open；③ 媒体在播放 → strip；④ 回合中先后出现两个同类 pending → 回合中零 open；⑤ 两个不同类在回合中出现 → 结算后只 open 最新者；⑥ strip 点击 → open 且 `owner: auto`；⑦ 切期后同一 `approvalId` 不再触发；⑧ `turn_started` 之后到 `settled` 之前，对象库先后出现 v1、v2 → 零 open；`settled` 到达时恰 open v2 一次；⑨ `settled` 的 `turnId` 与 `awaiting` 不符 → 忽略；⑩（二轮 🟡-2）回合内零停机点、对象库没变：`settled` 到达后，下一次新出现的 pending **立即** open（没有卡在等待态）；⑪ R6 从 snapshot 重建 `awaiting`（三轮 🔵-1）：(a) host 重启——新 snapshot 无旧回合，`awaiting` 为 null，新 pending 照 R1 立即处理；(b) renderer 重载、回合仍在跑——snapshot 的最后一个 `turn_started` 无对应 `settled`，`awaiting` 恢复为该 `turnId`，重载后出现的中间版本零 open，`settled` 到达后恰 open 一次；(c) renderer 重载时该回合已结算——`awaiting` 为 null；(d)（四轮 🔵-3）snapshot 已截头（`truncatedHead: true`、`entries` 里没有该回合的 `turn_started`）、`phase: "running"` → `awaiting` 仍恢复为 `snapshot.turnId`；`phase: "idle"`、`settlePending` 非空 → `awaiting = settlePending` |
| TV-7 | 时间线标签（H-10）：`{decision:"approved", source:"write_episode_file"}` → 「命令卡批准」；`rejected` → 「命令卡拒绝」；带 `approval_id` 的两种形状不变 |
| TV-8 | `keyFromSecurityStdout`：`"sk-abc\n"` → `sk-abc`；`"sk-abc\n\n"` → null；`"a b\n"` → null；`"秘密\n"` → null；空串 → null。E5 的十六进制输出（如 `"736b2d…\n"`）→ 返回该串本身：这是 §2.9 第 4 条如实记录的盲区，用例把它钉成「已知行为」，防止将来有人加一段猜测式解码而无人察觉 |
| TV-9 | `validKeyEnvName`：`CPA_API_KEY`、`OPENAI_API_KEY`、`X_TOKEN` 通过；`PATH`、`PYTHONPATH`、`DYLD_INSERT_LIBRARIES`、`NODE_OPTIONS`、`api_key`、`A-KEY` 拒绝 |
| TV-10 | `convStore` 的 `reset-convs`：先装入 `generation "boot1:3"`、`seq 5` 的会话，`reset-convs` 后投喂 `"boot2:3"`、`seq 6` 的 delta → 丢弃并请求 snapshot（没有拼到旧对话上）；旧桶的 `open` 不再出现 |
| TV-11 | `compareIso("2026-09-25T10:00:00Z", "2026-09-25T10:00:00.000001Z") < 0`；`"…:00.5Z"` 与 `"…:00.500000Z"` 相等；不可解析 → 抛错 |
| TV-12 | `needsNativeConfirm`：`fetch`+approve → true；`tool_call`（`fields.tool: "browser"`）+approve → true；二者 + reject → false；`tool_call`（`write_episode_file`）、`checkpoint`、`memory_ack` + approve → false |

**TH：host 会话集成（vitest + 假 `protocol.py`，PR2）**

| 编号 | 场景 | 断言 |
|---|---|---|
| TH-1 | 懒启动 | `episode.activate`、`subscribe`、`refresh`、切期后 `SESSION_*` spawn 计数为 0；首个 `conv.send` 恰 spawn 1 次，stdin 第一行是 `user_message` 且在 `ready` 之后；随后第二次 `conv.send` 不再 spawn |
| TH-2 | 只读 stdout（H-9） | 假进程向 stderr 写一条合法的 `request` 帧、向 stdout 写一行非 JSON 与一条合法帧 → 打开请求集合为空、`framesLost == 1`、合法帧被处理；stderr 内容只出现在诊断里 |
| TH-3 | 答复绑定 | 未知 `requestId` → `E_STALE`，stdin 零新增行；`decision` 不在 `options` → `E_BAD_REQUEST`；对 `checkpoint` 附 `feedback` → `E_BAD_REQUEST`；`feedback` 为数字 → `E_BAD_REQUEST`；`feedback` 超 `FEEDBACK_MAX_BYTES` → `E_BAD_REQUEST`、零写入；合法答复 → stdin 恰新增 1 行，解析后等于 `{v:1,t:"answer",request_id,decision,feedback,rid}`；同一请求第二次答复在第一次未回之前 → `E_BUSY` |
| TH-4 | 进程退出作废（H-5） | 有 2 张打开的卡时假进程 `exit(1)`（另一变体为 SIGKILL）→ snapshot 的 `open` 为空、出现含两个 id 的 `voided_local`；之后对其答复 → **`E_STALE`**、零写入 |
| TH-5 | 不代发（H-8） | 假进程 10 个回合内持续发 `turn_finished`、`notice`、`stop_points`、`ready` 重复帧 → stdin 中 `user_message` 行数恰等于 `conv.send` 调用次数；回合运行中 `conv.send` → `E_BUSY`，零写入 |
| TH-6 | 密钥全链路 | 假钥匙串脚本输出标记串 `SK-MARKER-<随机>`：`SESSION_*` 子进程环境恰多一个键、值为标记串；其余模板环境不含它（TI-7 继续成立）；标记串**不出现**在 spawn 日志、`AVA_SPAWN` 行、诊断、任何发往 renderer 的消息、host stdout/stderr 捕获 |
| TH-7 | 密钥缺失与不合规 | 假钥匙串退 44；配置指名 `PATH`；值含空格；`PROBE_KEY_ENV` 输出空串 → 均不注入；会话照常 spawn；前三种的降级文案含以正确变量名拼出的 `security add-generic-password -s ava -a <名> -w`，第四种显示「未能读取 config/agent*.json 的 api_key_env」 |
| TH-8 | 中断 | 当前 `turnId` → stdin 恰 1 行 `interrupt`；`turn_finished` 之后用旧 `turnId` → `E_STALE`、零写入 |
| TH-9 | 结束与退出序列 | ① 空闲假进程收到 `shutdown` 即退 → 无信号；② **同时忽略 `shutdown` 与 SIGTERM** 的假进程（🔵-9）：`conv.end` 在 90 s（测试注入缩短的常量）后只有会话 pid 收到 SIGTERM、同组孙进程未收到；再过宽限期整组被 SIGKILL（孙进程消失）；③ 假进程收到 `shutdown` 后正常退出、但留下一个同组孙进程 → 退出后该孙进程被 SIGKILL；④ `stopAllForQuit`：空闲会话 stdin 收到 `shutdown`、未收到信号；忙会话（有回合或未答卡）未收到 `shutdown`、收到 SIGTERM；忽略 SIGTERM 的忙会话在 5 s 后整组被 SIGKILL |
| TH-10 | renderer 重置 | `resetRenderer` 后会话进程仍在（`kill -0` 成功），`conv.snapshot` 返回之前的全部条目与打开请求 |
| TH-11 | 租约被占（带帧） | 假进程发 `error{code:"E_SESSION_LOCKED"}` 并退 3 → `conv.send` 得 `E_SESSION_LOCKED`；stdin 中无 `user_message` |
| TH-11b | 租约被占（只退 3、不发帧，🔵-10） | `conv.send` 得 `E_SESSION_LOCKED`；退 4 → `E_UNREACHABLE`；退 2 → `E_SESSION` |
| TH-12 | 帧超限（🟡-8） | ① 一块内含「`SESSION_FRAME_MAX_BYTES + 1` 字节的完整合法 JSON 行 + 换行 + 合法帧」；② 超限行分多块到达（残段超限）→ 两种都 `framesLost == 1`、`frames_lost{reason:"oversize"}` 存在、超限行**未被处理**、后续合法帧照常处理 |
| TH-13 | 切换仓库互斥（S8-R10，🟡-11） | 有活会话时 `app.requestRepoRootChange` → `E_BUSY`，对话框桩调用次数 0；以测试钩子把对话框停在「已打开」（`choosing`）→ `conv.send`、`conv.resume`、`episode.create` 均 `E_BUSY`、零 spawn；`NEW_EPISODE` 在途时发切换 → 切换等它结束后才换 repoRoot（spawn 日志顺序可证） |
| TH-14 | 建期 | `episode.create{name}` 的 spawn argv 逐元素等于 `NEW_EPISODE` 模板；假 core 退 0 → 期列表含该 epKey；假 core 退 2 → `E_CORE` 且附 stderr 尾部；两种情况下 host 自身零写入（在只读夹具树上跑一次 TI-3a 同法断言） |
| TH-15 | 命令 | `scope` 带 `arg: "asset"` → stdin 1 行；`memory_ack` 带 `arg` → `E_BAD_REQUEST`；`name: "voice"` → `E_BAD_REQUEST`；以同 `rid` 的 `command_result` 解析 |
| TH-16 | 后台常驻与徽标 | A 回合运行中激活 B → A 进程存活、继续收帧；`episodes.summary` 中 A 的 `conv.running == true`、`openRequests` 随请求开关变化 |
| TH-17 | `rid` 关联（S9-R2） | 假进程先回一条不带 `rid` 的 `error`、再回同 `rid` 的 `turn_started` → `conv.send` 以 `turnId` 成功（无关错误只进流内提示） |
| TH-18 | 原生确认框（§2.4 第 5 层） | 确认框桩返回 `false`：批准抓取卡 → `E_STALE`、stdin 零新增、卡仍打开、桩调用 1 次且 `detail` 含该候选 URL；桩返回 `true` → 恰 1 行 `answer`；拒绝抓取卡、批准写入卡 → 桩调用 0 次；browser 卡同上；桩等待期间假进程退出 → `E_STALE`、零写入；（二轮 🔵-4）候选 URL 含 U+202E 与 U+200B → `detail` 中两处都显示为 `⟨U+202E⟩`、`⟨U+200B⟩`，首行注明「含 2 个不可见字符」；`why` 超 `CONFIRM_FREE_TEXT_MAX_CHARS` → 该段末尾有截断标记、URL 全文在；答复参数里多带一个伪造的 `url` 键 → `E_BAD_REQUEST`（exact-keys），`detail` 恒取自 host 保存的请求；（三轮 🔵-4）browser 卡的 `reason` 为 5 000 字符、含一行伪造的「URL: https://evil.example」、参数键序把 `url` 排在最后 → `detail` 中「目标：<真实 URL>」全文出现在「以下为模型填写的理由（未经核实）：」这一行**之前**，伪造行只出现在其后，`reason` 部分有截断标记 |
| TH-22 | 确认框不串号（二轮 🔵-5） | `reqId` 形如 `"<bootId>:<n>"`；以 main 侧桩模拟：旧 host 发出 `confirm-query` 后退出 → 桩收到 `AbortSignal` 的 abort；新 host 起来后收到一条旧 `bootId` 的 `confirm-result` → 忽略、零写入 |
| TH-19 | host 重启后的 generation（🟡-9） | 两个 `HostService` 实例（不同 `bootId`）对同一会话键各起一次会话 → `generation` 字符串不同 |
| TH-20 | host 侧回合结算（§2.7，二轮 🟡-2） | 注入单调时钟与对象库读取桩，逐步驱动：① `turn_finished` → 读取 → `stop_points` → 读取（其 `readStart` 早于 `stop_points` 到达、读完晚于它）→ 零 `settled`；再一次 `readStart` 更晚的读取 → 恰 1 条 `settled{timedOut:false}`，且本次读取的 `episode.delta` 在推送顺序上先于它；② `stop_points.items` 为空、对象库无变化 → 同样恰 1 条 `settled`；③ 期不活跃 → `stop_points` 到达即 `settled`；④ 进程退出无 `stop_points` → 退出后首次读取即结算；⑤ 不发 `stop_points` 的假进程 → `SETTLE_TIMEOUT_MS` 后 `settled{timedOut:true}` 且诊断有「回合结算超时」；⑥（三轮 🟡-1）`turn_finished` 与 `stop_points` 之间插一个 `log`（`[approvals] 05 待审批已更新：…`）和一个 `notice` → 照常结算；⑦（三轮 🔵-2；四轮 🔵-1 改为按 `turn_id`）回合 A 未结算时回合 B 的 `turn_finished` 到达 → 此后只可能出现 B 的 `settled`；随后先到一帧 `stop_points{turn_id: A}`（落在 B 的 `turn_finished` 之后）→ 不推进 B 的槽位、零 `settled`；再到 `stop_points{turn_id: B}` 与一次更晚的读取 → 恰 1 条 B 的 `settled`；另一变体：A 已超时结算后才到的 `stop_points{turn_id: A}` 被忽略、不产生第二条 `settled`；`stop_points{turn_id: null}`（`ready` 帧）从不推进结算；⑧（四轮 🔵-2）活跃期但 approvals 能力缺席、以及 `reach !== "ok"` 两个变体：`stop_points` 到达即 `settled`，读取桩调用次数为 0、诊断无「回合结算超时」。**除 ⑤ 外，①~④、⑥~⑧ 一律断言 `timedOut === false`，且时钟推进不超过 `SETTLE_TIMEOUT_MS` 的一半**（三轮 🟡-1：否则超时兜底会让时序缺陷蒙混过关） |
| TH-21 | 退出期间拒绝新请求（二轮 🔵-6） | `stopAllForQuit` 开始后 `conv.send`、`conv.answer`、`episode.create` 均 `E_BUSY`、零写入、零 spawn |

**TX：端到端（Playwright，未打包构建，PR3 用假 `protocol.py`、PR4 换真实 core + 本地假 LLM 端点；全部经 TF-2 的夹具）**

| 编号 | 场景 | 断言 |
|---|---|---|
| TX-0 | 契约（PR4，真实 `pipeline.agent.protocol`） | 一轮含只读工具、失败的 `run_pipeline`、工具卡的会话：host 收到的每一帧都通过 `parseOutFrame`；`framesLost == 0` |
| TX-1 | 失败原文与 H-2 | 界面上失败工具行展开的文本逐字节等于该期 `session.jsonl` 中对应 tool 消息的 `content`（判据 1：比对的是真实产物）；模型回复里含「[批准]」与一段伪造的 `answer` JSON 时，界面上除待答区卡片外没有任何批准按钮 |
| TX-2 | 显式点击 | 以 `page.evaluate(() => btn.click())` 合成点击工具卡「批准」→ 卡仍在、会话 stdin 无 `answer`；真实点击 → 恰 1 条 `answer`、工具被执行；对停机点卡「批准」同样合成点击 → 零 ack spawn、对象仍 pending。另：打开期、切期、重载窗口全过程 `answer`/`user_message` 写入与 ack spawn 计数为 0 |
| TX-3 | 拒绝附反馈 | 拒绝并填「改成第三人称」→ 假端点收到的下一次请求中该 tool 消息含原文；流内留痕显示「已拒绝（附反馈：改成第三人称）」 |
| TX-4 | 停止 | 慢端点上点「停止」→ 2 s 内出现收尾总结或本地说明，`turn_finished.stopped == "interrupted"` |
| TX-5 | 自动呼出与焦点 | ① 预览空时新 03.5 pending → 自动打开 `03-audio/`；② 先在产物树点开 `02-script.md` → 新 pending 只出「已就绪」条；③ 回合运行中连续产生两个 05 pending → 回合中预览不变、结算后打开且只打开一次；④ 每次自动呼出与出现「已就绪」条的**前后** `document.activeElement` 不变（🔵-8）；⑤（PR4，真实 heal 时序，一轮 🟡-7；二轮 🟡-2 第 5 点写明产出手段）夹具期处于 04（`ackFixtures` 同法造出 03.5 已批准的 `03-audio/`）；夹具副本里把 `pipeline/clips.py` 换成桩（同 `fakeCore` 做法：每次被执行写一版确定内容的 `04-clips.json`，版本号递增），**会话、协议、`run_pipeline`、H5 heal、Spec 9 回合末 `ensure_pending` 全部真实**；桩同时写出带版本标记的 `04-review.html`（05 的呼出目标，`stopPreview.ts`）。假 LLM 剧本在一个回合内两次调用 `run_pipeline("clips …")`，人以真实点击批准两张工具卡。**同步点**（四轮 🔵-4）：批准第一张卡后，测试先等 `approvals_store.json` 中出现钉住第一版指纹的 pending（由 host 的 H2/H5 自愈建出），并断言此时 `data-auto-open-count` 未变、`data-auto-open-approval-id` 不是它，**然后**才点第二张卡——保证第一版确实在回合中被 renderer 记入 `deferred`，否则 MUT-50 在这里抓不到（第一版若从未建出，结算即呼出与延迟呼出结果相同）。**观测口**（三轮 🔵-3：v1、v2 呼出的是同一个文件，从预览内容分辨不出）：预览区根元素带 `data-auto-open-approval-id`（最近一次自动呼出的对象）与 `data-auto-open-count`（本期自动呼出次数），均为生产代码的只读属性。断言：结算后 `data-auto-open-approval-id` 等于第二版 pending 的 `approval_id`（该对象钉住第二版 `04-clips.json` 的指纹），`data-auto-open-count` 恰增 1；host 诊断中无「回合结算超时」、该回合的 `settled.timedOut === false`；连续重跑 10 次结果一致 |
| TX-6 | 后台与隔离 | A 回合运行中切到 B → A 在期列表显示「运行中」；B 的对话流与待答区从不出现 A 的条目；切回 A 后条目按到达顺序完整 |
| TX-7 | 建期 | idea 会话里聊一轮；表单填 `../x` → 显示 core 原文、磁盘无变化；填合法名 → 目录与 `01-topic.md` 存在、该期被激活且工序条显示「01 选题」、对话区只有本地提示行、idea 会话仍活着（切回可见原对话） |
| TX-8 | 退出确认 | 有回合在跑时退出 → 确认框桩收到的列表含该期；取消 → app 与会话存活，**活跃期的事件轮询仍在继续**（取消后写一行事件，1 s 内时间线出现）；确认 → 会话 stdin 未收到 `shutdown`、会话收到 SIGTERM，8 s 内进程消失，`session.jsonl` 末尾该回合 `turn_end.wrapup == "skipped"` |
| TX-8b | host 不可用时退出（🟡-3） | ① 以测试钩子使 host 熔断（TI-9b 同法）后 `app.close()` → 5 s 内进程退出；② host 就绪但让它对 `quit-query` 不应答（测试钩子）→ `QUIT_QUERY_TIMEOUT_MS` 后退出 |
| TX-8c | 重入（🟡-3） | 确认框打开时再触发一次退出 → 确认框桩调用次数仍为 1，app 未退出 |
| TX-8d | 关窗后取消（二轮 🟡-3） | 有回合在跑时以 `BrowserWindow.close()` 关窗（走 `window-all-closed`），确认框桩返回「取消」→ app 与会话存活；随后 10 s 内 `AVA_SPAWN` 中活跃期的 `STATUS` spawn 仍按 `STATUS_REFRESH_MS`（5 s）周期出现（host 订阅在关窗时不清，可观测）；触发 `activate` 重开窗口 → 时间线与对话恢复显示 |
| TX-9 | 崩溃作废 | 卡片打开时 `kill -9` 会话 pid → 待答区该卡消失、流内显示「已作废（会话已结束）」 |
| TX-10 | 恢复 | 「继续上次会话」→ 出现历史分隔条与历史条目；随后发送一条消息正常得到回复 |
| TX-11 | H-10 | 批准一张工具卡后，时间线该事件显示「命令卡批准」 |
| TX-12 | 卡片按钮 | 检查点卡恰有「继续」「停止」两个按钮；`acquire_propose` 提案 2 条 → 待答区恰 2 张抓取卡、无批量按钮 |
| TX-13 | 原生确认框 | 点抓取卡「批准」→ 确认框桩被调用；桩返回「取消」→ 卡仍在、无 `answer`；返回「批准」→ 恰 1 条 `answer` |
| TX-14 | 焦点沿用（🟡-2 d） | 待答区有两张工具卡，Tab 聚焦第一张的「批准」、`keyboard.press("Enter")` 答复；随后再按一次 Enter → 第二张卡零 `answer`、仍打开 |
| TX-15 | host 重启（🟡-9） | 有打开卡时 `kill -9` host → 新 host 就绪后，原会话键显示「会话已结束（host 重启）」，待答区为空；点「继续上次会话」恢复历史 |

**TG：静态守卫（vitest，PR1 起）**

| 编号 | 断言 |
|---|---|
| TG-4′ | §2.4 第 1 层表中 `approval.decide`、`conv.answer` 的规则：只在 `HumanCards.tsx`；调用点最近的外层函数即 `onClick` 值函数；该函数体内无循环语句与迭代方法调用；首句为对第一个形参的 `nativeEvent.isTrusted` 检查；`onClick` 所在 JSX 元素的标签名为小写；每个处理器里表中方法的调用合计至多一处。检查器先用合成片段自测正反例（沿用 `guards.test.ts` 惯例），反例至少含：`useEffect` 里调用、`.map` 回调里调用、在 onClick 里定义具名函数再 `forEach` 调用（🟡-2 a）、首句检查的是闭包里的别的事件变量、挂在 `<Button onClick>` 上、一个处理器里连写两处 `rpc.call("conv.answer", …)`（二轮 🔵-2）；正例含改写后的 `StopPointCard` 批准处理器（二轮 🔵-1） |
| TG-10 | 同上规则施加于 `conv.send`、`conv.interrupt`（`Composer.tsx`，允许 `onKeyDown`）、`conv.command`、`conv.end`、`conv.resume`（`SessionHeader.tsx`）、`episode.create`（`NewEpisodeForm.tsx`） |
| TG-11 | `desktop/src/` 中属性名为 `t`、值为 `"user_message"`/`"answer"`/`"interrupt"`/`"command"`/`"shutdown"` 的对象字面量（即入站帧的构造点）只出现在 `host/sessions.ts`，且每种恰 1 处（AST 匹配属性赋值，不按裸字符串匹配：`host/index.ts:91` 的生命周期消息 `case "shutdown"` 不受影响） |
| TG-12 | 模板名 `KEYCHAIN_READ` 只出现在 `spawner.ts`（定义）与 `secrets.ts`（唯一调用） |
| TG-13 | 全 `renderer/` 中只有 `PreviewPane.tsx` 可 import `markdown-it`、使用 `dangerouslySetInnerHTML`（🔵-7） |
| TG-14 | `desktop/src` 不含字面量 `session.jsonl`（红线 3：桌面端不读会话记录） |
| TG-15 | `NewEpisodeForm.tsx` 中期名输入的初始状态为空串字面量（`useState("")`），且不接收任何来自对话的 props |
| TG-16 | （AST，二轮 🔵-3）`renderer/` 中每一个名为 `call` 的属性访问表达式都直接是一次调用的被调用者，且该调用的第一个实参是方法闭集（`shared/protocol.ts` 的 `METHODS`）中的字符串字面量；只豁免 `renderer/testHooks.ts`。反例：`rpc.call(m)`、`rpc.call.bind(rpc)`、`rpc.call.apply(rpc, a)`、`const c = rpc.call`、`rpc.call("no.such")` |
| TG-17 | `HumanCards.tsx` 中 `RequestCard`、`StopPointCard` 的每个渲染点都带 `key`，其表达式分别为 `…request_id`、`…approval_id` |
| TG-6 扩展 | 新钩子标识（假钥匙串路径、确认框桩、退出应答抑制、`choosing` 停留、`sendInputEvent` 点击中继）纳入 `HOOK_IDENT`，全部被 `!isPackaged` 守卫 |

### 7.2 变异检验矩阵

逐条实跑：植入 → 目标用例变红 → 还原，结果回填施工报告。

| 编号 | 变异 | 变红 | 机理（为何不会被别的断言掩盖） |
|---|---|---|---|
| MUT-1 | `episode.activate` 里顺手 `ensureSession()` | TH-1 | spawn 计数在首个 `conv.send` 之前已为 1 |
| MUT-2 | 把 stderr 也喂给 `LineSplitter` | TH-2 | stderr 里的 `request` 进入打开集合，「集合为空」失败 |
| MUT-3 | `answer` 不查打开集合 | TH-3 | 未知 `requestId` 也写了 stdin，「零新增行」失败（断言看 stdin 行数，不会被 core 的拒绝掩盖） |
| MUT-4 | 进程退出时不清空 `open` | TH-4、TX-9 | snapshot 仍含两张卡；v0.2 冻结顺序下答复越过打开集合检查、在「活进程存在」处得 `E_SESSION` 而非 `E_STALE`（v0.2 按 🟡-12 改正机理） |
| MUT-5 | 收到 `turn_finished` 后 host 自动续发一条 `user_message` | TH-5 | `user_message` 行数多于 `conv.send` 次数 |
| MUT-6 | 密钥写进诊断「已注入 <名>=<值>」 | TH-6 | 标记串出现在诊断 |
| MUT-7 | 全部模板都带密钥变量 | TH-6 | `STATUS` 等子进程环境含标记串（TI-7 同时变红） |
| MUT-8 | `keyFromSecurityStdout` 去掉全部尾随空白 | TV-8 | `"sk-abc\n\n"` 返回 `sk-abc` 而非 null |
| MUT-9 | `validKeyEnvName` 只查大写 | TV-9 | `PATH`、`NODE_OPTIONS` 通过 |
| MUT-10 | `interrupt` 不核对 `turnId` | TH-8 | 旧 `turnId` 也写了一行 |
| MUT-11 | 结束序列 SIGTERM 发给整组 | TH-9 ② | 孙进程在 SIGTERM 阶段就收到信号 |
| MUT-12 | 结束序列省掉 SIGKILL | TH-9 ② | 假进程忽略 SIGTERM 不会自行退出，「pid 退出后清理整组」永远不触发；孙进程在宽限期后仍存活（v0.2 按 🔵-9 改夹具） |
| MUT-13 | `resetRenderer` 顺带结束全部会话 | TH-10 | `kill -0` 失败 |
| MUT-14 | 退出码 3 映射成 `E_SESSION` | TH-11b | 没有 `error` 帧可依赖，只能按退出码映射（v0.2 按 🔵-10 改挂） |
| MUT-15 | 会话读端去掉完整行的长度检查（只靠 `LineSplitter`） | TH-12 ① | 块内完整的超限行被照收并处理（v0.2 按 🟡-8 改写：v0.1 的「整会话判死」变异依附在必红用例上） |
| MUT-16 | repoRoot 切换不看活会话 | TH-13 | 对话框桩被调用 |
| MUT-17 | host 在 `episode.create` 失败时 `mkdirSync` 兜底 | TG-2、TH-14 | 静态扫描命中；只读夹具树清单变化 |
| MUT-18 | 删去 `create_new_episode` 第 1 步的 `require_data_at` 检查（v0.2 按 🟡-5 改写：v0.1 的「恢复 `parents=True`」被第 1 步掩盖，永远走不到） | TY-1 | （v0.3 按二轮 🔵-11 改正机理）删掉后先起作用的是「`data/episodes/` 须已是目录」那道检查，退出码仍是 2；变红靠的是 TY-1 对 stderr **原文**的断言（缺席、悬空两场景不再出现「不存在。先建存储骨架」「是悬空的符号链接」）与「`data/library` 缺席、`data/episodes` 存在」场景（此时会建成，退 0 而非 2） |
| MUT-19 | 删除名字规则中的 `/` 检查 | TY-2 | `a/b` 在 `parents=False` 下因中间目录不存在抛错，退出码不是 2 |
| MUT-20 | 删除 `_`、`.`、`-` 前缀检查 | TY-2 | `_x`、`-x` 退 0 |
| MUT-21 | `api_key_env_name` 顺手读 `os.environ` 判断有无 | TY-5 | 访问即抛的映射触发异常 |
| MUT-22 | `approval.decide` 的 `onClick` 去掉 `isTrusted` 首句 | TG-4′、TX-2 | 静态形状不符；合成点击产生 ack spawn |
| MUT-23 | 待答区加「全部批准」按钮（`.map` 里调 `conv.answer`） | TG-4′ | 处理器体内出现迭代方法 |
| MUT-23b | 「全部批准」写成 onClick 内定义 `const a = (id) => rpc.call("conv.answer", …)` 再 `ids.forEach(a)`（🟡-2 a） | TG-4′ | 调用点最近的外层函数是 `a` 而不是 onClick；且 onClick 体内有 `.forEach` |
| MUT-24 | `conv.answer` 挪进 `ConversationPane.tsx` 的 `useEffect` | TG-4′、TX-2 | 文件与外层函数不符；无点击出现 `answer` |
| MUT-25 | 助手回复改用 markdown-it 渲染 | TG-13 | import 出现在 `PreviewPane.tsx` 之外 |
| MUT-26 | 组件从 `assistant` 文本里识别「批准」生成按钮 | TX-1 | 界面出现待答区之外的批准按钮 |
| MUT-27 | `autoOpen` 忽略 `owner`（总是替换） | TV-6 ②、TX-5 ② | 应为 strip 却 open |
| MUT-28 | `autoOpen` 忽略回合运行状态 | TV-6 ④、TX-5 ③ | 回合中已 open |
| MUT-29 | 结算后按到达顺序呼出全部 deferred | TV-6 ④⑤ | open 次数 2 而非 1 |
| MUT-30 | 自动呼出后调用 `previewRef.focus()` | TX-5 ④ | 呼出前后 `activeElement` 不同 |
| MUT-31 | `convStore` 不按会话键分桶 | TV-4、TX-6 | B 的桶含 A 的 `request_id` |
| MUT-32 | `foldConv` 把失败的 `observation` 截断到 500 字 | TV-2、TX-1 | 逐字节比较失败 |
| MUT-33 | 时间线恢复「无 approval_id 一律拒执」 | TV-7、TX-11 | 标签不等 |
| MUT-34 | 建期表单以对话里最后一个「期名：」预填 | TG-15 | 初值不是空串字面量 |
| MUT-35 | 建期成功后 host 把 idea 讨论作为首条 `user_message` 发给新期 | TX-7、TH-5 同法计数 | 新期对话区出现消息；`user_message` 行数多于人的发送次数 |
| MUT-36 | host 用普通 `JSON.stringify` 写帧 | TG-9（Spec 8 既有） | 静态扫描命中 |
| MUT-37 | 忽略 `rid`，按到达顺序把第一个 `error` 归给在途的 `conv.send` | TH-17 | `conv.send` 被无关错误拒绝 |
| MUT-38 | `SESSION_*` 改为 stdin `ignore` | TH-1 | 无法写入 `user_message`，用例超时失败 |
| MUT-39 | 退出时不弹确认 | TX-8 | 确认框桩调用次数 0 |
| MUT-40 | `desktop/src` 读取 `session.jsonl` 恢复对话 | TG-14 | 字面量命中 |
| MUT-41 | 会话 pid 退出后不再检查、清理其进程组 | TH-9 ③ | 正常退出后同组孙进程仍存活（②④ 走的是超时路径，末尾本来就有整组 SIGKILL，掩盖不了也证明不了这一条） |
| MUT-42 | 假会话夹具改回「写 `<tmp>/.venv/bin/python` 替身」（🔴-1） | TF-1 | `fixtureWrite` 的 realpath 守卫抛错（**只以 TF-1 的受控调用实跑**，不对真实 `.venv` 植入写操作） |
| MUT-43 | 抓取卡批准跳过原生确认框 | TH-18、TX-13 | 桩返回 `false` 时仍写了 `answer` |
| MUT-44 | renderer 新增 `const m = "conv.answer"; rpc.call(m, …)` | TG-16 | 首参不是字面量；且 TG-4′ 因字面量不在调用处而无法覆盖，只有 TG-16 能抓 |
| MUT-45 | `onClick` 挂到自定义组件 `<CardButton onClick>` 上 | TG-4′ | 标签名非小写 |
| MUT-46 | `RequestCard` 的 `key` 改为数组下标 | TG-17、TX-14b | 静态 key 表达式不符；第一张卡关闭后第二张卡复用其组件实例，继承人在第一张卡里写的反馈（N34 更正：原写的「第二次 Enter 答到第二张卡」不可观测——答复即 `disabled`、焦点离开，第二次 Enter 无论 key 对错都落空，TX-14 因此杀不死它） |
| MUT-47 | `before-quit` 无条件等 `quit-state`（去掉 host 缺席与超时分支） | TX-8b | 熔断后 `app.close()` 5 s 内不退出 |
| MUT-48 | `window-all-closed` 恢复「先发 `shutdown` 再 `app.quit()`」 | TX-8d | 关窗后取消，host 定时器已停，`STATUS` spawn 不再出现（v0.3 按二轮 🟡-3 改挂：TX-8 走 `before-quit`、不经过 `window-all-closed`，抓不到它） |
| MUT-49 | 去掉退出状态机的重入保护 | TX-8c | 确认框桩被调用 2 次 |
| MUT-50 | host 在 `turn_finished` 到达即追加 `settled`（v0.1 语义） | TH-20 ①、TX-5 ⑤ | ① 中第一次读取之前就出现 `settled`；⑤ 中以 v1 结算、v2 再呼出，「恰 1 次」「第二版」失败 |
| MUT-51 | `generation` 去掉 `hostBootId` | TV-10、TH-19 | 新 host 的 `"3"` 与旧值相同，delta 被拼到旧对话上；两实例 generation 相等 |
| MUT-52 | `choosing` 期间不拦 `conv.send` | TH-13 | 对话框开着时起了会话 |
| MUT-53 | C10-R3 的正则用 `\s*`（可跨行） | TY-8 ⑤ | `类型：` 吃到下一行的 `模式：立论`，判为已填 |
| MUT-54 | C10-R3 改用严格规则（类型须属题材表） | TY-8 ③ | 5 个非标准夹具的 status 输出变化 |
| MUT-55 | `autoOpen` 用字符串比较 `created_at` | TV-11、TV-6 | 微秒为 0 的时间戳被判为更新 |
| MUT-56 | 会话夹具不改写 `config/agent.local.json` | TF-2 | `base_url` 断言失败，用例在出网前停下 |
| MUT-57 | 原生确认框对拒绝也弹 | TH-18 | 拒绝抓取卡时桩调用次数不为 0 |
| MUT-58 | 待答区「批准」处理器里展开连写两处 `rpc.call("conv.answer", …)`（二轮 🔵-2） | TG-4′ | 同一处理器内调用超过一处 |
| MUT-59 | host 只在 `stop_points.items` 非空时推进结算（模拟 S9-R4 未补全的实现） | TH-20 ②、TV-6 ⑩ | 零停机点回合永不结算，超时前没有 `settled`；renderer 侧 ⑩ 中新 pending 被延迟 |
| MUT-60 | 结算以读取**完成**时刻比较 | TH-20 ① | 那次「`stop_points` 之前开始、之后读完」的读取被当成结算依据，第一次读取后即出现 `settled` |
| MUT-61 | 删去结算超时兜底 | TH-20 ⑤ | 不发 `stop_points` 的假进程下永无 `settled` |
| MUT-62 | `onConnect` 不重置 `autoOpen` | —（SURVIVED：成功路径不可观测，失败路径无用例；N34 评审把「等价」更正为此） | N34 判定：重连后 `fetchConv(当前会话键, resetAuto=true)` 以新 host 的 snapshot 再做同一次 reset，两次之间没有可达的 pendings 事件，故不可观测；只有 `episodes.list`/`conv.snapshot` 失败时 onConnect 的同步 reset 才起作用 |
| MUT-62′（N34 补） | onConnect 与 `fetchConv` 两处 reset 一起去掉 | TX-15b | 旧 `awaiting` 残留，重连后的新 pending 只登记不呼出 |
| MUT-63 | `renderConfirmDetail` 不转义 `\p{Cf}` | TH-18 | `detail` 含原始 U+202E，断言的 `⟨U+202E⟩` 不存在 |
| MUT-64 | `reqId` 去掉 `bootId`、host 退出时不 abort | TH-22 | 桩未收到 abort；旧 `confirm-result` 被新 host 接受 |
| MUT-65 | `quit-proceed` 后仍接受 `conv.send` | TH-21 | 退出期间写入了 `user_message` |
| MUT-66 | `fixtureWrite` 只对父目录 `realpath`、不 `lstat` 目标 | TF-1 | 经符号链接写穿，根外文件内容改变（在临时目录里受控实跑） |
| MUT-67 | host 要求 `stop_points` 紧跟 `turn_finished`（中间出现其他帧即不推进，三轮 🟡-1） | TH-20 ⑥、TX-5 ⑤ | ⑥ 只能靠超时结算，`timedOut === false` 断言失败；⑤ 中回合新建了停机点、`[approvals]` 行落在两帧之间，同样超时 |
| MUT-68 | R6 在 `onConnect` 时把 `awaiting` 直接清空、不从 snapshot 重建 | TV-6 ⑪(b) | 重载后回合中的中间版本被立即呼出 |
| MUT-69 | `renderConfirmDetail` 把 browser 的 `args` 整段 JSON 转储后截断 | TH-18 | 键序把 `url` 排到最后时，真实 URL 被截掉，「目标」行不存在；伪造的「URL:」行出现在理由标注之前 |
| MUT-70 | 每个会话键允许多个待结算槽位 | TH-20 ⑦ | A 的迟到 `stop_points` 又产生一条 `settled` |
| MUT-71 | host 不看 `stop_points.turn_id`，认领 `turn_finished` 之后的第一帧（v0.4 语义，四轮 🔵-1） | TH-20 ⑦ | 落在 B 的 `turn_finished` 之后的 `stop_points{turn_id: A}` 推进了 B 的槽位，B 在收到自己的 `stop_points` 之前就结算 |
| MUT-72 | approvals 能力缺席或不可达时仍等对象库读取（四轮 🔵-2） | TH-20 ⑧ | 读取从不发生，只能靠超时结算，`timedOut === false` 断言失败 |
| MUT-73 | R6 从 `entries` 扫描最后一个 `turn_started` 重建 `awaiting`（v0.4 语义，四轮 🔵-3） | TV-6 ⑪(d) | 截头后找不到 `turn_started`，`awaiting` 为 null，中间版本被立即呼出 |

---

## 8. PR 划分

| PR | 内容 | 测试 | 前置 |
|---|---|---|---|
| **PR0**（Python） | C10-R1（含 `paths.require_data_at` 与 **4 个**用例改写，E11）、C10-R2、C10-R3、C10-R4；TC-1 探针追加 `pipeline.agent.llm` | TY-1~TY-9（`protocol` 探针除外）；MUT-18~21、53、54 | §6.1 授权 + 红队 🟢 |
| **PR1**（desktop，不依赖 Spec 9） | 版面重排（产物树左移）；`HumanCards.tsx`（`DecisionBar.tsx` 搬入）；`isTrusted` 首句；TG-4′/TG-13/TG-14/TG-15/TG-16/TG-17；S8-R9 标签修复；`isoTime.ts` 与 S8-R12；`autoOpen.ts` 接入停机点（回合状态恒为「未运行」直到 PR3）；**提前落地 S8-R2 中的 `episode.create` 一项**（方法、`NEW_EPISODE` 模板、`NewEpisodeForm.tsx`，先挂在左栏「＋ 新建一期」；🔵-11）与 S8-R10 中「`episode.create` 受 `choosing`/`switching` 约束、`NEW_EPISODE` 计入在途」两条 | TV-6（①~⑦）、TV-7、TV-11、TG-*、TH-14；`e2e/ack.spec.ts` 零改动全绿 | PR0 |
| **PR2**（host 会话管理，对假 `protocol.py`） | 其余 S8-R2 方法、`sessions.ts`、`secrets.ts`、`spawner.ts` 增补、`convFrames.ts`、`convFold.ts`、`nativeConfirm.ts`、生命周期消息（含确认框）、S8-R10 其余部分、退出序列（host 侧）；夹具 `fixtureWrite` 与会话类夹具 | TF-1~TF-3、TV-1~TV-5、TV-8~TV-10、TV-12、TH-1~TH-22 | PR1；S9-R1/R2/R4 已授权（按其文字实现，真实 core 待 PR4） |
| **PR3**（renderer 会话界面与 main 退出流程，对假 `protocol.py`） | `ConversationPane.tsx`、`Composer.tsx`、`SessionHeader.tsx`、待答区、idea 视图、徽标、`onConnect` 重置；main 退出状态机与确认框 | TV-6 ⑧~⑪、TX-1~TX-4、TX-5 ①~④、TX-6~TX-15（含 TX-8d）的假会话版本；TG-10/TG-11/TG-12 | PR2 |
| **PR4**（真实 core） | 换真实 `pipeline.agent.protocol` 与本地假 LLM 端点重跑 TX 全表；TX-0、TX-5 ⑤；TC-1 追加 `pipeline.agent.protocol`；A5、A6、A8 实测回填；MUT 全表实跑 | TX-0~TX-15、MUT-1~73 | **Spec 9 PR3 已施工且其 S9-R1/R2/R4 用例全绿**；Spec 9 已按 S9-R3 修订 H-6 |
| **PR5**（打包版手验与收口） | 打包版上手验：钥匙串读取（A4）、一次完整回合、原生确认框、退出确认、输入法选词中按 Enter 不发送（A2 后半）；文档：`desktop/` 相关说明一行写明钥匙串命令与「只存密钥原文」（只许瘦身不许膨胀） | 门禁 12 | PR4 |

---

### 8.1 PR0 / PR1 施工回填（2026-09-26）

**PR0（Python）**：`uv run pytest` **1938 passed**（改动前 1899）；TY-1~TY-9 落为 `tests/test_spec10_episode_create.py`（4 个既有用例按 C10-R1 同源语义改写：`test_create_new_episode_and_reject_duplicates` 改由 spec §6.1 点名的 3 个 + 实测新增的第 4 个 `test_full_chain_smoke_in_temp_repo`）；TC-1 探针追加 `pipeline.agent.llm`，泄漏清单 `[]`。变异 **S10-MUT-18/19/20/21/53/54 全部杀死**（走 `scripts/verify_mutations.py`：红条数 4 / 3 / 6 / 2 / 2 / 30；前四条编号与 Spec 9 的 MUT-矩阵撞号，故加 `S10-` 前缀）。**另一处既有影响**：`scripts/verify_mutations.py` 的 M23 锚点随 `ava new` 分派改写同步更新（未更新会让「锚点逐字命中」自检变红）。

**PR1（desktop）**：`npx vitest run` **191 passed**；`npx playwright test` **45 passed / 1 skipped**（`e2e/ack.spec.ts` 与 `e2e/preview*.spec.ts` **零改动**，`e2e/visual.spec.ts` 新增 VE-1/VE-3 属 Spec 14 的单子）；`npx tsc --noEmit` 干净。TV-6①~⑦、TV-7、TV-11、TG-4′/TG-10/TG-13~TG-17、TH-14 全绿；另补 09 定稿的 `plan`/argv 形状断言（Spec 12 S8-R19 的接口，`tests/host/decide.test.ts`、`tests/host/spawner.test.ts`）。

**Spec 12 的 S10-R1（09 卡两输入）**：**已做完，不需迁移义务**——`HumanCards.tsx` 的 09 卡含封面选择器（`tree.list` 直读 `07-cover/`，按文件名排序、不排名）与标题输入，两者非空才可点批准；`approval.decide` 增可选 `cover`/`title`（承载 `finalize`），`APPROVE` 模板 09 变体按空格固定位置追加四个独立 argv 元素；`contracts.ts` 的 `ApprovalRecord`/`ApprovalJson` 增 `finalize` 形状并在 `parseApproval` 里校验（`cover_mtime_ns` 只收十进制字符串）。Spec 12 PR3 只剩 `IMPORT_COVER` 模板与导入 UI（拖放区）。

**归属变异实跑（PR1 相关，逐条植入 → 目标用例红 → 逐字节还原）**：MUT-22 / 23b / 45（TG-4′，各 2 条红）、MUT-25（TG-13，1）、MUT-44（TG-16，2）、MUT-34（TG-15，1）、MUT-33（TV-7，1）、MUT-17（TG-2，1）、MUT-27（TV-6 ②，3）。**MUT-55 首次实跑存活**（TV-6 的夹具里 created_at 的字符串序恰好等于数值序），已补一条「微秒恰为 0」的用例（`…:00Z` 对 `…:00.000001Z`），复跑红 1 条。

**验收结论（2026-09-26）**：独立评审复跑一致、四条发现全部关闭，**PR0 / PR1 范围判为通过**（Spec 10 门禁 2~7、11 的对应项 ✅）；**PR2 起不在本里程碑**，下一段从 PR2（host 会话管理，对假 `protocol.py`）起手。

**验收回改（2026-09-26，独立评审）**：① 🟡 `llm.py` 双读配置——改为 `_load_agent_cfg` 一次读入后由 `_env_name_of(data)` 取名字（`api_key_env_name` 与 `load_llm_config` 共用这一段），消除「两次读取之间配置被改」与多余 IO；`_warn_once` 的去重键恢复为 `local_cfg`（与 HEAD 逐字一致）；新增 `test_ty5_config_read_once` 钉住「只读一次」（`uv run pytest` 1939 passed）。此处与 C10-R2 的字面「load_llm_config 改为调用它取名字」略有出入（改为调用同段规则），理由即评审所指的双读缺陷——行为与可观察输出不变。② Spec 12 §8 的 PR3 行已同步标注 S8-R19 的 09 卡部分由本 spec S10-R1 落地，防做两遍。③ `test_tp6_busy_and_bad_frames` 全量负载下偶发红已登记为 `D31`。

**残余风险（如实登记）**：S8-R12 把 `heal.ts:latestPerStop` 改成 `compareIso` 后，`approvals_store.json` 里若出现不可解析的 `created_at`，比较会抛错（`compareIso` 契约如此，TV-11 钉住）；core 的 `_utc_now_iso()` 恒为可解析形状，故只可能由外部改坏的对象库触发，届时表现为 host 熔断而非静默按字符串比较。

**待办（不在本单）**：`docs/dev/plans/README.md` 的 Spec 10 状态行未动（PR2/PR3/PR4 未完，等 Spec 10 全绿再更新）。


### 8.2 PR2 施工回填（2026-09-27）

**交付物**（对假 `protocol.py`，Spec 9 未施工）：

- `shared/convFrames.ts`（§3.2 出站帧校验，14 类闭集 + 安全整数 + 多键忽略）、`shared/convFold.ts`（§2.3 帧 → 流 + `convSummary`）、`shared/secretsRules.ts`（TV-8/9）、`shared/nativeConfirm.ts`（§2.4 第 5 层触发与正文）、`shared/isoTime.ts`（PR1 已有）。
- `host/sessions.ts`（§4.2 `SessionManager`：懒启动、后台常驻、只读 stdout、`rid` 关联、`turn_id` 精确实时结算、结束/退出序列、进程组清理）、`host/secrets.ts`、`host/confirm.ts`（reqId `<bootId>:<n>` 的 host 侧端点）、`host/spawner.ts` 增补（`SESSION_NEW/CONTINUE/IDEA`、`PROBE_KEY_ENV`、`KEYCHAIN_READ`、`spawnSession`/`killGroup`/`groupAlive`）、`host/service.ts` 接线（会话方法、`readStart` 结算、repoRoot 切换互斥、建期后的 `episodes.summary` 推送）、`host/index.ts`（quit-query/quit-proceed/confirm-query/sessions-down、`bootId`、`--ava-keychain` 测试钩子）、`shared/protocol.ts` / `shared/lifecycle.ts` / `shared/constants.ts` 增量。
- 夹具：`tests/fixtures/session.ts`（`fixtureWrite` 逐级 `lstat`+`realpath` 守卫、假 `protocol.py` 剧本解释器、按会话键分文件、不出网配置改写、假钥匙串）、`e2e/sessionFixtures.ts`（复用同一份夹具 + 退出/确认框桩）。

**测试**：`npx vitest run` **285 passed**（PR1 后 191）。新增 `tests/shared/{convFrames,convFold,convStore,secretsRules,nativeConfirm}.test.ts`、`tests/host/{sessions,settle,confirm,sessionFixtures}.test.ts`；TF-1~3、TV-1~5/8~10/12、TH-1~TH-22 全绿。

**PR2 范围外登记**：TH-20 的"结算由 service 侧的活跃期读取驱动"另有一条集成用例（`sessions.test.ts` 末条），其余 ①~⑧ 在 `settle.test.ts` 用注入时钟与假 `SessionProc` 逐条驱动。

**施工偏差（如实登记）**：

1. `ConvSnapshot`/`ConvDelta` 增 `keyProblem: string | null`（§2.9 第 6 条要求会话头部显示密钥未就绪的**命令**；§3.1 原结构无此字段，host 无处安放）。其余字段与 §3.1 逐字一致。
2. `SessionDeps` 比 §4.2 多四项：`repoRoot`（spawn 的 cwd）、`isActive`、`canReadApprovals`（§2.7 第 4 条的两种即时结算）、`killGroup`/`groupAlive`/`changed`（进程组回收与徽标重推）。§4.2 只列了 `blocked`，但这四项无法从 `blocked` 推出。
3. `HostDeps` 增 `confirm`、`bootId`、`sessionTiming`、`resolveSessionKey`（测试注入密钥解析，避免真钥匙串）。
4. `host/confirm.ts` 与 `main/confirm.ts` 是把 §3.3 的确认框两端拆成可单测模块（TH-22）；`main/index.ts` 用注入的 `dialog` 接线。
5. 假 `protocol.py` 的 `reply` 指令用 `subst`（与 `serve` 同一替换规则）；首版用键 `$rid` 拔键，脚本里的 `rid:"$rid"` 是**值**而非键，导致 rid 未被替换——TF/TH 用例在 8.2 期间逮到，已改。
6. `createEpisode` 成功后追加一次 `episodes.summary` 推送：否则新期只在 30 s 周期后才出现在左栏（TX-7 实测）。

### 8.3 PR3 施工回填（2026-09-27）

**交付物**（对假 `protocol.py`）：

- `renderer/ConversationPane.tsx`（§2.3 行渲染；失败 observation 默认展开、逐字）、`renderer/Composer.tsx`（唯一 `conv.send`/`conv.interrupt`）、`renderer/SessionHeader.tsx`（唯一 `conv.command`/`conv.end`/`conv.resume`；scope/LLM/素材模式/确认记忆/建期）、`renderer/convStore.ts`（§3.6 纯 reducer + `reset-convs`）、`renderer/HumanCards.tsx`（新增 `RequestCard`；`conv.answer` 与 `approval.decide` 同文件）、`renderer/NewEpisodeForm.tsx`（唯一 `episode.create`，未改语义）、`renderer/App.tsx`（中栏 = 工序条 + 会话头 + 对话流 + 待答区 + 输入框；左栏「选题」入口与「运行中 / N 张卡待答」徽标；`onConnect` 清空会话桶并按新 host 的 `episodes.summary` 重取快照；R6 从 snapshot 顶层字段重建 `awaiting`；`turn_started`/`settled` 驱动 autoOpen）、`renderer/style.css`（`.conv-shell`/`.conv-notice` 等业务区块，只引用 §3.1 变量）。
- `main/index.ts` 退出状态机（`idle/querying/confirming/stopping`、重入保护、`QUIT_QUERY_TIMEOUT_MS`/`QUIT_STOP_TIMEOUT_MS` 兜底、`window-all-closed` 只调 `app.quit()`）、退出确认与原生确认框接线、`dialog` 桩（`__avaTestQuit` 含 `hold`）+ `__avaTestQuitRelease`。
- 静态守卫：`tests/static/scan.ts` 新增 `inboundFrameOwners`（TG-11）与 `keychainTemplateViolations`（TG-12）；`guards.test.ts` 的 TG-4′/TG-10 去掉「PR3 才落地」豁免，新增 TG-11/12。

**测试**：`TV-6 ⑧~⑪`（`tests/renderer/autoOpen.test.ts`）、`TG-10/11/12`（`guards.test.ts`）、`e2e/session.spec.ts` **19 passed**（TX-1~TX-11、TX-12~TX-15、TX-8b/8c/8d 的假会话版本）。全套未打包 e2e：**64 passed / 1 skipped**（VE-0 为 Spec 14 既有跳过项），`e2e/ack.spec.ts` 零改动全绿。

**施工偏差（如实登记）**：

1. Idea 视图的「建期…」同时挂在左栏与 `SessionHeader`（§2.5 要求头部常驻、左栏入口是同一表单）；两处都渲染 `NewEpisodeForm`，左栏那份用 `data-testid` 定位。
2. `TV-6 ④` 的 activeElement 断言只覆盖「出现『已就绪』条」的前后（自动呼出由人点期行触发，点击本身会移焦，无法把点击排除在观测窗口外）。
3. `TX-5 ①` 用 H1 自愈真实建出的 03.5 待审对象（夹具期停在 03.5），不写死 approval_id；②的候选对象钉住真实 `mtime_ns`，避免 H5 自愈把对象 supersede。
4. ~~`TX-8b②`（host 就绪但不应答 `quit-query`）未单独造钩子，以「host 不在 / 未就绪 → 直接退出」的等价路径覆盖。~~ **该偏差已在 §8.4 取消**：新增 host 侧 `pauseAt("quit-query")` 钩子，`TX-8b②` 真跑 `QUIT_QUERY_TIMEOUT_MS` 兜底分支（M7 F-2）。
5. `TX-5 ⑤`、`TX-0`、真实 `session.jsonl` 逐字比对属 PR4（需真实 `pipeline.agent.protocol`），未做。

**变异实跑（抽验，逐条植入 → 目标用例变红 → 还原）**：MUT-3（answer 不查打开集合）→ TH-3 红 1；MUT-15（去掉完整行长度检查）→ TH-12 ① 红 1；MUT-43（跳过原生确认框）→ TH-18 红 5；MUT-66（`fixtureWrite` 不 lstat 目标）→ TF-1 红 3。§7.2 其余条目按 §8 的 PR 划分留在 PR4（需要真实 core 或跨进程时序）。

**残余风险**：`conv.answer` 的 in-flight 记录与 `answered_local` 的 feedback 取自 host 自己写下的值（core 的 `request_closed` 不带 feedback，S9-R2 只回显 rid）——若 core 拒绝了答复却仍发 `request_closed{reason:"answered"}`，host 的留痕会与事实不符；真实 core 由 PR4 的 TX-0 契约用例覆盖。

### 8.4 M7 验收回改（2026-09-27）

M7 验收结论为「有条件通过」，四条发现全部处置如下（F-5 按验收意见登记不改；F-6 为正向确认）。

| # | 处置 | 落点 | 新测试 |
|---|---|---|---|
| F-1 🟡 | `before-quit` 不再只拦 `querying`/`confirming`，`stopping` 期间一样 `preventDefault`；收尾只由 `sessions-down` 或 `QUIT_STOP_TIMEOUT_MS` 决定 | `main/index.ts` | `TX-8e`：host 停在 `quit-proceed` 钩子上（stopping），第二次 `app.quit()` 必须被拦下（app 与 host 仍活），放行后才退 |
| F-2 🟡 | 新增 host 侧 `pauseAt("quit-query")` 钩子，真跑「就绪但不应答」分支 | `host/index.ts` | `TX-8b②`：arm 后退出，耗时 ≥ 2 s（兜底）且 < 8 s，走不到确认框 |
| F-3 🟡 | 钥匙串 `argv` 逐位钉住；补真实 `/usr/bin/security` 查不存在账户 → 退 44 的冒烟（离线、不碰真实条目） | `tests/host/spawner.test.ts` | 「M7 F-3 钥匙串读取」两条 |
| F-4 🔵 | host exit 处理器把 `stopping` 一并短路（原先只短路 querying/confirming，会白拉一个新 host 再被兜底带走） | `main/index.ts` | `TX-8f`：stopping 中 SIGKILL host → < 5 s 退出 |
| F-5 🔵 | 接受：TH-6 以 `spawnLog`/`pushes`/`diagnostics` 三处断言替代「host stdout 捕获」逐字落实，功能等价 | — | — |

**回改变异实跑**：F-1 回退（stopping 不 preventDefault）→ `TX-8e` 红；F-2 回退（删 `quitQueryTimer`）→ `TX-8b②` 红；F-4 回退（stopping 不短路）→ `TX-8f` 红（11.3 s，即看护重启 + 10 s 兜底）。

**回归**：`npx vitest run` 287 passed（27 文件，F-3 新增两条）；全套未打包 e2e 67 passed / 1 skipped（`e2e/session.spec.ts` 22 passed，新增 TX-8b②/8e/8f）；`tsc --noEmit` 干净。

### 8.5 PR4 施工回填（M9，2026-09-27）

**做了什么**：`e2e/sessionReal.spec.ts` 用真实 `pipeline.agent.protocol` + 本地假 LLM 端点（`e2e/fakeLlm.ts`：OpenAI 兼容；`tool` 配对不齐回 400；剧本里任何非 `127.0.0.1` 的 URL 加载即拒——真实 core 会真的执行被批准的工具）重写 TX-0~TX-15 全表，另加 TX-5 ⑤、A5、A8。夹具 `sessionRepo` 增 `llmUrl`：给出时不写假 `protocol.py`，副本即复制来的真实 core，并自检与仓库逐字节相同；TF-1/2/3 守卫原样覆盖。TC-1 探针追加 `pipeline.agent.protocol`（TY-7，`7e302cd`），泄漏清单 `[]`。

**结果**：
- **TX-0**：真实 core 每类帧的键集合与 `convFrames.REQUIRED`/`CONDITIONAL` 双向比对（为此导出两表，内容不变）——期望值只有一份，不在两侧各写对拍；`framesLost == 0`。
- **真实 core 版 TX 26 条全绿**；TX-5 ⑤ 连续 10 次一致（`settled.timedOut === false`，无「回合结算超时」诊断）。
- **首次联调暴露 core 侧 5 处协议不符**（见 A7），按 RF-8 停下报告、经人批准在 core 修，TS 侧零适配；Spec 9 补 TP-6b/TP-16 与 MUT-61/62。
- A5 / A6 / A8 见 §0 假设表。
- **门禁 10**：`vitest` 全绿；未打包 e2e 全量 100 过 / 1 跳过（负载下 TA-12 红过一次、单跑 3/3 绿，登记 issues D32）；打包构建（`26c9fbc`，`desktopDirty=false`）上 `e2e-packaged` 11/11（TS-8 夹具在 desktop/ 干净时 `git commit` 退 1，加 `--allow-empty` 修，`26c9fbc`）。

**施工偏差（如实登记）**：
1. **TX-12 / TX-14 的「同时两张卡」改为「逐张出现」**：真实 core 同一会话的人审卡串行打开（上一张答完才会有下一张），spec 原文措辞预设了并发。并发两卡的场景只剩假进程版 TX-14 能造（见下 MUT-46）。
2. TX-12b/13 的候选 `type` 用 `mv`（`video` 不是合法类型），第二张卡按 `request_id` 定位。
3. 假进程版 TX-1/TX-8 断言比 spec 窄（TX-1 只在待答区内数按钮、spec 要求整页；TX-8 不断言确认框桩被调用），MUT-26/39 在其下存活 → 目标改指真实 core 版（断言按 spec 全写）；假进程版的缺口登记 issues N34。

**变异全表实跑**（逐条植入 → 目标用例变红 → 写回原文）：TS 侧 68 条由新 harness `desktop/scripts/verify-mutations.mjs` + `scripts/mutations.mjs` 执行（每条的 `targets` 指明 vitest 全量或 e2e grep，`expect` 为矩阵指定的杀手；判定 KILLED = 指定杀手红，PARTIAL = 只红了一部分指定杀手，OTHER = 红的不是指定杀手）；Python 侧 MUT-18~21/53/54 在 `scripts/verify_mutations.py`（S10-MUT-*）。
- TS 侧 **66/68 KILLED**，杀手与矩阵一致（MUT-55/67 的期望按可达性收窄并在表内写明原因）；
  - **MUT-46 PARTIAL**：静态守卫 TG-17 红，但假进程版 TX-14 在 key 改下标后仍绿——DOM 复用未导致第二次 Enter 答到第二张卡，机理**未证实**；
    **2026-09-28 N34 回填**：机理＝答复即 `setBusy(true)` → 按钮 `disabled` → 焦点离开，第二次 Enter 落空与 key 无关（推断，与「变异下 TX-14 仍绿」相符）；key 真正守的是组件状态归属，新增假进程版 **TX-14b**（第一张卡写反馈后拒绝，第二张卡的反馈框必须为空、第二次拒绝的 `feedback` 为 null），harness 实跑 MUT-46 → **KILLED**（TG-17 + TX-14b 红，TX-14b 失败原文 `Expected: "" Received: "只改第一段"`）；
  - **MUT-62 SURVIVED**：变异落在 `App.tsx` 的 onConnect 调用点，TV-6 ⑪ 测的是纯 reducer，e2e 无「host 重启跨越自动呼出等待」路径——测试缺口；
    **2026-09-28 N34 回填**：补了该路径的 e2e **TX-15b**（回合在跑时 kill -9 host → 重连 → 追加新 pending 必须照常呼出），MUT-62 实跑仍 **SURVIVED**——施工判为等价变异，**评审更正**：成功路径下不可观测，但重连后 `episodes.list`/`conv.snapshot` 失败时 onConnect 的同步 reset 是唯一一次 reset，该失败路径无用例，故不是严格等价（理由见 §7 矩阵该行）；两处 reset 一起去掉的 **MUT-62′** → TX-15b 红，**KILLED**（失败原文 `Expected: 2 Received: 1`）；
  - 两条均登记 issues N34，待补用例。
- Python 侧 **6/6 KILLED**（S10-MUT-18→TY-1 红 4；-19→TY-2 红 3；-20→TY-2 红 6；-21→TY-5 红 2；-53→TY-8 ⑤ 红 2；-54→TY-8 ③ 非标准夹具 + 30 条中含依赖 status 的既有用例），在 `b728a61` 的干净分片工作树上跑（唯一未跟踪项是软链的 `.venv`，故带 `--dirty-ok`）。
- 实跑中查出并修掉的**测试自身缺陷 10 处**（`af0d0ff`，均非生产代码问题）：夹具孙进程读 `sys.argv[2]`（应为 `[1]`），SIGTERM 处理器抛 `IndexError`、TH-9 ② 恒真（MUT-11）；TH-10 重置后同步断言、异步退出未发生（MUT-13）；TH-18 标题写拒绝、用例体从未拒绝（MUT-57）；TH-19 只比了无会话时的缺省 generation（MUT-51）；TH-20 ①/⑦ 时刻构造使变异无从区分（MUT-60/71）；TV-2 失败原文太短、截断 500 字无影响（MUT-32）；真实 core 版 TX-2/5③/6/7 补 spec 子句或时序（MUT-22/28/31/35）。另 TF-1 改用根外诱饵 `.venv`（`871bcef`，原诱饵在仓库内，安全修复）。

**S21 抽验（2026-09-29，独立复跑）**：`node scripts/verify-mutations.mjs --only` 抽 6 条（MUT-10/31/43/49/58/68，含 vitest、e2e 假进程、e2e 真实 core 三类），**6/6 KILLED，杀手与矩阵一致**（MUT-49 → TX-8c，耗时 181 s）；另 Python 侧 S10-MUT-19 复跑 KILLED。

**PR5 文档一项调整（人裁决，2026-09-27）**：§8 PR5 的「`desktop/` 相关说明一行写明钥匙串命令」不在现有 README 上修补，**并入 M10 之后的 README 重写**。

## 9. 验收门禁清单

- [x] **门禁 1（授权）**：§6.1 全部阻塞项获用户授权；S9-R1~R4 经红队对 Spec 9 的定向复核 🟢；　证据：§6.1 授权与 S9-R1~R4 并入 Spec 9 v0.8（定向复核 🟢）已闭环，见 §1.4；M3 评审 2026-09-27 见 Spec 9 §9。
- [x] **门禁 2（建期由 core 完成、UI 不 mkdir、新期停在 01）**：TY-1~TY-4、TY-8、TY-9、TH-14、TX-7、TG-15 全绿，MUT-17~20/34/53/54 被捕获；TG-2 不变；　证据：TY-1~9 落为 `tests/test_spec10_episode_create.py`（§8.1）、TX-7、TG-15、TH-14 在全量中绿；2026-09-29 全量：`uv run pytest` 1983 passed、`npx vitest run` 377 passed、未打包 e2e 111 passed / 2 skipped（workers=2，空载 5.7 min，数据零污染）；TS 变异全表 69 条 68 KILLED / 1 SURVIVED（MUT-62，预期，见 §8.5）/ 0 BUILD_FAIL（N38 回填，`789f60e`）；Python 侧 S10-MUT-18/19/20/21/53/54 6/6 KILLED（§8.5）。
- [x] **门禁 3（答复只来自人的可信点击）**：TG-4′、TG-10、TG-16、TG-17、TX-2、TX-14、TH-3、TH-17 全绿，MUT-3/22/23/23b/24/37/44/45/46 被捕获；　证据：TG-4′/TG-10/TG-16/TG-17、TX-2、TX-14/14b、TH-3、TH-17 全绿；MUT-46 由 TX-14b 杀死（N34），其余 MUT-3/22/23/23b/24/37/44/45 KILLED；2026-09-29 全量：`uv run pytest` 1983 passed、`npx vitest run` 377 passed、未打包 e2e 111 passed / 2 skipped（workers=2，空载 5.7 min，数据零污染）；TS 变异全表 69 条 68 KILLED / 1 SURVIVED（MUT-62，预期，见 §8.5）/ 0 BUILD_FAIL（N38 回填，`789f60e`）；Python 侧 S10-MUT-18/19/20/21/53/54 6/6 KILLED（§8.5）。
- [x] **门禁 4（不代发、不自动；browser 与抓取另需原生确认）**：TH-5、TH-18、TG-11、TX-7、TX-13、TV-12 全绿，TH-22 全绿，MUT-5/35/43/57/58/63/64/69 被捕获；　证据：TH-5/TH-18/TH-22、TG-11、TX-7/TX-13、TV-12 全绿，MUT-5/35/43/57/58/63/64/69 KILLED。**遗留（不改本门禁的测试口径）**：D37——打包版真机上原生确认框曾被记为「批准」而未经人手点击。**2026-10-06 人手定性：无自动化下未复现**；Return 不触发任何按钮，按用户裁决改措辞、不改绑定，D40 同批修（见 §2.10 前修订记录）；**2026-10-06 D37-C 独立评审通过**：打包版 @ `a2da050` 人手复验 Return / Esc / 鼠标取消 / 鼠标批准与退出框 Return / Esc / 取消 / 退出共八步全部符合修订语义，变异 V1–V5 全部 KILLED，已迁 issues archive。
- [x] **门禁 5（作废、结束与退出）**：TH-4、TH-8、TH-9、TH-21、TX-4、TX-8、TX-8b、TX-8c、TX-8d、TX-9 全绿，MUT-4/10/11/12/39/41/47/48/49/65 被捕获；　证据：TH-4/8/9/21、TX-4/8/8b/8c/8d/8e/8f/9 全绿；MUT-4/10/11/12/39/41/47/48/49/65 KILLED；D38（退出确认把空闲会话也算忙）已修并评审通过（`isBusyForQuit`，TH-9⑤、TX-8g/8h）。打包版「忙弹框」一半已于 2026-10-06 D37-C 人手验过（有未答卡时 Cmd+Q 弹框；Return 无反应、Esc / 取消不退、鼠标「退出」才退）；「全空闲 Cmd+Q 不弹框直接退」仍待 ACC。
- [ ] **门禁 6（密钥；**未验部分：打包版手验——钥匙串有条目→`ready.llm=="ok"`、删条目→降级文案含命令**）**：TH-6、TH-7、TV-8、TV-9、TY-5 全绿，MUT-6~9/21 被捕获；**打包版手验一次**：钥匙串有条目 → `ready.llm == "ok"`；删除条目 → 降级文案含正确命令；
- [x] **门禁 7（对话呈现与缺口可见）**：TV-1~TV-3、TV-7、TX-0、TX-1、TX-11、TH-2、TH-12 全绿，MUT-2/15/25/26/32/33 被捕获；　证据：TV-1~3/7、TX-0/1/11、TH-2/12 全绿；MUT-2/15/25/26/32/33 KILLED；D36（作废卡不撤待答区）已修并评审通过。
- [x] **门禁 8（多期多会话隔离与 host 重启）**：TV-4、TV-10、TH-1、TH-10、TH-11、TH-11b、TH-13、TH-16、TH-19、TX-6、TX-15 全绿，MUT-1/13/14/16/31/38/51/52 被捕获；　证据：TV-4/10、TH-1/10/11/11b/13/16/19、TX-6/15 全绿；MUT-1/13/14/16/31/38/51/52 KILLED；D32、N37 已修并评审通过，N39（切仓窗口「新代号+旧根」）待拍板，未构造出可见现场。
- [ ] **门禁 9（呼出与焦点；**未验部分：05 返工真实期的真机手验**；MUT-62 SURVIVED 属预期——两处 reset 同时去掉的 MUT-62′ 已 KILLED）**：TV-6、TV-11、TH-20、TX-5（含 ⑤ 连续 10 次一致）全绿，MUT-27~30/50/55/59~62/67/68/70~73 被捕获；**真机手验**：在 05 返工的真实期上，agent 连改两版期间预览不闪，回合结束只呼出一次；
- [x] **门禁 10（Spec 8 不回归）**：Spec 8 全部 vitest、`e2e/ack.spec.ts`（零改动）、`e2e/preview*.spec.ts`、`e2e-packaged` 全绿；　证据：Spec 8 全部 vitest、`e2e/ack.spec.ts`（除 N44 把 TI-10 焦点断言降为 app 可控面、经人拍板）、`e2e/preview*.spec.ts` 全绿；`e2e-packaged` 11/11（M9，打包构建 `26c9fbc`）。
- [x] **门禁 11（依赖、纯洁与测试夹具安全）**：TG-1 不改仍绿、TG-9（Spec 8）与 TG-14 全绿；TF-1~TF-3 全绿，MUT-36/40/42/56/66 被捕获；TY-7 泄漏清单 `[]`；`pyproject.toml`、`desktop/package.json` 无 diff；TI-6 扩展后会话进程无 LISTEN；　证据：TG-1/TG-9/TG-14、TF-1~3、TY-7（泄漏清单 `[]`）全绿；MUT-36/40/42/56/66 KILLED；`pyproject.toml` 自 2026-09-25 起无 diff；`desktop/package.json` 仅两次改动（`2de9cb6` Spec 8 M12 打包加固、`ac4635e` Spec 11 PR3 的 CodeMirror 依赖），均非本 spec 的 PR（2026-09-29 `git log --since=2026-09-25 -- pyproject.toml desktop/package.json` 核实）。
- [ ] **门禁 12（打包版手验；**未验，待人：完整回合 + 原生确认框 + 退出确认 + 输入法选词中 Enter，与 D37 人手配方同场**）**：打包版上完成一次「idea 聊一轮 → 建期 → 新期发消息（agent 补全 01）（**2026-10-08 修订注记（D42 / Spec 18，人 2026-10-06 裁决「方案 (a) 转正继承、同一时刻只保留一个选题会话」）**：更新为「idea 聊一轮 → 建期 → 新期不重述、直接让 agent 把草案写进 01」，并入 ACC）→ 批准一张写入卡 → 批准一张抓取卡（过原生确认框）→ 退出确认」；输入法选词中按 Enter 不发送；
- [x] **门禁 13（文档）**：`uv run pytest tests/test_docs_invariants.py` 全绿；H-1~H-10 在 §6.2 逐条有测试且全绿。　证据：`uv run pytest tests/test_docs_invariants.py` 12 passed；H-1~H-10 各有测试且全绿（§6.2）。

---

## 10. 潜在红旗与自纠预案

| 编号 | 红旗 | 预案 |
|---|---|---|
| RF-1 | 后台会话无上限，内存与租约越积越多 | 每个会话 RSS 起点约 27 MiB（E6）；期列表徽标让活会话可见；真实使用中若同时活会话 ≥ 5 成为常态，再议上限（不预设数字） |
| RF-2 | 人忘了后台会话还挂着卡，agent 一直停在卡上 | 徽标「N 张卡待答」常驻；本 spec 不给卡片加任何超时（超时作废会把「人不在」当成拒绝） |
| RF-3 | **renderer 被攻破后的影响范围**（v0.2 按 🟡-1 重写） | 与 Spec 8 相比的差：**ack 停机点**——能，同 Spec 8；**答复会话人审卡**——新增：攻破的 renderer 可 `conv.send` 任意指令并自批写入卡、`run_pipeline` 卡（pipeline/creative scope 下只认 `PIPELINE_MODULES`，其中 `tts`、`render` 等会占用本机与云端算力；asset scope 的工具表是 `web_search`、`web_fetch`、`acquire_propose`、`crawl`、`browser`，**没有** `run_pipeline`，模型碰不到 `cloud up`——v0.3 按二轮 🔵-8 改正 v0.2 的事实错误）、记忆卡、检查点卡，等于完整驱动 agent；**browser 卡与抓取卡**——已收回：批准须过 main 原生确认框（用户裁决）；**任意 spawn**——不能：模板闭集、argv 固定、无路径参数。`isTrusted` 与静态规则只防代码层的误用（例如将来有人写循环批准），不防攻破。攻破 renderer 本身的前提是突破 CSP（`script-src 'self'`）与 sandbox，且 renderer 不执行任何外部内容；如实记录，不再加码 |
| RF-4 | 密钥经环境变量传给会话后，会话再 spawn 的 job 继承它 | 与终端现状相同（终端用户的 shell 环境同样被继承）；不扩大暴露面；记录 |
| RF-5 | 钥匙串授权框在无人值守时阻塞 spawn | `KEYCHAIN_READ` 10 s 超时 → 按「未取到」降级，不阻塞（A4） |
| RF-6 | host 崩溃后孤儿会话仍在收尾、持租约 | A3 已实测 EOF 约 8 ms 到达；孤儿按 Spec 9 收尾（≤ 60 s）后退出。期间 `conv.resume` 得 `E_SESSION_LOCKED`，文案给出 pid 与「稍后再试」 |
| RF-7 | 帧超限丢掉的恰是一张 `request` | 卡片不可见、模型停在卡上；`frames_lost` 行提示「可能有卡片未显示，建议停止本轮」；A6 实测后调整上限 |
| RF-8 | Spec 9 实现与 §3.1 + S9-R1/R2/R4 不一致 | TX-0 契约用例；不一致时停下报告，不在 TS 侧打补丁适配 |
| RF-9 | 自动呼出规则让人错过停机点 | 决策卡不受呼出规则影响，始终进待答区；「已就绪」条常驻到被点或该对象离开 pending |
| RF-10 | 人在 idea 里讨论了很久，建期后以为 agent 记得 | 建期成功后新期对话区首行显示本地提示「选题会话的讨论不会带入本期；需要的要点请在这里重述」（纯文本，不是消息）（**2026-10-08 修订注记（D42 / Spec 18，人 2026-10-06 裁决「方案 (a) 转正继承、同一时刻只保留一个选题会话」）**：风险已由「建期带入」消除——agent 确实记得；提示改为两态，带入时写「已带入选题会话记录（N 条消息）」，没东西可带时旧文案照旧） |
| RF-11 | 退出确认被频繁弹出而被人习惯性点「退出」 | 只在有回合在跑或有未答卡时弹；列表写明哪几期、几张卡，并写明「回合不会再做收尾总结」 |
| RF-12 | 终端与桌面端争同一期 | 懒启动使「看」不占锁；`E_SESSION_LOCKED` 文案指明原因 |
| RF-13 | 纯文本回复可读性差（H-3 的代价） | 如实接受；若要 Markdown，须另提修订并证明渲染器不能产出可点击控件。**2026-10-08 修订（D46，人裁决）**：助手回复改渲染 Markdown，渲染器关掉链接/自动链接/引用式链接/图片，证明与测试见 [D46 spec](../2026-10-08-conv-readability-spec.md)；卡片字段仍纯文本 |
| RF-14 | 人时仍不含桌面端对话与卡片停留 | 门禁 14 横幅照旧；归 Spec 11 |
| RF-15 | 钥匙串里存了非 ASCII 的值，`security -w` 吐出十六进制串被当成密钥（E5） | 不可检测（§2.9 第 4 条）；文档写明只存原文；首个模型请求以认证错误失败，走 Spec 9 `error` 路径如实显示，不静默 |
| RF-16 | C10-R3 只把关「类型行存在且为空」 | 没有类型行的旧式文件、类型值不在题材表的文件照旧判 01 完成（E7：收紧会让 5 个真实期倒退）；若要机器把关题材合法性，须先处理这 5 期的存量，另立修订 |
| RF-17 | 退出时回合中的会话不再收尾（S9-R3） | 用户裁决的代价：`turn_end{wrapup:"skipped"}` 确定可查，「继续上次会话」后人可以让 agent 总结；需要收尾时先点「结束会话」再退出。**例外如实写明**（二轮 🔵-6）：host 缺席、熔断或不应答时走 §2.10 第 1、2 步的直接退出，会话读到 EOF，按 Spec 9 做**完整**收尾（最长 60 s）后自行退出；这条路径与「不收尾」相反，也不经过确认框 |

---

## 附：实验记录（2026-09-25/26，均在 scratchpad，未改仓库）

| 编号 | 目的 | 结果 |
|---|---|---|
| E1 | `create_new_episode` 的边界行为（临时根，替换 `paths.ROOT` 后直接调用） | `data/` 缺席：rc 0，建出 `data/episodes/EP1/01-topic.md`；`data` 悬空：抛 `FileExistsError`；`../../escape`：rc 0，建在临时仓库根下的 `escape/01-topic.md`（episodes 根之外、仓库根之内；v0.2 按 🔵-5 改正 v0.1 的「根外一级」）；`番/01`：rc 0，嵌套；`_x`：rc 0；`"  "`：rc 0；绝对路径：rc 0，在绝对路径处建出。红队补测：`../../../escape` 建到仓库根之外，`-x` rc 0 |
| E2 | `security find-generic-password` 找不到条目时的退出码 | 44，stderr「The specified item could not be found in the keychain.」 |
| E3 | Electron 44.4.5 下点击事件的 `isTrusted` | `el.click()` → false；`dispatchEvent(new MouseEvent("click"))` → false；`webContents.sendInputEvent` 鼠标按下/抬起 → true 且 `navigator.userActivation.isActive` 为 true；隐藏窗口里对聚焦按钮发键盘 Enter 未触发 click（窗口未获焦，不作结论）。红队补测：Playwright `locator.click()` → true，`keyboard.press("Enter")` → 可信 keydown |
| E4 | detached 子进程在父进程被 SIGKILL 后能否读到 stdin EOF | 纯 Node 父进程 `spawn(..., {stdio: pipe, detached: true})`，另有一个兄弟子进程常驻：父进程 SIGKILL 后子进程 0.9 s 内读到 EOF，`ppid == 1`、自成进程组。红队补测 utilityProcess：约 8 ms |
| E5 | `security -w` 的输出格式（临时钥匙串文件，未加入搜索列表，用后删除） | 纯 ASCII 值：原文 + 一个 `\n`；含空格的 ASCII 值：原文 + `\n`；**含非 ASCII 的值：输出十六进制串**（`sk-test-秘密 x` 输出 `736b2d...`） |
| E6 | 会话进程的启动开销下界 | `.venv/bin/python -c "import pipeline.agent.cli, pipeline.agent.llm, pipeline.agent.memory, pipeline.approvals, pipeline.jobs"`：3 次均 0.06 s、最大 RSS 约 27 MiB |
| E7 | 01 完成判定的两种规则在真实数据上的影响（`get_episodes_list()` 可见的 26 期，只读） | 模板期现状判 `02 脚本写作`、`completed_steps: ["01 选题"]`；严格规则（类型 ∈ 题材表，杂谈须带模式与张力）→ 5 期不通过（`2026-08-18-春物-折射下的真物` 杂谈缺张力、`2026-09-03-伪恋-一条乐的算法死局` 类型「算法诗」、`2026-08-15-罪恶王冠-音乐乐评` 类型「杂谈（乐评）」、`2026-07-28-春物-自我牺牲` 无类型行、`2026-08-03-春物-谁最适合你` 类型「娱乐向（肥宅快乐导向）」）；最小规则（类型行存在且全为空）→ 0 期倒退，模板命中 |
| E8 | C10-R3 最小规则对现有测试的影响 | 以 pytest 插件在 `status._inspect_episode_core` 外包一层实现该规则，`uv run pytest -p <插件>`：1715 passed |
| E9 | `check_script.TOPIC_FIELDS` 对 `ava new` 模板的解析 | `[('类型', '模式：')]`——`\s*` 越过空值行吃到下一行 |
| E10 | `\p{Cf}` 覆盖哪些不可见字符（Node） | U+200B–200F、U+202A–202E、U+2066–2069、U+FEFF、U+00AD、U+2060 全部为 true；普通 URL 为 false。另：`electron@44.4.5` 的 `MessageBoxOptions` 有 `signal?: AbortSignal`（`electron.d.ts:22586`） |
| E11 | C10-R1 同源语义对现有测试的影响（二轮 🟡-1 后作者重跑） | 以 pytest 插件实现 §3.7 同源语义（`require_data_at(ROOT/"data")`、要求 `episodes/` 已是目录、名字规则、`parents=False`），**全量** `uv run pytest -p <插件>`：`4 failed, 1711 passed`，即 `test_create_new_episode_and_reject_duplicates`、`test_main_new_enters_repl_in_tty`、`test_non_tty_dual_gates_for_new_and_idea`、`test_full_chain_smoke_in_temp_repo` |

## 附：引用自查表（2026-09-29 S21 按施工后 HEAD 重核；以符号为锚，行号为 HEAD 快照会漂）

> v0.2–v0.5 的逐行核实表（HEAD `c31201d`，施工前）核的是施工前的 `cli.py`/`llm.py`/`host/service.ts`/`App.tsx` 等；PR0–PR4 已整体改写这些文件，旧行号全部作废，不再保留。下表只列本 spec 落地物在 HEAD 上的位置与仍依赖的外部符号。契约面由 TX-0（真实 core 帧键集与 `convFrames.REQUIRED/CONDITIONAL` 双向比对）与静态守卫机械守护。

| 引用（符号） | HEAD 位置 | 核实 |
|---|---|---|
| `cli.py::create_new_episode`（C10-R1 同源语义；`paths.require_data_at` 在 `paths.py` 137 起） | `cli.py` 511 起 | ✅ TY-1~4 守 |
| `llm.py::_load_agent_cfg` / `_env_name_of` / `api_key_env_name`（评审回改：单次读配置） | 145 / 166 / 176 | ✅ `test_ty5_config_read_once` 守 |
| `host/sessions.ts::SessionManager` / `quitState` / `isBusyForQuit`（D38） | 189 / 179 / 180 | ✅ |
| `host/spawner.ts` 模板：`KEYCHAIN_READ` / `SESSION_NEW`（及 `SESSION_CONTINUE/IDEA`） | 30 / 46 | ✅ 模板闭集由 TG-12 守 |
| `host/heal.ts::latestPerStop`（`compareIso` 解析比较）/ `shared/isoTime.ts::compareIso` | 111 / 19 | ✅ |
| `shared/convFrames.ts::REQUIRED`（导出，供 TX-0 对拍） | 35 | ✅ |
| `shared/protocol.ts` 快照增 `keyProblem` | 357 附近 | ✅ 即 §8.2 偏差 1 |
| `main/index.ts`：`createMainConfirmBroker`（原生确认框第 5 层）/ `before-quit` 退出状态机 | 9 / 423 | ✅（2026-10-06 D37/D40 修订：选项与 `windowGone` 在 `main/confirm.ts`，见 §2.10 前修订记录） |
| `renderer/App.tsx::onConnect`（重置会话桶并按新 host 重取快照） | 143 附近 | ✅ MUT-62/62′ 守 |
| 夹具：`tests/fixtures/session.ts::fixtureWrite`；静态守卫 `tests/static/scan.ts::inboundFrameOwners`（TG-11） | 18 / 456 | ✅ |
| e2e：`e2e/session.spec.ts`（假进程版）、`e2e/sessionReal.spec.ts`（真实 core 版）、`e2e/fakeLlm.ts` | 目录现状 | ✅ |
| Spec 9 §2.2/§2.5/§2.8/§3.1/§3.6/§4.4/§4.7/§6.6、`S9-R1~R4` | 见 Spec 9 v0.8 与其 §9 实跑记录 | ✅ 已并入并由 TP-3/12/15 守 |
| 实验记录 E1–E11（`create_new_episode` 边界、`security` 退出码、`isTrusted`、EOF、`\p{Cf}` 等） | 2026-09-25/26 scratchpad 实测 | ⚪ 历史实测，非活断言 |
