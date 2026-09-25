# 方案 P2：段级时长不变量——「产物即状态」的校验面（B4）

对应 issue：**B4**（人审改画面后段级时长不对齐，渲染漂移无门禁拦截）。
相关：`docs/adr/0005` 开头的手改先例、issues/N13（review.py 有未提交改动，见下）。

## 病根（为什么是架构补丁）

`clips.size()` 保证「每段画面总长 == 该段配音时长」，但这个不变量**只存在于机器生成
的那一刻**——人审在 05 直接改 `04-clips.json` 的 start/dur 时，没有任何环节重新校验。
qc 只查总量级，`-shortest` 还会把差额截掉进一步掩盖。「产物即状态」喊的是产物上带
不变量，校验却只挂在生成函数里。本方案把不变量挂到**产物**上：approve 与 render
两道口都验，另给一个 `--refit` 工具把人改过的段重新排版到满足不变量。

**选定 B4 的候选方向②+①结合**：收口编辑（人只改 start/source，dur 由工具重算）+
approve 硬校验兜底。否掉「靠人眼比对」：把验证责任推给最不可靠的传感器（判据 2
的同款教训）。

**实证（2026-08-14 盘上实测，8 期全查）**：唯一一期大规模人审改画面的
（08-09 东京喰种，18/20 段 differs）恰是唯一违例期——6 段不对齐，最大 −0.91s；
画面总长 314.83s vs 配音 315.98s（总量差 −1.15s）。该期 `06-check.log` 里
「音画时长对齐 PASS 差 15ms」「与排片一致 PASS 差 245ms」双绿：总量差被
逐片帧舍入（+0.9s）与 0.5s 容差吃掉、`-shortest` 截平音画，段级近 1 秒的
错位全程静默。其余 7 期全部对齐（人没大改）。病根确认，且损伤已发生在一期
已发布视频上。

## 前置条件

- 仓库根执行；`uv run pytest` 基线 400 条全绿。
- **不需要**挂载 data 盘（纯函数改动 + 测试）。
- `git status`：`skills/write-script/SKILL.md` 与 `pipeline/review.py` 有未提交改动
  （N13 的「手工」标记，改了一半）。**不许回退它们**；本方案对 review.py 的修改
  是字符串级新增，与其不重叠。动手前先 `git diff pipeline/review.py` 看清现状。

## 改动清单

### 1. `pipeline/clips.py`：新增不变量校验 + refit

在 `size()` 之后新增两个函数（含常量）：

```python
# 段级不变量容差。为什么是 0.05：size() 自己的接受线就是 drift ≤ 0.05 判 ok
# （见 size() 末尾 return 分支），机器产物满足 |Σdur − need| ≤ 0.05；而人手改
# start/dur 造成的漂移通常 ≥ 0.1s，两边分得开。收紧到 0.01 会误伤机器产物的
# 0.001 舍入累积（每片 ≤0.0005，多片叠加）。
SEG_TOL = 0.05

def verify_alignment(segments: list[dict], audio: list[dict]) -> list[str]:
    """段级不变量：每段 Σclip.dur == manifest 段时长（±SEG_TOL）。

    这是「产物即状态」的校验面：不变量不再只活在 size() 里，而是任何时刻
    拿 04-clips*.json + manifest.json 就能验。返回违例描述列表，空 = 通过。
    status != "ok" 的段没有 clips（render 本就拒收），跳过；但段数与 manifest
    对不上必须报——zip 会静默吞掉错位（判据 9：跳过不是通过，错位更不是）。
    """

def refit(segments: list[dict], audio: list[dict],
          sources: dict) -> tuple[list[dict], list[str]]:
    """人审改过 start/source 之后，把每段 dur 重排到满足段级不变量。

    规则：漂移由**本段最后一个片段**吸收。为什么是末片：dur 是排版结果、
    不是画面身份（本文件 OVERLAP_GAP 注释里已立过这条），人刚挑的画面身份
    （start/source）一个不动；末片的伸缩边界由片源时长（截取守卫同款判据）
    和 MIN_CLIP 夹住，吸收不了就报错——诚实失败，不静默截断。

    机器产物（已对齐）调它是幂等 no-op。返回（segments, 调整报告行）；
    改不了的段抛 SystemExit，带段号和三个数（Σdur / need / 差多少）。
    """
```

实现要点：

- `verify_alignment`：`len(segments) != len(audio)` → 直接算违例写进返回列表；
  每个 ok 段算 `abs(sum(c["dur"] for c in seg["clips"]) - audio[i]["duration"])`，
  超 `SEG_TOL` 记一条 `"段{i}: 画面 {x:.2f}s / 配音 {y:.2f}s 差 {d:+.2f}s"`。
  `status == "ok"` 但 clips 为空也算违例。
- `refit`：只处理违例段；末片 `new_dur = clamp(last["dur"] + drift, MIN_CLIP,
  sources[f"S{se:02d}E{ep:02d}"]["duration"] - last["start"])`，round 3 位；
  clamp 后仍差 > SEG_TOL → SystemExit。`sources` 用现有 `load_sources(anime)`
  的返回（`candidate()` 已示范键格式）。anime 从 `04-clips.json` 顶层 `"anime"` 读。
- `total_duration` 字段**不动**：它本来就是配音时长之和（`run()` 末尾的写法），
  与 clip dur 无关，refit 后依然成立。

### 2. `pipeline/clips.py` CLI：`--refit`

`main()` 加 `--refit` 开关：读 `04-clips.json`（人改过的那份）→ `load_sources`
→ `refit` → 原地写回 → 逐段打印调整行。**走这条路时不许碰检索**——直接跑
`python -m pipeline.clips <期>` 会重新检索并覆盖人改结果，这是既有行为，
WORKFLOW 文档里补一句警告（见第 5 条）。

### 3. `pipeline/review.py`：approve 变校验闸 + 审图页显示合计

- `approve()`：copy 之前加载 `03-audio/manifest.json`（缺文件 = SystemExit，
  判据 9）+ `04-clips.json`，跑 `verify_alignment`；有违例 → SystemExit 列出
  全部违例段并提示 `python -m pipeline.clips <本期> --refit`。通过才 `copy2`。
- `build()`：每段头一行加「画面合计 X.Xs / 配音 Y.Ys」，不一致标红
  （复用现有 `.flag` 样式）。manifest 缺失时该行显示
  「（缺 manifest，未比对）」——跳过必须显式。

### 4. `pipeline/render.py`：渲染前第二道闸

`run()` 里加载 plan 与 manifest 之后、切片之前（`bad = [...]` 检查旁边）加：

```python
from .clips import verify_alignment   # 文件顶部或函数内 import，避免循环依赖问题——
                                      # clips 不 import render，无环，顶部 import 即可
violations = verify_alignment(segments, manifest["segments"])
if violations:
    raise SystemExit("FAIL 段级时长不对齐（人审改过画面没 refit？）：\n  " + "\n  ".join(violations))
```

approve 是第一道，render 是第二道（防绕过 approve 直接改 approved 文件）。

### 5. 文档同步（改完代码就改，不留过夜债）

- `CLAUDE.md` 第七节「截取守卫」末尾加一行：**段级不变量：每段 Σclip.dur ==
  manifest 段时长（±0.05s），approve 与 render 双重拦截；05 人审改画面只改
  start/source，改完跑 `python -m pipeline.clips <本期> --refit` 重排版。**
- `docs/WORKFLOW.md` 第 05 步节：加同样的操作说明 + 警告「人改过之后重跑
  `pipeline.clips`（不带 --refit）会覆盖人工修改」。
- `docs/issues/`：删 B4 条目，`README.md` 索引同步（阻塞 4→3，B 编号不回填）。

## 测试

放 `tests/test_clips.py`（函数在 clips.py）与 `tests/test_review.py`（approve 闸），
沿用现有风格（构造 dict fixture，不碰 ffmpeg/盘）：

1. `verify_alignment`：对齐通过；漂移 0.3s 报段号；no_match 段跳过；ok 但空 clips
   报；段数不齐报。
2. `refit`：已对齐输入 no-op（幂等）；差 0.4s 由末片吸收且 Σ==need；末片 headroom
   不够 → SystemExit；absorb 后低于 MIN_CLIP → SystemExit。
3. `review.approve`：tmp 目录造 clips+manifest，违例 → `pytest.raises(SystemExit)`
   且 approved 文件**不存在**（不许先拷再报）；对齐 → 拷贝成功。
4. 变异检验（CLAUDE.md 写测试纪律②）：把 SEG_TOL 临时改成 10.0 跑测试，违例类
   断言必须红；改回。

## 验证命令

```bash
uv run pytest
./pipeline/preflight.sh
# 挂盘后可选实测：挑一期已发布的，验它的 approved 文件天然满足不变量
python -c "import json;from pathlib import Path;from pipeline.clips import verify_alignment;\
p=json.loads(Path('data/episodes/<期>/04-clips.approved.json').read_text());\
m=json.loads(Path('data/episodes/<期>/03-audio/manifest.json').read_text());\
print(verify_alignment(p['segments'], m['segments']) or 'aligned')"
```

## 明确不做

- 不动 `size()` / `_allocate` 的机器路径（机器产物本来满足不变量）。
- 不做「人眼求和比对」的审图页版本以外的 UI 改进。
- 不处理同集内人改出的 clip 重叠（`_overlaps` 是机器侧纪律；人审页面已能看到，
  超出本方案）。
- 不提交 git（用户拍板）。

## 完成判定

- [ ] `verify_alignment`/`refit`/approve 闸/render 闸/审图页合计显示全部落地
- [ ] 新测试全绿，且变异检验做过（结果写进会话）
- [ ] CLAUDE.md / WORKFLOW.md 同步
- [ ] `docs/issues/` 删 B4、索引更新
- [ ] `uv run pytest` 全绿、preflight 通过
