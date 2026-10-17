# D103 ② 小 spec：番池素材的云端打标命令

日期：2026-10-17（v0.1）；**人 10-17 裁决：做（按 coding agent 建议）**；人看过施工图说开工，同日已施工·机检通过，施工记录见第七节
关联：issues D103、D107、D102、ADR-0016、`skills/acquire-assets/SKILL.md` 第二节第 4 步

## 一、要解决什么

番池素材（`data/library/` 下的番剧集与 SP）要能被检索，必须在云端跑 `vindex captions`（VLM，本机跑不动）。现在 AVA 做不了这一步：

- `cloud push / run / pull` 只认期目录：`build_remote_run_command` 的 captions 分支把 shots / frames / out 三个目录钉在 `<期>/04-patch/` 下，`build_sync_up_files` 也只同步期目录里的文件。
- 能跑任意远端命令的 `cloud exec` 对 AVA 永久禁用（`tools.py` 护栏，R1-r10）。

所以每个新池打标都要人或 coding agent 手动 rsync 加 `cloud exec`（10-17 supercell 63 件就是这样）。这条手动路径还绕过了 D107 给 `cloud run` 加的远端代码核对：旧代码打标的问题在这条路上照样会发生。

## 二、现状（2026-10-17 读码与实跑核实）

| 环节 | 本地路径 | 远端（`remote_root` 下同一相对路径） | 10-17 怎么做的 |
|---|---|---|---|
| 镜头表 | `data/library/shots/<池>_<集>.json` | 同 | 手动 rsync 上行 |
| 打标帧 | `data/library/shots/frames/<池>_<集>_cap/` | 同 | 手动 rsync 上行 |
| 打标 | — | `python -m pipeline.vindex captions <池> <集…>`（不传目录参数即用上面两处的默认路径） | `cloud exec` |
| 产物 | `data/library/vindex/<池>_<集>.captions.json` | 同 | 手动 rsync 下行 |

打标只读这两样输入加 `config/`（10-17 实跑核过：只传这两样就能跑通）；`config/` 与代码由 D107 的核对保证一致。

## 三、改法

在 `pipeline/cloud.py` 加三个子命令，路径全部从 `shots.SHOTS_DIR` / `shots.FRAMES_DIR` / `vindex.VINDEX_DIR` 推出相对路径，不另写一份：

| 命令 | 做什么 | 卡 |
|---|---|---|
| `cloud lib-push <池> <集…>` | 每个集键：本地镜头表与 `_cap` 帧目录必须都在，缺一个就整批拒（报缺什么、先跑哪条 `shots` 命令）；过 `_preflight_remote_capacity`；rsync 上行 | 与 `push` 同级（免卡） |
| `cloud lib-captions <池> <集…> [--batch N] [--confirm-cost] [--limit N]` | 先过 D107 代码核对，不一致不开跑；记会话任务；tmux 后台跑 `vindex captions <池> <集…>`，参数沿用现有 captions 分支的白名单校验 | 计费卡（进 `CLOUD_CARD_SUBCOMMANDS`） |
| `cloud lib-pull <池> <集…>` | 逐个集键 rsync 下行 `<池>_<集>.captions.json`；远端还没有的列出来、不当成功；本地已有同名文件时在输出里写明「覆盖」 | 与 `pull` 同级（免卡） |

- **集键**：`SPnn` 或 `SxxEyy`（番剧集与 SP 在 `library/` 下路径口径相同，Phase 0 补打标也能用）。
- **池名**：允许中文（企划名、番名常是中文）；拒绝含 `/`、以 `-` 开头、含空白或控制字符的；所有路径过 `shlex.quote`。另要求本地 `data/library/shots/` 下真有这个池的镜头表，防止拼错池名把空目录推上去。
- **只拉指定集键**：不按 `<池>_*.captions.json` 整池拉，避免把远端旧产物盖到本地新产物上。
- 不改 `push / run / pull` 的期目录语义，不动 `cloud exec` 护栏。

AVA 侧：`tools.py` 的 cloud 白名单加这三个子命令；`skills/acquire-assets/SKILL.md` 第二节第 4 步第 5 小步改成 `lib-push → lib-captions → lib-pull`，删掉「在那之前停下告诉人」那句；runbook 04-clips 加一行指路。（说明文字由 coding agent 起草，按 D101 交人过目。）

## 四、不做

- 不把上传、打标、拉回合成一条命令：打标在 tmux 后台跑（几十分钟），拉回必须等它结束，和期目录流程一样分开。
- 不做整池自动发现集键：要打哪些集由人与 AVA 在对话里定，命令里写明。
- 不改 `vindex captions` 本身。

## 五、测试

- 纯函数：上行清单、远端命令、下行清单三个构造函数的路径与引号（池名含中文与空格的拒收用例）。
- `lib-push`：缺帧目录整批拒，且一条 rsync 都不发。
- `lib-captions`：D107 核对不一致不开跑（复用 `_remote_manifest` 假数据）；参数白名单拒收未授权旗标；会话文件记任务。
- `lib-pull`：远端缺某个集键时退出码非 0 并列出。
- 白名单与卡：`lib-captions` 在 `CLOUD_CARD_SUBCOMMANDS`，三个都过 `validate_pipeline_command`。
- 变异检验：去掉帧目录检查、去掉代码核对、改成整池拉，各自应有测试变红。

## 六、完成判定

`pytest` 全绿（已知与本改动无关的 2 条 `test_agent_assembly_integration` 除外）；变异检验全红；人下次租卡时用一个小池（或 supercell 里 1–2 个 SP）跑通 `lib-push → lib-captions → lib-pull → vindex embed → vindex status`。

## 七、施工记录（2026-10-17）

- `pipeline/cloud.py`：`lib_dirs`（从 `shots.SHOTS_DIR` / `shots.FRAMES_DIR` / `vindex.VINDEX_DIR` 推相对路径）、`validate_lib_target`、`lib_missing_inputs`、`build_lib_up_pairs`、`build_lib_captions_command` 五个纯函数；`cmd_lib_push` / `cmd_lib_captions` / `cmd_lib_pull`。
- 与施工图的差异：`lib-captions` 的参数改成显式的 `--batch` / `--limit` / `--confirm-cost` 三个 argparse 选项，不走 `validate_extra_args` 的透传白名单（只有这三个有用，目录参数在番池模式下不该被改）。另加两条施工图没写的护栏：上一批 `ava-lib-captions` 还在跑时，`lib-captions` 拒绝（同名 tmux 会话会被先 kill，再发一次等于打断）、`lib-pull` 拒绝（中途拉到的可能是半份产物）。
- 白名单：`tools.py` cloud 子命令集加三条；`session.CLOUD_CARD_SUBCOMMANDS` 加 `lib-captions`。
- 说明文字：SKILL 第二节第 4 步第 5 小步、runbook 04-clips 一处指路。
- 测试 +18（`tests/test_cloud.py`），变异 4/4（去帧目录检查、去代码核对、改整池拉、去「上一批在跑」检查）。
- 本机冒烟（实例已关机）：`lib-push supercell SP02,SP03` 本地检查通过、停在「实例不可达」；`SP99` 报缺镜头表与打标帧；池名拼错 `supercel` 报「池名写错了」。
- 待人：下次租卡用 supercell 里 1–2 个 SP 跑通 `lib-push → lib-captions → lib-pull → vindex embed → vindex status`。
