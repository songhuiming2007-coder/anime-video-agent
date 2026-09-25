# Implementation Spec：Electron 桌面端架构冻结稿（Spec 8 / ADR-0020 §4）

日期：2026-09-23（**v0.5**，红队一轮修订（5🔴 + 8🟡 + 11🔵 全收）+ 二轮复审修订（1🔴 + 8🟡 + 10🔵 全收）+ 三轮定向复审修订（1🔴 + 1🟡 + 6🔵 全收，另作者自查 1 项）+ 定向核对 🟢（4🔵 全收，红队明示无需再审）；状态：**PR0–PR3 可动工**；PR4 阻塞于 Spec 3 v0.6 PR3 施工（§6.2），Spec 3 §1.3–§1.6 的定向复核**尚未进行**）  
上位文档：`docs/dev/plans/2026-09-22-harness-evolution-direction.md`（§2 Spec 8 范围全文、§4 施工红线八条、§5 明确排除），`docs/dev/adr/0020-harness-eventization-and-electron-desktop.md`（§4 桌面端、§5 Context 纪律、「不做的事」、推翻条件）  
契约依赖：`docs/dev/plans/2026-09-22-jobs-and-events-spec.md`（Spec 2 v0.4）、`docs/dev/plans/2026-09-22-approval-objectification-spec.md`（**Spec 3 v0.6**，已并入本 spec 提出的 S3-R1/R2/R5/R6/R7/R9/R10/R11，v0.6 按本 spec 红队三轮 R3-M1 在 S3-R9 授权范围内收紧 §4.5；其中 S3-R9 改 `pipeline/review.py`，经用户单独授权）——**两者均未施工**（撰写时 `pipeline/jobs.py`、`pipeline/approvals.py` 不存在，已核实），本 spec 以其文档契约为准，缺口见 §6  
格式范本：Spec 2 v0.4、Spec 3 v0.2  
性质：**架构冻结稿**（direction：「最后做，可先只写架构 spec」）。冻结进程模型、通信协议、读写边界、契约与验收门禁；不细化 React 组件实现。

---

## 0. 一句话设计

**UI 是一面只读的镜子加一组显式的按钮：读走文件，写走 core，状态问 status，ack 绑定对象。**  
新增仓库内子目录 `desktop/`（Electron 44 + React 19，版本钉死见 §2.1），三进程：main（单实例单窗口、只读媒体协议 `ava-media://`、端口撮合、导航与调试口封锁）、host utilityProcess（期列表、`events.jsonl` 轮询 tail + snapshot 首载、`approvals_store.json` 直读、**唯一的 spawn 点**）、renderer（纯 UI，零 Node）。两条写不变量分开写：**I1** host 对 `data/` 零写入、零 mkdir；**I2** 由桌面端引发的 core 写入只有「自愈簿记」与「显式点击」两类，且各自的触发时机闭集冻结（§2.12）。工序状态一律 spawn `python -m pipeline.status <期> --json` 取得。ack 以 `--id <approval_id>` 绑定到人看过的那个对象（Spec 3 S3-R6）；对象库与事件行里的纳秒 `mtime_ns` 一律无损解析为 bigint（§3.1 规则 8），指纹比较与 argv 不经过 double；05 的解封物由 core 在同一个文件描述符上原子核验指纹后写出（`review --approve --expect-size=… --expect-mtime-ns=…`，Spec 3 S3-R9），物理闸门只可能为人审过的版本打开；ack 后以磁盘对象库做后置核验。打包开 asar 完整性、9 位 fuses 全部定值、`publish: null`、拒绝调试口启动，并以构建溯源横幅让「源码改了再重建」可见。本 spec 的 PR 对 core 零改动；依赖的 core 改动全部经 Spec 3 修订请求走（§6.2）。

---

## 1. 红队裁决与修订纪要

### 1.4 定向核对裁决与收口（v0.4 → v0.5，🟢 + 4🔵 全收）

核对结论：R3-B1、R3-M1 均已闭环，无新阻塞项；Spec 8 的 B1–B5、R2、R3 三轮全部闭环，PR0–PR3 可动工。红队明确说明：它只在 Spec 8 需要的范围内核对过 Spec 3 的 §4.1、§4.2、§4.5、T18–T21，**没有**按 Spec 3 自己的格式完整审过其 §1.3–§1.6，该复核不能当作已完成。4 条 🔵 红队建议施工时顺手改，本版直接收入文档，不再开复审。

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🔵 m1 | 规则 9 只管 host 往外发的那一层：host 内部的诊断、spawn 日志、快照去重若对 `ApprovalRecord` 调 `JSON.stringify` 会抛 TypeError，抛在 decide 路径上就中断这次操作；`toWire<T>(v: T): T` 的签名让 typecheck 分不清内部形状与线上形状 | **采纳** | 签名改为 `toWireApproval(r: ApprovalRecord): ApprovalJson`、`toWireEvent(e: EventRecord): EventJson`；新增 `stringifyLossless`（bigint replacer）；规则 9 冻结「host 与 shared 内所有 JSON 序列化只经 `toWire*` 或 `stringifyLossless`」；新增静态守卫 TG-9、MUT-55 |
| 🔵 m2 | host 侧指纹校验没有测试能单独触发：TA-9 变体 2（HEAL 前改写）返回 `E_STALE`，是因为 heal 先把 A 转成 SUPERSEDED、`ackable` 判不可 ack，根本没走到 `fingerprintsMatch`；删掉那一行也不会有测试变红 | **采纳（两处都改）** | 变体 2 的描述改为「经 `ackable` 返回 `E_STALE`」；新增 `after-heal` 测试钩子（heal 之后、读对象库之前），TA-9 变体 3 在此改写产物 → A 仍为 PENDING、`ackable` 通过，只有 `fingerprintsMatch` 能返回 `E_STALE`、零 ack spawn；新增 MUT-53 |
| 🔵 m3 | 规则 8 的 `typeof v === "number"` 与退路 S3-R12 放在一起有隐患：若将来 `mtime_ns` 改存字符串，字符串原样通过 reviver，bigint 与 string 比较恒假，每次 ack 都是 `E_STALE` 且不报解析错误 | **采纳（比建议再收紧一步）** | 规则 8 改为：`mtime_ns` 键的值只要不是 JSON 数字（字符串、null、对象……）就抛错，整份按解析失败处理——形状一变就响亮失败，而不是静默失配；TE-13 增 ⑦、MUT-54；§6.2 的 S3-R12 行同时写明：启用时必须在同一次修订里改规则 8 与 TE-13 |
| 🔵 m4 | 假设 10 的旁证：据红队所知 source text access 在 V8 11.4 / Chrome 114 起默认开启，Electron 44 大概率支持，但无本机证据 | **记录，不改设计** | 假设 10 保持「约」；启动自检与 TS-2 仍是唯一可靠依据 |

### 1.3 第三轮红队定向复审裁决与修订纪要（v0.3 → v0.4，1🔴 + 1🟡 + 6🔵 全收，另作者自查 1 项；原裁决「🔴 暂不能动工」）

复审结论：R2-M1/M2/M3/M5/M6 闭环；R2-M4 闭环附 🔵；R2-B1 在 core 侧设计成立，但桌面端到 core 的指纹链在 JSON 解析处断开。以下每条均已独立复核（附表「v0.4 补核」各行）。红队要求修完后只对 R3-B1、R3-M1 做定向核对。

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🔴 R3-B1 | 纳秒 mtime（约 1.79e18，大于 2^53）经普通 `JSON.parse` 丢精度：host 从对象库与事件行读到的 `mtime_ns` 永远不等于 `statSync(bigint)` 的值——§2.5 指纹校验恒 `E_STALE`；`--expect-mtime-ns` 传有损值，core 恒退 1；`gateMatches` 恒失配；H5 每次误触发。v0.2 附表「Node 读出相等」比的是 stat 对 stat，没有走对象库这条真实通路，违反「期望值先跑」 | **采纳** | 本机复现（Node v26.10.0）：Python 写 `…927676`，普通解析得 `…927700`、`Number.isSafeInteger` 为 false；带 `context.source` 的 reviver 还原为 `…927676n`，与 stat 相等。§3.1 新增规则 8（无损解析，冻结）、规则 9（出 host 的协议面一律十进制字符串）；新增 `shared/losslessJson.ts`；host 启动自检 fail-closed（拿不到 `context.source` 即判 approvals 能力缺席，决策条只读，假设 10）；argv 一律 `String(bigint)`；新增 TE-13，TA-2 追加「不可被 double 精确表示」的夹具断言；新增 MUT-45/46/47；附表该行改为「经 JSON 对象库通路比对」。只改本 spec，不改 core；退路 S3-R12（`mtime_ns` 改存字符串）只在自检失败时提出（§6.2） |
| 🟡 R3-M1 | S3-R9 在 fstat 与 read 之间有窗口：遇到原地覆盖写（同一 inode），fstat 看到 F1、read 读到 F2，解封物是 F2 的字节配 F1 的 mtime；`render.py:1068` 只看段级 diff（`align.py:210-235`），闸门为未审的 F2 打开 | **采纳** | 本机复现：同 inode 原地覆写后 read 得 F2，读后 fstat 的 mtime 已变。已在 S3-R9 授权范围内收紧，并入 Spec 3 v0.6 §4.5 第 3 步：读后再 `os.fstat` 一次，(size, mtime_ns) 须与第 2 步相同且 `len(bytes) == size`，否则退 1、不写文件；Spec 3 新增 T20 ⑥、MUT-24。本 spec PR4 的前置条件同步改为 Spec 3 v0.6 |
| 🔵 m1 | H5 没有基线：已被新一轮取代的旧 REJECTED 对象，钉住的指纹与磁盘永久不一致，每次设为活跃都多一次 heal | **采纳** | H5 基线 = 设为活跃期后的第一次采样（该次不触发）；H5 只看每种停机点**最新**的那个对象，且仅当它是 PENDING/REJECTED；TA-11 前置条件加「A 带一条历史 REJECTED」；TE-14、MUT-48/49 |
| 🔵 m2 | H5 在返工期间对每个中间版本各触发一次，反复弹出半成品 | **采纳** | 稳定期：同一指纹元组在连续两次采样中都出现才触发；TE-14、MUT-50。两次写入间隔大于采样周期时仍会多触发一次，如实记入 RF-24 |
| 🔵 m3 | H5 用对象库里的路径拼期目录再 stat，缺段检查 | **采纳** | 复用 §3.5 规则 1 的段检查：拒绝 `..`、绝对路径、空段与 `\0`，被拒路径计入诊断、不 stat；TE-14、MUT-51 |
| 🔵 m4 | repoRoot 切换时若有 decide/heal 在途，能力探针与期映射会在中途被换掉 | **采纳** | 两段守卫：main 弹框前先问 host，有 decide/heal 在途 → `E_BUSY`、不弹框；确认后 host 进入「切换中」，拒绝新 decide（`E_BUSY`）、不启动新 heal，等在途 spawn 结束后再换；只读 spawn（STATUS 等）的结果按 repoRoot 代号丢弃（§2.10）。新增 TA-12、MUT-52 |
| 🔵 m5 | TA-9 改写后的 manifest 若不合法，`current_step` 不停在 03.5，自愈不会新建 B | **采纳** | TA-9 写明：改写后的 `03-audio/manifest.json` 仍须合法（只改一段时长），并前置断言改写后 `status --json` 的 `current_step` 仍为 03.5 |
| 🔵 m6 | TI-2 的钩子在 renderer 与 main 两侧，TG-6 只查 renderer 不够 | **采纳** | TI-2 写明两侧钩子；TG-6 的静态断言覆盖 `main/`、`host/`、`renderer/` 下全部钩子 |
| 🔵 自查 | §4.3 伪代码里 `testHook("after-fingerprint-check")` 位于对象与指纹校验**之前**，名不副实：TA-9/TA-9c 在该钩子处改写产物，会先被 host 自己的指纹校验以 `E_STALE` 拦下，core 侧 `--id` 与 `--expect-*` 两道防线根本走不到。TA-9 断言的是 `E_CORE`，第一次实跑即红，属于文档自相矛盾，不会静默放过 | **作者自查（红队三轮未提）** | 钩子移到全部 host 侧校验之后、第一个 spawn 之前（§4.3） |

### 1.2 第二轮红队复审裁决与修订纪要（v0.2 → v0.3，1🔴 + 8🟡 + 10🔵 全收；原裁决「🔴 暂不能动工」）

复审结论：B2、B3 闭环；B1 对象层闭环、物理闸门层未闭环；B4 基本闭环；B5 闭集成立但覆盖不全。以下每条均已独立复核（附表「v0.3 补核」各行）。

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🔴 R2-B1 | `E_GATE_MISMATCH` 出现时闸门已为未审版本打开：host 核对 F1 后 spawn `REVIEW_APPROVE`，`review.py` 读文件前被 `--refit` 成段级对齐的 F2，`copy2` 复制 F2，下次自愈把替身对齐为 APPROVED，渲染闸门打开；警告只存在于一次 RPC 响应里，无持久痕迹 | **采纳方案 (a)（用户 2026-09-23 授权改 `pipeline/review.py`）** | 向 Spec 3 提 S3-R9 并入 v0.5 §4.5：`review --approve` 增加 `--expect-size=<N> --expect-mtime-ns=<M>`，只打开源文件一次、在 fd 上核对指纹、只用核对过的字节写出解封物；解封物要么是 F1、要么不存在。`REVIEW_APPROVE` 模板带上对象钉住的指纹（§3.4）；host 的 `gateMatches` 保留为事后核验，正常情况下不可达。§2.5 删去「端到端成立」的越界表述，改为分层写明。新增 TA-9c、MUT-39 |
| 🟡 R2-M1 | TA-9 对 05 的预期与 §4.3 顺序矛盾：05 在 PENDING 下第一个 spawn 是 `REVIEW_APPROVE`，走不到 `APPROVE --id A`，MUT-25 第二条证伪依据不成立 | **采纳** | TA-9 改用单步的 03.5（改写 `03-audio/manifest.json`）；05 的竞态单列 TA-9c，按 S3-R9 断言「core 退 1、解封物不存在、闸门关闭」 |
| 🟡 R2-M2 | MUT-12 被收紧后的规则 2 掩盖：跨 root 的 `%2F` 注入被规则 2 拦成 403，TS-3 只断言「≠200」，去掉规则 1 仍绿 | **采纳** | TS-3 增加只有规则 1 能拦住的 root 内部 `%2F` 用例（`ava-media://episodes/<EP>%2F02-script.md`，断言 400）；MUT-12 机理改为「该 URL 返回 200 或 404 而非 400」 |
| 🟡 R2-M3 | MUT-13b 被「每次撮合只接受一次」掩盖：伪造端口晚于真实端口到达 | **采纳** | TI-2 改为在重新握手窗口内注入：受 `!app.isPackaged` 守卫的测试钩子先把 renderer 置于「等待端口」，让 iframe 先投递伪造端口，再由 main 投递真实端口；MUT-13b 机理同步改写 |
| 🟡 R2-M4 | heal 闭集漏掉两条最常见路径：后台期切回活跃期（已订阅，H1 不触发）；同一停机点「打回 → 返工」（`current_step` 不变，H2 不触发） | **采纳** | H1 改为「设为活跃期」（含首次打开、切回、重连），新增方法 `episode.activate`；新增 H5：活跃期中对象库里 PENDING/REJECTED 对象的 `artifacts` 指纹与磁盘不一致时边沿触发一次（路径从对象库读，TS 不复制业务映射；heal 不改产物，无反馈环）。TA-11 增加「切回 A」「05 打回后返工」两个场景；新增 MUT-40/41 |
| 🟡 R2-M5 | `app.setRepoRoot` 让 renderer 递交文件系统路径，renderer 被攻破即可把所有 spawn 指向仿造仓库，后果从「误 ack」升级为本机代码执行 | **采纳** | repoRoot 选择整体搬到 main：原生 `dialog.showOpenDialog` + `dialog.showMessageBox` 二次确认；renderer 只能发无参数的 `app.requestRepoRootChange`。TI-8 增加断言：任何方法带路径参数一律拒绝；新增 MUT-42 |
| 🟡 R2-M6 | 构建溯源看不见被 gitignore 的 `desktop/node_modules/`：手改依赖后构建，无任何横幅 | **采纳** | 发布构建脚本第一步 `npm ci` 从 lockfile 干净重装，冲掉对 `node_modules` 的一切手改；`build-info.json` 记录 `package-lock.json` 的 sha256，健康面板与当前仓库的 lockfile 比对。TS-8 增加子场景 TS-8b；新增 MUT-43 |
| 🟡 R2-M7 | Spec 3 v0.4 冻结的加锁顺序自我死锁（跨 spec，卡住 PR4） | **采纳** | 向 Spec 3 提 S3-R10 并入 v0.5：拆出 `_self_heal_locked`，公共函数一次进锁；新增 Spec 3 T21「每次公共调用 2 s 内返回」。本 spec PR4 前置条件同步为 Spec 3 v0.5 PR3 且 T15–T21 全绿 |
| 🟡 R2-M8 | 确认路径的「决策延迟」可能是「打开桌面端的时刻」，污染审批疲劳判据；RF-12 仍然说大了 | **采纳（确定性规则，无阈值）** | 向 Spec 3 提 S3-R11 并入 v0.5：只有 artifact 对齐发生在本次 `approve` 调用的自愈中（即桌面端同一次 decide 里先跑第 ① 步），才记延迟；否则记确认但延迟为空。RF-12 按此如实改写 |
| 🔵 m1 | `generateFuseConfig` 实际到 298 行 | **采纳** | 改为 `266-298` |
| 🔵 m2 | `identity: null` 下整个 bundle 不签名，只有 fuses 对主二进制 ad-hoc 重签，`codesign --verify --deep --strict` 很可能不过；`identity: "-"` 会被 `macCodeSign.js:227` 的子串匹配误选含连字符的身份 | **采纳** | 保持 `identity: null`；假设 1 的验收标准改为「能启动；记录 `codesign -dv` 输出；记录重建后的 TCC 行为」 |
| 🔵 m3 | 调试口检查是黑名单 | **采纳** | 打包版改为 argv 白名单：除可执行路径与 macOS 可能附带的 `-psn_*` 外，出现任何参数一律拒绝启动；TS-6 增加任意未知开关用例；新增 MUT-44 |
| 🔵 m4 | H2 基线未定义；05 批准后工序 05→06 会触发一次 H2，TA-2/2b 的「恰为」序列会时好时坏 | **采纳** | H2 基线 = 设为活跃期后的第一次 status 采样，该次不触发；spawn 日志逐条带 trigger 标签与 decide 关联号，TA-2/2b 只统计该次 decide 关联的 spawn |
| 🔵 m5 | I2 只列了 `approval_requested`，heal 的惰性转移还会发 `approval_resolved` | **采纳** | §2.3 I2 措辞补上 |
| 🔵 m6 | `GIT_DESKTOP_DIFF` 在构建提交不存在时退出 128，处理未定义 | **采纳** | 退出 128 → 灰色横幅「无法比对 UI 构建版本（构建提交不在本仓库）」，与「一致」「不一致」分开显示 |
| 🔵 m7 | TA-9 的暂停钩子位于生产 decide 路径 | **采纳** | 全部测试钩子受 `!app.isPackaged` 守卫，纳入 TG-6 |
| 🔵 m8 | `requestHeal` 未检查退出码，失败时用未自愈的对象库继续 | **采纳** | heal 非 0 退出写入诊断面板；ack 流程继续（core 侧 `--id` 兜底） |
| 🔵 m9 | 威胁模型缺一句：裸形态 `/approve --id` 在非 TTY 下可被任何有 shell 的 agent 直接调用 | **采纳** | §2.10 补入：绊线防的是「对已安装 UI 的顺手修改」，不是「agent 代替人去 ack」；后者与 CLI 自身暴露面相同，不在本 spec 能力范围内（RF-22） |
| 🔵 m10 | 开发构建与打包构建的 userData 可能不同（约），单实例锁拦不住二者同时运行 | **采纳** | TI-10 与 RF-21 如实写明；跨实例安全仍由 Spec 3 的按期 flock 兜底 |

补充复审意见：MUT-35 的「反馈环」措辞夸大（Spec 3 只在有变更时 `atomic_write`，不会无限循环），已改为「每次真实变化后多一次 heal」。

### 1.1 第一轮红队裁决与修订纪要（v0.1 → v0.2，5🔴 + 8🟡 + 11🔵 全收；原裁决「🔴 驳回，需重大修订」）

红队报告为终端输出、未落盘，本表即唯一存档。每条指控在采纳前均已独立复核（复核证据见附表「v0.2 补核」各行）。

| 编号 | 红队指控 | 裁决 | 修订动作 |
|---|---|---|---|
| 🔴 B1 | 审批级隔离只在 host 侧成立：argv 只带 stop，core `approve()` 先自愈，指纹漂移时把 A 转 SUPERSEDED、新建替身 B 并批准 B——人看 F1、批准 F2；后置核验给出假原因；Spec 3 §2.2 与 T4 自相矛盾 | **采纳** | 向 Spec 3 提 S3-R6 并已并入 Spec 3 v0.4：裸形态必填 `--id`，指定对象被替换即退出 1、绝不处理替身，T4 已改正。host 侧：ack 前先 HEAL、再按 `obj.artifacts` 重新 stat 指纹（`E_STALE`）；05 第 ① 步后核验 `04-clips.approved.json` 的 (size, mtime_ns) 等于对象钉住的 `04-clips.json` 指纹（`shutil.copy2` 保留纳秒 mtime，已实测），不符则 `E_GATE_MISMATCH` 并停止第 ② 步；`E_UNVERIFIED` 文案改写（§2.6、§3.2、§4.3）；新增 TA-9/TA-9b、MUT-25/26 |
| 🔴 B2 | 05 两步链依赖 Spec 3 未冻结的语义：自愈先把对象对齐为 `APPROVED(artifact)`，显式 approve 是退 1 还是退 0 未定；02.5/05 的显式转移分支永远走不到，决策延迟永不记账，RF-12 兜底说法不成立 | **采纳** | 向 Spec 3 提 S3-R7 并已并入 v0.4：artifact 对齐后的同类型显式 approve 走「确认路径」，退 0，首次确认写 `confirmed_by/confirmed_at` 并调 `log_approval_decision`。§2.6 第 ② 步改为引用该确认路径；RF-12 按实际重写 |
| 🔴 B3 | `--confirm-patch` 重试按钮把补丁段人审闸门变成盲签：段号写在 stdout（`review.py:335`），UI 只显示 stderr；按钮任何失败后都亮；`retryReviewWithConfirmPatch` 流程未定义 | **采纳方案 (a)** | 删除该按钮与对应方法；第 ① 步失败时 stdout、stderr 尾部**都**原样显示（人能看到段号与提示）；含补丁段的期在终端完成 05 批准，列为 v1 已知缺口 RF-18；S3-R8 不提出 |
| 🔴 B4 | 「UI 层 Code Freeze」没有信任根：源码改了重建无告警；asar 与 Info.plist 同 bundle 可一并改再 ad-hoc 重签；无 fuse 禁用 `--remote-debugging-*`，CDP 可注入点击；`settings.json` 的 repoRoot 可被改指别的 core | **采纳（改写威胁模型 + 三条可见性绊线）** | §2.10 如实改写：asar 只挡「已安装包被字节级 patch」。新增：① 构建溯源——构建时把 `git rev-parse HEAD` 与 `desktop/` 是否脏写进包内 `build-info.json`，健康面板经 spawn git 对比，构建自脏树常驻红色横幅、`desktop/` 自构建后有提交常驻黄色横幅；② packaged 版在 main 入口检测 `remote-debugging-port/pipe` 开关即退出；③ repoRoot 变更须 UI 显式确认、顶栏常驻显示。明确这些是**让篡改可见的绊线**，不是防蓄意本地攻击；新增 TS-6/TS-8、MUT-27/36 |
| 🔴 B5 | heal 触发时机未定义：只在 subscribe 时 heal 则新停机点看不到；跟随周期 heal 则打开 app 就在所有期写 `_agent/`、发事件；按对象库变化触发会成反馈环；TI-3 在 Spec 3 施工后必红 | **修正后采纳** | 新增 §2.12 冻结 heal 触发闭集：活跃期 subscribe、**活跃期 `status.current_step` 变化（边沿触发一次）**、用户显式刷新、ack 前；后台期永不 heal，徽标只读 `status --json`。与红队建议的三时机相比多一条「status 边沿」，理由：保住 direction §0.1 旅程第 4 步「停机点主动触达」；它的触发源是 `status --json`（`status.py` 无写操作，红队已 grep 核实），不读对象库，不会形成反馈环。不变量拆为 I1/I2，TI-3 拆为 TI-3a/TI-3b；新增 TA-11、MUT-34/35 |
| 🟡 M1 | 观测缺口呈现不足：queue.Full 丢弃不落盘、`sidecar_degraded` 可能写进别的期、期目录缺席时静默不写、RF-7 蒸发；`finishedEventMissing` 漏 pending 无 pid、truncatedHead、刷盘竞态；§2.11 援引「完整保留」越界 | **采纳** | 时间线常驻「观测层有损（Spec 2 §2.3），轨迹可能不完整」；`sidecar_degraded` 单独渲染、不进本期折叠；pending 超过 `PENDING_STALE_MS` 无后续事件显示「未见后续事件」；`finishedEventMissing` 须连续 2 个 tick 成立；truncatedHead 提示「更早的 job 未载入」；删去 §2.11 的越界援引（§2.4、§3.3） |
| 🟡 M2 | 超时 SIGTERM 只打到 `ava`，孙进程 `review.py` 成孤儿继续写产物；「已从磁盘重读」给出过时结论 | **采纳** | spawn 加 `detached: true`（子进程为进程组组长），超时对进程组 `kill(-pid)`；`E_TIMEOUT` 文案改为「结果未知，请以稍后刷新为准」；新增 TA-10、MUT-28 |
| 🟡 M3 | 打包三处未冻结：`publish` 隐式发布（碰红线 6）、签名身份自动查 keychain、fuses 9 位只定了 7 位且 electron-builder 不映射 `WasmTrapHandlers` | **采纳** | `electron-builder.yml` 冻结 `publish: null`、`mac.identity: null`（源码核实为「跳过签名、不查 keychain」，`MacTargetHelper.js:13-19`）并纳入 TG-5；fuses 表补齐 9 位，`WasmTrapHandlers` 如实声明「electron-builder 26.15.3 不映射，保持 Electron 默认，TS-2 读出实际位值记入期望表」；假设 4 扩展为覆盖「重建后 TCC 授权是否失效」 |
| 🟡 M4 | 单实例、窗口、导航、崩溃上报未冻结；argv 中的打回原文 `ps` 可见 | **采纳** | §2.2 main 职责补：`requestSingleInstanceLock`、单窗口、`setWindowOpenHandler` 拒绝、`will-navigate` 阻止、禁止拖放导航、不调用 `crashReporter.start`（TG-8）且 `crashDumps` 在 userData 内（TI-4 扩展）；RF-19 声明 argv 可见性 |
| 🟡 M5 | 变异矩阵 4 条幽灵断言（MUT-5/6/23 所依赖断言不存在）、MUT-4 机理错误（对已存在目录 `mkdirSync(recursive)` 是空操作） | **采纳** | 补齐被依赖的断言（TE-11 期目录在两次 poll 间被删、TE-12 renderer 多期 reducer、TI-8 exact-keys、TI-9 host 崩溃恢复）；§7.2 逐条改正机理（MUT-2/4/9/12/13b/14/19） |
| 🟡 M6 | TP-2 期望值未先跑（23.976 fps 下断言必红）；TS-3 期望未先跑（standard scheme 会先规范化 dot-segment）；路径守卫真洞：段内 `%2F` 未拒、边界是 dataRoot 而非所选 root，可从 shots 跨读 `data/browser-profile` 与 `memory.md` | **采纳（守卫比红队建议更严）** | 规则 1 增「解码后段内含 `/`、`\` 即拒」；规则 2 改为「realpath 必须是**所选 root** 实路径的后代」，root 内指向 root 外的符号链接一律 403（v1 不开任何例外，PR3 对真实 data 实测，若发现合法跨 root 链接须修订本 spec 显式列出）；TP-2、TS-3 期望值改为「先跑再写」 |
| 🟡 M7 | 打包版无可行 e2e：Playwright 驱动 main 依赖 inspector，被 fuse 关掉；TS-1 未指定篡改哪个文件，懒加载块可能运行中途才崩 | **采纳** | 打包版验证改用非 inspector 手段：main 在 host 就绪时向 stdout 打印一行 `AVA_BOOT host-ready`，测试直接拉起 `.app` 二进制读 stdout；功能 e2e 只跑未打包构建（如实声明）；TS-1 固定篡改 main 入口，另加「篡改 asar 头」子场景 |
| 🟡 M8 | RF-12 有声明无门禁，人时 advisory 的数据源被桌面端系统性掏空；03.5 的结构化打点在桌面端做不了 | **采纳** | 新增门禁 14：人时记账命令落地前，03.5/05 决策条常驻「本次审阅不计人时」，人时类 advisory 旁标「数据源不完整：桌面端审阅不计入」；03.5 批准按钮旁注明「结构化打点须在终端 `/voice` 完成」 |
| 🔵 m1 | 头部契约依赖写 Spec 3 v0.2 | **采纳** | 改为 v0.4 |
| 🔵 m2 | ANCHOR_BYTES 理由写错（sort_keys 下行尾是 timestamp + type） | **采纳** | 理由改为「行尾含微秒级 timestamp」 |
| 🔵 m3 | snapshot 一次读 32 MiB 与 READ_CHUNK 理由矛盾 | **采纳** | snapshot 按 4 MiB 分块异步读、块间让出事件循环 |
| 🔵 m4 | `approval_resolved` 两种载荷形状未区分 | **采纳** | §3.1 规则 6：无 `approval_id` 的标「命令卡拒执（非停机点）」 |
| 🔵 m5 | TI-5 对拍盲区：Python `is_dir()` 跟随符号链接（`cli.py:135`），Node `Dirent.isDirectory()` 不跟随 | **采纳** | TS 用 `stat`（跟随）判目录；夹具加软链期目录 |
| 🔵 m6 | TC-3 子串 `desktop\|electron` 是脆弱代理指标 | **采纳** | 删除 TC-3；「core 不因桌面端改动」由 TC-4（PR diff 对 `pipeline/`、`config/` 为空）承担 |
| 🔵 m7 | epKey 映射滞后：期目录改名后传旧绝对路径，`resolve_episode_target` 走到 `rglob(<绝对路径>)`（`cli.py:1095`）抛 NotImplementedError | **采纳** | 每次 spawn 前 stat 期目录，不存在返回 `E_STALE` 并触发期列表刷新 |
| 🔵 m8 | 「无 running/pending 的期不 tail」与「靠事件判断有无 running」互为前提 | **采纳** | 改为：全部可见期每 5 s stat 一次 `events.jsonl`，size 增长即读增量；不再以事件内容决定是否 tail |
| 🔵 m9 | 媒体 fd 与轮询阻止外置盘正常推出 | **采纳** | reach 转非 ok 时 host 通知 main，main 立即销毁全部媒体流；媒体元素卸载即中止请求；新增 TP-6 |
| 🔵 m10 | 伪代码 resnapshot 重复 | **采纳** | §4.3 只在 finally 里 resnapshot 一次 |
| 🔵 m11 | 「core 零改动」标题误导；TG-7 字面量禁令可被拼接绕过 | **采纳** | 措辞改为「本 spec 的 PR 零改动；依赖的 core 改动经 Spec 3 修订请求」；§6.5 如实写明 TG-7 只防无意识引入，门禁 2 的真防线是真期人工对照 |

**作者自报的未实测假设（红队复审优先攻击面；正文均已标「约」）**：
1. `mac.identity: null` + `resetAdHocDarwinSignature: true` 下 arm64 包能启动（v0.3 按红队 m2 改验收标准：能启动、记录 `codesign -dv` 输出、记录重建后 TCC 行为；不要求 `codesign --verify --deep --strict` 通过——bundle 本身未签名）（§2.10）；
2. `utilityProcess.fork` 可直接加载 asar 内的 host 入口，全部 fuse 打开后仍可拉起（§2.10）；
3. 沙箱 iframe（无 `allow-same-origin`）内 gallery 的剪贴板写入是否可用（§2.7，RF-7）；
4. host 拉起的 Python 子进程访问外置卷时 TCC 权限归属于 app；**ad-hoc 签名每次重建 cdhash 变化后，可移除卷授权是否需要重授**（§2.8）；
5. 临时 repo 副本内 `.venv` 软链后 `import pipeline` 解析到副本（§7.1 夹具）；
6. `standard: true` 的自定义 scheme 在交给 handler 前已规范化 dot-segment（§3.5，影响 TS-3 期望值）；
7. packaged 版检测 `remote-debugging-*` 后退出时，DevTools 端口是否已短暂监听（§2.10，TS-6 如实记录）；
8. 外置盘（T7）文件系统上 `os.utime(ns=…)` 与 `copy2` 同样保留纳秒 mtime，使 05 解封物与 host 事后核验的指纹比对成立（§2.6；APFS 已实测相等）；
9. 开发构建与打包构建的 userData 路径不同，二者可同时运行（§2.2，RF-21；红队 m10）；
10. Electron 44 的 utilityProcess 里，`JSON.parse` 的 reviver 能拿到 `context.source`（V8 的 JSON.parse source text access；系统 Node v26.10.0 已实测支持，但 vitest 跑在系统 Node 上，证明不了 Electron 运行时）。host 启动自检 fail-closed；PR2 首日在 `electron-vite dev` 下记录自检结果，TS-2 在打包版上断言（§3.1 规则 8，红队 R3-B1）。

---

## 2. 关键设计决策（含代码现状证据）

### 2.1 决策 1：目录与技术栈——仓库内 `desktop/`，版本逐项钉死（正面回答预审问题 7）

- **决策**：桌面端放在本仓库 `desktop/` 子目录，自带 `package.json` + `package-lock.json`，与 Python core 同仓同提交；**不开独立仓库**。
- **理由**：
  1. **契约共版本**：桌面端消费的三份契约（Spec 2 Event、Spec 3 Approval、`status.py:19-30 EpisodeStatus`）全部定义在 Python 侧。同仓时「改 schema + 改 TS 消费者 + 改跨语言契约夹具」是一个提交，契约测试当场红；分仓则版本漂移没有任何闸门。
  2. **Code Freeze 边界**：ADR-0018 的 Code Freeze 判据是 `git status --porcelain -- pipeline/`（`cli.py:43`），`desktop/` 的开发改动不触发它。v0.1 曾把 asar 完整性称为「UI 层的 Code Freeze」——**红队一轮 B4 指出这不成立**：asar 只保护构建后的字节，不保护「改源码 → 重建」这条路径。v0.2 改为：已安装包的字节级完整性由 asar 保证；「UI 构建是否来自已提交的源码」由构建溯源横幅保证可见（§2.10）。二者合起来是 UI 层的**可见性**冻结，不是强制冻结。
  3. **打包与测试天然隔离**：`pyproject.toml:85 packages = ["pipeline"]` 使 `desktop/` 永不进 Python wheel；`pyproject.toml:74 testpaths = ["tests"]` 使 pytest 永不收集 `desktop/`；`node_modules/`、构建产物由 `.gitignore` 新增三行排除（PR1）。
  4. **ADR-0018「不引入第三方 Agent 框架」不受影响**：Electron/React 是 UI 框架不是 agent 框架（ADR-0020 §1 已解除 GUI 禁令）；§5.1 用**精确白名单**钉死 npm 依赖集合。
- **版本钉死**（2026-09-23 `npm view` 实测；全部写精确版本号）：

| 包 | 钉死版本 | 选择理由（均已核实） |
|---|---|---|
| `electron` | `44.4.5` | npm `latest` dist-tag；`engines.node >= 22.12.0`（本机 node v26.10.0 满足） |
| `react` / `react-dom` | `19.3.0` | npm latest；ADR-0020 调研中 ZCode 同为 React 19 |
| `electron-vite` | `5.0.0` | 管 main/preload/renderer 三路构建 + host 第四入口（经 main 构建的多 input 配置，约，PR1 实测）；peer `vite: ^5 \|\| ^6 \|\| ^7`（实测）——**因此 vite 不能用 latest 8.x** |
| `vite` | `7.3.6` | 7.x 最新，满足 electron-vite 5.0.0 peer |
| `@vitejs/plugin-react` | `5.2.0` | peer 含 `^7.0.0`（实测） |
| `typescript` | `5.9.3` | 5.x 最新。**不用 7.0.2**：TS 7 是原生重写大版本，与 electron-vite/vitest 类型工具链的兼容未验证 |
| `vitest` | `5.0.1` | peer `vite: ^6.4 \|\| ^7 \|\| ^8`（实测） |
| `@playwright/test` | `1.63.0` | `_electron` 启动器做**未打包构建**的 e2e（该 API 标注 experimental，约；打包版不可用，见 §7.1） |
| `electron-builder` | `26.15.3` | asar 完整性、fuses、publish、签名行为已在其源码核实（§2.10） |
| `@electron/fuses` | `2.1.3` | electron-builder 以动态 import 调用；另供 `verify-fuses` 脚本读 fuse wire |
| `markdown-it` | `15.0.2` | `.md` 预览；构造时**显式**传 `html: false` |

- **目录结构**（冻结到模块粒度）：

```
desktop/
  package.json  package-lock.json  tsconfig.json
  electron.vite.config.ts            # main / preload / renderer / host 四入口
  electron-builder.yml               # asar + 9 位 fuses + publish: null + identity: null（§2.10）
  scripts/verify-fuses.mjs           # 读已打包二进制的全部 9 位 fuse + 复算 asar 头哈希
  scripts/write-build-info.mjs       # 构建时写 out/build-info.json（git HEAD、desktop/ 是否脏）
  src/
    shared/        # 纯 TS：零 Node、零 DOM 依赖，全部可单测
      protocol.ts  contracts.ts  jsonl.ts  fold.ts  mediaUrl.ts  stopPreview.ts  constants.ts
    main/          # 单实例单窗口、协议、端口撮合、host 看护、导航与调试口封锁
    preload/       # 只做一件事：把 MessagePort 转交页面
    host/          # utilityProcess 入口
      episodes.ts  tailer.ts  store.ts  reach.ts  status.ts  heal.ts  gateCheck.ts
      spawner.ts       # 全 desktop/ 唯一 import child_process 的文件
      settings.ts      # 全 desktop/ 唯一写文件的模块（只写 userData）
    renderer/      # React UI
  tests/           # vitest：单元 + host 集成 + 跨语言契约 + 静态守卫
  e2e/             # playwright _electron（未打包构建）
  e2e-packaged/    # 直接拉起 .app 二进制、读 stdout 的打包版验证（§7.1 TS 系列）
```

### 2.2 决策 2：三进程职责切分——业务不进 main，写不进 host，Node 不进 renderer

| 进程 | 职责 | 明令禁止 |
|---|---|---|
| **main** | `app.requestSingleInstanceLock()`（拿不到即退出，第二次启动只聚焦已有窗口）；**恰好一个** `BrowserWindow`，`activate` 时仅在无窗口时重建；`setWindowOpenHandler(() => ({ action: "deny" }))`；`will-navigate` 一律 `preventDefault`（同时封住拖放文件导致的导航）；`protocol.handle("ava-media", …)` 只读媒体协议（Electron 限定协议注册只能在主进程，`electron.d.ts:11593`）；`MessageChannelMain`（`electron.d.ts:9812`）撮合端口；host 看护与重启（§2.9）；出站网络拦截（§2.7）；packaged 版拒绝调试口启动（§2.10）；收到 host 的 reach 变化即销毁全部媒体流（§2.8） | spawn 子进程；解析事件/审批；对 `data/` 的写；调用 `crashReporter.start`（TG-8） |
| **host**（`utilityProcess.fork`，`electron.d.ts:15844`，`serviceName: "ava-host"`） | 期列表；`events.jsonl` 轮询 tail 与折叠；`approvals_store.json` 直读；可达性诊断；heal 调度（§2.12）；**全部 spawn**；RPC 分发 | 对 `data/` 任何写（含 mkdir/rename/unlink/appendFile）；出网；监听端口；自行推导工序 |
| **renderer**（`contextIsolation: true`、`sandbox: true`、`nodeIntegration: false`） | 期列表、产物树、事件时间线、PreviewPane、Approval 决策条、健康面板与横幅 | 任何 Node API；直接读文件（文件内容一律经 `ava-media://`）；直连 host 以外的通道 |

- **通信**：不引入 RPC 框架（ADR-0020 §4）。renderer↔host 直连一条 `MessagePort`（main 只撮合不转发），载荷为 §3.2 的自有信封；host↔main 仅有生命周期消息（`port`、`renderer-reset`、`reach`、`host-ready`、`shutdown`）。**协议自有、方法闭集**：UI 只投影 Spec 2 事件与 Spec 3 approval 对象，不做 ACP/MCP 等通用 agent 协议适配（ZCode v2→v3 教训，direction §5）。
- **core 零 server**：host 通过 `child_process.spawn` 调既有 CLI，core 不为 UI 开任何端口、不加常驻进程。host 自身也不 `listen`（TI-6 以 `lsof` 断言）。
- **崩溃转储**：不启用崩溃上报；`app.getPath("crashDumps")` 必须位于 userData 子树（TI-4），因为内存转储里可能有稿件与打回原文。

### 2.3 决策 3：读写边界——哪些直读、哪些 spawn（正面回答预审问题 2）

**总原则**：**纯读、且格式由上游 spec 冻结的文件，host 直读；凡涉及状态推导、写副作用或业务判断的，一律 spawn core。** TS 侧不许出现第二份业务逻辑。

| 对象 | 方式 | 决策理由（含代码/契约证据） |
|---|---|---|
| `events.jsonl` | **host 直读（轮询 tail）** | ADR-0020 §4 原文「事件流直接镜像 events.jsonl 的增量（tail 式订阅 + snapshot 首载）」；Spec 2 §2.3 保证整行在 `fcntl.flock` 内写入，读端不需要锁，只需保留未完结的半行（§2.4） |
| `approvals_store.json` | **按 §2.12 时机 spawn 自愈 + host 直读** | Spec 3 的 `list_pending` 带 `_self_heal`（Spec 3 §2.2 B2）——直读拿到的是未自愈的状态，所以分两步：spawn `ava <期> /approvals` 让 core 自愈落盘（退出码必须 0，输出丢弃），再直读 JSON。直读安全：该文件经 `paths.atomic_write`（`paths.py:118-128`，`126` 写 `.tmp`、`128` `os.replace`）整文件替换，读端只会看到旧版或新版。desktop **从不打开 `approvals_store.lock`**。**自愈会写 `_agent/`，因此 heal 的触发时机是闭集**（§2.12） |
| 工序状态 | **spawn `python -m pipeline.status <期> --json`** | `status.py:447` 已有 `--json`，`status.py:501-502` 输出 `asdict(EpisodeStatus)`；这是 AGENTS.md「生产开工第一铁律」的同一个命令；`status.py` 全文无写操作（红队 grep 核实）。实测开销 0.03–0.04 s（本机 warm，3 次）；独立解释器里 `pipeline.status` 顶层未载入 numpy/torch（实测 `[]`） |
| 产物树 | **host 直读** `readdir` + `stat` | 纯列举。隐藏项只有 `.DS_Store`、`*.tmp`（`atomic_write` 中间物，`paths.py:126`）、`*.lock` |
| 产物内容 | **renderer 经 `ava-media://` 读**（main 协议处理，§3.5） | 单一读闸：所有文件内容走同一个路径守卫 |
| 期列表 | **host 直读**，规则镜像 `cli.py:122-158 get_episodes_list` | TS 侧唯一一处「复刻 Python 规则」——排除 `.`/`_` 前缀、含 `01-topic.md` 的子期展平（`cli.py:144`）、mtime 降序；判目录用跟随符号链接的 `stat`（对齐 `cli.py:135` 的 `is_dir()`）。代价由跨语言对拍 TI-5 兜住 |
| ack | **spawn 裸形态** `ava <期> /approve <stop> --id <id>` / `/reject <stop> --id <id> <哪段> <问题>` | Spec 3 §4.4 契约：「桌面端 ack 不直写对象库……写路径只有一个」；`--id` 见 Spec 3 v0.4 §4.2 |
| 05 解封物 | **spawn** `ava <期> /run review --approve` | `review` 在白名单（`tools.py:30-40`），走 `run_pipeline`（`tools.py:698`），Spec 2 施工后自动落 job 事件；对象层永不代产解封物（Spec 3 §2.6） |
| `human_time.json`、产物本体 | **desktop 永不写** | v1 人时记账缺口见 RF-12 与门禁 14 |

- **两条写不变量分开写（红队 B5）**：
  - **I1（host 零写入）**：host 进程自身对 `data/` 零写入、零 mkdir。三层守：① 静态——`desktop/src` 内 fs 写类 API 只允许出现在 `host/settings.ts`，目标恒为 userData 子树（TG-2）；② 动态——关闭 heal 后对只读权限夹具树跑全部读功能，前后树清单一致（TI-3a）；③ 动态——期目录在两次 poll 之间被删，poll 后仍不存在（TE-11，MUT-4 的真证伪点）。
  - **I2（桌面端引发的 core 写入闭集）**：只有两类。**自愈簿记**：`_agent/approvals_store.json`、`_agent/approvals_store.lock`、必要时 `_agent/` 目录本身（沿用 Spec 3 §2.1 / `status_card.py:301` 先例，期目录已存在前提下），以及 Spec 2 在场时 `events.jsonl` 追加的 `approval_requested` 行与惰性转移（SUPERSEDED、artifact 对齐）产生的 `approval_resolved` 行（红队 m5）——触发时机闭集见 §2.12。**显式点击**：ack 写入的 `approvals_store.json`/`approvals.jsonl`/`approval_feedback.md`/`events.jsonl`，以及 05 第 ① 步产出的 `04-clips.approved.json`。TI-3b 断言 heal 引起的写入不超出第一类清单。

### 2.4 决策 4：events.jsonl 的 tail 订阅——纯轮询，不用 fs.watch（正面回答预审问题 1）

- **决策：纯 `stat` 轮询**。活跃期每 1000 ms；**全部可见期**每 5000 ms stat 一次 `events.jsonl`，size 增长即读增量（红队 m8：不再以事件内容决定是否 tail）。**不使用 `fs.watch`**。
- **为什么不用 fs.watch**：
  1. `approvals_store.json` 以 `os.replace` 整文件替换（`paths.py:128`），文件级 watcher 绑在旧 inode 上，替换后静默失效；
  2. 外置卷脱卸时 watcher 报错或静默死亡，恢复同样要靠轮询——**轮询是正确性的必要路径，watcher 只是重复一遍正确性逻辑的优化**；
  3. 成本可忽略：`os.stat` 本机实测 ≈1 µs/次（10000 次均值，内置盘）；
  4. 延迟预算：Spec 2 RF-1 禁止逐行刷日志，事件只有生命周期粒度；审批决策以分钟计（Spec 3 §3.3 示例 `latency_s: 312.4`）。
- **每个被 tail 文件的状态**：`{dev, ino, offset, anchor, partial, generation}`。
- **单次 poll 判定顺序（冻结；顺序本身是契约——红队 MUT-2 推演：锚点比对若先于 size 判定，截断会被锚点不符掩盖）**：

| 顺序 | 观测 | 判定 | 动作 |
|---|---|---|---|
| 1 | 期目录或 `data/` 不可达 | 见 §2.8 | 状态 `unreachable`，保留上次快照并标陈旧；**不创建任何东西** |
| 2 | `stat` → ENOENT，期目录可达 | 文件尚未创建 | 状态 `absent`；offset 归零 |
| 3 | `(dev, ino)` 变化 | 文件被替换 | **resync**：`generation += 1`，从 0 重读 |
| 4 | `size < offset` | 截断 | resync |
| 5 | `size ≥ offset` 且 `offset ≥ 64` 且 `[offset−64, offset)` 与记录的锚点不同 | 截断后又被写到更长 | resync |
| 6 | `size > offset` | 正常追加 | 读 `[offset, min(size, offset + 4 MiB))`，拼上 `partial`，按 `\n` 切行；最后一段无换行的留作 `partial`——**半行永不解析、永不下发** |
| — | 某完整行 `JSON.parse` 失败 | 损坏行 | 跳过，`malformed += 1`，不中断 |
| — | `partial` 超过 1 MiB 仍无换行 | 非 JSONL 垃圾 | 丢弃 partial，跳到下一个换行之后继续读，诊断计数（**S21 施工修订，经用户同意**：原「resync」从 0 重读，文件里持久存在的超长行会让每次 poll 都重读一遍，永不前进） |

- **snapshot 首载**：从 0 读到当前 size，**按 4 MiB 分块异步读、块间让出事件循环**（红队 m3）；超过 32 MiB 时只读尾部 32 MiB（从第一个 `\n` 之后开始），标 `truncatedHead: true`，UI 提示「更早的 job 未载入」。折叠成 `JobView[]`（§3.3），连同 `generation`、`offset` 一起下发；此后只下发 delta。
- **snapshot + delta + coalesce**（借 ZCode）：每个 tick 把该期所有新行合并成**一条** delta（`seq` 递增）；renderer 只在 `generation` 相同且 `seq == last + 1` 时应用，否则丢弃本地状态并请求 snapshot。
- **断线的三种含义**：端口断开（renderer 重载或 host 重启）→ 重新 subscribe，拿全新 snapshot；卷脱卸 → `unreachable`；重新挂载 → `dev` 多半已变，resync。
- **事件归属真相 = 文件位置**：一行事件属于哪一期，由它所在的 `data/episodes/<期>/events.jsonl` 决定，不看行内 `episode` 字段（Spec 2 §4.1 在 `_episodes_root` 解析失败时把 `episode` 降级为 `ep_path.name`，同名子期会撞车）。字段与位置不一致只计数（`episodeFieldMismatch`）。**例外：`sidecar_degraded`**——Spec 2 把它写进「熔断恢复后第一条事件」所在期的文件，可能与熔断期丢事件的期不同，因此它**不进本期 job 折叠**，单独渲染为「某进程在熔断期丢了 N 条事件（不限于本期）」（红队 M1）。`data/_events.jsonl`（`_global`）v1 不展示。
- **观测层有损，必须常驻声明（红队 M1）**：Spec 2 的丢法中，queue.Full 丢弃的计数从不落盘（Spec 2 §4.1 `emit` 只累加 `_dropped_count`，`sidecar_degraded` 只上报熔断期计数）、期目录缺席时静默不写且不计数、父进程被信号杀死时队列蒸发（Spec 2 RF-7）——这些在磁盘上**没有任何痕迹**。时间线顶部常驻「观测层有损（Spec 2 §2.3），轨迹可能不完整；工序以 status 为准」。

### 2.5 决策 5：多期并行与串扰隔离（正面回答预审问题 3）

- **期的身份是 `epKey`**：相对 episodes 根的 posix 路径（如 `EGOIST/01-Live`）。`epKey → 绝对路径` 的映射**只存在于 host**；renderer 从不发送文件系统路径，host 收到未知 `epKey` 返回 `E_BAD_REQUEST`，参数多出任何键也拒（exact-keys，TI-8）。**每次 spawn 前 host 再 stat 一次期目录**，不存在即 `E_STALE` 并刷新期列表（红队 m7：期目录改名后传旧绝对路径会让 `resolve_episode_target` 走到 `rglob(<绝对路径>)`，`cli.py:1095`，抛 NotImplementedError）。
- **消息级隔离**：host 下发的每条 push 都带 `epKey`；renderer 状态按 `epKey` 分桶（`Map<epKey, EpisodeState>`），组件只读 `store[activeEpKey]`；不属于任何订阅的消息直接丢弃（TE-12）。
- **媒体级隔离**：切换活跃期时 PreviewPane 卸载全部媒体元素，全局同一时刻至多一个媒体元素在播放（TP-3）。
- **审批级隔离（分两层写，红队 R2-B1 更正 v0.2「端到端成立」的越界表述）**：
  - 决策条渲染时绑定 `(epKey, approvalId, stop)`；ack 请求携带全部三项；
  - host 在 spawn 前：HEAL → 重读对象库，确认 `approvalId` 仍为 PENDING（或处于 Spec 3 定义的「artifact 已对齐、待确认」态，v0.4 引入）且 `stop` 一致 → 按 `obj.artifacts` 重新 stat（`{ bigint: true }`）各关联产物的 (size, mtime_ns)，与经 §3.1 规则 8 无损解析的钉住值逐项做 bigint 相等比较（文件缺失按 Spec 3 `_fingerprint` 约定记为 (-1, -1)）；任一不符 → `E_STALE`；
  - **对象层**：`approvalId` 随 argv 送达 core（`--id`，Spec 3 S3-R6）：core 在 flock 内再按 id 定位，对象已被替换即退出 1，**绝不处理替身**。host 侧校验与 core 侧校验之间的 TOCTOU 窗口由 core 侧兜住；
  - **物理闸门层（05 解封物）**：host 把对象钉住的 `04-clips.json` 指纹作为 `--expect-size=`/`--expect-mtime-ns=` 传给 `review --approve`（Spec 3 S3-R9）；core 在同一个 fd 上核对并只用核对过的字节写出解封物。host 核对到 core 读文件之间的窗口由 core 兜住：文件已被改则 core 退 1、不写解封物；读后才被改则解封物仍是 F1，对当前 F2 无效，闸门保持关闭；
  - host 对每期维护一个 ack 互斥（同期 ack 串行，`E_BUSY`）；**单实例锁**（§2.2）保证这把互斥是全机唯一的，跨进程并发仍由 Spec 3 的按期 flock 兜底。
- **停机点自动呼出只作用于活跃期**：direction §0.1 旅程第 4 步的 PreviewPane 自动呼出只在活跃期出现新 `approvalId` 时触发一次；后台期只在期列表显示 `status --json` 的 `current_step`（`is_blocked` 时加停机标记），绝不抢焦点。

### 2.6 决策 6：ack 链路与决策条语义——显式点击、能力探针、对象绑定、指纹双核验、后置核验

- **现状证据（撰写时实测，红队独立重跑一致）**：Spec 3 未施工时，`ava <期> /approve 02.5` 在 `cli.py:1169-1199` 全部未命中，落入 `return run_repl(ep_dir)`（`cli.py:1199`）。在临时期（处于 02.5）以 `stdin=/dev/null` 实测：REPL 在 `cli.py:898` 的 `input()` 读到 EOF，`cli.py:899-902` 返回 0，`run_repl` 的 `finally`（`cli.py:845-846`）写出 `{"stop": "02.5", "minutes": 0.0}` 的 human_time.json 记录（`cli.py:66` 的 <0.1 分钟过滤只对 `scout` 生效）。Spec 3 v0.3 §4.2 第 3 条已在 core 侧把「非 TTY 未分派子命令」改为退出 2，但桌面端**不依赖**这条修复，自己设四道闸：
  1. **能力探针**：host 启动与 repoRoot 变更时 spawn `python -c "import pipeline.approvals"`；非 0 → 决策条只读显示「core 尚未提供 approval 对象层（Spec 3 未施工）」，**按钮禁用、heal 不 spawn**；
  2. **对象绑定与陈旧校验**：见 §2.5（HEAL → 状态 → 指纹 stat → `--id` 送达 core）；
  3. **05 解封物的指纹核验（v0.3 起主防线在 core）**：第 ① 步以 `REVIEW_APPROVE` 模板调用，携带对象钉住的 `04-clips.json` 指纹 `--expect-size=<N> --expect-mtime-ns=<M>`（Spec 3 v0.6 §4.5，S3-R9）；core 在同一个 fd 上核对并只用核对过的字节写出解封物，文件已被改则退 1、不写（→ `E_CORE`，UI 原样显示 core 的「已不是审阅时的版本」提示）。第 ① 步退出 0 后，host 仍 stat `04-clips.approved.json` 与钉住指纹比对作事后核验（解封物的 mtime 由 core 以 `os.utime(ns=…)` 设为源文件 fstat 值，APFS 实测纳秒相等；外置盘为假设 8）；不等 → `E_GATE_MISMATCH` 并停止第 ② 步——**在 S3-R9 正确施工的前提下此分支不可达**，它只用于发现 core 回归。host 不删除任何文件（I1；删除是红线 1）；
  4. **后置核验**：全部步骤退出 0 后，host 重读对象库，断言该 `approvalId` 已到达目标态——approve：`status == "approved"` 且（`resolved_by == "cli"` 或 `confirmed_by == "cli"`）；reject：`status == "rejected"` 且 `feedback.target/problem` 与请求**逐字节相等**。不符 → `E_UNVERIFIED`，UI 显示「目标对象未到达预期状态（可能已被替换），请以对象库为准」，**绝不显示成功**。
- **所有 spawn 的 stdin 恒为 `ignore`、`detached: true`**：任何意外交互立即 EOF 失败；超时对整个进程组发信号（§3.4，红队 M2）。
- **四种停机点的决策条动作**（语义继承 Spec 3 v0.6 §2.6、§4.1 与 §4.5）：

| 停机点 | 预览自动呼出 | 「批准」spawn 序列 | 「打回」 | 说明 |
|---|---|---|---|---|
| 02.5 | `02-script.md`（md 渲染） | `APPROVE 02.5 --id` | `REJECT 02.5 --id …` | Spec 3 要求 `02-diff.patch` 有效；patch 在时，自愈已把对象对齐为 `APPROVED(artifact)`，本次点击走 Spec 3 v0.4 **确认路径**（S3-R7：退 0、写 `confirmed_by`、记决策延迟）；无 patch 时 core 退 1，UI 原样显示其封板提示。v1 桌面端不能生成 patch（RF-13） |
| 03.5 | `03-audio/` 播放队列 | `APPROVE 03.5 --id` | `REJECT 03.5 --id …` | 纯记录型。批准按钮旁常驻「结构化打点（manifest `human_review`）须在终端 `/voice` 完成；本次审阅不计人时」（门禁 14） |
| 05 | `04-review.html`（沙箱 iframe） | 对象为 PENDING：① `REVIEW_APPROVE --expect-size=… --expect-mtime-ns=…` → 闸 3 事后核验 → ② `APPROVE 05 --id`；对象已是 `APPROVED(artifact)` 待确认（解封物已在终端生成）：闸 3 指纹核验 → `APPROVE 05 --id`，**不再跑 ①** | `REJECT 05 --id …`（仅 PENDING） | ① 产出解封物（显式人令，走白名单与 jobs 层）；② 走确认路径（① 之后自愈必然已对齐，S3-R7 冻结其为退 0）。① 失败时 **stdout 与 stderr 尾部都原样显示**（补丁段号在 stdout，`review.py:335`），**不提供任何重试按钮**；含补丁段的期须在终端执行 `python -m pipeline.review <期> --approve` 完成二次确认（RF-18）。另显示确定性事实：`04-review.html` 的 mtime 早于 `04-clips.json` 时标「审片页生成时间早于排片文件最后修改时间」（RF-20）。决策条常驻「本次审阅不计人时」 |
| 09 | `05-final.mp4` | `APPROVE 09 --id` | `REJECT 09 --id …` | 纯记录型，零副作用 |

- **Reject 表单**：两个必填字段「哪段」（→ `target`）与「问题」（→ `problem`，多行），对应 Spec 3 §2.4 schema；**不做 LLM 从自由文本抽取 target**（direction §5）。两字段各为一个 argv 元素，不经 shell、不拼接（Spec 3 v0.4 §4.2 第 1 条固定位置语法）。
- **显式点击是 ack 的唯一触发源**：renderer 中发送 `approval.decide` 的代码只允许出现在决策条组件的点击处理器里（TG-4 静态断言）；打开期、预览、切换期、窗口聚焦均不产生 ack spawn（TA-5）。这是 AGENTS.md 六节「`--approve` 必须是显式动作」在 UI 层的落实。运行时注入（CDP）由 §2.10 的调试口拒启挡住命令行入口。
- **决策条只呈现确定性事实**：停机点类型、对象创建时间、关联产物指纹（路径/字节数/mtime）、`note`、`status --json` 的 `block_reason` 与 `advisories`。人时类 advisory 旁标「数据源不完整：桌面端审阅不计入」（红队 M8；判据 4：不把「不知道」当成合格）。**UI 自身零判定**，不做 LLM 推荐（direction §5）。

### 2.7 决策 7：PreviewPane 五类预览与只读媒体协议

- **按扩展名分发**（`shared/previewKind.ts`，纯函数）：

| 类别 | 扩展名 | 实现 | 冻结的约束 |
|---|---|---|---|
| 网页 | `.html` | `<iframe>` 加载 `ava-media://…`，**不用 BrowserView**（Electron 30 起被 WebContentsView 取代，约；`electron@44.4.5` 的 `electron.d.ts:4134` 仍保留 `class BrowserView`，`18947` 为 `WebContentsView`） | **绝不加 `allow-same-origin`**。episodes 根下的 html → `sandbox=""`（`04-review.html` 由 `review.py:291-296` 生成，**不含 `<script>`**，已核实）；shots 根下的 gallery → `sandbox="allow-scripts"`（`shots.py:523` 有内联脚本） |
| 视频 | `.mp4` `.mov` | 原生 `<video controls>` + 时间码浮层 | 浮层时间取 `requestVideoFrameCallback` 回调的 `mediaTime`（实际呈现帧的时间戳，毫秒显示）。**v1 不显示帧号、不做逐帧步进**（帧长须按 `r_frame_rate` 现算，AGENTS.md 七节）。编码不被支持 → 显示媒体错误码 |
| 音频 | `.wav` `.flac` | 原生 `<audio controls>` + 播放队列 | 点目录 = 该目录全部音频按**自然序**排队（`tts.py:2024` 命名 `seg-{index:02d}.wav`），`ended` 自动接下一条 |
| 图片 | `.png` `.jpg` `.jpeg` | Pan-Zoom（CSS transform，无第三方库） | — |
| 文本 | `.md` / `.json` | `.md` → markdown-it（`html: false`、`linkify: false`；链接点击拦截不导航）；`.json` → 自研折叠树 + 语法着色 | 超过 5 MiB 只显示前 5 MiB 原文并标注。其余文本后缀（`.log` `.patch` `.txt`）按纯文本显示——**范围边缘，红队可裁** |
| 其他 | — | 只显示元数据 | 不猜格式 |

- **只读媒体协议 `ava-media://`**（main 进程，§3.5 URL 契约）：
  - 启动前 `protocol.registerSchemesAsPrivileged`（`electron.d.ts:11734`），privileges `standard: true`（相对 URL 解析必需：`review.py:275` 的相对缩略图路径、`shots.py:481` 的 `os.path.relpath`）、`secure: true`、`stream: true`、`supportFetchAPI: true`；**不给 `bypassCSP`**（`electron.d.ts:23488-23516`）；`corsEnabled: true`，但协议只在请求 Origin 恰为 renderer 自身源（`file://`；未打包构建另加 dev server 源）时回 `Access-Control-Allow-Origin`，沙箱 iframe 的 Origin 为 `null`，读不到（**S21 施工修订，经用户同意**：原「不给 `corsEnabled`」下 Chromium 拒绝 renderer 对非 CORS scheme 的一切跨源 fetch，文本类预览取不到正文）；
  - 只接受 `GET`/`HEAD`；Range **自行实现**单段 `bytes=a-b` → 206；多段 → 416；
  - MIME 按扩展名白名单；html 响应附带自己的 CSP 头（episodes 根：`default-src 'none'; img-src ava-media:; style-src 'unsafe-inline'`；shots 根额外 `script-src 'unsafe-inline'`）；
  - **fd 生命周期（红队 m9）**：main 登记每个进行中的响应流；收到 host 的 reach 非 ok 通知时全部 `destroy()`；renderer 卸载媒体元素时请求被中止，流随之关闭。目标：脱盘前 app 不持有 `data/` 下任何 fd，不阻止正常推出（TP-6）。
- **renderer 自身 CSP**：`default-src 'none'; script-src 'self'; style-src 'self'; img-src ava-media: data:; media-src ava-media:; frame-src ava-media:; connect-src ava-media:`。
- **出站网络为零**：main 在默认 session 上 `webRequest.onBeforeRequest` 取消一切 scheme ∉ {`ava-media`, 指向 app 自身资源的 `file`（仅限 `app.getAppPath()` 子树）, `devtools`} 的请求；host 不 import 网络模块、不调 `fetch`（TG-3，TS-5，MUT-29）。桌面端 v1 不向任何外部端点发送内容；未来加入对话面板（LLM 出网）时 egress 边界须重新审计（§6.3）。

### 2.8 决策 8：外置盘脱卸——UI 不比 CLI 更脆弱，也绝不 mkdir（正面回答预审问题 5）

- **现状证据**：撰写时本机 `data -> /Volumes/Samsung T7/anime-video-data`，该卷**未挂载**——脱盘是日常状态。CLI 侧：`paths.py:131-158 require_data` 三分报错、绝不创建；`cli.py` 无参看板在 episodes 根不可达时退出码 2。
- **可达性诊断**（`host/reach.ts`，每 2000 ms 对 `dataRoot` 做一次 `lstat` + `stat`）——镜像 `require_data` 的三分，并多一类 GUI 特有的失败：

| `Reach` 值 | 判定 | UI 呈现 |
|---|---|---|
| `ok` | `stat(dataRoot)` 成功且 `library/` 为目录 | 正常 |
| `volume-unmounted` | `lstat` 为符号链接、`stat` ENOENT | 「外置盘未挂载：`data` 指向 <readlink 结果>，插盘后自动恢复」 |
| `missing` | `lstat` ENOENT | 「`data/` 不存在，先跑 `./pipeline/preflight.sh --init`」 |
| `skeleton-incomplete` | `data/` 在但 `library/` 不是目录 | 同 `require_data` 第三条提示 |
| `permission-denied` | EPERM/EACCES | 「macOS 未授权本 app 访问可移除卷（系统设置 → 隐私与安全性 → 文件与文件夹）；每次重新构建 app 后可能需要重新授权」。Terminal 有自己的 TCC 授权，Electron app 需要单独授权；把它报成「盘没插」是指错方向（`paths.py:137-138` 记录过同类教训）。重建后授权是否失效为假设 4 |

- **脱卸期间**：全部 tail 转 `unreachable`；renderer 保留最后快照并置灰标「陈旧」；决策条禁用；main 销毁全部媒体流。**绝不 mkdir、绝不写**（I1）。
- **重新挂载**：全部 tail resync、重新探针、重新 snapshot。
- **TCC 归属**：不设 `ForkOptions.disclaim`（`electron.d.ts:22061`，默认 false），Python 子进程的 TCC 请求归属 app（约，PR2 实测）。
- **app 自身状态不放外置盘**：userData（设置、Chromium 缓存、`crashDumps`）固定在内置盘；TI-4 断言。

### 2.9 决策 9：崩溃与重启恢复——一切从磁盘重建（正面回答预审问题 6）

- **恢复总契约**：host 与 renderer 不持有任何「只在内存里」的事实。所有可见状态 = `status --json`、`approvals_store.json`、`events.jsonl` 三个磁盘来源的函数；任何崩溃后的恢复动作都是重新 snapshot。
- **renderer 崩溃**：main 重载窗口，重新撮合端口；renderer 对上次活跃期重新 subscribe（这会按 §2.12 触发一次 heal）。UI 便利状态由 renderer `localStorage` 记忆，读写均 try/catch。
- **host 崩溃**：main 以退避 1 s→2 s→4 s…封顶 30 s 重启；60 s 内崩溃 5 次则停止并显示致命面板（含 host stderr 尾部）。TI-9 验证。
- **崩溃时有 ack 在途**：Python 子进程不随 host 死亡而被杀（孤儿由 launchd 接管跑完）。Spec 3 的 ack 在 flock 内读-改-写并 `atomic_write`，要么完整生效要么完全没发生。host 重启后 UI 显示「上次操作结果未知，已从磁盘重新读取」。
- **超时**：对进程组 SIGTERM，5 s 后 SIGKILL（§3.4）；UI 显示「结果未知，请以稍后刷新为准」（红队 M2：孙进程可能在信号送达前已完成写入）。
- **v1 不拉起长任务**：spawn 闭集全部是短命令，app 退出时 host 不向子进程发信号，在途短命令自行跑完；这同时回避了 Spec 2 RF-7。
- **snapshot 首载契约**：见 §3.3 `EpisodeSnapshot`；`generation` 单调递增使旧 delta 无法污染新 snapshot。

### 2.10 决策 10：asar 完整性与打包加固——机制、失败行为、威胁模型（正面回答预审问题 4）

- **机制（逐项核实于 electron-builder 26.15.3 源码与 @electron/fuses 2.1.3）**：
  1. **打包时计算完整性**：`asar: true` 时 `platformPackager.js:225-227` 调 `integrity.computeData`（仅在 `disableAsarIntegrity` 未设时——本项目禁止设它，TG-5）；
  2. **写入 Info.plist**：`electron/electronMac.js:182-193` 写 `ElectronAsarIntegrity`；
  3. **翻转 fuses**：`electronFuses` 段经 `platformPackager.js:259-265 doAddElectronFuses` 与 `266-298 generateFuseConfig` 映射到 `FuseV1Options`（v0.3 更正行号：`generateFuseConfig` 为 `266-298`）。`FuseV1Options` **共 9 位**（`dist/config.d.ts:8-16`，v0.1 自查表截到 15 行漏了第 9 位，红队 M3）；`generateFuseConfig` **不映射 `WasmTrapHandlers`**，也不透传 `strictlyRequireAllFuses`（`platformPackager.js` 中无此二词，已 grep 核实）。本项目取值：

| fuse | 取值 | 为什么 |
|---|---|---|
| `enableEmbeddedAsarIntegrityValidation` | `true` | 核心：启动时按 Info.plist 校验 asar |
| `onlyLoadAppFromAsar` | `true` | 否则删掉 asar、旁边放 `app/` 目录即可绕过 |
| `runAsNode` | `false` | 禁止把 app 二进制当通用 node 用 |
| `enableNodeOptionsEnvironmentVariable` | `false` | 禁止经 `NODE_OPTIONS` 注入 |
| `enableNodeCliInspectArguments` | `false` | 禁止 `--inspect` 挂调试器 |
| `grantFileProtocolExtraPrivileges` | `false` | 媒体走 `ava-media://`，`file://` 不需额外特权 |
| `enableCookieEncryption` | `true` | 无副作用的加固 |
| `loadBrowserProcessSpecificV8Snapshot` | `false` | 不使用自定义 V8 快照 |
| `WasmTrapHandlers` | **不设值，保持 Electron 44 默认** | electron-builder 26.15.3 无法设置；不为一位与 v1 无关的 fuse 引入 afterPack 脚本。TS-2 读出实际位值写入期望表，漂移即红 |
| `resetAdHocDarwinSignature`（非 fuse，选项） | `true` | 翻 fuse 改了二进制，arm64 需重新 ad-hoc 签名（约，假设 1） |

  4. **其余打包项冻结**（TG-5 全部纳入）：`publish: null`——electron-builder 在 `publish` 未设时会因 npm `release` 生命周期、CI tag 或 CI 环境**隐式发布**（`publish/PublishManager.js:46-60`），且有 `GH_TOKEN`/`GITHUB_TOKEN` 时自动选 github provider（`:365`），碰红线 6；`mac.identity: null`——源码语义为「跳过签名、不查 keychain」（`macPackager.js:295-297` → `mac/MacTargetHelper.js:13-19`），避免构建结果取决于本机 keychain 内容；因此**整个 bundle 不签名**，只有 fuses 步骤对主二进制做 ad-hoc 重签（红队 m2；不改用 `identity: "-"`：`codeSign/macCodeSign.js:227` 按 `line.includes(qualifier)` 子串匹配，会误选名字含连字符的钥匙串身份）；`mac.target: dir`。
- **失败行为（冻结）**：校验不过 → Electron 在应用代码运行前终止进程（Electron 语义，约；TS-1 以篡改实测）。**fail-closed、无旁路**。诊断走 `scripts/verify-fuses.mjs`（读全部 9 位 fuse，复算 asar 头哈希并与 Info.plist 比对）。恢复唯一路径：重新构建安装。
- **启动参数白名单（红队 B4，v0.3 按 m3 由黑名单改白名单）**：9 位 fuses 中没有禁用 `--remote-debugging-port/pipe` 的一位，而黑名单不可能列全。packaged 版 main 入口第一件事检查 `process.argv.slice(1)`：除 macOS 可能附带的 `-psn_*` 外出现任何参数，即向 stdout 打印 `AVA_REFUSE argv` 并 `app.exit(1)`，不建窗口、不起 host（TS-6）。DevTools 端口在 JS 执行前是否已短暂监听为假设 7，TS-6 如实记录。
- **构建溯源横幅（红队 B4；v0.3 按 R2-M6 补 `node_modules`）**：发布构建脚本 `npm run release-build` 固定为 `npm ci` → `node scripts/write-build-info.mjs` → `electron-vite build` → `electron-builder`。第一步 `npm ci` 从 `package-lock.json` 干净重装，冲掉对被 gitignore 的 `desktop/node_modules/` 的一切手改；`write-build-info.mjs` 把 `{ gitHead, desktopDirty, lockfileSha256, builtAt }` 写入 `out/build-info.json`（随 asar 一起受完整性保护）。健康面板经 spawn 取当前 repoRoot 的 `git rev-parse HEAD`，并在 `gitHead` 与当前 HEAD 不同时跑 `git diff --quiet <gitHead> HEAD -- desktop/`：
  - `desktopDirty == true` → 顶栏常驻**红色**「UI 构建自未提交的 desktop/ 改动」；
  - `desktop/` 在构建之后有新提交，或当前仓库 `desktop/package-lock.json` 的 sha256 ≠ `lockfileSha256` → 常驻**黄色**「UI 未按当前源码重建（构建于 <sha>）」；
  - `GIT_DESKTOP_DIFF` 退出 128（构建提交不在本仓库，如被 gc 或来自别的克隆）→ 常驻**灰色**「无法比对 UI 构建版本」，不与「一致」混同（红队 m6）；
  - 这是 `check_code_freeze`（`cli.py:36-60`）告警语义在 UI 层的镜像。core 侧的 Code Freeze 横幅照旧由 `PROBE_FREEZE` 复用 `check_code_freeze` 给出（REPL 启动时 `cli.py:872` 打印的同一警告）。
- **repoRoot 只在 main 里选（红队 B4；v0.3 按 R2-M5 改）**：renderer 只能发送无参数的 `app.requestRepoRootChange`；main 弹原生 `dialog.showOpenDialog` 选目录，再以 `dialog.showMessageBox` 二次确认（显示所选路径与其形状校验结果），确认后由 main 通知 host 写 settings。renderer 从头到尾接触不到路径，被攻破也无法把 spawn 指向仿造仓库。**切换与在途命令互斥（红队 R3 m4）**：main 弹框前先问 host，有 decide 或 heal 在途 → 返回 `E_BUSY`、不弹框；用户确认后 host 进入「切换中」，期间新的 `approval.decide` 返回 `E_BUSY`、不启动新 heal，等在途 spawn 全部结束再换 repoRoot、重跑能力探针与期映射；repoRoot 带单调代号，只读 spawn（STATUS、PROBE_*、GIT_*）的结果若代号已过期则丢弃（TA-12）。顶栏常驻显示当前 repoRoot 与其 HEAD 短 sha。
- **威胁模型（v0.2 如实改写）**：
  - asar 完整性**只挡「已安装包被字节级 patch」**（ZCode 教训的那一类）；
  - 「改 `desktop/src` 再重建」无法被阻止，**由构建溯源横幅让它可见**；
  - 「运行时经 CDP 驱动点击」由调试口拒启挡住命令行开关这一入口；
  - 「改 `settings.json` 把 repoRoot 指向别的 core」无法被阻止，**由顶栏常驻显示让它可见**；经 renderer 改 repoRoot 的通道已关闭（R2-M5）；
  - **裸形态 `ava <期> /approve --id …` 在非 TTY 下可被任何有 shell 的 agent 直接调用**（红队 m9）：UI 层的「显式点击」纪律再严，也不可能比 CLI 自身的暴露面更严。本节绊线防的是「对已安装 UI 的顺手修改」，**不是**「agent 代替人去 ack」——后者属于 core 与 AGENTS.md 纪律的范围（RF-22）；
  - 有本机写权限且蓄意的攻击者可以同时改 asar 与 Info.plist 再 ad-hoc 重签、或绕开以上所有绊线——**本 spec 不防蓄意本地攻击**。上述措施的定位是「让开发期 agent 的顺手修改在生产期界面上可见」，与 core 的 Code Freeze（WARN 不拦截）同一强度。
- **开发构建**：`electron-vite dev` 不做完整性校验；`app.isPackaged === false` 时常驻「DEV BUILD」水印。测试专用启动开关只在未打包构建中生效（TG-6，TS-7）。

### 2.11 决策 11：v1 范围闸门

- **v1 做**：期列表 + 产物文件树 + PreviewPane 五类预览 + Approval 决策条（Approve / Reject 携带结构化反馈）+ 只读事件时间线（job 生命周期与审批事件；失败 job 的 `stderr_tail` 原样展示；**时间线是有损观测，不承诺完整**，§2.4）+ 健康面板（repoRoot、Python、approval 能力、core Code Freeze、UI 构建溯源、数据可达性）。
- **v1 明确不做**（均须另立 spec）：
  - 中央伴随式对话面板（二期 Spec 9/10，见 direction §6）；
  - 从 UI 发起任意流水线 job（唯一的 `/run` 是 05 的 `review --approve`），因此不需要 Spec 2 的 `job_heartbeat` 与 SIGTERM 优雅关闭；
  - 02.5 封板与任何产物编辑（RF-13；2026-09-23 产品裁决：二期做 app 内置 Markdown 编辑器，见 direction §6 Spec 11）；含补丁段的 05 批准（RF-18）；
  - 划词注音打点、03.5 结构化打点等深度业务组件；
  - 人时记账（RF-12，门禁 14）；
  - `data/_events.jsonl` 全局事件视图；
  - LLM 推荐/预判断、guardian LLM、多 agent 聚合、通用 agent 协议、数据库、web server（direction §5、ADR-0020「不做的事」）；
  - 自动更新、公证、dmg 分发、发布（`publish: null`）、Windows/Linux；
  - 退役 `04-review.html` 与 `shots gallery`（ADR-0020 §1 的退役是 core 改动，v1 上线验收后另立施工单）。

### 2.12 决策 12：heal 的触发时机闭集（红队 B5 新增）

- **问题**：heal（spawn `ava <期> /approvals`）会让 core 写 `_agent/approvals_store.{json,lock}`、必要时建 `_agent/`、发 `approval_requested`、执行惰性转移。触发太少，新停机点看不到；触发太多，打开 app 就会在所有期里写簿记；由对象库变化触发会形成反馈环。
- **冻结的触发闭集（只作用于能力探针通过的情况）**：

| # | 触发 | 作用期 | 理由 |
|---|---|---|---|
| H1 | 某期**被设为活跃期**（`episode.activate`：首次打开、从后台切回、重连后恢复活跃；v0.3 按 R2-M4 由「subscribe」改为「设为活跃」） | 该期 | 首载与切回都必须看到自愈后的队列（Spec 3 §2.2 B2）。后台期保持订阅（§2.4），订阅本身不触发 heal |
| H2 | 活跃期 `status --json` 的 `current_step` **发生变化**（边沿触发，每次变化恰一次；基线 = 设为活跃期后的第一次 status 采样，该次不触发，红队 m4） | 仅活跃期 | 保住 direction §0.1 旅程第 4 步「停机点主动触达」。触发源是 `status --json`——`status.py` 无写操作——**不读对象库，不会形成反馈环**；工序变化是低频事件，写放大有上界 |
| H3 | 用户点击「刷新」（`episode.refresh`） | 仅活跃期 | 显式人令。刷新期间 `current_step` 的变化只吸收进 H2 基线、不补触发 H2（刷新本身已 heal；**S22 审核修订，经用户同意**） |
| H4 | `approval.decide` 处理流程中的 HEAL 步（§3.2.1） | 被 ack 的期 | 陈旧校验必须基于自愈后的对象库（§2.5） |
| H5 | 活跃期对象库中，**每种停机点最新的那个对象**（按 `created_at`，相同取数组中靠后者）若为 PENDING 或 REJECTED，其 `artifacts` 所列路径 stat 得到的 (size, mtime_ns) 与钉住值不一致。与 `ACTIVE_POLL_MS` 同频采样；**基线** = 设为活跃期后的第一次采样，该次不触发；**稳定期** = 同一指纹元组在连续两次采样中都出现才触发；每个对象的每个新指纹元组至多触发一次；`artifacts[].path` 先过 §3.5 规则 1 的段检查，拒绝 `..`、绝对路径、空段与 `\0`，被拒路径只记诊断、不 stat（v0.3 按 R2-M4 新增；v0.4 按红队 R3 m1/m2/m3 加基线、最新对象、稳定期与段检查）；基线采样中已存在的漂移元组记为已触发、此后永不触发，之后出现的新元组照常按稳定期触发（**S22 审核修订，经用户同意**） | 仅活跃期 | 覆盖「打回 → 返工」闭环（ADR-0020 §3「驳回并回喂」）：05 被打回后 `current_step` 一直停在 05，H2 永远不触发；返工改变 `04-clips.json` 指纹后 H5 触发，Spec 3 自愈据指纹漂移新建 pending，新卡片出现。路径直接取自对象库，TS 不复制 `_STOP_ARTIFACTS` 这类业务映射；heal 不改产物，不会自我触发 |

- **明令禁止**：后台期 heal；按周期 heal；由对象库文件本身的 (ino, size, mtime) 变化或任何事件行触发 heal。同一期同一时刻至多一个 heal 在途，在途期间的新触发合并为「完成后再跑一次」。
- **heal 失败（红队 m8）**：非 0 退出写入诊断面板（附 stderr 尾部），不重试；H4 场景下 ack 流程继续，由 core 侧 `--id` 定位兜底。
- **后台期的审批徽标**：不经 heal，只显示 `status --json` 的 `current_step` 原文（`is_blocked` 时加停机标记）；UI 不显示后台期的对象库计数，避免把未自愈的数字当事实。
- **验证**：TA-11 断言在「打开 app → 依次打开 3 个期 → 后台期由终端推进到停机点 → 切回该期 → 05 打回后返工」的全过程中，heal spawn 只发生在 H1–H5 规定的时机与期上（MUT-34/35/40/41）；H5 的基线、最新对象、稳定期与段检查由纯函数单测 TE-14 逐条驱动（MUT-48~51）。

---

## 3. 数据契约

### 3.1 上游契约（只读消费，TS 镜像，容错解析）

| 契约 | 来源 | 桌面端消费方式 |
|---|---|---|
| Event | Spec 2 §3.2、§3.3 六种载荷示例；枚举值 9 个（`job_created/job_blocked/job_started/job_heartbeat/job_finished/approval_requested/approval_resolved/human_time_recorded/sidecar_degraded`） | tail 直读 |
| Approval | Spec 3 v0.6 §3.1（含 v0.4 新增 `confirmed_by`/`confirmed_at`）、§3.2（JSON 数组，indent=2） | 按 §2.12 heal 后直读 |
| EpisodeStatus | `status.py:19-30`，经 `status.py:501-502` `asdict` 输出 | spawn `--json` |

**容错规则（冻结）**：
1. 未知字段忽略；
2. 未知 `type` 值的事件保留为 `kind: "unknown"` 并原样显示，不丢弃；
3. 缺少必需字段 → 计入 `malformed`，不下发；
4. Approval 数组中单个元素解析失败 → 跳过并计数；整个文件解析失败 → 保留上次成功结果并标「对象库读取失败」；
5. 停机点类型只认 `"02.5" | "03.5" | "05" | "09"`（与 `cli.py:30 HUMAN_STOPS` 一致）；其他值只读显示；
6. **`approval_resolved` 两种载荷形状（红队 m4）**：带 `approval_id` 的是停机点决策（Spec 3 §3.3）；不带的是 Spec 2 §3.3 示例 5 的命令卡拒执（`source: "cli_card"` 等），时间线标「命令卡拒执（非停机点）」，不与停机点打回混淆；带 `"confirms": "artifact"` 的标「确认已对齐的批准」；
7. **`sidecar_degraded`** 不进 job 折叠，单独渲染（§2.4）；
8. **纳秒整数无损解析（v0.4，红队 R3-B1）**：`approvals_store.json` 与 `events.jsonl` 的每一行一律经 `shared/losslessJson.ts` 的 `parseLossless` 解析，其实现冻结为 `JSON.parse(text, (k, v, ctx) => k !== "mtime_ns" ? v : typeof v === "number" ? BigInt(ctx.source) : fail(k, v))`（`fail` 抛错；v0.5 按红队 m3 收紧：`mtime_ns` 的值只要不是 JSON 数字——字符串、null、对象——就判解析失败，形状一变就响亮失败，不静默失配）。`ctx.source` 不是纯十进制整数字面量（含 `.`、`e` 等）时 `BigInt` 抛错，整份解析按规则 3/4 计入失败，不回退到有损值。`size` 仍为 number，但要求 `Number.isSafeInteger`，否则同样按解析失败处理。host 启动时做一次自检：解析 `{"mtime_ns":1790171112636927676}` 须得到 `1790171112636927676n`；不成立（运行时不支持 `context.source`）→ 健康数据 `losslessJson: false`，approvals 能力按缺席处理（决策条只读、不 heal），并在诊断面板写明原因；
9. **出 host 的协议面不含 bigint**：host 内部的指纹比较一律用 bigint；经 MessagePort 下发给 renderer 的 `ApprovalJson`/`EventJson` 中的 `mtime_ns` 转成十进制字符串（`toWireApproval`/`toWireEvent`）。renderer 不做任何指纹判定，只做展示；这样结构化克隆和 renderer 侧的任何 `JSON.stringify` 都不会碰到 bigint。**host 与 shared 内部的一切 JSON 序列化（诊断、spawn 日志、快照去重、settings）只经 `toWire*` 或 `stringifyLossless`**，不直接调用 `JSON.stringify`（v0.5，红队 m1；TG-9）。

### 3.2 renderer ↔ host 协议（MessagePort 自有信封，v1 冻结）

```ts
// desktop/src/shared/protocol.ts
export const PROTOCOL_VERSION = 1 as const;

export type Method =                       // 方法闭集：新增方法 = 修订本 spec
  | "app.health"                           // repoRoot/python/capabilities/codeFreeze/buildProvenance/reach
  | "app.requestRepoRootChange"            // 无参数：由 main 弹原生对话框选择并二次确认（§2.10，红队 R2-M5）；decide/heal 在途 → E_BUSY（红队 R3 m4）
  | "episodes.list"
  | "episode.subscribe"                    // { epKey } → EpisodeSnapshot；不触发 heal
  | "episode.activate"                     // { epKey }：设为活跃期，触发 H1（红队 R2-M4）
  | "episode.unsubscribe"                  // { epKey }
  | "episode.resnapshot"                   // { epKey } → EpisodeSnapshot（协议跳号时内部使用，不触发 heal）
  | "episode.refresh"                      // { epKey } → EpisodeSnapshot；用户点击刷新，触发 H3
  | "tree.list"                            // { epKey, relDir } → TreeEntry[]
  | "shots.list"                           // 无参数 → { name, size, mtimeMs }[]：data/library/shots 顶层的 *.html（S21 修订，经用户同意：原闭集没有任何方法能到达 shots 根，gallery 在 UI 里打不开）
  | "approval.decide";                     // §3.2.1（v0.1 的 retryReviewWithConfirmPatch 已删除，红队 B3）

export type Envelope =
  | { v: 1; kind: "req"; id: number; method: Method; params: unknown }
  | { v: 1; kind: "res"; id: number; ok: true; result: unknown }
  | { v: 1; kind: "res"; id: number; ok: false; error: { code: ErrCode; message: string; stdoutTail?: string; stderrTail?: string } }
  | { v: 1; kind: "push"; topic: PushTopic; epKey?: string; generation?: number; seq?: number; data: unknown };

export type PushTopic = "episode.delta" | "episode.snapshot" | "episodes.summary" | "reach" | "diag";

export type ErrCode =
  | "E_BAD_REQUEST"     // 未知方法/未知 epKey/参数键不是 exact-keys
  | "E_UNREACHABLE"     // data/ 或期目录不可达（附 Reach 值）
  | "E_CAPABILITY"      // core 无 pipeline.approvals（§2.6 闸 1）
  | "E_STALE"           // 期目录已不存在 / approvalId 不在可 ack 态 / stop 不符 / 关联产物指纹已变（§2.5）
  | "E_BUSY"            // 该期已有 ack 在途；或 repoRoot 切换与 decide/heal 互斥（§2.10）
  | "E_CORE"            // spawn 非 0 退出；附 stdout 与 stderr 尾部原文
  | "E_GATE_MISMATCH"   // 05 第 ① 步产出的解封物指纹 ≠ 对象钉住的指纹（§2.6 闸 3）
  | "E_UNVERIFIED"      // 退出码 0 但目标对象未到达预期状态（可能已被替换），以对象库为准
  | "E_TIMEOUT";        // 超时，已对进程组发信号；结果未知，请以稍后刷新为准
```

- 版本不符 → 连接判死并在健康面板报错。
- 健康数据含 `losslessJson: boolean`（§3.1 规则 8 启动自检结果）。
- `params` 逐方法 exact-keys 校验：多一个键也拒（TI-8，MUT-6）。**任何方法的参数中都没有文件系统路径字段**（v0.3，红队 R2-M5）。

#### 3.2.1 `approval.decide` 参数（冻结）

```ts
type DecideParams =
  | { epKey: string; approvalId: string; stop: "02.5" | "03.5" | "05" | "09"; decision: "approve" }
  | { epKey: string; approvalId: string; stop: "02.5" | "03.5" | "05" | "09"; decision: "reject";
      feedback: { target: string; problem: string } };   // 两项 trim 后非空，否则 E_BAD_REQUEST
```

host 处理顺序（冻结）：能力探针 → `epKey` 映射 → 可达性 → stat 期目录 → 期内 ack 互斥 → **HEAL（H4；失败只记诊断）** → 读对象库做陈旧校验 → stat 关联产物做指纹校验 → 按 §2.6 表 spawn（05 approve 在 ① 与 ② 之间做解封物指纹核验）→ 退出码 → 后置核验 → finally 推一次 resnapshot。

### 3.3 snapshot / delta 结构

```ts
interface EpisodeSnapshot {
  epKey: string;
  generation: number;                 // 每次 resync 单调 +1
  seq: 0;
  reach: Reach;
  status:    { ok: true; value: EpisodeStatusJson } | { ok: false; code: ErrCode; message: string };
  approvals: { state: "absent" } | { state: "unsupported" }
           | { state: "ok"; items: ApprovalJson[]; skipped: number; healedAt: string | null }
           | { state: "error"; message: string; lastGood: ApprovalJson[] | null };
  events: { state: "absent" | "ok"; offset: number; truncatedHead: boolean;
            items: EventJson[]; malformed: number; episodeFieldMismatch: number };
  jobs: JobView[];                    // fold(events.items)
  degradedNotices: { at: string; droppedDuringCircuit: number }[];   // sidecar_degraded，不进 jobs
}

interface EpisodeDelta {
  epKey: string; generation: number; seq: number;
  newEvents: EventJson[];
  approvals?: EpisodeSnapshot["approvals"];
  status?: EpisodeSnapshot["status"];
}

interface JobView {
  jobId: string; command: string | null;
  state: "pending" | "running" | "succeeded" | "failed" | "blocked";
  lastEventAt: string;
  pid: number | null; returncode: number | null; durationS: number | null;
  stderrTail: string | null; message: string | null;
  noFollowupEvents: boolean;           // pending 且 lastEventAt 早于 PENDING_STALE_MS
  finishedEventMissing: boolean;       // running 且 pid 已不存在，连续 FINISHED_MISSING_TICKS 个 tick 成立
}
```

- **折叠规则**（`shared/fold.ts`，按文件行序）：`job_created` → pending；`job_started` → running（即使缺 created）；`job_finished` → 取 `payload.status`；`job_blocked` → blocked。无 `job_id` 的事件不进折叠。
- **缺口的事实并列呈现（红队 M1）**：`noFollowupEvents` 显示「未见后续事件」（Spec 2 §4.2 中 `run_pipeline` 先 `create_job` 再立即 `execute_job`，`job_started` 正常在毫秒级内出现，阈值理由见 §3.7）；`finishedEventMissing` 显示「进程已不在，未收到 job_finished」（`process.kill(pid, 0)` 返回 ESRCH），须**连续 2 个 tick** 成立才显示，避开「子进程已退出、父进程 sidecar 尚未刷盘」的瞬时竞态。两者都不推断成败。pid 复用导致误显示 running 是已知上限（RF-8）。

### 3.4 spawn 闭集（`host/spawner.ts`，冻结）

全部以 argv 数组调用 `child_process.spawn`：**`shell: false`、`stdio: ["ignore", "pipe", "pipe"]`、`detached: true`、`cwd: repoRoot`**；`py = <repoRoot>/.venv/bin/python`；`ep` = 由 host 映射且刚 stat 过存在的绝对路径（`resolve_episode_target` 对绝对路径直接命中，`cli.py:1081-1083`）。

| 模板 | argv | 超时 | 用途 |
|---|---|---|---|
| `PROBE_APPROVALS` | `[py, "-c", "import pipeline.approvals"]` | 10 s | §2.6 闸 1 |
| `PROBE_FREEZE` | `[py, "-c", "import sys; from pipeline.agent.cli import check_code_freeze; sys.exit(0 if check_code_freeze() else 3)"]` | 10 s | core Code Freeze 横幅 |
| `GIT_HEAD` | `["/usr/bin/git", "-C", repoRoot, "rev-parse", "HEAD"]` | 10 s | 构建溯源（§2.10） |
| `GIT_DESKTOP_DIFF` | `["/usr/bin/git", "-C", repoRoot, "diff", "--quiet", buildHead, "HEAD", "--", "desktop/"]` | 10 s | 构建溯源；退出 1 = 有差异 |
| `STATUS` | `[py, "-m", "pipeline.status", ep, "--json"]` | 10 s | §2.3 |
| `HEAL` | `[py, "-m", "pipeline.agent.cli", ep, "/approvals"]` | 30 s | §2.12，仅 H1–H5（v0.4 更正：v0.3 新增 H5 时漏改此处） |
| `APPROVE` | `[py, "-m", "pipeline.agent.cli", ep, "/approve", stop, "--id", approvalId]` | 30 s | §2.6 |
| `REJECT` | `[py, "-m", "pipeline.agent.cli", ep, "/reject", stop, "--id", approvalId, target, problem]` | 30 s | §2.6；target/problem 各为一个 argv 元素 |
| `REVIEW_APPROVE` | `[py, "-m", "pipeline.agent.cli", ep, "/run", "review", "--approve", "--expect-size=<N>", "--expect-mtime-ns=<M>"]`，`<M>` 为 `String(bigint)`，须逐字节等于 Python 写入对象库的十进制数字（§3.1 规则 8，红队 R3-B1）；N/M 取自对象 `artifacts` 中 `04-clips.json` 的钉住值 | 60 s | §2.6 05 ①。**必须用等号形式**：`tools.py:126-151 _extract_positional_args` 的 `valued_flags`（134-137）不含这两个旗标，空格形式会把值当成位置参数，`validate_pipeline_command` 随之不注入期目录（Spec 3 v0.6 §4.5） |
| `GIT_DESKTOP_DIFF` 退出码 | 0 = 一致；1 = 有差异；128 = 构建提交不存在 | — | §2.10 三色横幅 |

- **环境变量白名单**（不继承 `process.env`）：`PATH=/usr/bin:/bin:/usr/sbin:/sbin`、`HOME`、`USER`、`TMPDIR`、`LANG`（缺省补 `en_US.UTF-8`）、`PYTHONUTF8=1`、`PYTHONUNBUFFERED=1`。**明确排除** `AVA_EVENTS_ROOT`、一切 `*_API_KEY`/`*_TOKEN`、`NODE_OPTIONS`、`ELECTRON_*`。PATH 固定的理由：Finder 启动的 GUI app 拿不到 shell 的 PATH；v1 闭集里没有命令需要 ffmpeg（`review.approve` 只读 JSON + `shutil.copy2`，`review.py:300-340`），固定 PATH 让「将来加了需要 ffmpeg 的模板」当场失败（RF-3）。
- **超时与进程组（红队 M2）**：`detached: true` 使子进程成为进程组组长；`REVIEW_APPROVE` 的调用链是 host → `ava`（cli）→ `run_pipeline` 的 `Popen`（`tools.py:739-745`，未改进程组）→ `review.py`，孙进程与 `ava` 同组。超时 → `process.kill(-pid, "SIGTERM")`，5 s 后仍在 → `process.kill(-pid, "SIGKILL")`；返回 `E_TIMEOUT` 并 resnapshot。只杀直接子进程会让 `review.py` 成孤儿继续 `copy2`（Python 对 SIGTERM 默认立即终止，`tools.py` 的 `except BaseException: proc.kill()` 不会执行——与 Spec 2 RF-7 同一机理）。
- stdout/stderr 各保留尾部 8 KiB，**失败时两者都返回给 UI**（红队 B3：补丁段号在 stdout）。
- **`review --approve` 依赖真实 `data/` 可达**：`review.py:350` 的 `main` 先调 `paths.require_data()`，检查的是 `paths.ROOT/"data"`（`paths.py:20-21`）；repo 的 `data/` 脱卸时 05 批准必然失败，UI 原样显示其提示。

### 3.5 `ava-media://` URL 契约

- 形态：`ava-media://<root>/<encodeURIComponent 逐段编码的相对路径>`，`root ∈ { "episodes", "shots" }`，分别映射 `<dataRoot>/episodes` 与 `<dataRoot>/library/shots`（`shots.py:37-38`）。
- **路径守卫（v0.2 按红队 M6 收紧）**（`shared/mediaUrl.ts` 纯函数 + main 注入 `realpath`）：
  1. 逐段解码后，任一段为空、为 `.` 或 `..`、**含 `/` 或 `\`**、含 `\0` → 拒（400）。其中 `.`/`..` 大概率已被 URL 规范化剥掉（假设 6），保留该判定作纵深防御；**`%2F`/`%5C` 段注入是本规则真正要抓的**（v0.1 的真洞：`ava-media://shots/..%2F..%2Fbrowser-profile/…` 可跨出 shots 根）；
  2. 拼出候选路径后 `realpath`；结果必须是**所选 root 的 realpath 的严格后代**——不是 dataRoot。root 内指向 root 外的符号链接一律 403；v1 不开例外，PR3 对真实 data 实测，若存在合法跨 root 链接须修订本 spec 显式列出；
  3. 目标必须是普通文件，扩展名 ∈ MIME 白名单；否则 404/415。
- 只读：守卫之后只做 `fs.open(…, "r")` 流式读。由规则 2，`data/library/memory.md`（Spec 7）、`data/browser-profile/**`（Spec 5）等 root 之外的文件**在构造上不可达**。

### 3.6 设置文件（desktop 唯一的写目标）

`<userData>/settings.json`：`{ "version": 1, "repoRoot": "<绝对路径>" }`。`dataRoot` 恒为 `<repoRoot>/data`，生产版不可配置（与 `paths.py:21 DATA = ROOT / "data"` 同一口径）。repoRoot 校验：`pyproject.toml` 存在且含 `name = "anime-video-agent"`、`.venv/bin/python` 可执行、`pipeline/agent/cli.py` 存在；不通过 → 引导页。**修改只经 main 的原生对话框选择并二次确认，renderer 接触不到路径；顶栏常驻显示 repoRoot 与 HEAD 短 sha**（§2.10，红队 R2-M5）。本机存在两个工作树，app 只指向其中一个。

### 3.7 常量表（全部为**初值**，PR2/PR3 实测回填——AGENTS.md 十二节「期望值先跑」）

| 常量 | 初值 | 为什么是这个数 |
|---|---|---|
| `ACTIVE_POLL_MS` | 1000 | 事件为生命周期粒度（Spec 2 RF-1）；审批决策以分钟计（Spec 3 §3.3 `latency_s: 312.4`），检测延迟占比 <1%；stat 本机实测 ≈1 µs |
| `BACKGROUND_POLL_MS` | 5000 | 全部可见期的 `events.jsonl` stat；后台期只需刷新徽标 |
| `REACH_POLL_MS` | 2000 | 插拔盘是人手动作，2 s 内感知足够 |
| `STATUS_REFRESH_MS` | 5000 | 人手改文件不产生事件，只能周期性问 status；单次 spawn 实测 0.03–0.04 s；它同时是 heal 触发 H2 的采样周期 |
| `EPISODE_LIST_REFRESH_MS` | 30000 | 期列表每期一次 status spawn（并发 2）；30 期 × 0.04 s ≈ 0.6 s CPU / 30 s |
| `MAX_PARTIAL_BYTES` | 1 MiB | Spec 2 单行上界估算：`stdout_tail`、`stderr_tail` 各 ≤4096 字符（Spec 2 §3.1），UTF-8 最多 4 字节/字符 ≈ 32 KiB；1 MiB ≈ 30 倍余量 |
| `READ_CHUNK_BYTES` | 4 MiB | 单次读上限，tail 与 snapshot 都按此分块、块间让出事件循环 |
| `SNAPSHOT_MAX_BYTES` | 32 MiB | 约 1000 条满尾部的 `job_finished`；超出只载尾部并标 `truncatedHead` |
| `ANCHOR_BYTES` | 64 | `sort_keys=True` 下行内键序为 `episode < event_id < payload < timestamp < type`，行尾 64 字节落在 `timestamp`（微秒级）与 `type`，微秒时间戳足以区分「原行」与「截断后重写的新行」（红队 m2 更正） |
| `PENDING_STALE_MS` | 10000 | Spec 2 §4.2 中 `run_pipeline` 先 `create_job` 再立即 `execute_job`，created→started 正常在毫秒级；10 s 是三个数量级余量，只用于显示「未见后续事件」 |
| `FINISHED_MISSING_TICKS` | 2 | 子进程退出与父进程 sidecar 刷盘之间存在瞬时窗口；连续两个 tick（≥1 s）仍成立才显示 |
| 各 spawn 超时 | 10/30/60 s | ack 路径锁持有是毫秒级（Spec 3 §2.1），30 s 是两个数量级余量；`review --approve` 在外置盘上多给一倍 |
| host 重启退避 | 1 s 起翻倍、封顶 30 s；60 s 内 5 次熔断 | 崩溃循环时不刷屏、不空转，同时给偶发崩溃快速恢复 |
| `TEXT_PREVIEW_MAX_BYTES` | 5 MiB | 期内最大的文本产物（`04-clips.json`）为数百 KB 量级（约，PR3 实测） |

---

## 4. 模块接口与签名设计

### 4.1 `shared/`（纯函数，零 Node/DOM）

```ts
// jsonl.ts —— 半行永不下发
export class LineSplitter {
  constructor(maxPartialBytes: number);
  push(chunk: Uint8Array): { lines: string[]; overflow: boolean };
  reset(): void;
  pendingBytes(): number;
}

// fold.ts
export function foldEvents(events: EventJson[], now: number, pidAlive: (pid: number) => boolean,
                           prevMissing: ReadonlySet<string>): JobView[];   // 确定性
export function parseEventLine(line: string): { ok: true; ev: EventJson } | { ok: false };

// mediaUrl.ts
export type MediaRoot = "episodes" | "shots";
export function encodeMediaUrl(root: MediaRoot, rel: string): string;
export function decodeAndGuard(
  url: string,
  rootsReal: Record<MediaRoot, string>,      // 已 realpath 的根
  realpath: (p: string) => string,           // 注入，便于单测符号链接逃逸
): { ok: true; abs: string; mime: string } | { ok: false; status: 400 | 403 | 404 | 415 };

// losslessJson.ts —— §3.1 规则 8；对象库与事件行的唯一解析入口（红队 R3-B1）
export function parseLossless(text: string): unknown;               // mtime_ns → bigint；非整数字面量抛错
export function losslessSelfCheck(parse: (t: string) => unknown): boolean;   // 启动自检，parse 可注入以便单测
export function toWireApproval(r: ApprovalRecord): ApprovalJson;    // 出 host 前把 bigint 转十进制字符串（规则 9；v0.5 按红队 m1 分开内部/线上类型）
export function toWireEvent(e: EventRecord): EventJson;
export function stringifyLossless(v: unknown): string;              // bigint → 十进制数字；host/shared 内唯一允许的 JSON 序列化入口（TG-9）

// stopPreview.ts —— 纯展示映射，不参与任何判定
export const STOP_PREVIEW: Record<"02.5" | "03.5" | "05" | "09", { root: MediaRoot; rel: string }>;
```

### 4.2 `host/`

```ts
// reach.ts
export type Reach = "ok" | "volume-unmounted" | "missing" | "skeleton-incomplete" | "permission-denied";
export function diagnoseDataRoot(dataRoot: string, fs: FsProbe): { reach: Reach; detail: string };

// episodes.ts —— 规则镜像 cli.py:122-158，判目录用跟随符号链接的 stat；由 TI-5 对拍
export interface EpisodeEntry { epKey: string; abs: string; mtimeMs: number }
export function listEpisodes(episodesRoot: string, fs: FsProbe): { visible: EpisodeEntry[]; hiddenUnderscore: number };

// tailer.ts —— 纯状态机，fs 注入；判定顺序见 §2.4 表
export interface TailState { dev: bigint; ino: bigint; offset: number; anchor: Uint8Array; generation: number }
export function pollOnce(state: TailState | null, file: string, fs: FsRead, splitter: LineSplitter):
  { state: TailState | null; newLines: string[]; resynced: boolean; absent: boolean };

// store.ts —— 只经 parseLossless 解析（§3.1 规则 8）；fingerprintsMatch 用 stat({ bigint: true })
// 返回 host 内部形状 ApprovalRecord（artifacts[].mtime_ns: bigint）；写进 snapshot/delta 前经 toWireApproval 转成 ApprovalJson（mtime_ns: string，规则 9）
export function readApprovalStore(file: string, fs: FsRead): EpisodeSnapshot["approvals"];
export function fingerprintsMatch(epAbs: string, pinned: ArtifactFingerprint[], fs: FsProbe): boolean;   // ArtifactFingerprint = { path: string; size: number; mtime_ns: bigint }

// heal.ts —— 触发闭集 H1–H5 的唯一入口
export type HealTrigger = "H1-activated" | "H2-step-changed" | "H3-user-refresh" | "H4-pre-ack" | "H5-artifact-drift";
export function requestHeal(epKey: string, trigger: HealTrigger): Promise<{ ok: boolean; stderrTail: string }>;   // 同期合并，在途 +1 排队；失败写诊断
export interface H5State { baselineTaken: boolean; lastSample: Map<string, string>; fired: Set<string> }   // 键 = approvalId，值 = 指纹元组的规范字符串
export function h5Step(state: H5State, objs: ApprovalRecord[],
                       stat: (rel: string) => { size: bigint; mtimeNs: bigint } | "missing"):
  { state: H5State; fire: boolean; rejectedPaths: string[] };   // 纯函数：基线、最新对象、稳定期、段检查（§2.12，TE-14）

// gateCheck.ts —— 全 desktop/ 唯一允许出现 04-clips.approved.json 字面量的文件（TG-7 豁免）
export function gateMatches(epAbs: string, pinned: ArtifactFingerprint[], fs: FsProbe): boolean;

// spawner.ts —— desktop/ 中唯一 import "node:child_process" 的文件
export type Template = "PROBE_APPROVALS" | "PROBE_FREEZE" | "GIT_HEAD" | "GIT_DESKTOP_DIFF"
  | "STATUS" | "HEAL" | "APPROVE" | "REJECT" | "REVIEW_APPROVE";
export function runCore(t: Template, args: TemplateArgs[Template], ctx: { repoRoot: string }):
  Promise<{ code: number | null; signal: string | null; stdoutTail: string; stderrTail: string; timedOut: boolean }>;

// settings.ts —— desktop/ 中唯一的写模块，目标恒在 userData
export function loadSettings(userData: string): Settings | null;
export function saveSettings(userData: string, s: Settings): void;   // tmp + rename
```

### 4.3 `approval.decide` 处理骨架（伪代码，冻结顺序）

```ts
async function decide(p: DecideParams): Promise<Result> {
  if (!caps.approvals) return err("E_CAPABILITY");                              // 闸 1
  const ep = episodes.byKey(p.epKey); if (!ep) return err("E_BAD_REQUEST");
  if (reach.value !== "ok") return err("E_UNREACHABLE");
  if (!existsDir(ep.abs)) { refreshEpisodes(); return err("E_STALE"); }           // 红队 m7
  if (!ackLock.tryAcquire(p.epKey)) return err("E_BUSY");
  try {
    const h = await requestHeal(p.epKey, "H4-pre-ack");                           // 红队 B1/B5
    if (!h.ok) diag("heal failed before ack", h.stderrTail);                      // 红队 m8：只记诊断，core 侧 --id 兜底
    testHook("after-heal");                                                      // 仅未打包构建；TA-9 变体 3 在此改写产物，单独考 fingerprintsMatch（v0.5，红队 m2）
    const obj = findById(readApprovalStore(storePath(ep), fs), p.approvalId);     // 闸 2：对象（REVIEW_APPROVE 的 --expect-* 取自 obj.artifacts）
    if (!obj || obj.type !== p.stop || !ackable(obj, p.decision)) return err("E_STALE");
    if (!fingerprintsMatch(ep.abs, obj.artifacts, fs)) return err("E_STALE");     // 闸 2：指纹（bigint 比较，§3.1 规则 8）
    if (p.decision === "approve" && p.stop === "05" && obj.status === "approved"   // 已对齐待确认：先核验再只做确认
        && !gateMatches(ep.abs, obj.artifacts, fs)) return err("E_GATE_MISMATCH");
    testHook("after-fingerprint-check");                                         // 全部 host 侧校验之后、第一个 spawn 之前；仅未打包构建生效（TG-6；v0.4 自查移位）
    for (const s of plan(p, obj)) {                                               // §2.6 表：05 PENDING = [REVIEW_APPROVE, APPROVE]，已对齐 = [APPROVE]；--expect-mtime-ns 用 String(bigint)
      const r = await runCore(s.t, s.args, ctx);                                  // APPROVE/REJECT 带 --id
      if (r.timedOut) return err("E_TIMEOUT");
      if (r.code !== 0) return err("E_CORE", r.stdoutTail, r.stderrTail);
      if (s.t === "REVIEW_APPROVE" && !gateMatches(ep.abs, obj.artifacts, fs))    // 闸 3
        return err("E_GATE_MISMATCH");
    }
    if (!verified(readApprovalStore(storePath(ep), fs), p)) return err("E_UNVERIFIED");  // 闸 4
    return ok();
  } finally { ackLock.release(p.epKey); await resnapshot(p.epKey); }             // 只此一处（红队 m10）
}
```

- `ackable(obj, "approve")`：`PENDING`，或 `APPROVED ∧ resolved_by == "artifact" ∧ confirmed_by == null`（Spec 3 v0.4 确认路径）；`ackable(obj, "reject")`：`PENDING`。
- `gateMatches`：`stat(04-clips.approved.json)` 的 (size, mtime_ns) 等于 `pinned` 中 `04-clips.json` 的钉住值（bigint 纳秒比较）。
- `verified`：approve → `status == "approved"` 且（`resolved_by == "cli"` 或 `confirmed_by == "cli"`）；reject → `status == "rejected"` 且 feedback 两字段逐字节相等。

### 4.4 main 与 preload

- main：第一行做启动参数白名单检查（packaged：`process.argv.slice(1)` 只允许 `-psn_*`，红队 m3）；repoRoot 选择对话框与二次确认在 main 内完成（R2-M5）；`requestSingleInstanceLock()`；`registerSchemesAsPrivileged` 在 `app.whenReady()` 之前；`BrowserWindow` 的 `webPreferences` 固定为 `{ contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true, preload }`，不开 `webviewTag`；`setWindowOpenHandler` 拒绝、`will-navigate` 阻止；`utilityProcess.fork(hostEntry, [], { serviceName: "ava-host", stdio: "pipe" })`（`electron.d.ts:22028` 起注明 utilityProcess 的 stdin 只能是 ignore）；host 就绪时向 stdout 打印 `AVA_BOOT host-ready lossless-json=<ok|fail>`（供打包版验证，§7.1；后半为 §3.1 规则 8 启动自检结果）。
- preload：只做 `ipcRenderer.on("ava-port", (e, handshake) => window.postMessage({ type: "ava-port", handshake }, "*", e.ports))`，不 `exposeInMainWorld` 任何函数。main 每次撮合把单调递增的握手号随端口一起投递；`rendererLoaded` 只跟踪主框架导航（`did-navigate`）。（**S22 审核修订，经用户同意**：原 `once` 使页面不重载时的重新撮合全部丢失；iframe 导航触发的 `did-start-loading` 曾把撮合静默卡死。）
- renderer 接收端口时**只接受 `event.source === window` 的消息**，每次 main 撮合只接受一次——握手号严格大于已采纳的号才采纳（两条规则各自独立守卫：TI-2 在重新握手窗口内注入与真实消息同形、握手号极大的伪造端口，使「只接受一次」无法掩盖 source 校验的缺失，红队 R2-M3）；iframe 向父窗口 `postMessage` 的同名消息一律忽略（否则 `allow-scripts` 的 gallery 页面可伪造假 host 端口）。RPC 不设超时：当前端口 `close` 即把挂起请求以 `E_UNREACHABLE` 拒绝，断开期间的新请求立即拒绝（S22 审核修订，经用户同意）。

---

## 5. 依赖白名单与纯洁性保障

### 5.1 npm 依赖精确白名单（`desktop/package.json`）

- `dependencies` 键集合**恰为** `{react, react-dom, markdown-it}`；`devDependencies` 键集合**恰为** §2.1 表中其余各包加 `@types/react`、`@types/react-dom`、`@types/markdown-it`（版本 PR1 锁定）。版本必须是精确版本（`^\d+\.\d+\.\d+$`）。TG-1 以 exact-set 断言守护——新增任何依赖 = 修订本 spec。
- 由此排除：agent/LLM SDK（ADR-0018 保留条款）、`express`/`fastify`/`ws`/`socket.io`（红线 2）、`sqlite3`/`better-sqlite3`（红线 1）、状态管理库、UI 组件库。Electron 内置模块不受依赖白名单约束，另由 TG-8 约束（`crashReporter.start`、`autoUpdater` 不得调用）。

### 5.2 模块级 import 纪律

| 目录 | 允许 | 禁止 |
|---|---|---|
| `shared/` | 纯 TS | 任何 `node:*`、`electron`、DOM 全局 |
| `renderer/` | `react`、`react-dom`、`markdown-it`、`shared/` | `electron`、任何 `node:*` |
| `preload/` | `electron`（仅 `ipcRenderer`） | 其他一切 |
| `host/` | `node:fs`、`node:fs/promises`、`node:path`、`shared/`；`node:child_process` **仅 `spawner.ts`** | `node:http`、`node:https`、`node:net`、`node:dgram`、`fetch`、`electron` |
| `main/` | `electron`、`node:fs`、`node:path`、`node:stream`、`shared/` | `node:child_process`、`node:http(s)`、`node:net`；`crashReporter.start`、`autoUpdater` |

### 5.3 Python 侧：本 spec 的 PR 零改动、零新依赖

- 本 spec 全部 PR **不修改 `pipeline/`、`config/` 任何文件**，不修改 `pyproject.toml` 依赖（门禁 9）。**桌面端依赖的 core 改动（S3-R1/R2/R5/R6/R7）全部经 Spec 3 修订走、在 Spec 3 的 PR 中施工**（红队 m11）。本 spec 唯一的 Python 侧新增是测试文件 `tests/test_desktop_core_isolation.py`（PR0）。
- **core 无 server 依赖的独立子进程断言**（期望值已先跑，实测 `[]`）：

```python
def test_core_imports_no_server_stack():
    """core 热路径不因桌面端载入任何 server / 数据库 / ML 栈（独立子进程，防共享进程假阳性）。"""
    import subprocess, sys
    probe = (
        "import sys, pipeline.agent.cli, pipeline.status, pipeline.review; "
        "bad = ('http.server','socketserver','wsgiref','asyncio','websockets','aiohttp',"
        "'fastapi','uvicorn','flask','starlette','sqlite3','numpy','torch'); "
        "leaked = [m for m in bad if m in sys.modules]; "
        "assert not leaked, leaked"
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
```

---

## 6. 跨 Spec 接口与系统边界

### 6.1 与 Spec 2（jobs 层与 events.jsonl）

- **消费的契约**：文件位置 `data/episodes/<期>/events.jsonl`；每行 `json.dumps(sort_keys=True) + "\n"`，在 `fcntl.flock` 内整行写入（Spec 2 §2.3 第 3 条、§4.1）；append-only；9 个 `EventType` 值与六种载荷形状。
- **桌面端依赖的 Spec 2 保证只有两条**：整行写入 + append-only。**不依赖**事件不丢、`episode` 字段准确、`job_heartbeat` 存在；时间线如实声明有损（§2.4）。
- **施工依赖声明**：`pipeline/jobs.py` 未施工。PR2 的 tail 集成测试以「按 Spec 2 §3.3 格式写行的 Python 写端」（`json.dumps(sort_keys=True)` + `fcntl.flock`）驱动；Spec 2 施工后改用真实 `EventPublisher` 回看 TE-1 转绿（门禁 11 caveat）。
- **RF-7 边界**：桌面端 v1 不向 core 长任务发信号；超时只针对短命令进程组。将来拉起长任务必须先落 Spec 2 RF-7 的 SIGTERM 优雅关闭。

### 6.2 与 Spec 3（approval 对象化）——修订请求及其去向

- **消费的契约**：Approval JSON 形状（Spec 3 v0.6 §3.1/§3.2；其中 `mtime_ns` 为可能超过 2^53 的 JSON 整数，本 spec 按 §3.1 规则 8 无损解析）、裸形态 ack 子命令与退出码（v0.6 §4.2）、`review --approve` 的期望指纹参数（v0.6 §4.5）、「桌面端 ack 不直写对象库」（§4.4）、`approval_requested`/`approval_resolved` 载荷（§3.3）。
- **施工依赖**：`pipeline/approvals.py` 未施工 → **PR4 阻塞于 Spec 3 v0.6 PR3 施工且其 T15–T21（含 T20 ⑥）全绿、`tests/test_review.py` 零回归**；PR1–PR3 不依赖（能力探针失败时决策条只读）。
- **修订请求去向**（S3-R1~R5 于 2026-09-23 用户授权后并入 Spec 3 v0.3；S3-R6/R7 出自本 spec 红队一轮 B1/B2，沿用同一授权并入 Spec 3 v0.4；S3-R9/R10/R11 出自本 spec 红队二轮 R2-B1/R2-M7/R2-M8 并入 Spec 3 v0.5，其中 S3-R9 改 `pipeline/review.py` 经用户单独授权；Spec 3 v0.6 按本 spec 红队三轮 R3-M1 在 S3-R9 授权范围内收紧 §4.5；Spec 3 PR3 待红队定向复核）：

| 编号 | 请求 | 原始级别 | 去向 |
|---|---|---|---|
| **S3-R1** | 裸形态参数按 argv 位置取，无损 | 阻塞 | 已并入 Spec 3 v0.3 §4.2 第 1 条（v0.4 在其中加入 `--id`） |
| **S3-R2** | ack 子命令退出码契约 0/1/2 | 阻塞 | 已并入 v0.3 §4.2 第 2 条 |
| **S3-R3** | `/approvals --json` | 非阻塞 | 后置 |
| **S3-R4** | `source` 增加 `"desktop"` | 非阻塞 | 后置 |
| **S3-R5** | 未识别子命令不落入 REPL | 建议 | 收窄并入 v0.3 §4.2 第 3 条：只在非 TTY 下退出 2——`docs/WORKFLOW.md` 文档化的 `ava <期> /chat`、`/script` 在交互终端靠落入 REPL 工作（`cli.py:991`、`995`） |
| **S3-R6** | 裸形态 `/approve`、`/reject` 必填 `--id`，指定对象被替换即退出 1，绝不处理替身（红队 B1） | 阻塞 | 已并入 Spec 3 v0.4 §1.4、§4.1、§4.2；顺带改正 Spec 3 T4 与 §2.2 的矛盾 |
| **S3-R7** | artifact 对齐后同类型显式 approve 走确认路径：退 0，首次确认写 `confirmed_by`/`confirmed_at` 并记账（红队 B2） | 阻塞 | 已并入 Spec 3 v0.4 §1.4、§3.1、§4.1 |
| **S3-R8** | 补丁段清单的确定性输出通道 | 条件阻塞 | **不提出**：本 spec 对 B3 选方案 (a) |
| **S3-R9** | `review --approve` 增加 `--expect-size=`/`--expect-mtime-ns=`，同一 fd 核对并只用核对过的字节写解封物（红队 R2-B1） | 阻塞 | 已并入 Spec 3 v0.5 §1.5、§4.5（改 `pipeline/review.py`，用户单独授权）；v0.6 按红队 R3-M1 收紧：读后再 fstat 一次，(size, mtime_ns) 须不变且读出字节数等于 size（Spec 3 §1.6、T20 ⑥、MUT-24） |
| **S3-R10** | 公共函数一次进锁，拆出 `_self_heal_locked`（红队 R2-M7） | 阻塞 | 已并入 Spec 3 v0.5 §1.5、§4.1 内部规格；T21 |
| **S3-R11** | 确认路径仅在「对齐发生于本次调用」时记延迟（红队 R2-M8） | 阻塞（数据语义） | 已并入 Spec 3 v0.5 §1.5、§4.1；T19/T19b |
| **S3-R12** | 对象库 `artifacts[].mtime_ns` 改存十进制字符串（红队 R3-B1 的退路） | 条件阻塞 | **不提出**：启用时必须在同一次修订里同步改本 spec §3.1 规则 8 与 TE-13（否则规则 8 会把字符串值判为解析失败，红队 m3）；仅当 PR2 首日的启动自检证明 Electron 44 运行时不支持 reviver 的 `context.source`（假设 10）时才提出 |

### 6.3 与 Spec 4/5（网络工具）

- 桌面端 v1 **零出网**，不在 `assert_egress_boundary` 的作用面内；未来对话面板出网时须重新纳入 egress 审计。
- **profile 隔离**：Spec 5 的 browser 持久化 profile 在 `data/browser-profile`（Spec 5 §2.2）；桌面端 Chromium profile（userData）在内置盘。二者互不共享（防 `SingletonLock` 冲突），TI-4 断言；且由 §3.5 规则 2，`ava-media://` 在构造上读不到 `data/browser-profile`。

### 6.4 与 Spec 1 / Spec 6 / Spec 7

- Spec 1：无接口。Spec 6：v1 不读写 `candidates.json`。Spec 7：v1 不读写 `data/library/memory.md`，且 §3.5 规则 2 使其经媒体协议不可达。

### 6.5 与 `pipeline.status` 的边界

桌面端对工序的全部认知来自 `status --json`；`desktop/src` 中不得出现解封物判定用的产物名字面量 `02-diff.patch`、`04-clips.approved.json`（TG-7）。**如实声明（红队 m11）**：TG-7 只防无意识引入，字符串拼接即可绕过；「UI 不自己推导闸门」的真防线是门禁 2 的真期人工对照与代码评审。唯一豁免：`host/gateCheck.ts` 需要 stat `04-clips.approved.json`——它核验「第 ① 步产出物是否对应人审版本」，是事实比对，不参与闸门判定。`STOP_PREVIEW` 只是展示映射。

### 6.6 ADR-0018 保留条款逐条对照

| 保留条款 | 桌面端如何不越界 |
|---|---|
| 不引入第三方 Agent 框架 | §5.1 npm 精确白名单 |
| Code Freeze | 不改 `pipeline/`（门禁 9）；健康面板复用 `check_code_freeze`；UI 层由 asar 完整性 + 构建溯源横幅做可见性冻结（§2.10） |
| 配音双层闸（永不 `--force`） | spawn 闭集无 tts 模板；唯一的 `/run` 模板是 `review --approve`，仍经 `validate_pipeline_command` 的 `--force` 拒收（`tools.py:188-205`） |
| 封面标题只出候选 | 无任何「定稿」动作；09 批准是纯记录 |

---

## 7. 测试规格与变异检验

### 7.1 测试规格

> **同步点铁律（继承 Spec 2 §7.1）**：凡读 `events.jsonl` 的断言，写端先关闭/flush 再读；host tail 断言一律「驱动 N 次 `pollOnce`」，不 sleep 等轮询。  
> **真实数据零污染（继承 Spec 2 T8）**：全部夹具在临时目录；测试前后断言真实 `<repo>/data/_events.jsonl` 未被创建/修改。  
> **临时 repo 副本夹具（TA 系列）**：`review.py:350` 的 `require_data()` 检查 `paths.ROOT/"data"`，因此端到端 ack 链路在「复制 `pipeline/`、`config/`、`pyproject.toml` + 软链 `.venv` + 建 `data/library/` 与 `data/episodes/<夹具期>`」的临时 repo 副本上跑（假设 5）。  
> **期望值先跑（红队 M6）**：TP-2、TS-3、TS-6 的具体期望值在 PR3/PR5 首次实跑后写入，本表只写断言结构。  
> **打包版验证的可行路径（红队 M7）**：Playwright `_electron` 经 inspector 驱动 main，`enableNodeCliInspectArguments=false` 后不可用。因此：功能 e2e（`e2e/`）只跑**未打包构建**；打包版（`e2e-packaged/`）只做非 inspector 验证——直接拉起 `.app/Contents/MacOS/<productName>`，读 stdout 的 `AVA_BOOT`/`AVA_REFUSE` 行、查退出码、`lsof`、`ps`。打包版上的 UI 交互**没有自动化验证**，由门禁 15 手验覆盖。

**TE：host 消费 events.jsonl 的集成测试**（vitest，真实文件系统，Python 写端子进程）

| 编号 | 场景 | 断言 |
|---|---|---|
| **TE-1** | 并发追加完整性 | Python 写端在 flock 内追加 2000 行、host 每写 37 行驱动一次 `pollOnce`；最终下发序列与文件逐行相等。Spec 2 施工后写端换真实 `EventPublisher` |
| **TE-2** | 半行 | 写前半行 → 零下发；补齐 → 恰下发 1 行 |
| **TE-3** | 截断 | 截为 0 再写 3 行（总长小于原 offset）→ `resynced: true`、**`generation` 恰 +1**、下发恰为新 3 行 |
| **TE-4** | 截断后写长 | 写入比原 offset 更长的新内容（inode 不变）→ 锚点不符触发 resync，下发等于新文件全文 |
| **TE-5** | 替换 | 以新 inode 文件 `rename` 覆盖 → resync |
| **TE-6** | 缺席与出现 | 文件不存在 → `absent: true`；随后创建 → 从 0 读起 |
| **TE-7** | 损坏行与超长半行 | 非 JSON 行跳过且 `malformed == 1`；1 MiB+1 字节无换行 → 丢弃并跳到下一行、不重读（`resynced: false`），其后的完整行照常下发，诊断 +1（S21 修订） |
| **TE-8** | host 侧串扰隔离 | 期 A、B 同时写；A 的行永不出现在 B 的 delta；行内 `episode` 写成 B 的 A 文件行仍归 A，`episodeFieldMismatch == 1`；`sidecar_degraded` 行不进 `jobs`，进 `degradedNotices` |
| **TE-9** | 折叠与缺口呈现 | Spec 2 §3.3 六个示例载荷 → `foldEvents` 逐字段等于期望；删去 `job_finished` 且 pid 指向已退出进程：第 1 个 tick `finishedEventMissing == false`、第 2 个 tick `== true`；pid 存活时恒 false；`job_created` 后无后续且超过 `PENDING_STALE_MS` → `noFollowupEvents == true` |
| **TE-10** | renderer reducer 协议 | `generation` 不同的 delta 被丢弃；`seq` 跳号触发 resnapshot 请求 |
| **TE-11** | 期目录在两次 poll 间被删（红队 M5） | 首次 poll 正常；删除期目录；再 poll → `unreachable`，**期目录仍不存在** |
| **TE-12** | renderer 多期 reducer（红队 M5） | 交错投喂 A、B 两期的 snapshot 与 delta；`select(store, "B")` 的 jobs 与决策条对象集合中不含任何 A 的 jobId/approvalId |
| **TE-13** | 纳秒 mtime 无损通路（红队 R3-B1；期望值已按附表实跑） | 夹具文件经 Python `os.utime(ns=…)` 设为 `mtime_ns = 1790171112636927676`（不能被 double 精确表示）；对象库与事件行都由 Python `json.dumps` 写出（走真实写端形状，不手写 JSON）→ ① `readApprovalStore` 读出 `mtime_ns === 1790171112636927676n`，`fingerprintsMatch` 为 true；② `parseEventLine` 读出的 `approval_requested` 载荷 `artifacts[0].mtime_ns` 同样相等；③ `String(obj.artifacts[0].mtime_ns)` 逐字节等于用正则从对象库原文取出的数字串；④ 经 `toWireApproval`/`toWireEvent` 后下发给 renderer 的是十进制字符串；⑤ `losslessSelfCheck` 注入一个忽略 `ctx.source` 的解析器 → false，此时健康数据 `losslessJson: false`、`approval.decide` 返回 `E_CAPABILITY`、零 spawn；⑥ 对象库里 `mtime_ns` 写成 `1.79e18` → 整份按解析失败处理（`state: "error"`），不回退到有损值；⑦ `mtime_ns` 写成字符串 `"1790171112636927676"` 或 `null` → 同样整份解析失败（v0.5，红队 m3） |
| **TE-14** | H5 调度纯函数（红队 R3 m1/m2/m3） | 逐次驱动 `h5Step`：① 设为活跃后第一次采样即使存在漂移也 `fire: false`（基线）；② 同一停机点有一条历史 REJECTED（钉住旧指纹、磁盘早已不同）和一条更新的 APPROVED → 任意多次采样均不触发（只看最新对象）；③ 最新对象为 PENDING、指纹变为 T1：第一次采样不触发，第二次仍为 T1 才触发且恰一次；T1→T2→T3 每个只持续一次采样 → 零触发；④ `artifacts[].path` 为 `../x`、`/etc/hosts`、`a//b` → 注入的 stat 一次都未被调用，`rejectedPaths` 恰为这三者 |

**TA：ack 链路端到端**（UI 按钮 → host spawn → core 对象库；PR4，依赖 Spec 3 v0.6 PR3）

| 编号 | 场景 | 断言 |
|---|---|---|
| **TA-1** | 打回（无损） | 夹具期 05 停机点点「打回」，target `04-clips.json s07 1:20`、problem 含换行、双引号、前导 `-` → spawn argv 恰为 `REJECT` 模板（含 `--id <A>`），target/problem 各占一个元素；对象 `rejected`，feedback 逐字节相等；`_agent/approval_feedback.md` 含原文 |
| **TA-2** | 05 批准（两步 + 确认路径） | 夹具 `04-clips.json` 与 manifest 段级对齐 → 点「批准」→ **该次 decide 关联的** spawn 序列恰为 `[HEAL, REVIEW_APPROVE(--expect-size=…, --expect-mtime-ns=… 等于钉住值), APPROVE --id A]`；夹具 `04-clips.json` 的 mtime 经 `os.utime(ns=…)` 设为 `1790171112636927676`，argv 中 `--expect-mtime-ns=` 之后的数字串逐字节等于对象库原文里的该数字（红队 R3-B1）（批准后 05→06 引起的 H2 heal 按 trigger 标签排除，红队 m4）；`04-clips.approved.json` 与 `04-clips.json` 字节相等且 (size, mtime_ns) 相等；对象 `approved`、`resolved_by == "artifact"`、`confirmed_by == "cli"`；`approvals.jsonl` 恰新增 1 行且 `decision_latency_s` 非空（对齐发生在同一次 `approve` 调用中，Spec 3 S3-R11）；Spec 2 在场时读出 `job_finished`(review) 与带 `confirms` 的 `approval_resolved` |
| **TA-2b** | 05 已在终端批准后确认 | 含补丁段的夹具期先在终端执行 `python -m pipeline.review <期> --approve`（输入 y）→ 桌面端点「批准」→ 该次 decide 关联的 spawn 序列恰为 `[HEAL, APPROVE --id A]`，**无 `REVIEW_APPROVE`**；对象 `confirmed_by == "cli"`；`approvals.jsonl` 恰新增 1 行且 `decision_latency_s` 为空（对齐发生在更早的 heal 中，S3-R11） |
| **TA-3** | 05 批准失败原样呈现（红队 B3） | ① 段级不对齐夹具 → `E_CORE`，UI 同时显示 stdout 与 stderr 尾部（含 `review.py` 的「段级时长不对齐」原文）；② 补丁段夹具 → UI 显示的 stdout 尾部含「补丁段（段号：…）」原文；两种情况下**界面上不存在任何重试按钮**，对象仍 `pending` |
| **TA-4** | 陈旧卡 | UI 显示 pending 后在终端先行 `ava <期> /approve 03.5 --id A` → 再点按钮 → `E_STALE`，零 ack spawn |
| **TA-5** | 无点击零 ack | 打开期、预览 `04-review.html`、播放音频、切换期、重载窗口全过程中 `APPROVE/REJECT/REVIEW_APPROVE` spawn 计数为 0 |
| **TA-6** | 能力缺席 | repoRoot 指向无 `pipeline/approvals.py` 的副本 → 按钮禁用、`HEAL` 从未 spawn；期目录无 `human_time.json` |
| **TA-7** | 后置核验 | 以「退出码 0 但什么都不做」的假 core 替换 `APPROVE` → `E_UNVERIFIED`，UI 不显示成功 |
| **TA-8** | stdin 恒 ignore | 假 core 执行 `input()` → 2 s 内 EOF 非 0 退出 |
| **TA-9** | 不批准替身（红队 B1；v0.3 按 R2-M1 改用单步的 03.5） | 03.5 停机点，经测试钩子（`after-fingerprint-check`，仅未打包构建；v0.4 起确实位于全部 host 侧校验之后）在 spawn 之前改写 `03-audio/manifest.json`（改写后仍须合法：只改一段时长；先断言改写后 `status --json` 的 `current_step` 仍为 03.5，否则自愈不会新建 B，红队 R3 m5）→ `APPROVE 03.5 --id A` 退 1 → `E_CORE`（stderr 含「已被替换」）；A 为 SUPERSEDED；自愈新建的 B 仍为 PENDING、`approvals.jsonl` 无新增；变体 2：改写发生在 HEAL 之前 → heal 把 A 转为 SUPERSEDED，**经 `ackable` 返回 `E_STALE`**、零 ack spawn（v0.5 按红队 m2 更正：此路径走不到指纹校验）；变体 3：经 `after-heal` 钩子在 heal 之后改写 → A 仍为 PENDING、`ackable` 通过，**由 `fingerprintsMatch` 返回 `E_STALE`**、零 ack spawn |
| **TA-9c** | 05 物理闸门竞态（红队 R2-B1） | 05 停机点，同一钩子点把 `04-clips.json` 改写为**段级对齐**的 F2 → `REVIEW_APPROVE` 退 1 → `E_CORE`，UI 显示 core 的「已不是审阅时的版本」提示；`04-clips.approved.json` **不存在**；`python -m pipeline.status <期> --json` 的 `current_step` 仍为 05；第 ② 步未 spawn |
| **TA-9b** | 解封物指纹核验（红队 B1） | 以假 `REVIEW_APPROVE` 生成一个与对象钉住指纹不同的 `04-clips.approved.json`（模拟 ① 执行期间 clips 被改）→ `E_GATE_MISMATCH`，**第 ② 步未 spawn**，UI 显示「批准文件对应的不是你审阅的版本」，host 未删除该文件 |
| **TA-10** | 超时杀进程组（红队 M2） | 假 core fork 一个孙进程，孙进程 70 s 后写一个标记文件；`REVIEW_APPROVE` 60 s 超时 → 进程组被杀 → 80 s 时标记文件**不存在**；`E_TIMEOUT` 文案为「结果未知，请以稍后刷新为准」 |
| **TA-11** | heal 触发闭集（红队 B5、R2-M4、R3 m1） | 前置：期 A 的对象库带一条已被取代的历史 05 REJECTED（钉住的指纹与磁盘不同）。打开 app → 依次打开期 A、B、C（C 为活跃）→ 终端把 A 推进到停机点 → 在 C 上点刷新 → 终端把 C 推进到停机点 → **切回 A** → 在 A 上打回 05 → 终端对 A 返工 `04-clips.json`。断言 HEAL spawn 序列（按 trigger 标签）恰为：A(H1)、B(H1)、C(H1)、C(H3)、C(H2)、A(H1)、A(H4)、A(H5)；A 在后台被推进时**无** heal；切回 A 后决策条出现 A 的 pending；返工后新 pending 出现且旧 REJECTED 卡片消失；B 的对象库在其 H1 之后再无写入；历史 REJECTED 从未引起 H5 |
| **TA-12** | repoRoot 切换与在途命令互斥（红队 R3 m4） | ① 用 `after-fingerprint-check` 钩子把一次 decide 停在 spawn 之前 → 发 `app.requestRepoRootChange` → `E_BUSY`；main 侧对话框桩（未打包构建）记录 `dialog.showOpenDialog` 调用次数为 0；② 放行钩子，该 decide 正常完成且后置核验读的是原 repoRoot；③ 对话框桩返回新 repoRoot 并确认、同时用钩子暂停一个在途 STATUS → 此时发 `approval.decide` 得 `E_BUSY`；放行后切换完成，旧代号 STATUS 的结果未进入任何 snapshot |

**TI：profile / 进程隔离 / 写不变量**

| 编号 | 断言 |
|---|---|
| **TI-1** | renderer 中 `typeof require === "undefined"`、`typeof process === "undefined"`；`window` 上除端口外无 preload 暴露对象 |
| **TI-2** | `04-review.html` iframe 的 `contentDocument === null`；伪造端口在**重新握手窗口**内注入（红队 R2-M3）：**放在 shots 根下**（`allow-scripts`）的夹具 html 持续向父窗口投递与 preload 同形、握手号极大的伪造端口，受 `!app.isPackaged` 守卫的 main 侧钩子触发一次重新撮合并延迟投递真实端口 → 断言 renderer 采纳的握手号恰为这次撮合的号（真实端口确实被采纳，不是靠旧端口通过）、后续请求全部到达真实 host、伪造端口收到零条消息。钩子在两侧：renderer 侧「读出已采纳的握手号」与 main 侧「重新撮合并延迟投递真实端口」（S22 审核修订：原 renderer 侧「进入等待端口」在握手号机制下已无意义），均受 `!app.isPackaged` 守卫（红队 R3 m6）；观测方式：Playwright 对不透明源 iframe 做 frame evaluate，由 iframe 脚本统计 port2 收到的消息数 |
| **TI-3a** | 关闭 heal（能力探针置为缺席）后，对 `chmod -R a-w` 的夹具树执行期列表、订阅、tail、树浏览、全部预览 → 功能正常，前后树的（路径, 大小, mtime）清单完全相同（I1） |
| **TI-3b** | 在可写夹具树上只触发 H1 → 树清单差异 ⊆ { `_agent/`（若原先不存在）、`_agent/approvals_store.json`、`_agent/approvals_store.lock`、`events.jsonl`（Spec 2 在场时） }；期目录本身与其他产物零变化（I2 自愈类） |
| **TI-4** | `app.getPath("userData")` 与 `app.getPath("crashDumps")` 均不在 `realpath(dataRoot)` 子树内；userData 不等于 `data/browser-profile`、不在 `~/Library/Application Support/Google/Chrome` 子树内；`crashDumps` 在 userData 子树内 |
| **TI-5** | **期列表对拍**：同一夹具树（含 `.`/`_` 前缀、含与不含 `01-topic.md` 的子目录、**软链期目录**）上，TS `listEpisodes` 与 Python `cli.get_episodes_list(root=…)` 的（相对路径集合, 顺序, `hidden_underscore`）完全一致 |
| **TI-6** | `app.getAppMetrics()` 含 `type == "Utility"` 且 `name == "ava-host"` 的进程（S22 审核修订，经用户同意：Electron 44 实测 fork 传入的 `serviceName` 出现在 `name` 字段，`serviceName` 字段恒为 `node.mojom.NodeService`）；Python 子进程 `ppid == host pid`；app 全部进程 `lsof -iTCP -sTCP:LISTEN` 为空 |
| **TI-7** | 子进程 `os.environ` 键集合 ⊆ §3.4 白名单；host 环境里设 `AVA_EVENTS_ROOT` 与 `FOO_API_KEY` 后子进程均看不到 |
| **TI-8** | `approval.decide` 参数多一个 `path` 键 → `E_BAD_REQUEST`、零 spawn；合法请求的 spawn argv 中期路径恒等于 host 映射路径（红队 M5：MUT-6 的被依赖断言）；`app.requestRepoRootChange` 带任何参数（如 `{ path: "/tmp/x" }`）→ `E_BAD_REQUEST`，settings 不变、不弹对话框（红队 R2-M5） |
| **TI-9** | （未打包构建）`kill -9` host pid → 10 s 内健康面板显示 host 在线且活跃期重新订阅成功（红队 M5：MUT-23 的被依赖断言） |
| **TI-10** | 已运行一个实例时再次启动**同一构建** → 第二个进程 5 s 内退出，已有窗口获得焦点；全机仅一个 `ava-host`。如实声明：开发构建与打包构建 userData 不同，单实例锁拦不住二者同时运行（红队 m10，RF-21），本用例只覆盖同一构建 |

**TC：core 零改动回归**（PR0，pytest）

| 编号 | 断言 |
|---|---|
| **TC-1** | §5.3 独立子进程探针 |
| **TC-2** | `pyproject.toml` dependencies 与 optional-dependencies 中无 server/DB 包名 |
| **TC-4** | 本 spec 各 PR 的 diff 不触碰 `pipeline/`、`config/`（PR 验证命令中 `git diff --name-only <base>... -- pipeline/ config/` 为空）。（v0.1 的 TC-3 已按红队 m6 删除，编号不复用） |

**TS：打包加固 / 协议安全**

| 编号 | 断言 |
|---|---|
| **TS-1** | 打包产物复制两份：A 份翻转 `app.asar` 内 **main 入口文件**内容区一个字节；B 份翻转 **asar 头**一个字节 → 两份启动均非 0 退出、10 s 内 stdout 无 `AVA_BOOT`；原件出现 `AVA_BOOT host-ready`（红队 M7） |
| **TS-2** | `verify-fuses.mjs` 读出**全部 9 位**：8 位与 §2.10 表逐项一致，`WasmTrapHandlers` 等于期望表中记录的 Electron 默认值；Info.plist 含 `ElectronAsarIntegrity`；打包版拉起后 stdout 出现 `AVA_BOOT host-ready`（证明 host 从 asar 拉起成功），且该行含 `lossless-json=ok`（假设 10 在打包运行时成立，红队 R3-B1） |
| **TS-3** | 路径守卫（期望值先跑）：**root 内部的 `%2F` 注入 `ava-media://episodes/<EP>%2F02-script.md` → 必须 400**（只有规则 1 能拦住：拼接结果仍在 root 内，规则 2 会放行，红队 R2-M2）；跨 root 的 `%2F`/`%5C` 段注入、`\0`、空段、夹具内指向**所选 root 外**的符号链接（含指向同一 dataRoot 下另一 root 的链接）、白名单外扩展名 → 分别拒绝；`..`/`%2e%2e` 的实际状态码按 PR3 实测写入；`ava-media://shots/..%2F..%2Fbrowser-profile/x.json` 必不返回 200 |
| **TS-4** | Range：`bytes=100-199` → 206 且正文与文件切片相等；多段 → 416；`POST` → 405 |
| **TS-5** | 出网：renderer 内 `fetch("https://example.com")` 与 `<img src="http://…">` 均被取消 |
| **TS-6** | 打包版分别以 `--remote-debugging-port=0`、`--remote-debugging-pipe`、任意未知开关 `--foo` 启动 → stdout 出现 `AVA_REFUSE argv`、非 0 退出、无 `AVA_BOOT`；不带参数启动正常出现 `AVA_BOOT`；DevTools 端口是否短暂监听按实测如实记录（假设 7） |
| **TS-7** | 打包版带测试专用启动开关启动 → 开关被忽略（经 stdout 诊断行确认 dataRoot 仍为 `<repoRoot>/data`） |
| **TS-8** | 构建溯源：在 `desktop/` 有未提交改动时构建 → `build-info.json` 的 `desktopDirty == true`，健康数据含红色横幅；干净构建后在 `desktop/` 提交一次 → 黄色横幅；把 `build-info.json` 的 `gitHead` 换成不存在的 sha → 灰色横幅 |
| **TS-8b** | `node_modules` 手改被冲掉（红队 R2-M6） | 在 `desktop/node_modules/react-dom/` 某个会被打进 renderer 的文件里插入标记串，执行 `npm run release-build` → `out/` 下构建产物不含该标记串；`build-info.json` 的 `lockfileSha256` 等于当前 `package-lock.json` 的 sha256 |

**TG：静态守卫**（vitest 扫源码与配置，PR1 起入库）

| 编号 | 断言 |
|---|---|
| **TG-1** | `package.json` 依赖键集合与版本逐项等于 §5.1 白名单 |
| **TG-2** | fs 写类 API 只出现在 `host/settings.ts` |
| **TG-3** | §5.2 import 纪律表逐行成立 |
| **TG-4** | `approval.decide` 在 `renderer/` 中只出现在决策条组件文件，且调用点的祖先是 `onClick` 属性值函数（AST 检查） |
| **TG-5** | `electron-builder.yml`：`asar: true`、无 `disableAsarIntegrity`、`publish: null`、`mac.identity: null`、`mac.target` 仅 `dir`、`electronFuses` 与 §2.10 表一致 |
| **TG-6** | 测试专用启动开关与全部测试钩子（`testHook(...)`、renderer 侧与 main 侧的重新握手钩子、TA-12 的对话框桩）的代码被 `!app.isPackaged` 守卫包裹；扫描范围覆盖 `main/`、`host/`、`renderer/`（红队 m7、R3 m6） |
| **TG-7** | `desktop/src` 不含 `02-diff.patch` 字面量；`04-clips.approved.json` 字面量只出现在 `host/gateCheck.ts` |
| **TG-8** | `desktop/src` 不调用 `crashReporter.start`、不引用 `autoUpdater` |
| **TG-9** | `desktop/src/host/` 与 `desktop/src/shared/` 中 `JSON.stringify` 只出现在 `shared/losslessJson.ts`（`stringifyLossless` 的实现内）（v0.5，红队 m1） |

**TP：预览**（Playwright，未打包构建，夹具期）

| 编号 | 断言 |
|---|---|
| **TP-1** | `04-review.html` 在 iframe 内渲染，相对路径缩略图全部加载成功 |
| **TP-2** | 夹具视频（帧率在夹具中声明）拖动到 12.5 s 后，浮层显示值等于 rVFC 返回的 `mediaTime` 格式化结果；具体期望字符串按夹具实跑写入（红队 M6：23.976 fps 下 12.5 s 落在 12.4708–12.5125 s 那一帧，不是 12.5xx） |
| **TP-3** | 点 `03-audio/` → 队列等于文件名自然序；首段 `ended` 后自动播放第二段；切换期后页面内 `video`/`audio` 元素数为 0 |
| **TP-4** | `.md` 中的 `<script>` 原文按文本显示不执行；`.json` 折叠/展开；超 5 MiB 显示截断标注 |
| **TP-5** | 夹具 `data` 为悬空符号链接 → `volume-unmounted`；`chmod 000` → `permission-denied`；均无新建目录 |
| **TP-6** | 播放夹具视频时模拟 reach 转 `volume-unmounted`（改夹具 `data` 软链指向）→ 1 s 内 `lsof -p <main pid>` 不再列出 dataRoot 下任何 fd（红队 m9） |

### 7.2 变异检验矩阵

| 变异编号 | 注入变异 | 预期变红 | 证伪机理 |
|---|---|---|---|
| **MUT-1** | `LineSplitter` 把末尾半段也当作一行下发 | TE-2 | 半行被下发或计入 malformed，「零下发」失败 |
| **MUT-2** | 删除 `size < offset` 截断判定（§2.4 顺序 4） | TE-3 | 冻结的顺序使锚点比对只在 `size ≥ offset` 时执行（顺序 5），截断场景下锚点不会介入掩盖；按旧 offset 读不到新行，「下发恰为新 3 行」失败 |
| **MUT-3** | 删除锚点比对 | TE-4 | stat 看不出变化，从旧 offset 续读得到错位字节 |
| **MUT-4** | tailer 在期目录缺席时 `mkdirSync(dir, { recursive: true })` | TE-11、TG-2 | 期目录被删后 poll 把它重建，「期目录仍不存在」失败（v0.1 所引 TE-6/TI-3 在期目录已存在时是空操作，抓不到——红队 M5 更正） |
| **MUT-5** | renderer 状态不按 `epKey` 分桶 | TE-12 | B 期视图出现 A 的 jobId/approvalId |
| **MUT-6** | host 放宽 exact-keys 并使用 `params.path` | TI-8 | 多键请求不再被拒，或 spawn argv 期路径随注入改变 |
| **MUT-7** | `REJECT` 把 target 与 problem 拼成一个 argv 元素（或 `shell: true`） | TA-1 | 前者按 Spec 3 v0.4 固定位置语法 `args[6:]` 为空 → 退 2、对象仍 pending；后者 target 被切碎 → 逐字节断言失败 |
| **MUT-8** | 删除后置核验 | TA-7 | 假 core 零效果退 0，UI 显示成功 |
| **MUT-9** | 删除能力探针（按钮恒启用、heal 恒 spawn） | TA-6 | 「HEAL 从未 spawn」「按钮禁用」失败（Spec 3 v0.3 §4.2 第 3 条施工后，落入 REPL 写 human_time 的路径已被 core 堵住，机理以 spawn 计数为准——红队 M5 更正） |
| **MUT-10** | spawn 的 stdin 改为 `"pipe"` | TA-8 | 假 core 的 `input()` 挂到超时 |
| **MUT-11** | 路径守卫只做字符串 `startsWith`、不 `realpath` | TS-3 | 指向 root 外的符号链接被放行 |
| **MUT-12** | 规则 1 不拒段内 `/`、`\`（红队 M6 更正：原「不拒 `..`」可能被 URL 规范化掩盖） | TS-3（root 内部 `%2F` 用例） | `ava-media://episodes/<EP>%2F02-script.md` 返回 200 或 404 而非 400（v0.3 按 R2-M2 更正：跨 root 用例会被规则 2 拦成 403，抓不到本变异） |
| **MUT-13** | iframe 加 `allow-same-origin` | TI-2 | `contentDocument` 不再为 null |
| **MUT-13b** | renderer 接收端口时去掉 `event.source === window` 校验 | TI-2（shots 根夹具 + 重新握手窗口） | 伪造端口（握手号极大）被采纳，真实端口的握手号更小而被拒，「采纳的握手号恰为本次撮合的号」与「伪造端口收到零条消息」失败（两层掩盖均已排除：夹具在 shots 根才能执行脚本；伪造消息与真实消息同形，只有 source 校验能挡住；S22 审核修订） |
| **MUT-14** | `electronFuses.enableEmbeddedAsarIntegrityValidation: false` | TS-1、TS-2、TG-5 | 篡改 main 入口的包照常出现 `AVA_BOOT`；fuse 读数不符；配置断言失败 |
| **MUT-15** | 加 `express` 并在 host `createServer().listen()` | TG-1、TG-3、TI-6 | 依赖集合不等；import 纪律命中；`lsof` 出现 LISTEN |
| **MUT-16** | `pipeline/` 任意模块顶层 `import http.server` | TC-1 | 独立子进程探针捕获 |
| **MUT-17** | renderer 用文件树里是否存在 `04-clips.approved.json` 决定决策条显示 | TG-7 | 字面量出现在豁免文件之外（防线弱，见 §6.5） |
| **MUT-18** | 预览组件 `useEffect` 在打开 `04-review.html` 时调用 `approval.decide` | TA-5、TG-4 | 无点击出现 ack spawn；调用点祖先不是 `onClick` |
| **MUT-19** | resync 时不递增 `generation` | TE-3 | 「`generation` 恰 +1」失败（红队更正：TE-10 喂人工构造的 delta，抓不到 host 侧变异） |
| **MUT-20** | `diagnoseDataRoot` 把 EPERM 与 ENOENT 都归为 `volume-unmounted` | TP-5 | `chmod 000` 夹具文案错误 |
| **MUT-21** | spawn 继承完整 `process.env` | TI-7 | 子进程看得到 `AVA_EVENTS_ROOT`、`FOO_API_KEY` |
| **MUT-22** | `listEpisodes` 不展平子期，或用不跟随链接的 `Dirent.isDirectory()` | TI-5 | 与 Python 结果不等（软链期目录缺失） |
| **MUT-23** | 删除 host 崩溃重启逻辑 | TI-9 | 10 s 内健康面板不恢复 |
| **MUT-24** | 删除陈旧校验 | TA-4 | 已被终端 ack 的对象上仍发生 ack spawn（Spec 3 同决策幂等使其退 0），「零 ack spawn」失败 |
| **MUT-25** | `APPROVE`/`REJECT` 模板去掉 `--id` | TA-1、TA-9 | Spec 3 裸形态缺 `--id` 退 2 → TA-1 失败；若同时把 core 回退到 v0.3 语义，03.5 上的替身 B 被显式批准，TA-9「B 仍为 PENDING、`approvals.jsonl` 无新增」失败 |
| **MUT-26** | 删除 05 第 ① 步后的解封物指纹核验 | TA-9b | 第 ② 步照常 spawn，「第 ② 步未 spawn」失败 |
| **MUT-27** | 删除 packaged 调试口拒启 | TS-6 | 带开关启动出现 `AVA_BOOT`，无 `AVA_REFUSE` |
| **MUT-28** | spawn 不用 `detached`、超时只杀直接子进程 | TA-10 | 孙进程存活，80 s 时标记文件存在 |
| **MUT-29** | 删除 `webRequest` 出网拦截 | TS-5 | 外部请求未被取消 |
| **MUT-30** | 测试专用开关在 packaged 版也生效 | TS-7、TG-6 | dataRoot 被开关改写 |
| **MUT-31** | `finishedEventMissing` 不看 pid 或只需 1 个 tick | TE-9 | pid 存活时误报，或第 1 个 tick 即为 true |
| **MUT-32** | userData 改到 dataRoot 下 | TI-4 | 子树断言失败 |
| **MUT-33** | 切换期不卸载媒体元素 | TP-3 | `video`/`audio` 元素数不为 0 |
| **MUT-34** | 后台期参与周期性 heal | TA-11 | A 被终端推进时出现 heal spawn |
| **MUT-35** | heal 由对象库 mtime 变化触发 | TA-11 | 每次对象库真实变化后多一次 heal（Spec 3 只在有变更时 `atomic_write`，不会无限循环——v0.3 按复审更正「反馈环」措辞），spawn 序列长度超出期望 |
| **MUT-36** | 构建脚本不写 `desktopDirty` 或健康数据不比对 | TS-8 | 脏树构建无红色横幅 |
| **MUT-37** | 删除 `requestSingleInstanceLock` | TI-10 | 第二个实例常驻，出现两个 `ava-host` |
| **MUT-38** | 05 批准不看对象状态、恒先跑 `REVIEW_APPROVE` | TA-2b | 补丁段夹具上 ① 在补丁确认处 EOF 失败 → `E_CORE`，「spawn 序列无 `REVIEW_APPROVE`」与「`confirmed_by == "cli"`」失败 |
| **MUT-39** | `REVIEW_APPROVE` 模板不带 `--expect-*`（或 core 回退为按路径 `copy2`） | TA-9c | F2 被复制为解封物，「`04-clips.approved.json` 不存在」「`current_step` 仍为 05」失败 |
| **MUT-40** | 删除 H1′（切回活跃期不触发 heal，只在 subscribe 时 heal） | TA-11 | 切回 A 时序列缺 A(H1)，决策条不出现 A 的 pending |
| **MUT-41** | 删除 H5 | TA-11 | 返工后序列缺 A(H5)，旧 REJECTED 卡片不消失、新 pending 不出现 |
| **MUT-42** | 恢复带路径参数的 `app.setRepoRoot` 并在 host 接受 | TI-8 | 带路径参数的请求未被拒，settings 被改写 |
| **MUT-43** | 发布构建脚本去掉 `npm ci` | TS-8b | 手改的标记串出现在构建产物中 |
| **MUT-44** | argv 白名单退回为只拦 `remote-debugging-*` 的黑名单 | TS-6（`--foo` 用例） | 带未知开关启动出现 `AVA_BOOT` |
| **MUT-45** | `store.ts` 改回普通 `JSON.parse` | TE-13 ①③、TA-2 | `mtime_ns` 变成 number `…927700`，指纹比较失败；argv 数字串与对象库原文不等 |
| **MUT-46** | `parseEventLine` 改回普通 `JSON.parse` | TE-13 ② | 事件载荷里的 `mtime_ns` 丢精度，与 stat 不等 |
| **MUT-47** | 启动自检恒返回 true | TE-13 ⑤ | 注入不支持 `ctx.source` 的解析器时 `losslessJson` 仍为 true，decide 未返回 `E_CAPABILITY` |
| **MUT-48** | 删除 H5 基线（设为活跃后第一次采样即可触发） | TE-14 ① | 第一次采样 `fire: true` |
| **MUT-49** | H5 检查全部 PENDING/REJECTED 对象，不限每种停机点最新的那个 | TE-14 ②、TA-11 | 历史 REJECTED 引起 heal |
| **MUT-50** | 删除 H5 稳定期（首次看到新元组即触发） | TE-14 ③ | T1 第一次采样即触发；T1→T2→T3 触发 3 次 |
| **MUT-51** | H5 不做段检查，直接拼路径 stat | TE-14 ④ | 注入的 stat 收到 `../x` 等路径 |
| **MUT-52** | repoRoot 切换不检查在途 decide/heal | TA-12 | ① 中对话框桩被调用；③ 中 decide 未得 `E_BUSY` |
| **MUT-53** | 删除 §4.3 的 `fingerprintsMatch` 一行 | TA-9 变体 3 | spawn 了 `APPROVE --id A`，由 core 退 1 → 得到 `E_CORE` 而非 `E_STALE`，「零 ack spawn」断言失败 |
| **MUT-54** | 规则 8 的 reviver 对非数字 `mtime_ns` 原样放行 | TE-13 ⑦ | 字符串值被接受，解析未失败 |
| **MUT-55** | host 诊断模块直接调用 `JSON.stringify` | TG-9 | 静态扫描命中 `losslessJson.ts` 之外的调用 |

---

## 8. 施工 PR 划分

### PR0：core 隔离守卫（Python 侧，可立即施工）
- **范围**：新建 `tests/test_desktop_core_isolation.py`（TC-1、TC-2）；后续 PR 的验证命令内置 TC-4。
- **验证命令**：`uv run pytest tests/test_desktop_core_isolation.py tests/test_docs_invariants.py`

### PR1：骨架、协议、打包配置与静态守卫
- **范围**：`desktop/` 目录、精确版本 `package.json` + lockfile、四入口构建、单实例单窗口、导航封锁、端口撮合、§3.2 信封与方法闭集（方法体可为桩）、`electron-builder.yml`（asar、9 位 fuses、`publish: null`、`identity: null`）、`write-build-info.mjs`、TG-1~TG-9、`.gitignore` 增加 `desktop/node_modules/`、`desktop/out/`、`desktop/dist/`。
- **验证命令**：`cd desktop && npm ci && npm run typecheck && npx vitest run tests/static`；`uv run pytest tests/test_desktop_core_isolation.py`

### PR2：host 读路径（不依赖 Spec 2/3 施工）
- **范围**：`reach.ts`、`episodes.ts`（+ TI-5）、`tailer.ts` + `jsonl.ts` + `fold.ts`、`store.ts`、`losslessJson.ts` + 启动自检、`status.ts`、`heal.ts`（H1–H5 调度与 `h5Step`；能力缺席时为空操作）、PROBE_*/GIT_* 模板、健康数据；TE-1~TE-14、TI-3a、TI-7、TP-5；**首日在 `electron-vite dev` 下记录启动自检结果（假设 10）**，失败即按 §6.2 提出 S3-R12；§3.7 常量实测回填；验证假设 4 的首次授权部分。
- **验证命令**：`cd desktop && npx vitest run tests/host tests/shared`

### PR3：renderer、PreviewPane 与媒体协议
- **范围**：期列表、产物树、事件时间线（含有损声明、degradedNotices）、PreviewPane 五类、`ava-media://`（收紧后的守卫、Range、CSP、fd 生命周期）、出网拦截；TP-1~TP-6、TS-3~TS-5、TI-1、TI-2；验证假设 3、6。
- **验证命令**：`cd desktop && npx vitest run && npx playwright test e2e/preview`

### PR4：Approval 决策条与 ack 链路（**阻塞于 Spec 3 v0.6 PR3 施工且其 T15–T21（含 T20 ⑥）全绿、`tests/test_review.py` 零回归**）
- **范围**：决策条、Reject 表单、`approval.decide` 全流程（§4.3，含 `--id`、`--expect-*`、`gateCheck.ts` 事后核验、测试钩子）、HEAL/APPROVE/REJECT/REVIEW_APPROVE 模板、进程组超时、门禁 14 横幅、main 侧 repoRoot 对话框、临时 repo 副本夹具；TA-1~TA-12（含 TA-2b、TA-9b、TA-9c）、TI-2、TI-3b、TI-6、TI-8、TI-9、TI-10；验证假设 5、8（假设 8 须在真实外置盘上跑一次 TA-2）。
- **验证命令**：`cd desktop && npx playwright test e2e/ack`；`uv run pytest tests/test_approvals.py tests/test_desktop_core_isolation.py`

### PR5：打包、完整性与收口
- **范围**：`npm run release-build`（`npm ci` → build-info → 构建 → 打包，`mac.target: dir`）、`verify-fuses.mjs`、启动参数白名单、`e2e-packaged/`（TS-1、TS-2、TS-6、TS-7、TS-8、TS-8b）；全部变异 MUT-1~MUT-55（含 MUT-13b）实跑并回填结果；验证假设 1、2、4（重建后授权）、7；`docs/dev/plans/README.md` 状态更新。
- **验证命令**：`cd desktop && npm run release-build && node scripts/verify-fuses.mjs && npx playwright test && npx vitest run e2e-packaged`；`uv run pytest tests/test_docs_invariants.py`

---

## 9. 验收门禁清单

- [ ] **门禁 1（写不变量）**：I1——TG-2、TI-3a、TE-11、TP-5 全绿，MUT-4 被捕获；I2——TI-3b、TA-11、TE-14 全绿，MUT-34/35/40/41/48/49/50/51 被捕获；
- [ ] **门禁 2（状态单源）**：TG-7 全绿；UI 显示的当前工序、停机点、advisories 与同时刻 `python -m pipeline.status <期> --json` 一致（手验 3 个处于不同停机点的真实期——这是本门禁的真防线，TG-7 只防无意识引入）；
- [ ] **门禁 3（tail 正确性）**：TE-1~TE-10 全绿，MUT-1/2/3/19/31 被捕获；
- [ ] **门禁 4（多期隔离与 renderer 不可信）**：TE-8、TE-12、TA-4、TP-3、TI-8 全绿，MUT-5/6/24/33/42 被捕获；
- [ ] **门禁 5（ack 显式、绑定且可验证；物理闸门只为人审版本打开）**：TA-1~TA-10（含 TA-2b、TA-9b、TA-9c）全绿；MUT-7/8/9/10/18/25/26/28/38/39 被捕获；**纳秒指纹无损（红队 R3-B1）**：TE-13 全绿、TS-2 的 `lossless-json=ok` 成立，MUT-45/46/47/54 被捕获；host 侧指纹校验：TA-9 变体 3 全绿，MUT-53 被捕获；repoRoot 切换互斥：TA-12 全绿，MUT-52 被捕获；**在真实（非夹具）期上走一遍 05 打回 → 终端 `ava <期> /approvals` 可见终态且 feedback 原文一致**；
- [ ] **门禁 6（脱盘不脆弱）**：拔盘状态下启动 app → 显示 `volume-unmounted` 与 readlink 目标，无崩溃、无新建目录；播放中推出外置盘 → 系统能正常推出（不需强制推出）；插盘后 5 s 内自动恢复（真机手验，外加 TP-5、TP-6）；
- [ ] **门禁 7（崩溃恢复）**：TI-9 全绿；手验在 ack 在途时退出 app，重开后 UI 与磁盘一致；
- [ ] **门禁 8（打包加固）**：TS-1、TS-2、TS-6、TS-7、TS-8、TS-8b 全绿；MUT-14/27/30/36/43/44 被捕获；
- [ ] **门禁 9（本 spec 的 PR 对 core 零改动）**：TC-1、TC-2、TC-4 全绿；本 spec 全部 PR 合计对 `pipeline/`、`config/` 的 diff 为空；`pyproject.toml` 依赖未变；
- [ ] **门禁 10（进程与 profile 隔离）**：TI-1、TI-2、TI-4、TI-6、TI-7、TI-10 全绿，MUT-13/13b/32/37 被捕获；
- [ ] **门禁 11（Spec 2 降级 caveat）**：Spec 2 未施工期间 TE-1 以模拟写端通过；**Spec 2 施工后必须用真实 `EventPublisher` 回看 TE-1、TA-2 的事件断言转绿，本门禁才算关闭**；
- [ ] **门禁 12（依赖纯洁）**：TG-1、TG-3、TG-8、TG-9 全绿，MUT-55 被捕获；
- [ ] **门禁 13（文档门禁）**：`uv run pytest tests/test_docs_invariants.py` 全绿；
- [ ] **门禁 14（人时数据源不被静默掏空，红队 M8）**：人时记账命令（另立 spec）落地之前，03.5/05 决策条常驻「本次审阅不计人时」，人时类 advisory 旁标「数据源不完整：桌面端审阅不计入」，03.5 批准按钮旁注明「结构化打点须在终端 `/voice` 完成」（手验截图存档）；
- [ ] **门禁 15（打包版手验）**：在打包版上手验一次：打开真实期、预览五类文件各一个、在夹具期上完成一次打回（打包版无 UI 自动化，见 §7.1 说明）。

---

## 10. 潜在红旗与自纠预案

| 风险序号 | 潜在红旗 | 根因与危险性 | 预案与防御动作 |
|---|---|---|---|
| **RF-1** | UI 里长出第二份状态推导 | 与 `status.py` 分歧时 UI 与 CLI 给出不同答案 | §2.3；TG-7；门禁 2 真期对照 |
| **RF-2** | 退出码 0 被当成成功 | 实测：不支持的子命令落入 REPL 后退 0 | 能力探针 + 后置核验；Spec 3 v0.3 §4.2 第 3 条从 core 侧再堵一次；TA-6/TA-7 |
| **RF-3** | GUI 环境与终端不同 | Finder 启动的 app 没有 shell PATH、没有 LANG | §3.4 固定 PATH + 环境白名单；加模板时必须复核 |
| **RF-4** | TCC 拒绝被误报为脱盘；重建后授权失效 | 指错方向的提示会让人反复插拔盘 | `permission-denied` 独立分类，文案含「重建后可能需重新授权」；假设 4 实测；MUT-20 |
| **RF-5** | renderer 路径注入 | XSS 即可把 ack 或文件读取指向任意目录 | epKey 映射只在 host；exact-keys；媒体守卫按所选 root realpath；MUT-6/11/12 |
| **RF-6** | iframe 沙箱被打穿 | 为让 gallery 某功能可用加 `allow-same-origin` | TI-2 + MUT-13；明令禁止 |
| **RF-7** | gallery「复制锚点」在沙箱内不可用 | 不透明源 iframe 的剪贴板权限未实测（假设 3） | PR3 实测。不可用时的选项只列不选（留复审裁决）：a) iframe `allow="clipboard-write"` 委托；b) 接受 v1 降级并在 gallery 预览顶部说明；**禁止**以 `allow-same-origin` 解决 |
| **RF-8** | 事件丢失导致轨迹不完整 | Spec 2 多条丢事件路径在磁盘上无痕迹；pid 复用会误显示 running | §2.4 常驻有损声明；`noFollowupEvents`/`finishedEventMissing` 事实并列；工序不依赖事件 |
| **RF-9** | 自动 ack 偷渡 | 为少点一下在预览时自动批准，重演「自动写 approved 形同虚设」 | TG-4 + TA-5 + MUT-18；运行时注入由调试口拒启挡命令行入口 |
| **RF-10** | 加固措施被过度宣传 | 把绊线说成防护，会让人放松对真实风险的警惕 | §2.10 威胁模型逐条写明「挡住」与「只可见」的边界 |
| **RF-11** | `04-review.html` 缺失时 UI 自己去生成 | `review` 生成会写 `04-thumbs/` 与 `04-review.html`（`review.py:211`、`291`） | spawn 闭集不含 `review` 生成；缺失时显示 status 的推荐命令 |
| **RF-12** | 人时记账回归 | REPL 停留在停机点会记 `human_time.json`（`cli.py:818-846`）；桌面端审阅不产生这份记录，`k × 片长` 预算失去数据源 | **如实重写（红队 B2、R2-M8）**：桌面端不写 `human_time.json`。有效的决策**延迟**只来自三种情形：03.5/09 的显式转移；**在桌面端同一次 decide 中先跑第 ① 步再确认的 05**（artifact 对齐发生在同一次 `approve` 调用中，Spec 3 S3-R11）；02.5 同理仅当 patch 恰在本次调用前产生。其余确认只记「已确认」、延迟为空；纯终端工作流（`review --approve` 后不再确认）的 02.5/05 延迟照旧缺失。决策延迟也不等于人时（不含批准前的审阅时长）。门禁 14 让缺口在界面上可见；另立 spec 提供 core 侧人时记账命令前，桌面端不得作为 03.5/05 的主要审阅面 |
| **RF-13** | 02.5 在桌面端无法闭环 | 须先有 `02-diff.patch`，v1 不生成 | 已知缺口；须先在终端封板 |
| **RF-14** | userData 放进 `data/` | 盘没插时 app 起不来 | §2.8；TI-4 |
| **RF-15** | Electron 版本长期不升级 | 钉死保证可复现，但会失去安全更新 | 升级是独立 PR，全量跑 §7 与 MUT 矩阵 |
| **RF-16** | `events.jsonl` 长期增长 | append-only 且不进清理约定 | `SNAPSHOT_MAX_BYTES` 截头 + 分块读 |
| **RF-17** | Spec 3 v0.6 的修订在其定向复核中被推翻 | S3-R1/R2/R6/R7/R9（含 v0.6 读后二次 fstat）/R10/R11 若被改动，打回会被切碎、ack 会落到替身上、05 批准会误报失败、闸门可能为未审版本打开、ack 会死锁 | PR4 以 Spec 3 v0.6 PR3 落地且 T15–T21（含 T20 ⑥）全绿为动工前置条件；不以 UI 侧拼接或解析输出绕过 |
| **RF-18** | 含补丁段的期无法在桌面端完成 05 批准（红队 B3） | v1 删除了盲签按钮；`review.approve` 的补丁段二次确认（`review.py:325-338`）需要交互输入 | 已知缺口：UI 原样显示 stdout 中的补丁段号与提示，人在终端执行 `python -m pipeline.review <期> --approve`；回到桌面端后，自愈已把对象对齐为 `APPROVED(artifact)`，此时点「批准」只做闸 3 指纹核验与确认路径，**不再跑第 ① 步**（§2.6 表 05 行、§4.3）。补齐须 core 提供非交互的补丁段确认通道，另立修订 |
| **RF-19** | 打回原文在进程表可见（红队 M4） | `REJECT` 把 target/problem 作为 argv 传入，本机其他进程可经 `ps` 看到 | 单用户本机可接受，如实记录；若将来多用户或出网，改为经 stdin 传递并修订 Spec 3 语法 |
| **RF-21** | 开发构建与打包构建同时运行（红队 m10） | 二者 userData 不同（约），单实例锁互不可见，会出现两个 host 各自轮询与 spawn | 如实声明；跨实例的对象库安全由 Spec 3 按期 flock 兜底；开发构建常驻「DEV BUILD」水印便于辨认 |
| **RF-22** | agent 绕过 UI 直接 ack（红队 m9） | 裸形态 `/approve --id` 在非 TTY 下任何有 shell 的 agent 都能调用 | 不在本 spec 能力范围：UI 纪律不可能严于 CLI 暴露面。由 AGENTS.md「ack 永远显式」与 core 侧纪律约束；§2.10 威胁模型已写明 |
| **RF-20** | 人看的审片页与对象钉住的排片不是同一版 | `04-review.html` 由 `review` 生成时的 `04-clips.json` 渲染；之后若 `04-clips.json` 被改而未重建审片页，人看的是旧页面，而对象钉住的是新指纹 | 决策条显示确定性事实「审片页生成时间早于排片文件最后修改时间」；不自动重建（RF-11） |
| **RF-23** | 跨语言契约里的大整数被静默截断（红队 R3-B1） | Python 的 int 无上限，JS number 超过 2^53 丢精度且不报错；任何新增的纳秒/大整数字段都会重演 | §3.1 规则 8 只对 `mtime_ns` 键生效——**新增任何可能超过 2^53 的整数字段必须同步扩展规则 8 与 TE-13**；启动自检 fail-closed；出 host 协议面转字符串（规则 9） |
| **RF-24** | H5 在慢速多次写入的返工中仍会多次触发 | 稳定期只有两次采样（约 2 s）；agent 的两次写入间隔超过它时，中间版本会被当成稳定版本各触发一次 heal、新建一个 pending | 如实声明残余：每个新 pending 都会被下一个取代，不形成反馈环；但每个中间版本都是新的 approvalId，PreviewPane 会各自动呼出一次（§2.5），即仍可能抢焦点、展示半成品。若真期中频繁出现，按确定性事实（该期存在 running job）抑制 H5，另行修订 |

---

## 附：行号核实自查表

2026-09-23 对照工作树 `anime-video-agent-ava` 逐行核实：v0.1 撰写起点 HEAD `6e6f3fd`，v0.2/v0.3/v0.4 修订时 HEAD `384fc35`，两者间 `pipeline/` diff 为空；红队二轮抽查 v0.2 新增引用全部命中，唯一不符为 m1（已更正）；npm 包对照 `npm pack` 下载的发布包。红队一轮独立复核了 v0.1 的 60 余处代码行号与 16 处 `electron.d.ts` 行号，全部命中；不符项为 m1、m2、M3（fuse 枚举截断），已在下表更正。

| 引用 | 核实结果 |
|---|---|
| `pipeline/jobs.py` / `pipeline/approvals.py` | ✗ **均不存在**（Spec 2/3 未施工）；`import pipeline.approvals` 抛 ModuleNotFoundError |
| `cli.py:30 HUMAN_STOPS` | ✓ `{"02.5", "03.5", "05", "09"}` |
| `cli.py:36-60 check_code_freeze` / `43` | ✓ `["git", "status", "--porcelain", "--", "pipeline/"]` |
| `cli.py:66` | ✓ <0.1 分钟过滤只对 `stop == "scout"` |
| `cli.py:122-158 get_episodes_list` / `135` / `144` | ✓ `135` `if not d.is_dir()`（跟随符号链接，v0.2 补核）；`144` 子期展平 |
| `cli.py:818-846 run_repl` / `845-846` | ✓ `finally: close_stop()` |
| `cli.py:872` | ✓ `_run_repl_body`（def 864-870）函数体首行 `check_code_freeze()` |
| `cli.py:892` / `898` / `899-902` | ✓ 每轮 `inspect_episode`；`input(...)`；EOF → `return 0` |
| `cli.py:991` / `995` | ✓ `/chat`、`/script` 为 REPL 内部命令（v0.2 补核） |
| `cli.py:1079-1099 resolve_episode_target` / `1081-1083` / `1095` | ✓ 绝对路径存在即返回；`1095` `ep_root.rglob(target)`（红队实测 3.12.13 下绝对路径抛 NotImplementedError） |
| `cli.py:1169-1199` 裸形态分发 | ✓ `1170` join；`1174-1186` /run；`1199` `return run_repl(ep_dir)` |
| §2.6 落入 REPL 实测 | ✓ 退出 0，生成 `[{"stop": "02.5", …, "minutes": 0.0}]`（红队独立重跑一致） |
| `status.py:19-30` / `447` / `501-502` | ✓；全文无写操作（红队 grep） |
| `review.py:211` / `275` / `291-296` | ✓ 缩略图 mkdir；相对路径；无 `<script>` |
| `review.py:300-340 approve` | ✓ `318-323` 段级校验；`325-338` 补丁段确认闸（`335` `print(prompt)` 到 stdout、`336` `input("")`）；`340` `shutil.copy2` |
| `review.py:350` | ✓ `paths.require_data()` |
| `shutil.copy2` 保留纳秒 mtime | ✓ APFS 实测：源与副本 `st_mtime_ns` 相等，Node `statSync(bigint)` 读出 `mtimeNs` 相等（v0.2 补核——**只比了 stat 对 stat**，没走对象库通路，红队 R3-B1 指出；v0.4 的通路比对见下一行；外置盘为假设 8） |
| 经 JSON 对象库通路的纳秒精度 | ✓ v0.4 补核（Node v26.10.0）：Python `os.utime(ns=1790171112636927676)` 后 `json` 写出，普通 `JSON.parse` 得 `1790171112636927700`（`isSafeInteger` 为 false，与 `statSync(bigint).mtimeNs` 不等）；§3.1 规则 8 的 reviver 得 `1790171112636927676n`，与 stat 相等。Electron 44 运行时是否支持为假设 10 |
| 原地覆盖写的 fstat/read 窗口（Spec 3 v0.6 §4.5） | ✓ v0.4 补核：打开并 fstat 后，经另一路径 `write_text` 同长度覆写，再 read → inode 相同、读到 F2、`len(bytes) == size` 仍成立，读后 fstat 的 mtime_ns 已变——同长度覆写只有读后二次 fstat 的 mtime 比较能拦下 |
| `render.py:1068` / `align.py:210-235` / `status.py:116-117` | ✓ 渲染硬闸只调 `has_clips_approved_diff`；该函数只比 `segments`（或整体），不看 mtime；status 的 advisory 同源（v0.4 补核，R3-M1） |
| `clips.py:1242` / `1289` | ✓ 两处落盘均为 `paths.atomic_write`（`os.replace`，换 inode）：流水线自身的写法不会进入 R3-M1 的原地覆写窗口（v0.4 补核） |
| `tools.py:30-40` / `188-205` / `308` / `698` / `739-745` | ✓ 含 `review`；`--force` 拒收；READ_DENY_PARTS；`run_pipeline`；`Popen` 未传 stdin、未改进程组、`cwd=paths.ROOT` |
| `paths.py:20-21` / `24` / `118-128`（`126`、`128`）/ `131-158`（`137-138`） | ✓ |
| `shots.py:37-38` / `458` / `481` / `496` / `523` | ✓ |
| `tts.py:2024` | ✓ `seg-{seg.index:02d}.wav` |
| `status_card.py:282` / `301` | ✓ `log_approval_decision`；`_agent` mkdir 先例 |
| `pyproject.toml:54 / 74 / 85`；`.gitignore:2` | ✓ |
| TC-1 期望值；status spawn 与 stat 耗时；`data` 悬空 | ✓ `[]`；0.03–0.04 s、≈1 µs；撰写时悬空 |
| npm 版本（2026-09-23 `npm view`） | ✓ electron 44.4.5、react/react-dom 19.3.0、vite 7.3.6（latest 8.3.0 未采用）、electron-vite 5.0.0、@vitejs/plugin-react 5.2.0、typescript 5.9.3（latest 7.0.2 未采用）、vitest 5.0.1、@playwright/test 1.63.0、electron-builder 26.15.3、@electron/fuses 2.1.3、markdown-it 15.0.2 |
| `electron.d.ts`（electron@44.4.5） | ✓ `4134`、`9812`、`11593`、`11734`、`15844`、`18947`、`21994`、`22028`、`22061`、`23488-23516` |
| `app-builder-lib@26.15.3` `out/platformPackager.js` | ✓ `225-227` asarIntegrity；`258` 注释；`259-265` `doAddElectronFuses`；`266-298` `generateFuseConfig`（`return config` 在 297、右括号 298，v0.3 按红队 m1 更正），**不含 `WasmTrapHandlers`、`strictlyRequireAllFuses`**（grep 为空，v0.2 补核） |
| `app-builder-lib@26.15.3` `out/electron/electronMac.js:182-193` | ✓ 写 `ElectronAsarIntegrity` |
| `app-builder-lib@26.15.3` `out/macPackager.js:295-297` + `out/mac/MacTargetHelper.js:13-19` | ✓ `identity === null` → `handleNullIdentity()`：记日志「skipped macOS code signing」并返回，不查 keychain（v0.2 补核） |
| `app-builder-lib@26.15.3` `out/publish/PublishManager.js:46-60` / `365` | ✓ `publish` 未设时按 npm `release`、CI tag、CI 环境隐式发布；有 `GH_TOKEN`/`GITHUB_TOKEN` 时选 github（v0.2 补核） |
| `app-builder-lib@26.15.3` `out/codeSign/macCodeSign.js:227` | ✓ `if (qualifier != null && !line.includes(qualifier))`：身份按子串匹配（v0.3 补核，决定不用 `identity: "-"`） |
| `tools.py:126-151 _extract_positional_args` / `134-137` | ✓ `valued_flags` 不含 `--expect-size`/`--expect-mtime-ns`；带 `=` 的旗标整体跳过（v0.3 补核，决定 `REVIEW_APPROVE` 用等号形式） |
| `review.py:316` | ✓ `data = json.loads(src.read_text(...))`：现状按路径读、`340` 按路径 `copy2`，两次打开之间存在 R2-B1 所述窗口（v0.3 补核） |
| 同进程二次 flock / `threading.Lock` 重入 | ✓ 本机实测：第二个 fd `LOCK_EX\|LOCK_NB` → EWOULDBLOCK；`acquire(blocking=False)` → False（v0.3 补核，R2-M7） |
| `@electron/fuses@2.1.3` `dist/config.d.ts:7-17` | ✓ `FuseV1Options` 共 9 位，第 9 位 `WasmTrapHandlers = 8` 在第 16 行（v0.1 引「8-15」漏第 9 位，红队 M3 更正） |
| Electron 完整性校验失败时终止进程 | 约（Electron 语义；TS-1 证实） |
