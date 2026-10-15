# D96 browser 工具换 Camoufox（施工图）

> 状态：**已施工·机检通过**（2026-10-15 人说开工；真实冒烟三项见 §九）；待人：AVA 会话里用一次 browser、跑一次 `login` 入口。实测依据见 [2026-10-15-browser-login-options.md](2026-10-15-browser-login-options.md)（下称「实测记录」）。

## 一、目标与对应 issue

- 对应 issues **D96**；关联 ADR-0021（独立持久 profile、「不碰 cookie 本体」）、D95。
- 现状：`browser` 工具用 Playwright 启动隔离 Chromium（`data/browser-profile`），Google 拒绝登录，YouTube 不登录过不了 bot 检查；yt-dlp 读这份 Chromium 配置的 cookie 要钥匙串密钥。
- 目标：`browser` 工具改用 Camoufox（Firefox 内核）持久配置 `data/browser-profile-firefox`，yt-dlp 用 `firefox:<配置目录>` 读同一份登录态。三件事实测都已通过：Google 登录不被拦、重开保持登录、yt-dlp 直接读（不经钥匙串、不导出 cookie 本体、与主力 Chrome 完全隔离）。其他 7 个站（niconico / bilibili / X / pixiv / 知乎 / 小红书 / Bangumi）登录与持久性也已逐站验证（实测记录 §四之三）。
- 另加一个小入口 `login`：登录态过期、遇到 Cloudflare 验证时，人一条命令开窗口补登录或点验证。现在只能跑 docs 里的附件脚本。

## 二、前置条件

- 基线 `uv run pytest` 全绿（2026-10-15 为 3217 条）。
- `data/browser-profile-firefox` 已由人登录（2026-10-15 已完成，见实测记录）。
- 本机缓存已有：Camoufox 浏览器本体 `~/Library/Caches/camoufox/browsers/official/152.0.4-beta.30-…`；playwright 1.62 对应的 `~/Library/Caches/ms-playwright/chromium-1234`（crawl4ai 用）。施工不下载任何浏览器；若 `uv sync` 后发现缺本体，停下报告，由人执行一次 `uv run camoufox fetch`。
- 依赖装进项目虚拟环境（uv，改 `pyproject.toml` / `uv.lock`），不装全局。

## 三、改动清单（锚点引原文，不给行号）

| 文件 | 锚点 | 改动 |
|---|---|---|
| `pyproject.toml` | `browser = [` 下的 `"playwright>=1.63.0",` | 改为 `"playwright>=1.62.0,<1.63"`、加 `"camoufox==0.5.6"`；上方注释的钉版依据同步改写（理由见 §四）；人工安装说明里的 `uv run playwright install chromium` 保留（crawl4ai 仍用），加一行 `uv run camoufox fetch`（只在缺本体时） |
| `uv.lock` | — | `uv lock` 重新生成 |
| `pipeline/agent/web_browser.py` | `def _default_launch_fn(*, user_data_dir: Path, headed: bool) -> Any:` | `pw = sync_playwright().start()` 保留；`pw.chromium.launch_persistent_context(user_data_dir=..., headless=not bool(headed))` 换成 `camoufox.sync_api.NewBrowser(pw, persistent_context=True, user_data_dir=str(user_data_dir), headless=not bool(headed), os="macos")`。返回值仍是 Playwright `BrowserContext`，`_PlaywrightSessionAdapter` 不改。`from camoufox.sync_api import NewBrowser` 与 playwright 一起放进函数级延迟 import（纪律 1 不变） |
| 同上 | `binary_hint = "（浏览器二进制缺失，请人工执行一次 \`uv run playwright install chromium\`，严禁自动下载）"` | 改为提示 `uv run camoufox fetch`。缺本体时 camoufox 实际抛什么异常、消息里有什么字样，施工时先实测再写判断（不凭猜） |
| 同上 | 模块 docstring「playwright 只在 _default_launch_fn / _pw_error_types 内函数级延迟 import」 | 补「camoufox 同」 |
| 同上 | `def cookies_spec_for(` 里 `if not (profile / "Default" / "Cookies").is_file():` 与 `return f"chromium:{profile}"` | 改查 `profile / "cookies.sqlite"`、返回 `f"firefox:{profile}"`；报错文案「先用 browser 工具（headed）登录该站一次」改指向下面的 `login` 入口 |
| 同上 | 新函数 `login_main(argv)` + `if __name__ == "__main__":` | `uv run python -m pipeline.agent.web_browser login [URL ...]`：读 `load_browser_section()` 的 `profile_dir`、过 `_assert_profile_isolation`，用同一个 `_default_launch_fn(headed=True)` 开窗口，打开给定 URL（不给就开空白页），等人关掉窗口后退出。不读、不打印任何 cookie。AVA 会话里的 browser 工具正开着这份配置时，Firefox 的配置锁会让第二个进程起不来：捕获后提示「先结束 AVA 里的浏览器会话」 |
| `config/agent/web.json` | `"profile_dir": "data/browser-profile",` 与 `"cookies_for": []` | `profile_dir` 改 `data/browser-profile-firefox`；`cookies_for` 改 `["youtube.com", "youtu.be", "bilibili.com", "x.com", "twitter.com"]`（逐个依据见 §四） |
| `config/agent/web.local.json`（gitignore，本机覆盖文件） | `"profile_dir": "data/browser-profile",` | 同改 `data/browser-profile-firefox`。本机实际生效的是这份（`_active_config_path` 优先 local），不改的话上面全白改。它不含密钥 |
| `pipeline/acquire.py` | docstring「（如 `chromium:data/browser-profile`）」 | 改为 `firefox:data/browser-profile-firefox` |
| `docs/dev/adr/0021-network-and-asset-tools-internalization.md` | 文首已有的 ADR-0032 修订注之后 | 加修订注（2026-10-15，D96）：第三级浏览器由 Playwright Chromium 改为 Camoufox，配置目录 `data/browser-profile-firefox`；yt-dlp 读 `cookies.sqlite`，仍是「整个目录交给 yt-dlp，AVA 不碰 cookie 本体」；理由一句话指向实测记录 |
| `docs/dev/plans/2026-10-15-browser-login-options/` 附件 | `multi_login.py` 文档串 | 加一句：它按 cookie 名判「已登录」，对 pixiv / note / 小红书不可靠（实测记录 §四之三），以施工后的 `login` 入口为准 |
| 旧配置 `data/browser-profile` | — | **不动**。要不要删由人定（删除属红线） |

## 四、新常量 / 新判据的取值理由

- **`playwright>=1.62.0,<1.63`**：camoufox 0.5.6 与最新稳定版 0.5.8 都声明 `playwright<1.63`；crawl4ai 0.9.4 只要 `>=1.49`。原先钉 1.63.0 的理由是 `launch_persistent_context` + `context.on("close")` 的契约稳定，这两项在 1.62.0 上已经实测用过：实测记录 E6–E8 和第二轮全部跑在 1.62.0。1.62 对应的 Chromium（1234）本机已缓存，crawl4ai 不需要重新下浏览器。
- **`camoufox==0.5.6` 钉死**：全部实测用的就是这个版本，本机缓存的浏览器本体也是配它的。0.5.8 没测过，升级另起一项。
- **`os="macos"`**：宿主就是 macOS，指纹报的系统与真实字体、渲染一致；E6（Google 登录不被拦）就是在这个参数下测的。不传时 Camoufox 会随机挑系统，没测过。
- **`cookies_for` 每个域的依据**（「未实测不写判断」：只放测出有用的）：
  - `youtube.com`、`youtu.be`：不带 cookie 被 bot 检查拦，带登录配置拿到 1080p（E1 / E8）。`youtu.be` 是同一视频的短链，主机名不同，要单列。
  - `bilibili.com`：老视频 BV1gx411D7x2 不登录最高 720p，登录后多出 1080p。
  - `x.com`、`twitter.com`：推文直出的视频登录前后相同；但推文里嵌的是 YouTube 时，yt-dlp 会跟到 YouTube，用的仍是这条命令的 cookie——不带 cookie 被 bot 检查拦，带上拿到 1080p（ORICON 那条推文）。`twitter.com` 是旧域名，链接里仍常见。
  - **不放** `nicovideo.jp`：4 条样本登录前后格式完全相同。**不放** `b23.tv`（bilibili 短链）：没测；遇到再加。
- **`login` 入口复用 `_default_launch_fn`**：浏览器参数只在一处，人补登录的窗口和 AVA 用的完全是同一个浏览器、同一套指纹，不会出现「补登录时能过、AVA 用时被拦」。

## 五、测试要求

期望值先在实现上跑通再写断言；写完做变异检验。

- `_default_launch_fn`：注入假的 `camoufox.sync_api` / `playwright.sync_api` 模块，断言 `NewBrowser` 收到 `persistent_context=True`、`user_data_dir`、`headless=not headed`、`os="macos"`；返回的 context 被包进 `_PlaywrightSessionAdapter`；启动抛错时 `pw.stop()` 被调用。
- 导入失败：`camoufox` 被置为 `None` 时与现有 playwright 同样抛 `ValueError`（「导入失败」），不漏出 `ImportError`（改现有 `test_agent_crawl_browser.py` 腿①，补 camoufox 一条）。
- 缺本体提示：按施工时实测到的 camoufox 异常消息造假异常，断言提示里有 `uv run camoufox fetch`。
- `cookies_spec_for`：夹具 `_cookies_root` 改造为写 `cookies.sqlite`；有库 → `firefox:` 开头、含配置目录；只有旧 `Default/Cookies` 没有 `cookies.sqlite` → 照样报「还没登录」；子域名 / 后缀撞名 / 空清单三条原样保留。
- 真实 `config/agent/web.json`：`profile_dir` 过隔离守卫；`cookies_for` 恰为 §四 那 5 个域（防止随手加域不写依据）。
- `login` 入口：用假 `_default_launch_fn` 断言 headed=True、打开了给定 URL、等到窗口关闭才返回；配置目录越界被守卫拦；配置锁冲突 → 提示「先结束 AVA 里的浏览器会话」且退出码非 0。
- 变异（预期全部被抓）：`os` 参数去掉；`headless` 取反；`persistent_context` 去掉；`cookies.sqlite` 改回 `Default/Cookies`；`firefox:` 改回 `chromium:`；`cookies_for` 少一个域；`login` 不过隔离守卫；`login` 不等窗口关闭就退出。

## 六、验证命令

```bash
uv sync --extra browser --extra crawl --extra apple --extra dev   # uv sync 会卸掉没列出的 extras，按本机实际装的一并列
uv run pytest
cd desktop && npx tsc --noEmit -p . && npx vitest run              # 动了 config/agent/，按家规一律跑
# 真实冒烟（施工后由我跑，人不用在场）：
#   1) 用项目虚拟环境跑 persist_check（实测记录 §四之三 的同一判据），7 个站仍为已登录
#   2) uv run python -m pipeline.acquire probe <YouTube 候选号>：命令行里带 firefox:…，不再报 bot 检查
#   3) crawl 工具抓一页公开网页，确认降到 playwright 1.62 后 crawl4ai 照常工作
# 人验收：AVA 会话里让 browser 工具打开 youtube.com，看到是登录状态；跑一次 login 入口，窗口能开、关窗后命令退出
```

## 七、明确不做

- 不保留 Chromium 后备、不加「engine」配置开关：切换后旧路径没有已知用处，回退靠 git。
- 不删旧配置 `data/browser-profile`（由人定）。
- 不处理 Fandom 的 Cloudflare 时好时坏（遇到时用 `login` 入口人点一次验证，实测可行）；不处理 note 注册被拒；不测 niconico 付费会员。
- 不升级 camoufox 到 0.5.8、不改 yt-dlp 版本。
- 不做「登录态过期自动检测」：yt-dlp 的报错原文已会透出（`_LOGIN_HINTS`），过期了人跑 `login` 补。

## 八、完成判定

- [x] `uv run pytest` 全绿；vitest、tsc 全绿；变异 15/16（见 §九）
- [x] 真实冒烟：persist_check 7 站、crawl 抓页通过；YouTube probe 不再报 bot 检查，但另因 `--no-cache-dir` 失败（既有问题，见 §九，待人定）
- [x] ADR-0021 修订注、issues D96 行、plans/README 状态列已更新
- [ ] 待人：AVA 会话里 browser 打开 youtube.com 是登录状态；`login` 入口开关窗口正常

## 九、施工记录（2026-10-15）

**施工补记（与上文不一致处，按实测处理）**：

1. **camoufox 启动时会自动下载浏览器**：`camoufox.pkgman.camoufox_path(download_if_missing=True)` 是默认值，`NewBrowser` 经 `launch_path()` 走到它，找不到本体就从 GitHub 下载。违背「浏览器二进制绝不自动下载」。改为启动前先 `camoufox_path(download_if_missing=False)` 预检，缺了抛 `ValueError` 并提示 `uv run camoufox fetch`（常量 `CAMOUFOX_FETCH_HINT`）。缺本体时的原话是「… is not installed. Please run \`camoufox fetch\` to install.」（`CamoufoxNotInstalled`，`FileNotFoundError` 子类）。
2. **`web.local.json` 里原本没有 `cookies_for`**：local 存在时整份取代 `web.json`，只改 `profile_dir` 的话本机一个域都不挂。施工时把 5 个域一并写进 local。
3. **配置被占用**：实测第二个进程开同一配置，Camoufox 报「A copy of Camoufox is already open. Only one copy of Camoufox can be open at a time.」。启动器按 `already open`（常量 `_PROFILE_IN_USE`）补一句「先关掉它，或结束 AVA 里的浏览器会话」；`login` 入口打印后退出码 1。
4. `login` 用到的「新标签页打开」「等窗口关闭」做成 `_PlaywrightSessionAdapter.open_tab` / `wait_closed` 两个方法，入口不碰适配器内部的 context。

**依赖**：`uv lock` 后 playwright 1.63.0 → 1.62.0，新增 camoufox 0.5.6 及其依赖（browserforge、screeninfo、ua-parser 等），没有卸掉别的包；camoufox 直接找到本机缓存的浏览器本体，没有下载。

**测试**：`tests/test_agent_crawl_browser.py` 夹具改写 `cookies.sqlite`，新增 11 条（只有旧 Chromium 库不算登录过、真实 web.json、启动器 ×5、login ×4 等）。变异 15/16：去掉 `os`、`headless` 取反、去掉 `persistent_context`、预检允许下载、不预检、cookie 库改回 `Default/Cookies`、`firefox:` 改回 `chromium:`、`cookies_for` 少一个域、login 不等关窗 / 用无界面 / 一站失败就中断 / 启动失败返回 0 / 不关会话、占用判据失效、启动失败不停 playwright——全部被抓；存活 1 处是 login 里的隔离守卫：`load_browser_section` 读配置时已经守过一次，越界在那一步就抛，去掉 login 里这一次测不出差别（等价变异，保留作双保险）。

**真实冒烟**：

- 项目虚拟环境跑 persist_check：niconico / bilibili / X / pixiv / 知乎 / 小红书 / Bangumi 7 站仍为已登录。
- 走 `_default_launch_fn(headed=False)`：bilibili `isLogin` 为真；YouTube 首页没有登录按钮。
- `python -m pipeline.acquire probe 6`（bilibili）：命令行带 `firefox:…`，可解析、最高 1080p。
- `probe 9,13`（YouTube）：**不再报 bot 检查**，但报「The page needs to be reloaded」，前面有「Signature solving failed / n challenge solving failed」。查明原因是探针命令带的 `--no-cache-dir`：同一条视频、同一份 cookie，去掉这个参数就能解析（1080p），加上就失败；yt-dlp 解 YouTube 签名要用自己缓存目录里的求解脚本（`~/.cache/yt-dlp/challenge-solver/lib.json`，2026-09-06 留下的），禁缓存就用不上。下载命令 `fetch_argv` 不带这个参数，不受影响。这是探针原有的问题，以前被 bot 检查挡在前面没露出来，**不在本图范围，未改，待人定**。
- crawl（crawl4ai 0.9.4，playwright 1.62.0）抓萌娘百科一页正常。
- 观察：手动连测时有一次（启动器冒烟刚打开过 youtube.com 之后）不带 `--no-cache-dir` 也失败了一轮，几分钟后同样的命令连续两轮都成功；原因未查。
