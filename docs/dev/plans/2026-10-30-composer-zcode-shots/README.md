# D152 续：会话头 / 输入栏选型

日期：2026-10-30　状态：**人选 A，已施工**
关联：D152 P0 Composer / SessionHeader。发送钮保持粉色文字钮（已定）。

## 怎么看

截图：`desktop/out/d152-composer-shots/`（不进 git）。对照页 `index.html`。

```bash
node docs/dev/plans/2026-10-30-composer-zcode-shots/shoot.mjs
```

回合在跑，所以「停止」看得到。

## 不动

- 发送仍是 `ui-btn--primary`「发送 / 排队」
- `conv.send` / `conv.interrupt` 路径
- 模型 / 推理下拉还在输入栏左侧
- 降级「LLM 未就绪」仍要看见

## 三案

| | 停止 | 「LLM 已连接」 |
|---|---|---|
| 现状 | 实心底小按钮 | 绿点 + 五个字 |
| **A 幽灵停止（建议）** | ghost，跟顺听停钮同一套 | 只留绿点，悬停才出「LLM 已连接」 |
| B 再收 | 只留停止图标 | 绿点也去掉（连上就不说） |

**建议 A。** 停止还要能看见字。B 把「停」收成图标，和粉色发送不对等。
