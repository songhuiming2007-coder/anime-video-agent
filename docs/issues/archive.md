# Issues：已归档条目

活跃问题见 `README.md` 单表。本文件只存已解决、已作废或历史上有价值的问题。
**规则：解决即归档，不再删除。** 编号断裂比归档文件更害人。

---

## 2026-08-27：由 ADR-0008 解决并归档

### [D20] 排片机制重构：笔记 Ground Truth 锚点直通排片
- 状态：**已解决**（2026-08-27，ADR-0008 定案并实现落地）
- 关联：`docs/adr/0008-ground-truth-anchor-clips.md`
- 要点：02 写稿强制 `锚点:` 字段（check_script 机检）；clips.py 锚点通道起点吸附
  镜头切点、一等公民先占位；双塔检索降级为 `锚点: 无` 氛围段的补位。
  注意：08-26 期 clips.json 与 approved 逐字节相同（diff 通道污染），不作验证样本；
  验证义务与推翻条件在 ADR-0008 末节，等下期新番实测。

## 2026-08 批：由 P3 / P4 / 施工图解决并删除的条目

### [D3] 缩段不注水拍板落地
- 状态：**已解决**（2026-08-14，P4 E1 落地）
- 关联：`docs/plans/2026-08-14-p4-script-pipeline.md`
- 要点：`01-topic.md` 加 `缩段不注水: 是` 字段，字数下限 × shrink_factor；
  允许承认这段没料而缩短，不许注水凑数。

### [D14] TTS CPM 单源化
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/plans/2026-08-14-p3-doc-debt.md`
- 要点：CPM 取值口径统一，消除多文件默认值漂移。

### [D15] BGM 例外规则同步
- 状态：**已解决**（2026-08-14，BGM 例外规则同步）
- 关联：`docs/adr/0007-no-japanese-subs-support.md` 背景中提及
- 要点：例外曲目来源与理由在 config 与文档间同步完成。

### [D16] 人类介入点口径统一
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/plans/2026-08-14-p3-doc-debt.md`
- 要点：02.5 / 03.5 / 05 / 09 四处人类介入点在 CLAUDE.md / WORKFLOW.md / STANDARD.md 口径统一。

### [D17] 测试数清算
- 状态：**已解决**（2026-08-14，P3 解决删除）
- 关联：`docs/plans/2026-08-14-p3-doc-debt.md`
- 要点：测试断言与变异检验覆盖范围厘清并补全。

### [N3] shots calibrate 多参数支持
- 状态：**已解决**（2026-08-14 删除）
- 要点：`shots calibrate` 支持多参数标定。

### [N13] 人审手工片段标记
- 状态：**已解决**（2026-08-14 删除）
- 要点：05 人审改 `04-clips.json` 的标记机制落地。

### [N15] TTS 时长带改读 config
- 状态：**已解决**（2026-08-14 删除）
- 要点：TTS 时长估算带由代码硬编码改为 config 项。

### [N18] 切分标定判据补进 CLAUDE.md
- 状态：**已解决**（2026-08-14 删除）
- 要点：过切/漏切取舍的判据写进常驻规则。

---

## 2026-08-18 Pipeline 运行问题与架构缺陷复盘

- 状态：**已修复并验证**（2026-08-18，未提交 git，由人拍板）
- 来源：原 `docs/issues/2026-08-18-pipeline-inconsistency-issues.md`（孤儿文件，无编号）
- 修复项：
  1. **局部重跑级联校验**：`tts.py` 新增 `_stale_downstream()`，写完 manifest 立即对
     `04-clips.json` / `04-clips.approved.json` 跑 `verify_alignment`，有违例当场 WARN 并指明清算命令。
  2. **配置文件前置校验**：`tts.load_config()` 坏 JSON / 缺键在加载处 SystemExit；
     `render.run()` 把 `_maybe_music_plan` / `_bgm_plan` 提前到切片之前解析。
  3. **cover.py 字段 schema 兜底**：缺 `season`/`episode` 时按 `source` 路径反查片源登记表。
- 验证：`uv run pytest` 565 → 584 全绿，变异检验均红。

---

## 归档规则

1. 活跃区只保留 `README.md` 单表中的条目。
2. 问题解决后：状态改为已解决 / 已作废，迁移到本文件，README 中删除。
3. 编号不回填、不补号；删除后留下的空号用本文件记录去向。
