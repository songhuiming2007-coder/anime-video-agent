# D84：外部真实素材常规化 + 微单元短段落 施工 spec

> 对应 issue：**D84**（`docs/dev/issues/README.md`）。需求来源：人在 2026-10-13 交来的《制作痛点与真实素材工作流需求》（《束胸力学》期写出的三条痛点；原件在人本机桌面，不进仓库）。
> 相关：ADR-0010 决策二（D 类物证转微动入 SP）、ADR-0011（SP 集号、多锚点蒙太奇、`ok_extended`）、D59（期内补料通道）、N31（图片形态三堵墙）。
> 本 spec 无 UI 改动，plans/README 的 UI 五条规则不适用。

## 裁决记录

| # | 问题 | 人的回复（2026-10-13） | 施工注记 |
|---|---|---|---|
| Q1 | 是否出方案；微动时长这个次要问题要不要一起做 | 「要出方案，微动时长那个次要问题要一起做」 | PR-D 纳入；Plan Mode 方案经人批准后施工 |
| Q2 | `02-asset-requests.json` 要不要扩进期文件写入白名单 | 「扩大白名单有什么影响？影响不大的的话就扩大」 | 评估影响很小（见施工记录末节），扩入 |

## Context

需求来源见文首。三个痛点：
1. 硬核内容（压强、受力图、矿泉水对比）原片里没有对应画面，只能拿角色镜头硬凑。
2. 一段 20 秒只放一个镜头，节奏拖。
3. EGOIST 那套真实物证素材只有纪录片能用。

核对代码后的结论：大部分机制已经有了（`acquire register` 图片自动转微动、`--to-patch` 期内补丁池、单段多锚点蒙太奇、SP 锚点）。没接通的有五处：

| # | 缺口 | 位置 |
|---|---|---|
| A | 没写 `时长目标` 时段落数上限固定 20。默认带上限 1300 字 ÷ 每分钟 257 字 ≈ 303 s，按 skill §8.1 的 8–12 s 微单元要 25–37 段，**照 skill 写过不了机检** | `pipeline/check_script.py:503` |
| B | **check_script 不认补丁池锚点**：前缀不在番表也不是企划名时报「不在番表」，而且 `ingest.load_sources(pool)` 只查全局 `sources.json`。`clips._parse_anchor` 走 `extra_pools` 是认的，两边口径不一致。runbook 04「05 人审改稿写补丁锚点」那条流程里的 `check_script` 这一步按代码读必然报错（施工第一步先用测试复现） | `pipeline/check_script.py:651-707` |
| C | 补丁池要等云端 VLM 打标和向量化都做完才写 `pool.json`，`load_pool` 又要求每个资产都有 `scene.npy/json`。**只想锚点一张图也得开计费 GPU** | `pipeline/ingest_patch.py:426-525`、`load_pool:164-300` |
| D | 微动固定 6 s，而且 `min(zoom+0.0015,1.15)` 在第 100 帧（约 4.2 s）就到顶，**6 s 里最后 1.8 s 已经不动了**。放进 8–12 s 的段，后面再接 `ok_extended` 尾帧定格，前后加起来 4–8 s 静止 | `pipeline/acquire.py:400-427` |
| E | 搜图：`web_fetch` 只抽 `<a>` 链接，`crawl` 只返回 fit_markdown，**agent 拿不到图片直链**，「agent 帮我去网上搜图」做不到 | `pipeline/agent/web.py:504-562`、`web_crawl.py:166-180` |
| F | 写稿期补料（`02-asset-requests.json`、物证卡片表）只写在纪录片骨架里，其他题材只能等 04 排片失败后走救援 | `skills/write-script/SKILL.md:135-150`、runbook 02/04、`docs/WORKFLOW.md:45` |

**不做**：同一段里锚点和检索混用。拆成 8–12 s 微单元以后一段一个画面来源就够了，混用会打破「一段只走一个通道」（runbook 04 铁律 1、判据 10）。一段里要连放几张图，现成的多锚点蒙太奇（ADR-0011 决策 3）已经能做到。图片门禁（短边 ≥1600）不放宽，判据 3 不允许调阈值。

## 施工顺序

开新分支 `d84-external-stills`（从 main）。按「约束先行」，先改文档再改代码：

### PR-0 文档立项
- `docs/dev/issues/README.md` 登记 D84（上面六条缺口 A–F、需求文档出处、不做项）。
- 新建 `docs/dev/plans/2026-10-13-external-stills-microunit-spec.md`：本方案 + 每个 PR 的验证命令与完成判定；`docs/dev/plans/README.md` 活跃表加一行。

### PR-A 段落数上限按时长带推导
- `check_script.py:503`：不管有没有 `时长目标`，统一用 `max(conf max_segs, int(dur_hi*60/8))`。默认带下上限从 20 变成 37。8 s 取的是 skill §8.1 微单元的下限，两边同一个数，注释写上依据。下限 8 段不动。
- 测试（`tests/test_check_script.py`）：默认带下 30 段通过、38 段不通过；`时长目标` 覆盖分支行为不变。变异检验：把整句改回 `conf("script.max_segs", 20)` 时 30 段那条要变红。

### PR-B check_script 认补丁池锚点
- 前缀校验：番表、企划名之外，再放行 `ingest_patch.load_pool(episode)["pool"]`（只放行 SP 键，和 clips 一致）。
- 「锚点指向素材」：前缀是补丁池时，查 `pool["sources"]` 的键和 duration，不走 `ingest.load_sources`。
- 前缀看起来像补丁池（以 `-patch` 结尾）但本期还没有 `04-patch/` 时，报错里给出下一步：「先 `acquire register --to-patch` + `ingest_patch --anchor-only`」。
- 测试：用 `tmp_path` 夹具建期目录和最小 `04-patch/`（走现有 `tests/test_ingest_patch*` 夹具的写法）。先写一条在当前代码上红的复现测试，再修。变异检验：去掉补丁池放行时测试要红。

### PR-C 补丁池「只登记不打标」
- `ingest_patch --anchor-only`（布尔旗标，不进 `PIPELINE_VALUED_FLAGS`）：转图、切镜头、抽帧做完就写 `pool.json`，资产带 `"indexed": false`，不碰云端，不要求 `--floor`（`floor` 不写，`no_match_source: "anchor-only"`）。
- 不带旗标时流程照旧，最后写入时把资产标成 `"indexed": true`，并补上 floor。已经 anchor-only 登记过的资产直接沿用镜头表，不重新编号。
- `load_pool`：没有 `indexed` 键的当 `true` 处理（旧池零漂移）。`indexed: false` 的资产进 `sources`/`shots`，可以被锚点解析，但不进 `vecs`/`units`，救援检索搜不到它。`indexed: true` 的完整性校验照旧严格，半建照旧 FAIL。
- 还有四个模块读 `pool.json`，逐个核对它们对 `indexed: false` / 没有 floor 的容忍：`shots.py:558`（`gallery --patch`）、`status.py:118`、`scout.py:153`、`review.py:144`。救援在全是未打标资产时要明确打印「补丁池里 N 个资产未打标，救援检索不可用」，按判据 9，跳过不算通过。
- 审批：`--anchor-only` 照旧弹卡（写盘），但不触发计费卡。核对 `tools.py:598` 附近的成本卡判定。
- 测试：anchor-only 登记后 `clips` 能解析补丁锚点；`vecs` 为空时救援不崩并给出提示；anchor-only 之后再跑完整 ingest，编号不变、`indexed` 翻成 true；旧格式 `pool.json`（没有 `indexed` 键）读出来逐字段不变。变异检验：`load_pool` 把 `indexed: false` 当 true 处理时测试要红。

### PR-D 微动 12 s，推镜匀速到末帧
- `acquire.py`：`STILL_SECONDS = 12.0`（微单元上限；更长的段由 `ok_extended` 最多补 8 s，见 ADR-0011 决策 4）。缩放步长改成按帧数推导，`step = 0.15/frames`，保证第 `frames` 帧刚好到 1.15，中途不停。12 s 下每帧 +0.00052，属于「轻微推拉」。
- 已经转过的 `-still.mp4` 不重做（`-n` 和存在判断都已经有了），旧期零漂移。`scripts/rebuild_micromotion_sps.py` 是 EGOIST 专用的，不动。
- 测试：`tests/test_agentic_ops.py:91-111` 现在拿 `A.STILL_SECONDS` 自比，补一条硬断言：12 s、帧数 = round(12×24000/1001)，zoom 表达式在倒数第二帧还没到顶。变异检验：步长改回 0.0015 时要红。
- 文字里的「6 s」逐处改掉（`ingest_patch.py` 文档串/打印、`acquire.py:11/656`、runbook 04、acquire-assets SKILL、ADR-0010 决策二的「5 秒」加补记，不改原文）。

### PR-E 让 agent 拿得到图片直链
- `web_fetch`：在 `_extract_links` 旁边加 `_extract_images`，抽 `og:image`（排第一）和 `<img src/srcset 最大档>`，转绝对 URL、去重，带上 `alt` 和 HTML 里写了的 width/height。复用 `_cap_links` 的条数/字符上限和 `secret_values` 脱敏，返回值新增 `images` / `images_truncated` 两个键。
- `crawl`：从 crawl4ai 结果的 `media["images"]` 取同样的字段（版本钉死 0.9.4，施工时先实测字段名，取不到就如实返回空列表并写明原因，不编）。
- 工具描述里写清：拿到直链后走 `acquire_propose`（type `scan`），不许自己下载。
- 测试：HTML 夹具覆盖 og:image、srcset、相对路径、上限截断、脱敏。变异检验：去掉 og:image 排首时要红。

### PR-F 写稿期补料变成所有题材的常规动作
- `skills/write-script/SKILL.md`：新开一节「缺画面就补，所有题材通用」，把 §140–150 纪录片专属的补料流程提出来。写稿时发现缺受力图、数据图、生活照，就登记 `02-asset-requests.json`，走 acquire-assets，`register --to-patch`，`ingest_patch --anchor-only`，再写 `锚点: <补丁池名> SPxx 0:00`。可复用的素材（设定集扫描这类）登记进番池 `--pool <番>`，一次性的生活照/图表放补丁池。§8.1 写明机检段落数上限 = 时长上限 ÷ 8 s，拆短段不会触发机检。纪录片那节保留它自己的硬门禁（物证表、≥3 处 SP），改成引用通用节。
- `skills/acquire-assets/SKILL.md`：`scan` 的语义扩成「任何静态图片：扫图/图表/示意图/实物照」；渠道表加 Wikimedia Commons（带授权信息）、论文/科普原图、官方资料页；写明「web_fetch 返回 `images`」。
- runbook `02-script.md` 加写稿期补料子步骤；`04-clips.md` 04.5/05 补上 `--anchor-only`；`docs/WORKFLOW.md:45` 补料通道改成「02 写稿期或 04 之后」（守住 ≤100 行，`tests/test_docs_invariants.py` 会查）。

## 验证

每个 PR 完成后：
```bash
.venv/bin/python -m pytest -q                      # 全量，目前基线 2928 passed
.venv/bin/python -m pytest -q tests/test_docs_invariants.py
```
每个 PR 记一次变异检验（按上面各条，改坏、跑测试、确认变红、恢复），结果写进 spec 的施工记录。

端到端（需要 T7 在线，`data/` 可达）：
1. 找一张 ≥1600 短边的图，复制一份已有期做临时期（scratch，不碰真实期），跑 `acquire register <图> --to-patch <临时期>` → `ingest_patch <临时期> --anchor-only`。确认没调云端，`04-patch/pool.json` 资产是 `indexed: false`。
2. `ffprobe` 看生成的 `-still.mp4`：12 s、1920×1080、yuv420p、23.976、无音轨。抽首/中/末三帧，确认一直在推镜。
3. 在临时期 `02-script.md` 里写一段 `锚点: <池名> SP01 0:00`，跑 `check_script`，「锚点格式」和「锚点指向素材」要绿；再跑 `clips`，这一段要解析成补丁资产。
4. 拿 30 段左右的微单元稿跑 `check_script`，「段落数」要绿。
5. ava 里让 agent `web_fetch` 一个 Wikimedia Commons 文件页，返回里要有 `images` 原图直链。

全部做完后，desktop 有改动才重新 `npm run release-build`。预计 desktop 不用动。

## 施工记录

2026-10-13 施工（人离开期间无人值守完成，需人拍板的项跳过、列在文末）。分支 `d84-external-stills`，未提交。

**全量**：`pytest` 2950 passed（基线 2928，新增 22 条）；`test_docs_invariants` 绿。

| PR | 改动 | 新增测试 | 变异检验 |
|---|---|---|---|
| A | `check_script` 段落数上限统一为 `max(max_segs, 时长上界×60/8)`；默认带 20 → 37 | `TestMicroUnitSegCap` 2 条 | 改回固定 20 → 「30 段能过」变红 ✓ |
| B | `check_script` 认本期补丁池前缀（只放行 SP 键）；「集号在素材库」「锚点指向素材」改走 `ingest_patch.load_pool`；补丁池名写错 / 还没建池时报错给出真名或下一步命令 | `TestPatchPoolAnchors` 8 条 | 换回 HEAD 版 `check_script`：补丁池 7 条全红（**bug 属实**）；只去掉「指向素材」的补丁池分流 → 3 条红 ✓ |
| C | `ingest_patch --anchor-only`（切镜头登记即写 `pool.json`，资产 `indexed: false`，不打标、不碰云端、不要 floor）；完整 ingest 沿用已登记编号、翻 `indexed: true`；`load_pool` 无 `indexed` 键按已打标读（旧池零漂移），`indexed: false` 只进 sources/shots；`clips` 救援只在补丁池有已打标镜头时才跑，否则 stderr 如实提示；抽出 `_write_pool` 复用并发保护 | `TestAnchorOnly` 4 条、`TestAnchorOnlyPatchPool` 1 条、check_script 1 条 | `load_pool` 把 `indexed: false` 当 true → 3 条红 ✓；救援闸改回 `bool(patch)` → clips 用例红 ✓ |
| C 连带 | `status` 的「补料挂起」：原图已转出 `<名>-still.mp4` 且已登记时不再算挂起（原先每补一张图永远误报一条） | resolver 1 条 | 换回 HEAD 版 `status` → 红 ✓ |
| D | `STILL_SECONDS` 6 → 12；缩放改为按帧号 `1+step*on`，末帧刚好 1.15 | `test_ao3_still_微动推满全片` | 换回旧表达式 → 红 ✓ |
| D 连带 | **方案外，必要**：`clips._overlaps` 的「同一物证可跨段复用」豁免原先只认「SP 且 ≤8 s」代理，12 s 微动会让它静默失效（注释早有预警）。转图入库登记显式 `still: true`（`ingest.register(still=)`、`acquire register`、`ingest_patch`），`clips._is_micro` / `crosscheck._is_micro` 先读标、无标老池走代理 | clips 1 条、crosscheck 1 条、register / ingest_patch 断言各 1 处 | `_is_micro` 去掉 still 分支 → 红 ✓；e2e 去掉 `pool.json` 的 still 标 → 第二次引用同图变 `anchor_overlap` ✓ |
| E | `web_fetch` 返回 `images` / `images_truncated`（og:image / twitter:image 在前，`<img>` 取 srcset 最大档，data: 占位退 data-src，去重回填 alt，纯数字 width/height）；`crawl` 从渲染后 HTML 抽同一口径；工具描述写明「交 acquire_propose，不许自己下载」 | web 2 条、crawl 1 条（另改 1 条内部返回形状断言） | meta 排序反转 → 红 ✓（注：首轮因同长度替换 + 同秒恢复命中旧 `.pyc`，清缓存后复核） |
| F | write-script SKILL 新增 8.5「缺画面就补（所有题材通用）」、8.1 写明段数上限、8.2 加 E 类外部素材；acquire-assets SKILL `scan` 扩义、渠道表加图表 / 示意图行；runbook 02 第 2.5 步、04 `--anchor-only`；WORKFLOW 补料通道行；`creative.md` / `asset.md` 各加一行；ADR-0010 补记 | — | — |

**PR-E 常量实测表**（2026-10-13，`_extract_images` 对 curl 存下的页面）：

| 页面 | 图片条数 | JSON 累计字符 | 单条最长 |
|---|---|---|---|
| Commons 文件页（Pressure_exerted_by_collisions.svg） | 10 | 2157 | 330 |
| 中文维基「压强」 | 38 | 6034 | 272 |
| 英文维基 Pressure | 68 | 11412 | 280 |
| Commons 媒体搜索 water bottle | 44 | 11078 | 354 |
| 少数派长文 | 12 | 1748 | 213 |
| IT 之家文章（懒加载，静态 HTML 无图） | 0 | 0 | — |

取覆盖全部样本的最小整十 / 整千值：`IMAGES_MAX_COUNT=80`、`IMAGES_MAX_CHARS=12000`（与 links 同定法）。百度百科 403、B 站专栏页 3 KB 壳，未计入。

**端到端**（T7 在线，scratch 临时期，复制《束胸力学》`01-topic.md`，不碰真实期）：
1. 2400×1700 测试图 `acquire gate` PASS；`ingest_patch --anchor-only` 4.6 s 完成，未调云端，`pool.json` 资产 `still: true` / `indexed: false`，镜头表 1 个镜头 0–12.012 s。（`acquire register --to-patch` 只认 `data/episodes/` 下的期，为不在真实期列表里造临时期，这一步手动把图放进 `patch_assets/`；register 的转码与 still 标由真 ffmpeg 单测 `test_ao4` 覆盖。）
2. `ffprobe`：12.012 s、1920×1080、yuv420p、24000/1001、无音轨；相邻帧差均值首段 0.84、末段 0.78（旧写法第 100–109 帧 0.001，已静止）。
3. 30 段微单元稿 `check_script`：段落数 8–41 PASS、锚点格式 / 指向素材 PASS（含 2 段补丁池锚点）；池名写错时报「本期补丁池是 ep-d84-patch」。
4. `clips`：两段引用同一张 12 s 图表 + 一段伪恋锚点，3 段全 `ok`，stderr 打印「救援检索不可用」；去掉 still 标重跑 → 第 2 段 `anchor_overlap`。
5. 真实 `web_fetch` Commons 文件页 → 10 条 images（首条为 og:image 1280px 渲染）；真实 `crawl` 少数派 → 12 条。首次 `web_fetch` 遇一次 `SSL: UNEXPECTED_EOF`，重试即过（网络瞬断，非代码问题）。

**未做 / 偏离方案**：
- **渲染未跑**：scratch 期没有真实配音，06 渲染没在补丁池静态图上实跑；clip 里多出的 `still` 键不被 render 读取（它只读 source/start/dur/extend）。
- **`02-asset-requests.json` 扩进白名单**（Q2）：写稿 skill 原先（纪录片节）要求 agent 登记这个文件，但它不在 `EPISODE_WRITABLE_FILES` 白名单（测试钉死），agent 实际写不了。影响评估：写入护栏不变（期目录根、精确文件名、防 symlink、原子写、覆盖前留底），无代码读它，按 ADR-0030 免卡；只多一个工具 schema 枚举值。人裁决扩入；8.5 节第 1 步改回登记它，候选 `why` 照写。
- Commons 的 SVG：`register` 不收 `.svg`；skill 里写了用 3840px PNG 渲染（实测 3840 可取、2400 返回 400）。
