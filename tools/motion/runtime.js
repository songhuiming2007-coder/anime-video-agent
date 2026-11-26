// 动效场景页的共用运行时，由 tools/motion_render.py 在页面脚本之前注入（add_init_script）。
// 场景页不用 <script src> 引它：场景页放在 data/ 下（外置盘符号链接），相对路径回不到仓库。
//
// 约定：场景页定义全局 renderFrame(t)，t 是秒，由渲染器逐帧传入；画面只能是 t 的函数。
// 本文件只提供与风格无关的确定性纯函数与离屏材质辅助（R1 机制与内容分离），
// 不写死任何具体配色、版式或装饰皮肤；随机数只走带种子的 mulberry32。

(() => {
  const clamp = (x, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, x));
  const lerp = (a, b, k) => a + (b - a) * k;
  const remap = (x, inLo, inHi, outLo, outHi, doClamp = true) => {
    if (inHi === inLo) return outLo;
    const u = (x - inLo) / (inHi - inLo);
    return lerp(outLo, outHi, doClamp ? clamp(u) : u);
  };

  // ---- 缓动函数 ----
  const easeIn = (k) => {
    const x = clamp(k);
    return x * x * x;
  };
  const easeOut = (k) => 1 - Math.pow(1 - clamp(k), 3);
  const easeInOut = (k) => {
    const x = clamp(k);
    return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
  };
  const easeOutExpo = (k) => {
    const x = clamp(k);
    return x >= 1 ? 1 : 1 - Math.pow(2, -10 * x);
  };
  const easeInOutQuint = (k) => {
    const x = clamp(k);
    return x < 0.5 ? 16 * x * x * x * x * x : 1 - Math.pow(-2 * x + 2, 5) / 2;
  };
  const easeOutBack = (k, s = 1.55) => {
    const x = clamp(k) - 1;
    return 1 + (s + 1) * x * x * x + s * x * x;
  };
  const smoothstep = (edge0, edge1, x) => {
    if (edge1 === edge0) return x >= edge1 ? 1 : 0;
    const u = clamp((x - edge0) / (edge1 - edge0));
    return u * u * (3 - 2 * u);
  };

  // t 在 [t0, t1] 内的进度，缓动后返回 0→1；区间外钳住。t1 <= t0 时视为瞬时切换。
  const prog = (t, t0, t1, ease = easeInOut) =>
    t1 <= t0 ? (t >= t0 ? 1 : 0) : ease(clamp((t - t0) / (t1 - t0)));

  // 序列错峰进度：第 i 项在 [start + i * step, start + i * step + dur] 内从 0 到 1。
  const stagger = (t, i, start, step, dur, ease = easeOut) =>
    prog(t, start + i * step, start + i * step + dur, ease);

  // 解析解阻尼弹簧（无状态，100% 只依赖 t - t0；t <= t0 返回 0，稳态趋近 1）。
  // 默认欠阻尼（zeta < 1）带轻微物理过冲，调大 damping 可平滑过渡到临界/过阻尼。
  const spring = (t, t0, opts = {}) => {
    const stiffness = opts.stiffness ?? 180;
    const damping = opts.damping ?? 16;
    const mass = opts.mass ?? 1;
    if (
      !Number.isFinite(stiffness) ||
      !Number.isFinite(damping) ||
      !Number.isFinite(mass) ||
      stiffness <= 0 ||
      mass <= 0 ||
      damping < 0
    ) {
      throw new RangeError(
        `motion.spring: 参数无效（要求 stiffness > 0, mass > 0, damping >= 0，实际 stiffness=${stiffness}, mass=${mass}, damping=${damping}）`,
      );
    }
    const dt = t - t0;
    if (dt <= 0) return 0;
    const w0 = Math.sqrt(stiffness / mass);
    const zeta = damping / (2 * Math.sqrt(stiffness * mass));
    if (zeta < 1 - 1e-6) {
      const wd = w0 * Math.sqrt(1 - zeta * zeta);
      const decay = Math.exp(-zeta * w0 * dt);
      return 1 - decay * (Math.cos(wd * dt) + ((zeta * w0) / wd) * Math.sin(wd * dt));
    }
    if (zeta > 1 + 1e-6) {
      const s = Math.sqrt(zeta * zeta - 1);
      const r1 = -w0 * (zeta - s);
      const r2 = -w0 * (zeta + s);
      const c1 = r2 / (r2 - r1);
      const c2 = -r1 / (r2 - r1);
      return 1 - (c1 * Math.exp(r1 * dt) + c2 * Math.exp(r2 * dt));
    }
    const decay = Math.exp(-w0 * dt);
    return 1 - decay * (1 + w0 * dt);
  };

  // 衰减脉冲（用于撞击微震或节拍回弹，t <= t0 为 0）。
  const impulse = (t, t0, freq = 14, decay = 10) => {
    const dt = t - t0;
    if (dt <= 0) return 0;
    return Math.exp(-decay * dt) * Math.sin(2 * Math.PI * freq * dt);
  };

  // mulberry32：同一种子同一序列。需要「随机」点缀或纹理时用它，不用 Math.random。
  const rng = (seed) => {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let r = Math.imul(a ^ (a >>> 15), 1 | a);
      r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  };

  // 2D 镜头变换：在 ctx.save()/restore() 内调用，围绕画面中心 (cx, cy) 做推拉摇移。
  const camera2d = (ctx, opts = {}) => {
    const x = opts.x ?? 0;
    const y = opts.y ?? 0;
    const zoom = opts.zoom ?? 1;
    const rot = opts.rot ?? 0;
    const cx = opts.cx ?? (ctx.canvas ? ctx.canvas.width / 2 : 960);
    const cy = opts.cy ?? (ctx.canvas ? ctx.canvas.height / 2 : 540);
    ctx.translate(cx, cy);
    if (zoom !== 1) ctx.scale(zoom, zoom);
    if (rot !== 0) ctx.rotate(rot);
    ctx.translate(-cx - x, -cy - y);
  };

  // 离屏确定性细颗粒平铺纹理（仅在初始化调用一次，每帧 fillRect 叠加，零每帧噪声开销）。
  // 可用于暗调胶片颗粒（fibers=0、低 alpha/opacity）或浅/暗底纤维纹（fibers>0 + fiberColor），不绑定任何配色。
  const makeGrainPattern = (ctx, seed = 1, opts = {}) => {
    const size = Math.max(16, Math.round(opts.size ?? 256));
    const maxAlpha =
      opts.alpha != null
        ? clamp(opts.alpha, 0, 255)
        : opts.opacity != null
          ? Math.round(clamp(opts.opacity, 0, 1) * 255)
          : 18;
    const fibers = Math.max(0, Math.round(opts.fibers ?? 0));
    const fiberAlpha = clamp(opts.fiberAlpha ?? 0.04, 0, 1);
    const tile = document.createElement('canvas');
    tile.width = size;
    tile.height = size;
    const g = tile.getContext('2d');
    const img = g.createImageData(size, size);
    const d = img.data;
    const rand = rng(seed);
    for (let i = 0; i < d.length; i += 4) {
      const v = rand();
      const tone = v > 0.5 ? 255 : 18;
      const a = Math.floor(Math.pow(Math.abs(v - 0.5) * 2, 1.6) * maxAlpha);
      d[i] = tone;
      d[i + 1] = tone;
      d[i + 2] = tone;
      d[i + 3] = a;
    }
    g.putImageData(img, 0, 0);
    if (fibers > 0) {
      g.strokeStyle = opts.fiberColor || `rgba(24, 24, 28, ${fiberAlpha})`;
      g.lineWidth = 0.8;
      for (let k = 0; k < fibers; k++) {
        const x = rand() * size;
        const y = rand() * size;
        const ang = rand() * Math.PI * 2;
        const len = 5 + rand() * 14;
        g.beginPath();
        g.moveTo(x, y);
        g.lineTo(x + Math.cos(ang) * len, y + Math.sin(ang) * len);
        g.stroke();
      }
    }
    return ctx.createPattern(tile, 'repeat');
  };

  window.motion = {
    clamp,
    lerp,
    remap,
    easeIn,
    easeOut,
    easeInOut,
    easeOutExpo,
    easeInOutQuint,
    easeOutBack,
    smoothstep,
    prog,
    stagger,
    spring,
    impulse,
    rng,
    camera2d,
    makeGrainPattern,
  };
})();
