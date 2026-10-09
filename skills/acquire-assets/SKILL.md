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
| `type` | ✅ | `live` / `mv` / `scan` / `interview` 之一 |
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

## 二、衔接顺序（全程你跑，人只批卡）

**人不在终端敲任何命令**（2026-10-09 D59）。下面每一步都是你经 `run_pipeline` 发的命令：只读的免卡，
写盘的弹卡，人在卡上批或驳。

1. **检索 → 提议**：写好候选，调 `acquire_propose`（一张卡审整批）。批准后内核逐条弹「素材抓取」卡，
   批一条抓一条（`acquire fetch <号>`，不许批量抢跑）。抓大文件（>2GB）在提议的 `why` 里先写明体积。
2. **门禁（免卡）**：抓完立刻跑 `acquire gate <文件>`，把判据表原样给人看。
3. **登记（弹卡）**：gate 没有 FAIL 才提议 `acquire register <文件> --pool <池>`。
   - 不给 `--as`：自动取池里最后一个号 +1，卡上的命令里会写出算好的号；要指定就写 `--as SPnn`。
   - **扫图直接登记**：register 遇到图片自动按 `01-assets-video.md` 铁律三转 6 s 微动视频
     （1920×1080 / yuv420p / 23.976 fps / `-an`，四样锁死），原图留在 `incoming/`。
   - **期内补料**：`acquire register <文件> --to-patch <期目录>` 把文件挪进本期 `patch_assets/`
     （不进 `sources.json`），接着跑 `ingest_patch`（见 runbook 04.5）。
   - gate FAIL 但人坚持要收：`--waive "<理由>"`（卡上标 `[门禁豁免]`，理由写进登记输出）。不许自己决定豁免。
4. **入库后切镜头**：`shots calibrate <文件>`（弹卡）→ 把密度表给人，人拍板阈值 →
   `shots build <文件> --anime <池> --sp <N>` → `shots gallery <池> SPnn`（都弹卡）。
5. **要重抓**：`acquire forget <候选号>`（弹卡）把那条从抓取台账 `incoming/fetched.json` 移走（留痕进
   `incoming/forgotten.json`，没登记的下载文件挪进 `incoming/attic/`，不删），再走抓取卡。已登记的拒。

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
| `scan`（扫图/场刊/CD 原画） | 知乎/微博/B站专栏的考据长文、贴吧、Discord 图源频道、曲子实体附赠 booklet | 图源网站常压缩到 1200px 宽，**短边 <1600 直接被门禁拒**（放大到 1080p 会糊）——找原图，别存缩略图 |
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
  （2026-09-11 快照：SP01–SP18、缺口清单、别做的事）。检索前先用 `vindex status <池>` 和
  `sources.json` 核对池子现在到几号，不要信快照里的号。
