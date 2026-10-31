#!/usr/bin/env node
/**
 * ply_traincam_framing.mjs —— 入画率的"训练相机口径"测量（离线，不依赖设备）
 *
 * 与 `ply_quantile_report.mjs` 的区别（两套口径不要混用）：
 *   - ply_quantile_report.mjs：**viewer 合成相机**（yaw30/pitch18、fovy=45°、画布 411×914、
 *     距离由位置分位数给出）。量的是"用户在查看页看到的那一屏"。
 *   - 本脚本：**训练相机口径** —— 每个 splat 分配给**距离它最近的那台训练相机**（COLMAP
 *     images.bin 的真实位姿 + cameras.bin 的真实内参），投影后判是否落在该相机画幅内。
 *     量的是"模型有没有超出拍摄时的覆盖范围"，与 viewer 的那一屏不是同一个东西。
 *
 * 用法：
 *   node tools/ply_traincam_framing.mjs <ply> --dataset=DIR [--modes=B,D,E,F] [--q=0.05]
 *
 * 行（mode）定义：
 *   B  基线：相机不动
 *   D  **相机整体外扩 ×1.15**：每台相机沿"质心 → 相机"后退 15%，朝向与内参不变（轨道半径 ×1.15）
 *   E  同上 ×1.30
 *   F  只取"到相机距离最近的那 q（默认 5%）"那批 splat，相机不动
 *
 * 每个行给两组数（两套分母，别混）：
 *   ① **整体（按 splat 去重）**：每个 splat 只算一次，投到"距离它最近的那台相机"。一个数。
 *   ② **视角三行**：把**全模型**投到每一台相机，得到 44 个入画率；
 *      初始 = frame_0001 那台；最坏 = 44 台里最低的；中位 = 44 台的中位数。
 *      （这才是"入画率是视角函数"的类比：等价于 viewer 里绕 yaw 一圈。）
 *
 * ⚠ 为什么外扩是"整体后退"而不是"每个 splat 各自沿视线后退"：透视投影是**尺度不变**的，
 *    后者投影像素完全不动、入画率恒等，是个空操作。要产生变化必须改变**视角**（轨道半径）。
 *
 * ⚠ 自指检查（D/E 必须一起看）：外扩让视锥变大 → 更多点落进画幅 → 入画率必然上升。
 *   所以同时给出**主体参照集**（到质心 3D 半径 ≤ p95 的 splat）的入画率，以及新入画点的
 *   半径/主体阈值 中位（>1 说明被塞进来的是主体之外的点）：
 *   **整体升、主体不动 → 上升全来自外围，是伪改善。**
 */
import { readFileSync } from 'node:fs';
import { basename } from 'node:path';

const argv = process.argv.slice(2);
const positional = [];
const opt = {};
for (const a of argv) {
  if (a.startsWith('--')) {
    const i = a.indexOf('=');
    if (i > 0) opt[a.slice(2, i)] = a.slice(i + 1);
    else opt[a.slice(2)] = 'true';
  } else positional.push(a);
}
const plyPath = positional[0];
const DS = opt['dataset'] ?? 'native/testdata/ds_44/sparse/0';
const MODES = (opt['modes'] ?? 'B,D,E,F').split(',');
const QNEAR = parseFloat(opt['q'] ?? '0.05');
const SAMPLE = parseInt(opt['sample'] ?? '0', 10); // 0 = 全量
if (!plyPath) {
  console.error('usage: node tools/ply_traincam_framing.mjs <ply> --dataset <colmap_dir> [--modes=B,D,E,F] [--q=0.05]');
  process.exit(2);
}

// ---------------- PLY ----------------
const PLY_TYPES = { char: 1, uchar: 1, int8: 1, uint8: 1, short: 2, ushort: 2, int16: 2, uint16: 2, int: 4, uint: 4, int32: 4, uint32: 4, float: 4, float32: 4, double: 8, float64: 8 };
function readPly(path) {
  const fd = readFileSync(path);
  // header
  let hEnd = -1;
  const maxHead = Math.min(fd.length, 1 << 20);
  for (let i = 0; i < maxHead - 9; i++) {
    if (fd.toString('ascii', i, i + 10) === 'end_header') {
      hEnd = fd.indexOf(0x0a, i) + 1;
      break;
    }
  }
  if (hEnd < 0) throw new Error('PLY header not found');
  const head = fd.toString('ascii', 0, hEnd).split(/\r?\n/);
  let stride = 0, count = 0;
  const props = [];
  let inVertex = false;
  for (const line of head) {
    const t = line.trim();
    if (t.startsWith('element ')) {
      const [, name, n] = t.split(/\s+/);
      if (name === 'vertex') { inVertex = true; count = parseInt(n, 10); } else inVertex = false;
    } else if (t.startsWith('property ') && inVertex) {
      const [, type, name] = t.split(/\s+/);
      const sz = PLY_TYPES[type];
      if (!sz) throw new Error('unknown ply type ' + type);
      props.push({ name, type, size: sz, offset: stride });
      stride += sz;
    }
  }
  const dataStart = hEnd;
  const step = SAMPLE > 0 ? Math.max(1, Math.floor(count / SAMPLE)) : 1;
  const n = step === 1 ? count : Math.floor((count + step - 1) / step);
  const get = (name) => props.find((p) => p.name === name);
  const px = get('x') ?? get('px'), py = get('y') ?? get('py'), pz = get('z') ?? get('pz');
  const pop = get('opacity');
  if (!px || !py || !pz) throw new Error('ply has no x/y/z');
  const pos = new Float32Array(n * 3);
  const opa = new Float32Array(n);
  const buf = fd;
  for (let i = 0, k = 0; i < count; i += step, k++) {
    const o = dataStart + i * stride;
    pos[k * 3] = buf.readFloatLE(o + px.offset);
    pos[k * 3 + 1] = buf.readFloatLE(o + py.offset);
    pos[k * 3 + 2] = buf.readFloatLE(o + pz.offset);
    if (pop) {
      const l = buf.readFloatLE(o + pop.offset);
      opa[k] = 1 / (1 + Math.exp(-l)); // opacity 存的是 logit
    } else opa[k] = 1;
  }
  return { count, n, pos, opa, stride, props: props.map((p) => p.name) };
}

// ---------------- COLMAP ----------------
function readCameras(path) {
  const b = readFileSync(path);
  let o = 0;
  const num = Number(b.readBigUInt64LE(o)); o += 8;
  const cams = new Map();
  for (let i = 0; i < num; i++) {
    const id = b.readUInt32LE(o); o += 4;
    const model = b.readInt32LE(o); o += 4;
    const w = Number(b.readBigUInt64LE(o)); o += 8;
    const h = Number(b.readBigUInt64LE(o)); o += 8;
    const nPar = { 0: 3, 1: 4, 2: 4, 3: 5, 4: 8, 5: 8 }[model] ?? 4;
    const par = [];
    for (let k = 0; k < nPar; k++) { par.push(b.readDoubleLE(o)); o += 8; }
    cams.set(id, { model, w, h, par });
  }
  return cams;
}
function readImages(path) {
  const b = readFileSync(path);
  let o = 0;
  const num = Number(b.readBigUInt64LE(o)); o += 8;
  const imgs = [];
  for (let i = 0; i < num; i++) {
    const id = b.readUInt32LE(o); o += 4;
    const q = [b.readDoubleLE(o), b.readDoubleLE(o + 8), b.readDoubleLE(o + 16), b.readDoubleLE(o + 24)]; o += 32; // qw qx qy qz
    const t = [b.readDoubleLE(o), b.readDoubleLE(o + 8), b.readDoubleLE(o + 16)]; o += 24;
    const camId = b.readUInt32LE(o); o += 4;
    let s = 0; while (b[o + s] !== 0) s++;
    const name = b.toString('utf8', o, o + s); o += s + 1;
    const nPts = Number(b.readBigUInt64LE(o)); o += 8;
    o += nPts * 24; // (x,y) double + point3D_id int64
    imgs.push({ id, q, t, camId, name });
  }
  return imgs;
}
function quatToR(q) {
  const [qw, qx, qy, qz] = q;
  return [
    1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qw * qz), 2 * (qx * qz + qw * qy),
    2 * (qx * qy + qw * qz), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qw * qx),
    2 * (qx * qz - qw * qy), 2 * (qy * qz + qw * qx), 1 - 2 * (qx * qx + qy * qy),
  ];
}

const cams = readCameras(DS + '/cameras.bin');
const imgs = readImages(DS + '/images.bin').sort((a, b) => a.name.localeCompare(b.name));
// 相机中心 C = -R^T t
const T = imgs.map((im) => {
  const R = quatToR(im.q);
  const cam = cams.get(im.camId);
  const C = [
    -(R[0] * im.t[0] + R[3] * im.t[1] + R[6] * im.t[2]),
    -(R[1] * im.t[0] + R[4] * im.t[1] + R[7] * im.t[2]),
    -(R[2] * im.t[0] + R[5] * im.t[1] + R[8] * im.t[2]),
  ];
  return { R, t: im.t, C, cam, name: im.name };
});

// ---------------- 测量 ----------------
const { count, n, pos, opa } = readPly(plyPath);

// 质心（用于主体参照集）
let cx = 0, cy = 0, cz = 0;
for (let i = 0; i < n; i++) { cx += pos[i * 3]; cy += pos[i * 3 + 1]; cz += pos[i * 3 + 2]; }
cx /= n; cy /= n; cz /= n;
const rad = new Float32Array(n);
for (let i = 0; i < n; i++) {
  const dx = pos[i * 3] - cx, dy = pos[i * 3 + 1] - cy, dz = pos[i * 3 + 2] - cz;
  rad[i] = Math.sqrt(dx * dx + dy * dy + dz * dz);
}
const radSorted = Array.from(rad).sort((a, b) => a - b);
const pctOf = (arr, q) => arr[Math.min(arr.length - 1, Math.floor(arr.length * q))];
const R_P95 = pctOf(radSorted, 0.95);
const isCore = (i) => rad[i] <= R_P95;

// 分配最近相机 + 距离
const assign = new Int32Array(n);
const dist0 = new Float32Array(n);
for (let i = 0; i < n; i++) {
  let best = -1, bd = Infinity;
  for (let c = 0; c < T.length; c++) {
    const dx = pos[i * 3] - T[c].C[0], dy = pos[i * 3 + 1] - T[c].C[1], dz = pos[i * 3 + 2] - T[c].C[2];
    const d = dx * dx + dy * dy + dz * dz;
    if (d < bd) { bd = d; best = c; }
  }
  assign[i] = best;
  dist0[i] = Math.sqrt(bd);
}
const distSorted = Array.from(dist0).sort((a, b) => a - b);
const D_NEAR_Q = pctOf(distSorted, QNEAR);

function project(Tc, Cs, X) {
  const [x, y, z] = X;
  const dx = x - Cs[0], dy = y - Cs[1], dz = z - Cs[2];
  const R = Tc.R;
  const xc = R[0] * dx + R[1] * dy + R[2] * dz;
  const yc = R[3] * dx + R[4] * dy + R[5] * dz;
  const zc = R[6] * dx + R[7] * dy + R[8] * dz;
  if (!(zc > 1e-9)) return null;
  const cam = Tc.cam;
  const u0 = xc / zc, v0 = yc / zc;
  let u, v;
  if (cam.model === 0 || cam.model === 2 || cam.model === 3) {
    // SIMPLE_PINHOLE / SIMPLE_RADIAL / RADIAL：f, cx, cy, [k...]
    const f = cam.par[0], px = cam.par[1], py = cam.par[2];
    const r2 = u0 * u0 + v0 * v0;
    let fac = 1;
    if (cam.model === 2) fac = 1 + cam.par[3] * r2;
    else if (cam.model === 3) fac = 1 + cam.par[3] * r2 + cam.par[4] * r2 * r2;
    u = f * fac * u0 + px; v = f * fac * v0 + py;
  } else {
    // PINHOLE：fx, fy, cx, cy
    const fx = cam.par[0], fy = cam.par[1], px = cam.par[2], py = cam.par[3];
    u = fx * u0 + px; v = fy * v0 + py;
  }
  return { u, v, w: cam.w, h: cam.h, inside: u >= 0 && u <= cam.w && v >= 0 && v <= cam.h };
}

function centersFor(scale) {
  return T.map((t) => (scale === 1
    ? t.C
    : [cx + scale * (t.C[0] - cx), cy + scale * (t.C[1] - cy), cz + scale * (t.C[2] - cz)]));
}

function measure(mode, scale) {
  const CS = centersFor(scale);
  const NC = T.length;

  // ---- ① 整体（每个 splat 只算一次，投到最近相机）----
  let ins = 0, tot = 0, coreIns = 0, coreTot = 0;
  const opList = [];
  const newlyIn = [];
  for (let i = 0; i < n; i++) {
    if (mode === 'F' && dist0[i] > D_NEAR_Q) continue;
    const c = assign[i];
    const X = [pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]];
    const p = project(T[c], CS[c], X);
    const inNow = !!p && p.inside;
    tot++;
    if (inNow) { ins++; opList.push(opa[i]); }
    if (isCore(i)) { coreTot++; if (inNow) coreIns++; }
    if (scale !== 1) {
      const p0 = project(T[c], T[c].C, X);
      if (inNow && !(p0 && p0.inside)) newlyIn.push(rad[i] / R_P95);
    }
  }
  opList.sort((a, b) => a - b);
  const opP95 = opList.length ? opList[Math.floor(opList.length * 0.95)] : NaN;
  const opP50 = opList.length ? opList[Math.floor(opList.length * 0.5)] : NaN;

  // ---- ② 视角三行：全模型投到每一台相机 ----
  //     F 行：每台相机各取"到自己最近的 q"那批 splat（贴脸子群）
  const perCamThresh = [];
  if (mode === 'F') {
    for (let c = 0; c < NC; c++) {
      const ds = new Float64Array(n);
      for (let i = 0; i < n; i++) {
        const dx = pos[i * 3] - T[c].C[0], dy = pos[i * 3 + 1] - T[c].C[1], dz = pos[i * 3 + 2] - T[c].C[2];
        ds[i] = dx * dx + dy * dy + dz * dz;
      }
      const s = Array.from(ds).sort((a, b) => a - b);
      perCamThresh.push(Math.sqrt(s[Math.floor(n * QNEAR)]));
    }
  }
  const camRate = [];
  const camCoreRate = [];
  for (let c = 0; c < NC; c++) {
    let ti = 0, ii = 0, ct = 0, ci = 0;
    const thr2 = mode === 'F' ? perCamThresh[c] * perCamThresh[c] : Infinity;
    for (let i = 0; i < n; i++) {
      if (mode === 'F') {
        const dx = pos[i * 3] - T[c].C[0], dy = pos[i * 3 + 1] - T[c].C[1], dz = pos[i * 3 + 2] - T[c].C[2];
        if (dx * dx + dy * dy + dz * dz > thr2) continue;
      }
      const X = [pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]];
      const p = project(T[c], CS[c], X);
      ti++;
      if (p && p.inside) ii++;
      if (isCore(i)) { ct++; if (p && p.inside) ci++; }
    }
    camRate.push({ c, r: ti ? ii / ti : 0, tot: ti });
    camCoreRate.push({ c, r: ct ? ci / ct : 0 });
  }
  const byRate = camRate.slice().sort((a, b) => a.r - b.r);
  const byCore = camCoreRate.slice().sort((a, b) => a.r - b.r);
  const pick = (arr) => ({
    initial: arr.find((x) => x.c === 0)?.r ?? NaN,
    worst: arr[0].r,
    worstCam: T[arr[0].c].name,
    median: arr[Math.floor(arr.length / 2)].r,
  });
  const v = pick(byRate);
  const vc = pick(byCore);

  return {
    mode, scale,
    overall: tot ? ins / tot : NaN,
    coreOverall: coreTot ? coreIns / coreTot : NaN,
    initial: v.initial, worst: v.worst, worstCam: v.worstCam, median: v.median,
    coreInitial: vc.initial, coreWorst: vc.worst, coreMedian: vc.median,
    opP95, opP50, nUsed: tot,
    newly: newlyIn.length ? { n: newlyIn.length, medR: newlyIn.sort((a, b) => a - b)[Math.floor(newlyIn.length / 2)] } : null,
  };
}

const pctStr = (v) => (v * 100).toFixed(2) + '%';
const rows = [];
for (const m of MODES) {
  if (m === 'B') rows.push(measure('B', 1));
  else if (m === 'D') rows.push(measure('D', 1.15));
  else if (m === 'E') rows.push(measure('E', 1.30));
  else if (m === 'F') rows.push(measure('F', 1));
}

console.log(`== ply_traincam_framing: ${basename(plyPath)} ==`);
// ---------- ③ 并集覆盖率 + 从未被任何训练相机看到的 splat（native-sfm 建议的口径）----------
// 单相机 27–29% 是预期值（每台相机只看得到模型一部分），**不是**"超出拍摄范围"的信号。
// 要回答"模型有没有超出拍摄覆盖范围"，必须看全 44 台视锥的**并集**，以及它的补集。
const seen = new Uint8Array(n);
let seenCore = 0, coreTotal = 0;
for (let i = 0; i < n; i++) {
  const X = [pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]];
  for (let c = 0; c < T.length; c++) {
    const p = project(T[c], T[c].C, X);
    if (p && p.inside) { seen[i] = 1; break; }
  }
  if (isCore(i)) { coreTotal++; if (seen[i]) seenCore++; }
}
let seenN = 0;
const unseenR = [];
for (let i = 0; i < n; i++) {
  if (seen[i]) seenN++;
  else unseenR.push(rad[i] / R_P95);
}
unseenR.sort((a, b) => a - b);
const unseenMedR = unseenR.length ? unseenR[Math.floor(unseenR.length / 2)] : NaN;
// 从未被看到的点里，不透明（α>0.5）的比例：训练时拿不到梯度、不会被正常 densify/prune，
// 是飞点的天然候选（可与 native-sfm §9.5.13 的雾团 top 贡献者对账）
let unseenOpaque = 0;
for (let i = 0; i < n; i++) if (!seen[i] && opa[i] > 0.5) unseenOpaque++;

console.log('');
console.log('**③ 并集覆盖率（全 44 台训练相机视锥的并集）—— 回答"模型有没有超出拍摄覆盖范围"**');
console.log('');
console.log('| 项 | 值 |');
console.log('| --- | --- |');
console.log(`| 并集覆盖率（全模型） | **${pctStr(seenN / n)}** |`);
console.log(`| 并集覆盖率（主体参照集） | ${pctStr(seenCore / coreTotal)} |`);
console.log(`| 从未被任何训练相机看到的 splat | **${(n - seenN).toLocaleString()}** 个（${pctStr((n - seenN) / n)}） |`);
console.log(`| 其中 α>0.5（不透明，飞点候选） | ${unseenOpaque.toLocaleString()} 个（占未见到集合的 ${pctStr(unseenOpaque / Math.max(1, n - seenN))}） |`);
console.log(`| 未见到点的 半径/主体阈值(p95) 中位 | ${unseenMedR.toFixed(2)}（>1 = 主要是主体之外的点） |`);
console.log('');
console.log('   ⚠ 单相机 27–29% **不是**异常信号：每台训练相机只看得到模型一部分（fov 26.4°）。');
console.log('     判"超出拍摄范围"要看上面这张并集表，不是看单相机入画率。');

console.log(`   数据集 ${DS}  相机 ${T.length} 台  画幅 ${T[0].cam.w}x${T[0].cam.h}  内参模型 ${T[0].cam.model}`);
console.log(`   splats 读取 ${n}${SAMPLE ? '（采样）' : '（全量）'}  原始 ${count}  主体半径阈值 p95 = ${R_P95.toFixed(4)}  最近 q=${QNEAR} 距离阈值 ${D_NEAR_Q.toFixed(4)}`);
console.log('');
console.log('**① 整体（每个 splat 投到最近相机，只算一次）**');
console.log('');
console.log('| 行 | 缩放 | 入画率·整体 | 入画率·主体 | 入画 α p95 | 入画 α p50 |');
console.log('| --- | --- | --- | --- | --- | --- |');
for (const r of rows) {
  console.log(`| **${r.mode}** | ${r.scale === 1 ? '—' : r.scale.toFixed(2) + '×'} | ${pctStr(r.overall)} | ${pctStr(r.coreOverall)} | ${r.opP95.toFixed(3)} | ${r.opP50.toFixed(3)} |`);
}
console.log('');
console.log('**② 视角三行（全模型投到每一台相机；F 行用每台相机各自的"最近 q"子群）**');
console.log('');
console.log('| 行 | 缩放 | 初始 | **最坏** | 中位 | 主体·初始 | 主体·最坏 | 主体·中位 |');
console.log('| --- | --- | --- | --- | --- | --- | --- | --- |');
for (const r of rows) {
  console.log(`| **${r.mode}** | ${r.scale === 1 ? '—' : r.scale.toFixed(2) + '×'} | ${pctStr(r.initial)} | **${pctStr(r.worst)}** | ${pctStr(r.median)} | ${pctStr(r.coreInitial)} | ${pctStr(r.coreWorst)} | ${pctStr(r.coreMedian)} |`);
}
console.log('');
for (const r of rows) console.log(`   行 ${r.mode}: 最坏相机 = ${r.worstCam}`);
console.log('');
for (const r of rows) {
  if (r.newly) {
    console.log(`   行 ${r.mode} 自指检查：新入画 ${r.newly.n} 点，其半径/主体阈值(p95) 中位 = ${r.newly.medR.toFixed(2)}（>1 = 被塞进来的主要是主体之外）`);
  }
}
