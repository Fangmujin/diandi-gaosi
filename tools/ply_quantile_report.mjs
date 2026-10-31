#!/usr/bin/env node
/**
 * ply_quantile_report.mjs —— 真机 final.ply 的取景分位数体检（纯离线，不依赖 adb / Gradle / 设备）
 *
 * 用法：
 *   node tools/ply_quantile_report.mjs <final.ply> [--far-r=3.0] [--q=0.90,0.95,0.99]
 *                                      [--canvas=411x914] [--fovx=26.44] [--fovy=55.2] [--pitch=0]
 *
 * 设计约束（改动前请先读）：
 *  1) 分位半径的计算口径必须与 app/assets/js/viewer.js::plyFraming() **逐行一致**：
 *     - 原点 = splat 自身的质心（不是 SfM 点云质心 —— native-brush 的 framing_stats.py 用的是后者，
 *       两边口径不同，不要直接互相比数值）
 *     - 水平半径 sqrt(dx²+dz²)、竖直半高 |dy|、3D 半径，各自取分位
 *     - 分位下标 = floor(arr.length * q)（与 viewer 的 pct() 相同）
 *     - 采样 step = max(1, floor(count / 20000))，等间隔（不取前 N 个）
 *     - x/y/z 偏移按 PLY header 顺序累加 stride 得出，绝不硬编码
 *  2) scale_0/1/2 存的是 **log(scale)**，opacity 存的是 **logit**，必须先 exp() / sigmoid() 再判据。
 *     之前在这上面错过一次（计数表用了未 exp 的值），别再犯。
 *  3) 「极大 splat」判据沿用 docs/artifacts/apk-shell.md §4.8.1 已使用的口径原文：
 *       "世界单位 >1.0 档约 130–200 个、极端 >1.5 档约 90–100 个，占比 0.4%–2.2%"
 *     即阈值取 exp(scale) > 1.0 与 > 1.5，逐 splat 取 max(scale_0, scale_1, scale_2)。
 *  4) 远场占比的分母是 **全部 splat 数**，不是极大 splat 数。
 *  5) 「远场」的半径阈值 k 本身是自由参数（native-sfm §9.5.7）：同一组数据用 1.5×p90 / 2×p90 / 3×p90
 *     算出的占比能差好几倍。所以本脚本同时给出绝对阈值（--far-r）和三个尺度无关阈值，
 *     **排序可信、绝对数值待 k 选定后才可判读**。
 *  6) **占宽测的是哪个集合，必须说清，否则会被当独立证据反复引用**：
 *     - 直接量"p-q 壳自身占宽"是**死指标**：dist = r/(fill·tan(fov/2))，按定义它恒等于 fill=0.9，
 *       永远是 90%，**不能进结论**。
 *     - 本脚本的占宽测的是 **主体集合（到质心 3D 半径 ≤ p95 的那批点）** 的投影宽度 / 画布宽。
 *       这个集合是**固定的参照集**（不随 q 变），而投影用的 dist 随 q 变，所以它会变、是活指标。
 *       但它不是"p-q 壳占宽"，读报告时别混。
 *     - 注意：分轴取景是有意让水平/竖直分别贴边，所以按 **3D 半径** 定义的主体在水平方向
 *       溢出（>100%）属预期，**不要据此判裁切**；判裁切看入画率。
 *  7) **「远场占比 s > 1-q 则 q 分位失效」只能当设计约束，不能当测量判据**（native-sfm 2026-10-15 撤回）。
 *     前提不成立：按 log2(半径) 分 12 格统计密度，实测单调衰减、无第二峰 —— 远场不是可分离点群，
 *     而是一条连续长尾；于是 s 没有独立于 k 的定义（k=1.5×p90 得 4.48%、k=3×p90 得 1.22%，差 3.7 倍），
 *     **测到的是 k 不是数据**。本脚本照旧打出这些数（追溯用），但一律标"不可作判据"。
 *     总纲：**分位数定义的参照集可用（数据内在）；倍数/绝对阈值定义的量不可用（外部强加，值由 k 决定）。**
 */
import { readFileSync } from 'node:fs';
import { basename } from 'node:path';

// ---------- 参数 ----------
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
if (!plyPath) {
  console.error('usage: node tools/ply_quantile_report.mjs <final.ply> [--far-r=3.0] [--q=0.90,0.95,0.99]');
  process.exit(2);
}
const FAR_R = parseFloat(opt['far-r'] ?? '3.0');
const QS = (opt['q'] ?? '0.90,0.95,0.99').split(',').map((s) => parseFloat(s)).filter((n) => n > 0 && n < 1);
const [CW, CH] = (opt['canvas'] ?? '411x914').split('x').map((s) => parseInt(s, 10));
// 默认取 **查看页真实相机** 的参数，不是采集相机：
// viewer.js 里 PlayCanvas camera 默认 fov=45° 且 horizontalFov=false（即 45° 是竖直角），
// 水平角由画布宽高比推：tX = tan(fovy/2) * (W/H)。用采集相机的 26.44°/55.2° 会算错 dist。
const FOVY = parseFloat(opt['fovy'] ?? '45') * Math.PI / 180;
const ASP = CW / CH;
const FOVX = opt['fovx'] !== undefined
  ? parseFloat(opt['fovx']) * Math.PI / 180
  : 2 * Math.atan(Math.tan(FOVY / 2) * ASP);
const PITCH = parseFloat(opt['pitch'] ?? '18') * Math.PI / 180;  // viewer.js 默认 pitch=18
// 查看页默认 yaw=30（viewer.js: cam.yaw=30）。8 个 45° 间隔的采样点里**没有 30**，
// 所以默认视角的实际取值要从扫角里单独拎出来，否则拿 range 去比"甜区"是拿区间比点。
const APP_YAW = parseFloat(opt['yaw'] ?? '30');
// 顶栏/底栏是 absolute 盖在 canvas 上的，取景时 viewer.js::fitDistance() 会把竖直方向
// 扣掉这两条栏的高度（只缩 fill，不改 asp）。用 --inset-top / --inset-bottom 复现同一口径。
const INSET_TOP = parseFloat(opt['inset-top'] ?? '0');
const INSET_BOT = parseFloat(opt['inset-bottom'] ?? '0');
const VIS_FRAC = (INSET_TOP + INSET_BOT > 0)
  ? Math.max(0.5, Math.min(1, 1 - (INSET_TOP + INSET_BOT) / CH))
  : 1;
const FILL = 0.9;

// ---------- PLY 解析（与 viewer.js 同口径）----------
const PLY_TYPE_SIZE = {
  char: 1, uchar: 1, int8: 1, uint8: 1,
  short: 2, ushort: 2, int16: 2, uint16: 2,
  int: 4, uint: 4, int32: 4, uint32: 4, float: 4, float32: 4,
  double: 8, float64: 8
};

const buf = readFileSync(plyPath);
const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
const headBytes = new Uint8Array(ab, 0, Math.min(ab.byteLength, 65536));
const head = new TextDecoder('ascii').decode(headBytes);
const end = head.indexOf('end_header');
if (end < 0) { console.error('no end_header'); process.exit(1); }
if (head.indexOf('binary_big_endian') >= 0) { console.error('big endian PLY unsupported'); process.exit(1); }
const nl = head.indexOf('\n', end);
const dataOff = nl >= 0 ? nl + 1 : 0;

const lines = head.slice(0, end).split('\n');
let inVertex = false, count = 0, stride = 0;
const off = {}; const type = {};
for (const raw of lines) {
  const ln = raw.trim();
  if (ln.indexOf('element') === 0) {
    inVertex = /element\s+vertex\b/.test(ln);
    if (inVertex) count = parseInt(ln.split(/\s+/)[2], 10) || 0;
    continue;
  }
  if (!inVertex || ln.indexOf('property') !== 0) continue;
  const f = ln.split(/\s+/);
  const name = f[2], size = PLY_TYPE_SIZE[f[1]] || 0;
  if (['x', 'y', 'z', 'scale_0', 'scale_1', 'scale_2', 'opacity'].indexOf(name) >= 0) {
    off[name] = stride; type[name] = size;
  }
  stride += size;
}
if (!(count > 0) || stride <= 0 || off.x === undefined || off.y === undefined || off.z === undefined) {
  console.error('cannot locate x/y/z'); process.exit(1);
}

const dv = new DataView(ab);
const rd = (base, o, size) => (size === 8 ? dv.getFloat64(base + o, true) : dv.getFloat32(base + o, true));

const xs0 = new Float64Array(count), ys0 = new Float64Array(count), zs0 = new Float64Array(count);
const hasScale = off.scale_0 !== undefined && off.scale_1 !== undefined && off.scale_2 !== undefined;
const smax = hasScale ? new Float64Array(count) : null;
for (let k = 0; k < count; k++) {
  const base = dataOff + k * stride;
  if (base + stride > ab.byteLength) { count = k; break; }
  xs0[k] = rd(base, off.x, type.x);
  ys0[k] = rd(base, off.y, type.y);
  zs0[k] = rd(base, off.z, type.z);
  if (hasScale) {
    // log(scale) -> 世界单位必须 exp()
    const s0 = Math.exp(rd(base, off.scale_0, type.scale_0));
    const s1 = Math.exp(rd(base, off.scale_1, type.scale_1));
    const s2 = Math.exp(rd(base, off.scale_2, type.scale_2));
    smax[k] = Math.max(s0, s1, s2);
  }
}

// ---------- 分位半径（严格按 plyFraming 的采样与分位口径）----------
const step = Math.max(1, Math.floor(count / 20000));
const xs = [], ys = [], zs = [];
for (let k = 0; k < count; k += step) { xs.push(xs0[k]); ys.push(ys0[k]); zs.push(zs0[k]); }
let cx = 0, cy = 0, cz = 0;
for (let a = 0; a < xs.length; a++) { cx += xs[a]; cy += ys[a]; cz += zs[a]; }
cx /= xs.length; cy /= ys.length; cz /= zs.length;

const hs = [], vs = [], d3 = [];
for (let b = 0; b < xs.length; b++) {
  const dx = xs[b] - cx, dy = ys[b] - cy, dz = zs[b] - cz;
  hs.push(Math.sqrt(dx * dx + dz * dz));
  vs.push(Math.abs(dy));
  d3.push(Math.sqrt(dx * dx + dy * dy + dz * dz));
}
const pct = (arr, q) => {
  const s = arr.slice().sort((p, q2) => p - q2);
  return s[Math.min(s.length - 1, Math.floor(s.length * q))] || 0;
};
// ⚠ dist **必须是 viewer.js::fitDistance() 算出来的那个值**，不是"照着公式另写一遍"。
// 做法：从 viewer.js 里把 fitDistance 的函数体原文抽出来，在 node 里用桩对象直接执行。
// 这样只要 viewer.js 改了取景逻辑，本脚本自动跟着变，不存在"两份实现悄悄跑偏"的可能。
const VIEWER_JS = opt['viewer'] ?? 'app/assets/js/viewer.js';
const viewerSrc = readFileSync(VIEWER_JS, 'utf8');
const fdStart = viewerSrc.indexOf('function fitDistance(f, camera)');
const fdRet = viewerSrc.indexOf('return Math.max(dH, dV);', fdStart);
let distSourceNote = '';
if (fdStart >= 0 && fdRet > fdStart) {
  const fdBody = viewerSrc.slice(fdStart, viewerSrc.indexOf('}', viewerSrc.indexOf(';', fdRet)) + 1);
  const make = new Function('app', 'canvasEl', 'VR_INSET', 'fitDiag', fdBody + '\n return fitDistance;');
  var fitDistanceReal = make(
    { graphicsDevice: { width: CW, height: CH } },
    { clientWidth: CW, clientHeight: CH },
    { top: INSET_TOP, bottom: INSET_BOT },
    {}
  );
  distSourceNote = `viewer.js::fitDistance()（函数体原文执行，非重写；来源 ${VIEWER_JS}）`;
} else {
  console.error('!! 未能在 ' + VIEWER_JS + ' 里定位 fitDistance()，无法保证与查看页同口径。终止。');
  process.exit(3);
}
const distOf = (rH, rV) => fitDistanceReal({ rH, rV }, { camera: { fov: FOVY * 180 / Math.PI, horizontalFov: false } });

// ---------- 远场占比（分母 = 全部 splat 数）----------
const rAll = new Float64Array(count);
for (let k = 0; k < count; k++) {
  const dx = xs0[k] - cx, dy = ys0[k] - cy, dz = zs0[k] - cz;
  rAll[k] = Math.sqrt(dx * dx + dy * dy + dz * dz);
}
const share = (pred) => {
  let n = 0;
  for (let k = 0; k < count; k++) if (pred(rAll[k])) n++;
  return n / count;
};
const p90ref = pct(d3, 0.90);
const shareAbs = share((r) => r > FAR_R);
const shares = {
  '1.5xp90': share((r) => r > 1.5 * p90ref),
  '2xp90': share((r) => r > 2 * p90ref),
  '3xp90': share((r) => r > 3 * p90ref)
};

// ---------- 主体投影（§4.8 占宽口径）----------
const r95ref = pct(d3, 0.95);
const bodyIdx = [];
for (let k = 0; k < count; k += 1) if (rAll[k] <= r95ref) bodyIdx.push(k);
const fx = (CW / 2) / Math.tan(FOVX / 2);
const fy = (CH / 2) / Math.tan(FOVY / 2);

function project(dist, yawDeg) {
  const yaw = yawDeg * Math.PI / 180;
  const cam = [
    cx + dist * Math.cos(PITCH) * Math.sin(yaw),
    cy + dist * Math.sin(PITCH),
    cz + dist * Math.cos(PITCH) * Math.cos(yaw)
  ];
  let fwd = [cx - cam[0], cy - cam[1], cz - cam[2]];
  const fl = Math.hypot(fwd[0], fwd[1], fwd[2]); fwd = fwd.map((v) => v / fl);
  const up = [0, 1, 0];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  let rt = cross(fwd, up);
  const rl = Math.hypot(rt[0], rt[1], rt[2]); rt = rt.map((v) => v / rl);
  const tu = cross(rt, fwd);

  // 单次遍历同时量：主体(3D 半径 ≤ p95)的投影占宽/占高，以及全部 splat 的入画率
  let minU = Infinity, maxU = -Infinity, minV = Infinity, maxV = -Infinity;
  let insideAll = 0, total = 0;
  for (let k = 0; k < count; k++) {
    const v = [xs0[k] - cam[0], ys0[k] - cam[1], zs0[k] - cam[2]];
    const zc = v[0] * fwd[0] + v[1] * fwd[1] + v[2] * fwd[2];
    if (zc <= 1e-6) continue;
    const xc = v[0] * rt[0] + v[1] * rt[1] + v[2] * rt[2];
    const yc = v[0] * tu[0] + v[1] * tu[1] + v[2] * tu[2];
    const u = fx * xc / zc, w = fy * yc / zc;
    total++;
    if (Math.abs(u) <= CW / 2 && Math.abs(w) <= CH / 2) insideAll++;
    if (rAll[k] <= r95ref) {
      if (u < minU) minU = u; if (u > maxU) maxU = u;
      if (w < minV) minV = w; if (w > maxV) maxV = w;
    }
  }
  return {
    widthPct: (maxU - minU) / CW * 100,
    heightPct: (maxV - minV) / CH * 100,
    inRate: insideAll / total * 100
  };
}

function sweep(dist) {
  let wMin = Infinity, wMax = -Infinity, iMin = Infinity, iMax = -Infinity;
  for (let a = 0; a < 8; a++) {
    const r = project(dist, a * 45);
    wMin = Math.min(wMin, r.widthPct); wMax = Math.max(wMax, r.widthPct);
    iMin = Math.min(iMin, r.inRate); iMax = Math.max(iMax, r.inRate);
  }
  const d = project(dist, APP_YAW);   // 查看页真正停的那个角度
  return { wMin, wMax, iMin, iMax, dYaw: d, wMinAll: Math.min(wMin, d.widthPct), wMaxAll: Math.max(wMax, d.widthPct) };
}

// ---------- 输出 ----------
const out = [];
out.push(`== ply_quantile_report: ${basename(plyPath)} ==`);
out.push(`[1] splats = ${count}   (采样 ${xs.length} 点用于分位，step=${step}，与 plyFraming 一致)`);
out.push(`    stride = ${stride} B   x/y/z offset = ${off.x}/${off.y}/${off.z}   has scale_* = ${hasScale}`);

if (hasScale) {
  const s = Array.from(smax.subarray(0, count)).sort((a, b) => a - b);
  const sp = (q) => s[Math.min(s.length - 1, Math.floor(s.length * q))] || 0;
  const big = s.filter((v) => v > 1.0).length;
  const ext = s.filter((v) => v > 1.5).length;
  out.push(`[2] exp(scale) 物理尺度（逐 splat 取 max(scale_0/1/2)，已 exp）:`);
  out.push(`    p50=${sp(0.5).toFixed(4)}  p95=${sp(0.95).toFixed(4)}  p99=${sp(0.99).toFixed(4)}  p99.9=${sp(0.999).toFixed(4)}  max=${sp(1).toFixed(4)}`);
  out.push(`    极大 splat（判据沿用 §4.8.1: exp(scale)>1.0 与 >1.5）:`);
  out.push(`    >1.0 : ${big} 个  (${(big / count * 100).toFixed(3)}%)`);
  out.push(`    >1.5 : ${ext} 个  (${(ext / count * 100).toFixed(3)}%)`);
} else {
  out.push(`[2] 该 PLY 没有 scale_* 属性，跳过尺度统计`);
}

out.push(`[3][4] 分位半径 / 失效阈值 / 远场占比:`);
out.push(`    !! 本节数字 **不可作判据**（远场占比不可测：值由阈值 k 决定，不由数据决定）。`);
out.push(`       判据请看 [5] 的入画率（直接量、尺度无关、不需要 k）。见 apk-shell.md §4.8.3。`);
out.push(`    远场占比（绝对阈值 r>${FAR_R}）: ${(shareAbs * 100).toFixed(3)}%   分母=全部 ${count} 个 splat`);
out.push(`    远场占比（尺度无关，k 为自由参数）: 1.5xp90=${(shares['1.5xp90'] * 100).toFixed(3)}%  2xp90=${(shares['2xp90'] * 100).toFixed(3)}%  3xp90=${(shares['3xp90'] * 100).toFixed(3)}%`);
out.push(`    —— 上面三个数同一份数据能差数倍，说明 k 未定之前"有没有过阈值"没有答案，只有排序可信`);
const distByQ = {};
for (const q of QS) {
  const rH = Math.max(1e-3, pct(hs, q));
  const rV = Math.max(1e-3, pct(vs, q));
  const r3 = Math.max(1e-3, pct(d3, q));
  const dist = distOf(rH, rV);
  distByQ[q] = dist;
  const thr = 1 - q;
  const use = shareAbs / thr * 100;
  const flag = use > 100 ? '  <<< OVER' : '';
  out.push(`    q=${q.toFixed(2)}  rH=${rH.toFixed(4)} rV=${rV.toFixed(4)} r3=${r3.toFixed(4)}  dist=${dist.toFixed(4)}`);
  out.push(`           失效阈值(1-q)=${(thr * 100).toFixed(1)}%  当前已用到阈值的 ${use.toFixed(1)}%${flag}   （仅供追溯，不可作判据）`);
}

out.push(`[5] 占宽 / 入画率（8 个方位角 min~max；画布 ${CW}x${CH}）:`);
out.push(`    入画率 = 全部 splat 中投影落在画布内的占比（native-sfm 同口径，甜区 97%~98%）`);
out.push(`    占宽   = 主体（3D 半径 ≤ p95 的那批点）投影宽度 / 画布宽；分轴取景是有意让水平/竖直分别`);
out.push(`            贴边，所以按 3D 半径定义的主体在水平方向溢出属预期，不要据此判裁切。`);
for (const q of QS) {
  const rH = Math.max(1e-3, pct(hs, q));
  const rV = Math.max(1e-3, pct(vs, q));
  const dist = distOf(rH, rV);
  const sw = sweep(dist);
  // 升档上界是 p97，不是 p99。理由用不依赖阈值常数的直接量：q=0.99 的 dist 是 q=0.95 的约 2.6 倍
  // （实测 31.86 vs 12.47），相机远得多、主体小到不可用。
  // （旧理由"p99 失效阈值 1% < 实测远场占比 4.4%"已撤回 —— 远场占比不可测，见 §4.8.3。）
  let advice;
  if (sw.iMin < 95) advice = '入画率偏低（裁切多）-> 应【升】分位档 (p95->p96/p97)';
  else if (sw.iMax > 99.5 && sw.wMax < 85) advice = '主体偏小/留白多 -> 可【降】分位档 (p95->p92/p90)，' +
    '也可改取景 fill 拉近相机；是否降档由人工在真机上肉眼确认，不设自动触发';
  else advice = '落在甜区，不动';
  out.push(`    q=${q.toFixed(2)}  dist=${dist.toFixed(4)}  占宽 ${sw.wMin.toFixed(1)}%~${sw.wMax.toFixed(1)}%  入画率 ${sw.iMin.toFixed(2)}%~${sw.iMax.toFixed(2)}%   (8 个方位角 0/45/.../315 的 min~max)`);
  out.push(`           默认视角 yaw=${APP_YAW}: 占宽 ${sw.dYaw.widthPct.toFixed(1)}%  占高 ${sw.dYaw.heightPct.toFixed(1)}%  入画率 ${sw.dYaw.inRate.toFixed(2)}%   <- 真机首屏看到的就是这个`);
  out.push(`           ${advice}`);
}

if (distByQ[0.95] !== undefined && distByQ[0.90] !== undefined) {
  out.push(`[6] dist_p95 / dist_p90 = ${(distByQ[0.95] / distByQ[0.90]).toFixed(3)}   （>~1.2 提示 p95 已被污染；该常数仅 4 点校准，先只报告不动作）`);
  out.push(`    p95/p90(3D 半径) = ${(pct(d3, 0.95) / pct(d3, 0.90)).toFixed(3)}   p99/p95 = ${(pct(d3, 0.99) / pct(d3, 0.95)).toFixed(3)}`);
}
out.push(`    —— 画布 ${CW}x${CH}  fovx=${(FOVX * 180 / Math.PI).toFixed(2)}°  fovy=${(FOVY * 180 / Math.PI).toFixed(2)}°  pitch=${(PITCH * 180 / Math.PI).toFixed(0)}°  fill=${FILL}  visFrac=${VIS_FRAC.toFixed(4)} (inset ${INSET_TOP}+${INSET_BOT})`);
out.push(`    —— dist 来源：${distSourceNote}`);
out.push(`    ⚠ 入画率的 Level 由所选分位 q 决定（相机是从点云拟合出来的，自指）：q=0.80/0.90/0.95/0.99`);
out.push(`       对应约 91.3% / 95.9% / 98.8% / 99.7%（跨度 8.4 pp 纯由参数决定）。`);
out.push(`       → **别把入画率当绝对 Level 引**；只有"固定 q 之后两模型之间的差"才有信息。`);

console.log(out.join('\n'));
