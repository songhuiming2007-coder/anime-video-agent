# D152 续：桌面 UI 每日施工图

**编号**：D152（续工）
**日期**：2026-10-30
**状态**：天天看的壳已过一轮人选；按「下一处」逐项做
**人怎么开工**：对 agent 说「读 `docs/dev/plans/2026-10-30-desktop-ui-daily.md`，按下一处做 UI。」

关联：issues D152、[盘点](../../desktop/src/renderer/STYLE_INVENTORY.md)、[原方案](2026-10-25-desktop-ui-zcode-alignment.md)、Spec 14（`archive/2026-09-26-desktop-visual-design-spec.md`）、plans/README UI 规则 1–5。

---

## 〇、agent 先读这一节

你在做 **AVA 桌面端自写块向 ZCode 对齐**。不是新视觉系统，不是重做交互。

1. 打开本文 **§四 下一处**，取第一项未勾的。
2. 按 **§三 日循环** 走：先出 2–3 张真实窗口对照，等人选，再改产品代码。
3. 人选之前不改 `desktop/src/renderer/` 产品文件（选型 mock 除外）。
4. 做完勾掉该项，在本文件 §四 和 `STYLE_INVENTORY.md` 各记一行。不要 commit / push / 打包，除非人明说。
5. 人说「继续」→ 再取下一未勾项，从头走日循环。人说今天停 → 停。

基线：`8239af6`（2026-10-30 空模式「—」那笔）。后面每做完一项，把基线理解成当时 HEAD。

---

## 一、场景（UI 规则 1）

谁：宋桧铭，真实做片（02.5 改稿、03.5 顺听、05/09 停机）。

窗口：至少 **1280×800** 与 **1440×900**。三栏都开（侧栏 + 对话 + 预览）是对照默认；预览收起是做片常态，选型时至少一张要看得出该项在收起预览后还在。

连续做什么：开一期 → 看工序条/会话头 → 对话里批卡或改稿 → 右栏切文件/顺听 → 输入栏发消息或停止。

每步要看到：自写块和 ZCode 抄来的块（侧栏期行、`ui-btn--ghost`、`ui-row`、`ui-popover`）是同一套语言。不要看起来像内部工具突然插进来。

---

## 二、已定方向（不要再议）

- 能用 ZCode 的 UI 就用；自写也必须仿它。不引 Tailwind / lucide / Radix。
- Spec 14 §2.4「不学」照旧：次要文字透明度、输入框实色边界、品牌浅粉、语义绿、多主题变体。
- **只改样式与 DOM 皮肤**。RPC、写盘、批准/打回、纠错文法、建期路径一律不动。
- `tokens.css` / `ui.css` **值不动**（sha256 / 冻结门禁）。覆盖写在 `style.css`。
- 闸门按钮同权（VS-12）：`approval.decide` / `conv.answer` 的批准/打回 **不许** `ui-btn--primary`。
- 核心 `pipeline/agent/status_card.py` 的 `card_text` 框线 **不许改**（终端还在用）。桌面可以换皮呈现 `card_text`，原文仍可收在「原文」。
- 做视频期间不改 `pipeline/` `config/` `tests/`（AGENTS.md Code Freeze）。本施工图只动 `desktop/` 与 `docs/dev/plans/`。

### 不要重开的人选

| 已定 | 来源 |
|---|---|
| 03.5 全段可见 + 就地纠错卡 | D50-A |
| 02.5 编辑器留在预览栏 | D49-A |
| 工序卡默认一行，详情折叠；不并进会话头 | D39 C1 |
| 发送保持粉色文字钮「发送/排队」 | D152 Composer |
| 光标用 `--fg`，不用 `--accent` | D152 编辑器 |
| 顺听进度条只显示、不可拖 | D152 顺听 |
| 上下文读数是徽章（正文色） | D64-B |
| 工单卡面仍用 `card_summary` / 全文收起 | D82 |

---

## 三、日循环（每一项都走完）

对照目录：`docs/dev/plans/2026-10-30-<slug>-zcode-shots/`（进 git）。截图：`desktop/out/d152-<slug>-shots/`（**不进 git**）。照已有 `docs/dev/plans/2026-10-30-voice-panel-zcode-shots/` 那套 `mock.html` + `mock.css` + `shoot.mjs`。

1. **盘点该项**：打开对应 tsx / `style.css`，列出 e2e 依赖的 testid、不许动的 RPC。
2. **出对照**：现状 / **A（建议）** / B。静态 HTML，链 `tokens.css`+`ui.css`+`style.css`，不要 Electron。两档尺寸都拍。把 `desktop/out/d152-<slug>-shots/index.html` 给人。
3. **停下来等人选**。回 A、B、跳过、或口头改。
4. **只按人选改产品代码**。testid 保留。新 class 用已有 token。
5. **机检**：`cd desktop && npx tsc --noEmit -p tsconfig.json && npx vitest run tests/static/visual.test.ts`，再跑该项碰到的 e2e（不要拿全仓 pytest 当 UI 基线）。
6. **勾清单**：本文件 §四该项打勾；`STYLE_INVENTORY.md` 加一行施工记录。选型 README 改成「人选 X，已施工」。
7. 人说「看 / 打包」才 commit + `desktop && npm run release-build` + `open dist/mac-arm64/AVA.app`。人没说不要 push。`facts.jsonl` / `facts.md` 不准进 git。

选型 mock 允许「同一窗口只露出该项」；禁止为了好看改行为（例如把批准藏进菜单）。

---

## 四、下一处（按做片时出现的顺序；取第一项未勾的）

天天盯着的壳（顺听 / 停机卡 / 编辑器 / 工序条封面作业条 / 新建一期 / 停止+LLM / 预览切换器 / 空模式「—」）**2026-10-30 已过人选，不要重做**。提交链：`c7e2a8c` … `8239af6`。

- [ ] **1. 03.5 卡内打点**（`ReviewRating.tsx`）「记下打点」仍是实心底 `ui-btn--sm`。顺听每一期都看见。RPC `voice.review` 不动。testid `review-rating` / `review-save` / `review-*`。
- [ ] **2. 03.5 纠错卡与底栏**（`VoicePanel.tsx`）就地纠错「记下」是 `ui-btn--primary`；底栏「只重配这 N 段」是 primary；「撤回」是实心底。纠错文法、`voice.add` / `voice.global` / `voice.applyPatch` 不动。testid `fix-save` / `fix-cancel` / `voice-done` / `pending-retract`。
- [ ] **3. 素材模式重复**（`SessionHeader.tsx`）进了 asset 同时有徽章「素材模式」和按钮「素材模式」。按钮与 `conv.command` scope 不动。testid `session-scope` / `scope-toggle`。
- [ ] **4. 写稿卡 / 非 bash 请求卡**（`HumanCards.tsx`）bash / 停机卡已分层；写稿 diff、工单、其它工具卡仍整块 `card_text`。桌面可分层，**core `card_text` 不准改**。testid `request-card` / `request-card-text`。VS-12 仍适用。
- [ ] **5. 拒绝表单**（`HumanCards.tsx` `.reject-form`）点「打回」才出现。批准/打回同权不准改成 primary。
- [ ] **6. 09 封面格**（`HumanCards.tsx` `.cover-grid` / `.cover-opt`）只有 09 停机看见。原生 radio 组不准改成 button（VS-12）。
- [ ] **7. 用户泡边框**（`.conv-user`）`ui.css` 仍有 1px 边；`style.css` 只改过底和右上角。D80 结构不准拆。testid `conv-row`。
- [ ] **8. 空预览**（`PreviewPane.tsx` `StateView`）无文件时「选择左侧产物以预览」。
- [ ] **9. 时间线**（`App.tsx` `.timeline`）右下角，做片时要展开看作业。D71「看进度」仍要能打开它。
- [ ] **10. 健康面板**（`App.tsx` `.health`）顶栏按钮才开。
- [ ] **11. 左侧产物树**（`App.tsx` `ArtifactTree` / `.files`）标题「本期文件」是侧栏分段，不是预览切换器。
- [ ] **12. 工单提示条**（`ConversationPane.tsx` `HandoffStrip`）本会话开过工单才有。D82 复制交接文本不准拿掉。

某项人选「跳过」→ 勾掉并在该项末尾写「跳过，YYYY-MM-DD」。不要改成别的项。

十二项都勾完或人说停：把本文件状态改成「机检通过，待人验收」，等人按 UI 规则 2 用真实打包版做完一期。**agent 不得代勾验收。**

---

## 五、已完工（不要重做）

| 项 | 人选 | 提交 |
|---|---|---|
| 03.5 顺听行列表 | A | `c7e2a8c` |
| 停机卡 / bash 工具卡分层 | A | `d71e5b6` |
| 02.5 幽灵工具栏 + 光标 `--fg` | A | `972faf7` |
| 工序条收命令 / 封面一行 / 作业条「运行中」 | A | `0423e65` |
| 新建一期改成行 | A | `ca27037` |
| 停止 ghost、LLM 收成绿点 | A | `3ab9ef0` |
| 预览切换器去掉「本期文件」标题 | A | `cd47a13` |
| 会话头 auto 不再画「—」 | A | `8239af6` |
| 模型 popover 行距/勾位、排队块实线 | 2026-10-25 A 批 | 见 STYLE_INVENTORY |

---

## 六、机检与打包

```bash
cd desktop
npx tsc --noEmit -p tsconfig.json
npx vitest run tests/static/visual.test.ts
# 该项碰到的 e2e，例如：
PYTHONDONTWRITEBYTECODE=1 npx playwright test e2e/stopPoint.spec.ts   # 03.5 / 卡
PYTHONDONTWRITEBYTECODE=1 npx playwright test e2e/session.spec.ts -g "TX-4|TX-7|N32"
```

N32「素材模式」stdin 两条 `command` 在 `8239af6` 之前就红（pending 模型/推理 flush），**不是皮肤回归**，不要为它改 host。

人要看效果：

```bash
cd desktop && npm run release-build
open dist/mac-arm64/AVA.app
```

`out/build-info.json` 的 `desktopDirty` 必须是 `false`。

---

## 七、完成判定

- [x] 天天看的壳过完一轮人选（§五）
- [ ] §四 1–12 每项：人选或跳过，且产品代码与人选一致
- [ ] 视觉门禁绿；该项 e2e 绿（N32 预存在外）
- [ ] **人**用打包版真实做完一期，记下别扭之处（UI 规则 2；agent 不勾）

---

## 八、施工记录

| 日期 | 做了什么 |
|---|---|
| 2026-10-30 | 写下本施工图。下一处从 §四 第 1 项（记下打点）起。 |
