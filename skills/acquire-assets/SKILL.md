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

## 二、衔接顺序

```bash
# 1. 检索（你）→ 写 candidates.json，然后交人过目
# 2. 人逐条批准后，一条一条抓（不要批量抢跑）
python -m pipeline.acquire fetch 3 --dry-run     # 先看要执行什么
python -m pipeline.acquire fetch 3               # 抓 → data/library/incoming/
python -m pipeline.acquire gate data/library/incoming/xxx.mp4   # 门禁四项
python -m pipeline.acquire register data/library/incoming/xxx.mp4 --pool EGOIST --as SP19
# 3. 入库后才有素材意义：切镜头 → 标定阈值 → 出画廊
python -m pipeline.shots calibrate data/library/raw/EGOIST/SP19.mp4   # 看密度表，人拍板阈值
python -m pipeline.shots build EGOIST SP19 && python -m pipeline.shots gallery EGOIST SP19
```

- `register` **只收视频**（渲染要 mp4）。扫图先转 6s 微动再登记——命令在 `acquire register` 的报错里
  直接给出（`01-assets-video.md` 铁律三：1920×1080 / yuv420p / 23.976fps / `-an`，四样锁死，
  否则 concat 时报 `parameters do not match` 整片崩）。
- `register` 会把文件挪进 `data/library/raw/<池>/SPxx.mp4` 并**强制过完整性校验**（`ingest.intact`）：
  集键与文件名同号，池里十几条素材靠这个约定才看得懂。
- 新集键从池的最后一个号往后接。EGOIST 池现在到 **SP18**，新素材从 **SP19** 起。
- **不要重复跑 `fetch`**：URL 抓过、文件名撞了，`fetch` 会拒（查重台账在
  `incoming/fetched.json`）。要重抓先在台账里删掉那条记录——那是人的显式动作。
- 抓大文件（>2GB）**先报体积等人确认**。

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
（STANDARD §五）。

1. **静态抓取**（`acquire fetch` 的 curl / yt-dlp）：覆盖九成。
2. **`agent_crawl`（无头静默，优先）**：Crawl4AI + Camoufox，0 弹窗不抢焦点、不碰你的浏览器。
   内置 `fit_markdown` 过滤广告，Token 省 85–90%；严格反爬 / Cloudflare 盾时开 `stealth`。
   **长文考据与图片直链嗅探首选它。**
3. **`agent_browser` + 独立持久化 profile**：需要登录态（B站收藏夹、微博、Discord）时才上：
   ```bash
   # 独立 profile，拉起可见窗口，由人扫码/登录一次，后续会话复用
   --headed --profile ~/.config/pi-browser-profile
   ```
   ⚠️ **严禁 `--profile Default`**：主力 Chrome 运行时存在 SingletonLock 与 macOS Keychain
   加密阻断，会抢焦点、会把你的登录态写坏。（`STANDARD.md` §五 里「`agent_browser` 带
   `--profile Default`」那句是 2026-09-03 的旧写法，已被全局 `AGENTS.md` 的红线推翻。）

**反爬失败的正确结局是「停下来说抓不到」**，不是换一个质量更低的源顶上。

## 五、EGOIST 首期应用笔记

### 池子现状（2026-09-11，共 18 条）

| 集键 | 是什么 | 时长 |
|---|---|---|
| SP01–SP03 | 官方 MV《名前のない怪物》《当事者》《咲かせや咲かせ》 | 6/4/3 分钟 |
| SP04 | 2020.08.09「LIVE on www 2020」线上 Live | 7.5 分钟 |
| SP05 | 2023.10.09 横滨终场（**精华版**） | 19 分钟 |
| SP06–SP17 | 6s 微动物证（选秀公告、reche 出道视觉图、活动终了公告、三张专辑原画等） | 6s×12 |
| SP18 | 一条 2 小时 720p 长片（**还没切镜头表**） | 122 分钟 |

**素材总量是 v1 成片的死因之一**：18 条里 12 条是 6 秒微动，真正能动用的连续画面只有
SP04/SP05 两段 Live + 三条 MV。

### 缺口清单（`01-assets-video.md` 已明确标「缺」的）

| 缺口 | 为什么关键 | 检索起点 |
|---|---|---|
| **2023 横滨终场 Live 全场** | 现有 SP05 是 19 分钟精华版，缺「深鞠躬→碎光消散→观众席亮灯」的完整过程；三部曲终局的最高潮 | 「EGOIST Resonant Indigo Echoes of Everlasting」「EGOIST 横滨 1009 全场」「EGOIST LIVE 2023 パシフィコ横浜」；Nyaa 搜演唱会碟 rip |
| **动捕幕后 / making-of** | 「台前完美 3D 全息 vs 台后 20 岁女孩戴反光球汗流浃背」是全片最震撼的对比，**现有 18 条里一张幕后画面都没有** | 官方 YouTube「EGOIST メイキング」「EGOIST ムービー」；2015–2017 全息巡演的纪录片特典；B站搜「EGOIST 幕后」 |
| **ryo / chelly / reche 访谈** | 隐线的直接证词（选秀细节、皮套下的处境、2021 独立） | 「ryo supercell インタビュー EGOIST」「chelly インタビュー」「reche インタビュー 2021」；音乐ナタリー / リアルサウンド / CINRA 的文字访谈页 |
| **场刊 / 演唱会小册子扫图** | 排版的实物物证（与 6s 微动物证同类，但清晰的原图才有排版价值） | 「EGOIST 場刊 スキャン」「EGOIST パンフレット」「EGOIST 会場限定」；煤炉/雅虎拍卖的商品图（**原图**） |
| 门票票根 / 场馆外排队实录 | D 类物证里唯一还空着的一条（`01-assets-video.md` D 类第 7 条） | 微博/B站终场 repo、推特 #EGOIST 标签的当日实拍 |

### 顺手能补的小件

- **《Departures》《The Everlasting Guilty Crown》的 NCOP/NCED**：BDRip 特典里有，目前只有 CD 原画扫图，没有纯净动画画面。
- **伊藤计划三部曲 / Fate / 甲铁城**的 OP/ED 视频：三期分别要用，池子里现在一条都没有。

### 别做的事

- **别下「EGOIST 全曲合集」「12 年合集」这类大包**：几十 G 里能用的往往只有几分钟，带宽和时间都贵。
- **别用带电视台角标（右上角）的广播录像**当 MV/试听段素材——铁律四要求纯净画面。
- **别把「已经入库的」当候选**：SP01–SP18 的清单在上面，先看一遍再检索。
