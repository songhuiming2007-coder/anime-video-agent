# 桌面端组件风格盘点（D152）

**日期**：2026-10-25
**基准**：`tokens.css`（ZCode 五层面 token，sha256 门禁锁定）+ `ui.css`（`ui-*` 类契约，冻结）+ `style.css`（业务区块）
**ZCode 参照**：`/tmp/zcode`，commit `3c5b2276`（2026-10-20，浅克隆最新版；Spec 14 当时用 `29628c9`，task-row 已挪到 `project-sidebar/grouped/`）

## 分档口径

- **Z**：结构或样式直接照 ZCode 落（Spec 14 直接产物，或完全由 `ui-*` 类组装无自建样式块）
- **M**：用 `ui-*` / token 变量，但自写了一套 ZCode 里没有同构对应物的结构（自建 CSS 类、自写布局块）
- **S**：完全自写、风格独立

注意：**ava 的 `ui-*` 体系本身就是 ZCode 语言的移植**（Spec 14 PR1 把五层面/字号阶梯/圆角递减/低透明线落成 token + `ui.css`）。所以 M 不是「没对齐 token」——全部组件都只用了 token 变量，零 hex 字面量——而是「这个区块的形状在 ZCode 里有没有原型」。偏差要点写的是「和 ZCode 同功能件长得不一样的地方」。

## 盘点表

| 组件 | 文件 | 行数 | 分档 | 与 ZCode 对应物 | 风格偏差要点 |
|---|---|---|---|---|---|
| tokens.css | tokens.css | 231 | Z | `packages/ui/src/styles.css` | 五层面 token 原样移植，sha256 门禁锁定 |
| ui 组件类 | ui.css / ui.tsx | 168 | Z | `components/ui/button.tsx` 等 | `ui-btn`/`ui-row`/`ui-popover`/`ui-card`/`ui-badge`/`ui-seg`/`ui-state` 全套 = ZCode 语言的无 Radix 版 |
| 图标 | icons.tsx | 135 | Z | lucide（不学） | Spec 14 §2.5 手绘 24 个 SVG，自造但属已定裁决 |
| SessionList | SessionList.tsx | 247 | Z | `project-sidebar/grouped/task-row.tsx`（`types.ts:49-52`：行 `min-h-7 rounded-lg pl-2.5 pr-1`，两行结构） | 期行两行 = task-row 改版（D1）；会话子列表 `.ep-session*` 沿用同语言 |
| 框架/栏位 | App.tsx + style.css 基座 | — | Z | `WorkspaceSidebar.tsx` / frame 语言 | 12px 圆角独立框 + 4px 缝、`ui-search`/`ui-seg`/期行搜索头 = ZCode 侧栏语言 |
| Splitter | Splitter.tsx | 68 | Z | ZCode 分隔条语言 | `.splitter` 拉手 |
| PaneBoundary | PaneBoundary.tsx | 44 | Z | — | 纯 `StateView` + `ui-btn` 组装，零自建样式 |
| Composer 输入壳 | Composer.tsx | 138 | M | `v4/ConversationComposer.tsx`（`rounded-2xl bg-surface` 输入壳 + 工具栏行） | 壳结构对齐（`bg-input`+`border-input`+`radius-xl` 是裁决版）；`.composer-queue`/`composer-queued` 排队块（D79）ZCode 无对应物，虚线边界 + `radius-xl` 自写 |
| 会话流 | ConversationPane.tsx | 487 | M | `ToolCallBlocks/ToolLayout.tsx`+`ToolSummaryRow.tsx`（摘要行「图标+类别词+主文本+状态」、运行中扫光 `animated-gradient-text`）、`v4/ConversationUserInputBubble.tsx:27`（用户泡 `rounded-xl rounded-tr-xs bg-surface`） | 结构概念照抄（D80 明记「照 ZCode」）：工具行三态/折叠/扫光/progress 条已对齐。偏差：① 全套 `conv-*` 类是自建词汇（ZCode 里是同语言 Tailwind 类）② 用户泡 `bg-subtle`+全圆角 vs ZCode `bg-surface`+右上小角 ③ 详情块 `conv-tool-detail`/`conv-obs` 自写 |
| SessionHeader | SessionHeader.tsx | 478 | M | `chat-input-toolbar/ComposerModelMenu.tsx`+`ModelConfigSelect.tsx`（模型菜单：分组 label、`min-h-8 gap-2` 行、右侧 check）；`v4/ConversationHeader.tsx`（会话头部） | 模型控件已用 `ui-popover`+`ui-row`+check 列（形已似）。偏差：① popover 内间距/metah 排版（`model-pop-*` 一族自建）② effort popover 的「自定义输入+切换」行 ZCode 是滑杆（`ComposerModelMenu.tsx:204-` 滑杆不学，保留行式）③ `.session-head`/`.session-notes` 布局自写 |
| HumanCards | HumanCards.tsx | 446 | M | `PermissionDialog.tsx`（待答项 = `rounded-xl` 行列表+序号+选中态 `bg-selected`，`PermissionDialog.tsx:975-1000`）；`AssistantCodeCommentCards.tsx:56`（`rounded-xl border bg-card` 卡+头行 `h-10`） | 决策卡已套 `ui-card`。偏差：① ZCode 待答 UI 是「编号行列表+单确认钮」，ava 是「卡片+批准/打回双钮」——形态差异是功能差异（停机点审批 vs 工具权限），不是风格偏差；`.fingerprints`/`.reject-form`/`.tail`/`.decision-ok/err` 一族自建样式 ② 请求卡 `.request-card-*`（D47/D82）自写 |
| ScriptEditor | ScriptEditor.tsx | 486 | M | ZCode 无稿编辑器对应物 | 工具栏/`editor-split`/`checklist`/`editor-src`/`editor-preview` 一族自建；全程 `ui-btn`/`ui-seg`/token，但布局块在 ZCode 无原型 |
| VoicePanel | VoicePanel.tsx | 713 | M | ZCode 无配音面板对应物 | `.voice-*`/`.seg-*` 一族自建（段行 grid、状态徽标、`voice-fix` 网格纠错行、`voice-foot` 底栏）；已用 `ui-btn`/`ui-badge`/`ui-kv`/`ui-popover`/`ui-seg`，行内语言接近（`.voice-row` ≈ 行列表），但区块结构无原型 |
| PreviewPane | PreviewPane.tsx | 407 | M | `PreviewPane.tsx:1587-1640`（头部 `h-10` 面包屑+右侧动作） | 同名校：`.preview-head`（h36+mono 路径）/`preview-body`/`.queue`（`ui-row` 复用）/`.markdown`/`.json`/`timecode`/`image-wrap` 一族自建；ZCode 头是面包屑（rootLabel › 段 › 文件），ava 是单路径串 |
| NewEpisodeForm | NewEpisodeForm.tsx | 166 | M | ZCode 无建期表单对应物 | `ui-field`/`ui-input`/`ui-btn`/`ui-row`（`proj-new`）组合，`.new-episode-form` 布局自建——是规范用法，偏差小 |
| PreviewSwitcher | PreviewSwitcher.tsx | 115 | M | `ModelConfigSelect.tsx` 下拉行语言 | `ui-popover`+`ui-row`+分组标题，`.preview-switch-*` 一族自建；结构与模型 pop 同源 |
| CoverImport | CoverImport.tsx | 87 | M | ZCode 无拖放区对应物 | `ui-dropzone`（Spec 12，ui.css 内）+ `ui-btn`+`notice`；规范用法 |
| JobBar | JobBar.tsx | 46 | M | `v4/ConversationQuotaBanner.tsx:119`（`rounded-xl border bg-card` 横幅） | `.jobbar` 一族自建：wait-surface 平铺整条 vs ZCode 横幅是 card 圆角浮条；`ui-spinner`/`ui-btn` 复用 |
| ReviewRating | ReviewRating.tsx | 68 | M | `ui-seg` 原用法 | `ui-seg` 打分 + `ui-btn`，规范 |
| HumanTimeReadout | HumanTimeReadout.tsx | 31 | M | — | 单个 `.notice` div，规范 |
| copy.tsx | copy.tsx | 94 | M | — | `ui-btn--ghost` 复制钮 + `copy-btn--done` 态，规范 |
| ConversationPane 内 HandoffStrip | （同 ConversationPane） | — | M | ZCode 无工单条对应物 | `.conv-handoff-row`（border+`bg-subtle`+`radius-lg`）自写 |
| PreviewPane 内 StateView/meta | （同 PreviewPane） | — | Z | — | `meta`/空态按 ui 契约 |
| main.tsx / autoOpen / layout / theme / rpc / store / convStore / testHooks | *.ts | — | — | — | 非组件，不评 |

## 汇总

- **Z（明显借鉴）**：7 项——tokens.css、ui.css/ui.tsx、icons.tsx、SessionList.tsx、App.tsx 基座+style.css 框架、Splitter.tsx、PaneBoundary.tsx
- **M（混合）**：13 个组件文件——Composer、ConversationPane（含 HandoffStrip）、SessionHeader、HumanCards、ScriptEditor、VoicePanel、PreviewPane、NewEpisodeForm、PreviewSwitcher、CoverImport、JobBar、ReviewRating、HumanTimeReadout、copy.tsx
- **S（完全自写）**：0 个。**割裂不在「没用 token」而在「自建区块的形状」**——所有组件都过了 VS-3（只用 token 变量），肉眼割裂来自 `conv-*`/`model-pop-*`/`voice-*`/`editor-*`/`jobbar`/`request-card-*` 等自建类的形状、间距、排布与 ZCode 同功能件不一致。
- 组件 .tsx 总行数 ≈ 5466（去掉非组件 ts 与 ui/icons 后 M 类 ≈ 3800 行，占组件行数 ~70%）。

**与 ZCode 的真实结构差异**（对齐时的边界）：
- ZCode 用 Radix+Tailwind，`DropdownMenuItem`/`Popover`/`HoverCard` 不能直接抄代码，只能抄**视觉参数**（行高 `min-h-7/8`、圆角 `rounded-md/lg/xl`、间距 `gap-2`、浮层 `rounded-lg border bg-menu p-1 shadow-md`、菜单项 `px-2 py-1 min-h-7 rounded-md`）。
- ava 已把这些翻成 token（`--radius-*`、`--space-*`、`--control-h`、`ui-popover`、`ui-row`）。对齐 = 让自建类向这套翻译后的语言靠拢，不是引 Radix。
- ZCode 新增而 Spec 14 没有的：模型菜单分组+供应商子菜单（`ComposerProviderMenu`）、上下文用量浮层（`contextUsage.tsx`+`ai-elements/context.tsx`）、权限编号行列表（`PermissionDialog`）、横幅卡（`ConversationQuotaBanner`）、代码评论卡（`AssistantCodeCommentCards`）。

---

## 待改造清单

**施工记录** 2026-10-30（PreviewSwitcher 人选 A）：浮层去掉「本期文件」标题；触发文件名常规字重。分组留下。

**施工记录** 2026-10-30（Composer/SessionHeader 人选 A）：停止改 ghost；LLM 已连接收成绿点 title。发送钮未动。

**施工记录** 2026-10-30（NewEpisodeForm 人选 A）：「＋ 新建一期」改 `ui-row proj-new`，与新建企划同一套；建期/建企划/建卷钮 `ui-btn--sm`，不再拉满侧栏。

**施工记录** 2026-10-30（工序条/封面/作业条 人选 A）：next_command 进详情；封面收成一行；作业条「运行中」不再加粗。

**施工记录** 2026-10-30（ScriptEditor 人选 A）：工具栏改 `ui-btn--ghost`，脏点在左；要点标题收成「人审要点」；机检输出收成底边一行。光标从 `--accent` 浅粉改 `--fg`（正文色，浅粉在白底上看不见）。

**施工记录** 2026-10-30（HumanCards 人选 A）：停机卡去掉 ISO/id/mtime_ns，产物改 ui-kv；bash 工具卡改用途+命令块，框线原文进「原文」。闸门按钮同权未动。

**施工记录** 2026-10-30（VoicePanel 人选 A）：行列表皮肤。去 `.voice-row` 行间线、工具栏 `ui-btn--ghost`、换种子悬停/选中/焦点才出、行内 ▶ 改手绘 play 图标、底栏迷你播放条接 `audio.currentTime`（`scaleX`，VS-7）。RPC / 全文铺开 / 就地纠错卡不动。验证：vitest `voiceClock` + `visual` 36 绿；e2e TE-3 / D50-A S6 / D87 绿。

**施工记录** 2026-10-25（P1–P3 批）：剩余 9 项全落。具体：

- **HumanCards**：`.fingerprints` 列表 `gap: 2px`（与 `ui-kv`/ZCode 行距一致）；`.request-card-text` 补 `padding + bg-subtle + radius-md`（与同仓 `.conv-tool-cmd`/`.tail` 输出块同一形状，ZCode 代码块惯例）。
- **ConversationPane**：`.conv-user` 对齐 `ConversationUserInputBubble.tsx:27`——底 `bg-subtle`→`bg-panel`（ZCode `bg-surface`）、右上角小角 `border-top-right-radius: radius-sm`（ZCode `rounded-tr-xs`）。
- **ScriptEditor**：工具栏 5 个裸 `ui-btn` → `ui-btn--sm`（24px，ZCode 工具栏密度；要点切换/保存/从草稿新建/封板/机检）。
- **VoicePanel**：工具栏 3 个 → `ui-btn--sm`；`.voice-row:hover` 补 `bg-hover`；`.voice-fix-input` 160px 硬编码 → `100%/max-200px`。
- **PreviewPane**：`.preview-head` 36px→40px（ZCode `PreviewPane.tsx:1587` h-10）；`.preview-switch-row` `min-h` 改 `control-h` token + `radius-md`。
- **JobBar**：`.jobbar` 平铺 `wait-surface`+`border-bottom` → `rounded-lg border bg-card` 浮条（ZCode `ConversationQuotaBanner.tsx:119`）；`jobbar--warn` 保留 surface。
- **NewEpisodeForm**、CoverImport/ReviewRating/HumanTimeReadout/copy/PaneBoundary：已是 `ui-*` 规范，未动。
- 验证：vitest 629 绿；e2e `stopPoint` D71 作业条（不压区域断言）绿、`ack` D74 预览切换器绿。

**施工记录** 2026-10-25（A 批）：P0 两项已落。具体：

- **SessionHeader 模型控件**：胶囊行 `radius-lg`→`radius-md`、`min-height` 改 `control-h` token；check 挪行尾（ZCode `pr-8` 右列，16px 列定位）；`model-pop-reset`/`model-pop-custom` 分隔线横贯浮层内边距（ZCode `dropdown-menu.tsx:188` `-mx-1 my-1`）；effort popover 同理（check 尾置、meta 右对齐不变）。文件：`SessionHeader.tsx` 4 处 DOM 顺序、`style.css` `.model-pop-*` 一族。
- **Composer 排队块**：`.composer-queued` 虚线→实线低透明（`--border`）、`radius-xl`→`radius-lg`（嵌套递减）。文件：`style.css`。
- 验证：vitest 629 绿；e2e `modelSwitch` 的长名+窄窗用例绿（截国 `/tmp/d152-shots/`），`sessionTitle` 绿。`modelSwitch` 主用例红是 D147 预选功能让“会话未起”时按钮可用的既有环境性不一致（git stash 验证，与本次改动无关）。

原则：只改样式与 DOM 结构对齐，**不改行为/逻辑**（model 切换 RPC、slash 拦截、审批流程一律不动）；不引 Tailwind/lucide/Radix；`tokens.css`/`ui.css` 值不动（门禁锁，ui.css 整体冻结）。所有改造写进 `style.css` 业务区块（冻结类的覆盖也写这里，如 `editor-split`/`session-head` 先例）或微调组件 className。

### P0（用户最常看）

**1. `SessionHeader` 模型控件 popover（`SessionHeader.tsx:321-478`，CSS `style.css:429-451` `.model-pop*`）**

- ZCode 对应物：`chat-input-toolbar/ComposerModelMenu.tsx`（菜单骨架）、`ModelConfigSelect.tsx:402-415`（模型行 `min-h-8 gap-2 pl-2 pr-8` + 右侧 `CheckIcon`）、`components/ui/dropdown-menu.tsx:77`（菜单项 `min-h-7 rounded-md px-2 py-1`）、`:45`（浮层 `rounded-lg border bg-menu p-1 shadow-md`）、`:188`（分隔 `-mx-1 my-1 h-px bg-border`）、`ComposerProviderMenu.tsx`（分组行）
- 照抄哪部分（翻译成 ui 语言）：
  - 浮层：`ui-popover` 是 `radius-xl + shadow-overlay + p-2`（≈ZCode `bg-menu rounded-lg p-1 shadow-md`），ui.css 冻结——**padding 不动**；偏差在内部行距，`.model-pop` 的 `gap: 2px` 已对齐 `flex-col gap-0.5`
  - 菜单行：`.model-pop-row` 的 `min-height: 28px` 已≈ZCode `min-h-7`；**圆角**改成 `radius-md`（ZCode 菜单项 `rounded-md`，现在继承 `ui-row` 的 `radius-lg`）；check 挪到**行尾**（ZCode `pr-8`+右 check，现在 check 在行首左侧占 16px）
  - 分组/分隔：`model-pop-reset`（恢复默认）上方线对齐 ZCode `-mx-1 my-1`——现 `margin-top: 2px; padding-top: 2px` 微调为 `margin: var(--space-1) calc(-1 * var(--space-2)) 0`（横贯 popover 内边距）；`model-pop-custom` 输入行同款
  - effort popover 同理；`model-pop-meta`（档位字母）右对齐保留
- 估改动量：CSS ~25 行 + tsx className 微调 ~10 行；不需要新 token；不改逻辑
- 备注：ZCode 的 effort 是滑杆气泡动画（`ComposerModelMenu.tsx:204-280`）——**不学**，保留行式列表

**2. `Composer` 输入区（`Composer.tsx:50-138`，CSS `style.css:296-300` `.composer-queue`、ui.css `.composer`）**

- ZCode 对应物：`v4/ConversationComposer.tsx:2606`（输入壳 `rounded-2xl bg-surface`）、`2414`（发送钮 `rounded-lg bg-brand`）
- 照抄/对齐：
  - 发送钮：ZCode 是 icon-only `ArrowUp` 圆形/方钮 `size-7 rounded-lg bg-brand`；ava 是 `ui-btn--primary ui-btn--sm`「发送」文字钮——**保留文字钮**（Spec 14 按钮语言），样式已是 token
  - 排队块 `.composer-queued`：虚线 `border`+`radius-xl` 自写 → 改成 `border: 1px solid var(--border)`（实线低透明，同 banner/notice 语言）或 `bg-subtle` 无边框，去掉虚线（ZCode 无虚线元素；ui.css 的 `ui-dropzone` 虚线是拖放专用语义，排队块不是拖放）；`radius-xl` → `radius-lg`（嵌在输入壳内，圆角随嵌套递减）
  - `.composer-target`（会话名）保留
- 估改动量：CSS ~10 行；无新 token；不改逻辑

### P1（核心交互）

**3. `HumanCards` 决策卡（`HumanCards.tsx:77-446`，CSS `.decision*`/`.fingerprints`/`.reject-form`/`.tail`/`.request-card*`）**

- ZCode 对应物：`PermissionDialog.tsx:975-1000`（选项行 `rounded-xl px-3 py-2`+`bg-selected`/`hover:bg-hover`+序号 `w-5`）、`AssistantCodeCommentCards.tsx:56-86`（卡 `rounded-xl border bg-card`+头行 `h-10 px-3`+展开列表 `border-t`）
- 照抄/对齐：
  - 卡壳 `ui-card` 已是 `radius-xl border bg-card`——同构，**不动**
  - `.fingerprints`：产物列表改 `ui-kv` 或 `text-sm`+`code` 行，间距用 `space-*`（现在 margin `var(--space-2)` 已有，微调对齐 `py-1` 节奏）
  - `.reject-form`：顶部分隔线已有 `border-top + padding-top`——对齐 ZCode 分隔语言（`my-1` 节奏、`border` 用 `--border`）；两个 `ui-field`/`ui-input` 已规范
  - `.tail`/`.stderr` 输出块：已是 `bg-subtle radius-md mono text-sm`（ZCode 输出块同款），**可不动**
  - `.request-card-text`/`request-card-fields`（D47）：mono pre-wrap 块，`radius`/`padding` 用 token——微调 `padding: var(--space-2) var(--space-3)` 与 `.conv-tool-cmd` 一致
- 估改动量：CSS ~20 行 + tsx ~10 行；无新 token；不改逻辑

**4. `ConversationPane` 会话面板（`ConversationPane.tsx` 全量，CSS `style.css:303-392` `.conv-*`）**

- ZCode 对应物：`ToolCallBlocks/ToolLayout.tsx`+`ToolSummaryRow.tsx`（工具行）、`ToolCallProgressIndicator.tsx`（进度条）、`v4/ConversationUserInputBubble.tsx:27`（用户泡）
- 已是 D80 人选方案 A 落地件，结构照 ZCode。剩余偏差微调：
  - 用户泡 `.conv-user`：`bg-subtle + radius-xl 全圆角` → 对齐 ZCode `bg-surface`（`--bg-subtle`→`--bg-panel`/`--bg-card`?）+ `rounded-tr-xs`（右上角小角 `radius-sm`）——**可选**，气泡形状属视觉裁决点，改动一行 CSS
  - `.conv-tool-detail`/`.conv-obs`：`conv-obs` 是 `danger-surface` 底（模型看到的原文块）——语义色块已克制，**不动**
  - `.conv-handoff-row`（D82）：`bg-subtle`+实线 border 已接近横幅语言，**可不动**
  - `.conv-fold`/`conv-chev`：折叠 chevron 旋转、`conv-shimmer` 扫光、`conv-progress` 细进度条——均照 ZCode（`animated-gradient-text`/`ToolCallProgressIndicator`），**不动**
- 估改动量：0–5 行 CSS（若不动用户泡则为 0）；建议**只动 `.conv-user` 一行**或不碰

### P2（大组件）

**5. `ScriptEditor`（`ScriptEditor.tsx:333-486`，CSS `.toolbar`/`.editor-*`/`.checklist`）**

- ZCode 无稿编辑器对应物 → 按 §2.4「学什么」清单自查：
  - `.toolbar`：h40 + `border-bottom` + `gap-2`——与 `preview-head`/ZCode 头部语言一致，**基本不动**；按钮统一 `ui-btn--sm`（现在裸 `ui-btn` 28px，ZCode 工具栏钮 `h-7`≈`control-h`——`ui-btn` 默认已是 `control-h`，可不改）
  - `.editor-split` 三栏 grid、`.editor-src`/`editor-preview`/`checklist`：布局块，token 化完整；`checklist` 的 `border-left`+`bg-subtle` 符合「面分层」，**不动**
  - 偏差只有细节：`.editor-preview` 背景 `--bg`（比面板深一层）——符合 ZCode 分层；`notice`/`tail` 复用规范
- 估改动量：~0-10 行 CSS；**很可能只需统一工具栏按钮为 `ui-btn--sm` + `ui-btn--ghost` 次要化**

**6. `VoicePanel`（`VoicePanel.tsx` 全量，CSS `.voice-*`/`.seg-*`/`pending`/`hetero`）**

- ZCode 无对应物 → 按 §2.4 清单：
  - `.voice-row` grid（28px 播放钮列 + 主区）：`border-bottom` 分隔行 vs ZCode 行语言是 `radius`+`hover:bg-hover` 无下划线——**改行底为无分隔+行内 `radius-md` hover**，或保留分隔（列表语言两种都存在于 ZCode：`storage/StorageCategoryList.tsx:34` 用 `divide` 式 card 列表）。建议对齐 `.seg-row` 语言（`border-bottom` 已有先例 `seg-row`/`preview-switch`）——**保留 border-bottom，行 hover 补 `bg-hover`**
  - `.voice-fix`（就地纠错浮层）：`ui-popover` 但 `position:static`+grid 两行——结构自写但 token 规范；网格列距改 `space-*`
  - `.voice-foot` 底栏：`bg-subtle`+`border-top`——面板底栏语言，**不动**
  - `.voice-synth--*` 色条：`wait/ok/warn/danger-surface` 语义色条，克制使用，**不动**
- 估改动量：CSS ~20 行（行 hover、间距归一）；tsx ~10 行；不改逻辑

**7. `PreviewPane`（`PreviewPane.tsx` 全量，CSS `.preview-*`/`.queue`/`.markdown`/`.json`/`.meta`）**

- ZCode 对应物：`PreviewPane.tsx:1587-1640`（头 `h-10` 面包屑 `px-3 gap-0.5`+`text-ui-base text-foreground-subtle`、右侧 ghost 动作钮 `rounded-lg`）
- 照抄/对齐：
  - `.preview-head`/`preview-switch`：ava 是 `preview-switch` 下拉（`h40`+mono label）+`preview-head` 文件名行（`h36`+mono）——两段头部 vs ZCode 单段面包屑；**建议合并思路保留下拉**（ava 有分组文件列表功能 ZCode 无），头部行高对齐 h36→`h-9`/40px、右侧动作钮 `ui-btn--ghost ui-btn--sm`
  - `.queue`（音频队列）：`ui-row` 复用，`li.current` selected 底——**不动**
  - `.markdown`/`.json`/`.plain`/`.text-wrap`：阅读排版，token 完整，**不动**
  - `.meta`/`.meta-title`：目录空态，`mono title`+muted——微调 `padding` 用 `space-4`
- 估改动量：CSS ~10 行 + tsx ~5 行

### P3（小组件）

**8. `JobBar`（`JobBar.tsx`，CSS `.jobbar`）**
- ZCode 对应物：`ConversationQuotaBanner.tsx:119`（`rounded-xl border bg-card` 横幅卡 + ghost 关闭钮）
- 偏差：`.jobbar` 是 `wait-surface` 平铺整条 + `border-bottom`；ZCode 横幅是 `rounded-xl` 卡片 `px-3 py-2` 浮在内容里
- 改造：`.jobbar` → `margin: var(--space-1) var(--space-3) 0; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--bg-card)`（或保留 surface 提示色 `wait-surface`+`radius-lg`+`border`），去掉 `border-bottom` 平铺
- 估改动量：CSS ~5 行；不改逻辑

**9. `NewEpisodeForm`（`NewEpisodeForm.tsx`，CSS `.new-episode*`）**——已规范（`ui-field`/`ui-input`/`ui-btn`/`ui-row`），`.new-episode-form` 间距 `gap-2` 规范。**~0 改动**，最多表单 padding 微调

**10. `PreviewSwitcher`（`PreviewSwitcher.tsx`，CSS `.preview-switch-*`）**——`ui-popover`+`ui-row`+分组标题结构与模型 pop 同源；偏差只有 `preview-switch-gtitle` 与 `model-pop` 分组标题不齐——对齐 P0 项 1 的 popover 间距后顺带统一。**~5 行 CSS**

**11. `CoverImport` / `ReviewRating` / `HumanTimeReadout` / `copy.tsx` / `PaneBoundary`**——已是 `ui-*` 规范用法。**0 改动**

### 改造总表

| 优先级 | 组件 | 估改动量 | 新 token | 动逻辑 |
|---|---|---|---|---|
| P0 | SessionHeader 模型控件 | CSS ~25 + tsx ~10 | 否 | 否 | **已施工** 2026-10-25 |
| P0 | Composer | CSS ~10 | 否 | 否 | **已施工** 2026-10-25 |
| P1 | HumanCards | CSS ~3 | 否 | 否 | **已施工** 2026-10-25 |
| P1 | ConversationPane | CSS 2 | 否 | 否 | **已施工** 2026-10-25 |
| P2 | ScriptEditor | tsx 5 | 否 | 否 | **已施工** 2026-10-25 |
| P2 | VoicePanel | CSS ~3 + tsx 3 | 否 | 否 | **已施工** 2026-10-25 |
| P2 | PreviewPane | CSS ~2 | 否 | 否 | **已施工** 2026-10-25 |
| P3 | JobBar | CSS ~3 | 否 | 否 | **已施工** 2026-10-25 |
| P3 | NewEpisodeForm | 0 | 否 | 否 | 已规范·不动 |
| P3 | PreviewSwitcher | CSS ~1 | 否 | 否 | **已施工** 2026-10-25 |
| P3 | CoverImport/ReviewRating/HumanTimeReadout/copy/PaneBoundary | 0 | 否 | 否 | 已规范·不动 |

**核心结论**：割裂的根不是 token 缺失，是**自建类的形状**（popover 行圆角/勾位、排队块虚线、jobbar 平铺色条、卡片内字段排布）。全部改造都在 `style.css` + 各组件 `className`，预估总量 ~100-130 行 CSS、~30 行 tsx，零新 token、零逻辑改动。改造后须经 Spec 14 门禁重验（tk.mjs、三态截图对比 mock）。
