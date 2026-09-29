# Spec 15：web_search provider 可插拔（Exa 主 + Tavily 备）

日期：2026-09-29（**v0.1 草案**，未经红队；状态：**待人审 → 红队 → 施工**；对应 issues **D29**；上位：Spec 4（`network-tools-spec`，已归档）、Spec 13（门禁 8 复跑的前置）；相关 ADR：ADR-0021）

> **本稿只是草案。** 选型（Exa 主、Tavily 备）已由人 2026-09-29 拍板；其余设计点（§2）是我的建议，每条都标了「待人确认」的地方（§11）。**施工时点**：按 2026-09-26 人拍板，晚于二期 21 个 session 收官——现已满足，可排期。
> 本稿的 API 形态中：**Exa 免 key MCP 端点已在代理下实测**（§2.1 证据）；**Exa 带 key 的 REST 与 Tavily 的请求/响应形态来自官方文档与我的记忆，本机没有 key，未实测**——PR1 第一步必须用真实 key 各打一发，把响应存成 fixture 后再写解析器（§8）。

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
| 可落下一家 | HTTP 429 / 5xx、超时、DNS/连接失败、解析出 0 条、响应结构不符 | 记下失败原因，尝试链上下一家 |
| **不落下一家，直接失败** | egress 命中（`PermissionError`）、`_guard_url` 拒连、HTTP 401/403（key 无效/被拒） | 立即抛出。egress 命中必须停（换一家发同样的 query 依然是泄漏）；401/403 是**配置错误**，静默换家会把「key 配错了」藏起来（家规禁静默降级）。取舍见 §11 Q3 |

- **全链失败**：抛 `ValueError`，消息**逐家列出** `provider名: 原因`（429 / 超时 / 解析为空 / …），不含请求串与密钥。这是 Spec 4 §3.2「空结果不静默」的推广——模型看到的是「三家都挂了，原因分别是…」，而不是一个含糊的「搜索失败」。

### 2.3 决策 3：对外契约不变、`provider` 字段语义微调

- 返回值仍是 `{"query","provider","results","truncated"}` 四键，`results[]` 仍是 `{"title","url","snippet"}`。**`provider` 的值从「端点 URL」改为「适配器名」**（如 `"exa_mcp"`），这样模型/人能看出这次是主还是备回答的。这是 Spec 4 §3.2 示例值的语义变化，非键集变化；`tools.py` 的 `web_search` description 只需补一句「由配置的检索服务按序尝试」，**不改参数 schema**。
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

**凭据语义变化**：现行 `load_web_config` 是「指名了环境变量但为空 → 整个配置无效」。链上有多家时这条会让「Tavily 的 key 没设」拖垮整个 `web_search`（连不需要 key 的 exa_mcp 也用不了）。**新语义**：`api_key_env` 指名但变量为空 → **该家在本次运行标记为「未就绪」并跳过**，不使整个配置无效；全链都未就绪才报「无可用 provider」并列出缺哪些环境变量。（这与 Spec 4 的「指名却拿不到 = 配置错误」纪律有出入，**待人确认，§11 Q2**。）

### 2.5 决策 5：出网与安全——每家一次、发送前全套

对链上每一家、每一次实际发送：① 构造请求（含 body）→ ② `assert_egress_boundary(endpoint, {"query": 归一后的 query})` → ③ `_guard_url(endpoint, trusted_ranges)` → ④ `opener(request, timeout)`。egress 断言按**该家的端点**做（不同 provider = 不同接收方；query 对每一家都是外发）。**POST body 里的 query 也必须过归一 + 断言**（现行只覆盖 GET 的 query string；改 POST 后这是新增的必测面，§7 T-P3）。响应文本仍走 `_scrub()` 入方向清洗，密钥值 `_redact_secret` 全字段替换。响应体读取沿用 `_read_body_capped` 的字节封顶。

### 2.6 决策 6：明确退役与不做

- **退役**：`_SearchResultParser`、`_decode_ddg_href`、DDG 相关常量与 fixture、Spec 4 §7.1 T1/T16 中钉 DDG DOM 的用例（改写为各适配器的 fixture 用例）。
- **不做**：① 不做 provider 并发竞速（省额度、保持「至多 1 次/家」的可推理性）；② 不做结果缓存；③ 不做 query 改写/翻译（pi 有 `query-rewrite.ts`，本项目由模型自己换词）；④ 不接 pi 的其余 20 家；⑤ 不做 UA 伪装绕盾（Spec 4 §10 RF-1/RF-7 不变）；⑥ 不引入任何第三方库（§5）。

## 3. 数据契约

- **`search_web` 返回**：同 §2.3（四键不变，`provider` = 适配器名）。
- **错误数据**（`execute_tool` 包装为 `{"ok":False,"error":...}`，与 Spec 4 §3.4 两类错误命运一致）：
  - 全链失败：`ValueError("web_search 全部检索服务失败：exa_mcp: HTTP 429（限流）；tavily: 超时")`；
  - 无可用 provider：`ValueError("web_search 无可用检索服务：tavily 需要环境变量 TAVILY_API_KEY（未设置）")`；
  - egress 命中：原 `PermissionError`（本轮 `[BLOCKED]` 交人，行为不变）。
- **配置无效**：`load_web_config → None` → 既有「缺少或无效的 config/agent/web.json」错误，旧字段出现时附一句指向 `search.providers`。

## 4. 模块接口与改动清单

### 4.1 `pipeline/agent/web.py`

- `WebConfig`：删 `search_endpoint/search_query_param/api_key/api_key_param`；增 `search_providers: tuple[ProviderCfg, ...]`（`ProviderCfg` = frozen dataclass：`name`、`api_key`（已读出，空串=无 key））。`fetch_*`/`trusted_fake_ip_ranges` 不动。
- `load_web_config`：按 §2.4 校验；旧字段出现 → `None`。
- 新增 `_PROVIDERS` 注册表与三个适配器 `_search_exa_mcp / _search_exa_api / _search_tavily`（各自含**解析**，纯函数，可用 fixture 直测）；SSE 解析辅助 `_parse_sse_json(body)`。
- `search_web`：主流程重写为「遍历链 → 每家发送前全套（§2.5）→ 适配器 → 归一化（去重/截 snippet/丢非 http）→ 0 条按可落下一家处理」，签名不变（`query, limit, *, config, opener, root`）。
- 删除 `_SearchResultParser`、`_decode_ddg_href`。

### 4.2 `pipeline/agent/tools.py`

仅 `web_search` 的 `description` 文本（§2.3 一句）。**`RESTRICTED_EGRESS_PATTERNS`、`assert_egress_boundary`、参数 schema、注册表键集均不动。**

### 4.3 配置与迁移

- `config/agent/web.json`（进 git）：`search` 段换成 §2.4 默认链。
- **本机 `config/agent/web.local.json`（gitignore，当前指向 so.com）**：整文件覆盖会让 `web.json` 的新默认链**失效**，且含旧字段将被判无效。**施工 PR 必须显式处理**：删掉其中的 `search` 段（保留 `trusted_fake_ip_ranges/crawl/browser` 等本机项）；此步动的是用户本机文件，**需人确认后再改**，不得在 PR 里悄悄改。
- 环境变量：`TAVILY_API_KEY`（人注册后放 shell profile；不进仓库、不进 web.local.json——后者只写变量名）；可选 `EXA_API_KEY`。

## 5. 依赖白名单

`web.py` 仍只用 stdlib（`urllib`/`json`/`re`）。SSE 解析手写（`data:` 行 + `json.loads`），不引入 `sseclient` 等。既有 §5.2 子进程纯洁性探针照跑。

## 6. 跨 Spec 边界

- **Spec 13（门禁 8）**：本 spec 落地并通过后，门禁 8 用同一冒烟任务复跑（一色彩羽 → `/character/26090`）。首跑判定 ① 当时被 D29 污染，复跑才有定性资格；**判据不变、不许调阈值**（Spec 13 §10 RF-1）。
- **Spec 4**：只改 `search_web` 一处（§4.1）；`fetch_web` / 出网守卫 / 清洗 / 依赖白名单原样。Spec 4 §3.1 配置 schema 表与 §3.2 示例值随本 spec 在归档文件加**修订注记**（不改正文）。
- **D30**：无交集（§1）。但两者都会在 `03.5` 文本上撞 egress 断言的问题**与本 spec 无关**，不在此修。

## 7. 测试规格与变异检验

**铁律**：全部测试**零真实出网**（`opener` 注入 fake，`_pin_public_dns` 同 Spec 4）；密钥一律假值 `test-key-0000`；fixture 取自 PR1 首步的**真实响应**（`tests/fixtures/web_search/*.json|.txt`），不手编。

| 编号 | 用例 | 覆盖 |
|---|---|---|
| T-P1 | `exa_mcp` 解析 fixture | 实测 SSE 响应 → 逐字段 title/url/snippet；`limit` 生效；`\n\n---\n\n` 分块 |
| T-P2 | `exa_api` / `tavily` 解析 fixture | 各自真实响应 → 统一三元组 |
| T-P3 | **POST body 的 egress** | query 含 `cloud.local.json`（含 `%2563loud…` 编码变体）→ `PermissionError`，**每家的 opener 零调用**（含链上第二家：不得因第一家失败而把受限 query 发给第二家） |
| T-P4 | 落下一家 | 主家 fake 抛 429 / 超时 / 5xx / 返回 0 块 / 结构异常 → 备家被调且结果返回；`provider == "tavily"`；主家调用计数恰为 1（零重试） |
| T-P5 | **401/403 不落下一家** | 主家 401 → 直接失败，备家 opener **零调用**，错误消息指向 key 配置 |
| T-P6 | 全链失败 | 消息逐家列出原因；不含 query 全文、不含密钥 |
| T-P7 | 空结果纪律 | 全部家均 0 条 → 抛错，**严禁**返回空 list（钉 §2.2） |
| T-P8 | 凭据卫生 | 假 key 出现在请求头，**不出现在**返回值 / 错误消息 / `provider` 字段 |
| T-P9 | 配置校验 | 旧字段 → `None` 且消息指路；未知 provider 名、重名、空链、>4 家 → `None`；指名 key 变量为空 → 该家未就绪而非整体无效（**取决于 §11 Q2**） |
| T-P10 | 归一化 | snippet 封顶 500；URL 去重；`javascript:` 被丢弃 |
| T-P11 | `_guard_url` 每家生效 | getaddrinfo 指向 `127.0.0.1` 的 provider 端点 → `PermissionError`，opener 零调用 |
| T-P12 | 退役守卫 | `web.py` 不再含 `_SearchResultParser` / `result__a`（静态守卫，防旧路径复活） |
| T-P13 | Spec 4 既有 T1/T16 改写 | 改为通过新适配器 fixture 表达同一性质，不留钉 DDG DOM 的用例 |

**变异矩阵（每条须被上表**指定**用例杀死，并核对杀手身份——沿用二期纪律：`PYTHONDONTWRITEBYTECODE=1`、净树、不许因 collection 崩溃/NameError 算 KILLED）**：

| 变异 | 应被谁杀 |
|---|---|
| M1 egress 断言移到发送**之后** | T-P3 |
| M2 只对第一家做 egress 断言（第二家漏） | T-P3（第二家腿） |
| M3 401/403 也落下一家 | T-P5 |
| M4 落下一家时重试同一家 | T-P4（计数恰为 1） |
| M5 0 结果时返回空 list | T-P7 |
| M6 全链失败消息只留最后一家 | T-P6 |
| M7 `provider` 返回端点 URL | T-P1/P4 |
| M8 snippet 不封顶 | T-P10 |
| M9 去掉 `_guard_url` | T-P11 |
| M10 错误消息拼入密钥 | T-P8 |
| M11 旧字段不判无效 | T-P9 |
| M12 SSE 解析取最后一条 `data:` 而非第一条有 `result` 的 | T-P1 |

## 8. 施工与 PR 划分

- **PR0（人 + 施工 session，先于代码）**：① 人注册 Tavily、导出 `TAVILY_API_KEY`（可选 Exa key）；② 用真实 key 各打一发，把 `exa_mcp` / `exa_api` / `tavily` 的**原始响应**存成 fixture（含一个 429/无结果样本，若能拿到）；**未拿到真实响应前不写解析器**；③ 人确认 §11 三个问题。
- **PR1**：`web.py` 适配器 + 新 `search_web` + `load_web_config` + T-P1~T-P13 + 变异矩阵实跑；Spec 4 归档文件加修订注记。
- **PR2**：`web.json` 换默认链、`tools.py` description、（人确认后）清理本机 `web.local.json` 的 `search` 段；跑 Spec 13 门禁 8 冒烟；issues D29 迁 archive。

## 9. 验收门禁

1. T-P1~T-P13 全绿；全量 pytest 全绿（缺盘才红的既有用例除外，需在结论里写明）；
2. 变异 M1~M12 全部被**指定用例**杀死，回填矩阵；
3. `web.py` 静态守卫：无 `_SearchResultParser`、无第三方 import；
4. `web.json` 与（清理后的）本机配置均能被 `load_web_config` 读出，`web_search` 真实调一次拿到 ≥3 条含正文摘录的结果；
5. **模拟主家故障**（把 `exa_mcp` 端点临时指错）确认落到 Tavily 且 `provider=="tavily"`（真机，非 fake）；
6. Spec 13 门禁 8 复跑并给出定性结论（过 / 判定① 仍红则按 Spec 13 §10 RF-1 处理）；
7. **人审**：至少 3 条真实查询（中文角色 / 英文 wiki / 冷门作品）的结果质量由人看过并认可——首要判据是检索质量，这一条只有人能判。

## 10. 潜在红旗

- **RF-1 Exa 免 key 入口被收紧**：预案 = 申请 `EXA_API_KEY` 改用 `exa_api`（链配置一行）；Tavily 兜底期间不中断。
- **RF-2 两家同时不可用**：`web_search` 显式失败（§3），agent 走「直抓已知 URL / 站点 API」（Spec 13 门禁 8 起手本就如此），不静默降级。
- **RF-3 Exa 返回的摘录含提示注入文本**：与 `web_fetch` 同级风险，Spec 4 §2.5「工具层不做语义过滤、只做清洗与封顶」的决策继续适用；snippet 封顶 500 已缩小面。
- **RF-4 额度耗尽**：Tavily 1000/月，agent 循环里一次研究可能用掉数十次。本 spec **不做限额**（YAGNI），但 PR2 在 issues 登记「观察一个月用量」，超预期再另立项。
- **RF-5 代理下 `_guard_url` 的本地 DNS 解析**：本机 TUN fake-ip 段 `198.18.0.0/15` 已在 `web.local.json` 的 `trusted_fake_ip_ranges`；新端点 `mcp.exa.ai` / `api.tavily.com` 走同一放行，PR2 真机验证时确认（预期无需改）。

## 11. 人的裁决（2026-09-29）与遗留

- **Q1 ✅**：默认链**不含** `exa_api`；有 `EXA_API_KEY` 后人手动加进链。
- **Q2 ✅**：`api_key_env` 指名但变量为空 → **该家跳过（标记未就绪）**，不使整个配置无效；全链都未就绪才报「无可用检索服务」并列出缺哪些变量。与 Spec 4「指名却拿不到 = 配置错误」的纪律有出入，**以本条为准**，Spec 4 归档文件加修订注记。
- **Q3 ✅**：401/403 **直接失败、不落下一家**；错误汇总里同时列出已尝试各家的原因。
- **Q4 待定（默认 500）**：snippet 上限决定每条结果带多少正文进上下文，见下。**未收到不同意见则按 500 施工**。
- **Q5 ✅**：本机 `config/agent/web.local.json` 的 `search` 段**由施工 session 在 PR2 删除**（保留 `trusted_fake_ip_ranges/crawl/browser` 等本机项）；须在代码切换的**同一步**做（先删会让现行 `web_search` 因缺 `search` 段而失效，后删则新代码拒读旧字段），并在提交说明里写明删了什么。

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
