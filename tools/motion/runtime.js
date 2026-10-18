// 动效场景页的共用运行时，由 tools/motion_render.py 在页面脚本之前注入（add_init_script）。
// 场景页不用 <script src> 引它：场景页放在 data/ 下（外置盘符号链接），相对路径回不到仓库。
//
// 约定：场景页定义全局 renderFrame(t)，t 是秒，由渲染器逐帧传入；画面只能是 t 的函数。
// 所以这里只给纯函数，外加一个带种子的随机数——渲染器会让 Math.random 一调用就抛错。

(() => {
  const clamp = (x, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, x));
  const lerp = (a, b, k) => a + (b - a) * k;
  const easeInOut = (k) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);
  const easeOut = (k) => 1 - Math.pow(1 - k, 3);

  // t 在 [t0, t1] 内的进度，缓动后返回 0→1；区间外钳住。t1 <= t0 时视为瞬时切换。
  const prog = (t, t0, t1, ease = easeInOut) =>
    t1 <= t0 ? (t >= t0 ? 1 : 0) : ease(clamp((t - t0) / (t1 - t0)));

  // mulberry32：同一种子同一序列。需要「随机」点缀时用它，不用 Math.random。
  const rng = (seed) => {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let r = Math.imul(a ^ (a >>> 15), 1 | a);
      r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  };

  window.motion = { clamp, lerp, easeInOut, easeOut, prog, rng };
})();
