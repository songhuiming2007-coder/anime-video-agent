# N50 小 spec：密钥环境变量不再传给作业与浏览器

日期：2026-10-15（v0.1）；**人 10-15 裁决：做，C 选 C2**；同日已施工·机检通过，施工记录见第八节
关联：issues N50、N49、Spec 10 §2.9、TH-6、[Spec 15 §2.7 / R2-9](archive/2026-09-29-web-search-provider-spec.md)、ADR-0029 决策 5、D83

## 一、要解决什么

会话进程的环境里有 LLM 密钥变量和至多 4 个 web 检索密钥变量（桌面端从钥匙串注入，终端 `ava` 从 shell 继承）。这些变量今天会一路传给：

- `run_pipeline` 起的作业，以及作业的子孙进程（ffmpeg、ffprobe、yt-dlp、ssh、rsync 等）；
- 会话进程内拉起的浏览器（`browser` 工具的 Camoufox、`crawl` 工具里 crawl4ai 拉起的 Chromium）。

这些程序都不读这些变量。

**能防什么、不能防什么。** 这些程序与会话同用户运行。同用户进程本来就能直接调 `/usr/bin/security find-generic-password -s ava` 读到钥匙串里的密钥，终端用的密钥也明文写在 shell profile 里。所以剥掉变量**不防**恶意的同用户程序。它能防的是：第三方程序在调试模式或出错时把整份环境写进日志、诊断输出。这些程序会不会这样做，这次没有逐个核查。收益有限，A、B 两项代价也小。

## 二、现状（2026-10-15 读码核实）

| 拉起路径 | 位置 | 密钥变量 | 子进程里谁要用 |
|---|---|---|---|
| bash 作业 | `tools.py::_tool_bash` → `create_shell_job(env_drop=secret_env_names(...))` | **已剥**（D77） | — |
| `run_pipeline` 作业 | `tools.py::run_pipeline` → `jobs.create_job` → `execute_job`（`env_drop` 为空） | 全部继承 | `adversarial`、`fact_review`、`notes_review` 三个模块要调模型，只用 LLM 那一个变量；没有模块用 web 检索变量 |
| `browser` 工具 | `web_browser.py::_default_launch_fn` → camoufox `NewBrowser(...)` | 全部继承（camoufox 0.5.6 在 `env=None` 时取 `dict(os.environ)`） | 无 |
| `crawl` 工具 | `web_crawl.py` → crawl4ai 0.9.4 `AsyncWebCrawler(BrowserConfig(...))` | 全部继承 | 无 |
| 桌面端其他模板 | `spawner.ts::childEnv` 白名单 | 本来就不带 | — |
| 桌面端会话进程 | `spawner.ts::spawnSession(extraEnv)` | 注入，**需要**：会话进程在进程内调模型和 web 检索 | 会话进程自己 |

核实方式：`grep chat_complete pipeline/*.py` 只命中上面三个模块；`grep "agent.web" pipeline/*.py`（不含 `agent/`）无命中；crawl4ai 的 `BrowserManager._build_browser_args` 只组装 `headless` / `args` / `channel` / `downloads_path` / `proxy`，没有 `env`。

## 三、改法

**A. `run_pipeline` 作业剥密钥变量（改 `pipeline/`）**

- `tools.run_pipeline` 立项时，把 `secret_env_names(root)` 作为 `env_drop` 传给 `create_job`。`create_job` 加 `env_drop` 参数，默认空，保持 CLI 直接调用时的现行为不变。
- 例外：`adversarial`、`fact_review`、`notes_review` 保留 LLM 那一个变量（`api_key_env_name(root)`），web 变量照剥。例外名单写成 `tools.py` 里一个常量 `LLM_KEY_MODULES`。
- 守卫测试：扫 `pipeline/*.py`，凡是调用 `chat_complete` 的模块必须在 `LLM_KEY_MODULES` 里。将来新加调模型的模块漏登记，测试变红，而不是上线后报「缺密钥」。

**B. `browser` 工具传过滤后的环境（改 `pipeline/agent/`）**

- `_default_launch_fn` 给 `NewBrowser` 传 `env={k: v for k, v in os.environ.items() if k not in secret_env_names(root)}`。camoufox 会在这份环境上叠加它自己的 `CAMOU_CONFIG_*` 等变量。

**C. `crawl` 工具：三选一，请人定**

| 选项 | 做法 | 代价 |
|---|---|---|
| C1 | crawl 改在子进程里跑：会话进程起一个剥过密钥的 Python 子进程执行抓取，结果走 stdout 回传 | 改动最大（抓取、超时、错误包装都要过进程边界）；每次多 1～2 秒启动 |
| C2（推荐） | 不改，写成明示残留：issues N50 行与本 spec 都写明「crawl 拉起的 Chromium 仍带密钥变量」 | 无代码改动；残留写清楚，不会让人以为已全部收紧（R2-9 担心的正是这一点） |
| C3 | 拦截 playwright 的启动函数，往里塞 `env` | 依赖 crawl4ai 内部调用方式，升级就可能静默失效；不推荐 |

推荐 C2 的理由：第一节已说明收益有限，C1 的改动量和每次抓取的延迟不划算；crawl4ai 将来若开放 `env` 参数，再按 B 的写法补上。

**桌面端不用改。** 会话进程本身要用这些密钥，`spawnSession` 照旧注入；作业和浏览器都从会话进程拉起，在 Python 侧剥就同时覆盖桌面端和终端 `ava` 两条入口。R2-9 说的「两条拉起路径」指 `jobs.py` 与会话进程内的拉起，即上面 A 与 B / C。

## 四、不做

- 不改会话进程本身的环境（它要用）。
- 不改 bash 作业（已剥）。
- 不处理「同用户进程经 `security` 读钥匙串」：这是 macOS 钥匙串的信任模型，不在本项目能改的范围内。

## 五、测试与变异

测试：

1. `run_pipeline` 作业：替身模块打印自己的环境变量名，断言密钥名不在其中；同时断言 `PATH`、`HOME` 等普通变量还在。
2. `fact_review`（替身同名模块）：断言 LLM 变量在、web 变量不在。
3. 守卫：调 `chat_complete` 的模块都在 `LLM_KEY_MODULES` 里。
4. CLI 直接 `create_job` 不传 `env_drop`：环境照旧完整（不改现行为）。
5. `browser`：注入假的 `launch_fn` 截获参数，断言 `env` 里没有密钥名；再加一条真启动的冒烟（本机有 Camoufox 时跑，没有就跳过并写明原因）。
6. 协议层：真实 core + 假端点，`run_pipeline` 跑替身模块，断言作业环境里没有 `AVA_TEST_KEY`（N49 取证用过的夹具变量）。

变异（预计 6 处）：`run_pipeline` 不传 `env_drop`；`LLM_KEY_MODULES` 去掉 `fact_review`；例外模块把 web 变量也保留；`create_job` 忽略 `env_drop` 参数；`browser` 不传 `env`；守卫的扫描范围漏掉一个模块。

验证：全量 pytest；不动 `desktop/`，所以不跑 vitest 和 e2e。

## 六、文档同步

- issues N50 行：改为已施工，写明 C 的裁决结果与残留。
- Spec 15 §2.7「放过了什么」末尾加一句修订注，指向本 spec。
- 文档里的 TH-6 断言（会话环境只多出 LLM 名与 web 链上声明的名字）不变：本 spec 不改会话环境。

## 七、裁决（人 2026-10-15）

1. 做。
2. C 选 C2：crawl 不改，写成明示残留。

## 八、施工记录（2026-10-15）

**改动**

- `pipeline/agent/tools.py`：新增 `LLM_KEY_MODULES` 与 `pipeline_job_env_drop(argv)`；`run_pipeline` 立项后、执行前设 `job.env_drop`。
- `pipeline/agent/web_browser.py`：新增 `browser_env()`，`_default_launch_fn` 给 `NewBrowser` 传 `env=browser_env()`。
- 测试：新增 `tests/test_n50_secret_env.py` 10 条（第五节 1–6，其中「调模型的模块」按三个模块参数化）；`tests/test_agent_crawl_browser.py` 两条启动器用例原先逐字比对 `NewBrowser` 参数，改为先取出 `env` 再比对其余参数。

**与第三节的出入**

- A 写的是「`create_job` 加 `env_drop` 参数」。实际没改 `create_job` 的签名，而是在 `run_pipeline` 拿到 `job.argv` 后设 `job.env_drop`：要按模块名判例外，得先有校验后的 argv，而 argv 是在 `create_job` 里面算出来的。效果相同，CLI 直接调用 `create_job` 时仍是完整环境（第五节第 4 条测的就是这个）。
- 第五节第 5 条的「真启动冒烟」没有写成测试，改为下面的一次性冒烟（要起真浏览器，放进全量 pytest 太慢）。

**验证**

- 变异 8/8 被抓：`run_pipeline` 不剥；名单去掉 `fact_review`；例外模块连 web 变量也留；例外不生效；模块名解析错位；browser 不传 `env`；`browser_env` 不过滤；`execute_job` 忽略 `env_drop`。
- 全量 pytest 3358 passed。没动 `desktop/`，未跑 vitest / e2e。
- 真实冒烟（本机 Camoufox 152.0.4-beta.30）：设一个 `*_API_KEY` 变量后起 `_default_launch_fn`，用 `ps -AEww` 查本进程的子孙进程环境。Camoufox 主进程与 5 个 `plugin-container` 都没有该变量，普通变量都在。仍带该变量的有：playwright 的 node 驱动进程（`sync_playwright().start()` 拉起时复制整份环境，没有参数可改），以及一个 `multiprocess` 库的 Python 辅助进程。

**明示残留**

1. `crawl` 工具：crawl4ai 0.9.4 拉起的 Chromium 带密钥变量（C2）。
2. `browser` 工具：playwright 的 node 驱动进程带密钥变量（施工时发现，原因同上）。浏览器本体已不带。
3. 会话进程内其他直接起的子进程（如 `multiprocess` 辅助进程）未逐个处理。

将来 crawl4ai 或 playwright 开放 env 参数，按 B 的写法补上。
