# 常驻层上下文装配改造（config/pipeline 冻结期设计稿）

> 状态：**已施工**（2026-11-06）。人明确解冻后落地 P1–P8 / S12。
> 出处：2026-11-06 Pi/Hermes 上下文机制调研；问题编号沿用报告 P1–P8，建议编号沿用 S1–S12。

## 一、P1 常驻层比例倒挂

现状：`assemble_resident_prompt(scope)` 固定拼 `director.md + scopes/<scope>.md + production.md`（pipeline/agent/assembly.py:248-295）。实测 creative 常驻 10,697 字符，其中 director+production=6,458（60%）与 scope 无关；pipeline 常驻 7,605 中 pipeline.md 只有 1,136（15%）。

方案：**scope.md 只管「这个 scope 是什么角色、能做什么、边界在哪」**（每份 ≤ 2,000 字符）；scope 文件里现在重复的全局规则（联网策略、记忆两层、acquire 流程、先问人）全部上收到 director.md 或 production.md 一处权威文本，scope 里至多留一行文件级指针。
- director.md 管「你是谁 + 全局行为契约」（先诊断后开方、先问人、人拍板清单、停机点、回复写法）。
- production.md 管「做片流水线全局流程」（产物即状态、流程总图、Code Freeze、失败处理、升级链）。
- scope 文件管「这个会话的特殊边界」（creative 的写稿/改稿纪律、asset 的素材契约、idea 的选题边界、project 的企划统筹、pipeline 的工序推进）。

验收（施工后口径，2026-11-06 复审改过）：每份 scope.md 400–2000 字；asset/idea 常驻 ≤ 5000 字。40% 占比会逼出灌字，改为总量由 `resident_budget` 钉死（贴现状 +400 tok）。

## 二、P2 体积护栏

方案：两处一起上——
1. `config/agent/assembly.json` 加 `resident_budget` 键（施工后贴现状：creative/project 1450、pipeline 1400、idea 1250、asset 1100、default 1400；口径 `len//4`）。
2. `tests/test_agent_assembly.py` 的 `test_resident_token_budget`：逐 scope 断言键存在、`token_estimate <= budget <= token_estimate + 400`。缺表 / 缺键 / 坏值抛 `ValueError`，不许静默变宽。

不做截断：Hermes 的 `_truncate_content` 会砍正文——常驻层规则砍一半比报错更坏，直接红让它收敛。

## 三、P3/S12 状态卡与常驻层分离

现状：`get_initial_system_prompt` 返回 `resident\n\n---\n\nstatus_card` 单字符串，每轮 `messages[0]` 整段替换（assembly.py:80-81、session.py:1346-1349）。status card 必变 ⇒ 缓存前缀在 resident 后断。

方案（S12，比 S5 节级 diff 改动面小）：messages[0] 只放 resident；status card 改成每轮独立 user 消息（前缀 `[状态卡]`），`_assemble` 只在 `tracker.active_scope != scope or not tracker.resident_prompt` 时重写 messages[0]。参照 Hermes volatile 层思想但不动 resident 内部结构。
连带要改：`test_session_context_tracker` 的 `"RESIDENT_PROMPT\n\n---\n\nSTATUS_CARD"` 断言；出网可信集 (a) 的 `trusted_texts_of_resident`/`route_trusted_texts` 不变（它们本来就按文件原文收）。

备选 S5（节级 diff）：resident 改 `{"director","scope","agents"}` dict 逐节 diff，参照 Pi `diffSystemPromptSections`（system-prompt.js:133-149）。改动面更大，冻结期后再议。

## 四、P4 scope 路由

现状：`resolver.scope_of` 只返回 creative/pipeline（resolver.py:17-23）；asset.md 无自动触发路径；`assembly.json` routes 只路由 runbook 不路由常驻层；idea/project 的 routes 显式 `default: []`。

方案：先把两条机制各自收干净再谈合并——
- `resolver.scope_of` 保持只产出 creative/pipeline（按步骤推）；
- asset/idea/project 的 scope 选择固定走显式入口（`scope_mode` 参数 / `session_kind=="project"`），在 `assembly.json` 顶层加注释键 `_scope_note` 写明「asset/idea/project 只走显式 scope_mode，routes 只影响 runbook」；
- `routes` 里 asset 补一条 `_extends: _base`（让 asset scope 的 runbook 注入落到 _base 工序），idea/project 的 `default: []` 保留但要加注释说明是刻意不注入。
验收：`resolve_step_docs("asset","03")` 返回非空；`resolver.scope_of` 测试全枚举仍绿。

## 五、P5 指令具体性（scopes 侧）

待改清单（做片期解冻后逐条改）：
- director.md L13（663 字）拆 5 条：副作用清单 / 什么算同意 / 什么时候不问 / 诚实汇报 / 工具失败先报。
- director.md L16（509 字）拆 5 条：命令你发 / 不叫人跑终端 / 停机点批准例外 / config 四例外 / 不碰 pipeline。
- director.md L17（305 字）拆 4 条：出图先 view_image / 不交参数表 / 文案先商量 / 不描述没看过的效果。
- creative.md L17/L18/L20/L22/L38 五条同法拆短。
写法规约：每条 ≤60 字、一句一条、动词开头、条件用「当 X 做 Y」。

## 六、P6 scopes 编年史（config 侧，解冻后执行）

口径：与本轮 runbook 一致——删纯日期括注与「人裁决/人定 + 日期」，保留规则本体；D/ADR 编号先保留（它们是出网可信集与 dev 文档的锚点，全删成本高、需单独立项），但把「（D84、D100）」这种一句里塞四个编号的收敛成一个主要出处。
实测分布：creative.md D 编号 7 处、director.md 4 处、project.md 6 处、idea.md 2 处、asset.md 3 处、pipeline.md 0、production.md 1 处。

## 七、P7 acquire_propose 五处统一

权威文本定为 `skills/acquire-assets/SKILL.md` 的「衔接顺序」节（它已经在写登记→核对→抓取链）。改法：
- scopes/asset.md、creative.md、project.md：删掉各自的流程长句，留一行「素材候选→人点名→抓取链见 `skills/acquire-assets/SKILL.md` 衔接顺序节」；
- docs/runbook/02-script.md、04-clips.md：保留步骤箭头链但去掉与 SKILL 重复的措辞（02-script.md 与 04-clips.md 均已改）。

## 八、P8 字节级恒定

现状注释承诺「会话内字节级恒定」（assembly.py:251），但 messages[0] 每轮拼接 status card 后实际前缀断。P3/S12 落地后 `messages[0]` 在 scope/文件不变时逐字节恒定（P8 只管这一段）。状态卡是易变层：出网只留最后一张（认 `_ava_status_card` 键），请求体在上一张卡位置起不与上一轮前缀重合。

## 九、施工记录（2026-11-06）

- P1 / P5 / P6：director.md + production.md 收全局规则；五份 scope 400–2000 字（不做 40% 灌字）；asset/idea 常驻 ≤ 5000 字。
- P2：`assembly.json` 的 `resident_budget` 贴现状；`test_resident_token_budget` / `test_resident_scope_size_caps`。缺键抛错。
- P3 / S12 / P8：`messages[0]` 只放 resident；状态卡每轮 `origin=injection` 的 user 消息，带 `_ava_status_card`；scope 未变不改写 `messages[0]`。
- P4：顶层 `_scope_note`；asset `_extends: _base`；idea/project 保留 `default: []` 并加 `_note`。
- P7：scope 与 runbook 04 指向 SKILL「衔接顺序」节。S5 节级 diff 未做。
