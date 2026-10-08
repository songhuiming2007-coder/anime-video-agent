# `run_pipeline` 预检查拒掉多余位置参数与 `--redo` 空格段号（D53 ②）

日期：2026-10-08　状态：**已施工，机检通过（pytest 2161 通过，1 条既有竞态偶发见文末；vitest 469 / e2e 119 通过）**
关联：D53 ②（主）；D51；ADR-0025（不加工具）

## 起因

董香二期 `session.jsonl` 第 305–309 行：模型发 `run_pipeline{"command": "tts --redo 2 4 5 8 9 10"}`，预检查放行，人在卡上批准，0.2 s 后退出码 2：

```
argv: python -m pipeline.tts --redo 2 4 5 8 9 10
tts.py: error: unrecognized arguments: 5 8 9 10
```

argv 里**没有期目录**。根因在 `tools.py::validate_pipeline_command` 的自动补位：

1. `_extract_positional_args` 跳过 `--redo` 和它的一个值 `2`，把 `4 5 8 9 10` 当成位置参数；
2. 位置参数非空 → 认为调用方自己给了期目录 → 不补位；
3. `tts` 把 `4` 当成 episode，余下的报 unrecognized。

预检查有两个入口，都走这个函数：模型调用在弹卡前的 dry-run（`session.py` 工具审查 → `run_pipeline(confirmed=False)`），人在终端打 `/run`。修在这一处，两条路同时生效。

同一函数的旧债：函数注释写着「模块若新增带值旗标，须同步登记到 valued_flags，否则该旗标的值会被误判为位置参数」，但没有任何检查执行它（判据 6）。用 `ast` 扫自动补位的 7 个模块，漏登记的有：`clips --index-dir`、`review --expect-size`、`review --expect-mtime-ns`、`cover --pick`、`cover --character`。例如 `cover --pick 3` 会把 `3` 当期目录、不补位。

## 规则

只作用于自动补位的模块：`tts`、`clips`、`review`、`render`、`qc`、`cover`、`status`。这 7 个模块的 argparse 各只有一个位置参数（`tts` 另有子命令词 `run` / `probe`），已逐个核对。

1. **位置参数最多 1 个**（`tts` 去掉打头的子命令词后再数）。超了就在弹卡前拒，文案点名多出来的 token。
2. **`--redo` 值后紧跟裸段号**：`--redo` 的值（含 `--redo=…` 写法）后面紧跟的、形如段号（`^\d+(\.\d+)?$`）的裸 token 一律拒，并给出拼好的写法：

   ```
   拒绝执行：--redo 只接一个值，段号要用逗号连写。正确写法：tts --redo 2,4,5,8,9,10
   ```

   这条单独判，因为 `--redo 2 4` 只多出一个位置参数，规则 1 拦不住（`4` 会被当成期目录）。
3. 拒因走既有的 dry-run 拒因通道（`ToolVerdict("reject", reason=…)`）回给模型，不弹卡。

不做的事：段号是否存在由 `tts._apply_redo` 运行时对照真实稿件判（标签可能是 `12.3`，也可能是 `stale`），预检查不复制这份语义，只拦结构错。`tts --redo "2 4 5"`（引号包住、一个 token）是 tts 本身接受的写法，照旧放行。

## 带值旗标：补齐并防漂移

- `valued_flags` 提为模块级常量 `PIPELINE_VALUED_FLAGS`，补上上面 5 个。
- 新测试用 `ast` 扫 7 个模块加 `check_script` 的 `add_argument`：凡非布尔（action 不是 store_true / store_false / count / store_const）的 `--` 选项必须在 `PIPELINE_VALUED_FLAGS` 里。以后谁加了带值旗标没登记，测试当场红。

## 工具说明

`run_pipeline` 的 schema 示例从 `tts --redo 3` 改为 `tts --redo 2,4,5`，并写明「段号逗号分隔」。不加工具，工具数不变。

## 测试

期望值先在实现上实跑。

| 编号 | 输入 | 期望 |
|---|---|---|
| TA-1 | `tts --redo 2 4 5 8 9 10`（真实那条） | 拒；文案含 `tts --redo 2,4,5,8,9,10` |
| TA-2 | `tts --redo 2 4`、`tts --redo=2 4` | 拒（规则 2） |
| TA-3 | `tts --redo 2,4,5`、`tts --redo stale`、`tts --redo 2,4 --allow-engine-mix`、`tts --redo "2 4"` | 放行，argv 带期目录 |
| TA-4 | `tts run <期> --redo 3`、`tts probe 你好 --seed 3` | 放行（子命令词不计数） |
| TA-5 | `cover --pick 3` | 放行，argv 补上期目录（旧债） |
| TA-6 | `clips a b` | 拒（规则 1） |
| TA-7 | 经 `session.py` 工具审查：`run_pipeline{"command": "tts --redo 2 4 5"}` | `ToolVerdict` 为 reject，不弹卡 |
| TA-8 | ast 审计 | 绿 |

变异矩阵（`scripts/verify_mutations.py`）：

| 编号 | 改坏什么 | 该红的用例 |
|---|---|---|
| D53-MUT-1 | 去掉规则 1 | TA-6 |
| D53-MUT-2 | 去掉规则 2 | TA-1、TA-2、TA-7 |
| D53-MUT-3 | 从 `PIPELINE_VALUED_FLAGS` 删 `--pick` | TA-5、TA-8 |

## 施工备注

- `tests/test_review.py::test_expect_经等号形式可注入期目录` 的对照段原先断言「空格形式 `--expect-size 100` 不注入期目录」，记录的正是 `--expect-size` 漏登记的旧债。登记后空格形式也正确注入，断言按新行为改写（桌面端 `spawner.ts` 用的是等号形式，不受影响）。
- 全量里 `tests/test_jobs.py::test_child_process_killed_by_signal` 红过两次、单跑 3/3 绿：拿到 pid 就 SIGKILL，子进程可能还没打出 `CHILD_READY`，负载高时更易输掉竞态。它把 `validate_pipeline_command` 整个打桩，与本条无关，另登记。
