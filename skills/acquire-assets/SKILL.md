---
name: acquire-assets
description: 为素材池检索并扩充素材（Live/MV/扫图/访谈），产出人审用的 candidates.json，再走 fetch → gate → register 入库。用于动漫二创纪录片的 Phase 0 素材搜集，或用户要求「给这个池子找素材/扩充素材池/找 Live 全场」时。
---

# 素材搜集

**判断归你（agent），确定性归代码。** 检索、考据、「这本画册是不是那本」没有机器判据，
是你的活；抓取、门禁、登记有机器判据，是 `pipeline/acquire.py` 的活。三层分工不许越位：

| 层 | 谁干 | 产物 |
|---|---|---|
| 检索与判断 | 你（agent/skill） | `data/library/incoming/candidates.json` |
| 人审（**闸门**） | 人 | 逐条批准/否掉，必须留否掉的理由 |
| 抓取 / 门禁 / 登记 | `pipeline/acquire.py` | `incoming/` 里的文件 → `sources.json` |

**不许自动 fetch。** 版权与带宽风险由人的显式动作承担——你只负责把候选和判断摆到他面前。

## 一、交接契约

`data/library/incoming/candidates.json`（数组，一个候选一条）：

| 字段 | 必填 | 说明 |
|---|---|---|
| `title` | ✅ | 人一眼能认出是哪个素材的名字，**别用网站原标题原文**（含站点广告词） |
| `url` | ✅ | 可解析的 http(s) 直链或视频页 |
| `type` | ✅ | `live` / `mv` / `scan` / `interview` 之一；`scan` = 任何静态图片（扫图、图表、示意图、实物照，D84） |
| `source` | ✅ | 从哪儿来的：站点名 + 大致检索路径（如「B站搜『EGOIST 横滨』，UP 主 xxx 投稿」） |
| `why` | ✅ | **为什么值得下**（见下） |
| `expected_dur` | 可空 | 秒数。有考据页面写着时长就填——门禁拿它算时长偏差（>5% 报 WARN），也是「下成了剪辑版」的唯一自动哨兵 |

**`why` 是这份文件的全部价值所在。** 它让「下什么」这个判断落盘可审计：人扫一眼就能把垃圾挑掉，
不用自己再判断一遍。写法是**「它补的是哪个缺口 + 凭什么认为是它」**，不是「这个素材很好」。

- ✅ `「终场 Live 只入库了 19 分钟的精华版（SP05），这条是 2 小时全程，补『消散段落』的完整过程；标题卡与 SP05 帧内实证一致」`
- ✅ `「2015 动捕巡演的一手现场照，是『黑幕后女孩』隐线唯一的影像物证，现有 18 条素材里没有任何动捕幕后画面」`
- ❌ `「高质量的 Live 视频，值得收藏」`——没说补哪个缺口，人没法判
- ❌ `「可能有用」`——**「可能」不许进判断**（S7）

写完之后自查一遍：**每条 `why` 是不是都在回答「现有池子里缺什么、这条怎么补上」？**
答不上来的那条要么删掉，要么说明你还没查到它值得下的理由。

## 二、衔接顺序（全程你跑，人只拍板）

**人不在终端敲任何命令**（2026-10-09 D59）。下面每一步都是你经 `run_pipeline` 发的命令。ADR-0030 起它们都不弹卡
（只有 `--waive` 豁免门禁会弹），所以**有副作用的先在对话里问人、人同意再发**（director.md 第 2 条）。

1. **检索 → 提议 → 人看机器表 → 抓**：每条候选写 `found_on`（在哪一页看到它；视频页或直链本身就是出处时填它自己）。
   那页还没有抓取留档时，核对会由系统当场抓一次（静态，失败升级无头渲染，D136）；要登录态的页静态抓不到，先用
   `browser` 打开。别的 agent 在 AVA 之外查到的候选同样直接提，不必再让 AVA 抓一遍；它们抓登录态页用
   `python -m pipeline.agent.fetch <url> --browser --caller <名字>`。`acquire_propose` 只登记并核对、不下载，返回机器表；把表原样给人，人点名后 `acquire fetch <号>`（只能抓核对结论为「可下载」的）。核不上的换一个能核上的出处页重提，不绕过（D100）。
   对话里不另写候选表，不补分辨率、尺寸或授权：这些以机器表为准。
   **不许绕开 acquire 自己下载**（不用 bash 跑 curl / yt-dlp）。
1.5 **探针预检（2026-10-10 增）**：心里没底的源先 `acquire probe all`——只解析不下载，死链、
   要登录的、画质/时长对不上的在抓取前就曝光，失败条目换源重提，别拿废 URL 去抓。
   报「疑似需要登录态」的站：让人用 `browser` 工具登录一次（headed，profile 独立持久化），把该域加进
   `config/agent/web.json` 的 `browser.cookies_for`，之后 fetch/probe 会自动挂 `--cookies-from-browser`
   （ava 不碰 cookie 本体，profile 交给 yt-dlp 自己读）。
2. **门禁**：抓完立刻跑 `acquire gate <文件>`，把判据表原样给人看。
3. **登记**：gate 没有 FAIL 才提议 `acquire register <文件> --pool <池>`。
   - 不给 `--as`：自动取池里最后一个号 +1，输出里会写出算好的号；要指定就写 `--as SPnn`。
   - **扫图直接登记**：register 遇到图片自动按 `01-assets-video.md` 铁律三转 12 s 微动视频
     （1920×1080 / yuv420p / 23.976 fps / `-an`，四样锁死），原图留在 `incoming/`。
   - **期内补料**：`acquire register <文件> --to-patch <期目录>` 把文件挪进本期 `patch_assets/`
     （不进 `sources.json`），接着跑 `ingest_patch`（见 runbook 04.5）。写稿期只为写锚点就加 `--anchor-only`
     （只切镜头登记，不打标、不碰云端）。
   - **选池（2026-10-20 人定，D129）**：属于企划的卷，补料一律 `--pool <企划名>` 进企划池，只用一次的图也一样；
     补丁池只给不属于企划的期。理由：企划各卷讲同一段历史，哪张图后面的卷还会回扣，登记时判断不了，
     补丁池里的素材只有本期能锚；图片微动的次数上限按企划计，也以同一个池为前提。
     企划池的素材 register 之后就能写锚点，打标入库可以之后再做。
   - gate FAIL 但人坚持要收：`--waive "<理由>"`（卡上标 `[门禁豁免]`，理由写进登记输出）。不许自己决定豁免。
4. **番池 SP 入库全链**（登记进 `--pool` 之后、写稿要能检索到这条素材之前；2026-10-17 在 supercell 池 63 件上实跑核过，D103）。
   只登记、不往下做，这条素材只能写成时间码锚点，检索不到它：
   1. `shots calibrate <代表样本>` → 密度表给人，人拍板阈值 → `calibration set visual.scene_threshold.<池> <值> --evidence "…"`
      （单条要不同阈值时键写 `<池>/SPnn`）。
   2. `shots build <文件> --anime <池> --sp <N>`。
   3. 长镜头补刀：镜头切分对静态插画 PV、连续溶解的拼贴 MV、绘画过程认不出切点，降阈值也救不回；一个镜头只出一条描述、
      最多抽 3 帧，长镜头里的其余画面检索不到。规则：**超过 30 s 的镜头，从镜头起点起每 15 s 注一刀**（过切无害、漏切有害）。
      按规则算好每条要切的镜头与切点，整批列给人，人同意后逐条 `shots rebuild --anime <池> --episode SPnn --also-cut MM:SS,…`
      （写进 `meta.manual_cuts`，之后 rebuild 自动重放）。
   4. `shots caption-frames <池> SPnn` 抽打标帧。
   5. 云端打标：**只能在云端跑**（VLM，本机跑不动），计费，先问人。实例开着（`cloud up`）后依次
      `cloud lib-push <池> SPnn …`（传镜头表与打标帧）→ `cloud lib-captions <池> SPnn …`（计费卡；远端代码与本地不一致会拒跑，
      先 `cloud sync-code`）→ 等它跑完（`cloud attach --session ava-lib-captions` 看进度）→ `cloud lib-pull <池> SPnn …`。
      期目录的 `cloud push / run / pull` 只管 `04-patch/`，番池素材不走它们；不用 bash 自己 rsync / ssh。
   6. `vindex embed <池>`：本机跑（16 GB Mac 上 63 件不到 1 分钟）。
   7. `vindex status --anime <池>` 对账，七条数字对得上才算入库完成。

   `shots frames` + `shots gallery <池> SPnn` 出的是给人看的联系表，不在打标链上（gallery 缺 frames 会拒）。
   期内补丁池不走这条链：`ingest_patch` 不加 `--anchor-only` 时自己做完切镜头、打标、向量化（runbook 04-clips）。
   访谈类素材要字幕时，`ingest subs`（只抽内嵌字幕轨）与 `subindex build` 对 SP 是否适用还没核实，用到时先在一条上试，不照搬。
5. **要重抓**：`acquire forget <候选号>`（先问人）把那条从抓取台账 `incoming/fetched.json` 移走（留痕进
   `incoming/forgotten.json`，没登记的下载文件挪进 `incoming/attic/`，不删），再重新提议。已登记的拒。
   截图条目没有候选号，截歪了按文件撤：`acquire forget <incoming 里的文件路径>`（D138）。
6. **备选源**：候选可带 `also: [备选URL...]`（2026-10-10 增）——主源死了自动顺次换源，台账记实际命中的
   URL。老物（niconico 初投稿这类）提案时就该多源。

规矩不变：

- `register` **只收视频**（渲染要 mp4）——图片由它自己转码，你不用手写 ffmpeg。
- `register` 把文件挪进 `data/library/raw/<池>/SPxx-<名>.mp4` 并**强制过完整性校验**（`ingest.intact`）：
  集键与文件名同号，池里十几条素材靠这个约定才看得懂。
- **不要重复 `fetch` 同一条**：URL 抓过、文件名撞了，`fetch` 会拒。
- 底层排查参考：上面每条命令都等价于 `python -m pipeline.<模块> …`，人要复现问题时才用。

## 三、渠道清单（按素材类型）

**渠道三天两头变，这一节随时改。** 拿不准就先静态抓一次看有没有内容。

| 类型 | 渠道 | 备注 |
|---|---|---|
| `live`（Live 全场/片段） | YouTube、B站、N站（ニコニコ） | 官方频道常有全场回放；B站中文搬运多但常有台标/水印，优先无台标版 |
| `mv`（官方 MV / NCOP/NCED） | YouTube 官方频道、BDRip 特典原盘（NCOP/NCED） | **纯净画面优先**：前景试听段（`music.py`）必须无台标，广播录像过不了铁律四 |
| `scan`（扫图/场刊/CD 原画） | 知乎/微博/B站专栏的考据长文、贴吧、Discord 图源频道、曲子实体附赠 booklet | 图源网站常压缩到 1200px 宽；门禁按转成 1080p 缓推视频时要不要放大来判，**要放大就拒**（16:9 横图约需 2208×1242 以上，竖图、宽幅图缩进安全框，要求低得多）——找原图，别存缩略图 |
| `scan`（图表 / 示意图 / 实物照，D84） | Wikimedia Commons（文件页写着授权，`links` 里的「原始文件」是原图直链）、维基条目配图、论文与科普文章原图、官方资料页 | `web_fetch` / `crawl` 返回的 `images` 是页面图片直链（og:image 在前、`<img>` 取 srcset 最大档）。维基缩略图 URL 带 `/thumb/…/NNNpx-`，要换成原始文件。**`register` 不收 `.svg`**（只收 jpg/png/webp/bmp/tif）：Commons 上的 SVG 示意图用它的 PNG 渲染 `…/thumb/<路径>/<文件>.svg/3840px-<文件>.svg.png`（2026-10-13 实测：3840 可取；2400 这类非标准宽度返回 400）。`why` 写清授权（CC BY-SA 等要署名的在 `source` 里记作者） |
| `scan`（网页截图：官方公告、榜单、认证检索页，D122） | 页面本身就是物证的官网、ORICON / RIAJ 检索页、新闻稿、推特公告 | `browser` 的 `screenshot`：给 `selector` 或页面上原样出现的一段 `text`，只截证据那一块，不截整屏（不带导航和广告）；配音要证某句话就填 `expect_text`，截图里没有这句不保存。截图直接进 incoming 与台账，不走候选表；回执的 `gate` 是 FAIL 就换个更宽的区域再截。之后照常 `acquire register`（企划卷进企划池）。别的 agent 在 AVA 之外截图用 `python -m pipeline.agent.screenshot <url> --selector <CSS>｜--text <原文> --reason <证第几段哪句> [--expect-text …] --caller <名字>`，校验与落盘同 `browser` 截图，留档记「命令行抓」（D138）。正文栏窄、回执门禁 FAIL 时加 `scale` 3 或 4（命令行 `--scale`）：浏览器按更高像素密度排版，字是真清楚，响应式页面会换成手机版（D139） |
| `interview` | YouTube（官方/杂志频道）、音乐媒体的文字访谈网页、电视台节目 | **必须有音轨**，没音轨的访谈是死素材（门禁硬拒） |
| 种子/生肉 | Nyaa、nyaa.si 镜像、动漫花园 | 找 BDRip 特典（NCOP/NCED）、演唱会碟。**别下「合集」包**：几十 G 里只有一段有用，`fetch` 也不给你挑 |
| 官方付费 | Sony Music / EGOIST 官方商店、场刊代购、iTunes/OTOTOY | **只列清单，不下单**，写进候选让人去买 |

## 四、反爬升级链

**不许静默降级。** 403 / Cloudflare 是常态，抓不到就升级工具，不许退回「水百科凑一段」交差
（STANDARD §五）。ava 里的三级对应三个工具（ADR-0021 §1）：

1. **`web_fetch`（静态抓取）**：覆盖九成；看返回的 `links` 做站内导航，不猜 URL。
2. **`crawl`（无头静默，优先）**：被盾或 `links` 截断时升级；严格反爬开 `stealth`。
   长文考据与图片直链嗅探首选它。
3. **`browser`（登录态，逐调用过卡）**：需要登录态（B站收藏夹、微博、Discord）时才上；profile 只来自
   配置（`web.json` / `web.local.json` 的 `browser.profile_dir`，必须在 `data/` 之下），**永不接主力 Chrome 的 Default 配置**
   （SingletonLock 与 Keychain 阻断，会抢焦点、写坏登录态）。

每升一级在 `reason` 里写明下一级为何不够。下载本身仍走 `acquire fetch`（curl / yt-dlp），不是这三个工具。

**反爬失败的正确结局是「停下来说抓不到」**，不是换一个质量更低的源顶上。

## 五、各池的应用笔记

池子现状与缺口清单是**一时一池**的数据，不是规程，放在各自的计划文件里：

- EGOIST：[`docs/dev/plans/archive/2026-09-11-egoist-pool-notes.md`](../../docs/dev/plans/archive/2026-09-11-egoist-pool-notes.md)
  （2026-09-11 快照：SP01–SP18、缺口清单、别做的事）。检索前先用 `vindex status --anime <池>` 和
  `sources.json` 核对池子现在到几号，不要信快照里的号。
