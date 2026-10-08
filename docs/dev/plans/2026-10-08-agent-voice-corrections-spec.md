# agent 经人审卡录读音纠错（期级 / 全局），校验读音，只重配错的段（D51 + D53 ④⑤）

日期：2026-10-08　状态：**已施工，机检通过（pytest 2182 / vitest 469 / e2e 119）；待真实一期手验。开放问题：「先试拼音还是同音字」的顺序待人定（见 issues D51）**
关联：D51（主）；D53 ④⑤；D50；ADR-0019（期级 overlay）；ADR-0025（工具数上限，本条不加工具）

## 起因

董香二期 03.5：人报「绚都、肉体、少年」读错。agent 没有录纠错的工具（`/voice`、`/voice-add` 都是人的入口），只能叫人进终端；人要求同音字替换，它两次给出同义词（身体、年轻人），还改在稿件里；给出的「肉惕」读 rou4ti4，不是 rou4ti3（D53 ④）；「绚都」贯穿系列四期，该进全局层一次生效，它没有提（D53 ⑤）。

人的裁决（2026-10-08）：
1. 复用 `run_pipeline`，不加工具；
2. 全局层 `config/voice.json` 可以经人审卡写；
3. 期级不加同音字，同音字只走全局 `readings`。

## 读码事实

- 人的两个入口都是 `corrections.parse_correction` + `corrections.append_correction`（单写者锁、写前指纹、回读确认）。
- `parse_correction` 不核对「拼音音节数 = 词的字数」，也不核对拼音是否该字的读音。
- 全局两张表同键时拼音优先、`readings` 那条被跳过（`tts.speakable_traced`）。所以「拼音直注不灵、改走同音字」时，必须同时删掉拼音表里的同键条目，否则新加的同音字是死条目。
- `config/voice.json` 的磁盘格式恰为 `json.dumps(indent=2, ensure_ascii=False)`（无尾换行），可逐字节往返。
- 期级纠错由 `tts --apply-patch` 只重配受影响的段；全局表改动后普通 `tts` 重跑只重配念法变了的段（`tts._reusable` 段级比对）。

## 设计

### A. CLI：`python -m pipeline.corrections`

进 `PIPELINE_MODULES`（不进只读集合，所以经 `run_pipeline` 一律弹人审卡）。期目录与 tts 一样自动补位：子命令词之后补期目录，不计入位置参数。

| 子命令 | 写什么 | 说明 |
|---|---|---|
| `add [期] --text "<文法>"` | 本期 `03-audio/corrections.json` 追加一条 | 文法同终端 `/voice`；与 `/voice-add` 同路（重新解析原文 → `append_correction`）；读音条目先过 C 节校验 |
| `global [期] --word W --pinyin P` | `config/voice.json` 的 `pinyin_injections[W] = P`（TONE3） | |
| `global [期] --word W --homophone H --expect P` | `config/voice.json` 的 `readings[W] = H` | `H` 必须逐音节读作 `P` |
| `global … --supersede` | 同时删除**另一张表**里的同键条目 | 不加时遇同键冲突直接拒，并说明那条会失效 |
| `check --word W (--pinyin P \| --homophone H --expect P)` | 不写 | 打印校验结果，agent 写入前自查 |

全局写入的纪律：

- 只改一个键（加 `--supersede` 时另删一个同键），其余字节不变；写回格式同磁盘原格式；
- 写前重读文件比对内容哈希，与开读时不一致（人在手改）→ 拒，不覆盖；临时文件 + `os.replace` 落盘；
- 文件路径写死为 `paths.CONFIG / "voice.json"`，**CLI 不接受路径参数**（否则等于一个任意 JSON 写入口）；
- 给了期目录时，输出本期配音文本里含该词的段号，供下一步只重配。

### B. AGENTS.md 例外

Code Freeze 加一句：`config/voice.json` 的 `readings` / `pinyin_injections` 可经 `pipeline.corrections global` 在人审卡批准后逐条新增、覆盖或（`--supersede`）删除同键；其余字段与 `config/` 其他文件照旧只读。

### C. 读音校验（纯函数，`add` / `global` / `check` 与弹卡前预检共用）

记 `P` 为期望读音的 TONE3 音节列表（输入可为 `xuan4du1`、`xuan4 du1`、`xuān dū`，经 `split_pinyin_syllables` 归一）。

1. **拼音合法且音节数 = 词的字数**（词全是汉字时）。不合法或数不对 → 拒。
2. **同音字逐音节等于 `P`**：`H` 按「汉字段用 pypinyin、拉丁段按拼音音节解析」转 TONE3，兼容 `ròu体` 这种混写。不等 → 拒，打印两边。例：`肉惕` = `rou4 ti4` ≠ `rou4 ti3`。
3. **`P` 是否属于原词各字的候选读音**（pypinyin heteronym）：不属于只警告，不拒。专名读法可能不在词典里，拿不到证伪信息不定罪（判据 4）。

同音字只看音，不看义；同义词替换在 2 这一步必然被拒。

### D. 弹卡前预检与卡面

`session.py::review_tool_call` 对 `run_pipeline` 的 `corrections add/global` 在 dry-run 之后再调 C 节纯函数（`add` 还要先 `parse_correction`，需要本期 `02-script.md`）：不成立直接 reject（不弹卡，同 D53 ②）；成立则把「解析出的条目 / 要写的键值 / 校验结果与警告 / 本期受影响段 / supersede 删除项」加进卡面。

### E. 只重配错的段

不新增机制。agent 录完后提议下一条命令（同样弹卡）：期级 `tts --apply-patch`；全局 `tts`（不带 `--force`）；云端期走 `cloud push` → `cloud run <期> tts -- --apply-patch` → `cloud pull`。

### F. 提示词与规程

- 配音前问本地还是云端；`01-topic.md` 写了 `配音: 本地` 或 `配音: 云端` 就不问；重配、apply 跟随本期 manifest 的引擎侧，不问。
- 人报读错：先说方案（拼音直注优先，拼音实测不灵才用同音字），问放本期还是全局（跨期专名建议全局），`corrections check` 自查通过后再提议写入；读错不改稿。

## 测试与变异

| 编号 | 场景 | 期望 |
|---|---|---|
| TV-1 | `check 肉体 --homophone 肉惕 --expect rou4ti3` | 拒，打印 rou4 ti4 ≠ rou4 ti3 |
| TV-2 | `炫嘟/xuan4du1`、`ròu体/rou4ti3` | 过 |
| TV-3 | `绚都 --pinyin xuan4` | 拒（音节数） |
| TV-4 | `绚都 --pinyin xuan4dou1` 以外的候选外读音（如 `xuan4da1`） | 过但带警告 |
| TV-5 | `add --text "5段 重叠 改成 zhong4die2"` | 条目结构与 `/voice-add` 相同 |
| TV-6 | `global --pinyin` 写入 | 只多一个键，其余字节不变 |
| TV-7 | `global --homophone` 而拼音表有同键 | 无 `--supersede` 拒；有则删拼音同键 |
| TV-8 | 读后文件被改 | 拒，不覆盖 |
| TV-9 | `review_tool_call` 坏读音 | reject、不弹卡；好的弹卡，卡面含校验结果 |
| TV-10 | `corrections add --text …` 补位 | 期目录补在 `add` 之后 |

变异：D51-MUT-1 去掉音节数校验；D51-MUT-2 去掉同音逐音节比对；D51-MUT-3 去掉同键冲突检查；D51-MUT-4 去掉弹卡前预检；D51-MUT-5 去掉并发改动检查。
