# Implementation Spec：jobs 层与 events.jsonl（Spec 2 / ADR-0020 §2）

日期：2026-09-22（**v0.4**，红队三轮收口 + B1/B2/B3 定向复审通过；状态：**可动工**）  
上位文档：`docs/dev/plans/2026-09-22-harness-evolution-direction.md`（§2 Spec 2，§4 施工红线八条），`docs/dev/adr/0020-harness-eventization-and-electron-desktop.md`（§2 jobs 层与 events.jsonl）

---

## 0. 一句话设计

**执行即 Job，事件即观测，主流水线永不因观测受阻。**  
新增纯 Python 模块 `pipeline/jobs.py`，将所有流水线命令执行封装为具备严格确定性状态机的 `Job` 对象（`pending → running → succeeded | failed | blocked`）；借鉴 Hermes `event_publisher.py` 原语构建异步事件 Sidecar（Daemon 线程 + 有界队列 + 满即丢弃 + 故障短路 + atexit 毒丸排空 + fcntl 排他文件锁），向期目录追加写 `data/episodes/<期>/events.jsonl`；现有 CLI 交互与 LLM `run_pipeline` 工具入口底层全量切走 jobs 层，同时维持 `pipeline.status` 基于落盘物理产物的单源状态推导地位绝对不变，杜绝双头状态机、幽灵 Job 与并发数据交织。

---

## 1. 红队裁决与修订纪要

### 1.1 第三轮红队裁决与修订纪要（v0.3 → v0.4，3🔴 + 6🟡 + 6🔵 全收；原裁决「🔴 驳回重大修订」，修订后待 B1/B2/B3 定向复审）

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🔴 B1 | `popen_factory` 默认参数在 def 时绑定，PR3 的 mock 迁移方案在 Python 语义上必然穿透 | **采纳** | §4.1 `execute_job` 签名改为 `popen_factory: Callable \| None = None`，函数体内调用时解析（`popen_factory or subprocess.Popen`）；§8 PR3 迁移清单显式追加「FakeProc 补 `pid=12345` 与 `poll()` 桩」 |
| 🔴 B2 | 事件异步落盘与 T3/T4a/MUT-4/门禁 2 的读取断言之间无同步点，系统性竞态 | **采纳** | §7.1 立「同步点铁律」：凡读 `events.jsonl` 物理文件的断言，先注入独立 publisher 并显式 `publisher.close()`（毒丸排空 + join 为现成同步点）或 `_reset_global_publisher_for_testing()` 有界排空后再读；§9 门禁 2 补同款前置条件 |
| 🔴 B3 | 无期调用与既有测试直写真实 `data/_events.jsonl`，审计日志被假事件污染 | **采纳** | `EventPublisher` 增加 `data_root` sink 覆写通道（兼容 `AVA_EVENTS_ROOT` 环境变量）；§8 PR2 要求 conftest autouse fixture 将 sink 定向 `tmp_path`；§7.1 新增 T8 断言测试会话对真实 `paths.ROOT/data` 零写入；§3.3 显式声明无期落 `_global` 为有意设计而非副作用 |
| 🟡 M1 | LLM 审批路径（`cli.py:637-642`）与 EOFError 路径（`cli.py:1043-1045`）的拒执观测黑洞未消除（v0.3 只补了 1/3 个口子） | **采纳** | 发射点上移：`status_card.py:282 log_approval_decision` 内部以函数级延迟 import 同源双写 `APPROVAL_RESOLVED`，一处改动覆盖全部三个拒执出口（§2.1/§6.2/§8 PR4 同步改写） |
| 🟡 M2 | timestamp 契约「Z 标记」与实现 `+00:00` 直接矛盾（6 处示例均为 Z，作者本意是 Z） | **采纳** | 实现侧新增 `_utc_now_iso()` 统一 `.isoformat().replace("+00:00", "Z")`，jobs.py 全部时间戳走该函数，与 §3.3 契约对齐 |
| 🟡 M3 | `emit()` 在主线程执行 `Path.resolve()` 文件系统 syscall，外置盘挂死时「主线程永不阻塞」承诺破产 | **采纳** | `episodes_root` 在 publisher init 时 resolve 一次并缓存；`emit` 不再对 `ep_path` 重复 `resolve()`（`create_job` 已解析过一次），emit 路径零文件系统 syscall |
| 🟡 M4 | `join(timeout=3.0)` 超时窗口内 `get_tail()` 与存活 drain 线程竞争 deque，`RuntimeError` 可穿透正常执行路径 | **采纳** | jobs.py 不复用裸 `_TailBuffer`，新增 `_LockedTailBuffer`（append/get_tail 双端持锁）供 `execute_job` 使用 |
| 🟡 M5 | 无 Job 注册表，pending 态不可枚举，`cancel_job` 的输入对象无从获得，与 Spec 3 边界断裂 | **采纳** | 采用方案 (a) 并在 §6.2 写死：jobs.py 加最小内存 registry（`create_job` 注册、终态移除），暴露 `get_job()` / `list_pending()`；进程内索引，不落盘、不参与状态推导 |
| 🟡 M6 | SIGTERM/主进程被杀时队列内终止事件蒸发，边界未声明 | **采纳** | §10 新增 RF-7 声明已知上限「父进程被信号杀死时未落盘事件不保证持久」；SIGTERM 优雅关闭 handler 列为 Spec 3 桌面端落地时的升级项，本期不实现 |
| 🔵 m1 | 测试数量事实错误（「49 项」vs 实测 `--collect-only` 47 项） | **采纳** | §7.1 T7 修正为 47 项 |
| 🔵 m2 | `event_id` 注释「`evt_<epoch_ms>_<seq>`」与实现 `uuid4().hex[:4]` 不符 | **采纳** | §3.2 注释修正为 `_<hex4>`（§3.3 正则 `[0-9a-f]{4}` 本就与实现吻合） |
| 🔵 m3 | status.py 引用范围不精确（「187-240」vs 实际 def 194、return 点 243/257/272） | **采纳** | §2.2 修正为 `status.py:194-272` |
| 🔵 m4 | 熔断静默期零计数，观测层无法区分「无事件」与「熔断丢事件」 | **采纳** | 熔断窗口内累加 `_dropped_count` 与 `_circuit_dropped`；恢复后首写在同一把 flock 内补发一条 `EventType.SIDECAR_DEGRADED` 自描述诊断事件（载荷含熔断期丢弃计数） |
| 🔵 m5 | 「确定性序列化」声明越界（`job_created` 的 argv 含本机 venv 绝对路径） | **采纳** | §3.2/§3.3 措辞收紧为「本机字节级确定性」，跨机可重放不作承诺 |
| 🔵 m6 | 修订纪要内部引用漂移（§1.1 M1 称「行 379」，该行已是 Event 数据类） | **采纳** | §1.2 M1 行引用修正为「§4.1 emit 代码块」 |

> **定向复审收口记录（v0.4 补丁，B1/B2/B3 复审通过，总裁决「🟢 可动工」）**：
> - 🟡 M1 残留钉死（阻塞放行条件）：§6.2/§2.1 承诺「`episode_dir=None` 时落 `_global`」，但所选注入点 `status_card.py:296-297` 的 `if not ep_dir: return` 早退会先于 emit 执行——无期会话拒执依旧黑洞。§2.1/§6.2/§8 PR4 三处同步钉死：**emit 必须置于早退之前**（`approvals.jsonl` 维持原逻辑跳过）；
> - 🔵 顺手改 ×4（复审建议，不阻塞）：`popen_factory` 改 `is None` 判断（防 `__bool__` 为 False 的可调用对象被静默替换）；`_unregister_job` 改 `try/finally` 安全网（防 except 面外意外异常泄漏注册表项）；T8 断言补并发会话 caveat；`_episodes_root=None` 降级分支补撞车风险注释。

### 1.2 第二轮红队裁决与修订纪要（v0.2 → v0.3，4🔴 + 5🟡 + 3🔵 全收，总裁决「可动工」）

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🔴 B1 | cancel_job 成为未接线死代码，人类审批拒绝在事件层形成观测黑洞 | **采纳** | 明确两阶段下阶段一按 n 拒绝属于「Approval 被拒绝」，在 `cli.py` 拒执分支显式发射 `EventType.APPROVAL_RESOLVED(decision="rejected")` 消除观测黑洞；`cancel_job` 严格定位为已立项 PENDING Job 的异步取消接口（详见 §2.1） |
| 🔴 B2 | Sidecar 毒丸协议在队列拥塞时静默丢弃，导致 close() 无法退出或死等超时 | **采纳** | `close()` 投递毒丸改用带超时写入（0.2s），若队列满则强行抽除队头旧事件压入 `None`；`_consume_loop` 增加 `if self._closed and self._queue.empty(): break` 双重状态守卫，杜绝线程泄漏 |
| 🔴 B3 | 缺乏文件锁导致多进程/无期任务写 `data/_events.jsonl` 数据交织损坏 | **采纳** | 在 `_write_event_safely` 写盘上下文中使用标准库 `fcntl.flock(f.fileno(), fcntl.LOCK_EX)` 获取排他文件锁，写入并 flush 后释放，彻底解决多进程并发交织损坏 |
| 🔴 B4 | 进程被杀轨迹还原（T4 / 门禁 2）对外部信号与异常控制流的致命混淆 | **采纳** | 严格区分外部 SIGKILL（`proc.wait()` 正常返回负退出码）与父进程 SIGINT（抛出 KeyboardInterrupt）；将 T4 拆解为独立的子进程强杀测试与主进程中断测试两个用例 |
| 🟡 M1 | episodes_root 相对路径解析在同级边界下载入非法期名 "." | **采纳** | §4.1 emit 代码块纠正为严格真子集判断：`if ep_path != episodes_root and episodes_root in ep_path.parents:`，边界落入 `_global` |
| 🟡 M2 | 变异矩阵 MUT-5 变异断言为虚假机理（字典天然保序致测试意外通过） | **采纳** | 重构 T3 断言机理：读取物理文件首行 raw string，正则断言首个键必须是 `"episode"`（`sort_keys=True` 字母序排首位），使移除排序必然变红 |
| 🟡 M3 | _drain 双管排水线程在子进程派生祖父进程时无界死锁 | **采纳** | 正常退出路径的 `t_out.join()` 与 `t_err.join()` 加上防御性超时 `timeout=3.0`，超时后记录 warning，防止管道未关死锁主进程 |
| 🟡 M4 | 现有 test_agent_pr6.py 直接 mock tools.subprocess.Popen 引发兼容层穿透 | **采纳** | `execute_job` 接口提供可选 `popen_factory` 依赖注入；在 PR3 规划中显式列出对既有测试 mock 路径的同步迁移与对齐 |
| 🟡 M5 | EventPublisher.start() 多次调用累加注册 atexit 引发退出性能雪崩 | **采纳** | 添加 `self._atexit_registered: bool` 标志位确保单实例仅注册一次，并在 `close()` 完成后显式执行 `atexit.unregister(self.close)` 注销 |
| 🔵 m1 | Event 契约数据类混入内部文件系统路径 episode_dir | **采纳** | `Event` 数据类剔除内部物理路径，保持契约纯洁；落盘物理路径由内部队列以元组 `(event, ep_path)` 传递 |
| 🔵 m2 | execute_job 仅捕获 FileNotFoundError 遗漏权限异常 | **采纳** | 捕获类型拓宽为 `except (FileNotFoundError, PermissionError, OSError) as exc:` |
| 🔵 m3 | JOB_HEARTBEAT 事件缺少预留字段规格定义 | **采纳** | 在 §3.3 补齐心跳事件 payload 的标准字典结构规范 |

---

### 1.3 第一轮红队裁决与修订纪要（v0.1 → v0.2，3🔴 + 4🟡 + 3🔵 全收）

- 🔴 B1（幽灵 Job 泄漏）：确认 `confirmed=False` 为纯 dry-run 校验，零事件发射，零幽灵创建；
- 🔴 B2（Fake Bounded Flush）：增加 `atexit.register(self.close)`，退出改用毒丸哨兵机制；
- 🔴 B3（嵌套路径抹平与擅自 mkdir）：保留完整 `episode_dir` 派生落盘路径，坚决剔除 `mkdir`，无目录直接 fail-silent；
- 🟡 M1–M4、🔵 m1–m3：全量修正变异测试断言、中断 tail 抓取、独立子进程纯洁性测试、测试单例重置钩子等。

---

## 2. 关键设计决策（含代码现状证据）

### 2.1 决策 1：Job 状态机升格与审批拒执闭环（五状态闭环）

- **现状与痛点**：  
  `pipeline/agent/tools.py:698-821` 中的 `run_pipeline` 当前是同步执行包装器。调用 `subprocess.Popen`（行 739）并利用 `_TailBuffer`（行 673-696）抓取尾部输出，最终仅返回一个瞬时字典（行 812-821）。命令一旦执行完毕，执行元数据在终端外彻底挥发；若进程在中途被外部杀死（SIGKILL）或系统崩溃，外部界面没有任何结构化线索判断发生了什么。  
  同时，既有架构在 `pipeline/agent/cli.py:1004-1062`（REPL `/run` 入口）及 `cli.py:581-588`（LLM 工具调用拦截）采用标准的**两阶段推进**（阶段一 `confirmed=False` 预检命令并渲染审批卡，阶段二按 `y` 确认后以 `confirmed=True` 真正执行）。若阶段一无脑立项，将产生大量永远停留于 pending 的悬空幽灵 Job；若阶段一按 `n` 拒绝直接静默返回，又会导致人类拒执在事件层形成观测黑洞。
- **决策内容**：  
  定义显式 `Job` 对象与 `JobStatus` 五状态机：
  $$\text{pending} \longrightarrow \text{running} \longrightarrow \{\text{succeeded} \mid \text{failed}\}$$
  $$\text{pending} \longrightarrow \text{blocked}$$
  1. **pending**：任务已通过 `validate_pipeline_command` 校验，Job 实例已生成并分配唯一 `job_id`，等待人类审批或调度；
  2. **running**：子进程已启动（具备 pid 与 started_at），双管排空线程正在运行；
  3. **succeeded**：子进程正常退出且退出码为 0（`returncode == 0`）；
  4. **failed**：子进程退出码非 0（`returncode != 0`）、被外部信号强杀（返回负退出码如 -9）、或启动过程抛出致命异常（如 `FileNotFoundError`, `PermissionError`）；
  5. **blocked**：静态安全规则拦截（如 `--force`、`cloud exec`）或异步 Job 在启动前被显式取消（调用 `cancel_job`），Job 进入终态。  
  **两阶段契约与观测闭环**：
  - `run_pipeline(..., confirmed=False)` 仅执行纯 dry-run 校验，**不生成持久化 Job，不发射 `job_created` 事件**，消除幽灵 Job；
  - 拒执观测发射点统一上移至 `pipeline/agent/status_card.py:282` 的 `log_approval_decision`：写入 `approvals.jsonl` 后以函数级延迟 import 调 `jobs.get_publisher().emit(EventType.APPROVAL_RESOLVED, ...)` 同源双写（`decision` 归一化为 `"approved"|"rejected"`），一处改动覆盖全部三个拒执出口——REPL 按 n（`cli.py:1050-1052`）、REPL EOFError（`cli.py:1043-1045`）、LLM 工具审批拒绝（`cli.py:637-642`），彻底消除拒执观测黑洞。**emit 必须置于 `status_card.py:296-297` 的 `if not ep_dir: return` 早退之前**（`approvals.jsonl` 维持原逻辑跳过），否则无期会话拒执依旧黑洞（红队四轮钉死条件）；
  - `cancel_job` 接口专门服务于已显式立项为 PENDING 的异步 Job（如未来 Spec 3 审批队列或桌面端调度），驱动其进入 `JobStatus.BLOCKED` 并发射 `job_blocked` 事件。

### 2.2 决策 2：观测层与真相源物理隔离（防双头状态机）

- **现状与痛点**：  
  `pipeline/status.py:194-272` 的 `_inspect_episode_core`（return 点分布于 243/257/272 行）是流水线唯一的「产物即状态」真源。它仅依据 `01-topic.md`、`02-script.md`、`03-audio/manifest.json`、`04-clips.approved.json`、`05-final.mp4`、`06-check.log` 等落盘物理文件推导当前步骤与完成状态。若让事件日志（`events.jsonl`）参与业务状态推导，一旦事件丢失或文件不同步，必将引发致命的双头冲突（Split-Brain）。
- **决策内容**：  
  **`pipeline.status` 对 `events.jsonl` 零读取、零依赖、零感知**。  
  `events.jsonl` 纯属**观测层与审计轨迹**，仅供历史追溯、离线复盘、CLI/REPL 状态回显及未来 Electron 桌面端（ADR-0020 §4）订阅展示。流水线当前工序判断与下一步动作路由（`inspect_episode()`）仍然 100% 取决于文件系统物理产物。

### 2.3 决策 3：事件 Sidecar 失败静默、fcntl 排他锁与毒丸退出（借 Hermes 原语）

- **现状与痛点**：  
  `pipeline/agent/status_card.py:282-316` 的 `log_approval_decision` 虽有向 `_agent/approvals.jsonl` 的追加写，但直接在主线程中同步进行文件 I/O（行 312-313）。如果遇到磁盘只读、网络卷断开、磁盘写满或并发竞争，虽然外层有 `try...except`（行 314-315），但同步 I/O 阻塞或重试会直接卡死主流水线执行。且当多个进程同时写入共享无期日志（`data/_events.jsonl`）时，缺乏文件锁会导致跨 4KB 缓冲边界的数据交织损坏。
- **决策内容**：  
  内化 Hermes `tui_gateway/event_publisher.py` 的工业级 Sidecar 纪律：
  1. **主线程永不阻塞（Non-blocking）**：事件发射 `emit(event)` 使用有界队列 `queue.Queue(maxsize=1024)` 的 `put_nowait()`。队列满时触发 **Drop-on-Full（满即丢弃）**，累加丢弃计数器，绝不挂起流水线主线程；
  2. **事件写入永不抛出（Fail-silent）**：独立后台消费者线程（`daemon=True`）负责批处理写盘。捕获所有 `OSError`、`IOError`，并启动**死连接熔断机制（Circuit Breaker）**：连续写失败达到阈值（如 3 次）自动短路静默 30 秒，期间直接跳过物理 I/O，避免狂刷异常；
  3. **fcntl 排他文件锁（Process-safe）**：在写盘时利用标准库 `fcntl.flock(f.fileno(), fcntl.LOCK_EX)` 获取排他锁，保障跨进程并发写入 `events.jsonl` 时的行原子性与数据完整性；
  4. **毒丸哨兵与 atexit 守护（Poison Pill & atexit）**：单实例仅注册一次 `atexit.register(self.close)`。退出时通过带超时写入推入 `None` 毒丸哨兵（若满则弹出旧事件抢占插入），工作线程消费完毒丸前的全部事件后退出；`close()` 提供最多 0.5 秒的带超时等待，完成后注销 atexit 回调。

### 2.4 决策 4：执行入口单源收归，适配层零语义回归

- **现状与痛点**：  
  当前 CLI 执行入口散落在三处：
  1. `pipeline/agent/cli.py:581-588`：`_default_approve` 中拦截工具审批并调用 `run_pipeline(..., confirmed=False)` 做预校验；
  2. `pipeline/agent/cli.py:1004-1062`：REPL `/run` 命令，先调 `run_pipeline(..., confirmed=False)`（行 1012）渲染审批卡，确认后再调 `run_pipeline(..., confirmed=True)`（行 1057）；
  3. `pipeline/agent/cli.py:1174-1186`：命令行裸形态 `ava <期> /run <cmd>`，直接调 `run_pipeline(..., confirmed=True)`（行 1178-1180）。
- **决策内容**：  
  重构 `tools.py:run_pipeline` 为基于 `pipeline.jobs` 的薄适配层。`run_pipeline` 签名、入参语义和返回字典结构保持 100% 向后兼容（字段 `ok`, `message`, `argv`, `returncode`, `duration_s`, `stdout_tail`, `stderr_tail`, `truncated` 完全保留，新增只读键 `job_id`）。上层 `cli.py` 与测试无需做破坏性修改，底座自动切入 Job 调度与事件落盘。

### 2.5 决策 5：嵌套路径原生支持与严守存储安全红线

- **现状与痛点**：  
  真实代码 `pipeline/agent/cli.py:1089-1098` 明确支持多级嵌套子期目录（如 `data/episodes/EGOIST/01-Live` 企划子期）。同时，`pipeline/paths.py:131-158` 的 `require_data()` 铁律与 `AGENTS.md` 第五节严正规定：「**脚本绝不自动创建 `data/` 或在挂载卷上递归 `mkdir`**」。
- **决策内容**：  
  1. `EventPublisher` 接收物理 `episode_dir: Path | None`，落盘路径直接取 `episode_dir / "events.jsonl"`，事件标识通过严格真子集计算相对路径（如 `EGOIST/01-Live`），消除同级边界下解析为 `.` 的缺陷；
  2. **坚决禁止任何 `parent.mkdir` 调用**：若目标期目录在物理磁盘上尚不存在（如外置盘脱卸或未初始化），Sidecar 必须直接静默放弃写入（Fail-silent），绝不主动建立任何目录，誓死捍卫存储安全红线。

---

## 3. 数据契约（Data Contracts）

### 3.1 Job Schema 定义

每个 Job 在生命周期中由一个强类型数据类维护，并在跨层传递时以结构化字典呈现：

```python
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class Job:
    job_id: str                          # 唯一标识符，格式：job_<epoch_ms>_<hex4>
    command: str                        # 原始输入命令，如 "tts --redo 3"
    scope: str                          # 执行上下文 scope: "pipeline" | "creative" | "asset"
    episode_dir: Path | None            # 关联当期目录（绝对路径）
    argv: list[str] = field(default_factory=list)  # 规范化后的执行指令（含解释器与参数）
    status: JobStatus = JobStatus.PENDING          # 当前状态机状态
    pid: int | None = None                         # 运行子进程系统 PID
    created_at: str = ""                # 创建时间（ISO 8601 UTC）
    started_at: str | None = None       # 启动时间（ISO 8601 UTC）
    ended_at: str | None = None         # 终止时间（ISO 8601 UTC）
    returncode: int | None = None       # 进程退出码
    duration_s: float = 0.0             # 执行耗时（秒，保留 2 位小数）
    message: str = ""                   # 状态描述或错误说明
    stdout_tail: str = ""               # 尾部标准输出（≤4096 字符）
    stderr_tail: str = ""               # 尾部标准错误（≤4096 字符）
    truncated: bool = False             # 尾环缓冲区是否发生截断
```

### 3.2 Event Schema 定义

`events.jsonl` 中的每一行均为一个序列化的 Event 对象：

```python
class EventType(str, Enum):
    # Job 生命周期事件（Spec 2 核心）
    JOB_CREATED = "job_created"          # Job 已创建（进入 pending）
    JOB_BLOCKED = "job_blocked"          # Job 校验失败或被审批拦截/取消（进入 blocked）
    JOB_STARTED = "job_started"          # Job 进程已拉起（进入 running）
    JOB_HEARTBEAT = "job_heartbeat"      # 预留枚举（Spec 8 桌面端接入长任务监控）
    JOB_FINISHED = "job_finished"        # Job 结束（进入 succeeded 或 failed）

    # 停机点与审批事件（为 Spec 3 预留契约枚举）
    APPROVAL_REQUESTED = "approval_requested"  # 遇到停机点产生 pending approval 对象
    APPROVAL_RESOLVED = "approval_resolved"    # 人类在任一界面完成 ack（通过/拒绝/反馈）

    # 外部/辅助事件
    HUMAN_TIME_RECORDED = "human_time_recorded"  # 停机点人时记账事件
    SIDECAR_DEGRADED = "sidecar_degraded"        # Sidecar 自愈诊断（熔断恢复后补发，载荷含熔断期丢弃计数）


@dataclass(frozen=True)
class Event:
    event_id: str                       # 事件唯一 ID，格式：evt_<epoch_ms>_<hex4>
    timestamp: str                      # 事件触发时间（ISO 8601 UTC）
    episode: str                        # 关联期相对路径（如 "01-test" 或 "EGOIST/01-Live"），无期为 "_global"
    type: EventType                     # 事件类型
    payload: dict[str, Any]             # 强类型载荷

    def to_jsonl_line(self) -> str:
        """确定性序列化为单行 JSONL 字符串（本机字节级确定性；payload 可含本机绝对路径，不承诺跨机可重放）。"""
        data = {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "episode": self.episode,
            "type": self.type.value,
            "payload": self.payload,
        }
        return json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n"
```

### 3.3 `events.jsonl` 物理文件格式与逐字段规范

- **文件路径**：`data/episodes/<期号>/events.jsonl`（支持多级嵌套子期；非期任务或无期会话落盘在 `data/_events.jsonl`）。
- **无期落盘为有意设计**：REPL 无期会话（`episode_dir=None`）事件落 `data/_events.jsonl`（episode 字段为 `"_global"`），属预期行为而非副作用；但测试进程必须经 `data_root` / `AVA_EVENTS_ROOT` sink 覆写通道重定向至 `tmp_path`，严禁污染真实审计日志（见 §4.1 与 §7.1 T8）。
- **行级字段契约**：

| 字段名 | 类型 | 约束 | 示例 / 说明 |
|---|---|---|---|
| `event_id` | string | 正则 `^evt_\d{13}_[0-9a-f]{4}$` | `"evt_1790089200123_00a1"` |
| `timestamp` | string | ISO 8601 UTC（带微秒与 Z 标记） | `"2026-09-22T14:30:00.123456Z"` |
| `episode` | string | 当期相对路径或 `"_global"` | `"01-春物01"` 或 `"EGOIST/01-Live"` |
| `type` | string | `EventType` 允许值 | `"job_started"` |
| `payload` | object | 确定性字典序列化（本机字节级，不承诺跨机可重放） | 具体见下文各事件载荷定义 |

#### 典型事件载荷示例

1. **`job_created` 载荷**：
   ```json
   {
     "event_id": "evt_1790089200100_0001",
     "episode": "EGOIST/01-Live",
     "timestamp": "2026-09-22T14:30:00.100000Z",
     "type": "job_created",
     "payload": {
       "job_id": "job_1790089200100_01a2",
       "command": "tts --redo 3",
       "scope": "pipeline",
       "argv": ["/Users/.../venv/bin/python", "-m", "pipeline.tts", "/Users/.../data/episodes/EGOIST/01-Live", "--redo", "3"]
     }
   }
   ```

2. **`job_started` 载荷**：
   ```json
   {
     "event_id": "evt_1790089200200_0002",
     "episode": "EGOIST/01-Live",
     "timestamp": "2026-09-22T14:30:00.200000Z",
     "type": "job_started",
     "payload": {
       "job_id": "job_1790089200100_01a2",
       "pid": 48102,
       "started_at": "2026-09-22T14:30:00.198000Z"
     }
   }
   ```

3. **`job_finished` 载荷**：
   ```json
   {
     "event_id": "evt_1790089205500_0003",
     "episode": "EGOIST/01-Live",
     "timestamp": "2026-09-22T14:30:05.500000Z",
     "type": "job_finished",
     "payload": {
       "job_id": "job_1790089200100_01a2",
       "status": "succeeded",
       "returncode": 0,
       "duration_s": 5.3,
       "message": "退出码 0",
       "stdout_tail": "[OK] 段落 3 合成完毕 (1.4s)\n",
       "stderr_tail": "",
       "truncated": false
     }
   }
   ```

4. **`job_blocked` 载荷**（被护栏拦截或异步 Job 显式取消）：
   ```json
   {
     "event_id": "evt_1790089210000_0004",
     "episode": "01-test",
     "timestamp": "2026-09-22T14:30:10.000000Z",
     "type": "job_blocked",
     "payload": {
       "job_id": "job_1790089210000_01b3",
       "command": "render --force",
       "reason": "拒绝执行：禁止在 ava 中使用 --force 全量覆盖！"
     }
   }
   ```

5. **`approval_resolved` 载荷**（人类在 CLI/REPL 卡片按 n 拒绝执行）：
   ```json
   {
     "event_id": "evt_1790089215000_0005",
     "episode": "01-test",
     "timestamp": "2026-09-22T14:30:15.000000Z",
     "type": "approval_resolved",
     "payload": {
       "command": "tts --redo 3",
       "decision": "rejected",
       "source": "cli_card"
     }
   }
   ```

6. **`job_heartbeat` 载荷**（预留 Spec 8 桌面端长任务心跳）：
   ```json
   {
     "event_id": "evt_1790089230000_0006",
     "episode": "01-test",
     "timestamp": "2026-09-22T14:30:30.000000Z",
     "type": "job_heartbeat",
     "payload": {
       "job_id": "job_1790089200100_01a2",
       "elapsed_s": 30.0,
       "heartbeat_at": "2026-09-22T14:30:30.000000Z"
     }
   }
   ```

---

## 4. 模块接口与签名设计（`pipeline/jobs.py`）

### 4.1 核心类与接口全貌

```python
"""pipeline.jobs: ava 流水线 Job 调度与异步事件总线（Spec 2 / ADR-0020 §2）。"""

from __future__ import annotations

import atexit
import codecs
import collections
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import fcntl
import json
import os
from pathlib import Path
import queue
import shlex
import subprocess
import sys
import threading
import time
from typing import Any, Callable
import uuid

from pipeline import paths

# ---------------------------------------------------------------------------
# 1. 基础数据模型与状态机
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


class EventType(str, Enum):
    JOB_CREATED = "job_created"
    JOB_BLOCKED = "job_blocked"
    JOB_STARTED = "job_started"
    JOB_HEARTBEAT = "job_heartbeat"  # 预留枚举（Spec 8 桌面端接入长任务监控）
    JOB_FINISHED = "job_finished"
    APPROVAL_REQUESTED = "approval_requested"  # 预留 Spec 3
    APPROVAL_RESOLVED = "approval_resolved"    # 预留 Spec 3
    HUMAN_TIME_RECORDED = "human_time_recorded"
    SIDECAR_DEGRADED = "sidecar_degraded"  # Sidecar 自愈诊断（熔断恢复后补发）


@dataclass
class Job:
    job_id: str
    command: str
    scope: str
    episode_dir: Path | None
    argv: list[str] = field(default_factory=list)
    status: JobStatus = JobStatus.PENDING
    pid: int | None = None
    created_at: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    returncode: int | None = None
    duration_s: float = 0.0
    message: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    truncated: bool = False


@dataclass(frozen=True)
class Event:
    event_id: str
    timestamp: str
    episode: str                        # 相对路径字符串，如 "EGOIST/01-Live" 或 "_global"
    type: EventType
    payload: dict[str, Any]

    def to_jsonl_line(self) -> str:
        """确定性序列化为单行 JSONL 字符串。"""
        data = {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "episode": self.episode,
            "type": self.type.value,
            "payload": self.payload,
        }
        return json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n"


def _utc_now_iso() -> str:
    """统一 UTC 时间戳：ISO 8601 带微秒与 Z 标记（契约见 §3.3，红队三轮 M2）。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# 2. 事件 Sidecar 发布器与持久化落地（借 Hermes 原语，atexit + fcntl 排他锁）
# ---------------------------------------------------------------------------

class EventPublisher:
    """事件发布器 Sidecar：daemon 线程、队列满即丢、故障熔断、主循环零阻塞。"""

    def __init__(self, queue_capacity: int = 1024, data_root: Path | str | None = None) -> None:
        self._queue: queue.Queue[tuple[Event, Path | None] | None] = queue.Queue(maxsize=queue_capacity)
        self._worker_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._atexit_registered = False
        self._dropped_count = 0
        self._failure_count = 0
        self._circuit_broken_until = 0.0
        self._circuit_dropped = 0  # 熔断窗口内丢弃计数（恢复后随 sidecar_degraded 自描述事件上报，红队三轮 m4）
        # sink 覆写通道（红队三轮 B3）：测试经构造参数或 AVA_EVENTS_ROOT 环境变量重定向，严禁污染真实审计日志
        if data_root is None and os.environ.get("AVA_EVENTS_ROOT"):
            data_root = os.environ["AVA_EVENTS_ROOT"]
        self._data_root = Path(data_root) if data_root else paths.ROOT / "data"
        # episodes_root 仅在 init 时 resolve 一次并缓存；emit 主线程零文件系统 syscall（红队三轮 M3）
        try:
            self._episodes_root: Path | None = (self._data_root / "episodes").resolve()
        except Exception:
            # 降级语义（红队四轮）：resolve 失败后嵌套子期永久退化为 ep_path.name，
            # 两个同名子期（如两个企划的 01-Live）事件标识将撞车——已知上限，调用方可
            # 经 _episodes_root is None 自检发现
            self._episodes_root = None

    def start(self) -> None:
        with self._lock:
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._closed = False
                self._worker_thread = threading.Thread(
                    target=self._consume_loop,
                    name="ava-event-publisher-sidecar",
                    daemon=True,
                )
                self._worker_thread.start()
                if not self._atexit_registered:
                    atexit.register(self.close)
                    self._atexit_registered = True

    def emit(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        *,
        episode_dir: Path | str | None = None,
    ) -> None:
        """非阻塞派发事件。任何情况下绝不向调用方抛出异常。"""
        now_ts = _utc_now_iso()
        # 主线程零 syscall：不再 resolve（调用方 create_job 已解析过一次），仅做纯路径运算（红队三轮 M3）
        ep_path = Path(episode_dir) if episode_dir else None

        # 严格真子集判断：同级边界下载入 _global，嵌套子期保留相对路径（如 EGOIST/01-Live）
        ep_repr = "_global"
        if ep_path is not None:
            try:
                root = self._episodes_root
                if root is not None and ep_path != root and root in ep_path.parents:
                    ep_repr = ep_path.relative_to(root).as_posix()
                else:
                    ep_repr = ep_path.name
            except Exception:
                ep_repr = ep_path.name

        evt = Event(
            event_id=f"evt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}",
            timestamp=now_ts,
            episode=ep_repr,
            type=event_type,
            payload=payload,
        )

        try:
            self._queue.put_nowait((evt, ep_path))
        except queue.Full:
            self._dropped_count += 1
            # 满即丢，静默不阻塞主任务
        except Exception:
            pass

    def _consume_loop(self) -> None:
        """后台批处理消费循环：严格以毒丸哨兵（None）为唯一合法退出条件，辅以状态双重守卫。"""
        while True:
            if self._closed and self._queue.empty():
                break

            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if item is None:
                # 收到毒丸哨兵，表示队列已排空，退出消费
                self._queue.task_done()
                break

            event, target_ep_dir = item
            try:
                self._write_event_safely(event, target_ep_dir)
            finally:
                self._queue.task_done()

    def _write_event_safely(self, event: Event, episode_dir: Path | None) -> None:
        """安全写入物理文件，带 fcntl 排他锁、熔断保护与外置盘守卫。严禁自动建目录。"""
        now = time.time()
        if now < self._circuit_broken_until:
            # 熔断窗口内仍计数（红队三轮 m4）：恢复后可区分「无事件」与「熔断丢事件」
            self._dropped_count += 1
            self._circuit_dropped += 1
            return  # 处于熔断保护期，直接短路跳过物理 I/O

        try:
            target_path = self._resolve_target_path(episode_dir)
            if target_path is None:
                return

            # 存储红线：父目录必须已在磁盘真实存在；不存在直接放弃，严禁 parent.mkdir()
            if not target_path.parent.exists():
                return

            with target_path.open("a", encoding="utf-8") as f:
                # 使用 fcntl 排他文件锁防止多进程并发交织损坏
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(event.to_jsonl_line())
                    # 熔断恢复首写：同一把 flock 内补发自描述诊断事件，上报熔断期丢弃计数（红队三轮 m4）
                    if self._circuit_dropped > 0:
                        recovered = Event(
                            event_id=f"evt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}",
                            timestamp=_utc_now_iso(),
                            episode=event.episode,
                            type=EventType.SIDECAR_DEGRADED,
                            payload={"dropped_during_circuit": self._circuit_dropped},
                        )
                        f.write(recovered.to_jsonl_line())
                        self._circuit_dropped = 0
                    f.flush()
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            self._failure_count = 0  # 写入成功，重置失败计数
        except Exception:
            self._failure_count += 1
            if self._failure_count >= 3:
                # 连续 3 次写盘失败触发 30s 熔断
                self._circuit_broken_until = now + 30.0

    def _resolve_target_path(self, episode_dir: Path | None) -> Path | None:
        """解析落盘路径：无期走 data/_events.jsonl，有期严格走期目录自身。"""
        try:
            if episode_dir is None:
                if not self._data_root.exists():
                    return None
                return self._data_root / "_events.jsonl"

            # 遵循 paths.py 铁律：外置盘未挂载时不写
            if not episode_dir.exists():
                return None

            return episode_dir / "events.jsonl"
        except Exception:
            return None

    def close(self, timeout: float = 0.5) -> None:
        """有界等待后台队列排空并停机（毒丸强塞协议）。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True

        # 投递毒丸：带超时写入；若满则抽除队头旧事件插入毒丸，确保消费者必然收到哨兵
        try:
            self._queue.put(None, timeout=0.2)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(None)
            except Exception:
                pass
        except Exception:
            pass

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)

        if self._atexit_registered:
            try:
                atexit.unregister(self.close)
                self._atexit_registered = False
            except Exception:
                pass


# 全局单例 Sidecar（按需延迟启动）
_GLOBAL_PUBLISHER: EventPublisher | None = None
_INIT_LOCK = threading.Lock()

def get_publisher() -> EventPublisher:
    global _GLOBAL_PUBLISHER
    if _GLOBAL_PUBLISHER is None:
        with _INIT_LOCK:
            if _GLOBAL_PUBLISHER is None:
                pub = EventPublisher()
                pub.start()
                _GLOBAL_PUBLISHER = pub
    return _GLOBAL_PUBLISHER


def _reset_global_publisher_for_testing() -> None:
    """测试夹具专用：重置全局 Sidecar 单例，消灭跨用例测试污染。"""
    global _GLOBAL_PUBLISHER
    with _INIT_LOCK:
        if _GLOBAL_PUBLISHER is not None:
            _GLOBAL_PUBLISHER.close(timeout=0.2)
            _GLOBAL_PUBLISHER = None


# ---------------------------------------------------------------------------
# 3. Job 注册表、创建、执行与调度入口
# ---------------------------------------------------------------------------

# 最小内存 Job 注册表（红队三轮 M5）：create_job 注册、终态移除；供 cancel_job 与
# Spec 3 审批队列枚举 pending 任务。进程内索引，不落盘、不参与 status.py 状态推导。
_JOB_REGISTRY: dict[str, Job] = {}
_REGISTRY_LOCK = threading.Lock()


def get_job(job_id: str) -> Job | None:
    with _REGISTRY_LOCK:
        return _JOB_REGISTRY.get(job_id)


def list_pending() -> list[Job]:
    with _REGISTRY_LOCK:
        return [j for j in _JOB_REGISTRY.values() if j.status == JobStatus.PENDING]


def _register_job(job: Job) -> None:
    with _REGISTRY_LOCK:
        _JOB_REGISTRY[job.job_id] = job


def _unregister_job(job: Job) -> None:
    with _REGISTRY_LOCK:
        _JOB_REGISTRY.pop(job.job_id, None)


def create_job(
    command: str | list[str],
    episode_dir: Path | str | None = None,
    *,
    scope: str = "pipeline",
    publisher: EventPublisher | None = None,
) -> tuple[Job, bool, str]:
    """显式立项创建一个 Job 对象。通过校验则进入 PENDING，未通过进入 BLOCKED。

    返回: (job, is_valid, validation_message)
    """
    from pipeline.agent.tools import validate_pipeline_command

    cmd_str = shlex.join(command) if isinstance(command, list) else str(command).strip()
    ep_path = Path(episode_dir).resolve() if episode_dir else None
    job_id = f"job_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}"
    created_at = _utc_now_iso()

    valid, msg, argv = validate_pipeline_command(command, scope=scope, ep_dir=ep_path)

    job = Job(
        job_id=job_id,
        command=cmd_str,
        scope=scope,
        episode_dir=ep_path,
        argv=argv,
        status=JobStatus.PENDING if valid else JobStatus.BLOCKED,
        created_at=created_at,
        message=msg,
    )

    pub = publisher or get_publisher()
    if valid:
        pub.emit(
            EventType.JOB_CREATED,
            {"job_id": job.job_id, "command": job.command, "scope": job.scope, "argv": job.argv},
            episode_dir=ep_path,
        )
    else:
        pub.emit(
            EventType.JOB_BLOCKED,
            {"job_id": job.job_id, "command": job.command, "reason": msg},
            episode_dir=ep_path,
        )

    _register_job(job)
    if not valid:
        _unregister_job(job)  # BLOCKED 即刻终态，不留注册表
    return job, valid, msg


def cancel_job(
    job: Job,
    reason: str = "human_rejected",
    *,
    publisher: EventPublisher | None = None,
) -> Job:
    """显式取消一个 PENDING 状态的 Job，闭环终态，防止幽灵悬空。"""
    if job.status != JobStatus.PENDING:
        return job

    job.status = JobStatus.BLOCKED
    job.ended_at = _utc_now_iso()
    job.message = reason
    _unregister_job(job)

    pub = publisher or get_publisher()
    pub.emit(
        EventType.JOB_BLOCKED,
        {"job_id": job.job_id, "command": job.command, "reason": reason},
        episode_dir=job.episode_dir,
    )
    return job


class _LockedTailBuffer:
    """尾部缓冲双端持锁包装（红队三轮 M4）：防止 join(timeout=3.0) 超时窗口内
    get_tail() 迭代 deque 时存活 drain 线程并发 append 抛 RuntimeError。"""

    def __init__(self, max_bytes: int = 4096) -> None:
        from pipeline.agent.tools import _TailBuffer

        self._inner = _TailBuffer(max_bytes)
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        with self._lock:
            self._inner.append(chunk)

    def get_tail(self) -> tuple[str, bool]:
        with self._lock:
            return self._inner.get_tail()


def execute_job(
    job: Job,
    *,
    publisher: EventPublisher | None = None,
    popen_factory: Callable[..., Any] | None = None,
) -> Job:
    """真实执行一个处于 PENDING 状态的 Job。

    负责双管排水、尾部缓冲抓取、生命周期事件发射及异常守护。
    支持通过 popen_factory 注入执行器（供单元测试与 PR6 测试打桩）。
    注意：popen_factory 默认为 None 并在函数体内调用时解析——默认参数在 def 时
    绑定会穿透运行期 mock（红队三轮 B1），严禁写回 subprocess.Popen 作默认值。
    """
    if job.status != JobStatus.PENDING:
        raise ValueError(f"无法执行状态为 {job.status} 的 Job")

    # 调用时解析执行器（红队三轮 B1）：默认参数在 def 时绑定会穿透运行期 mock；
    # is None 判断（红队四轮）：防止 __bool__ 返回 False 的可调用对象被静默替换
    if popen_factory is None:
        popen_factory = subprocess.Popen

    pub = publisher or get_publisher()

    # 注册表安全网（红队四轮 M5 残留）：except 面外的意外异常（如工厂抛 TypeError）也必须移出注册表
    try:
        t_start = time.time()
        job.started_at = _utc_now_iso()

        try:
            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            proc = popen_factory(
                job.argv,
                cwd=paths.ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            job.pid = proc.pid
            job.status = JobStatus.RUNNING
            pub.emit(
                EventType.JOB_STARTED,
                {"job_id": job.job_id, "pid": proc.pid, "started_at": job.started_at},
                episode_dir=job.episode_dir,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            job.status = JobStatus.FAILED
            job.ended_at = _utc_now_iso()
            job.duration_s = round(time.time() - t_start, 2)
            job.message = f"执行器启动异常: {exc}"
            pub.emit(
                EventType.JOB_FINISHED,
                {"job_id": job.job_id, "status": job.status.value, "returncode": None, "message": job.message},
                episode_dir=job.episode_dir,
            )
            return job

        stdout_buf = _LockedTailBuffer(4096)
        stderr_buf = _LockedTailBuffer(4096)

        def _drain(pipe: Any, buf: _LockedTailBuffer, is_stderr: bool) -> None:
            target_stream = sys.stderr if is_stderr else sys.stdout
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            try:
                for chunk in iter(lambda: pipe.read1(4096), b""):
                    text = decoder.decode(chunk)
                    try:
                        target_stream.write(text)
                        target_stream.flush()
                    except Exception:
                        pass
                    buf.append(chunk)
                final_text = decoder.decode(b"", final=True)
                if final_text:
                    try:
                        target_stream.write(final_text)
                        target_stream.flush()
                    except Exception:
                        pass
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_buf, False), daemon=True)
        t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_buf, True), daemon=True)
        t_out.start()
        t_err.start()

        retcode: int | None = None
        try:
            retcode = proc.wait()
        except BaseException:
            # Ctrl-C 或外部中断：子进程必须立刻 kill，防止残留孤儿渲染进程打架（tools.py:794 先例）
            try:
                proc.kill()
            except Exception:
                pass
            t_out.join(2)
            t_err.join(2)

            job.status = JobStatus.FAILED
            job.ended_at = _utc_now_iso()
            job.duration_s = round(time.time() - t_start, 2)
            actual_code = proc.poll() if proc.poll() is not None else -9
            job.returncode = actual_code
            job.message = f"进程被中断终止 (BaseException, code={actual_code})"

            stdout_tail, out_trunc = stdout_buf.get_tail()
            stderr_tail, err_trunc = stderr_buf.get_tail()
            job.stdout_tail = stdout_tail
            job.stderr_tail = stderr_tail
            job.truncated = out_trunc or err_trunc

            pub.emit(
                EventType.JOB_FINISHED,
                {
                    "job_id": job.job_id,
                    "status": job.status.value,
                    "returncode": job.returncode,
                    "duration_s": job.duration_s,
                    "message": job.message,
                    "stdout_tail": job.stdout_tail,
                    "stderr_tail": job.stderr_tail,
                    "truncated": job.truncated,
                },
                episode_dir=job.episode_dir,
            )
            raise

        # 正常退出加 3 秒防御性超时，防止派生孙进程继承管道导致死锁挂死
        t_out.join(timeout=3.0)
        t_err.join(timeout=3.0)
        job.duration_s = round(time.time() - t_start, 2)
        job.ended_at = _utc_now_iso()
        job.returncode = retcode
        job.status = JobStatus.SUCCEEDED if retcode == 0 else JobStatus.FAILED
        job.message = f"退出码 {retcode}"

        stdout_tail, out_trunc = stdout_buf.get_tail()
        stderr_tail, err_trunc = stderr_buf.get_tail()
        job.stdout_tail = stdout_tail
        job.stderr_tail = stderr_tail
        job.truncated = out_trunc or err_trunc

        pub.emit(
            EventType.JOB_FINISHED,
            {
                "job_id": job.job_id,
                "status": job.status.value,
                "returncode": job.returncode,
                "duration_s": job.duration_s,
                "message": job.message,
                "stdout_tail": job.stdout_tail,
                "stderr_tail": job.stderr_tail,
                "truncated": job.truncated,
            },
            episode_dir=job.episode_dir,
        )

        return job
    finally:
        _unregister_job(job)  # 任何退出路径（含 except 面外意外异常）均移出注册表
```

### 4.2 `tools.py:run_pipeline` 兼容适配层实现

在 `pipeline/agent/tools.py` 中，原 `run_pipeline`（行 698-821）整体替换为对 `pipeline.jobs` 的调用。**彻底消除幽灵 Job**：

```python
def run_pipeline(
    command: str | list[str],
    episode_dir: Path | str | None = None,
    *,
    scope: str = "pipeline",
    confirmed: bool = False,
) -> dict[str, Any]:
    """白名单执行器入口（向后兼容适配器，底层由 pipeline.jobs 全权驱动）。

    纪律：
    1. confirmed=False 时为纯 dry-run 校验，不持久化 Job，不发 job_created，不留幽灵；
    2. confirmed=True 时真正调用 create_job 立项并 execute_job 执行。
    """
    from pipeline.agent.tools import validate_pipeline_command
    from pipeline.jobs import JobStatus, create_job, execute_job

    ep_path = Path(episode_dir).resolve() if episode_dir else None

    # 阶段一：未确认状态，做纯 dry-run 校验，零事件发射，零幽灵对象
    if not confirmed:
        valid, msg, argv = validate_pipeline_command(command, scope=scope, ep_dir=ep_path)
        if not valid:
            return {
                "ok": False,
                "message": msg,
                "argv": [],
                "returncode": None,
                "duration_s": 0.0,
                "stdout_tail": "",
                "stderr_tail": "",
                "truncated": False,
                "job_id": None,
            }
        return {
            "ok": True,
            "message": "待人类确认",
            "argv": argv,
            "returncode": None,
            "duration_s": 0.0,
            "stdout_tail": "",
            "stderr_tail": "",
            "truncated": False,
            "job_id": None,
        }

    # 阶段二：已确认状态，正式立项并流转状态机
    job, valid, msg = create_job(command, episode_dir=ep_path, scope=scope)
    if not valid:
        return {
            "ok": False,
            "message": msg,
            "argv": [],
            "returncode": None,
            "duration_s": 0.0,
            "stdout_tail": "",
            "stderr_tail": "",
            "truncated": False,
            "job_id": job.job_id,
        }

    executed_job = execute_job(job)
    return {
        "ok": executed_job.status == JobStatus.SUCCEEDED,
        "message": executed_job.message,
        "argv": executed_job.argv,
        "returncode": executed_job.returncode,
        "duration_s": executed_job.duration_s,
        "stdout_tail": executed_job.stdout_tail,
        "stderr_tail": executed_job.stderr_tail,
        "truncated": executed_job.truncated,
        "job_id": executed_job.job_id,
    }
```

---

## 5. 依赖白名单与纯洁性保障

### 5.1 模块级依赖白名单

`pipeline/jobs.py` 作为调度基础设施，属于高频热路径，必须保持极度轻量与高启动速度。

**允许导入清单（模块顶层）**：
- Python 标准库：`atexit`, `codecs`, `collections`, `dataclasses`, `datetime`, `enum`, `fcntl`, `json`, `os`, `pathlib`, `queue`, `shlex`, `subprocess`, `sys`, `threading`, `time`, `typing`, `uuid`
- 项目轻量基础库：`from pipeline import paths`

**绝对严禁顶层导入（重量级科学计算与 ML 栈）**：
- 严禁顶层 `import numpy`
- 严禁顶层 `import torch`
- 严禁顶层 `import mlx_whisper` / `whisper`
- 严禁顶层 `import moviepy` / `cv2`
- 严禁顶层 `import transformers`

### 5.2 依赖隔离回归测试（独立子进程守卫）

为了彻底杜绝共享 pytest 进程中其他测试已载入 `numpy` 导致的假阳性旁路漏网（红队 M3 裁决），纯洁性测试必须在全新的 Python 子进程中验证：

```python
def test_jobs_module_pure_and_no_numpy_in_hot_path():
    """验证 pipeline.jobs 保持纯洁性，在独立全新解释器中绝不违规引入 numpy 等重包。"""
    import subprocess
    import sys

    probe_code = (
        "import pipeline.jobs, sys; "
        "forbidden = ('numpy', 'torch', 'moviepy', 'transformers'); "
        "leaked = [m for m in forbidden if m in sys.modules]; "
        "assert not leaked, f'pipeline.jobs 顶层违规引入了重量级包: {leaked}'"
    )
    res = subprocess.run(
        [sys.executable, "-c", probe_code],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"依赖纯洁性检验失败:\n{res.stderr}"
```

---

## 6. 跨 Spec 接口与系统边界

### 6.1 与 Spec 1（上下文装配器 `assembly.py`）的接口边界

- **职责切分**：
  - **Spec 1** 负责根据 `pipeline.status.inspect_episode()` 计算得到的工序键，从 `config/agent/assembly.json` 检索对应 runbook Markdown 并拼装系统提示（三层架构）；
  - **Spec 2** 负责 Job 状态流转与事件持久化。
- **依赖单向性**：
  - Spec 1 装配器完全不感知 `events.jsonl`，也不读取 `jobs.py`。
  - Spec 2 的 `events.jsonl` 纯属下游观测产物，绝不反向干扰 `status.py` 的产物判断。
  - 两者通过 `pipeline.paths` 共享期目录位置，无代码耦合。

### 6.2 与 Spec 3（Approval 对象化）的接口边界

- **职责切分**：
  - **Spec 2** 只管流水线命令的执行调度与底层事件落盘；
  - **Spec 3** 负责停机点 02.5/03.5/05/09 升格为 pending approval 对象，支持跨表面（CLI / REPL / Electron）的结构化 ack 与 feedback 回写。
- **协议预留与拒执观测**：
  - Spec 2 在 `EventType` 枚举中提前预留了 `APPROVAL_REQUESTED` 和 `APPROVAL_RESOLVED` 两个事件槽位；
  - 拒执观测发射点统一上移至 `status_card.py:282` 的 `log_approval_decision`：写入 `approvals.jsonl` 后以**函数级延迟 import** 调 `jobs.get_publisher().emit(EventType.APPROVAL_RESOLVED, {"command": target, "decision": "approved"|"rejected", "source": tool_name})` 同源双写（`episode_dir=None` 时落 `_global`），一处改动覆盖全部三个拒执出口——REPL 按 n（`cli.py:1050-1052`）、REPL EOFError（`cli.py:1043-1045`）、LLM 工具审批拒绝（`cli.py:637-642`）。**emit 必须置于 `status_card.py:296-297` 的 `if not ep_dir: return` 早退之前**（`approvals.jsonl` 维持原逻辑跳过），否则「无期落 `_global`」的承诺按此施工单永远不可达（红队四轮钉死条件）。观测层与审计层从此对同一事实给出同一答案（红队三轮 M1）；
  - 现行 `pipeline/agent/status_card.py:log_approval_decision` 写入 `_agent/approvals.jsonl` 的行为在 Spec 2 实施期间维持现状，不被破坏；Spec 3 落地时再评估是否去重单写；
  - 提供 `cancel_job(job, reason=...)` 接口，使 Spec 3 或桌面端在人类审批拒绝异步任务时能够显式驱动 Job 进入 `JobStatus.BLOCKED` 终态。
- **Job 注册表归属（红队三轮 M5 裁决，采用方案 a）**：
  - `jobs.py` 内置最小内存注册表（`_JOB_REGISTRY`）：`create_job` 注册、`execute_job` / `cancel_job` 进入终态即移除，暴露 `get_job(job_id)` 与 `list_pending()`；
  - 注册表为进程内索引，**不落盘、不参与 `status.py` 状态推导**；Spec 3 审批队列通过 `list_pending()` 枚举未决任务并找回 Job 对象，严禁越过 jobs.py 自造索引。

---

## 7. 测试规格与变异检验方案（Test Specifications & Mutation Testing）

### 7.1 单元测试规格（`tests/test_jobs.py`）

> **同步点铁律（红队三轮 B2）**：凡涉及读取 `events.jsonl` 物理文件的断言（T2/T3/T4a/T4b/T7），必须先注入独立 `EventPublisher` 并在读取前显式 `publisher.close()`（毒丸排空 + join 为现成同步点）；走全局单例的路径则在读取前调用 `_reset_global_publisher_for_testing()` 强制有界排空。**严禁**在 `emit` / `execute_job` 返回后直接读文件——该竞态同时会让 MUT-4 产生假阴性。
>
> **sink 隔离铁律（红队三轮 B3）**：`tests/conftest.py` 提供 autouse fixture，经 `AVA_EVENTS_ROOT` 环境变量（或构造参数 `data_root=`）将 publisher 落盘根定向 `tmp_path`；T8 断言整个测试会话对真实 `paths.ROOT/data` 零写入。

| 编号 | 测试用例名 | 覆盖场景 | 验证断言 |
|---|---|---|---|
| **T1** | `test_job_state_machine_transitions` | Job 五状态合法流转（pending→running→succeeded/failed, pending→blocked） | 断言状态字段变化合法；断言非法流转（如 succeeded→running）抛出 `ValueError` |
| **T2** | `test_create_job_validation_and_blocked` | 白名单校验失败（如 `--force`、未注册模块）直接 blocked | `job.status == JobStatus.BLOCKED`，`events.jsonl` 记录 `job_blocked` 事件 |
| **T3** | `test_execute_job_and_events_trail` | 正常子进程执行与事件流完整性 | 执行成功后 `events.jsonl` 严格按序包含 `job_created`、`job_started`、`job_finished` 三条确定性事件；**读首行原始字符串正则断言首个键为 `"episode"`**（验证确定性排序） |
| **T4a** | `test_child_process_killed_by_signal` | 外部 SIGKILL 强杀子进程（**核心验收 1**） | 向子进程发送 SIGKILL，断言 `execute_job` 正常返回不抛异常，返回值 `job.status == JobStatus.FAILED`，`job.returncode == -9`，事件层完整读出 `job_started` 与 `job_finished` |
| **T4b** | `test_parent_process_interrupted_by_sigint` | 父进程被 KeyboardInterrupt 中断（**核心验收 2**） | 模拟父进程捕获 SIGINT，断言 `execute_job` 捕获后杀掉子进程、抓取 tail 日志、发射 `job_finished` 并向外 re-raise `KeyboardInterrupt` |
| **T5** | `test_sidecar_fail_silent_on_disk_error` | 磁盘只读 / 卷离线故障注入（**核心验收 3**） | 传入独立 publisher 并在写盘处抛出 `OSError`，断言连续失败后熔断器激活（`_circuit_broken_until > 0`），后台线程存活，`execute_job` 照常完成且不抛异常 |
| **T6** | `test_queue_congestion_drop_on_full` | 队列拥塞与丢弃（Drop-on-Full） | 构造容量为 2 的队列并不启动消费线程，连续 emit 10 次，断言主线程不阻塞且耗时 < 5ms，累加丢弃计数器 |
| **T7** | `test_run_pipeline_backward_compatibility` | 原生 `run_pipeline` 签名与字典返回兼容，无幽灵 Job 泄漏 | 断言 `confirmed=False` 时不写 `events.jsonl`；`confirmed=True` 时全量回归 47 项既有工具测试（实测 `--collect-only`），返回字典字段无缝吻合 |
| **T8** | `test_no_write_to_real_data_root` | 测试进程对真实审计日志零污染（红队三轮 B3） | conftest fixture 记录 `paths.ROOT/"data"/_events.jsonl` 会话前快照，会话结束断言该文件未被创建/修改；同时断言 `AVA_EVENTS_ROOT` 已指向 `tmp_path`。**并发会话 caveat（红队四轮）**：若测试运行期间本机另有 REPL/agent 会话在写真实日志，快照断言可能假红，需人工甄别；或收紧为「会话前不存在则断言会话后仍未创建」 |

### 7.2 变异检验方案（Mutation Testing Matrix）

遵循 AGENTS.md 十二节测试纪律，所有关键断言必须经受变异检验（故意将实现改坏，证明测试必然变红）：

| 变异编号 | 注入变异（故意写坏代码） | 预期变红的测试用例 | 证伪机理（为什么必须红） |
|---|---|---|---|
| **MUT-1** | 将 Sidecar 的 `queue.put_nowait` 改为阻塞式的 `queue.put(block=True, timeout=5)` | `test_queue_congestion_drop_on_full` | 队列占满时主线程发生阻塞超过 1 秒，测试设定的 5ms 阈值超时熔断 |
| **MUT-2** | 移除 Sidecar 的熔断器逻辑（`self._failure_count` 不累加或不短路） | `test_sidecar_fail_silent_on_disk_error` | 写盘持续异常时未激活短路，断言 `pub._circuit_broken_until > 0` 失败变红 |
| **MUT-3** | 在 `pipeline/jobs.py` 顶层添加一行 `import numpy as np` | `test_jobs_module_pure_and_no_numpy_in_hot_path` | 独立子进程纯洁性探测探针捕获 `numpy in sys.modules` 导致子进程返回非零退出码 |
| **MUT-4** | 在子进程被杀时跳过发射 `job_finished` 事件或不回填 tail | `test_child_process_killed_by_signal` | `events.jsonl` 中缺失终止事件或 tail 字段为空，轨迹还原测试断言条目与字段校验失败 |
| **MUT-5** | 将 `events.jsonl` 的 `sort_keys=True` 序列化移除 | `test_execute_job_and_events_trail` | 读取首行 raw string，首个键不再以 `"episode"` 起头，正则比对断言失败变红 |
| **MUT-6** | 在 `run_pipeline(..., confirmed=False)` 中恢复调用 `create_job` | `test_run_pipeline_backward_compatibility` | 预检阶段违规在 `events.jsonl` 中留下 `job_created` 事件，幽灵 Job 探测断言变红 |

---

## 8. 施工与 PR 划分（Step-by-Step Implementation Plan）

### PR1：数据模型、状态机与基础落盘（底座就绪）
- **范围**：
  - 新建 `pipeline/jobs.py`，实现 `JobStatus`、`EventType`、`Job`、`Event` 数据结构；
  - 实现基础的 `to_jsonl_line()` 确定性序列化；
  - 实现内存版状态机流转与单元测试 `tests/test_jobs.py:test_job_state_machine_transitions`。
- **验证命令**：`uv run pytest tests/test_jobs.py -k "state_machine"`

### PR2：异步事件 Sidecar、fcntl 文件锁、毒丸排空与故障自愈（Sidecar 就绪）
- **范围**：
  - 在 `pipeline/jobs.py` 中实现 `EventPublisher`（Daemon 线程、有界 Queue、Drop-on-Full、Circuit Breaker、atexit 毒丸排空、fcntl 文件锁），含 `data_root` / `AVA_EVENTS_ROOT` sink 覆写通道与 `episodes_root` init 缓存（红队三轮 B3/M3）；
  - 实现 `events.jsonl` 安全落盘与外置盘未挂载防护（严禁 mkdir）；
  - 导出 `_reset_global_publisher_for_testing` 并编写 T5（磁盘故障注入）、T6（队列拥塞丢弃）测试用例；
  - `tests/conftest.py` 新增 autouse fixture：经 `AVA_EVENTS_ROOT` 将事件 sink 定向 `tmp_path`，并断言测试会话对真实 `paths.ROOT/data` 零写入（T8，红队三轮 B3）。
- **验证命令**：`uv run pytest tests/test_jobs.py -k "sidecar or queue"`

### PR3：进程管道执行器与 `run_pipeline` 兼容层改造（核心置换）
- **范围**：
  - 在 `pipeline/jobs.py` 中实现 `create_job`、`cancel_job` 与 `execute_job`，迁移并复用 `_TailBuffer` 与 `_drain` 双管排水逻辑；
  - `execute_job` 提供 `popen_factory: Callable | None = None` 依赖注入（函数体内调用时解析，红队三轮 B1——默认参数在 def 时绑定必然穿透运行期 mock），同步将既有 `tests/test_agent_pr6.py:340` mock 目标对齐为 `pipeline.jobs.subprocess.Popen`；**迁移清单显式追加：FakeProc（`test_agent_pr6.py:321-330`）补 `pid = 12345` 属性与 `poll()` 桩**——否则 `job.pid = proc.pid` 抛 AttributeError（不在 `except (FileNotFoundError, PermissionError, OSError)` 捕获面内），中断路径 `proc.poll()` 同样 AttributeError；
  - 改造 `pipeline/agent/tools.py:run_pipeline` 为基于 `jobs.py` 的适配器（明确 `confirmed=False` dry-run 守卫）；
  - 跑通 T4a、T4b（子进程强杀与中断轨迹还原）与 T7（tools 兼容性与幽灵 Job 防御测试）。
- **验证命令**：`uv run pytest tests/test_jobs.py tests/test_agent_tools.py tests/test_agent_pr6.py`

### PR4：CLI 入口全面贯通、端到端集成与门禁固化（全线闭环）
- **范围**：
  - 检查核对 `cli.py` 中三处 `run_pipeline` 调用点（行 583、1012/1057、1178），确保在实际 REPL `/run` 和命令行直发时事件准确落盘到期目录 `events.jsonl`；
  - 在 `status_card.py:282 log_approval_decision` 内以函数级延迟 import 补充 `APPROVAL_RESOLVED` 同源双写，一处覆盖 `cli.py:637-642`（LLM 拒执）、`cli.py:1043-1045`（EOFError）、`cli.py:1050`（按 n）全部三个拒执出口（红队三轮 M1）；**emit 必须置于 `status_card.py:296-297` 的 `if not ep_dir: return` 早退之前**（`approvals.jsonl` 仍按原逻辑跳过），否则无期会话拒执依旧黑洞（红队四轮钉死条件）；
  - 补齐所有变异检验用例（MUT-1 至 MUT-6）；
  - 更新文档索引 `docs/dev/plans/README.md`。
- **验证命令**：`uv run pytest tests/test_jobs.py tests/test_agent_cli.py tests/test_docs_invariants.py`

---

## 9. 验收门禁清单（Accept Gates）

- [ ] **门禁 1（状态机完备性）**：Job 状态机流转覆盖五状态，非法转移当场抛出明确异常；
- [ ] **门禁 2（崩溃现场可还原）**：在子进程运行期间通过系统信号发送 `SIGKILL` 强杀子进程，`data/episodes/<期>/events.jsonl` 中能够完整读出 `job_started` 与 `job_finished`（状态标为 failed，记录真实退出码与尾部 tail 日志），无半行或损坏数据。**前置条件（红队三轮 B2）**：读取前必须由调用方 `publisher.close()` 或 `_reset_global_publisher_for_testing()` 完成有界排空，严禁以竞态方式验收；
- [ ] **门禁 3（极端故障静默）**：在模拟磁盘只读或空间耗尽环境下执行 Job，流水线命令正常跑完且终端输出无阻断，Sidecar 触发熔断保护，后台线程稳定存活；
- [ ] **门禁 4（真相源零污染）**：`pipeline/status.py` 源码不出现任何 `events.jsonl` 或 `jobs` 的引用，状态推导依然 100% 依赖落盘物理产物；
- [ ] **门禁 5（零外部重依赖）**：运行独立子进程纯洁性测试，断言 `sys.modules` 中绝对不存在 `numpy`、`torch`、`moviepy`；
- [ ] **门禁 6（既有测试零回归与零幽灵）**：`tests/test_agent_cli.py`、`tests/test_agent_tools.py`、`tests/test_agent_director.py` 全部 100% 通过；`confirmed=False` 预检调用不向 `events.jsonl` 泄漏未决 Job；测试进程对真实 `paths.ROOT/data` 零写入（T8）；
- [ ] **门禁 7（文档门禁全绿）**：`uv run pytest tests/test_docs_invariants.py` 全绿通过。

---

## 10. 潜在红旗与自纠预案（Red Flags & Remediation）

| 风险序号 | 潜在红旗（Red Flag） | 根因与危险性 | 预案与防御动作 |
|---|---|---|---|
| **RF-1** | **细粒度逐行刷日志进 `events.jsonl`** | 将 stdout 每行输出都包装为 `job_output` 事件写入，渲染 ffmpeg 输出数万行导致磁盘写爆、UI 卡死 | **坚决禁止逐行落盘**。`events.jsonl` 只记录 Job 生命周期节点（created/started/finished）及尾部 4KB tail 摘要；实时流式查看走标准管道透传 |
| **RF-2** | **自动在未挂载卷上递归 `mkdir`** | 当外置 SSD 脱卸时，写入 `events.jsonl` 触发自动 `mkdir`，在内置系统盘挂载点下创建幽灵目录，导致插盘后不可见 | 严格遵循 `paths.py` 纪律：若 `episode_dir` 在磁盘上不存在，事件 sink 直接短路静默放弃，严禁调用任何 `mkdir()` |
| **RF-3** | **退出时进程被 Sidecar 线程 hang 住** | Sidecar 线程未设 `daemon=True`、毒丸哨兵被队列丢弃、或 `close()` 使用了无界阻塞 `join()`，导致 CLI 退出时挂死 | 强制标记 `daemon=True`，采用毒丸协议退出（满队强塞哨兵），退出清理有界超时封顶 0.5s，超时直接放弃 |
| **RF-4** | **双头推导偷渡** | 开发者为了在 UI 里偷懒，在 `status.py` 里直接读 `events.jsonl` 倒推工序 | 设立源码静态检查门禁，禁止 `status.py` import `jobs`，保留唯一物理产物推导单源 |
| **RF-5** | **幽灵 Job 假死桌面端** | 预检阶段无脑生成 `job_id` 落盘，用户按 `n` 取消后留在 events.jsonl 中形成永久未决悬空任务 | `run_pipeline(..., confirmed=False)` 仅做纯 dry-run 校验，零事件发射；按 n 拒执直接发射 `APPROVAL_RESOLVED(decision="rejected")`，异步 Job 取消调用 `cancel_job` 写入终态 |
| **RF-6** | **多进程并发写共享事件日志交织损坏** | 多个无期命令并发写入 `data/_events.jsonl` 跨越 4KB 缓冲边界导致 JSON 文本交织损坏 | 在 `_write_event_safely` 写盘时全程由 `fcntl.flock` 排他文件锁守护，确保单行原子落盘 |
| **RF-7** | **父进程被信号杀死时队列内事件蒸发** | `atexit` 不响应 SIGKILL/SIGTERM 默认 handler，daemon 线程随主进程死亡即弃；ADR-0020 §4 桌面端 host 以 SIGTERM 停 core 是标准操作，此时最后一个 `job_finished` 若在队列中即永久丢失，轨迹还原承诺在该场景失效 | **已知上限声明**：父进程被信号杀死时未落盘事件不保证持久；SIGTERM 优雅关闭 handler（`signal.signal(SIGTERM, lambda *_: (close(), sys.exit(1)))`）列为 Spec 3 桌面端落地时的升级项，本期不实现（红队三轮 M6） |
