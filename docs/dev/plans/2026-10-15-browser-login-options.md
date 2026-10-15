# D96 浏览器登录态：实测记录与待人测项（施工图之前）

日期：2026-10-15　状态：**Google / YouTube 三项已过（人登录、我测）；人要求扩到其他站点，暂停，等人回来逐站实测后再写施工图**
附件：[`2026-10-15-browser-login-options/`](2026-10-15-browser-login-options/)（`camoufox_login.py`、`cdp_probe.py`，路径全取参数）
关联：D96、ADR-0021（独立持久 profile、「不碰 cookie 本体」）、D95

## 一、问题与人定的两个方向

AVA 的 `browser` 工具用 Playwright 启动隔离 Chromium（`data/browser-profile`），Google 拒绝登录（「This browser or app may not be secure」）；出口是机房 VPS，YouTube 不登录就要求「Sign in to confirm you're not a bot」。人定的两个主考查方向：① 给 AVA 专用的开源 agent 浏览器；② 人启动独立配置的浏览器、开调试端口，AVA 经 CDP 接上。

## 二、已实测（2026-10-15，人不在场能做的部分）

| # | 测什么 | 结果 |
|---|---|---|
| E1 | 当前出口不带 cookies，yt-dlp 2026.08.19 解析 YouTube | **复现**：`Sign in to confirm you're not a bot`。出口 AS47583 Hostinger（US） |
| E2 | Camoufox 0.5.6（本机已全局安装，无需新装）持久配置打开 YouTube | 写下 8 个 YouTube cookie；`navigator.webdriver = false`；Google 登录页正常显示邮箱框 |
| E3 | yt-dlp `--cookies-from-browser firefox:<Camoufox 配置目录>` | **能直接读**：`Extracted 10 cookies from firefox`，读的是配置目录里的 `cookies.sqlite`。Firefox 系 cookies 不经 macOS 钥匙串加密，不会弹钥匙串框，也不需要主力 Chrome 的密钥 |
| E3′ | 同上，但配置未登录 | 仍报 bot 检查——预期内，未登录的访客 cookie 不够，必须人登录后再测 |
| E4 | 真 Chrome 155 以独立 `--user-data-dir` + `--remote-debugging-port` 启动，Playwright `connect_over_cdp` 接入 | 接入成功；`navigator.webdriver = false`（不是自动化启动，没有 `--enable-automation`）；Google 登录页正常 |
| E5 | 本机 `agent-browser` 0.37.1 能力 | Chromium 自动化；登录态靠 `--restore` / `--state` 存成 cookies + localStorage 的 JSON 文件；能 `--cdp` 接已开的浏览器 |

**人在场实测（2026-10-15 午间，人只输账号密码，其余由我跑）**：

| # | 测什么 | 结果 |
|---|---|---|
| E6 | Camoufox（`os="macos"` 固定指纹系统）有界面窗口登录 Google（YouTube 服务） | **通过**：全程没有出现「This browser or app may not be secure」；12:21 打开、12:22 登录完成 |
| E7 | 无界面重开同一配置 `data/browser-profile-firefox` | **持久**：YouTube 右上角是头像、无「登录」按钮，`LOGIN_INFO` / `SID` / `__Secure-3PSID` 都在 |
| E8 | yt-dlp `--cookies-from-browser firefox:data/browser-profile-firefox` 解析 supercell「君の知らない物語」 | **通过 bot 检查**：拿到 27 个格式，最高 1080p（E1 同一条不带 cookies 被拒） |

**人追加的范围（2026-10-15）**：「不能只考虑 Google 系，许多网站都要考虑」。施工图之前要逐站实测，候选（按本项目抓取与考据的实际来源）：ニコニコ（niconico 账号，yt-dlp `niconico`）、bilibili、X / Twitter、pixiv、note.com 付费 / 会员文、知乎、小红书、Bangumi、Fandom / 萌娘百科（Cloudflare）。每站测三件事：Camoufox 里能否登录、重开是否保持、需要下载的站 yt-dlp 能否用同一份配置。人回来后再做（人 2026-10-15 说「等我回来再做」）。

**没测、为什么**：

- **真登录**（Google 账号、密码、二次验证）：必须人在场。「浏览器不安全」的拦截通常出现在输完邮箱之后，只看登录页不能下结论。
- **② 的 yt-dlp 读 cookies**：`--cookies-from-browser chrome:<独立配置>` 要从钥匙串读「Chrome Safe Storage」，会弹系统授权框；而且 macOS 上 Google Chrome 的所有配置共用这一个密钥项——给了 yt-dlp 这把钥匙，它就能解密主力 Chrome 的 cookies。我查钥匙串元数据的命令被权限拦了，这一点按 Chrome 在 macOS 上的已知设计写，**待人确认**。

## 三、候选方案（实测后排序）

| 方案 | Google 登录 | yt-dlp 共用登录态 | 与主力 Chrome 隔离 | 人补登录 | 改动面 |
|---|---|---|---|---|---|
| **A. Camoufox 持久配置**（方向 ①） | 待人测（E2 页面正常） | **能**，`firefox:<目录>` 直接读，不导出 cookie 本体（E3） | **完全**：不同内核、不同 cookie 库、不用钥匙串 | 跑附件 `camoufox_login.py` 开窗口登录 | `web_browser.py` 换启动器（Playwright Firefox 协议，API 基本不变）；`cookies_spec_for` 的写死 `chromium:` 改为按配置给 `firefox:`；camoufox 进项目依赖（uv，不装全局） |
| B. 人启动的真 Chrome + CDP（方向 ②） | 大概率放行（非自动化启动，E4 `webdriver=false`），待人测 | 要么钥匙串授权（共享主力 Chrome 密钥，隔离被打破），要么经 CDP 导出 cookies 文件（违背 ADR-0021「不碰 cookie 本体」） | 配置目录隔离，**密钥不隔离** | 浏览器常驻，人随时在窗口里登 | `web_browser.py` 改 `connect_over_cdp`；人每次开机要先起浏览器 |
| B′. 人启动的独立 Chrome for Testing（Playwright 缓存里那份二进制，不经 Playwright 启动）+ CDP | 待人测 | `chrome:<目录>` 或 `chromium:<目录>`；它与正式版 Chrome 是不同的 app，钥匙串项可能分开——**未核实**，人测时看弹框里写的项名 | 若钥匙串项分开则比 B 好 | 同 B | 同 B |
| C. agent-browser | 自动化启动，大概率同现状被拒 | 只能导出 cookies 文件 | 好 | `--headed` 登录后存 state | 新外部依赖 |

**我的推荐：A，B′ 作备胎。** 理由：A 是唯一一个三条（yt-dlp 直接共用、不碰 cookie 本体、与主力 Chrome 完全隔离）都已有实测支持的方案，剩下的不确定只有「Google 让不让 Camoufox 登录、登录能不能持久」，正好是下面要人测的两项。B 的问题不在登录而在密钥：钥匙串那一项一旦授权给 yt-dlp，「AVA 不碰主力 Chrome 凭据」就只剩口头约定。

## 四、请人做的实测（约 10 分钟，按顺序）

1. **A 登录**：
   ```bash
   /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -I \
     docs/dev/plans/2026-10-15-browser-login-options/camoufox_login.py /tmp/ava-cfx-test
   ```
   在弹出窗口里登录 YouTube 用的 Google 账号。记下：是否出现「This browser or app may not be secure」；是否要二次验证。
2. **A 持久性**：同一条命令再跑一次，打开 youtube.com 看是否仍是登录状态。
3. **A 给 yt-dlp**：
   ```bash
   yt-dlp --ignore-config --cookies-from-browser firefox:/tmp/ava-cfx-test -J --skip-download "https://youtu.be/jpV5jeFlt_E" | head -c 300
   ```
   看是否不再报 bot 检查。
4. 若 1 被拒：测 B′——由人启动 `"$HOME/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing" --user-data-dir=/tmp/ava-cft-test --remote-debugging-port=9333`，登录后跑 `uv run python -I docs/dev/plans/2026-10-15-browser-login-options/cdp_probe.py http://127.0.0.1:9333 /tmp/x.png`，再试 `yt-dlp --cookies-from-browser chrome:/tmp/ava-cft-test …`，记下是否弹钥匙串框、框里写的是哪一项（若是「Chrome Safe Storage」，说明与主力 Chrome 共用密钥，B′ 也不隔离）。

测完把结果告诉 AVA 开发会话，据此写施工图（落 `data/browser-profile-*`、改 `web_browser.py` 与 `cookies_spec_for`、ADR-0021 修订注）。测试目录用完人自己删。

## 五、明确不做的事

- 不碰主力 Chrome 的配置与钥匙串项。
- 不在施工图定稿前改 `web_browser.py`。
- 不走「导出 cookies 文件给 yt-dlp」，除非 A、B′ 都失败（ADR-0021 的最后手段）。
