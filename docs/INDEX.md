# 文档索引：开发态与生产态明确隔离

> **先查这张表，再决定跳去哪。** 生产作业只看生产态；系统迭代与架构取舍查开发态。

---

## 生产态（Operator 跑视频专用）

| 我要查 | 主文件 | 说明 |
|---|---|---|
| **生产开工状态诊断与路由** | 运行 `python -m pipeline.status <期目录>` | 自动判断当前卡点，输出唯一命令与对应的 SOP |
| **每期作业总纲速查（≤100 行）** | [`WORKFLOW.md`](WORKFLOW.md) | 四阶段工序卡、4 处人工停机点与四大物理红线 |
| **ava 终端交互极简速查** | [`CHEATSHEET.md`](CHEATSHEET.md) | 看板、REPL 路由、顺听纠错白话口诀与全流程命令速查 |
| 01 选题与张力 | [`runbook/01-topic.md`](runbook/01-topic.md) | 选题字段、体裁选择与编辑判断约束 |
| 02 脚本写作与机检 | [`runbook/02-script.md`](runbook/02-script.md) | 调 skills/write-script 与 check_script 机检（条数随判据迭代，不写死） |
| **02.5 人审改稿（停机点 1）** | [`runbook/02.5-human-review.md`](runbook/02.5-human-review.md) | 人工事实核验、精修与 diff 封板规程 |
| 03 语音合成 | [`runbook/03-tts.md`](runbook/03-tts.md) | 增量合成、注音修复与防 `--force` 红线 |
| **03.5 配音顺听（停机点 2）** | [`runbook/03.5-voice-check.md`](runbook/03.5-voice-check.md) | 顺听 + /voice 纠错（可选深挖，corrections.json / --apply-patch） |
| 04 素材排片 | [`runbook/04-clips.md`](runbook/04-clips.md) | 三通道互斥分派与全局贪心占坑 |
| **05 审时间码（停机点 3）** | [`runbook/05-timecode.md`](runbook/05-timecode.md) | 浏览器审片与 `--approve` 显式批准 |
| 06 本地渲染 | [`runbook/06-render.md`](runbook/06-render.md) | 本地 ffmpeg 一趟出片与双重截取守卫 |
| 07 质量门禁 | [`runbook/07-qc.md`](runbook/07-qc.md) | 11 项机器硬指标自动化检测 |
| 08 封面与标题候选 | [`runbook/08-cover-title.md`](runbook/08-cover-title.md) | 候选帧去重过滤与 5 条标题生成（严禁机器定稿） |
| **09 人工发布（停机点 4）** | [`runbook/09-publish.md`](runbook/09-publish.md) | 人选封面标题与手动平台上传 |

---

## 开发态（Developer / 系统迭代专用）

| 我要查 | 主文件 | 说明 |
|---|---|---|
| 质量与判据系统权威定义 | [`dev/STANDARD.md`](dev/STANDARD.md) | 每条判据的「为什么」与违反程序（非执行手册） |
| 架构决策记录 | [`dev/adr/`](dev/adr/) | 19 项架构决策、推翻条件与论证证据 |
| 全量踩坑论证与历史背景详注 | [`dev/postmortems/workflow-history.md`](dev/postmortems/workflow-history.md) | 包含 Phase 0 完整参数、历史事故与个案分析 |
| 施工路线与开发闸门 | [`dev/ROADMAP.md`](dev/ROADMAP.md) | 开发顺序与阶段验收条件 |
| 活跃问题单 | [`dev/issues/README.md`](dev/issues/README.md) | 待解决系统性缺陷与卡点 |
| 可执行施工图 | [`dev/plans/README.md`](dev/plans/README.md) | 阶段性架构重构方案 |
| 核心假设与招募说明 | [`dev/HELP.md`](dev/HELP.md) | 项目理念与远景 |

---

## 常驻与本地事实

- **常驻工程约定**：[`../AGENTS.md`](../AGENTS.md)（每次会话生效，统一 SSOT）
