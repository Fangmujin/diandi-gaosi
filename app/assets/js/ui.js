/* 通用 UI 工具：格式化、Toast、ActionSheet、输入弹窗 */
(function (global) {
  var esc = global.__esc || function (s) { return String(s); };

  function h(tag, attrs, html) {
    var s = '<' + tag;
    if (attrs) {
      for (var k in attrs) {
        if (attrs[k] === false || attrs[k] === null || attrs[k] === undefined) continue;
        s += ' ' + k + '="' + esc(attrs[k]) + '"';
      }
    }
    return s + '>' + (html || '') + '</' + tag + '>';
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n <= 0) return '—';
    if (n >= 1073741824) return (n / 1073741824).toFixed(2) + ' GB';
    if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
    if (n >= 1024) return Math.round(n / 1024) + ' KB';
    return n + ' B';
  }

  function fmtCount(n) {
    n = Number(n) || 0;
    if (n <= 0) return '—';
    if (n >= 1000000) return (n / 1000000).toFixed(2) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return String(n);
  }

  /** 大数字 + 单位：用于"预估剩余耗时" */
  function fmtEta(sec) {
    sec = Math.max(0, Math.round(Number(sec) || 0));
    if (sec < 60) return { v: String(sec), u: '秒' };
    if (sec < 3600) {
      var m = Math.floor(sec / 60), s = sec % 60;
      return { v: String(m), u: '分钟', sub: s > 0 ? (s + ' 秒') : '' };
    }
    var hh = Math.floor(sec / 3600), mm = Math.round((sec % 3600) / 60);
    return { v: String(hh), u: '小时', sub: mm > 0 ? (mm + ' 分') : '' };
  }

  function fmtDate(ts) {
    var d = new Date(Number(ts) || Date.now());
    function p(x) { return x < 10 ? '0' + x : String(x); }
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  }

  function toast(msg, ms) {
    var root = document.getElementById('toast-root');
    if (!root) return;
    var el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    root.appendChild(el);
    setTimeout(function () {
      el.style.transition = 'opacity 200ms';
      el.style.opacity = '0';
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 220);
    }, ms || 2000);
  }

  /** 底部 ActionSheet：items = [{label, icon, danger, onTap}] */
  function sheet(title, items) {
    var root = document.getElementById('sheet-root');
    if (!root) return;
    var body = '<div class="sheet-scrim"></div><div class="sheet">';
    body += '<div class="st">' + esc(title) + '</div>';
    items.forEach(function (it, i) {
      body += '<button class="sheet-item' + (it.danger ? ' danger' : '') + '" data-i="' + i + '">' +
        global.Icons.svg(it.icon || 'circle', 20) + '<span>' + esc(it.label) + '</span></button>';
    });
    body += '</div>';
    root.innerHTML = body;
    root.classList.add('on');

    function close() { root.classList.remove('on'); root.innerHTML = ''; }
    root.querySelector('.sheet-scrim').onclick = close;
    root.querySelectorAll('.sheet-item').forEach(function (b) {
      b.onclick = function () {
        var it = items[Number(b.getAttribute('data-i'))];
        close();
        if (it && it.onTap) setTimeout(it.onTap, 60);
      };
    });
  }

  /** 极简输入弹窗（重命名） */
  function prompt(title, value, cb) {
    var root = document.getElementById('sheet-root');
    if (!root) return;
    root.innerHTML =
      '<div class="sheet-scrim"></div><div class="sheet">' +
      '<div class="st">' + esc(title) + '</div>' +
      '<input id="sheet-input" type="text" value="' + esc(value || '') + '" ' +
      'style="width:100%;min-height:44px;background:var(--bg);color:var(--fg);' +
      'border:1px solid var(--border);border-radius:var(--r-sm);padding:0 12px;' +
      'font-family:inherit;font-size:16px;margin-bottom:12px">' +
      '<button class="btn btn-primary" id="sheet-ok">确定</button></div>';
    root.classList.add('on');
    function close() { root.classList.remove('on'); root.innerHTML = ''; }
    root.querySelector('.sheet-scrim').onclick = close;
    root.querySelector('#sheet-ok').onclick = function () {
      var v = root.querySelector('#sheet-input').value.trim();
      close();
      if (v) cb(v);
    };
  }

  global.UI = {
    h: h, esc: esc, fmtBytes: fmtBytes, fmtCount: fmtCount,
    fmtEta: fmtEta, fmtDate: fmtDate, toast: toast, sheet: sheet, prompt: prompt
  };
})(window);
