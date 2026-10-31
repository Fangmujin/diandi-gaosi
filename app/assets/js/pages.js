/* P1 导入页 / P4 图库池 / 设置页 —— 纯渲染，状态由 App 持有 */
(function (global) {
  var UI = global.UI, I = global.Icons, esc = UI.esc;

  var QUALITY = {
    fast: { label: '快档', note: '约 20-45 分钟 · 适合单件物体' },
    fine: { label: '精档', note: '约 1.5-3 小时 · 适合大场景' }
  };

  /* ---------------- P1 导入页 ---------------- */
  function renderNew(root, st) {
    var media = st.media || [];
    var html = '';

    html += '<div class="page-head">' +
      '<h1>新建重建</h1>' +
      '<p>环绕物体匀速走一圈，重建成可交互的高斯模型</p></div>';

    html += '<div class="capture-card">' +
      '<div class="ring">' + I.svg('camera', 32) + '</div>' +
      '<p class="lead">拍摄一段环绕视频</p>' +
      '<p class="sub">镜头匀速移动、物体始终在画面内、光线均匀，60 秒以内即可</p>' +
      '</div>';

    html += '<div class="btn-row">' +
      '<button class="btn btn-primary" data-act="capture">' + I.svg('camera', 20) + '拍摄素材</button>' +
      '<button class="btn btn-ghost" data-act="pick">' + I.svg('images', 20) + '从相册导入</button>' +
      '</div>';

    html += '<div class="thumbs" id="thumbs">';
    media.forEach(function (m, i) {
      html += '<div class="thumb">' +
        '<img src="' + esc(m.thumb) + '" alt="">' +
        '<button class="rm" data-rm="' + i + '" aria-label="移除">' + I.svg('x', 14) + '</button>' +
        '<div class="kind">' + (m.kind === 'image' ? '图片' : esc(UI.fmtBytes(m.bytes))) + '</div>' +
        '</div>';
    });
    html += '</div>';

    if (!media.length) {
      html += '<p class="empty-note">拍摄或导入一段环绕物体的视频</p>';
    }

    root.innerHTML = html;
    root.querySelectorAll('[data-act]').forEach(function (b) {
      b.onclick = function () {
        var a = b.getAttribute('data-act');
        if (a === 'capture') global.Native.captureVideo();
        else global.Native.pickMedia();
      };
    });
    root.querySelectorAll('[data-rm]').forEach(function (b) {
      b.onclick = function () {
        st.media.splice(Number(b.getAttribute('data-rm')), 1);
        global.App.render();
      };
    });

    // 吸底操作条
    var dock = document.getElementById('dock-new');
    if (!media.length) { dock.hidden = true; return; }
    dock.hidden = false;
    var q = st.quality || 'fast';
    var totalSec = 0;
    media.forEach(function (m) { totalSec = Math.max(totalSec, (m.durationMs || 0) / 1000); });
    var over = totalSec > 60;
    dock.innerHTML = '<div class="dock-inner">' +
      '<div class="seg">' +
      '<button data-q="fast" class="' + (q === 'fast' ? 'on' : '') + '">' + QUALITY.fast.label + '</button>' +
      '<button data-q="fine" class="' + (q === 'fine' ? 'on' : '') + '">' + QUALITY.fine.label + '</button>' +
      '</div>' +
      '<p class="seg-note">' + esc(QUALITY[q].note) +
      (over ? ' · 素材 ' + Math.round(totalSec) + ' 秒，建议控制在 60 秒内' : '') + '</p>' +
      '<button class="btn btn-primary" data-act="start">' + I.svg('play', 20) + '开始重建</button>' +
      '</div>';
    dock.querySelectorAll('[data-q]').forEach(function (b) {
      b.onclick = function () { st.quality = b.getAttribute('data-q'); global.App.render(); };
    });
    dock.querySelector('[data-act="start"]').onclick = function () {
      global.App.startJob();
    };
  }

  /* ---------------- P4 图库池 ---------------- */
  function renderGallery(root, st) {
    var list = st.projects || [];
    if (!list.length) {
      root.innerHTML =
        '<div class="page-head"><h1>图库</h1><p>已完成的重建项目</p></div>' +
        '<div class="empty">' + I.svg('box', 48) +
        '<div class="t1">还没有重建项目</div>' +
        '<div class="t2">拍一段环绕物体的视频，几分钟就能得到第一个高斯模型</div>' +
        '<button class="btn btn-primary" data-act="gonew" style="max-width:220px;margin:0 auto">' +
        I.svg('plus', 20) + '新建第一个</button></div>';
      root.querySelector('[data-act="gonew"]').onclick = function () { global.App.setTab('new'); };
      return;
    }

    var html = '<div class="page-head"><h1>图库</h1><p>' + list.length + ' 个项目</p></div><div class="grid">';
    list.forEach(function (p) {
      var status = p.status || 'running';
      var badge;
      if (status === 'done') {
        badge = '<span class="badge ok">' + I.svg('check', 10) + '已完成</span>';
      } else if (status === 'failed') {
        badge = '<span class="badge err">' + I.svg('alert-triangle', 10) + '失败</span>';
      } else {
        var pc = Math.max(0, Math.min(100, Math.round(p.progress || 0)));
        badge = '<span class="badge run">' + I.svg('loader', 10) + pc + '%</span>';
      }
      var cover = p.id ? ('/cover/' + p.id + '.jpg') : '';
      var hasCover = (status === 'done');
      // HTML 入口挂在每个卡片右上角。只有已完成（有 final.ply）的才给，
      // 未完成项目点它必然失败，不如不给。
      var shareBtn = (status === 'done')
        ? '<button class="gshare" data-share="' + esc(p.id) + '" aria-label="保存 HTML 到手机">' +
          I.svg('share-2', 20) + '</button>'
        : '';
      html += '<div class="gcard" data-id="' + esc(p.id) + '">' +
        shareBtn +
        '<img class="cv" src="' + esc(cover) + '" alt=""' +
        (hasCover ? '' : ' style="display:none"') +
        ' onerror="this.style.display=\'none\'">' +
        '<div class="nv"' + (hasCover ? ' style="display:none"' : '') + '>' +
        I.svg('image-off', 32) + '</div>' +
        '<div class="foot">' +
        '<div class="nm">' + esc(p.name || '未命名') + '</div>' +
        '<div class="row">' + badge +
        '<span class="dt">' + esc(p.date || UI.fmtDate(p.createdAt)) + '</span></div>' +
        '</div></div>';
    });
    html += '</div>';
    root.innerHTML = html;

    root.querySelectorAll('.gcard').forEach(function (card) {
      var id = card.getAttribute('data-id');
      var pressTimer = null, longed = false;
      function down(e) {
        longed = false;
        pressTimer = setTimeout(function () {
          longed = true;
          longPress(id);
        }, 480);
      }
      function up(e) {
        if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
        if (!longed) openProject(id);
      }
      card.addEventListener('touchstart', down, { passive: true });
      card.addEventListener('touchend', up);
      card.addEventListener('touchmove', function () {
        if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
      }, { passive: true });
      card.addEventListener('click', function (e) {
        // 桌面/无障碍点击
        if (!longed) openProject(id);
      });
      card.addEventListener('contextmenu', function (e) { e.preventDefault(); longPress(id); });

      // 右上角「保存 HTML」：必须拦掉冒泡，否则会连带触发卡片的点击/长按逻辑
      var sb = card.querySelector('[data-share]');
      if (sb) {
        sb.addEventListener('click', function (e) {
          e.stopPropagation();
          e.preventDefault();
          saveHtml(id);
        });
        sb.addEventListener('touchstart', function (e) { e.stopPropagation(); }, { passive: true });
        sb.addEventListener('touchend', function (e) { e.stopPropagation(); });
        sb.addEventListener('touchmove', function (e) { e.stopPropagation(); }, { passive: true });
      }
    });
  }

  /** 生成单文件 HTML 并保存到手机「下载/点点高斯/」。结果由原生侧 toast 回报。 */
  function saveHtml(id) {
    var p = (global.App.state.projects || []).filter(function (x) { return x.id === id; })[0];
    UI.toast('正在生成「' + ((p && p.name) || '未命名') + '」的 HTML…');
    var r = global.Native.saveHtml(id);
    if (r && r.ok === false && !r.pending) UI.toast(r.message || '启动保存失败');
  }

  function openProject(id) {
    var p = (global.App.state.projects || []).filter(function (x) { return x.id === id; })[0];
    if (!p) return;
    if (p.status === 'done') {
      global.Viewer.open(id, p.name || '未命名');
    } else {
      global.Progress.open(id, p.name || '未命名');
    }
  }

  function longPress(id) {
    var p = (global.App.state.projects || []).filter(function (x) { return x.id === id; })[0];
    if (!p) return;
    UI.sheet(p.name || '未命名', [
      {
        label: '重命名', icon: 'pencil', onTap: function () {
          UI.prompt('重命名项目', p.name || '', function (v) {
            global.Native.rename(id, v);
            global.App.refreshProjects(true);
          });
        }
      },
      {
        label: '删除', icon: 'trash-2', danger: true, onTap: function () {
          // 先从本地数组摘掉这一项：原生删除成功后若刷新被节流吞掉，
          // 页面也不会继续显示已经不存在的作品（幽灵卡片）。
          global.App.state.projects = (global.App.state.projects || []).filter(function (x) {
            return x.id !== id;
          });
          global.Native.remove(id);
          global.App.refreshProjects(true);
          UI.toast('已删除');
        }
      }
    ]);
  }

  /* ---------------- 设置页 ---------------- */
  function renderSettings(root, st) {
    var e = st.engine || {};
    var info = st.info || {};
    function dot(ok) { return '<span class="dot ' + (ok ? 'ok' : 'no') + '"></span>'; }
    var html = '<div class="page-head"><h1>设置</h1><p>运行状态与设备信息</p></div>';

    html += '<div class="section-title">原生引擎</div>';
    html += '<div class="kv-list">' +
      row('SfM 引擎', dot(e.sfm) + (e.sfm ? '已就绪' : '未接入'), true) +
      row('训练引擎', dot(e.train) + (e.train ? '已就绪' : '未接入'), true) +
      row('ABI', esc(e.abi || '—')) +
      '</div>';

    if (!e.sfm || !e.train) {
      // 降级提示必须点名缺的是哪个引擎：x86_64 包只有 trainer、arm64 包只有 SfM，
      // 说"引擎未就绪"用户无法判断该等哪一边。
      var miss = [];
      if (!e.sfm) miss.push('位姿重建引擎（' + (e.sfm_lib || 'SfM') + '）');
      if (!e.train) miss.push('高斯训练引擎（' + (e.train_lib || 'trainer') + '）');
      html += '<div class="card" style="margin-bottom:16px;border-color:var(--warn)">' +
        '<div style="display:flex;gap:8px;align-items:center;color:var(--warn);margin-bottom:6px">' +
        I.svg('alert-triangle', 18) + '<b style="font-size:14px">引擎待接入</b></div>' +
        '<p style="margin:0;color:var(--fg);font-size:13px;line-height:1.6">' +
        '当前 ABI（' + esc(e.abi || '—') + '）缺少：' + esc(miss.join('、')) + '。' +
        '</p>' +
        '<p style="margin:6px 0 0;color:var(--muted);font-size:12px;line-height:1.6">' +
        '该引擎尚未为这一 ABI 交叉编译完成，接入后重新安装即可开始重建。' +
        '这段时间内应用可正常浏览与导入，点击「开始重建」会明确提示缺哪个引擎，不会崩溃。' +
        '</p></div>';
    }

    html += '<div class="section-title">设备</div>';
    html += '<div class="kv-list">' +
      row('机型', esc(e.device || '—')) +
      row('系统 API', esc(String(e.sdk || '—'))) +
      row('版本', 'v' + esc(info.version || '0.1')) +
      '</div>';

    html += '<div class="section-title">双档参数</div>';
    html += '<div class="kv-list">' +
      row('快档', info.paramsFast ? (info.paramsFast.frames + ' 帧 · ' +
        info.paramsFast.maxResolution + 'px · ' + info.paramsFast.totalSteps + ' 步') : '—') +
      row('精档', info.paramsFine ? (info.paramsFine.frames + ' 帧 · ' +
        info.paramsFine.maxResolution + 'px · ' + info.paramsFine.totalSteps + ' 步') : '—') +
      '</div>';

    html += '<div class="section-title">路径</div>';
    html += '<div class="kv-list">' + row('nativeLibDir', esc(e.libDir || '—')) + '</div>';

    root.innerHTML = html;
  }

  function row(k, v) {
    return '<div class="kv-row"><span class="k">' + esc(k) +
      '</span><span class="v">' + v + '</span></div>';
  }

  global.Pages = {
    QUALITY: QUALITY,
    new: renderNew,
    gallery: renderGallery,
    settings: renderSettings
  };
})(window);
