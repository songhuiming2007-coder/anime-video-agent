# D94 模型请求改流式（施工图）

> 状态：**未动工**（2026-10-14 人裁决：先写施工图，这次不做）。前置 D93 已施工。

## 一、目标与对应 issue

- 对应 issues **D94**；源自 D93 调查（[2026-10-14-llm-timeout-retry-spec.md](2026-10-14-llm-timeout-retry-spec.md)）。
- `chat_complete` 现在是非流式：服务端整个回复生成完才返回，客户端只能对「整体多久返回」设超时。改成流式以后，超时可以按「多久没收到新数据」判断，也为桌面端实时显示模型输出铺路。
- **价值边界，先写清楚**：流式**修不了**模型思考期间的静默。本机代理在 Gemini 思考时一个字节都不发（见第二节），这段静默在流式下照样整段算进超时。D93 那次误杀靠放宽超时 + 重试解决，与本方案无关。流式的收益只有两条：① 长输出过程中不会因为整体耗时长被误判；② 能实时上屏（实时显示本身不在本方案范围内）。

## 二、前置条件（2026-10-14 探针实测）

环境：本机 CLIProxyAPI 7.2.132（`127.0.0.1:8317`）+ `gemini-3.8-flash-high`。探针脚本当时放在会话 scratch 目录，按家规不落仓库，要点如下。

| 请求方式 | 首个数据到达 | `reasoning_content` |
|---|---|---|
| 非流式 | 13.8 s 整体返回 | 键在、值为空 |
| 流式 | 8.2 s | 全程 null |
| 流式 + `reasoning_effort: high` | 9.6 s | 有（923 字），与首个数据同一刻才到 |

- 响应头 `Content-Type: text/event-stream`，每行 `data: {…}`，结尾 `data: [DONE]`。没观察到 `: keep-alive` 注释行。
- 每个增量块的结构：`choices[0].delta` 带 `role`、`content`、`reasoning_content`、`tool_calls`；还有 `finish_reason` 与 `native_finish_reason`。
- `tool_calls[]` 增量带 `index`、`id`、`type`、`function.name`、`function.arguments`。两个并行调用分别以 `index` 0 / 1 各自完整到达。
- 最后一块带 `finish_reason: "tool_calls"`，并附 `usage`（请求里带了 `stream_options.include_usage`）。
- 顶层 `model` 字段回的是 `gemini-3.8-flash-n`，与请求名不同。只是观察到的现象，不影响拼装。
- 现有会话日志里非流式回复的字段是 `content`、`reasoning_content`、`role`、`tool_calls[{function, id, index, type}]`；流式拼回来以后必须和它逐字段一致。

## 三、改动清单（施工时以现状为准，锚点不给行号）

| 文件 | 锚点 | 改动 |
|---|---|---|
| `pipeline/agent/llm.py` | `def chat_complete(` 的 `payload` | 一律加 `"stream": True`、`"stream_options": {"include_usage": True}` |
| 同上 | `with urllib.request.urlopen(` | 按 `Content-Type` 分流：`text/event-stream` 逐行读 SSE 并拼装；`application/json` 照旧整体解析（测试假端点、不支持流式的服务商） |
| 同上 | 新函数 `_assemble_stream(lines)` | 拼装规则：`content` / `reasoning_content` 字符串累加（全为 null 时保持现有形状：键在、值为空）；`tool_calls` 按 `index` 归并，`id` / `type` / `function.name` 取首次非空，`function.arguments` 累加；保留 `index` 键；未知字段取最后一次非 null；带 `usage` 的块写 `_LAST_USAGE` |
| 同上 | 异常分支 | 流中途断开、没收到 `[DONE]` 也没有 `finish_reason` → `LLMError(retryable=True)`，不拿半截回复当结果；`data: {"error": …}` → 按其中的状态码分类；行解析失败 → 不可重试 |
| 同上 | 超时 | 不新增常量：urllib 的 socket 超时本来就是每次读取各自计时，流式下 `request_timeout_s` 自动变成「两段数据之间的最长间隔」，首段数据之前的等待也受它约束 |

## 四、新常量 / 新判据的取值理由

- 不新增数值常量。超时沿用 D93 的 `request_timeout_s`（理由见 D93 第四节）。
- 判据「没收到 `[DONE]` 也没有 `finish_reason` 就算断流」：OpenAI 兼容流的正常结束必然带其中之一（探针实测两者都有）。只看其一，会把只发 `finish_reason` 不发 `[DONE]` 的服务商误判为断流。

## 五、测试要求

- 拼装：用探针抓到的真实增量序列做夹具（脱敏后进 `tests/fixtures/`），拼出的 message 与同题非流式回复逐字段相等，包括 `tool_calls[].index` 和 `reasoning_content` 的形状。
- 两个并行工具调用、`arguments` 跨多块分片、空 `content`、只有 `reasoning_content`，各一例。
- 分流：`application/json` 响应走旧路径（既有 `urlopen` 桩与 e2e `fakeLlm.ts` 不用改）。
- 断流：读到一半抛 `TimeoutError`、或连接关闭但没有 `[DONE]` → 可重试 `LLMError`，并接上 D93 的重试。
- `usage` 只在最后一块出现时，`_LAST_USAGE` 照样拿到 `prompt_tokens`（D56 读数不回退）。
- 变异：累加改覆盖、丢 `index`、不要求 `[DONE]` / `finish_reason`、`usage` 不写，预期全部被抓。

## 六、验证命令

```bash
uv run pytest
# 真实：用探针同题对本机代理分别跑非流式与流式，比较拼装结果字段集合与内容
```

## 七、明确不做

- 桌面端实时显示（另立 UI 方案，按 plans/README 的 UI 规则先出截图让人选）。
- 改超时数值、改重试策略（D93 已定）。
- 为思考期静默做任何「保活」假设：代理不发就是不发，不在客户端伪造。

## 八、完成判定

- [ ] 拼装夹具测试与变异全过
- [ ] `uv run pytest` 全绿
- [ ] 本机代理真实对照：流式拼出的回复与非流式字段一致，工具调用正常执行
- [ ] 待人：真实会话里跑一段，会话日志里的 assistant 消息形状与改造前一致（恢复会话、跨档重放都不受影响）
