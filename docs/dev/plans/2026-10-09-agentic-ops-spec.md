# 「人在终端跑」全部收进 agent：人只批卡，不敲命令（D59）

日期：2026-10-09　状态：**已施工·机检通过（2026-10-09）**；两项待人定见文末
关联：D59、ADR-0021 §3（fetch/gate/register 走白名单——施工时只接了 fetch）、ADR-0025（工具数上限）、ADR-0020（停机点由人）、Spec 9（审卡）、`skills/acquire-assets/SKILL.md`

## 起因

人反馈（2026-10-09）：素材流程里 `acquire gate` / `register` 要人自己在终端跑，「一点也不 agentic」。人裁决：**所有需要人在终端跑的操作和对应文档都要改成 agent 跑**。

病根是把「要人批准」和「要人亲手敲」混成了一件事。批准只需要一张审批卡；手敲命令既不比卡片更安全，又把人时花在搬运上。ADR-0021 §3 当初写的是「fetch/gate/register 走现有 `pipeline.acquire` 白名单命令」，S6-R1 施工时只接了 `fetch`，`gate` 和 `register` 一直漏在白名单外。

## 原则

1. **只读的免卡，写盘的弹卡，判断留在卡上和停机点。** 免卡的标准沿用 `READONLY_PIPELINE_MODULES` 既有口径：逐个子命令 grep 核实全链无写调用。用系统临时目录、用完自清的算只读。
2. **不加工具**：只扩 `run_pipeline` 白名单、补少数子命令。ADR-0025 的工具数不动。
3. 在 `run_pipeline` 里，`--force` / `--force-all` 照拒，`cloud exec` 照拒，白名单外照拒。
4. 停机点不动：05 approve、09 定稿仍然只能由人在桌面端决策条（或终端 `/approve`）完成（ADR-0020 §3）。
5. 文档口径：主路径写「让 agent 跑（免卡 / 弹卡）」，`python -m` 只作底层排查参考保留。

## 盘点

### A. 白名单外、只能人跑 → 进白名单

读码核实（2026-10-09）：
- `acquire gate`、`ingest probe/intact`、`vindex status/search`、`subindex search` 只打印；
- `ingest verify` 用 `tempfile.mkdtemp` 并在 `finally` 里自清；
- `shots calibrate` 带 `--sheet/--long` 时会写联系表，所以整体弹卡。

| 模块 | 免卡（只读） | 弹卡（写盘） |
|---|---|---|
| `acquire` | `gate` | `fetch`（已有，抓取卡）、`register` |
| `ingest` | `probe`、`intact`、`verify` | `subs`、`run`、`sources`；`phase0` 已有 |
| `shots` | — | `calibrate`、`rebuild`、`gallery`；`build/frames/caption-frames` 已有 |
| `vindex` | `status`、`search`；`who` 已有 | `presence`；`captions/embed` 已有 |
| `subindex` | `search` | `build` |
| `vprobe` | — | `tagger`、`presence`、`scene`、`captions`（01.4 换条件重测用，会写抽检表） |
| `scout`、`ingest_patch`、`timeline` | — | 整模块弹卡；`scout`、`ingest_patch` 自动补期目录 |

### B. runbook 里要人手敲的 shell 片段 → 补成子命令

| 现在要人手敲 | 改成 |
|---|---|
| 扫图 / 补丁图转视频的 `ffmpeg -loop …`（`acquire.py` register 报错、`ingest_patch.py` 报错、runbook 04.5） | `acquire register` 与 `ingest_patch` 遇到图片自动按铁律三转码：1920×1080（等比缩放后加黑边）/ yuv420p / 23.976 fps / 6 s / `-an`；原图保留不删 |
| 补丁画廊 `python -c "…gallery(…)"`（runbook 04 §05） | `shots gallery --patch <期目录> <SPxx>` |
| 补丁素材要人手放进 `patch_assets/` | `acquire register <文件> --to-patch <期目录>`：把 `incoming/` 里的文件挪进本期 `patch_assets/`（弹卡） |
| 「删 04-patch 目录，或重跑」（`ingest_patch.py` 七处报错） | `ingest_patch --reset`：旧 `04-patch/` 挪到 `04-patch.attic/<时间戳>/` 后重建；不真删 |
| `rm 03-audio/.apply_patch.lock`（03.5「锁残留」） | `tts --clear-stale-lock`：锁里记录的 pid 还活着就拒，死了才清（弹卡） |
| 远端 `mv data` + `ln -s`（WORKFLOW 阶段 0） | `cloud relocate-data`：已经是软链就报 OK 退出；远端有任务在跑、或数据盘上已有 `data/` 就拒；否则挪过去并软链（弹卡，云端动作） |
| 余量闸报错里的 `cloud exec --fg "rm -rf …/*_cap"`（agent 永远不许用 `exec`） | `cloud clean-frames`：清远端上一轮打标帧（本地可秒级重抽），有任务在跑就拒（弹卡） |
| 03.5 结构化打点 `tts --review …` 只写在「终端备用」里 | 改写为 agent 跑（弹卡）；本来就在白名单里，只是文档没说 |
| `cloud doctor` 修法里的 `cloud exec 'git clone …'` 与 `cloud exec '…snapshot_download…'` | `cloud fix-env`：按 doctor 同一判据，仓库缺了就克隆、模型缺失 / 体积不足 / 结构校验不过就从镜像重下（模型 id 只来自 `config/cloud.json` 的 `models`）；有任务在跑就拒（弹卡） |
| 02.5 未封板时报错让人敲 `git diff --no-index …` | 改指桌面端「封板」/ `/seal-script`（批准 02.5 时本来就会自动封板） |
| `register --as SPnn` 要人去数池子里最后一个号 | 不给 `--as` 时自动取最后一个号 +1，打印出来 |

### C. 已有桌面端入口，只是文档过期 → 只改文档

- 02.5 封板 `git diff --no-index`：已有桌面端「封板」按钮，批准时也会自动封板（D48）。
- 05 `open 04-review.html`：预览区会打开审看页；`review --approve` 由桌面端决策条完成。
- `uv run python -c "…to_tone3…"`：由 `corrections check` 覆盖（免卡）。
- `/scout` 派工单「交由 pi 采掘」：按 ADR-0021 §4，常规补料应由 ava 自己闭环。改成 agent 跑 `scout` 拿缺口清单，自己用 web 工具查，再走 `acquire_propose`。工单只作三级工具都失败后的显式升级通道。

### D. 保留由人做

| 项 | 理由 |
|---|---|
| 05 approve、09 定稿与上传 | ADR-0020 与发布红线；桌面端已有按钮，不是终端操作 |
| 正片片源文件放进 `raw/` | 由人提供文件 |
| 封面字体 `cover.font_file` | 一次性配置，在 Code Freeze 范围内 |
| `eval` / `recheck` / `verify_mutations` | 开发评估工具，不在生产流程里 |
| 远端装 PyTorch 等环境依赖（doctor [TORCH] 修法） | 属于装全局依赖（红线 5），交人 |
| 远端数据盘「清理无用的大文件」（doctor [DISK] 修法） | 删什么是判断；可确定可删的打标帧由 `cloud clean-frames` 处理 |

## 施工批次

| 批 | 内容 |
|---|---|
| 0 | 本 spec；issues D59；ADR-0021 修订注 |
| 1 | 素材入库链：`acquire gate/register` 入白名单；图片自动转码；`--to-patch`；自动取号；SKILL 第二、四、五节；`asset.md` |
| 2 | Phase 0 资产链：A 表其余子命令；`shots gallery --patch`；runbook 01.4 |
| 3 | 期内补料：`scout`、`ingest_patch` 入白名单并自动补位；`ingest_patch --reset`；scout / status 文案；runbook 04.5 |
| 4 | 运维：`tts --clear-stale-lock`；`cloud relocate-data`；runbook 03.5、WORKFLOW 阶段 0 |
| 5 | 文档总扫：WORKFLOW、CHEATSHEET、HELP、全部 runbook、scope 提示词、代码里打印给人看的命令提示 |

## 测试与变异

全部用临时夹具，不碰真实 `data/`。

| 编号 | 场景 | 期望 |
|---|---|---|
| AO-1 | `validate_pipeline_command`：A 表每个新子命令 | 放行 |
| AO-1b | `vindex scene`、`cloud exec`、`acquire register --force` | 拒 |
| AO-2 | `review_tool_call` | `acquire gate`、`vindex status`、`subindex search`、`ingest verify` 免卡；`acquire register`、`ingest phase0`、`ingest_patch --reset`、`tts --clear-stale-lock` 弹卡 |
| AO-3 | 图片转码 | ffprobe 核 1920×1080 / yuv420p / 24000/1001 / 无音轨 / 6 s |
| AO-4 | `register` 不给 `--as` | 取池里最后一个号 +1；空池从 SP01 起 |
| AO-5 | `--to-patch` | 落进本期 `patch_assets/`，`incoming/` 原文件已挪走，不登记 `sources.json` |
| AO-6 | `ingest_patch --reset` | 旧目录进 attic，内容逐字节在 |
| AO-7 | `tts --clear-stale-lock` | pid 活着：拒，锁仍在；pid 已死：清；锁不存在：报无锁 |
| AO-8 | `cloud relocate-data`（桩 ssh） | 已是软链：零写命令；否则按序发 mv / ln |
| AO-9 | `scout` / `ingest_patch` 不带期目录 | 自动补位 |

变异（加入 `scripts/verify_mutations.py`）：

| 编号 | 变异 |
|---|---|
| AO-MUT-1 | gate 从只读集合去掉 |
| AO-MUT-2 | register 误入只读集合 |
| AO-MUT-3 | 转码丢 `-an` |
| AO-MUT-4 | 清锁不判 pid |
| AO-MUT-5 | `--reset` 改成 `rmtree` |
| AO-MUT-6 | `scout` 不补位 |

## 施工中发现、待人定

| 项 | 现状 | 选项 | 我的建议 |
|---|---|---|---|
| 标定值写进 `config/project.json`（`visual.scene_threshold.<番>`、`ccip_*`、`face_expand`；01.4 换条件重测与开新番时） | `shots calibrate` 打印「定下来写进 config/project.json …」，由人手改 JSON。AGENTS.md Code Freeze 规定 `config/` 只读，例外只有读音表（D51） | (a) 维持人改；(b) 仿 `corrections global` 开窄口子：`shots set-threshold <番> <值> --evidence "<标定实录>"`，弹卡，只写这几个按番分键的值并把实录追加进对应 `_note` | (b)：标定判断仍归人（人看密度表 / 抽检表拍板），agent 只负责把拍板的数和证据落盘；改的是 AGENTS.md 硬约束，必须人定 |
| 番剧笔记（`data/library/notes/<番>.md`）的新写与补写 | ava 没有写 `data/library/` 的工具（`write_episode_file` 只认期目录白名单），缺笔记时 `scout --type notes` 出工单交 pi，人把工单搬过去 | (a) 维持 pi 工单；(b) 给写入工具开 `notes/<番>.md` 一个目标（弹卡带 diff，同 D47 写稿卡），笔记的零上下文对抗审查（D13）照旧 | (b)，但它是新写入面（全部期共用的资产），须人定；对抗审查流程要一起定 |

