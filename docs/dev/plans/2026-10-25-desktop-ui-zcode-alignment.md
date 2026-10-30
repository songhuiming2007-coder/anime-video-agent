# 桌面端 UI 风格整合（ZCode 对齐）方案

**编号**：D152
**日期**：2026-10-25
**状态**：天天看的壳已过一轮（2026-10-30）。续工施工图：[2026-10-30-desktop-ui-daily.md](2026-10-30-desktop-ui-daily.md)

## 背景

人反馈：AVA 桌面端 UI 割裂感强——照 ZCode 抄的部分（`tokens.css` / `SessionList` / 面板框语言）一套样，自写组件（`SessionHeader` 模型控件、`HumanCards`、`ScriptEditor`、`VoicePanel`、`NewEpisodeForm`）又一套样。

人原话：
> 「AVA有些地方UI几乎照抄ZCode（智谱的开源coding agent桌面端），有的地方是自己写的，就很割裂，什么时候闲了必须得深度整合一下，我建议是能用ZCode的UI就不要自己发明，哪怕自己写也模仿着他的风格，不然看起来难受极了」

**已定方向**（不需要再议）：
- 能用 ZCode 的 UI 就用 ZCode 的，不自己发明
- 自写也必须仿 ZCode 风格
- Spec 14 §2.4「不学」清单照旧遵守（次要不透明、品牌色规则、不引 Tailwind/lucide/Radix 等减法不动）

**本次解决**：差异点在「自写部分向 ZCode 风格对齐」，**不是推翻已定的减法**。

## 现状盘点（依据 `desktop/src/renderer/` 源码）

### A 类：明显借鉴 ZCode（Spec 14 直接落的）

| 组件 | 借鉴点 |
|---|---|
| `tokens.css` | ZCode 五层面分层 / 字号阶梯 / 圆角递减 / 低透明度线 |
| `style.css` 框架部分 | 12px 圆角独立框 + 4px 缝的 frame 语言 |
| `SessionList.tsx` | 期行两行结构 = ZCode task-row 改版（D1） |
| `icons.tsx` | 18 个手绘 SVG（Spec 14 §2.5） |
| `Splitter.tsx` | 分隔条语言 |

### B 类：自写、风格不一致的重灾区

| 组件 | 问题假设（待验证） |
|---|---|
| `SessionHeader.tsx` | 模型/推理下拉是 popover+行列表自写，样式可能没用 `ui-row` 体系；D91 后才加，不在原 spec 覆盖 |
| `HumanCards.tsx` | 446 行决策卡组件，自成一套卡片结构，未见借鉴 ZCode 决策 UI |
| `ScriptEditor.tsx` | 486 行稿编辑器，工具栏/区块样式自成 |
| `VoicePanel.tsx` | 713 行配音面板，最大的自写件 |
| `NewEpisodeForm.tsx` | 166 行建期表单，按钮/input 可能没用统一 token |
| `Composer.tsx` | 138 行输入区（已含 model/thinking slash 拦截），样式可能独立 |
| `ConversationPane.tsx` | 487 行会话面板，消息气泡样式自成 |

### C 类：小组件

`ui.tsx` / `copy.tsx` / `PaneBoundary.tsx` / `JobBar.tsx` / `ReviewRating.tsx` / `CoverImport.tsx` / `HumanTimeReadout.tsx` / `PreviewPane.tsx` / `PreviewSwitcher.tsx`

小组件但都自写，风格统一度未审计。

## 待办

### ① 盘点清单（第一步）

对每个 `.tsx` 组件打标：
- **Z（ZCode 借鉴件）**：结构或样式直接来自 ZCode
- **M（混合）**：用了 ZCode token 但结构自写
- **S（自写）**：完全自写、风格独立

产出一份 `desktop/src/renderer/STYLE_INVENTORY.md` 或在 issue 行末追加表格，让人一眼看清"割裂面有多大"。

### ② 对照 ZCode 仓库找对应物

对 B/C 类每个组件：
- ZCode 有对应组件 → 列出 ZCode 源文件路径，给出「照抄结构」方案
- ZCode 没有对应组件 → 按 Spec 14 §2.4「学什么」清单重写（面分层 / 字号阶梯 / 圆角递减 / 低透明线 / 克制语义色），保持 token 一致

ZCode 仓库：`github.com/zai-org/ZCode`（Apache-2.0），浅克隆参考 `29628c9`（Spec 14 时用的版本）。**可能需要重新拉新版**——Spec 14 是 2026-09-26 写的，到现在 ZCode 可能又加了组件。

### ③ 优先级排序（建议）

| 优先 | 组件 | 理由 |
|---|---|---|
| P0 | `SessionHeader` 模型控件 | 用户最常看；D147/D148/D149/D151 都在改它，顺手对齐风格最划算 |
| P0 | `Composer` 输入区 | 用户最常看 |
| P1 | `HumanCards` 决策卡 | 审批交互是核心场景 |
| P1 | `ConversationPane` | 会话主体 |
| P2 | `ScriptEditor` / `VoicePanel` / `PreviewPane` | 大组件，风格差异明显 |
| P3 | 其余小组件 | 影响小 |

### ④ 不动的事

- Spec 14 §2.4「不学」清单照旧：不引 Tailwind / lucide / Radix / 任何品牌资产；次要文字透明度、输入框实色边界、品牌色浅粉、绿色语义、"+/-变更行数"、多主题变体，**全部不学**
- `tokens.css` 的 token 值不动（已经对齐 ZCode 五层面）
- 组件的**行为/逻辑**不动，只改样式与结构对齐
- 桌面端与 ZCode 的进程模型差异不动（ava 是 main + host utilityProcess + renderer 三进程已借鉴）

## 施工红线

按 `plans/README.md` UI 规则 4：改样式/结构前先出方案让人选（截图 or mock），不直接动 UI。

## 验收

- 视觉：侧栏 / 中栏 / 预览区整体风格一致，不再有"这块是自己画的"感觉
- 代码：B/C 类组件要么标了"参考 ZCode 某文件"，要么标了"按 Spec 14 §2.4 落"
- 盘点清单落档（进 STYLE_INVENTORY.md 或 issue 行末）

## 关联

- D91（模型控件 D 块）
- D147/D148/D149/D151（模型控件功能改进，建议等本专项做完风格对齐再动功能）
- Spec 14（`archive/2026-09-26-desktop-visual-design-spec.md`）：原版 ZCode 借鉴方案
