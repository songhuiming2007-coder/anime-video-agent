# D157：一期多会话后台并存 施工 spec

日期：2026-11-03　状态：**待人终审后施工**
关联：D157；D45（会话管理）；D79（steering）；D81（企划会话）；ADR-0030
人拍板（2026-11-03）：方向 **B（真后台跑）**，参考 Pi / Codex「会话即并发单元」。

## 一、用户场景与目标

人在 1280×800 / 1440×900 窗口用 AVA。某期的一个会话正在跑长回合（工具调用中或有待答卡），人想：

1. 点开同期另一段旧会话，只看历史或排队下一条消息 —— 正在跑的进程**不死**。
2. 点「＋ 新会话」起全新一段 —— 正在跑的进程**不死**。
3. 在侧栏一眼看到哪段在跑、哪段有待答卡，能随时切回去答卡。
4. 删除某段会话：活段先停掉（确认框里写明）；删别的段不影响当前正在看的。

目标：会话成为独立的并发单元（对齐 Pi / Codex 的多会话模型），期只是它的命名空间与日志位置。

## 二、现状（已读码核对）

- core：`protocol.main` 启动即 `EpisodeLease.acquire(ep_dir)`，对 `<期>/session.jsonl` 持 `flock` 排他锁，进程不死锁不放 → **同期起不了第二个协议进程**。日志一份文件混存所有 sid 的行，行内自带 `sid`/`seq`。`begin(sid)` 记当前段；`append` 用 `lease.sid` 补 sid。
- `move_session_to_trash(lease, sid)`：持期租约重写整份日志搬出 sid 行；**调用后租约作废关闭**。
- host：`SessionManager.states: Map<ConvKey, SessionState>`，`ConvKey` 到期（`ep:<ep>`）不到 sid —— 一期一个 `SessionState`；`refuseIfBusy` 拦 `convEnter/convFresh/convDelete/convPurgeTrash`。
- 渲染：`convs` 桶按 `convKey` 索引（`convStore.ts`）；`conv.snapshot`/`conv.delta` 推送也只带 `convKey`。

## 三、设计裁决（施工不得偏离）

1. **protocol 协议扩展**：`ConvKey` 的线协议与帧一律**不改**。多会话用 **RPC 参数 `sid` + 推送 `sid` 字段**区分，不动 `ConvKey` 类型（`ep:<ep>`/`idea`/`proj:<p>` 仍是「逻辑会话桶」键）。host 内部以 `(convKey, sid)` 区分 `SessionState`；`conv.delta`/`conv.snapshot` 推送增加可选 `sid` 字段，渲染层按 `(convKey, sid)` 建桶。
   - 理由：`ConvKey` 从字符串改成复合键会碰 `protocol.ts` 全部 method 签名与 e2e 夹具；加 `sid` 字段是向后兼容的扩展。
2. **core 锁改按 sid 的「存在锁」**：`EpisodeLease` 语义不变（对 `session.jsonl` 的 flock），但 acquire 从「排他整期」改为：
   - `SESSION_NEW` / `--fresh`：**不取整期排他锁**；写日志时取一把**追加写锁**（对同一 `session.jsonl` 的 `flock LOCK_EX`，只在 `append` 调用期间持有，写完即放）。多进程并发 append 各自 `O_APPEND` + 短锁，行不撕裂（`os.write` 单行、fsync 照旧）。
   - `--continue <sid>`：**不取**排他锁（同 A 路径），恢复目标 sid 后同样短锁写。
   - 删会话 `/delete-session --sid=`：仍先 `truncate_torn_tail` + 原子重写整份日志 → 这一步要拿到「**该 sid 没有活进程**」的保证。新判据：core 侧对每个 sid 建 presence 文件 `<期>/_agent/live/<sid>.pid`（协议进程启动写 pid、退出删；`session_start`/`segment_start` 记录 pid）。delete 时若对应 pid 存活 → 退出码 3（E_SESSION_LOCKED），文案「该会话仍在运行」。
   - **风险**：短锁 append 下，两个进程写同一文件，`seq` 由各自 `lease` 维护会在同一 sid 内**重复编号**。已读码确认事实：`load_session`/`resume_target`/`list_sessions` 只按 sid 过滤 + `session_start`/`segment_start` 分段 + 文件序，不依赖跨进程 seq 唯一；**但 `_rebuild`（session_log.py:448 起）用 `seq → 下标` 与 `after_seq` 恢复回滚/修复位置，同 sid 内 seq 撞号会串位**。解法（写入 spec 裁决）：seq 语义收窄为「同 sid 段内单调」——新 sid 进程从 0 起、`--continue` 进程从该 sid 的 `last_seq` 续；`repair`/`rollback` 记录的 `after_seq` 只在同 sid 内解释（本来就是按 sid 过滤后建的 positions，撞号只出现在「同一 sid 被两个进程同时写」这种本就不允许的场景，presence 锁会挡住）。**施工前跑 `test_session_log.py` 全集确认**。
3. **host 状态模型**：`states` 键从 `ConvKey` 改为 `string`（实现上用 `${convKey}#${sid ?? "pending"}` 复合键，或嵌套 map；施工时选其一，对外暴露 `liveSids(key): Map<sid,…>`）。
   - `send/answer/command/interrupt/end/snapshot` 的 RPC 增加**可选 `sid`**；不带 `sid` 时指向 `focused[key]`（渲染层正在看的那段）。host 为每个 convKey 维护 `focused: Map<ConvKey, sid>`，`conv.enter`/发送消息时更新。
   - `conv.enter`：目标 sid 已有活进程 → 只切 `focused`、返回其 snapshot（**不 endLive、不新起进程**）；无活进程 → `ensure` 起 `--continue <sid>` 并把 `focused` 指向它。`isBusy` 不再拦 enter/fresh。
   - `conv.fresh`：不 `endLive`；`focused` 指向一个新「pending」占位（未发消息、sid 未知）；下一条 `send` 用 `SESSION_NEW` 起新进程。活进程留着。
   - `conv.delete`：`sid == liveSid(key)`（正在看的那段是活的）→ 确认框写「该会话仍在运行，将先停止」→ `endAndWait` → core 删；删别的段 → 不 endLive，直接 core（core 若见 sid 活锁 → 3，如实报错）。删完若删的是 focused，focused 落到剩余最近段或 pending。
   - `conv.end`：结的是 focused sid；后台非 focused 活进程不受影响。
   - `quitState`/`stopAllForQuit`：按全部 `states` 逐 state 判忙（与 sid 无关，逻辑不变）。
   - `summaries()`/`episodesList`：每 convKey 聚合 —— `live` = 任一活、`running` = 任一 running、`openRequests` = 各段求和。
   - 帧路由：`onFrame` 读到的 `f.sid` 决定该 state 的真实 sid（`session_start`/`segment_start` 帧定下 sid）；pending 占位在拿到 sid 后重键。
4. **渲染层**：
   - `convStore` 桶键从 `convKey` 改为 `convKey + "|" + (sid ?? "pending")`；`ConversationPane` 读 `convs[convKey|focusedSid]`。`focused` 由 host 推送（snapshot 带 `focused_sid`）而非渲染层自猜。
   - `SessionList`：`off` 只剩 `busy`（本地在途），不再吃 `running`；运行中的行保留 `conv-shimmer`，加「运行中」徽标与「待答」徽标（现有 openCards 口径从「focused 段」改为「该行 sid 段」）。
   - 期行徽标沿用聚合口径（任一 running → 运行中；openRequests 求和）。
   - 「＋ 新会话」在回合在跑时也可点。
   - autoOpen/停机点自动呼出：`applyConvEvents` 只作用于 focused 段（`key === convKeyRef.current` 且 `sid === focusedSid`），后台段的 `turn_started` 不呼出预览；后台段的卡与回合结束靠 D98 通知/Dock 角标提示（本期只接「徽标 + 期行运行中」，系统通知复用 D98 机制，新增「后台回合结束」事件另议）。
5. **明确不做**：终端 `ava` CLI 行为不变（它本来就是单进程单会话）；idea 会话同样适用（`acquire_idea_lease` 同路径）；跨期不受影响（本来就按期分锁）。

## 四、安全边界

- `session.jsonl` 并发 append：`O_APPEND` + 写前短 `flock`；每行一次 `os.write` 循环写满后 fsync。截尾（`truncate_torn_tail`）只在 delete 时且该 sid 无活进程、持短锁进行。
- `move_session_to_trash`：sid 有活 presence 文件 → 拒（码 3）。**不再关租约**（租约已非排他）。
- 回收站/purge 锁 `_trash_lock` 不变。
- 密钥、D98 通知、ADR-0030 免卡纪律不变。

## 五、测试要求

- pytest（`tests/test_session_log.py`、`test_agent_protocol.py`、`test_session_admin.py`）：同期两进程并行 append 不撕裂；A sid 活时删 B 行成功、删 A 拒 3；恢复指定 sid 正常；`list_sessions` 多 sid 汇总正确；presence 文件清理（崩溃残留 pid 不挡删除——pid 复用校验：`os.kill(pid,0)` + 校验是 python 协议进程）。
- vitest（`desktop/tests/host/sessions.test.ts`、`service.test.ts`）：focused 切换、enter 活段不 endLive、fresh 不杀后台、summaries 聚合、pending→sid 重键。
- e2e（`session.spec.ts` + `sessionReal.spec.ts` 各一条）：A 段跑长回合时点 B 段，A 的行显示运行中、B 能进；A 回合结束帧到后台后，切回 A 看到完整回合；删 A 段弹确认含「仍在运行」。

## 六、完成判定

- [ ] 同期两段并行：一段跑长回合，另一段正常收发、答卡
- [ ] 侧栏徽标分别显示两段状态；切回不丢条目
- [ ] 删除活段：确认框提示「仍在运行，将先停止」→ core 不再报 3；删非活段照常
- [ ] 退出 app：两段都被正确收尾（quitState 列出两段）
- [ ] 机检：pytest / vitest / e2e 指定段全绿；变异点（锁粒度、focused 指向、presence pid 判活）逐个被杀

## 七、风险与开放问题（待人确认）

1. **seq 语义收窄为「同 sid 段内单调」**（§三.2 裁决）：`_rebuild` 的 `after_seq` 只在同 sid 内解释；跨进程撞号靠 presence 锁挡住「同 sid 双写」。若 `test_session_log.py` 证伪此假设，回退 = 进程内 seq 带段前缀（改动更大，备选）。
2. **通知口径**：后台段 `turn_finished`/新卡出现是否弹系统通知（D98 机制的复用范围）——默认**不弹**，只徽标计数；要弹另议。
3. **`conv.sessions` 的 `live` 标记**：从「== focused 活 sid」改为「该 sid 有活进程」，`SessionRow.live` 语义相应改；e2e 旧断言要复查。
4. **迁移/带入（`carried` / `ideaFresh`）**：`convFresh` 不再 endLive 后，「＋ 新会话」时点（未发消息）与「建期带入」点（`endForMigration`）的先后序要写进用例。
