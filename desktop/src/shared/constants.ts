// Spec 8 §3.7 常量表；「为什么是这个数」见 spec 同名行。
// 2026-09-24 PR2/PR3 对真实 data/（T7 外置盘，21 个期目录）实测后回填，初值全部成立、未改数：
//   status --json spawn 0.03–0.07 s（3 期 × 3 次，warm）；events.jsonl stat 1.5 µs/次；
//   最大文本产物 171 KB（07-cover/pool.json），04-clips.json 最大 68 KB；最大 events.jsonl 441 B。

/** 活跃期 events.jsonl stat 周期；事件为生命周期粒度，审批决策以分钟计；stat 实测 1.5 µs。 */
export const ACTIVE_POLL_MS = 1000;
/** 全部可见期的 events.jsonl stat 周期；后台期只需刷新徽标。 */
export const BACKGROUND_POLL_MS = 5000;
/** data/ 可达性诊断周期；插拔盘是人手动作。 */
export const REACH_POLL_MS = 2000;
/** 活跃期 status --json 周期（人手改文件不产生事件）；同时是 H2 的采样周期。 */
export const STATUS_REFRESH_MS = 5000;
/** 期列表刷新周期（每期一次 status spawn，并发 2）；实测约 25 期 × 0.04 s ≈ 1 s CPU / 30 s。 */
export const EPISODE_LIST_REFRESH_MS = 30_000;
/** 期列表 status 并发上限。 */
export const EPISODE_STATUS_CONCURRENCY = 2;

/** Spec 2 单行上界约 32 KiB（两段 4096 字符尾部 × 4 字节）；1 MiB 约 30 倍余量。 */
export const MAX_PARTIAL_BYTES = 1024 * 1024;
/** tail 与 snapshot 的单次读上限；块间让出事件循环。 */
export const READ_CHUNK_BYTES = 4 * 1024 * 1024;
/** snapshot 首载上限，约 1000 条满尾部 job_finished；超出只载尾部并标 truncatedHead。 */
export const SNAPSHOT_MAX_BYTES = 32 * 1024 * 1024;
/** sort_keys 下行尾 64 字节落在微秒级 timestamp 与 type，足以区分原行与截断后重写的新行。 */
export const ANCHOR_BYTES = 64;

/** created→started 正常在毫秒级；10 s 是三个数量级余量，只用于显示「未见后续事件」。 */
export const PENDING_STALE_MS = 10_000;
/** 子进程退出与 sidecar 刷盘之间有瞬时窗口；连续两个 tick 仍成立才显示。 */
export const FINISHED_MISSING_TICKS = 2;

/** spawn 超时：探针/status 10 s；ack 路径 30 s（锁持有毫秒级）；review --approve 在外置盘多给一倍。 */
export const SPAWN_TIMEOUT_SHORT_MS = 10_000;
export const SPAWN_TIMEOUT_ACK_MS = 30_000;
export const SPAWN_TIMEOUT_REVIEW_MS = 60_000;
/** 超时先 SIGTERM 进程组，宽限后 SIGKILL。 */
export const SPAWN_KILL_GRACE_MS = 5000;
/** stdout/stderr 各保留的尾部字节数。 */
export const SPAWN_TAIL_BYTES = 8 * 1024;
/** status --json 的 stdout 完整读入上限（逐块累积，超出按错误处理）：2026-09-25 对真实 data 21 期实测最大 1130 B，约 58 倍余量（S22，经用户同意）。 */
export const STATUS_STDOUT_MAX_BYTES = 64 * 1024;
/**
 * spawn 日志环形缓冲条数（S22，经用户同意）：TA-11 全场景不到 100 条；常态约 3700 条/小时
 * （活跃期每 5 s 一次 status + 期列表每 30 s 约 25 次），1000 条约为最近 15 分钟，诊断够用、内存有界。
 */
export const SPAWN_LOG_MAX = 1000;

/** 期内最大文本产物实测 171 KB（pool.json），5 MiB 约 30 倍余量；超出只显示前 5 MiB。 */
export const TEXT_PREVIEW_MAX_BYTES = 5 * 1024 * 1024;

/**
 * host 崩溃重启看护（§2.9，§3.7「host 重启退避」行）：1 s 起翻倍、封顶 30 s；60 s 内崩溃 5 次熔断。
 * 崩溃循环时不刷屏、不空转，同时给偶发崩溃快速恢复。连续计数在 host 存活满一个熔断窗口后清零。
 */
export const HOST_RESTART_BASE_MS = 1000;
export const HOST_RESTART_MAX_MS = 30_000;
export const HOST_CRASH_WINDOW_MS = 60_000;
export const HOST_CRASH_LIMIT = 5;
/** 致命面板里 host stderr 的尾部字节数（与 spawn 尾部同量级） */
export const HOST_STDERR_TAIL_BYTES = 8 * 1024;

/**
 * ava-media:// 响应体的单次读取块（S23 门禁 6 修订，经用户同意）：响应体按块拉取，每块「打开 → 读 → 关闭」，
 * <video> 缓冲满后暂停拉取时不持有任何 fd，播放中外置盘可正常推出。HTTP 语义不变（Content-Range 照实到请求末尾）。
 * 取 1 MiB：外置盘上单次读取为毫秒级；8 Mbps 视频约每秒一次打开/关闭；每条在途流的内存约为 2 块。
 */
export const MEDIA_READ_CHUNK_BYTES = 1024 * 1024;

// ---------------- Spec 11 §3.5 / Spec 12 §3.1：停机点组件常量（全部为初值，spec 各 PR 实测回填） ----------------

/** 真实 02-script.md 为 KB 级（约 20 段 × 百字）；1 MiB 是两个数量级余量（Spec 11 §3.5）。 */
export const SAVE_SCRIPT_MAX_BYTES = 1024 * 1024;
/** 纠错原文是一行文法，64 KiB 已远超任何合法输入（Spec 11 §3.5）。 */
export const VOICE_PARSE_MAX_BYTES = 64 * 1024;
/** markdown-it 渲染 KB 级文本为亚毫秒；300 ms 节流在人感知上即时（Spec 11 §3.5）。 */
export const EDITOR_PREVIEW_DEBOUNCE_MS = 300;
/** host 与 core 同机同时钟，容差只为防取整边界（Spec 11 §3.5）。 */
export const RECORD_TIME_CLOCK_TOLERANCE_S = 60;
/** 封面图上限：真实 1–5 MiB，32 MiB 是防御性上界（Spec 12 §3.1；core 侧同值）。 */
export const IMPORT_COVER_MAX_BYTES = 32 * 1024 * 1024;
/** `/voice-info` stdout 完整读入上限：段表 + 待应用纠错条目（每条约 200 B）逐字节完整，超出按错误处理。 */
export const VOICE_INFO_STDOUT_MAX_BYTES = 1024 * 1024;

// ---------------- Spec 10 §3.5：会话常量（全部为初值，PR2/PR4 实测回填） ----------------

/** Spec 9 MAX_INBOUND_LINE_BYTES：入站只有人打的字与答复；超出即 E_BAD_REQUEST、零写入。 */
export const MAX_INBOUND_LINE_BYTES = 1_048_576;
/** 最大合法帧估计为 request.fields.args 里的整份稿件与长回复；8 MiB 约是 read_artifact 上限 200 000 字节的 40 倍（A6）。 */
export const SESSION_FRAME_MAX_BYTES = 8 * 1024 * 1024;
/** 超过即截头并标出；未关闭请求单独保存不受影响（§3.5）。 */
export const CONV_BUFFER_MAX_BYTES = 16 * 1024 * 1024;
/** spawn → ready；import 实测 0.06 s，恢复大会话历史与 ensure_pending 未测，留两个数量级余量（A5）。 */
export const SESSION_READY_TIMEOUT_MS = 30_000;
/** 装配前写 turn_start，turn_started 帧的发出时刻估计为毫秒级（约，PR4 实测）；超时 → E_TIMEOUT「结果未知」。 */
export const SEND_ACK_TIMEOUT_MS = 10_000;
export const ANSWER_ACK_TIMEOUT_MS = 10_000;
/** 人手写的改法说明；远小于 1 MiB 帧上限。 */
export const FEEDBACK_MAX_BYTES = 65_536;
/** 收尾最长一次模型请求（REQUEST_TIMEOUT = 60 s）+ job 杀组 + 余量。 */
export const SESSION_END_WAIT_MS = 90_000;
/** 与 Spec 8 SIGTERM→SIGKILL 一致，高于 Spec 9 FRAME_DRAIN_TIMEOUT_S 2 s。 */
export const SESSION_KILL_GRACE_MS = 5_000;
/** host 在线时 quit-state 是内存查询，毫秒级；2 s 内不答视同 host 不可用（🟡-3）。 */
export const QUIT_QUERY_TIMEOUT_MS = 2_000;
/** 退出第 5 步兜底：SESSION_KILL_GRACE_MS + 进程组清理 + 余量。 */
export const QUIT_STOP_TIMEOUT_MS = 10_000;
/** 结算要等的只有两帧（毫秒级）与一次活跃期读取（ACTIVE_POLL_MS 1 s）；10 倍余量（§2.7 第 6 条）。 */
export const SETTLE_TIMEOUT_MS = 10_000;
/** 原生确认框里模型自由文本的显示上限；确定性字段不受此限、全文显示（三轮 🔵-4）。 */
export const CONFIRM_FREE_TEXT_MAX_CHARS = 2_000;
/** 合并 log 洪峰（A8）；人的感知阈值约 100 ms。 */
export const CONV_PUSH_COALESCE_MS = 50;
/** 每个工具行下展示的作业输出行数上限（只影响显示）。 */
export const LOG_TAIL_LINES = 200;
/** 固定服务名；账户名 = 变量名（§2.9）。 */
export const KEYCHAIN_SERVICE = "ava";
/** 排除一切改变子进程行为的名字（PATH、PYTHON*、DYLD_*、NODE_OPTIONS…）。 */
export const KEY_ENV_NAME_RE = /^[A-Z][A-Z0-9_]*_(API_KEY|KEY|TOKEN)$/;
