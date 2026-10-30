# D152 续：预览文件切换器选型

日期：2026-10-30　状态：**人选 A，已施工**
关联：D152 C 类 `PreviewSwitcher`。点开后的浮层。

## 怎么看

截图：`desktop/out/d152-switcher-shots/`（不进 git）。对照页 `index.html`。

```bash
node docs/dev/plans/2026-10-30-switcher-zcode-shots/shoot.mjs
```

浮层是打开的。分组（稿件 / 配音 / 排片）和「未生成」灰项都不动。

## 不动

- `tree.list` / 点选切预览
- 02-script.md 可编辑、从草稿新建
- 生成中徽标
- testid `preview-switch` / `preview-switch-btn` / `preview-switch-pop` / `preview-switch-row`

## 三案

| | 触发 | 浮层 |
|---|---|---|
| 现状 | 文件名加粗 | 标题「本期文件」+ 分组 |
| **A 去掉标题（建议）** | 文件名常规字重 | 不要「本期文件」，分组留下 |
| B 拍扁 | 同 A | 不要分组标题，一列文件 |

**建议 A。** 触发已经是当前文件名，浮层顶上再写「本期文件」是重复。B 把「未生成」和已有文件混成一列，D49 分组就没了。
