# 审批按参数分级 + 字幕可检索（D44）

日期：2026-10-08　状态：**已施工，机检通过，待真实窗口手验**
关联：D44；Spec 9（红线 5、H-4）；ADR-0018；ADR-0024 决策 1；ADR-0025（工具表封顶）

## 起因

董香人物志（二）改段落 3：模型把「句长起伏 CV」没过当成必须修，整篇重写 → check_script → 再重写，转了十余轮。每轮弹两张卡，人点了二十多次批准，`02-script.md` 仍然没改成。根因有三：

1. **审批只看工具、不看参数**：`run_pipeline check_script` 和 `status` 都是纯只读，却与 `tts`、`render` 同等弹卡；写模型自己的草稿也每次弹卡。
2. **没有防打转的刹车**：判重只拦一模一样的调用，检查点要等 50 次工具执行。当时唯一的刹车其实是审批卡。
3. **模型查不了字幕**：`search_notes` 只扫 `.md`、`.txt`，剧情时间码只能凭记忆编（「S01E09 依子送便当」是编的，实为 S01E04 03:52 与 S01E05 02:53）。

## 为什么不做全局 auto mode

auto mode 解决的是「点得烦」，没解决「它不该一直转」。把卡全撤掉，上面那种循环会悄无声息地跑满 25 轮。所以按参数分级：只读的、可回退的放行，可回退的再加次数上限。按钮一个不加，H-4（没有「全部批准」）和红线 5（人审闸门不自动化）原样成立。

## 施工内容

| 项 | 落点 | 内容 |
|---|---|---|
| A1 只读模块免卡 | `tools.READONLY_PIPELINE_MODULES`；`session.review_tool_call` | `check_script`、`status` 免卡。只认规范化 argv 的模块位（`_pipeline_module_of`），不认模型写的原始串 |
| A2 草稿限次免卡 | `tools.DRAFT_FREE_WRITES_PER_TURN = 3`；`review_tool_call(draft_writes_this_turn=)` | `02-script.draft.md` 每轮前 3 次免卡，第 4 次起弹卡，`stop_label` 写「本轮第 N 次重写草稿」。`01-topic.md`、`07-titles.md` 不变 |
| A3 计数 | `Session._draft_writes` | 每轮开始清零；免卡放行一次加 1；人批准一张超限卡后清零（再给 3 次）。TTY 和桌面端共用 `Session._review` 一条路径 |
| B 草稿留底 | `tools._keep_draft_history` | 覆盖草稿前把旧版存到 `<期>/_agent/draft-history/<UTC 纳秒时间戳>-02-script.draft.md`，保留最近 20 份 |
| C 字幕检索 | `search_notes` 加 `source`/`episode`/`anime` 参数 | `source="subs"` 读 `data/library/index/<番>_SxxEyy.json`，番名整串匹配防跨番；按 WINDOW=2 规则从末单元往前还原单句（173 个现存索引文件全部还原成功），结构不符就报错；有 query 返回命中句与前后句，只给 episode 返回整集台词（上限 2 万字）。纯 stdlib，不 import `subindex` |
| D 提示词 | `config/agent/scopes/creative.md` | 局部改稿只改点名段落；check_script 跑一次即停，节奏类不过只报告；同项连修两次不过就问人；剧情断言先查字幕 |

3 这个数的来历：写一次、修两次。与 creative.md「同一项连修两次仍没过就停下来问人」用的是同一个数。

现存测试中拿「写草稿」当弹卡样例的 6 处（`test_agent_pr6` M3、`test_agent_session` TK-6/TK-7、golden G3/G4/G-P1）改用仍逐次弹卡的 `07-titles.md`，测的仍是审批卡机制本身。golden 只变了目标文件名一处。

## 验证（已跑）

- `pytest` 全量 2106 passed；`desktop` vitest 437 passed（桌面端零改动）。
- 新测试：只读/写入模块分级；草稿第 1–3 次放行、第 4 次弹卡；真实 `run_turn` 端到端（一轮 5 写只弹 1 卡、下一轮重新计数）；留底保留 20 份；字幕单句还原、前后句、整集模式、跨番隔离、非滑窗结构报错；盘在时对拍真实 E09（「依子」零命中、探病 S01E05 02:53、雏实同住 08:27）；`import pipeline.agent.tools` 不拉 numpy/torch/subindex。
- 变异检验 6 个全部被抓到：只读集合加 `tts`；上限改 99；单句不剥后缀；番名匹配放宽成前缀；新一轮不清零；批准超限卡后不清零。

- **漏验补记（同日 D45 施工时发现）**：当时只跑了 pytest 与 vitest，没跑 Playwright e2e。`e2e/sessionReal.spec.ts` 有 9 处拿 `check_script` 当「会弹卡的工具」，本项施工后卡不再出现、8 个用例失败。已在 D45 里改用仍弹卡的 `qc`（TX-14 第二张卡用 `review`），全量 e2e 复跑见 D45 spec。

## 待人验（真实窗口）

- [ ] 桌面端打开董香二期，问「S01E09 董香有哪些戏」：不弹卡，返回 08:27、09:24、17:31、19:11、20:00 这些真实时间码
- [ ] 让它改一段：写草稿和 check_script 都不弹卡
- [ ] 让它反复改：第 4 次写草稿时弹卡，卡上写着「本轮第 4 次重写草稿」
- [ ] `_agent/draft-history/` 里有历史版本
