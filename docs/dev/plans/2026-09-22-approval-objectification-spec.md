# Implementation Spec：approval 对象化（Spec 3 / ADR-0020 §3）

日期：2026-09-22（**v0.6**，2026-09-23 跨 Spec 修订四批：v0.3 并入 S3-R1/R2/R5（§1.3），v0.4 并入 S3-R6/R7（§1.4），v0.5 并入 Spec 8 红队二轮提出的 S3-R9/R10/R11（§1.5，含经用户授权的 `pipeline/review.py` 改动），v0.6 按 Spec 8 红队三轮 R3-M1 在 S3-R9 授权范围内收紧 §4.5（§1.6）；v0.2 红队两轮收口；状态：**v0.6 PR1/PR2 可动工，PR3 待红队定向复核 §1.3 + §1.4 + §1.5 + §1.6**）  
跨 Spec 修订来源：`docs/dev/plans/2026-09-23-electron-desktop-spec.md`（Spec 8）§6.2；S3-R6/R7 出自 Spec 8 v0.1 红队一轮 B1/B2（裁决见 Spec 8 §1.1）；S3-R9/R10/R11 出自 Spec 8 v0.2 红队二轮 R2-B1/R2-M7/R2-M8（裁决见 Spec 8 §1.1）  
上位文档：`docs/dev/plans/2026-09-22-harness-evolution-direction.md`（§2 Spec 3，§4 施工红线八条），`docs/dev/adr/0020-harness-eventization-and-electron-desktop.md`（§3 停机点升格为 approval 对象，§5 Session 恢复，「不做的事」）  
格式与契约范本：`docs/dev/plans/2026-09-22-jobs-and-events-spec.md`（Spec 2 v0.4，红队四轮收口）  
红队一轮、二轮报告：终端输出，未落盘（本项目红队报告不存档，§1 裁决表即唯一存档）；二轮结论 🟢 可动工  
文档更正（2026-09-23，不改版本号与状态）：① 原第 6、7 行及 §1.2 引用的 `…-redteam-r1.md` / `…-redteam-r2.md` 从未存在，已改为如实说明；② `build_status_card` 的行号原写 62 有误，实为 65（`status_card.py` 自 `7866ded`（2026-09-21）起未改动，属原始引用错误，不是行号漂移），正文与自查表共 5 处已更正。二轮「附录 28 处行号全部复核通过」的结论因此不准确，**施工前需把全部行号重核一遍**。

---

## 0. 一句话设计

**停机点即对象，ack 即显式，物理闸门永远先于对象状态。**  
新增纯 Python 模块 `pipeline/approvals.py`，把停机点 02.5 / 03.5 / 05 / 09 升格为可持久化、可跨进程恢复的 `Approval` 对象（`pending → approved | rejected(feedback) | superseded`），快照落盘 `data/episodes/<期>/_agent/approvals_store.json`（读-改-写全程 fcntl 排他锁 + atomic_write）；所有公共入口（`list_pending` / `approve` / `reject`）内置幂等自愈补全，不依赖任何单一接线点；ack 永远是人敲下的显式命令（REPL `/approve` `/reject`、裸形态 `ava <期> /approve`），同源三写：对象库更新 + `approvals.jsonl` 记账 + `APPROVAL_RESOLVED` 事件；rejected 携带结构化反馈（哪段、什么问题）回写 `_agent/approval_feedback.md` 供后续 agent 会话经 `read_artifact` 读到。**对象层永不替代物理产物闸门**（02-diff.patch / 04-clips.approved.json）；解封物对齐必须同时满足时序单调性与段级 diff 校验（复用 `align.py:210-235 has_clips_approved_diff`），杜绝过期解封物代批、驳回冲刷与双头状态机。

---

## 1. 红队裁决与修订纪要

### 1.6 跨 Spec 修订第四批（v0.5 → v0.6，来源 Spec 8 v0.3 红队三轮 R3-M1；在 S3-R9 已授权范围内收紧，不扩大 `pipeline/review.py` 的改动面；待红队定向核对）

| 编号 | 问题（红队原指控，已独立复核属实） | 裁决 | 修订动作 |
|---|---|---|---|
| S3-R9a | **S3-R9 在 fstat 与 read 之间有窗口**（Spec 8 R3-M1）：fd 只挡换 inode 的替换（流水线自身落盘走 `paths.atomic_write`，`clips.py:1242`、`1289` 已核实）；若写入方原地覆盖（truncate 后 write，如人在编辑器里改、外部 agent 直接写 `04-clips.json`），fd 指向同一 inode，fstat 见 F1、read 得 F2，解封物成了 F2 的字节配 F1 的 mtime。`render.py:1068` 的硬闸只调 `has_clips_approved_diff`（`align.py:210-235`，只比段内容），对 F2 判无 diff，闸门为未审的 F2 打开。T20 ③ 的改写发生在字节读完之后，覆盖不到这个窗口。本轮复核实测：同长度原地覆写后 read 得 F2、`len(bytes) == size` 仍成立，只有读后 fstat 的 mtime_ns 变了 | **采纳** | §4.5 第 3 步收紧：读完后再 `os.fstat(fd)` 一次，要求 (size, mtime_ns) 与第 2 步相同**且** `len(bytes) == size`，否则 `SystemExit` 退出 1、不写任何文件。读取经模块级辅助函数 `_read_all(f)` 完成，作为 T20 ⑥ 的测试缝。新增 T20 ⑥ / MUT-24；残余窗口如实记入 RF-10 |

### 1.5 跨 Spec 修订第三批（v0.4 → v0.5，来源 Spec 8 v0.2 红队二轮；S3-R9 经用户 2026-09-23 单独授权改 `pipeline/review.py`；待红队定向复核）

| 编号 | 问题（红队原指控，已独立复核属实） | 裁决 | 修订动作 |
|---|---|---|---|
| S3-R9 | **物理闸门为未审版本打开**（Spec 8 R2-B1）：调用方核对 `04-clips.json` 指纹（F1）后 spawn `review --approve`；在 `review.py:316` 读文件前另一进程 `--refit` 成 F2（段级对齐仍成立），`review.py:340` 的 `copy2` 复制 F2——`04-clips.approved.json` 成为 F2 的有效解封物，下次自愈把替身对齐为 APPROVED，渲染闸门为人没审过的版本打开。调用方事后比对只能检测、拦不住 | **采纳（用户选方案 a，授权改 `review.py`）** | 新增 §4.5：`review --approve` 增加可选参数 `--expect-size=<N>`、`--expect-mtime-ns=<M>`（**必须用等号形式**：`tools.py:126-151 _extract_positional_args`（`valued_flags` 在 134-137） 不认识这两个旗标，空格形式会把值当成位置参数，使 `validate_pipeline_command` 不再自动注入期目录）。给定时：**只打开源文件一次**，`os.fstat` 核对 (size, mtime_ns)，不符退出 1、不写任何文件；此后的段级校验与写出**只使用这一次读到的字节**，写出走 tmp + `os.utime(ns=…)`（mtime 取自 fstat，等价于 `copy2` 的 mtime 语义）+ `os.replace`。于是解封物要么是人审的那一版（F1），要么不存在；即使读后源文件被改成 F2，解封物仍是 F1，Spec 3 双闸与 `render.py` 段级 diff 硬闸都会判它对 F2 失效。不给参数时行为与现状逐字节一致（`copy2`）。新增 T20 / MUT-20、MUT-21 |
| S3-R10 | **v0.4 冻结的加锁顺序自我死锁**（Spec 8 R2-M7）：`approve()` 声明「全程在 `_locked_approvals` 内」且第 1 步 `_self_heal`，而 §4.1 内部规格写 `_self_heal`「内部走 `_locked_approvals`」；`_locked_approvals` 先拿不可重入的模块级 `threading.Lock`，再对**新开的 fd** `flock`。红队与本轮复核均实测：同进程第二个 fd 对同一锁文件 `LOCK_EX\|LOCK_NB` 返回 EWOULDBLOCK，`threading.Lock` 重入 `acquire(blocking=False)` 返回 False | **采纳** | §4.1 内部规格重写为「一次进锁」：拆出 `_self_heal_locked(store, ep_dir)` 与 `_ensure_pending_locked(store, ep_dir, status)`，只在已持有锁的列表上操作、**永不再进锁**；`approve`/`reject`/`list_pending`/`ensure_pending`/`get` 各自只进一次 `_locked_approvals`。新增 T21 / MUT-22 |
| S3-R11 | **确认路径记下的「决策延迟」可能不是决策延迟**（Spec 8 R2-M8）：v0.4 在确认时写 `latency_s = now − created_at`；若人几小时前已在终端 `review --approve`，之后才确认，这个数把「打开桌面端的时刻」当成了决策时刻，污染审批疲劳判据的原料（§2.4） | **采纳（确定性规则，无阈值）** | 确认路径的延迟**只在「artifact 对齐发生在本次 `approve` 调用的自愈中」时记录**（`_self_heal_locked` 返回本次转移清单，判据是确定性事实，不设时间阈值）：此时解封物产生于上一次自愈之后、本次调用之前，决策即刚刚发生；否则 `log_approval_decision(..., latency_s=None)`，只记「已确认」不记延迟。T19 拆为 T19（先 `/approvals` 对齐再确认 → 延迟为空）与 T19b（直接 `/approve` 在同一调用内对齐并确认 → 延迟非空）；新增 MUT-23 |

### 1.4 跨 Spec 修订第二批（v0.3 → v0.4，来源 Spec 8 v0.1 红队一轮 B1/B2；沿用用户 2026-09-23「可以修订 Spec 3」授权；待红队定向复核）

同样只动 ack 语义与 CLI 表面（§3.1 新增两个可选字段、§4.1 `approve`/`reject` 签名与顺序、§4.2 语法），PR1/PR2 不受影响。

| 编号 | 问题（红队原指控，已独立复核属实） | 裁决 | 修订动作 |
|---|---|---|---|
| S3-R6 | **ack 没有绑定对象**：裸形态只带 stop。调用方看到 pending A（指纹 F1）后，产物被改成 F2；`approve()` 第 1 步 `_self_heal` 按 §2.2 第 3 条把 A 转 SUPERSEDED 并新建 pending B（F2），随后批准 B——人看的是 F1，批准的是 F2。连带发现本 spec 内部矛盾：§2.2 规定漂移后可新建 pending，v0.3 的 T4 却断言「漂移后 `list_pending` 返回空」 | **采纳** | `approve`/`reject` 增加 `approval_id` 参数；裸形态**必填** `--id <approval_id>`（固定位置语法，§4.2）；指定对象已 SUPERSEDED → `ApprovalError`，**绝不转而处理替身**。REPL 不带 id 时：若本次调用的 `_self_heal` 刚把同类型对象转为 SUPERSEDED，同样拒绝。T4 按 §2.2 改正（漂移后恰有一条新 pending B，A 为 SUPERSEDED）。新增 T18 / MUT-17 |
| S3-R7 | **artifact 对齐之后的显式 approve 语义未冻结**：02.5/05 必须先有有效解封物才能 approve，而 `approve()` 第 1 步 `_self_heal` 会先把对象惰性对齐为 `APPROVED(resolved_by="artifact")`——v0.3 的第 4 步显式转移在这两个停机点上**永远走不到**；docstring 第 1 步「无 pending → ApprovalError」与第 5 步「同决策幂等」谁先谁后决定了退出 1 还是 0；而且 §2.5「ack 路径内部调用 `log_approval_decision`」在最关键的两个停机点上从不触发，决策延迟永远缺失 | **采纳** | 冻结「确认路径」：对 `APPROVED(resolved_by="artifact")` 且未确认的对象再做同类型显式 approve → 退出 0；**首次**确认写 `confirmed_by`/`confirmed_at`（§3.1 新增字段，`resolved_by` 保持 `"artifact"` 不被覆盖）、调 `log_approval_decision`（`latency_s = 确认时刻 − created_at`）、发 `APPROVAL_RESOLVED`（载荷加 `"confirms": "artifact"`）；重复确认幂等、不重复记账。新增 T19 / MUT-18、MUT-19 |
| S3-R8 | 补丁段清单的确定性输出通道 | **不提出** | Spec 8 对红队 B3 选方案 (a)：v1 删去「带 `--confirm-patch` 重试」按钮，含补丁段的期在终端完成 05 批准，无需本修订 |

### 1.3 跨 Spec 修订（v0.2 → v0.3，来源 Spec 8 §6.2；用户 2026-09-23 授权修订；待红队定向复核）

只动 CLI 表面契约（§4.2）及其测试/门禁，**不动数据模型、持久化、自愈、解封物双闸**——PR1/PR2 施工范围不受影响。

| 编号 | 请求 | 裁决 | 修订动作 |
|---|---|---|---|
| S3-R1 | 裸形态 `/reject` 参数无损：现行 `cli.py:1170` 先 `" ".join(args[1:])` 再分派，target 内空格必然被切碎；而本 spec §2.4 自己的 target 示例 `"04-clips.json s07 1:20"` 就含空格，与 v0.2 §4.2「第二个参数为 target」的语法互相矛盾 | **采纳** | §4.2 重写：裸形态 `/approvals` `/approve` `/reject` 直接按 `args` 列表**位置**取参（`args[2]`=stop、`args[3]`=target、`args[4:]`=problem 以单空格连接），不经 1170 的拼接串；REPL 的 `/reject` 改用 `shlex.split` 解析，含空格的 target 用引号包住；新增 T15 / MUT-14 |
| S3-R2 | ack 子命令退出码无契约，调用方（桌面端 host）无法分流 | **采纳** | §4.2 冻结退出码：0 = 已转移或同决策幂等；1 = `ApprovalError`（stderr 一行受控提示，不打栈）；2 = 用法错误；新增 T16 / MUT-15 |
| S3-R3 | `/approvals --json` 机读输出 | **后置** | 非阻塞；Spec 8 v0.1 以「spawn 触发自愈 + 直读 store」两步满足，不在本 spec 加面。需要时另行修订 |
| S3-R4 | `source` 枚举增加 `"desktop"` | **后置** | 非阻塞；桌面端 spawn 的是裸形态，按现有枚举记 `"cli"`，语义正确 |
| S3-R5 | 未识别子命令落入 `run_repl`（`cli.py:1199`）：Spec 8 撰写时实测 `ava <期> /approve 02.5 < /dev/null` 退出码 0 且写出 `{"stop": "02.5", "minutes": 0.0}` 的人时记录 | **采纳（收窄）** | **不能**改成「未识别即报错」：`docs/WORKFLOW.md` 把 `ava <期> /chat`、`ava <期> /script` 写成正式用法，而 `main()` 并不分派它们（二者是 REPL 内部命令，`cli.py:991`、`995`），交互终端里正是靠落入 REPL 才可用。收窄为：**stdin 非 TTY 且带了未分派的子命令时，stderr 报错并退出 2，不进 REPL**；TTY 行为不变。依据是 `cli.py:1120`（`ava new`）、`1141`（看板）、`1156`（`ava idea`）三处既有非 TTY 降级先例。新增 T17 / MUT-16 |

### 1.2 第二轮红队裁决（v0.2 复审，🟢 可动工）

一轮 14 项指控（3🔴 + 7🟡 + 4🔵）全部复核闭环，二轮未发现新增阻塞性漏洞。三项新设计专项抽检通过：① 解封物双闸的依赖纯洁性（align.py 纯 stdlib 叶子模块，无环）；② 状态卡引导行热路径无递归/死锁（`build_status_card → list_pending → _self_heal → inspect_episode` 单向 DAG + 异常静默降级）；③ MUT-11/12/13 变异验算无假阳性逃逸。附录 28 处行号引用全部独立复核通过（2026-09-23 更正：其中 `status_card.py:62` 实为 65，此结论不准确，见头部文档更正）。二轮报告为终端输出、未落盘，裁决结论以本节为准（2026-09-23 更正：原指向的 `…-redteam-r2.md` 从未存在）。

### 1.1 第一轮红队裁决与修订纪要（v0.1 → v0.2，3🔴 + 7🟡 + 4🔵 全收；原裁决「🔴 驳回重大修订」）

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🔴 B1 | 过期解封物导致新排片被错误自动批准：重跑 clips 不删旧 `04-clips.approved.json`（`clips.py:1229-1237` 仅打 WARN），§2.3 条件 3 仅凭 `exists()` 即对齐 APPROVED，击穿「--approve 显式动作」铁律 | **采纳** | §2.3 条件 3 重写：对齐必须**同时**满足时序单调性（解封物 mtime_ns ≥ 关联产物 mtime_ns）与段级内容 diff 为空（05 复用 `align.py:210-235 has_clips_approved_diff`，与 `render.py:1067-1075` 硬闸同一判据单源；02.5 用 mtime 单调性 + diff 文件非空）；任一不满足则解封物视为失效，对象保持 PENDING；新增 MUT-11 与 T5 增强断言钉死 |
| 🔴 B2 | pending 创建与 REPL 循环单点偶合：`ensure_pending` 仅插 `cli.py:892`，裸形态 `ava <期> /approvals` 查空、`/approve` 因无对象崩溃 | **采纳** | §2.2/§4.1 重写：状态诊断与补全逻辑内化至 `approvals.py`（`_self_heal(ep_dir)` 函数级延迟 import `inspect_episode`），`list_pending`/`approve`/`reject` 全部先自愈再执行；`cli.py:892` 降级为可选的新鲜度优化接线（传已算好的 status 避免重复 inspect）；`ensure_pending(ep_dir, status=None)` 签名改 status 可选 |
| 🔴 B3 | 驳回后未改产物引发同指纹无限重建 pending，驳回决策被瞬时冲刷，刷爆队列 | **采纳** | §2.2 新增**终态抑制规则**：同 `(期, 类型, 指纹)` 存在任意非 SUPERSEDED 终态（APPROVED/REJECTED）时严禁二次创建；仅指纹漂移（产物已重修）才允许新建 pending；T3 增加「reject 后同指纹刷新 10 次零新建」断言 |
| 🟡 M1 | `_load`/`_save` 拆分导致读路径无锁裸奔，多进程并发状态转移 ABA 覆写损坏 | **采纳** | §4.1 重构为 `_locked_approvals(ep_dir)` 上下文管理器：flock 排他锁包裹读-改-写全程，所有公共操作路由经它；T8 断言面同步对准「锁内完成读-改-写」 |
| 🟡 M2 | MUT-6/MUT-7/MUT-10 依赖的断言在 §7.1 正式用例中不存在，变异注入后意外变绿（伪证伪机理） | **采纳** | 三条断言全部写入 §7.1 正文：T2 增列「events 被 drop 场景恢复」子场景；T7 增列「09 ack 后期目录 diff 仅 `_agent/` 内变化」断言；T6 增列「reject 前后产物哈希快照不变」断言 |
| 🟡 M3 | §9 门禁清单遗漏上位要求「ack 落盘为事件」；RF-8 声称「门禁清单注明回看 T10a」经核实为虚构 | **采纳** | §9 新增门禁 10（ack 事件落盘，含 jobs 未施工期降级 caveat 与回看义务）；§10 RF-8 措辞改为如实引用门禁 10 |
| 🟡 M4 | 模型无法感知 `_agent/approval_feedback.md` 存在，核心验收 2「反馈能被后续 agent 会话读到」事实不可达 | **采纳** | 新增 §2.8 决策 8：`build_status_card`（`status_card.py:65`）追加确定性引导行（pending 类型清单 + 反馈文件指针，仅在存在时追加，函数级延迟 import approvals）；§6.2 同步改写（装配器仍不注入，改走状态卡通道） |
| 🟡 M5 | 03.5 建议型停机点打回后无物理重修出口与驱动机制，驳回意见被静默旁路 | **采纳** | §2.6 表 03.5 行补明：reject 03.5 维持建议型语义不新增物理闸门（与 `status.py:312-326` 注释一致）；重修驱动 = 反馈文件 + 状态卡引导行（§2.8）+ `/voice` 纠错通道；指纹漂移（manifest 重配变更）后经 B3 抑制规则放行才重新询问；升级 03.5 为强制闸须另立 ADR |
| 🟡 M6 | 未定义 `ep_dir → episode` 字符串算法，`relative_to` 在 pytest tmp_path 下必抛 ValueError | **采纳** | §4.1 新增 `_episode_repr(ep_dir)` 规格：镜像 Spec 2 emit 真子集判断（init 缓存 resolve 的 episodes_root，非子集/异常降级 `ep_dir.name`），测试环境落在 tmp_path 时稳定降级不炸 |
| 🟡 M7 | 64 条封顶只淘汰 SUPERSEDED，高频驳回场景队列满行为未定义 | **采纳** | §3.2 补淘汰阶梯：SUPERSEDED（最旧先）→ REJECTED（最旧先）→ APPROVED（最旧先）→ 全为 PENDING 时**拒绝新建并 WARN**，PENDING 永不进淘汰（防静默丢悬而未决） |
| 🔵 m1 | 附录自查表漏列正文引用的 `status_card.py:62`（2026-09-23 更正：应为 65）与 `cli.py:108` | **采纳** | 附录补列，并补列本轮新增引用（align.py:210-235、clips.py:1229-1237、render.py:1057-1075、status.py:117） |
| 🔵 m2 | T10b 的 `sys.modules` 置 None 缺 teardown，污染 pytest 共享进程 | **采纳** | §7.1 T10b 规定使用 `pytest monkeypatch` fixture（自动 teardown），严禁裸改 `sys.modules` |
| 🔵 m3 | 重复/并发 ack 裸抛 ValueError 穿透交互层 | **采纳** | §3.1/§4.1 引入领域异常 `ApprovalError(RuntimeError)`；REPL/裸形态捕获后打印受控提示；同决策重复 ack 幂等返回既有终态对象（不抛错），异决策二次转移才抛 `ApprovalError` |
| 🔵 m4 | `pending_approvals.json` 名实不符（文件含终态对象） | **采纳** | 全文更名为 `approvals_store.json`（锁文件 `approvals_store.lock`） |

---

## 2. 关键设计决策（含代码现状证据）

### 2.1 决策 1：pending approval 的持久化载体——独立快照文件，正面回答「从哪恢复」

- **问题**：验收方向要求「REPL 退出重开后 pending approval 仍在、可 ack」。候选载体有三：独立落盘文件、从 `approvals.jsonl` 重放、从 `events.jsonl` 重建。
- **决策**：**独立快照文件 `data/episodes/<期>/_agent/approvals_store.json`**（JSON 数组，atomic_write 整文件覆写）。它是 approval 队列的唯一真相源；`events.jsonl` 只是它的观测镜像，不参与恢复。
- **论证（为什么不违反「`pipeline.status` 对 events.jsonl 零感知」铁律）**：
  1. **铁律的约束对象是工序状态推导，不是 harness 簿记。** Spec 2 §2.2 钉死的铁律是「`status.py:194-409 _inspect_episode_core` 从物理产物推导工序，events.jsonl 不参与」。approval 队列不是工序状态——它是「人还没拍板」这个会话层事实的记录，与「流水线走到哪一步」正交。`status.py` 对本文件**零读取、零 import**（门禁 4 静态断言），本文件也不反向喂 `status.py`，双头无从形成；
  2. **为什么不能从 events.jsonl 重建**：events.jsonl 按 Spec 2 §2.3 设计是**允许丢的**——队列满即丢（Drop-on-Full）、熔断窗口丢弃、盘满静默、父进程被信号杀死时未落盘事件蒸发（Spec 2 RF-7 已声明的已知上限）。从「设计上允许丢的观测日志」重建「不许丢的审批队列」，是把弱保证当强保证用，红队要求的「REPL 重开 pending 仍在」在丢事件场景下必然破产；
  3. **为什么不能从 approvals.jsonl 重放**：`status_card.py:282-315 log_approval_decision` 写的是**决策记账日志**——每行只有 `timestamp/tool/target/decision(y|n)/decision_latency_s`（行 303-310 核实），没有 pending 对象的创建语义（无 approval_id、无关联产物指纹、无可选项、无 superseded 态）。从终态流水账无法反推「哪些对象还悬着」；
  4. **分工结论**：`approvals_store.json` = 状态（可恢复、可 ack 的输入）；`approvals.jsonl` = 决策审计（§2.5 维持）；`events.jsonl` = 观测与 UI 投影。三者各管一层，互不重建。
- **并发写纪律（红队一轮 M1 修订）**：公共操作不暴露裸 `_load`/`_save`，统一走 `_locked_approvals(ep_dir)` 上下文管理器——**`fcntl.flock` 排他锁包裹读-改-写全程**（锁兄弟文件 `_agent/approvals_store.lock`），区块退出时若有变更才走 `paths.atomic_write`（`paths.py:118-128`，tmp + os.replace）。锁加在兄弟文件而非目标文件上，因为 atomic_write 的 `os.replace` 会换 inode，对目标文件 flock 锁不住后来者；锁文件自身的首创建（`open("a")`）在 flock 语义下安全——`LOCK_EX` 获取前的 open 不产生数据竞争，两个进程各 open 同一路径拿到的是同一 inode 的锁（append 模式打开不截断）。
- **进程内并发**：模块级 `threading.Lock` 先串行化同进程线程，再进 flock 区块。
- **存储红线**：绝不自动 `mkdir` 期目录（`paths.py:131-158 require_data` 铁律）；期目录不存在 / 外置盘脱卸时 `list_pending` 返回空、`ensure_pending` 静默不写，fail-silent。`_agent/` 子目录的创建沿用 `status_card.py:301` 既有先例（`mkdir(parents=True, exist_ok=True)`，仅在期目录本身已确认存在的前提下）。

### 2.2 决策 2：pending 的创建——自愈内化 + 终态抑制（红队一轮 B2/B3 修订）

- **现状证据**：REPL 主循环每轮迭代在 `cli.py:892` 重算 `status = inspect_episode(ep_dir)`；停机点判定走 `cli.py:90-101 human_stop_of`（`HUMAN_STOPS = {"02.5", "03.5", "05", "09"}`，`cli.py:30`），卡片层的停机点标签已在用同一函数（`cli.py:611`、`cli.py:1018`）。
- **决策（v0.2 重写）**：
  1. **自愈内化**：`list_pending` / `approve` / `reject` 入口**必先执行 `_self_heal(ep_dir)`**——函数级延迟 import `pipeline.status.inspect_episode` 现算工序，命中停机点则按需补建 pending。任何表面（REPL、裸形态、未来 Electron host spawn）查到的都是自愈后的队列，**不存在「没走过 REPL 循环就查空」的路径**；
  2. **`cli.py:892` 接线降级为新鲜度优化**：REPL 循环仍调 `approvals.ensure_pending(ep_dir, status)`（传入已算好的 status 避免重复 inspect），但它不再是唯一创建点；
  3. **终态抑制规则（B3）**：同 `(期, 类型, 指纹)` 已存在 **APPROVED 或 REJECTED 终态**对象时，严禁二次创建 pending——驳回决策在指纹未漂移（产物未重修）前永远不被冲刷；只有指纹漂移（人/agent 已改产物）才允许新建 pending（同时触发 §2.3 条件 1 顶替逻辑）。SUPERSEDED 对象不抑制创建（它本身就表示「这版已作废」）；
  4. **创建即发射**：`APPROVAL_REQUESTED` 仅在真正新建时发射一次；自愈路径与 REPL 接线共用同一去重判据，不会双发。
- **关联产物指纹**：对象创建时钉住 `artifacts: [{path, size, mtime_ns}]`（02.5 → `02-script.md`；03.5 → `03-audio/manifest.json`；05 → `04-clips.json`；09 → `05-final.mp4` + `07-titles.md`）。指纹是 superseded 检测与终态抑制的共同锚。
- **可选项（options）**：对象携带 `options: ["approve", "reject"]`；05 附加 `note: "批准须先显式执行 python -m pipeline.review <期> --approve"`（ADR-0020 §4 桌面端 Approval 决策条的契约来源）。

### 2.3 决策 3：superseded 与解封物对齐——惰性检测 + 过期解封物失效判据（红队一轮 B1 修订）

- **superseded 触发条件（确定性规则）**：
  1. **同型顶替**：同 `(期, 类型)` 新 pending 创建时（按 §2.2 规则，此时指纹必已漂移），同型旧 PENDING → `superseded`；
  2. **指纹漂移**：ack 或列举时惰性校验 `artifacts` 指纹，当前磁盘状态与钉住值不符 → `superseded`，本次 ack 拒绝执行（人审的是旧版产物，批准作废）。
- **解封物对齐条件（B1 重写，双闸缺一不可）**：惰性检测发现解封物存在时，**必须同时满足**下列两条才允许 pending → `approved`（`resolved_by="artifact"`）：
  1. **时序单调性**：解封物 `mtime_ns ≥` 全部关联产物的 `mtime_ns`（解封物诞生于关联产物当前版本之后）；
  2. **内容一致性**：
     - 05：`has_clips_approved_diff(ep_dir)` 返回 False（**复用 `align.py:210-235`，与 `render.py:1067-1075` 渲染硬闸、`status.py:117` advisory、`clips.py:1229-1237` WARN 同一判据单源**，绝不自造第二份 diff 逻辑）；
     - 02.5：`02-diff.patch` 非空（>0 字节）且时序单调（patch 比 `02-script.md` 新）。
  任一不满足 → 解封物视为**失效**，对象保持 PENDING（状态卡 advisory 已有同款提示，行为一致）。**红队 B1 场景闭环**：重跑 clips 后旧 approved 段级 diff 非空 + mtime 早于新 clips，双闸同时拦下，不存在「旧 approved 代批新排片」的路径。
- **红线区分（维持 v0.1，红队未推翻）**：解封物对齐不是「自动批准」——解封物的产生本身已是人的显式终端动作（`review --approve` 要人敲且段级不变量不过不拷（`review.py:300-325`）；`02-diff.patch` 要人手动 `git diff --no-index`），对象只是**事后对齐物理事实**。机器产出解封物的路径一条都没有，门禁 5 静态断言兜底。B1 修订不改变此定性，只补上「解封物必须对当前产物仍然有效」的校验。
- **检测时机**：全部走**读路径惰性检测**（`_self_heal` / ack 路径），不开扫描线程、不挂文件监视。读路径的写副作用（惰性状态转移落盘）经 `_locked_approvals` 全程持锁完成，失败静默（对象保持原态返回，下次读再试）——观测层纪律同 sidecar，簿记修正永不阻塞会话。

### 2.4 决策 4：rejected 结构化反馈 schema 与回写形式

- **schema**（两项均必填、非空字符串，缺一拒收）：
  ```json
  {
    "target": "哪段。自由定位串，如 \"seg-05\" / \"02-script.md 第3节\" / \"04-clips.json s07 1:20\"",
    "problem": "什么问题。人类自然语言描述"
  }
  ```
- **回写形式**：双写——
  1. 内嵌进 Approval 对象本体（`feedback` 字段，随 `approvals_store.json` 持久）；
  2. 追加一段到 **`data/episodes/<期>/_agent/approval_feedback.md`**（Markdown 小节：停机点、时间、哪段、什么问题）。
- **可读性论证（本决策的支点，行号已核实）**：`tools.py:308 READ_DENY_PARTS = frozenset({"03-audio", "04-patch"})` 不含 `_agent`；`tools.py:309 READ_ALLOWED_SUFFIXES` 含 `.md`；期目录本身是 `_allowed_read_roots`（`tools.py:458-466`）的第一读根。因此后续 agent 会话的 `read_artifact("_agent/approval_feedback.md")`（`tools.py:482-526`）**在现行读域规则下天然可读，零改动**。（红队一轮独立复核确认此链路成立，但指出可发现性缺口，见 §2.8。）
- **有意的不对称（写死，防误判为疏漏）**：`approvals.jsonl` 维持「不进读域」现状（`.jsonl` 不在 READ_ALLOWED_SUFFIXES，`status_card.py:293` 注释已声明这是有意的——按键耗时是审批疲劳判据的观测原料，不给模型看）；**进读域的只有驳回意见本身**。人审元数据与驳回内容的可见性分开，是设计不是漏洞。
- **与单线程写产物纪律的关系**：反馈只写 `_agent/`（harness 簿记区），**永不写流水线产物本体**（不碰 `02-script.md` / `04-clips.json`）；写点只存在于交互主线程的 ack 路径（REPL/裸形态命令处理函数），read-only 子 agent（ADR-0020 §3）拿不到写通道，无并行写冲突面。

### 2.5 决策 5：与 `approvals.jsonl` 既有记账的关系——维持双轨、单写入点、零迁移

- **现状证据**：`status_card.py:282-315 log_approval_decision` 是审批记账的唯一入口，三个拒执出口（LLM 卡片拒执 `cli.py:636`、REPL EOFError `cli.py:1044`、REPL 按 n `cli.py:1049-1051`）全部经它落 `_agent/approvals.jsonl`；无期会话在 `status_card.py:296-297` 早退跳过。
- **决策**：
  1. **维持双轨，不去重单写**。`approvals.jsonl` 是 y/n 决策流水账（审批疲劳判据的唯一数据源），`approvals_store.json` 是对象状态库。二者语义不重叠，合并反而把两种生命周期塞进一个文件；
  2. **单写入点**：Spec 3 新增的 ack 路径（`/approve` `/reject`）**内部调用 `log_approval_decision`** 完成记账，不另开写 `approvals.jsonl` 的旁路；对象库更新与事件发射在同一个 ack 函数内顺序完成（同源三写）；
  3. **零迁移**：`approvals.jsonl` 历史行没有 pending 语义，无可迁内容；`approvals_store.json` 从空起步，pending 由 §2.2 的自愈创建自然产生。

### 2.6 决策 6：四种停机点的 ack 语义分型——09 纯记录，02.5/05 解封物联动，03.5 建议型（红队一轮 M5 修订）

| 停机点 | 类型枚举 | 物理闸门（现状，行号已核实） | ack 语义 | ack 副作用 |
|---|---|---|---|---|
| 02.5 人审改稿 | `"02.5"` | `02-diff.patch` 存在性（`status.py:284-297`；runbook 02.5 封板操作） | **解封物联动型**：`/approve 02.5` 仅当 `02-diff.patch` 已存在且通过 §2.3 失效判据时落 `approved`（补登记）；否则拒绝并提示封板命令 | 纯记录，不产任何文件 |
| 03.5 配音顺听 | `"03.5"` | 无（建议型停机点，机器语义不阻塞，`status.py:312-326` 注释写死） | **纯记录型**：`/approve 03.5` 落 `approved` 即「人已顺听」的自证 | 纯记录；纠错仍走 `/voice`（`cli.py:108` 起） |
| 05 审时间码 | `"05"` | `04-clips.approved.json`（`status.py:329-341`；`review.py:300-325 approve` 是显式动作 + 段级不变量第一道闸） | **解封物联动型**：`/approve 05` 仅当 `04-clips.approved.json` 已存在且通过 §2.3 失效判据（时序单调 + `has_clips_approved_diff` 为空）时落 `approved`；否则拒绝并提示 `python -m pipeline.review <期> --approve` | 纯记录；解封物只能由 review --approve 产出，对象层永不代产 |
| 09 人工发布 | `"09"` | 无（`status.py:398-409`，`next_command=None`；runbook 09「坚决不做自动上传」） | **纯记录型**：`/approve 09` 落 `approved` 即本期闭环记账 | **零副作用**，不触发任何上传/打包/状态推进 |

- **统一铁律（防双头的核心）**：**对象状态永不替代物理闸门。** 即使 05 的对象已是 `approved`，`04-clips.approved.json` 缺失或过期时渲染器照样拒启（`render.py:1067-1075` 段级 diff 硬闸）；反之解封物存在且有效而对象 pending 时，§2.3 惰性对齐。对象层是「队列与观测」，物理产物是「闸门」，两者答案永远一致且以物理产物为准。
- **03.5 reject 的重修驱动（M5 修订）**：reject 03.5 **维持建议型语义，不新增物理闸门**（与 `status.py:312-326` 注释、RF-7 一致）——下游 clips 在机器语义上仍可执行，这是现状不是漏洞。驳回意见的三条生效通道：① `_agent/approval_feedback.md` 进读域；② 状态卡引导行（§2.8）让 agent 会话必然看见反馈存在；③ `/voice` 纠错通道（`corrections.json` 期级 overlay）是既有物理重修出口。驳回后同指纹不重建 pending（§2.2 终态抑制），人/agent 重配音频改变 manifest（指纹漂移）后才重新询问。若未来要把 03.5 升级为强制闸，另立 ADR，不在本 spec。
- **09 与 02.5/03.5/05 的本质差异（正面回答预审问题 5）**：09 的 ack **不触发任何副作用**，纯闭环记录——它身后没有机器工序可解封（`status.py:402-408`，`next_command=None`），runbook 09 明令「坚决不做自动上传」。若未来要加「发布登记」副作用，必须另立 ADR，不在本 spec 范围内。

### 2.7 决策 7：拒执观测发射点语义原样保留

- Spec 2 v0.4 红队四轮钉死条件：拒执 `APPROVAL_RESOLVED` 双写的 emit 必须置于 `status_card.py:296-297` 的 `if not ep_dir: return` 早退**之前**（否则无期会话拒执依旧黑洞）。该改造属于 Spec 2 PR4 施工单。
- **本 spec 承诺**：Spec 3 施工不移动、不删除、不绕过该 emit 位置；`/approve` `/reject` 新路径的记账仍调用 `log_approval_decision`（§2.5），事件发射经 `jobs.get_publisher().emit()` 函数级延迟 import（§4.1），三写同源。PR4 回归测试（T12）覆盖「emit 在早退之前」语义无回归。

### 2.8 决策 8：状态卡确定性引导行——解决反馈可发现性（红队一轮 M4 新增）

- **问题**：反馈文件 `_agent/approval_feedback.md` 虽在读域内（§2.4），但模型无法凭空知道它存在——核心验收 2「rejected 的 feedback 能被后续 agent 会话读到」在「可读但不可知」状态下事实不可达。
- **决策**：`build_status_card`（`status_card.py:65-118`）追加**确定性引导行**（函数级延迟 import `pipeline.approvals`，读失败静默不炸卡片）：
  - 存在 pending 时追加：`待审批: 05 审时间码（/approvals 查看详情）`（多个并列，类型 + 工序名）；
  - 存在未被新 pending 覆盖的 REJECTED 反馈时追加：`驳回反馈: _agent/approval_feedback.md（read_artifact 可读）`。
- **边界**：引导行只陈述确定性事实（对象库里的类型与文件路径），**不含任何 LLM 推荐/预判断**（direction §5 排除项）；字符预算维持 ≤400 字符目标，引导行超预算时截断类型清单（保留「待审批: N 项」计数兜底）。

---

## 3. 数据契约（Data Contracts）

### 3.1 Approval Schema 定义

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


ApprovalType = Literal["02.5", "03.5", "05", "09"]  # 与 cli.py:30 HUMAN_STOPS 对齐，不另造枚举


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ApprovalError(RuntimeError):
    """approval 领域受控异常（红队一轮 m3）：交互层捕获后打印受控提示，
    严禁原生 ValueError 裸抛穿透 REPL。"""


@dataclass
class ArtifactFingerprint:
    path: str            # 期目录相对路径，如 "04-clips.json"
    size: int
    mtime_ns: int


@dataclass
class Approval:
    approval_id: str                     # 格式：appr_<epoch_ms>_<hex4>
    episode: str                         # 期相对路径（如 "EGOIST/01-Live"），算法见 §4.1 _episode_repr
    type: ApprovalType                   # 停机点类型，四值封闭集合
    status: ApprovalStatus = ApprovalStatus.PENDING
    artifacts: list[ArtifactFingerprint] = field(default_factory=list)  # 创建时钉住的关联产物指纹
    options: list[str] = field(default_factory=lambda: ["approve", "reject"])
    created_at: str = ""                 # ISO 8601 UTC，Z 标记（同 Spec 2 _utc_now_iso 口径）
    resolved_at: str | None = None
    resolved_by: str | None = None       # "repl" | "cli" | "artifact"（惰性对齐）
    confirmed_by: str | None = None      # v0.4 S3-R7：artifact 对齐后人的显式确认来源（"repl" | "cli"）
    confirmed_at: str | None = None      # v0.4 S3-R7：显式确认时刻（ISO 8601 UTC，Z 标记）
    feedback: dict[str, str] | None = None  # rejected 时必填：{"target": ..., "problem": ...}
    note: str = ""                       # 05 携带 review --approve 提示等
```

**不变量**：
- `status == REJECTED` ⟺ `feedback` 非空且 `target`/`problem` 均为非空字符串；
- `status == APPROVED` 且 `type ∈ {"02.5", "05"}` ⟹ 解封物在 resolve 时刻物理存在**且通过 §2.3 失效判据**（时序单调 + 内容一致）；
- 终态（`APPROVED | REJECTED | SUPERSEDED`）不可**异决策**转移（二次转移抛 `ApprovalError`）；**同决策重复 ack 幂等**——返回既有终态对象不抛错（红队一轮 m3）；
- 同 `(episode, type)` 至多一条 PENDING；同 `(episode, type, 指纹)` 存在 APPROVED/REJECTED 终态时无新建（§2.2 终态抑制）；
- `confirmed_by` 非空 ⟹ `status == APPROVED` 且 `resolved_by == "artifact"`（v0.4 S3-R7：确认只追加、不改写对齐事实）。

### 3.2 `approvals_store.json` 物理文件格式（红队一轮 m4 更名）

- **路径**：`data/episodes/<期>/_agent/approvals_store.json`；锁文件 `_agent/approvals_store.lock`（flock 载体，append 模式打开、内容为空）。
- **格式**：UTF-8 JSON 数组，元素为 Approval 的 `to_dict()`；整文件覆写走 `paths.atomic_write`（`paths.py:118-128`），indent=2 便于人读与 diff。
- **容量上限与淘汰阶梯（红队一轮 M7 修订）**：对象数量封顶 64 条。超出时按阶梯淘汰最旧对象：**SUPERSEDED → REJECTED → APPROVED**；**PENDING 永不进淘汰**——极端情形（64 条全 PENDING，病理状态）下拒绝新建并打印 WARN，宁可暴露问题也不静默丢悬而未决。
- **不进 git**：`data/` 整树本就不进 git（与 events.jsonl 同层纪律）。

### 3.3 事件载荷契约（复用 Spec 2 已冻结枚举，不新造）

`EventType.APPROVAL_REQUESTED` / `EventType.APPROVAL_RESOLVED` 枚举已在 Spec 2 §3.2 预留，本 spec 只定义 payload，**严禁新造重复枚举**。

1. **`approval_requested` 载荷**（新建对象时发射一次）：
   ```json
   {
     "event_id": "evt_1790089300000_0a1b",
     "episode": "EGOIST/01-Live",
     "timestamp": "2026-09-22T15:10:00.000000Z",
     "type": "approval_requested",
     "payload": {
       "approval_id": "appr_1790089300000_c3d4",
       "stop": "05",
       "artifacts": [{"path": "04-clips.json", "size": 48213, "mtime_ns": 1790089200000000000}],
       "options": ["approve", "reject"],
       "note": "批准须先显式执行 python -m pipeline.review <期> --approve"
     }
   }
   ```

2. **`approval_resolved` 载荷**（ack 或惰性对齐时发射；向后兼容 Spec 2 §3.3 示例 5 的既有字段 `command/decision/source`，新增字段为扩展不冲突）：
   ```json
   {
     "event_id": "evt_1790089360000_0e5f",
     "episode": "EGOIST/01-Live",
     "timestamp": "2026-09-22T15:11:00.000000Z",
     "type": "approval_resolved",
     "payload": {
       "approval_id": "appr_1790089300000_c3d4",
       "stop": "05",
       "decision": "rejected",
       "source": "repl",
       "feedback": {"target": "04-clips.json s07", "problem": "台词在说话但画面切给了路人"},
       "command": "review --approve",
       "latency_s": 312.4
     }
   }
   ```
   `decision` 允许值：`"approved" | "rejected" | "superseded"`；`source` 允许值：`"repl" | "cli" | "artifact"`。Spec 2 既有发射点（无 approval_id 的卡片拒执）维持原 payload 形状，消费者按 `approval_id` 是否存在区分新旧来源。

---

## 4. 模块接口与签名设计

### 4.1 `pipeline/approvals.py` 核心接口（红队一轮 B2/M1/M6/m3 修订）

```python
"""pipeline.approvals: 停机点 pending approval 对象化（Spec 3 / ADR-0020 §3）。

纪律：
1. ack 永远是显式动作；本模块没有任何自动批准路径（解封物对齐是事后登记，
   且必须过 §2.3 失效判据双闸，见红队一轮 B1）；
2. 对象状态永不替代物理产物闸门（02-diff.patch / 04-clips.approved.json）；
3. 绝不自动 mkdir 期目录；期目录不可达时全部 fail-silent（paths.py:131-158 铁律）；
4. 顶层仅 stdlib + pipeline.paths + pipeline.align（纯 stdlib 叶子模块，行 14-19 核实）；
   jobs / status / status_card 一律函数级延迟 import。
"""

# 停机点类型 → 关联产物（指纹对象）与解封物
_STOP_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "02.5": ("02-script.md",),
    "03.5": ("03-audio/manifest.json",),
    "05": ("04-clips.json",),
    "09": ("05-final.mp4", "07-titles.md"),
}
_STOP_GATE: dict[str, str | None] = {
    "02.5": "02-diff.patch",
    "03.5": None,
    "05": "04-clips.approved.json",
    "09": None,
}
_MAX_APPROVALS_PER_EPISODE = 64


def ensure_pending(ep_dir: Path, status: "EpisodeStatus | None" = None) -> Approval | None:
    """幂等补建停机点 pending（红队一轮 B2：status 可选，缺省自愈现算）。

    1. status 为 None 时函数级延迟 import inspect_episode 现算；
    2. 命中停机点且无同 (类型, 指纹) 对象时创建并发射 APPROVAL_REQUESTED；
    3. 终态抑制（B3）：同 (类型, 指纹) 存在 APPROVED/REJECTED 终态时严禁新建；
    4. 同型旧 PENDING 在新创建时转 SUPERSEDED（§2.3 条件 1）。
    无期/非停机点返回 None；期目录不可达 fail-silent，绝不 mkdir。"""


def list_pending(ep_dir: Path) -> list[Approval]:
    """读路径统一入口：先 _self_heal(ep_dir)（补建 + §2.3 惰性检测：
    指纹漂移转 SUPERSEDED、解封物过双闸转 APPROVED(source="artifact")），
    再返回 PENDING 对象。期目录不可达返回 []。"""


def approve(ep_dir: Path, stop: ApprovalType, *, approval_id: str | None = None,
            source: str = "repl") -> Approval:
    """显式 ack 通过。判定顺序冻结（v0.4 S3-R6/R7，全程在 _locked_approvals 内）：
    1. 进入 _locked_approvals 一次（v0.5 S3-R10：此后全程不再进锁）；
       transitions = _self_heal_locked(store, ep_dir)（返回本次调用发生的状态转移清单）；
    2. 定位目标对象：
       - 给了 approval_id：按 id 取；不存在、type != stop → ApprovalError；
         状态为 SUPERSEDED → ApprovalError「对象已被替换（关联产物已变更），请重新审阅」，
         **绝不转而处理同类型的新 pending（替身）**；为 REJECTED → ApprovalError（异决策）；
       - 未给 id（仅 REPL）：transitions 中若有同类型对象刚转 SUPERSEDED → ApprovalError
         「本次操作前对象已被替换，请先 /approvals 查看」；否则取同类型 PENDING，
         没有则取同类型「APPROVED 且 resolved_by == "artifact"」中最新一条，仍没有 → ApprovalError；
    3. 目标为 PENDING：指纹漂移 → 转 SUPERSEDED 并 ApprovalError；解封物联动型（02.5/05）
       解封物缺失或过 §2.3 失效判据 → ApprovalError 并提示正确命令（对象层永不代产解封物，§2.6）；
       通过则转 APPROVED(resolved_by=source)，同源三写：对象库 + log_approval_decision + APPROVAL_RESOLVED；
    4. 目标为 APPROVED 且 resolved_by == "artifact" 且 confirmed_by 为空（确认路径，S3-R7）：
       写 confirmed_by=source、confirmed_at=now；log_approval_decision(decision="y",
       latency_s = now − created_at 当且仅当该对象的 artifact 对齐出现在本次 transitions 中，
       否则 latency_s=None（v0.5 S3-R11：对齐发生在更早的调用里时，决策时刻未知，不记延迟））；
       发 APPROVAL_RESOLVED（载荷加 "confirms": "artifact"，latency_s 同上）；
    5. 其余 APPROVED（已显式批准或已确认）：同决策幂等，原样返回，不重复记账（m3）。"""


def reject(ep_dir: Path, stop: ApprovalType, feedback: dict[str, str],
           *, approval_id: str | None = None, source: str = "repl") -> Approval:
    """显式 ack 打回。feedback 必须含非空 target/problem（§2.4 schema，缺一
    ApprovalError）。目标定位规则同 approve 第 1–2 步（v0.4 S3-R6：给了 id 就只认该对象，
    SUPERSEDED/APPROVED → ApprovalError）。转 REJECTED 终态，同源三写 + 追加
    _agent/approval_feedback.md。打回不删产物、不推进工序——重修仍由物理产物状态驱动（03.5 见 §2.6 M5）。"""


def get(approval_id: str, ep_dir: Path) -> Approval | None:
    """按 id 取对象（含终态，供桌面端/测试核对）。"""
```

内部实现规格（写死给施工者）：
- **`_locked_approvals(ep_dir)` 上下文管理器（M1）**：模块级 `threading.Lock` → `_agent/approvals_store.lock` append 打开 → `fcntl.flock(LOCK_EX)` → 读文件 → yield 可变列表 → 区块内标记变更则 `paths.atomic_write` 覆写 → 释放。所有公共操作（含 `list_pending` 的惰性状态转移）**全程在此区块内完成**，不存在无锁读-改-写路径。**一次进锁纪律（v0.5 S3-R10）**：`threading.Lock` 不可重入，且同进程对同一锁文件新开 fd 再 `flock(LOCK_EX)` 会阻塞（实测 `LOCK_NB` 返回 EWOULDBLOCK）——因此每个公共函数恰进锁一次，区块内部只调用 `*_locked` 形态的辅助函数，任何辅助函数都不得再调用 `_locked_approvals` 或任何公共函数；
- **`_self_heal_locked(store, ep_dir) -> list[Transition]`**（v0.5 S3-R10 取代 v0.4 的 `_self_heal(ep_dir)`）：在调用方已持有锁的 `store` 列表上执行 `_ensure_pending_locked(store, ep_dir, status)`（status 缺省时函数级延迟 import `inspect_episode` 现算）+ §2.3 惰性检测，返回本次发生的状态转移清单；**本身不进锁**。惰性转移失败静默（返回当前态）；公共 `ensure_pending` 只是 `with _locked_approvals(ep_dir) as store: _ensure_pending_locked(...)` 的薄包装；
- **`_episode_repr(ep_dir)` 算法（M6）**：镜像 Spec 2 emit 的真子集判断——`_episodes_root` 模块级惰性 resolve 缓存一次（`paths.ROOT/"data"/"episodes"`），`if ep != root and root in ep.resolve().parents: relative_to(root).as_posix()`，否则降级 `ep_dir.name`；任何异常降级 `ep_dir.name`。pytest tmp_path 下的期目录必然落在降级分支，稳定不炸；
- `_fingerprint(ep_dir, rel_paths)`：读 `stat().st_size` 与 `st_mtime_ns`，文件缺失记 `size=-1, mtime_ns=-1`（缺失也是指纹，区别于 0 字节文件）；
- `_gate_valid(ep_dir, stop) -> bool`（§2.3 双闸）：解封物存在 ∧ `mtime_ns` 不早于全部关联产物 ∧ 内容一致（05 调 `align.has_clips_approved_diff` 取反；02.5 检查非空）；
- `_emit(event_type, payload, episode_dir)`：**函数级延迟 import** `from pipeline.jobs import get_publisher, EventType`，`try/except ImportError` 静默降级——jobs 层未施工时对象库照常工作（见 §6.1）；
- ack 记账复用 `status_card.log_approval_decision`（函数级延迟 import，避免 status_card → tools → approvals 潜在环），`decision` 参数传 `"y"`/`"n"` 维持其现行归一化语义（`status_card.py:303`）。

### 4.2 CLI 表面（ack 的显式动作入口）

- **REPL**（`cli.py:_run_repl_body`，帮助文本挂 `cli.py:934` 附近既有 /help 清单）：
  - `/approvals`：列出当期 pending（类型、创建时间、关联产物、可选项）；
  - `/approve <stop>`：`stop ∈ {02.5, 03.5, 05, 09}`，调 `approvals.approve`；`ApprovalError` 捕获后原样打印受控提示（缺解封物给提示命令），**不裸抛栈**；
  - `/approve <stop> [--id <approval_id>]`、`/reject <stop> [--id <approval_id>] <哪段> <问题…>`：REPL 中 `--id` 可选（v0.4 S3-R6，不带时按 §4.1 `approve` 第 2 步的 REPL 规则定位）；
  - `/reject` 整行经 `shlex.split` 解析（v0.3，S3-R1）：去掉可选的 `--id <approval_id>` 后，第一个 token 为 stop，第二个为 target，其余 token 以单空格连接为 problem；target 含空格时用引号，如 `/reject 05 "04-clips.json s07 1:20" 台词在说话但画面切给了路人`；引号不配对（`shlex` 抛 `ValueError`）或缺参数 → 打印用法拒绝，不裸抛栈。
- **裸形态**（`cli.py:1169` 起的子命令分发）：`ava <期> /approvals`、`ava <期> /approve <stop> --id <approval_id>`、`ava <期> /reject <stop> --id <approval_id> <哪段> <问题…>`——这是 Electron host spawn 的 ack 通道（ADR-0020 §4 host spawn 子进程模型；消费方契约见 Spec 8 §3.4）。**v0.3 冻结下列三条（S3-R1/R2/R5）**：
  1. **参数按 argv 位置取，无损（S3-R1；v0.4 S3-R6 加入必填 `--id`）**：这三个子命令的分派必须在 `cli.py:1170` 的 `sub_cmd = " ".join(args[1:])` **之前**，直接读 `args` 列表，**固定位置、不用 argparse**（problem 可能以 `-` 开头，argparse 会把它当选项）：`args[1]` 为子命令名，`args[2]` 为 stop；`/approve` 要求 `args[3] == "--id"`、`args[4]` 为 approval_id 且恰好 5 个元素；`/reject` 要求 `args[3] == "--id"`、`args[4]` 为 approval_id、`args[5]` 为 target（整个 argv 元素，内部空格、引号、换行、前导 `-` 原样保留）、`args[6:]` 以单空格连接为 problem。缺 `--id` 或位置不符 → 退出 2。调用方（桌面端 host）把 target、problem 各作为一个 argv 元素传入，core 端逐字节落库；
  2. **退出码契约（S3-R2）**：

     | 退出码 | 含义 | 输出 |
     |---|---|---|
     | 0 | 对象已转移到目标态；或 artifact 对齐后的显式确认（v0.4 S3-R7）；或同决策重复 ack 幂等返回（§3.1 不变量）；`/approvals` 自愈并列出成功 | stdout：人读结果 |
     | 1 | `ApprovalError`（无 pending、指纹漂移转 superseded、**指定 id 的对象已被替换或不存在**（v0.4）、解封物缺失/过期、异决策二次转移、feedback 缺字段） | stderr：一行受控提示（缺解封物时含正确命令），**不打印栈** |
     | 2 | 用法错误（缺参数、stop 不在 `{02.5, 03.5, 05, 09}`） | stderr：用法说明 |

     期目录不存在沿用 `cli.py:1164-1166` 既有行为（退出码 1，`[ERROR] 目标期目录不存在`），不另立；
  3. **非 TTY 不落入 REPL（S3-R5 收窄版）**：`main()` 走到末尾的 `return run_repl(ep_dir)`（`cli.py:1199`）之前，若 `len(args) > 1`（带了子命令却无分支命中）且 `not sys.stdin.isatty()`，则 stderr 打印 `[ERROR] 未识别的子命令：<args[1:]>（非交互环境不进入 REPL）` 并返回 2。TTY 下行为不变——`docs/WORKFLOW.md` 文档化的 `ava <期> /chat`、`ava <期> /script` 继续靠落入 REPL 工作。**无子命令的 `ava <期>` 在非 TTY 下的行为本 spec 不改**（不属于 ack 通道，超出范围）。
- **REPL 接线点（新鲜度优化，非唯一创建点）**：`cli.py:892` 每轮 `status = inspect_episode(ep_dir)` 之后插一行 `approvals.ensure_pending(ep_dir, status)`（函数级延迟 import，异常静默不炸 REPL——approval 簿记永不阻塞主会话，同 sidecar 纪律）。

### 4.3 状态卡引导行接线（§2.8 施工面）

- `status_card.py:65-118 build_status_card` 尾部（advisories 组装之后、脱敏清洗之前）追加引导行组装：函数级延迟 import `pipeline.approvals`，`list_pending` 取类型清单、`get` 查未覆盖 REJECTED；**任何异常静默跳过**（卡片是每轮热路径，簿记故障不许炸系统提示组装）；
- 引导行参与既有 `RESTRICTED_EGRESS_PATTERNS` 清洗（`status_card.py:112-116`），不新增脱敏面。

### 4.4 Electron 桌面端预留契约（只预留，不施工）

- Approval `to_dict()` 的 JSON 形状（§3.1）即 UI 契约；桌面端 renderer 的 Approval 决策条（Approve / Reject 携带 freeText，ADR-0020 §4）消费 `approval_requested` 事件 + `approvals_store.json` snapshot 首载（与 events.jsonl 的 tail+snapshot 模式同构）；
- 桌面端 ack **不直写对象库**，经 host spawn `ava <期> /approve|/reject <stop> --id <approval_id> ...` 完成——写路径只有一个（本模块），UI 是「读取 + 转发显式人令」；带 id 保证人点的就是人看过的那个对象（v0.4 S3-R6）；
- 本 spec 冻结 schema 与 ack 子命令；Electron 本体属 Spec 8，这里一行 UI 代码不写。

---

### 4.5 `review --approve` 的期望指纹参数（v0.5 S3-R9，改 `pipeline/review.py`，经用户授权）

- **CLI**：`python -m pipeline.review <期> --approve [--confirm-patch] [--expect-size=<N> --expect-mtime-ns=<M>]`。两个期望参数必须同时给或同时不给（只给一个 → 用法错误退出 2）。经 `ava <期> /run review --approve --expect-size=<N> --expect-mtime-ns=<M>` 调用时**必须用等号形式**（`tools.py:126-151` 的 `valued_flags` 不含这两个旗标，空格形式会让值被当成位置参数，`validate_pipeline_command` 随之不再注入期目录）。本 spec 不修改 `tools.py`。
- **`approve(episode, confirm_patch=False, expect=None)` 行为**（`expect: tuple[int, int] | None`）：
  1. `expect is None`：与现状逐字节一致（`review.py:300-340`，末尾 `shutil.copy2`），零回归；
  2. `expect` 给定：以 `open(src, "rb")` **只打开一次**，`os.fstat(fd)` 取 (size, mtime_ns)，与 `expect` 不等 → `SystemExit("FAIL 04-clips.json 已不是审阅时的版本（期望 size=…, mtime_ns=…；实际 …），未写批准文件")`，**不写任何文件**；
  3. 相等则经 `_read_all(f)` 从该 fd 读出全部字节，**读完后再 `os.fstat(fd)` 一次**：(size, mtime_ns) 须与第 2 步相同，且 `len(bytes) == size`；任一不成立 → `SystemExit("FAIL 04-clips.json 在读取期间被改写，未写批准文件")`，不写任何文件（v0.6，S3-R9a：挡住原地覆盖写——同一 inode 上 fstat 见 F1、read 得 F2 的情形）。此后段级校验（`verify_alignment`）与补丁段确认闸都基于这份字节解析出的数据；
  4. 写出：同目录 tmp 文件写入这份字节 → `os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))`（st 取自第 2 步的 fstat）→ `os.replace(tmp, dest)`。结果与 `copy2` 的内容与 mtime 语义一致，但**内容一定是被核对过的那一版**；
  5. 第二次 fstat 之后源文件即使被替换或原地改写为 F2，解封物仍是 F1：§2.3 双闸（`has_clips_approved_diff`）与 `render.py` 段级 diff 硬闸都会判它对当前 F2 失效，闸门保持关闭——不会出现「为未审版本打开」。
- **不做的事**：不改 `copy2` 路径的既有行为；不在 review 里读取 approval 对象（review 不知道 approval 的存在，期望值由调用方从对象钉住的指纹传入）。

## 5. 依赖白名单与纯洁性保障

### 5.1 模块级依赖白名单（`pipeline/approvals.py` 顶层）

- 标准库：`contextlib`, `dataclasses`, `datetime`, `enum`, `fcntl`, `json`, `pathlib`, `threading`, `time`, `typing`, `uuid`
- 项目轻量库：`from pipeline import paths`、`from pipeline import align`（纯 stdlib 叶子模块，顶层 import 仅 `hashlib/json/re/pathlib`，`align.py:14-19` 核实；`review.py:31` 注释亦确认其「不背 clips 的 ML 依赖」）
- **函数级延迟 import**（不进顶层）：`pipeline.jobs`（事件发射）、`pipeline.agent.status_card`（记账复用）、`pipeline.status`（自愈现算与类型注解）

**严禁顶层导入**：`numpy` / `torch` / `mlx_whisper` / `moviepy` / `cv2` / `transformers`（同 Spec 2 §5.1 清单）。

### 5.2 独立子进程纯洁性测试

与 Spec 2 §5.2 同款：全新解释器探针 `import pipeline.approvals` 后断言 `sys.modules` 无重包（防共享 pytest 进程假阳性旁路）。见 §7.1 T11。

---

## 6. 跨 Spec 接口与系统边界

### 6.1 与 Spec 2（jobs 层与 events.jsonl）的接口边界

- **契约复用，零新造**：`EventType.APPROVAL_REQUESTED / APPROVAL_RESOLVED` 枚举、`_utc_now_iso()` Z 标记时间戳口径、`event_id` 格式、`episode` 相对路径口径（含嵌套子期与 `_global`，本 spec `_episode_repr` 镜像同一算法），全部以 Spec 2 §3.2/§3.3 已冻结契约为准；
- **施工依赖声明（必须如实）**：截至本 spec 撰写日（2026-09-22），**`pipeline/jobs.py` 尚未施工**（Spec 2 v0.4 状态「可动工」但代码未落地，`pipeline/` 下无 jobs.py——红队一轮独立复核确认）。因此：
  - 本 spec 引用的 jobs 层签名（`get_publisher().emit(event_type, payload, *, episode_dir=None)`、`EventType` 枚举）**以 Spec 2 v0.4 §4.1 文档为准**；
  - `approvals._emit()` 对 `pipeline.jobs` 做函数级延迟 import + `ImportError` 静默降级：**jobs 未施工时 Spec 3 的对象库、恢复、ack、反馈回写全部照常工作**，仅事件镜像缺席；jobs 施工完成后镜像自动生效，无需改码；
  - 降级期的事件缺席**不是隐形完成**：门禁 10 以显式 caveat 形态把「jobs 施工后回看 T10a 转绿」写成验收义务（红队一轮 M3）；
- **Job 注册表归属不变**：枚举未决 Job 走 `jobs.list_pending()` / `jobs.get_job()`（Spec 2 §6.2 已写死）；approval 对象是**独立的概念**（停机点人审，不是流水线命令执行），不进 Job 注册表，也不自造第三套索引。05 的 `review --approve` 执行本身是 Job（走 jobs 层），它产出的解封物是 approval 对象惰性对齐的依据——两层各管各的，接口就是物理产物；
- **发射点红线复述**：Spec 2 PR4 的 `log_approval_decision` emit 置于 `status_card.py:296-297` 早退之前，Spec 3 不动此位置（§2.7）。

### 6.2 与 Spec 1（上下文装配器）的接口边界（红队一轮 M4 修订）

- 装配器不读 `approvals_store.json`，审批队列**不**经装配器进系统提示；
- rejected 反馈的可发现性走**状态卡通道**（§2.8 引导行），内容本体仍走工具层按需读（`read_artifact("_agent/approval_feedback.md")`）——装配器三层架构零改动；
- `build_status_card` 的引导行是确定性事实回显（类型清单 + 文件指针），不含 LLM 推荐（direction §5 排除项）。

### 6.3 与 Spec 8（Electron 桌面端）的接口边界

见 §4.4：本期冻结 schema + 裸形态 ack 子命令，UI 零施工。桌面端只投影 approval 对象与事件，不直连对象库写路径。v0.3 起裸形态的参数取法、退出码与非 TTY 行为由 §4.2 三条冻结，Spec 8 §3.4 的 spawn 模板与 §4.3 的退出码分流以此为准；Spec 8 另有能力探针与后置核验（Spec 8 §2.6），不以退出码作为唯一成功判据。

### 6.4 与 pipeline.status 的边界（铁律复述）

`status.py` 对本模块零感知：不 import、不读 `approvals_store.json`、不读 `events.jsonl`。门禁 4 静态断言兜底。反向地，本模块只**消费** `EpisodeStatus`（自愈现算或 `ensure_pending` 入参），绝不向 `status.py` 注入任何状态来源。

---

## 7. 测试规格与变异检验方案

### 7.1 单元测试规格（`tests/test_approvals.py`）

> **继承 Spec 2 同步点铁律**：凡读 `events.jsonl` 物理文件的断言（T10a），先注入独立 publisher 并显式 `publisher.close()` 再读；conftest autouse fixture 经 `AVA_EVENTS_ROOT` 定向 `tmp_path`，严禁污染真实审计日志（Spec 2 红队三轮 B2/B3）。

| 编号 | 测试用例名 | 覆盖场景 | 验证断言 |
|---|---|---|---|
| **T1** | `test_approval_state_machine` | 四状态合法流转与终态纪律 | pending→approved/rejected/superseded 合法；异决策二次转移抛 `ApprovalError`；**同决策重复 ack 幂等返回既有对象不抛错**（m3）；rejected 缺 feedback 任一字段抛 `ApprovalError` |
| **T2** | `test_pending_survives_process_restart` | **核心验收 1**：跨进程恢复 | 进程 A 创建 pending 落盘；全新子进程 `list_pending` 读回同一对象（id/类型/指纹一致）并可完成 ack；ack 后再开子进程读到终态。**子场景（M2 补入正文）**：构造「store 有对象、events 被 drop（不注入 publisher / 阻断 jobs import）」，恢复断言仍成立——证明恢复路径与事件层零耦合 |
| **T3** | `test_ensure_pending_idempotent_upsert` | 幂等 upsert + **终态抑制（B3）** | 同 status 重复调 10 次恰 1 条 pending、REQUESTED 恰 1 次；**reject 后不改产物（指纹不变）再刷新 10 次：零新建、零 REQUESTED**；改动产物（指纹漂移）后刷新：新建 1 条 pending，旧 REJECTED 保持终态不复活 |
| **T4** | `test_fingerprint_drift_supersedes` | 指纹漂移惰性检测（§2.3 条件 2；v0.4 按 §2.2 改正） | 05 停机点创建 pending A 后改写 `04-clips.json`（换内容变 size，仍处停机点）：`approve(05, approval_id=A)` 抛 `ApprovalError` 且 A 为 `SUPERSEDED`；`list_pending` 恰返回一条新 pending B（指纹为新值、id ≠ A）；**B 未被批准**；REPL 形态 `approve(05)`（不带 id）在「本次自愈刚替换」时同样抛 `ApprovalError` |
| **T5** | `test_gate_artifact_lazy_alignment` | 解封物对齐双闸（§2.3，**B1 增强**） | ① 正常路径：造 05 pending 后落**有效** approved（mtime ≥ clips 且段级 diff 为空）→ 惰性转 `APPROVED(resolved_by="artifact")`；② **过期路径**：落 approved 后重跑 clips 等价物（改写 `04-clips.json` 使段级 diff 非空）→ 对象**保持 PENDING**；③ 时序违例：手工把 approved mtime 调到早于 clips → 保持 PENDING |
| **T6** | `test_reject_feedback_readable_by_agent` | **核心验收 2**：反馈回写可读 | `reject()` 后 `_agent/approval_feedback.md` 存在且含 target/problem 原文；经 `tools._tool_read_artifact` 构造 ToolContext 读取成功（读域断言，不是裸 open）；**产物哈希快照断言（M2 补入正文）**：reject 前后 `02-script.md` / `04-clips.json` 字节级不变 |
| **T7** | `test_no_approve_without_gate_artifact` | 解封物联动型 ack 纪律 + 09 零副作用 | 缺 `02-diff.patch` 时 `approve(02.5)` 抛 `ApprovalError` 且 message 含封板命令提示；05 同理（含**过期 approved** 场景：存在但 diff 非空照样拒，B1）；03.5/09 无解封物要求可直接 ack；**09 ack 后期目录 diff 断言（M2 补入正文）**：除 `_agent/` 内 `approvals_store.json` 与 `approval_feedback.md`/`approvals.jsonl` 外零文件变化 |
| **T8** | `test_concurrent_ack_serialized_by_flock` | 跨进程并发写纪律（M1：断言面对准锁内读-改-写） | 主进程对 `approvals_store.lock` 持 `LOCK_EX\|LOCK_NB`，另一子进程 ack 在 2s 内无法完成（阻塞于锁）；释锁后子进程完成且 JSON 完整可解析、无双写交织；同进程双线程并发 ack 经模块锁串行化，终态恰一次转移 |
| **T9** | `test_fail_silent_when_episode_missing` | 存储红线 | 期目录不存在时 `list_pending` 返回 `[]`、`ensure_pending` 返回 None；断言 `_agent/` 目录**未被创建**（绝不 mkdir） |
| **T10a** | `test_ack_emits_event_when_jobs_present` | 事件镜像（jobs 在场） | ack 后 `publisher.close()` 排空，events.jsonl 读出 `approval_resolved` 且 payload 含 `approval_id/stop/decision/feedback` |
| **T10b** | `test_emit_degrades_when_jobs_absent` | jobs 未施工降级路径 | **经 `pytest monkeypatch` fixture**（m2：自动 teardown，严禁裸改 `sys.modules`）阻断 `pipeline.jobs` import，ack 照常成功、对象库正确落盘、零异常 |
| **T11** | `test_approvals_module_pure` | 依赖纯洁性 | 独立子进程探针：`import pipeline.approvals` 后 `sys.modules` 无 `numpy/torch/moviepy/transformers`（同 Spec 2 §5.2 机制）；附加断言 `pipeline.align` 已在白名单且其顶层同样无重包 |
| **T12** | `test_log_approval_decision_zero_regression` | Spec 2 钉死语义零回归 | 无期（`ep_dir=None`）调用 `log_approval_decision` 不建目录不写文件（早退 296-297 语义）；有期调用照常写 `approvals.jsonl`；Spec 2 PR4 落地后补断言：无期拒执的 emit 发生在早退之前（届时按 Spec 2 §8 PR4 验收） |
| **T13** | `test_status_py_zero_awareness` | 铁律静态断言 | 读 `pipeline/status.py` 源码，断言无 `approvals` / `events.jsonl` / `approvals_store` 子串（门禁 4） |
| **T14** | `test_status_card_guidance_line` | §2.8 引导行（M4） | 造 pending 后 `build_status_card` 输出含 `待审批:` 与停机点类型；reject 后输出含 `驳回反馈: _agent/approval_feedback.md`；无对象时两行均不出现；approvals 模块故障（monkeypatch 抛异常）时卡片照常返回（静默降级） |
| **T15** | `test_bare_reject_argv_lossless` | 裸形态参数无损（v0.3，S3-R1） | 子进程 `[python, -m, pipeline.agent.cli, <期>, /reject, 05, "04-clips.json s07 1:20", "第一行\n第二行 \"引号\" -x"]`（stdin=`/dev/null`）→ 退出 0；对象库 `feedback.target`、`feedback.problem` 与传入 argv 元素逐字节相等；REPL 形态 `/reject 05 "04-clips.json s07 1:20" 问题` 解析出同一 target；引号不配对 → 用法提示、零写盘 |
| **T16** | `test_bare_ack_exit_codes` | 退出码契约（v0.3，S3-R2） | 合法 approve → 0；同决策重复 → 0 且对象不变；缺解封物 approve 05 → 1，stderr 含 `review` 提示命令且不含 `Traceback`；stop 为 `06` 或 `/reject` 缺 problem → 2 |
| **T17** | `test_unknown_subcommand_non_tty_exits_2` | 非 TTY 不落入 REPL（v0.3，S3-R5） | 停在 02.5 的临时期上，stdin=`/dev/null` 运行 `ava <期> /nonexistent` → 退出 2，stderr 含「未识别的子命令」，期目录**未出现** `human_time.json`；monkeypatch `isatty` 为 True 时同一调用进入 `run_repl`（打桩断言被调用），TTY 行为无回归 |
| **T18** | `test_bare_ack_requires_id_and_never_acks_substitute` | ack 绑定对象（v0.4，S3-R6） | 裸形态 `/approve 05` 不带 `--id` → 退出 2；pending A 创建后改写 `04-clips.json` 并放入对**新版**有效的 `04-clips.approved.json`，再执行 `/approve 05 --id A` → 退出 1，stderr 含「已被替换」；A 为 SUPERSEDED；新对象 B 可以因解封物有效被惰性对齐为 `APPROVED(resolved_by="artifact")`，但 **`confirmed_by` 为空、`approvals.jsonl` 无新增行**（替身没有得到人的确认）；`/reject 05 --id A …` 同样退出 1 且 `approval_feedback.md` 未新增 |
| **T19** | `test_explicit_confirm_after_artifact_alignment` | artifact 对齐后显式确认（v0.4，S3-R7） | 05 停机点落有效 `04-clips.approved.json` → `/approvals` 使 A 对齐为 `APPROVED(resolved_by="artifact")`；`/approve 05 --id A` → 退出 0，A 的 `resolved_by` 仍为 `"artifact"`、`confirmed_by == "cli"`、`confirmed_at` 非空；`approvals.jsonl` 恰新增 1 行（`decision: "y"`，`decision_latency_s` 非空）；jobs 在场时 events 读出带 `"confirms": "artifact"` 的 `approval_resolved`；**因对齐发生在先前的 `/approvals` 调用中，该行 `decision_latency_s` 为空（v0.5 S3-R11）**；再执行一次 → 退出 0，`approvals.jsonl` 行数不变 |
| **T19b** | `test_confirm_latency_only_when_aligned_in_same_call` | 确认路径延迟语义（v0.5，S3-R11） | 放入有效 `04-clips.approved.json` 后**不先跑** `/approvals`，直接 `/approve 05 --id A` → 对齐与确认发生在同一调用，`approvals.jsonl` 新增行 `decision_latency_s` 非空且等于确认时刻 − `created_at`（容差 1 s） |
| **T20** | `test_review_approve_expect_fingerprint` | 解封物原子核验（v0.5，S3-R9） | ① `expect` 与当前 `04-clips.json` 不符 → 退出 1，`04-clips.approved.json` 不存在；② 相符 → 解封物与源字节相等、`st_mtime_ns` 相等；③ **竞态子场景**：monkeypatch `verify_alignment`，在它被调用时把 `04-clips.json` 改写为段级对齐的 F2 → 解封物字节等于 F1、`has_clips_approved_diff(ep)` 为 True（闸门对 F2 关闭）；④ 不给 `expect` 时与现有 `tests/test_review.py` 全部用例结果一致；⑤ 经 `ava <期> /run review --approve --expect-size=N --expect-mtime-ns=M` 调用时期目录被正确注入；⑥ **原地覆写子场景（v0.6，S3-R9a）**：monkeypatch `review._read_all`，在调用真实读取之前用 `Path.write_text` 把 `04-clips.json` 原地改写为**字节数相同**、段级对齐的 F2（同一 inode；字节数相同使 `len(bytes) == size` 拦不住，只有 mtime 比较能拦）→ 退出 1，`04-clips.approved.json` 不存在；变体：F2 字节数不同 → 同样退出 1、不写文件 |
| **T21** | `test_public_ops_enter_lock_once` | 一次进锁（v0.5，S3-R10） | 对处于 05 停机点的期依次调用 `list_pending`、`approve(approval_id=…)`、`reject(…)`，每次调用必须在 2 s 内返回；另以 `threading.Lock` 包装计数，断言每次公共调用 `_locked_approvals` 恰进入 1 次 |

### 7.2 变异检验矩阵（Mutation Testing Matrix）

遵循 AGENTS.md 十二节测试纪律，所有关键断言必须经受变异检验（故意写坏实现，证明测试必然变红）：

| 变异编号 | 注入变异（故意写坏代码） | 预期变红的测试 | 证伪机理（为什么必须红） |
|---|---|---|---|
| **MUT-1** | `_locked_approvals` 去掉 `fcntl.flock` | T8 | 主进程持锁不再能挡住子进程 ack：子进程 2s 内直接完成写入，「无法完成」断言失败变红 |
| **MUT-2** | `ensure_pending` 去掉同型同指纹去重判断 | T3 | 重复调用产生 ≥2 条 pending 且重复发射 REQUESTED，「恰 1 条 / 恰 1 次」计数断言失败变红 |
| **MUT-3** | 删除指纹校验（ack 前不比对 artifacts） | T4 | 指纹漂移后 `approve()` 不再抛错而是成功转 APPROVED，`ApprovalError` 与 SUPERSEDED 断言双双失败变红 |
| **MUT-4** | rejected 反馈改写 `_agent/approvals.jsonl`（`.jsonl` 后缀）或干脆不写文件 | T6 | `.jsonl` 不在 `READ_ALLOWED_SUFFIXES`（tools.py:309），`_tool_read_artifact` 抛 PermissionError；不写文件则 FileNotFoundError——读域断言失败变红 |
| **MUT-5** | `approve(02.5/05)` 去掉解封物校验 | T7 | 缺解封物时 ack 不再抛 `ApprovalError`，断言失败变红；此变异等价于「对象层代批」红线突破，T7 是第一防线 |
| **MUT-6** | 恢复路径改为从 `events.jsonl` 重建（违反决策 1） | T2（events-drop 子场景） | store 有、events 无场景下重建路径读不回 pending，T2 恢复断言失败变红。直接守卫 §2.1 决策，防施工者走观测层捷径 |
| **MUT-7** | `approve(09)` 附加副作用（写「已发布」标记文件或调外部命令） | T7（09 目录 diff 断言） | 09 ack 后出现 `_agent/` 外任何新文件，diff 断言变红——钉死「09 纯记录零副作用」（§2.6） |
| **MUT-8** | 顶层 `import numpy` | T11 | 独立子进程探针捕获 `numpy in sys.modules`，非零退出码变红 |
| **MUT-9** | `_emit` 改回顶层 `from pipeline.jobs import ...` | T10b | jobs 缺席场景下 import 即炸，ack 抛 ImportError，「照常成功」断言失败变红——守卫 Spec 2/3 施工解耦承诺 |
| **MUT-10** | rejected 反馈同时写进流水线产物本体（如追加 `02-script.md` 末尾） | T6（哈希快照断言） | reject 前后 `02-script.md` / `04-clips.json` 字节变化，快照比对失败变红——守卫「反馈只写 `_agent/` 簿记区」（§2.4） |
| **MUT-11** | `_gate_valid` 去掉段级 diff 校验（只看 exists + mtime）或去掉 mtime 单调性（只看 diff） | T5（过期路径与时序违例子场景） | 去 diff：过期 approved（段级 diff 非空但 mtime 被 touch 成最新）被误判有效 → 「保持 PENDING」断言失败；去 mtime：构造 diff 为空但 approved 早于 clips 的场景（如人工恢复旧 approved 备份）被误判有效 → 变红。双闸各配一个必红子场景，任一缺失都被捕获 |
| **MUT-12** | 删除终态抑制规则（§2.2 第 3 条） | T3（reject 后刷新子场景） | reject 后同指纹刷新立即重建 pending 并再发 REQUESTED，「零新建、零 REQUESTED」断言失败变红——守卫 B3 修订 |
| **MUT-13** | `list_pending`/`approve`/`reject` 去掉 `_self_heal` 前置 | 新增断言（T2/T7 内）：未走 REPL 接线、直接对磁盘上处于停机点的期调 `approve` | 自愈缺席时无 pending 对象，`approve` 抛 `ApprovalError` 而非成功，断言失败变红——守卫 B2「任何表面查到的都是自愈后的队列」 |
| **MUT-14** | 裸形态 `/reject` 改为从 `cli.py:1170` 的拼接串 `sub_cmd.split()` 取参 | T15 | target `"04-clips.json s07 1:20"` 被切成三段，`feedback.target == "04-clips.json"`，逐字节相等断言失败 |
| **MUT-15** | `ApprovalError` 分支改为 `return 0`（或不捕获任其抛栈） | T16 | 前者：缺解封物时退出 0，「退出码 1」断言失败；后者：退出码 1 但 stderr 含 `Traceback`，「不打印栈」断言失败 |
| **MUT-16** | 删除非 TTY 守卫（恢复无条件 `return run_repl(ep_dir)`） | T17 | 非 TTY 下落入 REPL：退出码 0 且期目录出现 `human_time.json`（`minutes: 0.0`），两条断言同时失败 |
| **MUT-17** | `approve` 忽略 `approval_id`，按 stop 取同类型 pending（v0.3 行为） | T18 | A 被替换后自愈新建的替身 B 被显式批准：退出 0、`approvals.jsonl` 新增行，「退出 1 / 无新增行」断言失败 |
| **MUT-18** | 确认路径按 v0.3 docstring 顺序处理：无 pending 即 `ApprovalError` | T19 | 对齐后的 `/approve 05 --id A` 退出 1，「退出 0」断言失败 |
| **MUT-19** | 确认路径不调 `log_approval_decision`（或不写 `confirmed_by`） | T19 | `approvals.jsonl` 行数不变 / `confirmed_by` 为空，断言失败 |
| **MUT-20** | `review.approve` 忽略 `expect`（不核对就写） | T20 ① | 指纹不符时仍生成解封物，「不存在」断言失败 |
| **MUT-21** | 核对 `expect` 后改回按路径 `shutil.copy2`（核对与复制不是同一份字节） | T20 ③ | 竞态中复制到 F2，解封物字节 ≠ F1、`has_clips_approved_diff` 为 False，断言失败 |
| **MUT-22** | `approve` 内部调用会再次进入 `_locked_approvals` 的 `_self_heal`（v0.4 字面实现） | T21 | 同进程二次 flock 阻塞，调用超过 2 s 不返回 |
| **MUT-23** | 确认路径恒记 `latency_s = now − created_at` | T19 | 先对齐后确认时该行 `decision_latency_s` 非空，断言失败 |
| **MUT-24** | 删除第 3 步读后的二次 `os.fstat` 比较（只留 `len(bytes) == size`） | T20 ⑥ | 同长度原地覆写的 F2 被写成解封物，「不存在」断言失败；`has_clips_approved_diff` 对 F2 为 False，闸门打开 |

---

## 8. 施工与 PR 划分

### PR1：Approval 数据模型、持久化与恢复（底座就绪）
- **范围**：新建 `pipeline/approvals.py`——`ApprovalStatus`/`Approval`/`ArtifactFingerprint`/`ApprovalError` 与不变量校验、`_locked_approvals`（flock 全程包裹读-改-写 + atomic_write + 绝不 mkdir）、`_episode_repr`、`get`；淘汰阶梯；测试 T1/T2/T8/T9/T11。
- **验证命令**：`uv run pytest tests/test_approvals.py -k "state_machine or restart or concurrent or fail_silent or pure"`

### PR2：自愈创建、惰性检测与状态卡引导行（队列活起来）
- **范围**：`ensure_pending`（含终态抑制）与 `_self_heal`；指纹钉住、superseded 双触发与 `_gate_valid` 双闸（复用 `align.has_clips_approved_diff`）；`list_pending` 全惰性检测；`APPROVAL_REQUESTED` 发射（含 `_emit` ImportError 降级）；REPL `/approvals` 列表命令与 `cli.py:892` 接线；`build_status_card` 引导行（§2.8/§4.3）；测试 T3/T4/T5/T10b/T14。
- **验证命令**：`uv run pytest tests/test_approvals.py -k "upsert or supersedes or alignment or degrades or guidance"`

### PR3：ack 表面与 rejected 反馈回写（闭环）
- **范围**：`approve()`/`reject()` 全纪律实现（`_gate_valid` 校验、同源三写、记账复用 `log_approval_decision`、`ApprovalError` 受控交互）；REPL `/approve` `/reject`（`/reject` 经 `shlex.split`）与裸形态子命令（按 argv 位置取参、§4.2 退出码契约、非 TTY 守卫，v0.3）；`_agent/approval_feedback.md` 追加写；测试 T6/T7/T10a/T15–T21（含 T19b）与全部变异检验 MUT-1~MUT-24（v0.4 起 approve/reject 按 §4.1 冻结顺序实现，含 `approval_id` 定位与确认路径；v0.5 起一次进锁、确认延迟规则、`pipeline/review.py` 的期望指纹参数（§4.5）一并在本 PR 施工；v0.6 起 §4.5 第 3 步含读后二次 fstat）。
- **验证命令**：`uv run pytest tests/test_approvals.py tests/test_agent_cli.py`

### PR4：门禁固化与文档收口
- **范围**：T12/T13 静态守卫入库；与 Spec 2 PR4 的 emit 位置红线做联合回归（若 Spec 2 已施工）；docs 索引更新；全量回归。
- **验证命令**：`uv run pytest tests/test_approvals.py tests/test_agent_cli.py tests/test_agent_tools.py tests/test_docs_invariants.py`

---

## 9. 验收门禁清单（Accept Gates）

- [ ] **门禁 1（跨进程恢复）**：REPL 会话 A 进入 05 停机点产生 pending，退出；会话 B 重开同期待 `/approvals` 可见该 pending 且可 ack（T2 端到端手验一遍）；**裸形态 `ava <期> /approvals` 在从未进过 REPL 的期上同样可见**（B2 自愈验收）；
- [ ] **门禁 2（反馈可达且可知）**：`/reject 05 <哪段> <问题>` 后，新 agent 会话的状态卡含驳回反馈引导行（T14），`read_artifact("_agent/approval_feedback.md")` 原样读出反馈（T6）；
- [ ] **门禁 3（显式 ack 零回归）**：`approve(02.5/05)` 在解封物缺失**或过期**（§2.3 双闸）时必然拒绝；全代码库 grep 不存在任何「未经人敲命令即转 APPROVED」的路径（`source="artifact"` 对齐除外，且其前置是解封物存在且有效）；
- [ ] **门禁 4（真相源零污染）**：`pipeline/status.py` 源码无 `approvals` / `approvals_store` / `events.jsonl` 任何引用（T13 静态断言）；工序推导 100% 依赖物理产物不变；
- [ ] **门禁 5（无自动批准路径）**：`pipeline/approvals.py` 中 `ApprovalStatus.APPROVED` 赋值点仅两处——`approve()` 显式 ack 与解封物惰性对齐（含双闸校验）；grep 断言无第三处；
- [ ] **门禁 6（存储红线）**：期目录不存在时零写盘零建目录（T9）；全程无 `mkdir` 期目录调用（`_agent/` 子目录创建仅限期目录已确认存在，沿用 status_card.py:301 先例）；
- [ ] **门禁 7（依赖纯洁性）**：独立子进程断言 `pipeline.approvals` 顶层零重依赖（T11）；
- [ ] **门禁 8（既有测试零回归）**：`tests/test_agent_cli.py`、`tests/test_agent_tools.py`、`tests/test_agent_director.py` 100% 通过；`log_approval_decision` 无期早退语义不变（T12）；
- [ ] **门禁 9（文档门禁全绿）**：`uv run pytest tests/test_docs_invariants.py` 全绿；
- [ ] **门禁 10（ack 落盘为事件，含降级 caveat，红队一轮 M3）**：jobs 层在场时 ack 后 `events.jsonl` 可读回 `approval_resolved`（T10a）；**jobs 未施工期间本门禁处于降级态**（`_emit` ImportError 静默，T10b 保证对象库功能完整）——Spec 2 施工完成后必须回看 T10a 转绿，本门禁才算真正关闭。
- [ ] **门禁 11（裸形态契约与 ack 语义，v0.3–v0.6）**：T15–T21（含 T19b、T20 ⑥）全绿，MUT-14~24 被捕获；`tests/test_review.py` 全部既有用例零回归；手验一次：终端里 `ava <期> /script` 仍进入 REPL（TTY 无回归）。

---

## 10. 潜在红旗与自纠预案（Red Flags & Remediation）

| 风险序号 | 潜在红旗 | 根因与危险性 | 预案与防御动作 |
|---|---|---|---|
| **RF-1** | **自动批准偷渡** | 施工者「图省事」让 `ensure_pending` 或 LLM 工具在某条件下直接把对象转 APPROVED——正是 ADR 历史教训（自动写 approved 形同虚设）的复辟 | 门禁 3/5 grep 断言 APPROVED 赋值点仅两处；MUT-5/MUT-11 钉死解封物双闸校验；`--approve` 语义零回归由 T7/T12 双层守卫 |
| **RF-2** | **从 events.jsonl 重建审批队列** | 观测层按设计允许丢（Drop-on-Full、熔断、RF-7 蒸发），拿弱保证重建强保证，REPL 重开后 pending 神秘消失 | §2.1 决策钉死独立快照文件；MUT-6 专杀此捷径（store 有、events 无场景必须恢复成功，T2 子场景） |
| **RF-3** | **对象层成为第二状态闸门（双头）** | 施工者让 render/tts 改读 approval 对象而非物理解封物，`status.py` 与对象库答案分裂 | §2.6 统一铁律：对象永不替代物理闸门；本 spec **不改动 render/review/tts 的任何闸门判断**（范围闸门）；门禁 4 静态断言 |
| **RF-4** | **并发写交织损坏对象库** | 两个 REPL/裸形态进程同期并发 ack，JSON 覆写半途交错 | `_locked_approvals` 全程持锁 + atomic_write（§2.1/§4.1）；T8/MUT-1 确定性验证（持锁挡写，不赌时序） |
| **RF-5** | **幽灵目录污染挂载点** | 外置盘脱卸时 ack/ensure_pending 触发 `mkdir`，在内置盘挂载点建出实体目录，插盘后真数据不可见（paths.py:131-158 防的正是这个） | 期目录 `exists()` 前置检查，不存在一律 fail-silent；T9 断言零建目录 |
| **RF-6** | **反馈写进产物本体污染流水线** | 把驳回意见追加进 `02-script.md` 等产物，下游 tts/clips 把反馈当正文消费 | §2.4 钉死只写 `_agent/`；MUT-10 产物哈希快照断言（T6 正文） |
| **RF-7** | **03.5 建议型语义被做成硬阻塞** | 03.5 在机器语义上本不阻塞（`status.py:312-326` 注释写死）；若对象层把它做成不 ack 不许 clips，就擅自加了新闸门 | 本 spec 不改 `status.py` 与 clips 门禁；03.5 的 ack 是纯记录（§2.6 表），reject 后重修驱动三通道已列明（§2.6 M5）；升级强制闸须另立 ADR |
| **RF-8** | **jobs 未施工导致事件镜像静默缺席被当成完成** | `_emit` 的 ImportError 降级是解耦手段，但也可能让「事件没落地」在验收时被忽略 | T10b 显式覆盖降级路径；门禁 10 以降级 caveat 形态把「jobs 施工后回看 T10a 转绿」写成验收义务（红队一轮 M3 修订） |
| **RF-9** | **读路径写副作用失控** | `list_pending`/`_self_heal` 在「读」操作里做惰性状态转移落盘；若转移逻辑出 bug 或落盘失败，读操作可能炸会话或反复重写 | 惰性转移全部走 `_locked_approvals` 持锁完成；落盘失败静默（返回当前态，下轮再试）；REPL 接线与状态卡引导行均异常静默（§4.2/§4.3）；簿记修正永不阻塞会话（sidecar 同款纪律） |
| **RF-10** | **解封物原子核验的残余窗口**（v0.6，S3-R9a） | 读后二次 fstat 依赖「内容变了 mtime 就变」：① 写入方在写完后把 mtime 改回原值（`touch -r`、`os.utime`），属蓄意对抗，不在本 spec 威胁模型内；② 外置盘若不是 APFS，mtime 粒度可能粗于纳秒，同粒度内的同长度覆写拦不住（与 Spec 8 假设 8 同源）；③ 写入与 mtime 更新在内核里的先后顺序，本机未逐一验证（约） | 如实声明；流水线自身落盘一律 `paths.atomic_write`（换 inode），不进这个窗口；外置盘文件系统随 Spec 8 假设 8 一并实测 |

---

## 附：行号核实自查表（2026-09-22 对照工作树逐行核实；红队一轮独立复核确认，差异项已修）

| 引用 | 核实结果 |
|---|---|
| `status_card.py:282 log_approval_decision` | ✓ def 在 282，函数体至 315 |
| `status_card.py:296-297` 早退 | ✓ `if not ep_dir: return` |
| `status_card.py:301` `_agent` mkdir 先例 | ✓ `agent_dir.mkdir(parents=True, exist_ok=True)` |
| `status_card.py:303` decision 归一化 / 312-313 写盘 / 314-315 WARN | ✓ |
| `status_card.py:65-118 build_status_card` | ✓ def 在 65（2026-09-23 更正：原写 62 有误，62 是 `_strip_episode_prefix` 的 return）；112-116 脱敏清洗循环（红队一轮 m1 补列） |
| `status_card.py:140 render_approval_card` | ✓ def 在 140 |
| `cli.py:30 HUMAN_STOPS` | ✓ `{"02.5", "03.5", "05", "09"}` |
| `cli.py:90-101 human_stop_of` | ✓ |
| `cli.py:108 run_voice_session` | ✓ def 在 108（红队一轮 m1 补列） |
| `cli.py:542-642 _default_approve` | ✓ def 在 542；三处预校验 [REJECT] 出口 570-572 / 576-578 / 585-587（不弹卡不记账）；弹卡 616；按键 629-631；记账 636；按 n 取消 640-641 |
| `cli.py:892` REPL 每轮重算 status | ✓ `status = inspect_episode(ep_dir)` |
| `cli.py:934` /help 清单 | ✓ 约（帮助文本块起始） |
| `cli.py:1004-1062` REPL /run | ✓；EOFError 记账 1042-1045；按 n 记账 1047-1051；confirmed=True 1057 |
| `cli.py:1169-1199` 裸形态分发 | ✓；`1170` `sub_cmd = " ".join(args[1:])`；`/run` 分支 1174-1186，confirmed=True 在 1178-1180；`1199` 未命中时 `return run_repl(ep_dir)`（2026-09-23 v0.3 补核） |
| `cli.py:991` / `995` | ✓ `/chat`、`/script` 为 REPL 内部命令，`main()` 不分派（v0.3 补核） |
| `cli.py:1120` / `1141` / `1156` 非 TTY 降级先例 | ✓ `ava new`、看板、`ava idea` 三处 `if not sys.stdin.isatty()`（v0.3 补核） |
| `cli.py:1164-1166` 期目录不存在 | ✓ `[ERROR] 目标期目录不存在` → `return 1`（v0.3 补核） |
| `review.py:300-340 approve` / `316` / `318-323` / `340` | ✓ def 300；316 `json.loads(src.read_text(...))`；318-323 段级校验；340 `shutil.copy2`（v0.5 补核） |
| `tools.py:126-151 _extract_positional_args`（`valued_flags` 在 134-137） | ✓ `valued_flags` 不含 `--expect-size`/`--expect-mtime-ns`；带 `=` 的旗标整体跳过（v0.5 补核，决定了等号形式） |
| 同进程二次 flock / `threading.Lock` 重入 | ✓ 本机实测：第二个 fd `LOCK_EX\|LOCK_NB` → EWOULDBLOCK；`acquire(blocking=False)` → False（v0.5 补核） |
| `cli.py:611 / 1018` 停机点标签 | ✓ 均为 `human_stop_of(status.current_step)` |
| `status.py:187 inspect_episode` / `194-409 _inspect_episode_core` | ✓（注：Spec 2 v0.4 引用作「194-272」，与现状 194-409 不符——停机点分支：02.5 在 284-297、03.5 在 312-326、05 在 329-341、09 在 398-409；Spec 2 文档行号疑似已漂移，建议红队复核时一并对齐） |
| `status.py:117` approved 过期 advisory | ✓ `advisories.append("approved 已过期，必须重走 05")`（红队一轮新增引用） |
| `paths.py:118-128 atomic_write` / `131-158 require_data` | ✓ |
| `tools.py:308 READ_DENY_PARTS` / `309 READ_ALLOWED_SUFFIXES` | ✓ `_agent` 不在排除集、`.md` 在白名单 |
| `tools.py:458-466 _allowed_read_roots` / `482-526 _tool_read_artifact` | ✓ |
| `tools.py:60-124 write_episode_file` / `698-821 run_pipeline` | ✓ |
| `review.py:300-325 approve` | ✓ def 在 300，显式动作 docstring 303-304 |
| `align.py:210-235 has_clips_approved_diff` | ✓ def 在 210；纯 stdlib 叶子模块（顶层 import 14-19 仅 `hashlib/json/re/pathlib`）（红队一轮新增引用） |
| `clips.py:1229-1237` 重排片不删旧 approved（仅 WARN） | ✓ 红队 B1 证据复核属实 |
| `render.py:1057-1075` 渲染硬闸 | ✓ `run` def 1057；段级 diff 硬闸 1067-1075 调 `has_clips_approved_diff` |
| `pipeline/jobs.py` | ✗ **不存在**（Spec 2 v0.4 可动工未施工，红队一轮复核确认），jobs 层签名以 Spec 2 §4.1 文档为准（§6.1 已声明降级方案 + 门禁 10 caveat） |
| `AGENTS.md` 六节 131 行「--approve 必须是显式动作」 | ✓ 原文核实 |
| `clips.py:1242` / `1289`（v0.6） | ✓ 两处落盘均为 `paths.atomic_write`（`os.replace`） |
| `render.py:1068`（v0.6） | ✓ `if not force and has_clips_approved_diff(episode):` 渲染硬闸只看段级 diff |
| `status.py:116-117`（v0.6） | ✓ `has_clips_approved_diff` 为真时追加 advisory「approved 已过期，必须重走 05」 |
| 原地覆写 fstat/read 窗口（v0.6） | ✓ 本机实测：open + fstat 后经另一路径同长度 `write_text`，再 read → inode 相同、读到 F2、`len == size`，读后 fstat 的 mtime_ns 已变 |
