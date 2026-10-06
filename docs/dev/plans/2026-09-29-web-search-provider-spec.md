# Spec 15：web_search provider 可插拔（Exa 主 + Tavily 备）

日期：2026-09-29（v0.1 草案）；**2026-10-06 v0.2（作者修订，回应红队一轮 5🟡 + 6🔵，见「作者修订回应」）**；状态：**v0.2，待红队定向复审**（§11 五问与 🟡-1 / 🟡-3 / 🟡-5 三处补充裁决人均已拍板）→ 施工；对应 issues **D29**；上位：Spec 4（`network-tools-spec`，已归档）、Spec 13（门禁 8 复跑的前置）；相关 ADR：ADR-0021）

> **本稿是 v0.2 修订稿。** 选型（Exa 主、Tavily 备）已由人 2026-09-29 拍板；§11 的 Q1～Q5 与 2026-10-06 的三处补充裁决（Q3 注、Q5 注、Q6）均已拍板，本稿不再有待人确认项（施工时动本机 `web.local.json` 仍须人当场确认，§4.3）。**施工时点**：按 2026-09-26 人拍板，晚于二期 21 个 session 收官——现已满足，可排期。
> 本稿的 API 形态中：**Exa 免 key MCP 端点已在代理下实测**（§2.1 证据）；**Exa 带 key 的 REST 与 Tavily 的请求/响应形态来自官方文档与我的记忆，本机没有 key，未实测**——PR1 第一步必须用真实 key 各打一发，把响应存成 fixture 后再写解析器（§8）。

## 红队一轮裁决（2026-10-06，D29-R，独立 session 未参与起草；只审文稿，未改代码）

**裁决：🟡 修订后复审**（5🟡 + 6🔵）。契约零回归（四键、参数 schema、`tools.py` 的 limit clamp [1,10] 不动）、stdlib only、发送前 egress + `_guard_url`、零自动重试、空结果不静默、`_scrub` 入方向——这几条落地忠实于 Spec 4 与 §11 裁决。阻塞项在凭据流转与施工顺序。

| 编号 | 指控 | 证据 | 建议 |
|---|---|---|---|
| 🟡-1 | **桌面端会话永远拿不到 `TAVILY_API_KEY`，备家在主力界面形同不存在** | Spec 10 §2.9：Finder 启动的 app 拿不到 shell 环境；`SESSION_*` 只注入**一个**钥匙串密钥（`config/agent*.json` 的 LLM `api_key_env`，`host/sessions.ts` 的 `extraEnv`）；`spawner.ts::childEnv` 白名单只有 PATH/LANG/PYTHONUTF8/PYTHONUNBUFFERED/HOME/USER/TMPDIR。PR0 写「放 shell profile」只对终端 `ava` 生效。按 §11 Q2，缺 key 的 tavily 被标「未就绪」跳过——桌面端里 Exa 一挂就是「无可用检索服务」 | 补一条对 Spec 10 §2.9 的修订请求：host 依 `web.json` 链上各家的 `api_key_env`（同 `KEY_ENV_NAME_RE` 与值校验）从钥匙串 `-s ava` 读并注入，日志/诊断同 TH-6 纪律；或由人明确接受「备家只在终端可用」。PR0 写清两处放 key（shell profile 与 `security add-generic-password -s ava -a TAVILY_API_KEY`）。门禁 5 必须在桌面端会话里跑 |
| 🟡-2 | **重定向会把密钥头带到别的主机** | 2026-10-06 本机 127.0.0.1 探针（scratchpad `redirect_probe.py`）：`urllib` 对 POST 收到 302 → 改 GET 跳到另一端口，**`Authorization: Bearer …` 与 `x-api-key` 原样转发**；`add_unredirected_header` 设的头不转发。`_GuardedRedirectHandler` 只逐跳 `_guard_url`，不剥头；公网目标照样通过 `_guard_url` | provider 请求的密钥头一律 `add_unredirected_header`，或 provider 请求不跟随重定向（3xx 记为「可落下一家」的故障并报出）。新增用例：fake opener 模拟 302 → 断言新请求不含任何密钥头；变异「改回 `headers=` 设头」应被杀 |
| 🟡-3 | **PR 划分与 §11 Q5 自相矛盾，PR1 落地即打断 web_fetch** | §8：PR1 改 `load_web_config`（旧字段 → `None`），`web.json` 与本机 `web.local.json` 的 `search` 段要到 PR2 才换；§11 Q5 却要求「同一步」。`load_web_config` 是 search/fetch 共用配置（`fetch_web` 同样 `cfg is None` 即报错），而 `web.local.json` 整文件覆盖 `web.json`——PR1 与 PR2 之间，web_search **和 web_fetch** 在本机与默认配置下都不可用，读真实配置的用例也会红 | 把 `web.json` 默认链与 `web.local.json` 清理并入 PR1 的同一提交（人确认后改本机文件）；或 PR1 期间旧字段仍兼容读、PR2 再收紧——二选一写死 |
| 🟡-4 | **删 `WebConfig.api_key` 会打断 `fetch_web` 的脱敏，而 §6 声称 fetch 原样** | `web.py` 的 `fetch_web` 在正文、`final_url`、链接清单、`url` 四处用 `_redact_secret(..., cfg.api_key)`；§4.1 删掉 `api_key`、改为每家 `ProviderCfg.api_key`，未说 fetch 用哪个 | 写死：search 与 fetch 都对**链上全部非空 key**逐一 `_redact_secret`（结果、错误消息、`provider` 字段、fetch 四处）；T-P8 增 fetch 腿 |
| 🟡-5 | **§11 Q3「401/403 直接失败」与 §2.1 RF-1「Exa 免 key 入口可能加验证」冲突** | 免 key 的 `exa_mcp` 若被收紧（或前置 Cloudflare 挑战），返回的正是 401/403；按 Q3 直接失败、Tavily 一次不试——备家恰在它最该顶上的场景缺席。Q3 的理由「key 配错了不许静默」只对**配了 key 的家**成立 | 不改 Q3 本意，**请人澄清适用范围**：建议「配了 key 的家 401/403 → 直接失败；免 key 的家 401/403 → 可落下一家，原因写『免 key 入口被拒，需配 key』」。**2026-10-06 人裁决：同意该建议**（作者修订时据此改 §2.2 故障分类表、§11 Q3 加注，并补用例与变异） |
| 🔵-1 | MCP 带内错误被误报为「解析为空」 | JSON-RPC 可在 HTTP 200 下返回 `error` 或 `result.isError: true`（限流文案常走这里）；§2.2 只按 HTTP 状态分类 | 带内错误单列一类（可落下一家），原因取其文案（截断 200 字、`_scrub`、不含 query）；PR0 尽量取一份带内错误样本做 fixture |
| 🔵-2 | `limit` 到各家请求参数的映射与 `truncated` 判定未写 | §2.3 只说 `truncated` 表示被 limit 截断；若向 provider 请求恰好 limit 条，`truncated` 恒为假 | 写死每家请求 `numResults`/`max_results` = limit + 1（≤ 10），按返回条数是否超 limit 判 `truncated` |
| 🔵-3 | 变异矩阵缺两条最粗的回退 | 「fallback 永不触发」只隐含在 T-P4；「未就绪的家仍被发送（无 key 请求 → 401 → 按 Q3 直接失败，掩盖缺 key）」无对应 | 增 M13「可落下一家的故障直接抛出」→ T-P4；M14「未就绪家照发」→ T-P9（断言该家 opener 零调用、汇总里写缺哪个变量） |
| 🔵-4 | 「key 泄进日志」只测了返回值与错误消息 | T-P8 未覆盖 stdout/stderr | T-P8 加 `capsys` 断言 stdout/stderr 不含假 key |
| 🔵-5 | PR0 真实响应 fixture 的卫生 | 原始响应入库前未要求检查 | 入库前 `grep` 不含 key 值与个人信息；查询用中性词 |
| 🔵-6 | 实测环境与运行环境不同 | 2026-09-29 实测走显式代理 `127.0.0.1:7897`；桌面端会话 env 无任何代理变量，依赖 TUN / 系统代理 | 门禁 4/5 在终端与桌面端各跑一次，记录各自出网路径 |

### 作者修订回应（v0.2，2026-10-06；修订人未参与红队一轮）

先核证据再改：🟡-1 读 `desktop/src/host/spawner.ts::childEnv`（白名单只有 PATH/LANG/PYTHONUTF8/PYTHONUNBUFFERED/HOME/USER/TMPDIR）、`host/sessions.ts`（`extraEnv` 只放 `resolveKey()` 的一个结果）、`host/secrets.ts::resolveLlmKey` 与 Spec 10 §2.9，属实；🟡-2 在 scratchpad `d29/redirect_probe.py` 复跑（只用 127.0.0.1，Python 3.12.13 与 3.14.3 结果相同）：POST 收到 301/302/303 → 改 GET 跟随，`headers=` 设的 `Authorization` / `x-api-key` **原样转发**，`add_unredirected_header` 设的**不转发**；307/308 对 POST 直接抛 `HTTPError`，属实；🟡-3 读 `web.py::load_web_config`（search 与 fetch 共用、任一段无效整份返回 `None`）与 `fetch_web`（`cfg is None` 即报错），属实，crawl / browser 走各自的段加载器（`load_crawl_section` / `load_browser_section`），不受影响；🟡-4 读 `fetch_web`，正文、`final_url`、链接清单（url + anchor）、`url` 四处用 `cfg.api_key`，属实。

| 编号 | 处置 | 改了哪里 |
|---|---|---|
| 🟡-1 | **采纳（人 2026-10-06 裁决：改 Spec 10 §2.9，不接受「备家只在终端可用」）** | 新增 §2.7（桌面端密钥注入：host 按 web 配置链上各家的 `api_key_env` 从钥匙串 `-s ava` 读取并注入，校验同 LLM 密钥，单家失败只让该家「未就绪」、不影响 LLM 就绪）；§8 新增 **PR3（desktop）** 与 Spec 10 §2.9 修订记录；§4.3 写清两处放 key；§7 新增 TH-W1～W3 与 M18～M20；§9 门禁 5 必须在桌面端会话里跑；N50 面扩大如实登记 |
| 🟡-2 | 采纳（两道都做） | §2.5 第 ⑤ 条：provider 请求**不跟随重定向**（3xx = 可落下一家，原因「HTTP 30x 重定向（未跟随）」），密钥头一律 `add_unredirected_header`；§2.2 故障表加 3xx；§7 T-P14a/b 与 M15/M16 |
| 🟡-3 | **采纳（人 2026-10-06 裁决：清理并入 PR1 同一提交）** | §8：`web.json` 默认链与本机 `web.local.json` 的 `search` 段清理并入 PR1，与代码切换同一提交（本机文件 gitignore，施工时人当场确认后再改，提交说明写明删了什么）；PR2 只剩 description、门禁 8 冒烟与归档；§4.3、§11 Q5 加注 |
| 🟡-4 | 采纳 | §4.1：`WebConfig` 增 `secret_values`（链上全部非空 key）；search 与 fetch 一律经 `_redact_all` 逐一脱敏；§6 改写 fetch「原样」的说法；§7 T-P8 增 fetch 腿与 M21 |
| 🟡-5 | **采纳（人 2026-10-06 裁决：同意红队建议）** | §2.2 故障表把 401/403 拆成两行：配了 key 的家 → 直接失败；免 key 的家（`exa_mcp`）→ 可落下一家，原因写明「Exa 免 key 入口被拒（HTTP 40x），需配 `EXA_API_KEY` 改用 `exa_api`」；§11 Q3 加注；§7 T-P5 拆 a/b、M3 拆 a/b；§10 RF-1 同步 |
| 🔵-1 | 采纳 | §2.2：JSON-RPC 带内错误（`error` 或 `result.isError: true`）单列为可落下一家，原因取其文案（截断 200 字、`_scrub` + `_redact_all`、不含 query）；§7 T-P15 与 M17；§8 PR0 尽量取带内错误样本 |
| 🔵-2 | 采纳 | §2.3：各家请求条数 = `limit + 1`，返回条数 > limit 即 `truncated = true` 并截到 limit；PR0 核实各家单次上限 ≥ 11 |
| 🔵-3 | 采纳 | §7 变异矩阵增 M13（可落下一家的故障直接抛出 → T-P4）、M14（未就绪的家照发 → T-P9） |
| 🔵-4 | 采纳 | §7 T-P8 加 `capsys`：stdout / stderr 不含假 key |
| 🔵-5 | 采纳 | §8 PR0：fixture 入库前 `grep` 不含 key 值与个人信息，查询用中性词 |
| 🔵-6 | 采纳 | §9 门禁 4 / 5 在终端与桌面端各跑一次，记录各自的出网路径（显式代理 / TUN / 系统代理） |

与 Spec 16（D30）的交叉：Spec 16 给 `assert_egress_boundary` 加的是带默认值的关键字参数，本 spec 的调用点不传、行为不变，两者无冲突；谁后落地谁 rebase。

## 0. 一句话设计

`search_web` 从「GET 一个 HTML 端点 + 钉死 DOM 的解析器」改成「**按配置顺序尝试一串 provider 适配器**」：第一个成功的答；失败（限流 / 5xx / 超时 / 空结果）落到下一个；全失败则**逐家如实报错**。`web_search` 的**对外契约四键不变**（`query/provider/results/truncated`），`_SearchResultParser` 与 DDG 端点退役。

## 1. 背景与硬约束

- **现状**（D29，2026-09-26 实测）：DDG HTML 端点返回 202 + 反机器人页，零结果；360 方案被人否（「用 360 搜索不如不用」——**否的是检索质量**）。`config/agent/web.json` 仍指向 DDG；**本机 `config/agent/web.local.json`（gitignore）仍指向 `https://www.so.com/s`，且整文件覆盖 `web.json`**——迁移时必须一并处理（§4.3）。
- **判据**（人原话）：「参考 pi 的 web search 实现，不要在意国内网络环境、我有代理」；首要判据是**检索质量**，国内可达性不作判据。
- **不可破的既有纪律**（Spec 4）：① 发送前 `assert_egress_boundary`（query 归一后断言，`web.py::search_web`）；② `_guard_url` 出网目标收敛（私网/保留地址拒连、`trusted_fake_ip_ranges`）；③ 密钥只从 `api_key_env` 指名的环境变量读、不进返回值/日志/回显；④ 空结果不许静默返回空 list；⑤ 零自动重试（每个 provider 至多 1 次请求；「fallback」是换一家，不是重试同一家）；⑥ `web.py` 依赖白名单（stdlib only，§5）。
- **D30 的边界**：D30（egress 断言收窄）动 Spec 4 冻结面，**不在本 spec**。本 spec 只在 `web.py` / `web.json` / `tools.py` 的 `web_search` description 上动，不碰 `RESTRICTED_EGRESS_PATTERNS` 与 `assert_egress_boundary`。

## 2. 关键设计决策

### 2.1 决策 1：provider 选型与调用形态（人已拍板；形态见证据）

| 顺位 | 名称 | 端点 / 形态 | 凭据 | 实测状态 |
|---|---|---|---|---|
| 1 | `exa_mcp` | `POST https://mcp.exa.ai/mcp?tools=web_search_exa`；JSON-RPC `tools/call`，头 `Accept: application/json, text/event-stream`；响应为 SSE，`data:` 行内是 JSON，`result.content[0].text` 为文本块 | **无需 key**（有限流，429 时提示加 key） | **已实测**（2026-09-29，本机代理 127.0.0.1:7897，3 条查询各 6 结果） |
| 2 | `exa_api` | `POST https://api.exa.ai/search`，头 `x-api-key`，body `{query, numResults, contents:{highlights:true}}`，响应 `results[].{title,url,highlights[]}` | `EXA_API_KEY`（每月 $10 免费额度） | **未实测**，PR1 首步验证 |
| 3 | `tavily` | `POST https://api.tavily.com/search`，头 `Authorization: Bearer <key>`，body `{query, max_results, search_depth:"basic"}`，响应 `results[].{title,url,content,score}` | `TAVILY_API_KEY`（每月 1000 次免费，basic = 1 credit） | **未实测**，PR1 首步验证 |

- **默认链**：`["exa_mcp", "tavily"]`（人拍板的「Exa 主 Tavily 备」）。`exa_api` 是 `exa_mcp` 的带 key 升级位——**只在本机配了 `EXA_API_KEY` 且写进链里才启用**，不进默认链（避免默认配置依赖不存在的 key）。
- **`exa_mcp` 实测响应形态**（决定解析器写法）：文本块以 `\n\n---\n\n` 分隔；每块字段 `Title: … / URL: … / Published: … / Author: … / Highlights:` 后接正文摘录（**可达数百至上千字**，含 `\n...\n` 拼接的多段）。空结果没有专门标记——对一条乱码查询它也会返回不相关的结果，即「零结果」在 Exa 上几乎不发生，空结果纪律主要靠「解析出 0 块」触发。
- **风险（如实登记）**：`exa_mcp` 是 pi 借用的公共免费入口，**无 SLA、无配额承诺、可能下线或加验证**。因此 **fallback 不是可选项**：Tavily 必须在默认链里，且 §7 有「主 provider 全挂时降级」的用例。

### 2.2 决策 2：适配器接口与故障分类

- 适配器是纯函数 `(query, limit, provider_cfg, opener) -> list[{"title","url","snippet"}]`，只做「构造请求 → 发 → 解析」，**不做** egress 断言 / `_guard_url` / 清洗（这些由 `search_web` 主流程在**每次**发送前统一做，避免每个适配器各写一遍而漏一个）。注册表 `_PROVIDERS: dict[str, Callable]`，未知名字在 `load_web_config` 就判配置无效。
- **故障分类（决定是否落下一家）**：

| 类别 | 例 | 行为 |
|---|---|---|
| 可落下一家 | HTTP 429 / 5xx、超时、DNS/连接失败、解析出 0 条、响应结构不符；**HTTP 3xx（不跟随，v0.2 🟡-2）**；**JSON-RPC 带内错误**（HTTP 200 下 `error` 或 `result.isError: true`，v0.2 🔵-1）；**免 key 的家（本次请求未带 key，即 `exa_mcp`）返回 401/403**（v0.2 🟡-5） | 记下失败原因，尝试链上下一家。带内错误的原因取其文案（截断 200 字、`_scrub` + `_redact_all`、不含 query）；免 key 家 401/403 的原因固定写「Exa 免 key 入口被拒（HTTP 40x），需配 `EXA_API_KEY` 改用 `exa_api`」 |
| **不落下一家，直接失败** | egress 命中（`PermissionError`）、`_guard_url` 拒连、**配了 key 的家**（本次请求带了 key：`exa_api` / `tavily`）返回 HTTP 401/403（key 无效/被拒） | 立即抛出。egress 命中必须停（换一家发同样的 query 依然是泄漏）；配了 key 的家 401/403 是**配置错误**，静默换家会把「key 配错了」藏起来（家规禁静默降级）。免 key 的家被拒不是配置错误，而是 §2.1 RF-1 预言的入口收紧，恰是备家该顶上的场景（人 2026-10-06 裁决）。取舍见 §11 Q3 |

- **全链失败**：抛 `ValueError`，消息**逐家列出** `provider名: 原因`（429 / 超时 / 解析为空 / …），不含请求串与密钥。这是 Spec 4 §3.2「空结果不静默」的推广——模型看到的是「三家都挂了，原因分别是…」，而不是一个含糊的「搜索失败」。

### 2.3 决策 3：对外契约不变、`provider` 字段语义微调

- 返回值仍是 `{"query","provider","results","truncated"}` 四键，`results[]` 仍是 `{"title","url","snippet"}`。**`provider` 的值从「端点 URL」改为「适配器名」**（如 `"exa_mcp"`），这样模型/人能看出这次是主还是备回答的。这是 Spec 4 §3.2 示例值的语义变化，非键集变化；`tools.py` 的 `web_search` description 只需补一句「由配置的检索服务按序尝试」，**不改参数 schema**。
- **`limit` 映射与 `truncated` 判定（v0.2 🔵-2）**：`tools.py` 已把 `limit` clamp 到 [1, 10]；每家请求条数（`exa_mcp` 的 `numResults`、`exa_api` 的 `numResults`、`tavily` 的 `max_results`）一律为 `limit + 1`；归一化（去重、丢非 http）之后条数 > limit → 截到 limit、`truncated = true`，否则 `false`。PR0 用真实请求核实各家单次上限 ≥ 11；若某家达不到，该家改为「返回数 == 请求数即判 `truncated`」并在本节注明。
- `snippet` 统一封顶 `SEARCH_SNIPPET_MAX_CHARS = 500`（Exa 的 Highlights 会到上千字，6 条就是数千字进上下文；Tavily 的 `content` 同理）。截断处不加省略号以外的标记，`truncated` 仍只表示「结果条数被 limit 截断」，不与 snippet 截断混用。
- 结果 URL 去重（同一 URL 保留首条）；`url` 非 http/https 的丢弃（防 `javascript:` 之类进入后续 `web_fetch`）。丢弃后为 0 条 → 按「解析出 0 条」处理。

### 2.4 决策 4：配置 schema（**破坏性变更，需迁移**）

`web.json` 的 `search` 段由「单端点五字段」改为 provider 链；`fetch`/`crawl`/`browser`/`trusted_fake_ip_ranges` 不动。

```json
"search": {
  "timeout_s": 20,
  "providers": [
    { "name": "exa_mcp" },
    { "name": "tavily", "api_key_env": "TAVILY_API_KEY" }
  ]
}
```

| 字段 | 约束 |
|---|---|
| `search.providers` | 非空数组；每项 `name` 必须在 `_PROVIDERS` 注册表内；**同名不得重复** |
| `providers[].api_key_env` | 指名环境变量；`exa_mcp` 允许省略（无 key 模式）；`exa_api` / `tavily` **必填** |
| `search.timeout_s` | 沿用（>0，≤60），作为**每家**的超时；全链最坏耗时 = 家数 × timeout，故 `providers` 上限 4 家 |
| **旧字段** `search.endpoint/query_param/api_key_env/api_key_param` | **出现即判无效**（`load_web_config → None`），错误消息指向 `search.providers`——旧配置不得被静默当成新配置读（否则 `web.local.json` 里残留的 so.com 会悄悄失效或悄悄生效，两者都是静默） |

**凭据语义变化**：现行 `load_web_config` 是「指名了环境变量但为空 → 整个配置无效」。链上有多家时这条会让「Tavily 的 key 没设」拖垮整个 `web_search`（连不需要 key 的 exa_mcp 也用不了）。**新语义**：`api_key_env` 指名但变量为空 → **该家在本次运行标记为「未就绪」并跳过**，不使整个配置无效；全链都未就绪才报「无可用 provider」并列出缺哪些环境变量。（这与 Spec 4 的「指名却拿不到 = 配置错误」纪律有出入，**人已于 2026-09-29 裁决按新语义，§11 Q2**。）

### 2.5 决策 5：出网与安全——每家一次、发送前全套

对链上每一家、每一次实际发送：① 构造请求（含 body）→ ② `assert_egress_boundary(endpoint, {"query": 归一后的 query})` → ③ `_guard_url(endpoint, trusted_ranges)` → ④ `opener(request, timeout)`。egress 断言按**该家的端点**做（不同 provider = 不同接收方；query 对每一家都是外发）。**POST body 里的 query 也必须过归一 + 断言**（现行只覆盖 GET 的 query string；改 POST 后这是新增的必测面，§7 T-P3）。响应文本仍走 `_scrub()` 入方向清洗，密钥值经 `_redact_all`（§4.1，链上全部非空 key 逐一 `_redact_secret`）全字段替换。响应体读取沿用 `_read_body_capped` 的字节封顶。

⑤ **重定向（v0.2 🟡-2）**：provider 请求**不跟随重定向**——默认 opener 换成专用的 `_provider_opener`（`redirect_request` 返回 `None`，3xx 以 `HTTPError` 浮出），3xx 归入「可落下一家」，原因「HTTP 30x 重定向（未跟随）」。理由：provider 端点都是 POST API，被重定向本身就是端点漂移；urllib 跟随 301/302/303 时把 POST 改成 GET、丢掉 body，跟过去也拿不到正确结果，却会把 `headers=` 设的密钥头原样转发给新主机（2026-10-06 本机 127.0.0.1 探针实测）。同时**密钥头一律 `add_unredirected_header`**（纵深：即使将来有人换回跟随重定向的 opener，密钥也不随跳转转发）。`fetch_web` 的 `_GuardedRedirectHandler` 不动（它本来就不带密钥头）。

### 2.6 决策 6：明确退役与不做

- **退役**：`_SearchResultParser`、`_decode_ddg_href`、DDG 相关常量与 fixture、Spec 4 §7.1 T1/T16 中钉 DDG DOM 的用例（改写为各适配器的 fixture 用例）。
- **不做**：① 不做 provider 并发竞速（省额度、保持「至多 1 次/家」的可推理性）；② 不做结果缓存；③ 不做 query 改写/翻译（pi 有 `query-rewrite.ts`，本项目由模型自己换词）；④ 不接 pi 的其余 20 家；⑤ 不做 UA 伪装绕盾（Spec 4 §10 RF-1/RF-7 不变）；⑥ 不引入任何第三方库（§5）。

### 2.7 决策 7：桌面端密钥注入（v0.2 新增，🟡-1；人 2026-10-06 裁决：改 Spec 10 §2.9）

**问题**：Finder 启动的 app 拿不到 shell 环境；Spec 10 §2.9 每次 `SESSION_*` spawn 只从钥匙串注入**一个**密钥（LLM 的 `api_key_env`），`childEnv` 白名单不含任何 `*_API_KEY`。不改的话，桌面端会话里 `tavily` 永远「未就绪」（§11 Q2），`exa_mcp` 一挂就是「无可用检索服务」——备家在主力界面形同不存在。

**流程**（扩展 Spec 10 §2.9，host 侧，PR3）：
1. 新增 `PROBE_WEB_KEY_ENVS` 模板问 core：当前生效的 web 配置（与 `load_web_config` 共用同一段「local 优先」选择逻辑）链上各家的 `api_key_env` 叫什么。core 新增纯读函数 `web_key_env_names() -> list[str]`（去重、保持链序、配置无效时返回空清单；不读环境变量、不读值）。host 不自己读 `config/`（与 C10-R2 同理，避免 TS 侧出现第二份规则）。新模板按 Spec 8 RF-3「加模板时必须复核」走一遍白名单。
2. 每个名字照 §2.9 第 2～4 步：`KEY_ENV_NAME_RE` 校验 → `KEYCHAIN_READ`（`security find-generic-password -s ava -a <名字> -w`）→ 值校验（可打印 ASCII、去恰好一个末尾换行）。与 LLM 密钥同名时只读一次。
3. 通过的写进同一次 `SESSION_*` spawn 的 `env[名字]`；**任一 web 密钥失败都不影响 LLM 就绪**，只是不注入该名字，core 侧该家按 §11 Q2「未就绪」跳过。失败原因不进会话头的 LLM 降级文案，而由 core 的「无可用检索服务」/「全部检索服务失败」错误消息列出缺哪个变量，并附两处放法（见第 5 条）。
4. 密钥纪律与 §2.9 第 5 条完全相同：不进 argv、spawn 日志、`AVA_SPAWN` 行、诊断、发往 renderer 的任何消息、host 的 stdout/stderr；`KEYCHAIN_READ` 的结果只在 `host/secrets.ts` 内被读取（TG-12）。注入的键数上限 = 1（LLM）+ 链上家数（≤ 4）。
5. 两处放 key（§4.3、§8 PR0 写进人手步骤）：终端 `ava` 用 shell profile 的 `export TAVILY_API_KEY=…`；桌面端用 `security add-generic-password -s ava -a TAVILY_API_KEY -w`。core 的错误消息写成「`tavily` 需要环境变量 `TAVILY_API_KEY`（未设置；终端：在 shell profile 里 export；桌面端：`security add-generic-password -s ava -a TAVILY_API_KEY -w`）」。

**放过了什么、代价**：会话进程多了至多 4 个密钥变量。它们与 LLM 密钥一样随 `jobs.py` 的 `env = dict(os.environ)` 继承到作业及其子孙（ffmpeg、yt-dlp 等），**N50 的面随之扩大**（N50 仍是备忘，本 spec 不修，在 N50 行加注）。TH-6「会话环境只比白名单多一个密钥键」的断言改为「只多出 LLM 名 + web 链上声明的名字，且每个都匹配 `KEY_ENV_NAME_RE`」。

## 3. 数据契约

- **`search_web` 返回**：同 §2.3（四键不变，`provider` = 适配器名）。
- **错误数据**（`execute_tool` 包装为 `{"ok":False,"error":...}`，与 Spec 4 §3.4 两类错误命运一致）：
  - 全链失败：`ValueError("web_search 全部检索服务失败：exa_mcp: HTTP 429（限流）；tavily: 超时")`；
  - 无可用 provider：`ValueError("web_search 无可用检索服务：tavily 需要环境变量 TAVILY_API_KEY（未设置；终端：在 shell profile 里 export；桌面端：security add-generic-password -s ava -a TAVILY_API_KEY -w）")`；
  - 免 key 家被拒后备家也失败：`ValueError("web_search 全部检索服务失败：exa_mcp: Exa 免 key 入口被拒（HTTP 403），需配 EXA_API_KEY 改用 exa_api；tavily: HTTP 429（限流）")`；
  - egress 命中：原 `PermissionError`（本轮 `[BLOCKED]` 交人，行为不变）。
- **配置无效**：`load_web_config → None` → 既有「缺少或无效的 config/agent/web.json」错误，旧字段出现时附一句指向 `search.providers`。

## 4. 模块接口与改动清单

### 4.1 `pipeline/agent/web.py`

- `WebConfig`：删 `search_endpoint/search_query_param/api_key/api_key_param`；增 `search_providers: tuple[ProviderCfg, ...]`（`ProviderCfg` = frozen dataclass：`name`、`api_key`（已读出，空串=无 key））与 **`secret_values: tuple[str, ...]`**（链上全部非空 key，v0.2 🟡-4）。`fetch_*`/`trusted_fake_ip_ranges` 不动。
- **脱敏（v0.2 🟡-4）**：新增 `_redact_all(text, cfg)` = 对 `cfg.secret_values` 逐一 `_redact_secret`。`search_web` 的结果三字段、错误消息、`provider` 字段，以及 `fetch_web` 原本用 `cfg.api_key` 的四处（正文、`final_url`、链接清单的 url 与 anchor、`url`）一律改用它。理由：删掉单一 `api_key` 后 fetch 不再知道该脱哪个 key；链上任何一个 key 都可能被页面回显（例如抓到一个打印请求头的调试页）。
- 新增 `_provider_opener(request, timeout)`：不跟随重定向（§2.5 ⑤）；provider 请求的密钥头一律 `request.add_unredirected_header(...)`。
- 新增 `web_key_env_names(root) -> list[str]`（§2.7 第 1 条，供桌面端 `PROBE_WEB_KEY_ENVS` 调用；纯读、不读值）。
- `load_web_config`：按 §2.4 校验；旧字段出现 → `None`。
- 新增 `_PROVIDERS` 注册表与三个适配器 `_search_exa_mcp / _search_exa_api / _search_tavily`（各自含**解析**，纯函数，可用 fixture 直测）；SSE 解析辅助 `_parse_sse_json(body)`。
- `search_web`：主流程重写为「遍历链 → 每家发送前全套（§2.5）→ 适配器 → 归一化（去重/截 snippet/丢非 http）→ 0 条按可落下一家处理」，签名不变（`query, limit, *, config, opener, root`）。
- 删除 `_SearchResultParser`、`_decode_ddg_href`。

### 4.2 `pipeline/agent/tools.py`

仅 `web_search` 的 `description` 文本（§2.3 一句）。**`RESTRICTED_EGRESS_PATTERNS`、`assert_egress_boundary`、参数 schema、注册表键集均不动。**

### 4.3 配置与迁移

- `config/agent/web.json`（进 git）：`search` 段换成 §2.4 默认链。
- **本机 `config/agent/web.local.json`（gitignore，当前指向 so.com）**：整文件覆盖会让 `web.json` 的新默认链**失效**，且含旧字段将被判无效。**施工 PR 必须显式处理**：删掉其中的 `search` 段（保留 `trusted_fake_ip_ranges/crawl/browser` 等本机项）；此步动的是用户本机文件，**需人确认后再改**，不得在 PR 里悄悄改。**时点（v0.2 🟡-3，人 2026-10-06 裁决）**：与 `load_web_config` 的切换、`web.json` 默认链的替换**在 PR1 同一提交里完成**——`load_web_config` 是 search 与 fetch 共用的配置，任何「代码已切、配置未换」的中间态都会让 `web_search` **和 `web_fetch`** 一起不可用。本机文件不进 git，提交说明写明「本机 web.local.json 删除了 search 段（endpoint/query_param/api_key_env/api_key_param/timeout_s）」。
- 环境变量（v0.2 🟡-1：两处都要放）：`TAVILY_API_KEY`——终端放 shell profile；桌面端放钥匙串 `security add-generic-password -s ava -a TAVILY_API_KEY -w`（§2.7）。不进仓库、不进 web.local.json（后者只写变量名）。可选 `EXA_API_KEY`，放法相同。

## 5. 依赖白名单

`web.py` 仍只用 stdlib（`urllib`/`json`/`re`）。SSE 解析手写（`data:` 行 + `json.loads`），不引入 `sseclient` 等。既有 §5.2 子进程纯洁性探针照跑。

## 6. 跨 Spec 边界

- **Spec 13（门禁 8）**：本 spec 落地并通过后，门禁 8 用同一冒烟任务复跑（一色彩羽 → `/character/26090`）。首跑判定 ① 当时被 D29 污染，复跑才有定性资格；**判据不变、不许调阈值**（Spec 13 §10 RF-1）。
- **Spec 4**：改 `search_web`（§4.1）；`fetch_web` 只有一处改动——脱敏从单 key `_redact_secret(…, cfg.api_key)` 换成链上全部 key 的 `_redact_all`（v0.2 🟡-4），其余（出网守卫、清洗、`_GuardedRedirectHandler`、依赖白名单）原样。
- **Spec 10（v0.2 🟡-1）**：§2.9「每次 spawn 只注入一个密钥」改为「LLM 密钥 + web 链上声明的密钥」，在 archive 原文加修订记录（日期、D29、人 2026-10-06 裁决），TH-6 断言口径随之调整（§2.7）。Spec 4 §3.1 配置 schema 表与 §3.2 示例值随本 spec 在归档文件加**修订注记**（不改正文）。
- **D30**：无交集（§1）。但两者都会在 `03.5` 文本上撞 egress 断言的问题**与本 spec 无关**，不在此修。

## 7. 测试规格与变异检验

**铁律**：全部测试**零真实出网**（`opener` 注入 fake，`_pin_public_dns` 同 Spec 4）；密钥一律假值 `test-key-0000`；fixture 取自 PR1 首步的**真实响应**（`tests/fixtures/web_search/*.json|.txt`），不手编。

| 编号 | 用例 | 覆盖 |
|---|---|---|
| T-P1 | `exa_mcp` 解析 fixture | 实测 SSE 响应 → 逐字段 title/url/snippet；`limit` 生效；`\n\n---\n\n` 分块 |
| T-P2 | `exa_api` / `tavily` 解析 fixture | 各自真实响应 → 统一三元组 |
| T-P3 | **POST body 的 egress** | query 含 `cloud.local.json`（含 `%2563loud…` 编码变体）→ `PermissionError`，**每家的 opener 零调用**（含链上第二家：不得因第一家失败而把受限 query 发给第二家） |
| T-P4 | 落下一家 | 主家 fake 抛 429 / 超时 / 5xx / 返回 0 块 / 结构异常 → 备家被调且结果返回；`provider == "tavily"`；主家调用计数恰为 1（零重试） |
| T-P5a | **配了 key 的家 401/403 不落下一家** | 链 `["tavily", "exa_mcp"]`（或 `["exa_api", "tavily"]`），首家带假 key 返回 401 → 直接失败，下一家 opener **零调用**，错误消息指向 key 配置 |
| T-P5b | **免 key 的家 401/403 落下一家**（v0.2 🟡-5） | 默认链，`exa_mcp` 返回 403 → `tavily` 被调且结果返回、`provider == "tavily"`；再让 `tavily` 也返回 429 → 全链失败消息含「Exa 免 key 入口被拒（HTTP 403）」与 tavily 的原因 |
| T-P6 | 全链失败 | 消息逐家列出原因；不含 query 全文、不含密钥 |
| T-P7 | 空结果纪律 | 全部家均 0 条 → 抛错，**严禁**返回空 list（钉 §2.2） |
| T-P8 | 凭据卫生 | 假 key 出现在请求头，**不出现在**返回值 / 错误消息 / `provider` 字段；**`capsys` 断言 stdout / stderr 不含假 key**（v0.2 🔵-4）；**fetch 腿**（v0.2 🟡-4）：链上两家各配一个假 key，`fetch_web` 抓回的正文、`final_url`、链接清单 url/anchor、`url` 里出现**任一** key 都被替换为脱敏标记 |
| T-P9 | 配置校验 | 旧字段 → `None` 且消息指路；未知 provider 名、重名、空链、>4 家 → `None`；指名 key 变量为空 → 该家未就绪而非整体无效（**取决于 §11 Q2**） |
| T-P10 | 归一化 | snippet 封顶 500；URL 去重；`javascript:` 被丢弃 |
| T-P11 | `_guard_url` 每家生效 | getaddrinfo 指向 `127.0.0.1` 的 provider 端点 → `PermissionError`，opener 零调用 |
| T-P12 | 退役守卫 | `web.py` 不再含 `_SearchResultParser` / `result__a`（静态守卫，防旧路径复活） |
| T-P13 | Spec 4 既有 T1/T16 改写 | 改为通过新适配器 fixture 表达同一性质，不留钉 DDG DOM 的用例 |
| T-P14a | **密钥头不随重定向**（v0.2 🟡-2） | 三个适配器构造出的请求：密钥只在 `request.unredirected_hdrs` 里，`request.headers` 里没有 |
| T-P14b | **provider 不跟随重定向** | `_provider_opener` 对 127.0.0.1 上的两台临时服务器（A 回 302 指向 B）发请求 → 以 `HTTPError` 302 浮出、B 命中计数为 0；`search_web` 把它归为可落下一家，原因含「重定向（未跟随）」（此用例只连回环地址，直接测 opener，不经 `_guard_url`） |
| T-P15 | **MCP 带内错误**（v0.2 🔵-1） | `exa_mcp` fake 返回 HTTP 200 + `error`（及 `result.isError: true` 各一例）→ 落到 `tavily`；全链失败时原因含带内错误文案（≤ 200 字、不含 query、不含 key），而**不是**「解析为空」 |
| T-P16 | `limit` 映射与 `truncated`（v0.2 🔵-2） | 请求体里的条数参数 = limit + 1；返回 limit + 1 条 → 截到 limit、`truncated == true`；返回 ≤ limit 条 → `false` |
| TH-W1（desktop，PR3） | 桌面端 web 密钥注入 | 假 core 回答 `PROBE_WEB_KEY_ENVS = ["TAVILY_API_KEY"]`、钥匙串桩有条目 → 会话子进程 env 恰多出 LLM 名与 `TAVILY_API_KEY` 两个键；`spawnLog`、诊断、发往 renderer 的消息里无标记值（TH-6 同口径） |
| TH-W2（desktop，PR3） | web 密钥失败不拖累 LLM | web 名不合规 / 钥匙串 44 / 值非 ASCII 各一例 → 不注入该名字，`ready.llm` 仍为 `ok`，会话照常启动 |
| TH-W3（desktop，PR3） | 同名只读一次 | web 名与 LLM 名相同 → `KEYCHAIN_READ` 只调用一次，env 只有一个键 |

**变异矩阵（每条须被上表**指定**用例杀死，并核对杀手身份——沿用二期纪律：`PYTHONDONTWRITEBYTECODE=1`、净树、不许因 collection 崩溃/NameError 算 KILLED）**：

| 变异 | 应被谁杀 |
|---|---|
| M1 egress 断言移到发送**之后** | T-P3 |
| M2 只对第一家做 egress 断言（第二家漏） | T-P3（第二家腿） |
| M3a 配了 key 的家 401/403 也落下一家 | T-P5a |
| M3b 免 key 的家 401/403 直接失败 | T-P5b |
| M4 落下一家时重试同一家 | T-P4（计数恰为 1） |
| M5 0 结果时返回空 list | T-P7 |
| M6 全链失败消息只留最后一家 | T-P6 |
| M7 `provider` 返回端点 URL | T-P1/P4 |
| M8 snippet 不封顶 | T-P10 |
| M9 去掉 `_guard_url` | T-P11 |
| M10 错误消息拼入密钥 | T-P8 |
| M11 旧字段不判无效 | T-P9 |
| M12 SSE 解析取最后一条 `data:` 而非第一条有 `result` 的 | T-P1 |
| M13 可落下一家的故障直接抛出（fallback 永不触发，v0.2 🔵-3） | T-P4 |
| M14 未就绪的家照发（无 key 请求，v0.2 🔵-3） | T-P9（断言该家 opener 零调用、汇总里写缺哪个变量） |
| M15 密钥头改回 `headers=` 设置（v0.2 🟡-2） | T-P14a |
| M16 `_provider_opener` 改回跟随重定向 | T-P14b |
| M17 带内错误按「解析为空」处理 | T-P15 |
| M18 web 密钥失败时把 LLM 也判降级（desktop） | TH-W2 |
| M19 web 密钥名不过 `KEY_ENV_NAME_RE` 也注入（desktop） | TH-W2 |
| M20 web 密钥值写进 spawn 日志（desktop） | TH-W1 |
| M21 `fetch_web` 只脱第一个 key（v0.2 🟡-4） | T-P8（fetch 腿） |

## 8. 施工与 PR 划分

- **PR0（人 + 施工 session，先于代码）**：① 人注册 Tavily，**两处放 key**：shell profile `export TAVILY_API_KEY=…`（终端）与 `security add-generic-password -s ava -a TAVILY_API_KEY -w`（桌面端，v0.2 🟡-1）；可选 Exa key 同样两处；② 用真实 key 各打一发，把 `exa_mcp` / `exa_api` / `tavily` 的**原始响应**存成 fixture（尽量含一个 429 / 无结果 / **JSON-RPC 带内错误**样本，v0.2 🔵-1），并核实各家单次条数上限 ≥ 11（§2.3）；**未拿到真实响应前不写解析器**；③ **fixture 卫生（v0.2 🔵-5）**：查询用中性词（不含个人信息）；入库前 `grep` 确认不含 key 值、邮箱、账号名等个人信息。
- **PR1（core，v0.2 🟡-3 改）**：`web.py` 适配器 + 新 `search_web` + `load_web_config` + `_redact_all` + `_provider_opener` + `web_key_env_names` + T-P1～T-P16 + 变异 M1～M17、M21 实跑；**同一提交**内替换 `config/agent/web.json` 默认链，并（施工时人当场确认后）删除本机 `web.local.json` 的 `search` 段——提交前用真实配置跑一次 `load_web_config` 与 `web_fetch`，确认两者都可用；Spec 4 归档文件加修订注记。
- **PR2（core）**：`tools.py` 的 `web_search` description；终端真机跑门禁 4、5。
- **PR3（desktop，v0.2 🟡-1）**：`PROBE_WEB_KEY_ENVS` 模板 + `host/secrets.ts` 扩展 + `sessions.ts` 注入 + TH-6 口径调整 + TH-W1～W3 + 变异 M18～M20；Spec 10 §2.9 archive 原文加修订记录；N50 行加注；`release-build` 后在**桌面端会话**里跑门禁 4、5。PR3 落地后跑 Spec 13 门禁 8 冒烟；issues D29 迁 archive。

## 9. 验收门禁

1. T-P1～T-P16 与 TH-W1～W3 全绿；全量 pytest 全绿（缺盘才红的既有用例除外，需在结论里写明）；
2. 变异 M1～M21 全部被**指定用例**杀死，回填矩阵；desktop 侧另跑 `npx vitest run`、`npx tsc --noEmit`、全量 e2e；
3. `web.py` 静态守卫：无 `_SearchResultParser`、无第三方 import；
4. `web.json` 与（清理后的）本机配置均能被 `load_web_config` 读出，`web_search` 真实调一次拿到 ≥3 条含正文摘录的结果；`web_fetch` 同时可用；**终端与桌面端各跑一次**，记录各自出网路径（显式代理 / TUN / 系统代理，v0.2 🔵-6）；
5. **模拟主家故障**（把 `exa_mcp` 端点临时指错）确认落到 Tavily 且 `provider=="tavily"`（真机，非 fake）；**必须在桌面端会话里跑一次**（v0.2 🟡-1：验证钥匙串注入），终端再跑一次；
6. Spec 13 门禁 8 复跑并给出定性结论（过 / 判定① 仍红则按 Spec 13 §10 RF-1 处理）；
7. **人审**：至少 3 条真实查询（中文角色 / 英文 wiki / 冷门作品）的结果质量由人看过并认可——首要判据是检索质量，这一条只有人能判。

## 10. 潜在红旗

- **RF-1 Exa 免 key 入口被收紧**：预案 = 申请 `EXA_API_KEY` 改用 `exa_api`（链配置一行）；Tavily 兜底期间不中断——v0.2 起免 key 家的 401/403 归为可落下一家（§2.2，人 2026-10-06 裁决），收紧当天就会落到 Tavily，错误汇总里写明「Exa 免 key 入口被拒，需配 key」。
- **RF-2 两家同时不可用**：`web_search` 显式失败（§3），agent 走「直抓已知 URL / 站点 API」（Spec 13 门禁 8 起手本就如此），不静默降级。
- **RF-3 Exa 返回的摘录含提示注入文本**：与 `web_fetch` 同级风险，Spec 4 §2.5「工具层不做语义过滤、只做清洗与封顶」的决策继续适用；snippet 封顶 500 已缩小面。
- **RF-4 额度耗尽**：Tavily 1000/月，agent 循环里一次研究可能用掉数十次。本 spec **不做限额**（YAGNI），但 PR2 在 issues 登记「观察一个月用量」，超预期再另立项。
- **RF-5 代理下 `_guard_url` 的本地 DNS 解析**：本机 TUN fake-ip 段 `198.18.0.0/15` 已在 `web.local.json` 的 `trusted_fake_ip_ranges`；新端点 `mcp.exa.ai` / `api.tavily.com` 走同一放行，PR2 真机验证时确认（预期无需改）。

## 11. 人的裁决（2026-09-29）与遗留

- **Q1 ✅**：默认链**不含** `exa_api`；有 `EXA_API_KEY` 后人手动加进链。
- **Q2 ✅**：`api_key_env` 指名但变量为空 → **该家跳过（标记未就绪）**，不使整个配置无效；全链都未就绪才报「无可用检索服务」并列出缺哪些变量。与 Spec 4「指名却拿不到 = 配置错误」的纪律有出入，**以本条为准**，Spec 4 归档文件加修订注记。
- **Q3 ✅**：401/403 **直接失败、不落下一家**；错误汇总里同时列出已尝试各家的原因。**2026-10-06 人补充裁决（红队 🟡-5）**：本条只适用于**配了 key 的家**（`exa_api` / `tavily`）；**免 key 的家**（`exa_mcp`）返回 401/403 → 落下一家，并在结果里写明「Exa 免 key 入口被拒」（§2.2）。
- **Q4 ✅**：`SEARCH_SNIPPET_MAX_CHARS = 500`（人 2026-09-29 确认）。上限的含义见下。
- **Q5 ✅**：本机 `config/agent/web.local.json` 的 `search` 段**由施工 session 在 PR2 删除**（保留 `trusted_fake_ip_ranges/crawl/browser` 等本机项）；须在代码切换的**同一步**做（先删会让现行 `web_search` 因缺 `search` 段而失效，后删则新代码拒读旧字段），并在提交说明里写明删了什么。**2026-10-06 人补充裁决（红队 🟡-3）**：「同一步」= **PR1 的同一提交**（连同 `web.json` 默认链），不留到 PR2（§8）。
- **Q6 ✅（2026-10-06，红队 🟡-1）**：桌面端会话也要能用备家——改 Spec 10 §2.9，host 按 web 配置链上各家的 `api_key_env` 从钥匙串读取并注入（§2.7、§8 PR3）；不接受「备家只在终端可用」。

**Q4 展开**：snippet 是 `web_search` 每条结果里给模型看的摘录，进入会话历史后**每一轮后续 LLM 调用都会重发**。上限一头决定上下文成本（默认 5 条 × 500 字 ≈ 2500 字；放到 1000 则翻倍且每轮都背着），另一头决定模型能否仅凭摘录判断「这条要不要 `web_fetch` 全文」——摘录只需够辨认页面，全文由 `web_fetch` 取。太小（如 150）辨认不出，会多花 fetch 次数；太大则挤占上下文、也放大提示注入的面（RF-3）。

## 附：引用自查表（2026-09-29 草案时按 HEAD `4ca9a03` 核对；以符号为锚）

| 引用 | 位置 | 核对 |
|---|---|---|
| `WebConfig` / `load_web_config` | `pipeline/agent/web.py:53` / `:73` | ✓ 字段与旧字段校验逻辑已读 |
| `_guard_url` / `_default_opener` | `web.py:209` / `:285` | ✓ |
| `_SearchResultParser` / `_decode_ddg_href` | `web.py:460` / `:445` | ✓ 将退役 |
| `search_web` | `web.py:595` | ✓ 签名不变 |
| `assert_egress_boundary` | `pipeline/agent/tools.py:307` | ✓ 不动 |
| `web_search` 工具条目 / `_tool_web_search` | `tools.py:463` / `:898` | ✓ 仅改 description |
| 本机 `web.local.json` 的 `search.endpoint` | `config/agent/web.local.json` | ✓ 当前为 `https://www.so.com/s`（gitignore） |
| Exa MCP 响应形态 | 2026-09-29 实测 | ✓ SSE，`result.content[0].text`，`\n\n---\n\n` 分块 |
| Exa REST / Tavily 形态 | 官方文档（未实测） | ⚠ PR0 用真实 key 验证 |
| **v0.2 补核（2026-10-06，HEAD `03ae4cd`）** | | |
| `childEnv` 白名单 / 会话 `extraEnv` | `desktop/src/host/spawner.ts::childEnv`、`host/sessions.ts`（`resolveKey` 一个结果）、`host/secrets.ts::resolveLlmKey` | ✓ 只注入一个钥匙串密钥（🟡-1 属实） |
| urllib 重定向转发密钥头 | scratchpad `d29/redirect_probe.py`（127.0.0.1） | ✓ 301/302/303 转发 `headers=` 设的头、不转发 `add_unredirected_header` 设的头；307/308 对 POST 抛 `HTTPError` |
| `fetch_web` 依赖 `load_web_config` 与 `cfg.api_key` | `web.py::fetch_web` | ✓ `cfg is None` 即报错；脱敏四处用 `cfg.api_key` |
| crawl / browser 配置 | `web_crawl.py::load_crawl_section`、`web_browser.py::load_browser_section` | ✓ 各自独立加载，不受 search 段变更影响 |
