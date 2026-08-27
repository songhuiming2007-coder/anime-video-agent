# 文档索引：1 个文件 + 最多 1 个 ADR

> **先查这张表，再决定跳去哪。** 每个主题只给 1 个主文件；相关 ADR 列在括号里。

---

## 按主题速查

| 我想查 | 主文件 | 相关 ADR |
|---|---|---|
| 每期九步的命令、产物、要盯什么 | [`WORKFLOW.md`](WORKFLOW.md) | — |
| 判据 / 标准 / 为什么 / 违反程序 | [`STANDARD.md`](STANDARD.md) | — |
| 开发顺序与闸门（先做什么、什么条件下做下一件） | [`ROADMAP.md`](ROADMAP.md) | [ADR-0003](adr/0003-visual-index-as-filter.md) |
| 当前未解决问题 | [`issues/README.md`](issues/README.md) | 见表内「关联」列 |
| 当前可执行施工图 | [`plans/README.md`](plans/README.md) | 见表内「相关 ADR」列 |
| 招募文案 / 核心假设 | [`HELP.md`](HELP.md) | — |
| 常驻规则（每次会话生效） | [`../CLAUDE.md`](../CLAUDE.md)（Claude Code）/ [`../AGENTS.md`](../AGENTS.md)（其他 agent） | — |
| 本机专属事实（盘符、账号数据、优先级） | [`../CLAUDE.local.md`](../CLAUDE.local.md) | — |

---

## ADR 清单

| 编号 | 决策 | 状态 | 相关 issues |
|---|---|---|---|
| [ADR-0001](adr/0001-ffmpeg-instead-of-jianying.md) | 放弃剪映草稿，直接用 ffmpeg 渲染 | 已采纳 | — |
| [ADR-0002](adr/0002-tts-local-indextts.md) | 本地 IndexTTS-1.5 + 自写解码循环 | 已采纳（决定二/三被 ADR-0006 取代） | N7, N11, N12, N19 |
| [ADR-0003](adr/0003-visual-index-as-filter.md) | 视觉索引只做角色在场过滤，不参与排序 | 第 1 层已验收；第 2 层探针没过，不建 | D4, D5, D6, D8, N1, N2, N8, N9, N14, N16 |
| [ADR-0004](adr/0004-script-episode-lock.md) | 排片先锁集号，presence 只做带内次级排序 | 已采纳已实现 | B1, D1, D6, D18, N9 |
| [ADR-0005](adr/0005-clip-mismatch-is-similarity-not-comprehension.md) | 排片错配根因：文本相似度≠语义理解 | 诊断确认，修复方案未定 | B1, D1, D2, D6, D18, D20 |
| [ADR-0006](adr/0006-tts-qwen3.md) | 配音引擎换 Qwen3-TTS 1.7B Base | 已采纳 | D21, N7, N11, N12, N19, N22 |
| [ADR-0007](adr/0007-no-japanese-subs-support.md) | ASR 兜底仅中文，不支持日语无字幕片源 | 已采纳 | D8 |
| [ADR-0008](adr/0008-ground-truth-anchor-clips.md) | 笔记 Ground Truth 锚点直通排片，检索降级补位 | 已实现，待新番实测验证 | D20（已归档） |

---

## 跨文件引用规则

1. **判据只定义在 `STANDARD.md`。** `WORKFLOW.md` / `CLAUDE.md` 只引用编号，不展开。
2. **活跃 issues 只在 `issues/README.md`。** 已归档见 [`issues/archive.md`](issues/archive.md)。
3. **活跃 plans 只在 `plans/README.md`。** 已归档见 [`plans/archive/`](plans/archive/)。
4. **ADR 用 frontmatter 回指 issues / plans。**
