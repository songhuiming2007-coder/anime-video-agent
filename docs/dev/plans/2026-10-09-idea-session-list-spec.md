# 选题会话也有会话列表：看、进、新开、删；建期只带当前这段（D58）

日期：2026-10-09　状态：**已施工·机检与 e2e 通过**，待真实窗口手验
关联：D58（主）、D57、D45（期会话列表）、D42 / Spec 18（`2026-10-08-idea-session-migration-spec.md`）、Spec 10 红线 3

## 人的裁决（2026-10-09）

1. 选题会话要和期会话一样：看得到有哪些、能进任意一段、能新开、能删（推翻 Spec 18「选题会话是单一滚动段」；同一时刻仍只有一个**活**会话，与期会话一致）。
2. 建期时**只带当前这段**（正在聊或最后聊的那段），其余留在选题列表里。

## 读码事实

- `renderer/SessionList.tsx`（D45）按 `convKey` 写，进 / 新开 / 删走 `conv.enter` / `conv.fresh` / `conv.delete`；宿主经 `epConv()` 对选题一律拒绝。`conv.sessions` 已在 D57 支持选题（core `ava idea /list-sessions`）。
- core：`protocol --idea` 恒恢复最近段，没有「指定段」与「新开」；`/delete-session` 只认期目录；`move_session_to_trash` 按 sid 拆行、持租约原子写回，与日志目录无关。
- 建期：宿主先 `endForMigration("idea")` 再 `ava new <名> --from-idea`；`migrate_idea_session` 把 `_idea/session.jsonl` 整份复制进新期再清空。

## 设计

### core
- `session_log.split_by_sid(raw, sid)`：纯函数，回收站与建期迁移共用（坏行无法归属，一律留原处）。
- `protocol --idea --continue <sid前缀>`：恢复指定段；`protocol --idea --fresh`：不恢复、开新段；裸 `--idea` 不变（终端 `ava idea` 不受影响）。
- `ava idea /delete-session --sid=<sid>`：取 `_idea` 租约 → 移到 `data/_idea/_agent/session-trash/`；退出码同期会话版（0 / 1 / 2 / 3）。
- `ava new <名> --from-idea[=<sid>]`：给了 sid 只迁这一段；没给迁最近可恢复段；新期只写这段的行，`_idea` 写回其余行（持租约原子写），不再整份清空。

### 宿主
- `conv.enter` / `conv.fresh` / `conv.delete` 对 `idea` 放行：进入 = 结束空闲活会话后以 `--idea --continue <sid>` 拉起；新开 = 结束并让下一次拉起带 `--fresh`；删除 = 原生确认框 → 结束活会话 → core 删除。
- 建期：`endForMigration("idea")` 之前记下选题活会话的 sid，传 `--from-idea=<sid>`；没有活会话不传。

### 渲染层
- 侧栏「选题」行选中时，下面挂同一个 `SessionList`（`convKey="idea"`）。
- D57 的会话头按钮由列表取代（与 D45 去掉期会话「继续上次会话」按钮同一口径）；D57 顺带修的「系统注入默认收起」保留。

## 测试与变异

core：拆行纯函数；`--continue <sid>` / `--fresh` / 裸 `--idea`；删除只搬目标段；迁移只带指定段、`_idea` 留其余段、不给 sid 迁最近段。vitest：spawner argv、宿主 idea 进 / 新开 / 删（取消不 spawn）、建期传 sid。真实 core e2e：两段选题对话 → 列表两条 → 进旧段接上历史 → 删一段 → 建期只带当前段。变异：迁移不按 sid 过滤、`--fresh` 被忽略、删除搬错段、建期不传 sid。
