# 上下文压缩（D62）：长会话的 compact 机制

日期：2026-10-09　状态：**PR1–4 + 收尾已施工**（2026-10-10，见 §九；§五④ 三探针已测完；自动触发默认关，开不开待人定）（一轮对抗审查 15 条全吸收，裁决见 §八；人定四项 2026-10-09 拍板，见 §五）
关联：D28（取消固定轮数硬上限，2026-09-24 人裁决）、D56（服务商实测 token 读数）、D54（诚实性纪律：事实带出处）、ADR-0018（出网断言）、ADR-0025（工具封顶）、AGENTS.md「产物即状态」「诚实失败」

## 一、问题

D28 取消固定轮数上限后，主会话上下文**只涨不缩**。现有三件相关物都不是管理动作：D56 的 token 读数只是显示；检查点（50 条/次问人）防失控不防溢出；session.jsonl 全量落盘但恢复是全文读回。真实结局：长会话跑到服务商报 context-length 错，回合报错交人。**`--continue` 是长会话常态入口——resume 不尊重压缩点就等于特性对核心痛点无效（审查 #11）**，这一条单独把 resume 投影提升为一等功能，不是兼容性补丁。

## 二、生效配置事实（对抗审查取证，2026-10-09）

- `config/agent.local.json` 整文件优先于 `agent.json`；生效端点是**本地代理** `http://127.0.0.1:8317/v1` + `gemini-3.8-flash-high`。`agent.json` 的 OpenAI/gpt-4o 仅在其缺席时生效。
- **因此轴一（cache 成本）的全部结论系于一个未实测的未知数**：代理上游是订阅/OAuth 配额还是按 token 计费、usage 与缓存命中是否透传。配额制 → 压缩纯赚、成本顾虑不成立；计费制 → 必须算 §四.0 的账。**实测结论出来前：手动 /compact 照做，自动触发挂实测（人裁决④）。**
- 受限串在真实会话中的频率（5 份现存 session.jsonl）：`03-audio/manifest.json` ×3，其余三个模式 ×0——罕见但非零， scrub 打洞按「放+注记」处理（§四.6）。

## 三、成熟方案调研（2026-10-09）

七家 coding agent harness（Codex / Claude Code / Gemini CLI / opencode / OpenHands / Roo Code / pi）的**公约数**：

1. **同模型**单独一次调用做摘要——压缩是「需要准确召回」的操作，不是省钱操作；
2. 全文进摘要器；
3. 摘要落史可识别、可滤除（Codex 的 summary prefix：多轮压缩滤掉旧摘要，防套娃）——ava 用宿主内部字段实现（§四.4），不用模型可见文本（审查 #8）；
4. **替换而非追加**：摘要 + 近期原文尾部；
5. 压缩失败中止回合交人（多数）；Gemini 另有「压缩后反而变长 → 本会话禁用自动压缩」护栏。

差异化亮点：Claude Code 压缩后重注入工作现场；Codex 双触发（回合前 + 工具循环中段）且 90% 偏晚是反例；Gemini 50% 触发（标称窗口 ≠ 可靠窗口）与压缩膨胀护栏；opencode 用服务商实测 token 触发；Codex DSC RFC（未验证）以事件日志确定性投影代替 LLM 摘要——与 ava「产物即状态」气质吻合，留为演进方向。

## 四、设计草案 v2

### 4.0 成本账（审查 #2 的账，前提见 §二）

以 OpenAI 刊例为参照系（gpt-4o 输入 $2.50/M、缓存命中 $1.25/M）：H=76.8k 触发时，压缩调用 ≈ $0.22（请求形状变 → 必 miss），压缩后首轮 ≈ $0.07，相对不压每轮省 ≈ $0.06，**回本约 5 轮**。「宁早勿晚」在成本上方向相反（早触发 = 更频繁的全价重写）——故 0.6 的理由只挂可靠性（标称≠可靠），成本账作参考不作主判据。**稳态前缀现状已坏（审查 #3）**：`assembly.py` 把易变状态卡嵌进 `messages[0]` 且逐轮整段替换，缓存命中本来就时有时无；压缩的重注入不另造逐轮刷新的卡，见 §四.5。

### 4.1 触发

- 数据：D56 的服务商实测 `prompt_tokens` 为准（opencode 路线）。**usage=None 的回落口径（审查 #5）**：按字符数 ÷ `CHARS_PER_TOKEN` 估算 token（原写 ÷4，施工时实测推翻、改 ÷1，见 §九 偏离 1），估算也不可得时（两者皆缺）不触发并 notice 人——不静默。
- 基数：**窗口进 config 的 per-model 表**（审查 #6：生效两配置标称差 8 倍，gpt-4o 128k vs gemini flash 1M；且按「标称≠可靠」配打折系数）。`agent.compact.models.<模型>: {window, reliable_ratio}`，`trigger = window × reliable_ratio × trigger_ratio`；`trigger_ratio` 默认 0.6 草案。
- 双触发（Codex 路线）：回合开始前；工具循环中段——**触发点 = 当前 reply 的全部工具结果闭环写史后、下一次模型调用前**（审查 #4a）。

### 4.2 切片：tool_call 组对齐（审查 #4，致命洞）

切口只许落在**完整闭环边界**：assistant 无 tool_calls，或其全部 tool 结果都已写史之后。尾部预算**按 token 计**（人裁决 ①）：目标 ≈ 0.15×可靠窗口（gpt-4o 下 ≈19k，正好落回 Codex 的 20k 实证值），条数 ≤20 作次级约束——从最新消息往前累加（token 按字符 ÷ `CHARS_PER_TOKEN` 估算，见 §九 偏离 1），撞任一预算即停，再回退到最近闭环边界。仓里已有「tool_calls 未配对 → 服务商 400」的病态记录（session_log 注释、plan_repairs 整段），切片对齐是对它的正面防御。

### 4.3 实现落点：loop 内原地改写（审查 #12、#13）

- 压缩在 `run_tool_loop` 内**原地改写**（`messages[:] = …`），**不重绑列表对象**——三个现有契约共同隐含对象同一性：`persist = messages is main_messages`、`commit` 的 append-only 进同一列表、`_rollback` 的 `del messages[length:]`。重绑 = persist 静默翻 False、rollback 作用错对象。
- 不开新回合：检查点计数与判重表是 loop 函数局部量，原地改写则不受影响；「中止回合重开」的省事写法会把它们归零、49 条即可绕过问人闸门——明确禁止。

### 4.4 摘要器与落史形态（审查 #8、#14）

- 同模型（主会话档）。prompt = Codex 最小 handoff 骨架 + ava 专属段：当前期与步骤（从磁盘读入 prompt，摘要器只许引用不许转述）、被驳回的判断及理由、事实-出处对照（D54：事实必须带出处锚点，无法锚定标「未证实」）、下一步。
- 落史：user-role 消息 + **宿主内部字段标记**（`_ava_compact: true`，对齐 `_TIER_KEY` 先例——进请求体前由 `_wire_messages` 删除，模型不可见、不可仿写）；多轮压缩按字段滤旧摘要。不用文本前缀。
- scrub 口径：压缩调用的**输入与输出两侧**都过 `_scrub`（审查 #7：历史可合法含未脱敏受限串，只扫输出等于让救命索撞闸）。命中处在摘要里显式注记（原文「用工具切片查 session.jsonl」做不到，改写见 §九 偏离 2）——不放任空锚让模型补编（审查 #14）。

### 4.5 压缩调用本身（审查 #7）

- 独立 `COMPACT_TIMEOUT_S = 240`（notes_review 的 LLM_TIMEOUT_S 先例；60s 的 REQUEST_TIMEOUT 对 600k 全文必撞墙）。
- 失败/膨胀（摘要 ≥ 被压缩区间 ×0.8，Gemini 护栏变体）→ 本回合中止交人，本会话禁用自动压缩。

### 4.6 重注入（审查 #3）

重注入是**一次性事件消息**，不是逐轮刷新的第二张卡：`messages[0]` 的现役状态卡机制照旧（它的缓存问题是既有事实，不在本 spec 施工面）；压缩时把磁盘重建的期状态卡作为一条静态消息插到摘要之后、尾部之前，此后不再刷新——下一轮起由 `messages[0]` 机制接管。

**可见形态（人裁决 ②）**：压缩改写模型能看到的工作前提，按「诚实失败」家规是一等工作条件变化——当场一张 `channel.show("notice", …)`（TTY 与协议两端现成通道，零新机制）+ `turn_end` 帧带 `{compacted: true, tokens_before, tokens_after}`；desktop 读数消费这两个字段，显示「压缩后 token（压缩前峰值）」，避免「占窗口比例」断崖误导（审查 #15）。

### 4.7 回滚禁令（审查 #10）

回合内一旦发生压缩，本回合 snapshot 失效（压缩把 len 缩到快照水位以下，`del` 成空操作 = 假回滚）。此后若撞需回滚事件（出网断言等）→ **中止回合交人，不执行回滚**。诚实失败，不静默改写历史。

### 4.8 压缩点落日志与 resume 投影（审查 #11，一等功能）

- compaction 事件写 session.jsonl：触发时 prompt_tokens、摘要 sha、保留区间（保留的第一条消息的序号）。
- `rebuild_messages` 按压缩事件**投影**：丢弃区间不重建、摘要消息保留；`plan_repairs` 在投影**之后**跑（先投影再修，避免给被丢弃的 tool_calls 补出与压缩态不一致的合成结果）。
- 子会话豁免（审查 #15）：`persist=False` 子会话（02.8 对抗审查、memory digest——短且单次）不触发自动压缩。

### 4.9 挂起人审卡（审查 #9，前提修正）

`ask` 阻塞主线程，与压缩执行点互斥——「压缩时正悬着卡」在当前内核不存在。待审批台账本来就在状态卡里，覆盖于 §四.6 的一次性重注入；无专门机制。

### 4.10 idea 会话（人裁决 ③）

同一套：触发、闭环切片、标记滤除、scrub 全适用；重注入自然降级——idea 卡是静态的 `build_idea_card()`，本就在 `messages[0]`，连一次性重注入都不需要，跳过该步即可。idea 会话无期目录、无 03-audio 受限串、无审批台账，风险面全场最小，仍放 PR4。

## 五、人的裁决（2026-10-09）

1. **默认值**：`trigger_ratio` 0.6 留作施工默认值（挂可靠性理由成立），生产值等实测④；尾部预算换 token 制：目标 ≈ 0.15×可靠窗口 + 条数 ≤20 次级约束（闭环对齐逻辑不变，只换预算量纲）。
2. **可见形态**：一张当场 notice + `turn_end` 带 `{compacted, tokens_before, tokens_after}`。「仅事后可查」= 静默改写历史，否决；「状态卡一角」要 desktop 施工且易被刷走，否决。
3. **idea 会话**：同一套，PR4 顺位不变；重注入降级跳过（静态 idea 卡本就在 messages[0]）。
4. **CPA 代理实测**：三条探针（① usage 透传：普通请求看 `usage.prompt_tokens` 在不在——这是触发器数据源是否存在的第二个 None 来源；② 缓存命中：同一 ≥1k token 长前缀连发两次，比 `prompt_tokens_details.cached_tokens` 有无 + 第二次延迟，字段缺席但延迟显著下降也算间接证据；③ 上游形态：翻代理配置/日志确认 OAuth 订阅配额还是 API key 计费——配额制则轴一顾虑解散、§4.0 账本作废，计费制则按 §4.0 复核阈值）。**PR2（手动 /compact）不等实测直接上**——手动场景下人就是触发判据，阈值错误伤不到它；**PR3 的自动双触发挂实测结果**。
5. **读数要带分母**（2026-10-09，人裁决；起因见 D64）：会话头只显示「上下文 102.0k token」，人看不出离上限多远，读数等于没给。分母取 §4.1 的 per-model 窗口表，随 PR1 落地后 desktop 改显示；表里没有当前模型时照旧只显示分子，不猜窗口。**待定（施工时出截图方案给人选）**：分母用标称 `window` 还是 `window × reliable_ratio`；写成「102k / 128k」还是百分比；与压缩后「压缩后 token（压缩前峰值）」口径（§4.6）怎么并排。

### 2026-10-10 人的裁决与实测

6. **施工偏离 1–3 通过**（÷1 折算、脱敏注记改写、标记种类串）；偏离 10「回滚到压缩点」通过。
7. **读数分母**：用标称 `window`（不乘 `reliable_ratio`），写成「102.0k / 128k token」；**不显示压缩状态**（压缩是人做的，人记得）；字数口径不配分母。
8. **/compact 入口**：对话框直接输入 `/compact` 发送即压缩（同 Claude Code / pi），不另做按钮。
9. **回合起点估算偏早**：接受。
10. **`--continue` 双 system**：修（`rebuild_messages` 不再重建落盘的 system）。

**§五④ 三探针结果（2026-10-10，经本机 CPA = CLIProxyAPI 反代实测）：**

- ① usage 透传：gemini-3.8-flash-high / gemini-3.7-flash-high 都返回 `usage.prompt_tokens`（触发器数据源存在）。
- ② 缓存命中：6,306 token 的固定前缀连发三次，第二、三次 `prompt_tokens_details.cached_tokens = 4267`（隐式缓存生效且透传）；延迟未见下降（3.4 → 3.8 → 4.2 s）。
- ③ 上游形态：CPA 配置里 `codex-api-key` / `claude-api-key` / `openai-compatibility` 全空，凭据走 OAuth 目录（antigravity / claude 通道），`quota-exceeded` 为免费层额度轮换——**配额制，非按量计费**。按 §二，轴一成本顾虑解散、§4.0 账本作废。
- 附加·窗口截断：开头埋暗号 + 填充，30 万字（16.96 万 token）、90 万字（50.87 万 token）、175 万字（98.92 万 token）三档都照收且暗号答对；200 万字返回 400「exceeds the maximum number of tokens allowed 1048576」。两个 gemini 档上限都是 1,048,576，**代理不截断**（不同于 Cursor 客户端的 200k 工作窗口）。`reliable_ratio` 维持 1.0，依据是单点召回；长上下文推理质量的衰减没测，余量由 `trigger_ratio` 承担。

## 六、验收判据（每条配「它会失败」的变异）

1. 触发：mock usage 超阈值 → 压缩；未到 → 不压；usage=None → 按字数 ÷4 估算触发（变异：None 直接不触发 → 红）。
2. 切片对齐：构造含未闭环 tool_calls 的历史，切口必在闭环边界（变异：纯条数切 → 红；用真 400 复现消息序做回归夹具）。
3. 原地改写：压缩前后 `messages is main_messages` 不变、persist 不翻（变异：重绑新 list → 红）。
4. 落史标记：摘要消息带 `_ava_compact` 且 `_wire_messages` 后不可见；二次压缩滤旧摘要（变异：不滤 → 套娃红）。
5. 尾部预算双闸：token 预算（0.15×可靠窗口）与条数 ≤20 都生效且切口在闭环边界（变异：只按条数不看 token → 红）。
6. 重注入卡来自磁盘重建、一次性不刷新（变异：摘要器输出原样进史 → 红）。
7. 压缩失败/膨胀 → 回合中止 + 本会话禁自动压缩（变异：失败照压 → 红）。
7a. 可见形态：压缩当场出 notice 且 turn_end 帧带 `{compacted, tokens_before, tokens_after}` 三字段（变异：不带字段 → 红）。
8. 重注入的状态卡含待审批行（变异：丢了 → 红）。（原「挂起卡」判据按 #9 修正）
9. 输入侧 scrub：历史含 `03-audio/manifest.json` 的会话可压缩、摘要无脏串且有注记（变异：输入不 scrub → PermissionError 红）。
10. 回滚禁令：压缩后同回合撞出网断言 → 中止交人且历史保持压缩态（变异：执行假回滚 → 红）。
11. resume 投影：含压缩事件的日志重建后 = 压缩态（变异：不投影 → 全文回归红）；plan_repairs 在投影后跑。
12. 子会话（persist=False）不触发自动压缩。
13. 压缩调用用独立 240s 超时（变异：沿用 60s → 红；240s 先例已核：notes_review `LLM_TIMEOUT_S = 240`）。
14. idea 会话（PR4）：同一套触发/切片/scrub，重注入步骤跳过（变异：idea 也注重建卡 → 红）。

## 七、分期

- PR1：纯函数层（窗口表、usage 回落估算、闭环切片、标记字段与滤除、scrub 双侧）+ 测试；
- PR2：摘要器调用与落史 + 手动 `/compact` + 独立超时——**不等实测④直接上**（手动场景人就是触发判据）；
- PR3：自动双触发 + 一次性重注入 + 回滚禁令 + 审计事件 + resume 投影 + notice/turn_end 字段与 desktop 读数——**自动触发挂实测④结果**；
- PR4：idea 会话适配（重注入降级跳过）。

## 八、对抗审查裁决（2026-10-09，15 条）

- **采纳 13 条**：#1（生效配置事实，写进 §二并立待人定项，已经裁决④）、#2（成本账进 §四.0，阈值理由改挂可靠性）、#3（§四.6 重注入改一次性事件消息）、#4（§四.2 闭环对齐，致命洞）、#5（usage=None 回落口径）、#6（窗口 per-model 配置位）、#7（独立超时 + 输入侧 scrub）、#8（宿主内部字段替代文本前缀）、#10（回滚禁令）、#11（resume 投影升一等功能）、#12（禁回合重开写法）、#13（原地改写约束写死）。
- **部分采纳 2 条**：#9（前提修正：悬卡场景不存在，判据 8 改写为「状态卡含待审批行」；desktop host 侧卡片登记未取证，留施工时核）、#14（放+注记；频率已实测：5 份日志共 3 处）。
- 驳回 0 条。

## 调研来源

- OpenAI 官方：Unrolling the Codex agent loop（openai.com/index/unrolling-the-codex-agent-loop/）
- wasnotwas《How AI Coding Agents Handle a Full Context Window》（七家对比，主要事实源）
- kangwooklee《Investigating How Codex Context Compaction Works》（Codex 双路径与 prompt 实证）
- Claude Cookbook：Automatic context compaction；Claude Code 阈值讨论（GitHub issue #23711、#17428）
- Codex PR #6692（多轮压缩滤旧摘要）、Issue #8573（DSC RFC）

## 九、施工记录

### PR1（2026-10-09）：纯函数层

落点：`pipeline/agent/compact.py`（新文件）、`config/agent/compact.json`（窗口表）、`llm._wire_messages` 删压缩标记、`tests/test_agent_compact.py`（35 条，13 个变异全红）；`scripts/verify_mutations.py` 的 D52-MUT-3 锚点随 `_wire_messages` 首行同步。全量 pytest 2476 条绿。

覆盖 §六 判据 1、2、3、4、5 全部，7、9 的纯函数部分（膨胀判定、双侧脱敏+注记）；其余判据属 PR2–4。

**偏离 spec 三处（施工中发现，已按证据定，人可推翻）：**

1. **字数折 token 用 ÷1，不用 ÷4。** ÷4 是英文口径。实测伪恋期 `session.jsonl` 8 个 `turn_end`（gemini-3.8-flash-high）：content 字符 ÷ 服务商 `prompt_tokens` = 1.32–1.39。÷4 低估约 3 倍——gpt-4o 下 usage=None 的回落触发要到真实 ≈225k token 才响，早已越过 128k 窗口，等于回落口径不存在。估算只许偏大，取 1.0（实测下限 1.32 之下再留余量，给没实测过的分词器兜底）。代价：尾部实际比 0.15 目标短约 25%，回落触发偏早。估算同时计入 tool_calls 的参数串（写稿工具的参数就是整篇稿子）。
2. **脱敏注记改写。** 原文让模型「用工具切片查 session.jsonl」：`read_artifact` 只放行 .md/.txt/.json/.patch/.log，读不到 `.jsonl`，受限文件名本身也一律不出网——写一句做不到的指示等于让模型去撞墙。现文：「[已脱敏] 原是受限文件名，不要猜测或补写原文；需要核对相关产物时用 read_status，或直接问人」。
3. **标记值是种类串不是 `true`**：`_ava_compact: "summary" | "card"`。摘要和一次性重注入卡下一次压缩都要滤掉，但 resume 投影（PR3）要分得清谁是谁。

**配置位**：窗口表放 `config/agent/compact.json` 而不是 `agent.json`——`agent.local.json` 整文件取代 `agent.json`，窗口表写在后者会被本机配置静默埋掉。两个生效模型都已入表：gpt-4o 128,000（OpenAI 文档）、gemini-3.8-flash-high 1,048,576（Vertex AI 文档 gemini-3.8-flash）；`reliable_ratio` 均 1.0 未标定，代理是否另行截断窗口挂 §五④ 实测。

**留给后续 PR 的施工发现：**

- **工序层文档会随压缩丢失**：`messages[1:]` 里的 runbook 注入、记忆注入是普通 user 消息，会被压进摘要；而 `tracker.injected_paths` 仍记着「已注入」，下一轮不会再注。PR2 落史时须同步清掉被压区间对应的 `injected_paths`（或重注入），否则压缩后模型手里没有当前工序的 runbook。
- 人裁决⑤的分母：`CompactConfig.window_for(model)` 已可用，desktop 显示形式仍待截图方案人选。

### PR2–4（2026-10-09，一次施工）

落点：`AgentSession.compact`（手动与自动共用）/ `_auto_compact` / `_kept_injected` / 回滚到压缩点（session.py）；`LoopControl.compact` 触发点（llm.py）；`compaction` 事件投影（session_log.py）；终端 `/compact`（cli.py）与协议 `command{compact}`、`turn_finished` 三字段（protocol.py）；摘要器请求构造（compact.py）。测试 `tests/test_agent_compact_session.py`（25 个用例函数 27 条；变异见下文审查段）。

判据落实：1–14 全部有用例；判据 8 用「卡由 `_status_card` 从磁盘现建」+ 卡文本断言覆盖（`build_status_card` 自带待审批行，其本身另有测试）。

**偏离 / 补充（人可推翻）：**

4. **resume 投影提前到 PR2。** 手动 /compact 一上线，`--continue` 不认压缩事件就会把全文 + 摘要一起重建回来，比不压更糟。压缩事件**自带**摘要、重注入卡与保留尾部的完整消息（不靠 seq 对位：内存里的 `messages` 不带 seq，回滚 / 修复插入都会让对位漂移）；代价是日志多存一份尾部（≤ 0.15 窗口）。
5. **自动触发做完但默认关**：`config/agent/compact.json` 的 `auto_trigger: false`（人裁决④）。实测后人改 true 即生效，无需改代码。
6. **回合起点触发用字数估算**：跨回合没有可对位的实测锚点（`messages[0]` 每轮整段换卡），按 ÷1 估算偏大约 35%，起点触发会比 0.6 线早。工具循环中段用「上次实测 + 其后新增消息的估算」，基本准。
7. **压掉的规程要重注**（PR1 记的发现 #1）：压缩后 `injected_paths` 只留正文仍逐字出现在保留尾部里的文档，`active_step_key` 清空，下一轮装配器重注当前工序规程与记忆。
8. **自动压缩「压不了」不算失败**：越线但 `nothing` / `no_window` 等 → 本会话禁自动压缩 + notice，回合照跑（撞窗口时服务商报错如实交人）。只有摘要器失败 / 空 / 膨胀才按 §4.5 中止回合（`stopped: compact_failed`，不做收尾——收尾请求带的正是那份压不下去的上下文）。
9. **手动压缩的 token 读数**：回合之间没有实测，压缩前后都是估算（notice 写「约」）；自动压缩前是实测、压缩后按字数比例折算（`tokens_after_source: scaled`），不拿实测比估算。

10. **回滚禁令改为「回到最近一次压缩点」**（推翻 §4.7 原文，起因是对抗审查 #2）。原禁令的出发点对：压缩后回合起点快照失效，照它 `del` 是假回滚。但「不回滚交人」实测会卡死：模型在工具参数里写了受限文件名（参数按 ADR-0026 不脱敏），同回合压缩后它留在尾部，此后每轮都被出网断言拦、手动 /compact 也压不掉、`--continue` 照样带回。现在每次自动压缩记一个水位（压缩后长度 + tracker 快照），回合要回滚就回到这里；压缩本身保留，当场 notice `rollback_to_compaction`。与日志投影恒等：压缩事件自带保留尾部，`turn_rollback` 只滤本回合在压缩点之后提交的消息。另加**出网预检**：自动压缩在调摘要器之前，用本回合同一份可信集对「system + 卡 + 保留尾部」跑 `assert_egress_boundary`，过不了就**只跳过这一次**、不禁用自动压缩（复核 B：脏消息会随整回合回滚出历史），回合照跑，撞断言时整回合回滚自愈。回到压缩点时，压缩点之前本轮已提交的内容（含本轮提问）仍在上下文里，notice 写明（复核 C）。
11. **压缩临界区屏蔽 SIGINT**（审查 #3）：`defer()` 只在 yield 处接异常，挡不住信号打断区内语句（`_commit` 注释早有记载）；中断落在写盘那一刻会造成「盘上压了、内存没压」。现在写盘 + 原地改写 + 登记 + 记账这一段临时把 SIGINT 的 Python 处理器换成「只记一笔」，退出时恢复并补交原处理器（复核 A：`pthread_sigmask` 只屏蔽主线程，终端 Ctrl-C 发给进程、会被常驻的事件发布线程接住，照样在主线程抛；Python 层处理器总在主线程执行，与谁接到信号无关）；压缩记账由会话层持有，不依赖工具循环收到返回值。
12. **`--continue` 认压缩事件里的 `docs_kept`**（审查 #1）：恢复时「已注入」登记收窄到压缩后仍在尾部里的文档，`step_key` 置空，被压掉的规程下一轮重注。

**对抗审查（2026-10-09，子代理一轮 + 复核）**：报 7 条（严重 2、一般 4、建议 1），#1–#6 全部修复并各配用例与变异；复核又报 A（SIGINT 屏蔽在多线程下失效）、B（预检不过永久关自动压缩）、C（回滚提示没说本轮提问仍在）三条，均已修，A/B 配变异；#7（手动压缩后桌面读数不更新、恢复时 `last_context_reading` 带回压缩前读数）属 desktop 读数施工面，留人。抽查通过 11 项（切片按 id 配对、原地改写、`_wire_messages` 无标记时逐字节不变、脱敏与断言口径一致含 ſ / K 变体、240 s 超时、中断不补重复合成结果、投影与多次压缩、子会话不压、二次压缩滤旧标记等）。全量 pytest 2503 条绿；会话层与纯函数层共 32 个变异全红。

**未做（留人）：**

- desktop：读数改显示「压缩后（压缩前峰值）」、手动压缩后即时更新读数（审查 #7）与分母（人裁决⑤ 形式待截图人选）、/compact 入口按钮。core 侧数据已齐：`turn_finished.{compacted, tokens_before, tokens_after}`、`command{compact}` → `command_result{name: compact}`；host `sessions.ts` 的命令白名单尚未加 `compact`。
- §五④ 三探针实测。

**施工中发现的既有问题（2026-10-10 人裁决修，已修）：** `--continue` 后历史里有**两条** system——`rebuild_messages` 把首轮落盘的 system 消息（旧状态卡）也重建出来，调用方再在前面放一条现建的。docstring 写「不含 system」，实现没滤。旧卡与新卡并存会给模型两份互相矛盾的期状态。压缩后旧卡会随区间被压掉，但不压缩的会话一直带着。

### 收尾（2026-10-10，人裁决 6–10 落地）

- **双 system 修复**：`rebuild_messages` 跳过落盘的 system 消息；伪恋期真实日志重建 47 → 46 条、0 条 system。
- **读数分母**：core 新增 `compact.context_window(scope)`（当前 scope 所用模型的标称窗口，表里没有为 None），随 `ready` 与 `turn_finished` 帧下发 `context_window`；desktop `convFold.contextText` 有实测 token 且有窗口时写「上下文 102.0k / 128k token」（百万级写 1.0M），字数口径与缺窗口不配分母；会话头读数与回合脚注同口径。悬停说明补分母口径与 /compact。真实窗口截图脚本 `docs/dev/plans/2026-10-10-context-window-shots/`。
- **对话框 /compact**：协议进程把正文恰为 `/compact` 的 `user_message` 当「压缩回合」处理——`turn_started` → 压缩（notice）→ `turn_finished{stopped: compacted | not_compacted}` → `stop_points`；不进模型、不落 user 消息；停止按钮可中断摘要器调用（历史不动）。`turn_finished.prompt_tokens` 为 None、`prompt_chars` 给压缩后字数，读数如实退回「约 N 字」直到下一回合拿到实测。终端 REPL 的 /compact 早已在。
- **窗口表补 light 档**：本机 `agent.local.json` 的 `models.light = gemini-3.7-flash-high`（pipeline scope）原先不在表里——流水线模式既无分母、也压不了。实测上限后补入。
- **补登帧契约（昨晚 `3feb84e` 的漏洞）**：`turn_finished` 新增的 `compacted / tokens_before / tokens_after` 与本次的 `context_window` 没登记进 desktop `convFrames.ts` 的 `REQUIRED`。`parseOutFrame` 容忍多余键，所以 vitest 全绿；但 e2e TX-0 / TX-0b 要求真实 core 每类帧的键集合与 `REQUIRED` **完全一致**，`3feb84e` 上这两条就是红的——当时只跑了 pytest 与 vitest，子代理审查也只核了解析器。现已登记，夹具同步补键；desktop vitest 493 / e2e 125（2 skip）全绿。教训：改协议帧必须跑 desktop e2e。

