// WebGL2 / 3D 确定性辅助库，由 tools/motion_render.py 在 runtime.js 之后注入。
// 不使用 WebGL 的 2D/SVG 场景零开销；使用 WebGL2 时通过 window.motionGL 初始化上下文，
// 渲染器会在每帧截图前自动检查上下文丢失并调用 gl.finish()，保证 --verify-repro 逐帧一致。

(() => {
  const contexts = [];
  window.__MOTION_GL_CONTEXTS__ = contexts;

  function registerContext(gl) {
    if (gl && !contexts.includes(gl)) {
      contexts.push(gl);
    }
    return gl;
  }

  // 兜底拦截原生 canvas.getContext('webgl2' / 'webgl')：即使场景页未走 motionGL.createContext，
  // 也能自动补齐 preserveDrawingBuffer: true 并纳入每帧上下文丢失检测与 gl.finish() 同步。
  if (typeof HTMLCanvasElement !== 'undefined' && HTMLCanvasElement.prototype) {
    const origGetContext = HTMLCanvasElement.prototype.getContext;
    if (typeof origGetContext === 'function' && !origGetContext.__motionWrapped) {
      const wrapped = function getContext(type, opts) {
        if (type === 'webgl2' || type === 'webgl' || type === 'experimental-webgl') {
          const merged = Object.assign({ preserveDrawingBuffer: true }, opts || {});
          return registerContext(origGetContext.call(this, type, merged));
        }
        return origGetContext.call(this, type, opts);
      };
      wrapped.__motionWrapped = true;
      HTMLCanvasElement.prototype.getContext = wrapped;
    }
  }

  function createContext(canvas, opts = {}) {
    if (!canvas || typeof canvas.getContext !== 'function') {
      throw new Error('motionGL.createContext: 需要传入有效 <canvas> 元素');
    }
    const attrs = Object.assign(
      {
        preserveDrawingBuffer: true,
        antialias: true,
        alpha: false,
        depth: true,
        powerPreference: 'high-performance',
      },
      opts,
    );
    const gl = canvas.getContext('webgl2', attrs);
    if (!gl) {
      throw new Error('motionGL.createContext: 无法创建 WebGL2 上下文');
    }
    return registerContext(gl);
  }

  function compileShader(gl, type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(shader) || 'unknown shader error';
      const label = type === gl.VERTEX_SHADER ? 'VERTEX' : 'FRAGMENT';
      gl.deleteShader(shader);
      throw new Error(`motionGL ${label} shader 编译失败: ${log.trim()}`);
    }
    return shader;
  }

  function createProgram(gl, vertSrc, fragSrc) {
    const vs = compileShader(gl, gl.VERTEX_SHADER, vertSrc);
    let fs = null;
    let program = null;
    try {
      fs = compileShader(gl, gl.FRAGMENT_SHADER, fragSrc);
      program = gl.createProgram();
      gl.attachShader(program, vs);
      gl.attachShader(program, fs);
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        const log = gl.getProgramInfoLog(program) || 'unknown link error';
        gl.deleteProgram(program);
        program = null;
        throw new Error(`motionGL program 链接失败: ${log.trim()}`);
      }
      return program;
    } finally {
      gl.deleteShader(vs);
      if (fs) gl.deleteShader(fs);
    }
  }

  // 全屏四边形（用于片段着色器、光影波场、背景体积光、后处理等）。
  function createFullscreenQuad(gl, program, attribName = 'a_pos') {
    const loc = gl.getAttribLocation(program, attribName);
    if (loc < 0) {
      throw new Error(`motionGL.createFullscreenQuad: 找不到顶点属性 ${attribName}`);
    }
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    const buf = gl.createBuffer();
    try {
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(
        gl.ARRAY_BUFFER,
        new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
        gl.STATIC_DRAW,
      );
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    } catch (err) {
      gl.bindVertexArray(null);
      gl.deleteBuffer(buf);
      gl.deleteVertexArray(vao);
      throw err;
    }
    gl.bindVertexArray(null);
    return {
      vao,
      buffer: buf,
      draw() {
        gl.bindVertexArray(vao);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        gl.bindVertexArray(null);
      },
    };
  }

  // ---- 列主序 4x4 矩阵（标准 WebGL/OpenGL 约定，纯函数无副作用）----
  const mat4 = {
    identity() {
      const m = new Float32Array(16);
      m[0] = m[5] = m[10] = m[15] = 1;
      return m;
    },

    multiply(a, b) {
      const out = new Float32Array(16);
      for (let col = 0; col < 4; col++) {
        const b0 = b[col * 4];
        const b1 = b[col * 4 + 1];
        const b2 = b[col * 4 + 2];
        const b3 = b[col * 4 + 3];
        for (let row = 0; row < 4; row++) {
          out[col * 4 + row] =
            a[row] * b0 + a[4 + row] * b1 + a[8 + row] * b2 + a[12 + row] * b3;
        }
      }
      return out;
    },

    perspective(fovyRad, aspect, near, far) {
      if (
        !Number.isFinite(fovyRad) ||
        !Number.isFinite(aspect) ||
        !Number.isFinite(near) ||
        !Number.isFinite(far) ||
        fovyRad <= 0 ||
        fovyRad >= Math.PI ||
        aspect <= 0 ||
        near <= 0 ||
        far <= near
      ) {
        throw new RangeError(
          `motionGL.mat4.perspective: 参数无效 (fovyRad=${fovyRad}, aspect=${aspect}, near=${near}, far=${far})`,
        );
      }
      const f = 1.0 / Math.tan(fovyRad / 2);
      const nf = 1 / (near - far);
      const m = new Float32Array(16);
      m[0] = f / aspect;
      m[5] = f;
      m[10] = (far + near) * nf;
      m[11] = -1;
      m[14] = 2 * far * near * nf;
      return m;
    },

    lookAt(eye, target, up = [0, 1, 0]) {
      let zx = eye[0] - target[0];
      let zy = eye[1] - target[1];
      let zz = eye[2] - target[2];
      const zl = Math.hypot(zx, zy, zz);
      if (zl < 1e-8) {
        throw new RangeError('motionGL.mat4.lookAt: eye 与 target 不能重合');
      }
      zx /= zl; zy /= zl; zz /= zl;

      let ux = up[0];
      let uy = up[1];
      let uz = up[2];
      let xx = uy * zz - uz * zy;
      let xy = uz * zx - ux * zz;
      let xz = ux * zy - uy * zx;
      let xl = Math.hypot(xx, xy, xz);
      if (xl < 1e-6) {
        // up 与视线方向共线（如正俯视 [0,5,0]->[0,0,0] 配默认 [0,1,0]）：自动切到正交备用上轴
        if (Math.abs(zy) < 0.9) {
          ux = 0; uy = 1; uz = 0;
        } else {
          ux = 0; uy = 0; uz = 1;
        }
        xx = uy * zz - uz * zy;
        xy = uz * zx - ux * zz;
        xz = ux * zy - uy * zx;
        xl = Math.hypot(xx, xy, xz);
      }
      xx /= xl; xy /= xl; xz /= xl;

      const yx = zy * xz - zz * xy;
      const yy = zz * xx - zx * xz;
      const yz = zx * xy - zy * xx;

      const m = new Float32Array(16);
      m[0] = xx; m[1] = yx; m[2] = zx; m[3] = 0;
      m[4] = xy; m[5] = yy; m[6] = zy; m[7] = 0;
      m[8] = xz; m[9] = yz; m[10] = zz; m[11] = 0;
      m[12] = -(xx * eye[0] + xy * eye[1] + xz * eye[2]);
      m[13] = -(yx * eye[0] + yy * eye[1] + yz * eye[2]);
      m[14] = -(zx * eye[0] + zy * eye[1] + zz * eye[2]);
      m[15] = 1;
      return m;
    },

    translate(x, y, z) {
      const m = mat4.identity();
      m[12] = x; m[13] = y; m[14] = z;
      return m;
    },

    scale(x, y, z) {
      const m = mat4.identity();
      m[0] = x; m[5] = y; m[10] = z;
      return m;
    },

    rotateX(rad) {
      const m = mat4.identity();
      const c = Math.cos(rad);
      const s = Math.sin(rad);
      m[5] = c; m[6] = s; m[9] = -s; m[10] = c;
      return m;
    },

    rotateY(rad) {
      const m = mat4.identity();
      const c = Math.cos(rad);
      const s = Math.sin(rad);
      m[0] = c; m[2] = -s; m[8] = s; m[10] = c;
      return m;
    },

    rotateZ(rad) {
      const m = mat4.identity();
      const c = Math.cos(rad);
      const s = Math.sin(rad);
      m[0] = c; m[1] = s; m[4] = -s; m[5] = c;
      return m;
    },
  };

  window.motionGL = {
    createContext,
    compileShader,
    createProgram,
    createFullscreenQuad,
    mat4,
  };
})();
