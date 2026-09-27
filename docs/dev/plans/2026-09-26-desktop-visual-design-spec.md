# Implementation Spec：桌面端视觉设计系统与 UI 美化（Spec 14）

日期：2026-09-26（**v0.3.1**，红队一轮修订（2🔴 + 9🟡 + 🔵 B1–B5）+ 二轮定向复审修订（4🟡 全收；🔵 B1–B3 全收，B4/B5 按人裁决关闭）+ **三轮定向复审 🟢**（0🔴/0🟡，4 条 🔵 由红队经人授权直接落地，见 §1.3）；状态：**🟢 可动工**；mock 已获人拍板（2026-09-26），S8-R22 / DIR-R3 / DOC-R1 / S8-R23 已授权（2026-09-26），N1–N11 已获人确认（2026-09-26））
上位文档：`docs/dev/plans/2026-09-22-harness-evolution-direction.md`（§0.1 产品画像、§0.3 借鉴边界、§4 施工红线八条、§5 明确排除）。**§6 二期需求原清单里没有本 spec**：来源是 2026-09-26 用户追加（对现状 UI 原话「堪比厕所」，指示学习 ZCode Desktop 的前端设计与 UI 风格），先例同 Spec 13，条目补登见 §6.1 DIR-R3
相关 ADR：ADR-0020（§4 桌面端）。**不新立 ADR**（§6.4）
契约依赖：**Spec 8**（已施工，`desktop/` 即现状，文档 `archive/2026-09-23-electron-desktop-spec.md` v0.5）；**Spec 10 / 11 / 12**（红队 🟢、**均未施工**；本 spec 给它们提供样式契约，**不阻塞它们**，边界见 §2.8、§6.2）
对应 issues：—
格式范本：Spec 2、Spec 3、Spec 8（`archive/`），及 Spec 10
撰写基线：HEAD `9eb1f97`；`cd desktop && npx vitest run` 121 passed；`uv run pytest` 1712 passed / 3 failed，3 条红全在 `tests/test_agent_web.py`，源于工作树里他人未提交的 `config/agent/web.json`、`pipeline/agent/web.py`、`tests/test_agent_web.py` 改动，与本 spec 无关，未触碰（均 2026-09-26 实跑）；另有本 spec 自己的 `docs/dev/plans/README.md` 状态行改动在途（红队三轮 🔵-4 补记）
附件目录：`docs/dev/plans/2026-09-26-desktop-visual-design-spec/`，全部纯文本：
- 将发货的样式：`tokens.css`、`ui.css`、`style.css`；
- mock：`mock-01`~`mock-06` 共 6 张，外加画布样式 `mock.css`；
- 判据与工具（v0.2 按红队 Y3 入库）：`tools/` 下的 `contrast.mjs`、`tk.mjs`、`vs.mjs`、`audit.js`、`render.cjs`、`gen.mjs`、`icons.mjs`。

PNG 截图不进 git，重现命令见 §2.7。

**拍板基准**（门禁 1 逐字对拍；2026-09-26 人拍板时的内容，sha256）：`tokens.css` = `3d62bafad38d4ff8d7f04161bfa2652ce8d2935674e5b061c0cc64218cb1a382`，`ui.css` = `9c123bc33c84962e2a5ad6c989f6d71151713fc42f4c56632f3d02ca5fd7d67b`。改这两个文件的任何取值都须回到人面前重看 mock。

**用户裁决记录**

| 日期 | 事项 | 裁决 |
|---|---|---|
| 2026-09-26（撰写） | 明暗主题 | **跟随系统 + 手动三档**（跟随系统 / 浅色 / 深色），选择持久化 |
| 2026-09-26（撰写） | 强调色 | **浅粉**，主按钮也用粉（浅粉底只配深色字）。v0.2 起受下一行「闸门按钮同权」约束：粉色主按钮只给非闸门动作 |
| 2026-09-26（撰写） | 图标 | **仓库内手绘少量 SVG**（不引图标库，不用 ZCode 的 lucide） |
| 2026-09-26（撰写） | 密度与字号 | **ZCode 密度**：正文 14px、4px 网格、辅助 12px、徽标下限 10px |
| 2026-09-26（撰写） | 期列表行右侧 | **当前工序号、状态点、待审徽标、相对时间**。v0.2 按红队 🔴-2：「待审」回到现状原文「停机」，见下两行 |
| 2026-09-26（撰写） | 侧栏入口 | **搜索框、视图切换、已完成折叠** |
| 2026-09-26（撰写） | 「已完成折叠」 | `status.py` 没有「已完成/已发布」状态（`status.py:398-409`：末态恒为 `09 人工发布` + `is_blocked=True`）。**本 spec 不做，登记待办** |
| 2026-09-26（撰写） | 施工顺序 | 用户请作者给建议。v0.2 采纳红队 Y9 的顺序（§8） |
| 2026-09-26（红队一轮后） | 🔴-1 主题持久化 | **方案 (a)：localStorage + `<html data-theme>`**，撤回 S8-R20/R21；代价是原生对话框与系统菜单只跟随 macOS 外观 |
| 2026-09-26（红队一轮后） | 🔴-2 停机标记与相对时间 | **两者都显示**：首行右侧恒为相对时间；「停机」标记放在次行工序原文前 |
| 2026-09-26（红队一轮后） | Y7 批准 / 打回主次 | **两者同权**：答复人工闸门的按钮一律用普通按钮，粉色主按钮只给发送、保存这类非闸门动作 |
| 2026-09-26（红队二轮期间） | 拍板 mock | **通过**：6 张 mock（v0.2 样式）连同 D1–D7 一并确认；红队请人专门看的两处也接受现状——分段控件按下态（B5，底色差 1.21 / 1.05，靠字色与字重区分）与脱盘时侧栏不变灰（B4）。此后 `tokens.css`、`ui.css` 为逐字基准 |
| 2026-09-26（红队二轮期间） | §6.1 修订请求 | **授权** S8-R22、DIR-R3、DOC-R1（授权不等于可动工：动工仍须本 spec 红队 🟢） |
| 2026-09-26（红队二轮期间） | Spec 8 §2.8 的「陈旧」标记 | **补**。按红队建议另开 Spec 8 修订 **S8-R23**（§6.1），不进本 spec 的范围闸门与新增文案清单 |

| 2026-09-26（红队三轮后） | §2.8 新增字符串清单 N1–N11 | **确认**（红队三轮已对拍 6 张 mock 与现状 renderer 源码，清单齐全） |
| 2026-09-26（红队三轮后） | S8-R23（Spec 8 补「陈旧」标记） | **授权**；位置为期列表头部 + 中栏顶部，与数据不可达横幅（`reach-banner`）分开 |

**作者默认（2026-09-26 已随 mock 获人确认）**

| 编号 | 默认 | 在哪看 |
|---|---|---|
| D1 | 期行两行。首行：标题 + 相对时间。次行：「停机」中性徽标（或未知空心圆点）+ `current_step` 原文。行高约 44px（ZCode 单行 28px，但 ava 期名是长中文，单行放不下工序） | mock-01 左栏 |
| D2 | 「停机」用中性灰徽标（不是蓝、不是红）。颜色分工：粉 = 选中与非闸门主操作，蓝 = 真实的待答请求（Spec 10 的卡），红 = 失败与故障，琥珀 = advisory 与警告，绿 = 成功 | mock-05 徽标行 |
| D3 | 主题三档放在顶栏右端「外观」图标按钮弹出的浮层里，用原生 popover API 实现：Esc 关闭，焦点回到触发按钮（E8 实测） | mock-01 右上 |
| D4 | 视图默认「按时间」；选择与主题一样存 localStorage | mock-01 左栏 |
| D5 | 中栏与预览是 12px 圆角的独立框，框间留 4px 缝，侧栏平铺（学 ZCode 的 frame 语言） | mock-01 |
| D6 | 预览区新增一条文件头：图标 + 相对路径 + 类型徽标 | mock-01 右栏 |
| D7 | 时间线顶部「观测层有损……」从琥珀色改为灰色 + ⓘ（它是常驻说明，不是警告），文字一字不改 | mock-01 底部 |

**作者自报的未实测假设**（红队优先攻击面；正文相应处已标「约」）

| 编号 | 假设 | 状态 / 退路 |
|---|---|---|
| A1 | `ui.css` + `style.css` 套在真实组件上与 mock 一致（mock 是按真实结构手写的静态标记） | 未测。退路：PR2 的 VE-1 对真实 app 跑同一份 `audit.js`；不一致处改 CSS，不改判据 |
| A2 | localStorage 同步读取后立即写 `data-theme`，首帧不闪 | 未测。`index.html` 的入口脚本是 `type="module"`（延迟执行），首帧可能先按系统外观画出空白底色（body 背景），最多一帧。退路：另加一个外链的同步脚本 `theme-boot.js` 放进 `<head>`（CSP `script-src 'self'` 允许），PR3 实测后再决定要不要 |
| A3 | 原生 `<audio controls>`、`<video controls>` 与滚动条随 `color-scheme` 变色 | 未测；只影响观感。PR2 截图交人看 |
| A4 | 行元素改 `<li><button>` / `<button>` 后，既有 e2e 零改动全绿 | 未跑 e2e。已逐条核对选择器：`preview.spec.ts:75,220` 对 `audio-queue li` 做 `toHaveText` 精确文件名断言，按钮内只放文件名、图标是无文本的 SVG，文本不变；`preview-security.spec.ts:53` 画廊文本同理；`.ep-name`（`ackFixtures.ts:244`）保留。退路：PR2 首日跑三份 e2e，红了先查选择器 |
| A5 | Spec 11 钉死的 CodeMirror 6 用 `adoptedStyleSheets` 注入主题，所以不被 CSP 拦 | **半测**：E3 证明 Electron 44 下可构造样式表不受本 CSP 拦截、`<style>` 元素被拦；CodeMirror（style-mod）实际走哪条路未测。这是 Spec 11 的 A1，本 spec 只提供证据 |
| A6 | 相对时间取期目录 mtime，能反映「最近一次顶层产物变动」 | **已测**（E6）。语义如实写进 §3.4 |
| A7 | 打包版 renderer 的 localStorage 跨重启保留 | 未测。打包版从 `file://` 载入，Chromium 按 origin 存进 userData；开发版的 origin 是 `http://localhost:<端口>`，端口变了偏好就丢（只影响开发，RF-12）。退路：VE-2 在打包版上手验一次（门禁 6） |

---

## 0. 一句话设计

**皮肤进 token，结构进类名，判定不进 UI，协议一字不动。** 新增三份样式：

- `tokens.css`：手写 CSS 变量。浅色为默认；深色写两块、逐值相同，分别响应「跟随系统」与「手动深色」。
- `ui.css`：只含 `ui-*` 组件类与 Spec 10–12 的预留类，作用域受守卫约束，所以 PR1 落地时旧界面零变化。
- `style.css`：基座与业务区块，和改类名在同一个 PR 里换掉。

主题三档存 renderer 的 localStorage，写到 `<html data-theme>` 上。Spec 8 §2.9 本来就授权「UI 便利状态由 renderer localStorage 记忆」，所以不改协议闭集，也不增加 settings 写入口。

零新 npm 依赖，理由有两条：CSP 会拦截运行时注入的 `<style>`（E1）；Spec 10 的 TG-4′ 要求答复类点击挂在小写原生元素上。所以交互组件的 API 就是「原生元素 + 类名」，只有纯展示件做成 React 组件。答复人工闸门的按钮一律同权，不做视觉引导。

机检只查客观的可访问性事实，分两层：token 对子表（290 对）；以及对真实渲染出来的 DOM 做逐元素审计，这一层合成祖先 opacity，并检查非文本边界。后一层两轮都抓到了前一层漏掉的真问题（E5、E9）。好不好看由人看 mock 拍板。

本 spec 不改任何文案、`data-testid`、数据流、rpc 调用、协议或写入面。

---

## 1. 红队裁决与修订纪要

### 1.3 第三轮红队定向复审纪要（v0.3 → v0.3.1，🟢 可动工）

红队独立复跑，与作者报数全部一致：冻结文件 sha256 与头部逐字相符；`tk.mjs` 290 对 0 失败、18 类最小值逐值相符；M37（透明渐变 → mock-01 浅色 `bgImageFail=61`）、M38、M39、M40、M41（退出码 1）全部复现；`vs12.mjs` 对现状 `DecisionBar.tsx` 恰报 3 处；18 次渲染元素数 82/63/33/70/72/54 与 VE-1 下限一致；`test_docs_invariants.py` 11 条全绿；B4/B5 撤回逐值核实（深色 `--border-input` 为 `#72727c`、stale 只置灰 `.center/.preview`）；N1–N11 与 6 张 mock、现状源码对拍无遗漏。结论 **🟢**，0🔴/0🟡，4 条 🔵 由红队经人授权直接落地（先例同 Spec 13 二轮）：

| 编号 | 问题 | 修订 |
|---|---|---|
| 🔵-1 | S8-R23「`renderer/` 全文没有该字样」字面不严谨（`DecisionBar.tsx:19` 注释含「陈旧」） | 改为「界面上没有该字样」（§6.1） |
| 🔵-2 | spec 与附件未入库，sha256 基准在 PR0 前无版本保护 | PR0 增「提交前重核 sha256 对拍」（§8） |
| 🔵-3 | VS-12 闸门卡片文件名单硬编码，文件增减会失明 | VS-12 补「名单增减须同步」（§7.1） |
| 🔵-4 | 撰写基线漏记 `README.md` 的 Spec 14 状态行改动 | 基线补记（头部） |

同日（2026-09-26）人确认 N1–N11、授权 S8-R23，裁决表新增两行。后续若有复审，范围限于 PR0 落盘时的 sha256 对拍与 README 状态行；其余章节三轮已闭环，不再重开。

**PR1 验收 session 的裁决（2026-09-26，验收方独立复跑后由人拍板）**：

| 编号 | 指控 | 复核 | 裁决 |
|---|---|---|---|
| VS-10/§2.5 冲突 | VS-10 要求 `ui.tsx` 只 import `react`，§2.5 要求图标路径数据只进 `icons.tsx`；`StateView` 要渲染状态图标，两条不能同时成立 | 属实（施工方按 VS-10 取字面，在 `ui.tsx` 内联 `file`/`alert`/`info` 三段路径，与 `icons.tsx` 逐字相同、当前无漂移） | **选 B：放宽 VS-10 白名单到 `react` + `./icons`**，§2.5 的唯一落点优先；`ui.tsx` 改 `import { Icon }`，删除内联路径。同日落地 |
| M10/M39 落点 | §7.2 把两条变异植在 `style.css`，但 §7.1 的 PR1 扫描集只含 `ui.css` | 属实（验收方实测：M39 植入 `style.css` 后 VS-3 全绿，该落点在 PR1 不可见） | 表内补落点注记，PR1 阶段按等价植入 `ui.css` 实跑；`style.css` 到 PR2 进扫描集 |

### 1.2 第二轮红队定向复审裁决与修订纪要（v0.2 → v0.3，4🟡 全收；🔵 B1–B3 全收，B4/B5 按人裁决关闭；原裁决「🟡 修订后定向复审」）

红队确认一轮两条 🔴 与 Y1–Y9 已关闭，并复跑了 tk.mjs（290 对）、vs.mjs、render.cjs（18 次、元素数 82/63/33/70/72/54）与 6 条变异，与作者报数一致。以下每条都先独立复核。

**关于 B4/B5 的一次作者返工**（如实记录）：作者起初把 B5 当客观缺陷修了：分段控件按下态描边改用 `--border-input`，并把深色 `--border-input` 从 `#72727c` 调到 `#7e7e88`，因为新增的对子测出它压在浮层上只有 2.67。B4 也改了：恢复整区置灰，这又使侧栏里的「停机」徽标降到 2.88，只好把透明度提到 0.8。改完还用新样式重渲了截图。随后人传来裁决：拍板 mock，接受 B4、B5 的现状，冻结 `tokens.css`、`ui.css`。作者据此把上述取值改动全部精确撤回，删掉只为 B5 加的那组对子与审计检查，并按拍板时的样式重渲截图。v0.3 相对拍板基准，两个冻结文件零取值变化（sha256 见头部）。

| 编号 | 红队指控 | 复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🟡-1 | `audit.js` 遇到任何带 `background-image` 的祖先就整段跳过，且不计数；一行透明渐变就能让 VE-1 对整片区域失明，下限断言照样绿 | **属实**。作者复现：植入 M1 后 mock-01 浅色为 38 处失败；加一行 `.left, .center, .preview, .topbar, .timeline { background-image: linear-gradient(transparent, transparent) }` 后只剩 12 处，`n` 从 82 降到 81 | **采纳** | `audit.js` 只对白名单媒体容器（`video, img, .image-wrap, .cover-opt, .video-wrap`）跳过，并计入 `skippedMedia`；其他祖先带 `background-image` 一律记为 `bgImage` 失败。VE-1 断言 `bgImage = 0`、`skippedMedia` 等于各状态的预期（mock 实测全为 0：时间码压在不透明的 `--bg-media` 上，不算媒体）。新增 M37：同一行透明渐变 → `bgImageFail=61` |
| 🟡-2 | VE-0 只在浅色下跑，而旧 `style.css` 与 token 的同名变量只在深色下冲突（特异性 0,2,0 > 0,1,0）；深色态又会因 `color-scheme` 的计算值变化全红 | **属实**（E11 实测：不改名时浅色 `--bg` 为旧值 `#fafafa`，深色被 token 覆盖为 `#161618`；`color-scheme` 由 `light dark` 变为 `dark`）。实验还暴露一点：**只改定义不改 `var()` 引用等于没改** | **采纳** | VE-0 在 `emulateMedia` 浅、深两态各跑一次，快照排除 `color-scheme`（理由：`light dark` 与 `dark` 在深色下对原生控件的实际渲染相同）；§4.1 写明改名须覆盖定义与全部 `var()` 引用；M31 改为深色态 |
| 🟡-3 | VS-12 只守住「不是主按钮」：把「打回…」（`DecisionBar.tsx:131`，只展开表单、不调用 `approval.decide`）改成 ghost 就能反向引导；动态 `className` 读不到 | **属实** | **采纳** | VS-12 改为：闸门卡片文件里每个 `<button>` 的 className 必须是含 `ui-btn` 的字符串字面量，不许 `--primary`/`--ghost`；同一动作区内各按钮的变体类集合（尺寸类除外）必须相同。原型 `tools/vs12.mjs`（typescript AST，6 条自测）。新增 M38（打回…改 ghost → 2 处违规） |
| 🟡-4 | 「新增文案只有两处」与设计矛盾：还有「当前工序」、类型标签、分组名、「外观」、「按时间 / 按工序」、占位符、title、相对时间各档；照此施工门禁 5 必红 | **属实** | **采纳** | §2.8 改为完整的「新增字符串清单」（N1–N11），门禁 5 对拍这张清单；清单随本版交人确认（§6.1 下方） |
| 🔵 B1 | 静态守卫旁路：① 在 `style.css` 定义带字面量的自定义属性，VS-3 放行；② 单组件覆盖焦点环（`outline-color`、负 `outline-offset`），VS-5 放行；③ VE-3 在 v0.2 删了 `outlineColor` 断言 | **属实**（作者用 vs.mjs 复现 ①②） | **采纳** | VS-3：`tokens.css` 以外禁止定义任何 `--*`；VS-5：`outline*` 只许出现在全局 `:focus-visible` 与两条豁免里；vs.mjs 各加自测，新增 M39、M40；VE-3 加回 `outlineColor === --focus-ring` |
| 🔵 B2 | `tk.mjs` 遇格式错或 blockDiff 时提前返回空表，移植后只断言「失败数 = 0」会空过 | **属实**（M2/M23 下 pairs=0） | **采纳** | VS-2 断言 `out.length === 290`；`tk.mjs` 命令行加同一兜底，不满足则退出码 1（M41：M2 下退出 1） |
| 🔵 B3 | §8 正文说 PR2 与 PR3 可并行，表里 PR3 前置却是 PR2 | **属实** | **采纳** | 改为串行：PR3 在 PR2 之后（两者都改 `App.tsx`） |
| 🔵 B4 | stale 不再让侧栏变灰，现状是整个 `.main` 置灰 | **属实**（`style.css:17`） | **人裁决接受现状（2026-09-26）** | 不改 CSS；S8-R23 补「陈旧」标记，给侧栏提供文字提示 |
| 🔵 B5 | 分段控件按下态底色差约 1.05:1，只靠字色与字重，无机检 | **属实**（作者复算：浅 1.21、深 1.05） | **人裁决接受现状（2026-09-26）** | 不改 CSS；§2.9 如实记为「按下态以字色与字重表达，人看图接受」，不列入机检 |
| 作者自查 | 本轮改动期间，一条 shell 命令因 `grep -c` 零命中返回 1，`&&` 短路，后续脚本未执行。作者一度误以为已生效，复查时发现 | — | — | 之后所有文本替换一律 `assert count == 1`，并逐条 grep 回读确认 |

**下一轮定向复审应限定的范围**：

1. §1.2 各条的修订是否落地：`tools/audit.js` 的白名单与计数、`tools/vs.mjs` 的 B1 两条、`tools/vs12.mjs`、`tools/tk.mjs` 的兜底；
2. §2.8 的新增字符串清单是否齐全（对照 6 张 mock 与 §3）；
3. §7.2 本轮新增的 M37–M41；
4. S8-R23 的写法。

其余章节未动。

### 1.1 第一轮红队裁决与修订纪要（v0.1 → v0.2，2🔴 + 9🟡 + 🔵 B1–B5 全收，B1 中一项部分属实；原裁决「🔴 驳回，v0.1 不可动工」）

红队报告为终端输出、未落盘，本表是唯一存档。每条都先对照工作树独立复核，复核方式写在「复核」列。

| 编号 | 红队指控 | 复核 | 裁决 | 修订动作 |
|---|---|---|---|---|
| 🔴-1 | 主题持久化改了 Spec 8 的方法闭集、生命周期消息与 settings 写入通路；`E_BAD_PARAMS` 不在错误码闭集；Spec 8 §2.9 已授权 localStorage；A2 在结构上不可能成立 | **属实**。Spec 8 §3.6 原文「修改只经 main 的原生对话框选择并二次确认」；`protocol.ts:21` 与 `service.ts:491,493` 只有 `E_BAD_REQUEST`；Spec 8 archive 第 306 行「UI 便利状态由 renderer `localStorage` 记忆，读写均 try/catch」；`main/index.ts:292-293` 为 `startHost(); createWindow();` 同步相继执行，host 的消息必然晚于窗口开始载入 | **采纳方案 (a)**（2026-09-26 用户裁决） | 撤回 S8-R20/R21；删 v0.1 §3.3–3.5、§4.4 与 VU-3/VU-4、M16/M17；新增 `renderer/theme.ts`（§4.2）；`tokens.css` 改为三块结构，VS-1 断言两块深色逐值相同；原生对话框只跟随系统外观，如实写进 §2.3；A2 改写为「模块脚本延迟执行，首帧可能闪一帧」 |
| 🔴-2 | 把 `is_blocked` 显示成蓝色「待审」、挤掉相对时间，并用 UI 自拟的「进行中」分组；已发布的期会永久挂「待审」；违反 Spec 8 §2.12「加停机标记」与本 spec 自己的文案冻结；照搬 ZCode 胶囊属错配 | **属实**。`status.py:398-409` 末态 `is_blocked=True`；`status.py:360-364` 的「07 自动质检（未通过）」为 `is_blocked=False`；Spec 8 archive 第 241、381 行「`is_blocked` 时加停机标记」；现状 `App.tsx:277` 文案为「停机」；ZCode `task-row.tsx:103` 注释所指是真实的待交互请求 | **采纳**；相对时间是否被挤掉交人裁决 → **两者都显示** | 期行恢复「停机」原文、改用中性徽标（D2）；首行右侧恒为相对时间；分组更名为「停机点 / 非停机点 / 未取到」；§2.4 删去「胶囊占位」这一条学习项；决策卡标题去掉徽标，副标题保持原文（aligned 态仍是「解封物已在终端生成，待你确认」）；同步修改 mock-01/02/05 与 S10-R2 |
| 🟡 Y1 | 附件违反自己定的守卫：两条 `:focus-visible` 规则、`.dev-mark` 用了 `--text-xs`；VS-3 按字面实现会误报（`white-space`、`.banner-red`、`transparent`） | **属实**（v0.1 `components.css` 第 25、186、267 行） | **采纳** | `.dev-mark` 规则删除（元素改用 `ui-badge`）；`.composer textarea:focus-visible { outline: none }` 写进 VS-5 的精确豁免（外壳 `.composer:focus-within` 画环）；VS-3 改为只扫声明值、选择器与属性名不扫，`transparent`/`currentColor`/`inherit` 放行；以上都写进 `tools/vs.mjs` 并带合成正反例自测 |
| 🟡 Y2 | 两层机检都漏掉：悬停行上的徽标 4.22、选中行上的未知圆环 2.92、侧栏搜索框边界 2.86、stale 在 opacity 0.6 下有效对比度约 2.65 而审计不读 opacity | **属实**（作者按原对子与合成方式复算，四个数字全部复现，E10） | **采纳** | 调色（§2.2）；对子表补三类叠法，扩到 290 对，复现 4.22 与 2.86 后调到全绿；未知圆环改用 `--fg-muted`；`audit.js` 合成祖先 opacity，stale 按非活动界面守 3:1（Spec 8 §2.8 要求「置灰」，不能去掉变暗），stale 透明度 0.6 → 0.75；审计新增输入框边界与状态点两类非文本检查；新增 mock-06（stale）；VE-1 夹具加 `page.hover()` 与 stale 两态 |
| 🟡 Y3 | VS-2 与 VE-1 的判据本体只在作者的 scratchpad 里 | **属实** | **采纳** | 判据与工具作为纯文本附件入库（`tools/`），spec 逐一引用（§7） |
| 🟡 Y4 | PR1 单独换 `style.css` 会造成中间态回退，选中期不可见且没有 e2e 会红 | **属实**。以现有 TSX 的类名与 v0.1 CSS 做差集，得 `.ep.active` `.stop-mark` `.section-title` `.decision-actions` `.decision-head` `.decision-readonly` `.job.failed` 等无样式 | **采纳** | 样式拆成 `ui.css`（PR1，作用域守卫 VS-11 保证对旧界面零影响）与 `style.css`（PR2，与改类名同一个 PR）；PR1 只把旧 `style.css` 里与 token 同名的 3 个变量（`--bg`/`--fg`/`--accent`）改名为 `--legacy-*`，由 VE-0「计算样式快照前后相等」证明零视觉变化 |
| 🟡 Y5 | mock 结构与 S8-R22 不一致（`.j-toggle` 仍是 `span`、音频队列是裸 `li`）；缺按钮重置；`<button>` 上用 `aria-selected` 属无效 ARIA | **属实**。`preview.spec.ts:75,220` 断言 `audio-queue li` 的精确文本 | **采纳** | 结构定为 `<li><button class="ui-row">文件名</button></li>`，按钮内只放文件名；`.j-toggle` 改 `<button>` 并加重置；`ui-row` 的选中态只认 `aria-current`；新增 VS-13（`button` 上禁用 `aria-selected`）；mock 按真实元素重渲 |
| 🟡 Y6 | 变异矩阵有缺陷：M17 的目标用例没有写盘失败场景；M11/M12 被既有红掩盖；VS-6/7/9/10、hover、stale、ARIA 无变异 | **属实** | **采纳** | M16/M17 随 🔴-1 删除；Y1 修掉后 M11/M12 不再被掩盖（vs.mjs 实跑：v0.2 基线 5 类全 0）；补齐变异并写明期望失败数，其中 21 条撰写时已实跑（§7.2） |
| 🟡 Y7 | mock 里藏着未声明的数据改动（`created_at` 被格式化、目录树显示子项数「12」）与产品决定（批准为主按钮） | **属实**（Spec 10 的 `isoTime.ts` 只做比较，不管显示格式） | **采纳**；主次交人裁决 → **两者同权** | 恢复 ISO 原文、删掉子项数；闸门按钮同权，新增 VS-12 机检；mock 同步 |
| 🟡 Y8 | 对 Spec 11/12 的覆盖是假象：`.editor-*` 是手写 DOM，Spec 11 实际用 CodeMirror 的 `.cm-*`；缺 Markdown 高亮 token；Spec 12 的拖放态不在类契约里 | **属实** | **采纳** | 删去 `.editor-src/.editor-line/.editor-gutter`；mock-04 的编辑器区明确标为「示意」；新增 `--syntax-heading/-link/-meta`，S11-R1 要求 Spec 11 用 `var()` 写 `HighlightStyle`；新增 `.ui-dropzone[data-dragover]` |
| 🟡 Y9 | S10-R2 反过来卡住已 🟢 的 Spec 10；单槽位优先级藏掉「运行中」，与 Spec 10:21 用户裁决冲突；「PR3/PR4 与 10–12 互不相干」不成立 | **属实** | **采纳** | S10-R2 降为建议性、不阻塞；期行不设单槽位，Spec 10 的徽标在次行并列显示（`.ep-step` 可容纳多个）；PR 顺序改为红队建议：本 spec PR1 → Spec 10 PR1 → 本 spec PR2 → PR3（§8） |
| 🔵 B1 | 文档数字：`--bg*` 应为 11 个；浮层「≥10:1」实为 9.23；「焦点环 3.89」口径不对；`accent-border` 口径未交代；CSS 头注释用旧编号；ZCode 提交日期应为 09-23 | 前五项**属实**；日期**部分属实**：`29628c9` 的 author date 为 2026-09-23、commit date 为 2026-09-24（`git log --format='%ad / %cd'`），v0.1 写的是 commit date | **采纳**（日期改为两者并列） | 数字全部按 `tk.mjs` 实跑重写（§2.2、VS-2）；时间码浮层口径改为「白画面 9.23 / 黑画面 21」；CSS 头注释改用 VS 编号 |
| 🔵 B2 | VE-2「跟随系统」无断言；VE-3 应循环 Tab 并设上限；VE-1 切主题的方式要写明；VU-1 要固定时区，跨年「M月D日」有歧义 | **属实** | **采纳** | 分别见 VE-1/2/3、VU-1 |
| 🔵 B3 | VE-4 的精确文本约束：`ack.spec.ts:505` 对 settings.json 做 `toEqual`；`preview-security.spec.ts:53` 画廊文本精确匹配 | **属实** | **采纳** | 方案 (a) 不碰 settings，`ack.spec.ts:505` 天然成立；画廊行按钮内只放文件名（A4） |
| 🔵 B4 | 渲染脚本路径绑死附件目录，归档后会断；`render.js` 是 CommonJS | **属实**，而且作者复现时撞上了同源问题：相对路径被 Electron 按脚本目录解析，未捕获的 rejection 让进程挂住不退 | **采纳** | 改名 `render.cjs`；路径全部取参数并按 cwd 解析；任何异常以退出码 2 退出；`gen.mjs` 按自身位置找附件目录 |
| 🔵 B5 | 只读决策卡也带 `--wait` 色条；数据盘不可达的真故障横幅被降到跟 Code Freeze 一样轻；外观浮层的键盘行为没写 | **属实** | **采纳** | 决策卡去掉色条，只读卡为灰色纯文本；`.banner-red` 加 3px 危险色左边线与中等字重，强于黄色横幅（mock-06）；外观浮层改用 popover API（E8 实测 Esc 关闭且焦点回到触发按钮） |
| 作者自查 | ① 审计新版在 mock-04 抓到 `⌘S` 键帽沿用 `--fg-muted`，在粉色主按钮里浅色 4.14、深色 1.36（E9）；② Spec 8 §2.8 要求 stale「置灰标『陈旧』」，现状代码只有置灰、没有「陈旧」字样 | ①修；②**只登记**：补文案属于 Spec 8 的功能缺口，不在本 spec 范围（§6.1 另列） | — | ① `.ui-kbd` 颜色改为 `inherit`，新增 M30；② 报人，是否补另行决定 |

**下一轮定向复审应限定的范围**：

1. §2.3 与 `tokens.css` 的三块结构、`renderer/theme.ts` 的读写与 VS-1、VS-9；
2. §8 的 PR 顺序与 PR1 的「零视觉变化」论证（VS-11 + 3 个变量改名 + VE-0）；
3. §7 两层机检能否再被绕过：`tools/tk.mjs` 的 290 对是否齐全，`tools/audit.js` 的 opacity 合成、stale 口径、非文本检查与下限断言；
4. 🔴-2 修订后期行、分组与决策卡是否仍有语义越界（mock-01/05）；
5. §7.2 已实跑的 21 条变异抽查。

其余章节只做了措辞与编号同步。

---

## 2. 关键设计决策（含代码现状证据）

### 2.1 决策 1：现状盘点——丑在哪，逐条列（正面回答设计问题 1）

证据来源：

- 对 HEAD `9eb1f97` 的 renderer 源码逐行阅读；
- 用 e2e 夹具启动真实 app 截图，浅、深两版（E4）；
- 对现有颜色逐对计算 WCAG 对比度（E2）。

| # | 维度 | 问题 | 证据 |
|---|---|---|---|
| U1 | 配色·语义错用 | 「停机」徽标用红底白字，与错误同色；停机点是正常的工序节拍，不是故障 | `App.tsx:277`、`style.css:22` |
| U2 | 配色·对比度不达标 | 琥珀色横幅白字 **3.64:1**，琥珀色提示字 **3.49–3.64:1**，都低于 4.5 | `style.css:10`、`:13`、`:33`；E2 |
| U3 | 配色·深色主题残缺 | 深色块只覆盖 7 个变量；深色下红字 **3.13**、JSON 紫 **2.90**、橙 **3.60**、绿 **4.01**；深色选中行是白字配浅蓝 `#4ea1ff` | `style.css:2`、`:20`、`:51-53`；E2、E4 |
| U4 | 配色·字面量散落 | 变量定义之外还有 13 处颜色字面量，共 7 种：`#fff` ×6、`#2e8b57` ×2、`#000`、`#b35c00`、`#8e44ad`、`rgba(...)` ×2 | `style.css:10-14`、`:28`、`:34`、`:36-37`、`:51-53`、`:76` |
| U5 | 信息层级·最重的元素放错了 | 选中期行是整行饱和蓝底白字，抢过了决策卡与预览 | `style.css:20`；E4 |
| U6 | 信息层级·机器事实喧宾夺主 | 决策卡里的机器事实（ISO 时间戳原文、`appr_…` id、19 位 `mtime_ns`）与标题同字号同色，没有层次，读起来是一整块等高的文字 | `DecisionBar.tsx:108`、`:114`；`style.css:15` |
| U7 | 信息层级·横幅响度不分 | Code Freeze 警告与数据不可达（真故障）都是通栏饱和色条，同等响度 | `App.tsx:227`、`:175`；E4 |
| U8 | 密度·命令逐字折行 | `next_command` 的绝对路径用 `word-break: break-all` 折成 6 行等宽字 | `style.css:24`、`App.tsx:325`；E4 |
| U9 | 留白·无网格 | 内边距混用 2/4/6/8/10/12/24px；产物树文件行贴着栏边 | `style.css` 全文；`App.tsx:365` |
| U10 | 状态可辨识 | 期列表只有「停机」一种标记；未取到工序时只显示「…」，与正常行不可区分 | `App.tsx:276-279` |
| U11 | 空/错/载三态各写各的 | 「选择一期」「加载中…」「读取中…」「读取失败」各用一个裸文本 | `App.tsx:192`、`:272`；`PreviewPane.tsx:20`、`:169-170` |
| U12 | 可访问性·键盘不可达 | 期行、产物树行、画廊行、JSON 折叠三角、音频队列项都是 `div`/`span`/`li` + `onClick`；全局没有焦点样式 | `App.tsx:274`、`:303`、`:363`；`PreviewPane.tsx:124`、`:235` |
| U13 | 字号·过小且无阶梯 | 正文 13px；stderr、诊断区、失败 tail 为 11px；字号直接写 px，散在 11 处 | `style.css:4`、`:59`、`:62`、`:78` |

（v0.1 的 U6 还写了「批准与打回两个一模一样的按钮，没有主次」。按 2026-09-26 裁决，闸门按钮同权本来就是正确的，此条已删。）

### 2.2 决策 2：token 体系——手写 CSS 变量，零新依赖（正面回答设计问题 2）

| 方案 | 结论 | 理由（证据） |
|---|---|---|
| **手写 CSS 变量 + 类契约** | **采纳** | 现有 `style.css` 5.4 KB，renderer 源码约 1.2k 行，用不上框架；零依赖，TG-1 精确白名单（`guards.test.ts:25-43`）不动 |
| Tailwind v4 | 否 | 要新增 `tailwindcss` + `@tailwindcss/vite` 两个构建期依赖和第二套样式语言；ava 用一张 token 表 + 静态守卫就能达到同样的约束，守卫还能查对比度与主题完整性 |
| CSS-in-JS（emotion、styled-components）及依赖它的组件库（MUI、Chakra 等） | **否，硬性不可用** | E1：生产 CSP `style-src 'self'`（`index.html:5`）下，运行时插入 `<style>` 与 `setAttribute("style")` 均被拦截，装上即无样式 |
| 无样式组件库（Radix、shadcn 等） | 否 | ① Spec 10 TG-4′ 要求答复类 `onClick` 挂在小写原生元素上，组件库的 `<Button>` 正是被禁的形态；② 复杂浮层只有「外观」一处，原生 popover API 即可（E8）；③ Spec 8 §5.1 明文排除「UI 组件库」 |

**token 表**（附件 `tokens.css`，共 79 个 token）：

- 颜色 40 个：面 `--bg*` 11、线 3、字 3、强调 6、语义状态 8、媒体浮层 2、代码 / JSON / Markdown 高亮 7；
- 非颜色：阴影 2、字体 2、字号 5、行高 3、字重 3、间距 7、圆角 5、尺寸 10、动效 3。

结构是三块（🔴-1 方案 a）：

- `:root` 浅色；
- `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {…} }`，对应「跟随系统」下的深色；
- `:root[data-theme="dark"] {…}`，对应手动深色。

后两块逐值相同，深色必须覆盖全部 40 个颜色 token（VS-1）。颜色值的格式纪律：只许 `#rrggbb`、`rgba(r, g, b, a)` 或整值 `var(--x)` 别名。这样检查器十几行就能解析；如果用 `color-mix`，Electron 给出的计算值是 `oklab(...)`，检查器就得引入色彩空间转换（E1）。

**浅粉的取值与约束**（数值均为 `tools/tk.mjs` 实跑）：

- 主按钮底 `--accent`：浅色 `#f7c5d5`，深色 `#f4b4c8`；字 `--fg-on-accent` 用深酒红，对比度 9.98（浅）/ 9.78（深）。
- 浅粉填充对白底只有 1.51:1，按钮轮廓几乎看不见。所以浅色主题加 `--accent-border: #d6457a`，口径是「对 panel / card / sidebar / bg 四种面的最小值」= 3.70（压在侧栏上）。
- 焦点环与粉色文字用 `--accent-strong`：浅 `#c2185b`，深 `#f48fb1`。焦点环口径是「对 6 种面的最小值」：浅 5.16，深 6.23。
- 选中行底 `--bg-selected`：浅 `#fce8ef`，深为 13% 粉（v0.1 是 16%，为让叠在上面的徽标过线而调低）。

**v0.2 调色**（Y2）：

| token | 主题 | v0.1 → v0.2 | 原因 |
|---|---|---|---|
| `--border-input` | 浅 | `#8e8e96` → `#818189` | 侧栏搜索框 2.86 → 3.40 |
| `--wait` / `--ok` / `--warn` | 浅 | 各加深一档 | 悬停行叠状态底的徽标 4.22–4.28 → ≥ 4.58 |
| `--fg-muted` | 深 | `#a1a1aa` → `#a9a9b1` | 选中行上的中性徽标 4.34 → ≥ 5.08 |

### 2.3 决策 3：主题三档——localStorage + `data-theme`，零协议改动（🔴-1 方案 a）

**机制**：

- 新增 `renderer/theme.ts`（§4.2）。`readTheme()` 在 try/catch 里读 `localStorage["ava.theme"]`，取值不在 `{"system","light","dark"}` 或读取抛错时返回 `"system"`。
- `applyTheme(t)`：`system` 时删掉 `<html>` 的 `data-theme` 属性，否则写入。
- `saveTheme(t)` 同样在 try/catch 里写 localStorage，写失败只影响重启后的记忆，当次照常生效。
- `main.tsx` 在 `createRoot` 之前同步调用 `applyTheme(readTheme())`。
- 授权依据：Spec 8 §2.9 原文「UI 便利状态由 renderer `localStorage` 记忆，读写均 try/catch」（archive 第 306 行）。VS-9 守护「localStorage 只在 `theme.ts`、且每处访问都在 try 块里」。

**代价**（如实声明）：原生确认框、系统菜单、窗口框只跟随 macOS 外观，不跟随 app 内的手动选择。例如系统为浅色、app 选了深色时，Spec 10 的原生确认框仍是浅色。

**首帧**（A2）：入口是 module 脚本，首帧可能按系统外观先画出空白底色，最多一帧。PR3 实测；如果确实可见，再加 `<head>` 里的外链同步脚本。

**e2e 的坑**（E4 实测）：Playwright 默认把页面的 `prefers-color-scheme` 模拟为 light，会覆盖 `nativeTheme`。本方案下：

- 手动三档由 `data-theme` 驱动，不受模拟影响；
- 验「跟随系统」必须先 `page.emulateMedia({ colorScheme: null })` 解除模拟（VE-2）；
- 已有的 e2e 在模拟的 light 下跑，行为不变。

### 2.4 决策 4：从 ZCode 学什么、不学什么（正面回答设计问题 3）

**源码依据**：`github.com/zai-org/ZCode` 浅克隆，提交 `29628c9`（author date 2026-09-23，commit date 2026-09-24），Apache-2.0 许可。读过的文件：

- `DESIGN.md`；
- `packages/ui/src/styles.css:461-760`（Zai Light / Zai Dark 两套主题 token）；
- `packages/ui/src/workspace-grouped-tasks/task-row.tsx`、`types.ts:45-47`；
- `lib/taskListItemPresentation.ts:33-53`；
- `WorkspaceSidebar.tsx`。

| 学 | ZCode 原样 | ava 的落法 |
|---|---|---|
| 面分层 | background / sidebar / panel / card / popover 五层，层次靠底色差而不是阴影 | `--bg*` 同构；`--shadow-*` 只用于浮层 |
| 独立框 + 4px 缝 | 会话区、侧窗各是 12px 圆角独立框，框间 4px 缝 | 中栏与预览同构（D5），不做拖拽改宽 |
| 线用低透明度 | border 10%、hover 5%、selected 5–10% | `--border` 10%、`--bg-hover` 5–6%；**输入框边界不学**（见下一张表） |
| 字号阶梯 | 14 为基，+4/+2/0/-2/-4，禁止任意 px | `--text-xs…xl` = 10/12/14/16/18，VS-4 机检 |
| 圆角随嵌套递减 | 外层容器 xl，嵌套逐级 lg → md → sm | `--radius-sm…xl` = 4/6/8/12 |
| 任务行结构 | 行高 28px、圆角；标题截断 + 右侧元信息（6px 状态点、相对时间「3分」） | 期行同构，按中文长名改为两行（D1）；**不学「胶囊占掉相对时间」**：那是为真实待交互请求设计的，套到 `is_blocked` 上是错配（🔴-2） |
| 入口组织 | 侧栏头部：搜索、视图切换、归档开关 | 搜索框 + 「按时间 / 按工序」分段控件；归档不做（§2.6） |
| 克制 | 「语义色只用于真实状态」「大面积不许铺品牌色」 | 写进 token 注释；U1、U7 的直接解法 |

| 不学 | 原因 |
|---|---|
| 次要文字用 60% 透明度 | E2：ZCode 浅色次要字对白底 **4.13:1**，不达 4.5 |
| 输入框边界用 10% 透明度 | 非文本对比度远低于 3:1。ava 用实色：浅 3.40 / 深 3.17 |
| 品牌色 = 纯黑 / 纯白 | 用户裁决为浅粉 |
| 「等待确认」用绿色 | 绿色在 ava 表示 succeeded |
| 用户可调字号（`--ui-font-size`） | YAGNI |
| Tailwind、lucide、Radix、任何品牌资产与 Logo | §2.2、§2.5 |
| `+/- 变更行数` | ava 没有对应物，换成 `current_step` 原文 |
| 多主题变体、Windows/Linux 窗框规则 | 只做 macOS、两套主题 |

### 2.5 决策 5：图标——仓库内手绘 18 个 SVG

- 规格：16px 网格、1.5 描边、`stroke="currentColor"`，无颜色字面量（VS-3 覆盖）。
- 清单：`chevron-right`、`chevron-down`、`file`、`folder`、`film`、`wave`、`image`、`play`、`stop`、`search`、`refresh`、`pulse`、`appearance`、`close`、`alert`、`info`、`check`、`repo`。路径数据在 `tools/icons.mjs`，施工时收进 `renderer/icons.tsx`。
- 全部由作者按几何原语手写，不描摹、不取自任何图标组。
- CSP 兼容（E3）：内联 SVG 的表现属性（`stroke` 等）不受 `style-src` 管辖，实测生效。
- 组件 `<Icon name size? />` 是纯展示件，带 `aria-hidden="true"`。图标永远配文字或 `aria-label`：顶栏「外观」按钮只有图标，带 `aria-label="外观"`。

### 2.6 决策 6：期列表——只呈现事实，不改语义（🔴-2 修订）

数据全部来自既有 `EpisodeSummary`（`protocol.ts:150-156`），零协议改动。

| 元素 | 来源 | 规则（冻结） |
|---|---|---|
| 当前工序 | `currentStep` | **原文**显示，`null` 显示「…」（现状如此） |
| 停机标记 | `isBlocked === true` | **文案「停机」不改**（Spec 8 §2.12），改为中性灰徽标（D2），放在次行工序原文前；不用 `--wait`，不表达「等你处理」 |
| 状态点 | `currentStep === null` | 空心圆环（`--fg-muted`），表示「未取到」；其余情况不画点。次行的点位同时给 Spec 10 的「运行中」转圈与「N 张卡待答」徽标并列使用，不设单槽位、不互相遮挡（Y9） |
| 相对时间 | `mtimeMs` | **首行右侧恒显示**（2026-09-26 用户裁决）；`shared/relTime.ts`（§3.4）；每 60 s 与 `visibilitychange` 时重算；`title` 为「期目录顶层最近变动」 |
| 搜索 | `epKey` | 大小写不敏感的子串匹配；无命中时显示空态「没有匹配的期」（**新增文案**，属新功能的空态，不是改既有文案；人看 mock 时一并确认） |
| 视图切换 | `isBlocked` | 「按时间」= host 原序（`episodes.ts:55-56`）。「按工序」= 三组：**停机点**（`true`）/ **非停机点**（`false`）/ **未取到**（`null`），组序固定，组内保序，空组省略。组名是对 `is_blocked` 布尔值的直译，不推断「进行中 / 已完成」 |
| 已完成折叠 | — | **不做**（用户裁决）：core 没有「已发布」事实，登记待办 |

### 2.7 决策 7：交付物形态与评审方式（正面回答设计问题 4）

| 交付物 | 附件 | 落地 |
|---|---|---|
| token 表 | `tokens.css` | PR1 原样复制为 `renderer/tokens.css` |
| 组件类 | `ui.css` | PR1 原样复制为 `renderer/ui.css` |
| 基座与业务区块 | `style.css` | PR2 替换 `renderer/style.css` 中 Spec 8 时代的规则（§8 清单） |
| 组件清单 mock | `mock-05-components.html` | 按钮（含闸门同权示例）、输入、分段、徽标、状态点、列表行三态、「按工序」分组、拖放区、横幅、空/错/载、18 个图标、字号阶梯、色板 |
| 页面 mock | `mock-01-workbench.html` | 现有三栏（含悬停行与外观浮层） |
|  | `mock-02-conversation.html` | Spec 10 版面套 token |
|  | `mock-03-preview.html` | 预览 9 态 |
|  | `mock-04-stop-points.html` | 02.5（编辑器区为示意）、03.5、05 打回表单与失败回执、09 封面与标题（含拖放区） |
|  | `mock-06-stale.html` | 数据盘不可达：置灰 + 强横幅 |

- mock 只引用将要发货的三份 CSS，外加一份不发货的 `mock.css`。页面带与生产 `index.html:5` **逐字相同**的 CSP。
- mock 由 `tools/gen.mjs` 生成（改 mock 只改生成器，不手改 HTML）。
- **截图与审计命令**（在 `desktop/` 下执行）：`./node_modules/.bin/electron <附件>/tools/render.cjs <附件目录> <输出目录>`。
  - 每张 mock 渲染 light / dark / forced-dark 三态，共 18 张 PNG，同时注入 `audit.js`。
  - 末行输出 `TOTAL_FAIL` 与 `CSP_VIOLATIONS`，当前均为 0（E5 第三轮）。
  - 路径按 cwd 解析，出错以退出码 2 退出。
  - PNG 不进 git。
- **评审 = 人看图拍板**。人要改只改取值，然后重跑 `tk.mjs` 与 `render.cjs`，**不改判据**。

### 2.8 决策 8：与 Spec 10–12 的接口与重构边界（正面回答设计问题 5）

**契约有三层**：

1. **CSS 变量清单**（§3.1）：新 CSS 只许引用这些变量。
2. **类契约**（§3.2）：交互元素一律是原生元素加 `ui-*` 类，与 Spec 10 TG-4′「`onClick` 只挂小写原生元素」兼容。状态用原生或 ARIA 属性表达：`disabled`、`aria-current`、`aria-pressed`、`aria-expanded`、`aria-busy`（`<button>` 上不用 `aria-selected`）。**闸门同权**：`onClick` 里调用答复类方法（`approval.decide`、`conv.answer`）的按钮不许带 `ui-btn--primary`（VS-12）。
3. **纯展示 React 组件**：`Icon`、`Badge`、`StatusDot`、`StateView`，props 类型里没有任何 `on*`（VS-10）。`ui.tsx` 的 `StateView` 渲染状态图标时从 `./icons` 取（§2.5 的路径数据唯一落点优先于 VS-10 的 import 白名单长度，2026-09-26 裁决）。

**重构边界**（冻结）：

- renderer 里**只许**改：
  - `className`；
  - 元素种类，仅限 U12 所列的可点击行与折叠控件，改为 `<button>` 或 `<li><button>`；
  - 插入 `Icon` / `Badge` / `StatusDot` / `StateView`；
  - 新增侧栏头部、外观浮层、预览文件头（D6）。
- **不许**改：
  - 任何可见的既有文案（例如 `ack.spec.ts:642-643` 断言的「本次审阅不计人时」，`App.tsx:277` 的「停机」）；
  - 任何 `data-testid` / `data-*` / `data-row` 与健康面板的 `td` 结构（`ack.spec.ts:547`）；
  - e2e 引用的旧类名：`.ep-name`（`ackFixtures.ts:244`）、`.meta`（`preview.spec.ts:125,234`）、`.advisories li`（`ack.spec.ts:650`）；
  - rpc 调用、协议、状态逻辑、自动呼出规则、决策条的四道闸；
  - 任何时间或数字的显示格式（ISO 与 `mtime_ns` 保持原文，Y7）。
- **新增字符串清单**（v0.3 按红队二轮 🟡-4 补全；门禁 5 对拍的就是这张表，清单之外的任何新增可见字符串都算违规）：

| 编号 | 字符串 | 位置 | 来源 |
|---|---|---|---|
| N1 | 「当前工序」 | 工序卡标签（`.status-label`） | mock-01/06 |
| N2 | 「HTML」「视频」「音频」「图片」「Markdown」「JSON」「文本」「其他」「目录」 | 预览文件头的类型徽标（D6），按 `previewKind` 一一映射 | mock-01/02 |
| N3 | 「停机点」「非停机点」「未取到」 | 「按工序」分组头（§3.5） | mock-05 |
| N4 | 「外观」 | 顶栏图标按钮的 `aria-label` 与浮层标题 | mock-01 |
| N5 | 「跟随系统」「浅色」「深色」 | 外观浮层三档 | mock-01 |
| N6 | 「按时间」「按工序」 | 侧栏视图切换 | mock-01 |
| N7 | 「搜索期名」 | 搜索框占位符 | mock-01 |
| N8 | 「没有匹配的期」 | 搜索空态 | §2.6 |
| N9 | 「期目录顶层最近变动」 | 相对时间的 `title` | mock-01 |
| N10 | 「刚刚」「N 分钟」「N 小时」「N 天」「M月D日」「YYYY年M月D日」 | 相对时间各档（§3.4） | mock-01 |
| N11 | 分组头与期总数的数字计数 | `.ui-count` | mock-01/05 |

  mock-02（对话面板）与 mock-04（停机点组件）里的字符串属于 Spec 10/11/12 各自的设计，只作示意，不计入本清单；本清单也不含 S8-R23 的「陈旧」。**N1–N11 随 v0.3 交人确认**（§6.1 下方）。

**与各 spec 的接口**（全部为**建议性修订**，不阻塞对方施工，Y9）：

| spec | 接口 | 修订请求 |
|---|---|---|
| Spec 10 | `HumanCards.tsx` 的卡片用 `.ui-card`，停机点卡另加 `.decision`；按钮用 `.ui-btn*`，**答复类按钮同权**；对话流用 `.conv-*`，输入框用 `.composer`，会话头用 `.session-head`（mock-02 给出全部形态）；期列表的「运行中」「N 张卡待答」徽标放在期行次行，与「停机」并列、不互相遮挡；时间线「横幅不变」按「文字与出现条件不变」解释，颜色改为说明色（D7） | S10-R2（建议性） |
| Spec 11 | `ScriptEditor` 用 `.toolbar` / `.editor-split` / `.checklist`；CodeMirror 主题经 `EditorView.theme`、`HighlightStyle` 只引用 CSS 变量（`--syntax-heading/-link/-meta/-key/-str`、`--fg`、`--fg-muted` 等），行号用 `--fg-muted`；「封板」「确认落盘」「完成并应用补丁」是闸门动作，同权；`VoicePanel` 用 `.seg-row` | S11-R1（建议性） |
| Spec 12 | 09 卡的封面选择用 `.cover-grid` / `.cover-opt[aria-pressed]`；导入拖放区用 `.ui-dropzone[data-dragover]`；压在图片上的文字只用 `--overlay-*`（DOM 审计看不到媒体上的文字，RF-3）；「批准并记录定稿」同权 | S12-R1（建议性） |

### 2.9 决策 9：明暗双主题与可访问性基线（正面回答设计问题 6）

判据只取客观、可复算的可访问性事实（WCAG 2.x 定义），不取任何审美量：

| 判据 | 阈值 | 机检 |
|---|---|---|
| 正文对比度 | ≥ 4.5:1；≥ 24px 或 ≥ 18.66px 粗体时 ≥ 3:1 | VS-2（token 对子表）+ VE-1（真实 DOM 逐元素，合成祖先 opacity） |
| 非文本对比度（焦点环、输入框边界、主按钮边界、状态点与圆环） | 对其所在的**每一种面与叠法**（含悬停行、选中行）≥ 3:1 | VS-2 + VE-1 的非文本检查 |
| 禁用态 | 按 WCAG 1.4.3 豁免对比度；`--fg-subtle` 仍 ≥ 3:1 | VS-2、VE-1 |
| 非活动界面（stale，Spec 8 §2.8 置灰） | 文字 ≥ 3:1；非文本按 WCAG 1.4.11 例外豁免 | VE-1 stale 态（`audit.js` 合成 opacity） |
| 字号下限 | 正文内容 ≥ 12px；10px 只许用于 `.ui-badge` / `.ui-count` / `.ui-kbd` | VS-4 + VE-1 |
| 焦点可见 | 全局唯一 `:focus-visible { outline: 2px solid var(--focus-ring); outline-offset: 2px }`；`outline: none` 只许出现在 `:focus:not(:focus-visible)` 与 `.composer textarea:focus-visible` 两处 | VS-5 + VE-3 |
| 键盘可达 | 所有可点击的列表行与折叠控件是原生 `<button>` | VS-8 + VE-3 |
| 两套主题完整、两块深色一致 | 深色块覆盖全部 40 个颜色 token；跟随系统块 ≡ 手动深色块 | VS-1 |
| 颜色不单独表意 | 状态点与文字同行，徽标自带文字 | 代码评审项（非机检，如实声明） |
| 动效可关 | `prefers-reduced-motion: reduce` 下过渡与动画归零 | VS-6 |
| 不许借背景图躲审计 | 只有白名单媒体容器（`video, img, .image-wrap, .cover-opt, .video-wrap`）上的文字可以跳过，且计数；其他祖先带 `background-image` 一律算失败 | VE-1（`bgImage = 0`、`skippedMedia` = 预期） |
| 分段控件按下态 | **人已看图接受**（2026-09-26，红队二轮 B5）：按下态以字色（`--fg` 对 `--fg-muted`）与字重区分，底色差 1.21 / 1.05 不作为状态指示；不列入机检，如实声明 | — |

**为什么两层都要**：

- **E5**：对子表 148 对全绿时，DOM 审计仍抓到徽标三层叠法（4.40–4.49）与 `--fg-subtle` 误用（3.42 / 3.87）。
- **红队 Y2**：又找出悬停叠法与 opacity 两类盲区。
- **E9**：v0.2 审计抓到主按钮里的键帽 1.36。

对子表是代理量，「token 合格」不等于「用对了 token」。按判据 1（测的必须是真实产物），**VE-1 是验收主判据**，VS-2 是开发时的快速反馈。VE-1 还必须断言「检查了至少 N 个元素」：M18 实测，审计选择器失效时只检查到 2 个元素，总失败数仍为 0。

### 2.10 决策 10：范围闸门

- **做**：
  - §2.1 全部问题的样式与结构修复；
  - token 与类契约；
  - 主题三档（localStorage）；
  - 期列表的停机标记、状态点、相对时间、搜索、视图切换；
  - 图标；
  - 空 / 错 / 载三态统一；
  - 键盘可达与焦点；
  - 可访问性机检；
  - 6 张 mock。
- **不做**：
  - 「已完成折叠」与任何归档；
  - 协议、settings、生命周期消息的任何改动；
  - 拖拽调整栏宽；
  - 用户可调字号；
  - Windows/Linux；
  - 任何既有文案改动（包括 Spec 8 §2.8 缺失的「陈旧」字样，另报人，§6.1）；
  - 时间或数字的显示格式化；
  - Spec 10 的版面重排；
  - `04-review.html` 与 shots gallery 的内部样式；
  - 截图比对式视觉回归（那是把审美变成机器判据，违反判据）。

---

## 3. 数据契约

### 3.1 CSS 变量清单（冻结名单；取值见 `tokens.css`，人看 mock 后可改值、不可改名）

| 组 | 变量 |
|---|---|
| 面（11） | `--bg` `--bg-sidebar` `--bg-panel` `--bg-card` `--bg-popover` `--bg-input` `--bg-subtle` `--bg-hover` `--bg-selected` `--bg-media` `--bg-frame` |
| 线（3） | `--border` `--border-strong` `--border-input` |
| 字（3） | `--fg` `--fg-muted` `--fg-subtle`（仅用于禁用态与装饰） |
| 强调（6） | `--accent` `--accent-hover` `--accent-border` `--fg-on-accent` `--accent-strong` `--focus-ring` |
| 状态（8） | `--ok` `--ok-surface` `--warn` `--warn-surface` `--danger` `--danger-surface` `--wait` `--wait-surface`（`--wait` 只用于真实的待答请求） |
| 浮层与高亮（9） | `--overlay-bg` `--overlay-fg` `--syntax-key` `--syntax-str` `--syntax-num` `--syntax-lit` `--syntax-heading` `--syntax-link` `--syntax-meta` |
| 阴影（2） | `--shadow-overlay` `--shadow-float` |
| 排版 | `--font-sans` `--font-mono` `--text-xs` `--text-sm` `--text-base` `--text-lg` `--text-xl` `--leading-tight` `--leading-normal` `--leading-reading` `--weight-regular` `--weight-medium` `--weight-semibold` |
| 间距与形状 | `--space-1`…`--space-6` `--space-8` `--radius-sm` `--radius-md` `--radius-lg` `--radius-xl` `--radius-pill` |
| 尺寸与动效 | `--row-h` `--control-h-sm` `--control-h` `--control-h-lg` `--icon-sm` `--icon` `--dot` `--sidebar-w` `--center-w` `--dur-fast` `--dur-base` `--ease` |

字体只用系统字体：CSP 没有 `font-src`（`default-src 'none'`），webfont 加载不了。

### 3.2 类契约（冻结名单）

| 组件 | 类（所在文件） | 状态表达 |
|---|---|---|
| 按钮 | `ui-btn` + `--primary`（仅非闸门动作）/ `--ghost` / `--sm` / `--lg` / `--icon`（ui.css） | `disabled`、`aria-busy`、`aria-expanded` |
| 输入 | `ui-input` `ui-textarea` `ui-field` `ui-search`（ui.css） | `:focus-visible` |
| 分段控件 | `ui-seg > button`（ui.css） | `aria-pressed` |
| 图标 | `ui-icon` `ui-icon--sm`（ui.css） | — |
| 徽标 / 计数 / 键帽 | `ui-badge` + `--wait` / `--ok` / `--warn` / `--danger` / `--accent`；`ui-count` `ui-kbd`（ui.css） | — |
| 状态点 / 转圈 / 骨架 | `ui-dot` + `--ok` / `--danger` / `--unknown`；`ui-spinner` `ui-skeleton`（ui.css） | — |
| 列表行 | `ui-row` `ui-row-title` `ui-row-meta`（ui.css）；期行加 `ep` `ep-line` `ep-name` `ep-step` `ep-step-text` `ep-time`、停机徽标加 `stop-mark`；树行加 `tree-row`（style.css） | `aria-current`、`aria-expanded` |
| 分组头 | `ui-section`（ui.css） | `aria-expanded` |
| 卡片与事实 | `ui-card` `ui-card-head` `ui-card-title` `ui-card-sub` `ui-card-actions` `ui-kv` `ui-code` `ui-divider`（ui.css） | — |
| 空 / 错 / 载 | `ui-state` + `--error` / `--inline`；`ui-state-title` `ui-state-detail`（ui.css） | — |
| 浮层 | `ui-popover` `ui-popover-title`（ui.css；元素带 `popover` 属性，触发按钮带 `popovertarget`） | `:popover-open` |
| 拖放区 | `ui-dropzone`（ui.css） | `data-dragover="true"` |
| 横幅 / 提示 | `banner` + `banner-red` / `banner-yellow` / `banner-grey`（保留现名，红色最强）；`notice` `notice--warn`（style.css） | — |
| 业务区块 | `app` `topbar` `main` `left` `center` `preview` `preview-head` `preview-body` `status` `status-label` `status-step` `advisories` `decisions` `decision` `decision-readonly` `fingerprints` `reject-form` `queue` `j-toggle` `timeline` `health`（style.css，沿用或新增） | `.main.stale` |
| Spec 10–12 预留 | `conv*` `dock` `composer*` `session-head`；`toolbar` `editor-split` `checklist` `seg-row`；`cover-grid` `cover-opt`（ui.css） | `aria-current`、`aria-pressed` |

### 3.3 `renderer/theme.ts`（纯 renderer，localStorage 键 `ava.theme`）

| 函数 | 行为 |
|---|---|
| `readTheme(): Theme` | 在 try 里读取；非法值、缺失或抛错一律返回 `"system"`（主题不是安全面，取中性默认值，不当故障） |
| `saveTheme(t: Theme): boolean` | 在 try 里写入；失败返回 false，调用方不报错、当次照常应用 |
| `applyTheme(t: Theme): void` | `system` 删 `data-theme`，否则设为 `t` |

同一模块还存视图偏好（键 `ava.episodeView`，取值 `"time" | "step"`，同样的读写纪律）。

### 3.4 `shared/relTime.ts`（纯函数，冻结）

`formatRelTime(mtimeMs, nowMs, tz?)`，按差值分档：

| 差值 | 输出 |
|---|---|
| < 60 s，或为负（时钟回拨） | 「刚刚」 |
| < 60 min | 「N 分钟」 |
| < 24 h | 「N 小时」 |
| < 30 天 | 「N 天」 |
| ≥ 30 天且同年 | 「M月D日」 |
| ≥ 30 天且跨年 | 「YYYY年M月D日」（B2） |

**语义声明**（E6）：值取期目录 mtime，只在期目录**顶层**有增删或原子写入时刷新；子目录写入（配音段）与原地改写不刷新。

### 3.5 `shared/episodeView.ts`（纯函数，冻结）

- `filterEpisodes(list, q)`：`q.trim()` 为空时原样返回；否则做 `toLowerCase` 后的子串匹配，保持原序。
- `groupByStep(list)`：返回 `{ key: "stopped" | "running" | "unknown", label: "停机点" | "非停机点" | "未取到", items }[]`。按 `isBlocked` 的 `true` / `false` / `null` 三分，组序固定，组内保序，空组省略。

---

## 4. 模块接口与签名

### 4.1 `renderer/`（样式）

- `tokens.css`、`ui.css`（PR1 新增，附件原样）。
- `style.css`：
  - PR1 只把 `--bg`/`--fg`/`--accent` 三个同名变量机械改名为 `--legacy-*`，**定义与全部 `var()` 引用一起改**（E11：只改定义不改引用，旧界面会直接吃到 token 的值）；
  - PR2 替换 Spec 8 时代的规则。
- `main.tsx`：按 `tokens.css → ui.css → style.css` 的顺序 import；PR3 起在 `createRoot` 之前调用 `applyTheme(readTheme())`。

### 4.2 `renderer/`（组件）

| 文件 | 导出 | 约束 |
|---|---|---|
| `icons.tsx`（PR1 新增） | `type IconName`、`Icon({ name, size? })` | 只 import `react`；无事件属性；`aria-hidden` |
| `ui.tsx`（PR1 新增） | `Badge({ tone?, children })`、`StatusDot({ tone })`、`StateView({ kind, title, detail? })` | 只 import `react` 与 `./icons`；props 类型里没有 `on*`（VS-10） |
| `theme.ts`（PR3 新增） | §3.3 | 全仓唯一碰 localStorage 的模块（VS-9） |
| `App.tsx`（PR2、PR3 改） | PR2：换类名，行改按钮，`StatusCard` / `ArtifactTree` / `Timeline` / `HealthPanel` / `GalleryList` 换装。PR3：`EpisodeList` 加侧栏头部与新行结构；`TopBar` 加外观浮层（popover API） | §2.8 边界 |
| `DecisionBar.tsx`（PR2 改；若 Spec 10 PR1 已搬到 `HumanCards.tsx`，则改那边） | 只换类名：批准 / 打回 / 提交打回为同权的 `ui-btn`；只读态为 `decision-readonly` | `approval.decide` 调用点与闸门逻辑一字不动（TG-4 照旧绿） |
| `PreviewPane.tsx`（PR2 改） | 预览文件头（D6）；`StateView` 替换空 / 错 / 载；JSON 折叠三角改 `<button class="j-toggle">`；音频队列项改 `<li><button class="ui-row">` | 媒体与读闸逻辑不动 |

### 4.3 `shared/`

`relTime.ts`、`episodeView.ts`（PR3），零 import（TG-3 已守 shared 纪律）。

### 4.4 `host/`、`main/`、`preload/`

**零改动**（🔴-1 方案 a）。

---

## 5. 依赖白名单与纯洁性保障

- **npm**：零新增。`package.json` 不改，TG-1（`guards.test.ts:25-43`）原样通过。
- **Python**：零改动。`pipeline/`、`config/`、`tests/`、`pyproject.toml` 不在任何 PR 的 diff 里（门禁 5）。
- **纯洁性**（红线 7 的对应物）：
  - 本 spec 没有新 Python 模块，也不碰 Python 热路径，既有子进程纯洁性断言原样生效。
  - 新 TS 模块：`shared/*.ts` 零 import（TG-3）；`icons.tsx` 只 import `react`、`ui.tsx` 只 import `react` 与 `./icons`（VS-10）。
- **运行时样式注入**：禁止。CSP 本身会拦，VS-3 也扫 `document.createElement("style")` 与 `setAttribute("style"`。

---

## 6. 跨 spec 接口与系统边界

### 6.1 修订请求（编号明确；S8-R22、DIR-R3、DOC-R1、S8-R23 已于 2026-09-26 获人授权；本 spec 红队三轮 🟢（2026-09-26），动工前置清零）

| 编号 | 对象 | 内容 | 性质 |
|---|---|---|---|
| ~~S8-R20~~ / ~~S8-R21~~ | ~~Spec 8 设置文件 / 协议闭集~~ | **v0.2 撤回**（🔴-1 方案 a） | — |
| **S8-R22** | Spec 8 §2.11 v1 界面、`renderer/` | 列表行与折叠控件改原生按钮（期、树、画廊、JSON 折叠、音频队列）；样式拆为三份；新增静态守卫 VS-1~VS-13 与 e2e VE-0~VE-4 | **已授权（2026-09-26）**；阻塞本 spec PR1–PR3 |
| **S10-R2** | Spec 10 §2.3、§2.4、§4.3 | 其新组件套用 §3.1/§3.2 契约；答复类按钮同权；期行徽标并列不遮挡；时间线「横幅不变」按「文字与出现条件不变」解释（D7） | **建议性，不阻塞** Spec 10 |
| **S11-R1** | Spec 11 §4.4 | 套用契约；CodeMirror 主题与 `HighlightStyle` 只引用 CSS 变量；闸门类按钮同权；附 E3 证据 | 建议性，不阻塞 |
| **S12-R1** | Spec 12 §4.2 | 封面选择器与拖放区用 §3.2 的类；图片上的文字只用 `--overlay-*`；「批准并记录定稿」同权 | 建议性，不阻塞 |
| **DIR-R3** | direction §6 | 补登 Spec 14 条目 | **已授权（2026-09-26）**；PR0 落盘 |
| **DOC-R1** | `docs/dev/plans/README.md` 结构约定表 | 增一行：「附件：放在与方案同名的目录 `<方案文件名去掉 .md>/` 下，只许纯文本，随方案一起归档；附件内脚本的路径一律取参数，不写死」 | **已授权（2026-09-26）**；PR0 落盘 |

**S8-R23（已授权 2026-09-26；独立的 Spec 8 修订，不属本 spec 的范围闸门，也不进 §2.8 的新增字符串清单）**：补上 Spec 8 §2.8「脱卸期间……置灰标『陈旧』」中缺失的「陈旧」标记（2026-09-26 人裁决「补」）。现状界面上没有该字样（`DecisionBar.tsx:19` 注释里的「陈旧」不是 UI 文案，红队三轮 🔵-1），只有 `.main.stale` 置灰（`App.tsx:179`、`style.css:17`）。

| 项 | 内容 |
|---|---|
| 字样 | 「陈旧」（Spec 8 §2.8 原文所定，两个字，不加修饰） |
| 位置 | 期列表头部与中栏顶部各一处，用中性徽标（`ui-badge`）；**与数据不可达横幅（`data-testid="reach-banner"`）分开**：横幅说原因，「陈旧」标出哪些区域是旧数据。侧栏按人裁决不置灰（B4），这个标记正好告诉人侧栏是旧数据 |
| 出现条件 | 与 `.main.stale` 相同（`health.reach !== "ok"`），不引入新判定 |
| 测试 | e2e 断言脱盘时两处都出现、恢复后都消失，复用 `preview.spec.ts` 的脱盘夹具；新增 `data-testid="stale-mark"` |
| 对比度 | 标在置灰的中栏里时，守非活动界面 3:1（VE-1 stale 态）；在侧栏里（不置灰）守 4.5 |
| 施工 | 可与本 spec PR2 同批施工，但单独提交、单独列入施工报告；门禁 5 的字符串对拍把「陈旧」单列为 S8-R23 的新增，不算违规 |

**另报人**（不属本 spec 的修订请求）：core 提供「已发布」事实后，再做「已完成折叠」。

**已确认（2026-09-26）**：§2.8 的新增字符串清单 N1–N11（红队二轮 🟡-4 补全；红队三轮对拍 mock 与现状源码核实齐全后交人确认）。

### 6.2 与 Spec 10 / 11 / 12 的边界

- 三者都是**消费方**：照 §3.1/§3.2 写样式，不自建颜色与字号；它们自己的文案与交互由各自的 spec 决定。
- **顺序**（Y9）：本 spec PR1 → Spec 10 PR1 → 本 spec PR2 → 本 spec PR3。
  - PR1 只加文件、不碰旧界面，Spec 10 PR1 可以直接在契约上搭新版面。
  - 旧中栏会在 Spec 10 PR1 里被拆掉，所以换皮（PR2）放在它之后，只给剩下的旧界面换，避免做两遍。
  - PR3 改 `EpisodeList` 与 `TopBar`，Spec 10 PR1 也改左栏：后合的一方负责变基，冲突面限于 `App.tsx` 的这两个函数。
- Spec 11 的 A1（CodeMirror 能否在 Electron 44 下装载）仍归 Spec 11；本 spec 只提供 E3。

### 6.3 八条施工红线对照

| 红线 | 本 spec |
|---|---|
| 1 不引数据库 | ✓ 偏好存 localStorage（Spec 8 §2.9 已授权），不新增文件存储 |
| 2 不引框架 / web server | ✓ 零新依赖 |
| 3 产物即状态 | ✓ 期行只呈现 `current_step` 原文与 `is_blocked` 的直译；不推导「待审」「进行中」「已完成」（🔴-2 修订） |
| 4 停机点不减、ack 显式 | ✓ 决策条逻辑一字不动（TG-4 照旧）；闸门按钮同权，不做引导（VS-12） |
| 5 人审闸门不自动化 | ✓ 不碰任何闸门 |
| 6 工具表封顶 | ✓ 不涉及 |
| 7 新模块零重依赖 | ✓ §5 |
| 8 期望值先跑 + 变异 | ✓ §7 的期望值全部来自 `tools/` 实跑；§7.2 中 26 条变异已实跑并写明失败数 |

### 6.4 为什么不立 ADR

本 spec 可以整体回退（删三个样式文件与 `theme.ts`、还原类名），不改变进程模型、读写边界、协议、工具表或依赖集合；「不引 UI 框架」已由 Spec 8 §5.1 与 TG-1 立法。

---

## 7. 测试规格与变异检验矩阵

判据本体在仓库里：`tools/tk.mjs`（VS-1/VS-2）、`tools/vs.mjs`（VS-3/4/5/6/11，含检查器自测）、`tools/vs12.mjs`（VS-12，含自测）、`tools/audit.js`（VE-1）。**扫描范围随 PR 推进**：PR1 时 VS-3/4/5/6 只扫 `ui.css`（旧 `style.css` 尚未替换，满是字面量）；PR2 起加入新的 `style.css`。施工时移植为 `desktop/tests/static/visual.test.ts` 与 `desktop/e2e/visual.spec.ts`，**移植后第一步用附件的同一输入对拍输出**：失败数与各类最小值都要一致。

### 7.1 测试规格

**静态（vitest；每个检查器先用合成片段做正反例自测）**

| 编号 | 用例 | 关键断言（期望值来自实跑） |
|---|---|---|
| VS-1 | token 格式、覆盖与两块深色一致 | 颜色 token 值全部匹配三种允许格式；浅色 40 个颜色 token 在手动深色块全部覆盖；跟随系统块与手动深色块的键集合与取值逐项相等；`var()` 别名可解析且无环 |
| VS-2 | 对比度对子表 | **先断言 `out.length === 290`**（红队二轮 B2：格式错或两块深色不一致时检查器提前返回空表，只断言失败数会空过；`tk.mjs` 命令行同样以退出码 1 兜底），再断言 290 对 × 阈值全部达标。各类最小值写进断言注释，浅 / 深：正文 5.49 / 5.96；选中行文字 5.34 / 5.56；悬停行文字 4.95 / 5.93；中性徽标（含「停机」）4.66 / 5.08；状态徽标（含悬停 / 选中叠法）4.58 / 4.58；焦点环 5.16 / 6.23；主按钮边界 3.70 / 8.76；输入框边界 3.40 / 3.17；状态点与圆环 4.95 / 5.56；浮层（白画面）9.23 / 9.23；高亮 5.87 / 6.46 |
| VS-3 | 颜色字面量只在 `tokens.css` | 只扫**声明值**：`#hex`、`rgb()/rgba()/hsl()/color-mix()/oklch()/oklab()`、命名色表（white/black/red/green/blue/gray/grey/yellow/orange/purple/pink/silver/navy）零命中；`transparent`/`currentColor`/`inherit` 放行；选择器与属性名不扫。**`tokens.css` 以外禁止定义任何 `--*` 自定义属性**（红队二轮 B1 ①）。TSX 另扫 `style={{}}` 值与 `createElement("style")`、`setAttribute("style"` |
| VS-4 | 字号纪律 | `font-size` 只许 `var(--text-*)` 或 `inherit`；`font` 简写不带 px；`var(--text-xs)` 只在选择器含 `ui-badge`/`ui-count`/`ui-kbd` 的规则里；token 本身满足 `--text-xs ≥ 10px`、`--text-sm ≥ 12px` |
| VS-5 | 焦点 | 恰一条 `:focus-visible`，值为 `outline: 2px solid var(--focus-ring)` 与 `outline-offset: 2px`；**任何 `outline*` 属性（含 `outline-color`、`outline-offset`）只许出现在这条全局规则里**，唯一例外是 `outline: none` 出现在 `:focus:not(:focus-visible)` 与 `.composer textarea:focus-visible`（红队二轮 B1 ②） |
| VS-6 | 减弱动效 | 存在 `prefers-reduced-motion: reduce` 块，`transition-duration` 与 `animation-duration` 都为 `0s` |
| VS-7 | TSX 内联样式白名单 | `style={{…}}` 的键只许 `paddingLeft`（树缩进）与 `transform`（图片平移） |
| VS-8 | 可点击元素是原生交互元素 | `onClick` 只挂在 `button`/`input`/`textarea`/`select`/`summary`/`a` 上；唯一豁免是 `PreviewPane.tsx` 的 `.markdown` 容器（它拦截链接导航，不是交互目标），按文件 + 类名精确列出 |
| VS-9 | localStorage 纪律 | `localStorage` 只出现在 `renderer/theme.ts`，且每处成员访问都位于某个 `try` 块内 |
| VS-10 | 纯展示组件无事件 | `icons.tsx` 只 import `react`，`ui.tsx` 只 import `react` 与 `./icons`（白名单两个模块，2026-09-26 验收裁决：§2.5 的路径数据唯一落点优先）；导出组件的 props 类型没有 `on*` 键 |
| VS-11 | `ui.css` 作用域 | 除三条全局规则外，每个选择器都以 `.ui-` 或 Spec 10–12 预留前缀开头 |
| VS-12 | 闸门按钮同权（v0.3 按红队二轮 🟡-3 重写，原型 `tools/vs12.mjs`） | 闸门卡片文件（`DecisionBar.tsx`，Spec 10 后为 `HumanCards.tsx`）里：① 每个 `<button>` 的 className 是含 `ui-btn` 的字符串字面量（动态写法一律违规）；② 不许 `ui-btn--primary` / `ui-btn--ghost`；③ 同一动作区（className 含 `ui-card-actions` 或 `decision-actions`）内各按钮的变体类集合（尺寸类除外）完全相同。期望值：现状 `DecisionBar.tsx` 报 3 处（按钮无 className，PR2 加上后应为 0）。闸门卡片文件名单为硬编码，文件增减时须同步扩名单（红队三轮 🔵-3） |
| VS-13 | 无效 ARIA | `button` 元素上不出现 `aria-selected` |

**单元（vitest）**

| 编号 | 用例 | 关键断言 |
|---|---|---|
| VU-1 | `formatRelTime`（固定 `tz = "Asia/Shanghai"`，B2） | 59.9 s →「刚刚」；60 s →「1 分钟」；59 min / 60 min；23 h / 24 h；29 天 / 30 天 →「M月D日」；跨年 →「YYYY年M月D日」；未来 5 min →「刚刚」 |
| VU-2 | `filterEpisodes` / `groupByStep` | 空查询原样返回；大小写不敏感；保序；三组组序与组名固定；空组省略；`null` 进「未取到」、`false` 进「非停机点」 |
| VU-3 | `theme.ts` | localStorage 抛错时 `readTheme()` 为 `"system"` 且不抛；非法值为 `"system"`；`saveTheme` 抛错时返回 false；`applyTheme("system")` 删属性，`"dark"` 写属性 |

**e2e（Playwright，临时 repo 副本，严禁指向真实 `data/`）**

| 编号 | 用例 | 关键断言 |
|---|---|---|
| VE-0 | PR1 零视觉变化 | PR1 前后各对夹具 app 的每个元素抓一次计算样式快照（非聚焦态，排除 `outline*` 与 `color-scheme`），**在 `emulateMedia` 浅、深两态下各做一次**，两次逐项相等（Y4；红队二轮 🟡-2：同名变量只在深色下冲突，E11）。排除 `color-scheme` 的理由：它的计算值会从 `light dark` 变成 `dark`，但深色下两者对原生控件的实际渲染相同 |
| VE-1 | 真实 DOM 审计（`audit.js` 同一份） | 夹具覆盖：期列表（含停机行、未取到行、`page.hover()` 下的停机行，以及选中的未取到行）、05 决策卡（含打回表单与失败回执）、markdown / JSON / 空 / 错四种预览、健康面板展开、外观浮层展开、stale 态（夹具数据盘脱卸）。每个状态 × 三种主题态：light / dark 用 `page.emulateMedia({ colorScheme })`，forced-dark 用 `page.evaluate` 写 `data-theme`。断言对比度失败 = 0、非文本失败 = 0、字号越界 = 0、**`bgImage` = 0、`skippedMedia` 等于该状态的预期**（红队二轮 🟡-1；mock 实测全为 0），且 `n` 不低于该状态的下限（下限在 PR2 首次实跑后填入，参考 mock：82 / 63 / 33 / 70 / 72 / 54） |
| VE-2 | 主题三档 | ① 点「深色」→ `data-theme="dark"`，body 背景计算值等于深色 `--bg`（在模拟 light 下也成立）；② 重启 app（同 userData）后仍为深色；③ 点「跟随系统」并 `emulateMedia({ colorScheme: null })` → `matchMedia("(prefers-color-scheme: dark)").matches === electronApp.evaluate(nativeTheme.shouldUseDarkColors)`；④ `settings.json` 与操作前逐字节相同 |
| VE-3 | 键盘 | 从 body 起循环按 Tab，最多 30 次，直到焦点落在 `[data-testid=episode]`（超限即红）；此时计算样式 `outlineWidth === "2px"`、`outlineOffset === "2px"`、`outlineColor` 等于 `--focus-ring` 的计算值（红队二轮 B1 ③）；按 Enter 后打开该期（`data-ep` 变化）。外观浮层：Tab 到按钮后按 Space 打开，Esc 关闭，焦点回到按钮（E8） |
| VE-4 | 既有 e2e 零回归 | `preview.spec.ts`、`ack.spec.ts`、`preview-security.spec.ts` 零改动全绿（A4、B3） |

### 7.2 变异检验矩阵

v0.3 起共 26 条已实跑（v0.2 的 21 条 + M37–M41）。「撰写时实跑」一列写实跑得到的失败数，这就是施工时的期望失败数；标「施工回填」的写的是预测机理。「不被掩盖」一列说明为什么没有别的断言会先替它变红。

| 编号 | 变异 | 目标用例 | 为什么红 | 不被掩盖 | 撰写时实跑 |
|---|---|---|---|---|---|
| M1 | 浅色 `--fg-muted` → `#8e8e97` | VS-2 | 多处正文与徽标对子 < 4.5 | VS-1 只查格式与覆盖 | ✓ 25 对 |
| M2 | 手动深色块删 `--warn` | VS-1 | 覆盖缺一项，两块不一致 | 两个子断言同时红，互为冗余 | ✓ missing=[--warn]、blockDiff=[--warn] |
| M3 | `--bg-selected` 改为 `color-mix(...)` | VS-1 | 格式非法 | VS-1 在解析颜色之前先判格式 | ✓ badFormat=1 |
| M4 | 浅色 `--fg-on-accent` → 白 | VS-2 | 白字配浅粉 | 仅 VS-2 | ✓ 2 对 |
| M5 | 深色 `--border-input` 改为 ZCode 式 10% 透明（两块同改） | VS-2 | 半透明前景合成后 < 3 | 两块同改，VS-1 不红；检查器合成半透明前景，不会因「底必须不透明」抛错 | ✓ 5 对 |
| M6 | 浅色 `--focus-ring` → `var(--accent)` | VS-2 | 浅粉环对各面 < 3 | 仅 VS-2 | ✓ 6 对 |
| M23 | 只改跟随系统深色块的 `--bg-panel` | VS-1 | 两块不一致 | 手动块覆盖完整，missing 为空 | ✓ blockDiff=[--bg-panel] |
| M25 | 浅色 `--wait` 回退 v0.1 值 | VS-2 | 悬停 / 选中叠法 < 4.5（红队 Y2 的 4.22） | 仅 VS-2；v0.1 的 148 对不含这类叠法，这正是 Y2 所指 | ✓ 3 对 |
| M26 | 浅色 `--border-input` 回退 v0.1 值 | VS-2 | 侧栏搜索框 2.86 | 同上 | ✓ 1 对 |
| M10 | `style.css` 加 `color: #333`（PR1 落点改为 `ui.css`：§7.1 的 PR1 扫描集只含 `ui.css`，style.css 要到 PR2 换掉后才进扫描集；PR1 验收实测 style.css 植入 22/22 绿，证实该落点在 PR1 不可见） | VS-3 | 声明值字面量 | VS-1/VS-2 不扫 style.css | ✓ vs3=1 |
| M11 | 期行用 `var(--text-xs)` | VS-4、VE-1 | 选择器不含徽标类；渲染后 10px | Y1 修掉后 VS-4 基线为 0，不再被既有红掩盖 | ✓ vs4=1 |
| M22 | 恢复 v0.1 的 `.dev-mark { font-size: var(--text-xs) }` | VS-4 | 同上（红队 Y1 原形） | 同上 | ✓ vs4=1 |
| M12 | 删 `outline-offset` | VS-5 | 缺 offset | VE-3 也断言 `outlineOffset`，两层冗余；VS-5 基线为 0 | ✓ vs5=1 |
| M27 | 加 `.x:focus-visible { outline: none }` | VS-5 | 不在豁免清单 | 同上 | ✓ vs5=1 |
| M20 | 删减弱动效块 | VS-6 | 缺归零块 | 仅 VS-6 | ✓ vs6=1 |
| M21 | `ui.css` 写 `.decision {…}` | VS-11 | 越出作用域 | 仅 VS-11；VE-0 也可能红（若该规则改变了旧界面），属冗余 | ✓ vs11=1 |
| M7 | `.conv-foot` 改用 `--fg-subtle`（v0.1 的真失误） | VE-1 | 渲染后 < 4.5 | VS-2 看不出：token 本身合格 | ✓ mock-02 三态各 1，合计 3 |
| M29 | stale 透明度回到 0.6（红队 Y2） | VE-1 stale 态 | 合成 opacity 后浅色 < 3 | v0.1 的审计不读 opacity，永远绿 | ✓ mock-06 浅色 7、深色 0 |
| M30 | `.ui-kbd` 回到 `--fg-muted`（E9） | VE-1 | 主按钮里的键帽 4.14 / 1.36 | VS-2 看不出 | ✓ mock-04 三态各 1，合计 3 |
| M18 | 审计的文本遍历失效（`SHOW_TEXT` → `SHOW_COMMENT`） | VE-1 的下限断言 | `n` 从 82 降到 2 | 失败数仍为 0：没有下限断言时审计永远绿 | ✓ n=2、TOTAL_FAIL=0 |
| M19 | VE-2 ③ 去掉 `emulateMedia({ colorScheme: null })` | VE-2 ③ | 系统为深色时 `matchMedia` 仍为 false，与 `shouldUseDarkColors` 不等（E4） | 反向变异：证明解除模拟这一步不可省 | ✓ E4 实测该现象 |
| M31 | PR1 漏改 `style.css` 的 `--bg` 同名变量（或只改定义、不改 `var()` 引用） | VE-0 深色态 | token 深色块（特异性 0,2,0）覆盖旧 `--bg`，旧界面深色底色从 `#1c1c1e` 变为 `#161618`（E11 实测） | 浅色态不红（旧 style.css 后加载、同特异性胜出），所以 VE-0 必须跑深色态 | 施工回填（E11 已证机理） |
| M37 | 给 `.left, .center, .preview, .topbar, .timeline` 加一层透明渐变 `background-image`（红队二轮 🟡-1） | VE-1 的 `bgImage = 0` 断言 | 非白名单祖先带背景图，一律记失败 | v0.2 的审计会整段跳过、`n` 只少 1，下限断言照样绿 | ✓ mock-01 浅色 `bgImageFail=61`；叠加 M1 时另有对比度失败 12（其余被背景图遮住的 26 处改记为 bgImage） |
| M38 | 「打回…」改为 `ui-btn ui-btn--ghost`（红队二轮 🟡-3） | VS-12 | 带 ghost + 动作区变体不一致 | v0.2 的 VS-12 只查调用 `approval.decide` 的按钮，「打回…」不在其内 | ✓ vs12.mjs 自测：2 处 |
| M39 | `style.css` 写 `.ep { --fg-muted: #999999; }`（红队二轮 B1 ①；PR1 落点同 M10 改为 `ui.css` 等价植入） | VS-3 | tokens.css 以外定义自定义属性 | v0.2 的 vs3 跳过所有 `--*` 声明 | ✓ vs3=1 |
| M40 | `ui.css` 写 `.ui-btn--primary:focus-visible { outline-color: var(--accent); }`（红队二轮 B1 ②） | VS-5 | `outline*` 出现在全局规则之外 | v0.2 的 vs5 只查 `outline: none` 或 `outline: 0` | ✓ vs5=1 |
| M41 | 手动深色块删 `--warn`（同 M2），看 VS-2 是否空跑（红队二轮 B2） | VS-2 的 `out.length === 290` | 检查器提前返回空表，对子数为 0 | 只断言失败数时为 0 失败、空过 | ✓ `tk.mjs` 退出码 1，「EXIT 1：期望 290 对全绿」 |
| M8 | 对子表删去「状态徽标压选中 / 悬停行」一类 | VE-1 | 夹具里的悬停停机行 / 待答徽标 | 对子表失去该类后 VS-2 全绿 | 施工回填 |
| M13 | 期行改回 `<div onClick>` | VS-8、VE-3 | onClick 挂在 div；Tab 到不了 | VE-4 用鼠标点击，不会先红 | 施工回填 |
| M14 | `formatRelTime` 的 `< 60 s` 写成 `<= 60 s` | VU-1 | 60 s 边界输出「刚刚」 | 仅 VU-1 | 施工回填 |
| M15 | `groupByStep` 把 `null` 并入 `false` 组 | VU-2 | 「未取到」行出现在「非停机点」 | 仅 VU-2 | 施工回填 |
| M32 | `readTheme` 去掉 try/catch | VS-9、VU-3 | 静态：成员访问不在 try 内；单元：localStorage 抛错时向上抛 | 两层冗余 | 施工回填 |
| M33 | `style={{ color: … }}` | VS-7、VS-3 | 键不在白名单；值是字面量 | 两层冗余 | 施工回填 |
| M34 | 批准按钮加 `ui-btn--primary` | VS-12 | 闸门按钮带主按钮类 | 仅 VS-12（VE-1 对比度照样过） | 施工回填 |
| M35 | 期行用 `aria-selected` | VS-13 | button 上的无效 ARIA | 仅 VS-13（视觉上此时选中态也会消失，但没有 e2e 看颜色） | 施工回填 |
| M36 | `ui.tsx` 的 `Badge` 接收 `onClick` | VS-10 | props 带 `on*` | 仅 VS-10 | 施工回填 |

### 7.3 PR2 施工实跑回填（2026-09-26）

**归属变异（§7.2 的 11 条，逐条「植入 → 目标用例红 → 逐字节还原」，工作树已复原）**

| 变异 | 落点（真实 app） | 目标用例 | 实跑红条数 |
|---|---|---|---|
| M7 | `style.css` `.muted` 用 `--fg-subtle`（`.conv-foot` 的宿主在 Spec 10 PR3 才落地，按同机理等价植入） | VE-1 | 1（浅色，`--fg-subtle` 不当承载必读文字） |
| M8 | `style.css` 加 `.ui-badge.stop-mark { color: var(--fg-subtle) }`（对子表不含「弱提示色压徽标」这一类的形状；原式的「删一类」会先把 `pairs === 290` 断言打红，失去「VS-2 全绿而 VE-1 红」的对照意义） | VE-1 | 1 |
| M13 | 期行改回 `<div onClick>` | VS-8 | 1 |
| M18 | 审计遍历 `SHOW_TEXT` → `SHOW_COMMENT` | VE-1 的 `n` 下限 | 1（`n` 从 51 塌到个位数） |
| M29 | `.main.stale` 透明度 0.75 → 0.6 | VE-1 stale 态 | 1 |
| M30 | `.ui-kbd` 回 `--fg-muted`（原式实跑：临时把它放进主按钮的键帽宿主，两文件植入，含 `color: inherit` 还原） | VE-1 | 1（浅色 4.14，与 E9 同值） |
| M33 | `style={{ color: "#333" }}` | VS-7 + VS-3(TSX) | 2 |
| M34 | 批准按钮加 `ui-btn--primary` | VS-12 | 1 |
| M35 | 期行 `aria-current` → `aria-selected` | VS-13 | 1 |
| M37 | 面板加透明渐变 `background-image` | VE-1 的 `bgImage = 0` | 1 |
| M38 | 「打回…」改 `ui-btn--ghost` | VS-12 | 1 |

**VE-1 首次实跑的元素数 `n`（三态一致；已按此填入 `N_LOWER`）**：list 51、markdown 56、json 63、empty 39、error 52、health 70、stale 48、card 52、card-reject 57、card-err 60（门禁 5 回改 4 处 `StateView` 的 `detail` 后重测）；对比度/非文本/字号/`bgImage`/`skippedMedia` 全为 0。

**验收结论（2026-09-26）**：独立评审逐条复核后**关闭全部四条发现，M6 验收通过**；Spec 14 门禁 3 / 4 / 5 判为 ✅（VE-3 的外观浮层部分随 PR3，已登记）。

**验收回改（2026-09-26，独立评审门禁 5 发现 🔴 后）**：门禁 5 的字符串差集曾多出 6 项——4 个清单外新增可见串（中栏空态 detail、预览空态 detail、`产物树读取失败`、`媒体打不开`）与 2 处既有文案的格式变化（`工序读取失败（code）：msg` 被拆成 title+detail、`读取失败：err` 同理）。处置：**全部改回原文**（`StateView` 的 `title` 直接承载 HEAD 的原句、去掉 detail），零新增串、零信息丢失，未动 N1–N11。回改后 VE-1 的元素数重测并重新填入 `N_LOWER`。

**门禁 5 的取证方式（如实声明）**：源文本级「可见字符串集合差集」在本仓库不可靠——正则抽文本会在两侧都产生假阳/假阴（JSX 子节点跨行、`className="…">文案<` 的行首、模板串与 JSX 子节点等价但文本不同形）。因此本 spec 的门禁 5 取证改为两条可复核证据：① 6 处逐条列出 HEAD 源码行与工作树源码行的可见文本对照；② 新增串清单（21 条）人工分类为「N1/N2」与「Spec 10 PR1/Spec 12 S8-R19 的新功能串」两类。真正的机器判据应是**真实 app 的渲染文本**，需要一份 HEAD 构建做对照——本次未做（`git checkout` 是事故形状，禁用），登记为后续可选项。

**施工偏差（如实登记）**
1. VE-3 只落「Tab 到 `[data-testid=episode]`、焦点环为 `--focus-ring`、Enter 打开该期」；**外观浮层（Space 打开 / Esc 关闭 / 焦点回到按钮）随 PR3 落地**——浮层本身属 §8 PR3 的「TopBar 加外观浮层」。
2. VE-1 的夹具状态与 §7.1 清单对齐，但「选中的未取到行」由 stale 态覆盖（脱盘时 status 取不到 → `currentStep: null` 与未知圆环同时出现）；外观浮层展开态随 PR3。
3. 已按 §8 PR2 清单整体替换 `style.css`，并保留 Spec 10 的区块（`.tree` `.galleries` `.new-episode*` `.strip` `.finalize`）；`tokens.css`、`ui.css` 一字未动（sha256 断言仍绿）。
4. S8-R23（「陈旧」标记）**未施工**：不属本次施工单，仍是已授权待办。


---

## 8. PR 划分

顺序：**PR0 → PR1 → Spec 10 PR1 → PR2 → PR3**（Y9），全程串行：PR2 与 PR3 都改 `App.tsx`，PR3 以 PR2 为前置（红队二轮 B3）。

| PR | 内容 | 测试 | 前置 |
|---|---|---|---|
| **PR0**（文档） | DIR-R3、DOC-R1 落盘；**人看 mock 拍板**（可改 token 取值与 D1–D7，改后重跑 `tk.mjs` 与 `render.cjs`）；提交前重核 `tokens.css`/`ui.css` 的 sha256 与头部「拍板基准」对拍（红队三轮 🔵-2） | `uv run pytest tests/test_docs_invariants.py` | §6.1 授权 + 红队 🟢 |
| **PR1**（契约，零视觉变化） | `renderer/tokens.css`、`renderer/ui.css`、`icons.tsx`、`ui.tsx`；旧 `style.css` 的 3 个同名变量改为 `--legacy-*`；`main.tsx` 的 import 顺序；判据移植（VS-1~6、VS-10、VS-11，并与附件对拍）；`desktop/scripts/render-mocks.cjs` | VS-1~6（扫 `ui.css`）、VS-10、VS-11、VE-0（浅、深两态）、VE-4；M1–M6、M10–M12、M20–M23、M25–M27、M31、M36、M39–M41 | PR0 |
| **PR2**（旧界面换皮，Spec 10 PR1 之后） | S8-R22：`style.css` 替换 Spec 8 时代的规则（清单：`.ep*` `.stop-mark` `.status*` `.cmd` `.advisories` `.tree*` `.section-title` `.decision*` `.fingerprints` `.reject-form` `.banner*` `.notice` `.warn` `.empty` `.muted` `.error` `.html-frame` `.video-wrap` `.timecode` `.audio-wrap` `.queue*` `.image-wrap` `.text-wrap` `.plain` `.markdown` `.json` `.j-*` `.timeline` `.jobs` `.events` `.job*` `.stderr` `.tail` `.health` `.diag` `.meta*` `.fatal` `.topbar*` `.repo` `.dev-mark` `.main` `.left` `.center` `.preview` `.app` `.spacer` 与基座规则）；Spec 10 已加的区块保留；组件改类名与元素种类；预览文件头 | VS-3~6 扩到新 `style.css`、VS-7、VS-8、VS-12、VS-13、VE-1、VE-3、VE-4；M7、M8、M13、M18、M29、M30、M33–M35、M37、M38 | PR1、Spec 10 PR1 |
| **PR3**（侧栏与主题） | `shared/relTime.ts`、`shared/episodeView.ts`、`renderer/theme.ts`；侧栏头部与期行；外观浮层 | VS-9、VU-1~3、VE-2、VE-1 增侧栏与浮层状态；M14、M15、M19、M32；A2、A7 实测回填 | PR2 |

每个 PR 结束时跑：`cd desktop && npx vitest run && npx tsc --noEmit -p tsconfig.json && npx playwright test`，再跑截图命令，把 PNG 交人看。

---

## 9. 验收门禁清单

- [x] **门禁 0（前置）**：§6.1 修订请求获人授权（S8-R22 / DIR-R3 / DOC-R1 / S8-R23 ✓ 2026-09-26）；本 spec 红队 🟢（三轮，2026-09-26）；人看过 6 张 mock 并拍板（✓ 2026-09-26，含 D1–D7）；§2.8 新增字符串清单 N1–N11 获人确认（✓ 2026-09-26）
- [ ] **门禁 1（契约）**：VS-1~VS-13 全绿；`renderer/tokens.css`、`renderer/ui.css` 的 sha256 与头部「拍板基准」一致；判据移植后与附件对拍一致（290 对、各类最小值）
- [ ] **门禁 2（零视觉变化）**：PR1 的 VE-0 绿
- [ ] **门禁 3（可访问性）**：VE-1 在全部夹具状态 × 三种主题态下对比度失败 0、非文本失败 0、字号越界 0，且 `n` 不低于下限；VE-3 绿
- [ ] **门禁 4（零回归）**：VE-4；TG-1~TG-9 全绿；`npx vitest run` 全绿
- [ ] **门禁 5（边界）**：`git diff` 中 renderer 文件的既有可见文案、`data-testid`、rpc 方法名零变化（施工报告附对拍：改动前后各抽一次可见字符串，求差集，差集必须恰好落在 §2.8 清单 N1–N11 内；若 S8-R23 同批施工，「陈旧」单列，不算违规）；`host/`、`main/`、`preload/`、`shared/protocol.ts`、`shared/lifecycle.ts` 零 diff；`pipeline/`、`config/`、`tests/`、`pyproject.toml` 零 diff
- [ ] **门禁 6（主题）**：VE-2 绿；打包版手验一次三档切换与重启保持（A2 首帧、A7 回填）
- [ ] **门禁 7（人看终版）**：PR2、PR3 完成后对真实 app 按 §2.7 截图（三态），人确认与 mock 一致或更好。这是验收里唯一的审美判定，由人做
- [ ] **门禁 8（变异）**：§7.2 逐条实跑，已实跑的 26 条失败数与表内一致，其余回填
- [ ] **门禁 9（文档）**：`uv run pytest tests/test_docs_invariants.py` 全绿；`docs/dev/plans/README.md` 状态行更新

---

## 10. 潜在红旗与自纠预案

| 编号 | 红旗 | 预案 |
|---|---|---|
| RF-1 | 人看了 mock 还是不喜欢 | 只改 token 取值与 CSS，重跑 `tk.mjs` 与 `render.cjs`；判据不动 |
| RF-2 | 浅粉主按钮在白底上轮廓弱（填充 1.51:1） | 已加 `--accent-border`（对 4 种面最小 3.70）；若人仍觉弱就加深边框。改成深粉底白字会背离「浅粉」裁决，须先问人 |
| RF-3 | DOM 审计看不到压在媒体上的文字 | 规则：媒体上的文字只用 `--overlay-*`（白画面 9.23、黑画面 21）；靠代码评审兜底（mock-04 的封面尺寸徽标即此类，v0.1 用人眼发现） |
| RF-4 | 中文字形与 WCAG 阈值的口径之争 | 按 WCAG 2.x 原定义，不自创修正系数；「达标但难读」属审美，走 RF-1 |
| RF-5 | 与 Spec 10 PR1 的合并冲突 | 按 §8 的顺序；PR3 与 Spec 10 的左栏改动由后合的一方负责变基 |
| RF-6 | 相对时间被误读为「最近活动」 | §3.4 的语义声明 + `title`；真要「最近活动」须 host 递归取 mtime，那是协议改动，另问人 |
| RF-7 | 系统字体在不同 macOS 版本下字宽不同 | 截断一律用 ellipsis + `title` 全文 |
| RF-8 | 「按工序」分组被当成 UI 判定 | 组名是对 `is_blocked` 布尔值的直译（停机点 / 非停机点 / 未取到），不解析字符串、不推断进度 |
| RF-9 | 行改按钮后的读屏体验 | 行内只放 `span`；读屏体验未测，如实声明 |
| RF-10 | 「已完成折叠」长期缺席，列表变长 | 搜索与分组先缓解；core 的「已发布」事实立项后再做 |
| RF-11 | 休眠后相对时间停在旧值 | 60 s 定时器 + `visibilitychange` 时立刻重算 |
| RF-12 | 开发版 origin 端口变化导致主题偏好丢失 | 只影响开发；打包版为 `file://`，origin 固定（A7 实测） |
| RF-13 | 原生对话框不跟随 app 内的手动主题 | 🔴-1 方案 a 的已知代价，已写进 §2.3；需要跟随时走独立的 Spec 8 修订（方案 b） |

---

## 附：实验记录（2026-09-26，均在 scratchpad 或附件 `tools/` 下，未改 `desktop/`、`pipeline/`）

| 编号 | 实验 | 结果 |
|---|---|---|
| E1 | Electron 44.4.5（Chromium 152）载入带生产 CSP 的页面，测：标记里的 `style=""`、`setAttribute("style")`、插入 `<style>`、CSSOM 写入、`color-mix`、`:focus-visible`、`nativeTheme.themeSource` | 前三者被拦；CSSOM 生效；`color-mix` 的计算值为 `oklab(...)`；`:focus-visible` 支持；`themeSource` 能驱动 `prefers-color-scheme` |
| E2 | WCAG 对比度计算（逐层合成）：现状颜色、ZCode 取值、本 spec 取值 | 现状：琥珀横幅白字 3.64、琥珀字 3.49/3.64、深色红 3.13、紫 2.90、橙 3.60、绿 4.01；ZCode 浅色次要字 4.13；本 spec v0.2 为 290 对 0 失败（`tools/tk.mjs`）；深色焦点环直接贴粉按钮为 1.30，由此规定 offset ≥ 2px |
| E3 | 同 E1 环境：`adoptedStyleSheets`；内联 SVG 表现属性 | 均生效 |
| E4 | 用既有 e2e 夹具启动真实 app 截图；在 Playwright 下切 `nativeTheme` | 截图即 §2.1 的证据；Playwright 默认模拟 light，先 `emulateMedia({ colorScheme: null })` 才跟随 `nativeTheme` |
| E5 | mock 在 Electron 真机渲染并注入 DOM 审计（三轮） | 第一轮（v0.1 首稿，148 对全绿）：审计抓到徽标叠加 4.40–4.49 与 `--fg-subtle` 误用 3.42/3.87 等约 20 处；第二轮（v0.1 定稿）：10 次渲染 0 失败；第三轮（v0.2，审计已合成 opacity 并加非文本检查）：6 张 × 3 态共 18 次渲染 0 失败、0 字号越界、0 CSP 违规 |
| E6 | 目录 mtime 的刷新条件 | 只有同目录 tmp + `os.replace` 会刷新期目录 mtime；`paths.atomic_write` 正是此形态（`paths.py:126-128`） |
| E7 | ZCode 源码浅克隆 `29628c9`（Apache-2.0）阅读 | 见 §2.4 |
| E8 | Electron 44 下的原生 popover API：`popovertarget` 按钮 + `popover` 元素，CSP 同生产 | 按 Space 打开；Tab 进入浮层；按 Esc 关闭且焦点回到触发按钮 |
| E9 | v0.2 审计首跑 | mock-04 `⌘S` 键帽在粉色主按钮里浅色 4.14、深色 1.36（`--fg-muted`）；改为继承字色后归零 |
| E11 | 同名变量冲突（红队二轮 🟡-2）：tokens.css 在前、旧 style.css 在后，比较改名与不改名两种情况下的 `--bg`、body 背景与 `color-scheme` | 不改名：浅色为旧值 `#fafafa`（后加载、同特异性胜出），深色被 token 的 `:root:not([data-theme="light"])`（0,2,0）覆盖为 `#161618`；`color-scheme` 由 `light dark` 变为 `dark`。只改定义、不改 `var()` 引用：body 直接吃到 token 值，说明引用也必须一起改 |
| E12 | 审计的背景图旁路（红队二轮 🟡-1）：植入 M1 后，给面板加一层透明渐变 | v0.2 审计：失败 38 → 12，`n` 82 → 81；v0.3 审计：`bgImageFail=61`、对比度失败 12，无一漏报 |
| E13 | `tools/vs12.mjs` 原型（typescript AST） | 6 条自测通过；对现状 `DecisionBar.tsx` 报 3 处（按钮均无 className，属 PR2 前的预期） |
| E10 | 按红队 Y2 复算 | 悬停行徽标 4.22、选中行未知圆环 2.92、侧栏搜索框边界 2.86、stale 0.6 下 muted 2.65，全部复现；v0.2 的对子表复现 4.22 与 2.86 后调到全绿 |

## 附：行号核实自查表

2026-09-26 对照工作树 HEAD `9eb1f97` 逐行核实（`sed -n <行>p | grep -F <锚>`，v0.1 42 条 + v0.2 补核 9 条全部命中）：

| 引用 | 核实结果 |
|---|---|
| `renderer/style.css:1`、`:2`、`:4`、`:10`、`:13-16`、`:20`、`:22`、`:24`、`:28`、`:33-34`、`:36-37`、`:51-53`、`:59`、`:62`、`:76`、`:78` | ✓ |
| `renderer/App.tsx:175`、`:192`、`:227`、`:272`、`:274`、`:276-279`、`:303`、`:325`、`:363`、`:365`、`:408` | ✓ |
| `renderer/DecisionBar.tsx:108`、`:114`、`:126`、`:131` | ✓ |
| `renderer/PreviewPane.tsx:20`、`:124`（v0.2 补核：音频队列 `li onClick`）、`:144`、`:169-170`、`:235` | ✓ |
| `renderer/index.html:5` | ✓ 生产 CSP，无 `font-src` |
| `tests/static/guards.test.ts:25-43`、`scan.ts:167-188` | ✓ |
| `shared/protocol.ts:21`（v0.2 补核：错误码闭集只有 `E_BAD_REQUEST`）、`:150-156` | ✓ |
| `host/service.ts:491`、`:493`（v0.2 补核） | ✓ `E_BAD_REQUEST` |
| `main/index.ts:292-293`（v0.2 补核） | ✓ `startHost(); createWindow();` 同步相继执行 |
| Spec 8 archive 第 241、298、306、381 行；§3.6（v0.2 补核） | ✓ 「加停机标记」；「置灰标『陈旧』」；「UI 便利状态由 renderer `localStorage` 记忆」；「修改只经 main 的原生对话框」 |
| `host/episodes.ts:55-56` | ✓ |
| `pipeline/status.py:360-364`（v0.2 补核）、`:398-409` | ✓ 「07 自动质检（未通过）」`is_blocked=False`；末态 `09 人工发布` `is_blocked=True` |
| `pipeline/paths.py:126-128` | ✓ |
| `e2e/ackFixtures.ts:244`、`e2e/preview.spec.ts:75`、`:125`、`:220`、`:234`（v0.2 补核 75/220）、`e2e/preview-security.spec.ts:53`（v0.2 补核）、`e2e/ack.spec.ts:505`（v0.2 补核）、`:547`、`:642-643`、`:650` | ✓ |
| Spec 10 第 21、301 行（v0.2 补核） | ✓ 「运行中」「N 张卡待答」两徽标；「停机标记仍只来自 `status --json`」 |
| `node_modules/electron/electron.d.ts:10195` | ✓ |
| ZCode `29628c9`：`task-row.tsx:101-104`（v0.2 补核：胶囊注释）、`styles.css:461-760`、`types.ts:45-47`、`taskListItemPresentation.ts:33-53` | ✓；提交日期 author 09-23 / commit 09-24 |
| 渲染包体积 | ✓ JS 879,322 B、CSS 5,378 B |
