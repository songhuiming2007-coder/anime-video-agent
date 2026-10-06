# Spec 17：工具不再按模式（scope）分配，所有模式开放全部工具（D43）

> **状态：v0.1 草案（2026-10-06 立文），待人审 §8 → 红队（D43-R）**。本文件不改代码；红队 🟢 且人确认后才施工（D43-B），施工后另开 session 独立评审（D43-C）。
> 对应 issues：**D43**（主）；与 **D42** 交叉（D42 方案 (a) 的「idea 补联网工具」被本 spec 覆盖，见 §6）。
> 相关：ADR-0021（网络工具内化，「网络工具只对 asset / creative 可见」）、ADR-0025（工具表封顶 14，`cover_edit` 只对 creative 可见）、ADR-0023（跨期记忆，`write_memory` 只挂 creative）、Spec 10（`archive/2026-09-25-desktop-conversation-panel-spec.md`）§2.5（idea 会话零写权限）、impl spec（`2026-09-18-ava-agent-impl-spec.md`）§2.4 / §2.5 B3-r6（scope 白名单）；新提 **ADR-0027**（`docs/dev/adr/0027-tools-not-gated-by-scope.md`，提议中）。

## 0. 一句话

模式（`creative` / `pipeline` / `asset` / `idea`）只决定注入哪份提示与工序手册，**不再决定模型能用哪些工具**。全部 13 个工具在任何模式下都可见、可调用；工具实现里按模式拒绝的检查一并删除；与模式无关的护栏（写入文件白名单、路径防穿透、人审卡、出网断言、`cloud exec` 禁令、`--force` 禁令、Code Freeze）一条不动。

## 1. 人的裁决（2026-10-06）

- 人原话：「我建议不论什么模式，都开放所有 tool，具体原因我先不展开，这是我深思后的考虑」。
- 同日确认两点：① 工具实现里的模式检查**也一起去掉**（不是只改 `tools.json` 的可见清单）；② **人审卡全部照旧**。
- 动机由人保留，本 spec 不替人补写理由；§3.6 只记录这项改动在机制上的客观效果，供红队核对，不作为立项依据。

## 2. 现状：工具被三层东西按模式卡住

| 层 | 位置（以符号为锚，行号会漂） | 现状 |
|---|---|---|
| ① 可见清单 | `config/agent/tools.json`；`tools.py::tool_names_for_scope` / `build_tool_schemas`；`scopes.py::load_scope` | 四个模式各一份清单：creative 11 个、pipeline 4 个、asset 5 个、idea 4 个（全部 13 个工具 schema ≈ 7.1k 字；现各模式 1.2k–5.7k 字） |
| ② 调用前的白名单闸 | `tools.py::execute_tool`（「四层闸」第 ②层）；`session.py::review_tool_call`（「未注册/越 scope」先拒，不弹卡） | 模型调了本模式清单外的工具 → 直接返回「不在 X scope 白名单内」 |
| ③ 实现内的模式检查 | `tools.py::write_episode_file`（`scope != "creative"` → `PermissionError` 零写权限）；`memory.py::apply_op`（`scope != "creative"` → 没有记忆写权限）；`tools.py::validate_pipeline_command`（creative/pipeline 用 `PIPELINE_MODULES`，asset 用 `ASSET_COMMANDS`，其他 → 「未知的 Scope」） | 只改 ① 而不动 ③，模型调用会拿到 `PermissionError`，白白浪费轮次（人 2026-10-06 确认一起去掉） |
| 文案 | `config/agent/scopes/idea.md`（「写权限为零（机制保证，不是纪律）」「指引人退出并运行 `ava new`」）、`asset.md`（「只出提案不抓取」）、`pipeline.md`（「零直接文件写权限」）；`status_card.py`（idea 卡「写权限: 无（机制保证）」）；`cli.py` idea 横幅与帮助三处；`protocol.py` 注释 | 与新能力对不上，须同步改 |

不受模式影响、本 spec 不碰的：`assembly.json` 的常驻层 / 工序层路由与 `memory.scopes`（记忆注入范围）；`resolver.scope_of`（工序 → 模式推导）；桌面端「素材模式」切换（仍切提示）；模型分级（ADR-0023）。

## 3. 设计

### 3.1 可见清单：`tools.json` 收为一张表

- `config/agent/tools.json` 从「四模式各一份」改为**单一清单** `{"tools": [13 个工具名]}`；`tool_names_for_scope(scope, root)` 改名为 `tool_names(root)`（不再收 scope），`build_tool_schemas(root)` 同理；`scopes.load_scope` 不再读工具表（只管提示文件）。
- 理由：四份完全相同的清单会各自漂移，「哪个模式漏了一个」正是本次要消除的那类问题；单表让「工具全开」由结构保证，而不是靠四份配置碰巧一致。（备选：保留四键、内容相同——改动更小，但留着漂移口子。见 §8 Q1。）
- 保留现有「`tools.json` 写了未注册的名字 → 当场报错」的分叉检查（B3-r6），新增反向检查：`TOOL_SCHEMAS` 里注册了但 `tools.json` 没列的 → 同样报错（全开之后两边应恰好相等）。
- ADR-0025 的封顶（14，现役 13）按全局数量计，本 spec 不新增工具，不触发。

### 3.2 调用前闸：② 层变成「是否注册」

- `execute_tool` 第 ② 层与 `review_tool_call` 的「越 scope」拒绝删除；第 ① 层「名字已注册」保留（等价于在单表内）。
- `ToolContext.scope` 字段保留，只作**记录用途**（事件 payload、`approvals.jsonl`、browser 事件里的 `scope` 字段照写），不再参与任何放行判断。施工时 grep `ctx.scope` / `scope=` 全部调用点，逐个标注「记录」或删除。

### 3.3 实现内检查

| 工具 | 删掉的 | 保留的（与模式无关） |
|---|---|---|
| `write_episode_file` | `scope != "creative"` 的零写权限 | 文件名白名单 `CREATIVE_WRITABLE_FILES`（`01-topic.md`、`02-script.draft.md`、`07-titles.md`；常量改名 `EPISODE_WRITABLE_FILES`）、双端 resolve、禁写 `pipeline/`、禁越出期根、写 `01-topic.md` 必须人确认、`atomic_write`；`CRITICAL_TOOLS` 人审卡 |
| `write_memory` | `memory.apply_op` 的 `scope != "creative"` | 持锁 → 锁内重读重规划 → 卡闸 → 日志 → 原子写；人审卡 |
| `run_pipeline` | 按 scope 分派的两份白名单与「未知的 Scope」分支 | 合成**一份**放行表：`PIPELINE_MODULES` 的 9 个模块（不限子命令）∪ `ASSET_COMMANDS` 的 6 个模块与各自子命令清单；`--force` / `--force-all` 前缀禁令、`cloud exec` 永久禁令、`cloud run` 的 `validate_extra_args`、当期目录自动补位；人在宿主确认后才执行（`confirmed`）的既有流程 |
| 其余 10 个 | 无实现内模式检查（`browser` / 记录类 `scope` 参数保留为记录） | 全部照旧：`assert_egress_boundary`、`_guard_url`、`_scrub` / `_redact_secret`、读域、`browser` 人审卡 + 原生确认框、`acquire_propose` 只写候选池 |

`validate_pipeline_command` 的 `scope` 参数删除；`create_job` / `run_pipeline` 的 `scope` 参数降为记录字段（`jobs.py` 的 `Job.scope` 仍写入，值取当时会话模式）。

### 3.4 没有期目录时（idea 会话）

idea 会话没有期目录。工具全部可见，但凡需要期目录的调用，统一返回同一条错误：「当前没有期目录：这一步要先建期（桌面端「＋ 新建一期」/ 终端 `ava new <名>`）」，不藏工具、不弹卡。逐个：

| 工具 | idea 下的行为 |
|---|---|
| `write_episode_file`、`cover_edit` | 返回上述错误（`cover_edit` 现已有 `if not ctx.episode_dir` 分支，改用同一文案） |
| `run_pipeline` | **建议**：返回上述错误（理由：作业、`approvals.jsonl`、事件都挂在期目录下，无期路径从未实测；`ASSET_COMMANDS` 里的库级命令如 `ingest phase0`、`cloud status` 理论上可无期执行，但要另补无期的记录落点，属于扩面）。见 §8 Q2 |
| `read_status` 不带期名 | 现状已报「未指定期，且当前会话未绑定期目录」，照旧 |
| `write_memory` | 放行（记忆是库级，`apply_op` 已接受 `episode_dir=None`）；过人审卡 |
| `acquire_propose` | 放行（只写库级候选池 `data/library/incoming`，不依赖期目录）；过人审卡 |
| `web_search` / `web_fetch` / `crawl` / `browser` | 放行（`browser` 过人审卡 + 原生确认框） |

idea 会话「不落盘」（messages 不写 `session.jsonl`）的语义不变。施工时须核实：idea 下弹出的人审卡在无期目录时的批准记录落点（`session.py` 现有 `self.ep_dir is None` 分支），写清是「不记」还是「记到库级」，不许静默丢。

### 3.5 文案同步

- `idea.md`：删「写权限为零（机制保证）」；产出与落盘一节改为「需要写 `01-topic.md` 时先请人建期」（D42 落地后再改为「建期后在同一会话里写」）。
- `asset.md`：「只出提案不抓取」改为「素材下载只出提案（`acquire_propose`），实际下载由人逐条批准后走 `pipeline.acquire`」——把「不抓取」限定在下载素材上，避免被理解成不抓网页（S13-G8 附带观察，未做 A/B，只是消除歧义）。
- `pipeline.md`：「零直接文件写权限」改为「写期文件只走 `write_episode_file`（白名单三份文件、过人审卡）」。
- `status_card.py` idea 卡：「写权限: 无（机制保证）」改为「期目录: 无（写期文件前须先建期）」。
- `cli.py` idea 横幅 / 帮助 / docstring 三处与 `protocol.py` 注释同步。
- **不改** `AGENTS.md`（常驻规则不涉及按模式分工具；140 行预算不动）。施工时 grep `AGENTS.md` 与 `docs/runbook/` 有无「某模式不能用某工具」的说法，有则列入修订面。

### 3.6 客观效果（供红队核对，不是立项理由）

- 每次请求的工具 schema 恒为 ≈ 7.1k 字：creative 多 ≈ 1.4k，asset 多 ≈ 4.4k，pipeline / idea 多 ≈ 5.9k。S13-G8 实测 asset 会话一轮 `prompt_chars` ≈ 2.1 万字，增量占比可见但不大。
- 自动模式下同一会话随工序推进会换模式（03→04 进 pipeline），此前工具表随之变化；全开后工具表在会话内恒定。
- 单会话可见工具数从 4–11 变为 13，ADR-0025 推翻条件里「各 scope 可见工具都 ≤ 8 → 可按 scope 设封顶」那条论证路径随之消失（不影响现行全局封顶 14）。

## 4. 不做的事

- 不新增工具，不改任何工具的 schema、参数取值域与返回契约。
- 不删、不放宽任何与模式无关的护栏（§3.3 右列全部保留）；`CRITICAL_TOOLS` 集合不变。
- 不改记忆注入范围（`assembly.json` 的 `memory.scopes` 仍是 creative / asset / idea）；pipeline 模式下可调用 `write_memory` 但不预先注入记忆全文，见 §5 R3。
- 不改模式推导（`scope_of`）与提示装配；不改桌面端（施工时全量 e2e 验证无回归即可，若发现桌面端有按模式过滤工具的代码再停下报人）。
- 不顺手做 D42 的「建期时迁移选题会话」。

## 5. 风险与攻击面（给红队）

| # | 风险 | 本 spec 的判断 | 请红队核 |
|---|---|---|---|
| R1 | pipeline 模式此前「永不见网络工具」（ADR-0021）。03 / 03.5 等工序的会话上下文里有期内产物路径，开放网络工具后出网面变大 | 出网断言 `assert_egress_boundary` 对 LLM 请求体与 web 四个出方向都照旧生效（Spec 16 豁免只针对可信规程原文），与模式无关 | 构造 pipeline 模式下把 `03-audio/manifest.json` 内容或受限文件名带进 `web_search` query / `web_fetch` URL 的最坏样例，确认仍被拦 |
| R2 | 非 creative 模式能写期文件：例如 05 返工期间改写 `02-script.draft.md` | 白名单只有三份文件，全部过人审卡；`02-script.md` 本就不在白名单；`status.py` 在 `02-script.md` 存在时不看草稿，工序不会倒退 | 核实封板后改写草稿是否影响 02.5 的 diff / 人时统计等任何下游判定 |
| R3 | pipeline 模式没注入记忆全文，模型可能在没读过记忆时调 `write_memory` | 写入走「锁内重读重规划」，且返回写后全文；不读而写最坏是写出重复或冲突条目，人审卡兜底 | 判断是否应把 pipeline 加进 `memory.scopes`（会改 ADR-0023 补记的「pipeline 不单独注入」），或接受现状 |
| R4 | idea 无期路径：人审卡记录落点、`run_pipeline` 作业落点 | §3.4 定为统一报错 / 核实记录落点 | 逐个工具实跑 idea 下的调用，确认没有静默丢记录或写到意外位置 |
| R5 | `run_pipeline` 合表后，creative / pipeline 模式也能提议 `cloud up` / `cloud run`（花钱）与 `faces` / `shots` 等库级重活 | 执行一律要人在宿主确认；`cloud exec` 与 `--force` 禁令不变 | 核 `cloud down` 在作业运行中被提议的提示是否足够 |
| R6 | 13 个工具全量可见，模型选错工具的概率上升 | 不在本 spec 内设防（人裁决的取舍）；施工后观察 | 无（列出即可） |

## 6. 与 D42 的关系

D42 人选方案 (a) 含两半：① idea 补联网三件（`web_search` / `web_fetch` / `crawl`）；② 建期时把选题会话记录带进新期。本 spec 落地后 ① 自动成立（且更宽：idea 全部工具可见），D42 的完整 spec 只剩 ②，范围相应缩小；D42 行与选型稿须加注。两者施工都动 `tools.json` 与 `idea.md`，**本 spec 先施工**，D42-B 在其后。

## 7. 冻结面影响与文档修订面

施工同一 PR 内修订（archive 原文加修订注记：日期、D43、人裁决原话）：
- ADR-0021：「网络工具只对 asset scope 与 creative scope 可见；pipeline scope 永不见网络工具」与「工具表保持静态注册、scope 过滤」→ 改为「静态注册、不按 scope 过滤（ADR-0027）」。
- ADR-0025：§2「`cover_edit` 只对 creative scope 可见」「pipeline scope 永不见它是 scope 分组授权的既有纪律」与「不改动任何既有工具的 scope 归属」加注被 ADR-0027 取代；封顶数字不动；推翻条件里依赖 scope 分组的那句加注。
- ADR-0023：`write_memory` 只挂 creative 的表述加注。
- Spec 10 §2.5：idea「零写权限」改为「无期目录，写期文件前须先建期」。
- impl spec §2.4 / §2.5 B3-r6：scope 白名单语义改为单表。
- Spec 4（`archive/2026-09-23-network-tools-spec.md`）与 Spec 12（cover）中「仅 X scope 可见」的句子：施工前 `grep -n "scope" ` 逐份核全，列表回填 §10。
- 新 ADR-0027 转「已通过」。

## 8. 待人裁决

| # | 问题 | 建议 |
|---|---|---|
| Q1 | `tools.json` 收成单表（改函数签名，测试改动面大一些），还是保留四个键、内容全相同（改动小，但留漂移口子） | **单表**（§3.1） |
| Q2 | idea 会话里 `run_pipeline` 怎么处理：统一报「先建期」，还是放行库级命令（`ingest phase0`、`cloud status` 等）并补无期记录落点 | **统一报「先建期」**（§3.4）；以后真有无期跑库级命令的需求再立文 |
| Q3 | pipeline 模式要不要也注入记忆全文（§5 R3） | **不加**，维持 ADR-0023 补记；人审卡兜底 |

## 9. 测试与变异（施工时按实现跑出期望值再写断言）

**改写**（被删语义的旧断言换成等强的新断言，不许只删）：`tests/test_agent_tools.py`（零写权限两条 → 改为「pipeline / asset 模式写白名单文件成功、写白名单外仍拒」；`validate_pipeline_command` 按 scope 的若干条 → 合表后同等覆盖；「tools.json 已漂移」的逐 scope 精确断言 → 单表精确等于 `TOOL_SCHEMAS` 全集；T6 idea 表等于 4 个只读工具 → 改为全集 + 无期报错）、`tests/test_agent_pr6.py` M20（越 scope 拦截 → 改为「未注册名字」拦截）、`tests/test_agent_memory.py`（idea 写记忆被拒 → 改为过卡）、`tests/test_agent_session.py`、`tests/test_agent_crawl_browser.py`、`tests/test_agent_director.py`（逐回合工具表断言）。清单以施工前**全量 `uv run pytest` 实跑失败列表**为准，逐条在 §10 回填「改成了什么」。

**新增**：
- TA-1：四个模式 `build_tool_schemas` 输出逐字节相等，且名字集合 == `TOOL_SCHEMAS` 键集合。
- TA-2：`tools.json` 少列一个已注册工具 → 加载报错（反向分叉检查）。
- TA-3：pipeline / asset / idea 三个模式下 `execute_tool` 调 `web_search`（打桩）都执行，不返回白名单错误。
- TA-4：asset 模式 `write_episode_file("02-script.draft.md")` 过卡后落盘；写 `02-script.md` / `../x` / `pipeline/x` 仍拒；`01-topic.md` 未确认仍拒。
- TA-5：creative 模式 `validate_pipeline_command("cloud status")` 通过；`cloud exec`、`tts --force-a`、`faces unknown` 在任何调用路径下仍拒。
- TA-6：idea 会话调 `write_episode_file` / `cover_edit` / `run_pipeline` → 统一「先建期」错误、零写入、不弹卡；调 `write_memory` → 弹卡。
- TA-7：pipeline 模式下 `web_fetch` 的 URL 含 `agent.local.json` → 仍被出网断言拦截（R1 的回归守卫）。

**变异**（每条须由指定用例的断言杀死；`PYTHONDONTWRITEBYTECODE=1`，还原 md5 对拍）：
- MUT-A1：`build_tool_schemas` 对 idea 只返回旧 4 件 → TA-1 / TA-6 杀。
- MUT-A2：删掉反向分叉检查 → TA-2 杀。
- MUT-A3：`write_episode_file` 恢复 `scope != "creative"` 检查 → TA-4 杀。
- MUT-A4：合表时漏掉 `ASSET_COMMANDS` → TA-5 杀。
- MUT-A5：合表时把 `cloud exec` 禁令挪进某个 scope 分支后丢失 → TA-5 杀。
- MUT-A6：idea 下 `run_pipeline` 不报错、直接校验通过 → TA-6 杀（若 Q2 选放行，本条改写）。
- MUT-A7：web 工具在 pipeline 模式跳过 `assert_egress_boundary` → TA-7 杀。
- MUT-A8：`memory.apply_op` 恢复 creative-only → TA-6（idea 写记忆弹卡）杀。

## 10. PR 划分、验证与门禁

- PR1（core 一个提交）：`tools.json`、`tools.py`、`scopes.py`、`memory.py`、`session.py`、`status_card.py`、`cli.py`、`protocol.py` 注释、三份 scope 提示、测试；PR2（文档）：§7 全部修订面 + ADR-0027 状态。
- 验证：全量 `uv run pytest` 全绿；`cd desktop && npx vitest run`、全量 `npx playwright test`（`workers: 2`，临时副本）全绿；§9 变异逐条回填。
- 门禁：① §9 新增用例与改写用例全绿，改写逐条可追溯；② 变异 8 条全杀；③ 真会话冒烟（临时仓库副本、真 LLM）：pipeline 模式会话里让模型查一条网页资料，确认 `web_search` 可用且出网断言照常；idea 会话里让模型写 `01-topic.md`，确认返回「先建期」而不是白名单错误；④ §7 文档修订面完整。
- 施工回填（D43-B 写）：测试失败清单与改写对照、变异回填表、偏差。
