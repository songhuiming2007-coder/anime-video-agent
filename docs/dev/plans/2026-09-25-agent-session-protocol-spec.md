# Implementation Spec：无终端 agent 会话协议与 Session 恢复（Spec 9 / core 侧）

日期：2026-09-25（**v0.7**，红队第六轮定向复审 🟢；三条 🔵 由红队按用户指示直接修订；状态：**v0.7 红队 🟢，可动工**（§6.1 授权已获，从 PR0 开始））  
上位文档：`docs/dev/plans/2026-09-22-harness-evolution-direction.md`（§0.1、§0.2 第 1/2 条、§4 施工红线、§5 明确排除、§6 Spec 9）  
相关 ADR：ADR-0020（§3、§4、§5）、ADR-0018（保留条款）、ADR-0021（素材 fetch 人批、browser 逐调用卡）、ADR-0022（系统提示只增不改）、ADR-0023（记忆首次写入人确认）  
契约依赖（均已施工，代码即现状）：Spec 1/2/3/5/6/7/8，见 `docs/dev/plans/archive/`  
**修订关系**：修订 impl-spec `2026-09-18-ava-agent-impl-spec.md` 的 **B3-r5**（依据 D28 的 2026-09-24 用户裁决）；修订 direction §6 Spec 9 与 ADR-0020 §4 各一处措辞（依据 2026-09-25 用户裁决）。全部修订请求见 §6.1，**已于 2026-09-25 获用户授权**（授权不等于可动工：动工仍须红队 🟢）  
对应 issues：D28  
格式范本：Spec 2、Spec 3、Spec 8  
撰写基线：HEAD `c31201d`，`uv run pytest` 1715 passed（2026-09-25 实跑，红队独立复现一致）

**用户裁决记录**

| 日期 | 事项 | 裁决 |
|---|---|---|
| 2026-09-24 | 工具循环上限（D28） | 不设固定轮数上限；停止前先做无工具收尾调用；防失控改用「人随时中断 + 完全相同的调用拒绝执行」 |
| 2026-09-25 | 素材 fetch 在无终端下怎么批 | 提案后逐条出卡：`acquire_propose` 写入后，每条未抓取候选一张抓取卡，批准即抓 |
| 2026-09-25 | 人不在场时防失控 | 检查点暂停，不杀活、不是任务预算 |
| 2026-09-25 | 判重窗口 | 本轮内判重；人拒过、失败过的调用也算「已调过」 |
| 2026-09-25 | 模型经工具卡请求 `review --approve` | 工具层拦下，只拦模型这条路 |
| 2026-09-25（红队一轮后） | 检查点计数单位（🟡-7） | **模型回复满 50 次或工具执行满 50 次，先到先停** |
| 2026-09-25（红队一轮后） | 判重表何时清空（🟡-7） | **只在人批准过的副作用执行完成后清空**；免卡写入（`write_memory op=cite`）不清空 |
| 2026-09-25（红队一轮后） | 两处上位条款的解释（🟡-8） | **两处都认可，登记修订**：恢复时按当前文件重建 `messages[0]`；桌面端的对话与工具轨迹来自协议帧，job/approval 事件仍走 `events.jsonl` |
| 2026-09-25（红队一轮后） | `--continue` 被新会话遮蔽（🟡-11） | **不遮蔽**：普通启动开新会话，存在更早可恢复会话时打印一行提示（不提问）；`--continue` 默认恢复最近一个有实质对话的会话，也可指定会话号 |
| 2026-09-25 | §6.1 全部修订请求（含 IS-R1、DIR-R1、ADR20-R1、C-R1~R5、S6-R1、S7-R1、S8-R1、测试改写、`verify_mutations` 重锚表） | **授权**（「授权你修改」）；不提出的 S6-R2、S1-R0 不在授权范围 |

**作者自报的未实测假设**

| 编号 | 假设 | 状态 / 退路 |
|---|---|---|
| A1 | 当前服务商（本机 OpenAI 兼容代理 → gemini）在带 `tools` + `tool_choice: "none"` 时不返回 `tool_calls` | 未测：环境无 `CPA_API_KEY`，代理 401（作者与红队各自复现）。退路：收尾回复里的调用不执行、补合成结果；门禁 4 首日由人持密钥实测 |
| A2 | 服务商拒收缺配对 `tool` 消息的历史（OpenAI 已知 400） | 未测，同上。无论是否必需都补齐配对；假端点按此规则校验（§7） |
| A3 | 服务商接受连续两条 `user` 消息 | **降为已有旁证**（红队 🔵-1）：现状首轮有工序注入时，`cli.py:943` 的注入与 `cli.py:991` 的用户消息就是连续两条 user，生产中一直在用 |
| A4 | 中断打断 `web_fetch` 不留残余状态；`crawl` 的中断要等其工作线程结束 | `crawl` 部分**已由红队实测**：`with ThreadPoolExecutor` 的 `shutdown(wait=True)`（`web_crawl.py:186-187`）使中断在工作线程跑完后才浮出（信号 0.5 s 发出、4.03 s 浮出），最长约 `crawl.timeout_s`（60 s）。已并入 §2.2 与 RF-9 |
| A5 | 会话锁在外置盘上可靠 | **关闭**（红队 🔵-2）：`mount` 显示 T7 为 `apfs, local`，作者复核一致 |
| A6 | 服务商 prompt cache 有效期为分钟级 | 未测（服务商内部行为）；只影响 §2.7 的成本论证 |
| A7 | 检查点初值 50 合适 | 无数据；§2.3 第 6 条的校准规则 |
| A8 | 协议下 browser 调用的中断延迟 ≤ `browser.timeout_s`（60 s） | 未测；终端下的行为已按红队实测改写（§2.2） |

---

## 0. 一句话设计

**一个 agent 内核，两种接线：终端接 `input()`，桌面端接 stdio 帧；人的答复只从这两条线进来，模型碰不到。**

新增 `pipeline/agent/session.py`（内核）、`session_log.py`（`session.jsonl` 纯函数）、`protocol.py`（stdio 适配器）。会话是 host spawn 的长驻子进程，一期一进程，不开端口。主会话与 `/chat`、`/script`、`/memory digest`、idea 子会话**全部经同一内核**，共享进程级的期租约、中断控制与检查点；只有主会话落盘。工具循环去掉 10 轮硬停，改为「人中断 + 本轮判重 + 检查点（回复或工具执行满 50 次）」，非正常停止先做 `tool_choice: "none"` 收尾。人审请求统一成一个模型，答复只认带不可猜 `request_id` 的入站答复；停机点仍走 Spec 3/8 既有 ack 路径。每条消息「写盘 + 进内存」是一次不可被中断拆开的提交；出站帧由独立写线程整帧写出。`session.jsonl` append-only，`ava <期> --continue [会话号]` 恢复；修复一律以追加记录完成，由重建时插回正确位置。终端零回归以 PR0 录下的归一化金样本为尺子。

---

## 1. 红队裁决与修订纪要

六轮红队评审（2026-09-25）全部闭环，**第六轮结论 🟢 可动工**。累计：一轮 2🔴 + 12🟡 + 12🔵，二轮 9🟡 + 4🔵，三轮 1🟡 + 4🔵，四轮 1🟡 + 3🔵，五轮 2🟡 + 2🔵，六轮 3🔵；另作者自查 3 项。无驳回。逐轮裁决表见文末「附：红队裁决纪要」。

施工须知（从裁决中提炼，正文已落实）：实测复现过的 5 处问题（`review` 前缀缩写旁路、同进程二次 flock 被拒、大帧被中断撕裂、终端 Ctrl-C 连带杀同组浏览器子进程、crawl 中断须等工作线程）均有对应用例与变异；A1、A2 须人持密钥在 PR1 首日实测；RF-15（判重挡不住参数微扰）是用户裁决时已接受的代价。

---

## 2. 关键设计决策（含代码现状证据）

### 2.1 决策 1：会话进程形态——长驻子进程 + stdio 行协议（问题 1）

**现状证据**：人审发生在工具循环中途（`llm.py:344-357` 调 `approve`，终端版阻塞在 `cli.py:856-859` 的 `input()`）；循环中间状态只在函数局部（`llm.py:320`）；browser 会话是模块级单例；启动耗时实测 0.05–0.09 s（E4）。

**裁决：一个会话进程 = 一期（或一个 idea 会话）的长驻子进程。** 否决每轮 spawn 的原因不是启动耗时，而是中途人审：进程要么在等人时一直活着（那就是长驻），要么把「循环走到一半」的状态序列化出去再续跑。本地 socket/HTTP 违反红线 2。

- **入口**：`python -m pipeline.agent.protocol <期目录> [--continue [<会话号前缀>]]` 或 `--idea`。stdin 是 TTY → 退出 2。
- **fd 隔离**（E1），在 **import 任何 `pipeline.*` 之前**完成（🔵-11）：
  1. `proto_out = os.dup(1)`、`proto_in = os.dup(0)`（`os.dup` 得到的 fd 默认不可继承，子进程拿不到协议通道）；
  2. `os.dup2(2, 1)`：C 层写 fd 1、继承 fd 1 的子进程全部进 stderr；
  3. `os.dup2(/dev/null, 0)`：`input()` 与继承 stdin 的子进程读到 EOF（`execute_job` 的 `Popen` 不传 stdin，`jobs.py:533-540`；`review.py:373` 有 `input("")`）。EOF 的后果因调用点而异：`cli.py:858-859` 取「否」，`review.py:373` 抛 EOFError 退非零，但 `cli.py:644-646` 取「是」（`/voice` 的 apply-patch）；后者在协议下不可达，Spec 11 按钮化 `/voice` 时必须处理（RF-17）；
  4. `sys.stdout` 换成「逐行转 `log` 帧」的流对象；`sys.stderr` 不动。
- **出站写线程**（🟡-2）：所有帧（含 `log` 帧）进一个无界队列，由独立写线程取出、对 `proto_out` 循环 `os.write` 直到整帧写完。主线程只入队，且入队在延迟区内（§2.2）。`pthread_kill` 只打主线程，写线程不会被中断撕裂帧。EPIPE/EBADF → 标记「host 已断」，此后丢弃出站帧，不抛给主线程。退出前以 `FRAME_DRAIN_TIMEOUT_S` 为上限排空队列。
- **不开端口、不起 server**：TG-2。

### 2.2 决策 2：中断与取消（问题 1、问题 8 ②）

**现状证据**：终端回合中途 Ctrl-C 会杀掉整个 REPL（E5b：rc=1，回溯停在 `socket.recv_into`；只有等提示符处 `cli.py:1315` 捕获）。job 层以 `start_new_session=True`（`jobs.py:539`）加 BaseException 分支 SIGKILL 整组（`jobs.py:599-637`，N28）处理中断。

**统一的是「主线程上的处理器」，入口按通道不同**（🟡-3）：

| | 终端 | 协议 |
|---|---|---|
| 入口 | 人按 Ctrl-C，SIGINT 发往**整个前台进程组** | host 发 `interrupt{turn_id}` → 读者线程核对 turn_id → `signal.pthread_kill(主线程, SIGINT)` |
| 同组子进程 | **也收到 SIGINT**：browser 的 playwright driver 与 chromium（`asyncio.create_subprocess_exec` 未开新会话，`_transport.py:120`）、播放器等直接退出 | 不受影响（只有主线程收到） |
| job 子进程 | 不受影响（新会话），由 N28 路径 SIGKILL | 同左 |
| 空闲时（等提示符 / 等消息） | Python 默认处理器：Ctrl-C 退出 REPL，与现状一致（`cli.py:1313-1318`） | 忽略并发 `notice`（MUT-31） |

E2 实测：`pthread_kill` 与进程级 `kill` 在「等子进程 / 等 HTTP / 等答复队列（有无超时）」四种阻塞点均在 0.50–0.76 s 内转成 KeyboardInterrupt（3.12.13 / 3.13.14 / 3.14.5）。

**处理器语义**：
1. 只在回合内安装（任何子会话的回合都装），回合结束恢复。
2. **回合内的中断状态**（🟡-B）：处理器在主线程运行，按回合所处状态决定行为：

| 状态 | 何时进入 | 中断到达时 | 用例 |
|---|---|---|---|
| 执行中 | 回合开始 | 置 `pending`；不在延迟区 → 立即抛出；在延迟区 → 延迟区退出时抛出。**「浮出」= 被内核的 `except KeyboardInterrupt` 接住**；每个回合在本状态至多浮出一次，浮出即转入「停止中」。浮出之前到达的重复中断只再置一次 `pending`，不计为第二次（crawl 慢浮出时人多按一次停止，不误伤收尾；红队三轮以真实 `execute_job` 在 0–1.0 s 十个时点注入第二次中断，进程组均被杀、`job_finished` 均发出） | TL-6、TL-9b |
| 停止中 | 第一个中断浮出后，补合成结果、决定收尾或回滚期间 | 只置标志，不抛 | TL-9c |
| 收尾中 | 收尾请求发出时 | 抛出 → 放弃收尾（`wrapup: "aborted"`） | TL-9 |
| 收尾后 | 收尾结束（或决定不收尾）后，写 `turn_end`、`ensure_pending`、发帧期间 | 只置标志，不抛；回合结束时丢弃并发 `notice`，**不带进空闲态**（否则终端会把它当成提示符处的 Ctrl-C 退出 REPL） | TL-9d |
3. **延迟区**（E6）：延迟区内到达的中断，等延迟区退出后再抛。**延迟区的深度计数与待处理中断只属于主线程**（🟡-C）：非主线程调用 `defer()` 是空操作，任何情况下都不在非主线程抛出（`_drain` 线程 `jobs.py:556-579` 经 `sys.stdout` 入队，它若抛出 KeyboardInterrupt 会死掉并关管道）；日志流对象的行缓冲加锁。延迟区闭集：
   - `CRITICAL_TOOLS = frozenset({"write_episode_file", "acquire_propose", "write_memory", "browser"})` 的执行体。这是显式字面量，TG-5 比对它与「side_effect 为真的工具 − `run_pipeline`」（MUT-34）；
   - `commit()`（§2.5）与出站入队；`log_approval_decision`；
   - **`run_pipeline` 不在内**：它的阻塞点是 `proc.wait()`，N28 的设计就是在这里被打断并杀组；
   - 终端下 browser 的延迟只保护 Python 侧状态：driver 已被同一个 Ctrl-C 杀死，这次调用以启动或调用失败的 `ValueError` 结束（`web_browser.py:317-322`），会话下次自愈。
4. **在途调用的收尾**（合成结果经 `commit()` 追加，不改既有消息）：

| 中断落点 | 当前调用 | 同一回复中其后的调用 | 随后 |
|---|---|---|---|
| 模型请求中 | —（无回复） | — | 本轮 ≥1 条回复则收尾，否则回滚 |
| 等人答复 | `人审请求因人类中断作废，未执行` | `未执行：人类中断` | 请求 `voided`；收尾 |
| `run_pipeline` job 中 | `已中断：job 进程组已被 SIGKILL，产物可能不完整，请用 read_status 核实` | 同上 | 收尾 |
| 只读工具中 | `已中断：执行被打断，结果未知`（crawl 要等工作线程结束才走到这一步） | 同上 | 收尾 |
| 临界区工具中 | 该工具的真实结果（协议下）；终端 browser 为其失败结果 | 同上 | 收尾 |
| 抓取 job 中 | `acquire_propose` 结果照常；该候选 `decision: "interrupted"`，其余候选的卡 `voided` | 同上 | 收尾 |

5. **SIGTERM**：中断 + 跳过收尾 + 写 `turn_end{wrapup:"skipped"}` + 退出 0（host 的 SIGKILL 倒计时 5 s，收尾最长 60 s）。

### 2.3 决策 3：工具循环——无固定上限，先收尾后停（问题 8）

**现状证据**：`DEFAULT_MAX_ITERATIONS = 10`（`llm.py:33`）数的是模型回复（`llm.py:325`），到顶返回 `convo[-1]`（`llm.py:365-366`）；S25 已在调用方只打 WARN（`cli.py:1007-1009`）；D28 实测一次查询 10 次不够。

1. **删除硬上限**（TG-3）。停止原因闭集：

| `stopped` | 触发 | 收尾 |
|---|---|---|
| `done` | 回复无 `tool_calls` | 不需要 |
| `interrupted` | 人中断 | 本轮 ≥1 条回复时做，否则回滚 |
| `error` | `LLMError`、意外异常 | 同上 |
| `checkpoint_stop` | 检查点上人选「停止」 | 做 |
| `blocked` | 出网断言拒绝（`llm.py:255`） | 不做（同一份历史必然再被拒）；回滚，与现状 `cli.py:998-1002` 相同 |
| `degraded` | 无 LLM 配置 | 不适用，与现状相同 |

2. **收尾调用**：经 `commit()` 追加一条 user 消息 `WRAPUP_INSTRUCTION`（§3.5），以**同一份 `tools`** 加 `tool_choice: "none"` 调一次（保留 `tools` 以免前缀缓存失效）。`chat_complete` 新增可选参数 `tool_choice`，不传时请求体与现状逐字节相同（TL-15）。
3. **回滚**（🟡-6 统一定义）：本轮一条回复都没拿到（首个请求失败或被中断）或 `blocked` 时，恢复到**回合开始时的快照**：`messages` 截回原长度（注入消息一并丢弃），`tracker` 的 `injected_paths`、`active_step_key`、`resident_prompt`、`active_scope` 回退；现 `memory_warned` 拆成两个标志（🔵-1）：`memory_warn_printed`（终端是否已打印，不回退）与 `memory_warn_injected`（告警消息是否在历史里，随回滚回退，下一轮重新注入）。`session.jsonl` 追加 `turn_rollback`，重建时丢弃该回合全部消息，**内存与重建结果恒等**（TS-11）。终端输出与现状相同（G8、G9）。
4. **收尾失败**：收尾回复仍带 `tool_calls`（A1 不成立）→ 不执行、逐个补合成结果 `未执行：收尾阶段禁止调用工具`，`content` 非空仍作答复。没有可用答复 → 输出本地确定性说明（`[收尾·本地] 本轮在第 N 次模型调用后因<原因>停止；工具调用 M 次（名称×次数）；收尾调用<失败原因>。已执行的工具结果保留在会话中。`），只以 `assistant{kind:"local_note"}` 帧或终端打印呈现，**不进 `messages`**（MUT-10）。
5. **本轮判重**（用户裁决两次）：
   - 键 = `(工具名, json.dumps(解析后参数, sort_keys=True, ensure_ascii=False, separators=(",", ":")))`，顶层字符串 `strip()`；不做 URL 或语义归一（与 Spec 6 §2.3 同口径）。
   - 记入判重表：执行过的（成功或失败，含免卡的 `write_memory op=cite`）、人拒绝的、预校验拒绝的。
   - **清空**：只在一个**经人批准**的调用（工具卡或抓取卡）执行完成时。免卡写入不清空（MUT-40）。
   - 命中：不执行、不弹卡，回喂 `重复调用：本轮已用完全相同的参数调用过 <name>（上次结果见上文）。换一条路，或基于已有信息作答。`
   - 残余风险（如实记录）：精确串判重挡不住参数微扰（URL 加 `?`、`#`），这是裁决的已知代价，由检查点兜底（RF-15）。
6. **检查点**（用户裁决两次）：
   - **规则**：本轮自上次检查点（或回合开始）起，模型回复满 `CHECKPOINT_EVERY = 50` 条**或**工具执行满 50 次（`execute_tool` 实际被调用的次数，含只读、含并行，不含判重命中与被拒），先到者触发。**检查位置有两处**（🟡-A）：每次模型请求之前查回复计数与执行计数；**每次执行之前**查执行计数——同一条回复里的并行调用也逐个受约束。「继续」→ 两个计数归零；「停止」→ 同一回复里尚未执行的调用补合成结果 `未执行：检查点停止`，然后 `checkpoint_stop` + 收尾。收尾调用与抓取 job 不计数。
   - **为什么能封住无人值守**（v0.1 论断已改正，🟡-7④）：需要人审的调用在人不在时停在卡上；**不需要人审**的只有只读工具与 `write_memory op=cite`（`memory.py:43` 的 `CARD_FREE_OPS`）。两个计数一起，让任何一条无人链路每 50 次回复或 50 次执行必须遇到一个人；判重挡住原样重复。host 侧的旁路（自动续发消息重置计数）由 H-8 禁止。
   - **不设检查点的最坏情况**：历史一轮内只增不减，唯一天然上限是上下文窗口（当前配置 gemini flash，百万 token 量级，约）；小结果循环撑满前可跑上千次，累计输入达亿级 token。
   - **50 的依据**：D28 唯一实测（一次查询 10 次不够）的 5 倍，作初值。校准规则（不自动）：累计 ≥ 30 个真实回合后，若触发检查点的回合里人选「继续」≥ 80%，说明间隔偏小；从未触发则维持。
7. **观测量**（问题 8 ④）：`turn_end` 记录与 `turn_finished` 帧含 `llm_calls`、`tool_calls`（本轮全部调用数）、`tool_executions`、`duplicates_rejected`、`checkpoints`、`stopped`、`wrapup`、`duration_s`、`prompt_chars`。
8. **修订关系**（问题 8 ⑤）：IS-R1（§6.1）。改写 `tests/test_agent_tools.py:509`、`527` 与 `tests/test_agent_director.py:763`；`scripts/verify_mutations.py:110` 的 M10 改守检查点（§6.1 重锚表）。

### 2.4 决策 4：统一人审请求模型（问题 2）

#### 2.4.1 请求种类

| kind | 触发 | 生命周期 | 答复来源 | 选项 |
|---|---|---|---|---|
| `tool_call` | 模型请求需人审的工具（含 browser 卡、提案卡、`write_memory` 卡） | 随进程 | 会话答复通道 | `approve`/`reject`（协议可附反馈） |
| `fetch` | `acquire_propose` 成功后内核逐条发出 | 随进程 | 同上 | `approve`/`reject` |
| `checkpoint` | 内核 | 随进程 | 同上 | `continue`/`stop` |
| `memory_ack` | 人发起（终端 `/memory ack`；协议 `command{memory_ack}`） | 随进程 | 同上 | `approve`/`reject` |
| `stop_point` | `approvals.ensure_pending` 建立的 Spec 3 对象 | **持久** | 不经会话：终端 `/approve`、`/reject`，桌面端 Spec 8 决策条 | Spec 3 定义 |

停机点只以 `stop_points` 帧通知（`answer_via: "decision_bar"`），不并入会话答复通道：Spec 3/8 的 ack 路径已过三轮红队，不再并行维护第二套。会话在 `ready` 与每轮结束时调用 `ensure_pending`（与 REPL 每轮 `cli.py:1307` 同源）。停机点期间会话不挂起（现状亦然）。

#### 2.4.2 答复只能来自人的操作

**T1（会话内的模型）：构造上不可能**，六条保证：
1. **方向**：答复只从入站通道进来；唯一投递函数 `_deliver_answer` 只在 `FrameReader` 内有 1 个调用点（TK-6 用 AST 断言）。
2. **工具闭集碰不到通道**：`_TOOL_IMPLS`（`tools.py:898-911`）12 项均不 spawn `pipeline.agent.*`；`PIPELINE_MODULES`（`tools.py:32-42`）与 `ASSET_COMMANDS` 不含 `agent`；协议 fd 不可继承，fd 0 已是 `/dev/null`。
3. **请求号不可猜、不可见**：`secrets.token_hex(16)`，只出现在出站帧与 `session.jsonl`，**从不进 `messages`**（TK-6、MUT-33）；`.jsonl` 不在读域（`tools.py:311`）。
4. **严格绑定**：答复须对上一个打开中的请求且 `decision ∈ options`；EOF、超时、进程退出一律作废，从不算批准（TP-5、TP-7）。
5. **确认只由宿主置位**：`confirmed` 由内核在人批准后写入 `ToolContext`（`llm.py:353-355`）。`_tool_write_episode_file` 仍读模型填的 `args["confirmed"]`（`tools.py:703`），今天无害只因每次都先过卡；内核立不变量「没有人审通道就拒绝运行工具循环」（TK-5）。
6. **确定性判定**：是否需要人审由 `TOOL_SCHEMAS` 的 `side_effect`（fail-closed）决定，卡片由参数确定性渲染并剥控制字符；卡片字段按纯文本渲染由 H-3 保证。

**T2（本机其他有 shell 的程序）：如实声明边界**。能 spawn 协议进程的程序可以像 host 一样答复，与裸形态 `/approve --id`（Spec 8 RF-22）、伪终端下的 `/memory ack`（E3：`script` 下 `isatty()` 为 True；Spec 7 RF-22）同一信任边界。本 spec 只做两件事：答复在 `approvals.jsonl` 里带 `channel`（C-R4）；请求号只在出站帧里，盲写 `echo … |` 驱动不了。这不是防线。

#### 2.4.3 模型发起的 `review` 只能生成审片页（🔴-1）

- **现状**：`run_pipeline("review --approve")` 通过白名单（`tools.py:156-277`），卡片不标停机点（`cli.py:829` 只认 tts/clips/render）；R1 实测前缀缩写 `--a`、`--ap`、`--appr`、`--appr=1`、`pipeline.review --ap` 同样通过（`review.py:394` 未关 `allow_abbrev`）。
- **规则**：内核的工具审查对**模型发起的** `run_pipeline`：规范化 argv 的模块为 `pipeline.review` 时，除 `validate_pipeline_command` 注入的期目录位置参数外，**出现任何以 `-` 开头的 token 即拒**，回喂 `模型只能生成审片页（review <期>）；停机点 05 的批准只能由人经 /approve 或桌面端决策条完成（ADR-0020 §3）`。
- **只拦模型**：`validate_pipeline_command` 对 `review` 不加这条；人经 REPL `/run review --approve`（`cli.py:1446-1504`）、裸形态 `/run`（`cli.py:1691-1703`）、`docs/WORKFLOW.md` 第 44 行记载的路径全部不变。
- **其余停机点无同类旁路**：`02-diff.patch`（`approvals.py:58`）不在写白名单（`tools.py:26`），也没有白名单模块能产出它；03.5、09 无解封物（`approvals.py:57-62`）。
- **同源问题（自查 S-1）**：`--force` 禁令同样只认全拼，`tts run --force-a` 通过（R1）。C-R5 在 `validate_pipeline_command` 里对 `--force`、`--force-all` 做前缀判定，**人与模型两条路同时生效**。

#### 2.4.4 素材抓取卡（用户裁决；🟡-9 修订）

1. `acquire_propose` 经人审卡批准且执行成功后（**工具实现与 schema 零改动**），内核用**不会抛出的写法**做同源检查（🟡-H）：`paths.DATA` 不存在 → 不出卡，发 `notice{code:"fetch_disabled_data_unreachable"}`；`os.path.realpath(paths.DATA) != os.path.realpath(ctx.base / "data")` → 不出卡，发 `notice{code:"fetch_disabled_root_mismatch"}`（测试夹具默认走这一支，生产中两者相同）。不调用 `require_data()`（它在 data 不可达时 `raise SystemExit`，`paths.py:144`、`149`）；抓取钩子外层另行捕获 `SystemExit` 并转成 `notice{code:"fetch_hook_error"}`，作纵深防御。
2. 读 `cmd_fetch` **实际读的**文件（`paths.DATA/library/incoming/candidates.json` 与同目录 `fetched.json`，即 `require_data()` 返回的同一路径；只读，不经会 mkdir 的 `incoming()`），对本次输入的每个 URL（按输入顺序去重）：在清单中（取 1-based 序号 N）且不在台账 → 一张 `fetch` 卡。这同时覆盖「本次新增」与「以前提过未抓」。
3. 逐条问，没有「全部批准」（H-4）。
4. 批准 → 重读同一文件，`cands[N-1]["url"]` 须等于卡上 URL，否则 `misaligned`、不抓（TK-3）→ 经注入的抓取执行器（`LoopControl.fetch_executor`；生产实现为 `run_pipeline(f"acquire fetch {N}", episode_dir=<会话期目录>, scope="asset", confirmed=True)`）以 job 执行；为此 `ASSET_COMMANDS` 增 `"acquire": {"fetch"}`（S6-R1）。
5. 结果并入该次 `acquire_propose` 的工具结果：`result["fetch"] = [{"no", "url", "decision": "approved|rejected|voided|misaligned|interrupted", "ok", "returncode", "message", "stdout_tail"}]`。
6. 批准与拒绝经 `log_approval_decision(ep_dir, "acquire_fetch", "#N <url>", …, channel=…)` 记账；作废不记（🔵-5）。
- **模型仍抓不到**：asset scope 工具表没有 `run_pipeline`；pipeline/creative scope 的 `run_pipeline` 只认 `PIPELINE_MODULES`（TK-4）。
- 终端同步获得此流程（I-6），人在 `/asset` 下 `/run acquire fetch N` 也可用（I-9）。`acquire gate`、`register` 不在本 spec（§2.10）。

#### 2.4.5 自由文本拒绝

协议 `answer` 对 `tool_call` 允许附 `feedback`，工具结果为 `人类拒绝执行该工具调用：<feedback>`。终端卡片仍是 `[y/N]`。

### 2.5 决策 5：`session.jsonl`、期租约与会话选择（问题 3）

- **位置**：`data/episodes/<期>/session.jsonl`（ADR-0020 §5），不进 git；期目录不存在就不写、不建目录。
- **期租约 `EpisodeLease`**（🔴-2）：每个进程对每个期**至多打开一次** `session.jsonl`，对该 fd `flock(LOCK_EX | LOCK_NB)`；进程生命期持有，进程死亡时自动释放。**取得时机**（🟡-D）：`--continue`（终端与协议）与**所有**协议进程在读文件之前取得，拿不到 → 终端打印原因并退出 3、协议在 `ready` 之前发 `error{code:"E_SESSION_LOCKED"}` 并退出 3；只有不带 `--continue` 的普通终端 REPL 在**首个 agent 回合**（主会话或任何子会话）懒取，拿不到 → 本回合拒绝并提示「该期已有活跃会话（pid …），本终端仍可使用 / 命令」（I-10）。**未持锁时读文件**（启动时的一行提示、`--sessions`）：末尾不完整的行视为未提交记录，只忽略、不截断；截断与修复只在持锁后进行（§2.8）。子会话从不自行开锁，只向进程内的租约登记，因此不存在同进程自锁。依据：ADR-0020 §3「写产物状态保持单线程」。
- **记录与写入**：一个文件多段会话，记录按 `sid` 归属，**会话可以在文件中交错**（恢复较早的会话时，其 `segment_start` 追加在文件尾）。记录闭集见 §3.3；每条 `json.dumps(sort_keys=True, ensure_ascii=False) + "\n"`，经租约 fd 以 `os.write` 循环写满（🔵-6）。
- **唯一提交函数**（🟡-1）：`commit(message, origin)` = 在一个延迟区内「写盘 → 追加进内存 `messages`」。任何消息只能经它进入历史，中断只可能落在一次提交之前或之后，不会落在两步之间（TS-10、MUT-37）。
- **写失败**：会话记录标记为「已损坏」，本回合按 `error` 停止且**此后不再写盘**（收尾照做，只在内存），此后回合一律拒绝并提示开新会话。绝不静默继续：会话记录丢了就无法恢复。
- **会话选择**（用户裁决：不遮蔽）：
  - 普通启动（终端或协议不带 `--continue`）开新会话（终端仍懒创建：首个主会话回合才写 `session_start`）。若文件中存在「含 ≥1 条 assistant 消息」的更早会话，终端打印一行 `[会话] 上次会话 <sid 前 8 位> · N 条消息 · <最后活动时间>，可用 ava <期> --continue 恢复`，不提问；协议在 `ready.other_sessions` 给出摘要。
  - `--continue` 默认恢复「最后活动时间最新、且含 ≥1 条 assistant 消息」的会话；`--continue <sid 前缀>` 指定（前缀须唯一，否则列出候选并退出 2）；`ava <期> --sessions` 列出全部会话。`ava --continue`（不带期）选 `session.jsonl` 最近写入的可见期，先打印期名。
- **与 `events.jsonl` 的边界**：

| | `events.jsonl`（Spec 2） | `session.jsonl`（本 spec） |
|---|---|---|
| 记什么 | job 生命周期、停机点 approval、人时、browser 启动 | 对话历史、回合起止、工具执行开始、人审请求开关 |
| 写者 | 多进程，逐条 flock，满则丢 | 单写者（持租约的进程），同步写，写失败即停 |
| 读者 | 桌面端 tail | 只有 `--continue` 的加载器；桌面端经协议拿历史 |
| 参与状态推导 | 否 | 否（TS-9、TG-4） |

  内核照旧经既有路径发事件，**不新增 EventType**。桌面端的对话与工具轨迹来自协议帧，这是对 ADR-0020 §4「事件流直接镜像 events.jsonl」的修订（ADR20-R1，用户已认可）。
- **多期并行**：一期一进程、一期一租约；进程绑定期目录，没有切换命令。跨期共享的资源只有 browser profile（RF-8）。
- **idea 会话不落盘**：无期目录可挂，零写权限。

### 2.6 决策 6：终端零回归（问题 4）

**切分点：人机 I/O 与回合编排分离。**
- 内核（`session.py`，无 I/O）：上下文装配（`cli.py:887-976` 整段搬入，逻辑不改）、`run_tool_loop`、工具审查策略（原 `_default_approve` 的预校验、side_effect 分流、卡片内容，不含 `print`/`input`）、判重、检查点、抓取钩子、提交、中断控制。
- 通道：`TtyChannel`（`cli.py`，打印 + `input()`，EOF 视为否，与现状同一段代码搬家）；`ProtocolChannel`（`protocol.py`）。
- **子会话全部经内核**（🔴-2）：进程内一个 `SessionHost` 持有租约、中断控制与检查点配置；主会话与 `/chat`、`/script`、`/memory digest`、idea 各为一个 `AgentSession`，只有主会话 `persist=True`（子会话维持现状「独立 messages、退出即丢」，`cli.py:1038-1125`、`1237-1246`）。四个调用点（`cli.py:1070`、`1114`、`1243`、`1510`）**保留**经模块属性调用 `cli._dispatch_agent_turn`，参数与签名不变（三轮 🟡-1：12 处现有替身按固定签名拦截这里）；由这个薄包装在内部交给 `SessionHost`。主会话的 `messages` 仍归 `_run_repl_body` 持有、原地修改；`_run_repl_body` 启动时以 `host.bind_main(messages)` 登记该列表对象，包装据 `messages is host.main_messages` 决定是否落盘——**不新增任何参数**。子会话回合同样有 Ctrl-C 收尾与检查点（I-1、I-2 对子会话同样成立）。
- **登记的生命期**（四轮 🟡）：登记是上下文管理器 `activate_host(ep_dir, *, root, channel)`，范围正好覆盖 `run_repl`/`_run_repl_body`、`run_agent_loop` 的执行期，`finally` 里注销并释放租约，任何异常路径都不会把登记留在进程里。`SessionHost` 绑定自己的 `ep_dir`（resolve 后比较），包装收到的 `ep_dir` 不一致 → 按「没有登记」处理。登记可重入：`/chat`、`/script` 在 `_run_repl_body` 内再调 `run_agent_loop`（`cli.py:1433-1444`）时，已有同一 `ep_dir` 的活动登记只计数、不替换，只有最外层登记与注销；`ep_dir` 不同的嵌套进入视为错误（现有代码中不存在）。**工具上下文的期目录一律取自包装收到的 `ep_dir` 参数，从不取自 `SessionHost`**（五轮自查）。
- **直接调用包装的契约**（测试里 21 处）：进程里没有登记 `SessionHost` 时，包装原地修改调用方传入的 `messages` 与 `tracker`，`persist=False`，**不取租约、不写 `session.jsonl`**；有 `SessionHost` 但传入的不是主会话列表（子会话）时，`persist=False`，共用进程租约（TK-10、TK-11）。
- `_default_approve`、`_dispatch_agent_turn` 保留签名作薄包装。`run_tool_loop` 保留 `messages, *, ctx, approve` 的调用形态，新增 `control` 关键字参数；`tests/test_agent_director.py:729`、`751`、`773` 的打桩签名补 `**_`。其中 729、751 两处直接抛 `PermissionError`、`LLMError`：内核在 `run_tool_loop` **外层**继续捕获这两类异常，按回滚处理，与现状 `cli.py:994-1002` 相同（🔵-10）。

**「零回归」的尺子**（🟡-4）：
1. 既有测试全绿，除 §6.1 列出的测试改动；
2. **金样本**：PR0 在改动前录制，重构后比对归一化文本。
   - **进程内录制**（沿用 `make_agent_root`、`patch_inputs`、`capsys`；LLM 打桩 `chat_complete`）：stdout 与 stderr **分别**录、分别比；归一化：tmp root → `<ROOT>`、`sys_python()` → `<PY>`、仓库根 → `<REPO>`、`\r\n` → `\n`，其余逐字节。场景：G1 纯文本回复、G2 只读工具回显、G3 副作用卡 y、G4 卡 n、G5 预校验拒收、G6 `write_memory` 卡、G7 降级、G8 LLM 首调失败、G9 出网拦截、G10 `/script` 一回合、G11 `/chat` 一回合、G12 idea 一回合、G13 `/memory digest`。
   - **pty 录制**（`pty.fork` 跑驱动脚本，证明真 TTY 路径）：G-P1 副作用卡 y、G-P2 回合中 Ctrl-C。回显与控制序列按同一规则归一化后比对。G-P2 改动前的期望值是「进程退出、回溯」，改动后按 I-2 更新：改动前录下，是为了证明它确实变了。
3. **有意改变闭集**：

| 编号 | 改变 | 依据 |
|---|---|---|
| I-1 | 无 10 轮硬停，改为检查点卡（主会话与子会话） | 用户裁决 |
| I-2 | 回合中 Ctrl-C 停止本轮、收尾、回到提示符（主会话与子会话） | 问题 8 ② |
| I-3 | 非首调的 LLM 失败保留进度并收尾 | 问题 8 ① |
| I-4 | 本轮内完全相同的调用被拒 | 用户裁决 |
| I-5 | 模型发起的 `review` 带任何选项被拒 | 用户裁决、🔴-1 |
| I-6 | `acquire_propose` 后逐条出抓取卡 | 用户裁决 |
| I-7 | 主会话写 `session.jsonl`；`--continue`、`--sessions`；启动时的一行会话提示 | ADR-0020 §5、用户裁决 |
| I-8 | 非正常停止多出 `[中断]`、`[收尾·本地]` 等行 | 问题 8 ① |
| I-9 | asset scope 下人可 `/run acquire fetch N` | S6-R1 |
| I-10 | 同期已有活会话时终端拒绝对话（`/` 命令照常） | §2.5 |
| I-11 | `--force`、`--force-all` 的前缀缩写被拒（人与模型） | C-R5 |

### 2.7 决策 7：恢复时的系统提示（问题 5；用户已认可，DIR-R1）

Spec 1 实际保证的是：常驻层会话内字节级恒定（只在 scope 变化时重算，`cli.py:910-914`）；状态卡在每轮开始刷新并整段替换 `messages[0]`（`cli.py:958-960`），一轮之内不变；工序层与记忆以追加 user 消息注入。状态卡在产物不变时逐字节稳定（E7）。

恢复规则：
1. `messages[0]` 不落盘，恢复时按**当前**文件重建；`session_start`、`segment_start` 记常驻层 sha256，变了就发 `notice{code:"resident_changed"}`（终端打印一行）。理由：缓存有效期是分钟级（A6），隔夜恢复必然冷启动，逐字节重放旧常驻层换不来缓存命中，反而会让会话按过期的红线工作。「会话内稳定」在缓存意义上就是**一个进程段**之内稳定。
2. `messages[1:]` 逐字重放（包括当时注入的规程原文），不改写。
3. 注入记录带每份文档的 `path` 与 sha256；恢复后第一轮，已注入文档的当前 sha 变了 → 经 `commit()` 追加「[系统提示更新] 规程已修订：<path>」加新正文（TS-6）。`injected_paths`、`active_step_key` 从记录恢复；`active_scope` 置空。`displayed_ids`（停机点 id 映射）**不**从会话恢复，每个进程从空开始（现状 `cli.py:1299`；TT-6）。
4. 登记未提出的 S1-R0：状态卡在 `messages[0]` 尾部，产物一变，其后整段历史的前缀缓存全部失效。改成「变化时追加」属于 Spec 1 的设计变更，留给红队与人判断。

### 2.8 决策 8：崩溃恢复（问题 6）

请求是非流式的（`llm.py:263-277`），不存在半条回复；将来加流式，必须保证未完成的片段不经 `commit()`。

| 场景 | 进程怎么结束 | 收尾 | 在途工具 / 请求 / job |
|---|---|---|---|
| `shutdown` 帧、stdin EOF | 中断当前回合 → 本轮 ≥1 条回复则收尾 → 写 `turn_end` → 退出 0；host 已断时帧静默丢弃，盘照写（🟡-2） | 做 | 按 §2.2 的表；请求 `voided`；job 进程组 SIGKILL |
| SIGTERM | 中断 → `turn_end{wrapup:"skipped"}` → 退出 0 | 跳过 | 同上 |
| SIGKILL、断电、终端窗口关闭 | 立即死亡 | — | 恢复时修复；job 在独立进程组，会自己跑完或失败（Spec 2 RF-7），结果只能从产物与 `events.jsonl` 看到 |

**恢复（`--continue`）的修复算法**：已提交的整行一字不改（TS-4 断言原前缀字节不变）；修复一律写成**追加记录**，由 `rebuild_messages` 在重建时插回正确位置（🟡-1）。
1. **撕裂尾巴**：末尾没有换行的残行是未提交记录，持锁后截断到最后一个换行符。这是整个算法里唯一的截断。
2. **坏行**：目标会话的 `session_start` 之后出现无法解析的完整行（无法判断归属，保守处理）→ 拒绝恢复该会话（`continue_status: "corrupt"`），开新会话。
3. **未知 schema** → 拒绝恢复，开新会话。
4. **全历史配对校验**：遍历目标会话重建出的全部消息，对每条带 `tool_calls` 的 assistant，检查「紧随其后的连续 `tool` 消息恰好覆盖它的全部 id」。缺失的 id 追加一条 `repair_tool_results{after_seq, results}` 记录，重建时插在该 assistant 之后、其已有 tool 消息之后。内容：有 `tool_exec_started` 的写「执行已开始、结果未知：会话在执行期间中断。若是 run_pipeline，请用 read_status 核实产物」，否则写「未执行：会话中断（人审未答复或尚未开始）」。
5. 未关闭的人审请求 → 追加 `request_closed{reason:"voided", cause:"session_ended"}`；从不重新展示，从不带入新进程。
6. 没有 `turn_end` 的回合（`turn_start` 已先于装配写入，任何消息都有归属回合，🟡-6）→ 追加 `turn_end{stopped:"crashed", recovered:true}`，并经 `commit()` 追加一条恢复说明 user 消息（A3 已有旁证）。
7. 停机点对象在 `approvals_store.json` 里，恢复后照常由 `ensure_pending` 列出。

### 2.9 决策 9：长会话——v1 不压缩（问题 7）

理由：① 状态已在文件里（状态卡、停机点对象、`_agent/approval_feedback.md`、`memory.md`），新开会话只丢讨论过程；② ADR-0022 禁止 LLM 摘要，而历史里混着判据与规程原文；③ 可恢复压缩要改写已发送的消息，与 append-only 冲突，应等有数据再取舍；④ 目前没有数据，会话体量从本 spec 起才开始记录（`prompt_chars`）。已知量级：常驻层约 8.2 千字符；一期全部工序注入合计约 2.2 万字符（E8）；单次 `web_fetch` 上限 3 万字符，`read_artifact` 上限 200 000 字节（`tools.py:313`）。大头是工具结果。

上下文撑满时：服务商返回 400 → `LLMError` → 走 `error` 路径，收尾必然也失败 → 本地说明追加一句「会话上下文可能已满：新开会话（不加 --continue）不会丢失任何产物与审批状态」。不按报错文本做分类。

重开条件：累计 ≥ 20 个真实会话中出现 ≥ 3 次撑满 → 另立 spec，对可重取的只读结果做省略，并一并处理 S1-R0。

### 2.10 决策 10：范围闸门

**做**：§2.1–§2.9；协议命令 `memory_ack`、`scope`（asset / auto）；`--continue`、`--sessions`。

**不做**：流式输出；子会话落盘与协议化（桌面端要不要聚焦子会话归 Spec 10）；`/voice`、`/scout`、`/patch`、`/board` 的协议化（`/voice` 归 Spec 11）；协议会话的人时记账（Spec 11；与 Spec 8 RF-12 一致）；桌面端 UI、spawn 模板与 API 密钥来源（Spec 10）；`acquire gate`、`register` 进 ava（素材准备不在 §0.2 第 1 条的 01–07 范围内）；自然语言建期（Spec 10）。**不新立 ADR**：会话形态与恢复由 ADR-0020 §4/§5 与 direction §6 授权，两处措辞修订走 DIR-R1、ADR20-R1；若将来需要新 ADR，编号从 0026 起（0024、0025 已预留给 Spec 11、12）。

---

## 3. 数据契约

### 3.1 协议帧（v1，冻结）

- 成帧：UTF-8，一行一个 JSON 对象，以 `\n` 结尾；出站用 `json.dumps(obj, ensure_ascii=False, separators=(",", ":"))` 生成（`\r`、`\n` 必被转义；host 按 `\n` 切行，与 `desktop/src/main/index.ts:166` 同一做法）。
- 公共键：`"v": 1`、`"t"`；出站另有 `"seq"`（进程内单调）、`"sid"`。
- 不含可能超过 2^53 的整数（Spec 8 RF-23）。
- 入站逐类型 exact-keys；违例 → `error{E_BAD_REQUEST}`，进程继续；单行超过 `MAX_INBOUND_LINE_BYTES` → `E_TOO_LARGE`，并丢弃到下一个换行。

**入站**

| `t` | 键 | 语义 | 错误 |
|---|---|---|---|
| `user_message` | `text` | 开始一轮 | `E_BUSY`、`E_NOT_READY`、`E_SESSION_BROKEN` |
| `interrupt` | `turn_id` | 中断指定回合 | `E_STALE` |
| `answer` | `request_id`、`decision`、`feedback`（str 或 null） | 答复打开中的请求；`feedback` 仅 `tool_call` + `reject` 时可非空 | `E_UNKNOWN_REQUEST`、`E_REQUEST_CLOSED`、`E_BAD_REQUEST` |
| `command` | `name ∈ {memory_ack, scope}`、`arg`（`scope` 时 ∈ {asset, auto}，否则 null） | 人发起的命令 | `E_BUSY`、`E_BAD_REQUEST`、`E_NO_EPISODE` |
| `shutdown` | — | 优雅退出 | — |

**出站**

| `t` | 主要键 |
|---|---|
| `ready` | `episode`、`scope`、`continue_status ∈ {new, resumed, no_session, corrupt, schema_unknown, ambiguous}`、`llm ∈ {ok, degraded}`、`degrade_reason`、`code_freeze_ok`、`history_count`、`session_bytes`、`other_sessions: [{sid, messages, last_activity}]` |
| `history` | `index`、`role ∈ {user, assistant, tool, system_note}`（按 `origin` 映射：`user`→user，`assistant`→assistant，`tool`/`synthetic_tool`→tool，`injection`/`memory`/`wrapup_instruction`/`recovery_note`→system_note）、`text`、`name` |
| `turn_started` | `turn_id` |
| `assistant` | `turn_id`、`kind ∈ {answer, wrapup, local_note}`、`text` |
| `tool` | `turn_id`、`phase ∈ {start, end}`、`index`、`name`、`summary`、`ok`、`duplicate` |
| `request` | `request_id`、`kind`、`turn_id`、`title`、`card_text`、`fields`、`options`、`feedback_allowed` |
| `request_closed` | `request_id`、`reason ∈ {answered, voided}`、`decision` |
| `command_result` | `name`、`ok`、`text`（`memory_ack` 时为 `ack_external` 本次打印的原文，🟡-12） |
| `stop_points` | `items: [{approval_id, type, created_at, artifacts: [path], options, note, answer_via: "decision_bar"}]` |
| `turn_finished` | `turn_id`、`stopped`、`llm_calls`、`tool_calls`、`tool_executions`、`duplicates_rejected`、`checkpoints`、`wrapup`、`duration_s`、`prompt_chars` |
| `log` | `stream: "stdout"`、`text` |
| `notice` | `level`、`code`、`text` |
| `error` | `code`、`message` |
| `bye` | `reason` |

### 3.2 人审请求

```python
@dataclass(frozen=True)
class HumanRequest:
    request_id: str            # secrets.token_hex(16)，不进 messages
    kind: Literal["tool_call", "fetch", "checkpoint", "memory_ack"]
    turn_id: str | None
    title: str
    card_text: str             # 终端卡片同一文本，去掉末行提示
    fields: dict[str, Any]     # 字符串/布尔/小整数/列表，已剥控制字符
    options: tuple[str, ...]   # checkpoint: ("continue","stop")；其余 ("approve","reject")
    feedback_allowed: bool     # 仅 tool_call

@dataclass(frozen=True)
class HumanAnswer:
    request_id: str
    decision: str              # ∈ options，或内核生成的 "voided"
    feedback: str | None
    channel: Literal["tty", "protocol"]
    latency_s: float
```

- `tool_call.fields`：`tool`、`args`、`argv`、`target`（同 `cli.py:841-852`）、`stop_label`、`danger`、`memory_preview`。
- `fetch.fields`：`no`、`title`、`url`、`type`、`source`、`why`、`expected_dur`。
- `checkpoint.fields`：`llm_calls`、`tool_executions`、`elapsed_s`、`trigger ∈ {replies, executions}`。
- `memory_ack.fields`：`text`（`ack_external` 传给 `confirm` 的整段文本，`memory.py:1091`，🟡-12）。
- 终端新卡片文案（抓取卡实际每个字段一行，此处为节省篇幅合并写）：

```
┌─ [素材抓取] 候选 #N（逐条人审，ADR-0021 / Spec 6）
│ 标题: … │ URL: … │ 类型: … | 来源: … │ why: … │ 预计时长: …s（或「未知」）
└─ 抓取? [y/N]: 

┌─ [检查点] 本轮已连续 50 次模型调用（或 50 次工具执行）：模型调用 X 次，工具执行 Y 次，用时 Z 分钟
│ 这不是任务预算：继续则计数归零；停止则先收尾总结。
└─ 继续? [y/N]: 
```

### 3.3 `session.jsonl` 记录（schema 1）

公共键：`k`、`sid`、`seq`（会话内单调，跨段连续）、`ts`（UTC ISO）。

| `k` | 额外键 |
|---|---|
| `session_start` | `schema: 1`、`episode`、`scope_mode: "auto"`、`resident_sha256`、`pid` |
| `segment_start` | `resident_sha256`、`pid`、`resumed_from_seq` |
| `turn_start` | `turn_id`、`scope`、`step_key`（**先于装配写入**） |
| `msg` | `turn_id`、`origin ∈ {user, injection, memory, assistant, tool, wrapup_instruction, synthetic_tool, recovery_note}`、`message`（原样，含 `_ava_tier`）、`docs: [{path, sha256}]`（仅 injection/memory） |
| `tool_exec_started` | `turn_id`、`tool_call_id`、`name` |
| `request_opened` | `turn_id`、`request_id`、`kind`、`title` |
| `request_closed` | `request_id`、`reason`、`decision`、`channel`、`latency_s`、`cause` |
| `turn_end` | `turn_id`、`stopped`、`llm_calls`、`tool_calls`、`tool_executions`、`duplicates_rejected`、`checkpoints`、`wrapup`、`duration_s`、`prompt_chars`、`recovered` |
| `turn_rollback` | `turn_id`（重建时丢弃该回合全部 msg） |
| `repair_tool_results` | `after_seq`（所属 assistant 的 seq）、`results: [{tool_call_id, content}]` |
| `recovery` | `truncated_bytes`、`repaired` |
| `note` | `turn_id`、`text`（本地说明，不进 messages） |

### 3.4 `run_tool_loop` 返回契约

```python
{
  "messages": list[dict],        # 经 commit() 的完整历史（调用方不再自行 extend）
  "final": dict | None,          # 永远是 assistant 消息或 None
  "local_note": str | None,
  "stopped": "done" | "interrupted" | "error" | "checkpoint_stop" | "blocked" | "degraded",
  "rollback": bool,              # True = 调用方恢复回合开始时的 messages 与 tracker 快照
  "iterations": int,             # = llm_calls（保留旧键名）
  "llm_calls": int, "tool_calls_made": int, "tool_executions": int,
  "duplicates_rejected": int, "checkpoints": int,
  "wrapup": "none" | "ok" | "failed" | "aborted" | "skipped",
  "error": str | None,
}
```

### 3.5 常量

| 常量 | 值 | 依据 |
|---|---|---|
| `CHECKPOINT_EVERY` | 50（回复与执行各自的阈值） | §2.3 第 6 条（初值） |
| `PROTOCOL_VERSION` | 1 | — |
| `MAX_INBOUND_LINE_BYTES` | 1 048 576 | 入站只有人打的字与答复；1 MiB 远大于人手输入，又能挡住 host 缺陷造成的无界行 |
| `REQUEST_ID_BYTES` | 16 | 请求号是答复的唯一凭据，128 位使猜中不可行 |
| `FRAME_DRAIN_TIMEOUT_S` | 2 | 退出时排空出站队列的上限；低于 Spec 8 SIGTERM→SIGKILL 的 5 s |
| `WRAPUP_INSTRUCTION` | `[系统收尾] 本轮因{reason}停止，不再执行任何工具。请只基于上文已获得的信息作答：① 目前能确定的结论；② 还缺什么信息、为什么没拿到；③ 需要人做什么决定或操作。不要假装已完成未完成的步骤。` | 问题 8 ① 的三项要求逐字进模板 |

### 3.6 协议进程退出码

0 正常；2 用法错误（含 stdin 是 TTY、`--continue` 前缀不唯一）；3 期租约被占；4 期目录或 `data/` 不可达。

---

## 4. 模块接口与签名

### 4.1 `pipeline/agent/llm.py`（修改）

```python
def chat_complete(..., tool_choice: str | None = None) -> dict   # None 时 payload 无该键

@dataclass(frozen=True)
class Decision:                                               # 🟡-F：结构化的人审/放行决定
    ok: bool
    reason: str = ""
    provenance: Literal["human", "auto", "card_free", "precheck", "voided"] = "auto"
    feedback: str | None = None
# 判重清空规则只认 ok and provenance == "human" 且已执行（§2.3 第 5 条）

@dataclass
class LoopControl:
    interrupt: Any                                            # session.TurnInterrupt（鸭子类型，llm.py 不 import session）
    commit: Callable[[dict, str], None]                       # 唯一提交入口（§2.5）
    review: Callable[[str, dict], Decision]                   # 判重之后、执行之前调用；内含 review_tool_call + channel.ask
    ask_checkpoint: Callable[[dict], bool]                    # 计数快照 → 继续？
    post_execute: Callable[[str, dict, dict], dict] | None    # 抓取钩子
    fetch_executor: Callable[[int], dict] | None              # 注入的抓取执行器（🟡-9）
    on_trace: Callable[[dict], None] | None
    on_exec_started: Callable[[str, str], None] | None
    critical_tools: frozenset[str]                            # = session.CRITICAL_TOOLS

def run_tool_loop(messages, *, ctx=None, scope="creative", config=None, root=None,
                  approve=None, control: LoopControl | None = None) -> dict
# 删除 max_iterations。control 为 None 时为「裸循环」（仅测试与兼容用途，🟡-E）：
#   不装中断处理器；commit = 追加到本地列表；**不判重**（保持现有 16 处测试的语义）；
#   **有**检查点且一律「停止」（有界，随后照常收尾）；
#   review = 旧 approve 的适配器（approve 为 None → 直接执行，与现状相同；返回真值 → provenance="auto"）。
# 「没有人审通道就拒绝运行」由 AgentSession 执行（TK-5）；生产调用点必须传 control（TG-6）。
# import 方向固定为 session → llm（TG-7）。
```

### 4.2 `pipeline/agent/session_log.py`（新增，纯 stdlib）

```python
SCHEMA = 1
class SessionLocked(RuntimeError): ...
class SessionLogBroken(RuntimeError): ...
class EpisodeLease:
    @classmethod
    def acquire(cls, ep_dir: Path) -> "EpisodeLease"          # 每进程每期至多一次；LOCK_EX|LOCK_NB
    def append(self, record: dict) -> None                   # 循环写满；失败抛 SessionLogBroken
    def truncate_torn_tail(self) -> int
def list_sessions(raw: bytes) -> list[SessionSummary]        # 按 sid 聚合
def load_session(raw: bytes, sid: str) -> LoadedSession
def plan_repairs(s: LoadedSession) -> list[dict]             # §2.8 第 4–6 步
def rebuild_messages(s: LoadedSession) -> list[dict]         # 丢弃 rollback 回合；按 after_seq 插入修复结果
```

### 4.3 `pipeline/agent/session.py`（新增，内核）

```python
CRITICAL_TOOLS = frozenset({"write_episode_file", "acquire_propose", "write_memory", "browser"})

class HumanChannel(Protocol):
    name: Literal["tty", "protocol"]
    def show(self, kind: str, payload: dict) -> None: ...
    def ask(self, request: HumanRequest) -> HumanAnswer: ...

class TurnInterrupt:
    def installed(self) -> ContextManager[None]
    def defer(self) -> ContextManager[None]
    def request(self) -> None                 # 协议读者线程调用
    wrapup_started: bool                      # 之后到达的中断 = 放弃收尾

@dataclass
class ToolVerdict:
    action: Literal["reject", "allow", "ask"]
    reason: str = ""
    echo: str | None = None
    request: HumanRequest | None = None

def review_tool_call(name, args, *, ep_dir, scope, status, root, origin="model") -> ToolVerdict
def dedup_key(name: str, args: dict) -> tuple[str, str]

class SessionHost:                             # 每进程一个
    def __init__(self, ep_dir: Path | None, *, root, channel: HumanChannel): ...
    lease: EpisodeLease | None                 # --continue 与协议进程启动时取得；普通终端 REPL 在首个 agent 回合懒取（§2.5）
    main_messages: list[dict] | None           # bind_main() 登记的主会话列表对象；包装据 `is` 判断是否落盘
    def bind_main(self, messages: list[dict]) -> None
    ep_dir: Path | None                         # 绑定的期目录；包装收到的 ep_dir 不一致即视为未登记

@contextmanager
def activate_host(ep_dir: Path | None, *, root, channel) -> Iterator[SessionHost]   # 可重入；最外层 finally 注销并释放租约
    def main_session(self, *, resume: str | None | Literal[False]) -> "AgentSession"
    def sub_session(self, scope_mode: str, *, ep_dir: Path | None, extra_prompt: str = "") -> "AgentSession"   # persist=False；ep_dir 取自包装参数（ToolContext 用它，不用 host.ep_dir）

class AgentSession:
    def run_turn(self, text: str) -> TurnResult   # 外层仍捕获 LLMError/PermissionError（🔵-10）
    def memory_ack(self) -> CommandResult
    def set_scope_override(self, value: str | None) -> None
```

### 4.4 `pipeline/agent/protocol.py`（新增）

```python
def main(argv=None) -> int      # 第一步 fd 隔离（在 import pipeline.* 之前）→ SessionHost → ready → 主循环
class FrameWriter:              # 无界队列 + 写线程；send() 在延迟区内入队；EPIPE → 静默丢弃
class FrameReader:              # 守护线程；逐行校验；answer → _deliver_answer；interrupt → TurnInterrupt.request
class ProtocolChannel(HumanChannel): ...
def _deliver_answer(slots, frame) -> None      # 全仓唯一答复投递点
```

### 4.5 `cli.py`（修改）

`TtyChannel`；`_default_approve`、`_dispatch_agent_turn` 改为薄包装（签名不变，语义见 §2.6「直接调用包装的契约」）；四个 `_dispatch_agent_turn` 调用点保持经模块属性调用、参数不变；模块级 `_SESSION_HOST: SessionHost | None` 只经上下文管理器 `activate_host(ep_dir, *, root, channel)` 设置（可重入、`finally` 注销并释放租约，§2.6「登记的生命期」）；`main` 增 `--continue [<前缀>]`、`--sessions`、`ava --continue`（非 TTY 下退出 2，同 `cli.py:1716-1722` 规则）；`/help` 增两行（Ctrl-C 的两段语义、`--continue`）。

### 4.6 其他

- `tools.py`：`ASSET_COMMANDS` 增 `"acquire": {"fetch"}`（S6-R1）；`validate_pipeline_command` 的 `--force` 前缀判定（C-R5）。
- `status_card.log_approval_decision`：增 `channel: str | None = None`，默认值下记录字节不变（C-R4）。检查点调用时 `emit_event=False`。

### 4.7 一轮的处理顺序（冻结）

```
run_turn(text):
  with interrupt.installed():
    snapshot = (len(messages), tracker 深拷贝)
    写 turn_start                                   ← 先于装配（🟡-6）
    status、scope 由调用方传入（终端：_run_repl_body 按现状每轮 inspect_episode + scope_of 计算，M11 锚点不动；协议：主循环同法计算）
    装配（原 cli.py:909-976）；注入消息一律经 commit()
    commit(user 消息)
    run_tool_loop(messages, ctx, approve=..., control=...):
      loop:
        若 replies_since_cp ≥ 50 或 execs_since_cp ≥ 50：ask checkpoint（stop → 收尾）
        reply = chat_complete(...)               ← 可被中断
        commit(reply)；llm_calls += 1；replies_since_cp += 1
        无 tool_calls → done
        for call in tool_calls:
          若 execs_since_cp ≥ 50：ask checkpoint；stop → 本回复剩余调用补「未执行：检查点停止」→ 收尾（🟡-A）
          命中判重 → commit(重复调用结果)；continue
          decision = control.review(name, args)  ← session 侧：review_tool_call（纯确定性）+ 必要时 channel.ask（可被中断；记账带 channel）
          not decision.ok → 记判重；commit(拒因或拒绝，含 feedback)；continue
          写 tool_exec_started
          with (defer if name in CRITICAL_TOOLS): outcome = execute_tool(...)   ← 执行计数 +1
          if name == "acquire_propose" and ok: outcome = post_execute(...)       ← 抓取卡
          decision.provenance == "human" 且已执行 → 清空判重；否则记判重
          commit(tool 消息)
      except KeyboardInterrupt / LLMError / Exception → 经 commit() 补齐合成结果 → 收尾或回滚
    rollback → 恢复 snapshot，写 turn_rollback
    写 turn_end；（协议）turn_finished、stop_points；ensure_pending
```

---

## 5. 依赖白名单与纯洁性保障

- 新模块只用 stdlib（`json`、`os`、`sys`、`signal`、`threading`、`queue`、`secrets`、`fcntl`、`hashlib`、`time`、`datetime`、`dataclasses`、`contextlib`、`typing`、`pathlib`），以及仓内 `pipeline.paths`、`pipeline.status`、`pipeline.agent.*`、`pipeline.approvals`、`pipeline.candidates`。
- 禁止：`socket`、`http.server`、`socketserver`、asyncio server、`sqlite3`；numpy/torch/sentence_transformers/pysubs2/playwright/crawl4ai 的顶层 import（TG-1、TG-2）。
- 子进程纯洁性断言（红线 7）：TG-1 在干净子进程里 import 三个新模块后检查 `sys.modules`。
- `pyproject.toml` 零改动。

---

## 6. 跨 spec 接口与系统边界

### 6.1 修订请求（**2026-09-25 用户已全部授权**，含测试改写与 `verify_mutations` 重锚；动工仍须红队 🟢）

| 编号 | 对象 | 请求 | 级别 |
|---|---|---|---|
| **IS-R1** | impl-spec B3-r5 | 「tool_calls 循环必须有 max_iterations 上限」改为：无固定上限；防失控 = 人中断 + 本轮判重 + 检查点（回复或执行满 50 次）；非正常停止先收尾 | 阻塞（方向已由用户裁决） |
| **DIR-R1** | direction §6 Spec 9「约束」 | 在「Spec 1 的系统提示会话内稳定纪律在恢复时仍成立」后补：「稳定指一个进程段内字节级不变；恢复时按当前文件重建 `messages[0]`，历史逐字重放，注入变更以追加处理（Spec 9 §2.7）」 | 阻塞（用户 2026-09-25 认可；随 PR0 落盘） |
| **ADR20-R1** | ADR-0020 §4「通信」 | 在「事件流直接镜像 events.jsonl」后补：「job/approval 等流水线事件如此；agent 会话的对话、工具轨迹与人审请求经会话进程的 stdio 协议传递（Spec 9），会话进程不开端口」 | 阻塞（同上） |
| **C-R1** | `llm.py` | `run_tool_loop` 按 §4.1 改造；`chat_complete` 增 `tool_choice` | 阻塞 |
| **C-R2** | `cli.py` | 薄包装、`TtyChannel`、`SessionHost` 接入四个调用点、`--continue`/`--sessions` | 阻塞 |
| **C-R3** | 工具审查 | 模型发起的 `review` 不得带任何选项（§2.4.3） | 阻塞（用户裁决） |
| **C-R4** | `status_card.py` | `log_approval_decision` 增 `channel` | 非阻塞 |
| **C-R5** | `tools.py` `validate_pipeline_command` | `--force`、`--force-all` 按前缀判定（自查 S-1），人与模型同时生效 | 阻塞（修 ADR-0018 护栏的旁路） |
| **S6-R1** | Spec 6 + `tools.py` | 抓取卡流程；`ASSET_COMMANDS` 增 `acquire: {fetch}`；工具实现、schema、`cmd_fetch` 零改动 | 阻塞（用户裁决） |
| **S7-R1** | Spec 7 §6.6 | 落地 §6.6 预留的桌面端 ack 通道：协议命令 `memory_ack` 调用 `ack_external(root=…, confirm=<协议通道>, is_tty=lambda: True)`（`memory.py:1055-1064`，零改动）。**明确承认**这条路径不受 isatty 保护，是对 Spec 7「非 TTY 一律拒绝」防线的部分反转，信任边界同 Spec 7 RF-22 | 阻塞 |
| **S8-R1** | Spec 8 §2.12、§3.4 | ① 桌面端 spawn 的会话进程引发的 core 写入 = 终端 REPL 同情形下的写入；② 会话 spawn 模板须提供 `api_key_env` 指名的环境变量（Spec 8 白名单排除了 `*_API_KEY`） | 阻塞（交 Spec 10 并入） |
| **测试改动** | `tests/test_agent_tools.py:509`、`527` 与 `tests/test_agent_director.py:763`（改写）；`tests/test_agent_director.py:729`、`751`、`773`（打桩补 `**_`） | §2.3、§2.6 | 阻塞（2026-09-25 已获同意） |
| S6-R2 | `acquire.py` | `fetch --expect-url=` | **不提出**（RF-6） |
| S1-R0 | Spec 1 | 状态卡改为变化时追加 | **不提出**（§2.7 第 4 条） |

**`scripts/verify_mutations.py` 重锚表**（🟡-5、二轮 🟡-G；`tests/test_verify_mutations.py:114` 要求每个锚点命中且唯一；守的不变量一律不变。**新锚点须由施工方在 PR 里给出逐字串**，并跑通 `test_anchors_in_shipped_matrix_are_unique_in_repo` 与该条变异；锚点若是另一取值的前缀子串，须带换行，沿用 `verify_mutations.py:111` 注释的教训）：

| 条目（起始行） | 现锚点 | 新锚点 | 守的不变量 | 本 spec 对应 |
|---|---|---|---|---|
| M3a（69） | `llm.py` approve 返回值归一化 | `run_tool_loop` 中消费 `control.review` 返回的 `Decision` 的判定行（生产路径，不锚裸循环的适配器） | 拒绝被识别 | TL-5 |
| M3b（72） | `llm.py` `decision = approve(name, args)` | `decision = control.review(name, args)` 调用行 | 审批结果被消费 | TL-8、MUT-15 |
| M15b（154） | `llm.py` 拒因回喂 | `run_tool_loop` 中以 `decision.reason`/`feedback` 构造 tool 消息的行 | 拒因具体回喂 | TP-3 |
| M7（91） | `_dispatch_agent_turn` 降级分支（含 `print`） | 内核降级分支返回 `local_directive_message(...)` 的行（打印移到 `TtyChannel`） | LLM 缺失显式降级 | G7 |
| M11（114） | `_run_repl_body` 局部 `messages` 与每轮 `scope_of(status)` | **不动**（v0.4 按三轮 🟡-1 撤回 v0.3 的重锚：`_run_repl_body` 仍持有 `messages` 并每轮计算 scope） | scope 每轮热推导 | `test_m11_scope_hot_derivation_from_creative_to_pipeline`（`tests/test_agent_director.py:455`） |
| M15a（143） | `_default_approve` 预校验段 | `session.review_tool_call` 的 `run_pipeline` 预校验段 | 预校验先于弹卡 | TK-1 |
| M16-1、M16-2（158、163） | `_default_approve` 的 `side_effect = TOOL_SCHEMAS[name].get("side_effect", True)` | `review_tool_call` 同一行 | side_effect fail-closed | G3/G4、TG-5 |
| M20（188） | `_default_approve` 未注册/越 scope 预校验 | `review_tool_call` 同段 | 预校验 | G5 |
| M19（181） | `cli.py` `/chat` 分派块 | **不动，原变异文本不动**（v0.4 撤回 v0.3 的改写：局部 `messages` 仍在 `_run_repl_body`，原变异照常成立；v0.3 所列 G10、G11 是单回合金样本，杀不死「共享 messages」） | 子会话 messages 隔离 | `test_m19_chat_subloop_does_not_pollute_main_messages`（`:590`）、`test_m19_script_subloop_with_focus_prompt`（`:647`） |
| M26（279） | `cli.py` idea 的 `_dispatch_agent_turn(` | **不动**（v0.5 按四轮 🔵-1 撤回：idea 调用点保留经 `cli._dispatch_agent_turn`） | idea 不传真实期目录 | 现有用例（原守护用例不变） |
| M10（110） | `DEFAULT_MAX_ITERATIONS = 10\n` | `CHECKPOINT_EVERY = 50\n` → `CHECKPOINT_EVERY = 10**9\n`（带换行） | 检查点确实触发 | MUT-3 |

### 6.2 与 Spec 2

消费 `create_job`、`execute_job`（抓取 job 同路径）与 N28 中断路径（`jobs.py:599-637`）。不新增 EventType。job 输出经 `_drain` 写 `sys.stdout`（`jobs.py:563`），变成 `log` 帧。

### 6.3 与 Spec 3

不改任何 approval 函数；`stop_points` 帧只取 `approval_id`、`type`、`created_at`、`artifacts[].path`、`options`、`note`。

### 6.4 与 Spec 5

逐调用卡不变（`kind: tool_call`）。终端下 Ctrl-C 会杀死同组的 driver（§2.2）；两期同时用 browser 见 RF-8。

### 6.5 与 Spec 6、Spec 7

见 §2.4.4、§6.1 的 S6-R1、S7-R1。记忆注入在恢复时按 sha 追加；`memory_unconfirmed` 告警以 `notice` 发出。

### 6.6 对 Spec 10（桌面端）的硬性要求

| 编号 | 要求 |
|---|---|
| H-1 | `answer` 帧只能由人的点击产生，任何条件下都不得自动发出 |
| H-2 | 只有 `request` 帧能生成可点击的批准控件；`assistant`、`log` 文本里的任何字样都不能 |
| H-3 | 卡片字段与模型文本按纯文本渲染 |
| H-4 | 抓取卡逐条答复，没有「全部批准」；检查点卡没有「以后不再询问」 |
| H-5 | 会话进程退出时，它的全部未关闭请求卡立即作废 |
| H-6 | 停止按钮发 `interrupt{turn_id}`；关窗先发 `shutdown`，留出收尾时间后再 SIGTERM |
| H-7 | spawn 时提供 LLM 密钥环境变量；缺失时如实显示降级 |
| **H-8** | host 不得在没有人操作时生成 `user_message`（🟡-7：否则检查点按轮重置就被绕过） |
| **H-9** | host 只从会话进程的 stdout 解析帧；stderr 只作诊断显示，不解析 |
| H-10 | `approval_resolved` 事件没有 `approval_id` 时，不得一律标「拒执」（`App.tsx:395` 现状，Spec 8 遗留） |

### 6.7 八条施工红线对照

| 红线 | 落点 |
|---|---|
| 1 不引入数据库 | JSONL + flock；TG-2 |
| 2 无框架、无 server | 自写回合编排；stdio；TG-2 |
| 3 产物即状态 | TS-9、TG-4 |
| 4 停机点不减、ack 显式 | 停机点 ack 路径不动；模型的 `review` 旁路（含缩写）堵上 |
| 5 人审闸门不自动化 | 抓取逐条；EOF、超时、退出一律作废；H-1、H-4、H-8 |
| 6 工具表封顶 | 新增工具 0 个，schema 与描述字节不变 |
| 7 零重依赖 | §5；TG-1 |
| 8 期望值先跑、变异检验 | PR0 金样本；实验先于断言；§7.2 |

ADR-0018 保留条款：不引入 agent 框架；Code Freeze 横幅不变，协议下以 `notice{code:"code_freeze"}` 呈现；配音双层闸由 C-R5 修复其前缀旁路；封面标题只出候选，本 spec 不新增任何定稿动作。

---

## 7. 测试规格与变异检验矩阵

### 7.1 测试规格

期望值先在实现上跑出并解释清楚，再写进断言。LLM 替身两种：单元测试打桩 `chat_complete`；协议端到端用本地假端点，**校验「每条带 tool_calls 的 assistant，紧随其后的连续 tool 消息恰好覆盖其全部 id」，否则返回 400**（🟡-1）。抓取相关测试加 autouse 夹具：出现任何真实 `subprocess.Popen` 即失败（🟡-9）。

**工具循环（`tests/test_agent_loop.py`，PR1）**

| 编号 | 场景 | 断言 |
|---|---|---|
| TL-1 | 60 条工具回复（每条 1 个调用）后给最终答复；检查点答「继续」 | `done`、`llm_calls == 61`、检查点 1 次 |
| TL-2 | 无限回复，每条 1 个调用；检查点答「停止」 | 提问时 `llm_calls == 50`、`trigger == "replies"`；`checkpoint_stop`；收尾请求 `tool_choice == "none"` 且 `tools` 与前一次相同；`final["role"] == "assistant"` |
| TL-2b | 每条回复 7 个并行只读调用，**参数各不相同**（避开判重） | 在第 8 条回复内、第 51 次执行之前提问，`trigger == "executions"` |
| TL-2c | 一条回复 60 个并行只读调用（参数各异）；检查点答「停止」 | 第 51 次执行之前提问；执行计数恰为 50；其余 10 个 id 的 tool 消息为「未执行：检查点停止」；配对通过 |
| TL-3 | 同轮重复调用（键序不同、带首尾空格） | 执行 1 次；`duplicates_rejected == 1` |
| TL-4 | `read_status X` → `write_episode_file`（人批准并执行）→ `read_status X` | `read_status` 执行 2 次 |
| TL-4b | `read_status X` → `write_memory op=cite`（免卡）→ `read_status X` | 第二次被判重，执行 1 次 |
| TL-5 | 人拒绝后原样再请求 | 人审回调 1 次 |
| TL-6 | 第 3 次请求时注入中断 | 收尾被调用；`interrupted`；配对检查通过 |
| TL-7 | 3 个调用中第 1 个执行时注入中断 | 3 个 id 都有 tool 消息（「已中断」「未执行」「未执行」）；配对通过 |
| TL-8 | 等人答复时注入中断 | 该调用作废；执行计数 0 |
| TL-9 | 收尾请求进行中再注入中断 | `wrapup == "aborted"`、`final is None` |
| TL-9b | 首个中断尚未浮出时再注入一次（模拟 crawl 慢浮出） | 两次合并；`wrapup == "ok"` |
| TL-9c | 「停止中」（补合成结果的各次 commit 之间）注入中断 | 不抛；合成结果齐全；`wrapup == "ok"` |
| TL-9d | 「收尾后」（写 `turn_end`、`ensure_pending` 期间）注入中断 | `run_turn` 正常返回、`turn_end` 已写；该中断被丢弃并发 `notice`；随后空闲态不受影响 |
| TL-17 | 非主线程进入并持有 `defer()`，同时向主线程投递中断 | 主线程在 0.5 s 内抛出；非主线程退出 `defer()` 时不抛 |
| TL-10 | 收尾回复仍带 `tool_calls` | 不执行；补合成结果；配对通过 |
| TL-11 | 已有回复后 `LLMError`，收尾也失败 | `error`、`wrapup == "failed"`；不存在内容等于本地说明的 assistant 消息 |
| TL-12 | 首次请求即 `LLMError` | `rollback`、请求计数 1 |
| TL-13 | 第 2 次请求触发出网断言 | `blocked`、`rollback`、请求计数 2 |
| TL-14 | 固定剧本 | 各计数精确等于剧本值 |
| TL-15 | 不传 `tool_choice` | 请求体按 `tests/golden/llm_request_no_models.json` 同法比对一致 |
| TL-16 | 中断到达时正执行登记在 `CRITICAL_TOOLS` 的假工具 | 工具体跑完（写出标记文件）后才处理中断 |

**内核（`tests/test_agent_session.py`，PR2）**

| 编号 | 场景 | 断言 |
|---|---|---|
| TK-1 | 模型 `run_pipeline("review --approve" / "--a" / "--ap" / "--appr" / "--appr=1" / "--confirm-patch" / "pipeline.review --ap")`；模型 `run_pipeline("review")`；人经 `validate_pipeline_command("review --approve")` | 前七种拒收、人审回调 0 次；`review` 本身放行进卡；人的路径校验通过 |
| TK-2 | 夹具清单 4 条（2 新增、1 以前未抓、1 已在台账），`ctx.root` 与 `paths` 同源（夹具**同时**把 `paths.ROOT` 与 `paths.DATA` 指向 tmp） | 恰 3 张抓取卡，序号与顺序正确；批准第 1 张 → 注入执行器收到 N；拒绝第 2 张 → 未调用 |
| TK-3 | 出卡后改写清单使 N 错位 | `misaligned`，执行器未调用 |
| TK-3b | `ctx.root` 与 `paths.DATA` 不同源 | 不出抓取卡，发 `fetch_disabled_root_mismatch` |
| TK-3c | `paths.DATA` 指向不存在的路径（模拟 T7 未挂载） | 不出抓取卡，发 `fetch_disabled_data_unreachable`；`acquire_propose` 结果照常；`turn_end` 已写；进程未退出 |
| TK-4 | pipeline scope `run_pipeline("acquire fetch 1")`；asset scope 工具表 | 前者校验拒绝；后者没有 `run_pipeline` |
| TK-5 | 不给人审通道构造 `AgentSession` 并运行回合 | 抛错，工具零执行（不变量在内核层，`run_tool_loop` 的裸循环语义不变，🟡-E） |
| TK-6 | AST 扫描；抓取模型请求体 | `_deliver_answer` 调用点恰 1 处；请求号不出现在任何请求体里 |
| TK-7 | 终端与协议各批一次工具卡、各答一次抓取卡与检查点卡 | `approvals.jsonl` 的 `channel` 正确；检查点行不产生 `approval_resolved` 事件；作废不进 `approvals.jsonl` |
| TK-8 | 同进程主会话取得租约后进 `/script`；另一进程取同期租约；持锁进程被 SIGKILL 后再取 | 子会话正常运行；另一进程 `SessionLocked`；SIGKILL 后可取得 |
| TK-9 | `validate_pipeline_command("tts run --force-a" / "--forc" / "--force-al=1")`；`cloud down --forc` | 全部拒绝；`tts run --redo 3` 仍通过 |
| TK-10 | 未登记 `SessionHost` 时直接调用 `cli._dispatch_agent_turn(line, messages, …, tracker=t)`（沿用现有测试写法） | 调用后传入的 `messages` 列表对象被原地追加、`t` 被更新；期目录下不产生 `session.jsonl`；未取租约（另一进程可立即取得） |
| TK-12 | 同一进程内依次（**只打桩 `chat_complete`，不替换 `cli._dispatch_agent_turn`**，包装真实执行；夹具须让 `load_llm_config` 返回非 None，即设 `AVA_TEST_KEY` 并带 `config/agent.json`，否则 `/chat` 子循环走降级分支直接返回（`cli.py:1087-1089`），「乙」会被主会话当作输入吃掉）：① 经 `run_agent_loop(ep_A, "auto")` 进 REPL，输入依次为主会话「甲」→ `/chat` → 子会话「乙」→ `/quit` → 主会话「丙」→ `/quit`；② 退出后**直接调用真实包装**（`ep_A`，新列表）；③ 测试自己进入 `with activate_host(ep_A, …)`（此时尚无 agent 回合，`ep_A` 未取租约），在其中以 `ep_B` 直接调用真实包装，LLM 打桩返回一次 `read_status` 调用，`read_status` 的实现打桩为记录 `ctx.episode_dir`；③b 同一登记内先以 `ep_A` 直接调用包装一次（使 host 取得 `ep_A` 租约），再以 `ep_B` 调用，同样记录 `ctx.episode_dir` | ① `ep_A/session.jsonl` 含「甲」「丙」、不含「乙」（嵌套 `/chat` 未提前注销登记）；② `ep_A/session.jsonl` 字节与 ① 结束时相同，另一进程可立即取得 `ep_A` 租约；③ 记录的 `ctx.episode_dir == ep_B`；`ep_B` 下无 `session.jsonl`；另一进程仍可立即取得 `ep_A` 租约；③b 以 `ep_B` 调用时记录的 `ctx.episode_dir == ep_B`（此时租约已被持有，租约断言不再能区分变异，只剩这一条） |
| TK-13 | 经 `_run_repl_body` 跑 5 个主会话回合（LLM 打桩） | `session.jsonl` 中恰有 5 条 `turn_end`，且 5 条用户消息都在 |
| TK-11 | 登记 `SessionHost` 后，主会话列表与一个子会话列表各跑一回合 | 只有主会话的消息进 `session.jsonl`；子会话回合与主会话共用同一租约（同进程不报 `SessionLocked`） |

**会话日志（`tests/test_session_log.py`，PR2）**

| 编号 | 场景 | 断言 |
|---|---|---|
| TS-1 | 写入再加载 | 确定性序列化；`rebuild_messages` == 写入时的 `messages[1:]` |
| TS-2 | 末尾残行 | 截断；此前字节不变 |
| TS-3 | 目标会话 `session_start` 之后有坏行 | `corrupt`，追加新 `session_start`；坏行在目标会话之前时正常恢复 |
| TS-4 | 断掉的调用（有 / 无 `tool_exec_started`）、未关闭请求、无 `turn_end` 的回合 | 追加的修复记录正确；**原前缀逐字节不变**（夹具旧行用非默认空白写入） |
| TS-5 | 含 `turn_rollback` | 该回合消息不出现在重建结果里 |
| TS-6 | 一份注入文档改了、一份没改 | 只为改了的那份追加一条 |
| TS-7 | AGENTS.md 已改 | `resident_changed`；`messages[0]` 为当前装配结果 |
| TS-8 | 3 个会话，第 3 个没有 assistant 消息 | `--continue` 恢复第 2 个；`--continue <第 3 个的前缀>` 恢复第 3 个 |
| TS-8b | 恢复第 1 段后再恢复一次 | 记录交错时按 sid 正确聚合 |
| TS-9 | 删除 `session.jsonl` 前后 | `python -m pipeline.status <期> --json` 逐字节相同 |
| TS-10 | 在 `commit()` 的写盘与进内存之间注入中断 | 中断在提交完成后才浮出；盘上与内存条数相等 |
| TS-11 | 各种回合结局（done、interrupted、error、checkpoint_stop、blocked、rollback，含「回滚掉的回合里注入过记忆告警」） | 每种结局后内存 `messages[1:]` == `rebuild_messages(磁盘)`；告警被回滚后下一轮重新注入、终端不重复打印 |
| TS-12 | 历史中段存在缺配对的 assistant（不是最后一条） | `repair_tool_results` 插在该 assistant 之后；假端点配对校验通过 |
| TS-13 | 另一进程持租约时，`--sessions` 读取一个末尾有残行的文件 | 列表正确；文件字节不变（未持锁不截断） |

**协议端到端（`tests/test_agent_protocol.py`，PR3；子进程 + 假端点；测试侧 import 钩子注入测试工具，生产代码零钩子）**

| 编号 | 场景 | 断言 |
|---|---|---|
| TP-1 | 模型回复含换行与伪造的 `answer` 帧文本；测试工具内 `print`、`os.write(1, …)`、`subprocess.run(["echo", …])`（继承 stdout）；job 输出 | stdout 每行都能解析且 `t` 属于出站闭集；直写 fd 1 的内容出现在 stderr；挂起请求仍挂起 |
| TP-2 | 正常一轮 | 帧序列与计数正确 |
| TP-3 | 工具卡批准；拒绝附反馈 | 执行；tool 消息含反馈原文 |
| TP-4 | 慢端点上发正确 / 错误 `turn_id` 的 `interrupt` | 前者 2 s 内 `interrupted`，假端点收到 `tool_choice:"none"`；后者 `E_STALE` |
| TP-4b | 空闲时 SIGINT | 进程存活，发 `notice` |
| TP-5 | 等答复时关闭 stdin | `voided`、未执行、退出 0 |
| TP-6 | 回合中再发 `user_message`；多键帧 | `E_BUSY`；`E_BAD_REQUEST`；进程存活 |
| TP-7 | 未知请求号；已关闭请求；对检查点答 `approve` | 三种错误码 |
| TP-8 | 等卡时 SIGKILL → `--continue` → 下一条消息 | `resumed`，`history` 帧按 origin 标 role；假端点接受 |
| TP-9 | 同期第二个协议进程（带与不带 `--continue` 各一次） | 在 `ready` 之前以 `error` 帧说明并退出 3；文件字节不变 |
| TP-10 | `memory_ack` 批准 / 拒绝 / 「无需 ack」 | `command_result.ok` 与 `text` 为 `ack_external` 原文；批准时出现审计行且 `channel == "protocol"` |
| TP-11 | job 运行中 SIGTERM | 进程组消失；`wrapup == "skipped"`；退出 0 |
| TP-12 | 有挂起停机点 | `stop_points` 对象键集合精确等于 §3.1 |
| TP-13 | job 子进程读 stdin 时 host 发 `user_message`（用例 10 s 超时） | 子进程得 EOF；该帧被会话处理（可见 `E_BUSY`） |
| TP-14 | 读端慢速读取时发大帧（≥ 400 KB），写出途中 `interrupt` | host 收到的每一行都能解析；没有残帧 |

**终端（PR0/PR2）**

| 编号 | 场景 | 断言 |
|---|---|---|
| TT-1 | 金样本 G1–G13（进程内）、G-P1（pty） | 归一化后一致 |
| TT-2 | pty 下，回合中挂一个同组子进程，按 ^C；再在提示符处按 ^C | 第一次后提示符重新出现、输出含 `[中断]`；第二次后 REPL 退出 0 |
| TT-3 | `--continue`、`--continue <前缀>`、非 TTY、无会话、`--sessions`；普通启动且存在旧会话 | 恢复正确；退出 2；提示无会话；列表；打印一行提示且不阻塞 |
| TT-4 | 抓取卡、检查点卡、EOF | 文案同 §3.2；EOF 视为否 |
| TT-5 | `/script` 回合中 ^C（pty） | 回到 `/script` 提示符，有收尾输出 |
| TT-6 | 恢复后 `/approve 05` 不带 `--id` | 报「尚未在本会话展示」 |

**静态与纯洁性**

| 编号 | 断言 |
|---|---|
| TG-1 | 子进程 import 三个新模块后没有重依赖 |
| TG-2 | 新模块源码不含 `socket`、`http.server`、`socketserver`、`start_server`、`.bind(`、`.listen(`、`sqlite3` |
| TG-3 | `pipeline/` 下不再有 `DEFAULT_MAX_ITERATIONS`、`max_iterations` |
| TG-4 | `status.py`、`approvals.py`、`jobs.py` 不出现 `session.jsonl` |
| TG-5 | 字面量 `CRITICAL_TOOLS` == `{side_effect 为真的工具} − {run_pipeline}` |
| TG-6 | AST：`pipeline/` 下每个 `run_tool_loop(` 调用点都带 `control=` 关键字参数 |
| TG-7 | `pipeline/agent/llm.py` 不 import `pipeline.agent.session`、`pipeline.agent.protocol` |

### 7.2 变异检验矩阵

逐条实跑：植入 → 目标用例变红 → 还原，结果回填施工报告。

| 编号 | 变异 | 变红 | 机理（为何不会被别的断言掩盖） |
|---|---|---|---|
| MUT-1 | 第 10 次回复后 break | TL-1 | `llm_calls == 61` 失败；检查点答「继续」，不会先停 |
| MUT-2 | 检查点差一（49 时问） | TL-2 | 提问时计数不等于 50 |
| MUT-3 | 忽略检查点答复 | TL-2 | 不会出现 `checkpoint_stop`（请求计数 200 为护栏，强制失败） |
| MUT-4 | 收尾不带 `tool_choice` | TL-2、TP-4 | 记录到 None |
| MUT-5 | 中断时不补合成结果 | TL-7 | 配对检查失败（收尾已成功，不掩盖） |
| MUT-6 | 判重键不 `sort_keys` | TL-3 | 执行 2 次 |
| MUT-7 | 人批准的副作用执行后不清空 | TL-4 | 第二次被拒 |
| MUT-8 | 任何执行后都清空 | TL-3 | 重复的只读调用被放行 |
| MUT-9 | 人拒过的不记判重 | TL-5 | 回调 2 次 |
| MUT-10 | 本地说明作为 assistant 消息追加 | TL-11 | 断言失败 |
| MUT-11 | 首调失败也收尾、不回滚 | TL-12、TT-1（G8） | 请求计数 2；G8 多出输出 |
| MUT-12 | 临界区不延迟 | TL-16 | 标记文件不存在 |
| MUT-13 | 删除 `review` 选项拦截 | TK-1 | 进卡 |
| MUT-14 | 不复核序号 | TK-3 | 执行器被调用 |
| MUT-15 | 抓取卡跳过人审 | TK-2 | 回调次数不是 3；被拒条目也被抓 |
| MUT-16 | 协议启动不做 `dup2(2, 1)` | TP-1 | `os.write(1)` 与 `echo` 的输出出现在 stdout，该行不能解析（v0.2 按 🟡-10 改正机理） |
| MUT-17 | 不替换 fd 0 | TP-13 | job 子进程读走 host 帧，会话没收到 |
| MUT-18 | `interrupt` 不核对 turn_id | TP-4 | 错误 turn_id 也打断了回合 |
| MUT-19 | EOF 时当成批准 | TP-5 | 工具被执行 |
| MUT-20 | 接受对已关闭请求的答复 | TP-7 | 没有 `E_REQUEST_CLOSED` |
| MUT-21 | 不取租约 | TK-8、TP-9 | 另一进程取得；没有退 3 |
| MUT-22 | 不截断撕裂尾巴 | TS-2 | 下一条记录拼接后解析失败 |
| MUT-23 | 修复时重写整个文件 | TS-4 | 非默认空白的旧行被改写，前缀比对失败 |
| MUT-24 | 恢复沿用旧常驻层 | TS-7 | `messages[0]` 不等 |
| MUT-25 | 不比对注入 sha | TS-6 | 缺再注入 |
| MUT-26 | `status.py` 读 `session.jsonl` | TS-9、TG-4 | 输出不同；静态扫描命中 |
| MUT-27 | `session.py` 顶层 `import numpy` | TG-1 | 命中 |
| MUT-28 | `TtyChannel` 改了提示文案 | TT-1（G3/G4、G-P1） | 金样本不等 |
| MUT-29 | REPL 不在回合级处理中断 | TT-2 | 进程退出 |
| MUT-30 | 不写 `channel` | TK-7 | 缺字段 |
| MUT-31 | 协议空闲时不忽略 SIGINT | TP-4b | 进程退出 |
| MUT-32 | `stop_points` 带出 `mtime_ns` | TP-12 | 键集合不等 |
| MUT-33 | 请求号写进工具结果 | TK-6 | 请求体里检出 |
| MUT-34 | 测试中临时注册一个 side_effect 假工具，而 `CRITICAL_TOOLS` 不变 | TG-5 | 集合不等（改成字面量后此变异才成立，🔵-8） |
| MUT-35 | `review` 拦截退回「只认 `--approve`、`--approve=`」 | TK-1 | `--a`、`--ap`、`--appr` 进卡 |
| MUT-36 | `--force` 禁令退回全拼比对 | TK-9 | `--force-a` 通过 |
| MUT-37 | `commit()` 的写盘与进内存不在同一延迟区 | TS-10 | 中断在两步之间浮出，盘上多一条 |
| MUT-38 | 主线程直接写帧（去掉写线程） | TP-14 | host 收到残帧，该行不能解析 |
| MUT-39 | 子会话各自 `EpisodeLease.acquire` | TK-8 | 进 `/script` 时 `SessionLocked` |
| MUT-40 | 免卡写入也清空判重 | TL-4b | 第二次 `read_status` 被执行 |
| MUT-41 | 检查点只数回复 | TL-2b | 第 9 次请求前没有提问 |
| MUT-42 | 回滚只弹用户消息（注入留在内存） | TS-11 | 内存与重建不等 |
| MUT-43 | 删掉同源检查（v0.3 按 🟡-I 改写：原「复核读哪个清单」为等价变异） | TK-3b | 不同源时仍出卡 |
| MUT-44 | `--continue` 恢复最后一个 `session_start` | TS-8 | 恢复了没有 assistant 消息的第 3 段 |
| MUT-45 | 同一回合的第二次中断不合并 | TL-9b | `wrapup == "aborted"` |
| MUT-46 | 执行计数只在模型请求之前检查 | TL-2c | 60 个调用全部执行，第 51 次前未提问 |
| MUT-47 | 延迟区深度与待处理中断改为不分线程 | TL-17 | 非主线程持有 `defer()` 时主线程的中断被延迟，0.5 s 内未抛；且非主线程退出时抛出 |
| MUT-48 | 「停止中」到达的中断照常抛出 | TL-9c | 合成结果不齐或 `run_turn` 抛出 |
| MUT-49 | 「收尾后」到达的中断带进空闲态 | TL-9d | 回合结束后立即以 KeyboardInterrupt 退出 |
| MUT-50 | 协议启动 / `--continue` 改为懒取租约 | TP-9 | 第二个进程先发出 `ready` 帧，断言「`ready` 之前退出 3」失败 |
| MUT-51 | 生产调用点不传 `control` | TG-6 | AST 检出 |
| MUT-52 | 同源检查改用 `require_data()` | TK-3c | `SystemExit` 被纵深防御捕获后发的是 `fetch_hook_error`，不是 `fetch_disabled_data_unreachable`，断言失败（若连纵深防御一并去掉，则 `turn_end` 未写） |
| MUT-53 | 回滚不回退 `memory_warn_injected` | TS-11 | 回滚后下一轮历史中没有告警消息，「下一轮重新注入」的断言失败 |
| MUT-54 | 包装按「有无 `SessionHost`」而非对象同一性决定落盘（子会话也落盘） | TK-11 | 子会话消息出现在 `session.jsonl` |
| MUT-56 | `activate_host` 退出时不注销登记 | TK-12 ② | 未注销的登记仍持有 `ep_A` 的租约（直接调用走子会话分支、`persist=False`，不写会话记录），「另一进程可立即取得租约」失败（v0.6 按五轮 🔵-1 改正机理） |
| MUT-57 | 包装不比较 `ep_dir`（有登记即用） | TK-12 ③ | `ep_B` 的调用借用 `ep_A` 的登记走子会话分支，首个 agent 回合使 host 懒取 `ep_A` 租约，「另一进程仍可立即取得 `ep_A` 租约」失败（`ep_B` 下无文件在两种实现下都成立，不作杀手，五轮 🟡-2） |
| MUT-58 | `activate_host` 不可重入（嵌套进入时替换或注销） | TK-12 ① | `/chat` 返回后登记已被撤销，主会话「丙」未落盘 |
| MUT-60 | **复合变异**：MUT-57 + 子会话的 `ToolContext.episode_dir` 取自 `SessionHost.ep_dir` 而非包装参数 | TK-12 ③b（③ 同样变红，但那里租约断言会先失败，证明不了期目录断言单独有效） | ③b 中租约已被持有，租约断言在变异下仍成立；只有「记录的 `ctx.episode_dir == ep_B`」失败（变异下为 `ep_A`）。单独植入后者是**等价变异**（期目录比较正确时，进子会话分支的调用其 `ep_dir` 与 `host.ep_dir` 解析后同一目录），不单列；「期目录取自参数」是 MUT-57 之上的纵深防御，只能以复合形式检验 |
| MUT-59 | `_run_repl_body` 第一个回合后执行 `messages = []` 重新赋值 | TK-13 | 其后的回合不再落盘，`turn_end` 少于 5 条 |
| MUT-55 | `_run_repl_body` 的回合改为直接调 `SessionHost`、绕过模块属性 `cli._dispatch_agent_turn` | `tests/test_agent_director.py:455`、`:590` 等现有替身用例 | 替身不再被调用：`test_m11` 记录的 scope 序列为空、`test_m19` 断言的调用不发生 |

---

## 8. PR 划分

| PR | 内容 | 测试 | 前置 |
|---|---|---|---|
| **PR0** | DIR-R1、ADR20-R1 文本落盘；录制金样本 G1–G13、G-P1、G-P2（只加测试与夹具，不改生产代码） | TT-1 | §6.1 授权（已获）+ 红队 🟢 |
| **PR1** | C-R1；改写 3 条上限测试；`verify_mutations` 的 M3a/M3b/M15b/M10 重锚；首日由人持密钥实测 A1、A2 | TL-*、TG-3 | PR0 |
| **PR2** | `session_log.py`、`session.py`、`cli.py` 接线（C-R2/C-R3/C-R4/C-R5）、S6-R1、`--continue`/`--sessions`；打桩签名；M15a/M16-1/M16-2/M20/M7 重锚（M11、M19、M26 不动） | TK-*、TS-*、TT-*、TG-1/2/4/5 | PR1 |
| **PR3** | `protocol.py` | TP-* | PR2 |
| **PR4** | `docs/WORKFLOW.md` 增 `--continue` 一行、`/help` 增两行；MUT 全表实跑；A1–A8 回填 | MUT 全表 | PR3 |

---

## 9. 验收门禁清单

- [x] **门禁 1（授权）**：§6.1 全部阻塞项与 `verify_mutations` 重锚表已于 2026-09-25 获用户授权；
- [ ] **门禁 2（终端零回归）**：TT-1 全部一致；除 §6.1 的测试改动外基线全绿——**12 处 `_dispatch_agent_turn` 替身用例与 21 处直接调用零改动**（TK-10，MUT-55 被捕获）；差异不超出 I-1~I-11；
- [ ] **门禁 3（无上限且不失控）**：TL-1/2/2b/2c/14、TG-3 全绿，MUT-1/2/3/41/46 被捕获；
- [ ] **门禁 4（先收尾后停）**：TL-6~13、TL-9b/9c/9d、TL-17、TP-4 全绿，MUT-4/5/10/11/45/47/48/49 被捕获；**首日实测 A1、A2**；
- [ ] **门禁 5（判重）**：TL-3/4/4b/5 全绿，MUT-6/7/8/9/40 被捕获；
- [ ] **门禁 6（答复只来自人）**：TK-5、TK-6、TP-1、TP-5、TP-7、TG-6、TG-7 全绿，MUT-15/19/20/33/51 被捕获；
- [ ] **门禁 7（旁路已堵）**：TK-1、TK-9 全绿，MUT-13/35/36 被捕获；
- [ ] **门禁 8（抓取卡）**：TK-2/3/3b/3c/4 全绿，MUT-14/15/43/52 被捕获；**在真实 `data/` 上手验一次**：提案 2 条 → 出 2 张卡 → 批准 1 条 → 台账 +1；
- [ ] **门禁 9（中断）**：TL-16、TP-4、TP-4b、TP-11、TT-2、TT-5 全绿，MUT-12/18/29/31 被捕获；**真机手验**：`render` 运行中按 ^C → `pgrep ffmpeg` 为空、回到提示符、有收尾输出；
- [ ] **门禁 10（崩溃恢复与提交）**：TS-1~12、TP-8、TP-14 全绿，MUT-22/23/24/25/37/38/42/44/53 被捕获；**手验**：工具卡上 `kill -9` 后 `--continue` 继续对话不报 400；
- [ ] **门禁 11（隔离与租约）**：TK-8、TK-11、TK-12、TK-13、TP-9、TP-13、TS-13 全绿，MUT-16/17/21/39/50/54/56/57/58/59/60 被捕获；排除执行顺序依赖：`uv run pytest tests/test_agent_director.py tests/test_agent_memory.py tests/test_agent_session.py` 与反过来的文件顺序各跑一遍全绿（不引入新插件）；
- [ ] **门禁 12（观测层不参与状态）**：TS-9、TG-4 全绿，MUT-26 被捕获；
- [ ] **门禁 13（依赖纯洁、无 server）**：TG-1/2/5 全绿，MUT-27/34 被捕获；`pyproject.toml` 无 diff；
- [ ] **门禁 14（文档）**：`uv run pytest tests/test_docs_invariants.py` 全绿；`docs/WORKFLOW.md` ≤ 100 行。

---

## 10. 潜在红旗与自纠预案

| 编号 | 红旗 | 预案 |
|---|---|---|
| RF-1 | 新增 side_effect 工具忘了登记临界区 | TG-5 当场红 |
| RF-2 | 检查点间隔不合适（A7） | §2.3 第 6 条校准规则 |
| RF-3 | 服务商忽略 `tool_choice:"none"`（A1） | 不执行、补合成结果；门禁 4 首日实测 |
| RF-4 | 收尾本身的成本与等待 | 只在已有回复时收尾；收尾开始后再中断即放弃 |
| RF-5 | 本机其他程序冒充 host 答复（T2） | 如实声明；`channel` 留痕；不引入签名（同 Spec 7 RF-22） |
| RF-6 | 抓取卡复核与 `cmd_fetch` 读清单之间的毫秒窗口 | 后果是抓了清单里另一条人提过的候选，`gate` 由人把关；需要关闭时提出 S6-R2 |
| RF-7 | 抓取阻塞工具循环 | 可中断；桌面端经 events 看 job 进度 |
| RF-8 | 两期同时用 browser | 后者启动失败，作为数据回喂（`web_browser.py:317-322`） |
| RF-9 | 中断浮出慢：browser（临界区，≤ `browser.timeout_s`）、crawl（等工作线程，≤ `crawl.timeout_s`；红队实测 0.5 s 发出、4.03 s 浮出） | 同回合中断合并，不误伤收尾（TL-9b）；`duration_s` 可见；实测过长时再评估改造 |
| RF-10 | `session.jsonl` 增长 | v1 不轮转；`ready.session_bytes` 观测 |
| RF-11 | 终端 Ctrl-C 习惯改变 | `/help` 写明；空闲时一次 Ctrl-C 退出的行为不变 |
| RF-12 | 出站队列无界，host 长时间不读 | 帧以摘要为主（工具结果正文不进帧）；job 输出多时 `log` 帧可能很多，记为观测项 |
| RF-13 | 金样本锁死本应改进的终端文案 | 改文案须单独提出，同步更新金样本 |
| RF-14 | 状态卡刷新使历史前缀缓存失效（S1-R0） | 未提出；`prompt_chars` 观测 |
| RF-15 | 判重挡不住参数微扰 | 裁决的已知代价；检查点兜底 |
| RF-16 | 桌面端违反 H-1~H-10 | Spec 10 逐条承接并测试 |
| RF-17 | `cli.py:644-646` 在 EOF 时取「是」 | 协议下不可达；Spec 11 按钮化 `/voice` 时必须改为显式答复 |

---

## 附：实验记录（2026-09-25，均在 scratchpad，未改仓库）

| 编号 | 目的 | 结果 |
|---|---|---|
| E1 | 协议 fd 隔离 | stdout 只有协议帧；`print` 与子进程 `echo` 进 stderr；子进程 `input('')` 得 EOFError、退 1 |
| E2 | 中断能否打断主线程阻塞点 | `pthread_kill` 与进程级 `kill`，四种阻塞点均在 0.50–0.76 s 抛 KeyboardInterrupt；3.12.13 / 3.13.14 / 3.14.5 一致 |
| E3 | 伪终端能否骗过 isatty | 管道下 False；`script -q /dev/null` 下 True |
| E4 | 启动耗时 | 0.05–0.09 s；热路径无重依赖 |
| E5a | 现状上限 | 恰 10 次请求后打印上限 WARN |
| E5b | 现状回合中 SIGINT | 进程退 1，回溯停在 `socket.recv_into` |
| E6 | 延迟区 | 延迟区内的中断在其完成后才抛；区外 0.20 s 即抛 |
| E7 | 状态卡稳定性 | 相隔 1.2 s 两次构建逐字节相等 |
| E8 | 注入体量 | 常驻层 8215 / 8233 / 8389 字符；工序层合计 22 204 |
| R1 | （复核 🔴-1、自查 S-1）前缀缩写 | `review --a` / `--ap` / `--appr` / `--appr=1` / `pipeline.review --ap`、`tts run --force-a` 均 `ok=True`；`tts run --force-all` 被拒 |
| R2 | （复核 🟡-2）大帧撕裂 | 400 034 字节帧写到 1 s 后才读的读端，0.3 s 发 SIGINT：读端收到 65 536 字节、末尾无 `\n` |
| R3 | （复核 🔴-2）同进程二次 flock | 第二个 fd `LOCK_EX\|LOCK_NB` 得 Errno 35 |
| R4 | （复核 🔵-2、🟡-12、🟡-9、🔵-3、🔵-5） | T7 为 `apfs, local`；`memory.py:1091` 拼接后传给 `confirm`；`candidates.py:30-49` 走 `require_data()`；`cli.py:644-646` EOF → "y"；`App.tsx:395` 对无 `approval_id` 的事件标「命令卡拒执」 |

## 附：行号核实自查表

2026-09-25 对照工作树（HEAD `c31201d`）。v0.1 的约 70 处引用经红队抽查全部命中；下表为 v0.2 现存引用。

| 引用 | 核实结果 |
|---|---|
| `llm.py:33` / `255` / `263-277` / `320` / `325` / `344-357` / `353-355` / `365-366` | ✓（v0.1 已核，红队复核一致） |
| `cli.py:644-646` | ✓ v0.2 补核：`except EOFError: confirm = "y"` |
| `cli.py:829` / `841-852` / `856-859` | ✓ |
| `cli.py:887-976` / `909-976` / `910-914` / `938-944` / `943` / `958-960` / `991` / `994-1002` / `998-1002` / `1007-1009` | ✓ |
| `cli.py:1038-1125` / `1070` / `1114` / `1237-1246` / `1243` / `1510` | ✓ v0.2 补核：`_dispatch_agent_turn(` 的四个调用点 |
| `cli.py:1299` / `1307` / `1313-1318` / `1315` / `1446-1504` / `1691-1703` / `1716-1722` | ✓ `1299` 为 `displayed_approvals: dict[str, str] = {}` |
| `tools.py:26` / `32-42` / `45-51` / `156-277` / `311` / `313` / `703` / `898-911` | ✓ |
| `tts.py:2113-2116` | ✓ v0.2 补核：`run` 子命令的 `--force`、`--force-all` |
| `review.py:373` / `394` | ✓ `input("")`；`ArgumentParser` 未设 `allow_abbrev` |
| `jobs.py:533-540` / `535` / `536` / `539` / `563` / `598` / `599-637` | ✓ `cwd=paths.ROOT`；`stdout=subprocess.PIPE`；BaseException 分支至 `raise`（637） |
| `approvals.py:57-62` / `58` | ✓ |
| `memory.py:43` / `810` / `1055-1064` / `1091` | ✓ `CARD_FREE_OPS = frozenset({"cite"})`；`requires_card=op not in CARD_FREE_OPS`；`is_tty` 注入点；`confirm(f"{diff}\n\n{text}")`（v0.3 更正：v0.2 误作 1093，1093 为 `return False`） |
| `candidates.py:30-49` | ✓ `incoming()` 经 `require_data()` 并 mkdir；`candidates_path()`、`ledger_path()` |
| `status_card.py:368` 起 | ✓ `if emit_event:` 默认发 `approval_resolved` |
| `web_browser.py:317-322` | ✓ |
| `web_crawl.py:186-187` | ✓ `with ThreadPoolExecutor(max_workers=1)` + `asyncio.run` |
| `playwright/_impl/_transport.py:120`（`.venv` 内） | ✓ `asyncio.create_subprocess_exec`，未开新会话 |
| `desktop/src/main/index.ts:166` | ✓ 按 `"\n"` 切行 |
| `desktop/src/renderer/App.tsx:395` | ✓ |
| `scripts/verify_mutations.py:69` / `72` / `110` / `143` / `154` / `158` / `163` / `181` / `188` / `279` | ✓ v0.2 补核：M3a/M3b/M10/M15a/M15b/M16-1/M16-2/M19/M20/M26 条目起始行 |
| `tests/test_verify_mutations.py:114` | ✓ 锚点须命中且唯一 |
| `tests/test_agent_tools.py:509` / `527`；`tests/test_agent_director.py:729-730` / `751-752` / `763` / `773` | ✓ 两处打桩直接抛 `PermissionError`、`LLMError` |
| `jobs.py:556-579` | ✓ v0.3 补核：`_drain` 在工作线程中 `target_stream.write`，只 `except Exception`，`finally` 关管道 |
| `paths.py:20-21` / `131` / `144` / `149` | ✓ v0.3 补核：`ROOT`、`DATA` 模块常量；`require_data()` 两处 `raise SystemExit` |
| `scripts/verify_mutations.py:91-97` / `111` / `114-120` / `184-186` | ✓ v0.3 补核：M7 锚点含 `print`；M10 注释「锚点带换行」；M11 锚在局部 `messages`；M19 变异文本 `messages.append(...)` |
| 测试中 `run_tool_loop` 调用 | ✓ v0.3 补核（AST）：24 处，16 处未传 `approve`：`test_agent_pr6.py:276`、`test_agent_tools.py:443/490/515/536/768/985/1017`、`test_agent_llm_tiering.py:115/131/141/169/241/266/283/320` |
| `tests/test_agent_director.py:100/124/143/478/503/608/634/661/710/925`、`tests/test_agent_memory.py:1726/1779` | ✓ v0.4 补核：12 处 `setattr` 替身；director 各处替身签名固定为 `(line, messages, ep_dir, scope, status, extra_prompt="", root=None, tracker=None)`（`:118`、`:472`、`:499`、`:601`、`:657`、`:699`），memory 两处为 `**kwargs` |
| `tests/test_agent_director.py:455` / `590` / `647` | ✓ v0.4 补核：`test_m11_scope_hot_derivation_from_creative_to_pipeline`、`test_m19_chat_subloop_does_not_pollute_main_messages`、`test_m19_script_subloop_with_focus_prompt` 的 `def` 行 |
| 测试中直接调用 `_dispatch_agent_turn(` | ✓ v0.4 补核：21 处 |
| `cli.py:1301` / `1512` / `1433-1444` | ✓ v0.5 补核：`messages` 唯一声明；作为参数下传；`/chat`、`/script` 在 REPL 循环内调 `run_agent_loop`（嵌套进入） |
| 基线 | ✓ 1715 passed |

## 附：红队裁决纪要（一至六轮，新轮次在前）

### 第六轮红队定向复审（v0.6 → v0.7；结论 🟢 可动工）

复审范围：TK-12 与 MUT-56/57/58/60。纸面推演：TK-12 ① 杀 MUT-58（兼杀 MUT-54），② 杀 MUT-56，③ 杀 MUT-57；五轮 🟡-1、🟡-2 与作者自查「工具上下文的期目录取自包装参数」闭环，无新 🟡/🔴。三条 🔵 由红队按用户指示（2026-09-25「直接顺手改🔵」）直接修订：

| 编号 | 问题 | 修订 |
|---|---|---|
| 🔵-1 | MUT-60 含 MUT-57，TK-12 ③ 的租约断言会先失败，证明不了 `ctx.episode_dir` 断言单独有效 | TK-12 增 ③b：同一登记内先以 `ep_A` 调用一次使租约已被持有，再以 `ep_B` 调用；MUT-60 的杀手改为 ③b（此时只剩期目录断言能区分变异） |
| 🔵-2 | TK-12 ① 未写前置条件：`load_llm_config` 为 None 时 `/chat` 子循环走降级分支直接返回（`cli.py:1087-1089`），「乙」被主会话吃掉 | TK-12 场景写明夹具须设 `AVA_TEST_KEY` 并带 `config/agent.json` |
| 🔵-3 | 篇幅 | 一至五轮裁决表移入本附录，§1 只留摘要 |

### 第五轮红队定向复审裁决与修订纪要（v0.5 → v0.6，2🟡 + 2🔵 全收，另作者自查 1 项；原裁决「🟡 修订后定向复审，改完可直接判 🟢」）

复审结论：四轮 🟡 与作者自查「登记可重入」在设计上关闭（红队核实 `/chat`、`/script` 在 REPL 循环内调 `run_agent_loop`，`cli.py:1433-1444`；`/memory digest` 直接调包装，`cli.py:1243`，不触发嵌套）；四轮 🔵-1~3 已落地。只剩 TK-12 写法的两处缺陷。**每条均已对照 spec 原文与代码独立复核**。

| 编号 | 红队指控 | 复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🟡-1 | TK-12 第 ① 步「替身拦截回合」与「主会话那句进 `session.jsonl`」互相矛盾：写盘在包装内部，替身拦在包装之外 | 属实（v0.5 TK-12 原文如此；按「期望值先跑」在无变异代码上即红，若施工方删断言则 MUT-58 失去杀手） | **采纳** | TK-12 第 ① 步改为**只打桩 `chat_complete`**，包装真实执行；第 ② 步写明直接调用**真实**包装 |
| 🟡-2 | MUT-57（不比较期目录）用 TK-12 第 ③ 步杀不死；真正的危害（`ep_B` 的回合工具上下文指向 `ep_A`）没测到 | 属实：正确实现与 MUT-57 下 `ep_B` 都无 `session.jsonl`（后者走子会话分支、`persist=False`）；v0.5 §4.3 的 `sub_session(scope_mode, *, extra_prompt)` 无 `ep_dir` 参数 | **采纳** | 第 ③ 步在测试自己进入的 `activate_host(ep_A, …)` 内执行，增加两条断言：打桩工具记下的 `ctx.episode_dir == ep_B`；`ep_A` 的租约状态未被这次调用改变（另一进程仍能立即取得）。MUT-57 机理改为由租约断言杀死 |
| 自查 | （红队未提）即使期目录比较正确，`sub_session` 从 `SessionHost` 取期目录也是隐患 | 属实（同 🟡-2 证据） | **作者自查新增** | §4.3：`sub_session(scope_mode, *, ep_dir, extra_prompt="")` 显式接收期目录；**`ToolContext.episode_dir` 一律取自包装收到的 `ep_dir` 参数，从不取自 `SessionHost`**。新增复合变异 MUT-60（MUT-57 + 工具上下文取 host 的期目录），由 TK-12 ③ 的 `ctx.episode_dir` 断言杀死；单独的后者是等价变异，如实不单列 |
| 🔵-1 | MUT-56 的机理栏写错：变异下直接调用走子会话分支、`persist=False`，不会写会话记录 | 属实 | 采纳 | 机理改为：未注销的登记仍持有 `ep_A` 的租约，TK-12 ② 的「另一进程可立即取得租约」失败 |
| 🔵-2 | `activate_host` 签名两处不一致 | 属实（§2.6、§4.5 写 `(ep_dir, root)`，§4.3 写 `(ep_dir, *, root, channel)`） | 采纳 | 统一为 `activate_host(ep_dir, *, root, channel)` |
| 🔵-3 | 判 🟢 时把 §1.1 起的裁决表移入附录 | — | 采纳（时机按约定） | 判 🟢 后执行；v0.6 不动，便于本轮对照 |

### 第四轮红队定向复审裁决与修订纪要（v0.4 → v0.5，1🟡 + 3🔵 全收，另作者自查 1 项；原裁决「🟡 修订后定向复审」）

复审结论：三轮 🟡-1 在设计上关闭（红队核实 `_run_repl_body` 的 `messages` 只在 `cli.py:1301` 声明一次、`1512` 处只作参数下传，对象同一性判断在现有代码上成立；MUT-54、MUT-55 机理成立）；三轮 🔵-1~3 已落地。新机制带出 1 条 🟡 与 3 处前后文未同步。**每条均已独立复核**（附「v0.5 补核」各行）。

| 编号 | 红队指控 | 复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🟡 | `SessionHost` 的登记没规定何时注销，也没绑定期目录；同一 pytest 进程里前一个 REPL 测试留下的登记会让后面的直接调用走子会话分支、去别人的临时期目录取租约，TK-10 的结果取决于执行顺序 | 属实（v0.4 §4.5 只写了「退出时清除」，未规定 `finally`、未绑定期目录）；测试中进入 REPL 的调用为数十处（按 grep 口径 20–34 处，红队「约 23」口径不同，结论不变） | **采纳** | §2.6、§4.3、§4.5：登记改为上下文管理器 `activate_host(ep_dir, root)`，范围正好覆盖 `run_repl`/`_run_repl_body`、`run_agent_loop` 的执行期，`finally` 里注销并释放租约；`SessionHost` 绑定自己的 `ep_dir`（resolve 后比较），包装收到的 `ep_dir` 与之不一致 → 按「没有登记」处理。新增 TK-12、MUT-56、MUT-57 |
| 自查 | （红队未提）`/chat`、`/script` 在 `_run_repl_body` 内部再调 `run_agent_loop`，是嵌套进入；若子循环退出时注销，主会话的登记会被提前撤掉 | 属实（`cli.py:1433-1444` 在 REPL 循环内调用 `run_agent_loop(ep_dir, scope_mode="creative", …)`） | **作者自查新增** | `activate_host` 可重入：已有同一 `ep_dir` 的活动登记时只计数、不替换，只有最外层负责登记与注销；`ep_dir` 不同的嵌套进入视为错误（现有代码中不存在）。TK-12 覆盖「主会话 → `/chat` → 主会话」后主会话仍落盘；MUT-58 |
| 🔵-1 | §6.1 重锚表 M26 行与 §2.6 矛盾 | 属实 | 采纳 | M26 改为「不动」，仍由现有用例守护 |
| 🔵-2 | §8 PR2 行仍写「M19/M26 重锚」，漏 M7 | 属实 | 采纳 | PR2 重锚改为 M15a、M16-1、M16-2、M20、M7；M11、M19、M26 不动 |
| 🔵-3 | 缺多回合持久化断言，将来 `messages = []` 重新赋值会让落盘静默停掉 | 属实 | 采纳 | 新增 TK-13：经 `_run_repl_body` 跑 N 个主会话回合，断言 N 个回合全部进 `session.jsonl`；MUT-59 |

### 第三轮红队定向复审裁决与修订纪要（v0.3 → v0.4，1🟡 + 4🔵 全收；原裁决「🟡 修订后定向复审，下一轮只看 🟡-1」）

复审结论：二轮 9🟡、4🔵 全部落地、无回归（红队逐条核对并推演 TL-2b/2c 期望值；新增行号全部命中）。红队自己对「第二次中断落进 `execute_job` 清理分支」的怀疑，已用真实 `execute_job` 在 0–1.0 s 共 10 个时点实验证伪（进程组均被杀、`job_finished` 均发出），降为 🔵-1 的措辞问题。**每条均已独立复核**（附「v0.4 补核」各行）。

| 编号 | 红队指控 | 复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🟡-1 | REPL 改走 `SessionHost` 会绕过 12 处 `_dispatch_agent_turn` 替身，M11、M19 的新「对应用例」杀不死变异；直接调用包装的契约未写 | 属实。替身在 `tests/test_agent_director.py:100/124/143/478/503/608/634/661/710/925`、`tests/test_agent_memory.py:1726/1779`，签名固定为 `(line, messages, ep_dir, scope, status, extra_prompt="", root=None, tracker=None)`（memory 两处为 `**kwargs`）；杀 M11、M19 的是 `test_m11_scope_hot_derivation_from_creative_to_pipeline`（`:455`）、`test_m19_chat_subloop_does_not_pollute_main_messages`（`:590`）、`test_m19_script_subloop_with_focus_prompt`（`:647`）——红队所引 456/591/648 各偏一行，结论不变；测试中直接调用包装 21 处 | **采纳方案 (a)** | §2.6、§4.5：四个调用点**保留**经模块属性调用 `cli._dispatch_agent_turn`，**参数与签名不变**（替身签名固定，不能加关键字参数）；包装内部交给 `SessionHost`。主会话的 `messages` 仍归 `_run_repl_body` 持有并原地修改。是否落盘不靠参数，靠**对象同一性**：`_run_repl_body` 启动时把自己的 `messages` 列表登记为主会话（`host.bind_main(messages)`），包装判断 `messages is host.main_messages` 才 `persist=True`。进程里没有登记 `SessionHost` 时（测试直接调用包装）：`persist=False`、不取租约、原地修改调用方的列表与 `tracker`。M11、M19 **恢复原锚点与原变异文本**（`_run_repl_body` 的 scope 行与 `/chat` 分派块都不动），仍由上述三个现有用例杀死。新增 TK-10、TK-11、MUT-54、MUT-55 |
| 🔵-1 | 「浮出」的定义前后打架 | 属实 | 采纳 | 状态表定义：「浮出」= 被内核的 `except KeyboardInterrupt` 接住；每个回合在「执行中」状态至多浮出一次，浮出即转入「停止中」。记录红队的证伪实验结论（第二次中断落在 `_kill_process_group` 的 `os.killpg` 之后，无实害） |
| 🔵-2 | §4.3 `SessionHost.lease` 注释与 §2.5 冲突 | 属实 | 采纳 | 注释改为「`--continue` 与协议进程在启动时取得；普通终端 REPL 在首个 agent 回合懒取」 |
| 🔵-3 | 裸循环是否判重、是否在 50 次处停，未写明 | 属实 | 采纳 | 写明：裸循环**不判重**（保持现有 16 处测试的语义），**有**检查点且一律「停止」（有界，随后照常收尾） |
| 🔵-4 | 篇幅：前两轮裁决表占约 55 行 | 属实 | 采纳（时机按红队建议） | 判 🟢 时把 §1.1–§1.3 移入附录；v0.4 不动，便于本轮定向复审对照 |

### 第二轮红队定向复审裁决与修订纪要（v0.2 → v0.3，9🟡 + 4🔵 全收；原裁决「🟡 修订后定向复审，v0.2 仍不可动工」）

复审结论：一轮 🔴-1、🔴-2 与自查 S-1 闭环（红队核实 `review.py` 未设 `fromfile_prefix_chars`，不存在不以 `-` 开头却能置 `approve=True` 的输入；白名单模块里以 `--f` 开头的选项除 `--force`/`--force-all` 外只有 `cloud --fg`、`vindex --frames-dir`，C-R5 不误伤）；一轮 12 条 🟡 已按要求修订。v0.2 新机制自身带出 9 条 🟡 语义缺口，无新 🔴。**每条均已独立复核**（附「v0.3 补核」各行）。另：根目录 `README.md` 的改动系红队按用户指示所为，与本 spec 无关（v0.2 汇报中的疑问就此澄清）。

| 编号 | 红队指控 | 复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🟡-A | 检查点只在「下一次模型请求之前」检查，一条回复里的并行调用不受约束 | 属实（v0.2 §2.3 第 6 条原文如此） | **采纳** | 执行计数改为**每次执行之前**检查：本段执行数已满 50 → 先弹检查点；「停止」时同一回复里剩下的调用补合成结果 `未执行：检查点停止`。新增 TL-2c、MUT-46；TL-2b 期望值随之改为「第 8 条回复内、第 51 次执行前提问」 |
| 🟡-B | 第一个中断浮出之后的状态没定义 | 属实 | **采纳** | §2.2 第 2 条改为四状态表（执行中 → 停止中 → 收尾中 → 收尾后），逐状态写「中断到达时做什么」；收尾后到达的中断在回合结束时丢弃，不带进空闲态。新增 TL-9c、TL-9d、MUT-48、MUT-49 |
| 🟡-C | 延迟区没限定只对主线程生效 | 属实：`_drain` 线程（`jobs.py:556-579`）经 `sys.stdout` 写 job 输出，即非主线程在入队；它只 `except Exception`，拦不住 KeyboardInterrupt，`finally` 会关管道 | **采纳** | §2.2 第 3 条明文：`defer()` 的深度计数与待处理中断只属于主线程，非主线程调用 `defer()` 为空操作，永不在非主线程抛出；日志流对象的行缓冲加锁。新增 TL-17（非主线程持有 `defer()` 时主线程的中断仍立即浮出）、MUT-47 |
| 🟡-D | `--continue` 与协议启动时取租约的规则丢了 | 属实（v0.1 有「启动时取锁」，v0.2 只剩懒取；而 §2.8 的截断与修复要求持锁） | **采纳** | §2.5：`--continue`（终端与协议）与**所有**协议启动都在读文件之前取租约，拿不到 → 终端退出 3、协议在 `ready` 之前退出 3；只有不带 `--continue` 的普通终端 REPL 懒取。未持锁读文件（启动提示、`--sessions`）时，末尾不完整的行视为未提交记录，只忽略、不截断。TP-9、TS-13、MUT-50 |
| 🟡-E | TK-5 挪进 `run_tool_loop` 会打破 16 处未列出的现有测试调用 | 属实：AST 统计测试里 24 处调用，16 处未传 `approve`（位置与红队所列一致） | **采纳（二选一取「不变量留在内核层」）** | `run_tool_loop` 在 `control is None` 时保持现状语义（称「裸循环」，仅测试与兼容用途）；「没有人审通道就拒绝运行」由 `AgentSession` 执行（TK-5 改测内核）；新增静态守卫 TG-6：`pipeline/` 下每个 `run_tool_loop(` 调用点都传 `control=`（MUT-51）。降级检查排在内核断言之前。16 处现有测试零改动，**无需补授权** |
| 🟡-F | `run_tool_loop` 与内核的接口不完整；`llm.py` 反向 import `session.py` 会成环；`approve` 的返回值带不出放行来源、作废与反馈 | 属实 | **采纳** | §4.1 在 `llm.py` 定义结构化 `Decision(ok, reason, provenance ∈ {human, auto, card_free, precheck, voided}, feedback)` 与 `LoopControl.review: Callable[[str, dict], Decision]`；import 方向固定为 `session → llm`（`llm.py` 不 import `session`，TG-7）。裸循环里旧 `approve` 经适配器转成 `Decision`（真值 → `auto`）。重锚表的 M3a/M3b/M15b 锚在 `control.review` 这条生产路径上 |
| 🟡-G | 重锚表仍不全：M7、M11 漏列；M19 只换锚点会因 NameError「被杀」 | 属实：M7 锚在 `_dispatch_agent_turn` 的降级分支（`verify_mutations.py:91-97`，锚点含 `print`）；M11 锚在 `_run_repl_body` 局部 `messages`（`:114-120`）；M19 的变异文本往局部 `messages` 追加（`:184-186`） | **采纳** | 重锚表补 M7、M11；M19 **改写变异文本**（让子会话共享主会话的 messages 对象），不只换锚点；表头写明：新锚点须在 PR 里给出逐字串，并跑通 `test_anchors_in_shipped_matrix_are_unique_in_repo` 与该条变异；M10 新锚点带换行（沿用 `verify_mutations.py:111` 注释的教训） |
| 🟡-H | 抓取卡的同源检查会让整个进程退出 | 属实：`require_data()` 在 data 不可达时 `raise SystemExit`（`paths.py:144`、`149`），判断用模块常量 `DATA`（`paths.py:21`）；§4.7 只捕获 `Exception` | **采纳** | 同源比较改为不抛的写法：`os.path.realpath(paths.DATA)` 与 `os.path.realpath(ctx.base / "data")` 比较，`paths.DATA` 不存在 → 不出卡、发 `notice{code:"fetch_disabled_data_unreachable"}`；抓取钩子外层另行捕获 `SystemExit` 转 notice（纵深防御）。TK-2 夹具同时改 `paths.ROOT` 与 `paths.DATA`；新增 TK-3c、MUT-52 |
| 🟡-I | MUT-43 是等价变异，杀不死 | 属实 | **采纳** | MUT-43 改为「删掉同源检查」（TK-3b 能杀） |
| 🔵-1 | 回滚时 `memory_warned` 不回退会丢告警 | 属实（告警消息随回滚被删，标志仍为真） | 采纳 | 拆成两个标志：`memory_warn_printed`（不回退，防终端重复打印）、`memory_warn_injected`（随回滚回退）；TS-11 增此结局、MUT-53 |
| 🔵-2 | TL-2b 并行调用须参数各异；TP-13 在 MUT-17 下会挂起 | 属实 | 采纳 | TL-2b 写明参数各异；TP-13 加 10 s 超时 |
| 🔵-3 | TS-8「3 段会话」与 `segment_start` 撞名 | 属实 | 采纳 | 改为「3 个会话」 |
| 🔵-4 | `confirm(...)` 在 `memory.py:1091`，不是 1093 | 属实（1093 为 `return False`；此错源自一轮红队，v0.2 照抄） | 采纳 | 全文改正 |

### 第一轮红队裁决与修订纪要（v0.1 → v0.2，2🔴 + 12🟡 + 12🔵 全收，另作者自查 1 项；原裁决「🟡 修订后定向复审，v0.1 不可动工」）

**复核方法**：每条先对照工作树独立复核，能复现的一律复现（「附：实验记录」R1–R4，「附：行号核实自查表」v0.2 补核各行）。14 条 🔴/🟡 全部属实；🔵 12 条全部属实。需人拍板的 4 件事已于 2026-09-25 由用户裁决（见头部裁决表）。

| 编号 | 红队指控 | 复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🔴-1 | `review --approve` 拦截只认全拼，argparse 前缀缩写可绕过 | 属实。R1：`run_pipeline("review --a" / "--ap" / "--appr" / "--appr=1" / "pipeline.review --ap", scope="pipeline", confirmed=False)` 全部 `ok=True`；`review.py:394` 的 `ArgumentParser` 未关 `allow_abbrev`，仓内无任何模块关闭 | **采纳（取更彻底的方案）** | §2.4.3 改为：模型发起的 `review` 除期目录外**不接受任何选项**（模型唯一合法用途是生成审片页，`--confirm-patch`、`--expect-*` 只在批准时有意义）。TK-1 补全部缩写变体；新增 MUT-35 |
| 自查 S-1 | （红队未提）同一机理绕过 ADR-0018 配音双层闸 | R1：`run_pipeline("tts run --force-a")` 通过校验（argparse 解析为 `--force-all`，`tts.py:2115`），而全拼 `--force-all` 被拒；人经 `/run` 同样可达 | **作者自查新增** | 新增修订请求 **C-R5**：`validate_pipeline_command` 的 `--force` 禁令改为「任何 `--` 开头、`split("=")[0]` 是 `--force` 或 `--force-all` 前缀且长度 ≥ 3 的 token 一律拒」，人与模型两条路同时生效（ADR-0018「ava 永不生成 `--force`」本就不区分来源）；TK-9、MUT-36 |
| 🔴-2 | `_dispatch_agent_turn` 有 4 个调用方，spec 只设计了主 REPL；同进程二次 flock 会互斥 | 属实。调用点 `cli.py:1070`（idea）、`1114`（`/chat`、`/script`）、`1243`（`/memory digest`）、`1510`（主 REPL）；R3 复现：同进程第二个 fd `LOCK_EX\|LOCK_NB` 得 Errno 35 | **采纳** | §2.5 新增进程级 `EpisodeLease`：每进程至多打开一次，首个 agent 回合（任何子会话）取得，进程生命期持有；子会话不再各自开锁。§2.6 规定四类子会话全部经内核（共享租约、中断、检查点），只有主会话落盘。金样本补子会话；新增 TT-5（`/script` 回合中 Ctrl-C）、MUT-39；`verify_mutations` 的 M19、M26 纳入 §6.1 重锚表 |
| 🟡-1 | 「先写盘再进内存」对中断不原子 | 属实（按 E6 语义推演成立：延迟区退出即抛，落在写盘与 `append` 之间） | **采纳** | §2.5 新增唯一提交函数 `commit(msg, origin)`：写盘与进内存在同一延迟区内完成。§2.8 修复算法改为**全历史**配对与相邻性校验，合成结果写成 `repair_tool_results` 记录，由重建时插到所属 assistant 之后（文件仍只追加）。假端点校验收紧为「紧随其后的连续 tool 消息恰好覆盖全部 id」。TS-10、TS-12、MUT-37 |
| 🟡-2 | 大帧写入被中断撕裂；EOF 后写帧 EPIPE | 属实。R2 复现：400 034 字节帧写到慢读端，0.3 s 发 SIGINT，读端收到 65 536 字节、末尾无 `\n` | **采纳** | §2.1 出站改为**独立写线程**从队列取帧、循环写满整帧；主线程只做入队（入队在延迟区内）。`pthread_kill` 只打主线程，写线程不受信号影响。EPIPE → 标记 host 已断、此后静默丢帧，收尾与 `turn_end` 照常写盘（§2.8 表同步）。TP-14、MUT-38 |
| 🟡-3 | 终端 Ctrl-C 与 `pthread_kill` 不是一条信号路径；crawl 中断要等工作线程 | 属实。终端 SIGINT 发往整个前台进程组；playwright driver 经 `asyncio.create_subprocess_exec` 启动、未开新会话（`playwright/_impl/_transport.py:120`），与会话同组；crawl 见 A4 | **采纳** | §2.2 按通道分写：终端下同组子进程（browser 的 driver 与 chromium、播放器）也收到 SIGINT，browser 会话随之死亡、下次调用自愈，临界区对 browser 只保护 Python 侧状态；协议下只有主线程收信号。同一回合内尚未浮出的中断**合并**（不计为第二次），只有收尾调用开始后到达的中断才放弃收尾。crawl 并入 RF-9。TT-2 改在真 pty（`pty.fork`）下跑并挂同组子进程；新增 TL-9b、MUT-45 |
| 🟡-4 | 金样本「逐字节」无定义（绝对路径、非 TTY 录制、stderr） | 属实（卡片 argv 含 `.venv` 与期目录绝对路径） | **采纳** | §2.6 定义录制夹具：进程内录制（沿用 `make_agent_root`、`patch_inputs`、`capsys`），stdout 与 stderr 分别录；归一化规则：tmp root → `<ROOT>`、`sys_python()` → `<PY>`、仓库根 → `<REPO>`、`\r\n` → `\n`。另录 2 条 pty 金样本（G-P1 工具卡、G-P2 回合中 Ctrl-C）；子会话与 idea 纳入（G10–G13） |
| 🟡-5 | `verify_mutations` 变更清单不全 | 属实。M3a、M3b、M15b 锚在 `llm.py` approve 段（`verify_mutations.py:69`、`72`、`154`），M15a、M16-1、M16-2、M20 锚在 `_default_approve` 正文（`143`、`158`、`163`、`188`），`tests/test_verify_mutations.py:114` 要求每个锚点命中且唯一 | **采纳** | §6.1 新增重锚表：逐条写新锚点、守的不变量不变、对应本 spec 的用例；经人同意后随 PR1/PR2 落地 |
| 🟡-6 | 回滚语义前后矛盾；`turn_start` 写得太晚 | 属实（v0.1 §2.3 第 3 条写「pop 用户消息」，§3.4 写「丢弃全部」；§4.7 的 `turn_start` 在注入之后） | **采纳** | 统一定义：回滚 = 恢复到回合开始时的 `messages` 与 `tracker` 快照（注入消息一并丢弃，`injected_paths`、`active_step_key` 回退；只有 `memory_warned` 这类显示锁存不回退，免得终端重复打印告警）。`turn_start` 挪到装配之前。新增断言：每种回合结局下内存历史 == 重建历史（TS-11、MUT-42） |
| 🟡-7 | 无人值守的成本上界有缺口 | 属实：① 并行调用不计数；② 判重可被微扰绕过（按裁决本就如此）；③ host 自动发 `user_message` 可绕检查点；④ `write_memory op=cite` 免卡（`memory.py:43`、`810`）且会清空判重表，v0.1 §2.3 第 6 条「有副作用的工具每次都等人」不成立 | **采纳 + 用户裁决** | 检查点按「回复或工具执行满 50 次，先到先停」（TL-2b、MUT-41）；判重表只在人批准过的副作用执行后清空（TL-4b、MUT-40）；新增 H-8「host 不得在无人操作时生成 `user_message`」；改正 §2.3 第 6 条论断；② 如实记为残余风险 RF-15 |
| 🟡-8 | 两处上位条款被作者自行重新解释 | 属实（direction §6「Spec 1 的系统提示会话内稳定纪律在恢复时仍成立」；ADR-0020 §4「事件流直接镜像 events.jsonl」） | **采纳 + 用户裁决（两处都认可）** | §6.1 新增 **DIR-R1**、**ADR20-R1**，文本修订随 PR0 落盘（先改文档再改实践）；§2.10 写明本 spec 不新立 ADR（ADR-0024、0025 已预留，如需新立从 0026 起） |
| 🟡-9 | 抓取卡路径不同源，测试有污染真实 data 的风险 | 属实：`cmd_fetch` 读 `candidates_path()` = `paths.require_data()/library/incoming/candidates.json`（`candidates.py:30-49`），子进程 `cwd=paths.ROOT`（`jobs.py:535`），与 `ctx.root` 无关 | **采纳** | 复核改读 `cmd_fetch` 实际读的同一文件；`ctx.base/"data"` 与 `paths.require_data()` 解析后不同 → 抓取卡停用并发 notice（测试夹具天然落在这一支）。抓取执行器经 `LoopControl` 注入；测试加 autouse 夹具「出现任何真实 `Popen` 即失败」。`decision` 枚举补 `interrupted`；被打断时 `acquire_propose` 结果照常为成功，只有该候选记 `interrupted`。TK-3b、MUT-43 |
| 🟡-10 | MUT-16 的证伪机理不成立 | 属实：job 子进程 `stdout=PIPE`（`jobs.py:536`），输出经 `_drain` 进 `sys.stdout`，本就会变 `log` 帧；TP-1 列出的写者无一直写 fd 1 | **采纳** | TP-1 增加两个真正直写 fd 1 的写者：测试工具内 `os.write(1, …)`、继承 stdout 的 `subprocess.run(["echo", …])`（经测试侧 import 钩子注入，生产代码零钩子）。TP-13 改测行为：job 子进程读 stdin 得 EOF，同时 host 发来的帧照常被处理 |
| 🟡-11 | `--continue` 会被新会话遮蔽 | 属实 | **采纳 + 用户裁决（不遮蔽）** | §2.5：普通启动开新会话；存在更早可恢复会话时终端打印一行提示；`--continue` 默认恢复「最近一个含 ≥1 条 assistant 消息的会话」，`--continue <会话号前缀>` 指定；新增 `ava <期> --sessions`；协议 `ready` 帧附 `other_sessions`。加载器按 `sid` 聚合记录（会话在文件中可以交错）。TS-8、TS-8b、MUT-44 |
| 🟡-12 | `memory_ack` 协议契约落不了地；S7-R1 实为反转 | 属实：`confirm()` 只拿到拼好的 `f"{diff}\n\n{text}"`（`memory.py:1091`）；「无需 ack」「不合法」直接 `return False`、不调 `confirm` | **采纳；「反转」部分采纳** | `fields` 改为单一 `text`；新增出站帧 `command_result{name, ok, text}`，`text` 为 `ack_external` 本次打印的原文（内核在调用期间截获，`memory.py` 零改动）。S7-R1 改写：Spec 7 删掉的是命令行 ack，其 §6.6（三轮 🔵-5）本就预留了「只能由人在 UI 上点击触发」的桌面端通道；本请求落地该通道，**并明确承认它不受 isatty 保护**，是对 Spec 7「非 TTY 一律拒绝」这道防线的部分反转 |
| 🔵-1 | A3 可降级 | 属实（`cli.py:938-944` 与 `991`） | 采纳 | A3 改为已有旁证 |
| 🔵-2 | A5 可关闭 | 属实 | 采纳 | A5 关闭 |
| 🔵-3 | 「EOF 默认值全是『不』」不成立 | 属实：`cli.py:644-646` 在 EOF 时取 `"y"`（`/voice` 的 apply-patch） | 采纳 | §2.1 改正；新增 RF-17 提醒 Spec 11 |
| 🔵-4 | 冻结契约内部不一致 | 属实 | 采纳 | `turn_finished` 补 `prompt_chars`；`ready` 补 `session_bytes`；§2.6 补 I-10（终端因租约被拒对话） |
| 🔵-5 | 检查点记账会发 `approval_resolved`；作废与拒绝混记 | 属实（`status_card.py:368` 起，`emit_event` 默认发事件；桌面端 `App.tsx:395` 对无 `approval_id` 的事件一律标「命令卡拒执」） | 采纳 | 检查点记账 `emit_event=False`；作废不进 `approvals.jsonl`（它不是人的决定），只进 `session.jsonl`；`App.tsx:395` 的误标记为 Spec 8 遗留，交 Spec 10（H-10） |
| 🔵-6 | 部分写与写失败路径未定义 | 属实 | 采纳 | §2.5：`os.write` 循环写满；写失败 → 会话记录标记为「已损坏」，本轮停止且不再写盘，此后回合一律拒绝并提示开新会话 |
| 🔵-7 | `history` 帧把注入消息标成 `user` | 属实 | 采纳 | `history.role` 按 `origin` 映射（§3.1） |
| 🔵-8 | TG-5 恒真；MUT-5 称 TP-8 变红不成立 | 属实 | 采纳 | 临界区改为显式字面量 `CRITICAL_TOOLS`，TG-5 比对它与 side_effect 集合；MUT-5 只列 TL-7 |
| 🔵-9 | `displayed_ids` 不得从会话恢复 | 属实（现状每进程从空开始，`cli.py:1299`） | 采纳 | TT-6 钉住：恢复后 `/approve 05` 不带 `--id` 报「尚未在本会话展示」 |
| 🔵-10 | 打桩会直接抛 `LLMError`、`PermissionError` | 属实（`tests/test_agent_director.py:729-730`、`751-752`） | 采纳 | §4.3：内核在 `run_tool_loop` 外层仍捕获这两类异常，按回滚处理，与现状 `cli.py:994-1002` 相同 |
| 🔵-11 | fd 隔离应先于 `import pipeline.*`；host 只解析 stdout | 属实 | 采纳 | §2.1；新增 H-9 |
| 🔵-12 | 篇幅可压缩 | 属实 | 采纳 | §2.7、§2.9 压缩；全文重排 |

**给下一轮的说明**：v0.2 新增的机制（写线程、提交函数、进程级租约、子会话接入、双计数检查点）都是对上表条目的直接回应；自查 S-1 扩大了本 spec 对 `validate_pipeline_command` 的改动面（C-R5），请一并审。
