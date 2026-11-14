# 接入 chrome-devtools-mcp：主力 Chrome 后台无扰检索与交互

> 状态：**已施工·机检与真机连线探针通过**（待人：实际做片会话中走一遍场景 A/B 验收）

## 1. 目标与对应 issue

- **目标**：在不破坏现有 `web_fetch` → `crawl` → Camoufox `browser` 抓取链路、`cookies_for` 导出与高 DPI 截图隔离的前提下，为 AVA 新增独立工具 `chrome`，通过标准 `chrome-devtools-mcp --autoConnect` 连接用户日常运行的 Google Chrome 144+，同时在代码机制层严格落实「后台不打扰」与「主力账号防越界」。
- **相关文档**：
  - `docs/dev/adr/0021-network-and-asset-tools-internalization.md`（独立 profile 边界、凭证不入上下文、公开网页链顺序）
  - `docs/dev/adr/0030-approval-danger-only.md`（仅硬危险动作弹审批卡，减少日常审批摩擦）
  - `docs/dev/adr/0032-tool-table-soft-constraint.md`（工具表 `adr` 字段必填，禁止动态展开 MCP 工具污染上下文）
  - `docs/dev/plans/2026-10-15-fact-gate-spec.md`（D99 `fetch_log` 留档池与 `fact_add` 校验）
  - `docs/dev/plans/2026-10-20-screenshot-asset-options.md`（D122 `screenshot.save_screenshot` 入库门禁）

### 为什么新增独立工具 `chrome` 而不合并进 `browser`

1. **后端隔离**：`browser` 面向独立 Firefox/Camoufox profile（`data/browser-profile-firefox`），承担 `cookies_for`（给 `yt-dlp` 使用，避免触发 macOS Keychain 弹窗）及 D122/D139 固定视口与高 DPI 重排截图（`SHOT_PREFS`/`SHOT_SCALES`）；`chrome` 面向主力 Chrome CDP 会话（`--autoConnect`），绝不触碰独立 profile 或篡改窗口缩放。
2. **上下文预算与安全边界清晰**：仅暴露精简裁剪后的 13 个 `action`，避免将 `chrome-devtools-mcp` 全量 33 个原始工具直接灌入模型工具表。

### 人裁决的关键取舍（2026-11-14）

1. **工具形态**：新增独立工具 `chrome`。
2. **审批策略**：不默认弹审批卡；遵循 ADR-0030 仅在硬危险动作时弹卡（`chrome_danger_tags`）。
3. **`screenshot` 行为**：默认仅保存到缓存目录 `data/_cache/chrome-shots/`（不可用时回退系统临时目录）并返回 `_image` 供模型同轮直接 `vision.attach_images` 读图，不污染 `data/library/incoming/` 与候选账本；显式传 `save_evidence=true`（附带 `reason`、可选 `expect_text`/`name`）时复用 `screenshot.save_screenshot()` 走 D122/D141 OCR 与入库留档流程。
4. **`extract_text` 留档口径**：复用 `fetch_log.KINDS` 既有 `"browser"` 类型，写入 `fetch-log` 并回写 `cache_path`/`snapshot_id`，与 `fact_add` / `acquire_propose(found_on)` 无缝衔接。
5. **首批优先级场景**：
   - 场景 A：02 写稿考据 / 补料阶段遇到强风控或登录态站点（X/Twitter、知乎评论与展开、B 站动态/专栏等），通过后台新建 Tab、点击展开/读取 Network XHR JSON、`extract_text` 留档后交 `fact_add` 引用。
   - 场景 B：协助处理用户在主力 Chrome 中已打开或已手动过验证码的标签页（经人工审批确认后允许只读或交互）。

---

## 2. 前置条件

- Python 3.12+（仓库现有 `uv run pytest` 与 `ruff` 静态检查绿灯）。
- 本机需具备 Node.js / `npx` 与 Google Chrome 144+（且用户开启 `chrome://inspect/#remote-debugging`），方可通过真实 `--autoConnect` 建连；离线单元测试以内存假 MCP stdio 进程或 mock 客户端覆盖完整协议与护栏，不依赖本机常驻 Chrome 进程。
- 不引入重型异步 MCP SDK 依赖：基于标准库 `subprocess` / `json` / `threading` 实现 MCP stdio JSON-RPC 2.0 客户端，避免与 `TurnInterrupt` 及现有同步工具循环冲突。

---

## 3. 改动清单

| 文件 | 改动锚点与内容 |
|---|---|
| `docs/dev/plans/2026-11-14-chrome-devtools-mcp-spec.md` | 本规格文档（ADR-0032 `adr` 字段指向此文件） |
| `docs/dev/plans/README.md` | 在活跃方案表登记本方案 |
| `config/agent/web.json` | 新增 `"chrome"` 配置段：`command`、`args`、`timeout_s`、`max_chars`、`sensitive_hosts` |
| `config/agent/tools.json` | `"tools"` 列表追加 `"chrome"` |
| `config/agent/scopes/production.md` | 抓取升级链末尾补充 `chrome`（仅登录态/交互/已打开页兜底） |
| `desktop/src/shared/toolView.ts` | `TOOL_CATEGORY` 追加 `chrome: "网页"` |
| `pipeline/agent/web_chrome.py` | 新建：配置加载（兼容已有 `web.local.json` 缺省 `"chrome"` 键回退 `web.json` 默认段）、零三方依赖 MCP stdio JSON-RPC 客户端、会话生命周期与 `atexit` 清理自有 Tab、13 个受控动作与全部硬护栏 |
| `pipeline/agent/tools.py` | `_WEB_TOOLS` 加入 `"chrome"`、`TOOL_SCHEMAS["chrome"]`（含 `adr` 指向本方案）、`_tool_chrome` 对接 `_keep_record(..., "browser", ...)` 与 `screenshot.save_screenshot`、`_TOOL_IMPLS` 注册 |
| `pipeline/agent/status_card.py` | 新增 `chrome_danger_tags`（识别 `[JS 执行]`、`[用户既有标签页]`、`[高敏站点交互]`）及 `render_approval_card` 卡片字段渲染 |
| `pipeline/agent/session.py` | `CRITICAL_TOOLS` 加入 `"chrome"`；`review_tool_call` 前置校验并按 `chrome_danger_tags` 决定免卡放行或弹审批卡；`tool_summary` 摘要展示 |
| `tests/test_agent_chrome.py` | 新建：覆盖 MCP stdio 协议、焦点隔离、Tab 归属隔离、SSRF/出网断言、敏感请求头剥离与正文脱敏、`extract_text` 进 `fetch-log`、`screenshot` 双模式、`chrome_danger_tags` 与 `review_tool_call`、`atexit` 清理及断连诚实报错 |
| `tests/test_agent_{web,tools,memory,crawl_browser,protocol,llm_tiering}.py` | 若测试内建硬编码 `tools.json` 全表夹具，同步补入 `"chrome"` |

---

## 4. 机制护栏与新常量 / 新判据的取值理由

### 4.1 代码层硬护栏

1. **后台焦点隔离**：`TOOL_SCHEMAS["chrome"]` 不暴露 `background` 与 `bringToFront` 参数。`open` 调用 `new_page` 时硬编码 `background=True`；切换上下文调用 `select_page` 时硬编码不传 `bringToFront=True`（显式 `False`）。
2. **Tab 归属隔离**：进程内维护 `_OWNED_PAGES: set[int]`（仅记录本次由 `open` 创建的 `page_id`）。
   - `close` 只允许关闭 `_OWNED_PAGES` 中的页面；对非自有页直接抛出 `PermissionError` 拒绝（不经审批卡放行）。
   - 对非自有页执行读取或交互（`navigate` / `snapshot` / `extract_text` / `screenshot` / `click` / `fill` / `wait_for` / `evaluate`）必须显式传入 `allow_existing=true`，并在 `review_tool_call` 中触发 `[用户既有标签页]` 审批卡。
   - `atexit` 退出清理仅遍历关闭 `_OWNED_PAGES`，绝不关闭用户日常页，并带超时收尾子进程。
3. **URL、出网与凭证隔离**：
   - `open`、`navigate` 前均经 `web._guard_url` 校验禁止内网 / `file://` / `chrome://` / `chrome-extension://`，且经 `tools.assert_egress_boundary` 阻止敏感词/密文随 URL 外泄；导航完成后对实际落地 URL 二次复核 `_guard_url`。
   - MCP 子进程环境变量复用 `web_browser.browser_env()`，剔除环境密钥（N50）。
   - `get_network` 在返回前结构化与正则双层剥离 `Cookie`、`Set-Cookie`、`Authorization`、`Proxy-Authorization`、`X-Auth-Token`、`X-Access-Token`、`X-Session-Id`、`X-Amz-Security-Token`、`X-CSRF-Token`、`X-XSRF-Token` 等请求/响应头及 URL 敏感 query 参数（并默认启用 `--redactNetworkHeaders`），并将全部输出送入 `web._scrub` + `tools.scrub_secrets` 脱敏。
   - `screenshot` 先将文件落盘到系统临时目录（`tempfile.gettempdir()`，满足 `chrome-devtools-mcp` 默认 `workspace roots` 限制）再读回转存到 `data/_cache/chrome-shots/`（或按 D122/D141 `save_evidence=True` 经 OCR 核验后入 `incoming`）。
4. **分级审批（不默认弹卡，ADR-0030）**：
   - `chrome_danger_tags` 检查以下 3 类硬危险，任一命中即弹卡 (`ask`)，否则免卡 (`allow`)：
     - `[JS 执行]`：`action == "evaluate"`（任意页内 JS 执行，支持按当前页或同域按规则收口为每次询问或可配置策略；`evaluate` 固定不写入 `session_allow`）。
     - `[用户既有标签页]`：`allow_existing=True` 且 `page_id` 不在 `_OWNED_PAGES`（跨回话或用户日常页操作；允许对同一 `page_id` 写入 `session_allow` 以免连点多步连续打扰）。
     - `[高敏站点交互]`：在 `click` / `fill` / `evaluate` 时，页表登记真实 URL 或显式 `url` 的主机命中 `config/agent/web.json` 的 `chrome.sensitive_hosts`（默认覆盖主流登录、支付、发帖/创作者后台域名与路径关键词；`args.url` 不得遮蔽真实页面 URL），不进 `session_allow`。

### 4.2 新常量取值理由

| 常量 | 值 | 依据 |
|---|---|---|
| `_DEFAULT_CHROME_CFG["command"]` | `"npx"` | macOS/本机无需全局安装 npm 包，直接复用与桌面端 MCP 一致的 `npx` 启动链 |
| `_DEFAULT_CHROME_CFG["args"]` | `["-y", "chrome-devtools-mcp@latest", "--autoConnect", "--redactNetworkHeaders"]` | 与本机已验证的 `chrome-devtools-mcp --autoConnect` 通道一致，`-y` 避免非交互子进程卡在首次确认提示，`--redactNetworkHeaders` 开启服务端敏感头脱敏 |
| `_DEFAULT_CHROME_CFG["timeout_s"]` | `45` | 初次 `npx` 解析 + Chrome CDP 握手及网页加载留足余量，同时低于模型单步工具超时上限（对齐 `crawl` `timeout_s=45`） |
| `_DEFAULT_CHROME_CFG["max_chars"]` | `20000` | 对齐 `crawl` 单次最大正文字数（`20000`），避免 snapshot / 长列表与 Network JSON 撑爆对话上下文 |
| `_DEFAULT_SENSITIVE_HOSTS` | 登录/支付/发帖高敏域名与路径后缀集合 | 涵盖主账号登录鉴权（`accounts.google.com`、`passport.bilibili.com`、`login.*`）、支付收银（`pay.*`、`checkout.*`）、创作者发布后台（`studio.youtube.com`、`member.bilibili.com`）与社交写接口高敏域，防止未审触发不可逆写操作 |
| `_SCREENSHOT_MAX_WAIT_S` / 清理超时 `_CLOSE_TIMEOUT_S` | `3.0` | `atexit` 关闭多个后台 Tab 时单个页最多等 3s，防止 Chrome 挂起拖死 Python 退出路径 |
| `_CACHE_SHOT_DIR` | `data/_cache/chrome-shots` | 位于 `data/_cache/` 下，既隔离于 `data/library/incoming/` 避免污染素材台账与候选池，又能在跨轮调试时保留路径索引 |

---

## 5. 测试要求

- 先跑通真实/模拟实现取得基准输出，再落断言；补充关键护栏的手动变异自检（例如：删掉 `background=True` 强制、删掉 `_OWNED_PAGES` 校验、删掉 `get_network` Cookie 剥离、删掉 `chrome_danger_tags` 对 `evaluate`/既有页的判断、删掉 `_guard_url`），确保测试能捕获全部变异。
- 关键测试用例覆盖：`tests/test_agent_chrome.py`、现有静态测试（`test_static.py`、`test_agent_session_static.py`、`test_agent_context_claims.py`、`test_agent_web.py`）、`desktop/tests/shared/toolView.test.ts`。

---

## 6. 验证命令

```bash
uv run pytest tests/test_agent_chrome.py tests/test_static.py tests/test_agent_session_static.py tests/test_agent_web.py tests/test_agent_tools.py
uv run pytest
cd desktop && npm test && npx tsc --noEmit
```

---

## 7. 明确不做的事（范围闸门）

- 不删除或改动 Camoufox `browser` 及其 `data/browser-profile-firefox`、`cookies_for`、`SHOT_PREFS`/`SHOT_SCALES`。
- 不改动 `fetch_log.KINDS` 枚举值（直接复用 `"browser"`）。
- 不新增第三方 Python 依赖到 `pyproject.toml`。
- 不在未获用户明示时修改 `config/agent/web.local.json` 或对用户 Chrome 执行持久配置写入。

---

## 8. 完成判定

- [x] `docs/dev/plans/2026-11-14-chrome-devtools-mcp-spec.md` 与 `docs/dev/plans/README.md` 已落盘且被 `TOOL_SCHEMAS["chrome"]["adr"]` 引用并通过 `test_every_tool_has_existing_adr`
- [x] `chrome` 已注册到 `config/agent/tools.json`、`config/agent/web.json`、`desktop/src/shared/toolView.ts`、`pipeline/agent/tools.py`、`pipeline/agent/session.py`
- [x] `new_page` 强制 `background=True`、`select_page` 禁止 `bringToFront=True`、`close` 拒绝非自有页、非自有页需 `allow_existing=True` + 人审卡、`get_network` 剥离敏感头、`extract_text` 入 `fetch-log` 均已实现并有测试保护（变异 5/5 KILLED）
- [x] `uv run pytest` 全仓绿灯、`tests/test_static.py` / `tests/test_verify_mutations.py` 及 `desktop` `vitest` (642) 与 `tsc --noEmit` 绿灯
- [x] 使用真实 `npx -y chrome-devtools-mcp@latest --autoConnect --redactNetworkHeaders` 完成全链路真实探针测试（`list_pages`、`open` 后台页、`snapshot`、`extract_text`、`screenshot` 普通与 `save_evidence=True` + OCR、`wait_for`、`list_network`、`get_network`、`close`）
