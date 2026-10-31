/* 原生桥封装：容错 + 统一 JSON 解析。引擎/权限等异常一律降级，不抛到页面。 */
(function (global) {
  var has = (typeof global.Diandi !== 'undefined');

  function j(name, arg) {
    if (!has) return null;
    try {
      var s = (arg === undefined) ? global.Diandi[name]() : global.Diandi[name](arg);
      if (s === null || s === undefined || s === '') return null;
      return JSON.parse(s);
    } catch (e) {
      console.warn('bridge.' + name + ' failed: ' + e.message);
      return null;
    }
  }

  function v(name, arg) {
    if (!has) return null;
    try {
      return (arg === undefined) ? global.Diandi[name]() : global.Diandi[name](arg);
    } catch (e) {
      console.warn('bridge.' + name + ' failed: ' + e.message);
      return null;
    }
  }

  /**
   * crop 的 JS 侧形状是 {kind:0|1|2, center:[3], param:number|[3]}，
   * 而 Bridge.saveEditCrop 收的是 {kind:"sphere"|"box"|null, center, radius|size}，
   * 两边形状不同，在这里做一次性转换，别把差异漏到调用方。
   */
  function cropJson(o) {
    o = o || {};
    var kind = Number(o.kind) || 0;
    var out = { id: o.id, kind: null, center: o.center || [0, 0, 0] };
    if (kind === 1) {
      out.kind = 'sphere';
      out.radius = (typeof o.param === 'number') ? o.param : (o.param ? o.param[0] : 0);
    } else if (kind === 2) {
      out.kind = 'box';
      out.size = (o.param && o.param.length >= 3) ? o.param : [0, 0, 0];
    }
    return JSON.stringify(out);
  }

  global.Native = {
    available: has,
    engineStatus: function () { return j('engineStatus') || {}; },
    appInfo: function () { return j('appInfo') || {}; },
    listProjects: function () { return j('listProjects') || []; },
    projectInfo: function (id) { return j('projectInfo', id) || {}; },
    jobProgress: function () { return j('jobProgress') || { active: false }; },
    startJob: function (cfg) {
      return j('startJob', JSON.stringify(cfg)) || { ok: false, message: '桥不可用' };
    },
    pickedMedia: function () { return j('pickedMedia') || []; },
    rename: function (id, name) { return v('renameProject', id, name); },
    remove: function (id) { return v('deleteProject', id); },
    sweep: function () { return j('sweepProjects') || {}; },
    saveHtml: function (id) { return j('saveHtml', id) || {}; },
    // ---------------- 3DGS 编辑（见 app/src/.../EditStore.java） ----------------
    editState: function (id) { return j('editState', id) || { ok: false, message: '桥不可用' }; },
    editMask: function (id) { return j('editMask', id) || { ok: false, message: '桥不可用' }; },
    saveEditMask: function (o) {
      return j('saveEditMask', JSON.stringify(o)) || { ok: false, message: '桥不可用' };
    },
    exportEdit: function (id) { return j('exportEdit', id) || { ok: false, message: '桥不可用' }; },
    saveEditTransform: function (o) {
      return j('saveEditTransform', JSON.stringify(o)) || { ok: false, message: '桥不可用' };
    },
    saveEditCrop: function (o) { return j('saveEditCrop', cropJson(o)) || { ok: false, message: '桥不可用' }; },
    resetEdits: function (id) { return j('resetEdits', id) || { ok: false, message: '桥不可用' }; },

    cancelJob: function () { v('cancelJob'); },
    pickMedia: function () { v('pickMedia'); },
    pickImages: function () { v('pickImages'); },
    captureVideo: function () { v('captureVideo'); },
    log: function (m) { v('log', m); }
  };
})(window);
