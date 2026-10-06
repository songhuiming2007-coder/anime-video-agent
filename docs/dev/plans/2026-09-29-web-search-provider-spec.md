# Spec 15：web_search provider 可插拔（Exa 主 + Tavily 备）

日期：2026-09-29（v0.1 草案）；**2026-10-06 v0.2（作者修订，回应红队一轮 5🟡 + 6🔵，见「作者修订回应」）**；**2026-10-06 v0.3（作者修订，回应 D29-R2 的 4🟡 + 5🔵，见「作者修订回应（v0.3）」）**；状态：**v0.3 定向复核（2026-10-06，D29-R3）🟢 可动工**（R2-1～R2-4 核销；2🔵 施工时顺手改，见「定向复核」）→ 施工 D29-A（PR0 → PR1 → PR2 → PR3；**2026-10-06 PR0 ✅、PR1 ✅、PR2 ✅（门禁 4、5 终端侧过）、PR3 ✅（门禁 4、5 终端与桌面端均过）；施工完成，待 D29-B 独立评审；门禁 6（Spec 13 门禁 8 复跑）在评审后；门禁 7 人审：人物介绍类过、人物剖析类不足 → N59**，施工记录与偏差见 §12）（§11 五问、🟡-1 / 🟡-3 / 🟡-5 三处补充裁决、R2-9 / N50、Q5 措辞改为「替换」均已由人拍板）；对应 issues **D29**；上位：Spec 4（`network-tools-spec`，已归档）、Spec 13（门禁 8 复跑的前置）；相关 ADR：ADR-0021）

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

与 Spec 16（D30）的交叉（v0.3 改，R2-4）：代码面无冲突——Spec 16 给 `assert_egress_boundary` 加的是带默认值的关键字参数，本 spec 的调用点不传。**测试面有交集**：本 spec PR1 必须改写 T5a / T5b / T17，而它们是 Spec 16 冻结「不删、不弱化」的用例、也是其 MUT-D7 的指定杀手。改写规则见 §7 T-P13；PR1 落地时在改写后的 T5a / T17 上复跑 Spec 16 的 MUT-D7。

### 定向复审（2026-10-06，D29-R2；复审人未参与红队一轮，也未参与 v0.2 修订；只审文稿，未改代码）

**裁决：🟡 再修订**（4🟡 + 5🔵）。一轮 11 条里有 9 条落地忠实，**🟡-3 没有堵住**（按 v0.2 写的迁移动作，`web_search` 和 `web_fetch` 照样一起失效，见 R2-1）；🟡-1 落地了，但新增的桌面端注入面又引入两处缺口（R2-2、R2-3）。另外与 Spec 16 的交叉不像「谁后落地谁 rebase」那么轻：本 spec 的 PR1 必须改写 Spec 16 指定的杀手用例（R2-4）。四条 🟡 都只需改文稿，不涉及人的既有裁决。

**一、逐条核销（以正文为准，不以回应表为准）**

| 一轮编号 | 正文落点 | 核销 |
|---|---|---|
| 🟡-1 | §2.7、§4.3、§8 PR0/PR3、§9 门禁 5、TH-W1～W3、M18～M20 | ⚠️ **已落，但新增面有缺口**：跨家借 key 没有防线（R2-2）；TH-W2 杀不死 M18，失败面也没列全（R2-3） |
| 🟡-2 | §2.2 故障表 3xx 行、§2.5 ⑤、§4.1 `_provider_opener`、T-P14a/b、M15→T-P14a、M16→T-P14b | ✅ 本次在 127.0.0.1 复跑（scratchpad `d29r2/noredirect_probe.py`）：`redirect_request` 返回 `None` 的 opener 对 POST 收到 301/302/303/307/308 **一律以 `HTTPError` 浮出，跳转目标命中 0 次**，设计可行；杀手对得上 |
| 🟡-3 | §4.3、§8 PR1、§11 Q5 注 | ❌ **未堵住**：时点改对了，但「删掉 `web.local.json` 的 `search` 段」这个动作本身会让配置失效（R2-1）。另外顺序、窗口期、回滚都没写（R2-7） |
| 🟡-4 | §4.1 `secret_values` / `_redact_all`、§6、T-P8 fetch 腿、M21→T-P8 | ✅ fetch 四处都写到了；T-P8 fetch 腿要求「两家各配一个假 key」，按默认链 `exa_mcp` 无 key，用例须自己造 `exa_api`+`tavily` 的链，施工时注意 |
| 🟡-5 | §2.2 两行、§3 第三例、§10 RF-1、§11 Q3 注、T-P5a/b、M3a→T-P5a、M3b→T-P5b | ✅（「免 key 的家」的判据有歧义，见 R2-5） |
| 🔵-1 | §2.2、§8 PR0、T-P15、M17→T-P15 | ✅（「不含 query」的做法没写，见 R2-6） |
| 🔵-2 | §2.3、T-P16 | ✅ 正文已落；T-P16 没有对应变异（R2-8） |
| 🔵-3 | M13→T-P4、M14→T-P9 | ✅ 杀手对得上；但「该家 opener 零调用、汇总写缺哪个变量」只写在 M14 的括注里，T-P9 行本身没写，而且 T-P9 还留着「取决于 §11 Q2」的旧措辞（R2-8） |
| 🔵-4 | T-P8 `capsys` | ✅ |
| 🔵-5 | §8 PR0 ③ | ✅ |
| 🔵-6 | §9 门禁 4 / 5 | ✅ |

**二、发现表**

| 编号 | 指控 | 证据 | 建议 |
|---|---|---|---|
| 🟡 R2-1 | **按 §4.3 / §8 PR1 的迁移动作操作后，配置照样失效，🟡-3 的原指控会原样复发**。`web.local.json` 存在时是**整份取代** `web.json`，不是逐段合并。删掉它的 `search` 段以后，生效文件仍是 `web.local.json`，里面已经没有 `search`，`web.json` 的新默认链根本读不到。新 loader 按 §2.4 要求 `search.providers` 非空，于是返回 `None`，**`web_search` 和 `web_fetch` 一起不可用**。PR1 的「提交前用真实配置跑一次 `load_web_config` 与 `web_fetch`」会当场红，等于 spec 规定的迁移动作和它自己的验收门禁互相矛盾 | `web.py::load_web_config`：`cfg_file = local_cfg if local_cfg.exists() else web.json`；`web_crawl.py` / `web_browser.py::_active_config_path` 的注释写明「整份取代」（Spec 4 §3.1）。scratchpad 探针 `d29r2/probe_override.py`：本机 `web.local.json` 去掉 `search` 段后，生效文件 = `web.local.json`、无 `search` 段，现行 `load_web_config → None`。§1 自己也写了「整文件覆盖 `web.json`」 | 二选一写死：**(a，推荐)** 本机 `web.local.json` 的 `search` 段**替换为**新 schema 的链（与 `web.json` 默认链相同），不是删除，并注明此后两处要人手同步；(b) 改成逐段回落（local 缺某段就读 `web.json` 的那段），但这会改 Spec 4 §3.1 的覆盖语义，crawl / browser 两个加载器也要一起改，范围更大。T-P9 或 PR1 门禁加一条：用「本机形态」（有 `trusted_fake_ip_ranges`/`crawl`/`browser`、`search` 为新链）的临时 local 文件，`load_web_config` 与 `fetch_web` 均可用 |
| 🟡 R2-2 | **跨家借 key 没有防线，TH-W3 还把它写成了正常情形**。web 配置把 `tavily` 的 `api_key_env` 写成 LLM 的密钥名（本机是 `CPA_API_KEY`），这个名字能过 `KEY_ENV_NAME_RE`。host 会读出 LLM 密钥并注入（TH-W3：同名只读一次），core 再把它当 `Authorization: Bearer` 发给 `api.tavily.com`。`exa_api` 同理，会以 `x-api-key` 发给 `api.exa.ai`。终端里风险更大：shell 环境里任何 `*_TOKEN` / `*_API_KEY`（如 `GITHUB_TOKEN`）都能被 web 配置指名后外发。按 Q3 对方会返回 401，「直接失败」能暴露配错，但那时密钥已经发出去了 | `KEY_ENV_NAME_RE = /^[A-Z][A-Z0-9_]*_(API_KEY|KEY|TOKEN)$/` 对 `CPA_API_KEY`、`OPENAI_API_KEY`、`GITHUB_TOKEN` 都返回 true（node 实测）；§2.7 第 2 条「与 LLM 密钥同名时只读一次」；TH-W3。现行 `load_web_config` 对 `api_key_env` 只读环境变量，不校验名字 | 最小防线放在 **core**，终端和桌面端共用一处：`load_web_config` 与 `web_key_env_names` 要求每家的 `api_key_env` 以该家的族前缀开头（`exa_api` → `EXA_`，`tavily` → `TAVILY_`）并匹配同一形状正则；同时不得等于 `api_key_env_name()`（LLM）。不满足就判配置无效，消息指向该字段。T-P9 补一例：`tavily.api_key_env = "CPA_API_KEY"` → `None`，且 opener 零调用。加变异 M22「去掉族前缀校验」→ T-P9。TH-W3 改成纵深防御：core 不会再回答 LLM 名，host 照旧去重，但「同名」不再当作正常情形写进 spec |
| 🟡 R2-3 | **TH-W2 杀不死 M18，桌面端注入的失败面也没列全**。① `ready.llm` 来自 core 的 READY 事件（`protocol.py`：`"llm": "ok" if config is not None`），在 host 测试里 READY 是脚本写死的（TH-6 的 `sessionScript([READY(...), ...])`），断言「`ready.llm` 仍为 `ok`」恒真；host 侧的降级标记其实是 `s.keyProblem`。M18（web 失败时把 LLM 也判降级）因此存活；更隐蔽的回退「web 失败时整份 `extraEnv` 都不注入」同样存活。② 没写的失败路径：`PROBE_WEB_KEY_ENVS` 退出码非 0 / 超时 / 输出乱码；探针或钥匙串调用**抛异常**（若异常冒出 `resolveKey` 之外，会拖垮整个会话启动）；名字重复；名字超过 4 个。③ `PROBE_WEB_KEY_ENVS` 的 stdout 格式（如每行一个名字）没写死 | `host/sessions.ts`：`keyProblem` 来自 `resolveKey()`；`desktop/tests/host/sessions.test.ts` TH-6 用 `dump_env` 取子进程实际环境；`spawner.ts::PROBE_KEY_ENV` 是单值 stdout | TH-W2 改为：`keyProblem === null`，并且 `dump_env` 显示 LLM 名与值仍在、web 名不在（照 TH-6 的写法）。补上述失败例各一个，断言同上，会话照常启动。写死：web 密钥解析整体包在一个 try 里，任何异常都等同于「无 web 密钥」；stdout 每行一个名字；host 侧去重，超过 4 个只取前 4 个并记诊断（不含值）。加变异「web 失败时清空 `extraEnv`」→ TH-W2 |
| 🟡 R2-4 | **与 Spec 16 的交叉：本 spec 的 PR1 必须改写 Spec 16 指定的杀手用例，而且有一条会被静默弱化**。T5a / T5b / T17 是 Spec 16 冻结「不删、不弱化」的用例，也是它 MUT-D7（断言整个关掉）的指定杀手。它们用的都是旧 schema：`_make_config()`（全文件 23 处）构造旧的 `WebConfig` 字段，返回 DDG fixture，并 monkeypatch `_default_opener`。T-P13 只提到 T1 / T16。T5a 会大声报错（`TypeError`）；**T17 腿 ② 会静默变弱**：它在临时目录写的是旧 schema 的 `web.json`，新 loader 返回 `None`，`web_search` 以「配置无效」失败，`web_opener_calls == 0` 恒真，下一轮又因为 tool_call 参数里含受限串而 `blocked`，整条腿照样绿，却**不再证明** `search_web` 在发送前断言。另外 search 改走 `_provider_opener` 之后，patch `_default_opener` 拦不住搜索请求，变异下（M1 / MUT-D7）会真的出网，违反「零真实出网」 | `tests/test_agent_web.py`：T5a（`_make_config` + DDG fixture）、T5b 与 T17（`monkeypatch.setattr(web, "_default_opener", …)`；T17 写旧 schema 的 `web.json`）；Spec 16 §8「不删、不弱化 T5a/T5b/T17」、MUT-D7 → T5a、T17 | T-P13 扩到「所有依赖旧 search 配置或 DDG fixture 的用例」，点名 T5a / T5b / T17。改写**只换管道**（新 schema 配置、patch `_provider_opener`、provider fixture），断言强度不变。T17 腿 ② 额外断言 tool 结果里的错误是「拦截出网请求」，而不是「配置无效」。PR1 施工时先把 Spec 16 的 MUT-D7 在改写后的 T5a / T17 上复跑一次，确认仍被杀。§6「D30：无交集」改为写明这条测试面交集 |
| 🔵 R2-5 | **「免 key 的家」判据有歧义**：§2.2 写的是「本次请求未带 key，即 `exa_mcp`」，§2.4 又允许 `exa_mcp` 配 `api_key_env`（「允许省略」），而 key 怎么传给 `mcp.exa.ai` 没写。pi 的做法是放在 URL 查询串（`?exaApiKey=`），那样 key 会进 URL、错误消息，`add_unredirected_header` 也管不到 | §2.1 表、§2.4 字段表 | 写死 `exa_mcp` **不接受** `api_key_env`（出现即判无效）；有 key 就用 `exa_api`。这样「免 key 的家」就是 `exa_mcp`，没有歧义 |
| 🔵 R2-6 | 带内错误「不含 query」没写做法：Exa 的错误文案可能回显查询串 | §2.2、T-P15 | 写死：截断前先把原始 query 与归一后的 query 都替换为占位（如 `<query>`），再做 `_scrub` 和 `_redact_all` |
| 🔵 R2-7 | **PR1 本机文件的顺序、窗口期和回滚没写**。`web.local.json` 进不了提交，所谓「同一提交」实际上是两个动作。桌面端直接从工作树起 core（`pythonOf(repoRoot)`），施工期间工作树里是半改的代码，人如果同时在用 `ava`，search / fetch 会失效。如果之后 revert 了 PR1，本机文件已经是新 schema，旧代码会拒读，同样两个都失效 | §4.3、§8 PR1；`spawner.ts::sessionArgv` | 写死顺序：代码与测试全绿 → 备份本机 `web.local.json` 到 scratchpad → 人当场确认 → 按 R2-1 (a) 改写 → 立刻用真实配置跑 `load_web_config` 与 `web_fetch` → 提交（说明写清改了什么）。注明施工期间人不用 `ava`，回滚 PR1 时同时恢复备份。可选的结构性改进（不要求）：fetch 也改成和 crawl / browser 一样按段加载，以后 search 段出问题就不会连带 fetch |
| 🔵 R2-8 | 用例表细节：T-P9 行仍写「取决于 §11 Q2」（Q2 早已裁决）；M14 要求的「opener 零调用、汇总写缺哪个变量」没进 T-P9 行；T-P16 没有对应变异 | §7 | T-P9 行写全这些断言并删掉旧措辞；加 M23「各家请求条数 = limit（不 +1）」→ T-P16 |
| 🔵 R2-9 | **N50 面扩大（附加题 ③）：建议接受，PR3 不顺手改 `jobs.py`**，由人拍板 | 见下方「③ 的建议」 | — |

**③ 的建议（需人拍板）**：**接受，PR3 不在 `jobs.py` 剥离密钥变量**，N50 保持备忘，只在 N50 行加注扩面。理由有三条。① 收到这些变量的 ffmpeg / ffprobe / yt-dlp 与会话同用户运行，都不读这些名字，也没有把环境变量外发的通道。真正被攻破的同用户进程本来就能直接调 `/usr/bin/security find-generic-password -s ava`（用 `security add-generic-password` 建的条目默认信任 `security` 本身），终端的 key 也明文放在 shell profile 里。所以在 `jobs.py` 剥离变量，对攻击者能拿到什么几乎没有影响。② 剥了也封不住：`web_browser.py::_default_launch_fn`（Playwright Chromium）和 crawl4ai 都在**会话进程内**拉起子进程，不经 `jobs.py`，只改 `jobs.py` 会给人「已经收紧」的错觉。③ 改 `pipeline/` 会让 PR3 从 desktop 单侧变成跨两侧，测试面也会扩大。如果人希望收紧，建议在 N50 下另立一个小 spec，同时覆盖 `jobs.py` 和会话进程内的浏览器拉起，不要并进本 spec。**R2-2 的族前缀校验比剥离环境变量更值得做**：它防的是「密钥被发给错误的接收方」，这一面确实会出网。

**施工前人还要做什么**：PR0 照旧。注册 Tavily，**两处都放 key**：shell profile 里 `export TAVILY_API_KEY=…`，钥匙串里 `security add-generic-password -s ava -a TAVILY_API_KEY -w`。如果采纳 R2-2，变量名必须以 `TAVILY_` 开头（`TAVILY_API_KEY` 符合）。PR1 施工当天按 R2-7 的顺序当场确认本机 `web.local.json` 的改写。v0.3 修订后只需对 R2-1～R2-4 做定向复核。

### 作者修订回应（v0.3，2026-10-06；修订人 = v0.2 作者，未参与 D29-R2）

先核证据再改：R2-1 读 `web.py::load_web_config`（`cfg_file = local_cfg if local_cfg.exists() else web.json`，整份取代），属实；R2-2 读 §2.7 第 2 条与 TH-W3，确实把「与 LLM 同名」写成了正常情形，属实；R2-3 读 `desktop/tests/host/sessions.test.ts`（READY 由 `sessionScript([READY(...), …])` 写死，host 侧的降级标记是 `snap.keyProblem`）与 `protocol.py`（`"llm": "ok" if config is not None`），属实；R2-4 读 `tests/test_agent_web.py`：T5a 用 `_make_config()`（旧字段），T5b / T17 monkeypatch `_default_opener`，T17 在临时目录写旧 schema 的 `web.json`，属实。

| 编号 | 处置 | 改了哪里 |
|---|---|---|
| R2-1 | **采纳 (a)；人 2026-10-06 当场同意把 Q5 措辞从「删除 search 段」改为「替换为新链」** | §4.3、§8 PR1、§11 Q5 注：本机 `web.local.json` 的 `search` 段**替换为**与 `web.json` 默认链相同的新 schema 链，其余本机段不动，此后两处人手同步；§7 新增 T-P17（本机形态的临时 local 文件，`load_web_config` 与 `fetch_web` 均可用）。v0.2 写「删除」是我只想到「local 会遮住 web.json 的新链」，没想到删段后生效的仍是 local、读出来整份无效——属作者失误 |
| R2-2 | 采纳 | §2.4 字段表：`api_key_env` 须以该家族前缀开头（`exa_api` → `EXA_`，`tavily` → `TAVILY_`）、匹配 `KEY_ENV_NAME_RE` 同形正则、且不等于 LLM 的 `api_key_env_name()`，否则整份配置无效、消息指向该字段（core 一处，终端与桌面端共用）；§2.7 第 1、2 条改写（`web_key_env_names` 只回答过了校验的名字；host 去重降为纵深，不再当正常情形）；§7 T-P9 补借名例、TH-W3 改写、新增 M22 |
| R2-3 | 采纳 | §2.7 新增第 6 条（web 密钥解析整体 try、stdout 每行一个名字、host 去重、> 4 个只取前 4 并记诊断）；§7 TH-W2 改为断言 `keyProblem` 为空且 `dump_env` 里 LLM 名与值仍在、web 名不在；新增 TH-W4（探针非 0 / 超时 / 乱码 / 抛异常 / 名字重复 / 超过 4 个）；新增 M24「web 失败时清空 `extraEnv`」→ TH-W2 |
| R2-4 | 采纳 | 文首「与 Spec 16 的交叉」与 §6 D30 行改写为「测试面有交集」；§7 T-P13 扩到 T5a / T5b / T17 及全部 `_make_config` / `_default_opener` 依赖用例，**只换管道、断言一条不删**；T17 腿 ② 加断言「tool 结果是『拦截出网请求』而不是『配置无效』」；「搜索前先拦受限内容」由三条接力守：直调层 T-P3、`execute_tool` 层改写后的 T5b、完整循环层 T17 腿 ②；§8 PR1 加「在改写后的 T5a / T17 上复跑 Spec 16 MUT-D7」 |
| R2-5 | 采纳 | §2.4 字段表：`exa_mcp` **不接受** `api_key_env`（出现即判无效），有 key 就用 `exa_api`；「免 key 的家」由此唯一指 `exa_mcp` |
| R2-6 | 采纳 | §2.2：带内错误文案先把原始 query 与归一后 query 都替换为 `<query>`，再截断 200 字、`_scrub`、`_redact_all`；T-P15 补「文案回显 query」一例 |
| R2-7 | **部分采纳** | §8 PR1 写死本机文件的顺序、备份、回滚，并注明施工期间人不用 `ava`。「fetch 也改成按段加载」这条可选的结构性改进**不做**：它改 Spec 4 §3.1 的覆盖语义，R2-1 (a) 已经消除了连带失效的触发条件 |
| R2-8 | 采纳 | §7 T-P9 行删去「取决于 §11 Q2」，写全「该家 opener 零调用、汇总写缺哪个变量」；新增 M23「各家请求条数 = limit（不 +1）」→ T-P16 |
| R2-9 | **人 2026-10-06 裁决：接受，PR3 不改 `jobs.py`** | §2.7「放过了什么」写明裁决与理由；issues N50 行加注扩面（本提交） |

### 定向复核（2026-10-06，D29-R3；复核人 = D29-R2 复审人，未参与 v0.2 / v0.3 修订；只审文稿，未改代码）

**裁决：🟢 可动工**（R2-1～R2-4 全部核销；另 2🔵，施工时顺手改，不需再审）。逐条去正文核，不以回应表为准：

| 编号 | 正文落点 | 核销 |
|---|---|---|
| R2-1 | §4.3、§8 PR1 ①～⑥、§11 Q5 再补充、T-P17 | ✅ 动作已改为「替换」，给出了新链的完整 JSON，合 §2.4 新 schema；T-P17 正反两腿（替换后可读、删段后为 `None`）把理由钉成了用例；Q5 原文保留，补充注明了人同意改措辞 |
| R2-2 | §2.4 字段表名字校验 ①～③、§2.7 第 1/2 条、T-P9 借名腿、TH-W3、M22 | ✅ 族前缀按家写死，与 LLM 同名判无效，`web_key_env_names` 只回答过了校验的名字；TH-W3 降为「假 core 违约」下的纵深。另见 🔵 R3-1 |
| R2-3 | §2.7 第 6 条、TH-W2、TH-W4、M24 → TH-W2、M25 → TH-W4 | ✅ TH-W2 断言改落在 `keyProblem` 与 `dump_env` 上，M24（清空整份 `extraEnv`）会让 LLM 名从 `dump_env` 消失，能被杀；失败面六例齐全，stdout 格式写死 |
| R2-4 | 文首交叉段、§6、T-P13、M26、§8 PR1、§9 门禁 1/2 | ✅ T5a / T5b / T17 只换管道、断言不删；T17 腿 ② 加了「拦截出网请求而非配置无效」断言；接力三条（T-P3 / T5b / T17 腿 ②）点名；M26 写明必须由新断言杀，不能靠「配置无效」偶然带红；PR1 复跑 Spec 16 MUT-D7 写进门禁 |

| 编号 | 指控 | 证据 | 建议 |
|---|---|---|---|
| 🔵 R3-1 | **R2-2 的「不等于 LLM 名」要求 `web.py` 调 `llm.api_key_env_name()`，但 Spec 4 §5.1 冻结的 `web.py` 模块级项目导入只有 `paths` 与 `tools`**，正文没写这个依赖怎么取。顶层 `import pipeline.agent.llm` 会违反该白名单 | Spec 4 §5.1；`web.py` 现顶层只导 `pipeline.paths` 与 `pipeline.agent.tools`；`llm.py::api_key_env_name(root)` | 在 §4.1 写明：**函数内**延迟导入（`load_web_config` / `web_key_env_names` 内 `from pipeline.agent.llm import api_key_env_name`），传同一个 `root`；§5 依赖白名单不变，§5.2 纯洁性探针照跑 |
| 🔵 R3-2 | 两处措辞残留 | §9 门禁 4「（清理后的）本机配置」仍是 v0.2 的「删除」口径；TH-W2 的「web 名不合规」腿写「不注入该名字」，而 §2.7 第 6 条与 TH-W4 定的是「出现不合规行 → 整体无 web 密钥」 | 门禁 4 改为「（替换后的）」；TH-W2 删去「web 名不合规」腿（已由 TH-W4 覆盖），或改为「整体不注入」 |

**施工前人还要做的**：PR0 注册 Tavily，变量名 `TAVILY_API_KEY`，两处都放（shell profile 的 `export` 与钥匙串 `security add-generic-password -s ava -a TAVILY_API_KEY -w`）；PR1 当天按 §8 的 ①～⑥ 当场确认本机 `web.local.json` 的替换。

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
| 可落下一家 | HTTP 429 / 5xx、超时、DNS/连接失败、解析出 0 条、响应结构不符；**HTTP 3xx（不跟随，v0.2 🟡-2）**；**JSON-RPC 带内错误**（HTTP 200 下 `error` 或 `result.isError: true`，v0.2 🔵-1）；**免 key 的家（本次请求未带 key，即 `exa_mcp`）返回 401/403**（v0.2 🟡-5） | 记下失败原因，尝试链上下一家。带内错误的原因取其文案：先把原始 query 与归一后的 query 都替换为 `<query>`（v0.3 R2-6），再截断 200 字、`_scrub` + `_redact_all`；免 key 家 401/403 的原因固定写「Exa 免 key 入口被拒（HTTP 40x），需配 `EXA_API_KEY` 改用 `exa_api`」 |
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
| `providers[].api_key_env` | 指名环境变量。`exa_api` / `tavily` **必填**；**`exa_mcp` 不接受**（出现即判无效，有 key 就用 `exa_api`；v0.3 R2-5）。**名字校验（v0.3 R2-2，core 一处，终端与桌面端共用）**：① 以该家族前缀开头——`exa_api` → `EXA_`，`tavily` → `TAVILY_`；② 匹配与 Spec 10 §2.9 `KEY_ENV_NAME_RE` 同形的正则 `^[A-Z][A-Z0-9_]*_(API_KEY\|KEY\|TOKEN)$`；③ 不等于 LLM 配置的 `api_key_env_name()`。任一不满足 → 整份配置无效（`None`），消息指向该字段。理由：防止把 LLM 密钥或 shell 里的 `GITHUB_TOKEN` 之类借名发给检索服务——按 Q3 对方会回 401，但那时密钥已经发出去了 |
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
1. 新增 `PROBE_WEB_KEY_ENVS` 模板问 core：当前生效的 web 配置（与 `load_web_config` 共用同一段「local 优先」选择逻辑）链上各家的 `api_key_env` 叫什么。core 新增纯读函数 `web_key_env_names() -> list[str]`（去重、保持链序、配置无效时返回空清单；不读环境变量、不读值；**只会回答通过 §2.4 名字校验的名字**，所以不可能回答 LLM 的名字或族外名字，v0.3 R2-2）。host 不自己读 `config/`（与 C10-R2 同理，避免 TS 侧出现第二份规则）。新模板按 Spec 8 RF-3「加模板时必须复核」走一遍白名单。
2. 每个名字照 §2.9 第 2～4 步：`KEY_ENV_NAME_RE` 校验 → `KEYCHAIN_READ`（`security find-generic-password -s ava -a <名字> -w`）→ 值校验（可打印 ASCII、去恰好一个末尾换行）。host 仍对「与 LLM 名相同」做去重，但这只是纵深：core 的校验保证正常配置下不会出现同名（v0.3 R2-2，不再把同名当作正常情形）。
3. 通过的写进同一次 `SESSION_*` spawn 的 `env[名字]`；**任一 web 密钥失败都不影响 LLM 就绪**，只是不注入该名字，core 侧该家按 §11 Q2「未就绪」跳过。失败原因不进会话头的 LLM 降级文案，而由 core 的「无可用检索服务」/「全部检索服务失败」错误消息列出缺哪个变量，并附两处放法（见第 5 条）。
4. 密钥纪律与 §2.9 第 5 条完全相同：不进 argv、spawn 日志、`AVA_SPAWN` 行、诊断、发往 renderer 的任何消息、host 的 stdout/stderr；`KEYCHAIN_READ` 的结果只在 `host/secrets.ts` 内被读取（TG-12）。注入的键数上限 = 1（LLM）+ 链上家数（≤ 4）。
5. 两处放 key（§4.3、§8 PR0 写进人手步骤）：终端 `ava` 用 shell profile 的 `export TAVILY_API_KEY=…`；桌面端用 `security add-generic-password -s ava -a TAVILY_API_KEY -w`。core 的错误消息写成「`tavily` 需要环境变量 `TAVILY_API_KEY`（未设置；终端：在 shell profile 里 export；桌面端：`security add-generic-password -s ava -a TAVILY_API_KEY -w`）」。
6. **失败面（v0.3 R2-3）**：web 密钥解析（探针 + 逐个钥匙串读取）整体包在一个 try 里，任何异常都等同于「无 web 密钥」，绝不冒出 `resolveKey` 之外拖垮会话启动；`PROBE_WEB_KEY_ENVS` 的 stdout 写死为**每行一个名字**（UTF-8、无其他内容），退出码非 0、超时、出现不合 `KEY_ENV_NAME_RE` 的行 → 整体视为「无 web 密钥」并记诊断（不含值）；host 侧去重，超过 4 个只取前 4 个并记诊断。以上任何一种情况，LLM 密钥的注入与 `keyProblem` 都不受影响。

**放过了什么、代价**：会话进程多了至多 4 个密钥变量。它们与 LLM 密钥一样随 `jobs.py` 的 `env = dict(os.environ)` 继承到作业及其子孙（ffmpeg、yt-dlp 等），**N50 的面随之扩大**。**人 2026-10-06 裁决（R2-9）：接受，PR3 不在 `jobs.py` 剥离这些变量**，N50 保持备忘、行内加注扩面。理由：同用户进程本来就能直接调 `/usr/bin/security find-generic-password -s ava` 读到 key（`security add-generic-password` 建的条目默认信任 `security` 本身），终端的 key 也明文放在 shell profile 里；浏览器与 crawl4ai 从会话进程内直接拉起、不经 `jobs.py`，只改那里会造成「已收紧」的错觉。若将来要收紧，在 N50 下另立小 spec，同时覆盖 `jobs.py` 与会话进程内的拉起。TH-6「会话环境只比白名单多一个密钥键」的断言改为「只多出 LLM 名 + web 链上声明的名字，且每个都匹配 `KEY_ENV_NAME_RE`」。

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
- **本机 `config/agent/web.local.json`（gitignore，当前指向 so.com）**：整文件覆盖会让 `web.json` 的新默认链**失效**，且含旧字段将被判无效。**施工 PR 必须显式处理**：把其中的 `search` 段**替换为**与 `web.json` 默认链相同的新 schema 链（`{"timeout_s": 20, "providers": [{"name": "exa_mcp"}, {"name": "tavily", "api_key_env": "TAVILY_API_KEY"}]}`），其余本机段（`trusted_fake_ip_ranges/crawl/browser` 等）不动（v0.3 R2-1，人 2026-10-06 同意；**不是删除**——local 存在时整份取代 `web.json`，删段后生效的仍是 local，读出来整份无效）。此后 `web.json` 与本机 local 的 `search` 段要人手同步；此步动的是用户本机文件，**需人确认后再改**，不得在 PR 里悄悄改。**时点（v0.2 🟡-3，人 2026-10-06 裁决）**：与 `load_web_config` 的切换、`web.json` 默认链的替换**在 PR1 同一提交里完成**——`load_web_config` 是 search 与 fetch 共用的配置，任何「代码已切、配置未换」的中间态都会让 `web_search` **和 `web_fetch`** 一起不可用。本机文件不进 git，提交说明写明「本机 web.local.json 的 search 段由旧五字段（endpoint/query_param/api_key_env/api_key_param/timeout_s）替换为新链」。
- 环境变量（v0.2 🟡-1：两处都要放）：`TAVILY_API_KEY`——终端放 shell profile；桌面端放钥匙串 `security add-generic-password -s ava -a TAVILY_API_KEY -w`（§2.7）。不进仓库、不进 web.local.json（后者只写变量名）。可选 `EXA_API_KEY`，放法相同。

## 5. 依赖白名单

`web.py` 仍只用 stdlib（`urllib`/`json`/`re`）。SSE 解析手写（`data:` 行 + `json.loads`），不引入 `sseclient` 等。既有 §5.2 子进程纯洁性探针照跑。

## 6. 跨 Spec 边界

- **Spec 13（门禁 8）**：本 spec 落地并通过后，门禁 8 用同一冒烟任务复跑（一色彩羽 → `/character/26090`）。首跑判定 ① 当时被 D29 污染，复跑才有定性资格；**判据不变、不许调阈值**（Spec 13 §10 RF-1）。
- **Spec 4**：改 `search_web`（§4.1）；`fetch_web` 只有一处改动——脱敏从单 key `_redact_secret(…, cfg.api_key)` 换成链上全部 key 的 `_redact_all`（v0.2 🟡-4），其余（出网守卫、清洗、`_GuardedRedirectHandler`、依赖白名单）原样。
- **Spec 10（v0.2 🟡-1）**：§2.9「每次 spawn 只注入一个密钥」改为「LLM 密钥 + web 链上声明的密钥」，在 archive 原文加修订记录（日期、D29、人 2026-10-06 裁决），TH-6 断言口径随之调整（§2.7）。Spec 4 §3.1 配置 schema 表与 §3.2 示例值随本 spec 在归档文件加**修订注记**（不改正文）。
- **D30 / Spec 16（v0.3 改，R2-4）**：代码面无交集；**测试面有交集**——T5a / T5b / T17 是 Spec 16 冻结的用例与 MUT-D7 的杀手，本 spec PR1 只换它们的管道、不弱化断言（§7 T-P13），落地时复跑 MUT-D7。03.5 文本撞 egress 断言的问题本身归 Spec 16，不在此修。

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
| T-P9 | 配置校验 | 旧字段 → `None` 且消息指路；未知 provider 名、重名、空链、>4 家 → `None`；**借名**（v0.3 R2-2）：`tavily.api_key_env = "CPA_API_KEY"`（族外）、`= "TAVILY_X"`（形状不符）、与 LLM 名相同、`exa_mcp` 带 `api_key_env`（R2-5）各一例 → `None`，且各家 opener 零调用；指名 key 变量为空 → 该家未就绪而非整体无效（§11 Q2），**该家 opener 零调用**，汇总里写明缺哪个变量（v0.3 R2-8） |
| T-P10 | 归一化 | snippet 封顶 500；URL 去重；`javascript:` 被丢弃 |
| T-P11 | `_guard_url` 每家生效 | getaddrinfo 指向 `127.0.0.1` 的 provider 端点 → `PermissionError`，opener 零调用 |
| T-P12 | 退役守卫 | `web.py` 不再含 `_SearchResultParser` / `result__a`（静态守卫，防旧路径复活） |
| T-P13 | Spec 4 既有用例改写（v0.3 R2-4 扩面） | 范围：T1 / T16，以及**全部**依赖旧 search 配置或 DDG fixture 的用例——点名 **T5a / T5b / T17**，和 `tests/test_agent_web.py` 里所有 `_make_config()`、monkeypatch `_default_opener` 的搜索用例。规则：**只换管道**（新 schema 配置、patch `_provider_opener`、provider fixture），**断言一条不删、强度不变**；变异下也零真实出网（搜索走 `_provider_opener`，只 patch `_default_opener` 拦不住）。**T17 腿 ②** 临时目录里的旧 schema `web.json` 必须换成新 schema（做不到字面不改），并**加一条断言**：tool 结果里的错误是「拦截出网请求」而不是「配置无效」——否则新 loader 拒读旧配置时整条腿仍绿，却不再证明发送前断言。「搜索前先拦受限内容」由三条接力守：直调层 T-P3、`execute_tool` 层改写后的 T5b、完整循环层 T17 腿 ② |
| T-P14a | **密钥头不随重定向**（v0.2 🟡-2） | 三个适配器构造出的请求：密钥只在 `request.unredirected_hdrs` 里，`request.headers` 里没有 |
| T-P14b | **provider 不跟随重定向** | `_provider_opener` 对 127.0.0.1 上的两台临时服务器（A 回 302 指向 B）发请求 → 以 `HTTPError` 302 浮出、B 命中计数为 0；`search_web` 把它归为可落下一家，原因含「重定向（未跟随）」（此用例只连回环地址，直接测 opener，不经 `_guard_url`） |
| T-P15 | **MCP 带内错误**（v0.2 🔵-1） | `exa_mcp` fake 返回 HTTP 200 + `error`（及 `result.isError: true` 各一例）→ 落到 `tavily`；全链失败时原因含带内错误文案（≤ 200 字、不含 query、不含 key），而**不是**「解析为空」 |
| T-P17 | **本机形态配置**（v0.3 R2-1） | 临时目录里放一份「本机形态」的 `web.local.json`（含 `trusted_fake_ip_ranges` / `crawl` / `browser`，`search` 为新链），同时放一份 `web.json`：`load_web_config` 读出的是 local 的链，`fetch_web` 可用（fake opener）；把 local 的 `search` 段删掉 → `load_web_config` 为 `None`（钉住「不许删、只许替换」的理由） |
| T-P16 | `limit` 映射与 `truncated`（v0.2 🔵-2） | 请求体里的条数参数 = limit + 1；返回 limit + 1 条 → 截到 limit、`truncated == true`；返回 ≤ limit 条 → `false` |
| TH-W1（desktop，PR3） | 桌面端 web 密钥注入 | 假 core 回答 `PROBE_WEB_KEY_ENVS = ["TAVILY_API_KEY"]`、钥匙串桩有条目 → 会话子进程 env 恰多出 LLM 名与 `TAVILY_API_KEY` 两个键；`spawnLog`、诊断、发往 renderer 的消息里无标记值（TH-6 同口径） |
| TH-W2（desktop，PR3；v0.3 R2-3 改；R3-2 施工时删「web 名不合规」腿，已由 TH-W4 覆盖） | web 密钥失败不拖累 LLM | 钥匙串 44 / 值非 ASCII 各一例 → 会话照常启动；**断言落在 host 能决定的量上**（READY 是脚本写死的，`ready.llm` 恒为 `ok`，不能当证据）：快照 `keyProblem` 为空，`dump_env`（照 TH-6 写法）里 LLM 名与值仍在、该 web 名不在 |
| TH-W3（desktop，PR3；v0.3 R2-2 改） | 同名去重（纵深） | 假 core 违约回答与 LLM 相同的名字 → `KEYCHAIN_READ` 只调用一次、env 只有一个该键。正常配置下 core 不会回答这个名字（T-P9 守），本例只验 host 的纵深去重 |
| TH-W4（desktop，PR3；v0.3 R2-3 新增） | 注入失败面 | `PROBE_WEB_KEY_ENVS` 退出码非 0 / 超时 / 输出含不合规行 / 探针或钥匙串调用抛异常 / 名字重复 / 超过 4 个，各一例：会话照常启动，`keyProblem` 为空，LLM 名与值在 `dump_env` 里；前四例 env 里无任何 web 名；重复只注入一次；超过 4 个只注入前 4 个；诊断不含任何密钥值 |

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
| M22 去掉族前缀校验（v0.3 R2-2） | T-P9（借名腿） |
| M23 各家请求条数 = limit（不 +1，v0.3 R2-8） | T-P16 |
| M24 web 密钥任一失败时清空整份 `extraEnv`（desktop，v0.3 R2-3） | TH-W2 |
| M25 web 密钥解析不包 try，异常冒出 `resolveKey`（desktop，v0.3 R2-3） | TH-W4（抛异常腿） |
| M26 删除 `search_web` 发送前的 `assert_egress_boundary` 调用（Spec 4 MUT-2 在新管道上的对应，v0.3 R2-4） | T-P3，以及 T17 腿 ②——后者须由新增的「tool 结果是『拦截出网请求』」断言杀死，而不是被「配置无效」偶然带红 |

## 8. 施工与 PR 划分

- **PR0（人 + 施工 session，先于代码）**：① 人注册 Tavily，**两处放 key**：shell profile `export TAVILY_API_KEY=…`（终端）与 `security add-generic-password -s ava -a TAVILY_API_KEY -w`（桌面端，v0.2 🟡-1）；可选 Exa key 同样两处；② 用真实 key 各打一发，把 `exa_mcp` / `exa_api` / `tavily` 的**原始响应**存成 fixture（尽量含一个 429 / 无结果 / **JSON-RPC 带内错误**样本，v0.2 🔵-1），并核实各家单次条数上限 ≥ 11（§2.3）；**未拿到真实响应前不写解析器**；③ **fixture 卫生（v0.2 🔵-5）**：查询用中性词（不含个人信息）；入库前 `grep` 确认不含 key 值、邮箱、账号名等个人信息。
- **PR1（core，v0.2 🟡-3 改）**：`web.py` 适配器 + 新 `search_web` + `load_web_config` + `_redact_all` + `_provider_opener` + `web_key_env_names` + T-P1～T-P17 + T-P13 范围内的 T5a / T5b / T17 改写 + 变异 M1～M17、M21～M23、M26 实跑；改写后的 T5a / T17 上复跑 **Spec 16 的 MUT-D7**（断言整个关掉），确认仍被杀（R2-4）。**同一提交**内替换 `config/agent/web.json` 默认链，并按下面的顺序处理本机 `web.local.json`（v0.3 R2-1 / R2-7）：① 代码与测试全绿；② 把本机 `web.local.json` 备份到 scratchpad；③ 人当场确认；④ 把其 `search` 段**替换**为新链（§4.3，不是删除）；⑤ 立刻用真实配置跑 `load_web_config` 与 `web_fetch`，两者都可用才继续；⑥ 提交，说明写清本机文件改了什么。施工期间人不用 `ava`（桌面端直接从工作树起 core）；若之后回滚 PR1，须同时用备份恢复本机文件，否则旧代码会拒读新 schema。Spec 4 归档文件加修订注记。
- **PR2（core）**：`tools.py` 的 `web_search` description；终端真机跑门禁 4、5。
- **PR3（desktop，v0.2 🟡-1）**：`PROBE_WEB_KEY_ENVS` 模板 + `host/secrets.ts` 扩展 + `sessions.ts` 注入 + TH-6 口径调整 + TH-W1～W4 + 变异 M18～M20、M24、M25；Spec 10 §2.9 archive 原文加修订记录；N50 行加注；`release-build` 后在**桌面端会话**里跑门禁 4、5。PR3 落地后跑 Spec 13 门禁 8 冒烟；issues D29 迁 archive。

## 9. 验收门禁

1. T-P1～T-P17 与 TH-W1～W4 全绿；改写后的 T5a / T5b / T17 全绿且断言未减；全量 pytest 全绿（缺盘才红的既有用例除外，需在结论里写明）；
2. 变异 M1～M26 全部被**指定用例**杀死；Spec 16 MUT-D7 在改写后的 T5a / T17 上仍被杀死，回填矩阵；desktop 侧另跑 `npx vitest run`、`npx tsc --noEmit`、全量 e2e；
3. `web.py` 静态守卫：无 `_SearchResultParser`、无第三方 import；
4. `web.json` 与（替换后的）本机配置均能被 `load_web_config` 读出，`web_search` 真实调一次拿到 ≥3 条含正文摘录的结果；`web_fetch` 同时可用；**终端与桌面端各跑一次**，记录各自出网路径（显式代理 / TUN / 系统代理，v0.2 🔵-6）；
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
- **Q5 ✅**：本机 `config/agent/web.local.json` 的 `search` 段**由施工 session 在 PR2 删除**（保留 `trusted_fake_ip_ranges/crawl/browser` 等本机项）；须在代码切换的**同一步**做（先删会让现行 `web_search` 因缺 `search` 段而失效，后删则新代码拒读旧字段），并在提交说明里写明删了什么。**2026-10-06 人补充裁决（红队 🟡-3）**：「同一步」= **PR1 的同一提交**（连同 `web.json` 默认链），不留到 PR2（§8）。**2026-10-06 人再次补充（D29-R2 R2-1）**：动作由「删除 `search` 段」改为「把 `search` 段**替换**为新链」——local 存在时整份取代 `web.json`，删段会让 search 与 fetch 一起失效。
- **Q7 ✅（2026-10-06，D29-R2 R2-9）**：N50 扩面接受，PR3 不改 `jobs.py`（理由见 §2.7）。
- **Q6 ✅（2026-10-06，红队 🟡-1）**：桌面端会话也要能用备家——改 Spec 10 §2.9，host 按 web 配置链上各家的 `api_key_env` 从钥匙串读取并注入（§2.7、§8 PR3）；不接受「备家只在终端可用」。

**Q4 展开**：snippet 是 `web_search` 每条结果里给模型看的摘录，进入会话历史后**每一轮后续 LLM 调用都会重发**。上限一头决定上下文成本（默认 5 条 × 500 字 ≈ 2500 字；放到 1000 则翻倍且每轮都背着），另一头决定模型能否仅凭摘录判断「这条要不要 `web_fetch` 全文」——摘录只需够辨认页面，全文由 `web_fetch` 取。太小（如 150）辨认不出，会多花 fetch 次数；太大则挤占上下文、也放大提示注入的面（RF-3）。

## 12. 施工记录（D29-A；施工方未参与本 spec 的任何一轮写作与审查）

### 12.1 PR0（2026-10-06）

- 人注册 Tavily（变量名 `TAVILY_API_KEY`），两处放好：`~/.zshrc` 的 `export` 与钥匙串 `-s ava -a TAVILY_API_KEY`；两份值一致（只比对不打印）。`EXA_API_KEY` 人裁决**先不申请**。
- 真实响应（代理下，中性查询词 `Iroha Isshiki character profile`）入库 `tests/fixtures/web_search/`：`exa_mcp_ok.sse.txt`（`numResults=11` → 11 块）、`exa_mcp_iserror_validation.sse.txt` / `exa_mcp_iserror_unknown_tool.sse.txt`（HTTP 200 + `result.isError: true`）、`exa_mcp_rpc_error.sse.txt`（HTTP 200 + 顶层 `error`）、`tavily_ok.json`（`max_results=11` → 11 条）、`tavily_401.json`（假 key）。两家单次上限 ≥ 11 已核实（§2.3）。未取到 429 样本（需刻意刷限流，不做）。卫生：`grep` 确认不含 key 值、邮箱与本人姓名/账号。
- Tavily 响应实测键：`query / follow_up_questions / answer / images / results[].{url,title,content,score,raw_content,id} / response_time / request_id`，与 §2.1 的官方文档形态一致（解析只用 `url/title/content`）。

### 12.2 施工偏差（如实登记）

1. **`exa_mcp` 多传固定 `objective`**（人 2026-10-06 裁决）：PR0 实测 `tools/list` 已把 `objective` 标为 `web_search_exa` 的**必填**参数（2026-09-29 立文时没有；服务端当下对缺省仍宽容）。改为每次固定传常量 `EXA_MCP_OBJECTIVE`（不含任何用户内容，无新的外发面）；egress 断言仍只对 query。用例 `test_exa_mcp_request_shape` 钉住。
2. **`exa_api` 不注册**（人 2026-10-06 裁决「先不申请 Exa key」）：没有真实响应不写解析器（§8 PR0）。`_PROVIDERS` 只有 `exa_mcp` / `tavily`；链里写 `exa_api` 会被判为未知服务（T-P9 `exa_api_not_registered` 腿）。原写给 `exa_api` 的用例腿（T-P2 解析、T-P5a 链例、T-P8 双 key、T-P14a 三适配器）改由 `tavily` 承担，fetch 双 key 腿直接构造两个 `secret_values`。免 key 入口被拒的原因文案相应改为「…需申请 EXA_API_KEY 并补 exa_api 适配器（Spec 15 §12）」。将来补 `exa_api`：取一份真实响应 → 注册表加一行（族前缀 `EXA_`）→ 补 T-P2 解析腿。
3. **T6a 的 search 腿**：§2.2 把超时 / 断网改为「可落下一家」，全链失败汇总成 `ValueError`，原断言 `pytest.raises(URLError/TimeoutError/gaierror)` 与 spec 语义冲突。改为 `pytest.raises(ValueError, match="全部检索服务失败.*<异常类型名>")`，仍逐类钉住原因；fetch 腿不动。T6b（`type(exc).__name__ in error`）原样通过。
4. **T5b / T6b 改用临时仓库根**（`_make_web_root`）：原用例读真实仓库的 `config/`，本机 `web.local.json` 仍是旧 schema 时会红、替换后才绿——测试结论不该取决于本机文件。只换 `root`，断言不动。
5. **T17 腿 ② 钉公网 DNS**：M26 实跑时发现腿 ② 在断言关掉后会让 `_guard_url` 做真实 DNS 解析（本机 TUN 得到 198.18.x）。补 `_pin_public_dns`，变异下零真实解析。
6. `_redact_all` 签名取 `secrets: tuple[str, ...]`（调用处传 `cfg.secret_values`），而非 §4.1 写的 `cfg`；语义相同。
7. 配置无效时的错误消息带上原因（`_invalid_config_error`）：旧字段 → 「…含旧字段 […]，请改用 search.providers」；借名 / 族外 / 形状不符 → 指向该字段。前缀「缺少或无效的 config/agent/web.json」保留（既有用例按它匹配）。
8. 「响应结构不符」除 `ValueError` 外也接 `KeyError / TypeError / AttributeError`（解析器访问缺键、opener 返回非响应对象）；`PermissionError` 在 `OSError` 分支之前显式重抛，`_guard_url` / egress 命中绝不被当成「连接失败」落下一家。

### 12.3 PR1 变异回填（2026-10-06；`PYTHONDONTWRITEBYTECODE=1`，每条植入 → 指定用例 → 还原，md5 对拍一致；判杀只认指定 testcase 的 `<failure>`）

| 变异 | 指定杀手 | 结果 |
|---|---|---|
| M1 断言移到发送后 | T-P3 | KILLED（`calls == {exa_mcp: 1}` ≠ 0） |
| M2 只断言第一家 | T-P3 第二家腿 | KILLED（DID NOT RAISE PermissionError） |
| M3a 配 key 家 401/403 落下一家 | T-P5a | KILLED（DID NOT RAISE） |
| M3b 免 key 家 401/403 直接失败 | T-P5b | KILLED（抛「exa_mcp 的密钥被拒」） |
| M4 落下一家前重试同一家 | T-P4 | KILLED（`exa_mcp: 2` ≠ 1） |
| M5 0 条返回空 list | T-P7 | KILLED（DID NOT RAISE） |
| M6 汇总只留最后一家 | T-P6 | KILLED（整句对拍） |
| M7 `provider` 回端点 URL | T-P1、T-P4 | KILLED（两者皆红） |
| M8 snippet 不封顶 | T-P10 | KILLED |
| M9 去掉 `_guard_url` | T-P11 | KILLED（DID NOT RAISE） |
| M10 错误消息拼入密钥 | T-P8 | KILLED（`test-key-0000` 出现在消息里） |
| M11 旧字段不判无效 | T-P9 `legacy_mixed_in`、旧字段消息腿 | KILLED |
| M12 SSE 取最后一条 | T-P1 SSE 腿 | KILLED |
| M13 可落下一家的故障直接抛 | T-P4 | KILLED |
| M14 未就绪的家照发 | T-P9 未就绪腿 | KILLED |
| M15 密钥头改回 `headers=` | T-P14a | KILLED |
| M16 opener 跟随重定向 | T-P14b | KILLED（DID NOT RAISE HTTPError） |
| M17 带内错误按解析为空 | T-P15 | KILLED（原因成了「解析为空」） |
| M21 fetch 只脱第一个 key | T-P8 fetch 腿 | KILLED（`test-key-1111` 漏出） |
| M22 去族前缀校验 | T-P9 `foreign_family` | KILLED |
| M23 请求条数 = limit | T-P16 | KILLED |
| M26 删发送前断言 | T-P3；T17 腿 ② | KILLED（T-P3 DID NOT RAISE；T17 `len(web_opener_calls) == 0` 断言——请求真的发到了 opener 桩） |
| Spec 16 MUT-D7 断言整个关掉 | T5a、T17 | T5a KILLED（DID NOT RAISE）。**T17 不计**：仍死在腿 ①（Spec 4 冻结、本 PR 未动）的 `TypeError`，属逃逸，腿 ② 走不到——与 Spec 16 §12 登记一致 |
| 用例自检：T17 腿 ② 写回旧 schema `web.json` | T17 腿 ② 新断言 | 红在 `'拦截出网请求' in '…含旧字段…'`——新断言能单独分辨「配置无效」 |

M18～M20、M24、M25 属 PR3（desktop）。

### 12.4 PR2（2026-10-06）

- **补一处结构**：PR1 里适配器的请求地址取自模块常量，而 egress 断言与 `_guard_url` 校验的是注册表的 `endpoint`——两处写同一个地址，改一处就会出现「校验 A、发往 B」。PR2 改为主流程把 `provider.endpoint` 传给 `build`，端点只在注册表写一处；新增用例 `test_sent_url_is_the_checked_endpoint`（把两家端点改到别处 → 实际发送地址与 `_guard_url` 校验地址逐一相等），变异 M-EP（tavily 的 `build` 写死地址）被它杀死；M15 / M23 在新签名上复跑仍被杀死。
- `tools.py` 的 `web_search` description 改为三句：按序尝试检索服务（`provider` 写明哪家答的）、摘要只够辨认页面全文用 `web_fetch`、全部失败逐家报错。参数 schema 与注册表键集不动。
- **门禁 4（终端）✅**：`zsh -ic` 起的终端环境（`TAVILY_API_KEY` 来自 shell profile），出网路径 = **显式代理**（`HTTP(S)_PROXY` 环境变量；`_guard_url` 本地解析得 TUN fake-ip 198.18.x，经本机 `trusted_fake_ip_ranges` 放行，RF-5 预期无需改，属实）。经 `execute_tool` 真实路径、仓库真实配置（替换后的本机 `web.local.json`）：「一色彩羽 角色介绍」「Iroha Isshiki wiki」「夏目友人帐 斑 原作 设定」各 5 条、5 条都带摘录，均由 `exa_mcp` 答；`web_fetch("https://www.python.org/")` 200。桌面端那一半随 PR3。
- **门禁 5（终端）✅**：进程内把 `exa_mcp` 端点临时指到 `https://mcp.exa.ai/no-such-endpoint`（真机，非 fake）→ `exa_mcp: HTTP 404` 落下一家，`provider == "tavily"`、5 条结果。桌面端那一半随 PR3。
- **门禁 7（人审检索质量）**：上述三条查询的结果清单已交人看，待人判。
- RF-4 用量观察登记为 issues **N58**。

### 12.5 PR3（2026-10-06，desktop）

- `spawner.ts` 新模板 `PROBE_WEB_KEY_ENVS`（短命令，argv 逐位钉住；Spec 8 RF-3 复核记在 Spec 8 §3.4 修订注记）；`secrets.ts::resolveWebKeys`（整体 try；探针非 0 / 超时 / 含不合规行 / 抛异常 → 整体无 web 密钥；去重、跳过 LLM 同名、> 4 取前 4；单个名字失败只跳过该名；诊断只写名字与退出码）；`sessions.ts` 在 LLM 密钥之后合并 web 密钥（`keyProblem` 只由 LLM 决定）；`service.ts` 新 dep `resolveSessionWebKeys`；常量 `WEB_KEY_MAX = 4`。
- 夹具：假钥匙串脚本加按账户名模式（`keys.strict` + `key.<名字>`），原「任何账户读同一个 key」行为不变。
- 用例：TH-W1、TH-W2（钥匙串 44 / 值非 ASCII 两腿；R3-2：「web 名不合规」腿已删，由 TH-W4 覆盖）、TH-W3、TH-W4（探针非 0 / 超时 / 含不合规行 / 探针抛异常 / 钥匙串抛异常 / 名字重复 / 超过 4 个）；`spawner.test.ts` 钉 `PROBE_WEB_KEY_ENVS` argv。TH-W* 的 `conv.send` 以断言「会话照常启动」收口，异常逃逸转成普通断言失败。
- 变异（植入 → vitest 指定用例 → 还原，md5 对拍一致；全部是断言失败）：

| 变异 | 指定杀手 | 结果 |
|---|---|---|
| M18 web 失败时把 LLM 也判降级 | TH-W2 | KILLED（两腿：`keyProblem` 非 null） |
| M19 不合规的名字也注入 | TH-W4「输出含不合规行」（R3-2 后由它守，原表写 TH-W2） | KILLED（env 多出 `TAVILY_API_KEY`） |
| M20 密钥写进 spawn 日志 | TH-W1 | KILLED（spawn 日志含标记串） |
| M24 web 失败时清空整份 `extraEnv` | TH-W2 | KILLED（两腿：LLM 值消失） |
| M25 web 解析不包 try | TH-W4「探针调用抛异常」 | KILLED（`conv.send 抛出：probe boom` ≠ `started`） |

- 打包：`npm run release-build` @ `36fd369`，`build-info.json` 的 `gitHead` = HEAD、`desktopDirty=false`；`npx vitest run e2e-packaged` 11 条全绿。
- **门禁 4（桌面端）✅**（2026-10-06，人在打包版上手点，施工方只被动读会话记录）：临时仓库副本（scratchpad，非 git，故会话头有 `code_freeze` 警告，与本项无关）+ 测试期 `D29-桌面端检索验收`（02 工序，`creative` scope），会话 `ce47cd0b` 里 `web_search("一色彩羽 角色介绍")` → `session.jsonl` 工具结果原文 `"provider": "exa_mcp"`，前 5 条与终端一致（含 `bangumi.tv/character/26090`）。出网路径 = **TUN**（Finder 启动的 app 无代理环境变量，`childEnv` 白名单也不传）。插曲：人第一次把消息发进了左栏「选题」（`idea` scope 无联网工具，即 D42），换到期内会话后正常。
- **门禁 5（桌面端）✅**：只在临时副本里把 `exa_mcp` 端点改为 `https://mcp.exa.ai/no-such-endpoint`，人点「结束会话」后在同一期发同一句 → 新会话 `fe0cf0cf`，工具结果 `"provider": "tavily"`、5 条结果。证明钥匙串里的 `TAVILY_API_KEY` 经 PR3 注入到了桌面端会话进程，且主家故障时真的落到备家。
- **门禁 7（人审检索质量）**：人 2026-10-06 原话「还行吧，5条其实远远不够，"人物介绍"的话还可以，如果要做人物剖析这样的搜索是有问题的」。判为**人物介绍类过、人物剖析类不足**；剖析类的取证（limit=10 两家全部截断、约一半仍是百科、两家重叠不到一半）与三个候选方向登记为 issues **N59**，涉及 Spec 4 冻结的 limit 上限与本 spec「每家至多 1 次」的取舍，须人排序后另立文，不在本 spec 修。
- 验证：`npx tsc --noEmit` 过；`npx vitest run` 37 文件 422 条全绿；全量 `npx playwright test` 115 passed / 2 skipped（两个跳过与 D41 时相同，是环境变量门控的截图用例）；`uv run pytest` 全绿。

## 13. 增补：N59 条数上限（2026-10-06，人裁决；施工方 = D29-A）

- **起因**：门禁 7 人审「5 条远远不够；人物介绍还可以，人物剖析有问题」；取证见 issues N59。
- **人裁决（2026-10-06）**：① 提高条数——**默认 20、上限 20**；② **不写**调研策略文案：「调研策略非常多……人物剖析、剧情伏笔等等等，完全数不完，这种决策不是 harness 该做的，是 LLM 该做的决策，你不要限制它轮数和上限就好了」；③ 两家合并模式**搁置**。
- **轮数现状（读码，未改）**：`run_tool_loop` 没有硬性轮数上限；只有 Spec 9 的防失控三件套——人中断、本轮完全相同参数判重（换说法不受影响）、每 50 次回复或工具执行问一次人（检查点，人可继续）。三者都不限制调研本身，不动。
- **改动**：`web.py` `SEARCH_DEFAULT_LIMIT = SEARCH_MAX_LIMIT = 20`（常量旁写依据与代价）；`tools.py` `web_search` 的 `limit` 描述「默认 20，上限 20」。请求条数仍为 limit + 1 = 21：Exa 免 key 入口实测 20 / 21 / 30 条照给；Tavily 文档写 `max_results` 上限 20，实测 21 不报错（广查询也只回 8～10 条）——若日后 Tavily 拒收 21，错误会以「tavily: HTTP 400」如实出现在汇总里，不静默。Spec 4 §3.2 archive 加修订注记。
- **用例**：`test_n59_default_and_cap_are_20`（直调层默认 / clamp / 工具描述）、`test_n59_tool_layer_passes_default_20`（工具层不带 limit 与超大 limit 都传 20）；T-P16 参数加 20；T-P4 的条数断言随默认值改为 fixture 全部 11 条。顺带：`test_exa_mcp_request_shape` 钉住 `User-Agent: ava-agent/1.0`——2026-10-06 探针实测不带 UA（urllib 默认 `Python-urllib/x`）时 Exa 免 key 入口回 Cloudflare 1010 / 403，生产代码一直带着，此前没有用例守。
- **变异**（同 §12.3 口径）：M-L1 上限改回 10 → `test_n59_default_and_cap_are_20` 与 T-P16[20] 杀；M-L2 默认改回 5 → 两条 N59 用例杀；M-L3 工具层写死 10 → `test_n59_tool_layer_passes_default_20` 杀；M-UA 删 UA 头 → `test_exa_mcp_request_shape` 杀（断言失败）；M23 复跑仍杀。
- **真机**：终端 `execute_tool("web_search", {"query": "春物 一色彩羽 角色剖析 长评"})`（不带 limit）→ `exa_mcp` 20 条、`truncated=true`、snippet 合计 9,818 字。桌面端无需重建：会话进程跑的是仓库里的 `pipeline/`。

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
