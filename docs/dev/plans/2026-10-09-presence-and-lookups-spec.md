# 在场索引开放给 agent + 回合脚注的查证计数 + 删 tagger general 标签（D54 ③、D48 ①、N2）

日期：2026-10-09　状态：**施工中**
关联：D54（编造说话人 / 在场人物）、D48 ①（回合脚注看不出本轮查没查）、N2（`gen` 死字段）、ADR-0025（工具数上限）、`2026-10-08-fact-sourcing-spec.md`

## 人的裁决（2026-10-09）

在场索引「应该给」agent；D48 ① 做；N2 删。

## 读码与实测

- 在场索引已有只读 CLI：`python -m pipeline.vindex who <番> <集> --start <秒> --end <秒>`，列时段内每个镜头的已识别角色（`load_presence` + `shots.load` + `display_names`，全链只读）。`vindex` 早在 `ASSET_COMMANDS`，只放行 `captions` / `embed`。
- 实测（只读，东京喰种 S02E07）：315 个镜头里 115 个有已识别角色（37%）；段 21 那一刻（19:49–19:59）只认出董香——足以否定「西尾锦在天桥上」，但**认不出雏实**：她本集出现 16 次，都不在这个时段。**索引只认得已贴名、脸被检出的角色，没列出不等于不在场。**
- 回合脚注（`desktop/src/shared/convFold.ts::footerText`）只有调用计数；tool 帧只带工具名和摘要，桌面端分不清 `search_notes` 查的是字幕还是笔记。
- `gen`：151 个 presence 文件全部 `producer=ccip`、`gen` 全空，全仓无读者（N2 复核）。

## 设计

### A. 开放 `vindex who`（不加工具）

- `ASSET_COMMANDS["vindex"]` 加 `who`；新增 `READONLY_ASSET_SUBCOMMANDS = {"vindex": {"who"}}`，`review_tool_call` 对它免卡（与 `corrections check` 同路：只认规范化 argv）。
- 输出改造：`--start/--end` 兼收秒数与 `mm:ss` / `hh:mm:ss`；集号大小写不敏感；本集不在索引里 → 明说「没有本集的在场索引」；时段内一个都没有 → 「该时段没有已识别角色（不等于没人）」；末尾固定一行覆盖率与免责：「本集 N 个镜头，M 个有已识别角色；只列已贴名、脸被检出的角色——没列出不等于不在场」。拼装抽成纯函数 `who_lines`（`load_presence` 要核对本机模型缓存，测试环境不一定有）。
- 提示词：`director.md` §5 查证顺序「整集台词 → 在场索引 → 笔记 → 分集剧情」，写明命令与「没列出不能当不在场的证据」；`creative.md` 剧情断言条同步。

### B. 查证计数（D48 ①）

core 按**实际执行**的调用分类计数，桌面端只显示，不判断编没编：

| 类 | 计入 |
|---|---|
| 字幕 | `search_notes` 且 `source="subs"` |
| 在场 | `run_pipeline` 且规范化后是 `vindex who` |
| 笔记 | `search_notes` 其余 source |
| 网页 | `web_search` / `web_fetch` / 抓取 / 浏览器类工具 |

`run_tool_loop` 的 outcome 加 `lookups`；`turn_finished` 帧与 `turn_end` 记录带上（Spec 9 §3 帧表加修订注）；桌面端 `footerText` 追加「· 查证 字幕 N · 在场 N · 笔记 N · 网页 N」，0 也显示——人要看的正是「说核对过、字幕却是 0」。

### C. N2

删 `gen` 的收集与落盘（`vindex.tag` / `write_presence` / `build_presence`、`faces.py` 的空 `gen`、测试夹具）。`load_presence` 本就不读它；旧文件多一个空字段无害，不迁移。

## 测试与变异

| 编号 | 场景 | 期望 |
|---|---|---|
| TP-1 | `who_lines`：时段过滤、空时段文案、尾注覆盖率 | 逐行相符 |
| TP-2 | 时间参数 `95`、`1:35`、`0:01:35`、坏值 | 95.0 ×3；坏值报错 |
| TP-3 | `validate_pipeline_command`：`vindex who …` 放行；`vindex search` 仍拒 | — |
| TP-4 | `review_tool_call`：`vindex who` 免卡；`vindex captions` 照旧弹卡 | — |
| TP-5 | 替身模型依次调字幕、笔记、`vindex who` → outcome `lookups` | `{subs:1, notes:1, presence:1, web:0}` |
| TP-6 | vitest：脚注含「查证 字幕 0 · 在场 0 · 笔记 0 · 网页 0」；旧帧无 `lookups` 不显示 | — |
| TP-7 | N2：presence 行无 `gen` | — |

变异：P-MUT-1 去掉免卡分支；P-MUT-2 `who` 不在放行集；P-MUT-3 字幕记成笔记；P-MUT-4 删尾注。
