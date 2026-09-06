---
title: ADR-0011 SP 特典集号落地、单段多锚点蒙太奇与锚点段尾帧定格
status: 已采纳已实现
date: 2026-09-07
deciders: pi（夜间无人值守任务，songhuiming 晨间验收）
consulted: [ADR-0008, ADR-0010]
informed: [WORKFLOW.md, skills/write-script/SHOTLIST.md]
---

# ADR-0011 SP 特典集号落地、单段多锚点蒙太奇与锚点段尾帧定格

## 语境（Context）

ADR-0010 决策二约定了非传统素材（MV/Live/物证）的「SP 特典集」收编模式，
但落地前引擎存在四个结构性缺口：`锚点`/`集` 正则锁死 `SxxEyy`，写 `SP01` 当场
报错；一段只认一个锚点，跨番蒙太奇（一句口播依次展现王冠/PP/甲铁城）无法表达；
锚点段素材不够长时 `size()` 直接判 `short` 让整期卡死；QC 黑帧门限 0.5s 会把
Live/MV 的艺术暗场大面积误报。

## 决策（Decision）

1. **SP 的机内表示是 `season=None`，不是 `season=0`。**
   `sources.json` 里 `S00E0x` 已是 OVA 的既有登记（东京喰种、伪恋），0 被占了。
   `None` 与 `0` 在 `_overlaps`/`by_ep` 的元组比较里天然分开；键格式化全部走
   `clips._ep_key()`（`SP{episode:02d}`），align.py 抄一份保持零依赖叶子。
   SP 片段额外带 `"sp": true` 标记——与人审手补的「缺 season 键」片段分得开，
   QC 豁免只认这个标。
2. **SP 素材挂企划池。** 锚点前缀校验对 SP 额外放行企划名（`番:` 行第一个，
   `bgm.anime_of`）；`clips.run()` 的片源池**按需**并入企划池——稿件真的锚了
   番表外的池才并，不用不并（登记缺失照常由 load_sources 报错）。普通季集锚点
   不许指企划名：企划没有 S01E01，写稿期就拦，不推迟到运行期。
3. **单段多锚点**：逗号（半/全角）或缩进续行分隔，顺序 = 书写顺序，
   交 `size()` 按自然跨度加权分满段时长。段内互撞或任一锚点解析不出，整段
   `anchor_overlap`/`no_match`——蒙太奇是确定的有序序列，缺一环就不成立，
   不静默少放。单锚点段落的产物形状与旧版逐字节一致（无 `anchors` 键）。
4. **锚点段尾帧定格（ok_extended）**：`size(..., allow_extend=True)` 仅锚点段
   可用；两个方向水填拉满仍不够时，缺口（≤8s=`EXTEND_MAX`，超过仍判 short）
   挂末片 `extend` 字段，render 用 `tpad=stop_mode=clone` 克隆末帧。
   段级不变量把 extend 算进总量（`Σ(dur+extend) == need`），align/review/render/
   qc 四处同步。检索段不开放：它的 short 应该触发下一轮多要候选，不是定格凑合。
5. **QC 艺术暗场豁免**：clip 带 `sp` 标记的源，黑帧未覆盖门限从
   `BLACK_MAX=0.5s` 放宽到 `BLACK_MAX_SP=1.5s`；同一条黑跨两类源时分类各自判。
6. **登记链闭环**：`ingest sources --sp N` 与 `shots build --sp N`
   （与 --season/--episode 互斥必填二选一）。

## 效果与不变量保证（Consequences）

- 基线 702 测试全绿 + 新增 70 个泛化测试全绿（合计 772）；
  三处变异检验（SP 键、定格分支、QC 门限）全部杀死。
- 现存单番期（伪恋/你的名字/天气之子）用 HEAD 与改造后代码对比解析与机检，
  输出完全一致（零漂移实测）。
- `集: SP01` 不写锚点当场报错（SP 没有字幕索引，检索通道锁不到它）。
