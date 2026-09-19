---
related-issues: B2, D23
related-plans: 2026-09-18-ava-agent-harness, 2026-09-18-ava-agent-impl-spec
status: accepted
---

# ADR-0019：期级 Overlay 与 corrections.json 资产生命周期

日期：2026-09-18
状态：**已通过**
前置：ADR-0006, ADR-0016, ADR-0017, ADR-0018

## 背景

在 03.5 配音顺听阶段，人耳听出错读音或语气发飘时，若直接改动全局 `config/voice_readings.json` 拼音表，会污染全局番剧资产并可能导致其他已通过审听的段落被意外改动；若手动删除 `seg-XX.wav`，则缺乏审计轨迹且容易引发全量重新合成。

此外，全库无进程锁，多个写者或长任务执行期间的并发写操作可能造成数据丢失或假完成状态。

## 决策

1. **优先级规则：期级 Overlay 压制全局表**：
   - 全局表 `config/voice_readings.json` 是长期沉淀层；
   - 单期纠错落盘至 `data/episodes/<期号>/03-audio/corrections.json`，形成期级 overlay；
   - 期级 overlay 中的同名键对全局表同名键具有严格优先权；改动全局表绝不会改动已有期级 overlay 的段。
   - 逐段 scope（默认）：词级注入默认仅作用于当前段（`scope="segment"`），防止同一词在其他正常段被破坏；仅当用户显式声明「全局」时才进入 `scope="global"`。

2. **永久资产与写者纪律**：
   - `03-audio/corrections.json` 是 append-only 永久资产，严禁删除；删除会导致下次重跑时 overlay 丢失并悄悄重配回错音；
   - 写入采用原子写入（`paths.atomic_write`）并实施写前指纹校验（mtime + SHA 比对），防止长任务重叠期间静默覆盖；
   - `--apply-patch` 运行期间，REPL 拒绝新增纠错落盘。

3. **条目状态与进度模型（applied / affected / done_segments）**：
   - 条目落盘初始为 `applied=false`，带计算出的 `affected` 列表；
   - 每段合成成功即时将 label 追加进 `done_segments`；
   - `applied=true` 仅当 `done_segments` 严格覆盖 `affected` 时置位（集合语义），避免假完成状态与中断续做漏洞；
   - redo 算法采用判据减法：`redo = affected 中（pending-inclusive overlay 下被 _reusable 判为不可复用的段）`。

4. **快照保留窗与回滚策略**：
   - 每次 `--apply-patch` 覆盖前将受影响段音频及 `manifest.json` 备份至 `03-audio/attic/<YYYYMMDD-HHMMSS>/`；
   - 系统自动保留最近 10 份快照，修剪时必须清晰报账被删快照及所含段号；
   - `回滚 <段号>` 精准查找包含该段的最近快照；成片交付归档后方可整目录清理 `attic/`。

## 推翻条件

若逐段 scope（默认 segment）在实践制片中被证明绝大多数错读都是全局性的、且用户总需要手动指定「全局」产生过高摩擦，则推翻默认逐段设定，改为默认 global 沉淀 + 显式声明 segment。
