/* 应用引导：路由 / 状态 / 与原生桥的事件通道 */
(function (global) {
  var UI = global.UI, I = global.Icons;

  global.onerror = function (msg, url, line) {
    var b = document.getElementById('boot');
    if (b) {
      b.innerHTML = '<div style="color:var(--error)">界面加载失败</div>' +
        '<div style="color:var(--muted);font-size:12px;margin-top:8px">' +
        UI.esc(String(msg)) + ' @' + line + '</div>';
    }
  };

  var TABS = [
    { key: 'new', label: '新建', icon: 'plus' },
    { key: 'gallery', label: '图库', icon: 'grid' },
    { key: 'settings', label: '设置', icon: 'sliders' }
  ];

  var App = {
    state: {
      tab: 'new',
      media: [],
      quality: 'fast',
      projects: [],
      engine: {},
      info: {}
    },
    _lastRefresh: 0
  };

  function pageEl(key) { return document.getElementById('page-' + key); }

  function renderTabbar() {
    var nav = document.getElementById('tabbar');
    var html = '';
    TABS.forEach(function (t) {
      html += '<button data-tab="' + t.key + '" class="' +
        (App.state.tab === t.key ? 'on' : '') + '">' +
        I.svg(t.icon, 22) + '<span>' + t.label + '</span></button>';
    });
    nav.innerHTML = html;
    nav.querySelectorAll('[data-tab]').forEach(function (b) {
      b.onclick = function () { setTab(b.getAttribute('data-tab')); };
    });
  }

  function render() {
    renderTabbar();
    TABS.forEach(function (t) {
      var el = pageEl(t.key);
      if (!el) return;
      if (t.key === App.state.tab) {
        el.hidden = false;
        try {
          if (t.key === 'new') global.Pages.new(el, App.state);
          else if (t.key === 'gallery') global.Pages.gallery(el, App.state);
          else global.Pages.settings(el, App.state);
        } catch (e) {
          el.innerHTML = '<div class="page-head"><h1>页面渲染出错</h1><p>' +
            UI.esc(e && e.message ? e.message : String(e)) + '</p></div>';
        }
      } else {
        el.hidden = true;
      }
    });
    var dock = document.getElementById('dock-new');
    if (App.state.tab !== 'new') dock.hidden = true;
  }

  function setTab(key) {
    if (App.state.tab === key) return;
    App.state.tab = key;
    if (key === 'gallery') refreshProjects();
    render();
  }

  /* force=true 时绕过 600ms 节流：删除/重命名这类本地操作后必须立刻同步，
     否则紧跟的这次刷新会被吞掉，页面还留着已经删掉的卡片。 */
  function refreshProjects(force) {
    var now = Date.now();
    if (!force && now - App._lastRefresh < 600) return;
    App._lastRefresh = now;
    App.state.projects = global.Native.listProjects();
    if (App.state.tab === 'gallery') {
      var el = pageEl('gallery');
      if (el && !el.hidden) {
        try { global.Pages.gallery(el, App.state); } catch (e) { console.warn(e); }
      }
    }
  }

  function defaultName() {
    var d = new Date();
    function p(x) { return x < 10 ? '0' + x : String(x); }
    return '重建 ' + p(d.getMonth() + 1) + p(d.getDate()) + '-' + p(d.getHours()) + p(d.getMinutes());
  }

  function startJob(cfg) {
    var st = App.state;
    var rel = (cfg && cfg.rel) || (st.media[0] && st.media[0].rel);
    if (!rel) { UI.toast('请先拍摄或导入素材'); return; }
    var payload = {
      name: (cfg && cfg.name) || defaultName(),
      quality: (cfg && cfg.quality) || st.quality || 'fast',
      rel: rel
    };
    if (cfg && cfg.projectId) payload.projectId = cfg.projectId;

    var r = global.Native.startJob(payload);
    if (!r || !r.ok) {
      UI.toast((r && r.message) || '启动失败');
      return;
    }
    global.Progress.open(r.projectId, payload.name);
    setTimeout(refreshProjects, 300);
  }

  function retryJob(id) {
    var p = global.Native.projectInfo(id);
    if (!p || !p.sourceRel) { UI.toast('找不到原始素材，请重新导入'); return; }
    startJob({ projectId: id, name: p.name, quality: p.quality, rel: p.sourceRel });
  }

  /* 原生推送事件 */
  global.__native = function (json) {
    try {
      if (json && json.type === 'progress') {
        global.Progress.push(json.data);
        if (Date.now() - App._lastRefresh > 1200) refreshProjects();
      } else if (json && json.type === 'media') {
        App.state.media.push(json.data);
        if (App.state.tab !== 'new') App.state.tab = 'new';
        render();
      } else if (json && json.type === 'toast') {
        UI.toast(json.message || '');
      }
    } catch (e) {
      console.warn('native event failed', e);
    }
  };

  function boot() {
    App.state.engine = global.Native.engineStatus();
    App.state.info = global.Native.appInfo();
    var picked = global.Native.pickedMedia();
    if (picked && picked.length) App.state.media = picked;
    refreshProjects();
    render();
    var bootEl = document.getElementById('boot');
    var appEl = document.getElementById('app');
    if (bootEl) bootEl.hidden = true;
    if (appEl) appEl.hidden = false;
    setInterval(refreshProjects, 2500);
    console.log('diandi-gaosi booted; engine=', JSON.stringify(App.state.engine));
  }

  global.addEventListener('error', function (e) {
    console.warn('window error: ' + (e && e.message));
  });

  App.render = render;
  App.setTab = setTab;
  App.refreshProjects = refreshProjects;
  App.startJob = startJob;
  App.retryJob = retryJob;
  global.App = App;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})(window);
