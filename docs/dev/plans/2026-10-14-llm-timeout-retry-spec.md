# D93 模型请求超时放宽可配 + 可重试错误自动重试

> 状态：**已施工·机检通过**（2026-10-14，main，未提交）。待人：重启 AVA 在大上下文会话里用一段时间，看不再 60 s 被杀；遇断流时看「自动重试 n/3」提示。

## 一、目标与对应 issue

- 对应 issues **D93**；后续独立事项 **D94**（流式，见 [2026-10-14-llm-streaming-spec.md](2026-10-14-llm-streaming-spec.md)）。
- 事件：2026-10-14 supercell 企划会话（`data/episodes/supercell-传奇企划志/session.jsonl`）两回合「直接停止」。回合 `efb801f5…`：4 次搜索后第 3 次模型调用 13:13:51 发出，13:14:51 正好 60 s 超时 → `LLMError("LLM 请求失败: timed out")` → `_stop("error")` → 收尾总结 + `turn_failed`。回合 `d143c467…` 同样形态。
- 根因：`llm.py` 写死 `REQUEST_TIMEOUT = 60`，非流式请求要在 60 s 内整体返回。同一会话里健康调用随上下文增长：20 s、7 s（58k token 回合）；26 s、40 s，收尾 56 s（64k token 回合）。慢但健康的模型被误杀，超时后也不重试。
- 目标：超时只判「连接已死」（默认 300 s、可配）；超时 / 断连类错误在工具循环里自动重试，用尽才收尾。

## 二、前置条件与调研

- 探针（2026-10-14，本机 CLIProxyAPI 7.2.132 + `gemini-3.8-flash-high`）：代理在模型思考期间一个字节都不发（首个数据 8–10 s），**流式 + 空闲超时修不了这次的误杀**。流式另立 D94。
- 同类工具的做法：
  - Pi（本机 `@earendil-works/pi-coding-agent` 1.1.0）：`core/http-dispatcher.js` 的 `DEFAULT_HTTP_IDLE_TIMEOUT_MS = 300_000`，同时用作 undici 的 `headersTimeout` 与 `bodyTimeout`，用户可选 30 s / 1 分 / 2 分 / 5 分 / 关闭；会话层自动重试（`settings-manager.js` 中 `retry.maxRetries ?? 3`、`baseDelayMs ?? 2000`），`pi-ai/dist/utils/retry.js` 把 `timed? out` / `timeout` / `connection refused` 等列为可重试。
  - Codex CLI（`openai/codex` main@7a08c5f627）：`codex-rs/model-provider-info/src/lib.rs` 的 `DEFAULT_STREAM_IDLE_TIMEOUT_MS = 300_000`、`DEFAULT_STREAM_MAX_RETRIES = 5`、`DEFAULT_REQUEST_MAX_RETRIES = 4`；`codex-api/src/sse/responses.rs` 用 `timeout(idle_timeout, stream.next())`，超时报 "idle timeout waiting for SSE"；`core/src/responses_retry.rs` 重连并在界面显示 "Reconnecting... n/m"。
  - 两家都没有总时长上限。
- 人裁决（2026-10-14）：默认 300 s、可配；超时类错误自动重试 3 次；流式写进 plans 先不做。
- 基线：`uv run pytest` 3073 passed。

## 三、改动清单

| 文件 | 锚点 | 改动 |
|---|---|---|
| `pipeline/agent/llm.py` | `REQUEST_TIMEOUT = 60` | → `DEFAULT_REQUEST_TIMEOUT_S = 300`；新增 `LLM_MAX_RETRIES = 3`、`LLM_RETRY_BASE_S = 2.0`、`_sleep = time.sleep`、`_RETRYABLE_HTTP = {408, 409, 429}` |
| 同上 | `class LLMError` | 加关键字参数 `retryable: bool = False` |
| 同上 | `class LLMConfig` | 加 `request_timeout_s: float \| None`；`load_llm_config` 调新函数 `_parse_timeout`（缺省 → 300，`0` → None 即不设，非法 → 警告一次并回落 300） |
| 同上 | `def chat_complete(` | `timeout` 默认改 `None` = 用配置；显式传值照旧优先。HTTP 408/409/429/≥500 与 `URLError` / `TimeoutError` / `OSError` 标 `retryable=True`；其余 4xx、非 JSON、缺 `choices` 不标 |
| 同上 | `run_tool_loop` 内 `def _chat(` | 拆成 `_chat`（重试外壳）+ `_chat_once`（原逻辑）；可重试错误按 2 / 4 / 8 s 重发，先调 `control.on_llm_retry`；用尽抛 `LLMError("…（已自动重试 3 次）")`；计数 `llm_retries` |
| 同上 | `class LoopControl` | 加 `on_llm_retry: Callable[[int, int, float, str], None] \| None = None` |
| 同上 | `def _loop_result(` | 加 `llm_retries`，返回键 `"llm_retries"` |
| `pipeline/agent/session.py` | `on_steer=self._on_steer,` | 接 `on_llm_retry=self._on_llm_retry`，发 `notice{level: warn, code: llm_retry}`，报错原文过 `turn_error_text` 脱敏截断 |
| 同上 | `"k": "turn_end"` 记录 | 加 `"llm_retries"` |
| `pipeline/agent/session_titles.py`、`pipeline/agent/compact.py`、`desktop/src/shared/constants.ts` | 引用「REQUEST_TIMEOUT 60 s」的注释 | 只改注释使其准确，数值不动 |
| `docs/CHEATSHEET.md` | 「五、模型配置速查」 | 补 `request_timeout_s` 一条 |

## 四、新常量的取值理由

- **`DEFAULT_REQUEST_TIMEOUT_S = 300`**：Pi 与 Codex 的默认值都是 300 s。实测健康调用最长 56 s 且随上下文增长，300 s 留了 5 倍余量。这个值只用来判「连接已死」，不给模型快慢设门槛；真卡住时人随时可点停止。
- **`LLM_MAX_RETRIES = 3`、`LLM_RETRY_BASE_S = 2.0`**：取 Pi 的默认（3 次、2 s 起指数退避），等待依次是 2 / 4 / 8 s。Codex 取 5 次；我们按人裁决取 3。
- **可重试的 HTTP 状态 408/409/429/≥500**：与 OpenAI / Anthropic SDK 以及 Pi `provider-retry.js` 的口径一致。400 / 401 不重试：D85 那种模型名失效，重试三轮只会白等。

## 五、测试要求

新文件 `tests/test_llm_timeout_retry.py`，26 条：
1. 分类：`TimeoutError`、`URLError`、`ConnectionResetError`、HTTP 503 / 500 / 429 / 408 → 可重试；400 / 401、非 JSON → 不可重试；裸 `LLMError` 默认不可重试（12 处既有测试替身行为不变）。
2. 超时取值：缺省 300、120、7.5、`0` → None；`"abc"`、-1、`true` → 300 并警告；显式 `timeout=30` 压过配置。
3. 循环：两次可重试错误后成功（sleep `[2, 4]`、回调参数、`llm_calls == 1`）；用尽（4 次请求、sleep `[2, 4, 8]`、回滚、报错含「已自动重试 3 次」）；不可重试错误一次即停；等待中被中断 → `interrupted`；收尾调用也会重试。
4. 会话层：发出 `llm_retry` notice，`turn_end.llm_retries == 1`。
5. 真 socket：本地假端点首个请求挂住，`request_timeout_s: 1` → 约 1 s 超时 → 重试 → 成功。

变异检验 14 / 14 全部被抓：可重试恒为假、去掉 5xx、不等待、退避序列错、忽略配置、用尽后不加说明、不调回调、上限改 4、0 不映射为不设、接受 bool、不可重试也重试、会话层不发 notice、turn_end 不记次数、400 也重试。

## 六、验证命令

```bash
uv run pytest tests/test_llm_timeout_retry.py   # 26 passed
uv run pytest                                   # 全量，含 tests/test_static.py ruff 门禁
```

真实冒烟（2026-10-14，本机代理，未改配置）：`chat_complete(timeout=1)` 在 1.0 s 抛出 `LLMError retryable=True: LLM 请求失败: timed out`；默认配置读到 `request_timeout_s = 300`，正常调用 4.5 s 回复。

## 七、明确不做

- 流式（D94）、桌面端实时显示模型输出。
- `turn_finished` 协议帧加字段（重试次数只进 `turn_end` 日志与 notice）。
- 解析 `Retry-After` 头（Pi、Codex 都会解析；本机代理没观察到需要，留作以后）。
- 标题 / 压缩 / 对抗审查 / 笔记审查的重试，以及改它们各自的超时数值。
- 「结束会话」等待 `SESSION_END_WAIT_MS = 90 s` 不跟着放大：人点结束就是要走，90 s 内没收完尾就 SIGTERM（收尾记 aborted，工具结果已落盘），只改注释说明。
- 不改 `config/agent.local.json`。

## 八、完成判定

- [x] `uv run pytest` 全绿（含 ruff 门禁）
- [x] 新测试 26 条，变异 14 / 14 被抓
- [x] 真实冒烟：本机代理超时判为可重试，默认 300 s 生效
- [ ] 待人：重启 AVA，在 supercell 这类大上下文会话里用一段时间，不再出现 60 s 被杀；遇到断流时对话区出现「自动重试 n/3」
