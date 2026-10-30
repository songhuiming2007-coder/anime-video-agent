# D152 续：会话头空模式徽章选型

日期：2026-10-30　状态：**人选 A，已施工**
关联：D152 SessionHeader。默认 scope 是 `auto`，现在画成徽章「—」。

## 怎么看

截图：`desktop/out/d152-scope-shots/`（不进 git）。对照页 `index.html`。

```bash
node docs/dev/plans/2026-10-30-scope-zcode-shots/shoot.mjs
```

这是默认自动模式。素材 / 流水线 / 创作 / 企划 有名字时，A 仍会显示徽章。

## 不动

- 素材模式按钮、`conv.command` scope
- 上下文读数徽章（D64 人选 B）
- 降级「LLM 未就绪」
- testid `session-scope`（有名字才渲染）

## 三案

| | 自动模式 |
|---|---|
| 现状 | 徽章「—」 |
| **A 空着（建议）** | 没有徽章 |
| B 写成「自动」 | 灰字「自动」，不是徽章 |

**建议 A。** 「—」不是模式名。B 发明一个用户没见过的词。
