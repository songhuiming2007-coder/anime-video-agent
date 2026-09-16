# 方案 P1：候选复核探针——先证明「会读的复核」分得清对错，再谈接入（B1 / D1 / D6）

对应 issue：**B1**（排片错配修复方案未选定）、**D1**（复核四子题）、**D6**（错配率
从未量化）。依据 ADR-0005：病根是余弦只证「用词相近」不证「就是那一刻」，唯一候选
方向是「会读的模型复核」，但 ADR 明文要求**动手前先跑探针**，不许「先上了再看效果」。

## 本方案交付什么（范围先钉死）

**交付探针工具链，不交付复核层。** 复核层进主流程是探针通过后的另一份方案
（第四节的规格草案，仅供拍板，不在本方案执行范围）。理由：ADR-0005 推翻条件写明
「若探针显示复核本身也不可靠 → 候选方向作废」——没过探针之前写接入代码是白写。

执行代理负责：量化工具、探针生成器、判分器、测试。**不负责跑复核**——复核由
零上下文的 agent 会话（判据 11）做，跑不跑、谁来跑由用户决定。

## 前置条件

- **必须挂载 data 盘**（`data -> /Volumes/Samsung T7`）：要读 `data/episodes/`、
  `data/library/index/`、片源抽帧。开工先 `./pipeline/preflight.sh` 确认 data 可达。
- `uv run pytest` 基线全绿；P2/P3 建议先落地（共用文件少，但都动 issues 索引）。

## 第一节：错配量化（D6）——`pipeline/recheck.py` 子命令 `diff`

新文件 `pipeline/recheck.py`（新增能力开新文件），docstring 写明「探针工具，
未过探针不进主流程」。

```
python -m pipeline.recheck diff data/episodes/<期>
python -m pipeline.recheck diff --all          # 扫 data/episodes/ 下所有期
```

对每期同时有 `04-clips.json` 与 `04-clips.approved.json` 的，逐段比对并分桶：

| 桶 | 判定（机器版 vs approved 版） | 含义 |
|---|---|---|
| `unchanged` | 段内容完全一致 | 机器选对了（候选级正确） |
| `content_changed` | 任一 clip 的 source/season/episode 变了 | 换画面——语义错配主嫌疑 |
| `start_shifted` | 同源同集只挪 start | 挪位——机器找对戏、切错时刻 |
| `dur_changed` | 只变 dur | 排版调整，非语义，**不算错配** |
| `count_changed` | clip 数增减 | 补片/删片 |
| `human_filled` | 机器 no_match、approved 有 clips | 候选池无答案或查询写错 |

**两个已知坑，必须处理（2026-08-14 盘上实测坐实）**：

1. **人是在 `04-clips.json` 上就地改的**（ADR-0005 先例：改 start + 留
   `_manual_fix` 注记，然后 approve 拷贝）。实测 8 期：楪祈一的 `04-clips.json`
   与 approved **零 diff 且 `_manual_fix` 注记已不在文件里**——机器基线被就地
   覆盖、注记丢失，ADR-0005 的文字记载成了楪祈一 5 处人工修正的唯一线索。
   所以 `diff` 必须同时扫两份文件里的手工注记（键名匹配 `manual|手改|手修|fix`，
   大小写不敏感；工具要打印「这期发现了哪些注记键」，不许静默猜）。带注记的
   clip 记为 `annotated_bad` 桶——注记本身就是「人工判错过」的 ground truth。
   **基线丢失的期可以重构机器视角**：`_ladder()` 对同一份 `02-script.md` +
   未变索引（`_check` 钉死模型身份）是确定性的，probe 反正要重跑它——diff 桶
   归不进的期，由 probe 的候选序列对照 approved 兜底量化。
2. 比对前把 float 全部 round 3 位再比，别拿 0.0004 的舍入差报成 start_shifted。

**实测样本盘点（2026-08-14）**：8 期里机器基线保住的只有东京喰种一期
（18/20 段 differs，其中含 B4 实证同源的 6 段时长违例）；楪祈一基线丢失但
ADR-0005 记了 5 处；其余 6 期 0 differ。可用 ground truth ≈ **18 段 diff +
5 处 ADR 记载 ≈ 23 段**，低于下文判定线原先假设的 40–60 段——判定线按此修订，
未来新期人审改动会被 diff 工具自动积累成新样本。

产物：`data/episodes/<期>/04-mismatch-report.json`，每段一行：

```json
{"index": 7, "bucket": "content_changed", "label": null,
 "machine": [...], "human": [...], "note": "_manual_fix: ..."}
```

`label` 留 null 待人工填（见第二节）。控制台打印每期分桶统计 + 全部段级行。
这个统计本身就是 D6 欠的量化，回填进 `docs/adr/0005` 的「实测印象」节
（把「待量化」段落的数字补上、标日期）。

## 第二节：ground truth 人工标注（用户做，~15 分钟/期）

不做工具。用户直接编辑 `04-mismatch-report.json`，把 `content_changed` /
`start_shifted` / `human_filled` / `annotated_bad` 各段的 `label` 填成：

- `"bad"`——人工确认机器选的画面配不上这段口播（真错配）
- `"good"`——改它是审美/节奏，机器原选没错
- `"skip"`——说不清，弃权

`unchanged` / `dur_changed` 段自动视为 `good`（人看了没动）。探针生成器**拒收
label 为 null 的非 unchanged 段**，不许带糊数据进判分。

## 第三节：探针——`recheck probe` 生成工作单 + `recheck score` 判分

### 生成（机械，可测）

```
python -m pipeline.recheck probe data/episodes/<期> [--n 8]
```

对每个有 label 的段：

1. **复现机器视角的候选序列**：`parse_shots(02-script.md)` 取该段 → `_ladder()`
   重跑（索引与模型没变，检索是确定性的，重跑=当时的机器顺序；写进 docstring：
   跨段分配冲突导致的降级不在探针范围——探针判候选本身，不判分配）→ 有 `人物`
   的段再过 `_by_character`。取前 `--n` 个候选。
2. **抽帧**：每个候选造伪 clip `{"start": u.start, "dur": u.end - u.start,
   "source": load_sources(...)[...]["path"]}`，调 `review._frames()`（复用，
   不重写 ffmpeg 逻辑），帧落 `probe-frames/<段号>-<rank>-{0,1,2}.jpg`。
3. **写工作单** `04-recheck-worklist.md`，**自含复核指令**（复核 agent 只拿到
   这个文件，零上下文）。每段一块：

```
## 段 7 ｜ 口播：<原文>
查询: <查询> ｜ 人物: <人物或无> ｜ 集: <集号或无>
以下 N 个候选是检索为这段口播找的画面。逐个判断：这个画面**拿来配这段口播**能不能用。
判 match / not_match / unsure，各给一句理由。不许参照其他段，不许猜测对错比例。
### 候选 1（台词分 0.653）S01E08 12:40-12:46
- 台词上下文：前一句「…」｜ 本句「…」｜ 后一句「…」
- 画面三帧：probe-frames/07-1-0.jpg / 07-1-1.jpg / 07-1-2.jpg
### 候选 2 …
```

上下文取候选 unit 在索引里的前后各一个 unit 的文本（同番同集相邻滑窗；跨集边界
就写「无」）。

4. **判分**：

```
python -m pipeline.recheck score data/episodes/<期>
```

读 `04-recheck-verdicts.json`（复核 agent 按工作单末尾给的 schema 写：

```json
{"segments": [{"index": 7, "judgments": [
  {"rank": 1, "verdict": "not_match", "reason": "镜头拍说话人，不是口播讲的人"}]}]}
```

）× report 的 label，输出四个数：

| 指标 | 定义 |
|---|---|
| **检出率** | label=bad 的段里，机器 top-1 被判 not_match 或 unsure 的占比 |
| **误拒率** | label=good 的段里，top-1 被判 not_match 的占比（unsure 不算拒） |
| **unsure 率** | 全部 top-1 判 unsure 的占比 |
| **人选复核率**（辅） | content_changed 段里，**人最终选中的那个 clip** 在候选单内且被判 match 的占比——量「复核认同人的选择」 |

### 判定线（跑完对照，写死在这，不现场发明）

- **检出率 ≥ 60% 且误拒率 ≤ 15% → 方向成立**，第四节规格转正式 ADR 另行立项。
- **检出率 < 60% → 方向作废**（ADR-0005 推翻条件 2 命中）：诊断仍成立，候选方向
  划掉，B1 重新找路。
- **误拒率 > 15% 但检出率达标 → 上下文不够**：先试把候选上下文从 ±1 unit 加到
  ±2、`--n` 从 8 降到 5（复核负担小、单候选看得细），**只许重跑一次**，仍超线
  就按作废处理。不许进「调阈值换通过」的循环（判据 3 同款纪律）。

数字理由（2026-08-14 按实测样本修订）：可用 ground truth ≈ 23 段（18 diff +
5 ADR 记载），60% = 检出 ≥ 14 段中的 9 段——小样本下这是「方向值得立 ADR」的
下限，不是效果承诺；15% 误拒 ≈ good 段每 7 段最多误伤 1 段，再高则复核制造的
人工处理量超过它省的。未来 diff 工具积累的新期样本只许加进样本池，不许用来
反复重试判定线。

## 第四节：接入规格草案（**只写不建**，探针通过后另立 ADR）

D1 四子题的拟定答案，随本方案一并拍板：

1. **接在哪**：新增机器步 04.5（clips 之后、review 之前），零上下文 agent 复核，
   产物 `04-recheck.json`；05 审图页把 not_match 段标红。人仍是唯一放行关卡。
2. **不通过算什么**：不自动改 status、不自动换候选——**复核只降不升**：能把
   top-1 标成「复核未过」示警，不许把分数更低的候选提上来（「替人挑更好的」
   没被探针验证过，不许超出验证范围使用）。
3. **判据 10**：复核是布尔判断非分数，不受该条约束；新规则即上面的「只降不升」。
4. **成本**：每期一次 agent 会话，~20 段 × 5–8 候选 ×（3 帧+文本），人工零耗时。

## 测试（`tests/test_recheck.py`，纯函数）

- diff 分桶：造机器/approved 两份 fixture，六桶各一条能正确归桶；float 舍入差
  不误报；带 `manual_fix` 注记的归 `annotated_bad`。
- 判分：合成 verdicts + labels，四个指标手算期望值写死进断言（先手算再写，
  不许「跑出啥断言啥」）；label=null 的段被拒收。
- 变异检验：把误拒率的分母故意改成全体段，断言必须红。

## 验证命令

```bash
uv run pytest
./pipeline/preflight.sh
python -m pipeline.recheck diff --all     # 挂盘后，输出即 D6 量化
```

## 明确不做

- 不改 `clips.py` 的主流程（04.5 是探针通过后的事）。
- 不下载任何模型；嵌入模型复用 subindex 现有的。
- 不替用户跑复核会话、不替用户填 label。
- 探针最多重跑一轮（判定线条款写死），不做迭代调优。

## 完成判定

- [ ] `recheck diff / probe / score` 三命令可用，测试全绿（含变异检验）
- [ ] `--all` 量化结果回填 ADR-0005「实测印象」节，D6 状态改「已量化」
- [ ] 工作单生成成功（挂盘状态下至少一期跑通）
- [ ] B1/D1/D6 的 issues 条目更新：注明「探针工具就绪，待人工标注 + 复核会话」，
      不删（探针没跑完不算解决）
