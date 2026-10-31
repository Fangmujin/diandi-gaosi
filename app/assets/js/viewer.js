/* P3 查看页 —— final.ply → splat-transform → GLB → PlayCanvas GSplat 渲染
   渲染链与桌面版一致：PLY 先转成 KHR_gaussian_splatting 的 GLB，再交给 PlayCanvas container 资源。 */
(function (global) {
  var UI = global.UI, I = global.Icons, esc = UI.esc;

  var layer, pc = null, stLib = null, app = null, canvasEl = null;
  /** 编辑层（edit.js）用得到的场景句柄：reloadFiltered() 要卸旧实体、装新 GLB，且相机必须复用 */
  var cameraEnt = null, splatEntity = null, curAsset = null;
  /**
   * open() 拉到的原始 PLY 字节。编辑层每刷一次预览就要重新过滤一遍，
   * 每次都走 HTTP 重下一份几十 MB 太慢，所以这里留一份引用；close() 时释放。
   * 代价是查看期常驻一份 PLY 大小的内存，换的是预览延迟。
   */
  var curBuf = null;
  var cam = { yaw: 30, pitch: 18, dist: 3, target: null };
  var home = { yaw: 30, pitch: 18, dist: 3, target: null };
  var cur = { id: null, name: '' };
  /** 初始视角：plyFraming() 算出的质心 + p95 半径，避免被远场飞点撑开 */
  var frame = null;
  /** fitDistance() 真正采用的 asp / fov / horizontal，供 [framing-fit] 日志回读。
   *  必须回读而不是在日志里另算一次：FILLMODE_FILL_WINDOW 下 CSS 尺寸、绘制缓冲尺寸、
   *  graphicsDevice 尺寸三者可能不一致，日志重算会和实际取景用的 asp 对不上。 */
  var fitDiag = { asp: 0, fov: 0, horizontal: false };
  /** 顶栏/底栏遮挡高度（CSS px），由 measureChromeInset() 量出；取不到时保持 0 = 不扣 */
  var VR_INSET = { top: 0, bottom: 0 };

  function el() { if (!layer) layer = document.getElementById('layer-viewer'); return layer; }

  function chrome(name) {
    return '<div class="vr-top">' +
      '<button class="fab" data-act="back" aria-label="返回">' + I.svg('arrow-left', 20) + '</button>' +
      '<span class="vr-title">' + esc(name) + '</span>' +
      '<button class="fab" data-act="reset" aria-label="重置视角">' + I.svg('rotate-ccw', 20) + '</button>' +
      '<button class="fab" data-act="edit" aria-label="编辑">' + I.svg('pencil', 20) + '</button>' +
      '</div>' +
      '<div class="vr-bottom"><div class="vr-meta">' +
      '<div class="kv"><div class="k">高斯点数</div><div class="v" id="v-splats">—</div></div>' +
      '<div class="kv"><div class="k">文件大小</div><div class="v" id="v-size">—</div></div>' +
      '</div></div>';
  }

  function setMeta(splats, bytes) {
    var a = document.getElementById('v-splats'), b = document.getElementById('v-size');
    if (a) a.textContent = splats ? UI.fmtCount(splats) : '—';
    if (b) b.textContent = bytes ? UI.fmtBytes(bytes) : '—';
  }

  function placeholder(title, text) {
    var L = el();
    var cv = L.querySelector('#vr-canvas');
    L.innerHTML = (cv ? '' : '<canvas id="vr-canvas"></canvas>') + chrome(cur.name) +
      '<div class="vr-placeholder"><span class="ic">' + I.svg('alert-triangle', 40) + '</span>' +
      '<div class="t1">' + esc(title) + '</div>' +
      '<div class="t2">' + esc(text) + '</div></div>';
    bindChrome();
  }

  function bindChrome() {
    var L = el();
    var b = L.querySelector('[data-act="back"]');
    if (b) b.onclick = close;
    var r = L.querySelector('[data-act="reset"]');
    if (r) r.onclick = function () { resetView(); };
    var e = L.querySelector('[data-act="edit"]');
    if (e) e.onclick = function () {
      // 延迟取：edit.js 在 viewer.js 之后加载，但 open() 可能早于脚本执行完
      if (global.Editor && global.Editor.enter) global.Editor.enter(cur.id, cur.name);
      else UI.toast('编辑模块未加载');
    };
  }

  // 「保存 HTML 到手机」的入口已移到图库每个卡片的右上角（见 pages.js 的 .gshare），
  // 这里不再提供，避免同一个功能两处入口、行为还不一致。

  async function open(id, name) {
    cur.id = id; cur.name = name || '未命名';
    var L = el();
    L.hidden = false;
    L.innerHTML = '<canvas id="vr-canvas"></canvas>' + chrome(cur.name) +
      '<div class="vr-placeholder"><span class="ic">' + I.svg('loader', 40) + '</span>' +
      '<div class="t1">正在载入模型…</div></div>';
    bindChrome();

    var buf;
    try {
      var resp = await fetch('/model/' + id + '.ply');
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      buf = await resp.arrayBuffer();
      curBuf = buf;
    } catch (e) {
      placeholder('引擎待接入', '还没有可渲染的高斯模型。原生重建/训练引擎接入后，' +
        '这里会展示 final.ply 的实时渲染结果。');
      return;
    }
    if (buf.byteLength < 1024) {
      placeholder('模型文件为空', '该项目的 final.ply 未生成，可在图库页长按删除后重新重建。');
      return;
    }
    var splats = parsePlyCount(buf);
    setMeta(splats, buf.byteLength);
    frame = plyFraming(buf, framingQuantile(id));

    // 1) PLY → GLB（splat-transform 纯 CPU 路径）
    var glb;
    try {
      if (!stLib) {
        stLib = await import('../runtime/dist/index.mjs');
      }
      glb = await plyToGlb(stLib, buf, id + '.ply');
    } catch (e) {
      console.warn('ply->glb failed', e);
      placeholder('模型转换失败', String(e && e.message ? e.message : e));
      return;
    }

    // 2) PlayCanvas 渲染
    try {
      if (!pc) pc = await import('../runtime/playcanvas.min.mjs');
    } catch (e) {
      placeholder('渲染引擎加载失败', String(e && e.message ? e.message : e));
      return;
    }
    try {
      await renderGlb(glb, splats);
    } catch (e) {
      console.warn('viewer render failed', e);
      placeholder('渲染失败', String(e && e.message ? e.message : e));
    }
  }

  /** PLY → GLB（KHR_gaussian_splatting） */
  function plyToGlb(st, buf, filename) {
    var bytes = new Uint8Array(buf);
    var rfs = new st.MemoryReadFileSystem();
    rfs.set(filename, bytes);
    var inFormat = 'ply';
    try { inFormat = st.getInputFormat(filename) || 'ply'; } catch (e) { inFormat = 'ply'; }
    return st.readFile({ filename: filename, inputFormat: inFormat, fileSystem: rfs })
      .then(function (sources) {
        if (!sources || !sources.length) throw new Error('PLY 解析结果为空');
        var pool = st.createChunkDataPool({});
        var outFs = new st.MemoryFileSystem();
        var outName = filename.replace(/\.[^.]+$/, '') + '.glb';
        return st.writeSource({
          // 必须传真正的 chunk pool（createChunkDataPool 产物），传 {} 会报
          // "pool.acquire is not a function"
          filename: outName, outputFormat: 'glb', source: sources[0], pool: pool
        }, outFs).then(function () {
          var out = outFs.results.get(outName);
          if (!out) throw new Error('GLB 输出缺失');
          return out;
        });
      });
  }
  function parsePlyCount(buf) {
    try {
      var head = new TextDecoder('ascii').decode(
        new Uint8Array(buf, 0, Math.min(buf.byteLength, 4096)));
      var m = head.match(/element\s+vertex\s+(\d+)/i);
      return m ? parseInt(m[1], 10) : 0;
    } catch (e) { return 0; }
  }

  /**
   * 取景分位数的可调范围。**上界刻意停在 p97，p99 不可达。**
   *
   * p99 禁用的理由（只用直接量，不依赖任何阈值常数）：
   *   实测同一份 PLY（**生产口径**快档基线 80,948 splats）：q=0.99 → dist=30.48，
   *   q=0.95 → dist=11.87，相机距离 **2.57 倍**；加正则后 17.19 vs 10.04（1.71×）。
   *   分位往上挪，相机按该分位的半径线性后退，主体按 1/dist 缩小；p99 落在长尾最尖端，
   *   必然把相机推到极远，主体小到不可用。这个论证不含 k，也不含任何"占比"概念。
   *   （旧注释里写的是 31.86 / 12.47 = 2.6 倍，来自二进制默认参数批次，已作废；
   *    结论与倍数都不变，只是两个数要换成生产口径。）
   *
   * 【已撤回的旧理由，别再照它实现】曾用"q 的失效阈值是 (1-q)=1%，实测远场占比 4.4%，
   * 超四倍故 p99 必失效"论证。该理由已被 native-sfm 撤回：**远场占比不可测** ——
   * 它依赖人为选定的倍数 k（k=1.5×p90 得 4.48%，k=3×p90 得 1.22%，同一份数据差 3.7 倍），
   * 测到的是 k 不是数据。结论（p99 不可用）仍成立，但只由上面的直接量支撑。
   */
  var FRAMING_Q_MIN = 0.90;
  var FRAMING_Q_MAX = 0.97;

  /**
   * 取景分位数按档位取。分档机制保留着，当前两档都是 p95。
   *
   * 精档（13000 步）回退 p95 的理由（同样只用直接量）：
   *   入画率实测（**生产口径**精档基线 89,742 splats，跨 8 个方位角）：
   *   q=0.95 是 96.76%~99.51%，q=0.90 是 93.89%~97.19%。
   *   （旧注释的 96.06%~99.59% / 93.81%~97.60% 来自二进制默认参数批次，已作废；方向不变。）
   *   p90 整段落在"裁切更多"那一侧 —— 代价是实测的，收益没兑现，所以两档都回 p95。
   *   入画率是尺度无关的直接量（数投影落在画布内的 splat 占比），不掺任何阈值常数。
   *
   * 降档触发条件：**不设自动触发，只能人工判断。**
   *   旧版本写的是"远场占比超过 (1-q)（已用% > 100%）时自动降到 p90"，该条件已作废：
   *   远场占比不可测（见上），这个表达式永远不会被正确求值，照它实现只会写成死代码。
   *   替代做法：真机上**肉眼看主体是否偏小**，是则人工把 q 往 p92/p90 调。
   *   注意"主体偏小"也可以靠取景 fill（拉近相机）解决，不一定动分位；
   *   "主体过大 / 边缘被裁切"才是**提高**分位的信号。
   *
   * 判据挑选的总纲（换判据前先读这条）：
   *   **由分位数定义的参照集可用** —— 参照集是数据内在的，q 一变它跟着变，值由数据决定。
   *   **由倍数或绝对阈值定义的量不可用** —— k×p90、r>3 之类是外部强加的，测出来的值由 k
   *   决定而非由数据决定；**只报一档的计数同样不可用**（换档横跳时无从判断该信哪档）。
   *   入画率、占宽（固定参照集 = 3D 半径 ≤ p95 的点）属前一类；
   *   远场占比、r>3、r>3×p90、巨型片计数属后一类 —— 后者只配打日志，不配做判据、不配做趋势。
   *   ⚠ 两条纪律**叠加**而非替代：**"分位数定义的可用" ≠ 不用扫敏感度 —— q 本身也是参数。**
   *   正确顺序是：先选长在数据上的定义（分位数），**再至少扫两档 q** 看稳不稳。
   *   （分位数可用是因为 q 无量纲、可单调枚举 0.90/0.95/0.99、能一眼看出稳不稳；
   *   倍数/绝对阈值没有自然枚举序列，换档横跳时无从判断。）
   *
   * 见 docs/artifacts/apk-shell.md §4.8.2 / §4.8.3。
   */
  function framingQuantile(projectId) {
    var q = 0.95;
    try {
      var ps = (global.App && global.App.state && global.App.state.projects) || [];
      for (var i = 0; i < ps.length; i++) {
        if (ps[i] && ps[i].id === projectId) {
          // 分档分支保留：真机上若快档/精档的入画率/占宽出现分化，改这里一行即可分开
          q = (ps[i].quality === 'fine') ? 0.95 : 0.95;
          break;
        }
      }
    } catch (e) { /* 查不到就按 p95 */ }
    return Math.min(FRAMING_Q_MAX, Math.max(FRAMING_Q_MIN, q));
  }

  /**
   * 用「质心 + 半径分位数」定初始视角，而不是包围盒 min/max。
   * 3DGS 训练产物里的远场飞点能把 bbox 撑大数倍；按 bbox 算相机距离会把主体缩成一个小点。
   *
   * 方向关系（调档前务必确认）：**分位越高 → 半径越大 → 相机越远 → 主体越小**。
   * 所以"主体偏小"要**降低**分位（p95→p92/p90），"主体过大/边缘裁切"才**提高**分位。
   *
   * @param q 分位数（0.95 / 0.90），由 framingQuantile() 按档位给
   *
   * ⚠ **取景是单视角设计**（未来若要改，先读这段）：
   * 本函数**只对默认视角（yaw=30）算一次**，用户旋转/缩放之后**不重算** ——
   * `cam.dist` 在 mount() 时定下来就不再变（手势里的缩放只是整体改 dist，不会重算分位数）。
   * 所以"入画率"这类量是 **模型 × 视角** 的属性，不是模型的属性：
   * 实测跨视角跨度 **3.65 pp**，是甜区宽度（1 pp）的 **3.6 倍** ——
   * **旋转带来的变化比换数据集（1.23 pp）还大**。
   * 后果：真机上看到"转到某个角度主体偏小/出画"，**多半是这个原因，不是 `FRAMING_Q_*` 选错了**。
   * 若将来要改成随视角重算，三个代价先想清楚：
   *   ① 每帧重算分位数的开销（现在是全量扫描 + 排序，300k splat 级别不能每帧做）；
   *   ② 相机距离随视角跳变会让画面"呼吸"，必须做平滑/迟滞，否则体验更差；
   *   ③ 重算后"主体占幅"会在旋转中变化，需要重新定义什么算"稳定"的取景。
   */
  var PLY_TYPE_SIZE = {
    char: 1, uchar: 1, int8: 1, uint8: 1,
    short: 2, ushort: 2, int16: 2, uint16: 2,
    int: 4, uint: 4, int32: 4, uint32: 4, float: 4, float32: 4,
    double: 8, float64: 8
  };

  function plyFraming(buf, q) {
    if (!(q > 0.5 && q < 1)) q = 0.95;
    try {
      var headBytes = new Uint8Array(buf, 0, Math.min(buf.byteLength, 65536));
      var head = new TextDecoder('ascii').decode(headBytes);
      var end = head.indexOf('end_header');
      if (end < 0) return null;
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
        var name = f[2], size = PLY_TYPE_SIZE[f[1]] || 0;
        // 偏移必须按 header 顺序累加，绝不硬编码：x/y/z 在末尾只是 brush 当前导出顺序，
        // SH degree 一变（f_rest_* 从 9 个变 24 个）或属性顺序一调，写死的偏移就会读到
        // scale_*/f_rest_*，而且不报错、只是悄悄框错地方。
        if (name === 'x') { offX = stride; tX = size; }
        else if (name === 'y') { offY = stride; tY = size; }
        else if (name === 'z') { offZ = stride; tZ = size; }
        stride += size;
      }
      if (!(count > 0) || stride <= 0 || offX < 0 || offY < 0 || offZ < 0) return null;
      if (head.indexOf('binary_big_endian') >= 0) return null;

      var rd = function (base, off, size) {
        return size === 8 ? dv.getFloat64(base + off, true) : dv.getFloat32(base + off, true);
      };

      // 等间隔采样：splat 通常按训练顺序/空间分块排列，只取前 N 个会偏在物体一角
      var step = Math.max(1, Math.floor(count / 20000));
      var xs = [], ys = [], zs = [];
      var dv = new DataView(buf);
      for (var k = 0; k < count; k += step) {
        var base = dataOff + k * stride;
        if (base + stride > buf.byteLength) break;
        xs.push(rd(base, offX, tX));
        ys.push(rd(base, offY, tY));
        zs.push(rd(base, offZ, tZ));
      }
      if (!xs.length) return null;
      var cx = 0, cy = 0, cz = 0;
      for (var a = 0; a < xs.length; a++) { cx += xs[a]; cy += ys[a]; cz += zs[a]; }
      cx /= xs.length; cy /= ys.length; cz /= zs.length;

      // 分轴取 p95：竖直轴是 y（PLY header 的 Vertical axis），手势旋转是绕 y 转的，
      // 所以水平半径用 sqrt(dx²+dz²)（对 yaw 不变），竖直半高用 |dy|。
      // 用一个 3D 球半径同时套水平/竖直会过度保守（把画面撑空）。
      var hs = [], vs = [], d3 = [];
      for (var b = 0; b < xs.length; b++) {
        var dx = xs[b] - cx, dy = ys[b] - cy, dz = zs[b] - cz;
        hs.push(Math.sqrt(dx * dx + dz * dz));
        vs.push(Math.abs(dy));
        d3.push(Math.sqrt(dx * dx + dy * dy + dz * dz));
      }
      function pct(arr, qq) {
        arr.sort(function (p, s) { return p - s; });
        return arr[Math.min(arr.length - 1, Math.floor(arr.length * qq))] || 0;
      }
      // 诊断：3D 半径的分位数全打出来，真机上靠它判断 p95 有没有被尾部污染
      // （判据见 §4.8.3：p95/p90 干净组 ≤1.13，接近污染那组 1.34）。
      var d3s = d3.slice().sort(function (p, s) { return p - s; });
      function dpct(qq) { return d3s[Math.min(d3s.length - 1, Math.floor(d3s.length * qq))] || 0; }
      var dg = { p50: dpct(0.50), p75: dpct(0.75), p90: dpct(0.90), p95: dpct(0.95), p99: dpct(0.99) };
      dg.max = d3s[d3s.length - 1] || 0;
      console.log('[framing] q=' + q + ' n=' + xs.length
        + ' r3 p50=' + dg.p50.toFixed(3) + ' p75=' + dg.p75.toFixed(3)
        + ' p90=' + dg.p90.toFixed(3) + ' p95=' + dg.p95.toFixed(3)
        + ' p99=' + dg.p99.toFixed(3) + ' max=' + dg.max.toFixed(3)
        + ' p95/p90=' + (dg.p90 > 0 ? (dg.p95 / dg.p90).toFixed(3) : 'n/a')
        + ' p99/p95=' + (dg.p95 > 0 ? (dg.p99 / dg.p95).toFixed(3) : 'n/a'));
      return {
        cx: cx, cy: cy, cz: cz,
        rH: Math.max(1e-3, pct(hs, q)),
        rV: Math.max(1e-3, pct(vs, q)),
        r3: Math.max(1e-3, pct(d3, q)),
        q: q, diag: dg
      };
    } catch (e) {
      return null;
    }
  }

  /**
   * 顶栏/底栏是绝对定位**盖在** canvas 上的（`.vr-top` / `.vr-bottom`），而 canvas 被
   * FILLMODE_FILL_WINDOW 撑成整窗。所以按整窗取景时，主体上下两端会压在 UI 底下。
   * 这里量出两条栏的实际高度，取景时把竖直方向的可视比例扣掉——**只动竖直约束，不动 asp**
   * （canvas 尺寸不变、相机 FOV 不变，所以 native-sfm 那套按 asp=0.45 算的雾团/取景数继续有效）。
   * 量不到（未布局/异常）就退化为"不扣"，行为与改动前一致。
   */
  function measureChromeInset() {
    VR_INSET.top = 0; VR_INSET.bottom = 0;
    try {
      var L = el(); if (!L) return;
      var th = 0, bh = 0;
      var t = L.querySelector('.vr-top'); if (t) th = t.getBoundingClientRect().height;
      var b = L.querySelector('.vr-bottom'); if (b) bh = b.getBoundingClientRect().height;
      var H = (canvasEl && canvasEl.clientHeight) || 0;
      // 超过 40% 一定是量错了（未布局会返回 0，异常值会返回离谱的大数），不采信
      if (H > 0 && th + bh > 0 && th + bh < H * 0.4) { VR_INSET.top = th; VR_INSET.bottom = bh; }
    } catch (e) { VR_INSET.top = 0; VR_INSET.bottom = 0; }
  }

  function renderGlb(glbBytes, splats) {
    return new Promise(function (resolve, reject) {
      var L = el();
      var ph = L.querySelector('.vr-placeholder');
      if (ph) ph.parentNode.removeChild(ph);
      canvasEl = document.getElementById('vr-canvas');

      var gl2 = false;
      try { gl2 = !!document.createElement('canvas').getContext('webgl2'); } catch (e) {}
      if (!gl2) { reject(new Error('当前设备不支持 WebGL2')); return; }

      app = new pc.Application(canvasEl, {
        mouse: new pc.Mouse(document.body),
        touch: typeof pc.TouchDevice !== 'undefined' ? new pc.TouchDevice(document.body) : undefined
      });
      app.setCanvasFillMode(pc.FILLMODE_FILL_WINDOW);
      app.setCanvasResolution(pc.RESOLUTION_AUTO);

      var camera = new pc.Entity('cam');
      camera.addComponent('camera', { clearColor: new pc.Color(0.02, 0.03, 0.04), farClip: 5000 });
      app.root.addChild(camera);
      cameraEnt = camera;

      loadGlbAsset(glbBytes, camera, false, splats, resolve, reject);
    });
  }

  /**
   * 建 container 资源并挂到场景。keepCam=true 时复用现有相机与当前视角（编辑层预览用），
   * 否则按 PLY 取景重新定初始视角（首次载入）。
   */
  function loadGlbAsset(glbBytes, camera, keepCam, splats, resolve, reject) {
    var blob = new Blob([glbBytes], { type: 'model/gltf-binary' });
    var asset = new pc.Asset('model.glb', 'container', { url: URL.createObjectURL(blob) });
    var settled = false;
    asset.on('load', function () {
      if (!settled) { settled = true; mount(asset, camera, keepCam, splats, resolve, reject); }
    });
    asset.on('error', function (err) {
      if (!settled) {
        settled = true;
        reject(new Error('GLB 资源加载失败：' + (err && err.message ? err.message : err)));
      }
    });
    app.assets.add(asset);
    app.assets.load(asset);
    setTimeout(function () {
      if (!settled) { settled = true; reject(new Error('模型加载超时')); }
    }, 90000);
  }

  function mount(asset, camera, keepCam, splats, resolve, reject) {
    try {
      // 编辑层每次预览都会重载一份新 GLB：不卸掉旧的会叠成一团
      if (splatEntity) {
        try { app.root.removeChild(splatEntity); splatEntity.destroy(); } catch (e0) {}
        splatEntity = null;
      }
      if (curAsset) {
        try { app.assets.remove(curAsset); URL.revokeObjectURL(curAsset.file.url); } catch (e1) {}
        curAsset = null;
      }

      var entity = asset.resource.instantiateRenderEntity();
      app.root.addChild(entity);
      splatEntity = entity;
      curAsset = asset;

      // 高斯点默认尺寸偏小会看不见，按上一轮实测放大
      if (entity.gsplat) {
        try {
          if (typeof entity.gsplat.splatScale === 'number') entity.gsplat.splatScale = 3.0;
          if (typeof entity.gsplat.build === 'function') entity.gsplat.build();
        } catch (e) { /* 只读属性时忽略 */ }
      }
      app.start();

      if (keepCam) {
        // 编辑层预览重载：相机与视角原样沿用，只换模型数据
        applyCam(camera);
        resolve({ splats: splats || 0 });
        return;
      }

      var center, dist;
      if (frame) {
        // 优先用 PLY 自身的质心 + 分轴 p95 半径：远场飞点不会把相机推远
        center = new pc.Vec3(frame.cx, frame.cy, frame.cz);
        measureChromeInset();
        dist = fitDistance(frame, camera);
        // 补一条带 dist / asp 的日志：视锥计数对屏幕比例敏感（dist 随 asp 变），
        // 只打分位数不打 asp 的话，真机数字没法跟 native-sfm 的 occlusion_stats 对齐。
        // asp 直接决定 dist，也决定巨型 floater 落在相机前面还是后面（native-sfm 的雾团分析
        // 对 asp 极敏感）。所以这里把四套尺寸全打出来：CSS 尺寸、绘制缓冲尺寸、
        // graphicsDevice 尺寸、window 尺寸 + DPR，外加 fitDistance 真正用的那个 asp。
        try {
          var dw = 0, dh = 0;
          try { if (app && app.graphicsDevice) { dw = app.graphicsDevice.width; dh = app.graphicsDevice.height; } } catch (e0) {}
          var dpr = (global.devicePixelRatio || 1);
          console.log('[framing-fit] q=' + frame.q + ' dist=' + dist.toFixed(4)
            + ' | aspUsed=' + fitDiag.asp.toFixed(4) + ' fov=' + fitDiag.fov
            + (fitDiag.horizontal ? '(H)' : '(V)')
            + ' | css=' + (canvasEl ? canvasEl.clientWidth : 0) + 'x' + (canvasEl ? canvasEl.clientHeight : 0)
            + ' buf=' + (canvasEl ? canvasEl.width : 0) + 'x' + (canvasEl ? canvasEl.height : 0)
            + ' dev=' + dw + 'x' + dh
            + ' win=' + (global.innerWidth || 0) + 'x' + (global.innerHeight || 0)
            + ' dpr=' + dpr
            + ' | inset=' + VR_INSET.top.toFixed(0) + '+' + VR_INSET.bottom.toFixed(0)
            + ' visFrac=' + (fitDiag.visFrac || 1).toFixed(4)
            + ' | rH=' + frame.rH.toFixed(4) + ' rV=' + frame.rV.toFixed(4));
        } catch (e) { /* 日志失败不影响渲染 */ }
      } else {
        // 兜底路径目前不可达（plyFraming 只在解析不出 x/y/z 时才返回 null）。
        // 一旦走了这里，取景会退化成"含 splat 半径的世界包围盒 × 0.9"，
        // 也就是会被远场飞点撑大的那条老路（实测 bbox 对角线可被放大 2~5 倍）。
        // 不要静默：真机上看到画面偏远时，得能一眼区分是"p95 没调好"还是"根本没走 p95"。
        console.warn('plyFraming 失败，退化为世界包围盒取景（会被远场飞点撑大，画面偏远属预期）');
        var box = null;
        try { box = entity.getWorldBounds ? entity.getWorldBounds() : null; } catch (e) {}
        center = (box && box.center) ? box.center : new pc.Vec3(0, 0, 0);
        dist = (box && box.halfExtents && box.halfExtents.length() > 1e-4)
          ? box.halfExtents.length() * 0.9 : 1;
      }
      cam.target = center;
      cam.dist = Math.max(0.05, dist);
      cam.yaw = 30; cam.pitch = 18;
      home = { yaw: cam.yaw, pitch: cam.pitch, dist: cam.dist, target: center };
      applyCam(camera);
      bindGestures(camera);
      setTimeout(function () { applyCam(camera); }, 120);
      resolve({ splats: splats || 0 });
    } catch (e) {
      reject(e);
    }
  }

  /**
   * 按 FOV 分别算"装得下水平"和"装得下竖直"需要的距离，取 max。
   * 只用一个系数乘半径在竖屏视口上会水平裁边（水平 FOV 比垂直窄得多）；
   * 横屏/分屏 UI 换了宽高比也一样会裁，所以必须两边都算。
   * fill = 主体占画面的比例，留 10% 余量。
   */
  function fitDistance(f, camera) {
    var fill = 0.9;
    var asp = 1;
    try {
      var w = app.graphicsDevice.width, h = app.graphicsDevice.height;
      if (w > 0 && h > 0) asp = w / h;
    } catch (e) {}
    if (canvasEl && canvasEl.clientWidth > 0 && canvasEl.clientHeight > 0) {
      asp = canvasEl.clientWidth / canvasEl.clientHeight;
    }
    var fov = 45;
    var horizontal = false;
    try {
      if (camera && camera.camera) { fov = camera.camera.fov || 45; horizontal = !!camera.camera.horizontalFov; }
    } catch (e) {}
    var tY = Math.tan(fov * Math.PI / 360);
    if (horizontal) tY = tY / asp;   // 给的是水平角，换算成竖直
    var tX = tY * asp;
    // 竖直方向要把顶栏+底栏遮挡的那段扣掉：只缩 fill，不改 asp、不改 canvas 尺寸。
    var visFrac = 1;
    try {
      var H = (canvasEl && canvasEl.clientHeight) || 0;
      if (H > 0 && VR_INSET.top + VR_INSET.bottom > 0) {
        visFrac = Math.max(0.5, Math.min(1, 1 - (VR_INSET.top + VR_INSET.bottom) / H));
      }
    } catch (e0) { visFrac = 1; }
    fitDiag.asp = asp; fitDiag.fov = fov; fitDiag.horizontal = horizontal;
    fitDiag.visFrac = visFrac;
    var dH = f.rH / (fill * tX);
    var dV = f.rV / (fill * visFrac * tY);
    return Math.max(dH, dV);
  }

  function applyCam(camera) {
    if (!cam.target) return;
    var r = Math.max(0.05, cam.dist);
    var cp = Math.cos(cam.pitch * Math.PI / 180), sp = Math.sin(cam.pitch * Math.PI / 180);
    var cy = Math.cos(cam.yaw * Math.PI / 180), sy = Math.sin(cam.yaw * Math.PI / 180);
    camera.setPosition(cam.target.x + r * cp * sy, cam.target.y + r * sp, cam.target.z + r * cp * cy);
    camera.lookAt(cam.target);
  }

  function bindGestures(camera) {
    var pointers = new Map(), lastPinch = 0, lastMid = null;
    canvasEl.addEventListener('touchstart', function (e) {
      e.preventDefault();
      for (var i = 0; i < e.changedTouches.length; i++) {
        var t = e.changedTouches[i];
        pointers.set(t.identifier, { x: t.clientX, y: t.clientY });
      }
      if (pointers.size === 2) {
        var p = Array.from(pointers.values());
        lastPinch = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
        lastMid = { x: (p[0].x + p[1].x) / 2, y: (p[0].y + p[1].y) / 2 };
      }
    }, { passive: false });

    canvasEl.addEventListener('touchmove', function (e) {
      e.preventDefault();
      for (var i = 0; i < e.changedTouches.length; i++) {
        var t = e.changedTouches[i];
        var p = pointers.get(t.identifier);
        if (!p) continue;
        if (pointers.size === 1) {
          cam.yaw -= (t.clientX - p.x) * 0.5;
          cam.pitch = Math.max(-89, Math.min(89, cam.pitch + (t.clientY - p.y) * 0.5));
        }
        p.x = t.clientX; p.y = t.clientY;
      }
      if (pointers.size === 2) {
        var a = Array.from(pointers.values());
        var d = Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y);
        var mid = { x: (a[0].x + a[1].x) / 2, y: (a[0].y + a[1].y) / 2 };
        if (lastPinch > 0 && d > 0) cam.dist *= lastPinch / d;
        if (lastMid && cam.target) {
          var k = cam.dist * 0.0015;
          var rx = Math.cos(cam.yaw * Math.PI / 180), rz = -Math.sin(cam.yaw * Math.PI / 180);
          cam.target.x -= (mid.x - lastMid.x) * k * rx;
          cam.target.z -= (mid.x - lastMid.x) * k * rz;
          cam.target.y += (mid.y - lastMid.y) * k;
        }
        lastPinch = d; lastMid = mid;
      }
      applyCam(camera);
    }, { passive: false });

    function end(e) {
      e.preventDefault();
      for (var i = 0; i < e.changedTouches.length; i++) pointers.delete(e.changedTouches[i].identifier);
      if (pointers.size < 2) { lastPinch = 0; lastMid = null; }
    }
    canvasEl.addEventListener('touchend', end, { passive: false });
    canvasEl.addEventListener('touchcancel', end, { passive: false });
    canvasEl.addEventListener('wheel', function (e) {
      e.preventDefault();
      cam.dist *= 1 + e.deltaY * 0.001;
      applyCam(camera);
    }, { passive: false });
  }

  function resetView() {
    if (!home.target || !pc) return;
    cam.yaw = home.yaw; cam.pitch = home.pitch; cam.dist = home.dist;
    cam.target = new pc.Vec3(home.target.x, home.target.y, home.target.z);
    var c = app && app.root ? app.root.findByName('cam') : null;
    if (c) applyCam(c);
  }

  function close() {
    var L = el();
    L.hidden = true;
    L.innerHTML = '';
    try { if (app) app.destroy(); } catch (e) {}
    app = null; canvasEl = null; frame = null;
    cameraEnt = null; splatEntity = null; curAsset = null; curBuf = null;
    global.App.refreshProjects();
  }

  // ==================== 编辑层（edit.js）用的两个受控入口 ====================

  /**
   * 解析 PLY 的顶点布局：stride、x/y/z 的字节偏移与类型宽度、数据起始偏移。
   * 偏移必须按 header 顺序累加（理由同 plyFraming：属性顺序会随 SH degree 变）。
   */
  function plyLayout(buf) {
    try {
      var headBytes = new Uint8Array(buf, 0, Math.min(buf.byteLength, 65536));
      var head = new TextDecoder('ascii').decode(headBytes);
      var end = head.indexOf('end_header');
      if (end < 0) return null;
      var nl = head.indexOf('\n', end);
      var dataOff = nl >= 0 ? nl + 1 : 0;
      if (head.indexOf('binary_big_endian') >= 0) return null;
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
        if (size <= 0) return null;                 // list 类型等不认识的property直接放弃
        if (f[2] === 'x') { offX = stride; tX = size; }
        else if (f[2] === 'y') { offY = stride; tY = size; }
        else if (f[2] === 'z') { offZ = stride; tZ = size; }
        stride += size;
      }
      if (!(count > 0) || stride <= 0 || offX < 0 || offY < 0 || offZ < 0) return null;
      if (dataOff + count * stride > buf.byteLength) return null;
      return { count: count, stride: stride, dataOff: dataOff, offX: offX, offY: offY, offZ: offZ, tX: tX, tY: tY, tZ: tZ };
    } catch (e) {
      return null;
    }
  }

  /**
   * 按 filterFn 逐顶点过滤，产出一份新的 PLY ArrayBuffer。
   * 只改 header 里的 `element vertex N`，顶点字节原样拷贝 —— 与桌面导出口径一致。
   * @param filterFn (i, x, y, z) -> boolean，false 表示丢弃（i 是**原始**下标）
   * @returns {buf: ArrayBuffer, count: number}
   */
  function filterPly(buf, filterFn) {
    var h = plyLayout(buf);
    if (!h) throw new Error('PLY 布局解析失败，无法过滤');
    var src = new Uint8Array(buf);
    var dv = new DataView(buf);
    function rd(base, off, size) {
      return size === 8 ? dv.getFloat64(base + off, true) : dv.getFloat32(base + off, true);
    }
    var body = new Uint8Array(h.count * h.stride);
    var w = 0, kept = 0;
    for (var i = 0; i < h.count; i++) {
      var base = h.dataOff + i * h.stride;
      if (base + h.stride > src.length) break;
      if (filterFn && !filterFn(i,
        rd(base, h.offX, h.tX), rd(base, h.offY, h.tY), rd(base, h.offZ, h.tZ))) continue;
      body.set(src.subarray(base, base + h.stride), w);
      w += h.stride; kept++;
    }
    var headStr = new TextDecoder('ascii').decode(src.subarray(0, h.dataOff));
    var newHead = new TextEncoder().encode(
      headStr.replace(/element\s+vertex\s+\d+/, 'element vertex ' + kept));
    var out = new Uint8Array(newHead.length + w);
    out.set(newHead, 0);
    out.set(body.subarray(0, w), newHead.length);
    return { buf: out.buffer, count: kept };
  }

  /**
   * 编辑层预览入口：按过滤条件重载模型，复用现有相机与视角。
   * 仍然走 plyToGlb 的 GLB 转换链路，不另起渲染路径。
   * @param id 项目 id（取不到原始 PLY 时用它重新拉 /model/<id>.ply）
   * @param filterFn (i,x,y,z)->boolean；传 null 表示不过滤（恢复完整模型）
   * @returns Promise<{splats:number}>
   */
  function reloadFiltered(id, filterFn) {
    return new Promise(function (resolve, reject) {
      if (!app || !pc || !cameraEnt) { reject(new Error('查看器未就绪')); return; }
      var fid = id || cur.id;
      if (!fid) { reject(new Error('缺少项目 id')); return; }
      var p = rawPly(fid);
      p.then(function (buf) {
        if (!stLib) { throw new Error('splat-transform 未加载'); }
        var src = filterFn ? filterPly(buf, filterFn) : { buf: buf, count: parsePlyCount(buf) };
        return plyToGlb(stLib, src.buf, fid + '.ply').then(function (glb) {
          loadGlbAsset(glb, cameraEnt, true, src.count, resolve, reject);
        });
      }).catch(function (e) { reject(e); });
    });
  }

  /** 原始 PLY 字节：优先复用 open() 时那份，没有（或换了项目）才重新拉 */
  function rawPly(id) {
    if (curBuf && cur.id === id) return Promise.resolve(curBuf);
    return fetch('/model/' + id + '.ply').then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.arrayBuffer().then(function (b) { curBuf = b; return b; });
    });
  }

  /** 编辑层只读引用：当前 PlayCanvas 实例、相机状态、画布 */
  function scene() {
    return { app: app, pc: pc, cam: cam, home: home, canvasEl: canvasEl };
  }

  global.Viewer = { open: open, close: close, scene: scene, reloadFiltered: reloadFiltered };
})(window);
