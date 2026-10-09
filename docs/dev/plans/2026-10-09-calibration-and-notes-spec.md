# 标定值由 agent 写进 config（D60）；ava 写番剧笔记并跑笔记对抗审查（D61）

日期：2026-10-09　状态：**已施工·机检通过（2026-10-09）**；`notes_review` 已在《罪恶王冠》上实跑一次（见施工记录），终审待做
关联：D59（人只批卡不敲命令）、D51（读音表例外先例）、D47 / D48（写稿卡 diff 与 edits）、D13（笔记对抗审查）、ADR-0025（工具封顶 14）、ADR-0028、`docs/dev/postmortems/workflow-history.md`「番剧笔记：三步流水线」

## 人的裁决（2026-10-09）

D59 留下的两项待人定：① 标定值落 config——**要**；② ava 写番剧笔记——**必须要**。

## 一、D60 标定值写入（AGENTS.md Code Freeze 第二个窄口子）

`pipeline/calibration.py`，写法同 `corrections.write_global_entry`：往返格式校验（`json.dumps(indent=2)` + 末尾换行，三份 config 实测可往返）、读后 digest 防并发、原子替换。

| 文件 | 键 | 取值域 | 实录追加到 |
|---|---|---|---|
| `config/project.json` | `visual.scene_threshold.<番>`、`visual.scene_threshold.<番>/<SPxx>` | 1–50 | `visual._scene_threshold_note` |
| 同上 | `visual.ccip_same.<番>`、`visual.ccip_margin.<番>` | 0–1 | `visual._ccip_note` |
| 同上 | `visual.face_expand.<番>` | 1–3 | `visual._ccip_note` |
| 同上 | `script.cpm` | 150–400 | `script._cpm_note` |
| `config/scenes.json` | `<番>.no_match` | 0–1 | `<番>._no_match_note` |
| `config/voice.json` | `titles.<歌名>` | 0.1–30 | `titles._note` |

- `calibration set <键路径> <值> --evidence "<标定实录>"`：弹卡（`[全局配置]`），卡面写文件、旧值 → 新值、追加的实录与影响面；实录至少 20 字。键不在白名单、值越界、实录太短在弹卡前拒。
- `calibration show <键路径>`：只读免卡。
- 标定判断仍归人：人看密度表 / 抽检表拍板，agent 只把拍板的数和证据落盘。

## 二、D61 新工具 `write_note`（ADR-0028，占 ADR-0025 第 14 槽）

`{anime, target: notes|review, report?, content | edits, reason}`。`notes` 写 `data/library/notes/<番>.md`（番名安全字符；新建须已登记于 `sources.json` / `config/characters.json` 或已有笔记）；`review` 只改已存在的 `<番>-对抗审查报告*.md`（填终审表）。覆盖前留底 `notes/_history/<番>/`；每次弹卡带 diff；不需要期目录。

## 三、D61 笔记对抗审查 `pipeline/notes_review.py`

`notes_review <番> [--episodes …] [--mechanical-only]`（弹卡）。

| 层 | 做法 |
|---|---|
| 第零层 厚度准入（机械） | 按 `### SxxEyy` 切集；每集编号场景 ≥ 4、带时间码的逐字台词 ≥ 2；总行数 ≥ 集数 × 30 |
| 第二层 引用核销（机械） | 「台词」+ 紧跟时间码 → 该集字幕 [t−3 s, t_end+5 s] 里找归一化原文：通过 / 时间码错（给实际位置）/ 全集找不到（待复核：简繁或转述） |
| 第一层 剧情 diff（LLM） | 每集一次零上下文调用：该集笔记节 + 整集字幕；只报与字幕矛盾、说话人 / 顺序存疑、字幕无据；逐字引用笔记原文 + 字幕 mm:ss，机械自校，核不上标 ⚠ |
| 第三层 元层 / 网源 | 留给终审：ava 用 web 工具核 |

报告 `notes/<番>-对抗审查报告-<日期>[-n].md`，从不覆盖（旧报告永久留档）；含覆盖率声明、抽样通过清单、问题表与空终审表。终审由 ava 逐条裁决，采纳的用 `write_note` 改笔记并记「已知更正记录」。状态卡只提示不拦。

## 测试与变异

见施工记录；变异：calibration 去白名单 / 不追加实录；write_note 番名不校验 / 不留底；核销窗口不加缓冲；引用自校去掉；报告覆盖旧文件。

## 施工记录（2026-10-09）

- 只读实测机械两层（真实笔记，不写报告）。初版正则把 `0:14:19` 吞成 `4:19`、长引文跨句判错，修正后：
  - 《罪恶王冠》：22 集小节，带时间码引文 74 条，通过 66、时间码错 2、找不到 6；8 集达不到「≥ 2 条带时间码台词」（抽看 S01E04：台词确实没给时间码，判对）。
  - 《伪恋》：通过 134 / 169，时间码错 35（多为偏 5–12 s，S12 行级对准不达标）；分集小节 20 个、片源 32 集，缺 12 集。
  - 剧场版：片源只登记 1 集，整份笔记当一集，不判「未按集展开」。《你的名字》《天气之子》引文多为日文或时间码不合，大量「找不到 / 时间码错」，待终审逐条看。
  - 《东京喰种》《春物》：没有 `### SxxEyy` 小节，判「未按集展开（写薄，致命）」——与 workflow-history 的写厚标准一致。
- 用例：`tests/test_calibration.py`、`tests/test_write_note.py`、`tests/test_notes_review.py`；变异 CAL-MUT-1/2、WN-MUT-1/2、NR-MUT-1..4。
- **实跑（2026-10-09，人授权）**：`notes_review 罪恶王冠`，模型 gemini-3.8-flash-high，22/22 集完成、0 失败。报告 `data/library/notes/罪恶王冠-对抗审查报告-2026-10-09.md`：厚度问题 8 集（带时间码台词不足）、核销问题 8/74、剧情 diff 46 条，其中 1 条被机械自校标 ⚠（原文在笔记里找不到）。
  - 对照字幕抽查：F17（集的母亲是春夏不是冴子，字幕 0:08:01–0:08:05）、F20（字幕是「把你送入天国」，笔记引成「這裡已是天國」）、F43（「我曾試過殺掉真名…」是春夏说的，不是仓知）确认是笔记的真错；F24 大概率成立；「梟 / 杏」名字、谁抢走飞那鲁这类要看画面或网源，留给终审。
  - 发现并修掉一个 bug：提示词把「说话人存疑 / 顺序存疑」写在同一行，模型照抄成合写类别（11 条标「类别不认识」）。提示词改为四类逐条列出，解析时合写取第一个认得的。本次报告不重跑，合写类别的条目内容不受影响。
