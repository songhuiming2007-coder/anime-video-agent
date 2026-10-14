# D95 企划会话放行素材抓取（acquire）

> 状态：**已施工·机检通过**（2026-10-14，main，未提交）。待人：重启 AVA，在 supercell 企划会话里一次把候选提完、抓完、登记进素材池。

## 一、目标与对应 issue

- 对应 issues **D95**；修订 D81 §4.2（[企划层施工 spec](2026-10-12-project-layer-spec.md)，红队 R-3）。
- 事件：2026-10-14 supercell 企划会话。AVA 说「抓取提案在企划会话里会被拒，必须到对应卷的会话里做」，于是把 12 条官方 MV / 预告拆成三批，要人分别开卷二、卷三、卷四的会话去抓。
- 原规则：企划会话里 `acquire_propose`、`cover_edit` 一律拒，`run_pipeline` 也不放行 `acquire`。拒因是「不拒的话封面与抓取提案会写进母目录」。
- 读码核实，这个拒因对抓取不成立：
  - 候选：`propose_candidates` 写进 `data/library/incoming/candidates.json`；
  - 下载：`acquire fetch` 落到 `data/library/incoming/`；
  - 登记：`acquire register --pool <番>` 进按番的素材池。

  这三步都是全库共用。只有 `register --to-patch <期目录>` 真正写进某一期。原规则还和人确认过的真实流程冲突（[real-workflow](2026-10-14-real-workflow.md)「立项先搜资料、素材先预收集」）：立项时卷可能还没建，按原规则根本无处可抓。
- 目标：企划会话能提候选、抓取、gate、登记进素材池；只有挪进某卷补丁池仍到卷会话做。`cover_edit`、`view_image` 保持卷级不变。

## 二、前置条件

- 基线：D93 提交后 `uv run pytest` 3099 passed。
- 只改 Python 与 scope 提示词。桌面端以仓库里的 Python 启动 core（`desktop/src/host/spawner.ts` 的 `python -m pipeline.agent.protocol`），**重启即生效，不用重新打包**。

## 三、改动清单

| 文件 | 锚点 | 改动 |
|---|---|---|
| `pipeline/agent/tools.py` | `PROJECT_SESSION_EXTRA: frozenset[str]` 之后 | 新常量 `PROJECT_LIBRARY_MODULES = frozenset({"acquire"})`，注释写依据 |
| 同上 | `def project_module_refusal(` 之前 | 新函数 `acquire_targets_volume(argv)`：是否带 `--to-patch`，前缀缩写与 `=` 写法都算 |
| 同上 | `project_module_refusal` 的企划分支 | `acquire` 不带 `--to-patch` → 放行；带 → 拒，拒因写「挪进某卷补丁池是卷级操作」并列出本企划的卷 |
| 同上 | `def _tool_acquire_propose(` | 删掉企划会话的拒绝分支；无期（idea）报「先建期」不变 |
| 同上 | `PROJECT_VOLUME_ONLY_TOOLS` / `PROJECT_VOLUME_ONLY_MESSAGE` | 去掉 `acquire_propose`；拒因括注改为「封面与看图只认某一卷的期目录」 |
| `config/agent/scopes/project.md` | 「能做什么」「不能做什么」「素材多轮搜」三处 | 预收集在企划会话里做；不能做的改为「挪进某卷补丁池」；后续几轮跟着卷走 |
| `docs/dev/plans/2026-10-12-project-layer-spec.md` | §4.2 `cover_edit`、`acquire_propose`：拒 | 加修订注记 |

抓取的执行器 `AgentSession._fetch_executor` 调 `run_pipeline("acquire fetch N", episode_dir=host.ep_dir)`，本来就不经 `project_module_refusal`，所以不用改。用 dry-run 核实过：`validate_pipeline_command` 对 `acquire fetch` / `gate` / `register --pool` 都通过，企划会话里唯一的拦截点就是 `project_module_refusal`。

## 四、取值与判据理由

- **放行 `acquire` 整个模块，只拒 `--to-patch`**：`acquire` 只有这一个出口写进期目录（`cmd_to_patch`），其余子命令都写 `data/library/`。
- **前缀缩写也算 `--to-patch`**：`acquire` 的 argparse 用默认的 `allow_abbrev=True`。`register` 的选项是 `--pool`、`--to-patch`、`--as`、`--waive`、`--force`，只有 `--to-patch` 以 `--t` 开头，所以 `--t` 已经唯一指向它。判法照 `crosscheck_writes` 的先例。
- **企划目录里新增的写入只有两样**：抓取 job 的 `events.jsonl` 和 `_agent/approvals.jsonl` 审批流水。D81 §4.2（红队 R-7 校正）本就允许企划会话写审批流水，事件日志与它同类，不另设落点。

## 五、测试要求

`tests/test_project_session.py`：
- 原用例 `test_封面与抓取提案拒_母目录不被写` 改名 `test_封面拒_母目录不被写`，只保留 `cover_edit` 的断言。
- 新增 13 条：
  - 企划会话提候选：审查层不拒、实现层成功，候选写进全库候选表，企划目录下没有新文件。
  - 企划会话放行 `acquire fetch` / `gate` / `register --pool` / `forget` / `probe`（5 条参数化）。
  - `--to-patch`、`--to-p`、`--t`、`--to-patch=` 四种写法都在审查层和实现层被拒，拒因列出两卷（4 条参数化）。
  - 卷会话、普通期会话的 `--to-patch` 不受影响。
  - 企划会话一回合内提候选后自动抓取：执行器拿到序号 1，结果回填进工具消息。

变异 7 / 7 全部被抓：`acquire_propose` 放回卷级名单、实现层恢复企划拒、`PROJECT_LIBRARY_MODULES` 置空、删掉 `--to-patch` 例外、不认前缀缩写、例外误用到卷会话、拒因不列卷名。

## 六、验证命令

```bash
uv run pytest tests/test_project_session.py
uv run pytest            # 全量，含 ruff 门禁
```

## 七、明确不做

- 不动 `cover_edit`、`view_image` 的卷级限制。
- 不改抓取 job 事件的落点。
- 不做「企划会话代某卷挪进补丁池」。
- 不改抓取本身的行为（ADR-0030 起提案即抓、不出卡，照旧）。

## 八、完成判定

- [x] `uv run pytest` 全绿
- [x] 新测试 13 条，变异 7 / 7
- [ ] 待人：重启 AVA，在 supercell 企划会话里一次提完、抓完、登记进素材池，确认不再被拒
