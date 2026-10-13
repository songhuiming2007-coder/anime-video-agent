---
related-issues: D78, D79, D77, D44, D37
related-plans: 2026-10-12-steer-queue-and-approval-spec
status: accepted
---

# ADR-0030：审批只拦不可逆与花钱的操作，能回退的写入免卡（人裁决「再减少一些弹框」）

日期：2026-10-12
状态：**已通过**（2026-10-12 人提出方向：「在目前的 agent 时代，实际上尽可能减少人的审批，模型是在不断进步的」；人看过 Claude 第一版边界表后要求「再减少一些弹框」，按第二版施工）
前置：ADR-0029（bash 不加沙箱）、ADR-0027（工具不按模式授权）、D44（审批按参数分级）、D37（原生确认框只认鼠标）

## 背景

开放 bash（ADR-0029）后 ava 能干大部分活，但人几乎每次工具调用都要用鼠标点一次「批准」。查实的原因：

- bash 只有「单条只读命令、无任何 shell 元字符」免卡（`tools.bash_readonly`）。管道、`&&`、`$`、`cd x && …` 全部判为写，而模型写的命令绝大多数带这些。
- `side_effect` 默认 True 的工具（写期文件、`cover_edit`、`write_note`、`write_memory`、`acquire_propose`、`browser`）每次弹卡；`run_pipeline` 只有只读模块免卡。
- browser / 抓取卡批准后还要再过一道原生确认框，D37 故意设计成「回车不触发、只能鼠标点」。

## 决策

**原则：能回退的不问，用留底代替审批；只问不可逆或花钱的。**

1. **bash 只在命中「硬危险」时弹卡**，其余一律免卡。硬危险（`status_card.bash_danger_tags`，宿主静态规则）：删除（`rm` / `rmdir` / `unlink` / `find -delete`）、git 改历史或推远端（`push` / `reset` / `rebase` / `clean`）、提权（`sudo` / `su`）、全局装依赖（`brew install` 等、`npm -g`、`uv tool install`）、**写入** `pipeline/` 或 `config/`（Code Freeze）、碰停机点解封物。`mv`、`chmod`、`curl` / `wget`、`git commit` / `checkout`、项目内 `uv` / `pip` 不再算危险。危险标记从「只提示」升格为「决定弹不弹」。
2. **`run_pipeline` 白名单内全部免卡，以下三类除外**（白名单、`--force` 拒收、模型带选项的 `review` 拒收等弹卡前拒因一条不动）：
   - 花钱或销毁云端东西的 `cloud up / run / down / clean-frames / relocate-data`（开放「本会话都批准」）；
   - 写 `config/` 的 `corrections global`、`calibration set`、`bgm register`：AGENTS.md Code Freeze 三条例外写明「人审卡批准后」才写，与决策 1「写 config/ 算硬危险」同一口径（施工中发现，2026-10-12 补；逐条弹卡）；
   - 带越过人工关卡旗标的命令：`--confirm-patch`（跳过 03.5 补丁段二次确认）、`--waive`（素材门禁豁免），含 argparse 前缀缩写（同上，逐条弹卡）。
3. **写入类工具免卡**：`write_episode_file`（含 `02-script.md` 定稿与 `01-topic.md`）、`write_note`、`write_memory`、`cover_edit`、`acquire_propose`。回退靠留底：草稿 / 定稿 / 笔记原本就写前留底（D47、ADR-0028），本次给 `01-topic.md`、`07-titles.md`、`02-adversarial.md` 补上同样的留底（`_agent/file-history/`）；记忆日志本身带 before / after。（施工前设想的独立快照模块不需要：现有留底已覆盖。）草稿「每轮前 3 次免卡、超出恢复弹卡」的防打转刹车保留。
4. **抓取（fetch）与 browser 免卡**，原生确认框随之不再触发（退出确认框不动）。browser 用的是独立 profile，不碰主力 Chrome。
5. **剩下会弹的卡**：bash 硬危险、`cloud` 花钱 / 销毁子命令、写 `config/` 的三条命令、越过人工闸的旗标、草稿打转刹车、每 50 次的检查点、未来新工具（fail-closed 照旧）。其中 bash 与 cloud 卡多一个选项 **「本会话都批准」**：按「工具 + 危险标记组合」记在会话进程内存里（不落盘，换会话、重启即失效），之后同类免卡。检查点与草稿刹车不开放此选项。
6. **回车批准**：卡片出现时，输入框为空且没在打字，焦点落到「批准」按钮，按回车即批准（原生按钮派发 `isTrusted` 的 click，走既有 onClick；TG-4′「答复方法只在 onClick 里」不变）。自动聚焦后 400 ms 内的回车不生效（防连按 / 按键自动重复顺手批掉紧接着出现的下一张卡——原 TX-14 守的事，施工中补）。
7. 停机点（02.5 / 05 / 09 的解封物）是人工关卡，不是工具卡，**本 ADR 一条不动**。

## 已向人说明、由人接受的后果

- 硬危险规则是静态正则，可被绕过（如 `python -c "import shutil; shutil.rmtree(…)"` 不命中「删除」）。ADR-0029 写过「逐条弹卡是唯一一道人眼关」，本 ADR 把非危险 bash 的这道关撤掉：网页内容里的注入指令 → 本机命令执行，基本只剩模型自身的判断与这组静态规则。
- 停机点与 Code Freeze 只靠提示词规则 + 硬危险规则（ADR-0029 已接受前者）。
- browser 免卡：模型可自行在网页上点击、提交表单（独立 profile，无主力账号登录态）。
- 留底只覆盖写入工具；bash 改文件（`sed -i`、`cp` 覆盖）没有留底。
- 剩下的卡恰好都是危险类，回车即批意味着手滑回车即放行。

## 取代与修订

- 取代 ADR-0029 决策 4（「单条只读命令免卡，其余每次弹卡」）；危险标记的规则表按决策 1 重写。
- 修订 ADR-0027 决策 3 中「`CRITICAL_TOOLS` 人审卡、`browser` 原生确认框、`run_pipeline` 执行须人在宿主确认」三项；`CRITICAL_TOOLS` 的「执行期间延迟中断」语义不变。
- 取代 D37 方案 (b) 在 browser / 抓取上的适用：会话卡上的原生确认框（Spec 10 §2.4 第 5 层）整体撤掉（`shared/nativeConfirm.ts` 删除）；回收站删除、增量重配等宿主自己的确认框不在本 ADR 范围，照旧只认鼠标。
- 取代 ADR-0021「fetch 永远由人逐条批准」（人 2026-10-12 确认抓取直接执行）。
- 修订 D47「02-script.md 每次弹卡带 diff」：改为免卡，回退靠 `_agent/script-history/` 全量留底。
- D44 的「参数级免卡」被本 ADR 的更宽口径吸收。
- **修订（2026-10-12，D82，[spec](../plans/2026-10-12-bug-ticket-spec.md)）**：决策 1 的危险清单扩入三条——
  「须人审的流水线命令」（`tools.HUMAN_GATED_COMMANDS` 里的模块命令经 bash 直接跑也弹卡：`corrections global` /
  `calibration set` / `bgm register` 经 bash 不弹卡是现有缺口，人 2026-10-12 确认堵上；另含 `tickets open`）、
  「写工单目录」（bash 写 `tickets/`）、「拉起 coding agent」（命令词是 claude / codex / opencode / pi / aider / gemini /
  cursor-agent）。静态规则可被 `python -c` 一类写法绕过，与本 ADR 已接受的同类风险相同。

## 推翻条件

出现**一次**以下实例即复议（候选：恢复对应工具的卡，或回到 ADR-0029 方案 A 系统沙箱）：

- 免卡的 bash 越过停机点（写出 `02-diff.patch` / `04-clips.approved.json` / 定稿封面等解封物）、改动 `pipeline/` 源码或 `config/` 只读字段；
- 执行了网页内容诱导的命令；
- 免卡写入造成了留底救不回来的损失。
