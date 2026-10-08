# 工具结果脱敏受限路径 + 出网被拦时发带模式名的 notice（D52 / N53 / N52）

日期：2026-10-08　状态：**已施工，机检通过（pytest 2149 / vitest 469 / e2e 119 通过），待真实一期手验**
关联：D52（主）；N53（D30-A 读码推断，本条实证）；N52（桌面端看不到被拦原因）；ADR-0026（后果段加修订注）；Spec 9（协议 `notice` 新增 code）

## 起因

董香二期（`2026-09-21-东京喰种-雾岛董香人物志-二`）agent 跑本地配音 1500 s 成功，下一次请求模型时整轮被拦并回滚：

- `session.jsonl` 第 288 行是 `run_pipeline` 的工具结果（3063 字节），受限模式的唯一命中是 `stdout_tail` 末行 `… → /Volumes/…/03-audio/manifest.json`；
- 下一次 `chat_complete` 对整个请求体做断言（`tools.py::assert_egress_boundary`）→ `PermissionError` → `run_tool_loop` 返回 `stopped:"blocked", rollback:True`（不收尾，因为同一份历史必然再被拒）；
- 第 289 行 `turn_rollback`、第 290 行 `turn_end{stopped:"blocked", duration_s:1524.382}`。模型看不到配音结果；
- 桌面端只收到 `turn_finished{stopped:"blocked"}`，没有原因（N52）。终端路径有 `cli.py` 的 `[BLOCKED] 出网被拦截：…`。

打印该路径的作业不止 tts：`tts.py`（打点写入、找不到 manifest）、`cloud.py`（云端 tts 拉回校验）、`corrections.py`（manifest 缺失）。逐个改作业打印治标不治本，下一个作业照样复发。

## 威胁模型（为什么脱敏不削弱防线）

沿用 ADR-0026 §理由与 Spec 16 §3：Y2-r19 要防的是凭据与 `03-audio/` 的**内容**出网。内容防线是结构性的：`read_artifact` 读域硬排除 `03-audio/`，`config/` 不在任何读根。请求体上的子串断言只比文件名，是第二层绊线。

工具结果里出现的是作业打印的**路径**，不是文件内容。把路径里的受限字样换成 `[已脱敏]`，不放出任何内容；仓库已有两处同口径先例：状态卡（`status_card.py`）与抓回网页（`web.py::_scrub`）。

## 施工内容

### A. 脱敏落点：`llm.py::_tool_message`

工具结果进会话的唯一序列化点（真实结果、人拒绝、判重、中断合成结果都经它）。

- `tools.py` 新增 `scrub_restricted(text) -> str`：对 `RESTRICTED_EGRESS_PATTERNS` 逐条 `re.sub(re.escape(p), "[已脱敏]", text, flags=re.IGNORECASE)`。`web.py::_scrub` 与 `status_card.py` 的同款循环改为调用它（行为逐字节不变，免得三份分叉）。
- `_tool_message` 的 `content` = `scrub_restricted(json.dumps(outcome, ensure_ascii=False))`。四条模式都不含 JSON 需要转义的字符，替换文本也不含引号或反斜杠，所以结果仍是合法 JSON。
- 会话落盘的是脱敏后的消息：`session.jsonl` 与模型实际看到的字节一致，`--continue` 重放不会再被拦。
- 人看的工具帧走 `_trace(content=outcome)`，是**原始**结果，桌面端「工具原文」仍显示完整路径。

**刻意不脱敏的面**（保住 ADR-0026 的「读域拒了 + 发送闸再掐」双保险）：

- assistant 消息与其中 `tool_calls` 的参数。模型自己在参数里写出 `03-AUDIO/manifest.json`，下一轮照旧被拦（`test_egress_payload_blocks_case_variant_audio_read` 守着）；
- 人的输入（Spec 16 §10 Q2 已裁：人亲手打出的受限路径照旧拦）。

断言本身一字不改，仍是最后一道闸。`re.IGNORECASE` 与断言用的 `casefold` 口径不同，实测：`re.sub(…, flags=re.I)` 能把 `03-AUDIO/MANIFEſT.JSON`（长 s）也换掉；万一有脱敏漏掉的变体，断言照拦，退化为改前行为，不会放出去。

### B. 被拦时的 notice（`protocol.py::_run_turn`）

`outcome["stopped"] == "blocked"` 时，在 `turn_finished` 之前发一帧：

```
{"t": "notice", "level": "error", "code": "egress_blocked", "text": "<正文>"}
```

正文只有：命中的模式名（取自 `outcome["error"]`，断言报错文案里的 `'<pattern>'`；取不到就不写模式名），固定说明「本轮已回滚，请求没有发出」，以及常见来源「对话里打出了该路径，或模型在工具参数里写了该路径」。**不含请求体或工具结果正文。**

终端路径不动：`cli.py` 的 `[BLOCKED]` 两行与 golden G9 保持原样，避免终端重复打印。桌面端已按 `notice` 渲染一行（`ConversationPane.tsx`，带 `data-code`），不改前端。

Spec 9 §3.1 的 `notice` 帧格式不变，只新增一个 code，在 Spec 9 文首加修订注。

## 测试

期望值先在实现上实跑，看懂再写断言。

| 编号 | 场景 | 期望 |
|---|---|---|
| TS-1 | mock LLM：先发 `run_pipeline` 调用（打桩返回 `stdout_tail` 含 `…/03-audio/manifest.json` 与大写变体），再给最终回复 | 第二次请求发出、`stopped == "done"`、无回滚；第二次请求体里工具结果含 `[已脱敏]`、不含任何受限模式（casefold 后比） |
| TS-2 | `_tool_message` 直调：合成结果（`ok:false` 的 error 文案带路径） | 脱敏；`json.loads(content)` 成功 |
| TS-3 | 双保险：既有 `test_egress_payload_blocks_case_variant_audio_read` | 原样通过（assistant 的 tool_call 参数仍含受限路径 → blocked） |
| TS-4 | 协议回合被拦（打桩 `run_turn` 返回 blocked + error） | 帧序 `turn_started → notice{code:"egress_blocked", level:"error"} → turn_finished{blocked} → stop_points`；notice 正文含模式名、不含请求体 |
| TS-5 | 既有 web `_scrub`、状态卡脱敏用例、G9 golden | 原样通过 |

变异矩阵（`scripts/verify_mutations.py`，D52-MUT-*）：

| 编号 | 改坏什么 | 该红的用例 |
|---|---|---|
| D52-MUT-1 | `_tool_message` 去掉脱敏 | TS-1 |
| D52-MUT-2 | `scrub_restricted` 去掉 `re.IGNORECASE` | TS-1（大写变体） |
| D52-MUT-3 | 把脱敏挪到 `_wire_messages` 对全部消息做 | TS-3 |
| D52-MUT-4 | 去掉 blocked 时的 notice | TS-4 |

## 残余

- 人在对话里打出受限路径、模型在工具参数里写出受限路径：照旧整轮拦（有意保留）。现在桌面端能看到命中的模式名。
- 被拦回合仍整轮回滚（含已执行的工具结果）。本条修掉的是最常见的触发源，回滚语义不动。
