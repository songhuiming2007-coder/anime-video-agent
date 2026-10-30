# 03.5 顺听面板：向 ZCode 对齐的选型图

日期：2026-10-30
关联：D152（桌面端风格整合）；UI 规则 4（先出真实窗口图让人选，不直接改产品）

截图：`desktop/out/d152-voice-shots/`（不进 git）。对照页 `index.html`。

复现：

```bash
node docs/dev/plans/2026-10-30-voice-panel-zcode-shots/shoot.mjs
```

静态 mock，CSS 直接链产品的 `tokens.css` / `ui.css` / `style.css`。不启 Electron、不碰 `data/`。行为与 RPC 一字不动；这轮只选皮肤。

## 方案

| 方案 | 截图 | 做法 | 得 | 失 |
|---|---|---|---|---|
| 现状 | `now-*` | 现有 VoicePanel：描边工具栏、行间 1px 线、每行常驻「换种子」、▶ 字符、隐藏 `<audio>` 无进度 | 一眼看完全文（D50 已定） | 表格感；和已抄 ZCode 的侧栏 / 对话流不是一套语言 |
| **A 行列表（建议）** | `A-*`、`A-fix-*` | 去行间线，hover/选中铺底（ZCode file/task-row）；工具栏改 ghost；换种子悬停才出；底加迷你播放条；纠错卡仍挂在段上 | 保留「一眼看全片 + 就地标错」；只换皮肤 | 段多时仍要在预览栏里滚全文 |
| B 列表+详情 | `B-*` | 上：28px 段索引（ZCode 文件树）；下：当前段全文 + 播放条 + 动作（ZCode PreviewPane） | 段多时更像 ZCode 预览；播放/纠错有固定位置 | **改了 D50 的信息结构**：不能同时看见所有段文 |

**2026-10-30 人选 A，已施工。** D50 人选过「预览区段落表、全文铺开、选中即弹卡」。A 只把那张表换成 ZCode 行语言，不重开结构。B 未做。

不动：`tokens.css` / `ui.css` 取值、批准/打回同权、voice.* RPC、隐藏 `<audio>` 的播放链路（只加一条看起来像进度的条，进度仍是示意，施工时接 `audio.currentTime`）。
