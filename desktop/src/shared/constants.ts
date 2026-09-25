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
