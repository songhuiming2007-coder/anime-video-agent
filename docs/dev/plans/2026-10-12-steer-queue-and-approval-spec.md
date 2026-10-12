# D78 审批减负 + D79 运行中排队发消息 施工 spec

> 对应 issue：**D78**、**D79**（`docs/dev/issues/README.md`）。决策见 [ADR-0030](../adr/0030-approval-danger-only.md)。
> 人的原话（2026-10-12）：「几乎 ava 每一次工具调用都需要人来用手点击『批准』」「ava 的发送消息很原始，必须等模型做完一轮才可以发送消息，我建议学习 Pi agent 的消息发送」「点击『批准』也不太方便，如果点击回车就可以『批准』就好了」；看过第一版边界表后：「建议再减少一些弹框」。
> **本 spec 含 UI，plans/README 的 UI 五条规则生效**：排队气泡与卡片回车属于交互改动，验收第 1 条是真实一期、人勾（§五）。

## 施工状态（2026-10-12）

- **D79 已施工、机检通过**：§一 ①–⑨ 全做。pytest 新增 `tests/test_agent_steer.py` 16 条（变异 9/9 被抓），TP-6 / TP-13 按新语义改写（真子进程端到端：排队 → 工具批后送达 → 请求体里排在 tool 结果之后、内部标记已剥）；vitest 523 条全绿（新增折叠 3 条，变异 3/3 被抓）。待真实一期手验（§五）。
- **D78 已施工、机检通过**（2026-10-12；人在安全分类器拦截后裁决「继续」，切手动模式逐条放行完成）：§二 ⑩–⑯ 全做。另：会话卡上的原生确认框整体撤掉（`shared/nativeConfirm.ts` 删除）；回车批准加 400 ms 防连按；记忆日志不出卡记 `auto`；`scripts/verify_mutations.py` 的 M16-1/M16-2 重新钉锚、S9-MUT-15 换成 D78-1/2/3。约 60 条旧断言按新边界改写（测卡片机制的用例改用仍弹卡的 `bash rm`；抓取卡两条 D35 用例与 e2e TX-12b/13 随功能删除）；金样本 G3/G4/G6/G-P1 重录。D78 变异 13/13 被抓。**待办**：G-P1 是在硬盘不在时录的（剥掉了一行「跨期记忆未注入」警告），插盘后跑一次 `tests/test_golden_terminal.py` 确认。
- 施工中对 §二 的修正已写回 ADR-0030：写 `config/` 的三条 run_pipeline 命令、`--confirm-patch` / `--waive` 照旧弹卡（决策 2）；回车批准防连按（决策 6）。

---

## 一、D79 排队发消息（照 Pi 的 steering）

Pi 源码对照（`@earendil-works/pi-agent-core/dist/agent-loop.js::runLoop`）：一批工具结果全部进上下文 → `getSteeringMessages()` 取队列 → 作为 user 消息追加 → 再发下一次请求；模型给出最终答复（无工具调用）时队列非空则继续一轮；中断时排队消息退回输入框。

| # | 文件 | 改动 |
|---|---|---|
| ① | `pipeline/agent/protocol.py` | `FrameReader._dispatch`：`user_message` 遇回合在途不再回 `E_BUSY`，进 `slots["steer"]`（锁保护），回 `{"t":"queued","id","text"}`（带 rid）。`command` 帧照旧 `E_BUSY`。`_run_turn` 收尾：锁内取走剩余排队并清 `in_flight`；回合正常结束（`done`）时把剩余排队合成下一回合的用户消息立即开跑，其余结束方式（中断 / 出错 / 检查点停）发 `{"t":"steer_returned","items":[{id,text}]}` 退回输入框 |
| ② | `pipeline/agent/llm.py` | `LoopControl.drain_steer`；`run_tool_loop` 在每批工具结果全部 commit 之后、下一次请求之前 drain，逐条 commit `{"role":"user","content":text,"_ava_steer":True}`（origin `user_steer`）并经 `on_steer` 回调通知已送达；模型无工具调用、准备收工前再 drain 一次，非空则继续循环。收尾调用期间不 drain。`_ava_steer` 进 `_INTERNAL_KEYS`（进请求体前删除） |
| ③ | `pipeline/agent/aging.py` | 老化边界扫描跳过 `_ava_steer` 消息（同收尾标记）：否则回合中途插一条用户消息，会把本回合前半段的工具结果当旧回合换成占位串 |
| ④ | `pipeline/agent/session.py` | `_control()` 接上 drain 与送达通知（送达发 `{"t":"steer_delivered","turn_id","ids"}`）；终端通道没有排队，`drain_steer` 为 None |
| ⑤ | `pipeline/agent/protocol.py::_role_of` | `user_steer` → `user`（继续会话时历史照常显示为用户消息） |
| ⑥ | `desktop/src/shared/convFrames.ts` | 出站闭集加 `queued` / `steer_delivered` / `steer_returned` 及必需键 |
| ⑦ | `desktop/src/host/sessions.ts` | `send()`：会话 `running` 时照写 `user_message`，等 `queued` 帧（按 rid）解析；`starting` / `ending` / `exited` 照旧拒 |
| ⑧ | `desktop/src/shared/convFold.ts` | `FoldedConv.queued`（未送达的排队）；`steer_delivered` 在时间线该位置插用户行；`steer_returned` / `turn_finished` / 退出后清掉；`returned`（最近一次退回的文本与序号）供输入框回填 |
| ⑨ | `desktop/src/renderer/Composer.tsx` | 运行中也能 Enter / 点发送；输入框上方显示「排队中」气泡；退回时回填输入框（已有内容换行拼接） |

**卡片挂着时发的消息**照样排队，卡答完、这批工具跑完再送达（不把打字当拒绝）。

## 二、D78 审批边界（ADR-0030 决策 1–5）

| # | 文件 | 改动 |
|---|---|---|
| ⑩ | `pipeline/agent/status_card.py` | `_BASH_DANGER_RULES` 重写为硬危险表；Code Freeze 只认「写入」（重定向、`tee`、`sed -i`、`cp`/`mv`/`ln`/`touch` 等写法与 `pipeline/` `config/` 同时出现；`2>&1`、`>/dev/null` 先剥掉） |
| ⑪ | `pipeline/agent/session.py::review_tool_call` | 弹卡前拒因原样保留；之后：bash 命中硬危险 → 卡（带 `allow_key`）；`run_pipeline cloud` 花钱 / 销毁子命令 → 卡；草稿超限 → 卡；`AUTO_APPROVED_TOOLS` 与其余白名单内 `run_pipeline` → 免卡；未知新工具按 `side_effect` fail-closed |
| ⑫ | `pipeline/agent/session.py::_review` | 「本会话都批准」：卡选项多一个 `approve_session`；答它即记 `allow_key`，之后同 key 免卡（`Decision(provenance="session_allow")`）；审批日志照记一行 |
| ⑬ | `pipeline/agent/session.py::_fetch_cards` | 不再出抓取卡，直接执行；审批日志记 `channel="auto"` |
| ⑭ | `pipeline/agent/tools.py` | 覆盖 `01-topic.md` / `07-titles.md` / `02-adversarial.md` 前留底到 `_agent/file-history/` |
| ⑮ | `pipeline/agent/cli.py::TerminalChannel.ask` | 终端答 `a` = `approve_session`（选项里有时） |
| ⑯ | `desktop/src/renderer/HumanCards.tsx` | `approve_session` 标签「本会话都批准」；卡出现时若输入框空闲，焦点落到批准按钮，按钮旁标「⏎」 |

## 三、不做的事

- 不给 bash 改文件做留底（无法可靠识别它改了什么）。
- 不做「全自动」模式开关：现边界下剩下的卡都是危险类，再加开关等于取消最后一道关；需要时再立项。
- 停机点不动。

## 四、验证

- `pytest`：排队入队 / 工具批后注入 / 收工前有排队则继续 / 退回 / 老化边界 / 新边界逐行 / 本会话都批准 / 留底；变异检验。
- `cd desktop && npx vitest run`；e2e 视真机情况补。

## 五、验收（人勾）

- [ ] 真实一期：让 ava 跑一串带管道的 bash，不弹卡。
- [ ] 回合中途发一句「改成 X」，它在当前工具跑完后改向；排队气泡送达后进入时间线。
- [ ] 让它 `rm` 一个临时文件：弹卡，回车即批；点「本会话都批准」后第二次 `rm` 不弹。
- [ ] 中途点停止：未送达的排队消息退回输入框。
