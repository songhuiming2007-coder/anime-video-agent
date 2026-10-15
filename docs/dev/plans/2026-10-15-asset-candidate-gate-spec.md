# D100 素材候选关卡：交人批的表只能由机器核过的数据生成（施工图）

> 状态：**已施工·机检与真实冒烟通过**（2026-10-15；人定第五节 ① 不自动下载、② v1 不许豁免、③ 不做模型对比；施工记录见第十节）。
> 关联：D99（事实关卡，本图照它的思路做到素材上）、ADR-0021、ADR-0030（`acquire_propose` 一调用就下载）、D96。

## 一、目标与对应 issue

- 对应 issues **D100**。
- **现象**：2026-10-15 supercell 卷一补素材，AVA 交来一张 53 段的候选总表，要人「一次性批准」。抽查发现：
  - 点名的 6 个 Wikimedia Commons 文件（`Tokyo_Night_Street_Winter.jpg`、`Logic_Pro_X_screenshot.png`、`Piano_Keys_Sheet_Music.jpg`、`Classroom_in_Japan.jpg`、`Neumann_U87_microphone.jpg`、`Tokyo_Big_Sight_Comiket.jpg`）经 Commons API 查询**全部不存在**，配套的「3840×2160」「CC0」「CC-BY-SA 4.0」也是编的；
  - 7 条「官方画集超清扫图」没有任何链接，只写了画集名和精确到像素的尺寸；
  - 段 47 写「动画正片截图（已声明授权引用）」，授权是凭空写的。
- **同类前科**：卷一事实文件第一版把没打开过的出处标「一手」，人抽查出硬错（D99 的起因）。两次的共同点：模型被要求交「完整、能一次批完的表」，而交表前没有任何机器核对；表越完整，人越难一眼看出哪条是编的。prompt 里写「不许编」已证明不管用。
- **目标**：人看到的候选表里，链接、分辨率、时长、大小、授权这些**只能来自机器实测**；核不上的候选进不了下载。模型能写的只剩「为什么要这条」（`why`），并在表里标明是模型说的。人从此不用再核来源真假，只判断要不要。

## 二、前置条件

- 基线 `uv run pytest` 全绿。
- D99 PR-1 的抓取留档（`data/library/fetch-log/`）已在用。注意：留档从 2026-10-15 才有，**卷一会话 10-14 抓过的页面不在留档里**，用到时要重新抓一次（抓取本身很便宜）。
- 建议先做 D96 收尾（yt-dlp 进项目依赖），否则 YouTube 候选的可得性实测会因签名求解器缺失误报失败（见 D96 施工图 §九）。

## 三、改动清单（锚点引原文，不给行号）

### A. 抓取留档补上「页面里有哪些图和链接」

| 文件 | 锚点 | 改动 |
|---|---|---|
| `pipeline/agent/web.py` | `"images": images,` / `"links": links,`（`fetch_web` 返回处） | `keep_full=True` 时另带截上限之前的完整列表 `full_images` / `full_links`，与 `full_text` 同一处理：tools 包装层记完即 pop，不进 LLM |
| `pipeline/agent/web_crawl.py` | `images, images_truncated = _cap_images(` | 同上（crawl 的 markdown 里的链接一并抽出） |
| `pipeline/agent/web_browser.py` | `def extract_text(self, *, timeout_s: float = 60.0)` | 用同一份 html 跑与 crawl 同口径的图片 / 链接抽取，`keep_full` 时带出 |
| `pipeline/agent/fetch_log.py` | `def record(*, url: str, final_url: str | None, kind: str, text: str, sid: str | None,` | 增参数 `images` / `links`（可空），存为同目录 `<ts>-<kind>.refs.json`，sha256 记进 index 行；新函数 `read_refs(entry)`，文件被改过（sha 对不上）报错，口径同 `read_text` |
| `pipeline/agent/tools.py` | `def _keep_record(` | 把 `full_images` / `full_links` 一并交给 `fetch_log.record` |

### B. 候选多两个字段

| 文件 | 锚点 | 改动 |
|---|---|---|
| `pipeline/agent/tools.py` | `"acquire_propose": {` 的 `items.properties` | 加 `found_on`（**必填**：在哪个页面上找到这条的；视频页或直链本身就抓过时可以等于 `url`）、`segment`（可选：补第几段）。`required` 加 `found_on` |
| `pipeline/candidates.py` | `def load_candidates(` 的校验 | 认这两个字段；旧条目没有 `found_on` 不报错，但核对时直接判「出处未登记」 |

### C. 新模块 `pipeline/candidate_check.py`：逐条机器核对

每条候选核四项，结果写 `data/library/incoming/checks.jsonl`（按 URL 追加一行；`candidates.json` 保持只追加、不改既有条目的约定）：

1. **出处**：`found_on` 在抓取留档里有记录（任何会话都算，与 D99 人定口径一致），并且 `url` 出现在那条记录的图片列表、链接列表或正文里（`fetch_log.norm_url` 比对）；`url == found_on` 时只要求这页本身抓过。核不上 → 「出处核不上」。
2. **可得性实测**：复用 `probe_argv`。视频页走 `yt-dlp -J`，取最高分辨率、时长、预估大小；直链走 HTTP 头取状态、类型、大小；图片另取前 256 KB 用 Pillow 读宽高。打不开 → 「打不开」；读不出尺寸 → 写「尺寸未知」，不猜。
3. **授权**：只有 Wikimedia（`commons.wikimedia.org` / `upload.wikimedia.org`）走 API 查 `imageinfo` 的原图宽高、`LicenseShortName`、作者，表里显示 API 返回值；其余一律显示「授权：未核实」。模型写的授权不进表。
4. **门禁预判**：`scan` 类短边 < `SCAN_MIN_SIDE`（1600，沿用 `acquire.py` 的常量）→ 「门禁会拒」，提前曝光，不等下载完才发现。

每条的结论：`可下载` / `出处核不上` / `打不开` / `门禁会拒`。

### D. 流程：登记即核对，下载另走一步，表由代码生成

| 文件 | 锚点 | 改动 |
|---|---|---|
| `pipeline/candidates.py` | `def propose_candidates(` | 登记后对本批跑核对，返回值带**机器生成的 Markdown 表**（列：号 / 段 / 标题 / 类型 / 结论 / 实测画质·时长·大小 / 授权 / 出处页与抓取时间 / why〔模型说明〕），同时写一份 `incoming/review-<时间>.md`；表头先给统计：可下载几条、各类核不上几条，按类型（视频 / 图 / 网页截屏）计数 |
| `pipeline/agent/llm.py` | `if name == "acquire_propose" and outcome.get("ok") and control.post_execute is not None:` | **去掉提议后自动下载的钩子**（见第五节决定 ①） |
| `pipeline/acquire.py` | `fetch` 子命令 | 只抓 `checks.jsonl` 里最近一次结论为「可下载」的条目；其余拒绝并说出原因。新增子命令 `acquire check <号>`：对旧条目或换源后的条目补跑核对（出网只解析，与 `probe` 同等级别） |
| `pipeline/agent/tools.py` | `ASSET_COMMANDS` 的 `"acquire"` | 加 `check` |
| `config/agent/scopes/director.md`、`asset.md`、`creative.md`、`project.md`；`skills/acquire-assets/SKILL.md` 第二节 | 讲 `acquire_propose` 的各处 | 改写流程：检索 → `acquire_propose`（登记并核对，不下载）→ **把工具返回的表原样给人**，不另写表、不在对话里补分辨率或授权 → 人说要哪几号 → `acquire fetch <号>`。人看表时以工具表为准，对话里的数字不算 |
| `docs/dev/adr/0030-*.md` | 文首 | 加修订注：`acquire_propose` 不再一调用就下载，理由一句话指向本图 |

## 四、新常量 / 新判据的取值理由

- **图片只取前 256 KB 读宽高**：JPEG 的尺寸在 SOF 段、PNG 在 IHDR，正常都在文件开头几 KB 内；带大块 EXIF 或嵌入缩略图的 JPEG 会把 SOF 往后推，256 KB 留足余量。仍读不出就标「尺寸未知」，不下载全图（候选可能几十 MB）。施工时用今天卷一会话里的真实图片链接实测一遍，读不出的比例写进完成记录。
- **短边 1600**：沿用 `acquire.py` 的 `SCAN_MIN_SIDE`，同一个判据只有一处来源。
- **出处核对跨会话都算**：与 D99 人定的口径一致（同一篇报道多卷共用）。
- 不新增其他数值常量。

## 五、要人定的三点

1. **`acquire_propose` 不再一调用就下载（推荐：是）**。ADR-0030 为了少弹卡，让提议即下载，靠「先在对话里问人」把关；但人在对话里看到的正是模型自己写的表，这道关实际上不存在。改成两步后，人仍然不用点卡片，只是在看完机器表后说一句「要 3、5、7」。
2. **核不上的候选能不能人工豁免（推荐：v1 不能）**。如果核对本身误判（比如某站把图片放在脚本里加载，留档里没有直链），正确做法是修核对器或换一个能核上的出处页，而不是开口子；先跑一段时间，看误判多不多再说。
3. **模型档位（另议，不在本图）**：AVA 现在研究和素材环节用的是 `gemini-3.8-flash`。换更强的模型能减少编造，但减不到零；本图的机械核对不管用哪个模型都要有。要不要给研究 / 素材类调用单独配更强的模型，另开一项。**人 2026-10-15 定：不做模型对比**（「测模型没有意义」），本图只做机械核对。

## 六、测试要求

期望值先在实现上跑通再写断言；写完做变异检验。

- 抓取留档：`web_fetch` / `crawl` / `browser` 的完整图片与链接列表落进 `.refs.json`、不进 LLM 返回；refs 文件被改过时 `read_refs` 报错。
- 出处核对：`url` 在 `found_on` 页的图片列表里 → 过；只在别的页出现 → 不过；`found_on` 从没抓过 → 不过；`url == found_on` 且抓过 → 过；`norm_url` 口径（fragment、末尾斜杠）与 D99 一致。
- 可得性：用替身 `subprocess.run` 喂 yt-dlp `-J` / curl 头 / 图片字节，断言取到的画质、时长、大小、宽高；读不出尺寸时写「尺寸未知」。
- 授权：替身 Commons API 返回「文件不存在」→ 打不开；返回授权 → 表里显示 API 值；非 Wikimedia 来源一律「未核实」。
- **真实回放夹具**：今天表里的 6 个 Commons 文件名 → 全部「打不开」；一条有真实出处页的候选 → 「可下载」。
- 流程：`acquire_propose` 返回值里有机器表、不触发下载；`acquire fetch` 拒抓非「可下载」条目并说原因；`acquire check` 补核旧条目。
- 变异（预期全部被抓）：出处只查 `found_on` 抓过、不查 `url` 是否在页内；授权回退成模型写的值；尺寸读不出时填默认值；`fetch` 不看核对结论；提议后仍自动下载；`found_on` 改回可选。

## 七、验证命令

```bash
uv run pytest
cd desktop && npx tsc --noEmit -p . && npx vitest run     # 动了工具表 schema 与 config/agent/，按家规跑
# 真实冒烟（施工后我跑）：
#   1) 重抓今天卷一表里的几个真实出处页（ORICON 存档、originalnews.nico、supercell.jp），对其上的图片提候选 → 可下载，表里画质为实测值
#   2) 用今天那 6 个 Commons 文件名提候选 → 全部「打不开」
#   3) acquire fetch 一条「可下载」的，gate 照常；fetch 一条「出处核不上」的，被拒
```

## 八、明确不做

- 不判断 `why` 写得对不对（那是人的判断，表里已标明是模型说明）。
- 不做实体画集 / 付费商品的采购流程（以后可加 `type: buy` 单列，本图不做）。
- 不回溯修改既有 `candidates.json` 条目；旧条目要下载先 `acquire check`。
- 不换模型（见第五节 ③）。
- 不核候选里的画面 / 分镜描述（如「0:35–1:05 雨中撑伞」）：下载后素材会经过 VLM 打标，画面内容以 VLM 为准。人 2026-10-15 定；当天实测 AVA 给《メルト》原投稿写的分镜是编的（原片是一张静图加歌词），但这不在本图的拦截范围内。

## 九、完成判定

- [x] pytest / tsc / vitest 全绿，变异结果贴出（见第十节）
- [x] 真实冒烟三项通过（见第十节）
- [x] ADR-0030 修订注、各处规程改写、issues D100 行、plans/README 状态列已更新
- [ ] 待人：下一轮补素材时，只看工具返回的表批一次，确认表能看懂、不用再自己核来源

## 十、施工记录（2026-10-15 晚）

与施工图的出入（人在施工前批过）：

- **核对不在 `propose_candidates` 里跑**：`candidates.py` 首条纪律是零网络能力，LLM 工具进程也要绕开 `acquire.py` 的 numpy 导入链。改为把原「提议后自动下载」钩子（`session._post_execute`）换成「提议后自动核对」：对本次输入里、在清单中且不在台账的候选跑一次 `run_pipeline("acquire check <号>")`（子进程），按末行 `REVIEW_FILE <路径>` 读回机器表，挂进工具结果 `result.review`（路径须在 incoming/、名为 review-*.md）。效果同施工图：模型拿到的工具返回就带机器表，不下载。
- **出处比对去掉 `utm_*` 参数**（施工图写「`norm_url` 口径与 D99 一致」）：冒烟实测 Commons 文件页指向原图的链接带 `?utm_source=…&utm_campaign=…`，干净链接按 `norm_url` 比不上。只在 `candidate_check` 里多去 utm_，D99 的 `norm_url` 不动；其他查询参数照旧区分。
- 留档里的链接保留**无锚文本**的（`_extract_links(keep_unanchored=True)`）：包着缩略图的 `<a href="原图">` 没有锚文本，正是最常用的原图链接；给模型看的链接清单照旧丢空锚。
- 结论优先级：打不开 > 出处核不上 > 门禁会拒（文件不存在是更基本的事实）；表里列出全部不过的原因。
- 规程改写除批过的四处外，`docs/runbook/02-script.md` 2.5 步、`04-clips.md` 找素材、`skills/write-script/SKILL.md` 8.5 节也写着「一调用就下载 / 人在卡上批」，同步改成同一句，免得说明互相矛盾。
- `scripts/verify_mutations.py` 的 S9-MUT-14（抓取前复核序号）随钩子不再抓取撤销，换成 D100-1（钩子跑 check 不跑 fetch）。

新增 `pipeline/candidate_check.py`（出处 / 可得性 / 授权 / 门禁预判，出网走注入的 runner；4 路并发）、`acquire check`（免卡，出网只解析）；`acquire fetch` 只抓最近一次结论为「可下载」的。

测试：新增 `tests/test_candidate_check.py` 40 条；改写 `test_agent_session.py` TK-2/3 一组为核对、`test_project_session.py` 一条；fetch 用例的夹具补一笔「可下载」核对；`test_agent_context_claims` 改断言「不下载」。变异 18/18 被抓（施工图列的 6 类，加钩子仍抓、utm 两向、留档链接丢空锚、包装层不交列表、refs 不查 sha、钩子不校验路径、图片全量下载、取最早一次核对、crawl 不出列表、门禁 `<=`、优先级对调、工具不拦缺 found_on）。

真实冒烟（10-15 晚）：
1. web_fetch 重抓 supercell.jp、originalnews.nico/20667/2、ascii.jp 一页（ORICON 存档的 URL 不在留档里，换成 ascii.jp），留档分别存下 12 / 8 / 51 张图、13 / 74 / 169 条链接。用页上的 6 张图提候选：核对全部给出实测尺寸（1200×720、72×72、1200×800、17×18、251×251、31×10），全部「门禁会拒」（短边 < 1600），读不出尺寸 0 条。另取一张真实 Commons 原图（File:Day 3 - Big Sight at Night.jpg）：「可下载」，5056×3792，授权 CC BY-SA 2.0 与作者取自 API。
2. 10-15 那 6 个 Commons 文件名：全部「打不开」（Commons API：没有这个文件），模型写的 CC0 没进表。
3. `acquire fetch 46,39`：#46 下载落盘（1.4MB），#39（同一张图、found_on 填没抓过的页）被拒，原因「出处核不上」。

冒烟留下的东西：candidates.json 里 14 条标题以「[D100冒烟]」开头的候选（#33–#46，清单只追加不删）、incoming/ 下一张 `D100冒烟_Commons_真实文件_Day_3_Big_Sight_at_Night.jpg` 与台账一条、checks.jsonl 与两份 review-*.md。删不删由人定。

待人：下一轮补素材时只看工具返回的表批一次，确认能看懂、不用再自己核来源。

