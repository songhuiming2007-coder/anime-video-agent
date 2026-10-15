# D96 浏览器登录态：实测记录与待人测项（施工图之前）

日期：2026-10-15　状态：**实测完成**（Google / YouTube 午间；其他 8 站 + 两个百科 16:00 后，见 §四之三）；施工图 [2026-10-15-browser-camoufox-spec.md](2026-10-15-browser-camoufox-spec.md) 待人确认
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

## 四之二、第二轮：其他站点（人回来后做）

人只做一件事：在一个窗口里把要用的站点逐个登录，然后关窗。命令（同一份配置，Google 那份登录态不受影响）：

```bash
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -I \
  docs/dev/plans/2026-10-15-browser-login-options/multi_login.py "$PWD/data/browser-profile-firefox" /tmp/ava-login.jsonl
# 只测几个站：在末尾加站点键，如 niconico bilibili x
```

站点键：niconico、bilibili、x、pixiv、note、zhihu、xiaohongshu、bangumi（不登的标签页直接关掉）。脚本按站记「是否出现不安全 / 机器人验证字样」和「是否登上」。之后由开发会话逐站跑：无界面重开看是否保持登录；要下载的站（niconico、bilibili、X）用 `yt-dlp --cookies-from-browser firefox:data/browser-profile-firefox` 实抓一条。全部测完再写施工图。

## 四之三、第二轮结果（2026-10-15 15:50–16:40，人登录、我测）

人在 `multi_login.py` 的窗口里登了 bilibili、X、pixiv、知乎、小红书、Bangumi，当场注册并登录了 niconico；note 没注册（AVA 只读公开文章，用不到账号；人试注册时 note 回「しばらくたってからもう一度お試し下さい」= 请稍后再试，原因未查）。

**登录时**：8 个站都没出现「浏览器不安全」类拦截。pixiv 登录页正文里有「captcha」字样，空配置打开也有，是登录页上的 reCAPTCHA 说明，不是拦截。

**`multi_login.py` 的「已登录」判断不可靠**：它按「登录 cookie 名在不在」判，pixiv（`PHPSESSID`）、note（`_note_session_v5`）、小红书（`web_session`）窗口打开 5 秒就判为已登录——访客也发同名 cookie；没注册的 note 最终也记成 true。所以持久性改为下面这种判法。

**无界面重开是否保持登录**（scratch `persist_check.py`：同一配置 headless 重开，每站打开一个只有登录后才看得到的页面；同一脚本对一份空配置跑一遍作反面对照，确认每条判据在没登录时为假）：

| 站 | 判据 | 登录配置 | 空配置对照 |
|---|---|---|---|
| niconico | `nicovideo.jp/my` 不被转到 `account.nicovideo.jp` | 保持 | 转到登录页 |
| bilibili | `api.bilibili.com/x/web-interface/nav` 的 `isLogin` | 保持 | false |
| X | `x.com/home` 停在 `/home` | 保持 | 转到 `/i/jf/onboarding/web` |
| pixiv | `pixiv.net/settings/account` 停在设置页 | 保持 | 转回首页 `/en/`（初版判据「不转 accounts.pixiv.net」在这里误判为真，已改） |
| 知乎 | `zhihu.com/api/v4/me` 有 `id` | 保持 | 无 |
| 小红书 | `/explore` 正文没有「登录」字样 | 保持 | 有登录框（5 处「登录」）；初版判据「有作者主页链接」在这里误判为真，已改 |
| Bangumi | 首页有 `logout` 链接 | 保持 | 无 |
| note | `note.com/settings/account` 不转 `/login` | 未登录（与事实一致） | 未登录 |

**yt-dlp 2026.08.19 用 `--cookies-from-browser firefox:data/browser-profile-firefox`**（读出 143 个 cookie；与不带 cookie 对比，只做解析不下载）：

| 站 | 样本 | 不带 cookie | 带登录配置 |
|---|---|---|---|
| niconico | sm1715919、sm15630734（2007 / 2011） | 360p | 360p，格式完全相同 |
| niconico | sm45961264、sm41466174（2026 / 2022） | 720p | 720p，格式完全相同（免费账号没多拿到画质；付费会员是否不同未测） |
| bilibili | BV1gx411D7x2（老视频，2 小时） | 360p / 720p 两档 | **多出 1080p** |
| bilibili | BV1iRrNBLEq5 | 1080p | 1080p，相同 |
| X | mahoyo_movie 推文视频 | 540p | 540p，相同 |
| X | oricon_anime_ 推文（视频是嵌的 YouTube） | **被 YouTube bot 检查拦** | 1080p（yt-dlp 跟到 YouTube，用的是同一条命令的 cookie） |
| X | ryo_spcl、lobster425 推文 | 没有视频 | 同左 |

没找到「必须登录才能看」的 X 推文（敏感内容 / 受保护账号），X 登录态本身的作用未测。

**Cloudflare 站（不登录，只读）**：萌娘百科无界面打开直接 200、正文完整。Fandom 时好时坏：第一次无界面 403 停在「Just a moment...」，第二次无界面 5 秒通过；有界面窗口停在验证页，**人手点一次验证后**配置里留下 `fandom.com` 的 `cf_clearance`，之后无界面打开 200、正文完整。即：Cloudflare 验证可以靠「有界面窗口人点一次」解决，通过状态留在同一份配置里。

**依赖实测**：camoufox 最新稳定版 0.5.8（与本机全局 0.5.6 一样）要求 `playwright<1.63`，项目 `browser` 可选依赖现钉 `playwright>=1.63.0`，二者冲突；crawl4ai 0.9.4 只要求 `playwright>=1.49`。本机 `~/Library/Caches/ms-playwright/` 里 1.62 对应的 `chromium-1234` 已在，Camoufox 浏览器本体（`152.0.4-beta.30`）也已在 `~/Library/Caches/camoufox/`。上面全部实测都跑在 playwright 1.62.0 上。

## 五、明确不做的事

- 不碰主力 Chrome 的配置与钥匙串项。
- 不在施工图定稿前改 `web_browser.py`。
- 不走「导出 cookies 文件给 yt-dlp」，除非 A、B′ 都失败（ADR-0021 的最后手段）。
