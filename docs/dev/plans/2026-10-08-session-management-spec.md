# 桌面端会话管理：列表 / 进入 / 新建 / 删除（D45）

日期：2026-10-08　状态：**已施工，机检与 e2e 通过，待真实一期手验**
关联：D45；ADR-0024 §3 I2（2026-10-08 增补）；Spec 9 §2.5（`--continue <前缀>`）；Spec 10 H-1/H-5/H-8
附件：[`2026-10-08-session-management-options/`](2026-10-08-session-management-options/)（三个原型截图脚本 `options.spec.ts`、施工步骤截图 `steps.spec.ts`；截图落 `desktop/out/d45-shots/`，不进 git）

## 起因

一期实际上只能用一个对话：没有活会话时发消息其实会开新会话，但界面上只有「继续上次会话」一个按钮。2026-10-08 被污染的那段对话（董香二期 `cfaee0…`，9 月 26 日开的）就是这样被接上的。人要的是：看得到本期有哪些会话、能选一个进去、能删除、能开新会话。

## 人的裁决（2026-10-08）

1. 删除语义 = **移到回收站**（`_agent/session-trash/<sid>.jsonl`，可手工找回），不是隐藏、也不是彻底删除。
2. 界面选 **方案 B：侧栏当前期下面展开会话子列表**（A 会话头下拉、C 对话区标签条未选；原型截图见附件）。
3. 会话头的「继续上次会话」按钮**去掉**（侧栏点一下就是继续）；「结束会话」保留。
4. 空会话（0 条消息）**灰色显示、可删**。

## 施工内容

| 层 | 落点 | 内容 |
|---|---|---|
| core | `session_log.SessionSummary.first_user`；`move_session_to_trash` | 列表标题取首条 user 消息（截 60 字、折叠空白）；删除持租约，先截残行，回收站文件落盘成功后再原子替换日志，坏行无法归属一律保留，调用后租约关闭 |
| core | `cli._dispatch_session_admin` | 裸形态 `/list-sessions`（JSON，只读）与 `/delete-session --sid=<16 位 hex>`（退出码 0 成 / 1 不存在 / 2 用法错 / 3 该期有活会话） |
| core | `protocol.FrameWriter.sid_source` | **修了一个原有缺陷**：输出帧的 `sid` 只在启动时取一次，新会话要到首回合才生成会话号，所以此后每一帧都是 null。改为每帧实时读 `host.sid` |
| host | `spawner` `LIST_SESSIONS` / `DELETE_SESSION`；`SESSION_CONTINUE` 带 `sid` | 会话号在 TS 侧也按 `^[0-9a-f]{16}$` 校验，不合格抛错不 spawn |
| host | `sessions` `sid`/`liveSid`/`isBusy`/`endAndWait`/`reset` | 从帧里记下活会话号；复用迁移时的「结束并等退出」「清空已退出状态」两步 |
| host | `service` `conv.sessions`/`conv.enter`/`conv.fresh`/`conv.delete` | 有回合在跑一律 `E_BUSY`，不弹框；删除先原生确认（取消一字不动），活会话占着租约就先结束；删的是别的会话，删完把原会话接回来 |
| 界面 | `renderer/SessionList.tsx`、`App.tsx` 期列表、`style.css` `.ep-session*` | 当前期下：「＋ 新会话」（没有活会话时它是当前）、按最近活动倒序的会话行（悬停看全文、消息数、时间）、悬停或聚焦出「删除」 |

## 一并修的 e2e（D44 的连带影响）

D44 让 `check_script` / `status` 免卡后，`e2e/sessionReal.spec.ts` 里 9 处拿 `check_script` 当「会弹卡的工具」的用例全部失效（卡不再出现）。改用 `qc`：仍逐次弹卡，在夹具上执行必判不合格（`result.ok=false`），只写临时夹具里的 `06-check.log`。TX-14 需要同一轮两张不同的卡，第二张用 `review`。另外 5 处 `session-resume` 改为点侧栏会话行或看 `session-head[data-phase]`。

## 验证（已跑）

- pytest 全量通过；`tests/test_session_admin.py` 13 条（其它会话逐字节保留、坏行保留、残行先截、活会话时退出 3、各类非法参数一字不写、删除后租约已释放）；`test_agent_protocol::test_d45_*`（`--continue <完整 sid>` 回到非最近会话，新会话的帧带上 sid）。
- vitest 全量通过；`sessions.test.ts` D45 七条、`spawner.test.ts` D45 八条。
- e2e：`sessionReal` 新增「D45 真实 core」一条（新会话 → 两个会话；删除取消一字不动；确认后进回收站、另一个会话被接回）；TX-10/TX-15 改走侧栏后通过（真实历史回放）。
- 变异检验 6/6 被抓：删除时连坏行一起搬走；帧 sid 不实时；spawner 不校验 sid；跳过确认框；进入前不结束旧会话；删别的会话后不接回。
- `scripts/verify_mutations.py` 的 S9-MUT-38 锚点随 `FrameWriter.send` 改写同步，手工施加后仍被 `test_agent_protocol.py` 抓到。

## 待人验（真实窗口、真实一期）

- [ ] 董香二期侧栏看到 3 个旧会话（含一个灰色空会话）
- [ ] 点 9 月 26 日那个，能看到它的历史
- [ ] 「＋ 新会话」后对话区清空，发消息后上下文是干净的
- [ ] 删除 `cfaee0…`：弹确认框，删完 `_agent/session-trash/` 里有它
