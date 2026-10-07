# Spec 18：建期迁会话——选题会话（idea）落盘与转正继承（D42）

> **状态：已施工·待评审（D42-B，2026-10-08；回填见 §9）**；此前 v0.2 + 定向复审（D42-R2，2026-10-08）🟢 可动工——8 条全部核销，2 条非阻断残余（见「定向复审」）。2026-10-08 立文（D42-A 第二步）。
> 对应 issues：**D42**。前置：选型稿 `2026-10-06-idea-to-episode-options.md`（人 2026-10-06 裁决：方案 (a)、联网三件含 `crawl`、同一时刻只留一个选题会话）；**D43 / Spec 17 已施工（2026-10-08）**——方案 (a) 的「idea 补联网三件」已被覆盖且更宽（单表全开），本 spec 只剩「建期把选题会话记录带进新期」一半。
> 触及冻结面：Spec 9（`archive/2026-09-25-agent-session-protocol-spec.md`，`--idea` 不落盘 → 落盘 + 恢复）、Spec 10 §2.5（建期流程与 `idea-note`）、D27 spec（`archive/2026-09-21-ava-entry-idea-scope.md` §3.1，idea 的立规文件；D42-R2 余-1 更正——此前误指 ADR-0018，后者全文无 idea 条款）、Spec 10 §2.5 第 5 条（idea 会话保留后台 → 建期即结束）。

## 红队一轮裁决（2026-10-08，D42-R，独立 session 未参与立文；只审文稿，未改仓库代码）

**裁决：🟡 修订后复审**（3🟡 + 5🔵，其中 🟡-2 需人裁决）。方向与机制主干站得住：落盘复用 `session_log` 全套机制、零新格式属实；租约即「同一时刻只留一个选题会话」的机制保证成立（flock 进程级互斥、进程死锁即释、无 stale 锁，终端与桌面端互斥同源）；迁移整段字节复制经探针实测——回滚回合丢弃、repair 插回、tool 配对完整、未闭请求在新期恢复时作废、源文件一字不动、清空后无可恢复段；R4 反面全仓核实成立（所有期枚举根都是 `data/episodes/`——`cli.get_episodes_list` / `status.py` / `memory.py` / `approvals.py` / 桌面端 `listEpisodes`——`data/_idea` 在 `data/` 下天然够不着；`read_artifact` 读域 = 期目录 + `data/library/`，`search_notes` 与记忆不触及；`_episode_name_problem` 禁止 `_` 开头期名，双保险）。阻塞项：`data/_idea` 的创建者与缺失/不可达降级路径通篇未写（🟡-1，所有现存仓库第一次进 idea 必踩）；含既有修复记录的段在「崩溃 → idea 恒恢复（修复#1）→ 迁移 → 新期 --continue（修复#2）」链路上重复修复出两条同 `tool_call_id` 的 tool 消息（🟡-2，HEAD 既有缺陷、被恒恢复放大，需人裁决归属）；清空步骤（§3.2 d）的失败形态未定义，且撞 `session_log` 的「唯一截断」纪律而未提修订（🟡-3）。

| 编号 | 指控 | 证据 | 建议 |
|---|---|---|---|
| 🟡-1 | **`data/_idea` 谁来创建、缺失/不可达时怎样降级，通篇未写——所有现存仓库（含人的真仓）第一次进 idea 必踩。** `EpisodeLease.acquire` 对不存在的目录抛 `SessionLogBroken(「期目录不存在，拒绝建目录与写会话记录」)`；`preflight.sh --init` 的骨架只有 `library/*`、`models/local`、`voice/*`、`episodes`，不含 `_idea`；协议 ⑤ 的租约获取与 ④ 的 `E_DATA_UNREACHABLE` 检查都包在 `ep_dir is not None` 里，idea 分支今天两样都不做。照 spec 落到协议侧，首启必报「期目录不存在」（文案误导）；若走 `ensure_lease` 的懒取路径则更糟——它吞异常只记 `lease_error`，`_record` 对 `lease is None` 静默 return，idea 会话**静默退回「退出即丢」**，正是本 spec 要消灭的行为，违反「诚实失败」。T-D42-1 的夹具若预先 mkdir 就永远测不到这条 | `session_log.py::EpisodeLease.acquire`（`is_dir` 检查）；`pipeline/preflight.sh` --init 骨架清单；`protocol.py` ④ `if ep_dir is not None and not (paths.ROOT / "data").is_dir()` 与 ⑤ 租约块；`session.py::SessionHost.ensure_lease` 与 `AgentSession._record` 的静默 return | spec 写死：① idea 启动（终端与协议同口径）先查 `data/` 可达（不可达 → 终端报错退出非零 / 协议 `E_DATA_UNREACHABLE`，与期会话同闸），可达而 `_idea` 不存在则由 core 创建（有先例：`log_approval_decision` mkdir `_agent/`；这不是「自动创建 data/」）；② 租约拿不到（锁冲突/其他错误）一律显式报错退出，idea 会话**禁止**静默非持久运行；③ 补 T-D42-8（`_idea` 缺失首启自建）与「data/ 不可达时 idea 拒启动」用例，变异加「静默吞租约失败 → T-D42-8 杀」 |
| 🟡-2 | **含既有修复记录的段被「再恢复」时重复修复（HEAD 既有缺陷，恒恢复 + 迁移把它放大成主路径，需人裁决归属）。** `plan_repairs` 只把 `msg{role:tool}` 计入 `satisfied`，不认已追加的 `repair_tool_results`——于是同一段每恢复一次就再追加一份修复、重建出两条同 `tool_call_id` 的 tool 消息（多数 API 对重复 tool_call_id 直接 400，会话卡死到开新会话）。探针实测：HEAD 上普通期「崩溃残留缺 tool 结果 → 恢复#1 → 恢复#2」，盘上 repair 记录 1→2 条、重建 tool 消息 1→2 条。D42 的暴露链：idea 崩溃 → 下次 `ava idea` 恒恢复（修复#1，落 `_idea`）→ 迁移进新期 → 新期 --continue（修复#2）→ 重复 tool 消息进 creative 上下文 | 探针 `scratchpad/d42r/probe_double_resume.py`（隔离迁移、纯单期两次恢复复现）与 `probe_migrate.py`（迁移链复现）；`session_log.py::plan_repairs` 的 `satisfied` 只数 `msg`；`prepare_resume` 每次恢复无条件跑 `plan_repairs` | **需人裁决**：(A) 本 spec 顺手修 `plan_repairs`（把既有 `repair_tool_results` 覆盖的 `tool_call_id` 计入 `satisfied`，改动局部、配变异）；(B) 登记独立 issue 留待以后，本 spec 不加断言。无论选哪条，T-D42 清单加「含既有 repair 记录的 idea 段迁移后 --continue，重建的 tool 消息不重复」——选 (B) 则该用例按现状是红的，只能写成「如实记录重复」的观测断言或留空待修，由人一并定 |
| 🟡-3 | **§3.2 d「清空」的失败形态未定义，且与 `session_log` 的明文纪律冲突未提修订。** 原子性约定只覆盖 b（读）/c（写）：d 截断失败时退出码、stderr 文案、桌面端形态全无规定——R3 把「复制成功但截断失败 → 下次建期重复带入」抛给红队却没有默认答案。同时 `session_log.py` docstring 写死「整个模块里唯一的截断是 `truncate_torn_tail()`」，清空 _idea 是第二种截断，纪律条不改就是「判据留着不执行」。另：`verify_mutations.py` 的 shipped 矩阵无「复制后清空失败」的对应变异（MUT-D42-b 只覆盖「不清空」的代码路径，不覆盖清空调用本身抛错的容错路径） | spec §3.2 d 与 §3.2.3 的覆盖范围；`session_log.py` 模块 docstring 第 1 条；MUT-D42-a~e 清单 | 写死：d 失败 → 退出非零 + stderr 明示「建期成功、迁移成功、清空失败：`_idea` 记录未清，下次 `--from-idea` 会重复带入，请手动清空 `data/_idea/session.jsonl`」（诚实失败，不许静默 0）；`session_log` docstring 加第二截断点的注记（时机：持租约、整段复制成功之后）；T-D42-3 扩一条 d 失败腿（注入 `ftruncate` 失败），或新增 T-D42-3b；对应变异「截断失败静默返回 0 → T-D42-3b 杀」 |
| 🔵-1 | **§4 修订面错指 + 漏三处。** 「ADR-0018（idea 条款）」不存在——ADR-0018 全文 grep `idea`/`选题`/`scope`/`无期` 零命中；idea 落盘的真正立规文件是 D27 spec（`archive/2026-09-21-ava-entry-idea-scope.md` §3.1「讨论内容落点——v1 不落盘……不新建 idea/ 目录」）。另漏：ADR-0023 补记第 2 条「idea 会话的历史独立，不与制片会话共用」（迁移后 idea 段成为期会话历史的一部分，措辞需注记）；Spec 10 风险表 RF-10 行（「不会带入本期」措辞，与 §2.5 第 4 条同一句话的另一处） | `grep -n "idea" docs/dev/adr/0018-*` 零命中；D27 spec §3.1；ADR-0023 L74-76；Spec 10 RF-10 行 | §4 的 ADR-0018 行改为 D27 spec，并补列 ADR-0023 补记第 2 条与 Spec 10 RF-10（D43-R 🟡-4 已是同类漏项先例，此处复发） |
| 🔵-2 | **「migrated 标注」的解析链格式未钉死，且两个口径互相矛盾。** ③c 说 stdout 标注「`migrated: true` 与会话段 sid」，① 却说 idea-note 的「N 条消息」「N 取自 core 返回」——N 没有来源字段；「stdout 末行」脆弱（TTY 路径后面还有 REPL 输出；将来加一行提示就破）；marker 缺失时（版本漂移、输出被截）host 当 `migrated: false` 还是报错，未规定 | spec §3.2 ③c 与 §3.3 ①；`service.ts::createEpisode` 现状只回 `{epKey}`、完全不解析 stdout | 写死单行机器可读格式（如 `[from-idea] migrated=true sid=<hex> messages=<n>`，恰好一行、stderr 不掺）；marker 缺失 → 按 `migrated: false` 处理并给 host 侧 notice（建期本身已成功，不许因解析失败把期卡死）；③c 字段清单补 `messages` |
| 🔵-3 | **「首会话用 `SESSION_CONTINUE` 起」缺机制落点。** renderer 发首条消息走 `conv.send` → `convTarget(convKey, "new")` 恒 `SESSION_NEW`（`service.ts`）；spec 未写谁把 `migrated: true` 变成首发时的 continue 语义（renderer 存旗标 → 首发先 `conv.resume` 再 `conv.send`？还是 `conv.send` 加 mode 参？）。`conv.resume` 对无会话记录期回 `continue_status: "no_session"` 是既有良行为，机制上可行，但两段 RPC 的回合归属与中途失败面要写清 | `service.ts::convSend` 恒 `mode: "new"`；`sessions.ts::resume` 的 E_BUSY 条件（`cur.phase !== "exited"`）与新期无会话兼容 | spec §3.3 ② 补一句机制（建议：`episode.create` 返回 `migrated` 后 renderer 记 `justCreatedMigrated`，该期首发走「先 resume 后 send」；H-8 不碰——resume 只是起进程）。附带：host 「先结束 idea 再建期」与 §2.10 `end()` 序列兼容（shutdown→SIGTERM→SIGKILL 必死，无 E_BUSY 冲突），但 idea 进程**结束失败/超时**时建期是否继续，未写——建议「结束不了就不建期，如实报错」 |
| 🔵-4 | **T-D42-7 进程内测不出互斥。** `EpisodeLease.acquire` 有进程内单例 `_LEASES`：同进程第二次 acquire 直接返回同一租约，不抛 `SessionLocked`——测试若在同一 pytest 进程里起两次「idea 会话」永远绿（假绿）。桌面端 SESSION_IDEA 重启场景（host 杀旧进程再起新的）由 flock 进程级语义保证，无 stale 锁 | `session_log.py::EpisodeLease.acquire` 的 `_LEASES` 查表段 | T-D42-7 写明用真子进程（`python -m pipeline.agent.protocol --idea` ×2 / 终端两进程），断言第二个拿到锁冲突错误 |
| 🔵-5 | **两处小事写清即可。** (a) 「读全部字节」会把源文件 torn tail（崩溃残行）一并复制进新期——行为正确（新期首次 --continue 的 `prepare_resume` 持锁截掉），spec 一句话写明即可；(b) 建期后 renderer 的 idea 视图仍显示旧讨论条目缓冲（`sessions.ensure` 重建 state 才清空），与「_idea 已清空、模型全新」不符 | `session_log.py::_parse_lines` 残行语义 + `prepare_resume` 的 `truncate_torn_tail`；`sessions.ts::ensure` 的 entries 重置 | (a) §3.2 c 加注「残行随复制带入、由新期恢复时的撕裂尾巴截断吸收」；(b) §3.3 补「建期成功后 host 清 idea 会话的条目缓冲（或显示『已带入 <期名>』本地提示）」 |

**攻击面逐项结论（含通过项）**：

- **① 落盘改造面**：`ep_dir is None` 的依赖全链为——`ensure_lease`（None → 不取租约）、`matches`、`_episode_name`（""）、`_assemble`（`build_idea_card`）、`protocol._run_turn`（`persist=not idea`、stop_points 空帧）、`review_tool_call` 的 `NEEDS_EPISODE_TOOLS`「先建期」、`log_approval_decision`（库级 `data/_events.jsonl`，D43 已实测）、`_fetch_cards`（`paths.DATA` 与 root/data 同源检查）。全部是「无期」语义，与「日志目录」无关，解耦方向正确，未发现会把 `data/_idea` 当期目录用的漏网分支；出网断言可信集（`tracker.trusted_doc_texts` ∪ `route_trusted_texts(scope, root)`）与 `ep_dir` 无关，不受影响。关键纪律是 **`ep_dir` 保持 `None`**：若实现误把它指向 `data/_idea`，`write_episode_file` 会被 tools.py「期目录必须在 data/episodes 之下」的纵深防御拦死（fail-closed），`read_artifact` 读不到 `.jsonl`，但 `inspect_episode` / 状态卡会错乱——D43 既有用例（TA-6 等）构成回归网。persist=True 带进 idea 的行为逐项成立：检查点卡、中断收尾、`_rollback`（只追加 `turn_rollback`）、`plan_repairs` 都是落盘正确性所需，无炸点。
- **② 租约**：flock 是进程级、进程死锁即释，无 stale 锁判定问题；终端 `ava idea` ×2、桌面端 SESSION_IDEA ×2、终端+桌面互斥同一条机制；桌面重启场景无碍。唯一缺口是目录缺失语义（🟡-1）与同进程单例对测试的影响（🔵-4）。
- **③ 迁移原子性与回放**：探针 `scratchpad/d42r/probe_migrate.py` 实测（含回滚回合 + 既有 `repair_tool_results` + 未关闭请求的 idea 段，整段字节复制进新期后 `prepare_resume`）：回滚回合消息丢弃 ✓、修复结果插回所属 assistant 之后 ✓、tool 配对完整 ✓、未闭请求在新期恢复时追加 `request_closed{voided}`（写进新期日志、不回写 `_idea`）✓、源文件一字不动 ✓、清空后 `resume_target` 无候选 ✓、旧常驻层作为历史消息原样重放（与既有 --continue 行为一致，非新问题）✓。例外：重复修复（🟡-2）。「先建期后迁移」在 `cli.py` 现有结构上落得了：`create_new_episode` 是独立函数、失败原样返回，`--from-idea` 在其成功后加一段即可；bad 行归属（`load_session` 按 sid 过滤 + 坏行位置判定）对整段复制天然成立。
- **④ 清空即删除**：截断在持租约的 fd 上 `ftruncate(0)`，与 `truncate_torn_tail` 同机制、无语义冲突，但模块纪律条要修订（🟡-3）；「复制成功但截断失败 → 重复带入」属实，需 spec 写死失败形态（🟡-3）；残行复制无害（🔵-5a）。
- **⑤ 桌面端链路**：`NEW_EPISODE` 恒加 `--from-idea` 的失败形态：`createEpisode` 现状是 core 非零 → `E_CORE` + stderr 尾部原样显示，期已建为空期（spec 的原子性约定保证）——但「重试建同名期会撞『期目录已存在』」的恢复指引未写，建议补一句。「host 先结束 idea 进程再建期」可行（`sessions.end` 走 §2.10 序列），与 E_BUSY 不冲突（`createEpisode` 只查 quitting/choosing/switching）。`convTarget` 的 idea resume 报错是 UI 死代码（SessionHeader 的「继续上次会话」按钮 `!isIdea` 才渲染），删除安全，无其他调用方依赖。解析链与首会话机制见 🔵-2/🔵-3。
- **⑥ R4 反面**：成立，见上文核实清单。无备份机制、无 web 读域、无 library 消费者会触及 `data/_idea`；`status` / `resolver` / `inspect_episode` 全部只认 `data/episodes/` 下或显式路径。
- **⑦ 测试与变异**：T-D42-1~7 与 MUT-D42-a~e 的指定杀手逐条对得上；「迁移成功但 sid 改了」被 T-D42-2 的「字节一致」断言杀死；「恒恢复恢复出别人的段」在单文件单逻辑会话下不构成回退（最新可恢复段唯一）。杀不死的回退有两处，均已列阻塞：清空失败的容错路径（🟡-3）、`_idea` 缺失首启（🟡-1）。TP-16 改写可行（「不写 session.jsonl」→「写 `data/_idea/session.jsonl`、期目录零产出」），改写后仍杀 MUT-62（`error` vs `turn_started` 之别不动）。
- **⑧ 文档修订面**：§4 错指 ADR-0018 并漏三处（🔵-1）；与 Spec 10 门禁 12 的更新、与 ACC 的并入关系自洽（§7 ⑤ 写明走更新版门禁 12、打包版真机手验并入 ACC）。

**探针**（全部在 scratchpad，未进仓库）：`d42r/probe_migrate.py`（③ 迁移回放全链）、`d42r/probe_double_resume.py`（🟡-2 隔离复现：纯单期两次恢复，与迁移无关的 HEAD 既有行为）。

## 作者修订回应（v0.2，2026-10-08；修订人 = 立文 session，人明示准许；🟡-2 人同日裁决 (A)）

逐条核过证据再改：🟡-1（`EpisodeLease.acquire` 对不存在目录抛 `SessionLogBroken`、协议 ④⑤ 全包在 `ep_dir is not None` 里、`ensure_lease` 吞异常 + `_record` 静默 return——探针核实属实）、🟡-2（复跑红队探针 `d42r/probe_double_resume.py`：HEAD 上两次恢复后 repair 记录 1→2 条、重建 tool 消息 1→2 条，属实）、🟡-3（`session_log` docstring「唯一截断」原文属实；shipped 矩阵无清空失败变异，属实）、🔵-1（ADR-0018 grep 零命中属实；三处漏项逐行核对命中）、🔵-4（`_LEASES` 进程内单例属实）。

| 编号 | 处置 | 改了哪里 |
|---|---|---|
| 🟡-1 | **采纳**：启动降级路径写死——① idea 启动（终端与协议同口径）先查 `data/` 可达（不可达 → 终端报错退出非零 / 协议 `E_DATA_UNREACHABLE`，与期会话同闸）；可达而 `_idea` 不存在则由 core 创建（先例：`log_approval_decision` mkdir `_agent/`；这不是「自动创建 data/」）；② 租约拿不到一律显式报错退出，**禁止静默非持久运行**；③ 补 T-D42-8（`_idea` 缺失首启自建 + data/ 不可达拒启动）与变异 MUT-D42-f | §3.1 新增「启动与降级」段、§6 |
| 🟡-2 | **采纳，人 2026-10-08 裁决 (A)**：本 spec 顺手修 `plan_repairs`——把既有 `repair_tool_results` 覆盖的 `tool_call_id` 计入 `satisfied`（改动局部在 `session_log.py`，配变异 MUT-D42-g）；T-D42 清单加 T-D42-9（含既有 repair 记录的 idea 段迁移后 `--continue`，重建的 tool 消息不重复），修后该用例落地即绿 | §3.2 新增第 6 条、§6 |
| 🟡-3 | **采纳**：d（清空）失败形态写死——退出非零 + stderr 明示「建期成功、迁移成功、清空失败：`_idea` 记录未清，下次 `--from-idea` 会重复带入，请手动清空 `data/_idea/session.jsonl`」（诚实失败，不许静默 0）；`session_log` docstring 加第二截断点注记（时机：持租约、整段复制成功之后）；新增 T-D42-3b（注入截断失败）与变异 MUT-D42-h | §3.2 d、§3.2 原子性约定、§4、§6 |
| 🔵-1 | **采纳**：§4 的 ADR-0018 行改为 D27 spec（`archive/2026-09-21-ava-entry-idea-scope.md` §3.1「v1 不落盘」）；补列 ADR-0023 补记第 2 条（「idea 历史独立」措辞注记）与 Spec 10 风险表 RF-10 行 | §4 |
| 🔵-2 | **采纳**：标注格式钉死为单行机器可读 `[from-idea] migrated=<true|false> sid=<hex|-> messages=<n>`（恰好一行、stdout、stderr 不掺）；marker 缺失/不合式 → host 按 `migrated: false` 处理并发 notice（建期已成功，不许因解析失败把期卡死）；③c 字段清单补 `messages` | §3.2 ③c、§3.3 |
| 🔵-3 | **采纳**：§3.3 ② 机制写死——`episode.create` 返回 `migrated: true` 后 renderer 记 `justCreatedMigrated`，该期首发走「先 `conv.resume` 后 `conv.send`」两段（`conv.resume` 对无会话记录期回 `no_session` 是既有良行为；resume 只是起进程，H-8 不碰）；两段中途失败面写明；附带：idea 进程结束失败/超时 → **不建期，如实报错**（E_BUSY 类，期未建） | §3.3 |
| 🔵-4 | **采纳**：T-D42-7 写明用真子进程（`python -m pipeline.agent.protocol --idea` ×2），断言第二个拿到锁冲突错误；不许进程内双 acquire（`_LEASES` 单例会假绿） | §6 |
| 🔵-5 | **采纳**：(a) §3.2 c 加注「残行随复制带入、由新期恢复时的撕裂尾巴截断吸收」；(b) §3.3 补「建期成功后 host 清 idea 会话的条目缓冲，并显示『已带入 <期名>』本地提示」 | §3.2 c、§3.3 |

另吸收红队攻击面 ⑤ 的一句补充（不编号）：桌面端「重试建同名期撞『期目录已存在』」的恢复指引写进 §3.3（期已建为空期时，重试需换名或进该期继续，文案由 host 原样透传 core 的 `E_CORE` + stderr 尾部，不另造恢复通道）。

## 定向复审（2026-10-08，D42-R2；复审人 = D42-R 红队 session，未参与 v0.2 修订；只审文稿，未改仓库代码）

**裁决：🟢 可动工**。8 条逐条核销全部成立——每条都去回应表指向的正文看过「真的改了」、改法能堵住原指控、用例与变异的指定杀手对得上；v0.2 新增面的四个攻击点核完，修法探针三场景实测通过。无需人裁决。2 条非阻断残余，施工 PR1 顺手清理即可：

| 编号 | 性质 | 内容 | 处置建议 |
|---|---|---|---|
| 余-1 | 措辞残余（不阻断） | 🔵-1 的误指在 §4 本体已修正，但两处导航面残留：本文件文首「触及冻结面」行仍写「ADR-0018（idea 条款）」（ADR-0018 全文无 idea 条款，v0.2 未改该行）；`plans/README` 的 Spec 18 行与 issues D42 行「相关」列仍列 ADR-0018 | PR1 同一 PR 内把文首该行改为 D27 spec；两个「相关」列随施工回填同步（与本表同行改，不另立项） |
| 余-2 | 边界记录（不阻断） | 🟡-2 的修法是 **id 级满足**：同一 session 内 `tool_call_id` 被复用且后者缺结果时，后者按「已满足」不再补修（探针场景 (c) 实测）。触发条件是同 session id 复用——OpenAI 兼容端点每响应新生成 call id，实际不可达；且修复前同场景产出的是「两条同 id tool 消息」，照样 400。修法没有把任何可达场景从好变坏，只是把一种病态坏法换成另一种 | §3.2 第 6 条加半句「satisfied 为 id 级口径；同 session tool_call_id 复用属病态历史，不在本修复范围」 |

**逐条核销**：🟡-1 → §3.1「启动与降级」三条写死（data/ 不可达同闸拒启、`_idea` 缺失 core 自建、租约失败禁止静默非持久）+ T-D42-8 + MUT-D42-f ✓（MUT-D42-f 的「静默吞」变异会被 T-D42-8 的拒启动腿杀死）；🟡-2 → §3.2 第 6 条（修法代码原文写死）+ Spec 9 §2.8 注记入 §4 + T-D42-9 两腿 + MUT-D42-g ✓；🟡-3 → §3.2 d 失败形态（非零 + stderr 手动指引）+ `session_log` docstring 注记入 §4 + T-D42-3b + MUT-D42-h ✓；🔵-1 → §4 的 ADR-0018 行已改 D27 spec、ADR-0023 补记与 RF-10 已补 ✓（残余见余-1）；🔵-2 → marker 单行格式写死（`[from-idea] migrated=<true|false> sid=<hex|-> messages=<n>`、恰好一行、stderr 不掺、缺失按 false + notice）+ §3.3 解析链 + §6 host 单测 ✓；🔵-3 → §3.3 ②「先 resume 后 send」+ 失败面 + idea 进程结束失败不建期 ✓；🔵-4 → T-D42-7 真子进程写明 ✓；🔵-5 → §3.2 c torn-tail 注 + §3.3 ④ 清条目缓冲 ✓；⑤ 同名期重试指引已吸收 ✓。

**v0.2 新增面攻击结论**：

- ① **plan_repairs 修法**：探针 `scratchpad/d42r/probe_fix_intent.py`（按 §3.2 第 6 条原文在进程内打补丁复刻，不改仓库代码）三场景实测——(a) 崩溃后双恢复：repair 记录 1→1、重建 tool 消息 1→1（重复修复消除）；(b) 旧修复在场时新崩溃的另一 call 仍被补修（repair 1→2 恰一条，不漏修）；(c) id 冲突边界如实（余-2）。坏行形态的 repair 记录走 `load_session` 的 corrupt 拒恢，不会静默满足。修法在现有结构上落得下去。
- ② **启动降级兼容性**：protocol ④ 从「`ep_dir` 不 None 才查」扩为无条件查不误伤既有用例——`test_agent_protocol.py` 三处 `--idea` 用例（含 TP-16）的假根 `_make_root` 都建 `data/`（L309-310）；终端 `ava idea` 新增 data 检查是意图中的行为变化，由 T-D42-8 覆盖。
- ③ **marker 与两段式首发**：`createEpisode` 现状有 `r.stdoutTail` 在手，NEW_EPISODE 输出极小不会截断，单行解析可行；`sessions.ts::resume` 的 E_BUSY 条件核实——新期无 state 直接 spawn `SESSION_CONTINUE`，resume 后进程 idle、`send` 走同一进程，**无死锁/竞态**（唯一瞬时态：resume 未就绪时 send 得 E_BUSY，可重试，非缺陷）；`no_session` 经 snapshot 的 `continue_status` 如实呈现，与 spec 措辞对得上。
- ④ **新用例/变异可写性**：T-D42-8（缺失自建 + 悬空 data 拒启）、T-D42-9（两腿断言不重复、盘上 repair 不新增）、T-D42-3b（注入 `ftruncate` 失败 → 非零 + stderr 指引 + 两文件状态）均可落成断言；MUT-D42-f/g/h 的指定杀手逐条对得上。

**探针**（scratchpad，未进仓库）：`d42r/probe_fix_intent.py`（① 三场景）。

## 1. 痛点（人 2026-09-29 指出；D43 后仍成立的部分）

人在左栏「选题」（idea 会话）里与模型聊透张力、锚点与 `01-topic.md` 草案后，点「＋ 新建一期」只建空目录、切到全新的 creative 会话，并显示「选题会话的讨论不会带入本期；需要的要点请在这里重述」（`App.tsx` `idea-note`）。上下文彻底断裂，模型无法直接把刚聊好的内容写进 `01-topic.md`，逼人充当复读机。

（D43 已解决的那半：idea 会话现在可见全部 13 个工具——含联网三件；但**需期工具在无期会话统一报「先建期」**（Spec 17 §3.4），所以「在 idea 里直接写 `01-topic.md`」仍须先建期——本 spec 把「建期」与「带入上下文」合成一步。）

## 2. 人的既有裁决（不再争议）

- D42-A 选型（2026-10-06）：方案 (a) 转正继承；联网三件含 `crawl`（已被 D43 覆盖）；**同一时刻只保留一个选题会话**（建期即转正并结束，再点「选题」开新的）。
- 期名由人拍板、不从对话预填（Spec 10 §2.5 第 2 条、TG-15）；建目录由人点按钮、core 执行。
- H-8：host 不代发人没打过的 `user_message`（带入 = 复制记录 + 起进程，不是发消息）。
- D43 / ADR-0027：所有模式开放全部工具；无期会话调需期工具统一报「先建期」。

## 3. 设计

### 3.1 idea 会话落盘：`data/_idea/session.jsonl`

- idea 会话从「不落盘」改为落盘到 **库级** `data/_idea/session.jsonl`，与期会话**同一格式、同一租约机制**（`session_log.py` 的 append-only / flock / `rebuild_messages` 全部复用，零新格式）。
- `_` 前缀不进期列表（`cli.py` 的 `hidden_underscore` 既有机制，`list_episodes` 与桌面端期列表都不见它）；`status` / `resolver` / `inspect_episode` 一律不触碰 `data/_idea/`。
- **工具语义不变**：idea 会话的 `ep_dir` 仍是 `None`（需期工具照报「先建期」；读域仍只有 `data/library/`）。落盘目录只是「日志的家」，不是期目录——实现上把「日志目录」从「期目录」解耦：`SessionHost` 增加日志目录概念，idea 的日志目录固定为 `data/_idea/`，租约取在该目录上。
- **租约即「同一时刻只留一个选题会话」的机制保证**：第二个 idea 会话进程拿不到 `data/_idea` 的租约 → 既有 `SessionLocked` 路径（报「另一个选题会话进行中」）。
- **恢复**：`--idea` 恒恢复 `data/_idea/session.jsonl` 里最近的可恢复段（无需 `--continue` 与 sid——逻辑上只有一个选题会话）；`messages[0]` 常驻层按 `idea.md` 现装（既有「不落盘、恢复时按当前 scope 重建」机制原样适用）。无可恢复段 = 全新选题会话。
- 批准记录落点不变（D43 §3.4 已接受的口径）：库级 `data/_events.jsonl`，期级 `approvals.jsonl` 不产生。

**启动与降级路径（v0.2，🟡-1，写死）**：
- idea 启动（终端 `ava idea` 与协议 `--idea` 同口径）**先查 `data/` 可达**：不可达 → 终端报错退出非零 / 协议回 `E_DATA_UNREACHABLE`——与期会话同一道闸（`protocol.py` ④ 的检查从「`ep_dir is not None` 才查」扩为无条件查）。
- `data/` 可达而 `data/_idea/` 不存在 → **由 core 创建**（先例：`log_approval_decision` 自建 `_agent/`；这不是「自动创建 data/」——data/ 不可达仍然拒启动）。
- 租约拿不到（锁冲突 / 其他错误）一律**显式报错退出**（终端非零 / 协议 `E_SESSION_LOCKED` 或同等错误帧），**禁止静默非持久运行**——`ensure_lease` 的懒取静默路径对 idea 不适用；idea 会话若不能落盘就必须当场说，不许退回「退出即丢」（那是本 spec 要消灭的行为）。

### 3.2 建期迁移：`ava new <名> --from-idea`

core 侧 `pipeline.agent.cli` 的 `new` 子命令加 `--from-idea` 旗标（不带时行为一字不变）：

1. 先照旧 `create_new_episode`（失败即原样返回，零新增行为）。
2. 建期成功后迁移（**顺序写死：先建期、后迁移**，保证任何失败都留下一个合法空期）：
   a. 取 `data/_idea` 租约（独占，含读与清空）——**拿不到（另一选题会话进行中）→ 报错退出非零**：新期保留为干净空期，`data/_idea/session.jsonl` 一字不动，错误文案明示「选题会话进行中，请先退出它再建期带入」；
   b. 读 `data/_idea/session.jsonl` 全部字节；**无可迁移记录**（文件不存在 / 无任何含 assistant 消息的会话段）→ 不算失败：stdout 标注 `migrated: false`（格式见下），新期为空期，正常返回 0；
   c. 整段**追加写入**新期的 `session.jsonl`（新期该文件尚不存在 → 等同复制；sid、seq、ts 一字不改，append-only 语义不破）；（v0.2，🔵-5a 注：源文件的撕裂残行随复制一并带入，行为正确——新期首次 `--continue` 的 `prepare_resume` 持租约截掉它，`truncate_torn_tail` 是既有机制）；
   d. 迁移成功后**清空** `data/_idea/session.jsonl`（持租约在已持有的 fd 上 `ftruncate(0)`，截断为空文件，保留文件与锁语义；「建期即转正并结束」的落盘点）。**d 失败 → 退出非零 + stderr 明示**「建期成功、迁移成功、清空失败：`_idea` 记录未清，下次 `--from-idea` 会重复带入，请手动清空 `data/_idea/session.jsonl`」（诚实失败，不许静默返回 0；v0.2，🟡-3）。
   `session_log.py` 的模块纪律同步修订（§4）：「整个模块里唯一的截断是 `truncate_torn_tail()`」加注第二截断点——`_idea` 清空，时机：持租约、整段复制成功之后。

   **机器可读标注（v0.2，🔵-2，写死）**：core 在 stdout 输出**恰好一行** `[from-idea] migrated=<true|false> sid=<hex|-> messages=<n>`（migrated=false 时 sid 为 `-`、messages 为 0）；stderr 不掺。host 只认这一行；marker 缺失或不合式 → 按 `migrated: false` 处理并发 host 侧 notice（建期本身已成功，不许因解析失败把期卡死）。
3. **原子性约定**（选型稿既定）：b 的读取失败或 c 的写入失败 → 新期保持干净空期（**不回滚建期**）、idea 记录原样保留、报错退出非零并在 stderr 写明「建期成功、迁移失败」。c 的写入走「先写临时文件再 `os.replace`」级别的原子落盘（复用 `paths.atomic_write` 对临时副本操作后改名到位，或直接整段一次性 `os.write` 后 fsync——实现二选一，验收看效果：任何时刻 `session.jsonl` 要么是空/不存在、要么是完整副本，不许出现半份）。
4. 迁出的段在新期里就是一段普通会话记录：`--continue`（无前缀）即可恢复它——`resume_target` 既有规则「最新且含 ≥1 条 assistant 消息」天然命中。**常驻层按新期当前 scope（creative）现装、01 工序规程注入**，都是 `--continue` 的既有机制，零新代码路径。
5. 历史回放安全：idea 段里的工具调用记录（`read_status` / `web_search` / 被拒的 `write_episode_file`（「先建期」回执）等）作为消息原样回放；D43 后工具表全模式全量，不存在「当前 scope 工具表里没有的工具」的悬空调用。`rebuild_messages` 对回滚回合的丢弃逻辑对 idea 段同样成立（idea 会话也可能有回滚回合）。
6. **顺手修 `plan_repairs` 的重复修复（v0.2，🟡-2，人 2026-10-08 裁决 (A)）**：`session_log.py::plan_repairs` 的 `satisfied` 只数 `msg{role:tool}`，不认已追加的 `repair_tool_results`——含既有修复记录的段每恢复一次就重复追加一份修复、重建出两条同 `tool_call_id` 的 tool 消息（多数 API 直接 400；HEAD 既有缺陷，红队探针 `d42r/probe_double_resume.py` 实测，本 session 复跑核实：两次恢复后盘上 repair 记录 1→2 条、重建 tool 消息 1→2 条）。修法写死：`plan_repairs` 统计 `satisfied` 时，**把既有 `repair_tool_results` 记录的 `results[].tool_call_id` 一并计入**（追加遍历：`
for record in records:
    if record.get("k") == "repair_tool_results":
        for item in record.get("results") or []:
            satisfied[str(item.get("tool_call_id"))] = True
`）。改动局部、不动 `rebuild_messages`；Spec 9 §2.8 的修复语义注记同步（§4）。satisfied 为 id 级口径；同 session tool_call_id 复用属病态历史，不在本修复范围（D42-R2 余-2）。这条修复对「同一期崩溃后多次 `--continue`」的既有路径同样生效（不限于迁移链）。

### 3.3 桌面端

- `episode.create`：host 的 `NEW_EPISODE` 模板 argv 恒加 `--from-idea`（无记录时 core 返回 `migrated: false`，语义等价于现状，零分叉）；core 的 stdout 按 §3.2 的单行 marker（`[from-idea] migrated=<true|false> sid=<hex|-> messages=<n>`）解析，经 `episode.create` 的返回值带 `{ migrated, sid, messages }` 给 renderer；**marker 缺失或不合式按 `migrated: false` + host notice**（🔵-2）。core 非零 → 照旧 `E_CORE` + stderr 尾部原样显示，期已建为空期（原子性约定保证）；此时**重试建同名期会撞「期目录已存在」**——恢复指引：换个期名重试，或直接进入已建的那个空期继续（host 不另造恢复通道，文案原样透传）。
- `migrated: true` 时：① renderer 的 `idea-note` 文案改为「已带入选题会话记录（N 条消息）」（N 取自 marker 的 `messages` 字段；**migrated: false 时旧文案不变**——idea 没东西可带，提示照旧成立）；② 该期的**首个**会话有记录可续：机制写死（v0.2，🔵-3）——renderer 记 `justCreatedMigrated=<epKey>`，该期首发走**「先 `conv.resume` 再 `conv.send`」两段**（`conv.resume` 让 host 以 `SESSION_CONTINUE` 起进程——迁入段是新期里唯一会话段，`--continue` 无前缀必中；resume 只是起进程，不是代发消息，H-8 不碰）。中途失败面：resume 失败（如 `no_session`）→ 如实显示、不代发，`conv.send` 仍可走 `SESSION_NEW` 兜底；send 段失败照旧有既有重试；③ 若 idea 会话进程活着（`SESSION_IDEA` 在跑），host 先结束它再建期——否则 core 拿不到 `_idea` 租约必报「进行中」。**结束顺序**：host 结束 idea 进程 → `NEW_EPISODE --from-idea` → 切期 → 首发「先 resume 后 send」。**idea 进程结束失败/超时 → 不建期，如实报错**（E_BUSY 类，期未建、idea 记录不动；v0.2，🔵-3 附带）。④ 建期成功后 host 清 idea 会话的条目缓冲，并在选题视图显示「已带入 <期名>」本地提示（v0.2，🔵-5b）。
- 「再点『选题』开新的」：`SESSION_IDEA` 照旧懒启动；`data/_idea/session.jsonl` 已是空文件 → 全新会话。app 重启后点「选题」→ core 恢复最近的选题段（3.1 的恒恢复）。
- `sessions.ts` 的「idea 会话不支持『继续上次会话』」报错（`convTarget`）随落盘删除：idea 会话的「继续」= 恒恢复最近段，由 core 在 `--idea` 启动时完成，不需要独立的 resume 指令（红队核实：该报错是 UI 死代码，`SessionHeader` 的「继续上次会话」按钮 `!isIdea` 才渲染，删除安全）。

### 3.4 终端

- `ava idea`：照旧进选题会话，但现在落盘并恒恢复最近段（退出后再进，讨论还在）。
- `ava new <名> --from-idea`：同 3.2；随后 `ava <名>`（或建期后直接进对话的既有行为）即带着选题记录继续。
- 若终端的选题会话正在另一个进程里开着：`--from-idea` 报「选题会话进行中」（3.2 的 a），不抢锁。

### 3.5 文案同步

- `idea.md` 的「产出与落盘」一节按选型稿既定改为「建期后在同一会话里写」（D43 施工时的过渡文案「需要写 `01-topic.md` 时先请人建期」随之更新）；`cli.py` idea 横幅与 `_print_idea_non_tty_help` 同步（「退出即丢」类措辞删除，改为「会话记录保存在库级，建期时可带入」）。
- `build_idea_card` 的「产出落盘」行改为「定稿后点『＋ 新建一期』，选题讨论自动带入新期」。

## 4. 冻结面影响与文档修订清单（施工同一 PR 内完成，archive 原文加修订注记：日期、D42、人裁决原话）

- **Spec 9**（`archive/2026-09-25-agent-session-protocol-spec.md`）：§2「idea 会话不落盘：无期目录可挂」一条改为「落盘到 `data/_idea/session.jsonl`、恒恢复最近段」（该条已有 D43 注记，本 spec 再改「不落盘」半句）；「只有主会话落盘 / 子会话退出即丢」（§2.5 附近）加 idea 例外的注记；入口行（§2 「`--continue` 或 `--idea`」）注记 `--idea` 启动即恒恢复、无需显式 `--continue`；TP-16 行加注（「不写 session.jsonl」已改，用例改写）。MUT-62 锚点与断言复核。
- **Spec 9 §2.8 修复语义注记**（v0.2，🟡-2 人裁决 (A)）：`plan_repairs` 的 `satisfied` 计入既有 `repair_tool_results` 所覆盖的 `tool_call_id`——「修复一律以追加记录完成」不变，追加的「不重不漏」口径补上「漏了会重复追加」一侧。
- **Spec 10 §2.5**：第 1 条（`SESSION_IDEA` 语义：无期目录、**落盘到库级、可恢复**）；第 4 条（`idea-note` 两态文案 + 迁移成功后首发「先 resume 后 send」）；第 5 条（「idea 会话保留在后台，可切回继续聊或再建一期」→「建期即转正并结束；同一时刻只保留一个选题会话」人 2026-10-06 裁决原话）。**风险表 RF-10 行**（「不会带入本期」措辞）同步加注（v0.2，🔵-1）。
- **D27 spec**（`archive/2026-09-21-ava-entry-idea-scope.md`，idea 的立规文件；v0.2 🔵-1 更正——v0.1 误指 ADR-0018，后者全文无 idea 条款）：§3.1「讨论内容落点——v1 不落盘……不新建 idea/ 目录」加修订注记（落盘到 `data/_idea/`，库级、恒恢复、建期可带入）。
- **ADR-0023 补记第 2 条**（v0.2，🔵-1 补列）：「idea 会话的历史独立，不与制片会话共用」加注——建期迁移后 idea 段整体成为新期会话历史的一部分（一次性、人点建期触发），注入范围不受影响。
- **impl spec**（`2026-09-18-ava-agent-impl-spec.md`）文首 ① 与 §2.3 的 `ava idea` 行：补「落盘到库级、建期可带入」注记。
- **`session_log.py` 模块 docstring**（v0.2，🟡-3）：「整个模块里唯一的截断是 `truncate_torn_tail()`」加注第二截断点——`_idea` 清空（时机：持租约、整段复制成功之后）。
- **README**「已知限制」：D42 行随施工删除（评审通过后）。

## 5. 风险与攻击面（给红队）

| # | 风险 | 本 spec 的判断 | 请红队核 |
|---|---|---|---|
| R1 | 迁移把 idea 段塞进新期后，`rebuild_messages` 回放破坏协议不变量（工具调用与 tool 结果配对、回滚回合、修复记录） | 整段字节级复制，sid/seq 不动；`load_session` 的坏行归属与 `rebuild_messages` 对跨段文件本就成立（一个文件多段会话是既有形态） | 构造含回滚回合与 repair_tool_results 的 idea 段，迁移后 `--continue` 重建，逐条核对消息序 |
| R2 | 迁移竞态：选题会话进行中另开终端 `--from-idea` | `_idea` 租约独占，拿不到即报错退出非零、新期留空期、idea 记录不动 | 核租约的持有者存活判定（Stale 锁、进程死而未释） |
| R3 | 「清空 `_idea/session.jsonl`」是删除类动作 | 只截断为**空文件**，不删目录不删文件；且发生在整段复制**成功之后**；这是人裁决 Q3「建期即转正并结束」的直接落地，在 spec 里显式声明。v0.2（🟡-3）：截断失败形态已写死——退出非零 + stderr 明示手动清理指引（诚实失败，不静默 0），§3.2 d | 判截断时机与失败面（复制成功但截断失败 → 会重复带入——v0.2 已定：不静默，报错并给手动指引） |
| R4 | `data/_idea` 被当期的误处理：`status`、`resolver`、备份、桌面端期列表 | `_` 前缀既有隐藏机制；spec 写死「status/resolver 不触碰」并配用例 | 全仓 grep 还有没有按 `data/episodes/*` 通配的新消费者会被 `_idea` 混进（注意：`_idea` 在 `data/` 下不在 `data/episodes/` 下——期目录枚举天然够不着它；核桌面端 `episodes` 列表来源） |
| R5 | 迁入的 idea 段里可能含选题阶段的敏感探索（被否掉的选题） | 期内容本来就给人看；不处理 | 无（列出即可） |
| R6 | `migrated: false` 被桌面端误当成功带入 | core 的标注是结构化输出的唯一来源；renderer 只认它，不做字符串猜 | 核 `episode.create` 返回链路exactly-once 与解析失败形态 |

## 6. 测试与变异（施工时按实现跑出期望值再写断言；变异一律 `PYTHONDONTWRITEBYTECODE=1`、还原 md5 对拍）

**新增**：
- T-D42-1（落盘与恒恢复）：idea 会话两轮后退出 → `data/_idea/session.jsonl` 存在且含两段消息；再开 `--idea` → 历史回放进 messages（常驻层为 idea.md 现装，不落盘的 `messages[0]` 断言不变）；期列表不含 `_idea`。
- T-D42-2（迁移全链）：idea 段就位后 `ava new 02-x --from-idea` → 返回 0、`migrated: true`；新期 `session.jsonl` 与原段字节一致；`_idea/session.jsonl` 为空文件；`--continue` 恢复新期会话 → messages 含 idea 回合的用户与 assistant 原文，常驻层已是 creative + 01 规程。
- T-D42-3（原子性）：注入复制失败（占住新期 `session.jsonl` 路径为目录 / 只读）→ 退出非零、新期空且合法（目录与模板文件在、`session.jsonl` 无半份）、`_idea` 记录逐字节不动、stderr 写明「建期成功、迁移失败」。
- T-D42-4（无记录）：`--from-idea` 在 `_idea/session.jsonl` 不存在与「只有 user 无 assistant 的段」两种形态下都 `migrated: false`、返回 0、新期为空。
- T-D42-5（隐藏）：`list_episodes`、桌面端期列表源、`ava`（无参选期列表）都不出现 `_idea`；`inspect_episode`/`status` 不读 `data/_idea`。
- T-D42-6（转正后可写）：迁移后新期会话里模型调 `write_episode_file("01-topic.md")` → 正常弹人审卡（不再是「先建期」），批准后落盘。
- T-D42-7（单一选题会话）：第二个 `--idea` 进程在第一个存活时启动 → 报「另一个选题会话进行中」类错误，不开第二份。（v0.2，🔵-4）**必须真子进程**（`python -m pipeline.agent.protocol --idea` ×2 / 终端两进程），断言第二个拿到锁冲突错误；不许进程内双 `acquire`——`_LEASES` 进程内单例会假绿。
- T-D42-8（v0.2，🟡-1③）：`_idea` 缺失首启自建——删掉 `data/_idea/` 后起 `--idea` → core 创建目录、会话正常落盘；`data/` 不可达（悬空链接）时 idea 拒启动——终端退出非零 / 协议 `E_DATA_UNREACHABLE`，与期会话同闸。夹具**不许**预先 mkdir `_idea`（否则永远测不到自建路径）。
- T-D42-9（v0.2，🟡-2 人裁决 (A)）：含既有 `repair_tool_results` 记录的会话段——纯单期两次 `--continue`（红队探针的回归化）与「idea 段迁移后新期 `--continue`」两腿，重建的 tool 消息**不重复**、盘上 repair 记录不新增。修 `plan_repairs` 后该用例落地即绿。
- T-D42-3b（v0.2，🟡-3）：注入清空步骤（`ftruncate`）失败 → 退出非零、stderr 明示「建期成功、迁移成功、清空失败……请手动清空」、新期完整、`_idea` 记录逐字节不动。
- 桌面端 e2e（临时副本）：idea 聊一轮 → 建期 → 新期首会话带历史（assistant 原文可见）、`idea-note` 显示「已带入」、再点「选题」是全新空会话；`migrated: false` 路径（idea 没聊过就建期）→ 旧文案照旧。
- marker 解析（v0.2，🔵-2）：host 单测——`[from-idea] migrated=true sid=<hex> messages=<n>` 恰好一行的解析；marker 缺失 / 不合式 → `migrated: false` + host notice，期不卡死。
- TP-16 改写：「不写 session.jsonl」→「写 `data/_idea/session.jsonl`、期目录零产出」。

**变异**（指定杀手；登记进 `scripts/verify_mutations.py` shipped 矩阵）：
- MUT-D42-a：迁移只建期不复制记录 → T-D42-2 杀。
- MUT-D42-b：复制后不清空 `_idea`（同一逻辑会话存在于两处，Q3 被破）→ T-D42-2 的「`_idea` 为空文件」断言杀。
- MUT-D42-c：复制失败时回滚建期（删新期，违反原子性约定）→ T-D42-3 杀。
- MUT-D42-d：`_idea` 进期列表 → T-D42-5 杀。
- MUT-D42-e：`--idea` 不恢复历史（恒开新段）→ T-D42-1 杀。
- MUT-D42-f（v0.2，🟡-1③）：`_idea` 租约失败被静默吞（idea 退回非持久运行）→ T-D42-8 杀。
- MUT-D42-g（v0.2，🟡-2）：`plan_repairs` 不把既有 `repair_tool_results` 计入 `satisfied`（即退回 HEAD 既有缺陷）→ T-D42-9 杀。
- MUT-D42-h（v0.2，🟡-3）：清空失败静默返回 0 → T-D42-3b 杀。

## 7. PR 划分、验证与门禁

- PR1（core）：`session_log`/`session.py` 的日志目录解耦、`--idea` 落盘与恒恢复（含启动降级路径 §3.1）、`new --from-idea`（含 marker 输出与 d 失败形态）、**`plan_repairs` 重复修复修复（🟡-2 (A)，含 Spec 9 §2.8 注记）**、cli/protocol 接线、测试与变异、Spec 9 / impl spec / D27 spec / ADR-0023 补记修订注记、`session_log` docstring 第二截断点注记。
- PR2（desktop）：`NEW_EPISODE` 模板加 `--from-idea`、`episode.create` 返回 `migrated`/`sid`/`messages`（marker 单行解析 + 缺失兜底 notice）、idea 进程先结束再建期（结束失败不建期）、首发「先 resume 后 send」、`idea-note` 两态、`convTarget` 的 idea resume 报错删除、建期后清 idea 条目缓冲 + 「已带入」本地提示、e2e。
- 验证：全量 `uv run pytest` 全绿；`cd desktop && npx vitest run`、`npx tsc --noEmit`、全量 `npx playwright test`（workers 2，临时副本）全绿；变异逐条回填。
- 门禁：① 新增与改写用例全绿、改写逐条可追溯；② MUT-D42-a~h 全杀并登记 shipped 矩阵；③ 真会话冒烟（临时仓库副本、真 LLM）：idea 里聊一轮选题 → 退出 → 再进（历史在）→ `ava new --from-idea` → 新期会话里直接「把刚才的草案写进 01-topic.md」→ 弹卡批准落盘；④ §4 文档修订面完整；⑤ 桌面端门禁走 Spec 10 门禁 12 的更新版（「idea 聊一轮 → 建期 → 新期不重述直接写」），打包版真机手验并入 ACC。

## 8. 与既有 spec/ADR 的关系速查

- D43 / ADR-0027 已废「按 scope 分工具」；本 spec 不动工具表。
- 本 spec 不改 `--continue` 的选择规则、不改 `session.jsonl` 格式、不改 H-8（host 不代发消息）、不改人审卡链。
- 「期名由人拍板」「建目录由人点按钮」不动；模型自始至终没有建期工具（选型稿已否决 `propose_new_episode` 子变体）。

## 9. 施工回填（D42-B，2026-10-08；施工方 = 独立 session，未参与立文 / v0.2 修订 / 红队）

**提交**：`d1baacf` PR1（core + 测试 + 变异登记 + core 侧文档注记）、`907d280`（PR1 补：收紧两条杀手断言）、`a7501d2` PR2（desktop + Spec 10 注记）、本提交 PR3（回填）。未 push。

### 9.1 diff 摘要

- **core**：`session_log.py`——`IDEA_DIR`、`DataUnreachable`、`acquire_idea_lease`（data/ 不可达拒、`_idea` 缺失自建、建不了目录 / 拿不到锁一律抛，锁冲突文案「另一个选题会话进行中」）、`EpisodeLease.clear`（第二截断点，docstring 注记）、`plan_repairs` 按 §3.2 第 6 条原文把既有 `repair_tool_results` 的 id 计入 `satisfied`；`session.py`——`SessionHost(log_dir=…)`（缺省 = 期目录；idea 的 `ep_dir` 恒 `None`）、`ensure_lease` 改取 `log_dir`、新增 `resume_idea`；`protocol.py`——data/ 闸无条件、idea 取 `_idea` 租约（`DataUnreachable` → `E_DATA_UNREACHABLE` 退 4，其余 → `E_SESSION_LOCKED` 退 3）、idea 启动恒恢复并回放 history、idea 回合 `persist=True`；`cli.py`——终端 `ava idea` 同口径（data 不可达退 2、租约失败退 3）、恒恢复；`new <名> --from-idea`（`migrate_idea_session`：a 取租约 → b 读全部字节 → c 临时文件 + fsync + `os.replace` → d `clear`；退出码 0 / 3 进行中 / 4 迁移失败 / 5 清空失败；stdout 恰好一行 marker）；TTY 下带入后直接 `_continue_repl`；§3.5 文案（`idea.md`、idea 状态卡、终端横幅、非 TTY 说明）。
- **desktop**：见 §9.6 偏差 1；其余按 §3.3 落地（`NEW_EPISODE` 恒带 `--from-idea`；`parseFromIdeaMarker`；先结束选题会话再建期、结束不了 `E_BUSY` 不建期；带入后清选题缓冲；`idea-note` 两态；选题视图「已带入 <期名>」；`convTarget` 的 idea 继续报错删除，`conv.resume` 对 idea 以 `SESSION_IDEA` 起）。
- **余-1**：Spec 18 文首「触及冻结面」行已改 D27 spec（PR1）；plans/README 与 issues D42 行「相关」列随本提交改。**余-2**：§3.2 第 6 条补 id 级口径半句（PR1），实现保持 id 级。

### 9.2 测试读数

| 项 | 基线（施工前 `98bf8cc`） | 施工后 |
|---|---|---|
| `uv run pytest` 全量 | 2060 passed / 6 failed / 5 skipped | 2075 passed / 6 failed / 5 skipped |
| `npx vitest run` | 422 | 432 |
| `npx tsc --noEmit` | 绿 | 绿 |
| `npx playwright test`（workers 2，临时副本） | 115 passed / 2 skipped | 115 passed / 2 skipped（跳过的是环境变量门控的截图用例；L-3 在内） |

6 红前后同一集合，均为数据盘未挂载（`test_agent_assembly_integration` ×2、`test_cloud`、`test_corrections`、`test_golden_terminal` pty ×2）。

**新增**：`tests/test_d42_idea_migration.py` 15 例（T-D42-1、2、3×2、3b、4×2、5、6、7、8a/8b/8c、9a/9b）；desktop `TH-D42 ①~⑤`（sessions.test）、`TH-D42-M`（marker 解析，service.test）、`TH-D42-E`（SessionManager 单元，ideaMigration.test）。
**改写（逐条可追溯）**：TP-16（「不写」→「只写 `data/_idea/session.jsonl`、期目录零产出」）；`test_agent_tools.py` T7（idea 会话落盘后不能再对真仓库根跑，改临时根 + 软链 config）；G12 金样本重录（只多横幅两行）；desktop TH-14 三处 argv 断言补 `--from-idea`；e2e TX-7 真实 core 版改为带入全链（含 MUT-35 的「不代发」断言原样保留）、假进程版改为未带入腿（`idea-note` 旧文案、idea 原对话仍可见）。

### 9.3 变异回填

全量套件口径（`verify_mutations.py --only …`，commit `d1baacf`），再以「只跑指定杀手」逐条复核断言级失败并 md5 对拍（scratchpad `d42b/killcheck.py`）：

| 编号 | 指定杀手 | 全量红数（含基线 6） | 复核：杀手的失败行 | md5 还原 |
|---|---|---|---|---|
| MUT-D42-a | T-D42-2 | 12 | 「新期没有会话记录：迁移只建了期、没复制」（`907d280` 收紧前是 FileNotFoundError） | ✓ |
| MUT-D42-b | T-D42-2 | 8 | `assert (True and 19767 == 0)`（`_idea` 不是空文件） | ✓ |
| MUT-D42-c | T-D42-3[dir_at_log_path] | 7 | `assert (False)`（新期目录不在了）；只读目录腿杀不死——该变异的 `rmtree` 删不动只读目录里的文件，属变异自身受限，不是护栏缺口 | ✓ |
| MUT-D42-d | T-D42-5 | 8 | `['_idea', '01-a'] == ['01-a']` | ✓ |
| MUT-D42-e | T-D42-1 | 7 | `'new' == 'resumed'` | ✓ |
| MUT-D42-f | T-D42-8c | 8 | 「租约失败却发了 ready（静默非持久运行）」（`907d280` 收紧前靠 60 s 等待超时） | ✓ |
| MUT-D42-g | T-D42-9a / 9b | 8 | 「同一 tool_call_id 重建出了两条 tool 消息」 | ✓ |
| MUT-D42-h | T-D42-3b | 7 | `assert 0 == 5` | ✓ |

改锚复跑（守护语义不变）：M23（17，含 `test_main_new_enters_repl_in_tty`）、M25a（13，含 `test_non_tty_dual_gates_for_new_and_idea`）、M29（9，含 `test_build_idea_card_invariants`）、S9-MUT-50（19，含 TP-9 / TP-8）、S9-MUT-62（14，含 TP-16 改写后仍杀）全部 KILLED；desktop MUT-35（`a7501d2`，`verify-mutations.mjs --only MUT-35`）由 TX-7 在 5 s 内杀死。

### 9.4 门禁 ③ 真会话冒烟（临时仓库根 + 真 LLM `CPA_API_KEY`，钥匙串只读进子进程环境；驱动与帧证据：scratchpad `d42b/smoke.py`、`d42b/smoke-frames.jsonl` 35 帧）

① `--idea` 聊一轮（ready `continue_status: new`），退 0，`_idea` 记录 22349 字节；② 再起 `--idea`：`continue_status: resumed`、`history_count 3`、回放 3 帧 history（旧常驻层 system_note / 用户原文 / assistant 原文）；③ `ava new 2026-10-08-冒烟带入 --from-idea`：退 0、marker `[from-idea] migrated=true sid=5bd2c0c0eaf6515d messages=3`、新期记录 22349 字节、`_idea` 0 字节；④ 新期 `--continue`（resumed，history 3）里只说「把刚才的草案写进 01-topic.md」→ 模型 `read_artifact` 后调 `write_episode_file` 弹一张卡 → 批准 → `01-topic.md` 写成草案内容（番 / 类型 / 模式 / 张力 / 锚点），回合内再 `read_status` 自查。人没有重述任何内容。

### 9.5 截图（UI 纪律）

scratchpad `d42b/shots/`：1280×800 与 1440×900 各 4 张（选题聊一轮 / 新期首行「已带入选题会话记录（3 条消息）」/ 回选题视图「已带入 <期名>」/ 没聊过就建期时的旧文案）。人 2026-10-08 看后确认按现状提交（含「N 条消息」沿用 core 计数口径，系统消息也计入）。

### 9.6 偏差与未实测项

1. **首发机制（人 2026-10-08 裁决）**：§3.3 ② 原写「renderer 记 `justCreatedMigrated`，首发先 `conv.resume` 再 `conv.send`」，与冻结静态门禁 TG-10（`conv.resume` 只许在 SessionHeader.tsx、`conv.send` 只许在 Composer.tsx、每个点击处理器至多一处、调用点最近外层函数须是原生元素事件处理器）冲突。人选「host 侧一次性标记」：`createEpisode` 拿到 `migrated:true` 记下该期，首次 `conv.send` 以 `SESSION_CONTINUE` 起进程，发送成功才消费（失败不消费，下一次仍接着迁入段起）；`conv.resume` 成功同样消费；切仓清空。语义等同「先 resume 后 send」，TG-10 与 H-8 不动。
2. **终端带入后直接续聊**：§3.4 写「随后 `ava <名>`（或建期后直接进对话的既有行为）即带着选题记录继续」——普通 `ava <名>` 开新会话、只打一行可恢复提示，所以 TTY 下 `--from-idea` 带入成功后改走 `_continue_repl`（等同 `ava <名> --continue`）；未带入照旧 `run_repl`。
3. **marker 的 `messages` 口径**：取 core `SessionSummary.messages`（全部 `msg` 记录，含常驻层与注入），与终端「[会话] 上次会话 … N 条消息」同口径；人已确认。
4. **协议对 idea 的 `ready.other_sessions` 恒为空、`session_bytes` 取 `_idea` 记录长度**：spec 未写，按「逻辑上只有一个选题会话」处理。
5. **未带入时选题缓冲保留**：§3.3 ④ 只规定带入成功后清缓冲；未带入（选题没聊过 / 只有 user）时不清，与改造前一致（假进程版 TX-7 钉住）。
6. **未实测**：打包版真机（Spec 10 门禁 12 更新版，并入 ACC，需硬盘）；`endForMigration` 返回假 → 服务层 `E_BUSY` 不建期这一支只在 SessionManager 单元层测到（集成层的真进程挨 SIGKILL 必死，造不出）；`ava new --from-idea` 的 TTY 续聊分支未做 pty 测试（逻辑是既有 `_continue_repl`，`test_main_new_enters_repl_in_tty` 覆盖的是未带入分支）。
7. **旁见（非本 spec 引入，未修）**：desktop 变异矩阵 MUT-7 / MUT-24 / MUT-64 的锚点在 HEAD（施工前）即已命中 0 / 0 / 2 次，未登记；恢复后的历史把旧常驻层作为 system_note 回放是 `--continue` 既有行为（D42-R 已记）。
