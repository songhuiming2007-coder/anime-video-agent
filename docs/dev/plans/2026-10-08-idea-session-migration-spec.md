# Spec 18：建期迁会话——选题会话（idea）落盘与转正继承（D42）

> **状态：v0.1 草案，待人审 → 红队（D42-R）**。2026-10-08 立文（D42-A 第二步）。
> 对应 issues：**D42**。前置：选型稿 `2026-10-06-idea-to-episode-options.md`（人 2026-10-06 裁决：方案 (a)、联网三件含 `crawl`、同一时刻只留一个选题会话）；**D43 / Spec 17 已施工（2026-10-08）**——方案 (a) 的「idea 补联网三件」已被覆盖且更宽（单表全开），本 spec 只剩「建期把选题会话记录带进新期」一半。
> 触及冻结面：Spec 9（`archive/2026-09-25-agent-session-protocol-spec.md`，`--idea` 不落盘 → 落盘 + 恢复）、Spec 10 §2.5（建期流程与 `idea-note`）、ADR-0018（idea 条款）、Spec 10 §2.5 第 5 条（idea 会话保留后台 → 建期即结束）。

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

### 3.2 建期迁移：`ava new <名> --from-idea`

core 侧 `pipeline.agent.cli` 的 `new` 子命令加 `--from-idea` 旗标（不带时行为一字不变）：

1. 先照旧 `create_new_episode`（失败即原样返回，零新增行为）。
2. 建期成功后迁移（**顺序写死：先建期、后迁移**，保证任何失败都留下一个合法空期）：
   a. 取 `data/_idea` 租约（独占，含读与清空）——**拿不到（另一选题会话进行中）→ 报错退出非零**：新期保留为干净空期，`data/_idea/session.jsonl` 一字不动，错误文案明示「选题会话进行中，请先退出它再建期带入」；
   b. 读 `data/_idea/session.jsonl` 全部字节；**无可迁移记录**（文件不存在 / 无任何含 assistant 消息的会话段）→ 不算失败：stdout 标注 `migrated: false`，新期为空期，正常返回 0；
   c. 整段**追加写入**新期的 `session.jsonl`（新期该文件尚不存在 → 等同复制；sid、seq、ts 一字不改，append-only 语义不破）；写完后 stdout 标注 `migrated: true` 与迁移的会话段 sid；
   d. 迁移成功后**清空** `data/_idea/session.jsonl`（截断为空文件，保留文件与锁语义；「建期即转正并结束」的落盘点）。
3. **原子性约定**（选型稿既定）：b 的读取失败或 c 的写入失败 → 新期保持干净空期（**不回滚建期**）、idea 记录原样保留、报错退出非零并在 stderr 写明「建期成功、迁移失败」。c 的写入走「先写临时文件再 `os.replace`」级别的原子落盘（复用 `paths.atomic_write` 对临时副本操作后改名到位，或直接整段一次性 `os.write` 后 fsync——实现二选一，验收看效果：任何时刻 `session.jsonl` 要么是空/不存在、要么是完整副本，不许出现半份）。
4. 迁出的段在新期里就是一段普通会话记录：`--continue`（无前缀）即可恢复它——`resume_target` 既有规则「最新且含 ≥1 条 assistant 消息」天然命中。**常驻层按新期当前 scope（creative）现装、01 工序规程注入**，都是 `--continue` 的既有机制，零新代码路径。
5. 历史回放安全：idea 段里的工具调用记录（`read_status` / `web_search` / 被拒的 `write_episode_file`（「先建期」回执）等）作为消息原样回放；D43 后工具表全模式全量，不存在「当前 scope 工具表里没有的工具」的悬空调用。`rebuild_messages` 对回滚回合的丢弃逻辑对 idea 段同样成立（idea 会话也可能有回滚回合）。

### 3.3 桌面端

- `episode.create`：host 的 `NEW_EPISODE` 模板 argv 恒加 `--from-idea`（无记录时 core 返回 `migrated: false`，语义等价于现状，零分叉）；core 的 stdout 末行解析出 `migrated` 与 `sid`，经 `episode.create` 的返回值带给 renderer。
- `migrated: true` 时：① renderer 的 `idea-note` 文案改为「已带入选题会话记录（N 条消息）」（N 取自 core 返回；**migrated: false 时旧文案不变**——idea 没东西可带，提示照旧成立）；② 该期的**首个**会话用 `SESSION_CONTINUE` 起（迁入段是新期里唯一会话段，`--continue` 无前缀必中）；③ 若 idea 会话进程活着（`SESSION_IDEA` 在跑），host 先结束它再建期——否则 core 拿不到 `_idea` 租约必报「进行中」。**结束顺序**：host 结束 idea 进程 → `NEW_EPISODE --from-idea` → 切期 → `SESSION_CONTINUE`。
- 「再点『选题』开新的」：`SESSION_IDEA` 照旧懒启动；`data/_idea/session.jsonl` 已是空文件 → 全新会话。app 重启后点「选题」→ core 恢复最近的选题段（3.1 的恒恢复）。
- `sessions.ts` 的「idea 会话不支持『继续上次会话』」报错（`convTarget`）随落盘删除：idea 会话的「继续」= 恒恢复最近段，由 core 在 `--idea` 启动时完成，不需要独立的 resume 指令。

### 3.4 终端

- `ava idea`：照旧进选题会话，但现在落盘并恒恢复最近段（退出后再进，讨论还在）。
- `ava new <名> --from-idea`：同 3.2；随后 `ava <名>`（或建期后直接进对话的既有行为）即带着选题记录继续。
- 若终端的选题会话正在另一个进程里开着：`--from-idea` 报「选题会话进行中」（3.2 的 a），不抢锁。

### 3.5 文案同步

- `idea.md` 的「产出与落盘」一节按选型稿既定改为「建期后在同一会话里写」（D43 施工时的过渡文案「需要写 `01-topic.md` 时先请人建期」随之更新）；`cli.py` idea 横幅与 `_print_idea_non_tty_help` 同步（「退出即丢」类措辞删除，改为「会话记录保存在库级，建期时可带入」）。
- `build_idea_card` 的「产出落盘」行改为「定稿后点『＋ 新建一期』，选题讨论自动带入新期」。

## 4. 冻结面影响与文档修订清单（施工同一 PR 内完成，archive 原文加修订注记：日期、D42、人裁决原话）

- **Spec 9**（`archive/2026-09-25-agent-session-protocol-spec.md`）：§2「idea 会话不落盘：无期目录可挂」一条改为「落盘到 `data/_idea/session.jsonl`、恒恢复最近段」（该条已有 D43 注记，本 spec 再改「不落盘」半句）；「只有主会话落盘 / 子会话退出即丢」（§2.5 附近）加 idea 例外的注记；入口行（§2 「`--continue` 或 `--idea`」）注记 `--idea` 启动即恒恢复、无需显式 `--continue`；TP-16 行加注（「不写 session.jsonl」已改，用例改写）。MUT-62 锚点与断言复核。
- **Spec 10 §2.5**：第 1 条（`SESSION_IDEA` 语义：无期目录、**落盘到库级、可恢复**）；第 4 条（`idea-note` 两态文案 + 迁移成功后的 `SESSION_CONTINUE`）；第 5 条（「idea 会话保留在后台，可切回继续聊或再建一期」→「建期即转正并结束；同一时刻只保留一个选题会话」人 2026-10-06 裁决原话）。
- **ADR-0018**：idea 条款中「不落盘」修订为落盘（库级），「零写权限」按 ADR-0027 已废的口径引用。
- **impl spec**（`2026-09-18-ava-agent-impl-spec.md`）文首 ① 与 §2.3 的 `ava idea` 行：补「落盘到库级、建期可带入」注记。
- **README**「已知限制」：D42 行随施工删除（评审通过后）。

## 5. 风险与攻击面（给红队）

| # | 风险 | 本 spec 的判断 | 请红队核 |
|---|---|---|---|
| R1 | 迁移把 idea 段塞进新期后，`rebuild_messages` 回放破坏协议不变量（工具调用与 tool 结果配对、回滚回合、修复记录） | 整段字节级复制，sid/seq 不动；`load_session` 的坏行归属与 `rebuild_messages` 对跨段文件本就成立（一个文件多段会话是既有形态） | 构造含回滚回合与 repair_tool_results 的 idea 段，迁移后 `--continue` 重建，逐条核对消息序 |
| R2 | 迁移竞态：选题会话进行中另开终端 `--from-idea` | `_idea` 租约独占，拿不到即报错退出非零、新期留空期、idea 记录不动 | 核租约的持有者存活判定（Stale 锁、进程死而未释） |
| R3 | 「清空 `_idea/session.jsonl`」是删除类动作 | 只截断为**空文件**，不删目录不删文件；且发生在整段复制**成功之后**；这是人裁决 Q3「建期即转正并结束」的直接落地，在 spec 里显式声明 | 判截断时机与失败面（复制成功但截断失败 → 会重复带入——是否接受） |
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
- T-D42-7（单一选题会话）：第二个 `--idea` 进程在第一个存活时启动 → 报「另一个选题会话进行中」类错误，不开第二份。
- 桌面端 e2e（临时副本）：idea 聊一轮 → 建期 → 新期首会话带历史（assistant 原文可见）、`idea-note` 显示「已带入」、再点「选题」是全新空会话；`migrated: false` 路径（idea 没聊过就建期）→ 旧文案照旧。
- TP-16 改写：「不写 session.jsonl」→「写 `data/_idea/session.jsonl`、期目录零产出」。

**变异**（指定杀手；登记进 `scripts/verify_mutations.py` shipped 矩阵）：
- MUT-D42-a：迁移只建期不复制记录 → T-D42-2 杀。
- MUT-D42-b：复制后不清空 `_idea`（同一逻辑会话存在于两处，Q3 被破）→ T-D42-2 的「`_idea` 为空文件」断言杀。
- MUT-D42-c：复制失败时回滚建期（删新期，违反原子性约定）→ T-D42-3 杀。
- MUT-D42-d：`_idea` 进期列表 → T-D42-5 杀。
- MUT-D42-e：`--idea` 不恢复历史（恒开新段）→ T-D42-1 杀。

## 7. PR 划分、验证与门禁

- PR1（core）：`session_log`/`session.py` 的日志目录解耦、`--idea` 落盘与恒恢复、`new --from-idea`、cli/protocol 接线、测试与变异、Spec 9 / impl spec / ADR-0018 修订注记。
- PR2（desktop）：`NEW_EPISODE` 模板加 `--from-idea`、`episode.create` 返回 `migrated`/`sid`、idea 进程先结束再建期、首会话 `SESSION_CONTINUE`、`idea-note` 两态、`convTarget` 的 idea resume 报错删除、e2e。
- 验证：全量 `uv run pytest` 全绿；`cd desktop && npx vitest run`、`npx tsc --noEmit`、全量 `npx playwright test`（workers 2，临时副本）全绿；变异逐条回填。
- 门禁：① 新增与改写用例全绿、改写逐条可追溯；② MUT-D42-a~e 全杀并登记 shipped 矩阵；③ 真会话冒烟（临时仓库副本、真 LLM）：idea 里聊一轮选题 → 退出 → 再进（历史在）→ `ava new --from-idea` → 新期会话里直接「把刚才的草案写进 01-topic.md」→ 弹卡批准落盘；④ §4 文档修订面完整；⑤ 桌面端门禁走 Spec 10 门禁 12 的更新版（「idea 聊一轮 → 建期 → 新期不重述直接写」），打包版真机手验并入 ACC。

## 8. 与既有 spec/ADR 的关系速查

- D43 / ADR-0027 已废「按 scope 分工具」；本 spec 不动工具表。
- 本 spec 不改 `--continue` 的选择规则、不改 `session.jsonl` 格式、不改 H-8（host 不代发消息）、不改人审卡链。
- 「期名由人拍板」「建目录由人点按钮」不动；模型自始至终没有建期工具（选型稿已否决 `propose_new_episode` 子变体）。
