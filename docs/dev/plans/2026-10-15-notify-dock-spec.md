# D98 桌面端提醒：方案 C（Dock 角标 + 等人时持续弹跳）

日期：2026-10-15　状态：**机检通过，待人验收**（UI 规则 2/3：真实任务手验由人做、人勾）
选型：[三方案与演示](2026-10-15-notify-options.md)，人 2026-10-15 选 **C**（「在 Dock 的弹跳比较好」）

## 一、目标、对应 issue 与用户场景

- **D98**：AVA 等人审批、回合结束、作业跑完时没有任何提醒，人切走就不知道它在等自己。
- 场景（UI 规则 1）：人在 1280×800 / 1440×900 的 AVA 窗口里交代一个长任务，切到浏览器或编辑器；AVA 需要人时，人要在不切回来的情况下知道「它在等我」；切回来时提醒自动消失。
- **人试演示时的发现**：A / B 两个方案的系统横幅在人的机器上**都没弹出来**（开发版 Electron；原因没查，可能是通知权限或专注模式）。方案 C 不发横幅，不受影响；以后若要横幅，先查这一条。

## 二、前置条件

- 基线 `uv run pytest` 全绿；`desktop/` 下 `npx tsc --noEmit -p .`、vitest、e2e 全绿。
- 只在 macOS 有 Dock；`app.dock` 为 undefined 的平台整套静默不做（代码里全部 `app.dock?.`）。

## 三、改动清单

| 文件 | 锚点 | 改动 |
|---|---|---|
| `desktop/src/shared/attention.ts`（新） | — | 纯函数：`turnAttention`（`turn_finished.stopped` → 类别）、`jobAttentions`（上次在跑、这次结束的作业）、`onAttention` / `onFocus`（Dock 状态机：角标数、是否在弹、去重表） |
| `desktop/src/host/sessions.ts` | `case "request"` 的 `if (!s.openIds.has(id))` 内；`case "turn_finished"`；`case "stop_points"` | 新审批卡、停机点内容变化且非空、回合结束三处调 `deps.attention`；停机点按会话记上一帧内容（每回合都会重发一帧，不变就不叫） |
| `desktop/src/host/service.ts` | `refold` 的 `rt.jobs = foldEvents(` 前后 | 比较前后两次折叠，新结束的作业报 attention；`HostDeps.attention` 可选，同时转给 `SessionManager` |
| `desktop/src/host/index.ts` | `new HostService(` 的第二个参数 | `attention` → `parentPort.postMessage({ type: "attention", … })` |
| `desktop/src/shared/lifecycle.ts` | `HostToMain` | 增 `{ type: "attention"; kind; key }` |
| `desktop/src/main/index.ts` | `h.on("message"` 分支表；`win.on("closed"` 之前 | 收到 attention 时按状态机执行 `app.dock.setBadge` / `bounce("critical")`；窗口 `focus` 时清角标、`cancelBounce` |

事件分类：

| 时刻 | 类别 | Dock |
|---|---|---|
| 新审批卡（含检查点询问） | 等人 | 角标 +1，开始持续弹跳 |
| 新停机点（02.5 / 03.5 / 05 / 09） | 等人 | 同上 |
| 回合失败（`stopped` 非 done / interrupted / command） | 等人 | 同上 |
| 作业失败 | 等人 | 同上 |
| 回合正常结束、作业成功 | 做完了 | 只加角标 |
| 人自己点停止、斜杠命令 | — | 不提醒（人就在窗口前） |

## 四、新常量与取值理由

- `SEEN_CAP = 500`：去重表只在窗口失焦期间有意义（回到前台即清空），封顶只防极端情况下无界增长；一次失焦期间 500 个不同事件远超真实用量（一轮会话几到几十个），到顶时清空重来，最坏后果是重复计数一次，不会漏提醒。
- 弹跳用 `critical`：人选 C 的理由就是「一直弹到我回来」；`informational` 只弹一下。

## 五、测试（已完成）

- `tests/shared/attention.test.ts` 14 条：分类、作业只报转变、前台不打扰、去重、只弹一次、切回清零 / 只在弹时才 cancel、切回后去重表清空。
- `tests/host/attention.test.ts` 4 条：审批卡去重、停机点不变不叫 / 空不叫 / 换新再叫、回合三种结束、作业结束经 service 真实折叠（`events.jsonl` 夹具，pid 用测试进程自己）、会话层经 service 接线。
- `e2e/dock.spec.ts` 1 条：真实窗口失焦 → 假会话弹审批卡 → main 记下 `badge 1` + `bounce` → 窗口 focus → `badge ""` + `cancelBounce`。
- 变异 17/17：纯逻辑与 host 14 处（前台也提醒、不去重、做完了也弹、重复弹、切回不停、点停止也提醒、失败当 info、作业失败当 info、开机补报、停机点不变也叫、空停机点也叫、审批卡不报、service 不报作业、service 没接会话层），主进程 / host 入口 3 处（main 不处理、focus 不清零、host 不转发）。

## 六、验证命令

```bash
cd desktop && npx tsc --noEmit -p . && npx vitest run && PYTHONDONTWRITEBYTECODE=1 npx playwright test
```

## 七、明确不做

- 不发系统横幅、不出提示音（人选 C）。
- 不做设置开关（macOS 专注模式与 Dock 设置已能管）。
- 不改 Dock 图标本身。

## 八、完成判定

- [x] 机检：tsc / vitest / e2e 全绿，变异 17/17
- [ ] **验收第 1 条（UI 规则 2，人勾）**：重新打包后在真实一期里交代任务、切走，确认等人时 Dock 一直弹、做完了只有角标、切回清零；别扭之处记回本节
