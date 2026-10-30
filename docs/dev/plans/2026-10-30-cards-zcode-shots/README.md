# 停机卡 / 工具审批卡：向 ZCode 对齐的选型图

日期：2026-10-30
关联：D152；UI 规则 4

截图：`desktop/out/d152-card-shots/`（不进 git）。对照页 `index.html`。

```bash
node docs/dev/plans/2026-10-30-cards-zcode-shots/shoot.mjs
```

静态 mock，CSS 链产品 token。不改 core 的 `card_text`（终端通道仍是框线原文）；桌面端可以改「人先看到什么」。

闸门按钮同权（批准 / 打回 / 拒绝一律 `ui-btn`，不许 primary）三套都不动。

| 方案 | 做法 | 得 | 失 |
|---|---|---|---|
| 现状 | 停机卡副标题堆 ISO + approval_id + mtime_ns；工具卡把 core 的框线 `card_text` 当正文 | 终端和桌面看到同一段字 | 对话流里已经是 ZCode 工具行，卡还是 2010 年终端表单 |
| **A 分层卡（建议）** | 工具卡：用途 + 命令代码块 + 危险徽标；框线原文进「原文」。停机卡：标题 + 待审徽标 + kv（路径和字节）；ISO / id / mtime 进详情或不展示 | 命令仍一眼可见（D80 要的）；和对话流同一套块语言 | 卡还是卡，没有变成 PermissionDialog 那种行 |
| B 行+展开 | 主信息一行（类别 + 用途 + 命令省略号 + 按钮），详情挂竖线展开 | 最像 ZCode PermissionDialog | 命令被挤成一行；09 封面网格 / 02.5 编辑器插槽塞不进一行 |

**2026-10-30 人选 A，已施工。** core 的 `status_card.py` 框线不改（终端还要用）。bash 卡桌面端改用途+命令块；写稿 diff / 工单摘要仍走原文卡面。
