/* P2 运行进度页 —— 阶段清单 + 动态 ETA + 折叠日志 + 行内错误重试 */
(function (global) {
  var UI = global.UI, I = global.Icons, esc = UI.esc;

  var layer, timer = null;
  var cur = { projectId: null, name: '', data: null };

  var STAGE_ICON = {
    pending: 'circle',
    running: 'loader',
    done: 'check',
    failed: 'alert-triangle'
  };

  function el() {
    if (!layer) layer = document.getElementById('layer-progress');
    return layer;
  }

  function open(projectId, name) {
    cur.projectId = projectId;
    cur.name = name || '未命名';
    cur.data = null;
    var L = el();
    L.hidden = false;
    render();
    poll();
    if (timer) clearInterval(timer);
    timer = setInterval(poll, 900);
  }

  function close() {
    if (timer) { clearInterval(timer); timer = null; }
    var L = el();
    L.hidden = true;
    L.innerHTML = '';
    cur.data = null;
  }

  function isOpen() { var L = el(); return L && !L.hidden; }

  function poll() {
    var d = global.Native.jobProgress();
    if (d && d.projectId === cur.projectId) {
      cur.data = d;
      render();
    } else if (!cur.data) {
      cur.data = { active: false, projectId: cur.projectId, state: 'idle' };
      render();
    }
    if (cur.data && (cur.data.state === 'done' || cur.data.state === 'failed'
      || cur.data.state === 'cancelled')) {
      if (timer) { clearInterval(timer); timer = null; }
      global.App.refreshProjects();
    }
  }

  function push(d) {
    if (!isOpen()) return;
    if (!d || d.projectId !== cur.projectId) return;
    cur.data = d;
    render();
  }

  function render() {
    var L = el();
    if (!L || L.hidden) return;
    var d = cur.data || {};
    var stages = d.stages || defaultStages();
    var eta = UI.fmtEta(d.etaSec || 0);
    var overall = Math.round((d.overall || 0) * 100);
    var state = d.state || 'idle';

    var html = '<div class="prog-head">' +
      '<div class="prog-top">' +
      '<button class="icon-btn" data-act="close" aria-label="返回">' + I.svg('arrow-left', 20) + '</button>' +
      '<span class="title">' + esc(cur.name) + '</span>' +
      (state === 'running'
        ? '<button class="btn btn-sm btn-ghost" data-act="cancel">取消</button>'
        : '') +
      '</div>' +
      '<div class="eta-block">' +
      '<span class="eta-num">' + esc(eta.v) + '</span>' +
      '<span class="eta-unit">' + esc(eta.u) + (eta.sub ? ' ' + esc(eta.sub) : '') + '</span>' +
      '</div>' +
      '<div class="eta-label">预估剩余耗时' +
      (d.etaCalibrated ? ' <span class="eta-cal">' + I.svg('zap', 11) + '已按实测速度校准</span>' : '') +
      '</div>' +
      '<div class="bar"><i style="width:' + overall + '%"></i></div>' +
      '</div>';

    html += '<div class="prog-body"><div id="stage-list">';
    stages.forEach(function (s) {
      var st = s.status || 'pending';
      var pct = st === 'done' ? 100 : (st === 'running' ? s.pct : 0);
      var cls = STAGE_ICON[st] || 'circle';
      html += '<div class="stage-wrap">' +
        '<div class="stage ' + st + '" style="grid-column:1/4">' +
        '<span class="ico">' + I.svg(cls, 18, st === 'running' ? 'spin' : '') + '</span>' +
        '<span class="nm">' + esc(s.name) + '</span>' +
        '<span class="pct">' + (st === 'pending' ? '—' : (Math.round(pct * 10) / 10) + '%') + '</span>' +
        '</div></div>';
      if (st === 'running' && s.detail) {
        html += '<div class="stage-wrap"><div class="det" style="grid-column:2/4">' +
          esc(s.detail) + '</div></div>';
      }
    });
    html += '</div>';

    if (d.error) {
      // kind==='gpu'：设备/模拟器图形驱动不支持训练所需的 GPU 计算。
      // 这类失败重试必然再崩，给"重试"按钮是在骗用户，只能给"知道了"。
      var gpu = d.error.kind === 'gpu';
      var stageLabel = d.error.stageName || '';
      html += '<div class="err-box"' +
        (gpu ? ' style="border-color:var(--warn)"' : '') + '>' +
        '<div class="eh">' + I.svg(gpu ? 'info' : 'alert-triangle', 18) +
        '<span>' + (gpu
          ? '「' + esc(stageLabel || '训练') + '」这一步在当前设备上无法完成'
          : (stageLabel
            ? '「' + esc(stageLabel) + '」这一步未能完成'
            : '重建未能完成')) + '</span></div>' +
        '<p class="em">' + esc(d.error.message || '') + '</p>' +
        (d.error.hint ? '<p class="hint">' + esc(d.error.hint) + '</p>' : '') +
        (gpu
          ? '<p class="hint" style="margin-top:8px">这不是应用故障：' +
          '抽帧与位姿重建已在本地跑完，只有这一步依赖设备 GPU。' +
          '请在支持 Vulkan 的真机上继续。</p>'
          : '') +
        (d.error.retryable !== false
          ? '<button class="btn btn-ghost" data-act="retry">' + I.svg('refresh-cw', 20) + '重试</button>'
          : '<button class="btn btn-ghost" data-act="close">' + I.svg('check', 20) + '知道了</button>') +
        (d.error.raw
          ? '<button class="log-head" data-act="raw" style="margin-top:10px">' +
          I.svg('chevron-down', 16) + '<span class="t">引擎原始输出</span></button>' +
          '<pre class="log-body mono"' + (rawOpen ? '' : ' hidden') + '>' +
          esc(d.error.raw) + '</pre>'
          : '') +
        '</div>';
    }

    if (state === 'done') {
      html += '<div class="err-box" style="background:var(--success-dim);border-color:var(--success)">' +
        '<div class="eh" style="color:var(--success)">' + I.svg('check', 18) + '<span>重建完成</span></div>' +
        '<button class="btn btn-primary" data-act="view" style="margin-top:8px">' +
        I.svg('box', 20) + '查看模型</button></div>';
    }

    var log = d.log || [];
    html += '<div class="log-box">' +
      '<button class="log-head" data-act="log">' +
      I.svg('chevron-down', 16) + '<span class="t">运行日志 · ' + log.length + ' 行</span></button>' +
      '<div class="log-body" id="log-body"' + (logOpen ? '' : ' hidden') + '>';
    log.forEach(function (l) {
      var err = /ERROR|failed|panic/i.test(l.msg || '');
      html += '<div class="ln' + (err ? ' err' : '') + '"><span class="ts">' + esc(l.t) +
        '</span><span class="msg">' + esc(l.msg) + '</span></div>';
    });
    html += '</div></div></div>';

    L.innerHTML = html;

    var b;
    if ((b = L.querySelector('[data-act="close"]'))) b.onclick = close;
    if ((b = L.querySelector('[data-act="cancel"]'))) b.onclick = function () {
      global.Native.cancelJob();
      UI.toast('正在取消…');
    };
    if ((b = L.querySelector('[data-act="retry"]'))) b.onclick = function () {
      global.App.retryJob(cur.projectId);
    };
    if ((b = L.querySelector('[data-act="view"]'))) b.onclick = function () {
      close();
      global.Viewer.open(cur.projectId, cur.name);
    };
    if ((b = L.querySelector('[data-act="log"]'))) b.onclick = function () {
      logOpen = !logOpen;
      render();
    };
    if ((b = L.querySelector('[data-act="raw"]'))) b.onclick = function () {
      rawOpen = !rawOpen;
      render();
    };
    var lb = document.getElementById('log-body');
    if (lb && logOpen) lb.scrollTop = lb.scrollHeight;
  }

  var logOpen = false;
  var rawOpen = false;

  function defaultStages() {
    var names = ['抽帧', '提特征', '匹配', '重建', '训练', '导出'];
    return names.map(function (n, i) {
      return { key: n, name: n, status: i === 0 ? 'running' : 'pending', pct: 0 };
    });
  }

  global.Progress = { open: open, close: close, push: push, isOpen: isOpen, poll: poll };
})(window);
