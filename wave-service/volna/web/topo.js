// Топографические линии на фоне: изолинии медленно текущего шумового поля,
// растянутого вдоль волны. Громкость музыки ускоряет течение и поднимает рельеф.
(() => {
  "use strict";

  // --- 3D simplex noise (алгоритм Стефана Густавсона) ---
  const GRAD = new Float32Array([
    1, 1, 0, -1, 1, 0, 1, -1, 0, -1, -1, 0,
    1, 0, 1, -1, 0, 1, 1, 0, -1, -1, 0, -1,
    0, 1, 1, 0, -1, 1, 0, 1, -1, 0, -1, -1,
  ]);
  const perm = new Uint8Array(512);
  const permMod12 = new Uint8Array(512);
  (function seed(s) {
    const p = new Uint8Array(256);
    for (let i = 0; i < 256; i++) p[i] = i;
    for (let i = 255; i > 0; i--) {
      s = (s * 1664525 + 1013904223) >>> 0;
      const j = s % (i + 1);
      [p[i], p[j]] = [p[j], p[i]];
    }
    for (let i = 0; i < 512; i++) {
      perm[i] = p[i & 255];
      permMod12[i] = perm[i] % 12;
    }
  })(20261031);

  const F3 = 1 / 3;
  const G3 = 1 / 6;

  function corner(gi, x, y, z) {
    let t = 0.6 - x * x - y * y - z * z;
    if (t < 0) return 0;
    t *= t;
    return t * t * (GRAD[gi] * x + GRAD[gi + 1] * y + GRAD[gi + 2] * z);
  }

  function noise3(xin, yin, zin) {
    const s = (xin + yin + zin) * F3;
    const i = Math.floor(xin + s);
    const j = Math.floor(yin + s);
    const k = Math.floor(zin + s);
    const t = (i + j + k) * G3;
    const x0 = xin - (i - t);
    const y0 = yin - (j - t);
    const z0 = zin - (k - t);
    let i1, j1, k1, i2, j2, k2;
    if (x0 >= y0) {
      if (y0 >= z0) { i1 = 1; j1 = 0; k1 = 0; i2 = 1; j2 = 1; k2 = 0; }
      else if (x0 >= z0) { i1 = 1; j1 = 0; k1 = 0; i2 = 1; j2 = 0; k2 = 1; }
      else { i1 = 0; j1 = 0; k1 = 1; i2 = 1; j2 = 0; k2 = 1; }
    } else {
      if (y0 < z0) { i1 = 0; j1 = 0; k1 = 1; i2 = 0; j2 = 1; k2 = 1; }
      else if (x0 < z0) { i1 = 0; j1 = 1; k1 = 0; i2 = 0; j2 = 1; k2 = 1; }
      else { i1 = 0; j1 = 1; k1 = 0; i2 = 1; j2 = 1; k2 = 0; }
    }
    const ii = i & 255;
    const jj = j & 255;
    const kk = k & 255;
    const n0 = corner(permMod12[ii + perm[jj + perm[kk]]] * 3, x0, y0, z0);
    const n1 = corner(permMod12[ii + i1 + perm[jj + j1 + perm[kk + k1]]] * 3,
      x0 - i1 + G3, y0 - j1 + G3, z0 - k1 + G3);
    const n2 = corner(permMod12[ii + i2 + perm[jj + j2 + perm[kk + k2]]] * 3,
      x0 - i2 + 2 * G3, y0 - j2 + 2 * G3, z0 - k2 + 2 * G3);
    const n3 = corner(permMod12[ii + 1 + perm[jj + 1 + perm[kk + 1]]] * 3,
      x0 - 1 + 3 * G3, y0 - 1 + 3 * G3, z0 - 1 + 3 * G3);
    return 32 * (n0 + n1 + n2 + n3);
  }

  // --- изолинии методом marching squares ---
  // Поле = наклон по вертикали + шум, поэтому изолинии ложатся волнистыми грядами,
  // а сдвиг наклона во времени заставляет их бесконечно накатывать сверху вниз.
  const SPACING = 0.17; // шаг между соседними изолиниями
  const ACCENT_EVERY = 5; // каждая пятая линия ярче, как на топокарте

  const canvas = document.getElementById("topo");
  const ctx = canvas.getContext("2d");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let width = 0;
  let height = 0;
  let step = 12;
  let cols = 0;
  let rows = 0;
  let field = new Float32Array(0);
  let time = Math.random() * 100;
  let energy = 0;
  let energySource = () => 0;
  let lastFrame = 0;

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    width = canvas.clientWidth;
    height = canvas.clientHeight;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    step = width < 600 ? 10 : 12;
    cols = Math.ceil(width / step) + 1;
    rows = Math.ceil(height / step) + 1;
    field = new Float32Array(cols * rows);
    if (reduceMotion) render();
  }

  function sample() {
    const sc = 0.0017;
    const amp = 1 + energy * 0.55; // при 0.9 линии прыгали на каждый удар
    const flow = time * 0.06;
    for (let r = 0; r < rows; r++) {
      const y = r * step;
      for (let c = 0; c < cols; c++) {
        const x = c * step;
        const swell = Math.sin(x * 0.0031 + y * 0.0009 - time * 0.55);
        field[r * cols + c] =
          y * 0.0042 - flow +
          amp * (noise3(x * sc, y * sc * 1.5, time * 0.05) * 0.95 +
            noise3(x * sc * 2.6 + 31.7, y * sc * 2.6, time * 0.09) * 0.22 +
            swell * 0.2);
      }
    }
  }

  // Один проход по клеткам: для каждой берём только те уровни, что её пересекают.
  function contours(paths) {
    for (let r = 0; r < rows - 1; r++) {
      const y0 = r * step;
      for (let c = 0; c < cols - 1; c++) {
        const i = r * cols + c;
        const a = field[i]; // верх-лево
        const b = field[i + 1]; // верх-право
        const d = field[i + cols + 1]; // низ-право
        const e = field[i + cols]; // низ-лево
        const lo = Math.min(a, b, d, e);
        const hi = Math.max(a, b, d, e);
        const x0 = c * step;
        for (let n = Math.ceil(lo / SPACING); n * SPACING <= hi; n++) {
          const level = n * SPACING;
          const index = (a > level ? 8 : 0) | (b > level ? 4 : 0) | (d > level ? 2 : 0) | (e > level ? 1 : 0);
          if (index === 0 || index === 15) continue;
          const path = paths[((n % ACCENT_EVERY) + ACCENT_EVERY) % ACCENT_EVERY === 0 ? 1 : 0];
          const tx = x0 + step * (level - a) / (b - a);
          const ry = y0 + step * (level - b) / (d - b);
          const bx = x0 + step * (level - e) / (d - e);
          const ly = y0 + step * (level - a) / (e - a);
          const yb = y0 + step;
          const xr = x0 + step;
          switch (index) {
            case 1: case 14: line(path, x0, ly, bx, yb); break;
            case 2: case 13: line(path, bx, yb, xr, ry); break;
            case 3: case 12: line(path, x0, ly, xr, ry); break;
            case 4: case 11: line(path, tx, y0, xr, ry); break;
            case 6: case 9: line(path, tx, y0, bx, yb); break;
            case 7: case 8: line(path, tx, y0, x0, ly); break;
            case 5: line(path, tx, y0, xr, ry); line(path, x0, ly, bx, yb); break;
            case 10: line(path, tx, y0, x0, ly); line(path, bx, yb, xr, ry); break;
          }
        }
      }
    }
  }

  function line(path, x1, y1, x2, y2) {
    path.moveTo(x1, y1);
    path.lineTo(x2, y2);
  }

  // live — обычное течение; calm — медленнее и тише; off — без линий.
  // dim приглушает фон на страницах со списками, чтобы текст читался.
  const MODES = { live: { speed: 1, alpha: 1 }, calm: { speed: 0.3, alpha: 0.65 }, off: { speed: 0, alpha: 0 } };
  let mode = MODES.live;
  let dim = 1;
  let dimTarget = 1;

  function render() {
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, width, height);
    const alpha = mode.alpha * dim;
    if (alpha <= 0.01) return;
    sample();
    const paths = [new Path2D(), new Path2D()];
    contours(paths);
    ctx.lineWidth = 1;
    ctx.strokeStyle = `rgba(255,255,255,${(0.12 + energy * 0.1) * alpha})`;
    ctx.stroke(paths[0]);
    ctx.strokeStyle = `rgba(255,255,255,${(0.32 + energy * 0.3) * alpha})`;
    ctx.stroke(paths[1]);
  }

  // Постоянные времени в секундах: результат не зависит от частоты кадров.
  const ATTACK = 0.09; // удар поднимает рельеф мягко, но заметно
  const RELEASE = 0.55; // а опускается он плавно
  const DRIVE = 0.8; // скорость течения ещё плавнее: линии не дёргаются на каждый удар
  const approach = (value, target, dt, tau) => value + (target - value) * (1 - Math.exp(-dt / tau));
  let drive = 0;
  let minFrame = 15; // кадры на частоте экрана; не успеваем — переходим на половину
  let slowFrames = 0;

  function frame(now) {
    requestAnimationFrame(frame);
    if (document.hidden || now - lastFrame < (mode.speed < 1 ? 30 : minFrame)) return;
    const dt = Math.min((now - lastFrame) / 1000, 0.1);
    lastFrame = now;
    const target = energySource();
    energy = approach(energy, target, dt, target > energy ? ATTACK : RELEASE);
    drive = approach(drive, energy, dt, DRIVE);
    dim = approach(dim, dimTarget, dt, 0.25);
    if (mode.speed === 0 && Math.abs(dimTarget - dim) < 0.01) return;
    time += dt * (0.5 + drive * 2.2) * mode.speed;
    const started = performance.now();
    render();
    slowFrames = performance.now() - started > 12 ? slowFrames + 1 : Math.max(0, slowFrames - 1);
    if (slowFrames > 30) minFrame = 30; // слабое устройство: 30 кадров, зато ровных
  }

  window.addEventListener("resize", resize);
  resize();
  if (reduceMotion) render();
  else requestAnimationFrame(frame);

  window.Topo = {
    setEnergySource(fn) { energySource = fn; },
    getEnergy() { return energy; },
    setMode(name) {
      mode = MODES[name] || MODES.live;
      render();
    },
    setDim(on) {
      dimTarget = on ? 0.4 : 1;
      if (reduceMotion) {
        dim = dimTarget;
        render();
      }
    },
  };
})();
