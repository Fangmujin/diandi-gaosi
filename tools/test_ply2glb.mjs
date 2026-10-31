// 独立验证：PLY -> GLB (KHR_gaussian_splatting) 转换链路
// 目的：在 Node 里先跑通 viewer.js 的 plyToGlb 逻辑，避免只在 WebView 里才发现问题
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const ROOT = 'C:/Users/28928/WorkBuddy/2026-10-11-15-32-41/diandi-gaosi';
const PLY = process.argv[2] || 'D:/点点高斯/20261011-160551_6a42461e8bbb631ea225ab29b7b520b1_raw/edit.ply';
const LIMIT = parseInt(process.argv[3] || '0', 10); // >0 时只取前 N 个 splat

let buf = fs.readFileSync(PLY);
console.log('[in]', PLY, buf.byteLength, 'bytes');
if (LIMIT > 0) buf = truncatePly(buf, LIMIT);

const RT = process.argv[5] || path.join(ROOT, 'app/assets/runtime');
const st = await import(pathToFileURL(path.join(RT, 'dist/index.mjs')).href);
console.log('[st] loaded, version =', st.version, 'revision =', st.revision);

const filename = 'test.ply';
const rfs = new st.MemoryReadFileSystem();
rfs.set(filename, new Uint8Array(buf));
console.log('[st] MemoryReadFileSystem ok');

const inFormat = st.getInputFormat ? (st.getInputFormat(filename) || 'ply') : 'ply';
console.log('[st] inputFormat =', inFormat);

const sources = await st.readFile({ filename, inputFormat: inFormat, fileSystem: rfs });
console.log('[st] readFile ->', Array.isArray(sources) ? sources.length : typeof sources, 'source(s)');
if (!sources || !sources.length) throw new Error('PLY 解析结果为空');
const s0 = sources[0];
console.log('[st] source keys =', Object.keys(s0).join(','));

const outName = 'test.glb';
const outFs = new st.MemoryFileSystem();
console.log('[st] MemoryFileSystem keys =', Object.keys(outFs).join(','));

async function tryWrite(poolArg, tag) {
  try {
    await st.writeSource({ filename: outName, outputFormat: 'glb', source: s0, pool: poolArg }, outFs);
    const out = outFs.results ? outFs.results.get(outName) : null;
    if (!out) throw new Error('GLB 输出缺失 (outFs.results=' + (outFs.results ? 'ok' : 'undefined') + ')');
    console.log('[out] ' + tag + ' OK, glb bytes =', out.byteLength ?? out.length);
    return out;
  } catch (e) {
    console.log('[out] ' + tag + ' FAILED:', e && e.message ? e.message : e);
    return null;
  }
}

// 先按 viewer.js 当前的写法（pool 传 {}）
let glb = await tryWrite({}, 'pool={} (viewer.js 现状)');
// 再按正确写法（createChunkDataPool）
if (!glb) {
  const pool = st.createChunkDataPool({});
  console.log('[st] createChunkDataPool ->', pool && Object.keys(pool).join(','));
  glb = await tryWrite(pool, 'pool=createChunkDataPool()');
}

if (glb) {
  const b = Buffer.from(glb);
  fs.writeFileSync(path.join(ROOT, 'tools/out_test.glb'), b);
  const magic = b.readUInt32LE(0);
  const jsonLen = b.readUInt32LE(12);
  const json = b.slice(20, 20 + jsonLen).toString('utf8');
  console.log('[glb] magic = 0x' + magic.toString(16), '(expect 0x46546c67)');
  console.log('[glb] version =', b.readUInt32LE(4));
  const j = JSON.parse(json);
  console.log('[glb] extensionsUsed =', JSON.stringify(j.extensionsUsed));
  console.log('[glb] extensionsRequired =', JSON.stringify(j.extensionsRequired));
  console.log('[glb] meshes =', (j.meshes || []).length, 'nodes =', (j.nodes || []).length);
  const m = (j.meshes || [])[0];
  if (m) console.log('[glb] mesh0 primitives =', JSON.stringify((m.primitives || []).map(p => p.extensions && Object.keys(p.extensions))));
  console.log('[glb] written ->', path.join(ROOT, 'tools/out_test.glb'));
} else {
  process.exitCode = 1;
}

// ---- 工具：把 PLY 截断到前 N 个 splat（保持 header 合法）----
function truncatePly(src, n) {
  const txt = src.toString('latin1');
  const endIdx = txt.indexOf('end_header');
  if (endIdx < 0) throw new Error('no end_header');
  const nl = txt.indexOf('\n', endIdx);
  const header = txt.slice(0, nl + 1);
  const dataOff = Buffer.byteLength(header, 'latin1');
  const m = header.match(/element\s+vertex\s+(\d+)/i);
  if (!m) throw new Error('no element vertex');
  const count = parseInt(m[1], 10);
  const take = Math.min(n, count);
  const stride = (src.length - dataOff) / count;
  if (!Number.isInteger(stride)) throw new Error('stride not integer: ' + stride);
  const newHeader = header.replace(/element\s+vertex\s+\d+/i, 'element vertex ' + take);
  const body = src.slice(dataOff, dataOff + stride * take);
  console.log('[trunc] original', count, 'splats, stride', stride, '-> take', take);
  return Buffer.concat([Buffer.from(newHeader, 'latin1'), body]);
}
