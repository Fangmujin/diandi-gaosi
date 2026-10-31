/* P3 查看页的 3DGS 编辑交互层 —— 对标桌面版 OOOSplat 的编辑能力。
 *
 * 数据模型与桌面完全同构（改代码前先读这三条）：
 *   1. mask：LSB-first 位图，bit i = payload[i>>3] >> (i&7) & 1，**1 = 已删除**；
 *      payload 长度 = ceil(sourceSplatCount / 8)。52 字节的 mask 文件头在 Java 侧（EditStore），
 *      这一层只处理 payload 本体。
 *   2. crop：kind 0=无裁剪 / 1=球{center,radius} / 2=盒{center,size}，语义是**保留区域内**
 *      （桌面 shader 原文 return loadOoosplatDeleted().r > 0.5 || !ooosplatCropContains(center)）。
 *   3. transform：position[3] + rotation[3]（欧拉角，单位**度**）+ scale（标量）。
 *
 * 预览非破坏性：final.ply 永不动，只多一个 mask，渲染时按位跳过。
 * 导出才是破坏性的，走 Java 侧 EditStore.exportEdit()。
 *
 * 刻意不碰 PlayCanvas 的私有 API：投影矩阵自己算（viewer 只有 cam 状态 + fov/aspect），
 * gsplat 实体靠遍历 app.root 找。viewer.js 只暴露 scene() / reloadFiltered() 两个入口。
 */
(function (global) {
  var UI = global.UI, I = global.Icons, esc = UI.esc;
  var Native = global.Native;

  var UNDO_MAX = 10;             // 快照栈深度（payload 副本，30KB 级 × 10）
  var PREVIEW_DEBOUNCE = 350;    // 预览刷新防抖，见 requestPreview()
  var GRID_CELL = 32;            // 屏幕空间网格边长（px），笔刷/框选靠它避免全量扫描
  var BRUSH_MIN = 8, BRUSH_MAX = 160;

  /* ==================== 掩码位图（纯函数，可脱离浏览器单测） ==================== */
  var Mask = {
    /** ceil(n/8) */
    bytesFor: function (n) { return n > 0 ? ((n + 7) >> 3) : 0; },

    /** 读位；越界返回 0 */
    get: function (p, i) {
      var b = i >> 3;
      if (!p || !(i >= 0) || b >= p.length) return 0;
      return (p[b] >> (i & 7)) & 1;
    },

    /** 置 1。i 越界（>= n）或位图不够长时返回 false 且一个字节都不写。 */
    set: function (p, i, n) {
      var b = i >> 3;
      if (!p || !(i >= 0) || i >= n || b >= p.length) return false;
      p[b] |= (1 << (i & 7));
      return true;
    },

    /** 清 0。越界规则同 set。 */
    clear: function (p, i, n) {
      var b = i >> 3;
      if (!p || !(i >= 0) || i >= n || b >= p.length) return false;
      p[b] &= (0xFF ^ (1 << (i & 7)));
      return true;
    },

    /** 1 的个数 = deletedCount（逐字节 SWAR） */
    popcount: function (p) {
      var n = 0;
      if (!p) return 0;
      for (var i = 0; i < p.length; i++) {
        var v = p[i];
        v = v - ((v >> 1) & 0x55);
        v = (v & 0x33) + ((v >> 2) & 0x33);
        n += (v + (v >> 4)) & 0x0F;
      }
      return n;
    }
  };

  /* ==================== crop 判定（与桌面 ooosplatCropContains 同义） ==================== */
  var Crop = {
    radiusOf: function (c) {
      if (!c || c.param === null || c.param === undefined) return 0;
      return (typeof c.param === 'number') ? c.param : c.param[0];
    },
    /** 区域内 = 保留。kind 0（或参数不全）一律全保留。 */
    contains: function (x, y, z, c) {
      if (!c || !c.kind || !c.center) return true;
      var dx = x - c.center[0], dy = y - c.center[1], dz = z - c.center[2];
      if (c.kind === 1) {
        return Math.sqrt(dx * dx + dy * dy + dz * dz) <= Crop.radiusOf(c);
      }
      if (c.kind === 2) {
        var s = c.param;
        if (!s || s.length < 3) return true;
        return Math.abs(dx) <= s[0] * 0.5
          && Math.abs(dy) <= s[1] * 0.5
          && Math.abs(dz) <= s[2] * 0.5;
      }
      return true;
    }
  };

  /* ==================== 编辑会话状态 ==================== */
  var S = {
    on: false, id: null, name: '',
    n: 0,                                   // sourceSplatCount（以 PLY 的 element vertex 为准）
    payload: null,                          // Uint8Array
    revision: 0,
    crop: { kind: 0, center: [0, 0, 0], param: 0 },
    tf: { position: [0, 0, 0], rotation: [0, 0, 0], scale: 1 },
    pos: null,                              // Float32Array(3n)，PLY 里的 x/y/z
    center: [0, 0, 0], span: 1,             // 包围盒中心与跨度，给滑杆定量程
    delCount: 0,                            // 掩码里删除的个数
    cropOut: 0,                             // 未删但被 crop 剔除的个数
    undo: [],
    dirty: false,
    tool: 'brush',
    brushR: 40,
    brushErase: true,                       // true=涂抹删除，false=涂抹恢复
    rectErase: true,                        // true=框内删除，false=框外删除（仅保留框内）
    // 投影缓存
    px: null, py: null, pok: null, grid: null, projOk: false,
    W: 0, H: 0, ox: 0, oy: 0
  };

  var els = {};                             // DOM 句柄
  var stroke = null;                        // 当前笔触/框选
  var pvTimer = null, pvBusy = false, pvPending = false;

  /* ==================== 小工具 ==================== */

  function b64encode(u8) {
    var s = '', CH = 0x8000;
    for (var i = 0; i < u8.length; i += CH) {
      s += String.fromCharCode.apply(null, u8.subarray(i, Math.min(i + CH, u8.length)));
    }
    return btoa(s);
  }

  function b64decode(str) {
    var bin = atob(str || '');
    var u = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    return u;
  }

  var PLY_TYPE_SIZE = {
    char: 1, uchar: 1, int8: 1, uint8: 1,
    short: 2, ushort: 2, int16: 2, uint16: 2,
    int: 4, uint: 4, int32: 4, uint32: 4, float: 4, float32: 4,
    double: 8, float64: 8
  };

  /**
   * 从 PLY 里读全部 x/y/z。只留 Float32Array，原始 buffer 读完就扔。
   * 偏移按 header 顺序累加，绝不硬编码（SH degree 一变顺序就变）。
   */
  function parsePositions(buf) {
    var headBytes = new Uint8Array(buf, 0, Math.min(buf.byteLength, 65536));
    var head = new TextDecoder('ascii').decode(headBytes);
    var end = head.indexOf('end_header');
    if (end < 0) throw new Error('PLY 缺少 end_header');
    if (head.indexOf('binary_big_endian') >= 0) throw new Error('不支持 big endian PLY');
    var nl = head.indexOf('\n', end);
    var dataOff = nl >= 0 ? nl + 1 : 0;
    var lines = head.slice(0, end).split('\n');
    var inVertex = false, count = 0, stride = 0;
    var offX = -1, offY = -1, offZ = -1, tX = 4, tY = 4, tZ = 4;
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i].trim();
      if (ln.indexOf('element') === 0) {
        inVertex = /element\s+vertex\b/.test(ln);
        if (inVertex) count = parseInt(ln.split(/\s+/)[2], 10) || 0;
        continue;
      }
      if (!inVertex || ln.indexOf('property') !== 0) continue;
      var f = ln.split(/\s+/);
      var size = PLY_TYPE_SIZE[f[1]] || 0;
      if (size <= 0) throw new Error('不认识的 property 类型：' + f[1]);
      if (f[2] === 'x') { offX = stride; tX = size; }
      else if (f[2] === 'y') { offY = stride; tY = size; }
      else if (f[2] === 'z') { offZ = stride; tZ = size; }
      stride += size;
    }
    if (!(count > 0) || stride <= 0 || offX < 0 || offY < 0 || offZ < 0) {
      throw new Error('PLY 里找不到 x/y/z');
    }
    if (dataOff + count * stride > buf.byteLength) throw new Error('PLY 数据被截断');

    var dv = new DataView(buf);
    var pos = new Float32Array(count * 3);
    var minX = Infinity, minY = Infinity, minZ = Infinity;
    var maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    function rd(base, off, size) {
      return size === 8 ? dv.getFloat64(base + off, true) : dv.getFloat32(base + off, true);
    }
    for (var k = 0, j = 0; k < count; k++, j += 3) {
      var base = dataOff + k * stride;
      var x = rd(base, offX, tX), y = rd(base, offY, tY), z = rd(base, offZ, tZ);
      pos[j] = x; pos[j + 1] = y; pos[j + 2] = z;
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
      if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
    }
    return {
      count: count, pos: pos,
      min: [minX, minY, minZ], max: [maxX, maxY, maxZ]
    };
  }

  function svg(n, s) { return I.svg(n, s || 20); }

  /* ==================== 变换（与 PlayCanvas 的 T·R·S 对齐） ====================
   * PlayCanvas 的 setLocalEulerAngles(x,y,z) 生成的旋转是 Rz(z)·Ry(y)·Rx(x)
   * （Mat4.setFromEulerAngles 按列展开逐项对拍过），本地变换是 T·R·S。
   * 这里照抄同一套顺序，笔刷拾取才能跟画面上的模型对得上。 */
  var XF = { sx: 0, cx: 1, sy: 0, cy: 1, sz: 0, cz: 1, identity: true };
  function syncXf() {
    var t = S.tf, D = Math.PI / 180;
    XF.sx = Math.sin(t.rotation[0] * D); XF.cx = Math.cos(t.rotation[0] * D);
    XF.sy = Math.sin(t.rotation[1] * D); XF.cy = Math.cos(t.rotation[1] * D);
    XF.sz = Math.sin(t.rotation[2] * D); XF.cz = Math.cos(t.rotation[2] * D);
    XF.identity = t.rotation[0] === 0 && t.rotation[1] === 0 && t.rotation[2] === 0
      && t.scale === 1 && !t.position[0] && !t.position[1] && !t.position[2];
  }
  /** 把 PLY 原始坐标变换到场景世界坐标，写进 out[0..2] */
  function xfPoint(x, y, z, out) {
    var t = S.tf;
    x *= t.scale; y *= t.scale; z *= t.scale;
    var a = XF.cx * y - XF.sx * z; z = XF.sx * y + XF.cx * z; y = a;
    a = XF.cy * x + XF.sy * z; z = -XF.sy * x + XF.cy * z; x = a;
    a = XF.cz * x - XF.sz * y; y = XF.sz * x + XF.cz * y; x = a;
    out[0] = x + t.position[0];
    out[1] = y + t.position[1];
    out[2] = z + t.position[2];
  }

  /* ==================== 投影：自己算 view·proj，不依赖 PlayCanvas 内部 API ==================== */

  /**
   * 行主序 4x4 的 view·proj。
   * 相机位置/朝向取真实实体（getPosition + cam.target），fov/aspect 取 camera 组件与画布尺寸，
   * 与 viewer.js 的 fitDistance / applyCam 用的是同一套输入。
   */
  function viewProj() {
    var sc = global.Viewer && global.Viewer.scene ? global.Viewer.scene() : null;
    if (!sc || !sc.app || !sc.canvasEl || !sc.cam || !sc.cam.target) return null;
    var camEnt = null;
    for (var i = 0; i < sc.app.root.children.length; i++) {
      if (sc.app.root.children[i].name === 'cam') { camEnt = sc.app.root.children[i]; break; }
    }
    if (!camEnt) return null;
    var p = camEnt.getPosition();
    var t = sc.cam.target;
    var fov = 45, horiz = false, near = 0.1, far = 5000;
    if (camEnt.camera) {
      fov = camEnt.camera.fov || 45;
      horiz = !!camEnt.camera.horizontalFov;
      near = camEnt.camera.nearClip || 0.1;
      far = camEnt.camera.farClip || 5000;
    }
    var c = sc.canvasEl;
    var W = c.clientWidth, H = c.clientHeight;
    if (!(W > 0 && H > 0)) return null;
    var asp = W / H;

    // lookAt（gluLookAt 的行主序形式，相机看向 -Z）
    var fx = t.x - p.x, fy = t.y - p.y, fz = t.z - p.z;
    var fl = Math.sqrt(fx * fx + fy * fy + fz * fz);
    if (!(fl > 1e-9)) return null;
    fx /= fl; fy /= fl; fz /= fl;
    var upx = 0, upy = 1, upz = 0;
    // s = f × up
    var rx = fy * upz - fz * upy, ry = fz * upx - fx * upz, rz = fx * upy - fy * upx;
    var rl = Math.sqrt(rx * rx + ry * ry + rz * rz);
    if (!(rl > 1e-9)) return null;      // 正上/正下看，退化
    rx /= rl; ry /= rl; rz /= rl;
    // u = s × f
    var ux = ry * fz - rz * fy, uy = rz * fx - rx * fz, uz = rx * fy - ry * fx;

    var V = [
      rx, ry, rz, -(rx * p.x + ry * p.y + rz * p.z),
      ux, uy, uz, -(ux * p.x + uy * p.y + uz * p.z),
      -fx, -fy, -fz, (fx * p.x + fy * p.y + fz * p.z),
      0, 0, 0, 1
    ];

    // perspective
    var tY = Math.tan(fov * Math.PI / 360);
    if (horiz) tY = tY / asp;            // 给的是水平角，换算成竖直半角
    var tX = tY * asp;
    var nf = 1 / (near - far);
    var P = [
      1 / tX, 0, 0, 0,
      0, 1 / tY, 0, 0,
      0, 0, (far + near) * nf, 2 * far * near * nf,
      0, 0, -1, 0
    ];

    return { m: mul4(P, V), W: W, H: H, rect: c.getBoundingClientRect() };
  }

  function mul4(a, b) {
    var o = new Float64Array(16);
    for (var r = 0; r < 4; r++) {
      for (var c = 0; c < 4; c++) {
        o[r * 4 + c] = a[r * 4] * b[c] + a[r * 4 + 1] * b[4 + c]
          + a[r * 4 + 2] * b[8 + c] + a[r * 4 + 3] * b[12 + c];
      }
    }
    return o;
  }

  /**
   * 单点投影到屏幕（CSS px，相对画布左上角）。out=[sx,sy,ok]。
   * buildProj() 与调试入口 Editor.project() 都走这一段，避免两处矩阵各行其是。
   */
  function projectOne(m, x, y, z, W, H, out) {
    var w = m[12] * x + m[13] * y + m[14] * z + m[15];
    if (w <= 1e-6) { out[2] = 0; return false; }      // 在相机后面
    var iw = 1 / w;
    out[0] = ((m[0] * x + m[1] * y + m[2] * z + m[3]) * iw * 0.5 + 0.5) * W;
    out[1] = (1 - ((m[4] * x + m[5] * y + m[6] * z + m[7]) * iw * 0.5 + 0.5)) * H;
    out[2] = 1;
    return true;
  }

  /**
   * 把全部 splat 投到屏幕，并按 32px 网格建索引。
   * 只在笔触/框选开始时做一次（相机在那期间不动），之后每次 pointermove 只扫几个格子，
   * 几十万 splat 也能跟得上手指。
   */
  function buildProj() {
    var vp = viewProj();
    if (!vp) { S.projOk = false; UI.toast('取不到相机，无法拾取'); return false; }
    var m = vp.m, W = vp.W, H = vp.H;
    if (!S.px || S.px.length !== S.n) {
      S.px = new Float32Array(S.n);
      S.py = new Float32Array(S.n);
      S.pok = new Uint8Array(S.n);
    }
    var px = S.px, py = S.py, pok = S.pok, pos = S.pos;
    var tmp = [0, 0, 0], o = [0, 0, 0];
    for (var i = 0, j = 0; i < S.n; i++, j += 3) {
      var x = pos[j], y = pos[j + 1], z = pos[j + 2];
      if (!XF.identity) { xfPoint(x, y, z, tmp); x = tmp[0]; y = tmp[1]; z = tmp[2]; }
      if (!projectOne(m, x, y, z, W, H, o)) { pok[i] = 0; continue; }
      px[i] = o[0]; py[i] = o[1]; pok[i] = 1;
    }
    S.W = W; S.H = H;
    S.ox = vp.rect.left; S.oy = vp.rect.top;

    // 计数排序建网格索引
    var cs = GRID_CELL;
    var gw = Math.max(1, Math.ceil(W / cs)), gh = Math.max(1, Math.ceil(H / cs));
    var nc = gw * gh;
    var start = new Int32Array(nc + 1);
    var cell = new Int32Array(S.n);
    var order = new Int32Array(S.n);
    for (var k = 0; k < S.n; k++) {
      if (!pok[k]) { cell[k] = -1; continue; }
      var gx = Math.floor(px[k] / cs), gy = Math.floor(py[k] / cs);
      if (gx < 0) gx = 0; else if (gx >= gw) gx = gw - 1;
      if (gy < 0) gy = 0; else if (gy >= gh) gy = gh - 1;
      var cIdx = gy * gw + gx;
      cell[k] = cIdx; start[cIdx + 1]++;
    }
    for (var c2 = 1; c2 <= nc; c2++) start[c2] += start[c2 - 1];
    var cur = start.slice(0, nc);
    for (var q = 0; q < S.n; q++) {
      if (cell[q] >= 0) order[cur[cell[q]]++] = q;
    }
    S.grid = { cs: cs, gw: gw, gh: gh, start: start, order: order };
    S.projOk = true;
    return true;
  }

  /* ==================== 掩码改动（同时维护计数，避免每次 O(n) 重算） ==================== */

  function setDeleted(i, v) {
    var was = Mask.get(S.payload, i);
    if (was === (v ? 1 : 0)) return false;
    var ok = v ? Mask.set(S.payload, i, S.n) : Mask.clear(S.payload, i, S.n);
    if (!ok) return false;
    S.delCount += v ? 1 : -1;
    var j = i * 3;
    if (!Crop.contains(S.pos[j], S.pos[j + 1], S.pos[j + 2], S.crop)) {
      S.cropOut += v ? -1 : 1;   // 本来就被 crop 剔掉的，改标记后不再重复计
    }
    return true;
  }

  /** 遍历屏幕格 (x0..x1, y0..y1)，对命中的 splat 调 fn */
  function forEachCell(x0, y0, x1, y1, fn) {
    var g = S.grid;
    if (!g) return;
    var cs = g.cs;
    var gx0 = Math.max(0, Math.floor(x0 / cs)), gx1 = Math.min(g.gw - 1, Math.floor(x1 / cs));
    var gy0 = Math.max(0, Math.floor(y0 / cs)), gy1 = Math.min(g.gh - 1, Math.floor(y1 / cs));
    for (var gy = gy0; gy <= gy1; gy++) {
      for (var gx = gx0; gx <= gx1; gx++) {
        var c = gy * g.gw + gx;
        for (var k = g.start[c]; k < g.start[c + 1]; k++) fn(g.order[k]);
      }
    }
  }

  /** 圆形笔刷：命中半径内的全部 splat（不做遮挡判断，见文件尾的限制说明） */
  function brushAt(cx, cy, r, erase) {
    var r2 = r * r, px = S.px, py = S.py, hit = 0;
    forEachCell(cx - r, cy - r, cx + r, cy + r, function (i) {
      var dx = px[i] - cx, dy = py[i] - cy;
      if (dx * dx + dy * dy > r2) return;
      if (setDeleted(i, erase)) hit++;
    });
    return hit;
  }

  /** 矩形：erase=true 删框内；false 删框外（即只保留框内） */
  function rectApply(x0, y0, x1, y1, erase) {
    var l = Math.min(x0, x1), r = Math.max(x0, x1);
    var t = Math.min(y0, y1), b = Math.max(y0, y1);
    var px = S.px, py = S.py, hit = 0;
    if (erase) {
      forEachCell(l, t, r, b, function (i) {
        if (px[i] < l || px[i] > r || py[i] < t || py[i] > b) return;
        if (setDeleted(i, true)) hit++;
      });
    } else {
      for (var i = 0; i < S.n; i++) {
        if (!S.pok[i]) continue;
        var inside = px[i] >= l && px[i] <= r && py[i] >= t && py[i] <= b;
        if (inside) continue;
        if (setDeleted(i, true)) hit++;
      }
    }
    return hit;
  }

  /** crop 变了：全量重算「未删但被 crop 剔除」的个数（O(n)，只在 crop 改动时跑） */
  function recountCrop() {
    var c = 0, pos = S.pos;
    for (var i = 0, j = 0; i < S.n; i++, j += 3) {
      if (Mask.get(S.payload, i)) continue;
      if (!Crop.contains(pos[j], pos[j + 1], pos[j + 2], S.crop)) c++;
    }
    S.cropOut = c;
  }

  function remaining() { return S.n - S.delCount - S.cropOut; }

  /* ==================== 撤销栈 ==================== */

  function pushUndo() {
    S.undo.push({
      payload: S.payload.slice(),
      crop: { kind: S.crop.kind, center: S.crop.center.slice(), param: cloneParam(S.crop.param) }
    });
    if (S.undo.length > UNDO_MAX) S.undo.shift();
  }

  function cloneParam(p) {
    return (typeof p === 'number') ? p : (p ? p.slice() : 0);
  }

  function doUndo() {
    if (!S.undo.length) { UI.toast('没有可撤销的操作'); return; }
    var s = S.undo.pop();
    S.payload = s.payload;
    S.crop = s.crop;
    S.delCount = Mask.popcount(S.payload);
    recountCrop();
    S.dirty = true;
    requestPreview();
    refresh();
    UI.toast('已撤销 · 剩余 ' + UI.fmtCount(remaining()));
  }

  /* ==================== 预览刷新（防抖 + 串行合并） ====================
   * plyToGlb + GLB 上传是重活，几十万 splat 一次要大几百毫秒。
   * 所以：① 连续改动只触发最后一次（防抖）；② 同一时刻只跑一次，跑完了若期间又有改动再补一次。
   * 这样模型再大也只是"预览跟得慢"，绝不会出现排队堆积或界面假死。 */
  function requestPreview() {
    if (!S.on) return;
    if (pvTimer) clearTimeout(pvTimer);
    pvTimer = setTimeout(function () { pvTimer = null; runPreview(); }, PREVIEW_DEBOUNCE);
  }

  function runPreview() {
    if (!S.id) return;
    if (pvBusy) { pvPending = true; return; }     // 串行：跑完再补一次，绝不并发
    pvBusy = true;
    var V = global.Viewer;
    if (!V || !V.reloadFiltered) { pvBusy = false; return; }
    // S.on 为 false 时（退出中）传 null = 不过滤，把模型还原成完整的 final.ply
    // 一个都不剩时别去重載：0 顶点的 PLY 过不了 splat-transform，宁可留着上一帧画面
    if (S.on && remaining() <= 0) {
      pvBusy = false; pvPending = false;
      UI.toast('当前没有保留任何高斯点');
      return;
    }
    var filter = S.on ? function (i, x, y, z) {
      if (!S.payload) return true;
      if (Mask.get(S.payload, i)) return false;
      return Crop.contains(x, y, z, S.crop);
    } : null;
    V.reloadFiltered(S.id, filter).then(function () {
      pvBusy = false;
      applyTransform();
      if (!S.on) UI.toast('已退出编辑');
      if (pvPending) { pvPending = false; runPreview(); }
    }).catch(function (e) {
      pvBusy = false; pvPending = false;
      console.warn('[edit] 预览刷新失败', e);
      UI.toast('预览刷新失败：' + (e && e.message ? e.message : e));
    });
  }

  /* ==================== 变换预览 ==================== */

  function findSplat() {
    var sc = global.Viewer && global.Viewer.scene ? global.Viewer.scene() : null;
    if (!sc || !sc.app) return null;
    var stack = [sc.app.root];
    while (stack.length) {
      var e = stack.pop();
      if (!e) continue;
      if (e.gsplat) return e;
      if (e.children) for (var i = 0; i < e.children.length; i++) stack.push(e.children[i]);
    }
    return null;
  }

  /** 变换只改实体本地变换，不用重载模型，所以是即时生效的 */
  function applyTransform() {
    var e = findSplat();
    if (!e) return;
    var t = S.tf;
    e.setLocalPosition(t.position[0], t.position[1], t.position[2]);
    e.setLocalEulerAngles(t.rotation[0], t.rotation[1], t.rotation[2]);
    e.setLocalScale(t.scale, t.scale, t.scale);
  }

  /* ==================== UI ==================== */

  function layer() { return document.getElementById('layer-viewer'); }

  function mountUi() {
    var L = layer();
    L.classList.add('editing');

    var cv = document.getElementById('vr-canvas');
    var ov = document.createElement('div');
    ov.id = 'ed-overlay';
    ov.className = 'ed-ov';
    if (cv && cv.parentNode === L) L.insertBefore(ov, cv.nextSibling);
    else L.insertBefore(ov, L.firstChild);
    els.ov = ov;

    var ring = document.createElement('div');
    ring.id = 'ed-ring';
    ring.className = 'ed-ring';
    ov.appendChild(ring);
    els.ring = ring;

    var rect = document.createElement('div');
    rect.id = 'ed-rect';
    rect.className = 'ed-rect';
    ov.appendChild(rect);
    els.rect = rect;

    var bar = document.createElement('div');
    bar.id = 'ed-bar';
    L.appendChild(bar);
    els.bar = bar;

    ov.addEventListener('pointerdown', onDown);
    ov.addEventListener('pointermove', onMove);
    ov.addEventListener('pointerup', onUp);
    ov.addEventListener('pointercancel', onUp);
    ov.addEventListener('pointerleave', onUp);

    renderBar();
    setTool('brush');
  }

  function unmountUi() {
    var L = layer();
    L.classList.remove('editing');
    ['ed-overlay', 'ed-bar'].forEach(function (id) {
      var e = document.getElementById(id);
      if (e && e.parentNode) e.parentNode.removeChild(e);
    });
    els = {};
    if (pvTimer) { clearTimeout(pvTimer); pvTimer = null; }
  }

  var TOOLS = [
    { k: 'brush', icon: 'brush', label: '涂抹' },
    { k: 'rect', icon: 'box-select', label: '框选' },
    { k: 'crop', icon: 'scissors', label: '裁剪' },
    { k: 'tf', icon: 'move', label: '变换' }
  ];
  var ACTS = [
    { k: 'undo', icon: 'undo-2', label: '撤销' },
    { k: 'reset', icon: 'refresh-cw', label: '重置' },
    { k: 'save', icon: 'check', label: '保存' },
    { k: 'export', icon: 'download', label: '导出' },
    { k: 'done', icon: 'x', label: '退出' }
  ];

  function renderBar() {
    var h = '<div class="ed-status">' +
      '<span class="ed-dot"></span><span class="ed-t1">编辑中</span>' +
      '<span class="ed-ct mono" id="ed-count">—</span></div>' +
      '<div class="ed-pane" id="ed-pane"></div>' +
      '<div class="ed-tools">';
    TOOLS.forEach(function (t) {
      h += '<button class="ed-tb" data-t="' + t.k + '">' + svg(t.icon, 20) +
        '<span>' + esc(t.label) + '</span></button>';
    });
    h += '<span class="ed-sep"></span>';
    ACTS.forEach(function (t) {
      h += '<button class="ed-tb" data-a="' + t.k + '">' + svg(t.icon, 20) +
        '<span>' + esc(t.label) + '</span></button>';
    });
    h += '</div>';
    els.bar.innerHTML = h;

    els.bar.querySelectorAll('[data-t]').forEach(function (b) {
      b.onclick = function () { setTool(b.getAttribute('data-t')); };
    });
    els.bar.querySelectorAll('[data-a]').forEach(function (b) {
      var k = b.getAttribute('data-a');
      b.onclick = function () {
        if (k === 'undo') doUndo();
        else if (k === 'reset') doReset();
        else if (k === 'save') doSave();
        else if (k === 'export') doExport();
        else if (k === 'done') requestExit();
      };
    });
    els.count = document.getElementById('ed-count');
  }

  function setTool(k) {
    S.tool = k;
    els.bar.querySelectorAll('[data-t]').forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-t') === k);
    });
    // 只有涂抹/框选要吃掉画布上的指针事件，裁剪/变换留给用户转视角看效果
    els.ov.classList.toggle('grab', (k === 'brush' || k === 'rect'));
    els.ring.hidden = true;
    els.rect.hidden = true;
    renderPane();
  }

  function seg(id, items, cur) {
    var h = '<div class="ed-seg" id="' + id + '">';
    items.forEach(function (it) {
      h += '<button data-v="' + esc(it.v) + '"' + (it.v === cur ? ' class="on"' : '') + '>' +
        esc(it.label) + '</button>';
    });
    return h + '</div>';
  }

  function sl(id, label, min, max, step, val, text) {
    return '<div class="ed-row"><span class="ed-lb">' + esc(label) + '</span>' +
      '<input class="ed-rg" type="range" id="' + id + '" min="' + min + '" max="' + max +
      '" step="' + step + '" value="' + val + '">' +
      '<span class="ed-nv mono" id="' + id + '-v">' + esc(text) + '</span></div>';
  }

  function numCell(id, val) {
    return '<input class="ed-nu mono" type="number" id="' + id + '" step="0.01" value="' + val + '">';
  }

  function rv(id) { var e = document.getElementById(id); return e ? Number(e.value) : 0; }
  function sv(id, t) { var e = document.getElementById(id); if (e) e.textContent = t; }
  function fix(v, d) { return (Math.round(v * Math.pow(10, d)) / Math.pow(10, d)).toFixed(d); }

  /** 归一化滑杆 [-1000,1000] → [-span, span] */
  function mapSpan(v) { return v / 1000 * S.span; }
  function unmapSpan(x) { return Math.round(Math.max(-1000, Math.min(1000, x / S.span * 1000))); }

  function renderPane() {
    var p = document.getElementById('ed-pane');
    if (!p) return;
    var h = '';
    if (S.tool === 'brush') {
      h += '<div class="ed-row"><span class="ed-lb">模式</span>' +
        seg('ed-bm', [{ v: 'del', label: '删除' }, { v: 'keep', label: '恢复' }],
          S.brushErase ? 'del' : 'keep') + '</div>';
      h += sl('ed-br', '笔刷', BRUSH_MIN, BRUSH_MAX, 1, S.brushR, S.brushR + ' px');
      h += '<div class="ed-tip">在画面上拖动即可' + (S.brushErase ? '擦除' : '恢复') + '高斯点</div>';
    } else if (S.tool === 'rect') {
      h += '<div class="ed-row"><span class="ed-lb">模式</span>' +
        seg('ed-rm', [{ v: 'del', label: '框内删除' }, { v: 'keep', label: '仅留框内' }],
          S.rectErase ? 'del' : 'keep') + '</div>';
      h += '<div class="ed-tip">在画面上拖出矩形，抬手即生效</div>';
    } else if (S.tool === 'crop') {
      h += '<div class="ed-row"><span class="ed-lb">形状</span>' +
        seg('ed-ck', [{ v: '0', label: '无' }, { v: '1', label: '球' }, { v: '2', label: '盒' }],
          String(S.crop.kind)) + '</div>';
      if (S.crop.kind) {
        h += '<div class="ed-row"><span class="ed-lb">中心</span><div class="ed-xyz">' +
          numCell('ed-c0', fix(S.crop.center[0], 2)) +
          numCell('ed-c1', fix(S.crop.center[1], 2)) +
          numCell('ed-c2', fix(S.crop.center[2], 2)) +
          '</div></div>';
        h += '<div class="ed-row"><span class="ed-lb"></span>' +
          '<button class="ed-mi" id="ed-cc">取视角中心</button></div>';
        if (S.crop.kind === 1) {
          h += sl('ed-cr', '半径', 0, 1000, 1, unmapSpan(Crop.radiusOf(S.crop)),
            fix(Crop.radiusOf(S.crop), 2));
        } else {
          h += sl('ed-s0', '尺寸 X', 0, 1000, 1, unmapSpan(S.crop.param[0]), fix(S.crop.param[0], 2));
          h += sl('ed-s1', '尺寸 Y', 0, 1000, 1, unmapSpan(S.crop.param[1]), fix(S.crop.param[1], 2));
          h += sl('ed-s2', '尺寸 Z', 0, 1000, 1, unmapSpan(S.crop.param[2]), fix(S.crop.param[2], 2));
        }
        h += '<div class="ed-tip">区域内保留，区域外剔除</div>';
      } else {
        h += '<div class="ed-tip">当前不裁剪，全部保留</div>';
      }
    } else if (S.tool === 'tf') {
      h += sl('ed-t0', '位移 X', -1000, 1000, 1, unmapSpan(S.tf.position[0]), fix(S.tf.position[0], 2));
      h += sl('ed-t1', '位移 Y', -1000, 1000, 1, unmapSpan(S.tf.position[1]), fix(S.tf.position[1], 2));
      h += sl('ed-t2', '位移 Z', -1000, 1000, 1, unmapSpan(S.tf.position[2]), fix(S.tf.position[2], 2));
      h += sl('ed-r0', '旋转 X', -1800, 1800, 1, Math.round(S.tf.rotation[0] * 10), fix(S.tf.rotation[0], 1) + '°');
      h += sl('ed-r1', '旋转 Y', -1800, 1800, 1, Math.round(S.tf.rotation[1] * 10), fix(S.tf.rotation[1], 1) + '°');
      h += sl('ed-r2', '旋转 Z', -1800, 1800, 1, Math.round(S.tf.rotation[2] * 10), fix(S.tf.rotation[2], 1) + '°');
      h += sl('ed-sc', '缩放', 10, 500, 1, Math.round(S.tf.scale * 100), fix(S.tf.scale, 2) + '×');
      h += '<div class="ed-row"><span class="ed-lb"></span>' +
        '<button class="ed-mi" id="ed-ti">归零</button></div>';
    }
    p.innerHTML = h;
    bindPane();
  }

  function bindPane() {
    var p = document.getElementById('ed-pane');
    if (!p) return;
    p.querySelectorAll('.ed-seg').forEach(function (s) {
      var id = s.id;
      s.querySelectorAll('button').forEach(function (b) {
        b.onclick = function () {
          var v = b.getAttribute('data-v');
          if (id === 'ed-bm') { S.brushErase = (v === 'del'); renderPane(); }
          else if (id === 'ed-rm') { S.rectErase = (v === 'del'); renderPane(); }
          else if (id === 'ed-ck') { setCropKind(Number(v)); }
        };
      });
    });

    var br = document.getElementById('ed-br');
    if (br) br.oninput = function () { S.brushR = Number(br.value); sv('ed-br-v', S.brushR + ' px'); };

    ['ed-c0', 'ed-c1', 'ed-c2'].forEach(function (id, i) {
      var e = document.getElementById(id);
      if (e) e.onchange = function () {
        S.crop.center[i] = Number(e.value) || 0;
        pushUndo(); S.dirty = true;
        recountCrop(); requestPreview(); refresh();
      };
    });
    var cc = document.getElementById('ed-cc');
    if (cc) cc.onclick = function () {
      var sc = global.Viewer && global.Viewer.scene ? global.Viewer.scene() : null;
      if (!sc || !sc.cam || !sc.cam.target) return;
      pushUndo();
      S.crop.center = [sc.cam.target.x, sc.cam.target.y, sc.cam.target.z];
      S.dirty = true;
      recountCrop(); requestPreview(); refresh(); renderPane();
    };

    var cr = document.getElementById('ed-cr');
    if (cr) cr.oninput = function () {
      S.crop.param = mapSpan(Number(cr.value));
      sv('ed-cr-v', fix(S.crop.param, 2));
      S.dirty = true; recountCrop(); requestPreview(); refresh();
    };
    ['ed-s0', 'ed-s1', 'ed-s2'].forEach(function (id, i) {
      var e = document.getElementById(id);
      if (e) e.oninput = function () {
        S.crop.param[i] = mapSpan(Number(e.value));
        sv(id + '-v', fix(S.crop.param[i], 2));
        S.dirty = true; recountCrop(); requestPreview(); refresh();
      };
    });

    ['ed-t0', 'ed-t1', 'ed-t2'].forEach(function (id, i) {
      var e = document.getElementById(id);
      if (e) e.oninput = function () {
        S.tf.position[i] = mapSpan(Number(e.value));
        sv(id + '-v', fix(S.tf.position[i], 2));
        syncXf(); applyTransform(); S.dirty = true;
      };
    });
    ['ed-r0', 'ed-r1', 'ed-r2'].forEach(function (id, i) {
      var e = document.getElementById(id);
      if (e) e.oninput = function () {
        S.tf.rotation[i] = Number(e.value) / 10;
        sv(id + '-v', fix(S.tf.rotation[i], 1) + '°');
        syncXf(); applyTransform(); S.dirty = true;
      };
    });
    var sc = document.getElementById('ed-sc');
    if (sc) sc.oninput = function () {
      S.tf.scale = Number(sc.value) / 100;
      sv('ed-sc-v', fix(S.tf.scale, 2) + '×');
      syncXf(); applyTransform(); S.dirty = true;
    };
    var ti = document.getElementById('ed-ti');
    if (ti) ti.onclick = function () {
      S.tf = { position: [0, 0, 0], rotation: [0, 0, 0], scale: 1 };
      syncXf(); applyTransform(); S.dirty = true; renderPane();
    };
  }

  function setCropKind(k) {
    if (k === S.crop.kind && k !== 0) { renderPane(); return; }
    pushUndo();
    S.crop.kind = k;
    if (k === 1) S.crop.param = S.span * 0.5;
    else if (k === 2) S.crop.param = [S.span, S.span, S.span];
    else S.crop.param = 0;
    S.dirty = true;
    recountCrop(); requestPreview(); refresh(); renderPane();
  }

  function refresh() {
    if (els.count) {
      els.count.textContent = '已删除 ' + UI.fmtCount(S.delCount + S.cropOut) +
        ' / 剩余 ' + UI.fmtCount(remaining());
    }
    var t = layer() ? layer().querySelector('.vr-title') : null;
    if (t) t.textContent = S.name + ' · 编辑中';
  }

  /* ==================== 画布指针：涂抹 / 框选 ==================== */

  function localPt(e) {
    return { x: e.clientX - S.ox, y: e.clientY - S.oy };
  }

  function onDown(e) {
    if (!S.on || (S.tool !== 'brush' && S.tool !== 'rect')) return;
    e.preventDefault();
    if (!S.projOk || !buildProj()) return;
    var p = localPt(e);
    pushUndo();
    stroke = { x0: p.x, y0: p.y, x: p.x, y: p.y };
    if (S.tool === 'brush') {
      brushAt(p.x, p.y, S.brushR, S.brushErase);
      showRing(p.x, p.y);
    } else {
      els.rect.hidden = false;
      drawRect(p.x, p.y, p.x, p.y);
    }
    S.dirty = true;
    refresh();
  }

  function onMove(e) {
    if (!S.on || !stroke) return;
    e.preventDefault();
    var p = localPt(e);
    if (S.tool === 'brush') {
      brushAt(p.x, p.y, S.brushR, S.brushErase);
      showRing(p.x, p.y);
    } else {
      drawRect(stroke.x0, stroke.y0, p.x, p.y);
    }
    stroke.x = p.x; stroke.y = p.y;
    S.dirty = true;
    refresh();
  }

  function onUp(e) {
    if (!stroke) return;
    if (S.tool === 'rect') {
      rectApply(stroke.x0, stroke.y0, stroke.x, stroke.y, S.rectErase);
      els.rect.hidden = true;
      S.dirty = true;
      refresh();
      UI.toast((S.rectErase ? '框内已删除' : '框外已删除') + ' · 剩余 ' + UI.fmtCount(remaining()));
    }
    stroke = null;
    els.ring.hidden = true;
    requestPreview();
  }

  function showRing(x, y) {
    var r = S.brushR;
    els.ring.hidden = false;
    els.ring.style.left = (x - r) + 'px';
    els.ring.style.top = (y - r) + 'px';
    els.ring.style.width = (r * 2) + 'px';
    els.ring.style.height = (r * 2) + 'px';
    els.ring.classList.toggle('keep', !S.brushErase);
  }

  function drawRect(x0, y0, x1, y1) {
    var l = Math.min(x0, x1), t = Math.min(y0, y1);
    els.rect.style.left = l + 'px';
    els.rect.style.top = t + 'px';
    els.rect.style.width = Math.abs(x1 - x0) + 'px';
    els.rect.style.height = Math.abs(y1 - y0) + 'px';
  }

  /* ==================== 落盘动作 ==================== */

  function doSave() {
    if (!S.on) return;
    UI.toast('正在保存…');
    setTimeout(function () { saveNow(false); }, 30);
  }

  /**
   * 写 mask + crop + transform。
   * revision 是乐观锁版本号：带上「基于的版本 + 1」提交（Java 侧 EditStore 不做冲突检测，
   * 只按传入值落盘并把 editing.revision 推到该值，所以这里必须 +1 才会真的前进）。
   */
  function saveNow(silent) {
    var rev = S.revision + 1;
    var want = Mask.bytesFor(S.n);
    if (!S.payload || S.payload.length !== want) {
      UI.toast('掩码长度 ' + (S.payload ? S.payload.length : 0) + ' != ' + want + '，已重建');
      S.payload = new Uint8Array(want);
    }
    var r1 = Native.saveEditMask({
      id: S.id,
      payloadBase64: b64encode(S.payload),
      revision: rev,
      sourceSplatCount: S.n
    });
    if (!r1 || !r1.ok) {
      UI.toast('保存失败：' + ((r1 && r1.message) || '未知错误'));
      return false;
    }
    S.revision = (r1.revision !== undefined && r1.revision !== null) ? r1.revision : rev;

    // 这两个失败不能静默：mask 已经落盘了，crop/transform 没写进去的话「已保存」就是在撒谎
    var r2 = Native.saveEditCrop({
      id: S.id, kind: S.crop.kind, center: S.crop.center, param: S.crop.param
    });
    var r3 = Native.saveEditTransform({
      id: S.id, position: S.tf.position, rotation: S.tf.rotation, scale: S.tf.scale
    });

    S.dirty = false;
    S.undo.length = 0;
    if (!silent) {
      var warn = [];
      if (!r2 || !r2.ok) warn.push('裁剪未保存');
      if (!r3 || !r3.ok) warn.push('变换未保存');
      UI.toast(warn.length
        ? ('已保存掩码，但' + warn.join('、') + ' · rev ' + S.revision)
        : ('已保存 · 剩余 ' + UI.fmtCount(remaining()) + ' · rev ' + S.revision));
    }
    return true;
  }

  function doExport() {
    if (!S.on) return;
    UI.toast('正在导出 edit.ply…');
    setTimeout(function () {
      // 导出用的是 Java 侧已落盘的 mask/crop，所以有未保存改动必须先存
      if (S.dirty && !saveNow(true)) return;
      var r = Native.exportEdit(S.id);
      if (!r || !r.ok) {
        UI.toast('导出失败：' + ((r && r.message) || '未知错误'));
        return;
      }
      UI.toast('已导出 ' + UI.fmtCount(r.splats) + ' 个高斯点 · ' + (r.path || ''), 4000);
    }, 30);
  }

  function doReset() {
    if (!S.on) return;
    UI.sheet('重置编辑会清空掩码、裁剪与变换，不可撤销', [
      {
        label: '清空全部编辑', icon: 'refresh-cw', danger: true, onTap: function () {
          Native.resetEdits(S.id);
          S.payload = new Uint8Array(Mask.bytesFor(S.n));
          S.crop = { kind: 0, center: S.center.slice(), param: 0 };
          S.tf = { position: [0, 0, 0], rotation: [0, 0, 0], scale: 1 };
          syncXf();
          S.delCount = 0; S.cropOut = 0; S.revision = 0; S.dirty = false;
          S.undo.length = 0;
          requestPreview(); refresh(); renderPane();
          UI.toast('已重置');
        }
      },
      { label: '取消', icon: 'x', onTap: function () { } }
    ]);
  }

  /* ==================== 进出编辑态 ==================== */

  function enter(id, name) {
    if (S.on) return;
    if (!id) return;
    var sc = global.Viewer && global.Viewer.scene ? global.Viewer.scene() : null;
    if (!sc || !sc.app || !sc.cam) { UI.toast('查看器尚未就绪'); return; }
    if (!Native || !Native.available) { UI.toast('原生桥不可用，无法编辑'); return; }

    UI.toast('正在载入编辑数据…');
    S.id = id; S.name = name || '';
    fetch('/model/' + id + '.ply').then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.arrayBuffer();
    }).then(function (buf) {
      var m = parsePositions(buf);
      S.n = m.count;
      S.pos = m.pos;
      S.center = [
        (m.min[0] + m.max[0]) / 2, (m.min[1] + m.max[1]) / 2, (m.min[2] + m.max[2]) / 2
      ];
      var r = Math.max(m.max[0] - m.min[0], m.max[1] - m.min[1], m.max[2] - m.min[2]) / 2;
      S.span = Math.max(1e-3, r * 2);
      S.payload = new Uint8Array(Mask.bytesFor(S.n));
      return loadState();
    }).then(function () {
      S.on = true;
      syncXf();
      mountUi();
      refresh();
      UI.toast('共 ' + UI.fmtCount(S.n) + ' 个高斯点，开始编辑');
    }).catch(function (e) {
      console.warn('[edit] 进入编辑失败', e);
      UI.toast('无法进入编辑：' + (e && e.message ? e.message : e));
    });
  }

  /** 读 Java 侧已有的编辑状态 + mask。源模型点数变了就丢弃旧掩码（位图长度对不上） */
  function loadState() {
    var st = Native.editState(S.id) || {};
    S.revision = Number(st.revision) || 0;
    var crop = st.crop;
    if (crop && crop.kind) {
      if (crop.kind === 'sphere') {
        S.crop = {
          kind: 1,
          center: arr3(crop.center, S.center),
          param: Number(crop.radius) || 0
        };
      } else if (crop.kind === 'box') {
        S.crop = {
          kind: 2,
          center: arr3(crop.center, S.center),
          param: arr3(crop.size, [S.span, S.span, S.span])
        };
      }
    } else {
      S.crop = { kind: 0, center: S.center.slice(), param: 0 };
    }
    var tf = st.transform || {};
    S.tf = {
      position: arr3(tf.position, [0, 0, 0]),
      rotation: arr3(tf.rotation, [0, 0, 0]),
      scale: Number(tf.scale) || 1
    };

    var want = Mask.bytesFor(S.n);
    if (st.sourceSplatCount && Number(st.sourceSplatCount) !== S.n) {
      UI.toast('源模型点数已变（' + st.sourceSplatCount + ' → ' + S.n + '），旧掩码已忽略');
      return null;
    }
    // 约定：没有 mask 是 {ok:true, payloadBase64:""}，只有 magic/version/maskBytes/checksum
    // 校验不过才是 ok:false。空串不是错误，静默跳过；ok:false 必须出声，否则用户会以为没编辑过
    var mk = Native.editMask(S.id);
    if (mk && mk.ok === false) {
      UI.toast('读取已有掩码失败：' + (mk.message || '校验不通过') + '，按空掩码继续');
    } else if (mk && mk.ok && mk.payloadBase64) {
      var p = b64decode(mk.payloadBase64);
      if (p.length === want) {
        S.payload = p;
      } else {
        UI.toast('掩码长度与模型不符（' + p.length + ' vs ' + want + '），已忽略');
      }
    }
    S.delCount = Mask.popcount(S.payload);
    recountCrop();
    S.dirty = false;
    S.undo.length = 0;
    return null;
  }

  function arr3(a, dft) {
    if (!a || a.length < 3) return dft ? dft.slice() : [0, 0, 0];
    return [Number(a[0]) || 0, Number(a[1]) || 0, Number(a[2]) || 0];
  }

  function requestExit() {
    if (!S.dirty) { exit(); return; }
    UI.sheet('有未保存的编辑', [
      {
        label: '保存并退出', icon: 'check', onTap: function () {
          if (saveNow(true)) { UI.toast('已保存'); exit(); }
        }
      },
      { label: '放弃编辑', icon: 'trash-2', danger: true, onTap: function () { exit(); } },
      { label: '取消', icon: 'x', onTap: function () { } }
    ]);
  }

  /**
   * 退出：卸 UI，并把预览还原成完整的 final.ply。
   * 未保存的改动只活在内存里，本来就没落盘；变换也一并归零 —— 退出后画面必须跟
   * 「final.ply 原样」一致，不能留着一个 Java 侧导出时并不应用的变换。
   * 还原走的是同一条串行预览队列，不会跟还在跑的那次刷新打架。
   */
  function exit() {
    if (!S.on) return;
    S.on = false;
    stroke = null;
    unmountUi();
    var t = layer() ? layer().querySelector('.vr-title') : null;
    if (t) t.textContent = S.name;
    S.tf = { position: [0, 0, 0], rotation: [0, 0, 0], scale: 1 };
    syncXf();
    S.payload = null; S.pos = null; S.px = null; S.py = null; S.pok = null; S.grid = null;
    S.undo.length = 0;
    UI.toast('正在还原模型…');
    runPreview();
  }

  global.Editor = {
    enter: enter,
    exit: exit,
    Mask: Mask,
    Crop: Crop,
    /** 世界坐标 → 屏幕 px（相对画布左上角）。调试/离线校验投影矩阵用；取不到相机返回 null */
    project: function (x, y, z) {
      var vp = viewProj();
      if (!vp) return null;
      var o = [0, 0, 0];
      if (!projectOne(vp.m, x, y, z, vp.W, vp.H, o)) return { x: 0, y: 0, ok: false };
      return { x: o[0], y: o[1], ok: true, W: vp.W, H: vp.H };
    },
    /** 调试/自测用：当前会话快照 */
    state: function () {
      return {
        on: S.on, id: S.id, n: S.n, revision: S.revision,
        delCount: S.delCount, cropOut: S.cropOut, remaining: remaining(),
        crop: S.crop, tf: S.tf, tool: S.tool, undo: S.undo.length, dirty: S.dirty
      };
    }
  };
})(window);
