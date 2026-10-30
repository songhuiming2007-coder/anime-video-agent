# D152 续：侧栏「新建一期」选型

日期：2026-10-30　状态：**人选 A，已施工**
关联：D152 B 类 `NewEpisodeForm`。企划的「＋ 新建企划」已经是 `ui-row`，建期还是实心底按钮。

## 怎么看

截图：`desktop/out/d152-newep-shots/`（不进 git）。对照页 `index.html`。

```bash
node docs/dev/plans/2026-10-30-newep-zcode-shots/shoot.mjs
```

## 不动

- 期名仍是空串初值，不从对话预填（TG-15）
- 建目录仍走 `episode.create`
- testid：`new-episode` / `new-episode-toggle` / `episode-name` / `episode-create`

## 三案

| | 触发 | 表单 |
|---|---|---|
| 现状 | 实心底「＋ 新建一期」压满侧栏 | 期名 + 大「建期」 |
| **A 改成行（建议）** | 跟「＋ 新建企划」同一套 `ui-row` | 建期改 `ui-btn--sm` |
| B 收进顶行 | 「＋」放搜索旁，平时不占一行 | 表单变浮层 |

**建议 A。** 现状和企划入口两套样。B 把建期藏进图标，误触也更烦。
